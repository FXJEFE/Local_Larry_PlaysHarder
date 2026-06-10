# 🚀 Larry G-Force — Windows Quickstart (10-15 minutes to working system)

This is the **practical, opinionated guide** for Windows users who just want the **Dashboard + Telegram bot** running with persistent chat memory.

> The full detailed guide is in `docs/README.md` and the old `SETUP_GUIDE.md`. This file gets you productive fastest.

---

## 1. One-time Prerequisites

1. **Python 3.11 or 3.12** (add to PATH)
2. **Ollama** installed and running (`ollama serve`)
3. At least one model pulled:

```powershell
ollama pull dolphin-mixtral:8x7b     # or your preferred model
ollama pull nomic-embed-text         # needed for RAG/memory features
```

4. Recommended: Create a folder you will treat as the **home** for this project, e.g.:

```powershell
C:\Users\YourName\Documents\LocalLarry\GITHUB
```

Copy or clone the contents of this `GITHUB` folder there.

---

## 2. Environment Variables (Critical for Telegram)

Copy the example:

```powershell
cd C:\Users\YourName\Documents\LocalLarry\GITHUB
copy config\.env.example .env
```

Edit `.env` and at minimum fill in:

```env
TELEGRAM_BOT_TOKEN=123456:ABCDEF-your-real-token-from-BotFather
TELEGRAM_ALLOWED_CHAT_IDS=8539863129          # Get yours by messaging @userinfobot
# TELEGRAM_ALLOW_ALL=true                     # Only for testing — not recommended
```

Optional but useful now that we have persistence:

```env
TELEGRAM_MAX_HISTORY=50                       # How many turns the bot remembers in a thread
```

---

## 3. Start the Dashboard (Your Control Center)

This is the easiest way to manage everything:

```powershell
cd C:\Users\YourName\Documents\LocalLarry\GITHUB
python src\dashboard_hub.py
```

Or (recommended) use the RUNTIME snapshot if you have one set up:

```powershell
cd C:\Users\YourName\Documents\LocalLarry\RUNTIME ok
python dashboard_hub.py
```

Your browser should open to `http://localhost:3777`.

**In the Dashboard → SERVICES tab you will see:**

- `agent_larry` (port 3778)
- `telegram_bot` (port 3779) ← **This is what you want**

Click **START** on `telegram_bot`. It will open in its own console window with the nice banner.

---

## 4. The Telegram Bot Now Remembers You

Recent improvements (as of this quickstart):

- Chat history is **persisted to disk** automatically (`src/data/telegram_chats/`)
- The bot survives restarts, crashes, and "Stop → Start" from the dashboard
- Default memory is 40 turns (change with `TELEGRAM_MAX_HISTORY`)
- One bad message/tool call will no longer kill the whole bot

**First things to try in Telegram after starting the bot:**

```
/myid          → Shows your chat ID (use this in .env)
/start
/help
/status        → Shows whether RAG, context, voice etc. are active
```

---

## 5. Recommended Daily Workflow (Windows)

**Option A — Easiest (Dashboard-driven)**

1. Open PowerShell → run `dashboard_hub.py`
2. In browser Services tab → Start `telegram_bot`
3. Talk to your bot on Telegram
4. When done: close the bot console or hit Stop in the dashboard

**Option B — Direct**

```powershell
cd C:\...\GITHUB\src
python telegram_bot.py
```

The bot will load persisted conversations automatically.

---

## 6. Common First-Run Fixes

| Problem                        | Fix |
|--------------------------------|-----|
| "TELEGRAM_BOT_TOKEN not set"   | Put it in `.env` next to the GITHUB folder (or in `launchers\.env`) |
| Bot ignores you                | Make sure your chat ID is in `TELEGRAM_ALLOWED_CHAT_IDS` (use `/myid`) |
| No models / slow               | Run `ollama ps` and pull at least one decent model |
| Context not remembered         | Check that `data/telegram_chats/` folder is being created (it should be) |
| Bot dies on complex commands   | Normal for very heavy tool use. The bot now recovers automatically. Use `/clear` if it gets confused. |

---

## 7. After You Have It Working

- Read the full `docs/README.md` for advanced features (RAG, MCP tools, hardware profiles, voice, etc.)
- Use `/profile ACCURACY` or `/profile SPEED` in the bot to change how much VRAM/context it uses
- The same persistent memory system is shared between the CLI agent (`agent_v2.py`) and Telegram

---

## Need Help?

Send these commands in the bot itself:

```
/status
/rag
/myid
```

Then paste the output here if you're stuck.

---

**You now have a private, local, memory-persistent AI that you control through both a nice web dashboard and Telegram.**

Welcome to the G-Force. ⚡

_Last updated: May 2026 (after adding robust context persistence + crash recovery to the Telegram bot)_
