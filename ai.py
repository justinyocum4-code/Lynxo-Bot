"""Optional Groq (openai/gpt-oss-20b) assist for auto-mod.

Free tier, no card required. Only used when GROQ_API_KEY is set AND the
guild has ai_moderation enabled. Any failure -> None (rule-based filters
keep working on their own).
"""
import base64
import json
import os

import aiohttp

MODEL = "openai/gpt-oss-20b"
URL = "https://api.groq.com/openai/v1/chat/completions"
MODELS_URL = "https://api.groq.com/openai/v1/models"

# Preferred models, in order. _available_models() filters these against
# what Groq actually serves right now, so retired models are skipped
# automatically instead of failing at call time.
_TEXT_PREFS = [
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
    "gemma2-9b-it",
]
_VISION_PREFS = [
    "qwen/qwen3-32b",
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
]

_model_cache = {"text": None, "vision": None}


async def _available_models():
    """Return the set of model IDs Groq currently serves (cached)."""
    if _model_cache.get("all") is not None:
        return _model_cache["all"]
    key = os.environ.get("GROQ_API_KEY")
    ids = set()
    if key:
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    MODELS_URL,
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=timeout,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for m in data.get("data", []):
                            mid = m.get("id")
                            if mid:
                                ids.add(mid)
        except Exception as e:  # noqa: BLE001
            print(f"ai: model list fetch failed: {e}", flush=True)
    _model_cache["all"] = ids
    print(f"ai: Groq serves {len(ids)} models", flush=True)
    return ids


async def _pick_models(kind):
    """Pick working models of a kind ('text' or 'vision')."""
    if _model_cache[kind] is not None:
        return _model_cache[kind]
    available = await _available_models()
    prefs = _TEXT_PREFS if kind == "text" else _VISION_PREFS
    if available:
        picked = [m for m in prefs if m in available]
    else:
        picked = list(prefs)  # list fetch failed; try prefs blind
    _model_cache[kind] = picked
    print(f"ai: using {kind} models: {picked}", flush=True)
    return picked


# Backwards-compat: module-level lists (populated lazily).
TEXT_MODELS = _TEXT_PREFS
VISION_MODELS = _VISION_PREFS


async def ai_moderate(text):
    """Return 'FLAG: <reason>' if the message looks bad, else None."""
    key = os.environ.get("GROQ_API_KEY")
    if not key or not (text or "").strip():
        return None
    prompt = (
        "You are a Discord moderation helper. Reply with exactly one line: "
        "either 'OK' or 'FLAG: <short reason>'. Flag hate speech, threats, "
        "sexual content involving minors, phishing or scam attempts, and spam. "
        "Normal chat, friendly profanity, and metal lyrics are OK.\n\n"
        f"Message: {text[:500]}"
    )
    for model in await _pick_models("text"):
        try:
            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    URL,
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 60,
                        "temperature": 0,
                    },
                    timeout=timeout,
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
            out = data["choices"][0]["message"]["content"].strip()
            if out.upper().startswith("FLAG"):
                return out
            return None
        except Exception:  # noqa: BLE001
            continue
    return None


