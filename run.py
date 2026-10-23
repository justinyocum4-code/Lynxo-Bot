"""Combined entry point: Lynxo Bot + Murray Bot on ONE service.

Starts ONE aiohttp app on $PORT (Lynxo keepalive routes via
keepalive.build_app, plus Murray's dashboard routes via
murray_bot.register_murray_routes), then starts both Discord bots
as tasks on the same event loop and waits forever.

Tokens: Lynxo reads LYNXO_DISCORD_TOKEN; Murray keeps DISCORD_TOKEN
(plus any extra bots configured in the Supabase murray_bots table).
"""
import asyncio
import os

from aiohttp import web

import keepalive
import lynxo_bot
import murray_bot


async def main():
    # Both bots must skip binding their own web server: there is
    # exactly ONE app / ONE $PORT listener in the combined service.
    lynxo_bot.COMBINED_MODE = True
    murray_bot.COMBINED_MODE = True

    port = int(os.environ.get("PORT", "8000"))
    app = keepalive.build_app(lynxo_bot.bot)
    murray_bot.register_murray_routes(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"combined web listening on 0.0.0.0:{port}", flush=True)

    lynxo_task = asyncio.create_task(lynxo_bot.start_lynxo_bot())
    murray_task = asyncio.create_task(murray_bot.start_murray_bots())
    print("combined service: Lynxo + Murray starting", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
