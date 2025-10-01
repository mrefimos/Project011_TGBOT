import asyncio
import csv
import datetime
import logging
import os
import traceback
import urllib
import aiohttp
from telethon import TelegramClient, events
from moderation import (
    mute_handler,
    unmute_handler,
    mute_list_handler,
    kick_handler,
    check_expired_mutes,
    check_user_muted,
    handle_profanity,
    automod_on_handler,
    automod_off_handler
)

from broadcast import *
from analytics import *
from config import *
from admin import *

logging.basicConfig(level=logging.INFO)

client = TelegramClient('session_name', api_id, api_hash)

async def get_user_info(user):
    if user.username:
        return user.username
    name_parts = []
    if user.first_name:
        name_parts.append(user.first_name)
    if user.last_name:
        name_parts.append(user.last_name)
    return " ".join(name_parts).strip() or f"user_{user.id}"
def log_and_print(log_message):
    print(log_message)
    logging.info(log_message)

def format_reaction(reactions):
    return ', '.join(r.emoticon if isinstance(r, ReactionEmoji) else str(r) for r in reactions)

async def get_entity_info(client, peer, actor):
    chat_id = getattr(peer, 'channel_id', None) or getattr(peer, 'user_id', None)
    actor_id = getattr(actor, 'id', None) or getattr(actor, 'user_id', None)
    return await client.get_entity(chat_id), await client.get_entity(actor_id)

@client.on(events.Raw)
async def reaction_handler(event):
    cursor = get_cursor()
    conn = get_conn()
    try:
        if isinstance(event, UpdateBotMessageReaction):
            if not hasattr(event, 'actor') or not hasattr(event.actor, 'user_id'):
                logging.error("Не удалось получить информацию о пользователе")
                return

            user_id = event.actor.user_id
            user = await client.get_entity(user_id)
            chat = await client.get_entity(event.peer)
            chat_title = getattr(chat, 'title', 'Private Chat')
            username = await get_user_info(user)

            def format_reactions(reactions):
                return [
                    r.emoticon if isinstance(r, ReactionEmoji) else f"custom_{r.document_id}"
                    for r in (reactions or [])
                ]

            new = format_reactions(event.new_reactions)
            old = format_reactions(event.old_reactions)

            if new and not old:
                log_msg = f"🟢 Пользователь {username} добавил {', '.join(new)} к сообщению {event.msg_id} в {chat_title}"
            elif old and not new:
                log_msg = f"🔴 Пользователь {username} удалил {', '.join(old)} с сообщения {event.msg_id} в {chat_title}"
            else:
                log_msg = (
                    f"🟡 Пользователь {username} изменил реакции на сообщение {event.msg_id} в {chat_title}\n"
                    f"Старые: {', '.join(old)}\nНовые: {', '.join(new)}"
                )

            log_and_print(log_msg)
            await insert_user(cursor, user.id, username)
            cursor.execute("DELETE FROM reactions WHERE message_id = ? AND user_id = ?", (event.msg_id, user.id))

            if new:
                await insert_reaction(cursor, event.msg_id, user.id, ", ".join(new))

            if not cursor.execute("SELECT 1 FROM messages WHERE message_id = ?", (event.msg_id,)).fetchone():
                await insert_message(cursor, event.msg_id, chat.id, user.id, "[Сообщение не отслеживалось]")

    except Exception as e:
        logging.error(f"Ошибка обработки реакции: {str(e)}\n{traceback.format_exc()}")

@client.on(events.NewMessage)
async def new_message_handler(event):
    cursor = get_cursor()
    conn = get_conn()
    if event.message.out:
        return
    if await check_user_muted(event):
        return
    if event.message.action or event.message.text.startswith('/'):
        return

    try:
        chat = await event.get_chat()
        sender = await event.get_sender()

        chat_title = getattr(chat, 'title', 'Private Chat')
        username = await get_user_info(sender)

        await insert_chat(cursor, chat.id, getattr(chat, 'title', 'Private Chat'))
        await insert_user(cursor, sender.id, username)

        log_msg = (
            f"Новое сообщение {event.message.id} "
            f"в чате {chat_title} от {username}: "
            f"{event.message.message}"
        )
        log_and_print(log_msg)
        await insert_message(cursor, event.message.id, chat.id, sender.id, event.message.message)
        await handle_profanity(event)
        await log_event(cursor, "NewMessage", log_msg)

    except Exception as e:
        logging.error(f"Ошибка при обработке сообщения: {e}")

async def fetch_and_respond(event, query, params, not_found_msg, row_formatter):
    cursor = get_cursor()
    conn = get_conn()
    if event.message.out:
        return
    try:
        cursor.execute(query, params)
        data = cursor.fetchall()
        if not data:
            await event.respond(not_found_msg)
        else:
            await event.respond(row_formatter(data))

    except Exception as e:
        logging.error(f"Ошибка при выполнении запроса: {e}")

