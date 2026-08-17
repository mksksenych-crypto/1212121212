import os
import sqlite3
import time
import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)

# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
# ТВОЙ TELEGRAM ID
ADMIN_IDS = {1555042637}

# Premium
PREMIUM_PLANS = {
    "1": {
        "name": "Premium — 1 день",
        "days": 1,
        "price": 50,
    },
    "3": {
        "name": "Premium — 3 дня",
        "days": 3,
        "price": 100,
    },
    "7": {
        "name": "Premium — 7 дней",
        "days": 7,
        "price": 200,
    },
}

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# =========================================================
# БАЗА
# =========================================================

db = sqlite3.connect(
    "anonchat.db",
    check_same_thread=False
)

db.row_factory = sqlite3.Row

db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    age INTEGER,
    gender TEXT,
    premium_until INTEGER DEFAULT 0,
    banned INTEGER DEFAULT 0,
    registered INTEGER DEFAULT 0
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS friends (
    user_id INTEGER NOT NULL,
    friend_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, friend_id)
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter INTEGER,
    reported INTEGER,
    created_at INTEGER
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS user_rules (
    user_id INTEGER PRIMARY KEY,
    accepted INTEGER DEFAULT 0,
    accepted_at INTEGER DEFAULT 0
)
""")

db.commit()

# =========================================================
# СОСТОЯНИЕ БОТА
# =========================================================

# user_id -> partner_id
chats = {}

# пользователи, которые ищут
searching = set()

# настройки Premium-поиска
search_filters = {}

# количество медиа в альбомах
media_groups = {}

# =========================================================
# ПРАВИЛА
# =========================================================

RULES_TEXT = """
📜 ПРАВИЛА ANONCHAT

Перед использованием бота обязательно ознакомься с правилами.

1️⃣ Уважение

Запрещены:
• оскорбления
• угрозы
• травля
• унижение других пользователей

2️⃣ Личные данные

❌ Не проси и не распространяй:
• номер телефона
• адрес
• пароли
• банковские данные
• документы
• данные аккаунтов
• другую личную информацию

3️⃣ Запрещённый контент

❌ Запрещены:
• порнография
• сексуальный контент
• контент с эксплуатацией несовершеннолетних
• шокирующий контент
• незаконные материалы

4️⃣ Запрещённые действия

❌ Запрещены:
• мошенничество
• вымогательство
• спам
• навязчивая реклама
• попытки взлома
• распространение вирусов

5️⃣ Безопасность

Не сообщай незнакомцам:
• настоящее имя
• адрес
• школу
• номер телефона
• пароли
• данные документов

6️⃣ Жалобы

Если собеседник нарушает правила,
используй кнопку 🚨 «Пожаловаться».

7️⃣ Блокировка

За нарушение правил администрация может
временно или навсегда заблокировать пользователя.

8️⃣ Анонимность

Не пытайся раскрывать личность собеседника
или получать его личные данные.

9️⃣ Ответственность

Используя AnonChat, ты подтверждаешь,
что прочитал правила и согласен их соблюдать.

Если тебе или другому пользователю угрожают
или происходит опасная ситуация — прекрати
общение и обратись к взрослому, которому доверяешь,
или в соответствующие службы.

"""

# =========================================================
# ПРОВЕРКИ
# =========================================================

def is_admin(user_id):
    return user_id in ADMIN_IDS


def create_user(user_id):
    db.execute(
        "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
        (user_id,)
    )
    db.commit()


def get_user(user_id):
    return db.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()


def is_premium(user_id):
    user = get_user(user_id)

    if not user:
        return False

    return user["premium_until"] > int(time.time())


def rules_accepted(user_id):
    row = db.execute(
        "SELECT accepted FROM user_rules WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    return row is not None and row["accepted"] == 1


# =========================================================
# ДРУЗЬЯ
# =========================================================

def add_friend(user_id, friend_id):
    now = int(time.time())

    db.execute(
        """
        INSERT OR IGNORE INTO friends
        (user_id, friend_id, created_at)
        VALUES (?, ?, ?)
        """,
        (user_id, friend_id, now)
    )

    db.execute(
        """
        INSERT OR IGNORE INTO friends
        (user_id, friend_id, created_at)
        VALUES (?, ?, ?)
        """,
        (friend_id, user_id, now)
    )

    db.commit()


def remove_friend(user_id, friend_id):
    db.execute(
        """
        DELETE FROM friends
        WHERE user_id = ? AND friend_id = ?
        """,
        (user_id, friend_id)
    )

    db.execute(
        """
        DELETE FROM friends
        WHERE user_id = ? AND friend_id = ?
        """,
        (friend_id, user_id)
    )

    db.commit()


def is_friend(user_id, friend_id):
    row = db.execute(
        """
        SELECT 1 FROM friends
        WHERE user_id = ? AND friend_id = ?
        """,
        (user_id, friend_id)
    ).fetchone()

    return row is not None


def get_friends(user_id):
    return db.execute(
        """
        SELECT friend_id
        FROM friends
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (user_id,)
    ).fetchall()


