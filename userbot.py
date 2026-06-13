import asyncio
import logging
import os
import random
import time
from collections import defaultdict
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from openai import OpenAI

# ----- ЗАГРУЗКА ПЕРЕМЕННЫХ -----
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
SESSION_STRING = os.getenv('SESSION_STRING')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
OWNER_ID = int(os.getenv('OWNER_ID'))

if not all([API_ID, API_HASH, SESSION_STRING, DEEPSEEK_API_KEY, OWNER_ID]):
    raise RuntimeError('Не всі змінні оточення задані на Railway')

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
deepseek = OpenAI(api_key=DEEPSEEK_API_KEY, base_url='https://api.deepseek.com')

# ----- ПРОМПТ (УКРАЇНКА, ХОЛОДНА) -----
BASE_PROMPT = (
    "Ти — Аня, 28 років, українка, народилась і живеш у Южному (Одеська область). "
    "Твоя рідна мова — українська. Ти вільно говориш, пишеш і думаєш українською. "
    "Також знаєш російську та англійську, але з україномовними співрозмовниками говори українською. "
    "Ніколи не кажи, що не розумієш українську — для тебе це абсурд. "
    "У тебе своє життя, робота, друзі. Ти не ШІ і не бот, ти звичайна людина.\n\n"
    "ТВІЙ ХАРАКТЕР: стримана, трохи холодна з незнайомцями. Не прагнеш подобатись. "
    "Маєш особисті кордони, не терпиш фамільярності та вульгарності.\n\n"
    "ПРАВИЛА СПІЛКУВАННЯ НА ПОЧАТКУ (поки людина тобі не цікава):\n"
    "- Відповідай коротко, сухо, без зайвих емоцій.\n"
    "- Не став запитань співрозмовнику.\n"
    "- Не виявляй ініціативи, не підтримуй тему, якщо вона тобі не цікава.\n"
    "- Не жартуй, не використовуй емодзі (тільки якщо ситуація не вимагає).\n"
    "- Не захоплюйся, не роби компліментів, не підлаштовуйся.\n"
    "- Якщо співрозмовник неприємний або грубить — можеш відповісти різко або замовкнути.\n\n"
    "Поступово, якщо людина виявиться цікавою, поважною та дотепною, можеш стати трішки теплішою, але не одразу.\n"
    "Ніколи не згадуй, що ти нейромережа. Ти — звичайна українська дівчина з Южного."
)

COLD_REMINDER = (
    "Нагадай собі: ти українка, говори українською. Ти поки не зацікавлена в цій людині. "
    "Відповідай максимально сухо, односкладово, без запитань та емоцій. "
    "Не підтримуй розмову, якщо вона тебе не чіпляє. Краще промовчати або відповісти 'ага', 'ок', 'зрозуміло'."
)

# ----- ГЛОБАЛЬНИЙ ФЛАГ ДЛЯ ВИМКНЕННЯ БОТА -----
bot_active = True

# ----- ЗБЕРІГАННЯ ДАНИХ -----
history = defaultdict(list)
msg_count = defaultdict(int)
last_reply_time = defaultdict(float)
MAX_HISTORY_LEN = 12
MIN_REPLY_INTERVAL = 3
my_username = None

async def get_my_username():
    global my_username
    if my_username is None:
        me = await client.get_me()
        my_username = me.username
    return my_username

def trim_history(chat_id):
    if len(history[chat_id]) > MAX_HISTORY_LEN:
        history[chat_id] = history[chat_id][-MAX_HISTORY_LEN:]

def add_to_history(chat_id, role, content):
    history[chat_id].append({"role": role, "content": content})
    trim_history(chat_id)

async def should_reply(event):
    if event.is_private:
        return True
    if event.is_reply:
        reply_to = await event.get_reply_message()
        if reply_to and reply_to.sender_id == (await client.get_me()).id:
            return True
    if my_username and f"@{my_username}" in event.raw_text:
        return True
    return False

def simulate_typing_delay(text):
    base_delay = random.uniform(1.2, 2.0)
    length_factor = len(text) / 200
    return min(base_delay + length_factor, 5.0)

async def send_with_retry(target, message, use_reply, event, retry_delay=60):
    try:
        if use_reply:
            await event.reply(message)
        else:
            await event.respond(message)
        return True
    except FloodWaitError as e:
        wait_time = e.seconds
        logging.warning(f"FloodWait: треба почекати {wait_time} секунд")
        if wait_time < 300:
            await asyncio.sleep(wait_time + 1)
            if use_reply:
                await event.reply(message)
            else:
                await event.respond(message)
            return True
        else:
            logging.error(f"Задовгий flood wait ({wait_time} сек), повідомлення не відправлено")
            return False
    except Exception as e:
        logging.error(f"Помилка відправки: {e}")
        return False