@client.on(events.NewMessage(pattern=r'/help'))
async def help_handler(event):
    help_text = """
    🛠️ **Доступные команды**:

    **Модерация**:
    /mute [@юзер] [время] [причина] - Замутить пользователя 
    /unmute [@юзер] - Снять мут
    /mutelist - Список активных мутов
    /kick [@юзер] [причина] - Исключить пользователя
    
    **Аналитика**:
    /find [текст] - Поиск сообщений
    /topreactions - Топ-10 реакций
    /userstats - Активность участников
    /exportcsv [тип] - Экспорт данных
    /getuserinfo @юзер - Статистика пользователя
    
    ⚙️ Примеры модерации:
    ```
    /mute @spammer 2h Спам
    /unmute @reformed_user
    /kick @troublemaker Оскорбления
  
    version: 2.2
    (с) made by KOGLEF, 2025
    ```
    """
    await event.respond(help_text, parse_mode='Markdown')

async def migrate_existing_users():
    cursor = get_cursor()
    conn = get_conn()
    try:
        cursor.execute("SELECT user_id FROM users WHERE username = 'Unknown'")
        users_to_update = cursor.fetchall()

        for (user_id,) in users_to_update:
            try:
                user = await client.get_entity(user_id)
                new_username = await get_user_info(user)
                await insert_user(cursor, user_id, new_username)
                logging.info(f"Обновлен пользователь {user_id}: {new_username}")
            except Exception as e:
                logging.error(f"Ошибка обновления {user_id}: {str(e)}")

        cursor.connection.commit()
    except Exception as e:
        logging.error(f"Ошибка миграции: {str(e)}")

@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    cursor = get_cursor()
    conn = get_conn()
    try:
        user_data = cursor.fetchone()

        if not user_data or user_data[0]:
            welcome_text = """
        👋 Привет! Я исконно-русский сэр Коглеф!

        🔍 Я помогу вам:
        - Модерировать чаты
        - Анализировать активность участников
        - Собирать статистику

        📚 Используйте /help для просмотра команд
        💡 Настройте меня под свои нужды!

        ❤️ С любовью, команда КОГЛЕФ """
            await event.respond(welcome_text)

            await insert_user(cursor, event.sender.id, await get_user_info(await event.get_sender()))
            cursor.execute(
                "UPDATE users SET first_launch = 0 WHERE user_id = ?",
                (event.sender.id,)
            )
            conn.commit()
    except Exception as e:
        logging.error(f"Ошибка приветствия: {str(e)}")

client.add_event_handler(
    mute_handler,
    events.NewMessage(pattern=r'/mute\s+(\S+)\s+(\d+[mhd])\s*(.*)')
)

client.add_event_handler(
    unmute_handler,
    events.NewMessage(pattern=r'/unmute\s+(\S+)')
)

client.add_event_handler(
    mute_list_handler,
    events.NewMessage(pattern=r'/mutelist')
)

client.add_event_handler(
    kick_handler,
    events.NewMessage(pattern=r'/kick\s+(\S+)\s*(.*)')
)

client.add_event_handler(
    user_info_handler,
    events.NewMessage(pattern=r'/userinfo(?:\s+(@?\w+))?')
)

client.add_event_handler(
    get_user_info_handler,
    events.NewMessage(pattern=r'/getuserinfo (.+)')
)

client.add_event_handler(
    get_chat_info_handler,
    events.NewMessage(pattern=r'/getchatinfo')
)

client.add_event_handler(
    get_reactions_info_handler,
    events.NewMessage(pattern=r'/getreactionsinfo')
)

client.add_event_handler(
    top_reactions_handler,
    events.NewMessage(pattern=r'/topreactions')
)

client.add_event_handler(
    automod_on_handler,
    events.NewMessage(pattern=r'/automod_on')
)

client.add_event_handler(
    automod_off_handler,
    events.NewMessage(pattern=r'/automod_off')
)

client.add_event_handler(
    user_stats_handler,
    events.NewMessage(pattern=r'/userstats')
)

client.add_event_handler(
    export_csv_handler,
    events.NewMessage(pattern=r'/exportcsv(?:\s+(.+))?')
)

client.add_event_handler(
    find_messages_handler,
    events.NewMessage(pattern=r'/find (.*)')
)

client.add_event_handler(
    getdb_handler,
    events.NewMessage(pattern=r'/getdb$')
)

client.add_event_handler(
    dbtable_handler,
    events.NewMessage(pattern=r'dbtable_(\w+)')
)




async def main():
    connect_db()
    await client.start()
    register_handlers(client)
    register_admin_handlers(client)
    asyncio.create_task(check_expired_mutes(client))
    print("Клиент запущен")
    await client.run_until_disconnected()

if __name__ == '__main__':
    with client:
        client.loop.run_until_complete(main())
