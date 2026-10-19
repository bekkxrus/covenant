"""
Quiet Transformation Bot
A private Telegram bot for "The Blueprint of Quiet Transformation".

- Reminders anchored to real prayer times (fetched daily from aladhan.com)
- Tap-to-tick daily checklist (the 10 pillars)
- Per-prayer "prayed on time" buttons, auto-filling the 5/5 pillar
- Instant Recovery nudge if the previous prayer wasn't logged
- Evening audit, weekly tracker grid, streaks
"""

import asyncio
import json
import logging
import os
import random
import sqlite3
import datetime as dt
from zoneinfo import ZoneInfo

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ───────────────────────── config ─────────────────────────
TOKEN = os.environ.get("BOT_TOKEN", "")
TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Tashkent"))
CITY = os.getenv("CITY", "Tashkent")
COUNTRY = os.getenv("COUNTRY", "Uzbekistan")
METHOD = os.getenv("METHOD", "3")  # 3 = Muslim World League
SCHOOL = os.getenv("SCHOOL", "1")  # 1 = Hanafi Asr, 0 = Shafi'i
SLEEP_TIME = os.getenv("SLEEP_TIME", "22:30")  # target lights-out
OWNER_ID = os.getenv("OWNER_ID")  # optional; else first /start claims the bot
DB_PATH = os.getenv("DB_PATH", "covenant.db")

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("covenant")

PRAYERS = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]

PILLARS = [
    ("phone", "No phone at wake-up", "Phone"),
    ("prayers", "Prayers on time (5/5)", "Salah"),
    ("quran", "Quran & Dhikr", "Quran"),
    ("lang", "Arabic / English study", "Lang"),
    ("exam", "SAT / IELTS practice", "Exam"),
    ("deep", "Startup deep work", "Deep"),
    ("gym", "Workout / Gym", "Gym"),
    ("noscroll", "Zero doom-scrolling", "Scrl"),
    ("family", "Quality family time", "Fam"),
    ("sleep", "Asleep on time", "Sleep"),
]
PILLAR_KEYS = [p[0] for p in PILLARS]

RULES = [
    "*Quality over speed.* Slow down. Do every action with precision and beauty (Ihsan).",
    "*Guard your tongue.* Never mock anyone: looks, height, anything. Keep the heart light.",
    "*Conquer boredom.* Boredom is where strategy is born. Don't reach for the scroll.",
    "*Instant recovery.* An imperfect moment is not a ruined day. Reset at the next prayer.",
]

OATH = (
    "*Why am I doing this?*\n\n"
    "To fulfill my duties to Allah, to elevate my parents and give them the best life "
    "they deserve, and to build a strong, reliable self. I will not seek applause. My "
    "actions will speak for me. When I am lazy, I will move anyway. When I fail, I will "
    "get back up instantly."
)

# Events anchored to prayer times: (anchor, offset_minutes, id, text, buttons)
# buttons: list of ("p", prayer) for prayer log or ("c", pillar_key) for checklist toggle
EVENTS = [
    ("Fajr", 0, "fajr",
     "🌙 *Fajr — {time}*\nPhone down. Wudu, pray, dhikr, Quran, Arabic vocab.\n\n_Rule for today:_ {rule}",
     [("p", "Fajr"), ("c", "phone"), ("c", "quran")]),
    ("Fajr", 50, "deep",
     "💻 *Deep work.* One task, one tab. Build while the world is quiet.",
     [("c", "deep")]),
    ("Dhuhr", 0, "dhuhr",
     "☀️ *Dhuhr — {time}*\nPray with full khushu. Then a high-protein lunch.",
     [("p", "Dhuhr")]),
    ("Dhuhr", 45, "study",
     "📚 *Academic block.* English / Arabic, then IELTS or SAT practice. Zero scrolling.",
     [("c", "lang"), ("c", "exam")]),
    ("Asr", 0, "asr",
     "🏋️ *Asr — {time}*\nPray, then train: gym, run, or street workout.",
     [("p", "Asr"), ("c", "gym")]),
    ("Maghrib", 0, "maghrib",
     "🏠 *Maghrib — {time}*\nPray. Close the laptop, silence the phone. Be fully with your family.",
     [("p", "Maghrib"), ("c", "family")]),
    ("Isha", 0, "isha",
     "🌌 *Isha — {time}*\nPray, then a short Quran review.",
     [("p", "Isha")]),
    ("Isha", 40, "audit", None, None),  # evening audit = full checklist
    ("SLEEP", -30, "winddown",
     "😴 *Wind down.* Screens off in 30 min. Magnesium, lights out by {sleep}.",
     [("c", "sleep"), ("c", "noscroll")]),
]

