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
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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

# ── Music panel login (Discord OAuth2) ──────────────────────────────────────
# Separate from DISCORD_CLIENT_ID's use above (that one's only used to build
# the public "Add to Discord" invite link — no secret needed for that).
# Logging in for the music panel is a real OAuth2 flow, so it needs the
# application's client secret too (Discord Developer Portal -> your app ->
# OAuth2 -> Client Secret). Uses the "identify guilds" scope to find out
# which servers a visitor is actually in and what permissions they hold
# there — never asked to do anything on the user's behalf beyond that.
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_OAUTH_SCOPE = "identify guilds"
# Signs the session cookie issued after login (HMAC, not encryption — the
# payload is non-secret: just a Discord user id/username/avatar + which of
# their guilds are manageable). MUST be set in production; a blank/default
# secret means anyone could forge a session cookie for any user.
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
SESSION_MAX_AGE = 7 * 24 * 3600  # 7 days
# Discord permission bit for "Manage Server" — used to decide which of a
# logged-in user's guilds show up as controllable in the music panel.
PERM_MANAGE_GUILD = 0x20

# Shared with the bot (set the SAME value as MUSIC_API_SECRET on the bot's
# deployment — see Jarvis-4.0/web/app.py). Every action the panel sends to
# the bot is signed with this and expires in MUSIC_TOKEN_TTL seconds, so a
# leaked/logged token is only ever usable for a few seconds.
MUSIC_API_SECRET = os.getenv("MUSIC_API_SECRET", "")
MUSIC_TOKEN_TTL = 60

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

# Optional but recommended: set this to the same secret you configure on the
# GitHub webhook (Settings -> Webhooks) for Jarvis-4.0 and/or Jarvis_Website.
# When set, POST /webhook/github lets GitHub push new commits to the site
# the instant you push, instead of waiting up to COMMITS_CACHE_TTL for the
# next natural refresh (and instead of needing a manual restart). If left
# blank, the webhook route still works but accepts unsigned requests — fine
# for a private/low-stakes deploy, not recommended if the URL could leak.
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
COMMITS_PER_REPO = 15
COMMITS_CACHE_TTL = 900  # seconds (15 min) — commits don't need to appear
                          # instantly; this just needs to be short enough
                          # that a push shows up same-day without hammering
                          # GitHub's API on every visitor.
CHANGELOG_DISPLAY_LIMIT = 20  # cap how many merged commits render on the
                                # page itself — the full history is still
                                # one click away on GitHub via the links
                                # rendered under the table.

# Only commits pushed AFTER this moment show up in the feed — everything
# older (the backlog that was already sitting in the repos) stays hidden.
# Override with CHANGELOG_CUTOFF ("YYYY-MM-DDTHH:MM:SSZ") to move the line;
# left as-is, only new pushes from here on will ever appear.
CHANGELOG_CUTOFF = os.getenv("CHANGELOG_CUTOFF", "2026-07-04T00:00:00Z")

# ── Publish gate ─────────────────────────────────────────────────────────
# Bot-side kill switch: Jarvis-4.0 has a `publish_config.json` at its repo
# root with {"publish": false}. While publish is false, any commits pushed
# after the last publish stay hidden from the site — you can land a whole
# batch of WIP commits for a big update without it leaking early. Flip
# "publish" to true and push -> the whole pending batch appears at once.
# Flip it back to false afterwards to start hiding the next batch again.
PUBLISH_CONFIG_REPO = os.getenv("PUBLISH_CONFIG_REPO", "Phantom3192/Jarvis-4.0")
PUBLISH_CONFIG_BRANCH = os.getenv("PUBLISH_CONFIG_BRANCH", "main")
PUBLISH_CONFIG_PATH = os.getenv("PUBLISH_CONFIG_PATH", "publish_config.json")
# Where the "last time publish was true" timestamp is remembered across
# restarts, so toggling publish back to false freezes the cutoff instead
# of resetting it.
PUBLISH_STATE_FILE = BASE_DIR / ".changelog_publish_state.json"

HIGHLIGHT_KEYS = ["🤖 AI", "🧠 Memory", "♟️ Games", "🎵 Music", "🪙 Jarvis Credits", "⏰ Reminders"]

