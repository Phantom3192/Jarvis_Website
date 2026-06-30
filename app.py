"""
Jarvis website — standalone landing page + docs, deployed completely
separately from the bot.

This project knows NOTHING about discord.py, cogs, or the bot's internals.
It only ever talks to the bot over plain HTTP, via the small public JSON
API the bot exposes at BOT_API_URL:

    GET {BOT_API_URL}/api/stats       -> live guild/user/uptime numbers
    GET {BOT_API_URL}/api/categories  -> docs category data + bot name

That means this website and the bot can live in two totally separate
Railway projects, with separate deploys, separate restarts, separate
scaling — the only coupling is one env var (BOT_API_URL).

If the bot is unreachable (deploying, crashed, restarting), the site
still renders fine using cached/fallback data instead of failing.
"""
import os
import re
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).parent

# ── Config (set these as Railway environment variables) ───────────────────
BOT_API_URL = os.getenv("BOT_API_URL", "").rstrip("/")        # e.g. https://jarvis-bot.up.railway.app
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DEFAULT_BOT_NAME = os.getenv("BOT_NAME", "Jarvis")

INVITE_PERMISSIONS = "414531833920"  # send/embed/history/react/connect/speak/manage messages
INVITE_URL = (
    f"https://discord.com/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
    f"&permissions={INVITE_PERMISSIONS}&scope=bot%20applications.commands"
    if DISCORD_CLIENT_ID else "#"
)
SUPPORT_SERVER_URL = os.getenv("SUPPORT_SERVER_URL", "#")  # e.g. https://discord.gg/your-invite-code
LEGAL_LAST_UPDATED = os.getenv("LEGAL_LAST_UPDATED", "July 1, 2026")

HIGHLIGHT_KEYS = ["🤖 AI", "🧠 Memory", "♟️ Games", "🎵 Music", "🪙 Jarvis Credits", "⏰ Reminders"]

REQUEST_TIMEOUT = 5.0       # seconds — fail fast if the bot is slow/down
CATEGORIES_CACHE_TTL = 300  # seconds — docs content barely changes, cache it

app = FastAPI(title="Jarvis Website", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["md_bold"] = lambda s: re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s or "")

# Shown if the bot has never successfully responded yet (first deploy, etc.)
_FALLBACK_STATS = {
    "guilds": 0, "users": 0, "uptime_human": "—",
    "latency_ms": None, "online": False, "bot_name": DEFAULT_BOT_NAME,
}

_categories_cache: dict = {"data": {}, "bot_name": DEFAULT_BOT_NAME, "ts": 0.0}


async def _fetch_json(path: str, timeout: float = REQUEST_TIMEOUT) -> dict | None:
    if not BOT_API_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.get(f"{BOT_API_URL}{path}")
            res.raise_for_status()
            return res.json()
    except Exception:
        return None


async def _get_categories() -> tuple[dict, str]:
    """Categories barely change, so cache them and only refetch every
    CATEGORIES_CACHE_TTL seconds — keeps page loads fast and avoids
    hammering the bot's API on every visitor."""
    now = time.time()
    if _categories_cache["data"] and now - _categories_cache["ts"] < CATEGORIES_CACHE_TTL:
        return _categories_cache["data"], _categories_cache["bot_name"]

    payload = await _fetch_json("/api/categories")
    if payload and payload.get("categories"):
        _categories_cache["data"] = payload["categories"]
        _categories_cache["bot_name"] = payload.get("bot_name", DEFAULT_BOT_NAME)
        _categories_cache["ts"] = now

    return _categories_cache["data"], _categories_cache["bot_name"]


@app.get("/")
async def home(request: Request):
    categories, bot_name = await _get_categories()
    highlights = {k: categories[k] for k in HIGHLIGHT_KEYS if k in categories}
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "categories": categories,
            "highlights": highlights,
            "invite_url": INVITE_URL,
            "support_server_url": SUPPORT_SERVER_URL,
            "bot_name": bot_name,
        },
    )


@app.get("/about")
async def about(request: Request):
    _, bot_name = await _get_categories()
    return templates.TemplateResponse(
        "about.html",
        {
            "request": request,
            "invite_url": INVITE_URL,
            "support_server_url": SUPPORT_SERVER_URL,
            "bot_name": bot_name,
        },
    )


@app.get("/privacy")
async def privacy(request: Request):
    _, bot_name = await _get_categories()
    return templates.TemplateResponse(
        "privacy.html",
        {
            "request": request,
            "invite_url": INVITE_URL,
            "support_server_url": SUPPORT_SERVER_URL,
            "bot_name": bot_name,
            "last_updated": LEGAL_LAST_UPDATED,
        },
    )


@app.get("/terms")
async def terms(request: Request):
    _, bot_name = await _get_categories()
    return templates.TemplateResponse(
        "terms.html",
        {
            "request": request,
            "invite_url": INVITE_URL,
            "support_server_url": SUPPORT_SERVER_URL,
            "bot_name": bot_name,
            "last_updated": LEGAL_LAST_UPDATED,
        },
    )


@app.get("/api/stats")
async def api_stats():
    """Proxied straight through to the bot's API. The frontend JS keeps
    calling this same relative '/api/stats' path on the website's own
    domain — no CORS headaches, no hardcoded bot URL in client-side code."""
    data = await _fetch_json("/api/stats")
    return JSONResponse(data or _FALLBACK_STATS)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}