# ───────────────────────── storage ─────────────────────────
def db():
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS checks (day TEXT, key TEXT, PRIMARY KEY(day, key))")
    con.execute("CREATE TABLE IF NOT EXISTS prayers (day TEXT, name TEXT, PRIMARY KEY(day, name))")
    con.execute("CREATE TABLE IF NOT EXISTS times (day TEXT PRIMARY KEY, json TEXT)")
    return con


def meta_get(k):
    with db() as con:
        r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else None


def meta_set(k, v):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, str(v)))


def checks_for(day: str) -> set:
    with db() as con:
        s = {r[0] for r in con.execute("SELECT key FROM checks WHERE day=?", (day,))}
        n = con.execute("SELECT COUNT(*) FROM prayers WHERE day=?", (day,)).fetchone()[0]
    if n >= 5:
        s.add("prayers")
    return s


def prayers_for(day: str) -> set:
    with db() as con:
        return {r[0] for r in con.execute("SELECT name FROM prayers WHERE day=?", (day,))}


def toggle(table: str, day: str, value: str) -> bool:
    col = "key" if table == "checks" else "name"
    with db() as con:
        exists = con.execute(f"SELECT 1 FROM {table} WHERE day=? AND {col}=?", (day, value)).fetchone()
        if exists:
            con.execute(f"DELETE FROM {table} WHERE day=? AND {col}=?", (day, value))
            return False
        con.execute(f"INSERT INTO {table} VALUES (?,?)", (day, value))
        return True


# ───────────────────────── prayer times ─────────────────────────
async def fetch_timings(day: dt.date) -> dict:
    key = day.isoformat()
    with db() as con:
        r = con.execute("SELECT json FROM times WHERE day=?", (key,)).fetchone()
    if r:
        return json.loads(r[0])
    url = f"https://api.aladhan.com/v1/timingsByCity/{day.strftime('%d-%m-%Y')}"
    params = {"city": CITY, "country": COUNTRY, "method": METHOD, "school": SCHOOL}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            resp = await c.get(url, params=params)
            resp.raise_for_status()
            raw = resp.json()["data"]["timings"]
        timings = {k: raw[k].split()[0] for k in PRAYERS + ["Sunrise"]}
        with db() as con:
            con.execute("INSERT OR REPLACE INTO times VALUES (?,?)", (key, json.dumps(timings)))
        return timings
    except Exception as e:  # fall back to the most recent cached day
        log.warning("Prayer time fetch failed (%s); using last cached", e)
        with db() as con:
            r = con.execute("SELECT json FROM times ORDER BY day DESC LIMIT 1").fetchone()
        if r:
            return json.loads(r[0])
        raise


def at(day: dt.date, hhmm: str) -> dt.datetime:
    h, m = map(int, hhmm.split(":"))
    return dt.datetime.combine(day, dt.time(h, m), tzinfo=TZ)


def build_schedule(day: dt.date, timings: dict):
    """Return [(datetime, event_tuple)] for the day, sorted."""
    out = []
    for ev in EVENTS:
        anchor, offset = ev[0], ev[1]
        base = at(day, SLEEP_TIME) if anchor == "SLEEP" else at(day, timings[anchor])
        out.append((base + dt.timedelta(minutes=offset), ev))
    return sorted(out, key=lambda x: x[0])


# ───────────────────────── rendering ─────────────────────────
def today() -> dt.date:
    return dt.datetime.now(TZ).date()


def btn_label(kind, value, day):
    if kind == "p":
        done = value in prayers_for(day)
        return f"{'✅' if done else '🕌'} {value} on time"
    done = value in checks_for(day)
    name = next(p[1] for p in PILLARS if p[0] == value)
    return f"{'✅' if done else '⬜'} {name}"


