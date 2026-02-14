import os
import sqlite3
from contextlib import closing

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

DB_PATH = "debts.db"
TOKEN_FILE = "bot_token.txt"

MAIN_MENU = "قائمة الأسماء"
ADD_NEW_NAME = "إضافة اسم جديد"
LIST_ALL = "الكل"
NEW_DEBT = "دين جديد"
PAYMENT = "سداد"
STATUS = "الحالة"
DELETE_PERSON = "حذف الشخص"
BACK = "رجوع"

STATE_ADD_NAME = "add_name"
STATE_ADD_NAME_AMOUNT = "add_name_amount"
STATE_NEW_DEBT = "new_debt"
STATE_PAYMENT = "payment"


def normalize_digits(value: str) -> str:
    table = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷８٩", "01234567890123456789")
    return value.translate(table)

def init_db() -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS debts (
                name TEXT PRIMARY KEY,
                total REAL NOT NULL,
                paid REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()

def load_token() -> str:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if token:
        return token

    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            return token

    raise RuntimeError("Telegram token not found. Set TELEGRAM_BOT_TOKEN or create bot_token.txt")

def get_names() -> list[str]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute("SELECT name FROM debts ORDER BY name").fetchall()
    return [row[0] for row in rows]

def get_person(name: str) -> tuple[float, float] | None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute("SELECT total, paid FROM debts WHERE name=?", (name,)).fetchone()
    if not row:
        return None
    return float(row[0]), float(row[1])

def add_new_person(name: str, amount: float) -> bool:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        exists = conn.execute("SELECT 1 FROM debts WHERE name=?", (name,)).fetchone()
        if exists:
            return False
        conn.execute("INSERT INTO debts(name, total, paid) VALUES (?, ?, 0)", (name, amount))
        conn.commit()
    return True

def increase_debt(name: str, amount: float) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("UPDATE debts SET total = total + ? WHERE name = ?", (amount, name))
        conn.commit()

def add_payment(name: str, amount: float) -> tuple[bool, float]:
    person = get_person(name)
    if not person:
        return False, 0.0

    total, paid = person
    new_paid = paid + amount
    if new_paid > total:
        return False, total - paid

    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("UPDATE debts SET paid = ? WHERE name = ?", (new_paid, name))
        conn.commit()
    return True, total - new_paid

def delete_person(name: str) -> bool:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("DELETE FROM debts WHERE name = ?", (name,))
        conn.commit()
    return cur.rowcount > 0

def parse_amount(text: str) -> float | None:
    try:
        amount = float(normalize_digits(text.strip()))
    except ValueError:
        return None
    if amount <= 0:
        return None
    return amount

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[MAIN_MENU], [ADD_NEW_NAME, LIST_ALL]], resize_keyboard=True)

def names_keyboard(names: list[str]) -> ReplyKeyboardMarkup:
    rows = [[name] for name in names]
    rows.append([BACK])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def person_actions_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[NEW_DEBT, PAYMENT], [STATUS, DELETE_PERSON], [BACK]],
        resize_keyboard=True,
    )

