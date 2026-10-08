"""HTTP server: /health plus the dashboard JSON API (Phase 3).

Dashboard login is "Log in with Discord" (OAuth2):
  1. The dashboard page calls GET /api/oauth/start -> {url}.
  2. The user approves on discord.com and lands on /api/oauth/callback.
  3. The bot checks the Discord user id against the server owner id
     (or the /nuke exempt user whitelist) and, if allowed, mints a
     session token and redirects back to the dashboard page with it.

Every other /api/* endpoint then accepts
    Authorization: Bearer <session token>
The old DASHBOARD_KEY still works as a fallback if it is set.
If NEITHER DISCORD_CLIENT_SECRET nor DASHBOARD_KEY is set, /api/*
(except /api/health and /api/oauth/*) answers 503 "not configured".

Sessions live in memory only: a bot restart logs everyone out.
CORS is wide open (*) so the static dashboard on GitHub Pages can call it.
"""
import json
import os
import secrets
import time
import urllib.parse

import aiohttp
from aiohttp import web

from config import DEFAULT_SETTINGS

# Public: the Discord application id for Lynxo Bot. Safe to ship in code.
DISCORD_CLIENT_ID_FALLBACK = "1557495025921949839"
SESSION_DAYS = 7


def _client_id():
    return os.environ.get("DISCORD_CLIENT_ID") or DISCORD_CLIENT_ID_FALLBACK


def _client_secret():
    return os.environ.get("DISCORD_CLIENT_SECRET") or ""


def _legacy_key():
    return os.environ.get("DASHBOARD_KEY") or ""


def _dashboard_configured():
    return bool(_client_secret()) or bool(_legacy_key())


def _dashboard_url():
    base = (os.environ.get("DASHBOARD_URL")
            or "https://justinyocum4-code.github.io/Lynxo-Bot/dashboard/").strip()
    return base.rstrip("/")


def _redirect_uri(request):
    # Built from the request's own host so it keeps working if the
    # Render URL ever changes. This exact URL must be registered in the
    # Discord developer portal under OAuth2 -> Redirects.
    return f"https://{request.host}/api/oauth/callback"


def _sessions(request):
    return request.app["sessions"]


def _prune_sessions(request):
    now = time.time()
    sessions = _sessions(request)
    for tok in [t for t, s in sessions.items() if s["expires"] < now]:
        sessions.pop(tok, None)


