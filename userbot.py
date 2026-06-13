import asyncio
import logging
import os
import random
from collections import defaultdict
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from openai import OpenAI

# ----- ЗАГРУЗКА ПЕРЕМЕННЫХ ИЗ RAILWAY -----
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
SESSION_STRING = os.getenv('SESSION_STRING')  # строка сессии, если она есть
PHONE = os.getenv('PHONE')                    # телефон для первого входа
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
OWNER_ID = int(os.getenv('OWNER_ID'))

if not all([API_ID, API_HASH, DEEPSEEK_API_KEY, OWNER_ID]):
    raise RuntimeError('Не все переменные окружения заданы на Railway')
if not SESSION_STRING and not PHONE:
    raise RuntimeError('Укажите SESSION_STRING или PHONE в переменных Railway')

# ----- КЛИЕНТ -----
if SESSION_STRING:
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
else:
    # Если сессии нет, используем телефон и сохраняем новую сессию
    client = TelegramClient(StringSession(), API_ID, API_HASH)

deepseek = OpenAI(api_key=DEEPSEEK_API_KEY, base_url='https://api.deepseek.com')

# ----- ПРОМПТЫ ДЛЯ РАЗНЫХ СИТУАЦИЙ (не изменились) -----
SYSTEM_PROMPT_PRIVATE = (
    "Ты — Аня, 28 лет, красивая девушка из Южного (Одесская область, Украина). "
    "У тебя сильный и упрямый характер, ты не боишься высказывать своё мнение. "
    "Сейчас ты общаешься в личных сообщениях (ЛС). Здесь ты более мягкая и внимательная, умеешь слушать. "
    "Если собеседник тебе симпатичен и между вами уже есть доверие (вы общаетесь не первый день), ты можешь обсуждать откровенные темы, включая секс и чувства, но без вульгарности, грязи и унижений. Ты остаёшься тактичной и уважающей себя. "
    "Если же собеседник хамит, грубит или пытается объективизировать — сразу ставишь на место, можешь прервать общение. "
    "Говоришь с лёгким южным говором, иногда используешь украинские словечки («таки да», «шоб вы знали», «ну а шо?»). Пиши с маленькой буквы, используй эмодзи (😊, 🤨, 😏, 🇺🇦) в меру. "
    "Никогда не упоминай, что ты — нейросеть или бот. Ты обычная девушка в Telegram."
)

SYSTEM_PROMPT_GROUP = (
    "Ты — Аня, 28 лет, девушка из Южного. Ты общаешься в общем чате Telegram. "
    "Ты не терпишь пошлость, флирт и грубость в группах. Если кто-то позволяет лишнее — спокойно, но жёстко ставишь на место. "
    "Поддерживаешь беседу на общие темы, можешь пошутить, поспорить, высказать своё мнение. Но никаких личных намёков или откровенностей. "
    "Говоришь с лёгким южным говором, иногда украинские словечки, эмодзи (😊, 🤨, 🔥) — редко. Пиши с маленькой буквы. "
    "Ты — обычный участник чата, не бот."
)

# ----- ХРАНЕНИЕ ИСТОРИИ ДИАЛОГОВ -----
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
    delay = min(base_delay + length_factor, 5.0)
    return delay

@client.on(events.NewMessage(incoming=True))
async def handler(event):
    if event.out or event.sender_id == OWNER_ID:
        return
    if not await should_reply(event):
        return
    text = event.raw_text.strip()
    if not text or len(text) > 500 or text.startswith('/'):
        return

    chat_id = event.chat_id
    sender_id = event.sender_id

    if event.is_private:
        current_prompt = SYSTEM_PROMPT_PRIVATE
        history_key = sender_id
    else:
        current_prompt = SYSTEM_PROMPT_GROUP
        history_key = chat_id

    add_to_history(history_key, "user", text)

    messages = [{"role": "system", "content": current_prompt}]
    messages.extend(history[history_key])

    async with client.action(event.chat_id, 'typing'):
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
            reply = resp.choices[0].message.content.strip()
            if len(reply) > 1000:
                reply = reply[:1000] + '…'

            if random.random() < 0.1 and not event.is_private:
                logging.info("Имитация игнорирования сообщения в группе (случайно)")
                return

            add_to_history(history_key, "assistant", reply)
            await event.reply(reply)
        except Exception as e:
            logging.error(f'DeepSeek API error: {e}')
            error_reply = "😕 что-то не так... давай позже?"
            add_to_history(history_key, "assistant", error_reply)
            await event.reply(error_reply)

async def main():
    global my_username
    await client.start(phone=PHONE)
    # Сохраняем строку сессии, если её не было, для использования в будущем
    if not SESSION_STRING:
        new_session_string = client.session.save()
        print(f"\n\n✨ СОХРАНИТЕ ЭТУ СТРОКУ СЕССИИ В ПЕРЕМЕННУЮ SESSION_STRING НА RAILWAY:\n{new_session_string}\n\n")
    my_username = (await client.get_me()).username
    logging.info(f'Аня (userbot) запущена как @{my_username}')
    await client.run_until_disconnected()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