def keyboard(buttons, day: str):
    rows = [[InlineKeyboardButton(btn_label(k, v, day), callback_data=f"{k}|{v}|{day}")]
            for k, v in buttons]
    return InlineKeyboardMarkup(rows)


def checklist_kb(day: str):
    done = checks_for(day)
    prayed = prayers_for(day)
    rows = [[InlineKeyboardButton(
        f"{'✅' if p in prayed else '🕌'} {p}", callback_data=f"p|{p}|{day}") for p in PRAYERS]]
    for key, name, _ in PILLARS:
        if key == "prayers":
            continue
        rows.append([InlineKeyboardButton(
            f"{'✅' if key in done else '⬜'} {name}", callback_data=f"c|{key}|{day}")])
    return InlineKeyboardMarkup(rows)


def checklist_text(day: str):
    done = checks_for(day)
    n = len(done)
    d = dt.date.fromisoformat(day)
    head = f"📋 *Audit — {d.strftime('%a %d %b')}*  ({n}/{len(PILLARS)})"
    if n == len(PILLARS):
        foot = "Full day. Alhamdulillah. Sleep well."
    elif n >= 7:
        foot = "Strong day. Note what slipped and fix one thing tomorrow."
    else:
        foot = "Not your best — and that's fine. *Reset at Fajr. Never compound it.*"
    return f"{head}\nTap to tick what you honored today.\n\n{foot}"


def week_text(ref: dt.date):
    monday = ref - dt.timedelta(days=ref.weekday())
    days = [monday + dt.timedelta(days=i) for i in range(7)]
    sets = [checks_for(d.isoformat()) for d in days]
    lines = ["       " + " ".join(d.strftime("%a")[:2] for d in days)]
    for key, _, short in PILLARS:
        cells = ["·" if d > ref else ("✓" if key in s else "✗") for d, s in zip(days, sets)]
        lines.append(f"{short:<6} " + " ".join(f"{c} " for c in cells).rstrip())
    total = sum(len(s) for d, s in zip(days, sets) if d <= ref)
    possible = len(PILLARS) * sum(1 for d in days if d <= ref)
    pct = round(100 * total / possible) if possible else 0
    return f"🗓 *Week of {monday.strftime('%d %b')}* — {pct}%\n```\n" + "\n".join(lines) + "\n```"


def streak(key: str) -> int:
    n, d = 0, today()
    if key not in checks_for(d.isoformat()):
        d -= dt.timedelta(days=1)  # today not done yet doesn't break the streak
    while key in checks_for(d.isoformat()):
        n += 1
        d -= dt.timedelta(days=1)
    return n


# ───────────────────────── access control ─────────────────────────
def is_owner(update: Update) -> bool:
    owner = OWNER_ID or meta_get("owner")
    return owner is not None and str(update.effective_chat.id) == str(owner)


# ───────────────────────── jobs ─────────────────────────
async def fire_event(ctx: ContextTypes.DEFAULT_TYPE):
    chat = meta_get("owner") or OWNER_ID
    if not chat:
        return
    ev, when_str = ctx.job.data
    _, _, eid, text, buttons = ev
    day = today().isoformat()

    if eid == "audit":
        await ctx.bot.send_message(chat, checklist_text(day), parse_mode=ParseMode.MARKDOWN,
                                   reply_markup=checklist_kb(day))
        return

    msg = text.format(time=when_str, rule=random.choice(RULES), sleep=SLEEP_TIME)

    # Instant Recovery: nudge if the previous prayer wasn't logged
    if ev[0] in PRAYERS and ev[0] != "Fajr" and ev[1] == 0:
        prev = PRAYERS[PRAYERS.index(ev[0]) - 1]
        if prev not in prayers_for(day):
            msg += f"\n\n↩️ {prev} isn't logged. If you missed it, make it up and reset now. One slip, not a ruined day."

    await ctx.bot.send_message(chat, msg, parse_mode=ParseMode.MARKDOWN,
                               reply_markup=keyboard(buttons, day) if buttons else None)