# =========================================================
# ДИАЛОГ
# =========================================================

def stop_chat(user_id):
    partner = chats.pop(user_id, None)

    if partner:
        chats.pop(partner, None)

    searching.discard(user_id)
    search_filters.pop(user_id, None)

    return partner


# =========================================================
# КЛАВИАТУРЫ
# =========================================================

def rules_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Я согласен с правилами",
                callback_data="accept_rules"
            )
        ]
    ])


def main_keyboard(user_id):
    buttons = [
        [
            InlineKeyboardButton(
                "🔎 Найти собеседника",
                callback_data="find"
            )
        ],
        [
            InlineKeyboardButton(
                "🛑 Остановить поиск/диалог",
                callback_data="stop"
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Друзья",
                callback_data="friends"
            )
        ],
        [
            InlineKeyboardButton(
                "⭐ Premium",
                callback_data="premium"
            )
        ],
        [
            InlineKeyboardButton(
                "👤 Мой профиль",
                callback_data="profile"
            )
        ],
        [
            InlineKeyboardButton(
                "📜 Правила",
                callback_data="rules"
            )
        ],
    ]

    if is_admin(user_id):
        buttons.append([
            InlineKeyboardButton(
                "👑 Админ-панель",
                callback_data="admin"
            )
        ])

    return InlineKeyboardMarkup(buttons)


# =========================================================
# ВОЗРАСТ
# =========================================================

async def show_age_buttons(message):
    buttons = []

    for start in range(13, 31, 3):
        row = []

        for age in range(
            start,
            min(start + 3, 31)
        ):
            row.append(
                InlineKeyboardButton(
                    str(age),
                    callback_data=f"age_{age}"
                )
            )

        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            "31+",
            callback_data="age_31plus"
        )
    ])

    await message.reply_text(
        "🎂 Выбери возраст:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


def gender_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👨 Мужской",
                callback_data="gender_male"
            ),
            InlineKeyboardButton(
                "👩 Женский",
                callback_data="gender_female"
            ),
        ]
    ])


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    create_user(user_id)

    user = get_user(user_id)

    if user["banned"]:
        await update.message.reply_text(
            "🚫 Ты заблокирован в AnonChat."
        )
        return

    if not rules_accepted(user_id):
        await update.message.reply_text(
            RULES_TEXT,
            reply_markup=rules_keyboard()
        )
        return

    if not user["registered"]:
        context.user_data["registration"] = "age"

        await update.message.reply_text(
            "👋 Добро пожаловать в AnonChat!\n\n"
            "Для начала выбери свой возраст."
        )

        await show_age_buttons(update.message)
        return

    await update.message.reply_text(
        "👋 С возвращением!\n\n"
        "Выбери действие:",
        reply_markup=main_keyboard(user_id)
    )


