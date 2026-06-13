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
    raise RuntimeError('Не все переменные окружения заданы на Railway')

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
deepseek = OpenAI(api_key=DEEPSEEK_API_KEY, base_url='https://api.deepseek.com')

# ----- ПРОМПТ (без внутренних комментариев) -----
BASE_PROMPT = (
    "Ты — Аня, 28 лет, живёшь в Европе. У тебя своя жизнь, работа, друзья. "
    "Ты обычный человек, не бот и не ИИ. Ты общаешься в Telegram.\n\n"
    "Твой характер: сдержанная, равнодушная к незнакомцам. Ты не стремишься понравиться. "
    "У тебя есть личные границы, ты не терпишь фамильярности и пошлости.\n\n"
    "Правила общения:\n"
    "- Отвечай коротко, без лишних эмоций.\n"
    "- Никогда не задавай вопросов собеседнику.\n"
    "- Не проявляй инициативу.\n"
    "- Не используй эмодзи.\n"
    "- Не делай комплиментов, не восхищайся.\n"
    "- Если собеседник грубит — можешь ответить резко или игнорировать.\n"
    "- Никогда не описывай свои действия в скобках. Не пиши 'холодно', 'равнодушно', 'пауза', 'задумалась' и т.п.\n"
    "- Никогда не упоминай, что ты нейросеть, бот, программа, алгоритм.\n"
    "- Ты просто девушка, которая не очень заинтересована в разговоре.\n\n"
    "Запрещено: писать что-либо в скобках, комментировать своё состояние, объяснять свои ответы."
)

COLD_REMINDER = (
    "Ты не заинтересована в этом человеке. Отвечай максимально сухо, односложно, без вопросов и эмоций. "
    "Не пиши в скобках никаких пояснений."
)

# ----- ХРАНЕНИЕ ДАННЫХ -----
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
        logging.warning(f"FloodWait: надо подождать {wait_time} секунд")
        if wait_time < 300:
            await asyncio.sleep(wait_time + 1)
            if use_reply:
                await event.reply(message)
            else:
                await event.respond(message)
            return True
        else:
            logging.error(f"Слишком долгий flood wait ({wait_time} сек), сообщение не отправлено")
            return False
    except Exception as e:
        logging.error(f"Ошибка отправки: {e}")
        return False

def clean_reply(text: str) -> str:
    """Удаляет из ответа любые скобки с комментариями и фразы-маркеры поведения"""
    # Удаляем всё, что в круглых скобках, включая сами скобки
    text = re.sub(r'\([^)]*\)', '', text)
    # Удаляем всё, что в квадратных скобках
    text = re.sub(r'\[[^\]]*\]', '', text)
    # Удаляем фразы типа "холодно:", "равнодушно:", "пауза" и т.п. в начале строки
    text = re.sub(r'^(холодно|равнодушно|сухо|без эмоций|пауза|задумалась|пожимаю плечами)\s*[:;,-]?\s*', '', text, flags=re.IGNORECASE)
    # Удаляем такие же фразы в середине после точки или запятой
    text = re.sub(r'[.,!;]\s*(холодно|равнодушно|сухо|пауза)\s*[:;,-]?\s*', '. ', text, flags=re.IGNORECASE)
    # Убираем лишние пробелы
    text = re.sub(r'\s+', ' ', text).strip()
    # Если после чистки строка пустая, возвращаем "..."
    if not text:
        text = "..."
    return text

def calculate_read_delay(event, will_reply, msg_len, user_msg_count):
    """Возвращает задержку в секундах от 2 до 3600 (1 час)"""
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
    mood = random.uniform(0.5, 1.5)
    base *= mood
    delay = max(2.0, min(3600.0, base))
    return delay

async def delayed_read_ack(event, delay, user_id):
    await asyncio.sleep(delay)
    try:
        await client.send_read_acknowledge(event.chat_id, message=event.message)
        logging.info(f"Отметка о прочтении для {user_id} отправлена через {delay:.1f} сек")
    except Exception as e:
        logging.warning(f"Не удалось отметить прочитанным: {e}")

@client.on(events.NewMessage(incoming=True))
async def handler(event):
    if event.out or event.sender_id == OWNER_ID:
        return

    text = event.raw_text.strip()
    if not text or len(text) > 500 or text.startswith('/'):
        return

    # Для ЛС считаем, нужно ли ответить (will_reply)
    will_reply = await should_reply(event)

    # Определяем параметры для rate limit и истории
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

    # Отложенная отметка о прочтении (только ЛС)
    if event.is_private:
        delay = calculate_read_delay(event, will_reply, len(text), user_msg_count)
        logging.info(f"Запланирована отметка о прочтении для {event.sender_id} через {delay:.1f} сек")
        asyncio.create_task(delayed_read_ack(event, delay, event.sender_id))

    # Rate limit (только для ответов)
    now = time.time()
    if now - last_reply_time[history_key] < min_interval:
        logging.info(f"Rate limit: пропускаем {history_key}")
        return
    last_reply_time[history_key] = now

    if event.is_private:
        msg_count[history_key] += 1

    add_to_history(history_key, "user", text)

    messages = [{"role": "system", "content": BASE_PROMPT}]
    if event.is_private and msg_count[history_key] <= 4:
        messages.append({"role": "system", "content": COLD_REMINDER})
    messages.extend(history[history_key])

    # Имитация печати
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
        reply = clean_reply(reply)   # фильтрация мета-комментариев
    except Exception as e:
        logging.error(f'DeepSeek error: {e}')
        reply = "😕 что-то не так... давай позже?"

    # Случайное игнорирование (не отвечаем, но прочтение уже запланировано)
    if not event.is_private and random.random() < 0.2:
        logging.info("Случайное игнорирование в группе")
        return
    if event.is_private and random.random() < 0.1:
        logging.info("Случайное игнорирование в ЛС")
        return

    add_to_history(history_key, "assistant", reply)
    await send_with_retry(target, reply, use_reply, event)

async def main():
    global my_username
    await client.start()
    my_username = (await client.get_me()).username
    logging.info(f'Аня запущена как @{my_username}')
    await client.run_until_disconnected()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
