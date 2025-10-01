import asyncio
import csv
import datetime
import logging
import os
import traceback
import asyncio
import datetime
import logging
from database import *
from config import *
from database import insert_chat, insert_user, insert_message, insert_reaction, log_event, connect_db


async def is_admin(client, chat_id, user_id):
    try:
        participant = await client.get_permissions(chat_id, user_id)
        return participant.is_admin
    except Exception:
        return False
def parse_duration(duration_str):
    unit = duration_str[-1]
    value = int(duration_str[:-1])

    if unit == 'm':
        return datetime.timedelta(minutes=value)
    elif unit == 'h':
        return datetime.timedelta(hours=value)
    elif unit == 'd':
        return datetime.timedelta(days=value)
    else:
        return datetime.timedelta(minutes=30)
async def resolve_user(client, user_ref):
    try:
        if user_ref.startswith('@'):
            return await client.get_entity(user_ref)
        elif user_ref.isdigit():
            return await client.get_entity(int(user_ref))
        else:
            try:
                return await client.get_entity(int(user_ref))
            except ValueError:
                return None
    except Exception:
        return None
async def mute_handler(event):
    cursor = get_cursor()
    conn = get_conn()
    try:
        sender = await event.get_sender()
        chat = await event.get_chat()

        if not await is_admin(event.client, chat.id, sender.id):
            await event.respond("❌ Только администраторы могут использовать эту команду")
            return

        user_ref = event.pattern_match.group(1).strip()
        time_input = event.pattern_match.group(2).strip()
        reason = event.pattern_match.group(3).strip() or "Нарушение правил"

        user = await resolve_user(event.client, user_ref)
        if not user:
            await event.respond("⚠️ Пользователь не найден")
            return

        if user.id == CREATOR_ID:
            await event.respond("🚫 Я не могу поступить так со своим создателем! 😢")
            return

        if await is_admin(event.client, chat.id, user.id):
            await event.respond("⛔ Нельзя замутить администратора")
            return

        duration = parse_duration(time_input)
        end_time = datetime.datetime.now() + duration

        cursor.execute(
            "INSERT INTO mutes (chat_id, user_id, end_time, reason, moderator_id) VALUES (?, ?, ?, ?, ?)",
            (chat.id, user.id, end_time, reason, sender.id)
        )
        conn.commit()

        await event.client.edit_permissions(
            entity=chat.id,
            user=user.id,
            until_date=end_time,
            send_messages=False,
            send_media=False,
            send_stickers=False,
            send_gifs=False,
            send_games=False,
            send_polls=False
        )

        time_format = end_time.strftime('%d.%m.%Y %H:%M')
        response = (
            f"🔇 Пользователь @{user.username} отлетает в мут! "
            f"⏳ Время окончания: {time_format}\n"
            f"Причина: {reason}\n"

            f"Модератор: @{sender.username}"
        )

        await event.respond(response)

    except Exception as e:
        logging.error(f"Ошибка мута: {str(e)}\n{traceback.format_exc()}")
        await event.respond("⚠️ Ошибка при выполнении команды")
async def unmute_handler(event):
    cursor = get_cursor()
    conn = get_conn()
    try:
        sender = await event.get_sender()
        chat = await event.get_chat()

        if not await is_admin(event.client, chat.id, sender.id):
            await event.respond("❌ Только администраторы могут использовать эту команду")
            return

        user_ref = event.pattern_match.group(1).strip()
        user = await resolve_user(event.client, user_ref)
        if not user:
            await event.respond("⚠️ Пользователь не найден")
            return

        if user.id == CREATOR_ID:
            await event.respond("🚫 Мой создатель всегда свободен! 😇")
            return

        await event.client.edit_permissions(
            entity=chat.id,
            user=user.id,
            send_messages=True,
            send_media=True,
            send_stickers=True,
            send_gifs=True,
            send_games=True,
            send_polls=True
        )
        cursor.execute(
            "UPDATE mutes SET end_time = datetime('now') WHERE chat_id = ? AND user_id = ? AND end_time > datetime('now')",
            (chat.id, user.id)
        )
        conn.commit()

        await event.respond(f"🔊 Пользователь {user.first_name} размучен")

    except Exception as e:
        logging.error(f"Ошибка размута: {str(e)}\n{traceback.format_exc()}")
        await event.respond("⚠️ Ошибка при выполнении команды")

