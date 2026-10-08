"""NSFW photo scan: auto-check images in designated channels for AI generation.
Uses Groq vision (advisory only — best guess, not a guarantee)."""
import sys

import discord
from discord.ext import commands

from ai import ai_vision_scan

SCAN_PROMPT = (
    "Analyze this photo. Is it likely AI-generated or a real photograph? "
    "Look for: unnatural skin texture, wrong number of fingers or weird hands, "
    "inconsistent lighting or shadows, overly perfect symmetry, strange or "
    "warped backgrounds, digital artifacts. Reply in exactly this format:\n"
    "VERDICT: REAL  (or VERDICT: FAKE)\n"
    "REASON: one short sentence explaining why."
)

DEFAULTS = {
    "enabled": False,
    "channel_ids": [],
}


class NsfwScan(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _config(self, guild):
        g = self.bot.store.guild(guild.id)
        cfg = g.setdefault("nsfw_scan", {})
        for k, v in DEFAULTS.items():
            cfg.setdefault(k, v)
        return cfg

    def _save(self):
        self.bot.store.save()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.author.bot:
            return
        # Only images.
        images = [a for a in message.attachments
                  if a.content_type and a.content_type.startswith("image/")]
        if not images:
            return
        try:
            cfg = self._config(message.guild)
            if not cfg.get("enabled"):
                return
            if message.channel.id not in [int(c) for c in cfg.get("channel_ids", [])]:
                return
            att = images[0]
            try:
                image_bytes = await att.read()
            except (discord.HTTPException, discord.Forbidden):
                return
            if not image_bytes or len(image_bytes) > 8_000_000:
                return
            result = await ai_vision_scan(
                image_bytes, att.content_type or "image/jpeg", SCAN_PROMPT)
            if not result:
                return
            verdict, reason = "UNKNOWN", ""
            for line in result.splitlines():
                line = line.strip()
                if line.upper().startswith("VERDICT:"):
                    v = line.split(":", 1)[1].strip().upper()
                    verdict = "FAKE" if "FAKE" in v else "REAL" if "REAL" in v else "UNKNOWN"
                elif line.upper().startswith("REASON:"):
                    reason = line.split(":", 1)[1].strip()[:300]
            if verdict == "FAKE":
                emoji, label = "⚠️", "looks AI-generated"
            elif verdict == "REAL":
                emoji, label = "✅", "looks like a real photo"
            else:
                emoji, label = "❓", "couldn't be judged"
            text = f"{emoji} AI scan: this photo {label}."
            if reason:
                text += f" {reason}"
            text += "\n_(AI best guess — not a guarantee.)_"
            try:
                await message.reply(text, mention_author=False)
            except (discord.Forbidden, discord.HTTPException):
                pass
        except Exception as e:  # noqa: BLE001
            print(f"NsfwScan: on_message failed: {e}", file=sys.stderr,
                  flush=True)