REQUEST_TIMEOUT = 5.0       # seconds — fail fast if the bot is slow/down

# ── Background refresh intervals ────────────────────────────────────────────
# All three of these (stats, categories, leaderboard) are pulled by a fixed-
# schedule background task started at app startup (see lifespan below), NOT
# fetched live on a visitor's request. A request handler only ever reads
# whatever is already sitting in memory. This means traffic volume has zero
# effect on how often the bot's API gets hit — one visitor or ten thousand,
# the bot sees exactly one call per interval, per route.
STATS_REFRESH_SECS = 2          # matches the bot's own sampling cadence —
                                 # no point polling faster than the source
                                 # actually updates
CATEGORIES_REFRESH_SECS = 300  # docs content barely changes
LEADERBOARD_REFRESH_SECS = 60  # mirrors the bot's own leaderboard cadence

# A single reused httpx.AsyncClient instead of creating a new one (and
# paying a fresh TCP+TLS handshake) on every single upstream call. Created
# once at startup and closed on shutdown via the lifespan below; every
# helper that talks to the bot's API or GitHub reuses this same client and
# its connection pool.
http_client: httpx.AsyncClient | None = None


_background_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)

    # Pull everything once, right now, before we start serving requests —
    # so the very first visitor after a deploy/restart already sees real
    # data instead of the empty/fallback state for a few seconds.
    await asyncio.gather(
        _refresh_stats_once(),
        _refresh_categories_once(),
        _refresh_leaderboard_once(),
    )

    # Then hand off to the three background loops, which keep re-pulling
    # on their own fixed schedules for the rest of the process's life —
    # completely independent of how many people are visiting the site.
    _background_tasks.extend([
        asyncio.create_task(_refresh_stats_loop()),
        asyncio.create_task(_refresh_categories_loop()),
        asyncio.create_task(_refresh_leaderboard_loop()),
    ])

    try:
        yield
    finally:
        for task in _background_tasks:
            task.cancel()
        await http_client.aclose()


app = FastAPI(title="Jarvis Website", docs_url=None, redoc_url=None, lifespan=lifespan)
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
    "api_latency_ms": None,
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
        res = await http_client.get(f"{BOT_API_URL}{path}", timeout=timeout)
        res.raise_for_status()
        return res.json()
    except Exception:
        return None


async def _get_bot_guild_ids() -> set[str]:
    """Which guild IDs the bot is actually in right now, straight from
    /api/guilds — no caching layer here (unlike stats/leaderboard) since
    this is only called on a login/panel page load, not polled, so it's
    already at most one bot API call per visitor action, not per second."""
    data = await _fetch_json("/api/guilds")
    if not data:
        return set()
    return {g["id"] for g in data.get("guilds", [])}


def _sign_music_token(user_id: str, guild_id: str) -> str:
    """Short-lived (MUSIC_TOKEN_TTL seconds) proof, for the BOT to trust,
    that this website already authenticated `user_id` via Discord OAuth
    and is asking on their behalf about `guild_id`. The bot still
    independently re-checks the user's live permissions in that guild
    before honoring any action — this token only proves identity, not
    authorization."""
    payload = {"user_id": user_id, "guild_id": guild_id, "exp": int(time.time()) + MUSIC_TOKEN_TTL}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(MUSIC_API_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


async def _call_bot_music_api(
    method: str, guild_id: str, session: dict, path: str, json_body: dict | None = None,
) -> dict:
    if not BOT_API_URL or not MUSIC_API_SECRET:
        return {"error": "not_configured"}
    token = _sign_music_token(session["user_id"], guild_id)
    headers = {"X-Music-Token": token}
    url = f"{BOT_API_URL}/api/music/{guild_id}{path}"
    try:
        if method == "GET":
            res = await http_client.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        else:
            res = await http_client.post(url, headers=headers, json=json_body or {}, timeout=REQUEST_TIMEOUT)
        return res.json()
    except Exception:
        return {"error": "bot_unreachable"}


async def _authorize_panel_request(request: Request, guild_id: str):
    """Website-side gate before any bot call is even attempted: must be
    logged in, and the guild must be one this specific user is allowed to
    manage (from their own OAuth guild list, checked at login) AND one the
    bot is actually currently in. Returns (session, error_response)."""
    session = await _get_session(request)
    if not session:
        return None, JSONResponse({"error": "not_logged_in"}, status_code=401)
    if not any(g["id"] == guild_id for g in session.get("guilds", [])):
        return None, JSONResponse({"error": "forbidden"}, status_code=403)
    bot_guild_ids = await _get_bot_guild_ids()
    if guild_id not in bot_guild_ids:
        return None, JSONResponse({"error": "bot_not_in_guild"}, status_code=404)
    return session, None


# ── Session signing (music panel login) ─────────────────────────────────────
# Lightweight HMAC-signed cookie — no server-side session store needed since
# the payload itself is small and non-secret. Same pattern as the
# HMAC-verified webhooks below (hmac.compare_digest to avoid timing leaks).

def _sign_session(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _verify_session(token: str | None) -> dict | None:
    if not token or not SESSION_SECRET:
        return None
    try:
        body, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body.encode()))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