def _session_for(request):
    """Return the session dict for a valid Bearer session token, else None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    tok = auth[len("Bearer "):].strip()
    if not tok:
        return None
    _prune_sessions(request)
    sess = _sessions(request).get(tok)
    if not sess or sess["expires"] < time.time():
        _sessions(request).pop(tok, None)
        return None
    return sess


def _authed(request):
    """(ok, why, session_or_None). why is 'ok', 'disabled', or 'bad'."""
    sess = _session_for(request)
    if sess is not None:
        return True, "ok", sess
    key = _legacy_key()
    if key and request.headers.get("Authorization", "") == f"Bearer {key}":
        return True, "ok", None
    if not _dashboard_configured():
        return False, "disabled", None
    return False, "bad", None


def _guard(handler):
    async def wrapper(request):
        ok, why, sess = _authed(request)
        if not ok:
            if why == "disabled":
                return web.json_response(
                    {"error": "Dashboard login is not configured. Set "
                              "DISCORD_CLIENT_SECRET in the Render environment "
                              "to turn it on."},
                    status=503)
            return web.json_response(
                {"error": "You are not logged in. Log in with Discord on "
                          "the dashboard page first."}, status=401)
        request["dash_session"] = sess  # None for legacy-key logins
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            return web.json_response({"error": f"Server error: {e}"}, status=500)
    return wrapper


def _bot(request):
    return request.app["bot"]


def _guild(request):
    bot = _bot(request)
    if bot is None:
        return None
    gid = request.query.get("guild_id") or (request.match_info.get("gid"))
    if gid:
        try:
            return bot.get_guild(int(gid))
        except (ValueError, TypeError):
            return None
    ids = bot.store.setup_guilds()
    return bot.get_guild(ids[0]) if ids else None


def _user_allowed(bot, user_id):
    """True if this Discord user id is the server owner or whitelisted."""
    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        return False
    for gid in bot.store.setup_guilds():
        guild = bot.get_guild(gid)
        if guild is not None and guild.owner_id == uid:
            return True
        wl = (bot.store.guild(gid).get("nuke_whitelist", {}) or {})
        if uid in (wl.get("users") or []):
            return True
    return False


# ---------------- public ----------------

async def _health(request):
    return web.Response(text="ok")


# ---------------- Discord OAuth ----------------

async def oauth_start(request):
    """Public. Returns the Discord authorize URL for 'Log in with Discord'."""
    redirect_uri = _redirect_uri(request)
    params = {
        "client_id": _client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify",
    }
    url = "https://discord.com/oauth2/authorize?" + urllib.parse.urlencode(params)
    return web.json_response({"url": url, "redirect_uri": redirect_uri})


async def oauth_callback(request):
    """Public. Handles Discord's redirect, mints a session, bounces home."""
    dash = _dashboard_url()
    code = request.query.get("code", "")
    if request.query.get("error") or not code:
        raise web.HTTPFound(f"{dash}/?error=denied")

    secret = _client_secret()
    if not secret:
        raise web.HTTPFound(f"{dash}/?error=not_configured")

    redirect_uri = _redirect_uri(request)
    bot = _bot(request)

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                "https://discord.com/api/oauth2/token",
                data={
                    "client_id": _client_id(),
                    "client_secret": secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                tok = await r.json()
        access = (tok or {}).get("access_token", "")
        if not access:
            print(f"oauth token exchange failed: {tok}", flush=True)
            raise web.HTTPFound(f"{dash}/?error=token")

        async with aiohttp.ClientSession() as sess:
            async with sess.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {access}"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                me = await r.json()
        user_id = str((me or {}).get("id", ""))
        username = (me or {}).get("username", "") or "Discord user"
    except web.HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"oauth callback failed: {e!r}", flush=True)
        raise web.HTTPFound(f"{dash}/?error=token")

    if not user_id or bot is None or not _user_allowed(bot, user_id):
        print(f"oauth denied for discord user {user_id}", flush=True)
        raise web.HTTPFound(f"{dash}/?error=not_owner")

    token = secrets.token_urlsafe(32)
    _prune_sessions(request)
    _sessions(request)[token] = {
        "user_id": user_id,
        "username": username,
        "expires": time.time() + SESSION_DAYS * 24 * 3600,
    }
    print(f"oauth login: {username} ({user_id})", flush=True)
    raise web.HTTPFound(f"{dash}/?session={token}")


# ---------------- dashboard API ----------------

async def api_health(request):
    # Never 503s. Authed callers also get the guild list and, for
    # Discord logins, the linked Discord username.
    ok, why, sess = _authed(request)
    resp = {"ok": True, "dashboard_configured": _dashboard_configured()}
    if ok:
        bot = _bot(request)
        guilds = []
        if bot is not None:
            for gid in bot.store.setup_guilds():
                g = bot.get_guild(gid)
                if g:
                    guilds.append({"id": gid, "name": g.name,
                                   "members": g.member_count})
        resp["guilds"] = guilds
        if sess:
            resp["discord_user"] = {"id": sess["user_id"],
                                    "username": sess["username"]}
    elif why == "bad":
        return web.json_response({"error": "You are not logged in."},
                                 status=401)
    return web.json_response(resp)


@_guard
async def api_settings_get(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    g = _bot(request).store.guild(guild.id)
    return web.json_response({
        "guild": {"id": guild.id, "name": guild.name},
        "settings": g["settings"],
        "whitelist": g.get("nuke_whitelist", {"users": [], "roles": []}),
        "panic": bool(g.get("panic")),
    })


@_guard
async def api_settings_put(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "Body must be JSON."}, status=400)
    bot = _bot(request)
    g = bot.store.guild(guild.id)
    s = g["settings"]
    changed = []
    for key, value in (body.get("settings") or {}).items():
        if key not in DEFAULT_SETTINGS:
            continue  # unknown keys are ignored, never stored
        default = DEFAULT_SETTINGS[key]
        try:
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int):
                value = int(value)
            elif isinstance(default, list):
                if not isinstance(value, list):
                    continue
            elif isinstance(default, str):
                value = str(value)
                if key == "nuke_action" and value not in ("strip", "ban"):
                    continue
        except (ValueError, TypeError):
            continue
        s[key] = value
        changed.append(key)
    bot.store.save()
    return web.json_response({"ok": True, "changed": changed, "settings": s})


