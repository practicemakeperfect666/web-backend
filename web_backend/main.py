"""FastAPI entry point for the web monitor backend.

Run with:
    python -m uvicorn web_backend.main:app --host 127.0.0.1 --port 8000
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router as api_router
from .db import init_db
from .ws import broadcast_loop
from .ws import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(broadcast_loop())
    yield
    task.cancel()


app = FastAPI(title="vnpy-domestic web monitor", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(ws_router)
