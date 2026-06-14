import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import AsyncSessionLocal
from app.models import AuthSession, TelegramUser
from app.services.crypto import generate_state, hash_state
from app.utils.text_split import split_telegram_text

logger = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}"

    async def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.base_url}/{method}", json=payload)
            response.raise_for_status()
            data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error on {method}: {data.get('description')}")
        return data

    async def _get(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
        async with httpx.AsyncClient(timeout=self.settings.telegram_polling_timeout + 10) as client:
            response = await client.get(f"{self.base_url}/{method}", params=params)
            response.raise_for_status()
            data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error on {method}: {data.get('description')}")
        return data

    async def send_message(self, tg_id: int, text: str) -> None:
        await self._post("sendMessage", {"chat_id": tg_id, "text": text, "disable_web_page_preview": True})

    async def send_long_message(self, tg_id: int, text: str) -> None:
        for chunk in split_telegram_text(text):
            await self.send_message(tg_id, chunk)

    async def send_connect_button(self, tg_id: int, state: str) -> None:
        url = f"{self.settings.connect_url_base}/connect?state={state}"
        text = (
            "Привет! Я помогу проанализировать твой Instagram по данным Insights.\n\n"
            "Нажми кнопку ниже и подключи профессиональный Instagram-аккаунт."
        )
        await self._post(
            "sendMessage",
            {
                "chat_id": tg_id,
                "text": text,
                "reply_markup": {
                    "inline_keyboard": [[{"text": "Подключить Instagram", "url": url}]],
                },
            },
        )

    async def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": self.settings.telegram_polling_timeout,
            "allowed_updates": '["message"]',
        }
        if offset is not None:
            params["offset"] = offset
        data = await self._get("getUpdates", params)
        return data.get("result", [])


async def upsert_telegram_user(session: AsyncSession, message_from: dict[str, Any]) -> TelegramUser:
    tg_id = int(message_from["id"])
    result = await session.execute(select(TelegramUser).where(TelegramUser.tg_id == tg_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = TelegramUser(tg_id=tg_id)
        session.add(user)

    user.username = message_from.get("username")
    user.first_name = message_from.get("first_name")
    user.last_name = message_from.get("last_name")
    await session.flush()
    return user


async def create_auth_session(session: AsyncSession, tg_id: int) -> str:
    state = generate_state()
    auth_session = AuthSession(
        tg_id=tg_id,
        state_hash=hash_state(state),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    session.add(auth_session)
    await session.flush()
    return state


async def handle_start_or_connect(session: AsyncSession, tg_user: TelegramUser, client: TelegramClient) -> None:
    state = await create_auth_session(session, tg_user.tg_id)
    await session.commit()
    await client.send_connect_button(tg_user.tg_id, state)


async def handle_update(update: dict[str, Any], client: TelegramClient) -> None:
    message = update.get("message") or {}
    text = (message.get("text") or "").strip()
    sender = message.get("from")
    if not sender or not text:
        return

    async with AsyncSessionLocal() as session:
        tg_user = await upsert_telegram_user(session, sender)
        if text.startswith("/start") or text.startswith("/connect"):
            await handle_start_or_connect(session, tg_user, client)
        else:
            await session.commit()
            await client.send_message(tg_user.tg_id, "Отправь /connect, чтобы подключить Instagram.")


async def polling_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    if not settings.telegram_polling_enabled:
        logger.info("Telegram polling disabled")
        return
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN is empty; Telegram polling is not started")
        return

    client = TelegramClient(settings)
    offset: int | None = None
    logger.info("Telegram polling started")

    while not stop_event.is_set():
        try:
            updates = await client.get_updates(offset)
            for update in updates:
                offset = int(update["update_id"]) + 1
                await handle_update(update, client)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram polling iteration failed")
            await asyncio.sleep(5)

    logger.info("Telegram polling stopped")
