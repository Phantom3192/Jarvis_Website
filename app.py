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
import asyncio
import os
import re
import time
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

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

# top.gg voting — set TOPGG_URL once the bot is listed (e.g.
# https://top.gg/bot/<client_id>/vote). Until then it's left blank on
# purpose: /vote still renders normally, but the "Vote Now" button routes
# through /vote/go, which shows a "temporarily unavailable" page instead
# of sending people to a listing that doesn't exist yet.
TOPGG_URL = os.getenv("TOPGG_URL", "").strip()

# ── Auto-updating changelog ─────────────────────────────────────────────────
# Rather than requiring a manual CHANGELOG.md edit for every change, the
# /changelog page pulls real commit history straight from GitHub's public
# API. Push a commit -> it shows up here on the next cache refresh, no
# redeploy or file edit needed. CHANGELOG.md (if present) is still shown
# above this as optional hand-written "highlight" entries for major
# releases — the commit feed below it is what actually stays current
# automatically.
CHANGELOG_REPOS = [
    r.strip() for r in os.getenv(
        "CHANGELOG_GITHUB_REPOS", "Phantom3192/Jarvis-4.0,Phantom3192/Jarvis_Website"
    ).split(",") if r.strip()
]
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")  # optional — raises the API rate
                                                # limit from 60/hr to 5000/hr.
                                                # Not required for a couple of
                                                # low-traffic public repos.
COMMITS_PER_REPO = 15
COMMITS_CACHE_TTL = 900  # seconds (15 min) — commits don't need to appear
                          # instantly; this just needs to be short enough
                          # that a push shows up same-day without hammering
                          # GitHub's API on every visitor.

# Only commits pushed AFTER this moment show up in the feed — everything
# older (the backlog that was already sitting in the repos) stays hidden.
# Override with CHANGELOG_CUTOFF ("YYYY-MM-DDTHH:MM:SSZ") to move the line;
# left as-is, only new pushes from here on will ever appear.
CHANGELOG_CUTOFF = os.getenv("CHANGELOG_CUTOFF", "2026-07-04T00:00:00Z")

HIGHLIGHT_KEYS = ["🤖 AI", "🧠 Memory", "♟️ Games", "🎵 Music", "🪙 Jarvis Credits", "⏰ Reminders"]

REQUEST_TIMEOUT = 5.0       # seconds — fail fast if the bot is slow/down
CATEGORIES_CACHE_TTL = 300  # seconds — docs content barely changes, cache it
STATS_CACHE_TTL = 8         # seconds — stats update every 15s on the client
                             # poll anyway, so caching this short avoids a
                             # live httpx round-trip to the bot on every
                             # single page load/visitor without the numbers
                             # ever looking stale to a human.
LEADERBOARD_CACHE_TTL = 60  # seconds — mirrors the bot's own leaderboard
                             # cache TTL (see web/app.py), no point polling
                             # faster than the source refreshes.

app = FastAPI(title="Jarvis Website", docs_url=None, redoc_url=None)
# Railway (like most PaaS) terminates TLS at its edge and forwards plain
# HTTP internally, tagging the original scheme in X-Forwarded-Proto. Without
# this, request.url.scheme (and therefore url_for(...) / request.url used
# for og:image and og:url in <head>) would render as "http://" even on the
# live https site — which is exactly what makes Discord/Slack/etc. link
# embeds fail to show a preview image.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["md_bold"] = lambda s: re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s or "")
templates.env.filters["md_code"] = lambda s: re.sub(r"`(.+?)`", r"<code>\1</code>", s or "")


def _fmt_commit_date(iso: str) -> str:
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%b %-d, %Y")
    except (ValueError, TypeError):
        return iso or ""


templates.env.filters["commit_date"] = _fmt_commit_date

# Shown if the bot has never successfully responded yet (first deploy, etc.)
_FALLBACK_STATS = {
    "guilds": 0,
    "users": 0,
    "uptime_human": "—",
    "latency_ms": None,
    "online": False,
    "bot_name": DEFAULT_BOT_NAME,
    "cpu_percent": None,
    "ram_used_bytes": None,
    "ram_total_bytes": None,
    "ram_percent": None,
    "rss_usage": None,
    "bot_cpu": None,
    "storage_percent": None,
    "storage_used_bytes": None,
    "storage_limit_bytes": None,
    "threads": None,
}

_categories_cache: dict = {"data": {}, "bot_name": DEFAULT_BOT_NAME, "ts": 0.0}
_stats_cache: dict = {"data": None, "ts": 0.0}
_leaderboard_cache: dict = {"data": None, "ts": 0.0}
_commits_cache: dict = {"data": [], "ts": 0.0}


