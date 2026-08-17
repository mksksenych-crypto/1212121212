import os
import sqlite3
import logging
from datetime import datetime, timedelta, timezone

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    LabeledPrice,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_FILE = "anonchat.db"

ADMIN_IDS = set()

for value in os.getenv("ADMIN_IDS", "").split(","):
    value = value.strip()

    if value.isdigit():
        ADMIN_IDS.add(int(value))

MEDIA_LIMIT = 5
MEDIA_WINDOW = 10


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger("AnonChat")


# ============================================================
# DATABASE
# ============================================================

def db():
    connection = sqlite3.connect(DB_FILE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = db()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            age INTEGER,
            gender TEXT,
            registered INTEGER DEFAULT 0,
            adult_confirmed INTEGER DEFAULT 0,
            premium_until TEXT,
            banned INTEGER DEFAULT 0,
            searching INTEGER DEFAULT 0,
            partner_id INTEGER,
            created_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS friends (
            user_id INTEGER NOT NULL,
            friend_id INTEGER NOT NULL,
            UNIQUE(user_id, friend_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS friend_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER NOT NULL,
            to_user INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            UNIQUE(from_user, to_user)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS blocks (
            user_id INTEGER NOT NULL,
            blocked_id INTEGER NOT NULL,
            UNIQUE(user_id, blocked_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id INTEGER,
            reported_id INTEGER,
            reason TEXT,
            created_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            stars INTEGER,
            days INTEGER,
            created_at TEXT
        )
    """)

    connection.commit()
    connection.close()


def ensure_user(user_id):
    connection = db()

    connection.execute("""
        INSERT OR IGNORE INTO users
        (
            user_id,
            registered,
            adult_confirmed,
            banned,
            searching,
            partner_id,
            created_at
        )
        VALUES (?, 0, 0, 0, 0, NULL, ?)
    """, (
        user_id,
        datetime.now(timezone.utc).isoformat(),
    ))

    connection.commit()
    connection.close()


def get_user(user_id):
    ensure_user(user_id)

    connection = db()

    user = connection.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    connection.close()

    return user


def update_user(user_id, **values):
    if not values:
        return

    connection = db()

    fields = []
    parameters = []

    for field, value in values.items():
        fields.append(f"{field} = ?")
        parameters.append(value)

    parameters.append(user_id)

    connection.execute(
        f"""
        UPDATE users
        SET {", ".join(fields)}
        WHERE user_id = ?
        """,
        parameters,
    )

    connection.commit()
    connection.close()


# ============================================================
# PREMIUM
# ============================================================

def get_premium_until(user_id):
    user = get_user(user_id)

    value = user["premium_until"]

    if not value:
        return None

    try:
        date = datetime.fromisoformat(value)

        if date > datetime.now(timezone.utc):
            return date

    except Exception:
        pass

    return None


def premium_active(user_id):
    return get_premium_until(user_id) is not None


def give_premium(user_id, days):
    now = datetime.now(timezone.utc)

    current = get_premium_until(user_id)

    start = current if current else now

    new_date = start + timedelta(days=days)

    update_user(
        user_id,
        premium_until=new_date.isoformat(),
    )

    return new_date


# ============================================================
# BLOCKS
# ============================================================

def is_blocked(user_id, other_id):
    connection = db()

    result = connection.execute("""
        SELECT 1
        FROM blocks
        WHERE user_id = ?
        AND blocked_id = ?
    """, (
        user_id,
        other_id,
    )).fetchone()

    connection.close()

    return result is not None


def block_user(user_id, other_id):
    connection = db()

    connection.execute("""
        INSERT OR IGNORE INTO blocks
        (user_id, blocked_id)
        VALUES (?, ?)
    """, (
        user_id,
        other_id,
    ))

    connection.commit()
    connection.close()


# ============================================================
# FRIENDS
# ============================================================

def are_friends(user_id, friend_id):
    connection = db()

    result = connection.execute("""
        SELECT 1
        FROM friends
        WHERE user_id = ?
        AND friend_id = ?
    """, (
        user_id,
        friend_id,
    )).fetchone()

    connection.close()

    return result is not None


def send_friend_request(from_user, to_user):
    if from_user == to_user:
        return False

    if are_friends(from_user, to_user):
        return False

    if is_blocked(from_user, to_user):
        return False

    if is_blocked(to_user, from_user):
        return False

    connection = db()

    existing = connection.execute("""
        SELECT id
        FROM friend_requests
        WHERE from_user = ?
        AND to_user = ?
        AND status = 'pending'
    """, (
        from_user,
        to_user,
    )).fetchone()

    if existing:
        connection.close()
        return False

    connection.execute("""
        INSERT INTO friend_requests
        (
            from_user,
            to_user,
            status,
            created_at
        )
        VALUES (?, ?, 'pending', ?)
    """, (
        from_user,
        to_user,
        datetime.now(timezone.utc).isoformat(),
    ))

    connection.commit()
    connection.close()

    return True


def get_friend_requests(user_id):
    connection = db()

    result = connection.execute("""
        SELECT *
        FROM friend_requests
        WHERE to_user = ?
        AND status = 'pending'
        ORDER BY id DESC
    """, (user_id,)).fetchall()

    connection.close()

    return result


def accept_friend_request(request_id, user_id):
    connection = db()

    request = connection.execute("""
        SELECT *
        FROM friend_requests
        WHERE id = ?
        AND to_user = ?
        AND status = 'pending'
    """, (
        request_id,
        user_id,
    )).fetchone()

    if not request:
        connection.close()
        return None

    from_user = request["from_user"]

    connection.execute("""
        UPDATE friend_requests
        SET status = 'accepted'
        WHERE id = ?
    """, (request_id,))

    connection.execute("""
        INSERT OR IGNORE INTO friends
        (user_id, friend_id)
        VALUES (?, ?)
    """, (
        user_id,
        from_user,
    ))

    connection.execute("""
        INSERT OR IGNORE INTO friends
        (user_id, friend_id)
        VALUES (?, ?)
    """, (
        from_user,
        user_id,
    ))

    connection.commit()
    connection.close()

    return from_user


def reject_friend_request(request_id, user_id):
    connection = db()

    connection.execute("""
        UPDATE friend_requests
        SET status = 'rejected'
        WHERE id = ?
        AND to_user = ?
        AND status = 'pending'
    """, (
        request_id,
        user_id,
    ))

    connection.commit()
    connection.close()


def get_friends(user_id):
    connection = db()

    result = connection.execute("""
        SELECT friend_id
        FROM friends
        WHERE user_id = ?
    """, (user_id,)).fetchall()

    connection.close()

    return result


# ============================================================
# KEYBOARDS
# ============================================================

def main_menu(user_id):
    buttons = [
        ["🔎 Найти собеседника"],
        ["💎 Premium"],
        ["👤 Профиль", "👥 Друзья"],
        ["📜 Правила"],
    ]

    if user_id in ADMIN_IDS:
        buttons.append(["🛡 Админ-панель"])

    return ReplyKeyboardMarkup(
        buttons,
        resize_keyboard=True,
    )


def adult_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Мне 18+",
                callback_data="adult_yes",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Мне нет 18",
                callback_data="adult_no",
            )
        ],
    ])


def gender_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👨 Мужской",
                callback_data="gender_male",
            ),
            InlineKeyboardButton(
                "👩 Женский",
                callback_data="gender_female",
            ),
        ]
    ])


def chat_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⏭ Следующий",
                callback_data="next",
            ),
            InlineKeyboardButton(
                "🛑 Завершить",
                callback_data="stop",
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 В друзья",
                callback_data="friend_request",
            )
        ],
        [
            InlineKeyboardButton(
                "🚫 Заблокировать",
                callback_data="block",
            ),
            InlineKeyboardButton(
                "🚨 Жалоба",
                callback_data="report",
            ),
        ],
    ])


def premium_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⭐ 1 день — 50",
                callback_data="premium_1",
            )
        ],
        [
            InlineKeyboardButton(
                "⭐ 3 дня — 100",
                callback_data="premium_3",
            )
        ],
        [
            InlineKeyboardButton(
                "⭐ 7 дней — 200",
                callback_data="premium_7",
            )
        ],
    ])


def search_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👤 Любой пол",
                callback_data="search_any",
            )
        ],
        [
            InlineKeyboardButton(
                "👨 Мужчина",
                callback_data="search_male",
            ),
            InlineKeyboardButton(
                "👩 Женщина",
                callback_data="search_female",
            ),
        ],
    ])


# ============================================================
# RULES
# ============================================================

RULES = """
📜 <b>ПРАВИЛА ANONCHAT 18+</b>

🔞 Бот предназначен только для пользователей 18+.

1. ❌ Запрещены угрозы и травля.
2. ❌ Запрещён спам.
3. ❌ Запрещены мошенничество и вымогательство.
4. ❌ Не передавайте пароли и коды.
5. ❌ Не сообщайте личные данные незнакомым людям.
6. ❌ Запрещена незаконная деятельность.
7. ❌ Запрещена реклама без разрешения администрации.
8. 🚨 Нарушителей можно пожаловаться через кнопку «Жалоба».
9. 🛡 Администрация может блокировать нарушителей.

Используя бота, вы соглашаетесь с правилами.
"""


# ============================================================
# START
# ============================================================

async def start(update, context):
    user_id = update.effective_user.id

    ensure_user(user_id)

    user = get_user(user_id)

    if user["banned"]:
        await update.message.reply_text(
            "🚫 Ваш аккаунт заблокирован."
        )
        return

    if not user["adult_confirmed"]:
        await update.message.reply_text(
            "🔞 <b>ANONCHAT 18+</b>\n\n"
            "Бот предназначен только для пользователей "
            "старше 18 лет.\n\n"
            "Подтвердите ваш возраст:",
            parse_mode=ParseMode.HTML,
            reply_markup=adult_keyboard(),
        )
        return

    if not user["registered"]:
        await update.message.reply_text(
            "🚻 Выберите ваш пол:",
            reply_markup=gender_keyboard(),
        )
        return

    await update.message.reply_text(
        "👋 С возвращением!",
        reply_markup=main_menu(user_id),
    )


# ============================================================
# REGISTRATION
# ============================================================

async def registration_callback(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if query.data == "adult_yes":

        update_user(
            user_id,
            adult_confirmed=1,
        )

        await query.edit_message_text(
            "✅ Возраст подтверждён.\n\n"
            "Теперь выберите пол:",
            reply_markup=gender_keyboard(),
        )

        return

    if query.data == "adult_no":

        update_user(
            user_id,
            adult_confirmed=0,
        )

        await query.edit_message_text(
            "❌ Использование этого бота доступно "
            "только пользователям 18+."
        )

        return

    if query.data.startswith("gender_"):

        gender = query.data.split("_", 1)[1]

        update_user(
            user_id,
            gender=gender,
            registered=1,
        )

        await query.edit_message_text(
            "✅ Профиль создан!"
        )

        await context.bot.send_message(
            user_id,
            "Главное меню:",
            reply_markup=main_menu(user_id),
        )


# ============================================================
# PROFILE
# ============================================================

async def profile(update, context):
    user_id = update.effective_user.id
    user = get_user(user_id)

    gender = {
        "male": "👨 Мужской",
        "female": "👩 Женский",
    }.get(user["gender"], "—")

    premium = get_premium_until(user_id)

    if premium:
        premium_text = (
            "💎 Активен\n"
            f"До: {premium.strftime('%d.%m.%Y %H:%M UTC')}"
        )
    else:
        premium_text = "❌ Нет"

    text = (
        "👤 <b>ПРОФИЛЬ</b>\n\n"
        f"🚻 Пол: {gender}\n"
        f"💎 Premium: {premium_text}"
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(user_id),
    )


# ============================================================
# PREMIUM
# ============================================================

async def premium_menu(update, context):
    user_id = update.effective_user.id

    premium = get_premium_until(user_id)

    if premium:
        status = (
            "💎 <b>PREMIUM АКТИВЕН</b>\n\n"
            f"До: {premium.strftime('%d.%m.%Y %H:%M UTC')}\n\n"
        )
    else:
        status = "❌ Premium не активен.\n\n"

    text = (
        status +
        "💎 <b>ЧТО ДАЁТ PREMIUM</b>\n\n"
        "🔎 Поиск по полу\n"
        "🎯 Более точный поиск\n"
        "⭐ Premium-статус\n\n"
        "<b>Тарифы:</b>\n"
        "⭐ 1 день — 50 Stars\n"
        "⭐ 3 дня — 100 Stars\n"
        "⭐ 7 дней — 200 Stars\n\n"
        "Выберите тариф:"
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=premium_keyboard(),
    )


async def premium_payment(update, context):
    query = update.callback_query
    await query.answer()

    plans = {
        "premium_1": (1, 50),
        "premium_3": (3, 100),
        "premium_7": (7, 200),
    }

    if query.data not in plans:
        return

    days, stars = plans[query.data]

    payload = (
        f"premium:{days}:{query.from_user.id}"
    )

    prices = [
        LabeledPrice(
            f"Premium на {days} дн.",
            stars,
        )
    ]

    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title=f"Premium на {days} дней",
        description=(
            "Premium для AnonChat."
        ),
        payload=payload,
        currency="XTR",
        prices=prices,
    )


async def pre_checkout(update, context):
    await update.pre_checkout_query.answer(
        ok=True
    )


async def successful_payment(update, context):
    payment = update.message.successful_payment

    try:
        prefix, days, user_id = (
            payment.invoice_payload.split(":")
        )

        days = int(days)
        user_id = int(user_id)

    except Exception:
        logger.exception(
            "Ошибка payment payload"
        )
        return

    if prefix != "premium":
        return

    if user_id != update.effective_user.id:
        return

    until = give_premium(
        user_id,
        days,
    )

    connection = db()

    connection.execute("""
        INSERT INTO payments
        (user_id, stars, days, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        user_id,
        payment.total_amount,
        days,
        datetime.now(timezone.utc).isoformat(),
    ))

    connection.commit()
    connection.close()

    await update.message.reply_text(
        "🎉 <b>Premium активирован!</b>\n\n"
        f"⭐ Оплачено: {payment.total_amount} Stars\n"
        f"📅 Срок: {days} дней\n"
        f"⏰ До: {until.strftime('%d.%m.%Y %H:%M UTC')}",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(user_id),
    )


# ============================================================
# SEARCH
# ============================================================

async def find_button(update, context):
    user_id = update.effective_user.id
    user = get_user(user_id)

    if not user["registered"]:
        await update.message.reply_text(
            "Сначала используй /start."
        )
        return

    if user["partner_id"]:
        await update.message.reply_text(
            "💬 У вас уже есть собеседник.",
            reply_markup=chat_keyboard(),
        )
        return

    if premium_active(user_id):

        await update.message.reply_text(
            "💎 Premium позволяет выбрать пол:",
            reply_markup=search_keyboard(),
        )

    else:

        await update.message.reply_text(
            "🔎 Ищу случайного собеседника..."
        )

        await start_search(
            user_id,
            context,
            None,
        )


async def search_callback(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if query.data == "search_any":
        gender = None

    elif query.data == "search_male":
        gender = "male"

    elif query.data == "search_female":
        gender = "female"

    else:
        return

    await query.edit_message_text(
        "🔎 Ищу собеседника..."
    )

    await start_search(
        user_id,
        context,
        gender,
    )


def find_partner(user_id, wanted_gender):
    connection = db()

    users = connection.execute("""
        SELECT *
        FROM users
        WHERE user_id != ?
        AND registered = 1
        AND adult_confirmed = 1
        AND banned = 0
        AND searching = 1
        AND partner_id IS NULL
    """, (user_id,)).fetchall()

    connection.close()

    for user in users:

        if is_blocked(
            user_id,
            user["user_id"],
        ):
            continue

        if is_blocked(
            user["user_id"],
            user_id,
        ):
            continue

        if wanted_gender:
            if user["gender"] != wanted_gender:
                continue

        return user

    return None


async def start_search(
    user_id,
    context,
    wanted_gender,
):
    update_user(
        user_id,
        searching=1,
        partner_id=None,
    )

    partner = find_partner(
        user_id,
        wanted_gender,
    )

    if not partner:

        await context.bot.send_message(
            user_id,
            "⏳ Пока подходящего собеседника нет.\n\n"
            "Вы оставлены в очереди.",
        )

        return

    partner_id = partner["user_id"]

    update_user(
        user_id,
        searching=0,
        partner_id=partner_id,
    )

    update_user(
        partner_id,
        searching=0,
        partner_id=user_id,
    )

    await context.bot.send_message(
        user_id,
        "🎉 <b>Собеседник найден!</b>\n\n"
        "Можете начинать общение.",
        parse_mode=ParseMode.HTML,
        reply_markup=chat_keyboard(),
    )

    await context.bot.send_message(
        partner_id,
        "🎉 <b>Собеседник найден!</b>\n\n"
        "Можете начинать общение.",
        parse_mode=ParseMode.HTML,
        reply_markup=chat_keyboard(),
    )


# ============================================================
# CHAT CONTROL
# ============================================================

async def stop_chat(user_id, context):
    user = get_user(user_id)

    partner_id = user["partner_id"]

    update_user(
        user_id,
        partner_id=None,
        searching=0,
    )

    if partner_id:

        update_user(
            partner_id,
            partner_id=None,
            searching=0,
        )

        try:
            await context.bot.send_message(
                partner_id,
                "🛑 Собеседник завершил чат.",
                reply_markup=main_menu(partner_id),
            )
        except Exception:
            pass

    await context.bot.send_message(
        user_id,
        "🛑 Чат завершён.",
        reply_markup=main_menu(user_id),
    )


async def stop_callback(update, context):
    query = update.callback_query
    await query.answer()

    await stop_chat(
        query.from_user.id,
        context,
    )


async def next_callback(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user = get_user(user_id)

    partner_id = user["partner_id"]

    if partner_id:

        update_user(
            partner_id,
            partner_id=None,
            searching=0,
        )

        try:
            await context.bot.send_message(
                partner_id,
                "🛑 Собеседник ушёл искать другого.",
                reply_markup=main_menu(partner_id),
            )
        except Exception:
            pass

    update_user(
        user_id,
        partner_id=None,
        searching=0,
    )

    await query.edit_message_text(
        "🔎 Ищу нового собеседника..."
    )

    await start_search(
        user_id,
        context,
        None,
    )


# ============================================================
# FRIEND REQUEST
# ============================================================

async def friend_request_callback(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user = get_user(user_id)

    partner_id = user["partner_id"]

    if not partner_id:
        await query.message.reply_text(
            "❌ Собеседник отсутствует."
        )
        return

    if are_friends(
        user_id,
        partner_id,
    ):
        await query.message.reply_text(
            "👥 Вы уже друзья."
        )
        return

    if send_friend_request(
        user_id,
        partner_id,
    ):

        await query.message.reply_text(
            "📨 Заявка отправлена!"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Принять",
                    callback_data=(
                        f"friend_accept_{user_id}"
                    ),
                ),
                InlineKeyboardButton(
                    "❌ Отклонить",
                    callback_data=(
                        f"friend_reject_{user_id}"
                    ),
                ),
            ]
        ])

        try:
            await context.bot.send_message(
                partner_id,
                "📨 <b>Новая заявка в друзья!</b>\n\n"
                "Собеседник хочет добавить вас в друзья.",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except Exception:
            pass

    else:

        await query.message.reply_text(
            "⚠️ Заявка уже отправлена "
            "или вы уже друзья."
        )


async def friend_accept_callback(
    update,
    context,
    from_user,
):
    query = update.callback_query

    user_id = query.from_user.id

    connection = db()

    request = connection.execute("""
        SELECT id
        FROM friend_requests
        WHERE from_user = ?
        AND to_user = ?
        AND status = 'pending'
    """, (
        from_user,
        user_id,
    )).fetchone()

    connection.close()

    if not request:
        await query.edit_message_text(
            "❌ Заявка уже обработана."
        )
        return

    accepted = accept_friend_request(
        request["id"],
        user_id,
    )

    if accepted:

        await query.edit_message_text(
            "✅ Заявка принята!\n\n"
            "Теперь вы друзья."
        )

        try:
            await context.bot.send_message(
                from_user,
                "🎉 Ваша заявка в друзья принята!",
            )
        except Exception:
            pass


async def friend_reject_callback(
    update,
    context,
    from_user,
):
    query = update.callback_query

    user_id = query.from_user.id

    connection = db()

    request = connection.execute("""
        SELECT id
        FROM friend_requests
        WHERE from_user = ?
        AND to_user = ?
        AND status = 'pending'
    """, (
        from_user,
        user_id,
    )).fetchone()

    connection.close()

    if request:

        reject_friend_request(
            request["id"],
            user_id,
        )

    await query.edit_message_text(
        "❌ Заявка отклонена."
    )


# ============================================================
# FRIENDS MENU
# ============================================================

async def friends_menu(update, context):
    user_id = update.effective_user.id

    friends = get_friends(user_id)
    requests = get_friend_requests(user_id)

    text = "👥 <b>МОИ ДРУЗЬЯ</b>\n\n"

    keyboard = []

    if friends:

        for index, friend in enumerate(
            friends,
            start=1,
        ):

            friend_id = friend["friend_id"]

            text += (
                f"{index}. "
                f"<code>{friend_id}</code>\n"
            )

            keyboard.append([
                InlineKeyboardButton(
                    f"💬 Написать #{index}",
                    callback_data=(
                        f"friend_chat_{friend_id}"
                    ),
                )
            ])

    else:

        text += "Пока нет друзей.\n"

    if requests:

        text += (
            "\n📨 Новых заявок: "
            f"{len(requests)}"
        )

        keyboard.append([
            InlineKeyboardButton(
                "📨 Заявки",
                callback_data="friend_requests",
            )
        ])

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=(
            InlineKeyboardMarkup(keyboard)
            if keyboard
            else None
        ),
    )


async def friend_requests_menu(
    update,
    context,
):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    requests = get_friend_requests(
        user_id
    )

    if not requests:

        await query.message.reply_text(
            "📨 Заявок нет."
        )

        return

    for request in requests:

        from_user = request["from_user"]

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Принять",
                    callback_data=(
                        f"friend_accept_{from_user}"
                    ),
                ),
                InlineKeyboardButton(
                    "❌ Отклонить",
                    callback_data=(
                        f"friend_reject_{from_user}"
                    ),
                ),
            ]
        ])

        await query.message.reply_text(
            "📨 <b>Заявка в друзья</b>\n\n"
            "Пользователь хочет добавить вас в друзья.",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )


