"""Guild settings, strikes, quarantine, and verification storage.

Two modes, same interface:
  * Supabase mode (SUPABASE_URL + SUPABASE_SERVICE_KEY are set): every
    guild's data lives as one JSON blob in the public.lynxo_store table.
    Blobs are loaded once at startup and cached in memory; save() writes
    them back in background threads so slow network never stalls the bot.
    This survives Render redeploys and restarts.
  * File mode (env vars missing): the old data.json behavior. Works fine
    locally, but Render's free tier wipes the file on every deploy.

Cogs use Store exactly as before: Store(path), .guild(gid), .save(),
.setup_guilds(), and the .data attribute. Nothing else changed.
"""
import copy
import json
import os
import sys
import threading
import time

import db
from config import DEFAULT_SETTINGS

_WRITE_DEBOUNCE = 5.0  # seconds between Supabase writes for one guild


def _default_releases():
    return {
        "enabled": False,
        "channel_id": None,
        "announced": [],   # [{"id": mbid, "title": str, "artist": str}]
        "last_check": None,
    }


def _default_reaction_roles():
    return {
        "channel_id": None,
        "message_id": None,
        "title": "Pick your roles",
        "description": ("Tap a reaction to give yourself that role. "
                        "Tap it again to remove it."),
        "color": 16766720,  # gold #FFD700
        "mappings": [],     # {"emoji": str, "role_id": int, "label": str}
    }


class Store:
    def __init__(self, path="data.json"):
        self.path = path
        self._lock = threading.Lock()
        self._use_db = db.configured()
        self._last_write = {}  # guild key -> monotonic timestamp
        # Keys whose blobs came from Supabase. A guild key gets added here
        # after its first successful write too. save() never writes a guild
        # that isn't in this set unless /setup ran for it this session —
        # that way a failed startup load can never overwrite real remote
        # data with a blank guild.
        self._loaded_keys = set()
        self.data = {"guilds": {}}
        if self._use_db:
            self.data["guilds"] = db.get_all_blobs()
            self._loaded_keys.update(self.data["guilds"].keys())
            print(f"Store: using Supabase ({len(self.data['guilds'])} guild(s) loaded).",
                  file=sys.stderr, flush=True)
        else:
            print("Store: SUPABASE_URL/SUPABASE_SERVICE_KEY not set — "
                  "using data.json, which is wiped on Render redeploys.",
                  file=sys.stderr, flush=True)
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        self.data = json.load(f)
                except Exception:
                    self.data = {"guilds": {}}

    def save(self):
        if self._use_db:
            self._save_db()
        else:
            self._save_file()

    def _save_file(self):
        tmp = self.path + ".tmp"
        with self._lock:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)

    def _save_db(self):
        """Queue a background write per guild, debounced to one per 5s.

        Skips guilds we never loaded from Supabase and never ran /setup
        for — writing those would overwrite real remote data with blanks.
        """
        with self._lock:
            now = time.monotonic()
            queued = []
            for key, guild_data in self.data["guilds"].items():
                if key not in self._loaded_keys and not guild_data.get("setup"):
                    continue
                if now - self._last_write.get(key, 0) < _WRITE_DEBOUNCE:
                    continue
                self._last_write[key] = now
                queued.append((key, copy.deepcopy(guild_data)))
        for key, blob in queued:
            thread = threading.Thread(
                target=self._write_one, args=(key, blob),
                daemon=True, name=f"lynxo-save-{key}")
            thread.start()

    def _write_one(self, key, blob):
        try:
            ok = db.upsert_guild_blob(key, blob)
        except Exception as e:  # never let a writer thread die loudly
            print(f"Store: background write for guild {key} crashed: {e}",
                  file=sys.stderr, flush=True)
            ok = False
        with self._lock:
            if ok:
                self._loaded_keys.add(key)
            else:
                # Clear the debounce stamp so the next save() retries.
                self._last_write.pop(key, None)

    def _blank_guild(self):
        return {
            "setup": False,
            "channels": {},      # verify_here, get_adult_access, mod_logs, verify_review
            "roles": {},         # member, adult, quarantined
            "settings": copy.deepcopy(DEFAULT_SETTINGS),
            "strikes": {},       # user_id -> {"count": int, "history": [...]}
            "quarantined": {},   # user_id -> [role ids to restore]
            "verify": {},        # user_id -> {"fails": int}
            "verify_reviews": {},  # review message_id -> {"user_id": str, "expected": int}
            "verify_photo_hashes": {},  # sha256 hex -> user_id (duplicate-photo check)
            "nuke_whitelist": {"users": [], "roles": []},  # exempt from anti-nuke
            "panic": {},         # saved pre-panic channel overwrites (for /unlock)
            "reaction_roles": _default_reaction_roles(),
            "releases": _default_releases(),
        }

    def guild(self, gid):
        key = str(gid)
        with self._lock:
            g = self.data["guilds"].setdefault(key, self._blank_guild())
            # Merge in any defaults added by newer versions.
            for k, v in DEFAULT_SETTINGS.items():
                g["settings"].setdefault(k, copy.deepcopy(v))
            g.setdefault("nuke_whitelist", {"users": [], "roles": []})
            g.setdefault("panic", {})
            g.setdefault("verify_reviews", {})
            g.setdefault("verify_photo_hashes", {})
            if "reaction_roles" not in g:
                g["reaction_roles"] = _default_reaction_roles()
            if "releases" not in g:
                g["releases"] = _default_releases()
            return g

    def setup_guilds(self):
        with self._lock:
            return [int(gid) for gid, g in self.data["guilds"].items()
                    if g.get("setup")]
