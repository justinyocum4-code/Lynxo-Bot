"""Tiny HTTP health endpoint so Render (and uptime monitors) can see the bot."""
from aiohttp import web


async def _health(request):
    return web.Response(text="ok")


async def start(port):
    app = web.Application()
    app.router.add_get("/health", _health)
    app.router.add_get("/", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"health endpoint listening on 0.0.0.0:{port}", flush=True)
    return runner