async def show_main_menu(update: Update) -> None:
    await update.message.reply_text(
        "اختر من القائمة:",
        reply_markup=main_keyboard(),
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await show_main_menu(update)

async def show_all(update: Update) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute("SELECT name, total, paid FROM debts ORDER BY name").fetchall()
    if not rows:
        await update.message.reply_text("لا توجد ديون.")
        return
    lines = [f"{name} - المتبقي: {total - paid:g}" for name, total, paid in rows]
    await update.message.reply_text("\n".join(lines))

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if text == BACK:
        context.user_data.clear()
        await show_main_menu(update)
        return
    pending = context.user_data.get("pending")
    selected_name = context.user_data.get("selected_name")
    if pending == STATE_ADD_NAME:
        if not text:
            await update.message.reply_text("اكتب اسم صحيح.")
            return
        context.user_data["pending"] = STATE_ADD_NAME_AMOUNT
        context.user_data["draft_name"] = text
        await update.message.reply_text(
            f"اكتب المبلغ الأولي لـ {text}:")
        return
    if pending == STATE_ADD_NAME_AMOUNT:
        amount = parse_amount(text)
        if amount is None:
            await update.message.reply_text(
                "المبلغ غير صحيح. اكتب رقم موجب."
            )
            return
        name = context.user_data.get("draft_name")
        if not name:
            context.user_data.clear()
            await show_main_menu(update)
            return
        if not add_new_person(name, amount):
            await update.message.reply_text("الاسم موجود مسبقاً.")
        else:
            await update.message.reply_text(
                f"تم تسجيل دين {name} بمبلغ {amount:g}"
            )
        context.user_data.clear()
        await show_main_menu(update)
        return
    if pending == STATE_NEW_DEBT and selected_name:
        amount = parse_amount(text)
        if amount is None:
            await update.message.reply_text(
                "المبلغ غير صحيح. اكتب رقم موجب."
            )
            return
        increase_debt(selected_name, amount)
        total, paid = get_person(selected_name) or (0.0, 0.0)
        await update.message.reply_text(
            f"تمت إضافة دين جديد لـ {selected_name} بمبلغ {amount:g}\n"
            f"المتبقي الآن: {total - paid:g}",
            reply_markup=person_actions_keyboard(),
        )
        context.user_data["pending"] = None
        return
    if pending == STATE_PAYMENT and selected_name:
        amount = parse_amount(text)
        if amount is None:
            await update.message.reply_text(
                "المبلغ غير صحيح. اكتب رقم موجب."
            )
            return
        ok, remaining = add_payment(selected_name, amount)
        if not ok:
            await update.message.reply_text(
                f"لا يمكن السداد بهذا المبلغ. "
                f"المتبقي الحالي: {remaining:g}",
                reply_markup=person_actions_keyboard(),
            )
            return
        await update.message.reply_text(
            f"تم السداد. المتبقي: {remaining:g}",
            reply_markup=person_actions_keyboard(),
        )
        context.user_data["pending"] = None
        return
    if text == MAIN_MENU:
        names = get_names()
        if not names:
            await update.message.reply_text(
                "القائمة فارغة. أضف اسم جديد أولاً."
            )
            return
        await update.message.reply_text(
            "اختر الاسم:",
            reply_markup=names_keyboard(names),
        )
        return
    if text == ADD_NEW_NAME:
        context.user_data.clear()
        context.user_data["pending"] = STATE_ADD_NAME
        await update.message.reply_text("اكتب الاسم الجديد:")
        return
    if text == LIST_ALL:
        await show_all(update)
        return
    names = get_names()
    if text in names:
        context.user_data["selected_name"] = text
        context.user_data["pending"] = None
        await update.message.reply_text(
            f"تم اختيار: {text}",
            reply_markup=person_actions_keyboard(),
        )
        return
    if text == NEW_DEBT:
        if not selected_name:
            await update.message.reply_text(
                "اختر اسم أولاً من قائمة الأسماء."
            )
            return
        context.user_data["pending"] = STATE_NEW_DEBT
        await update.message.reply_text(
            f"اكتب مبلغ الدين الجديد لـ {selected_name}:")
        return
    if text == PAYMENT:
        if not selected_name:
            await update.message.reply_text(
                "اختر اسم أولاً من قائمة الأسماء."
            )
            return
        context.user_data["pending"] = STATE_PAYMENT
        await update.message.reply_text(
            f"اكتب مبلغ السداد لـ {selected_name}:")
        return
    if text == STATUS:
        if not selected_name:
            await update.message.reply_text(
                "اختر اسم أولاً من قائمة الأسماء."
            )
            return
        person = get_person(selected_name)
        if not person:
            await update.message.reply_text("الاسم غير موجود.")
            return
        total, paid = person
        await update.message.reply_text(
            f"{selected_name}\n"
            f"إجمالي: {total:g}\n"
            f"مدفوع: {paid:g}\n"
            f"متبقي: {total - paid:g}",
            reply_markup=person_actions_keyboard(),
        )
        return
    if text == DELETE_PERSON:
        if not selected_name:
            await update.message.reply_text(
                "اختر اسم أولاً من قائمة الأسماء."
            )
            return
        if delete_person(selected_name):
            context.user_data.clear()
            await update.message.reply_text(
                f"تم حذف {selected_name} من القائمة."
            )
            await show_main_menu(update)
            return
        await update.message.reply_text("الاسم غير موجود.")
        return
    await update.message.reply_text(
        "غير مفهوم. اختر من الأزرار المعروضة."
    )

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(update, Update) and update.message:
        await update.message.reply_text(
            "حدث خطأ غير متوقع. حاول مرة ثانية."
        )

def main() -> None:
    init_db()
    token = load_token()

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    app.run_polling()


if __name__ == "__main__":
    main()