async def _get_session(request: Request) -> dict | None:
    return _verify_session(request.cookies.get("jarvis_session"))


async def _get_stats() -> dict:
    """Pure in-memory read — never makes a network call.

    Populated by _refresh_stats_loop() on a fixed background schedule (see
    lifespan below). Route handlers and main.js polls just read whatever
    is already cached; visitor traffic never triggers a live call to the
    bot, so 1 visitor and 10,000 visitors put the exact same load on it.
    """
    return _stats_cache["data"] or {}


async def _refresh_stats_once() -> None:
    """One pull of /api/stats into the cache. A failed pull just leaves
    the last good snapshot in place — it never blanks the cache out or
    falls back to zeros while the bot is only briefly unreachable
    (deploy, restart, blip)."""
    payload = await _fetch_json("/api/stats")
    if payload is not None:
        _stats_cache["data"] = payload
        _stats_cache["ts"] = time.time()


async def _refresh_stats_loop() -> None:
    """Runs for the life of the process, independent of request traffic."""
    while True:
        await asyncio.sleep(STATS_REFRESH_SECS)
        await _refresh_stats_once()


async def _get_leaderboard() -> dict:
    """Pure in-memory read — see _get_stats() above for why."""
    return _leaderboard_cache["data"] or {"leaderboard": [], "currency_name": "Jarvis Credit", "currency_emoji": "🪙"}


async def _refresh_leaderboard_once() -> None:
    payload = await _fetch_json("/api/leaderboard")
    if payload is not None:
        _leaderboard_cache["data"] = payload
        _leaderboard_cache["ts"] = time.time()


async def _refresh_leaderboard_loop() -> None:
    while True:
        await asyncio.sleep(LEADERBOARD_REFRESH_SECS)
        await _refresh_leaderboard_once()


def _read_publish_state() -> str:
    """Last cutoff timestamp we froze the feed at. Falls back to
    CHANGELOG_CUTOFF the very first time (nothing published yet)."""
    try:
        return json.loads(PUBLISH_STATE_FILE.read_text())["published_until"]
    except Exception:
        return CHANGELOG_CUTOFF


def _write_publish_state(iso_ts: str) -> None:
    try:
        PUBLISH_STATE_FILE.write_text(json.dumps({"published_until": iso_ts}))
    except Exception:
        pass  # non-fatal — worst case we re-reveal the same batch next TTL


