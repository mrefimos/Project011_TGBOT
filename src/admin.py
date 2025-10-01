import asyncio
import logging
import os
from telethon import events, Button
from telethon.tl.types import PeerUser
from database import get_cursor, get_conn
from config import TRUSTED_USER_IDS
import csv
from database import *


def register_admin_handlers(client):
    @client.on(events.NewMessage(pattern='/admin$'))
    async def admin_handler(event):
        if not isinstance(event.peer_id, PeerUser):
            return
        if event.sender_id not in TRUSTED_USER_IDS:
            await event.respond("🚫 Доступ только для доверенных лиц")
            return

        cursor = get_cursor()
        cursor.execute("SELECT chat_id, chat_name FROM chats")
        chats = cursor.fetchall()

        buttons = []
        for chat_id, chat_name in chats:
            buttons.append([Button.inline(f"{chat_name}", f"admin_chat_{chat_id}")])

        await event.respond("🔧 Управление чатами:", buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r'admin_chat_(\d+)'))
    async def admin_chat_handler(event):
        chat_id = int(event.pattern_match.group(1))
        user_id = event.sender_id

        if user_id not in TRUSTED_USER_IDS:
            await event.answer("🚫 Доступ запрещен")
            return

        try:
            chat = await event.client.get_entity(chat_id)
            perms = await event.client.get_permissions(chat, 'me')
            is_admin = perms.is_admin
            admin_status = "✅ Админ" if is_admin else "❌ Не админ"
        except Exception:
            admin_status = "❌ Ошибка проверки"

        cursor = get_cursor()
        cursor.execute("SELECT automod_enabled FROM chat_settings WHERE chat_id=?", (chat_id,))
        setting = cursor.fetchone()
        automod_status = "🟢 Вкл" if (setting and setting[0]) else "🔴 Выкл"

        buttons = [
            [Button.inline(f"Автомод: {automod_status}", f"toggle_automod_{chat_id}")],
            [Button.inline("📊 Статистика", f"stats_{chat_id}")],
            [Button.inline("📤 Экспорт данных", f"export_{chat_id}")],
            [Button.inline("🔙 Назад", "back_admin")]
        ]

        await event.edit(
            f"💬 Чат: {getattr(chat, 'title', '')}\n"
            f"🆔 ID: {chat_id}\n"
            f"🤖 Статус бота: {admin_status}",
            buttons=buttons
        )

    @client.on(events.CallbackQuery(pattern=r'toggle_automod_(\d+)'))
    async def toggle_automod_handler(event):
        chat_id = int(event.pattern_match.group(1))
        cursor = get_cursor()

        cursor.execute("SELECT automod_enabled FROM chat_settings WHERE chat_id=?", (chat_id,))
        setting = cursor.fetchone()
        new_status = not setting[0] if setting else False

        set_automod_status(cursor, chat_id, new_status)
        get_conn().commit()

        await event.answer(f"Автомодерация {'включена' if new_status else 'выключена'}!")
        await admin_chat_handler(event)

    @client.on(events.CallbackQuery(pattern=r'export_(\d+)'))
    async def export_handler(event):
        chat_id = int(event.pattern_match.group(1))
        cursor = get_cursor()

        # Список таблиц для экспорта
        buttons = [
            [Button.inline("📨 Сообщения", f"export_msgs_{chat_id}")],
            [Button.inline("❤️ Реакции", f"export_reacts_{chat_id}")],
            [Button.inline("👤 Пользователи", f"export_users_{chat_id}")],
            [Button.inline("🔙 Назад", f"admin_chat_{chat_id}")]
        ]
        await event.edit("📤 Выберите данные для экспорта:", buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r'export_(msgs|reacts|users)_(\d+)'))
    async def export_data_handler(event):
        data_type = event.pattern_match.group(1)
        chat_id = int(event.pattern_match.group(2))
        cursor = get_cursor()

        if data_type == 'msgs':
            cursor.execute("SELECT * FROM messages WHERE chat_id=?", (chat_id,))
            filename = f"messages_{chat_id}.csv"
        elif data_type == 'reacts':
            cursor.execute(
                "SELECT * FROM reactions WHERE message_id IN (SELECT message_id FROM messages WHERE chat_id=?)",
                (chat_id,))
            filename = f"reactions_{chat_id}.csv"
        else:
            cursor.execute("SELECT * FROM users WHERE user_id IN (SELECT user_id FROM messages WHERE chat_id=?)",
                           (chat_id,))
            filename = f"users_{chat_id}.csv"

        data = cursor.fetchall()
        if not data:
            await event.answer("ℹ️ Нет данных для экспорта")
            return

        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([col[0] for col in cursor.description])
            writer.writerows(data)

        await event.client.send_file(
            event.chat_id,
            filename,
            caption=f"Экспорт данных ({data_type})"
        )
        os.remove(filename)
        await event.answer()

    @client.on(events.CallbackQuery(pattern='back_admin'))
    async def back_admin_handler(event):
        await admin_handler(event)


async def getdb_handler(event):
    if not isinstance(event.peer_id, PeerUser):
        return
    if event.sender_id not in TRUSTED_USER_IDS:
        await event.respond("🚫 Доступ только для доверенных лиц")
        return

    cursor = get_cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]

    buttons = []
    for table in tables:
        buttons.append([Button.inline(table, f"dbtable_{table}")])

    await event.respond("📦 Выберите таблицу для экспорта:", buttons=buttons)


async def dbtable_handler(event):
    table_name = event.pattern_match.group(1)
    cursor = get_cursor()

    try:
        cursor.execute(f"SELECT * FROM {table_name}")
        with open(f"{table_name}.csv", 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([col[0] for col in cursor.description])
            writer.writerows(cursor.fetchall())

        await event.client.send_file(
            event.chat_id,
            f"{table_name}.csv",
            caption=f"Экспорт таблицы {table_name}"
        )
        os.remove(f"{table_name}.csv")
    except Exception as e:
        await event.answer(f"⚠️ Ошибка: {str(e)}")