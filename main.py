import logging
import sqlite3
from datetime import datetime, timedelta, date, time
import re
from typing import List, Tuple, Optional

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters
)

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- DB SETTINGS ---
DB_PATH = "beauty_bookings.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            duration_hours INTEGER NOT NULL,
            services TEXT NOT NULL,
            client_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

# --- CONSTANTS ---
MON_SAT = [0,1,2,3,4,5]
WORK_START = 9
WORK_END = 21     # 21:00 is the finishing time

SERVICES = {
    "manicure": "Маникюр",
    "pedicure": "Педикюр",
    "both": "Маникюр + Педикюр"
}

COVERINGS = {
    "none": "Без покрытия",
    "gel": "С гель-лаком"
}

# --- Conversation States ---
(
    CHOOSING_SERVICE,
    CHOOSING_COVER,
    CHOOSING_DATE,
    CHOOSING_TIME,
    ASK_NAME,
    ASK_PHONE,
    CONFIRM
) = range(7)

# --- Helpers for slots ---
def time_range(start: int, end: int) -> List[Tuple[str, str]]:
    """Returns list of (start, end) strings in HH:MM."""
    return [
        (f"{h:02}:00", f"{h+1:02}:00")
        for h in range(start, end)
    ]

def slot_to_time(slot: Tuple[str, str]) -> Tuple[time, time]:
    return (
        datetime.strptime(slot[0], "%H:%M").time(),
        datetime.strptime(slot[1], "%H:%M").time()
    )

def get_booking_slots(duration: int) -> List[Tuple[str, str]]:
    """Return all possible starting slots for a given duration (in hours)."""
    slots = []
    for start_hour in range(WORK_START, WORK_END - duration + 1):
        slots.append((
            f"{start_hour:02}:00",
            f"{start_hour+duration:02}:00"
        ))
    return slots

def get_week_dates() -> List[date]:
    today = date.today()
    days = []
    for i in range(14): # allow booking for next 2 weeks
        d = today + timedelta(days=i)
        if d.weekday() in MON_SAT:
            days.append(d)
    return days

def service_duration(services, covers) -> int:
    # services: str key. covers: dict
    if services == "manicure":
        return 1 if covers["manicure"] == "none" else 2
    elif services == "pedicure":
        return 1 if covers["pedicure"] == "none" else 2
    elif services == "both":
        man = covers['manicure']
        ped = covers['pedicure']
        if man == "none" and ped == "none":
            return 2
        elif man == "gel" and ped == "gel":
            return 4
        else:
            return 3