# =========================================================
# CALLBACK
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    create_user(user_id)

    # =====================================================
    # ПРАВИЛА
    # =====================================================

    if data == "accept_rules":

        db.execute(
            """
            INSERT INTO user_rules
            (user_id, accepted, accepted_at)
            VALUES (?, 1, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET
                accepted = 1,
                accepted_at = excluded.accepted_at
            """,
            (user_id, int(time.time()))
        )

        db.commit()

        user = get_user(user_id)

        if not user["registered"]:

            await query.edit_message_text(
                "✅ Правила приняты!\n\n"
                "Теперь выбери свой возраст."
            )

            await show_age_buttons(query.message)

        else:

            await query.edit_message_text(
                "✅ Правила приняты!",
                reply_markup=main_keyboard(user_id)
            )

        return

    if data == "rules":

        await query.edit_message_text(
            RULES_TEXT,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "◀️ Назад",
                        callback_data="profile"
                    )
                ]
            ])
        )

        return

    # =====================================================
    # ВОЗРАСТ
    # =====================================================

    if data.startswith("age_"):

        value = data.replace("age_", "")

        if value == "31plus":
            age = 31
        else:
            age = int(value)

        context.user_data["age"] = age
        context.user_data["registration"] = "gender"

        await query.edit_message_text(
            "⚧ Теперь выбери пол:",
            reply_markup=gender_keyboard()
        )

        return

    # =====================================================
    # ПОЛ
    # =====================================================

    if data.startswith("gender_"):

        gender = data.replace("gender_", "")

        age = context.user_data.get("age")

        if not age:
            await query.edit_message_text(
                "❌ Ошибка регистрации.\n"
                "Напиши /start"
            )
            return

        db.execute(
            """
            UPDATE users
            SET age = ?, gender = ?, registered = 1
            WHERE user_id = ?
            """,
            (age, gender, user_id)
        )

        db.commit()

        context.user_data.pop("age", None)
        context.user_data.pop("registration", None)

        gender_text = (
            "👨 Мужской"
            if gender == "male"
            else "👩 Женский"
        )

        await query.edit_message_text(
            "✅ Профиль создан!\n\n"
            f"🎂 Возраст: "
            f"{age if age < 31 else '31+'}\n"
            f"⚧ Пол: {gender_text}\n\n"
            "Теперь можно искать собеседника.",
            reply_markup=main_keyboard(user_id)
        )

        return

    # =====================================================
    # ПОИСК
    # =====================================================

    if data == "find":

        if not rules_accepted(user_id):
            await query.edit_message_text(
                RULES_TEXT,
                reply_markup=rules_keyboard()
            )
            return

        await find_start(query, user_id)
        return

    # =====================================================
    # STOP
    # =====================================================

    if data == "stop":

        partner = stop_chat(user_id)

        if partner:

            try:
                await context.bot.send_message(
                    partner,
                    "🛑 Собеседник завершил диалог.",
                    reply_markup=main_keyboard(partner)
                )
            except Exception:
                pass

            await query.edit_message_text(
                "🛑 Диалог завершён.",
                reply_markup=main_keyboard(user_id)
            )

        else:

            await query.edit_message_text(
                "🛑 Поиск остановлен.",
                reply_markup=main_keyboard(user_id)
            )

        return

    # =====================================================
    # ДОБАВИТЬ В ДРУЗЬЯ
    # =====================================================

    if data == "add_friend":

        partner = chats.get(user_id)

        if not partner:
            await query.answer(
                "❌ Сейчас нет собеседника.",
                show_alert=True
            )
            return

        if is_friend(user_id, partner):
            await query.answer(
                "👥 Этот пользователь уже у тебя в друзьях.",
                show_alert=True
            )
            return

        add_friend(user_id, partner)

        await query.answer(
            "👥 Собеседник добавлен в друзья!",
            show_alert=True
        )

        try:
            await context.bot.send_message(
                partner,
                "👥 Собеседник добавил тебя в друзья!"
            )
        except Exception:
            pass

        return

    # =====================================================
    # ДРУЗЬЯ
    # =====================================================

    if data == "friends":
        await friends_menu(query, user_id)
        return

    if data.startswith("friend_remove_"):

        friend_id = int(
            data.replace(
                "friend_remove_",
                ""
            )
        )

        if is_friend(user_id, friend_id):

            remove_friend(
                user_id,
                friend_id
            )

            await query.answer(
                "❌ Друг удалён.",
                show_alert=True
            )

        await friends_menu(
            query,
            user_id
        )

        return

    if data.startswith("friend_info_"):

        friend_id = int(
            data.replace(
                "friend_info_",
                ""
            )
        )

        if not is_friend(
            user_id,
            friend_id
        ):

            await query.answer(
                "❌ Этого пользователя нет в друзьях.",
                show_alert=True
            )
            return

        friend = get_user(friend_id)

        if not friend:

            await query.answer(
                "❌ Пользователь не найден.",
                show_alert=True
            )
            return

        gender = (
            "👨 Мужской"
            if friend["gender"] == "male"
            else "👩 Женский"
        )

        age = (
            "31+"
            if friend["age"] >= 31
            else str(friend["age"])
        )

        await query.edit_message_text(
            "👤 ПРОФИЛЬ ДРУГА\n\n"
            f"🆔 ID: {friend_id}\n"
            f"🎂 Возраст: {age}\n"
            f"⚧ Пол: {gender}\n\n"
            "🔒 Личные данные пользователя скрыты.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "❌ Удалить из друзей",
                        callback_data=
                        f"friend_remove_{friend_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "◀️ Назад",
                        callback_data="friends"
                    )
                ]
            ])
        )

        return

    # =====================================================
    # PREMIUM
    # =====================================================

    if data == "premium":

        await premium_menu(
            query,
            user_id
        )

        return

    if data in (
        "buy_1",
        "buy_3",
        "buy_7"
    ):

        days = data.replace(
            "buy_",
            ""
        )

        await send_premium_invoice(
            query,
            context,
            days
        )

        return

    # =====================================================
    # PREMIUM ПОИСК
    # =====================================================

    if data.startswith("search_age_"):

        if not is_premium(user_id):

            await query.edit_message_text(
                "⭐ Эта функция доступна только Premium.",
                reply_markup=main_keyboard(user_id)
            )

            return

        age = data.replace(
            "search_age_",
            ""
        )

        if age == "any":

            search_filters.setdefault(
                user_id,
                {}
            )["age"] = None

        else:

            search_filters.setdefault(
                user_id,
                {}
            )["age"] = int(age)

        await query.edit_message_text(
            "⚧ Выбери пол собеседника:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "👨 Мужской",
                        callback_data=
                        "search_gender_male"
                    ),
                    InlineKeyboardButton(
                        "👩 Женский",
                        callback_data=
                        "search_gender_female"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🎲 Любой",
                        callback_data=
                        "search_gender_any"
                    )
                ]
            ])
        )

        return

    if data.startswith("search_gender_"):

        if not is_premium(user_id):
            return

        gender = data.replace(
            "search_gender_",
            ""
        )

        if gender == "any":

            search_filters.setdefault(
                user_id,
                {}
            )["gender"] = None

        else:

            search_filters.setdefault(
                user_id,
                {}
            )["gender"] = gender

        await start_search(
            query,
            user_id,
            search_filters.get(
                user_id,
                {}
            )
        )

        return

    # =====================================================
    # ПРОФИЛЬ
    # =====================================================

    if data == "profile":

        await profile(
            query,
            user_id
        )

        return

    # =====================================================
    # АДМИНКА
    # =====================================================

    if data == "admin":

        if is_admin(user_id):

            await admin_panel(
                query
            )

        else:

            await query.edit_message_text(
                "🚫 Нет доступа."
            )

        return

    if data == "admin_give":

        if is_admin(user_id):

            context.user_data[
                "admin_action"
            ] = "give"

            await query.edit_message_text(
                "🎁 Введи Telegram ID пользователя:"
            )

        return

    if data == "admin_remove":

        if is_admin(user_id):

            context.user_data[
                "admin_action"
            ] = "remove"

            await query.edit_message_text(
                "❌ Введи Telegram ID пользователя:"
            )

        return

    if data == "admin_ban":

        if is_admin(user_id):

            context.user_data[
                "admin_action"
            ] = "ban"

            await query.edit_message_text(
                "🚫 Введи Telegram ID пользователя:"
            )

        return

    if data == "admin_unban":

        if is_admin(user_id):

            context.user_data[
                "admin_action"
            ] = "unban"

            await query.edit_message_text(
                "✅ Введи Telegram ID пользователя:"
            )

        return

    if data == "admin_stats":

        if is_admin(user_id):

            await admin_stats(
                query
            )

        return

    # =====================================================
    # ЖАЛОБА
    # =====================================================

    if data == "report":

        partner = chats.get(user_id)

        if not partner:

            await query.answer(
                "❌ Собеседник не найден.",
                show_alert=True
            )

            return

        db.execute(
            """
            INSERT INTO reports
            (reporter, reported, created_at)
            VALUES (?, ?, ?)
            """,
            (
                user_id,
                partner,
                int(time.time())
            )
        )

        db.commit()

        await query.answer(
            "🚨 Жалоба отправлена администрации.",
            show_alert=True
        )

        return


