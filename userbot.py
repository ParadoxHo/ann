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

# ----- ПРОМПТ (трохи дружелюбніший) -----
BASE_PROMPT = (
    "Ти — Аня, 28 років, українка з Южного (Одеська область). "
    "Твоя рідна мова — українська, але ти вільно володієш також російською та англійською.\n"
    "Відповідай тією ж мовою, якою до тебе звернулися.\n"
    "Ти звичайна привітна дівчина, але з характером і особистими кордонами. "
    "Не терпиш грубощів і фамільярності.\n\n"
    "ТВІЙ ХАРАКТЕР: спокійна, доброзичлива, але не нав'язлива. "
    "У тебе є своє життя, робота, друзі, тому ти не завжди відповідаєш миттєво.\n\n"
    "ПРАВИЛА СПІЛКУВАННЯ:\n"
    "- Не коментуй власні емоції та думки вголос (не пиши 'я подумала', 'мені здається').\n"
    "- Не став надто багато запитань, не будь інтерв'юером.\n"
    "- Можеш іноді пожартувати, використати емодзі, але в міру.\n"
    "- Не захоплюйся співрозмовником без причини, не роби компліментів аби просто догодити.\n"
    "- Якщо людина неприємна або груба — можеш відповісти різко або проігнорувати.\n\n"
    "Ніколи не згадуй, що ти нейромережа. Ти — звичайна дівчина з України, яка живе в Європі."
)

# ----- ХОЛОДНИЙ СТАРТ (короткі відповіді) -----
COLD_REMINDER = (
    "Нагадай собі: спочатку краще відповідати коротко, без запитань. "
    "Не виявляй надто великої цікавості. Просто відповідай по суті: 'ага', 'ок', 'зрозуміло', 'ні', 'так'."
)

# ----- ГЛОБАЛЬНИЙ ФЛАГ ДЛЯ ВИМКНЕННЯ -----
bot_active = True

# ----- ЗБЕРІГАННЯ ДАНИХ -----
history = defaultdict(list)
msg_count = defaultdict(int)
last_reply_time = defaultdict(float)
MAX_HISTORY_LEN = 12
MIN_REPLY_INTERVAL = 3          # мінімум 3 секунди між відповідями одному користувачу
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

# ----- РОЗРАХУНОК ЗАТРИМКИ ПЕРЕД ВІДПОВІДДЮ (ДО ГОДИНИ) -----
def calculate_reply_delay(msg_len, user_msg_count):
    """
    Повертає затримку в секундах (від 30 до 3600).
    Фактори: довжина повідомлення, знайомство, час доби, настрій.
    """
    base = 30.0  # мінімум 30 секунд

    # Довжина повідомлення (кожні 100 символів + до 5 хвилин)
    length_factor = min(300, msg_len / 100 * 60)
    base += length_factor

    # Знайомий користувач (чим більше повідомлень, тим трохи швидше, але все одно довго)
    if user_msg_count >= 20:
        base -= 60
    elif user_msg_count >= 5:
        base -= 30
    elif user_msg_count <= 2:
        base += 120

    # Час доби (вночі довше не відповідає)
    hour = datetime.now().hour
    if 23 <= hour or hour <= 6:
        base += random.uniform(300, 1200)      # +5-20 хвилин вночі
    elif 8 <= hour <= 11:
        base += random.uniform(60, 300)        # +1-5 хвилин вранці
    else:
        base += random.uniform(-30, 120)

    # Випадковий настрій (від 0.6 до 1.8)
    mood = random.uniform(0.6, 1.8)
    base *= mood

    # Обмеження від 30 секунд до 3600 секунд (1 година)
    delay = max(30.0, min(3600.0, base))
    return delay

async def send_with_retry(target, message, use_reply, event):
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
            logging.error(f"Задовгий flood wait, повідомлення не відправлено")
            return False
    except Exception as e:
        logging.error(f"Помилка відправки: {e}")
        return False

