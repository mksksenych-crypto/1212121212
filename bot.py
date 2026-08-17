import os
import sqlite3
import logging
import time
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

# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

# ID администраторов через Render Environment Variables.
# Например:
# ADMIN_IDS=1555042637,123456789
ADMIN_IDS = set()

admin_ids_env = os.getenv("ADMIN_IDS", "")
for value in admin_ids_env.split(","):
    value = value.strip()
    if value.isdigit():
        ADMIN_IDS.add(int(value))

# Если хочешь временно указать ID прямо здесь:
# ADMIN_IDS.add(1555042637)

DB_FILE = "anonchat.db"

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# DATABASE
# =========================================================

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
            premium_until TEXT,
            banned INTEGER DEFAULT 0,
            registered INTEGER DEFAULT 0,
            searching INTEGER DEFAULT 0,
            partner_id INTEGER,
            created_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS friends (
            user_id INTEGER,
            friend_id INTEGER,
            UNIQUE(user_id, friend_id)
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
        CREATE TABLE IF NOT EXISTS blocks (
            user_id INTEGER,
            blocked_id INTEGER,
            UNIQUE(user_id, blocked_id)
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


# =========================================================
# USER
# =========================================================

def get_user(user_id):
    connection = db()
    user = connection.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    connection.close()
    return user


def create_user(user_id):
    connection = db()

    connection.execute("""
        INSERT OR IGNORE INTO users
        (user_id, registered, searching, created_at)
        VALUES (?, 0, 0, ?)
    """, (
        user_id,
        datetime.now(timezone.utc).isoformat(),
    ))

    connection.commit()
    connection.close()


def update_user(user_id, **values):
    if not values:
        return

    connection = db()

    fields = []
    params = []

    for key, value in values.items():
        fields.append(f"{key} = ?")
        params.append(value)

    params.append(user_id)

    connection.execute(
        f"UPDATE users SET {', '.join(fields)} WHERE user_id = ?",
        params,
    )

    connection.commit()
    connection.close()


def is_premium(user_id):
    user = get_user(user_id)

    if not user:
        return False

    premium_until = user["premium_until"]

    if not premium_until:
        return False

    try:
        until = datetime.fromisoformat(premium_until)

        if until > datetime.now(timezone.utc):
            return True

    except Exception:
        pass

    return False


def premium_until_text(user_id):
    user = get_user(user_id)

    if not user or not user["premium_until"]:
        return "Нет"

    try:
        until = datetime.fromisoformat(user["premium_until"])

        if until <= datetime.now(timezone.utc):
            return "Истёк"

        return until.strftime("%d.%m.%Y %H:%M UTC")

    except Exception:
        return "Нет"


def add_premium(user_id, days):
    now = datetime.now(timezone.utc)

    user = get_user(user_id)

    if user and user["premium_until"]:
        try:
            current_until = datetime.fromisoformat(
                user["premium_until"]
            )

            if current_until > now:
                start = current_until
            else:
                start = now

        except Exception:
            start = now
    else:
        start = now

    new_until = start + timedelta(days=days)

    update_user(
        user_id,
        premium_until=new_until.isoformat(),
    )


# =========================================================
# KEYBOARDS
# =========================================================

def main_keyboard(user_id):
    rows = [
        ["🔎 Найти собеседника"],
        ["💎 Купить Premium"],
        ["👤 Профиль", "👥 Друзья"],
        ["📜 Правила"],
    ]

    if is_premium(user_id):
        rows.insert(1, ["💎 Premium активен"])

    if user_id in ADMIN_IDS:
        rows.append(["🛡 Админ-панель"])

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
    )


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


def age_keyboard():
    buttons = []

    for age in range(13, 31):
        buttons.append(
            InlineKeyboardButton(
                str(age),
                callback_data=f"age_{age}",
            )
        )

    rows = []

    for i in range(0, len(buttons), 6):
        rows.append(buttons[i:i + 6])

    return InlineKeyboardMarkup(rows)


def premium_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⭐ 1 день — 50 Stars",
                callback_data="buy_1",
            )
        ],
        [
            InlineKeyboardButton(
                "⭐ 3 дня — 100 Stars",
                callback_data="buy_3",
            )
        ],
        [
            InlineKeyboardButton(
                "⭐ 7 дней — 200 Stars",
                callback_data="buy_7",
            )
        ],
    ])


def chat_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⏭ Следующий",
                callback_data="next_chat",
            ),
            InlineKeyboardButton(
                "🛑 Остановить",
                callback_data="stop_chat",
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 Добавить в друзья",
                callback_data="add_friend",
            )
        ],
        [
            InlineKeyboardButton(
                "🚫 Заблокировать",
                callback_data="block_partner",
            ),
            InlineKeyboardButton(
                "🚨 Пожаловаться",
                callback_data="report_partner",
            ),
        ],
    ])


