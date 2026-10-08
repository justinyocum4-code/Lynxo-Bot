"""Tiny JSON persistence for guild settings, strikes, quarantine, verification.

Note: Render's free tier has an ephemeral disk, so this file resets whenever
the service restarts or redeploys. That is fine for Phase 1; Phase 3
(backups + dashboard) will move this to durable storage.
"""
import copy
import json
import os
import threading

from config import DEFAULT_SETTINGS


class Store:
    def __init__(self, path="data.json"):
        self.path = path
        self._lock = threading.Lock()
        self.data = {"guilds": {}}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {"guilds": {}}

    def save(self):
        tmp = self.path + ".tmp"
        with self._lock:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)

    def _blank_guild(self):
        return {
            "setup": False,
            "channels": {},      # verify_here, get_adult_access, mod_logs, verify_review
            "roles": {},         # member, adult, quarantined
            "settings": copy.deepcopy(DEFAULT_SETTINGS),
            "strikes": {},       # user_id -> {"count": int, "history": [...]}
            "quarantined": {},   # user_id -> [role ids to restore]
            "verify": {},        # user_id -> {"fails": int}
            "nuke_whitelist": {"users": [], "roles": []},  # exempt from anti-nuke
            "panic": {},         # saved pre-panic channel overwrites (for /unlock)
        }

    def guild(self, gid):
        key = str(gid)
        g = self.data["guilds"].setdefault(key, self._blank_guild())
        # Merge in any defaults added by newer versions.
        for k, v in DEFAULT_SETTINGS.items():
            g["settings"].setdefault(k, copy.deepcopy(v))
        g.setdefault("nuke_whitelist", {"users": [], "roles": []})
        g.setdefault("panic", {})
        return g

    def setup_guilds(self):
        return [int(gid) for gid, g in self.data["guilds"].items()
                if g.get("setup")]