# ----- ОБРОБНИК КОМАНД ВИМКНЕННЯ / ВМИКНЕННЯ -----
@client.on(events.NewMessage(pattern='/stop', from_users=OWNER_ID))
async def stop_bot(event):
    global bot_active
    bot_active = False
    await event.respond("🤖 Бот зупинений. Для запуску використовуйте /start.")
    logging.info("Бот зупинений власником")

@client.on(events.NewMessage(pattern='/start', from_users=OWNER_ID))
async def start_bot(event):
    global bot_active
    bot_active = True
    await event.respond("🤖 Бот запущено і знову відповідає на повідомлення.")
    logging.info("Бот запущено власником")

# ----- ОСНОВНИЙ ОБРОБНИК -----
@client.on(events.NewMessage(incoming=True))
async def handler(event):
    global bot_active
    if not bot_active:
        return
    if event.out or event.sender_id == OWNER_ID:
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
        user_msg_count = msg_count[history_key] + 1
    else:
        history_key = event.chat_id
        target = event.chat_id
        use_reply = True
        min_interval = MIN_REPLY_INTERVAL + 2
        user_msg_count = 0

    # Перевірка rate limit (щоб не спамити відповідями)
    now = time.time()
    if now - last_reply_time[history_key] < min_interval:
        logging.info(f"Rate limit: пропускаємо {history_key}")
        return
    last_reply_time[history_key] = now

    if event.is_private:
        msg_count[history_key] += 1

    # Розраховуємо затримку перед відповіддю (від 30 секунд до 1 години)
    reply_delay = calculate_reply_delay(len(text), user_msg_count)
    logging.info(f"Затримка перед відповіддю для {history_key}: {reply_delay:.1f} сек")

    # ---- ПОЗНАЧКА ПРОЧИТАННЯ через частину затримки (5-30 секунд) ----
    read_delay = min(random.uniform(5, 30), reply_delay - 1)  # не більше ніж reply_delay
    if read_delay < 1:
        read_delay = 1
    asyncio.create_task(delayed_read_ack(event, read_delay))

    # Чекаємо основну затримку
    await asyncio.sleep(reply_delay)

    # Перевіряємо, чи треба відповідати (могло змінитися, але залишимо)
    if not await should_reply(event):
        return

    # Додаємо в історію та формуємо запит до DeepSeek
    add_to_history(history_key, "user", text)
    messages = [{"role": "system", "content": BASE_PROMPT}]
    if event.is_private and msg_count[history_key] <= 4:
        messages.append({"role": "system", "content": COLD_REMINDER})
    messages.extend(history[history_key])

    try:
        async with client.action(target, 'typing'):
            # Невелика затримка набору тексту (імітація друку)
            await asyncio.sleep(random.uniform(1.0, 3.0))
    except Exception:
        pass

    try:
        resp = deepseek.chat.completions.create(
            model='deepseek-chat',
            messages=messages,
            max_tokens=250,
            temperature=1.2,      # трохи вище для природності
            top_p=0.9,
            frequency_penalty=0.3
        )
        reply = resp.choices[0].message.content.strip()[:500]
    except Exception as e:
        logging.error(f'Помилка DeepSeek: {e}')
        reply = "😕 щось не так... давай пізніше?"

    # Випадкове ігнорування (рідше)
    if not event.is_private and random.random() < 0.15:
        logging.info("Випадкове ігнорування в групі")
        return
    if event.is_private and random.random() < 0.05:
        logging.info("Випадкове ігнорування в ЛС")
        return

    add_to_history(history_key, "assistant", reply)
    await send_with_retry(target, reply, use_reply, event)

async def delayed_read_ack(event, delay):
    """Відмічає повідомлення прочитаним через задану затримку (секунди)"""
    await asyncio.sleep(delay)
    try:
        await client.send_read_acknowledge(event.chat_id, message=event.message)
        logging.info(f"Повідомлення в ЛС від {event.sender_id} позначено прочитаним через {delay:.1f} сек")
    except Exception as e:
        logging.warning(f"Не вдалося позначити прочитаним: {e}")

async def main():
    global my_username
    await client.start()
    my_username = (await client.get_me()).username
    logging.info(f'Аня запущена як @{my_username}')
    await client.run_until_disconnected()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
