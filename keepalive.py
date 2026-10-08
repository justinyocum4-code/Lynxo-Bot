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

import discord

from config import DEFAULT_SETTINGS

# Public: the Discord application id for Lynxo Bot. Safe to ship in code.
DISCORD_CLIENT_ID_FALLBACK = "1557495025921949839"
SESSION_DAYS = 7

# Sane min/max for integer settings set from the dashboard.
_INT_BOUNDS = {
    "new_account_age_days": (1, 90),
    "copypasta_count": (2, 20),
    "copypasta_window": (5, 300),
    "mass_mention_count": (2, 50),
    "heat_slowmode_seconds": (1, 21600),
    "starboard_threshold": (1, 50),
}

# Settings that hold a channel id: None/"" means "unset", otherwise the
# value must be a real text channel in the guild.
_CHANNEL_ID_KEYS = (
    "auto_backup_channel_id",
    "welcome_channel_id",
    "goodbye_channel_id",
    "levelup_channel_id",
    "starboard_channel_id",
    "suggest_channel_id",
)

# Max characters for free-text settings.
_TEXT_CAPS = {
    "welcome_message": 1000,
    "goodbye_message": 1000,
}


def _validate_channel_id(guild, value):
    """None/"" -> None; otherwise must be a real text channel.

    Returns (ok, cleaned_value)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return True, None
    try:
        cid = int(value)
    except (ValueError, TypeError):
        return False, None
    ch = guild.get_channel(cid)
    if not isinstance(ch, discord.TextChannel):
        return False, None
    return True, cid


def _validate_dict(key, value, guild):
    """Validate dict settings from the dashboard.

    Returns (ok, cleaned_dict). Unknown dict keys are rejected."""
    if not isinstance(value, dict):
        return False, None
    if key == "custom_commands":
        cleaned = {}
        for k, v in value.items():
            k = str(k).strip().lower()
            if not k or len(k) > 32 or " " in k:
                continue
            v = str(v)
            if not v or len(v) > 1500:
                continue
            cleaned[k] = v
        return True, cleaned
    if key == "autoresponders":
        cleaned = {}
        for k, v in value.items():
            k = str(k).strip().lower()
            if not k or len(k) > 100:
                continue
            v = str(v)
            if not v or len(v) > 1500:
                continue
            cleaned[k] = v
        return True, cleaned
    if key == "level_roles":
        cleaned = {}
        for k, v in value.items():
            try:
                level = int(k)
            except (ValueError, TypeError):
                continue
            if not 1 <= level <= 100:
                continue
            try:
                rid = int(v)
            except (ValueError, TypeError):
                continue
            if guild.get_role(rid) is None:
                continue
            cleaned[str(level)] = rid
        return True, cleaned
    return False, None


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
        # Channel ids: None/empty means "unset"; otherwise the value must
        # be a real text channel in this guild.
        if key in _CHANNEL_ID_KEYS:
            ok, value = _validate_channel_id(guild, value)
            if not ok:
                continue
            s[key] = value
            changed.append(key)
            continue
        # Slowmode heat threshold: None/empty means "automatic";
        # otherwise a number in range.
        if key == "heat_slowmode_threshold":
            if value is None or (isinstance(value, str) and not value.strip()):
                value = None
            else:
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    continue
                if not 2 <= value <= 50:
                    continue
            s[key] = value
            changed.append(key)
            continue
        default = DEFAULT_SETTINGS[key]
        try:
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int):
                value = int(value)
                if key in _INT_BOUNDS:
                    lo, hi = _INT_BOUNDS[key]
                    value = max(lo, min(hi, value))
            elif isinstance(default, dict):
                ok, value = _validate_dict(key, value, guild)
                if not ok:
                    continue
            elif isinstance(default, list):
                if not isinstance(value, list):
                    continue
            elif isinstance(default, str):
                value = str(value)
                if key == "nuke_action" and value not in ("strip", "ban"):
                    continue
                if key == "new_account_action" and value not in ("flag", "quarantine"):
                    continue
                if key in _TEXT_CAPS:
                    value = value[:_TEXT_CAPS[key]]
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


# ---------------- reaction roles ----------------

def _parse_rr_color(value):
    """Accept '#FFD700', 'FFD700', or an int. Returns int or None."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value if 0 <= value <= 0xFFFFFF else None
    s = str(value or "").strip().lstrip("#")
    if len(s) == 6:
        try:
            return int(s, 16)
        except ValueError:
            return None
    return None