# --- DB Logic ---
def is_slot_available(date_str: str, start: str, end: str) -> bool:
    """Check if time slot is fully available on a specific date."""
    start_dt = datetime.strptime(f"{date_str} {start}", "%Y-%m-%d %H:%M")
    end_dt = datetime.strptime(f"{date_str} {end}", "%Y-%m-%d %H:%M")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        SELECT start_time, end_time FROM bookings
        WHERE date = ?
    ''', (date_str,))
    for row in cur.fetchall():
        booked_start = datetime.strptime(f"{date_str} {row[0]}", "%Y-%m-%d %H:%M")
        booked_end = datetime.strptime(f"{date_str} {row[1]}", "%Y-%m-%d %H:%M")
        # if intervals overlap
        if max(start_dt, booked_start) < min(end_dt, booked_end):
            conn.close()
            return False
    conn.close()
    return True

def get_available_date_slots(duration: int) -> List[Tuple[str, List[Tuple[str,str]]]]:
    """Return available dates and their allowed time slots for service of duration, e.g. [(date1, [slot1,slot2]), ...]"""
    result = []
    for d in get_week_dates():
        slots = []
        for slot in get_booking_slots(duration):
            if is_slot_available(d.strftime("%Y-%m-%d"), slot[0], slot[1]):
                slots.append(slot)
        if slots:
            result.append((d.strftime("%Y-%m-%d"), slots))
    return result

def save_booking(date, start, end, duration, services, client_name, phone):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO bookings
         (date, start_time, end_time, duration_hours, services, client_name, phone, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        date,
        start,
        end,
        duration,
        services,
        client_name,
        phone,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    conn.commit()
    conn.close()

# --- Data validation ---
def clean_phone(phone: str) -> Optional[str]:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]  # replace Russian 8 with 7
    if len(digits) >= 10:
        return digits[-10:]
    return None

# --- Telegram Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Записаться", callback_data="book")]
    ]
    await update.message.reply_text(
        "Здравствуйте! Я Beauty Bot. Я помогу вам записаться на маникюр и/или педикюр.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING_SERVICE

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # --- Choose service ---
    if data == "book":
        keyboard = [
            [InlineKeyboardButton(SERVICES["manicure"], callback_data="service-manicure")],
            [InlineKeyboardButton(SERVICES["pedicure"], callback_data="service-pedicure")],
            [InlineKeyboardButton(SERVICES["both"], callback_data="service-both")],
        ]
        await query.edit_message_text(
            "Пожалуйста, выберите услугу:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CHOOSING_COVER

    # --- Choose cover for services ---
    if data.startswith("service-"):
        service_choice = data.split("-")[1]
        context.user_data["service"] = service_choice
        context.user_data["covers"] = {}
        # For combinations, ask separately
        if service_choice == "both":
            keyboard = [
                [InlineKeyboardButton(COVERINGS["none"], callback_data="cover-manicure-none")],
                [InlineKeyboardButton(COVERINGS["gel"], callback_data="cover-manicure-gel")]
            ]
            await query.edit_message_text(
                "Маникюр: выберите покрытие:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return CHOOSING_COVER
        else:
            cover_target = "manicure" if service_choice == "manicure" else "pedicure"
            keyboard = [
                [InlineKeyboardButton(COVERINGS["none"], callback_data=f"cover-{cover_target}-none")],
                [InlineKeyboardButton(COVERINGS["gel"], callback_data=f"cover-{cover_target}-gel")]
            ]
            await query.edit_message_text(
                "Выберите покрытие:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return CHOOSING_DATE

    # Handle coverings
    if data.startswith("cover-"):
        _, target, cov = data.split("-")
        context.user_data["covers"][target] = cov
        service_choice = context.user_data["service"]
        if service_choice == "both":
            # If manicure chosen, ask for pedicure coverage
            if len(context.user_data["covers"]) == 1:
                keyboard = [
                    [InlineKeyboardButton(COVERINGS["none"], callback_data="cover-pedicure-none")],
                    [InlineKeyboardButton(COVERINGS["gel"], callback_data="cover-pedicure-gel")]
                ]
                await query.edit_message_text(
                    "Педикюр: выберите покрытие:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return CHOOSING_DATE
        # Coverage chosen for all
        return await offer_dates(query, context)

    # Step: pick date
    if data.startswith("pick-date-"):
        picked_date = data.removeprefix("pick-date-")
        context.user_data["date"] = picked_date
        duration = service_duration(context.user_data["service"], context.user_data["covers"])
        available_slots = []
        all_slots = get_booking_slots(duration)
        for slot in all_slots:
            if is_slot_available(picked_date, slot[0], slot[1]):
                available_slots.append(slot)
        # No slots
        if not available_slots:
            await query.edit_message_text("⚠️ Нет свободных слотов на эту дату. Пожалуйста, выберите другую дату.")
            return await offer_dates(query, context)
        # Show slots
        kb = []
        for slot in available_slots:
            kb.append([InlineKeyboardButton(f"{slot[0]}–{slot[1]}", callback_data=f"pick-time-{slot[0]}-{slot[1]}")])
        await query.edit_message_text(
            f"Выберите время на {picked_date}:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return CHOOSING_TIME

    # Step: pick time
    if data.startswith("pick-time-"):
        _, start, end = data.split("-")
        context.user_data["start_time"] = start
        context.user_data["end_time"] = end
        await query.edit_message_text("Пожалуйста, введите ваше имя:")
        return ASK_NAME

    # Confirmation nav
    if data == "change-date":
        return await offer_dates(query, context)
    if data == "cancel":
        await query.edit_message_text("Запись отменена. Чтобы начать заново, нажмите /start")
        return ConversationHandler.END
    if data == "confirm":
        # CRITICAL CHECK for slot availability again
        userd = context.user_data
        available = is_slot_available(
            userd["date"], userd["start_time"], userd["end_time"]
        )
        if not available:
            kb = [
                [InlineKeyboardButton("Изменить дату/время", callback_data="change-date")],
                [InlineKeyboardButton("Отменить", callback_data="cancel")]
            ]
            await query.edit_message_text(
                "К сожалению, время уже занято. Пожалуйста, выберите другую дату или время.",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            return CHOOSING_DATE
        # Save booking
        save_booking(
            userd["date"],
            userd["start_time"],
            userd["end_time"],
            service_duration(userd["service"], userd["covers"]),
            summary_service(userd),
            userd["client_name"],
            userd["phone"]
        )
        await query.edit_message_text(
            "✅ Запись подтверждена!\n\n"
            f"{booking_summary_text(userd)}"
        )
        return ConversationHandler.END

async def offer_dates(query, context):
    userd = context.user_data
    duration = service_duration(userd["service"], userd["covers"])
    available_dates = get_available_date_slots(duration)
    if not available_dates:
        await query.edit_message_text("Нет свободных дат на ближайшие 2 недели.")
        return ConversationHandler.END
    kb = []
    for dt, slots in available_dates:
        # button label: date, e.g. 12.06 (кол-во слотов)
        kb.append([InlineKeyboardButton(
            f"{datetime.strptime(dt, '%Y-%m-%d').strftime('%d.%m')} ({len(slots)} слотов)",
            callback_data=f"pick-date-{dt}"
        )])
    await query.edit_message_text(
        "Выберите дату:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_TIME

def summary_service(userd):
    if userd["service"] == "manicure":
        cov = COVERINGS[userd["covers"]["manicure"]]
        return f"Маникюр, {cov}"
    elif userd["service"] == "pedicure":
        cov = COVERINGS[userd["covers"]["pedicure"]]
        return f"Педикюр, {cov}"
    elif userd["service"] == "both":
        man = COVERINGS[userd["covers"]["manicure"]]
        ped = COVERINGS[userd["covers"]["pedicure"]]
        return f"Маникюр, {man}; Педикюр, {ped}"

def booking_summary_text(userd):
    return (
        f"Услуга: {summary_service(userd)}\n"
        f"Дата: {datetime.strptime(userd['date'],'%Y-%m-%d').strftime('%d.%m.%Y')}\n"
        f"Время: {userd['start_time']}–{userd['end_time']}\n"
        f"Имя: {userd['client_name']}\n"
        f"Телефон: {userd['phone']}"
    )

# --- Message Handlers (name, phone, etc.) ---
async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Пожалуйста, введите ваше имя (текстом):")
        return ASK_NAME
    context.user_data["client_name"] = name
    await update.message.reply_text(
        "Пожалуйста, введите ваш телефон (например, 79998887766 или +7 999 888 77 66):"
    )
    return ASK_PHONE

async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    clean = clean_phone(phone)
    if not clean or len(clean) != 10:
        await update.message.reply_text(
            "Проверьте номер — введите снова (10 цифр без кода страны, например, 9998887766):"
        )
        return ASK_PHONE
    context.user_data["phone"] = clean
    # Show final confirmation
    userd = context.user_data
    kb = [
        [InlineKeyboardButton("Подтвердить", callback_data="confirm")],
        [InlineKeyboardButton("Изменить дату/время", callback_data="change-date")],
        [InlineKeyboardButton("Отменить", callback_data="cancel")],
    ]
    await update.message.reply_text(
        "Проверьте все данные:\n\n" + booking_summary_text(userd),
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CONFIRM

# --- Export to Excel for master ---
async def export_to_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        import pandas as pd
    except ImportError:
        await update.message.reply_text("Для экспорта нужен pandas. Установите командой: pip install pandas")
        return
    conn = sqlite3.connect(DB_PATH)
    bookings = pd.read_sql_query("SELECT * FROM bookings ORDER BY date, start_time", conn)
    conn.close()
    fname = f"bookings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    bookings.to_excel(fname, index=False)
    with open(fname, "rb") as f:
        await update.message.reply_document(
            document=f, filename=fname,
            caption="Экспорт ваших записей (Excel)"
        )

# --- Main ---

def main():
    import os
    TOKEN = os.getenv("8703049578:AAH5zYJGw63BdJPANUBGhvbNJVjkiGz2UBE")
    if not TOKEN:
        print("Установите переменную окружения TG_BEAUTY_BOT_TOKEN с токеном Telegram-бота.")
        return
    init_db()
    application = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_SERVICE: [
                CallbackQueryHandler(button_handler)
            ],
            CHOOSING_COVER: [
                CallbackQueryHandler(button_handler)
            ],
            CHOOSING_DATE: [
                CallbackQueryHandler(button_handler)
            ],
            CHOOSING_TIME: [
                CallbackQueryHandler(button_handler)
            ],
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            CONFIRM: [CallbackQueryHandler(button_handler)]
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )

    application.add_handler(conv)
    # Add export command for master (use only for private owner; here open for all for demo)
    application.add_handler(CommandHandler("export", export_to_excel))
    print("Beauty Bot started!")
    application.run_polling()

if __name__ == "__main__":
    main()