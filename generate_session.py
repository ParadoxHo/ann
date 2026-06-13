import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

# Замените на свои данные (получить на my.telegram.org)
API_ID = 123456        # ваш api_id
API_HASH = 'ваш_api_hash'
PHONE = '+380XXXXXXXXX'   # ваш номер телефона

async def main():
    async with TelegramClient(StringSession(), API_ID, API_HASH) as client:
        await client.start(phone=PHONE)
        session_string = client.session.save()
        print('Скопируйте эту строку и добавьте в переменную SESSION_STRING на Railway:')
        print(session_string)

if __name__ == '__main__':
    asyncio.run(main())