# ============================================================
# FRIEND CHAT
# ============================================================

async def friend_chat_callback(
    update,
    context,
):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    try:
        friend_id = int(
            query.data.replace(
                "friend_chat_",
                "",
            )
        )
    except ValueError:
        return

    if not are_friends(
        user_id,
        friend_id,
    ):

        await query.message.reply_text(
            "❌ Этот пользователь не ваш друг."
        )

        return

    context.user_data[
        "friend_chat"
    ] = friend_id

    await query.message.reply_text(
        "💬 <b>Диалог с другом открыт.</b>\n\n"
        "Отправляй сообщения — они будут "
        "передаваться другу.\n\n"
        "Для выхода используй /stopfriend.",
        parse_mode=ParseMode.HTML,
    )


async def stop_friend_chat(
    update,
    context,
):
    context.user_data.pop(
        "friend_chat",
        None,
    )

    await update.message.reply_text(
        "🛑 Диалог закрыт.",
        reply_markup=main_menu(
            update.effective_user.id
        ),
    )


# ============================================================
# BLOCK
# ============================================================

async def block_callback(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user = get_user(user_id)

    partner_id = user["partner_id"]

    if not partner_id:
        return

    block_user(
        user_id,
        partner_id,
    )

    update_user(
        user_id,
        partner_id=None,
        searching=0,
    )

    update_user(
        partner_id,
        partner_id=None,
        searching=0,
    )

    await query.message.reply_text(
        "🚫 Пользователь заблокирован."
    )

    try:
        await context.bot.send_message(
            partner_id,
            "🛑 Чат завершён.",
            reply_markup=main_menu(partner_id),
        )
    except Exception:
        pass


# ============================================================
# REPORT
# ============================================================

async def report_callback(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user = get_user(user_id)

    partner_id = user["partner_id"]

    if not partner_id:
        return

    connection = db()

    connection.execute("""
        INSERT INTO reports
        (
            reporter_id,
            reported_id,
            reason,
            created_at
        )
        VALUES (?, ?, ?, ?)
    """, (
        user_id,
        partner_id,
        "Жалоба из чата",
        datetime.now(timezone.utc).isoformat(),
    ))

    connection.commit()
    connection.close()

    await query.message.reply_text(
        "🚨 Жалоба отправлена администрации."
    )


# ============================================================
# MEDIA LIMIT
# ============================================================

def media_allowed(context, user_id):
    now = datetime.now().timestamp()

    limits = context.application.bot_data.setdefault(
        "media_limits",
        {},
    )

    timestamps = limits.setdefault(
        user_id,
        [],
    )

    timestamps[:] = [
        timestamp
        for timestamp in timestamps
        if now - timestamp < MEDIA_WINDOW
    ]

    if len(timestamps) >= MEDIA_LIMIT:
        return False

    timestamps.append(now)

    return True


# ============================================================
# MESSAGE FORWARDING
# ============================================================

async def forward_message(update, context):
    if not update.message:
        return

    user_id = update.effective_user.id

    user = get_user(user_id)

    if not user:
        return

    if user["banned"]:
        return

    if not user["registered"]:
        return

    # --------------------------------------------------------
    # FRIEND CHAT
    # --------------------------------------------------------

    friend_id = context.user_data.get(
        "friend_chat"
    )

    if friend_id:

        if not are_friends(
            user_id,
            friend_id,
        ):

            context.user_data.pop(
                "friend_chat",
                None,
            )

            return

        try:

            await update.message.copy(
                chat_id=friend_id
            )

        except Exception as error:

            logger.error(
                f"Friend chat error: {error}"
            )

            await update.message.reply_text(
                "❌ Не удалось отправить сообщение."
            )

        return

    # --------------------------------------------------------
    # ANONYMOUS CHAT
    # --------------------------------------------------------

    partner_id = user["partner_id"]

    if not partner_id:
        return

    try:

        # TEXT

        if update.message.text:

            await context.bot.send_message(
                partner_id,
                update.message.text,
            )

            return

        # PHOTO

        if update.message.photo:

            if not media_allowed(
                context,
                user_id,
            ):

                await update.message.reply_text(
                    "⚠️ Максимум 5 фото/видео "
                    "за 10 секунд."
                )

                return

            await context.bot.send_photo(
                partner_id,
                update.message.photo[-1].file_id,
                caption=update.message.caption,
            )

            return

        # VIDEO

        if update.message.video:

            if not media_allowed(
                context,
                user_id,
            ):

                await update.message.reply_text(
                    "⚠️ Максимум 5 фото/видео "
                    "за 10 секунд."
                )

                return

            await context.bot.send_video(
                partner_id,
                update.message.video.file_id,
                caption=update.message.caption,
            )

            return

        # VIDEO NOTE / КРУЖОЧЕК

        if update.message.video_note:

            if not media_allowed(
                context,
                user_id,
            ):

                await update.message.reply_text(
                    "⚠️ Максимум 5 медиа "
                    "за 10 секунд."
                )

                return

            await context.bot.send_video_note(
                partner_id,
                update.message.video_note.file_id,
            )

            return

        # VOICE

        if update.message.voice:

            await context.bot.send_voice(
                partner_id,
                update.message.voice.file_id,
            )

            return

        # STICKER

        if update.message.sticker:

            await context.bot.send_sticker(
                partner_id,
                update.message.sticker.file_id,
            )

            return

        # DOCUMENT

        if update.message.document:

            await context.bot.send_document(
                partner_id,
                update.message.document.file_id,
            )

            return

    except Exception as error:

        logger.error(
            f"Forward error: {error}"
        )


# ============================================================
# ADMIN
# ============================================================

def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Статистика",
                callback_data="admin_stats",
            )
        ],
        [
            InlineKeyboardButton(
                "💎 Выдать Premium",
                callback_data="admin_premium",
            )
        ],
        [
            InlineKeyboardButton(
                "🚫 Бан",
                callback_data="admin_ban",
            ),
            InlineKeyboardButton(
                "♻️ Разбан",
                callback_data="admin_unban",
            ),
        ],
    ])


