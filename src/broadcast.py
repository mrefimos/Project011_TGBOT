import asyncio
import logging
from telethon import events, Button
from telethon.tl.types import PeerUser
from database import get_cursor
from config import TRUSTED_USER_IDS

broadcast_states = {}
user_chats_cache = {}

def register_handlers(client):
    @client.on(events.NewMessage(pattern='/broadcast$'))
    async def broadcast_handler(event):
        if not isinstance(event.peer_id, PeerUser):
            await event.respond("ℹ️ Эта команда доступна только в личном чате с ботом")
            return

        if event.sender_id not in TRUSTED_USER_IDS:
            await event.respond("🚫 У вас нет прав на использование этой команды")
            return

        broadcast_states[event.sender_id] = {'step': 'waiting_message'}
        await event.respond(
            "📝 Введите сообщение для рассылки:",
            buttons=Button.force_reply(single_use=True, selective=True)
        )

    @client.on(events.NewMessage(func=lambda e: (
            e.is_private and
            e.sender_id in broadcast_states and
            broadcast_states[e.sender_id]['step'] == 'waiting_message'
    )))
    async def broadcast_message_handler(event):
        message_text = event.message.text
        if message_text.startswith('/'):
            await event.respond("⚠️ Пожалуйста, введите текст сообщения, а не команду")
            return

        broadcast_states[event.sender_id] = {
            'step': 'waiting_chat_selection',
            'message': message_text
        }

        cursor = get_cursor()
        cursor.execute("SELECT chat_id, chat_name FROM chats")
        chats = cursor.fetchall()

        if not chats:
            await event.respond("ℹ️ В базе нет чатов для рассылки")
            del broadcast_states[event.sender_id]
            return

        user_chats_cache[event.sender_id] = chats

        buttons = []
        for i, (chat_id, chat_name) in enumerate(chats):
            buttons.append([Button.inline(f"{i + 1}. {chat_name}", f"broadcast_chat_{i}")])

        buttons.append([Button.inline("✅ Начать рассылку", "broadcast_start")])
        buttons.append([Button.inline("❌ Отмена", "broadcast_cancel")])

        await event.respond(
            "🔍 Выберите чаты для рассылки:",
            buttons=buttons
        )

    @client.on(events.CallbackQuery(pattern=r'broadcast_(.*)'))
    async def broadcast_button_handler(event):
        user_id = event.sender_id
        data = event.pattern_match.group(1).decode('utf-8')

        if user_id not in broadcast_states:
            await event.answer("🚫 Сессия рассылки завершена. Начните заново.")
            return

        if data == "cancel":
            await event.answer("❌ Рассылка отменена")
            await event.edit("❌ Рассылка отменена")
            if user_id in broadcast_states:
                del broadcast_states[user_id]
            if user_id in user_chats_cache:
                del user_chats_cache[user_id]
            return

        if data == "start":
            state = broadcast_states[user_id]
            if 'selected_chats' not in state or not state['selected_chats']:
                await event.answer("⚠️ Сначала выберите чаты!")
                return

            await event.answer("⏳ Начинаю рассылку...")
            await event.edit("🔄 Идет рассылка сообщения...")

            success = 0
            errors = 0
            message = state['message']

            for chat_id in state['selected_chats']:
                try:
                    await client.send_message(int(chat_id), message)
                    success += 1
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logging.error(f"Ошибка рассылки в {chat_id}: {str(e)}")
                    errors += 1

            report = (
                f"✅ Рассылка завершена!\n"
                f"• Успешно: {success}\n"
                f"• Ошибок: {errors}\n"
                f"• Всего: {success + errors}"
            )

            await event.edit(report)
            if user_id in broadcast_states:
                del broadcast_states[user_id]
            if user_id in user_chats_cache:
                del user_chats_cache[user_id]
            return

        if data.startswith("chat_"):
            try:
                chat_idx = int(data.split('_')[1])
                chats = user_chats_cache.get(user_id, [])

                if not chats or chat_idx >= len(chats):
                    await event.answer("⚠️ Ошибка выбора чата")
                    return

                chat_id, chat_name = chats[chat_idx]

                if 'selected_chats' not in broadcast_states[user_id]:
                    broadcast_states[user_id]['selected_chats'] = []

                if str(chat_id) in broadcast_states[user_id]['selected_chats']:
                    broadcast_states[user_id]['selected_chats'].remove(str(chat_id))
                    await event.answer(f"❌ Чат {chat_name} удален из рассылки")
                else:
                    broadcast_states[user_id]['selected_chats'].append(str(chat_id))
                    await event.answer(f"✅ Чат {chat_name} добавлен в рассылку")

                buttons = []
                selected_chats = broadcast_states[user_id].get('selected_chats', [])

                for i, (cid, cname) in enumerate(chats):
                    prefix = "☑️" if str(cid) in selected_chats else "🔘"
                    buttons.append([Button.inline(f"{prefix} {i + 1}. {cname}", f"broadcast_chat_{i}")])

                buttons.append([Button.inline("✅ Начать рассылку", "broadcast_start")])
                buttons.append([Button.inline("❌ Отмена", "broadcast_cancel")])

                await event.edit(
                    "🔍 Выберите чаты для рассылки:",
                    buttons=buttons
                )
            except Exception as e:
                logging.error(f"Ошибка обработки выбора чата: {str(e)}")
                await event.answer("⚠️ Ошибка обработки запроса")