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

# ----- ОСНОВНОЙ ПРОМПТ (холодная и равнодушная девушка) -----
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

# Дополнительное напоминание для самых первых сообщений
COLD_REMINDER = (
    "Напомни себе: ты пока не заинтересована в этом человеке. "
    "Отвечай максимально сухо, односложно, без вопросов и эмоций. "
    "Не поддерживай беседу, если она тебя не цепляет. Лучше промолчать или ответить 'ага', 'ок', 'понятно'."
)

# ----- ХРАНЕНИЕ ИСТОРИИ И СТАТУСА -----
history = defaultdict(list)          # история сообщений (role, content)
msg_count = defaultdict(int)         # количество сообщений от пользователя (для холодного старта)
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

    # Определяем ключ пользователя/чата
    if event.is_private:
        history_key = event.sender_id
        target = event.sender_id
        use_reply = False
    else:
        history_key = event.chat_id
        target = event.chat_id
        use_reply = True

    # Увеличиваем счётчик сообщений от этого пользователя (только в ЛС)
    if event.is_private:
        msg_count[history_key] += 1

    add_to_history(history_key, "user", text)

    # Формируем сообщения для DeepSeek
    messages = [{"role": "system", "content": BASE_PROMPT}]

    # Если это ЛС и количество сообщений от пользователя <= 4 (очень холодно)
    if event.is_private and msg_count[history_key] <= 4:
        messages.append({"role": "system", "content": COLD_REMINDER})

    messages.extend(history[history_key])

    # Имитация печати
    try:
        async with client.action(target, 'typing'):
            await asyncio.sleep(simulate_typing_delay(text))
    except Exception as e:
        await asyncio.sleep(simulate_typing_delay(text))

    try:
        resp = deepseek.chat.completions.create(
            model='deepseek-chat',
            messages=messages,
            max_tokens=200,          # короткие ответы в начале
            temperature=1.1,         # чуть более холодные ответы (ниже = менее креативно)
            top_p=0.9,
            frequency_penalty=0.3
        )
        reply = resp.choices[0].message.content.strip()[:500]  # короткие ответы
        if random.random() < 0.15 and not event.is_private:
            return
        add_to_history(history_key, "assistant", reply)
        if use_reply:
            await event.reply(reply)
        else:
            await event.respond(reply)
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
