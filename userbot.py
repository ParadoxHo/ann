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
    raise RuntimeError('Не все переменные окружения заданы на Railway')

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
deepseek = OpenAI(api_key=DEEPSEEK_API_KEY, base_url='https://api.deepseek.com')

# ----- ПРОМПТ (холодная девушка) -----
BASE_PROMPT = (
    "Ты — Аня, 28 лет, живёшь в Европе. У тебя своя жизнь, работа, друзья. "
    "Ты не ИИ и не бот, ты обычный человек.\n\n"
    "ТВОЙ ХАРАКТЕР: сдержанная, немного холодная с незнакомцами. Не стремишься понравиться. "
    "У тебя есть личные границы, ты не терпишь фамильярности и пошлости.\n\n"
    "ПРАВИЛА ОБЩЕНИЯ В НАЧАЛЕ (пока человек тебе не интересен):\n"
    "- Отвечай коротко, сухо, без лишних эмоций.\n"
    "- Не задавай вопросов собеседнику.\n"
    "- Не проявляй инициативу, не поддерживай тему, если она тебе не интересна.\n"
    "- Не шути, не используй эмодзи (только если ситуация не требует).\n"
    "- Не восхищайся, не делай комплиментов, не подстраивайся.\n"
    "- Если собеседник тебе неприятен или грубит — можешь ответить резко или замолчать.\n\n"
    "Постепенно, если человек окажется интересным, уважительным и остроумным, ты можешь стать чуть теплее, но не сразу.\n"
    "Никогда не упоминай, что ты нейросеть. Ты — обычная девушка."
)

COLD_REMINDER = (
    "Напомни себе: ты пока не заинтересована в этом человеке. "
    "Отвечай максимально сухо, односложно, без вопросов и эмоций. "
    "Не поддерживай беседу, если она тебя не цепляет. Лучше промолчать или ответить 'ага', 'ок', 'понятно'."
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

def calculate_read_delay(event, will_reply, msg_len, user_msg_count):
    """
    Возвращает задержку в секундах от 2 до 3600 (1 час).
    Влияющие факторы:
    - будет ли ответ (will_reply) -> если да, задержка меньше
    - длина сообщения (msg_len) -> чем длиннее, тем дольше читает
    - количество сообщений от пользователя (user_msg_count) -> чем больше, тем быстрее (привычный собеседник)
    - время суток (чем позже, тем дольше)
    - случайное настроение (mood)
    """
    # Базовое значение (сек)
    base = 2.0

    # 1. Если бот ответит -> быстрее читаем
    if will_reply:
        base += random.uniform(-1, 5)   # быстро, но не мгновенно
    else:
        base += random.uniform(30, 300) # без ответа долго

    # 2. Длина сообщения (каждый 100 символов добавляет до 60 секунд)
    length_factor = min(300, msg_len / 100 * 60)
    base += length_factor

    # 3. Знакомый пользователь (чем больше сообщений, тем быстрее)
    if user_msg_count >= 20:
        base -= 60
    elif user_msg_count >= 5:
        base -= 30
    elif user_msg_count <= 2:
        base += 90

    # 4. Время суток (час на сервере)
    hour = datetime.now().hour
    if 23 <= hour or hour <= 6:  # ночь
        base += random.uniform(300, 1200)  # добавляем 5-20 минут
    elif 8 <= hour <= 11:  # утро, возможно занята
        base += random.uniform(60, 300)
    else:
        base += random.uniform(-30, 60)

    # 5. Случайное настроение (от 0.5 до 1.5)
    mood = random.uniform(0.5, 1.5)
    base *= mood

    # Ограничиваем от 2 секунд до 3600 секунд (1 час)
    delay = max(2.0, min(3600.0, base))
    return delay

async def delayed_read_ack(event, delay, user_id):
    """Отметить сообщение прочитанным через вычисленную задержку"""
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
    if not await should_reply(event):
        # даже если не отвечаем, прочтение может быть
        will_reply = False
    else:
        will_reply = True

    text = event.raw_text.strip()
    if not text or len(text) > 500 or text.startswith('/'):
        return

    # Определяем параметры
    if event.is_private:
        history_key = event.sender_id
        target = event.sender_id
        use_reply = False
        min_interval = MIN_REPLY_INTERVAL
        user_msg_count = msg_count[history_key] + 1  # ещё не увеличили
    else:
        # В группах прочтение не отмечаем, выходим из обработки прочтения
        history_key = event.chat_id
        target = event.chat_id
        use_reply = True
        min_interval = MIN_REPLY_INTERVAL + 2
        user_msg_count = 0  # не используется

    # ----- ОТЛОЖЕННАЯ ОТМЕТКА О ПРОЧТЕНИИ (только в ЛС, динамическая задержка) -----
    if event.is_private:
        delay = calculate_read_delay(
            event=event,
            will_reply=will_reply,
            msg_len=len(text),
            user_msg_count=user_msg_count
        )
        logging.info(f"Запланирована отметка о прочтении для {event.sender_id} через {delay:.1f} сек")
        asyncio.create_task(delayed_read_ack(event, delay, event.sender_id))

    # Rate limit для ответа
    now = time.time()
    if now - last_reply_time[history_key] < min_interval:
        logging.info(f"Rate limit: пропускаем {history_key}, прошло {now - last_reply_time[history_key]:.1f}с")
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
        logging.error(f'DeepSeek error: {e}')
        reply = "😕 что-то не так... давай позже?"

    # Случайное игнорирование
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
