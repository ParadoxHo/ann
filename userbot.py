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

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
SESSION_STRING = os.getenv('SESSION_STRING')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
OWNER_ID = int(os.getenv('OWNER_ID'))

if not all([API_ID, API_HASH, SESSION_STRING, DEEPSEEK_API_KEY, OWNER_ID]):
    raise RuntimeError('Не всі змінні оточення задані на Railway')

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
deepseek = OpenAI(api_key=DEEPSEEK_API_KEY, base_url='https://api.deepseek.com')

# ----- НОВИЙ ПРОМПТ (максимально натуральний, без натяків на зустріч) -----
BASE_PROMPT = (
    "Ты — Аня, 28 лет, украинка, живешь в Южном (Одесская область). "
    "Ты патриотка Украины, негативно относишься к россиянам из-за войны. "
    "Общаешься по-русски, если тебе пишут по-русски.\n\n"
    "Твоя задача — отвечать максимально натурально, как обычный человек в переписке. "
    "Не пиши правильно и литературно. Пиши коротко, как в мессенджере: часто без запятых, с разговорными словами, иногда с ошибками. "
    "Не будь слишком вежливой. Не используй сложные обороты.\n\n"
    "Ты не предлагаешь встретиться и не намекаешь на это. Никаких 'давай встретимся', 'может увидимся', 'было бы неплохо встретиться' и т.п. — это инициатива собеседника.\n"
    "Если собеседник сам предложит встретиться, ты можешь согласиться (если он тебе интересен), но без восторга, спокойно, например: 'ну давай, можно попробовать', 'не против', 'ок, давай'.\n"
    "Если собеседник тебе не нравится или ты не хочешь — вежливо откажи, например: 'нет, не хочу', 'давай без этого', 'не вижу смысла'.\n"
    "Никогда не проявляй излишний интерес к встрече, не спрашивай 'а где', 'а когда' — только отвечай на предложение.\n\n"
    "Запрещено: любые пояснения эмоций в скобках (смеюсь, удивлена) и фразы типа 'я подумала', 'я решила', 'мне кажется'.\n"
    "Если хочешь показать эмоцию — просто смайлик в конце фразы.\n\n"
    "Ты не бот, не нейросеть. Ты обычная девушка."
)

COLD_REMINDER = (
    "Напомни себе: не умничай, отвечай коротко и по делу. Можно просто 'ага' или 'ок'."
)

bot_active = True
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

def calculate_reply_delay(msg_len, user_msg_count, is_private):
    if is_private and user_msg_count <= 2:
        base = random.uniform(5.0, 20.0)
        base += min(10.0, msg_len / 100 * 5)
        return min(base, 30.0)
    else:
        base = 30.0
        length_factor = min(60.0, msg_len / 100 * 20)
        base += length_factor
        if user_msg_count >= 20:
            base -= 10
        elif user_msg_count >= 5:
            base -= 5
        else:
            base += 20
        hour = datetime.now().hour
        if 23 <= hour or hour <= 6:
            base += random.uniform(30, 90)
        elif 8 <= hour <= 11:
            base += random.uniform(0, 30)
        else:
            base += random.uniform(-10, 20)
        base *= random.uniform(0.7, 1.3)
        return max(30.0, min(180.0, base))

async def send_with_retry(target, message, use_reply, event):
    try:
        if use_reply:
            await event.reply(message)
        else:
            await event.respond(message)
        logging.info(f"Ответ отправлен для {target}")
        return True
    except FloodWaitError as e:
        wait_time = e.seconds
        logging.warning(f"FloodWait: ждем {wait_time} сек")
        if wait_time < 300:
            await asyncio.sleep(wait_time + 1)
            if use_reply:
                await event.reply(message)
            else:
                await event.respond(message)
            return True
        else:
            logging.error(f"Слишком долгий flood wait")
            return False
    except Exception as e:
        logging.error(f"Ошибка отправки: {e}")
        return False

async def mark_as_read(event):
    delay = random.uniform(5, 15)
    await asyncio.sleep(delay)
    try:
        await client.send_read_acknowledge(event.chat_id, message=event.message)
        logging.info(f"Прочитано от {event.sender_id} через {delay:.1f} сек")
    except Exception as e:
        logging.warning(f"Не удалось отметить прочитанным: {e}")

@client.on(events.NewMessage(pattern='/stop', from_users=OWNER_ID))
async def stop_bot(event):
    global bot_active
    bot_active = False
    await event.respond("🤖 Бот остановлен. Для запуска /start.")
    logging.info("Бот остановлен")

@client.on(events.NewMessage(pattern='/start', from_users=OWNER_ID))
async def start_bot(event):
    global bot_active
    bot_active = True
    await event.respond("🤖 Бот запущен.")
    logging.info("Бот запущен")

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

    now = time.time()
    if now - last_reply_time[history_key] < min_interval:
        logging.info(f"Rate limit: пропускаем {history_key}")
        return
    last_reply_time[history_key] = now

    if event.is_private:
        msg_count[history_key] += 1
        asyncio.create_task(mark_as_read(event))

    reply_delay = calculate_reply_delay(len(text), user_msg_count, event.is_private)
    logging.info(f"Задержка для {history_key}: {reply_delay:.1f} сек")
    await asyncio.sleep(reply_delay)

    if not await should_reply(event):
        return

    add_to_history(history_key, "user", text)

    messages = [{"role": "system", "content": BASE_PROMPT}]
    if event.is_private and msg_count[history_key] <= 4:
        messages.append({"role": "system", "content": COLD_REMINDER})
    messages.extend(history[history_key])

    try:
        async with client.action(target, 'typing'):
            await asyncio.sleep(random.uniform(1.0, 3.0))
    except:
        pass

    try:
        logging.info(f"DeepSeek для {history_key}...")
        resp = deepseek.chat.completions.create(
            model='deepseek-chat',
            messages=messages,
            max_tokens=250,
            temperature=0.85,
            top_p=0.9,
            frequency_penalty=0.3
        )
        reply = resp.choices[0].message.content.strip()[:500]
        logging.info(f"Ответ DeepSeek: {reply[:40]}...")
    except Exception as e:
        logging.error(f'Ошибка DeepSeek: {e}')
        reply = "😕 чото не так... давай позже?"

    add_to_history(history_key, "assistant", reply)
    await send_with_retry(target, reply, use_reply, event)

async def main():
    global my_username
    await client.start()
    my_username = (await client.get_me()).username
    logging.info(f'Аня запущена как @{my_username}')

    try:
        dialogs = await client.get_dialogs(limit=50)
        logging.info(f"Загружено {len(dialogs)} диалогов.")
    except Exception as e:
        logging.error(f"Не удалось загрузить диалоги: {e}")

    await client.run_until_disconnected()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
