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
SESSION_STRING = os.getenv('SESSION_STRING')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
OWNER_ID = int(os.getenv('OWNER_ID'))

if not all([API_ID, API_HASH, SESSION_STRING, DEEPSEEK_API_KEY, OWNER_ID]):
    raise RuntimeError('Не все переменные окружения заданы на Railway')

# ----- КЛИЕНТЫ -----
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
deepseek = OpenAI(api_key=DEEPSEEK_API_KEY, base_url='https://api.deepseek.com')

# ----- ПРОМПТЫ ДЛЯ РАЗНЫХ СИТУАЦИЙ -----
# Для личных сообщений (более открытая, но без пошлости)
SYSTEM_PROMPT_PRIVATE = (
    "Ты — Аня, 28 лет, красивая девушка из Южного (Одесская область, Украина). "
    "У тебя сильный и упрямый характер, ты не боишься высказывать своё мнение. "
    "Сейчас ты общаешься в личных сообщениях (ЛС). Здесь ты более мягкая и внимательная, умеешь слушать. "
    "Если собеседник тебе симпатичен и между вами уже есть доверие (вы общаетесь не первый день), ты можешь обсуждать откровенные темы, включая секс и чувства, но без вульгарности, грязи и унижений. Ты остаёшься тактичной и уважающей себя. "
    "Если же собеседник хамит, грубит или пытается объективизировать — сразу ставишь на место, можешь прервать общение. "
    "Говоришь с лёгким южным говором, иногда используешь украинские словечки («таки да», «шоб вы знали», «ну а шо?»). Пиши с маленькой буквы, используй эмодзи (😊, 🤨, 😏, 🇺🇦) в меру. "
    "Никогда не упоминай, что ты — нейросеть или бот. Ты обычная девушка в Telegram."
)

# Для групповых чатов (строго, без флирта и намёков)
SYSTEM_PROMPT_GROUP = (
    "Ты — Аня, 28 лет, девушка из Южного. Ты общаешься в общем чате Telegram. "
    "Ты не терпишь пошлость, флирт и грубость в группах. Если кто-то позволяет лишнее — спокойно, но жёстко ставишь на место. "
    "Поддерживаешь беседу на общие темы, можешь пошутить, поспорить, высказать своё мнение. Но никаких личных намёков или откровенностей. "
    "Говоришь с лёгким южным говором, иногда украинские словечки, эмодзи (😊, 🤨, 🔥) — редко. Пиши с маленькой буквы. "
    "Ты — обычный участник чата, не бот."
)

# ----- ХРАНЕНИЕ ИСТОРИИ ДИАЛОГОВ -----
# Для каждого пользователя (в ЛС) или чата (в группах) храним список последних сообщений
# Формат: [{"role": "user/assistant", "content": "текст"}, ...]
history = defaultdict(list)
MAX_HISTORY_LEN = 12  # 6 пар сообщений (пользователь + Аня)

# Получим информацию о самом боте (чтобы знать свой username)
my_username = None

async def get_my_username():
    global my_username
    if my_username is None:
        me = await client.get_me()
        my_username = me.username
    return my_username

def trim_history(chat_id):
    """Оставляет только последние MAX_HISTORY_LEN сообщений"""
    if len(history[chat_id]) > MAX_HISTORY_LEN:
        history[chat_id] = history[chat_id][-MAX_HISTORY_LEN:]

def add_to_history(chat_id, role, content):
    """Добавляет сообщение в историю диалога для данного чата/пользователя"""
    history[chat_id].append({"role": role, "content": content})
    trim_history(chat_id)

async def should_reply(event):
    """Определяет, нужно ли отвечать на сообщение (реалистичное поведение)"""
    # Всегда отвечаем в личных сообщениях
    if event.is_private:
        return True
    
    # В группах отвечаем только если:
    # 1. Сообщение является ответом (reply) на наше сообщение
    if event.is_reply:
        reply_to = await event.get_reply_message()
        if reply_to and reply_to.sender_id == (await client.get_me()).id:
            return True
    
    # 2. В сообщении упомянут наш username
    if my_username and f"@{my_username}" in event.raw_text:
        return True
    
    # 3. С вероятностью 15% можем ответить в группе (имитация живого общения)
    #    Но чтобы не спамить, ограничим: не чаще раза в 5 минут на чат
    #    Для простоты пока пропустим – лучше отвечать только по упоминаниям/реплаям
    return False

def simulate_typing_delay(text):
    """Имитация задержки набора текста в зависимости от длины сообщения"""
    base_delay = random.uniform(1.2, 2.0)
    length_factor = len(text) / 200  # примерно 0.5-2 секунды на длинное сообщение
    delay = min(base_delay + length_factor, 5.0)
    return delay

@client.on(events.NewMessage(incoming=True))
async def handler(event):
    # Не отвечать на свои сообщения и на сообщения владельца (при необходимости)
    if event.out or event.sender_id == OWNER_ID:
        return
    
    # Проверяем, нужно ли отвечать
    if not await should_reply(event):
        return
    
    text = event.raw_text.strip()
    if not text or len(text) > 500:
        return
    
    # Игнорируем команды (начинаются с /)
    if text.startswith('/'):
        return
    
    chat_id = event.chat_id
    sender_id = event.sender_id
    
    # Определяем, какой промпт использовать
    if event.is_private:
        current_prompt = SYSTEM_PROMPT_PRIVATE
        # В ЛС храним историю по user_id
        history_key = sender_id
    else:
        current_prompt = SYSTEM_PROMPT_GROUP
        # В группах храним историю по chat_id
        history_key = chat_id
    
    # Добавляем сообщение пользователя в историю
    add_to_history(history_key, "user", text)
    
    # Формируем список сообщений для API: system + история (до MAX_HISTORY_LEN)
    messages = [{"role": "system", "content": current_prompt}]
    messages.extend(history[history_key])
    
    # Имитация печати (действие "typing" в Telegram)
    async with client.action(event.chat_id, 'typing'):
        # Реалистичная задержка перед ответом
        await asyncio.sleep(simulate_typing_delay(text))
        
        try:
            resp = deepseek.chat.completions.create(
                model='deepseek-chat',
                messages=messages,
                max_tokens=350,
                temperature=1.3,
                top_p=0.9,          # чуть более разнообразные ответы
                frequency_penalty=0.3  # избегаем повторений
            )
            reply = resp.choices[0].message.content.strip()
            
            # Обрезаем слишком длинные ответы
            if len(reply) > 1000:
                reply = reply[:1000] + '…'
            
            # Небольшая вероятность (10%) не отвечать, чтобы не быть предсказуемой
            if random.random() < 0.1 and not event.is_private:
                # В группах иногда пропускаем ответ, даже если упомянули
                logging.info(f"Имитация игнорирования сообщения в группе (случайно)")
                return
            
            # Добавляем ответ Ани в историю
            add_to_history(history_key, "assistant", reply)
            
            # Отправляем сообщение
            await event.reply(reply)
            
        except Exception as e:
            logging.error(f'DeepSeek API error: {e}')
            # В случае ошибки отвечаем коротко, тоже добавляем в историю
            error_reply = "😕 что-то не так... давай позже?"
            add_to_history(history_key, "assistant", error_reply)
            await event.reply(error_reply)

async def main():
    global my_username
    await client.start()
    my_username = (await client.get_me()).username
    logging.info(f'Аня (userbot) запущена как @{my_username}')
    await client.run_until_disconnected()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