# =========================================================
# ПОИСК
# =========================================================

async def find_start(
    query,
    user_id
):

    if user_id in chats:

        await query.edit_message_text(
            "💬 Ты уже находишься в диалоге.",
            reply_markup=main_keyboard(user_id)
        )

        return

    if user_id in searching:

        await query.edit_message_text(
            "🔎 Ты уже ищешь собеседника..."
        )

        return

    if is_premium(user_id):

        await query.edit_message_text(
            "⭐ PREMIUM-ПОИСК\n\n"
            "Выбери возраст собеседника:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🎲 Любой",
                        callback_data=
                        "search_age_any"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "13–15",
                        callback_data=
                        "search_age_14"
                    ),
                    InlineKeyboardButton(
                        "16–18",
                        callback_data=
                        "search_age_17"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "19–25",
                        callback_data=
                        "search_age_22"
                    ),
                    InlineKeyboardButton(
                        "26–30",
                        callback_data=
                        "search_age_28"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "31+",
                        callback_data=
                        "search_age_31"
                    )
                ]
            ])
        )

    else:

        await start_search(
            query,
            user_id,
            {}
        )


async def start_search(
    query,
    user_id,
    preferences
):

    user = get_user(user_id)

    if not user or not user["registered"]:

        await query.edit_message_text(
            "❌ Сначала пройди регистрацию через /start."
        )

        return

    searching.add(user_id)

    search_filters[
        user_id
    ] = preferences

    partner = find_partner(
        user_id
    )

    if partner:

        searching.discard(user_id)
        searching.discard(partner)

        chats[user_id] = partner
        chats[partner] = user_id

        chat_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "➕ Добавить в друзья",
                    callback_data="add_friend"
                )
            ],
            [
                InlineKeyboardButton(
                    "🛑 Завершить",
                    callback_data="stop"
                )
            ],
            [
                InlineKeyboardButton(
                    "🚨 Пожаловаться",
                    callback_data="report"
                )
            ]
        ])

        await query.edit_message_text(
            "💬 СОБЕСЕДНИК НАЙДЕН!\n\n"
            "Теперь можете общаться анонимно.\n\n"
            "🛑 Чтобы закончить диалог — нажми "
            "«Завершить».",
            reply_markup=chat_keyboard
        )

        try:

            await query.get_bot().send_message(
                partner,
                "💬 СОБЕСЕДНИК НАЙДЕН!\n\n"
                "Теперь можете общаться анонимно.",
                reply_markup=chat_keyboard
            )

        except Exception:

            stop_chat(user_id)

    else:

        await query.edit_message_text(
            "🔎 Ищу собеседника...\n\n"
            "Подожди немного.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🛑 Отменить",
                        callback_data="stop"
                    )
                ]
            ])
        )