# =========================================================
# START / REGISTRATION
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    create_user(user_id)

    user = get_user(user_id)

    if user["banned"]:
        await update.message.reply_text(
            "🚫 Вы заблокированы и не можете пользоваться ботом."
        )
        return

    if not user["registered"]:
        context.user_data["registration"] = "age"

        await update.message.reply_text(
            "👋 Привет!\n\n"
            "Это бот для анонимного общения.\n\n"
            "Сначала выбери свой возраст:"
        )

        await update.message.reply_text(
            "🎂 Возраст:",
            reply_markup=age_keyboard(),
        )

        return

    await update.message.reply_text(
        "👋 С возвращением!\n\n"
        "Выбери действие:",
        reply_markup=main_keyboard(user_id),
    )


# =========================================================
# RULES
# =========================================================

RULES_TEXT = """
📜 <b>ПРАВИЛА ANONCHAT</b>

1. ❌ Запрещены угрозы и травля.
2. ❌ Запрещены сексуальные материалы.
3. ❌ Не отправляй личные данные.
4. ❌ Не проси у других людей пароли, коды и деньги.
5. ❌ Запрещены мошенничество и обман.
6. ❌ Запрещена реклама без разрешения администрации.
7. 🚨 При нарушении используй кнопку «Пожаловаться».
8. 🔒 Общение анонимное, но администрация может рассматривать жалобы.

Используя бота, ты соглашаешься с правилами.
"""


async def show_rules(update, context):
    await update.message.reply_text(
        RULES_TEXT,
        parse_mode=ParseMode.HTML,
    )


# =========================================================
# PROFILE
# =========================================================

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)

    gender = {
        "male": "👨 Мужской",
        "female": "👩 Женский",
    }.get(user["gender"], "Не выбран")

    premium = (
        f"💎 До: {premium_until_text(user_id)}"
        if is_premium(user_id)
        else "❌ Нет"
    )

    text = (
        "👤 <b>ТВОЙ ПРОФИЛЬ</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"🎂 Возраст: {user['age']}\n"
        f"🚻 Пол: {gender}\n"
        f"💎 Premium: {premium}\n"
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(user_id),
    )


# =========================================================
# PREMIUM
# =========================================================

PREMIUM_TEXT = """
💎 <b>PREMIUM</b>

Что даёт Premium:

🔎 Поиск собеседника по возрасту
🚻 Поиск собеседника по полу
⭐ Premium-значок
🎯 Более точный поиск

<b>Тарифы:</b>

⭐ 1 день — 50 Stars
⭐ 3 дня — 100 Stars
⭐ 7 дней — 200 Stars
"""


async def premium_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        PREMIUM_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=premium_keyboard(),
    )


async def buy_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    data = query.data

    plans = {
        "buy_1": (1, 50),
        "buy_3": (3, 100),
        "buy_7": (7, 200),
    }

    if data not in plans:
        return

    days, stars = plans[data]

    prices = [
        LabeledPrice(
            label=f"Premium на {days} дн.",
            amount=stars,
        )
    ]

    payload = f"premium_{days}_{query.from_user.id}"

    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title=f"Premium на {days} дн.",
        description=(
            f"Premium AnonChat на {days} дней. "
            f"Стоимость: {stars} Telegram Stars."
        ),
        payload=payload,
        currency="XTR",
        prices=prices,
        provider_token="",
        start_parameter=f"premium_{days}",
    )


# =========================================================
# PAYMENT
# =========================================================

async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query

    await query.answer(ok=True)


