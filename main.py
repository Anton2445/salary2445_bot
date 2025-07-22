import os
import json
import re
from pathlib import Path
from datetime import datetime, timedelta, date, timezone

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)

# ─── CONFIG ───
TOKEN = os.environ["TOKEN"]
SAVE_FILE = Path("deals.json")
DATE, NAME, AMOUNT, FEE, RATE, MEMBERS = range(6)

# ─── HELPERS ───
def load_deals():
    if not SAVE_FILE.exists():
        return []
    try:
        return json.loads(SAVE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_deals(deals):
    SAVE_FILE.write_text(json.dumps(deals, indent=2, ensure_ascii=False), encoding="utf-8")

def parse_date(txt: str) -> date | None:
    txt = txt.strip().lower()
    if txt in {"сегодня", "today"}:
        return datetime.now(timezone.utc).date()
    match = re.match(r"(\d{1,2})[./](\d{1,2})", txt)
    if not match:
        return None
    day, month = map(int, match.groups())
    try:
        return date(datetime.now().year, month, day)
    except ValueError:
        return None

def tuesday_week_range(ref: date | None = None):
    if ref is None:
        ref = datetime.now(timezone.utc).date()
    offset = (ref.weekday() - 1) % 7  # Tuesday is 1
    start = ref - timedelta(days=offset)
    return start, start + timedelta(days=6)

def filter_deals_by_date_range(start: date, end: date):
    return [
        d for d in load_deals()
        if start <= datetime.fromisoformat(d['date_iso']).date() <= end
    ]

def get_next_index_for_date(deals, d: date):
    same_day = [deal for deal in deals if datetime.fromisoformat(deal['date_iso']).date() == d]
    return len(same_day) + 1

# ─── WORKFLOW ───
async def start_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите дату депозита (например, 21.07):")
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = parse_date(update.message.text)
    if not d:
        await update.message.reply_text("⚠️ Неверный формат даты")
        return DATE
    context.user_data["date"] = d
    await update.message.reply_text("Название депозита (например: СБП, QR):")
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("Сумма депозита в рублях:")
    return AMOUNT

async def get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["amount"] = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("⚠️ Введите число.")
        return AMOUNT
    await update.message.reply_text("Комиссия в %:")
    return FEE

async def get_fee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["fee"] = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("⚠️ Введите число.")
        return FEE
    await update.message.reply_text("Курс рубля к доллару:")
    return RATE

async def get_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["rate"] = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("⚠️ Неверный курс")
        return RATE
    await update.message.reply_text("Участники (пример: #10 #12 #14):")
    return MEMBERS


async def get_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    members = re.findall(r"#\d+", raw)
    if not members:
        await update.message.reply_text("⚠️ Укажите хотя бы одного участника через #")
        return MEMBERS
    total = len(members)

    rub = context.user_data["amount"]
    fee = context.user_data["fee"]
    rate = context.user_data["rate"]

    clean_rub = rub - (rub * fee / 100)
    usd = clean_rub / rate
    pool = usd * 0.25
    share = pool / total

    d = context.user_data["date"]
    deals = load_deals()
    idx = get_next_index_for_date(deals, d)

    new_deal = {
        "date_iso": d.isoformat(),
        "index": idx,
        "name": context.user_data["name"],
        "rub": rub,
        "fee": fee,
        "rate": rate,
        "clean_rub": clean_rub,
        "usd": usd,
        "pool": pool,
        "share": share,
        "members": members
    }

    deals.append(new_deal)
    save_deals(deals)

    await update.message.reply_text(
        f"✅ Депозит сохранён\n\n"
        f"📅 {d.strftime('%d.%m')} | {context.user_data['name']}\n"
        f"💰 {rub:.0f} ₽ → {clean_rub:.0f} ₽ после комиссии ({fee:.1f}%)\n"
        f"💱 {usd:.2f} $ @ {rate:.2f}, пул 25% = {pool:.2f} $\n"
        f"👥 Участников: {total}, твоя доля: {share:.2f} $"
    )
    return ConversationHandler.END

# ─── REPORTS ───
async def show_report(update, context, start, end, label="Отчёт"):
    deals = filter_deals_by_date_range(start, end)
    if not deals:
        await update.message.reply_text("Нет депозитов в выбранный период")
        return
    total = sum(d['share'] for d in deals)
    lines = []
    for d in deals:
        day = datetime.fromisoformat(d["date_iso"]).strftime("%d.%m")
        members_str = ', '.join(d['members'])
        lines.append(
            f"📅 {day} | {d['name']} #{d['index']}\n"
            f"💰 {d['rub']:.0f} ₽ → {d['clean_rub']:.0f} ₽ после комиссии ({d['fee']:.1f}%)\n"
            f"💱 {d['usd']:.2f} $ @ {d['rate']:.2f}, пул 25% = {d['pool']:.2f} $\n"
            f"👥 Участники: {len(d['members'])} ({members_str})\n"
            f"→ твоя доля: {d['share']:.2f} $\n"
        )
    await update.message.reply_text(
        f"📊 {label}\nВсего: {total:.2f} $\n\n" + "\n".join(lines)
    )

async def show_range(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("Формат: /report DD.MM DD.MM")
        return
    d1 = parse_date(context.args[0])
    d2 = parse_date(context.args[1])
    if not d1 or not d2:
        await update.message.reply_text("Неверные даты")
        return
    await show_report(update, context, d1, d2, label=f"{d1:%d.%m}–{d2:%d.%m}")

async def show_by_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Формат: /bydate DD.MM")
        return
    d = parse_date(context.args[0])
    if not d:
        await update.message.reply_text("Неверная дата")
        return
    deals = [d_ for d_ in load_deals() if datetime.fromisoformat(d_["date_iso"]).date() == d]
    if not deals:
        await update.message.reply_text("Нет депозитов на эту дату")
        return
    lines = []
    for d_ in deals:
        members_str = ', '.join(d_['members'])
        lines.append(
            f"#{d_['index']} • {d_['name']}, {d_['rub']:.0f} ₽ → {d_['clean_rub']:.0f} ₽ после комиссии ({d_['fee']:.1f}%)\n"
            f"💱 {d_['usd']:.2f} $ @ {d_['rate']:.2f}, пул 25% = {d_['pool']:.2f} $\n"
            f"👥 Участники: {len(d_['members'])} ({members_str})\n"
            f"→ твоя доля: {d_['share']:.2f} $"
        )
    await update.message.reply_text('\n\n'.join(lines))

async def delete_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("Формат: /delete DD.MM номер")
        return
    d = parse_date(context.args[0])
    if not d:
        await update.message.reply_text("Неверная дата")
        return
    try:
        num = int(context.args[1])
    except:
        await update.message.reply_text("Номер должен быть числом")
        return
    deals = load_deals()
    filtered = [
        (i, d_) for i, d_ in enumerate(deals)
        if datetime.fromisoformat(d_["date_iso"]).date() == d and d_["index"] == num
    ]
    if not filtered:
        await update.message.reply_text("Не найдено")
        return
    i, _ = filtered[0]
    del deals[i]
    save_deals(deals)
    await update.message.reply_text("✅ Удалено")

    
# ─── OTHER ───
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено")
    return ConversationHandler.END

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/deal – новая сделка\n"
        "/week – за текущую зарплатную неделю (вт–пн)\n"
        "/bydate DD.MM – за день\n"
        "/report DD.MM DD.MM – диапазон\n"
        "/month MM – за месяц\n"
        "/delete DD.MM номер – удалить\n"
        "/cancel – отмена"
    )
async def show_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start, end = tuesday_week_range()
    await show_report(update, context, start, end, label="За текущую зарплатную неделю (вт–пн)")

async def show_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args and context.args[0].isdigit():
        month = int(context.args[0])
        year = datetime.now(timezone.utc).year
        start = date(year, month, 1)
        if month == 12:
            end = date(year, 12, 31)
        else:
            end = date(year, month + 1, 1) - timedelta(days=1)
        await show_report(update, context, start, end, label=f"За месяц {month:02d}")
    else:
        await update.message.reply_text("Формат: /month MM (например, /month 07)")

# ─── MAIN ───
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("deal", start_deal)],
        states={
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount)],
            FEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_fee)],
            RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_rate)],
            MEMBERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_members)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("start", help_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("week", show_week))
    app.add_handler(CommandHandler("month", show_month))
    app.add_handler(CommandHandler("report", show_range))
    app.add_handler(CommandHandler("bydate", show_by_date))
    app.add_handler(CommandHandler("delete", delete_deal))

    print("✅ Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()

