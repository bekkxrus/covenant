# Quiet Transformation Bot

A private Telegram bot that keeps you on the Blueprint, anchored to real prayer times.

## What it does each day

| When | Message | Buttons |
|---|---|---|
| Fajr | Phone down, wudu, dhikr, Quran, Arabic + rule of the day | Fajr on time · No phone · Quran |
| Fajr + 50 min | Deep work block | Deep work |
| Dhuhr | Pray with khushu, high-protein lunch | Dhuhr on time |
| Dhuhr + 45 min | Academic block (English/Arabic, IELTS/SAT) | Study · Exam practice |
| Asr | Pray, then train | Asr on time · Gym |
| Maghrib | Pray, laptop closed, family time | Maghrib on time · Family |
| Isha | Pray, short Quran review | Isha on time |
| Isha + 40 min | **Evening audit**: the full 10-pillar checklist | all pillars |
| Sleep − 30 min | Wind down, magnesium | Sleep · No scrolling |

Prayer times refresh every night at 00:05 from aladhan.com. Logging all 5 prayers auto-ticks "Prayers on time (5/5)". If you didn't log the previous prayer, the next reminder carries an **Instant Recovery** nudge.

Commands: `/today` `/check` `/week` `/streak` `/rules` `/why` `/help`

## Setup (5 minutes)

1. In Telegram, message **@BotFather** → `/newbot` → copy the token.
2. `cp .env.example .env` and paste the token. Adjust city, method, and sleep time if needed.
3. Run it:

```bash
pip install -r requirements.txt
set -a; source .env; set +a
python bot.py
```

4. Open your bot in Telegram and send `/start`. The first chat to do this becomes the owner; everyone else is ignored.

## Keeping it running 24/7

The bot needs to be always on. Any of these work:

- **A small VPS** (simplest): run with Docker
  ```bash
  docker build -t covenant-bot .
  docker run -d --restart=always --env-file .env -v covenant-data:/data covenant-bot
  ```
- **Any host that runs a long-lived Python/Docker worker** (Railway, Render background worker, Fly.io, etc.). Attach a persistent volume at `/data` so your history survives redeploys.
- **A home machine** that stays on.

## Customising

All reminders live in the `EVENTS` list in `bot.py`: change offsets, wording, or which buttons each message shows. Pillars are in `PILLARS`.