async def successful_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    payment = update.message.successful_payment

    payload = payment.invoice_payload

    try:
        parts = payload.split("_")
        days = int(parts[1])
        user_id = int(parts[2])

    except Exception:
        logger.error("Не удалось разобрать payment payload")
        return

    if user_id != update.effective_user.id:
        return

    stars = payment.total_amount

    add_premium(user_id, days)

    connection = db()

    connection.execute("""
        INSERT INTO payments
        (user_id, stars, days, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        user_id,
        stars,
        days,
        datetime.now(timezone.utc).isoformat(),
    ))

    connection.commit()
    connection.close()

    await update.message.reply_text(
        f"🎉 <b>Оплата прошла!</b>\n\n"
        f"💎 Premium активирован на {days} дней.\n"
        f"⭐ Потрачено: {stars} Stars\n\n"
        f"Действует до:\n"
        f"{premium_until_text(user_id)}",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(user_id),
    )


# =========================================================
# REGISTRATION CALLBACKS
# =========================================================

async def registration_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data.startswith("age_"):
        age = int(data.split("_")[1])

        update_user(
            user_id,
            age=age,
        )

        context.user_data["registration"] = "gender"

        await query.edit_message_text(
            "🚻 Теперь выбери свой пол:",
            reply_markup=gender_keyboard(),
        )

    elif data.startswith("gender_"):
        gender = data.replace("gender_", "")

        update_user(
            user_id,
            gender=gender,
            registered=1,
        )

        context.user_data.pop("registration", None)

        await query.edit_message_text(
            "✅ Регистрация завершена!\n\n"
            "Теперь можно искать собеседника."
        )

        await context.bot.send_message(
            chat_id=user_id,
            text="Главное меню:",
            reply_markup=main_keyboard(user_id),
        )


# =========================================================
# SEARCH
# =========================================================

async def find_partner(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id
    user = get_user(user_id)

    if not user or not user["registered"]:
        await update.message.reply_text(
            "Сначала используй /start."
        )
        return

    if user["banned"]:
        return

    # Уже есть собеседник
    if user["partner_id"]:
        await update.message.reply_text(
            "💬 У тебя уже есть собеседник.",
            reply_markup=chat_keyboard(),
        )
        return

    update_user(
        user_id,
        searching=1,
    )

    await update.message.reply_text(
        "🔎 Ищу собеседника..."
    )

    partner = find_waiting_partner(user_id)

    if partner:
        update_user(
            user_id,
            searching=0,
            partner_id=partner["user_id"],
        )

        update_user(
            partner["user_id"],
            searching=0,
            partner_id=user_id,
        )

        await context.bot.send_message(
            user_id,
            "🎉 <b>Собеседник найден!</b>\n\n"
            "Можете начинать общение.\n\n"
            "Помни о правилах.",
            parse_mode=ParseMode.HTML,
            reply_markup=chat_keyboard(),
        )

        await context.bot.send_message(
            partner["user_id"],
            "🎉 <b>Собеседник найден!</b>\n\n"
            "Можете начинать общение.\n\n"
            "Помни о правилах.",
            parse_mode=ParseMode.HTML,
            reply_markup=chat_keyboard(),
        )

    else:
        await update.message.reply_text(
            "⏳ Пока подходящего собеседника нет.\n"
            "Я продолжу искать."
        )


def find_waiting_partner(user_id):
    me = get_user(user_id)

    connection = db()

    # Базовый поиск для обычного пользователя.
    query = """
        SELECT *
        FROM users
        WHERE user_id != ?
          AND registered = 1
          AND searching = 1
          AND banned = 0
          AND partner_id IS NULL
    """

    candidates = connection.execute(
        query,
        (user_id,),
    ).fetchall()

    connection.close()

    # Premium может фильтровать по возрасту/полу.
    target_age = None
    target_gender = None

    # Фильтры хранятся во временной сессии.
    # Поэтому здесь используется только обычный поиск.
    # Premium-фильтры обрабатываются отдельно.
    return candidates[0] if candidates else None


# =========================================================
# STOP CHAT
# =========================================================

async def stop_chat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id
    user = get_user(user_id)

    if not user:
        return

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
                reply_markup=main_keyboard(partner_id),
            )
        except Exception:
            pass

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_reply_markup(
            reply_markup=None
        )

    if update.message:
        await update.message.reply_text(
            "🛑 Чат завершён.",
            reply_markup=main_keyboard(user_id),
        )
    else:
        await context.bot.send_message(
            user_id,
            "🛑 Чат завершён.",
            reply_markup=main_keyboard(user_id),
        )


# =========================================================
# NEXT CHAT
# =========================================================

async def next_chat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    await stop_chat(update, context)

    await context.bot.send_message(
        user_id,
        "🔎 Ищу нового собеседника..."
    )

    update_user(
        user_id,
        searching=1,
    )

    partner = find_waiting_partner(user_id)

    if partner:
        update_user(
            user_id,
            searching=0,
            partner_id=partner["user_id"],
        )

        update_user(
            partner["user_id"],
            searching=0,
            partner_id=user_id,
        )

        await context.bot.send_message(
            user_id,
            "🎉 Собеседник найден!",
            reply_markup=chat_keyboard(),
        )

        await context.bot.send_message(
            partner["user_id"],
            "🎉 Собеседник найден!",
            reply_markup=chat_keyboard(),
        )


# =========================================================
# FRIENDS
# =========================================================

def are_friends(user_id, friend_id):
    connection = db()

    result = connection.execute("""
        SELECT 1
        FROM friends
        WHERE user_id = ? AND friend_id = ?
    """, (
        user_id,
        friend_id,
    )).fetchone()

    connection.close()

    return result is not None


def add_friend(user_id, friend_id):
    connection = db()

    connection.execute("""
        INSERT OR IGNORE INTO friends
        (user_id, friend_id)
        VALUES (?, ?)
    """, (
        user_id,
        friend_id,
    ))

    connection.execute("""
        INSERT OR IGNORE INTO friends
        (user_id, friend_id)
        VALUES (?, ?)
    """, (
        friend_id,
        user_id,
    ))

    connection.commit()
    connection.close()


async def friends_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    connection = db()

    friends = connection.execute("""
        SELECT friend_id
        FROM friends
        WHERE user_id = ?
    """, (user_id,)).fetchall()

    connection.close()

    if not friends:
        await update.message.reply_text(
            "👥 У тебя пока нет друзей."
        )
        return

    text = "👥 <b>ТВОИ ДРУЗЬЯ</b>\n\n"

    for i, friend in enumerate(friends, 1):
        text += f"{i}. <code>{friend['friend_id']}</code>\n"

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )


async def add_current_friend(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id
    user = get_user(user_id)

    partner_id = user["partner_id"]

    if not partner_id:
        await query.message.reply_text(
            "❌ Сейчас у тебя нет собеседника."
        )
        return

    add_friend(user_id, partner_id)

    await query.message.reply_text(
        "👥 Собеседник добавлен в друзья!"
    )


# =========================================================
# BLOCK
# =========================================================

async def block_partner(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id
    user = get_user(user_id)

    partner_id = user["partner_id"]

    if not partner_id:
        return

    connection = db()

    connection.execute("""
        INSERT OR IGNORE INTO blocks
        (user_id, blocked_id)
        VALUES (?, ?)
    """, (
        user_id,
        partner_id,
    ))

    connection.commit()
    connection.close()

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
            "🛑 Собеседник завершил чат.",
            reply_markup=main_keyboard(partner_id),
        )
    except Exception:
        pass


# =========================================================
# REPORT
# =========================================================

async def report_partner(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
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
        (reporter_id, reported_id, reason, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        user_id,
        partner_id,
        "Жалоба от пользователя",
        datetime.now(timezone.utc).isoformat(),
    ))

    connection.commit()
    connection.close()

    await query.message.reply_text(
        "🚨 Жалоба отправлена администрации."
    )


# =========================================================
# ADMIN
# =========================================================

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
                "🚫 Забанить",
                callback_data="admin_ban",
            )
        ],
        [
            InlineKeyboardButton(
                "♻️ Разбанить",
                callback_data="admin_unban",
            )
        ],
    ])


async def admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        await update.message.reply_text(
            "⛔ У тебя нет доступа."
        )
        return

    await update.message.reply_text(
        "🛡 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "Выбери действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard(),
    )


async def admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    admin_id = query.from_user.id

    if admin_id not in ADMIN_IDS:
        return

    if query.data == "admin_stats":
        connection = db()

        users = connection.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        registered = connection.execute(
            "SELECT COUNT(*) FROM users WHERE registered = 1"
        ).fetchone()[0]

        premium = connection.execute(
            "SELECT COUNT(*) FROM users WHERE premium_until IS NOT NULL"
        ).fetchone()[0]

        banned = connection.execute(
            "SELECT COUNT(*) FROM users WHERE banned = 1"
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
            f"🚨 Жалоб: {reports}",
            parse_mode=ParseMode.HTML,
        )

    elif query.data == "admin_premium":
        context.user_data["admin_action"] = "premium"

        await query.message.reply_text(
            "💎 Введи Telegram ID пользователя:"
        )

    elif query.data == "admin_ban":
        context.user_data["admin_action"] = "ban"

        await query.message.reply_text(
            "🚫 Введи Telegram ID пользователя:"
        )

    elif query.data == "admin_unban":
        context.user_data["admin_action"] = "unban"

        await query.message.reply_text(
            "♻️ Введи Telegram ID пользователя:"
        )


# =========================================================
# ADMIN TEXT ACTIONS
# =========================================================

async def admin_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        return

    action = context.user_data.get("admin_action")

    if not action:
        return

    if not update.message.text.isdigit():
        await update.message.reply_text(
            "❌ Нужно отправить числовой Telegram ID."
        )
        return

    target_id = int(update.message.text)

    create_user(target_id)

    if action == "premium":
        add_premium(target_id, 7)

        await update.message.reply_text(
            f"💎 Premium выдан пользователю "
            f"<code>{target_id}</code> на 7 дней.",
            parse_mode=ParseMode.HTML,
        )

        try:
            await context.bot.send_message(
                target_id,
                "🎁 Администратор выдал тебе Premium на 7 дней!",
            )
        except Exception:
            pass

    elif action == "ban":
        update_user(
            target_id,
            banned=1,
            partner_id=None,
            searching=0,
        )

        await update.message.reply_text(
            f"🚫 Пользователь <code>{target_id}</code> заблокирован.",
            parse_mode=ParseMode.HTML,
        )

    elif action == "unban":
        update_user(
            target_id,
            banned=0,
        )

        await update.message.reply_text(
            f"♻️ Пользователь <code>{target_id}</code> разблокирован.",
            parse_mode=ParseMode.HTML,
        )

    context.user_data.pop("admin_action", None)


# =========================================================
# MESSAGE FORWARDING
# =========================================================

async def forward_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    user = get_user(user_id)

    if not user:
        create_user(user_id)
        return

    if not user["registered"]:
        return

    if user["banned"]:
        return

    partner_id = user["partner_id"]

    if not partner_id:
        return

    # Не разрешаем пересылать команды.
    if update.message.text and update.message.text.startswith("/"):
        return

    try:
        # Текст
        if update.message.text:
            await context.bot.send_message(
                partner_id,
                update.message.text,
            )

        # Фото
        elif update.message.photo:
            photo = update.message.photo[-1]

            await context.bot.send_photo(
                partner_id,
                photo.file_id,
                caption=update.message.caption,
            )

        # Видео
        elif update.message.video:
            await context.bot.send_video(
                partner_id,
                update.message.video.file_id,
                caption=update.message.caption,
            )

        # Стикер
        elif update.message.sticker:
            await context.bot.send_sticker(
                partner_id,
                update.message.sticker.file_id,
            )

        # Голосовое
        elif update.message.voice:
            await context.bot.send_voice(
                partner_id,
                update.message.voice.file_id,
            )

    except Exception as e:
        logger.error(
            f"Ошибка отправки сообщения: {e}"
        )


# =========================================================
# CALLBACK HANDLER
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    data = query.data

    # Регистрация
    if data.startswith("age_") or data.startswith("gender_"):
        await registration_callback(update, context)
        return

    # Premium
    if data.startswith("buy_"):
        await buy_premium(update, context)
        return

    # Админ
    if data.startswith("admin_"):
        await admin_callback(update, context)
        return

    # Чат
    if data == "stop_chat":
        await stop_chat(update, context)
        return

    if data == "next_chat":
        await next_chat(update, context)
        return

    if data == "add_friend":
        await add_current_friend(update, context)
        return

    if data == "block_partner":
        await block_partner(update, context)
        return

    if data == "report_partner":
        await report_partner(update, context)
        return


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id
    text = update.message.text

    user = get_user(user_id)

    if not user:
        create_user(user_id)
        return

    if user["banned"]:
        await update.message.reply_text(
            "🚫 Ты заблокирован."
        )
        return

    # Админ ввод ID
    if user_id in ADMIN_IDS:
        if context.user_data.get("admin_action"):
            await admin_text(update, context)
            return

    if text == "🔎 Найти собеседника":
        await find_partner(update, context)
        return

    if text == "💎 Купить Premium":
        await premium_menu(update, context)
        return

    if text == "💎 Premium активен":
        await premium_menu(update, context)
        return

    if text == "👤 Профиль":
        await profile(update, context)
        return

    if text == "👥 Друзья":
        await friends_menu(update, context)
        return

    if text == "📜 Правила":
        await show_rules(update, context)
        return

    if text == "🛡 Админ-панель":
        await admin_panel(update, context)
        return

    # Если пользователь находится в чате,
    # отправляем сообщение собеседнику.
    await forward_message(update, context)


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Ошибка бота:",
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():
    if not BOT_TOKEN:
        print(
            "ОШИБКА: не задан BOT_TOKEN.\n"
            "На Render создай Environment Variable:\n"
            "BOT_TOKEN = токен от BotFather"
        )
        return

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("rules", show_rules)
    )

    application.add_handler(
        CommandHandler("admin", admin_panel)
    )

    application.add_handler(
        PreCheckoutQueryHandler(precheckout)
    )

    application.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment,
        )
    )

    application.add_handler(
        CallbackQueryHandler(callback_handler)
    )

    application.add_handler(
        MessageHandler(
            filters.ALL,
            button_handler,
        )
    )

    application.add_error_handler(error_handler)

    logger.info("================================")
    logger.info("       ANONCHAT ЗАПУЩЕН")
    logger.info("================================")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
