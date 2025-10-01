import sqlite3
import logging

_conn = None
_cursor = None
def connect_db():
    global _conn, _cursor
    _conn = sqlite3.connect('../telegram_bot.db')
    _cursor = _conn.cursor()
    create_tables(_cursor, _conn)

    _cursor.execute('''
               CREATE TABLE IF NOT EXISTS violations (
                   violation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                   user_id INTEGER NOT NULL,
                   chat_id INTEGER NOT NULL,
                   violation_count INTEGER DEFAULT 1,
                   timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   FOREIGN KEY (user_id) REFERENCES users(user_id)
               )
           ''')

    _cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id BIGINT PRIMARY KEY,
            automod_enabled BOOLEAN DEFAULT 1
        );
    """)

    _cursor.execute('''
            CREATE TABLE IF NOT EXISTS mutes (
                mute_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                end_time TIMESTAMP NOT NULL,
                reason TEXT,
                moderator_id INTEGER,
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        ''')

    _conn.commit()

    return _conn, _cursor

def get_cursor():
    return _cursor

def get_conn():
    return _conn
def create_tables(cursor, conn):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            chat_id BIGINT PRIMARY KEY,
            chat_name TEXT
        );
    """)
    cursor.execute('''
            CREATE TABLE IF NOT EXISTS mutes (
                mute_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                end_time TIMESTAMP NOT NULL,
                reason TEXT,
                moderator_id INTEGER,
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        ''')

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            message_id BIGINT PRIMARY KEY,
            chat_id BIGINT,
            user_id BIGINT,
            message_text TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (chat_id) REFERENCES chats (chat_id),
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reactions (
            reaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id BIGINT,
            user_id BIGINT,
            reaction_emoji TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (message_id) REFERENCES messages (message_id),
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            description TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_text ON messages(message_text)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reactions_emoji ON reactions(reaction_emoji)")

    conn.commit()

async def insert_chat(cursor, chat_id, chat_name):
    cursor.execute(
        "INSERT OR IGNORE INTO chats (chat_id, chat_name) VALUES (?, ?)",
        (chat_id, chat_name)
    )
    cursor.connection.commit()

async def insert_user(cursor, user_id, username):
    """Обновляет username даже при изменении"""
    cursor.execute(
        """INSERT OR REPLACE INTO users (user_id, username) 
        VALUES (?, ?)""",
        (user_id, username)
    )
    cursor.connection.commit()

async def insert_reaction(cursor, message_id, user_id, reaction_emoji):
    if isinstance(reaction_emoji, list):
        reaction_emoji = ", ".join(reaction_emoji)

    cursor.execute(
        "DELETE FROM reactions WHERE message_id = ? AND user_id = ?",
        (message_id, user_id)
    )
    cursor.execute(
        "INSERT INTO reactions (message_id, user_id, reaction_emoji) VALUES (?, ?, ?)",
        (message_id, user_id, reaction_emoji)
    )
    cursor.connection.commit()


async def insert_message(cursor, message_id, chat_id, user_id, message_text):
    cursor.execute("SELECT 1 FROM chats WHERE chat_id = ?", (chat_id,))
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO chats (chat_id, chat_name) VALUES (?, 'Новый чат')",
            (chat_id,)
        )

    cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (user_id, username) VALUES (?, 'Новый пользователь')",
            (user_id,)
        )

    cursor.execute(
        "INSERT INTO messages (message_id, chat_id, user_id, message_text) VALUES (?, ?, ?, ?)",
        (message_id, chat_id, user_id, message_text)
    )
    cursor.connection.commit()


async def log_event(cursor, event_type, description):
    cursor.execute(
        "INSERT INTO logs (event_type, description) VALUES (?, ?)",
        (event_type, description)
    )
    cursor.connection.commit()

async def add_violation(cursor, user_id, chat_id):
    cursor.execute(
        "SELECT violation_count FROM violations WHERE user_id = ? AND chat_id = ?",
        (user_id, chat_id)
    )
    row = cursor.fetchone()
    if row:
        new_count = row[0] + 1
        cursor.execute(
            "UPDATE violations SET violation_count = ? WHERE user_id = ? AND chat_id = ?",
            (new_count, user_id, chat_id)
        )
    else:
        cursor.execute(
            "INSERT INTO violations (user_id, chat_id) VALUES (?, ?)",
            (user_id, chat_id)
        )
        new_count = 1
    cursor.connection.commit()
    return new_count

def set_automod_status(cursor, chat_id: int, enabled: bool):
    cursor.execute(
        "INSERT OR REPLACE INTO chat_settings (chat_id, automod_enabled) VALUES (?, ?)",
        (chat_id, 1 if enabled else 0)
    )
    cursor.connection.commit()
    logging.info(f"Сохранен статус автомодерации: {'ВКЛ' if enabled else 'ВЫКЛ'} для чата {chat_id}")


def get_automod_status(cursor, chat_id: int) -> bool:
    try:
        cursor.execute(
            "SELECT automod_enabled FROM chat_settings WHERE chat_id = ?",
            (chat_id,)
        )
        row = cursor.fetchone()

        if row:
            logging.info(f"Статус автомодерации для чата {chat_id}: {'ВКЛ' if row[0] else 'ВЫКЛ'}")
            return bool(row[0])
        else:
            logging.info(f"Настройки автомодерации для чата {chat_id} не найдены, используем по умолчанию ВКЛ")
            return True
    except Exception as e:
        logging.error(f"Ошибка получения статуса автомодерации: {str(e)}")
        return True