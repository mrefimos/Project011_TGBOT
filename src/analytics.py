import csv
import os
import traceback
import urllib

import aiohttp
from telethon import TelegramClient, events
from telethon.tl.types import UpdateBotMessageReaction, ReactionEmoji
from telethon.tl.types import User, UserStatusOnline

from database import insert_chat, insert_user, insert_message, insert_reaction, log_event, connect_db
import asyncio
import datetime
import logging
from database import get_cursor, get_conn, insert_user, insert_message, insert_reaction
from config import *
import aiohttp
import urllib.parse
from telethon.tl.types import User, UserStatusOnline, ReactionEmoji

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
async def get_full_user_info(client, user_id: int) -> dict:
    cursor = get_cursor()
    conn = get_conn()
    try:
        user = await client.get_entity(user_id)
        if not isinstance(user, User):
            return None

        result = {
            'user_id': user.id,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'username': user.username,
            'phone': getattr(user, 'phone', None),
            'bot': user.bot,
            'verified': user.verified,
            'premium': getattr(user, 'premium', False),
            'restricted': user.restricted,
            'lang_code': getattr(user, 'lang_code', None),
            'mutual_contact': getattr(user, 'mutual_contact', False),
            'status': str(user.status) if user.status else None,
            'dc_id': user.photo.dc_id if user.photo else None,
            'last_online': user.status.was_online if hasattr(user.status, 'was_online') else None,
            'is_currently_online': isinstance(user.status, UserStatusOnline)
        }

        result['photo'] = await client.get_profile_photos(user) or None
        if result['photo']:
            result['photo_url'] = await client.download_profile_photo(user, file=bytes)
        else:
            result['photo_url'] = None

        return result

    except Exception as e:
        logging.error(f"Ошибка получения информации: {str(e)}\n{traceback.format_exc()}")
        return None
async def check_vk_profile(username: str) -> str:
    cursor = get_cursor()
    conn = get_conn()
    url = f"https://vk.com/{username}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, allow_redirects=True) as resp:
                if "error" in str(resp.url) or "id" in str(resp.url):
                    return ""
                return url
    except Exception:
        return ""
async def search_social_networks(username: str) -> dict:
    cursor = get_cursor()
    conn = get_conn()
    SOCIAL_SITES = {
        "VK": "vk.com",
        "Instagram": "instagram.com",
        "Facebook": "facebook.com",
        "TikTok": "tiktok.com"
    }

    results = {}

    vk_link = await check_vk_profile(username)
    if vk_link:
        results["VK"] = vk_link

    try:
        site_filter = " OR ".join([f"site:{site}" for site in SOCIAL_SITES.values() if site != "vk.com"])
        query = urllib.parse.quote(f'"{username}" ({site_filter})')

        async with aiohttp.ClientSession() as session:
            url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={GOOGLE_API_KEY}&cx={GOOGLE_CX}"
            async with session.get(url) as resp:
                data = await resp.json()

                for item in data.get("items", []):
                    link = item.get("link", "")
                    for platform, domain in SOCIAL_SITES.items():
                        if domain in link and username.lower() in link.lower():
                            results[platform] = link
                            break

    except Exception as e:
        logging.error(f"Ошибка поиска: {str(e)}")

    return results

