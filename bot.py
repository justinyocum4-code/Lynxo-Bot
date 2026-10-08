"""Lynxo Bot — all-in-one Discord security bot. Phases 1-3."""
import asyncio
import os
import sys
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands

import keepalive
from storage import Store
import raid
import automod
import verification
import antinuke
import backups


class LynxoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True          # privileged: join tracking, role mgmt
        intents.message_content = True  # privileged: auto-mod scanning
        super().__init__(command_prefix="!", intents=intents)
        self.store = Store(os.environ.get("DATA_PATH", "data.json"))
        # In-memory ring buffer of mod-log lines for the dashboard API.
        self.recent_logs = defaultdict(lambda: deque(maxlen=200))

    async def setup_hook(self):
        await self.add_cog(raid.Raid(self))
        await self.add_cog(automod.AutoMod(self))
        await self.add_cog(verification.Verification(self))
        await self.add_cog(antinuke.AntiNuke(self))
        await self.add_cog(backups.Backups(self))
        # Persistent buttons must be re-registered every startup.
        self.add_view(verification.VerifyView(self))
        self.add_view(verification.AdultView(self))
        port = int(os.environ.get("PORT", "8000"))
        self._health_runner = await keepalive.start(port, self)

    async def on_ready(self):
        print(f"logged in as {self.user} ({self.user.id})", flush=True)
        try:
            synced = await self.tree.sync()
            print(f"synced {len(synced)} global command(s)", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"global command sync failed: {e}", flush=True)
        for gid in self.store.setup_guilds():
            guild = self.get_guild(gid)
            if guild is None:
                continue
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                print(f"synced {len(synced)} command(s) to {guild.name}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"command sync failed for guild {gid}: {e}", flush=True)


bot = LynxoBot()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction,
                               error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "You don't have permission to use that command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        missing = ", ".join(error.missing_permissions)
        msg = f"I'm missing permissions for that: {missing}. I need Administrator."
    else:
        msg = "Something went wrong running that command."
        print(f"command error: {error!r}", flush=True)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


async def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("ERROR: DISCORD_TOKEN environment variable is not set.", flush=True)
        raise SystemExit(1)
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
