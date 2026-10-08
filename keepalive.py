"""HTTP server: /health plus the dashboard JSON API (Phase 3).

Every /api/* endpoint requires the header
    Authorization: Bearer <DASHBOARD_KEY>
If DASHBOARD_KEY is not set on Render, the API answers 503 (disabled) and
only /health keeps working.

CORS is wide open (*) so the static dashboard on GitHub Pages can call it.
"""
import json
import os

from aiohttp import web

from config import DEFAULT_SETTINGS


def _api_key():
    return os.environ.get("DASHBOARD_KEY") or ""


def _authed(request):
    key = _api_key()
    if not key:
        return False, "disabled"
    return request.headers.get("Authorization", "") == f"Bearer {key}", "bad key"


def _guard(handler):
    async def wrapper(request):
        ok, why = _authed(request)
        if not ok:
            if why == "disabled":
                return web.json_response(
                    {"error": "Dashboard API is disabled. Set DASHBOARD_KEY in "
                              "the Render environment to turn it on."},
                    status=503)
            return web.json_response(
                {"error": "Wrong or missing dashboard key."}, status=401)
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


# ---------------- public ----------------

async def _health(request):
    return web.Response(text="ok")


# ---------------- dashboard API ----------------

@_guard
async def api_health(request):
    bot = _bot(request)
    guilds = []
    if bot is not None:
        for gid in bot.store.setup_guilds():
            g = bot.get_guild(gid)
            if g:
                guilds.append({"id": gid, "name": g.name,
                               "members": g.member_count})
    return web.json_response({"ok": True, "guilds": guilds})


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
    app.router.add_get("/health", _health)
    app.router.add_get("/", _health)
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
    if not _api_key():
        print("DASHBOARD_KEY is not set — dashboard API is disabled.", flush=True)
    return runner