async def _get_effective_cutoff(client: httpx.AsyncClient) -> str:
    """Resolve the upper-bound timestamp — commits newer than this stay
    hidden — honoring the bot's publish_config.json publish flag.

    publish == true  -> reveal everything up to now, and remember "now" as
                         the new frozen ceiling for next time.
    publish == false -> cap visibility at whatever was last published (or
                         the original CHANGELOG_CUTOFF if nothing's shipped
                         yet), so anything newer stays hidden.
    """
    frozen = _read_publish_state()
    headers = {"Accept": "application/vnd.github.raw", "User-Agent": "jarvis-website"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        res = await client.get(
            f"https://raw.githubusercontent.com/{PUBLISH_CONFIG_REPO}/{PUBLISH_CONFIG_BRANCH}/{PUBLISH_CONFIG_PATH}",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        res.raise_for_status()
        publish = bool(res.json().get("publish", False))
    except Exception:
        # Can't read the flag -> fail closed, stay frozen rather than leak.
        return frozen

    if not publish:
        return frozen  # ceiling stays put -> anything newer stays hidden

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_publish_state(now_iso)
    return now_iso  # raise the ceiling to now so the whole pending batch
                     # (everything between the old and new ceiling) shows up


async def _fetch_repo_commits(client: httpx.AsyncClient, repo: str, ceiling: str) -> list[dict]:
    """Fetch recent commits for one 'owner/repo' from the public GitHub API.

    Two bounds apply: CHANGELOG_CUTOFF is the absolute floor (never show the
    pre-launch backlog), and `ceiling` is the dynamic publish-gate bound
    (never show commits newer than the last thing that was published)."""
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
                continue  # older than the site launch — pre-launch backlog, skip it
            if date > ceiling:
                continue  # newer than the last publish — still WIP, skip it
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


async def _refresh_commits_cache() -> list[dict]:
    """Actually hit the GitHub API and repopulate _commits_cache.

    Split out from _get_recent_commits() so the webhook handler can force
    an immediate refresh (bypassing the TTL check) without duplicating the
    fetch/merge/sort logic.
    """
    ceiling = await _get_effective_cutoff(http_client)
    results = await asyncio.gather(
        *(_fetch_repo_commits(http_client, repo, ceiling) for repo in CHANGELOG_REPOS)
    )

    merged = [commit for repo_commits in results for commit in repo_commits]
    if not merged:
        # GitHub unreachable/rate-limited — keep serving the stale cache
        # rather than blanking it out, but don't bump the timestamp so the
        # next request tries again instead of waiting a full TTL.
        return _commits_cache["data"]

    merged.sort(key=lambda c: c["date"], reverse=True)
    _commits_cache["data"] = merged
    _commits_cache["ts"] = time.time()
    return merged


async def _get_recent_commits() -> list[dict]:
    """Cached, merged, newest-first commit feed across CHANGELOG_REPOS.

    Normally refreshed lazily whenever the TTL lapses. The GitHub webhook
    (/webhook/github) can also invalidate this early so a push shows up
    immediately instead of waiting up to COMMITS_CACHE_TTL.
    """
    now = time.time()
    if _commits_cache["data"] and now - _commits_cache["ts"] < COMMITS_CACHE_TTL:
        return _commits_cache["data"]

    return await _refresh_commits_cache()


def _verify_github_signature(secret: str, payload: bytes, signature_header: str) -> bool:
    """Validate GitHub's X-Hub-Signature-256 header (HMAC-SHA256 of the raw
    request body, keyed with the webhook secret) using a constant-time
    comparison so this can't be timing-attacked."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


async def _get_categories() -> tuple[dict, str]:
    """Pure in-memory read — see _get_stats() above for why."""
    return _categories_cache["data"], _categories_cache["bot_name"]


async def _refresh_categories_once() -> None:
    payload = await _fetch_json("/api/categories")
    if payload and payload.get("categories"):
        _categories_cache["data"] = payload["categories"]
        _categories_cache["bot_name"] = payload.get("bot_name", DEFAULT_BOT_NAME)
        _categories_cache["ts"] = time.time()


async def _refresh_categories_loop() -> None:
    while True:
        await asyncio.sleep(CATEGORIES_REFRESH_SECS)
        await _refresh_categories_once()


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


# ── Music panel: Discord OAuth2 login ───────────────────────────────────────

@app.get("/auth/login")
async def auth_login(request: Request):
    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET:
        return RedirectResponse(url="/music?error=oauth_not_configured")
    # A random one-time value, stashed in a short-lived cookie and checked
    # against Discord's redirect back — standard OAuth2 CSRF protection so
    # a malicious site can't trick a logged-in browser into completing a
    # login flow it never started.
    oauth_state = secrets.token_urlsafe(24)
    redirect_uri = str(request.url_for("auth_callback"))
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": DISCORD_OAUTH_SCOPE,
        "state": oauth_state,
        "prompt": "consent",
    }
    url = "https://discord.com/oauth2/authorize?" + urllib.parse.urlencode(params)
    resp = RedirectResponse(url=url)
    resp.set_cookie("jarvis_oauth_state", oauth_state, httponly=True, secure=True,
                     samesite="lax", max_age=600)
    return resp


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        # User clicked "Cancel" on Discord's consent screen, or something
        # else went wrong on Discord's side — not a bug on ours.
        return RedirectResponse(url="/music?error=oauth_denied")

    expected_state = request.cookies.get("jarvis_oauth_state", "")
    if not code or not state or not hmac.compare_digest(state, expected_state):
        return RedirectResponse(url="/music?error=oauth_state_mismatch")

    redirect_uri = str(request.url_for("auth_callback"))
    try:
        token_resp = await http_client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token")
        if not access_token:
            return RedirectResponse(url="/music?error=oauth_token_failed")

        auth_header = {"Authorization": f"Bearer {access_token}"}
        user_resp = await http_client.get("https://discord.com/api/users/@me", headers=auth_header)
        user_resp.raise_for_status()
        user = user_resp.json()

        guilds_resp = await http_client.get("https://discord.com/api/users/@me/guilds", headers=auth_header)
        guilds_resp.raise_for_status()
        discord_guilds = guilds_resp.json()
    except Exception:
        return RedirectResponse(url="/music?error=oauth_discord_unreachable")

    # Only keep guilds this user can actually manage — owner, or has the
    # Manage Server permission. Everything else is irrelevant to the panel
    # even if the bot happens to be in it (they shouldn't be able to touch
    # music in a server they have no authority over).
    manageable = [
        {"id": g["id"], "name": g["name"], "icon": g.get("icon")}
        for g in discord_guilds
        if g.get("owner") or (int(g.get("permissions", 0)) & PERM_MANAGE_GUILD)
    ]

    session_payload = {
        "user_id": user["id"],
        "username": user.get("global_name") or user.get("username"),
        "avatar": user.get("avatar"),
        "guilds": manageable,
        "exp": int(time.time()) + SESSION_MAX_AGE,
    }
    token = _sign_session(session_payload)

    resp = RedirectResponse(url="/music")
    resp.set_cookie(
        "jarvis_session", token,
        httponly=True, secure=True, samesite="lax", max_age=SESSION_MAX_AGE,
    )
    resp.delete_cookie("jarvis_oauth_state")
    return resp


@app.get("/auth/logout")
async def auth_logout():
    resp = RedirectResponse(url="/music")
    resp.delete_cookie("jarvis_session")
    return resp


@app.get("/music")
async def music_page(request: Request, error: str = ""):
    _, bot_name = await _get_categories()
    session = await _get_session(request)

    controllable_guilds: list[dict] = []
    if session:
        bot_guild_ids = await _get_bot_guild_ids()
        controllable_guilds = [g for g in session["guilds"] if g["id"] in bot_guild_ids]

    return templates.TemplateResponse(
        "music.html",
        {
            "request": request,
            "invite_url": INVITE_URL,
            "support_server_url": SUPPORT_SERVER_URL,
            "bot_name": bot_name,
            "session": session,
            "controllable_guilds": controllable_guilds,
            "oauth_error": error,
        },
    )


# ── Music panel control API (browser -> website -> bot) ─────────────────────
# The browser never talks to the bot directly and never sees MUSIC_API_SECRET
# — every call here re-checks the session cookie, then signs a fresh
# short-lived token server-side before forwarding to the bot.

@app.get("/api/panel/{guild_id}/state")
async def panel_state(guild_id: str, request: Request):
    session, err = await _authorize_panel_request(request, guild_id)
    if err:
        return err
    return JSONResponse(await _call_bot_music_api("GET", guild_id, session, "/state"))


@app.get("/api/panel/{guild_id}/channels")
async def panel_channels(guild_id: str, request: Request):
    session, err = await _authorize_panel_request(request, guild_id)
    if err:
        return err
    return JSONResponse(await _call_bot_music_api("GET", guild_id, session, "/channels"))


@app.post("/api/panel/{guild_id}/join")
async def panel_join(guild_id: str, request: Request):
    session, err = await _authorize_panel_request(request, guild_id)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    return JSONResponse(await _call_bot_music_api("POST", guild_id, session, "/join", body))


@app.post("/api/panel/{guild_id}/leave")
async def panel_leave(guild_id: str, request: Request):
    session, err = await _authorize_panel_request(request, guild_id)
    if err:
        return err
    return JSONResponse(await _call_bot_music_api("POST", guild_id, session, "/leave"))


@app.post("/api/panel/{guild_id}/pause")
async def panel_pause(guild_id: str, request: Request):
    session, err = await _authorize_panel_request(request, guild_id)
    if err:
        return err
    return JSONResponse(await _call_bot_music_api("POST", guild_id, session, "/pause"))


@app.post("/api/panel/{guild_id}/skip")
async def panel_skip(guild_id: str, request: Request):
    session, err = await _authorize_panel_request(request, guild_id)
    if err:
        return err
    return JSONResponse(await _call_bot_music_api("POST", guild_id, session, "/skip"))


@app.post("/api/panel/{guild_id}/volume")
async def panel_volume(guild_id: str, request: Request):
    session, err = await _authorize_panel_request(request, guild_id)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    return JSONResponse(await _call_bot_music_api("POST", guild_id, session, "/volume", body))


@app.post("/api/panel/{guild_id}/play")
async def panel_play(guild_id: str, request: Request):
    session, err = await _authorize_panel_request(request, guild_id)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    return JSONResponse(await _call_bot_music_api("POST", guild_id, session, "/play", body))


@app.post("/api/panel/{guild_id}/queue/remove")
async def panel_queue_remove(guild_id: str, request: Request):
    session, err = await _authorize_panel_request(request, guild_id)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    return JSONResponse(await _call_bot_music_api("POST", guild_id, session, "/queue/remove", body))


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
    # The merged feed can grow past what's worth rendering inline (two repos
    # x COMMITS_PER_REPO each) — cap what's shown on the page and point
    # people at the real commit history on GitHub for anything older.
    displayed_commits = commits[:CHANGELOG_DISPLAY_LIMIT]
    repo_links = [
        {"label": repo.split("/")[-1], "url": f"https://github.com/{repo}/commits"}
        for repo in CHANGELOG_REPOS
    ]
    return templates.TemplateResponse(
        "changelog.html",
        {
            "request": request,
            "entries": CHANGELOG_ENTRIES,
            "commits": displayed_commits,
            "has_more_commits": len(commits) > len(displayed_commits),
            "repo_links": repo_links,
            "invite_url": INVITE_URL,
            "support_server_url": SUPPORT_SERVER_URL,
            "bot_name": bot_name,
        },
    )


@app.get("/api/changelog")
async def api_changelog():
    """JSON feed of the same auto-pulled commit history shown on /changelog."""
    return JSONResponse({"commits": await _get_recent_commits()})


@app.post("/webhook/github")
async def github_webhook(request: Request):
    """GitHub calls this the moment someone pushes to Jarvis-4.0 or
    Jarvis_Website (once configured as a webhook on those repos — see
    README/comments near GITHUB_WEBHOOK_SECRET above).

    This is what makes the changelog "just update" instead of needing a
    restart: on every push event we drop the in-memory commit cache and
    refetch right away, so the very next page load (yours or anyone
    else's) already has the new commit — no waiting for COMMITS_CACHE_TTL
    to lapse and no redeploy needed.
    """
    body = await request.body()

    if GITHUB_WEBHOOK_SECRET:
        signature = request.headers.get("x-hub-signature-256", "")
        if not _verify_github_signature(GITHUB_WEBHOOK_SECRET, body, signature):
            return JSONResponse({"error": "invalid signature"}, status_code=401)

    event = request.headers.get("x-github-event", "")

    if event == "push":
        global CHANGELOG_ENTRIES
        # Re-read CHANGELOG.md too, in case it exists and was just edited —
        # today it's not present so this is a no-op, but it means adding
        # one later doesn't reintroduce a "needs a restart" gap.
        CHANGELOG_ENTRIES = _load_changelog()
        await _refresh_commits_cache()
        return JSONResponse({"ok": True, "refreshed": True})

    # "ping" (sent automatically when the webhook is first created) and any
    # other event types are just acknowledged so GitHub doesn't flag the
    # webhook as failing.
    return JSONResponse({"ok": True, "refreshed": False})


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