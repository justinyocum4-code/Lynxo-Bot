"""Minimal synchronous Supabase REST client for Lynxo Bot's storage.

Uses only the standard library (urllib) so the Store class can stay
synchronous — no event-loop juggling, no new dependencies.

Table contract (created separately in Supabase):
    public.lynxo_store (
        guild_id  TEXT PRIMARY KEY,
        data      JSONB NOT NULL DEFAULT '{}',
        updated_at TIMESTAMPTZ DEFAULT now()
    )
RLS is on with no policies, so the service_role key is REQUIRED here
(SUPABASE_SERVICE_KEY). The anon key cannot read or write this table.

Every function fails soft: on any network/auth error it prints one plain
line to stderr and returns an empty result. The bot must never crash
because storage is unreachable.
"""
import json
import os
import sys
import urllib.error
import urllib.request

TABLE = "lynxo_store"
TIMEOUT = 10  # seconds


def _config():
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    return url, key


def configured():
    """True when both Supabase env vars are present."""
    url, key = _config()
    return bool(url and key)


def _call(method, path, body=None):
    url, key = _config()
    if not url or not key:
        return None
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url + "/rest/v1" + path,
        data=payload,
        method=method,
        headers={
            "apikey": key,
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            # POST becomes an upsert on the guild_id primary key.
            "Prefer": "resolution=merge-duplicates",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8").strip()
            return json.loads(raw) if raw else []
    except Exception as e:  # network down, bad key, table missing, ...
        print(f"Supabase {method} {path} failed: {e}. "
              f"Storage will retry on the next save.", file=sys.stderr, flush=True)
        return None


def get_all_blobs():
    """Return {guild_id: data_dict} for every stored guild, or {} on error."""
    rows = _call("GET", f"/{TABLE}?select=guild_id,data")
    if not rows:
        return {}
    out = {}
    for row in rows:
        gid = row.get("guild_id")
        if gid is not None:
            data = row.get("data")
            out[str(gid)] = data if isinstance(data, dict) else {}
    return out


def get_guild_blob(guild_id):
    """Return one guild's data dict, or {} if missing/on error."""
    rows = _call("GET", f"/{TABLE}?guild_id=eq.{guild_id}&select=data")
    if rows and isinstance(rows[0].get("data"), dict):
        return rows[0]["data"]
    return {}


def upsert_guild_blob(guild_id, data):
    """Write one guild's blob. Returns True on success, False on error."""
    result = _call("POST", f"/{TABLE}",
                   {"guild_id": str(guild_id), "data": data})
    return result is not None