@_guard
async def api_channels(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    channels = sorted(
        (c for c in guild.channels if c.type.name == "text"),
        key=lambda c: c.position,
    )
    return web.json_response({"channels": [
        {"id": str(c.id), "name": c.name} for c in channels]})


@_guard
async def api_roles(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    roles = [r for r in guild.roles
             if not r.is_default() and not r.managed]
    roles.sort(key=lambda r: r.name.lower())
    return web.json_response({"roles": [
        {"id": str(r.id), "name": r.name,
         "color": ("#%06X" % r.color.value) if r.color.value else None,
         "position": r.position} for r in roles]})


@_guard
async def api_mycolor(request):
    """Plain-words name-color diagnostic for the logged-in Discord user."""
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    sess = request.get("dash_session")
    if not sess or not sess.get("user_id"):
        return web.json_response(
            {"error": "Log in with Discord first so I know whose name to check."},
            status=401)
    try:
        user_id = int(sess["user_id"])
    except (ValueError, TypeError):
        return web.json_response(
            {"error": "Your login session looks broken. Log in with Discord again."},
            status=401)
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except Exception:  # noqa: BLE001
            member = None
    if member is None:
        return web.json_response(
            {"error": "I could not find you in the server. "
                      "Make sure you are a member of it."},
            status=404)
    roles = sorted(member.roles, key=lambda r: r.position, reverse=True)
    out = []
    display_color = None
    display_role = None
    for r in roles:
        color_hex = ("#%06X" % r.color.value) if r.color.value else None
        out.append({"name": r.name, "color": color_hex, "position": r.position})
        if display_color is None and color_hex:
            display_color = color_hex
            display_role = r.name
    return web.json_response({
        "roles": out,
        "display_color": display_color,
        "display_role": display_role,
    })


def _rr_embed_view(guild, embed):
    mappings = []
    for m in embed.get("mappings", []) or []:
        role = guild.get_role(int(m.get("role_id", 0)))
        mappings.append({
            "emoji": str(m.get("emoji", "")),
            "role_id": str(m.get("role_id", "")),
            "label": str(m.get("label", "")),
            "role_name": role.name if role else None,
        })
    return {
        "id": str(embed.get("id") or ""),
        "name": str(embed.get("name") or "Reaction Roles"),
        "channel_id": str(embed.get("channel_id") or ""),
        "message_id": str(embed.get("message_id") or ""),
        "title": str(embed.get("title") or ""),
        "description": str(embed.get("description") or ""),
        "color": "#%06X" % (int(embed.get("color") or 16766720) & 0xFFFFFF),
        "image_url": str(embed.get("image_url") or ""),
        "mappings": mappings,
    }


def _rr_config_view(guild, cfg):
    return {"embeds": [_rr_embed_view(guild, e)
                       for e in cfg.get("embeds", [])]}


@_guard
async def api_reaction_roles_get(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    bot = _bot(request)
    cog = bot.get_cog("ReactionRoles")
    cfg = cog.touch(guild) if cog else {"embeds": []}
    return web.json_response(_rr_config_view(guild, cfg))


@_guard
async def api_reaction_roles_post(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "Body must be JSON."}, status=400)
    bot = _bot(request)
    cog = bot.get_cog("ReactionRoles")
    if cog is None:
        return web.json_response(
            {"error": "Reaction roles are not loaded."}, status=500)
    cfg = cog.touch(guild)

    embed_id = str(body.get("id") or "").strip()
    embed = cog._get_embed(cfg, embed_id) if embed_id else None
    if embed is None:
        embed = {
            "id": cog.new_embed_id(),
            "name": "Reaction Roles",
            "channel_id": None,
            "message_id": None,
            "title": "Pick your roles",
            "description": "",
            "color": 16766720,
            "image_url": "",
            "mappings": [],
        }
        cfg["embeds"].append(embed)

    channel_id = str(body.get("channel_id") or "").strip()
    channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
    if channel is None or channel.type.name != "text":
        return web.json_response({"error": "Pick a text channel."}, status=400)

    name = str(body.get("name") or "Reaction Roles").strip()[:100]
    title = str(body.get("title") or "Pick your roles").strip()[:256]
    description = str(body.get("description") or "").strip()[:2000]
    color = _parse_rr_color(body.get("color"))
    if color is None:
        color = 16766720  # gold
    image_url = str(body.get("image_url") or "").strip()[:2000]
    if image_url and not image_url.lower().startswith(("http://", "https://")):
        return web.json_response(
            {"error": "Image URL must start with http:// or https://."},
            status=400)

    raw_mappings = body.get("mappings") or []
    if not isinstance(raw_mappings, list) or len(raw_mappings) > 20:
        return web.json_response(
            {"error": "Mappings must be a list of at most 20."}, status=400)
    mappings = []
    seen_emoji = set()
    for m in raw_mappings:
        if not isinstance(m, dict):
            continue
        emoji = str(m.get("emoji", "")).strip()
        role_id = str(m.get("role_id", "")).strip()
        label = str(m.get("label", "")).strip()[:100]
        if not emoji or not role_id.isdigit() or emoji in seen_emoji:
            continue
        role = guild.get_role(int(role_id))
        if role is None or role.is_default() or role.managed:
            continue
        if not label:
            label = role.name
        seen_emoji.add(emoji)
        mappings.append({"emoji": emoji, "role_id": int(role_id),
                         "label": label})

    embed.update({
        "name": name,
        "channel_id": channel.id,
        "title": title,
        "description": description,
        "color": color,
        "image_url": image_url,
        "mappings": mappings,
    })
    bot.store.save()
    return web.json_response({"ok": True,
                              "embed": _rr_embed_view(guild, embed)})


@_guard
async def api_reaction_roles_publish(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    bot = _bot(request)
    cog = bot.get_cog("ReactionRoles")
    if cog is None:
        return web.json_response(
            {"error": "Reaction roles are not loaded."}, status=500)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    embed_id = str(body.get("id") or "").strip()
    if not embed_id:
        return web.json_response({"error": "Missing embed id."}, status=400)
    try:
        message = await cog.post_reaction_message(guild, embed_id)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"error": f"Could not post: {e}"},
                                 status=500)
    return web.json_response({"ok": True, "message_id": str(message.id)})


@_guard
async def api_reaction_roles_delete(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    bot = _bot(request)
    cog = bot.get_cog("ReactionRoles")
    if cog is None:
        return web.json_response(
            {"error": "Reaction roles are not loaded."}, status=500)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    embed_id = str(body.get("id") or "").strip()
    if not embed_id:
        return web.json_response({"error": "Missing embed id."}, status=400)
    deleted = await cog.delete_reaction_message(guild, embed_id)
    return web.json_response({"ok": True, "deleted": deleted})


@_guard
async def api_reaction_roles_find(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    bot = _bot(request)
    cog = bot.get_cog("ReactionRoles")
    if cog is None:
        return web.json_response(
            {"error": "Reaction roles are not loaded."}, status=500)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    raw = str(body.get("message_id") or "").strip()
    # Accept a full message link too — the ID is the last number in it.
    import re
    nums = re.findall(r"\d+", raw)
    if not nums:
        return web.json_response({"error": "No message ID found."}, status=400)
    mid = int(nums[-1])
    cfg = cog.touch(guild)
    for embed in cfg.get("embeds", []):
        if embed.get("message_id") and int(embed["message_id"]) == mid:
            return web.json_response({"ok": True,
                                      "embed": _rr_embed_view(guild, embed)})
    return web.json_response({"error": "No reaction-role embed with that "
                                       "message ID."}, status=404)


@_guard
async def api_reaction_roles_remove(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    bot = _bot(request)
    cog = bot.get_cog("ReactionRoles")
    if cog is None:
        return web.json_response(
            {"error": "Reaction roles are not loaded."}, status=500)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    embed_id = str(body.get("id") or "").strip()
    if not embed_id:
        return web.json_response({"error": "Missing embed id."}, status=400)
    # Delete the posted message first, then remove the config.
    await cog.delete_reaction_message(guild, embed_id)
    removed = cog.remove_embed(guild, embed_id)
    return web.json_response({"ok": True, "removed": removed})


def _ticket_panel_view(guild, cfg):
    return {
        "enabled": bool(cfg.get("enabled")),
        "channel_id": str(cfg.get("channel_id") or ""),
        "message_id": str(cfg.get("message_id") or ""),
        "title": str(cfg.get("title") or ""),
        "description": str(cfg.get("description") or ""),
        "color": "#%06X" % (int(cfg.get("color") or 16766720) & 0xFFFFFF),
        "image_url": str(cfg.get("image_url") or ""),
        "button_text": str(cfg.get("button_text") or ""),
        "button_emoji": str(cfg.get("button_emoji") or ""),
        "button_style": str(cfg.get("button_style") or "primary"),
        "welcome": str(cfg.get("welcome") or ""),
        "support_role_id": str(cfg.get("support_role_id") or ""),
    }


@_guard
async def api_tickets_get(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    bot = _bot(request)
    cog = bot.get_cog("Tickets")
    if cog is None:
        return web.json_response({"error": "Tickets not loaded."}, status=500)
    cfg = cog._config(guild)
    return web.json_response({
        "panel": _ticket_panel_view(guild, cfg),
        "open_tickets": cog.open_tickets(guild),
    })


@_guard
async def api_tickets_save(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "Body must be JSON."}, status=400)
    bot = _bot(request)
    cog = bot.get_cog("Tickets")
    if cog is None:
        return web.json_response({"error": "Tickets not loaded."}, status=500)
    cfg = cog._config(guild)
    cfg["enabled"] = bool(body.get("enabled"))
    ch_id = str(body.get("channel_id") or "").strip()
    ch = guild.get_channel(int(ch_id)) if ch_id.isdigit() else None
    if ch_id and (ch is None or ch.type.name != "text"):
        return web.json_response({"error": "Pick a text channel."}, status=400)
    cfg["channel_id"] = ch.id if ch else None
    cfg["title"] = str(body.get("title") or "Need help?").strip()[:256]
    cfg["description"] = str(body.get("description") or "").strip()[:2000]
    color = _parse_rr_color(body.get("color"))
    cfg["color"] = color if color is not None else 16766720
    image_url = str(body.get("image_url") or "").strip()[:2000]
    if image_url and not image_url.lower().startswith(("http://", "https://")):
        return web.json_response(
            {"error": "Image URL must start with http:// or https://."},
            status=400)
    cfg["image_url"] = image_url
    cfg["button_text"] = str(body.get("button_text") or "Open a Ticket").strip()[:80]
    cfg["button_emoji"] = str(body.get("button_emoji") or "").strip()[:32]
    style = str(body.get("button_style") or "primary").strip()
    if style not in ("primary", "secondary", "success", "danger"):
        style = "primary"
    cfg["button_style"] = style
    cfg["welcome"] = str(body.get("welcome") or "").strip()[:1000]
    sr_id = str(body.get("support_role_id") or "").strip()
    sr = guild.get_role(int(sr_id)) if sr_id.isdigit() else None
    cfg["support_role_id"] = sr.id if sr else None
    bot.store.save()
    return web.json_response({"ok": True, "panel": _ticket_panel_view(guild, cfg)})


@_guard
async def api_tickets_post(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    bot = _bot(request)
    cog = bot.get_cog("Tickets")
    if cog is None:
        return web.json_response({"error": "Tickets not loaded."}, status=500)
    try:
        message = await cog.post_panel(guild)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"error": f"Could not post: {e}"}, status=500)
    return web.json_response({"ok": True, "message_id": str(message.id)})


@_guard
async def api_tickets_close(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    bot = _bot(request)
    cog = bot.get_cog("Tickets")
    if cog is None:
        return web.json_response({"error": "Tickets not loaded."}, status=500)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    tid = str(body.get("thread_id") or "").strip()
    if not tid.isdigit():
        return web.json_response({"error": "Missing thread id."}, status=400)
    closed = await cog.close_thread(guild, int(tid))
    return web.json_response({"ok": True, "closed": closed})


def _achievements_view(guild, cfg):
    defs = []
    for d in cfg.get("defs", []):
        defs.append({
            "id": str(d.get("id") or ""),
            "name": str(d.get("name") or ""),
            "description": str(d.get("description") or ""),
            "emoji": str(d.get("emoji") or "🏆"),
            "type": str(d.get("type") or "messages"),
            "threshold": int(d.get("threshold") or 0),
        })
    return {
        "enabled": bool(cfg.get("enabled")),
        "announce_channel_id": str(cfg.get("announce_channel_id") or ""),
        "defs": defs,
    }


@_guard
async def api_achievements_get(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    bot = _bot(request)
    cog = bot.get_cog("Achievements")
    if cog is None:
        return web.json_response({"error": "Achievements not loaded."}, status=500)
    cfg = cog._config(guild)
    return web.json_response(_achievements_view(guild, cfg))


@_guard
async def api_achievements_save(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "Body must be JSON."}, status=400)
    bot = _bot(request)
    cog = bot.get_cog("Achievements")
    if cog is None:
        return web.json_response({"error": "Achievements not loaded."}, status=500)
    cfg = cog._config(guild)
    cfg["enabled"] = bool(body.get("enabled"))
    ch_id = str(body.get("announce_channel_id") or "").strip()
    ch = guild.get_channel(int(ch_id)) if ch_id.isdigit() else None
    cfg["announce_channel_id"] = ch.id if ch else None
    raw_defs = body.get("defs") or []
    if not isinstance(raw_defs, list) or len(raw_defs) > 30:
        return web.json_response(
            {"error": "defs must be a list of at most 30."}, status=400)
    defs = []
    seen_ids = set()
    import uuid as _uuid
    for d in raw_defs:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name") or "").strip()[:100]
        if not name:
            continue
        ach_type = str(d.get("type") or "messages").strip()
        if ach_type not in ("messages", "level", "days", "roles"):
            ach_type = "messages"
        try:
            threshold = int(d.get("threshold") or 0)
        except (TypeError, ValueError):
            threshold = 0
        if threshold <= 0 or threshold > 100000:
            continue
        aid = str(d.get("id") or "").strip()
        if not aid or aid in seen_ids:
            aid = str(_uuid.uuid4())
        seen_ids.add(aid)
        defs.append({
            "id": aid,
            "name": name,
            "description": str(d.get("description") or "").strip()[:500],
            "emoji": str(d.get("emoji") or "🏆").strip()[:32],
            "type": ach_type,
            "threshold": threshold,
        })
    cfg["defs"] = defs
    # Drop earned records for deleted achievements.
    g = bot.store.guild(guild.id)
    earned = g.get("ach_earned", {})
    for uid in list(earned.keys()):
        earned[uid] = [a for a in earned[uid] if a in seen_ids]
    bot.store.save()
    return web.json_response({"ok": True,
                              "achievements": _achievements_view(guild, cfg)})


def _releases_view(cfg):
    recent = cfg.get("announced") or []
    recent = recent[-10:]
    return {
        "enabled": bool(cfg.get("enabled")),
        "channel_id": (str(cfg["channel_id"])
                       if cfg.get("channel_id") else None),
        "last_check": cfg.get("last_check"),
        "recent": [{"title": a.get("title", ""),
                    "artist": a.get("artist", "")} for a in recent],
    }


@_guard
async def api_releases_get(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    cfg = _bot(request).store.guild(guild.id)["releases"]
    return web.json_response(_releases_view(cfg))


@_guard
async def api_releases_post(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "Bad request."}, status=400)
    channel_id = body.get("channel_id")
    channel = None
    if channel_id:
        try:
            channel = guild.get_channel(int(channel_id))
        except (ValueError, TypeError):
            channel = None
        if channel is None or channel.type.name != "text":
            return web.json_response(
                {"error": "Pick a real text channel."}, status=400)
    cfg = _bot(request).store.guild(guild.id)["releases"]
    cfg["enabled"] = bool(body.get("enabled"))
    cfg["channel_id"] = int(channel_id) if channel_id else None
    _bot(request).store.save()
    return web.json_response({"ok": True})


@_guard
async def api_releases_check(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    bot = _bot(request)
    cog = bot.get_cog("ReleaseAlerts")
    if cog is None:
        return web.json_response(
            {"error": "Release alerts are not loaded."}, status=500)
    try:
        posted = await cog.run_check(guild)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"error": f"Could not check: {e}"},
                                 status=500)
    return web.json_response({"ok": True, "posted": posted})


# ---------- plain-English server builder ----------

_BUILDER_COLORS = {
    "red": 0xFF0000, "blue": 0x0000FF, "green": 0x00FF00, "gold": 0xFFD700,
    "purple": 0x800080, "orange": 0xFFA500, "pink": 0xFFC0CB, "teal": 0x008080,
    "white": 0xFFFFFF, "black": 0x000000,
}
_BUILDER_PERMS = {
    "view": "view_channel", "send": "send_messages", "speak": "speak",
    "connect": "connect", "react": "add_reactions",
}
_BUILDER_ACTIONS = {
    "create_channel", "delete_channel", "create_role", "delete_role",
    "set_role_color", "set_channel_perms",
    "rename_channel", "rename_role", "move_channel",
    "config_leveling", "add_level_reward", "remove_level_reward",
    "config_achievements", "add_achievement", "remove_achievement",
}


def _builder_norm(name):
    """Lowercase, no emoji/symbols — for forgiving name matching."""
    import re
    name = (name or "").strip().lstrip("#").lower()
    # Keep letters, numbers, hyphens, underscores, spaces.
    return re.sub(r"[^a-z0-9\-_ ]", "", name).strip()


def _builder_find_channel(guild, name):
    name = (name or "").strip().lstrip("#").lower()
    if not name:
        return None
    for ch in guild.channels:
        if ch.name.lower() == name:
            return ch
    # Fallback: match ignoring emojis/symbols (AI sometimes drops them).
    norm = _builder_norm(name)
    if norm:
        for ch in guild.channels:
            if _builder_norm(ch.name) == norm:
                return ch
    return None


def _builder_find_role(guild, name):
    name = (name or "").strip().lower()
    if not name:
        return None
    for r in guild.roles:
        if r.name.lower() == name:
            return r
    # Fallback: match ignoring emojis/symbols.
    norm = _builder_norm(name)
    if norm:
        for r in guild.roles:
            if _builder_norm(r.name) == norm:
                return r
    return None


def _builder_protected_ids(bot, guild):
    """Channel/role ids the builder must never touch (from /setup)."""
    g = bot.store.guild(guild.id)
    ids = set()
    for cid in (g.get("channels") or {}).values():
        try:
            ids.add(int(cid))
        except (ValueError, TypeError):
            pass
    for rid in (g.get("roles") or {}).values():
        try:
            ids.add(int(rid))
        except (ValueError, TypeError):
            pass
    return ids


def _builder_color(value):
    v = (value or "").strip().lower()
    if v in _BUILDER_COLORS:
        return discord.Colour(_BUILDER_COLORS[v])
    if v.startswith("#") and len(v) == 7:
        try:
            return discord.Colour(int(v[1:], 16))
        except ValueError:
            return None
    return None


def _builder_role_locked(guild, role, protected_ids):
    """Plain-words reason a role can't be touched, or None if it's fine."""
    if role is None:
        return "I couldn't find that role."
    if role.id in protected_ids:
        return (f"Role {role.name} is part of the bot's setup — "
                "I won't touch it.")
    if role.is_default():
        return "I can't change the @everyone role."
    if role.managed:
        return (f"Role {role.name} is managed by an integration — "
                "I can't change it.")
    me = guild.me
    if me and role >= me.top_role:
        return (f"Role {role.name} sits above the bot's own role — "
                "I can't reach it.")
    return None


async def _builder_run(bot, guild, actions):
    """Execute validated builder actions. Returns (done, skipped) strings."""
    from utils import mod_log
    done, skipped = [], []
    protected_ids = _builder_protected_ids(bot, guild)
    for raw in actions[:30]:
        if not isinstance(raw, dict):
            continue
        act = raw.get("action")
        if act not in _BUILDER_ACTIONS:
            continue
        try:
            if act == "create_channel":
                name = str(raw.get("name", "")).strip().lstrip("#")
                ctype = str(raw.get("type", "text")).strip().lower()
                if not name or ctype not in ("text", "voice", "category"):
                    skipped.append("Skipped making a channel: bad name or type.")
                    continue
                if _builder_find_channel(guild, name):
                    skipped.append(f"Channel #{name} already exists.")
                    continue
                cat = None
                cat_name = str(raw.get("category", "") or "").strip()
                if cat_name and ctype in ("text", "voice"):
                    for c in guild.categories:
                        if c.name.lower() == cat_name.lower():
                            cat = c
                            break
                if ctype == "voice":
                    ch = await guild.create_voice_channel(
                        name, category=cat, reason="Dashboard server builder")
                elif ctype == "category":
                    ch = await guild.create_category_channel(
                        name, reason="Dashboard server builder")
                else:
                    ch = await guild.create_text_channel(
                        name, category=cat, reason="Dashboard server builder")
                done.append(f"Created {ctype} channel #{ch.name}.")
                await mod_log(bot, guild,
                              f"BUILDER — created {ctype} channel #{ch.name}.")
            elif act == "delete_channel":
                ch = _builder_find_channel(guild, raw.get("name"))
                if ch is None:
                    skipped.append(
                        f"Could not find channel '{raw.get('name')}'.")
                    continue
                if ch.id in protected_ids:
                    skipped.append(
                        f"Channel #{ch.name} is part of the bot's setup — "
                        "left alone.")
                    continue
                cname = ch.name
                await ch.delete(reason="Dashboard server builder")
                done.append(f"Deleted channel #{cname}.")
                await mod_log(bot, guild,
                              f"BUILDER — deleted channel #{cname}.")
            elif act == "create_role":
                name = str(raw.get("name", "")).strip()
                if not name:
                    skipped.append("Skipped making a role: no name given.")
                    continue
                if _builder_find_role(guild, name):
                    skipped.append(f"Role '{name}' already exists.")
                    continue
                color = _builder_color(raw.get("color"))
                role = await guild.create_role(
                    name=name,
                    colour=color or discord.Colour.default(),
                    reason="Dashboard server builder")
                if raw.get("color") and color is None:
                    done.append(
                        f"Created role {role.name}, but I didn't recognize "
                        f"the color '{raw.get('color')}' so it has no color.")
                else:
                    done.append(f"Created role {role.name}.")
                await mod_log(bot, guild,
                              f"BUILDER — created role {role.name}.")
            elif act == "delete_role":
                role = _builder_find_role(guild, raw.get("name"))
                locked = _builder_role_locked(guild, role, protected_ids)
                if locked:
                    skipped.append(locked)
                    continue
                rname = role.name
                await role.delete(reason="Dashboard server builder")
                done.append(f"Deleted role {rname}.")
                await mod_log(bot, guild,
                              f"BUILDER — deleted role {rname}.")
            elif act == "set_role_color":
                role = _builder_find_role(guild, raw.get("name"))
                locked = _builder_role_locked(guild, role, protected_ids)
                if locked:
                    skipped.append(locked)
                    continue
                color = _builder_color(raw.get("color"))
                if color is None:
                    skipped.append(
                        f"Didn't recognize the color '{raw.get('color')}'.")
                    continue
                await role.edit(colour=color,
                                reason="Dashboard server builder")
                done.append(f"Set {role.name} to {raw.get('color')}.")
                await mod_log(bot, guild,
                              f"BUILDER — set role {role.name} color to "
                              f"{raw.get('color')}.")
            elif act == "set_channel_perms":
                ch = _builder_find_channel(guild, raw.get("channel"))
                role = _builder_find_role(guild, raw.get("role"))
                if ch is None:
                    skipped.append(
                        f"Could not find channel '{raw.get('channel')}'.")
                    continue
                if role is None:
                    skipped.append(
                        f"Could not find role '{raw.get('role')}'.")
                    continue
                if ch.id in protected_ids:
                    skipped.append(
                        f"Channel #{ch.name} is part of the bot's setup — "
                        "left alone.")
                    continue
                allow = [w for w in (raw.get("allow") or [])
                         if w in _BUILDER_PERMS]
                deny = [w for w in (raw.get("deny") or [])
                        if w in _BUILDER_PERMS]
                if not allow and not deny:
                    skipped.append(
                        f"No valid permissions given for #{ch.name}.")
                    continue
                ow = ch.overwrites_for(role)
                for w in allow:
                    setattr(ow, _BUILDER_PERMS[w], True)
                for w in deny:
                    setattr(ow, _BUILDER_PERMS[w], False)
                # You can't use a channel you can't see.
                if (any(w in allow for w in ("send", "speak", "connect", "react"))
                        and "view" not in deny):
                    ow.view_channel = True
                await ch.set_permissions(
                    role, overwrite=ow, reason="Dashboard server builder")
                done.append(
                    f"Updated what {role.name} can do in #{ch.name}.")
                await mod_log(bot, guild,
                              f"BUILDER — set {role.name} permissions in "
                              f"#{ch.name}.")
            elif act == "rename_channel":
                ch = _builder_find_channel(guild, raw.get("name"))
                new_name = str(raw.get("new_name", "")).strip().lstrip("#")
                if ch is None:
                    skipped.append(
                        f"Could not find channel '{raw.get('name')}'.")
                    continue
                if not new_name:
                    skipped.append("No new name given for the rename.")
                    continue
                old = ch.name
                await ch.edit(name=new_name, reason="Dashboard server builder")
                done.append(f"Renamed #{old} to #{ch.name}.")
                await mod_log(bot, guild,
                              f"BUILDER — renamed channel #{old} to #{ch.name}.")
            elif act == "rename_role":
                role = _builder_find_role(guild, raw.get("name"))
                new_name = str(raw.get("new_name", "")).strip()
                if role is None:
                    skipped.append(
                        f"Could not find role '{raw.get('name')}'.")
                    continue
                if not new_name:
                    skipped.append("No new name given for the rename.")
                    continue
                if role.is_default() or role.managed:
                    skipped.append(
                        f"Role {role.name} can't be renamed.")
                    continue
                old = role.name
                await role.edit(name=new_name, reason="Dashboard server builder")
                done.append(f"Renamed role {old} to {role.name}.")
                await mod_log(bot, guild,
                              f"BUILDER — renamed role {old} to {role.name}.")
            elif act == "move_channel":
                ch = _builder_find_channel(guild, raw.get("name"))
                cat_name = str(raw.get("category", "") or "").strip()
                if ch is None:
                    skipped.append(
                        f"Could not find channel '{raw.get('name')}'.")
                    continue
                cat = None
                if cat_name:
                    for c in guild.categories:
                        if c.name.lower() == cat_name.lower():
                            cat = c
                            break
                    if cat is None:
                        skipped.append(
                            f"Could not find category '{cat_name}'.")
                        continue
                await ch.edit(category=cat, reason="Dashboard server builder")
                where = f"into {cat.name}" if cat else "out of its category"
                done.append(f"Moved #{ch.name} {where}.")
                await mod_log(bot, guild,
                              f"BUILDER — moved #{ch.name} {where}.")
            elif act == "config_leveling":
                g = bot.store.guild(guild.id)
                s = g["settings"]
                if "enabled" in raw:
                    s["leveling_enabled"] = bool(raw["enabled"])
                lch_name = str(raw.get("levelup_channel", "") or "").strip()
                if lch_name:
                    lch = _builder_find_channel(guild, lch_name)
                    if lch is None:
                        skipped.append(
                            f"Could not find channel '{lch_name}' for "
                            "level-up messages.")
                        continue
                    s["levelup_channel_id"] = lch.id
                bot.store.save()
                done.append("Updated the leveling settings.")
                await mod_log(bot, guild, "BUILDER — updated leveling settings.")
            elif act == "add_level_reward":
                try:
                    level = int(raw.get("level", 0))
                except (TypeError, ValueError):
                    level = 0
                role = _builder_find_role(guild, raw.get("role"))
                if not (1 <= level <= 100):
                    skipped.append("Level must be a number from 1 to 100.")
                    continue
                if role is None:
                    skipped.append(
                        f"Could not find role '{raw.get('role')}'.")
                    continue
                g = bot.store.guild(guild.id)
                lr = g["settings"].setdefault("level_roles", {})
                lr[str(level)] = role.id
                bot.store.save()
                done.append(
                    f"Level {level} now grants the {role.name} role.")
                await mod_log(bot, guild,
                              f"BUILDER — level {level} reward set to "
                              f"{role.name}.")
            elif act == "remove_level_reward":
                try:
                    level = int(raw.get("level", 0))
                except (TypeError, ValueError):
                    level = 0
                g = bot.store.guild(guild.id)
                lr = g["settings"].setdefault("level_roles", {})
                if str(level) in lr:
                    del lr[str(level)]
                    bot.store.save()
                    done.append(f"Removed the level {level} reward.")
                    await mod_log(bot, guild,
                                  f"BUILDER — removed level {level} reward.")
                else:
                    skipped.append(f"No reward set for level {level}.")
            elif act == "config_achievements":
                g = bot.store.guild(guild.id)
                cfg = g.setdefault("achievements", {})
                cfg.setdefault("defs", [])
                if "enabled" in raw:
                    cfg["enabled"] = bool(raw["enabled"])
                ach_name = str(raw.get("announce_channel", "") or "").strip()
                if ach_name:
                    ach_ch = _builder_find_channel(guild, ach_name)
                    if ach_ch is None:
                        skipped.append(
                            f"Could not find channel '{ach_name}' for "
                            "achievement announcements.")
                        continue
                    cfg["announce_channel_id"] = ach_ch.id
                bot.store.save()
                done.append("Updated the achievement settings.")
                await mod_log(bot, guild,
                              "BUILDER — updated achievement settings.")
            elif act == "add_achievement":
                import uuid as _uuid
                name = str(raw.get("name") or "").strip()[:100]
                if not name:
                    skipped.append("Achievement needs a name.")
                    continue
                ach_type = str(raw.get("type") or "messages").strip().lower()
                if ach_type not in ("messages", "level", "days", "roles"):
                    ach_type = "messages"
                try:
                    threshold = int(raw.get("threshold", 0))
                except (TypeError, ValueError):
                    threshold = 0
                if threshold <= 0 or threshold > 100000:
                    skipped.append(
                        f"Achievement '{name}' needs a threshold from 1 to "
                        "100000.")
                    continue
                g = bot.store.guild(guild.id)
                cfg = g.setdefault("achievements", {})
                defs = cfg.setdefault("defs", [])
                # Replace same-named achievement instead of duplicating.
                defs = [d for d in defs
                        if str(d.get("name", "")).lower() != name.lower()]
                defs.append({
                    "id": str(_uuid.uuid4()),
                    "name": name,
                    "description": str(raw.get("description") or "").strip()[:500],
                    "emoji": str(raw.get("emoji") or "🏆").strip()[:32],
                    "type": ach_type,
                    "threshold": threshold,
                })
                cfg["defs"] = defs
                bot.store.save()
                done.append(f"Created the '{name}' achievement.")
                await mod_log(bot, guild,
                              f"BUILDER — created achievement '{name}'.")
            elif act == "remove_achievement":
                name = str(raw.get("name") or "").strip().lower()
                if not name:
                    skipped.append("Achievement needs a name to remove.")
                    continue
                g = bot.store.guild(guild.id)
                cfg = g.setdefault("achievements", {})
                defs = cfg.get("defs", [])
                kept = [d for d in defs
                        if str(d.get("name", "")).lower() != name]
                if len(kept) == len(defs):
                    skipped.append(
                        f"Could not find achievement '{raw.get('name')}'.")
                    continue
                cfg["defs"] = kept
                bot.store.save()
                done.append(f"Removed the '{raw.get('name')}' achievement.")
                await mod_log(bot, guild,
                              f"BUILDER — removed achievement "
                              f"'{raw.get('name')}'.")
        except discord.Forbidden:
            skipped.append("Discord wouldn't let me do one change "
                           "(missing permission).")
        except discord.HTTPException as e:  # noqa: BLE001
            skipped.append(f"One change failed: {e}")
    return done, skipped


@_guard
async def api_editserver(request):
    guild = _guild(request)
    if guild is None:
        return web.json_response({"error": "No server found."}, status=404)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "Bad request."}, status=400)
    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        return web.json_response(
            {"error": "Type what you want changed first."}, status=400)
    if len(prompt) > 1000:
        return web.json_response(
            {"error": "Keep it under 1000 characters."}, status=400)
    from ai import ai_plan_server_edit
    actions = await ai_plan_server_edit(prompt)
    if actions is None:
        return web.json_response(
            {"error": "The AI helper didn't answer. Try again in a bit."},
            status=502)
    if not actions:
        return web.json_response(
            {"error": "I couldn't understand that — try simpler wording like "
                      "'add a text channel called X'."},
            status=400)
    done, skipped = await _builder_run(_bot(request), guild, actions)
    return web.json_response({"ok": True, "done": done, "skipped": skipped})


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
    app.router.add_get("/api/channels", api_channels)
    app.router.add_get("/api/roles", api_roles)
    app.router.add_get("/api/mycolor", api_mycolor)
    app.router.add_get("/api/reaction-roles", api_reaction_roles_get)
    app.router.add_post("/api/reaction-roles", api_reaction_roles_post)
    app.router.add_post("/api/reaction-roles/post", api_reaction_roles_publish)
    app.router.add_post("/api/reaction-roles/delete", api_reaction_roles_delete)
    app.router.add_post("/api/reaction-roles/remove", api_reaction_roles_remove)
    app.router.add_get("/api/tickets", api_tickets_get)
    app.router.add_post("/api/tickets", api_tickets_save)
    app.router.add_post("/api/tickets/post", api_tickets_post)
    app.router.add_post("/api/tickets/close", api_tickets_close)
    app.router.add_get("/api/achievements", api_achievements_get)
    app.router.add_post("/api/achievements", api_achievements_save)
    app.router.add_post("/api/reaction-roles/find", api_reaction_roles_find)
    app.router.add_get("/api/releases", api_releases_get)
    app.router.add_post("/api/releases", api_releases_post)
    app.router.add_post("/api/releases/check", api_releases_check)
    app.router.add_post("/api/editserver", api_editserver)
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