async def schedule_day(ctx: ContextTypes.DEFAULT_TYPE):
    jq = ctx.job_queue
    for job in jq.jobs():
        if job.name and job.name.startswith("evt-"):
            job.schedule_removal()
    d = today()
    timings = await fetch_timings(d)
    now = dt.datetime.now(TZ)
    count = 0
    for when, ev in build_schedule(d, timings):
        if when > now:
            jq.run_once(fire_event, when=when, data=(ev, when.strftime("%H:%M")), name=f"evt-{ev[2]}")
            count += 1
    log.info("Scheduled %d events for %s: %s", count, d, timings)


# ───────────────────────── commands ─────────────────────────
HELP = (
    "*Commands*\n"
    "/today — today's plan with prayer times\n"
    "/check — today's checklist\n"
    "/week — weekly tracker\n"
    "/streak — your streaks\n"
    "/rules — the four rules\n"
    "/why — your oath\n"
    "/help — this list"
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    owner = OWNER_ID or meta_get("owner")
    if owner is None:
        meta_set("owner", update.effective_chat.id)
    elif not is_owner(update):
        return
    await update.message.reply_text(
        "Bismillah. I'll keep you on the Blueprint: a reminder at every prayer and block, "
        "and an audit after Isha.\n\nKeep your plans between you and Allah.\n\n" + HELP,
        parse_mode=ParseMode.MARKDOWN)
    await schedule_day(ctx)


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    d = today()
    t = await fetch_timings(d)
    names = {"fajr": "Fajr Foundation", "deep": "Deep work", "dhuhr": "Dhuhr + lunch",
             "study": "Academic block", "asr": "Asr + training", "maghrib": "Maghrib + family",
             "isha": "Isha + Quran review", "audit": "Evening audit", "winddown": "Wind down"}
    now = dt.datetime.now(TZ)
    lines = []
    for when, ev in build_schedule(d, t):
        mark = "▸" if when > now else "·"
        lines.append(f"{mark} `{when.strftime('%H:%M')}`  {names[ev[2]]}")
    lines.append(f"· `{SLEEP_TIME}`  Lights out")
    await update.message.reply_text(
        f"*{d.strftime('%A %d %B')}* — {CITY}\n\n" + "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN)


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    day = today().isoformat()
    await update.message.reply_text(checklist_text(day), parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=checklist_kb(day))


async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.message.reply_text(week_text(today()), parse_mode=ParseMode.MARKDOWN)


async def cmd_streak(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    lines = [f"`{streak(k):>3}` days — {name}" for k, name, _ in PILLARS]
    await update.message.reply_text("🔥 *Streaks*\n\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_rules(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.message.reply_text("\n\n".join(f"{i}. {r}" for i, r in enumerate(RULES, 1)),
                                    parse_mode=ParseMode.MARKDOWN)


async def cmd_why(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.message.reply_text(OATH, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.message.reply_text(HELP, parse_mode=ParseMode.MARKDOWN)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_owner(update):
        await q.answer()
        return
    kind, value, day = q.data.split("|")
    now_on = toggle("prayers" if kind == "p" else "checks", day, value)
    await q.answer("Logged ✅" if now_on else "Unticked")
    # re-render whichever keyboard this was
    if q.message.text and q.message.text.startswith("📋"):
        await q.edit_message_text(checklist_text(day), parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=checklist_kb(day))
    else:
        old = q.message.reply_markup.inline_keyboard
        buttons = [tuple(row[0].callback_data.split("|")[:2]) for row in old]
        await q.edit_message_reply_markup(reply_markup=keyboard(buttons, day))


# ───────────────────────── main ─────────────────────────
async def post_init(app: Application):
    app.job_queue.run_daily(schedule_day, time=dt.time(0, 5, tzinfo=TZ), name="daily-scheduler")
    if OWNER_ID or meta_get("owner"):
        app.job_queue.run_once(schedule_day, when=2, name="startup-scheduler")


def main():
    if not TOKEN:
        raise SystemExit("Set BOT_TOKEN (get one from @BotFather).")
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    for name, fn in [("start", cmd_start), ("today", cmd_today), ("check", cmd_check),
                     ("week", cmd_week), ("streak", cmd_streak), ("rules", cmd_rules),
                     ("why", cmd_why), ("help", cmd_help)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_button))
    # Python 3.14+ no longer creates an event loop implicitly
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling()


if __name__ == "__main__":
    main()
