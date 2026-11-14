"""Small local-only FastAPI app and heartbeat-enabled POST SSE relay."""

import asyncio
import contextlib
import json
import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .foundry import check_health, produce_run
from .settings import ROOT, Settings
from .telemetry import configure_tracing

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
settings = Settings()
FRONTEND = ROOT / "app" / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(asyncio.to_thread(configure_tracing, settings))
    yield
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Caldova Control Tower", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


class RunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=16000, pattern=r"\S")
    scenario_id: str | None = Field(default=None, max_length=120)
    mode: Literal["a2a", "orchestrated"] = "a2a"


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/config")
async def config():
    return settings.public_config()


@app.get("/api/health")
async def health():
    try:
        return await check_health(settings)
    except Exception as exc:
        return {"status": "error", "agents": {key: {"status": "error", "message": str(exc)[:700]} for key in ("coordinator", "analyst")}}


@app.post("/api/run")
async def run(body: RunRequest, request: Request):
    async def relay():
        queue = asyncio.Queue(maxsize=512)

        async def worker():
            try:
                await produce_run(settings, body.message.strip(), body.mode, body.scenario_id, queue.put)
            finally:
                if not asyncio.current_task().cancelling():
                    await queue.put(None)

        task = asyncio.create_task(worker())
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if item is None:
                    break
                yield f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False, default=str)}\n\n"
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(relay(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