async def check_mute(event):
    cursor = get_cursor()
    conn = get_conn()
    if event.message.out:
        return
    if await check_user_muted(event):
        return
    if event.message.action or event.message.text.startswith('/'):
        return
    try:
        if event.message.action:
            return
        if not hasattr(event, 'sender') or not hasattr(event.sender, 'id'):
            return
        if await is_admin(event.client, event.chat_id, event.sender.id):
            return
        cursor.execute(
            "SELECT 1 FROM mutes WHERE chat_id = ? AND user_id = ? AND end_time > datetime('now')",
            (event.chat_id, event.sender.id)
        )
        if cursor.fetchone():
            await event.delete()
            try:
                await event.respond(
                    f"⚠️ {event.sender.first_name}, вы замучены и не можете отправлять сообщения",
                    reply_to=event.message.id
                )
            except:
                pass

    except Exception as e:
        logging.error(f"Ошибка проверки мута: {str(e)}\n{traceback.format_exc()}")
async def check_user_muted(event):
    cursor = get_cursor()
    conn = get_conn()
    if event.sender.id == CREATOR_ID:
        return False
    cursor.execute(
        "SELECT 1 FROM mutes WHERE chat_id = ? AND user_id = ? AND end_time > datetime('now')",
        (event.chat_id, event.sender.id)
    )
    if cursor.fetchone():
        await event.delete()
        try:
            await event.respond(f"⚠️ {event.sender.first_name}, вы в муте!", reply_to=event.message.id)
        except:
            pass
        return True
    return False
async def check_expired_mutes(client):
    cursor = get_cursor()
    conn = get_conn()
    while True:
        try:
            cursor.execute(
                "SELECT mute_id, chat_id, user_id FROM mutes WHERE end_time <= datetime('now')"
            )
            expired = cursor.fetchall()

            for mute in expired:
                mute_id, chat_id, user_id = mute
                try:
                    await client.edit_permissions(
                        entity=chat_id,
                        user=user_id,
                        send_messages=True
                    )
                except Exception as e:
                    logging.error(f"Ошибка снятия мута {mute_id}: {str(e)}")
                cursor.execute("DELETE FROM mutes WHERE mute_id = ?", (mute_id,))

            conn.commit()
            await asyncio.sleep(60)

        except Exception as e:
            logging.error(f"Ошибка проверки мутов: {str(e)}")
            await asyncio.sleep(60)
async def mute_list_handler(event):
    cursor = get_cursor()
    conn = get_conn()
    try:
        sender = await event.get_sender()
        chat = await event.get_chat()

        if not await is_admin(event.client, chat.id, sender.id):
            await event.respond("❌ Только администраторы могут использовать эту команду")
            return

        cursor.execute(
            "SELECT user_id, end_time, reason FROM mutes WHERE chat_id = ? AND end_time > datetime('now')",
            (chat.id,)
        )
        mutes = cursor.fetchall()

        if not mutes:
            await event.respond("ℹ️ В этом чате нет активных мутов")
            return

        response = ["🔇 Активные муты:"]

        for user_id, end_time, reason in mutes:
            try:
                user = await event.client.get_entity(user_id)
                user_name = f"{user.first_name} {user.last_name}" if user.last_name else user.first_name
                time_str = datetime.datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S").strftime('%d.%m.%Y %H:%M')
                response.append(f"👤 {user_name} (@{user.username or 'нет'})\n"
                                f"⌛ До: {time_str}\n"
                                f"📝 Причина: {reason}\n"
                                f"──")
            except:
                response.append(f"👤 Неизвестный пользователь (ID: {user_id})\n"
                                f"⌛ До: {end_time}\n"
                                f"📝 Причина: {reason}\n"
                                f"──")

        full_text = "\n".join(response)
        for i in range(0, len(full_text), 4096):
            await event.respond(full_text[i:i + 4096])

    except Exception as e:
        logging.error(f"Ошибка списка мутов: {str(e)}\n{traceback.format_exc()}")
        await event.respond("⚠️ Произошла ошибка")