@_guard
async def api_logs(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    bot = _bot(request)
    try:
        limit = max(1, min(200, int(request.query.get("limit", "50"))))
    except ValueError:
        limit = 50
    buf = getattr(bot, "recent_logs", {}).get(guild.id, [])
    entries = list(buf)[-limit:]
    return web.json_response({"logs": entries})


@_guard
async def api_strikes_get(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    user_id = request.query.get("user_id", "")
    bot = _bot(request)
    g = bot.store.guild(guild.id)
    rec = g["strikes"].get(str(user_id), {"count": 0, "history": []})
    member = guild.get_member(int(user_id)) if user_id.isdigit() else None
    return web.json_response({
        "user_id": user_id,
        "name": str(member) if member else None,
        "count": rec["count"],
        "history": rec["history"][-20:],
    })


@_guard
async def api_strikes_clear(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "Body must be JSON."}, status=400)
    user_id = str(body.get("user_id", ""))
    bot = _bot(request)
    g = bot.store.guild(guild.id)
    g["strikes"].pop(user_id, None)
    bot.store.save()
    from utils import mod_log
    await mod_log(bot, guild,
                  f"STRIKES CLEARED — user id {user_id} — via dashboard")
    return web.json_response({"ok": True})


@_guard
async def api_backup(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    bot = _bot(request)
    cog = bot.get_cog("Backups")
    if cog is None:
        return web.json_response({"error": "Backup system is not loaded."}, status=500)
    snap = cog.build_snapshot(guild, by="dashboard")
    fname, _path = cog.save_snapshot(guild, snap)
    from utils import mod_log
    await mod_log(bot, guild, f"BACKUP — snapshot {fname} taken via dashboard.")
    # The snapshot rides along in the response so the dashboard can offer it
    # as a download — files on the bot's disk vanish on restart.
    return web.json_response({"ok": True, "filename": fname, "snapshot": snap})


@_guard
async def api_backups(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    bot = _bot(request)
    cog = bot.get_cog("Backups")
    items = cog.list_backups(guild.id) if cog else []
    return web.json_response({"backups": items,
                              "note": "Files stored on the bot disappear when it "
                                      "restarts. Download the file from a backup "
                                      "to keep it."})


@_guard
async def api_panic(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    mode = str(body.get("mode", "panic")).lower()
    full = mode == "lockdown"
    bot = _bot(request)
    cog = bot.get_cog("AntiNuke")
    if cog is None:
        return web.json_response({"error": "Anti-nuke is not loaded."}, status=500)
    msg = await cog.apply_panic(guild, full=full, by="dashboard")
    return web.json_response({"ok": True, "message": msg})


@_guard
async def api_unlock(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    bot = _bot(request)
    cog = bot.get_cog("AntiNuke")
    if cog is None:
        return web.json_response({"error": "Anti-nuke is not loaded."}, status=500)
    msg = await cog.release(guild, by="dashboard")
    return web.json_response({"ok": True, "message": msg})


@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
    return resp


async def start(port, bot=None):
    app = web.Application(middlewares=[cors_middleware])
    app["bot"] = bot
    app["sessions"] = {}
    app.router.add_get("/health", _health)
    app.router.add_get("/", _health)
    app.router.add_get("/api/oauth/start", oauth_start)
    app.router.add_get("/api/oauth/callback", oauth_callback)
    app.router.add_get("/api/health", api_health)
    app.router.add_get("/api/settings", api_settings_get)
    app.router.add_put("/api/settings", api_settings_put)
    app.router.add_get("/api/logs", api_logs)
    app.router.add_get("/api/strikes", api_strikes_get)
    app.router.add_post("/api/strikes/clear", api_strikes_clear)
    app.router.add_post("/api/backup", api_backup)
    app.router.add_get("/api/backups", api_backups)
    app.router.add_post("/api/panic", api_panic)
    app.router.add_post("/api/unlock", api_unlock)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"health endpoint listening on 0.0.0.0:{port}", flush=True)
    if not _dashboard_configured():
        print("Dashboard login is not configured — set DISCORD_CLIENT_SECRET "
              "in the Render environment to turn on 'Log in with Discord'.",
              flush=True)
    return runner
