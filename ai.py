"""Optional Groq (openai/gpt-oss-20b) assist for auto-mod.

Free tier, no card required. Only used when GROQ_API_KEY is set AND the
guild has ai_moderation enabled. Any failure -> None (rule-based filters
keep working on their own).
"""
import base64
import os

import aiohttp

MODEL = "openai/gpt-oss-20b"
URL = "https://api.groq.com/openai/v1/chat/completions"

# Vision-capable models, tried in order. Groq retires models over time,
# so ai_vision_scan falls through the list and returns None if none work.
VISION_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
]


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
    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                URL,
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 60,
                    "temperature": 0,
                },
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        out = data["choices"][0]["message"]["content"].strip()
        if out.upper().startswith("FLAG"):
            return out
        return None
    except Exception:
        return None


async def ai_vision_scan(image_bytes, mime, prompt):
    """Send a photo to a Groq vision model. Returns the model's text,
    or None if vision is unavailable (no key, model retired, network error)."""
    key = os.environ.get("GROQ_API_KEY")
    if not key or not image_bytes:
        return None
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime or 'image/jpeg'};base64,{b64}"
    for model in VISION_MODELS:
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
                        continue  # try the next model
                    data = await resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            if text:
                return text
        except Exception:
            continue  # try the next model
    return None
