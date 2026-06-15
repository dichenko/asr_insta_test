import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db import init_db
from app.routes import connect, health, instagram_auth, static_pages
from app.services.telegram import polling_loop

logger = logging.getLogger(__name__)


async def init_db_with_retry(attempts: int = 30, delay_seconds: float = 2.0) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await init_db()
            return
        except Exception as exc:
            last_error = exc
            logger.warning("Database init failed on attempt %s/%s: %s", attempt, attempts, exc.__class__.__name__)
            await asyncio.sleep(delay_seconds)
    raise RuntimeError("Database init failed after retries") from last_error


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    await init_db_with_retry()

    stop_event = asyncio.Event()
    polling_task: asyncio.Task | None = None
    if settings.telegram_polling_enabled:
        polling_task = asyncio.create_task(polling_loop(stop_event))

    try:
        yield
    finally:
        stop_event.set()
        if polling_task:
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Instagram Insights Telegram Bot MVP", lifespan=lifespan)
app.include_router(health.router)
app.include_router(connect.router)
app.include_router(instagram_auth.router)
app.include_router(static_pages.router)