async def kick_handler(event):
    cursor = get_cursor()
    conn = get_conn()
    try:
        sender = await event.get_sender()
        chat = await event.get_chat()

        if not await is_admin(event.client, chat.id, sender.id):
            await event.respond("❌ Только администраторы могут использовать эту команду")
            return

        user_ref = event.pattern_match.group(1).strip()
        reason = event.pattern_match.group(2).strip() or "Нарушение правил"

        user = await resolve_user(event.client, user_ref)
        if not user:
            await event.respond("⚠️ Пользователь не найден")
            return

        if user.id == CREATOR_ID:
            await event.respond("🚫 Никогда не предам своего создателя! 🤖❤️")
            return

        if await is_admin(event.client, chat.id, user.id):
            await event.respond("⛔ Нельзя кикнуть администратора")
            return

        await event.client.kick_participant(chat.id, user.id)

        response = (
            f"👢 Пользователь {user.first_name} был исключен\n"
            f"Причина: {reason}\n"
            f"Модератор: {sender.first_name}"
        )

        await event.respond(response)

    except Exception as e:
        logging.error(f"Ошибка кика: {str(e)}\n{traceback.format_exc()}")
        await event.respond("⚠️ Ошибка при выполнении команды")

PROFANITY_LIST = ["коля", "дурак", "кис"]

async def handle_profanity(event):
    cursor = get_cursor()
    conn = get_conn()
    automod_enabled = get_automod_status(cursor, event.chat_id)

    if not automod_enabled:
        logging.info("Автомодерация отключена, пропускаем проверку")
        return

    if event.message.action or event.message.text.startswith('/'):
        return

    try:
        logging.info(f"Обработка сообщения в чате {event.chat_id}: {event.message.text}")

        message_text = event.message.text.lower()
        contains_profanity = any(word in message_text for word in PROFANITY_LIST)

        if contains_profanity:
            violation_count = await add_violation(cursor, event.sender.id, event.chat_id)
            logging.info(f"Violation count: {violation_count} for user: {event.sender.id}")
            deleted = False
            muted = False
            response = ""

            try:
                await event.delete()
                deleted = True
            except Exception:
                pass

            try:
                if not await is_admin(event.client, event.chat_id, event.sender.id):
                    mute_duration = datetime.timedelta(hours=violation_count)
                    end_time = datetime.datetime.now() + mute_duration

                    cursor.execute(
                        "INSERT INTO mutes (chat_id, user_id, end_time, reason, moderator_id) VALUES (?, ?, ?, ?, ?)",
                        (event.chat_id, event.sender.id, end_time, f"Авто-мут за нарушение #{violation_count}",
                         event.client.get_me().id)
                    )
                    conn.commit()

                    await event.client.edit_permissions(
                        entity=event.chat_id,
                        user=event.sender.id,
                        until_date=end_time,
                        send_messages=False
                    )
                    muted = True
            except Exception:
                pass

            user_name = event.sender.first_name
            if deleted and muted:
                response = f"🤬 {user_name}! Твоё сквернословие стёрто, а ты заперт в подвале на {violation_count} часов! Позорище!"
            elif deleted and not muted:
                response = f"🤬 {user_name}! Стер твою мерзость, но запереть не смог! Знай, что ты - позор рода!"
            elif not deleted and muted:
                response = f"🤬 {user_name}! Не смог стереть твою пакость, но зато запру в подвале на {violation_count} часов! Стыдись!"
            else:
                response = f"🤬 {user_name}! Ни стереть твою мерзость, ни запереть тебя! Но знай - ты опозорил род до пятого колена!"

            await event.respond(response)


    except Exception as e:

        logging.error(f"Ошибка обработки нарушения: {str(e)}")
        logging.error(traceback.format_exc())


async def automod_toggle_handler(event, enable: bool):
    cursor = get_cursor()
    conn = get_conn()
    sender = await event.get_sender()
    if sender.id not in TRUSTED_USER_IDS:
        await event.respond("🚫 Эта команда доступна только доверенным пользователям!")
        return

    chat = await event.get_chat()

    set_automod_status(cursor, chat.id, enable)
    logging.info(
        f"Установлен статус автомодерации: {'ВКЛ' if enable else 'ВЫКЛ'} в чате: {chat.id} пользователем: {sender.id}")

    status = "включена" if enable else "выключена"
    await event.respond(f"🛡️ Автомодерация в этом чате {status}!")

async def automod_on_handler(event):
    await automod_toggle_handler(event, True)

async def automod_off_handler(event):
    await automod_toggle_handler(event, False)