def find_partner(user_id):

    my_user = get_user(
        user_id
    )

    if not my_user:
        return None

    my_filter = search_filters.get(
        user_id,
        {}
    )

    for candidate_id in list(searching):

        if candidate_id == user_id:
            continue

        candidate = get_user(
            candidate_id
        )

        if not candidate:
            continue

        if candidate["banned"]:
            continue

        candidate_filter = search_filters.get(
            candidate_id,
            {}
        )

        # запрос первого к кандидату

        age_filter = my_filter.get(
            "age"
        )

        if age_filter is not None:

            if age_filter == 31:

                if candidate["age"] < 31:
                    continue

            elif candidate["age"] != age_filter:

                continue

        gender_filter = my_filter.get(
            "gender"
        )

        if gender_filter is not None:

            if candidate["gender"] != gender_filter:
                continue

        # запрос кандидата к первому

        age_filter_2 = candidate_filter.get(
            "age"
        )

        if age_filter_2 is not None:

            if age_filter_2 == 31:

                if my_user["age"] < 31:
                    continue

            elif my_user["age"] != age_filter_2:

                continue

        gender_filter_2 = candidate_filter.get(
            "gender"
        )

        if gender_filter_2 is not None:

            if my_user["gender"] != gender_filter_2:
                continue

        return candidate_id

    return None