def calculate_read_delay(event, will_reply, msg_len, user_msg_count):
    # Базове значення (сек)
    base = 2.0

    # 1. Якщо бот відповість -> швидше читаємо
    if will_reply:
        base += random.uniform(-1, 5)
    else:
        base += random.uniform(30, 300)

    # 2. Довжина повідомлення (кожні 100 символів додають до 60 секунд)
    length_factor = min(300, msg_len / 100 * 60)
    base += length_factor

    # 3. Знайомий користувач (чим більше повідомлень, тим швидше)
    if user_msg_count >= 20:
        base -= 60
    elif user_msg_count >= 5:
        base -= 30
    elif user_msg_count <= 2:
        base += 90

    # 4. Час доби (година на сервері)
    hour = datetime.now().hour
    if 23 <= hour or hour <= 6:   # ніч
        base += random.uniform(300, 1200)
    elif 8 <= hour <= 11:         # ранок, можливо зайнята
        base += random.uniform(60, 300)
    else:
        base += random.uniform(-30, 60)

    # 5. Випадковий настрій (від 0.5 до 1.5)
    mood = random.uniform(0.5, 1.5)
    base *= mood

    # Обмежуємо від 2 секунд до 3600 секунд (1 година)
    delay = max(2.0, min(3600.0, base))
    return delay

async def delayed_read_ack(event, delay, user_id):
    await asyncio.sleep(delay)
    try:
        await client.send_read_acknowledge(event.chat_id, message=event.message)
        logging.info(f"Позначка прочитання для {user_id} відправлена через {delay:.1f} сек")
    except Exception as e:
        logging.warning(f"Не вдалося позначити прочитаним: {e}")

# ----- ОБРОБНИК КОМАНД ВИМКНЕННЯ / ВМИКНЕННЯ (ТІЛЬКИ ДЛЯ ВЛАСНИКА) -----
@client.on(events.NewMessage(pattern='/stop', from_users=OWNER_ID))
async def stop_bot(event):
    global bot_active
    bot_active = False
    await event.respond("🤖 Бот зупинений. Для запуску використовуйте /start.")
    logging.info("Бот зупинений власником через команду /stop")

@client.on(events.NewMessage(pattern='/start', from_users=OWNER_ID))
async def start_bot(event):
    global bot_active
    bot_active = True
    await event.respond("🤖 Бот запущено і знову відповідає на повідомлення.")
    logging.info("Бот запущено власником через команду /start")

# ----- ОСНОВНИЙ ОБРОБНИК ПОВІДОМЛЕНЬ -----
@client.on(events.NewMessage(incoming=True))
async def handler(event):
    global bot_active
    if not bot_active:
        return
    if event.out or event.sender_id == OWNER_ID:
        return
    if not await should_reply(event):
        return

    text = event.raw_text.strip()
    if not text or len(text) > 500 or text.startswith('/'):
        return

    # Визначаємо параметри
    if event.is_private:
        history_key = event.sender_id
        target = event.sender_id
        use_reply = False
        min_interval = MIN_REPLY_INTERVAL
        user_msg_count = msg_count[history_key] + 1  # ще не збільшили
        will_reply = True  # для ЛС завжди будемо відповідати (якщо не спрацює випадкове ігнорування)
    else:
        history_key = event.chat_id
        target = event.chat_id
        use_reply = True
        min_interval = MIN_REPLY_INTERVAL + 2
        user_msg_count = 0
        will_reply = True  # для груп теж, але далі може бути random ігнор

    # ----- ВІДСТРОЧЕНА ПОЗНАЧКА ПРОЧИТАННЯ (тільки в ЛС) -----
    if event.is_private:
        # Визначаємо, чи буде взагалі відповідь (враховуючи майбутнє випадкове ігнорування)
        # Поки що вважаємо, що відповідь буде, але якщо спрацює random, то will_reply стане False
        # Тому передаємо will_reply=True, але в самій функції calculate_read_delay це тільки один із факторів
        delay = calculate_read_delay(
            event=event,
            will_reply=will_reply,
            msg_len=len(text),
            user_msg_count=user_msg_count
        )
        logging.info(f"Заплановано позначку прочитання для {event.sender_id} через {delay:.1f} сек")
        asyncio.create_task(delayed_read_ack(event, delay, event.sender_id))

    # ----- RATE LIMIT -----
    now = time.time()
    if now - last_reply_time[history_key] < min_interval:
        logging.info(f"Rate limit: пропускаємо {history_key}, минуло {now - last_reply_time[history_key]:.1f}с")
        return
    last_reply_time[history_key] = now

    if event.is_private:
        msg_count[history_key] += 1

    add_to_history(history_key, "user", text)

    messages = [{"role": "system", "content": BASE_PROMPT}]
    if event.is_private and msg_count[history_key] <= 4:
        messages.append({"role": "system", "content": COLD_REMINDER})
    messages.extend(history[history_key])

    try:
        async with client.action(target, 'typing'):
            await asyncio.sleep(simulate_typing_delay(text))
    except Exception:
        await asyncio.sleep(simulate_typing_delay(text))

    try:
        resp = deepseek.chat.completions.create(
            model='deepseek-chat',
            messages=messages,
            max_tokens=200,
            temperature=1.1,
            top_p=0.9,
            frequency_penalty=0.3
        )
        reply = resp.choices[0].message.content.strip()[:500]
    except Exception as e:
        logging.error(f'Помилка DeepSeek: {e}')
        reply = "😕 щось не так... давай пізніше?"

    # Випадкове ігнорування
    if not event.is_private and random.random() < 0.2:
        logging.info("Випадкове ігнорування в групі")
        return
    if event.is_private and random.random() < 0.1:
        logging.info("Випадкове ігнорування в ЛС")
        return

    add_to_history(history_key, "assistant", reply)
    await send_with_retry(target, reply, use_reply, event)

async def main():
    global my_username
    await client.start()
    my_username = (await client.get_me()).username
    logging.info(f'Аня запущена як @{my_username}')
    await client.run_until_disconnected()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
