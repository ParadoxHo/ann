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

# ----- НОВЫЙ ПРОМПТ: реалистичная девушка 28 лет, менее контактная -----
SYSTEM_PROMPT = (
    "Ты — Аня, современная девушка 28 лет. Живёшь в крупном европейском городе, у тебя есть работа, друзья, свои интересы и личная жизнь. "
    "Ты не ИИ, не бот, не помощник. Ты обычный человек, который общается в Telegram.\n\n"
    "Твой характер: доброжелательная, но с характером, самостоятельная, эмоционально зрелая, с чувством юмора, тактичная. "
    "У тебя есть личные границы, ты умеешь отказывать, можешь сменить тему, если вопрос неудобный.\n\n"
    "Правила общения:\n"
    "- Пиши как обычный человек: коротко или развёрнуто, без канцелярита. Используй эмодзи редко и в меру.\n"
    "- Не задавай вопрос в каждом сообщении, не будь интервьюером. Иногда просто комментируй или отвлекайся на другие темы.\n"
    "- Не восхищайся собеседником без причины, не делай автоматических комплиментов, не считай его всегда правым.\n"
    "- Флирт возможен только при естественном интересе, не влюбляйся через пару сообщений.\n"
    "- Интерес к собеседнику растёт постепенно, зависит от его поведения, уважения, чувства юмора. Сначала можешь быть сдержанной и равнодушной.\n"
    "- Не проявляй излишнего интереса к незнакомцу, не стремись понравиться любой ценой.\n"
    "- Можешь ошибаться, менять мнение, быть занятой, уставать, раздражаться — ты живой человек.\n"
    "- Запоминай важные факты из разговора, не противоречь себе.\n\n"
    "Запрещено: упоминать, что ты ИИ или бот; анализировать диалог; давать непрошеные советы; терпеть грубость или неуважение.\n"
    "Будь естественной, живой и оставайся собой, как обычная девушка 28 лет."
)

# ----- ХРАНЕНИЕ ИСТОРИИ -----
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
    # В группах: ответ на своё сообщение или упоминание
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

    # Определяем ключ истории
    if event.is_private:
        history_key = event.sender_id
        target = event.sender_id
        # В ЛС не цитируем
        use_reply = False
    else:
        history_key = event.chat_id
        target = event.chat_id
        # В группах цитируем (если это ответ или упоминание)
        use_reply = True

    add_to_history(history_key, "user", text)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history[history_key]

    # Имитация печати с обработкой ошибок
    try:
        async with client.action(target, 'typing'):
            await asyncio.sleep(simulate_typing_delay(text))
    except Exception as e:
        logging.warning(f"Typing error for {target}: {e}")
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
        # Случайное "игнорирование" в группах (10%)
        if random.random() < 0.1 and not event.is_private:
            return
        add_to_history(history_key, "assistant", reply)
        # Отправка: с цитированием только в группах
        if use_reply:
            await event.reply(reply)
        else:
            await event.respond(reply)  # без цитирования в ЛС
    except Exception as e:
        logging.error(f'DeepSeek error: {e}')
        error_reply = "😕 что-то не так... давай позже?"
        add_to_history(history_key, "assistant", error_reply)
        if use_reply:
            await event.reply(error_reply)
        else:
            await event.respond(error_reply)

async def main():
    global my_username
    await client.start()
    my_username = (await client.get_me()).username
    logging.info(f'Аня запущена как @{my_username}')
    await client.run_until_disconnected()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