def _load_changelog() -> list[dict]:
    """Parse CHANGELOG.md into [{date, title, bullets}], newest first.

    Format: '## YYYY-MM-DD — Title' headings followed by '- ' bullet
    lines. Kept as a tiny hand-rolled parser rather than pulling in a
    markdown dependency for four lines of syntax.
    """
    path = BASE_DIR / "CHANGELOG.md"
    if not path.exists():
        return []

    entries: list[dict] = []
    current: dict | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if line.startswith("## "):
            if current:
                entries.append(current)
            heading = line[3:].strip()
            if "—" in heading:
                date, title = heading.split("—", 1)
            elif " - " in heading:
                date, title = heading.split(" - ", 1)
            else:
                date, title = "", heading
            current = {"date": date.strip(), "title": title.strip(), "bullets": []}
        elif line.startswith("- ") and current is not None:
            current["bullets"].append(line[2:].strip())
        elif stripped and current is not None and current["bullets"]:
            # Soft-wrapped continuation of the previous bullet line — join
            # it back on rather than dropping it or treating as a new one.
            current["bullets"][-1] = f"{current['bullets'][-1]} {stripped}"
    if current:
        entries.append(current)
    return entries


CHANGELOG_ENTRIES = _load_changelog()


def _normalize_stats(raw_stats: dict) -> dict:
    usage = raw_stats.get("usage", {}) if isinstance(raw_stats.get("usage"), dict) else {}

    def pick(*keys, fallback=None):
        for key in keys:
            if key in raw_stats and raw_stats[key] not in (None, ""):
                return raw_stats[key]
            if key in usage and usage[key] not in (None, ""):
                return usage[key]
        return fallback

    return {
        "cpu_percent": pick("cpu_percent", "cpu", "cpu_usage", "system_cpu", "system_cpu_percent"),
        "ram_used_bytes": pick("ram_used_bytes"),
        "ram_total_bytes": pick("ram_total_bytes"),
        "ram_percent": pick("ram_percent", "ram", "memory", "memory_percent"),
        "rss_usage": pick("process_rss_bytes", "rss_usage", "rss", "bot_rss"),
        "bot_cpu": pick("process_cpu_percent", "bot_cpu", "process_cpu", "bot_cpu_percent"),
        "storage_percent": pick("storage_percent", "storage", "disk_usage", "bot_storage", "storage_usage", "disk_usage_human"),
        "storage_used_bytes": pick("storage_used_bytes", "storage_used"),
        "storage_limit_bytes": pick("storage_limit_bytes", "storage_limit"),
        "threads": pick("threads", "thread_count", "bot_threads"),
    }


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


async def _get_stats() -> dict:
    """Short-TTL cache in front of the bot's /api/stats.

    Without this, every single visitor to '/', '/status', or a poll from
    main.js triggers a live httpx call straight to the bot process. That's
    fine at low traffic but doesn't scale — the cache here is short enough
    (STATS_CACHE_TTL) that no human ever perceives stale data, while still
    collapsing bursts of concurrent requests into one upstream call.
    """
    now = time.time()
    if _stats_cache["data"] is not None and now - _stats_cache["ts"] < STATS_CACHE_TTL:
        return _stats_cache["data"]

    payload = await _fetch_json("/api/stats")
    if payload is not None:
        _stats_cache["data"] = payload
        _stats_cache["ts"] = now
        return payload

    # Bot unreachable — serve the last good cached value if we have one,
    # rather than falling all the way back to zeros.
    return _stats_cache["data"] or {}


async def _get_leaderboard() -> dict:
    now = time.time()
    if _leaderboard_cache["data"] is not None and now - _leaderboard_cache["ts"] < LEADERBOARD_CACHE_TTL:
        return _leaderboard_cache["data"]

    payload = await _fetch_json("/api/leaderboard")
    if payload is not None:
        _leaderboard_cache["data"] = payload
        _leaderboard_cache["ts"] = now
        return payload

    return _leaderboard_cache["data"] or {"leaderboard": [], "currency_name": "Jarvis Credit", "currency_emoji": "🪙"}