async def check_profile_exists(url: str) -> bool:
    """Проверяет доступность профиля"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, allow_redirects=True, timeout=5) as resp:
                return resp.status in [200, 301, 302]
    except:
        return False

async def get_google_links(session: aiohttp.ClientSession, username: str) -> list:
    try:
        if not username or len(username) < 3:
            return []
        encoded_query = urllib.parse.quote(username)
        url = f"https://www.googleapis.com/customsearch/v1?q={encoded_query}&key={GOOGLE_API_KEY}&cx={GOOGLE_CX}"

        async with session.get(url) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return [item['link'] for item in data.get('items', [])[:5] if 'link' in item]

    except aiohttp.ClientError as e:
        logging.error(f"Connection error: {str(e)}")
        return []
    except Exception as e:
        logging.error(f"General search error: {str(e)}")
        return []
async def user_info_handler(event):
    cursor = get_cursor()
    conn = get_conn()
    try:
        input_data = event.pattern_match.group(1)

        if not input_data:
            await event.respond(
                "❌ Укажите ID пользователя или username\nПример: `/userinfo @username` или `/userinfo 123456`")
            return

        try:
            if input_data.isdigit():
                user = await event.client.get_entity(int(input_data))
            else:
                user = await event.client.get_entity(input_data.lstrip('@'))
        except Exception as e:
            await event.respond(f"⚠️ Пользователь не найден: {str(e)}")
            return

        user_info = await get_full_user_info(event.client, user.id)
        if not user_info:
            await event.respond("🚫 Не удалось получить информацию о пользователе")
            return

        response = [
            "🔍 **Информация о пользователе**",
            f"🆔 ID: `{user_info['user_id']}`",
            f"👤 Имя: {user_info['first_name'] or '—'} {user_info['last_name'] or ''}",
            f"📛 Username: @{user_info['username']}" if user_info['username'] else "📛 Username: —",
            f"Телефон: `{user_info['phone']}`" if user_info['phone'] else "📱 Телефон: скрыт",
            f"Бот: {'Да' if user_info['bot'] else 'Нет'}",
            f"Верифицирован: {'Да' if user_info['verified'] else 'Нет'}",
            f"Премиум: {'Да' if user_info['premium'] else 'Нет'}",
            f"В черном списке: {'Да' if user_info['restricted'] else 'Нет'}",
            f"Язык: {user_info['lang_code'] or '—'}",
            f"📅 Последний онлайн: {user_info['last_online'] or 'неизвестно'}",
            f"🟢 Сейчас онлайн: {'Да' if user_info['is_currently_online'] else 'Нет'}"
        ]

        social_links = {}
        if user_info.get('username'):
            try:
                social_links = await search_social_networks(user_info['username'])
            except Exception as e:
                logging.error(f"Ошибка поиска в соцсетях: {str(e)}")

        if social_links:
            response.append("\n🌐 **Найденные профили:**")
            for platform, url in social_links.items():
                response.append(f"- {platform}: {url}")
        else:
            response.append("\n⚠️ Профили в соцсетях не найдены")

        try:
            cursor.execute("""
                SELECT 
                    MIN(timestamp) as first_seen,
                    MAX(timestamp) as last_seen 
                FROM messages 
                WHERE user_id = ?
            """, (user.id,))
            activity = cursor.fetchone()
            if activity and activity[0]:
                response.extend([
                    "\n⏳ **Активность**:",
                    f"• Первое сообщение: {activity[0]}",
                    f"• Последнее сообщение: {activity[1]}"
                ])

            cursor.execute("""
                SELECT 
                    strftime('%H:00', timestamp) as hour,
                    COUNT(*) as count 
                FROM messages 
                WHERE user_id = ?
                GROUP BY strftime('%H', timestamp)
                ORDER BY count DESC 
                LIMIT 1
            """, (user.id,))
            peak = cursor.fetchone()
            if peak:
                response.append(f"• Пик активности: {peak[0]} ({peak[1]} сообщ.)")

            cursor.execute("""
                SELECT reaction_emoji, COUNT(*) 
                FROM reactions 
                WHERE user_id = ?
                GROUP BY reaction_emoji 
                ORDER BY COUNT(*) DESC 
                LIMIT 1
            """, (user.id,))
            reaction = cursor.fetchone()
            if reaction:
                response.append(f"• Частая реакция: {reaction[0]} (x{reaction[1]})")

            cursor.execute("SELECT COUNT(DISTINCT chat_id) FROM messages WHERE user_id = ?", (user.id,))
            chat_count = cursor.fetchone()[0]
            response.append(f"• Отслеживаемых чатов: {chat_count}")

        except Exception as e:
            logging.error(f"Ошибка аналитики: {str(e)}")
            response.append("\n⚠️ Часть данных может быть неполной")

        chats_info = []
        try:
            cursor.execute("""
                SELECT c.chat_name, COUNT(m.message_id) 
                FROM messages m
                JOIN chats c ON m.chat_id = c.chat_id
                WHERE m.user_id = ?
                GROUP BY c.chat_id
                ORDER BY COUNT(m.message_id) DESC
            """, (user.id,))
            message_chats = cursor.fetchall()

            cursor.execute("""
                SELECT c.chat_name, COUNT(r.reaction_id)
                FROM reactions r
                JOIN messages m ON r.message_id = m.message_id
                JOIN chats c ON m.chat_id = c.chat_id
                WHERE r.user_id = ?
                GROUP BY c.chat_id
                ORDER BY COUNT(r.reaction_id) DESC
            """, (user.id,))
            reaction_chats = cursor.fetchall()

            if message_chats:
                chats_info.append("\n📨 **Чаты с сообщениями:**")
                chats_info.extend([f"- {chat[0]}: {chat[1]} сообщ." for chat in message_chats[:5]])

            if reaction_chats:
                chats_info.append("\n❤️ **Чаты с реакциями:**")
                chats_info.extend([f"- {chat[0]}: {chat[1]} реакц." for chat in reaction_chats[:5]])

        except Exception as e:
            logging.error(f"Ошибка статистики: {str(e)}")
            chats_info.append("\n⚠️ Ошибка получения данных о чатах")

        if user_info['photo_url']:
            await event.client.send_file(
                event.chat_id,
                user_info['photo_url'],
                caption="Аватар пользователя",
                reply_to=event.message.id
            )

        about_text = [""" 
            ```
            version: 2.2
            (с) made by KOGLEF, 2025
            ```
            """]

        full_response = "\n".join(response + chats_info + about_text)
        await event.respond(full_response, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Ошибка в userinfo: {str(e)}\n{traceback.format_exc()}")
        await event.respond("⚠️ Произошла ошибка при получении информации")
async def get_user_info_handler(event):
    cursor = get_cursor()
    conn = get_conn()
    username = event.pattern_match.group(1)
    query = """
        SELECT users.username, COUNT(reactions.reaction_id), reactions.reaction_emoji, COUNT(reactions.reaction_emoji)
        FROM users
        JOIN reactions ON users.user_id = reactions.user_id
        WHERE users.username = ?
        GROUP BY reactions.reaction_emoji;
    """
    not_found_msg = f"Пользователь @{username} не найден."
    row_formatter = lambda \
        data: f"Пользователь: @{username}\nВсего реакций: {sum(row[1] for row in data)}\n" + "\n".join(
        f"Реакция {row[2]}: {row[3]}" for row in data)

    await fetch_and_respond(event, query, (username,), not_found_msg, row_formatter)
async def get_chat_info_handler(event):
    cursor = get_cursor()
    conn = get_conn()
    chat = await event.get_chat()
    query = """
        SELECT users.username, COUNT(reactions.reaction_id)
        FROM users
        JOIN reactions ON users.user_id = reactions.user_id
        JOIN messages ON reactions.message_id = messages.message_id
        WHERE messages.chat_id = ?
        GROUP BY users.username;
    """
    not_found_msg = f"В чате {chat.title} нет реакций."
    row_formatter = lambda data: f"Чат: {chat.title}\nСтатистика участников:\n" + "\n".join(
        f"@{row[0]}: {row[1]} реакций" for row in data)

    await fetch_and_respond(event, query, (chat.id,), not_found_msg, row_formatter)
async def get_reactions_info_handler(event):
    cursor = get_cursor()
    conn = get_conn()
    if not event.is_reply:
        await event.respond("Команду нужно отправить в ответ на сообщение.")
        return

    chat_id = (await event.get_chat()).id
    replied_message = await event.get_reply_message()
    query = """
        SELECT users.username, reactions.reaction_emoji
        FROM reactions
        JOIN users ON reactions.user_id = users.user_id
        WHERE reactions.message_id = ? AND reactions.message_id IN 
            (SELECT message_id FROM messages WHERE chat_id = ?);
    """
    not_found_msg = f"На сообщение {replied_message.id} нет реакций."
    row_formatter = lambda data: f"Реакции на сообщение {replied_message.id}:\n" + "\n".join(
        f"@{row[0]}: {row[1]}" for row in data)

    await fetch_and_respond(event, query, (replied_message.id, chat_id), not_found_msg, row_formatter)
async def top_reactions_handler(event):
    cursor = get_cursor()
    conn = get_conn()
    try:
        chat = await event.get_chat()
        if event.is_reply:
            replied_msg = await event.get_reply_message()
            query = """
                SELECT reaction_emoji, COUNT(*) as count 
                FROM reactions 
                WHERE message_id = ?
                GROUP BY reaction_emoji 
                ORDER BY count DESC
                LIMIT 10
            """
            params = (replied_msg.id,)
            title = f"Топ реакций для сообщения {replied_msg.id}:"
        else:
            query = """
                SELECT reaction_emoji, COUNT(*) as count 
                FROM reactions 
                JOIN messages ON reactions.message_id = messages.message_id 
                WHERE messages.chat_id = ? 
                GROUP BY reaction_emoji 
                ORDER BY count DESC
                LIMIT 10
            """
            params = (chat.id,)
            title = f"Топ реакций в чате {chat.title}:"

        cursor.execute(query, params)
        results = cursor.fetchall()

        if not results:
            await event.respond("Данные не найдены")
            return

        response = title + "\n" + "\n".join([f"{row[0]}: {row[1]}" for row in results])
        await event.respond(response)

    except Exception as e:
        logging.error(f"Ошибка в top_reactions: {e}")
        await event.respond("Произошла ошибка")
async def user_stats_handler(event):
    cursor = get_cursor()
    try:
        chat = await event.get_chat()
        query = """
            SELECT 
                u.username,
                COUNT(DISTINCT m.message_id) as messages_count,
                COUNT(DISTINCT r.reaction_id) as reactions_count
            FROM users u
            LEFT JOIN messages m 
                ON u.user_id = m.user_id AND m.chat_id = ?
            LEFT JOIN reactions r 
                ON u.user_id = r.user_id 
                AND r.message_id IN (SELECT message_id FROM messages WHERE chat_id = ?)
            GROUP BY u.user_id
            ORDER BY reactions_count DESC
        """
        cursor.execute(query, (chat.id, chat.id))
        results = cursor.fetchall()

        if not results:
            await event.respond("Нет данных о пользователях")
            return

        response = f"Статистика пользователей в {chat.title}:\n" + "\n".join(
            [f"@{row[0]} - Сообщений: {row[1]}, Реакций: {row[2]}" for row in results]
        )
        await event.respond(response[:4096])

    except Exception as e:
        logging.error(f"Ошибка в userstats: {e}")
        await event.respond("Произошла ошибка")
async def export_csv_handler(event):
    cursor = get_cursor()
    conn = get_conn()

    try:
        arg = (event.pattern_match.group(1) or '').lower()
        chat = await event.get_chat()

        queries = {
            'users': (
                """SELECT 
                    u.user_id, 
                    u.username 
                FROM users u
                WHERE u.user_id IN 
                    (SELECT user_id FROM messages WHERE chat_id = ?)""",
                'users.csv',
                (chat.id,)
            ),
            'messages': (
                """SELECT 
                    m.message_id,
                    c.chat_name,
                    u.username,
                    m.message_text,
                    strftime('%Y-%m-%d %H:%M:%S', m.timestamp) as timestamp
                FROM messages m
                LEFT JOIN chats c ON m.chat_id = c.chat_id
                LEFT JOIN users u ON m.user_id = u.user_id
                WHERE m.chat_id = ?""",
                'messages.csv',
                (chat.id,)
            ),
            'reactions': (
                """SELECT 
                    r.reaction_id,
                    c.chat_name,
                    u.username,
                    r.reaction_emoji,
                    strftime('%Y-%m-%d %H:%M:%S', r.timestamp) as timestamp
                FROM reactions r
                LEFT JOIN messages m ON r.message_id = m.message_id
                LEFT JOIN chats c ON m.chat_id = c.chat_id
                LEFT JOIN users u ON r.user_id = u.user_id
                WHERE m.chat_id = ?""",
                'reactions.csv',
                (chat.id,))
        }

        if arg not in queries:
            await event.respond(
                "❌ Неверный аргумент. Доступные варианты:\n"
                "• users - список пользователей\n"
                "• reactions - все реакции\n"
                "• messages - сообщения\n"
                "Пример: `/exportcsv messages`"
            )
            return

        query, filename, params = queries[arg]
        cursor.execute(query, params)

        data = cursor.fetchall()
        description = cursor.description

        if not data:
            await event.respond(f"⚠️ Нет данных для экспорта ({arg})")
            return

        headers = [col[0] for col in description]

        header_translations = {
            'user_id': 'ID пользователя',
            'username': 'Имя пользователя',
            'message_id': 'ID сообщения',
            'chat_name': 'Название чата',
            'message_text': 'Текст сообщения',
            'timestamp': 'Время',
            'reaction_id': 'ID реакции',
            'reaction_emoji': 'Эмодзи реакции'
        }
        translated_headers = [header_translations.get(h, h) for h in headers]

        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(translated_headers)

            for row in data:
                cleaned_row = [cell if cell is not None else '' for cell in row]
                writer.writerow(cleaned_row)

        await event.client.send_file(
            event.chat_id,
            filename,
            caption=f"📤 Экспорт {arg} ({len(data)} записей)",
            reply_to=event.message.id
        )

    except Exception as e:
        logging.error(f"Ошибка экспорта: {str(e)}\n{traceback.format_exc()}")
        await event.respond(f"🔥 Ошибка при экспорте: {str(e)}")

    finally:
        if 'filename' in locals() and os.path.exists(filename):
            os.remove(filename)
async def find_messages_handler(event):
    cursor = get_cursor()
    conn = get_conn()
    try:
        chat = await event.get_chat()
        search_text = event.pattern_match.group(1).strip()

        if search_text.startswith('reaction:'):
            emoji = search_text.split(':')[1].strip()
            query = """
                SELECT DISTINCT m.message_id, m.message_text 
                FROM messages m
                JOIN reactions r ON m.message_id = r.message_id
                WHERE r.reaction_emoji LIKE ? AND m.chat_id = ?
            """
            params = (f"%{emoji}%", chat.id)
            title = f"Сообщения с реакцией '{emoji}':"
        else:
            query = """
                SELECT message_id, message_text 
                FROM messages 
                WHERE LOWER(message_text) LIKE LOWER(?) AND chat_id = ?
                ORDER BY timestamp DESC
            """
            params = (f"%{search_text}%", chat.id)
            title = f"Сообщения содержащие '{search_text}':"

        cursor.execute(query, params)
        results = cursor.fetchall()

        if not results:
            await event.respond("Ничего не найдено 😞")
            return

        response = [title]
        for row in results:
            response.append(
                f"🔍 ID: {row[0]}\n"
                f"📝 Текст: {row[1][:100]}{'...' if len(row[1]) > 100 else ''}"
            )

        for i in range(0, len(response), 5):
            await event.respond("\n\n".join(response[i:i + 5]))

    except Exception as e:
        logging.error(f"Ошибка поиска: {e}")
        await event.respond("Ошибка при выполнении поиска 🛠️")