# =========================================================
# СООБЩЕНИЯ
# =========================================================

async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    create_user(
        user_id
    )

    user = get_user(
        user_id
    )

    if user["banned"]:
        return

    if not rules_accepted(user_id):

        await update.message.reply_text(
            RULES_TEXT,
            reply_markup=rules_keyboard()
        )

        return

    # =====================================================
    # АДМИНСКИЕ ДЕЙСТВИЯ
    # =====================================================

    action = context.user_data.get(
        "admin_action"
    )

    if is_admin(user_id) and action:

        text = (
            update.message.text or ""
        ).strip()

        if not text.isdigit():

            await update.message.reply_text(
                "❌ Нужно отправить числовой Telegram ID."
            )

            return

        target_id = int(text)

        create_user(
            target_id
        )

        if action == "give":

            until = (
                int(time.time())
                + 24 * 60 * 60
            )

            db.execute(
                """
                UPDATE users
                SET premium_until = ?
                WHERE user_id = ?
                """,
                (
                    until,
                    target_id
                )
            )

            db.commit()

            await update.message.reply_text(
                f"✅ Premium выдан пользователю "
                f"{target_id} на 1 день."
            )

        elif action == "remove":

            db.execute(
                """
                UPDATE users
                SET premium_until = 0
                WHERE user_id = ?
                """,
                (target_id,)
            )

            db.commit()

            await update.message.reply_text(
                f"❌ Premium забран у {target_id}."
            )

        elif action == "ban":

            db.execute(
                """
                UPDATE users
                SET banned = 1
                WHERE user_id = ?
                """,
                (target_id,)
            )

            db.commit()

            stop_chat(
                target_id
            )

            await update.message.reply_text(
                f"🚫 Пользователь "
                f"{target_id} заблокирован."
            )

        elif action == "unban":

            db.execute(
                """
                UPDATE users
                SET banned = 0
                WHERE user_id = ?
                """,
                (target_id,)
            )

            db.commit()

            await update.message.reply_text(
                f"✅ Пользователь "
                f"{target_id} разблокирован."
            )

        context.user_data.pop(
            "admin_action",
            None
        )

        return

    # =====================================================
    # МЕДИА
    # =====================================================

    message = update.message

    if message:

        is_media = (
            message.photo is not None
            or message.video is not None
        )

        if is_media:

            group_id = message.media_group_id

            if group_id:

                key = (
                    user_id,
                    group_id
                )

                media_groups[key] = (
                    media_groups.get(
                        key,
                        0
                    ) + 1
                )

                if media_groups[key] > 5:

                    await update.message.reply_text(
                        "⚠️ Максимум 5 фото/видео "
                        "в одном альбоме."
                    )

                    return

    # =====================================================
    # ПЕРЕСЫЛКА В ДИАЛОГ
    # =====================================================

    partner = chats.get(
        user_id
    )

    if not partner:

        await update.message.reply_text(
            "🔎 Ты сейчас не в диалоге.\n\n"
            "Нажми «Найти собеседника».",
            reply_markup=main_keyboard(user_id)
        )

        return

    try:

        await context.bot.copy_message(
            chat_id=partner,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id
        )

    except Exception:

        await update.message.reply_text(
            "❌ Не удалось отправить сообщение."
        )


# =========================================================
# ДРУЗЬЯ
# =========================================================

async def friends_menu(
    query,
    user_id
):

    friends = get_friends(
        user_id
    )

    if not friends:

        await query.edit_message_text(
            "👥 ДРУЗЬЯ\n\n"
            "У тебя пока нет друзей.\n\n"
            "Во время общения нажми "
            "«➕ Добавить в друзья».",
            reply_markup=main_keyboard(user_id)
        )

        return

    buttons = []

    for row in friends:

        friend_id = row[
            "friend_id"
        ]

        buttons.append([
            InlineKeyboardButton(
                f"👤 {friend_id}",
                callback_data=
                f"friend_info_{friend_id}"
            ),
            InlineKeyboardButton(
                "❌",
                callback_data=
                f"friend_remove_{friend_id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "◀️ Назад",
            callback_data="profile"
        )
    ])

    await query.edit_message_text(
        f"👥 ДРУЗЬЯ\n\n"
        f"Количество друзей: "
        f"{len(friends)}\n\n"
        "Нажми на пользователя.",
        reply_markup=
        InlineKeyboardMarkup(buttons)
    )


