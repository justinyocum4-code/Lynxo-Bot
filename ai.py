"""Optional Groq (openai/gpt-oss-20b) assist for auto-mod.

Free tier, no card required. Only used when GROQ_API_KEY is set AND the
guild has ai_moderation enabled. Any failure -> None (rule-based filters
keep working on their own).
"""
import os

import aiohttp

MODEL = "openai/gpt-oss-20b"
URL = "https://api.groq.com/openai/v1/chat/completions"


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