async def ai_plan_server_edit(prompt):
    """Turn a plain-English server-edit request into a JSON action list.

    Returns the list (possibly empty when the request is unclear) or None
    if the AI call itself failed."""
    key = os.environ.get("GROQ_API_KEY")
    if not key or not (prompt or "").strip():
        return None
    system = (
        "You turn a Discord server owner's plain-English request into a JSON "
        "array of actions. Respond with ONLY the JSON array, no other text, "
        "no markdown fences.\n\n"
        "Allowed actions (use exactly these field names):\n"
        '{"action":"create_channel","name":"metal-memes","type":"text",'
        '"category":"Music"}\n'
        '{"action":"create_channel","name":"nsfw-pics","type":"text",'
        '"nsfw":true}\n'
        '{"action":"create_channel","name":"Lounge","type":"voice"}\n'
        '{"action":"create_channel","name":"Games","type":"category"}\n'
        '{"action":"delete_channel","name":"off-topic"}\n'
        '{"action":"create_role","name":"VIP","color":"gold"}\n'
        '{"action":"delete_role","name":"Old Role"}\n'
        '{"action":"set_role_color","name":"VIP","color":"gold"}\n'
        '{"action":"set_channel_perms","channel":"general","role":"Member",'
        '"allow":["view","send"],"deny":[]}\n\n'
        '{"action":"rename_channel","name":"new-releases",'
        '"new_name":"\U0001F195 new-releases"}\n'
        '{"action":"rename_role","name":"Old","new_name":"New"}\n'
        '{"action":"move_channel","name":"general",'
        '"category":"Community"}\n'
        '{"action":"config_leveling","enabled":true,'
        '"levelup_channel":"general"}\n'
        '{"action":"add_level_reward","level":5,"role":"VIP"}\n'
        '{"action":"remove_level_reward","level":5}\n'
        '{"action":"config_achievements","enabled":true,'
        '"announce_channel":"general"}\n'
        '{"action":"add_achievement","name":"Chatterbox",'
        '"description":"Send 100 messages","emoji":"\U0001F4AC",'
        '"type":"messages","threshold":100}\n'
        '{"action":"remove_achievement","name":"Chatterbox"}\n'
        '{"action":"config_nsfw_scan","enabled":true,'
        '"channel":"nsfw-pics"}\n\n'
        "Examples:\n"
        "Request: add a text channel called metal-memes\n"
        '[{"action":"create_channel","name":"metal-memes","type":"text"}]\n'
        "Request: make a VIP role with a gold color\n"
        '[{"action":"create_role","name":"VIP","color":"gold"}]\n'
        "Request: let the Member role send messages in general\n"
        '[{"action":"set_channel_perms","channel":"general","role":"Member",'
        '"allow":["view","send"],"deny":[]}]\n\n'
        "Request: put an emoji on the new-releases channel\n"
        '[{"action":"rename_channel","name":"new-releases",'
        '"new_name":"\U0001F195 new-releases"}]\n'
        "Request: turn on leveling and give VIP at level 5\n"
        '[{"action":"config_leveling","enabled":true},'
        '{"action":"add_level_reward","level":5,"role":"VIP"}]\n\n'
        "Rules:\n"
        "- type is one of: text, voice, category (default text).\n"
        "- category is optional: exact name of a category to place the channel in.\n"
        "- color is one of: red, blue, green, gold, purple, orange, pink, "
        "teal, white, black, or a #RRGGBB hex code.\n"
        "- allow/deny use only these words: view, send, speak, connect, react.\n"
        "- achievement type is one of: messages, level, days, roles.\n"
        "- set \"nsfw\":true for adult/18+ channels.\n"
        "- Return at most 30 actions. Copy names exactly as the user wrote them.\n"
        "- If the request is not a server-editing request, or you cannot "
        "understand it, return []."
    )
    for model in await _pick_models("text"):
        try:
            timeout = aiohttp.ClientTimeout(total=45)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    URL,
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt[:1000]},
                        ],
                        "max_tokens": 2500,
                        "temperature": 0,
                    },
                    timeout=timeout,
                ) as resp:
                    if resp.status != 200:
                        try:
                            body = (await resp.text())[:200]
                        except Exception:
                            body = "?"
                        print(f"ai_plan: {model} -> HTTP {resp.status}: {body}",
                              flush=True)
                        continue
                    data = await resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            # Strip markdown fences if the model added them anyway.
            if text.startswith("```"):
                text = "\n".join(
                    ln for ln in text.split("\n")
                    if not ln.strip().startswith("```")
                ).strip()
            try:
                actions = json.loads(text)
            except Exception as e:  # noqa: BLE001
                print(f"ai_plan: {model} -> JSON parse failed: {e}; "
                      f"text was: {text[:300]}", flush=True)
                continue
            return actions if isinstance(actions, list) else None
        except Exception as e:  # noqa: BLE001
            print(f"ai_plan: {model} -> error: {e}", flush=True)
            continue
    return None


async def ai_vision_scan(image_bytes, mime, prompt):
    """Send a photo to a Groq vision model. Returns the model's text,
    or None if vision is unavailable (no key, model retired, network error)."""
    key = os.environ.get("GROQ_API_KEY")
    if not key or not image_bytes:
        return None
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime or 'image/jpeg'};base64,{b64}"
    for model in await _pick_models("vision"):
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    URL,
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": model,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url",
                                 "image_url": {"url": data_url}},
                            ],
                        }],
                        "max_tokens": 400,
                        "temperature": 0,
                    },
                    timeout=timeout,
                ) as resp:
                    if resp.status != 200:
                        try:
                            body = (await resp.text())[:200]
                        except Exception:
                            body = "?"
                        print(f"ai_vision_scan: {model} -> HTTP {resp.status}: {body}",
                              flush=True)
                        continue  # try the next model
                    data = await resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            if text:
                return text
        except Exception as e:  # noqa: BLE001
            print(f"ai_vision_scan: {model} -> error: {e}", flush=True)
            continue  # try the next model
    return None