# =========================================================
# PREMIUM
# =========================================================

async def premium_menu(
    query,
    user_id
):

    if is_premium(user_id):

        user = get_user(
            user_id
        )

        remaining = (
            user["premium_until"]
            - int(time.time())
        )

        days = remaining // 86400
        hours = (
            remaining % 86400
        ) // 3600

        await query.edit_message_text(
            "⭐ PREMIUM АКТИВЕН\n\n"
            f"⏳ Осталось: "
            f"{days} дн. {hours} ч.\n\n"
            "⭐ Что даёт Premium:\n"
            "🔎 Поиск по возрасту\n"
            "⚧ Поиск по полу\n"
            "🎯 Выбор параметров собеседника\n"
            "🚀 Более точный поиск",
            reply_markup=main_keyboard(user_id)
        )

        return

    await query.edit_message_text(
        "⭐ PREMIUM\n\n"
        "Premium открывает дополнительные "
        "возможности поиска.\n\n"
        "⭐ ЧТО ДАЁТ PREMIUM:\n\n"
        "🔎 Поиск по возрасту\n"
        "⚧ Поиск по полу\n"
        "🎯 Выбор параметров собеседника\n"
        "🚀 Более точный поиск\n\n"
        "💎 ТАРИФЫ:\n\n"
        "⭐ 1 день — 50 Stars\n"
        "⭐ 3 дня — 100 Stars\n"
        "⭐ 7 дней — 200 Stars\n\n"
        "Выбери тариф:",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⭐ 1 день — 50",
                    callback_data="buy_1"
                )
            ],
            [
                InlineKeyboardButton(
                    "⭐ 3 дня — 100",
                    callback_data="buy_3"
                )
            ],
            [
                InlineKeyboardButton(
                    "⭐ 7 дней — 200",
                    callback_data="buy_7"
                )
            ],
            [
                InlineKeyboardButton(
                    "◀️ Назад",
                    callback_data="profile"
                )
            ]
        ])
    )


async def send_premium_invoice(
    query,
    context,
    days
):

    plan = PREMIUM_PLANS[
        days
    ]

    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title=plan["name"],
        description=(
            f"Premium на "
            f"{plan['days']} дн. "
            "с поиском по возрасту и полу."
        ),
        payload=(
            f"premium_{days}_"
            f"{query.from_user.id}"
        ),
        currency="XTR",
        prices=[
            LabeledPrice(
                plan["name"],
                plan["price"]
            )
        ],
        provider_token=""
    )


async def precheckout_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.pre_checkout_query

    await query.answer(
        ok=True
    )


async def successful_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = (
        update.effective_user.id
    )

    payment = (
        update.message.successful_payment
    )

    payload = (
        payment.invoice_payload
    )

    if payment.currency != "XTR":
        return

    if not payload.startswith(
        "premium_"
    ):
        return

    parts = payload.split("_")

    if len(parts) < 3:
        return

    days = parts[1]

    if days not in PREMIUM_PLANS:
        return

    plan = PREMIUM_PLANS[
        days
    ]

    if payment.total_amount != plan["price"]:
        return

    current = int(
        time.time()
    )

    user = get_user(
        user_id
    )

    old_until = (
        user["premium_until"]
        if user
        else 0
    )

    added_time = (
        plan["days"]
        * 24
        * 60
        * 60
    )

    if old_until > current:

        new_until = (
            old_until
            + added_time
        )

    else:

        new_until = (
            current
            + added_time
        )

    db.execute(
        """
        UPDATE users
        SET premium_until = ?
        WHERE user_id = ?
        """,
        (
            new_until,
            user_id
        )
    )

    db.commit()

    await update.message.reply_text(
        "🎉 Оплата прошла успешно!\n\n"
        f"⭐ Premium активирован "
        f"на {plan['days']} дн.\n\n"
        "Теперь доступны:\n"
        "🔎 поиск по возрасту\n"
        "⚧ поиск по полу\n"
        "🎯 выбор параметров собеседника",
        reply_markup=main_keyboard(user_id)
    )


# =========================================================
# ПРОФИЛЬ
# =========================================================

