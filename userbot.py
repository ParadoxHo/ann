import asyncio
import logging
import os
import random
from collections import defaultdict
from telethon import TelegramClient, events
from telethon.sessions import StringSession
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

# ----- ПРОМПТЫ -----
SYSTEM_PROMPT_PRIVATE = (
    "Ты — Аня, 28 лет, красивая девушка из Южного (Одесская область, Украина). "
    "У тебя сильный и упрямый характер, ты не боишься высказывать своё мнение. "
    "Сейчас ты общаешься в личных сообщениях (ЛС). Здесь ты более мягкая и внимательная, умеешь слушать. "
    "Если собеседник тебе симпатичен и между вами уже есть доверие (вы общаетесь не первый день), ты можешь обсуждать откровенные темы, включая секс и чувства, но без вульгарности, грязи и унижений. "
    "Если же собеседник хамит — сразу ставишь на место. Говоришь с лёгким южным говором, иногда украинские словечки. Пиши с маленькой буквы, используй эмодзи в меру. "
    "Никогда не упоминай, что ты — нейросеть или бот."
)

SYSTEM_PROMPT_GROUP = (
    "Ты — Аня, 28 лет, девушка из Южного. Общаешься в общем чате Telegram. "
    "Не терпишь пошлость, флирт и грубость. Поддерживаешь беседу, можешь пошутить, поспорить. "
    "Никаких личных намёков. Говоришь с лёгким южным говором, эмодзи редко. Пиши с маленькой буквы. "
    "Ты — обычный участник чата, не бот."
)

history = defaultdict(list)
MAX_HISTORY_LEN = 12
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

@client.on(events.NewMessage(incoming=True))
async def handler(event):
    if event.out or event.sender_id == OWNER_ID:
        return
    if not await should_reply(event):
        return
    text = event.raw_text.strip()
    if not text or len(text) > 500 or text.startswith('/'):
        return

    # Определяем цель для действия "печатает" и ключ истории
    if event.is_private:
        current_prompt = SYSTEM_PROMPT_PRIVATE
        history_key = event.sender_id
        target = event.sender_id
    else:
        current_prompt = SYSTEM_PROMPT_GROUP
        history_key = event.chat_id
        target = event.chat_id

    add_to_history(history_key, "user", text)

    messages = [{"role": "system", "content": current_prompt}] + history[history_key]

    # Имитация печати с обработкой ошибок
    try:
        async with client.action(target, 'typing'):
            await asyncio.sleep(simulate_typing_delay(text))
    except Exception as e:
        logging.warning(f"Не удалось отправить 'typing' для {target}: {e}")
        await asyncio.sleep(simulate_typing_delay(text))

    try:
        resp = deepseek.chat.completions.create(
            model='deepseek-chat',
            messages=messages,
            max_tokens=350,
            temperature=1.3,
            top_p=0.9,
            frequency_penalty=0.3
        )
        reply = resp.choices[0].message.content.strip()[:1000]
        if random.random() < 0.1 and not event.is_private:
            return
        add_to_history(history_key, "assistant", reply)
        await event.reply(reply)
    except Exception as e:
        logging.error(f'DeepSeek error: {e}')
        error_reply = "😕 что-то не так... давай позже?"
        add_to_history(history_key, "assistant", error_reply)
        await event.reply(error_reply)

async def main():
    global my_username
    await client.start()
    my_username = (await client.get_me()).username
    logging.info(f'Аня запущена как @{my_username}')
    await client.run_until_disconnected()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
