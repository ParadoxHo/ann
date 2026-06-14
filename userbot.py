import asyncio
import logging
import os
import random
import re
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

# ----- ПРОМПТ (без заохочення коментарів) -----
BASE_PROMPT = (
    "Ти — Аня, 28 років, українка з Южного. Твоя рідна мова — українська, також вільно володієш російською та англійською. "
    "Відповідай ТІЄЮ Ж МОВОЮ, якою написано повідомлення співрозмовника.\n\n"
    "Ти звичайна людина, не ШІ. Твоя відповідь має бути природною, без жодних пояснень, без опису своїх емоцій, без внутрішніх коментарів. "
    "НЕ пиши в дужках свої дії або настрій, наприклад: (сухо), (строго), (нейтрально), (з посмішкою), (задумливо) — це заборонено. "
    "Просто пиши текст відповіді. Не використовуй мета-коментарі. Не аналізуй свої думки вголос.\n\n"
    "ТВІЙ ХАРАКТЕР: стримана, трохи холодна з незнайомцями. Не прагнеш подобатись. Маєш особисті кордони.\n\n"
    "ПРАВИЛА НА ПОЧАТКУ (поки людина тобі не цікава): відповідай коротко, сухо, без запитань, без ініціативи, без жартів, без емодзі. "
    "Можна просто 'ага', 'ок', 'зрозуміло', 'ні', 'так'. Не вступай в довгі бесіди. "
    "Поступово, якщо людина цікава, можеш стати трохи теплішою, але без зайвої емоційності.\n"
    "Ніколи не згадуй, що ти нейромережа. Ти — звичайна дівчина."
)

COLD_REMINDER = (
    "Ти поки не зацікавлена в цій людині. Відповідай максимально сухо, коротко, без запитань. "
    "Не використовуй дужки з коментарями. Просто коротка фраза або одне слово."
)

# ----- ГЛОБАЛЬНИЙ ФЛАГ ДЛЯ ВИМКНЕННЯ -----
bot_active = True

# ----- ЗБЕРІГАННЯ ДАНИХ -----
history = defaultdict(list)
msg_count = defaultdict(int)
last_reply_time = defaultdict(float)
MAX_HISTORY_LEN = 12
MIN_REPLY_INTERVAL = 3
my_username = None

def clean_reply(text: str) -> str:
    """Видаляє режисерські ремарки на початку повідомлення: (сухо), [строго] тощо"""
    # Видаляємо (текст) або [текст] на початку рядка, можливо з пробілами після
    text = re.sub(r'^\s*[\(\[]\s*[^\)\]]+\s*[\)\]]\s*', '', text)
    # Також видаляємо, якщо ремарка всередині, але краще не ризикувати
    return text.strip()

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
            logging.error(f"Задовгий flood wait ({wait_time} сек), повідомлення не відправлено")
            return False
    except Exception as e:
        logging.error(f"Помилка відправки: {e}")
        return False

def calculate_read_delay(event, will_reply, msg_len, user_msg_count):
    base = 2.0
    if will_reply:
        base += random.uniform(-1, 5)
    else:
        base += random.uniform(30, 300)
    length_factor = min(300, msg_len / 100 * 60)
    base += length_factor
    if user_msg_count >= 20:
        base -= 60
    elif user_msg_count >= 5:
        base -= 30
    elif user_msg_count <= 2:
        base += 90
    hour = datetime.now().hour
    if 23 <= hour or hour <= 6:
        base += random.uniform(300, 1200)
    elif 8 <= hour <= 11:
        base += random.uniform(60, 300)
    else:
        base += random.uniform(-30, 60)
    base *= random.uniform(0.5, 1.5)
    return max(2.0, min(3600.0, base))

async def delayed_read_ack(event, delay, user_id):
    await asyncio.sleep(delay)
    try:
        await client.send_read_acknowledge(event.chat_id, message=event.message)
        logging.info(f"Позначка прочитання для {user_id} відправлена через {delay:.1f} сек")
    except Exception as e:
        logging.warning(f"Не вдалося позначити прочитаним: {e}")

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
    await event.respond("🤖 Бот запущено.")
    logging.info("Бот запущено власником")

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

    if event.is_private:
        delay = calculate_read_delay(event, True, len(text), user_msg_count)
        logging.info(f"Заплановано прочитання для {event.sender_id} через {delay:.1f} сек")
        asyncio.create_task(delayed_read_ack(event, delay, event.sender_id))

    now = time.time()
    if now - last_reply_time[history_key] < min_interval:
        logging.info(f"Rate limit: пропускаємо {history_key}")
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
        reply = resp.choices[0].message.content.strip()
        # Очищаємо від режисерських ремарок
        reply = clean_reply(reply)
        # Якщо після очищення порожньо, ставимо нейтральну відповідь
        if not reply:
            reply = "ага"
        reply = reply[:500]
    except Exception as e:
        logging.error(f'Помилка DeepSeek: {e}')
        reply = "😕 щось не так... давай пізніше?"

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
