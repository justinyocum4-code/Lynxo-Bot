"""Murray — grumpy old metalhead AI character.
Roasts music taste in his home channel and when mentioned. Text only for now."""
import sys
import time

import discord
from discord import app_commands
from discord.ext import commands

from ai import ai_chat

MURRAY_SYSTEM = (
    "You are Murray, a grumpy old metalhead in his 60s hanging out on a "
    "metal Discord server. You've been listening since Black Sabbath was new. "
    "Your personality:\n"
    "- You call everyone 'kid'.\n"
    "- You think most music after 1991 is garbage, but you say it with "
    "grudging affection, not real hate.\n"
    "- You worship Sabbath, Priest, Maiden, Motorhead. Vinyl only. "
    "Streaming is for cowards.\n"
    "- You roast people's music taste playfully — tease them, don't wound them.\n"
    "- You're grumpy but lovable, like a sitcom grandpa.\n"
    "Rules you NEVER break:\n"
    "- Keep replies short: 1-3 sentences.\n"
    "- Roast music taste only. Never mock someone's body, disability, race, "
    "gender, sexuality, or anything personal. No slurs, ever.\n"
    "- If someone is upset or asks you to stop, drop the act and be kind.\n"
    "- Never claim to be a real person.\n"
    "Talk like a grumpy old roadie, not a chatbot."
)

DEFAULTS = {
    "enabled": False,
    "channel_id": None,
}


class Murray(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._cooldown = {}  # (guild_id, user_id) -> timestamp

    def _config(self, guild):
        g = self.bot.store.guild(guild.id)
        cfg = g.setdefault("murray", {})
        for k, v in DEFAULTS.items():
            cfg.setdefault(k, v)
        return cfg

    def _save(self):
        self.bot.store.save()

    async def _reply(self, message, text):
        reply = await ai_chat(MURRAY_SYSTEM, text)
        if not reply:
            return
        try:
            await message.reply(reply[:1500], mention_author=False)
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.author.bot:
            return
        try:
            cfg = self._config(message.guild)
            if not cfg.get("enabled"):
                return
            content = (message.content or "").strip()
            if not content:
                return
            bot_user = self.bot.user
            mentioned = bot_user is not None and bot_user in message.mentions
            # Murray's home channel: he replies to everything.
            # Elsewhere: only when mentioned or addressed by name.
            home_id = cfg.get("channel_id")
            in_home = home_id and message.channel.id == int(home_id)
            addressed = content.lower().startswith("murray")
            if not (in_home or mentioned or addressed):
                return
            # Cooldown: one reply per user per 30s (home channel),
            # 10s for direct mentions.
            now = time.time()
            key = (message.guild.id, message.author.id)
            cd = 10 if (mentioned or addressed) and not in_home else 30
            if now - self._cooldown.get(key, 0) < cd:
                return
            self._cooldown[key] = now
            # Don't reply to commands.
            if content.startswith(("/", "!")):
                return
            async with message.channel.typing():
                await self._reply(message,
                                  f"{message.author.display_name} says: {content}")
        except Exception as e:  # noqa: BLE001
            print(f"Murray: on_message failed: {e}", file=sys.stderr,
                  flush=True)

    @app_commands.command(name="murray",
                          description="Ask Murray the grumpy old metalhead something.")
    @app_commands.describe(question="What do you want to ask him?")
    async def murray_cmd(self, interaction: discord.Interaction, question: str):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Use that inside the server.", ephemeral=True)
            return
        cfg = self._config(guild)
        if not cfg.get("enabled"):
            await interaction.response.send_message(
                "Murray isn't awake right now.", ephemeral=True)
            return
        await interaction.response.defer()
        reply = await ai_chat(
            MURRAY_SYSTEM,
            f"{interaction.user.display_name} asks: {question[:500]}")
        if not reply:
            await interaction.followup.send(
                "Murray grunted and went back to sleep. Try again in a bit.")
            return
        await interaction.followup.send(reply[:1500])
