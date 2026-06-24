"""
Точка входа: запускает aiogram-бота и FastAPI-сервер в одном asyncio-цикле.
Render.com: Start Command = python run.py
"""
import asyncio
import importlib
import subprocess
import sys

_REQUIRED = {
    "aiogram": "aiogram>=3.7,<4.0",
    "aiosqlite": "aiosqlite>=0.20.0",
    "fastapi": "fastapi>=0.111.0",
    "uvicorn": "uvicorn[standard]>=0.29.0",
}

def _ensure_deps():
    missing = []
    for mod, pkg in _REQUIRED.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("Installing:", *missing)
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "--disable-pip-version-check", *missing])
        importlib.invalidate_caches()

_ensure_deps()

import uvicorn
import bot as B
import api   # регистрирует патч _broadcast и создаёт app

async def main():
    port = int(__import__("os").getenv("PORT", "8080"))
    config = uvicorn.Config(api.app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    # бот и API работают в одном event loop, делят _db и _sessions
    await asyncio.gather(B._main(), server.serve())

if __name__ == "__main__":
    asyncio.run(main())