async def _fetch_repo_commits(client: httpx.AsyncClient, repo: str) -> list[dict]:
    """Fetch recent commits for one 'owner/repo' from the public GitHub API."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "jarvis-website"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    try:
        res = await client.get(
            f"https://api.github.com/repos/{repo}/commits",
            params={"per_page": COMMITS_PER_REPO},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        res.raise_for_status()
        raw_commits = res.json()
    except Exception:
        return []

    repo_label = repo.split("/")[-1]
    parsed = []
    for c in raw_commits:
        try:
            commit = c["commit"]
            message = commit["message"].split("\n", 1)[0].strip()
            if message.lower().startswith("merge "):
                continue  # skip noisy merge commits
            date = commit["author"]["date"]  # ISO 8601, e.g. 2026-07-01T12:34:56Z
            if date <= CHANGELOG_CUTOFF:
                continue  # older than the cutoff — part of the old backlog, skip it
            parsed.append({
                "repo": repo_label,
                "message": message,
                "sha": c["sha"][:7],
                "date": date,
                "author": commit["author"].get("name", ""),
            })
        except (KeyError, TypeError):
            continue
    return parsed


async def _get_recent_commits() -> list[dict]:
    """Cached, merged, newest-first commit feed across CHANGELOG_REPOS."""
    now = time.time()
    if _commits_cache["data"] and now - _commits_cache["ts"] < COMMITS_CACHE_TTL:
        return _commits_cache["data"]

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(_fetch_repo_commits(client, repo) for repo in CHANGELOG_REPOS)
        )

    merged = [commit for repo_commits in results for commit in repo_commits]
    if not merged:
        # GitHub unreachable/rate-limited — serve stale cache rather than a
        # blank page.
        return _commits_cache["data"]

    merged.sort(key=lambda c: c["date"], reverse=True)
    _commits_cache["data"] = merged
    _commits_cache["ts"] = now
    return merged


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


@app.get("/status")
async def status_page(request: Request):
    raw_stats = await _get_stats() or {}
    normalized = _normalize_stats(raw_stats)
    stats = {**_FALLBACK_STATS, **raw_stats, **normalized}
    _, bot_name = await _get_categories()
    return templates.TemplateResponse(
        "status.html",
        {
            "request": request,
            "stats": stats,
            "invite_url": INVITE_URL,
            "support_server_url": SUPPORT_SERVER_URL,
            "bot_name": bot_name,
        },
    )


@app.get("/leaderboard")
async def leaderboard(request: Request):
    board = await _get_leaderboard()
    _, bot_name = await _get_categories()
    return templates.TemplateResponse(
        "leaderboard.html",
        {
            "request": request,
            "leaderboard": board.get("leaderboard", []),
            "currency_name": board.get("currency_name", "Jarvis Credit"),
            "currency_emoji": board.get("currency_emoji", "🪙"),
            "invite_url": INVITE_URL,
            "support_server_url": SUPPORT_SERVER_URL,
            "bot_name": bot_name,
        },
    )


@app.get("/api/leaderboard")
async def api_leaderboard():
    """Proxied + cached the same way /api/stats is — see _get_leaderboard()."""
    data = await _get_leaderboard()
    return JSONResponse(data)


@app.get("/vote")
async def vote(request: Request):
    _, bot_name = await _get_categories()
    return templates.TemplateResponse(
        "vote.html",
        {
            "request": request,
            "invite_url": INVITE_URL,
            "support_server_url": SUPPORT_SERVER_URL,
            "bot_name": bot_name,
            "topgg_configured": bool(TOPGG_URL),
        },
    )


@app.get("/vote/go")
async def vote_go(request: Request):
    """Single choke point the 'Vote Now' button posts through.

    Configured -> bounce straight to the real top.gg listing.
    Not configured -> render the "temporarily unavailable" page instead
    of 404ing or sending people to a dead '#' link.
    """
    if TOPGG_URL:
        return RedirectResponse(TOPGG_URL, status_code=302)

    _, bot_name = await _get_categories()
    return templates.TemplateResponse(
        "vote_unavailable.html",
        {
            "request": request,
            "invite_url": INVITE_URL,
            "support_server_url": SUPPORT_SERVER_URL,
            "bot_name": bot_name,
        },
        status_code=503,
    )


@app.get("/changelog")
async def changelog(request: Request):
    _, bot_name = await _get_categories()
    commits = await _get_recent_commits()
    return templates.TemplateResponse(
        "changelog.html",
        {
            "request": request,
            "entries": CHANGELOG_ENTRIES,
            "commits": commits,
            "invite_url": INVITE_URL,
            "support_server_url": SUPPORT_SERVER_URL,
            "bot_name": bot_name,
        },
    )


@app.get("/api/changelog")
async def api_changelog():
    """JSON feed of the same auto-pulled commit history shown on /changelog."""
    return JSONResponse({"commits": await _get_recent_commits()})


@app.get("/guide")
async def guide(request: Request):
    _, bot_name = await _get_categories()
    return templates.TemplateResponse(
        "guide.html",
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
    data = await _get_stats()
    return JSONResponse(data or _FALLBACK_STATS)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}