async def admin_panel(update, context):
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:

        await update.message.reply_text(
            "⛔ Доступ запрещён."
        )

        return

    await update.message.reply_text(
        "🛡 <b>АДМИН-ПАНЕЛЬ</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard(),
    )


async def admin_callback(update, context):
    query = update.callback_query
    await query.answer()

    admin_id = query.from_user.id

    if admin_id not in ADMIN_IDS:
        return

    action = query.data

    if action == "admin_stats":

        connection = db()

        users = connection.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        registered = connection.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE registered = 1
            """
        ).fetchone()[0]

        banned = connection.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE banned = 1
            """
        ).fetchone()[0]

        premium = connection.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE premium_until IS NOT NULL
            """
        ).fetchone()[0]

        stars = connection.execute(
            """
            SELECT COALESCE(SUM(stars), 0)
            FROM payments
            """
        ).fetchone()[0]

        reports = connection.execute(
            "SELECT COUNT(*) FROM reports"
        ).fetchone()[0]

        connection.close()

        await query.message.reply_text(
            "📊 <b>СТАТИСТИКА</b>\n\n"
            f"👤 Пользователей: {users}\n"
            f"✅ Зарегистрировано: {registered}\n"
            f"💎 Premium: {premium}\n"
            f"🚫 Заблокировано: {banned}\n"
            f"⭐ Получено Stars: {stars}\n"
            f"🚨 Жалоб: {reports}",
            parse_mode=ParseMode.HTML,
        )

        return

    if action == "admin_premium":

        context.user_data[
            "admin_action"
        ] = "premium"

        await query.message.reply_text(
            "💎 Отправь Telegram ID пользователя."
        )

        return

    if action == "admin_ban":

        context.user_data[
            "admin_action"
        ] = "ban"

        await query.message.reply_text(
            "🚫 Отправь Telegram ID пользователя."
        )

        return

    if action == "admin_unban":

        context.user_data[
            "admin_action"
        ] = "unban"

        await query.message.reply_text(
            "♻️ Отправь Telegram ID пользователя."
        )


async def admin_text(update, context):
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        return

    action = context.user_data.get(
        "admin_action"
    )

    if not action:
        return

    if not update.message.text.isdigit():

        await update.message.reply_text(
            "❌ Отправь только Telegram ID."
        )

        return

    target_id = int(
        update.message.text
    )

    ensure_user(target_id)

    if action == "premium":

        until = give_premium(
            target_id,
            7,
        )

        await update.message.reply_text(
            "💎 Premium выдан на 7 дней.\n\n"
            f"ID: <code>{target_id}</code>\n"
            f"До: {until.strftime('%d.%m.%Y %H:%M UTC')}",
            parse_mode=ParseMode.HTML,
        )

        try:

            await context.bot.send_message(
                target_id,
                "🎁 Администратор выдал вам Premium на 7 дней.",
            )

        except Exception:
            pass

    elif action == "ban":

        update_user(
            target_id,
            banned=1,
            searching=0,
            partner_id=None,
        )

        await update.message.reply_text(
            f"🚫 ID <code>{target_id}</code> заблокирован.",
            parse_mode=ParseMode.HTML,
        )

    elif action == "unban":

        update_user(
            target_id,
            banned=0,
        )

        await update.message.reply_text(
            f"♻️ ID <code>{target_id}</code> разблокирован.",
            parse_mode=ParseMode.HTML,
        )

    context.user_data.pop(
        "admin_action",
        None,
    )


# ============================================================
# COMMANDS
# ============================================================

async def rules_command(update, context):
    await update.message.reply_text(
        RULES,
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# TEXT HANDLER
# ============================================================

async def text_handler(update, context):
    if not update.message:
        return

    user_id = update.effective_user.id

    ensure_user(user_id)

    user = get_user(user_id)

    if user["banned"]:

        await update.message.reply_text(
            "🚫 Ваш аккаунт заблокирован."
        )

        return

    # ADMIN

    if (
        user_id in ADMIN_IDS
        and context.user_data.get(
            "admin_action"
        )
    ):

        await admin_text(
            update,
            context,
        )

        return

    text = update.message.text

    # MAIN MENU

    if text == "🔎 Найти собеседника":

        await find_button(
            update,
            context,
        )

        return

    if text == "💎 Premium":

        await premium_menu(
            update,
            context,
        )

        return

    if text == "👤 Профиль":

        await profile(
            update,
            context,
        )

        return

    if text == "👥 Друзья":

        await friends_menu(
            update,
            context,
        )

        return

    if text == "📜 Правила":

        await rules_command(
            update,
            context,
        )

        return

    if text == "🛡 Админ-панель":

        await admin_panel(
            update,
            context,
        )

        return

    # CHAT

    await forward_message(
        update,
        context,
    )


# ============================================================
# CALLBACK ROUTER
# ============================================================

async def callback_router(update, context):
    query = update.callback_query

    data = query.data

    # AGE

    if data in (
        "adult_yes",
        "adult_no",
    ):

        await registration_callback(
            update,
            context,
        )

        return

    # GENDER

    if data.startswith("gender_"):

        await registration_callback(
            update,
            context,
        )

        return

    # PREMIUM

    if data.startswith("premium_"):

        await premium_payment(
            update,
            context,
        )

        return

    # SEARCH

    if data.startswith("search_"):

        await search_callback(
            update,
            context,
        )

        return

    # FRIEND REQUEST

    if data == "friend_request":

        await friend_request_callback(
            update,
            context,
        )

        return

    if data.startswith("friend_accept_"):

        await query.answer()

        try:

            from_user = int(
                data.replace(
                    "friend_accept_",
                    "",
                )
            )

        except ValueError:
            return

        await friend_accept_callback(
            update,
            context,
            from_user,
        )

        return

    if data.startswith("friend_reject_"):

        await query.answer()

        try:

            from_user = int(
                data.replace(
                    "friend_reject_",
                    "",
                )
            )

        except ValueError:
            return

        await friend_reject_callback(
            update,
            context,
            from_user,
        )

        return

    if data == "friend_requests":

        await friend_requests_menu(
            update,
            context,
        )

        return

    if data.startswith("friend_chat_"):

        await friend_chat_callback(
            update,
            context,
        )

        return

    # CHAT

    if data == "next":

        await next_callback(
            update,
            context,
        )

        return

    if data == "stop":

        await stop_callback(
            update,
            context,
        )

        return

    if data == "block":

        await block_callback(
            update,
            context,
        )

        return

    if data == "report":

        await report_callback(
            update,
            context,
        )

        return

    # ADMIN

    if data.startswith("admin_"):

        await admin_callback(
            update,
            context,
        )

        return


# ============================================================
# ERROR
# ============================================================

async def error_handler(update, context):
    logger.error(
        "Ошибка бота:",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        print(
            "\n"
            "ОШИБКА: не задан BOT_TOKEN.\n\n"
            "В Render создай Environment Variable:\n\n"
            "Name: BOT_TOKEN\n"
            "Value: токен от BotFather\n\n"
        )

        return

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # COMMANDS

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "rules",
            rules_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "admin",
            admin_panel,
        )
    )

    application.add_handler(
        CommandHandler(
            "stopfriend",
            stop_friend_chat,
        )
    )

    # PAYMENTS

    application.add_handler(
        PreCheckoutQueryHandler(
            pre_checkout,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment,
        )
    )

    # CALLBACKS

    application.add_handler(
        CallbackQueryHandler(
            callback_router,
        )
    )

    # ALL OTHER MESSAGES

    application.add_handler(
        MessageHandler(
            ~filters.COMMAND,
            text_handler,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "================================"
    )

    logger.info(
        "       ANONCHAT 18+"
    )

    logger.info(
        "       BOT STARTED"
    )

    logger.info(
        "================================"
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