async def profile(
    query,
    user_id
):

    user = get_user(
        user_id
    )

    if not user:
        return

    if is_premium(user_id):

        remaining = (
            user["premium_until"]
            - int(time.time())
        )

        days = remaining // 86400
        hours = (
            remaining % 86400
        ) // 3600

        premium_text = (
            f"⭐ Premium — активно\n"
            f"⏳ Осталось: "
            f"{days} дн. {hours} ч."
        )

    else:

        premium_text = (
            "⭐ Premium — нет"
        )

    gender = (
        "👨 Мужской"
        if user["gender"] == "male"
        else "👩 Женский"
    )

    age = (
        "31+"
        if user["age"] >= 31
        else str(user["age"])
    )

    await query.edit_message_text(
        "👤 ТВОЙ ПРОФИЛЬ\n\n"
        f"🎂 Возраст: {age}\n"
        f"⚧ Пол: {gender}\n\n"
        f"{premium_text}",
        reply_markup=main_keyboard(user_id)
    )


# =========================================================
# АДМИНКА
# =========================================================

async def admin_panel(
    query
):

    await query.edit_message_text(
        "👑 АДМИН-ПАНЕЛЬ\n\n"
        "Выбери действие:",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🎁 Выдать Premium",
                    callback_data="admin_give"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Забрать Premium",
                    callback_data="admin_remove"
                )
            ],
            [
                InlineKeyboardButton(
                    "🚫 Заблокировать",
                    callback_data="admin_ban"
                ),
                InlineKeyboardButton(
                    "✅ Разблокировать",
                    callback_data="admin_unban"
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 Статистика",
                    callback_data="admin_stats"
                )
            ]
        ])
    )


async def admin_stats(
    query
):

    users_count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM users
        """
    ).fetchone()["count"]

    premium_count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM users
        WHERE premium_until > ?
        """,
        (int(time.time()),)
    ).fetchone()["count"]

    banned_count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM users
        WHERE banned = 1
        """
    ).fetchone()["count"]

    reports_count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM reports
        """
    ).fetchone()["count"]

    await query.edit_message_text(
        "📊 СТАТИСТИКА\n\n"
        f"👥 Пользователей: "
        f"{users_count}\n"
        f"⭐ Premium: "
        f"{premium_count}\n"
        f"🚫 Заблокировано: "
        f"{banned_count}\n"
        f"🚨 Жалоб: "
        f"{reports_count}\n"
        f"🔎 Ищут сейчас: "
        f"{len(searching)}\n"
        f"💬 Активных диалогов: "
        f"{len(chats) // 2}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "◀️ Назад",
                    callback_data="admin"
                )
            ]
        ])
    )


# =========================================================
# MY ID
# =========================================================

async def myid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🆔 Твой Telegram ID:\n\n"
        f"{update.effective_user.id}"
    )


# =========================================================
# STOP COMMAND
# =========================================================

async def stop_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    partner = stop_chat(
        user_id
    )

    if partner:

        try:

            await context.bot.send_message(
                partner,
                "🛑 Собеседник завершил диалог.",
                reply_markup=
                main_keyboard(partner)
            )

        except Exception:
            pass

    await update.message.reply_text(
        "🛑 Диалог/поиск остановлен.",
        reply_markup=main_keyboard(user_id)
    )


# =========================================================
# ОШИБКИ
# =========================================================

async def error_handler(
    update,
    context
):

    logging.exception(
        "Ошибка бота:",
        exc_info=context.error
    )


# =========================================================
# ЗАПУСК
# =========================================================

def main():

    if not BOT_TOKEN:

        print(
            "ОШИБКА: не задан BOT_TOKEN."
        )

        print(
            "Установи переменную окружения BOT_TOKEN."
        )

        return

    print(
        "================================"
    )

    print(
        "       ANONCHAT ЗАПУЩЕН"
    )

    print(
        "================================"
    )

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Команды

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "myid",
            myid
        )
    )

    application.add_handler(
        CommandHandler(
            "stop",
            stop_command
        )
    )

    # Кнопки

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # Telegram Stars

    application.add_handler(
        PreCheckoutQueryHandler(
            precheckout_callback
        )
    )

    application.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment
        )
    )

    # Сообщения

    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            message_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()