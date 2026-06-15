import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import AsyncSessionLocal
from app.models import (
    AuthSession,
    CompetitorAnalysisJob,
    FacebookAuthSession,
    FacebookConnection,
    FacebookPage,
    InstagramAccount,
    TelegramUser,
)
from app.services.crypto import generate_state, hash_state
from app.utils.instagram_username import normalize_instagram_username
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
        await self._post(
            "sendMessage",
            {
                "chat_id": tg_id,
                "text": (
                    "Привет! Я помогу проанализировать твой Instagram по данным Insights.\n\n"
                    "Нажми кнопку ниже и подключи профессиональный Instagram-аккаунт."
                ),
                "reply_markup": {
                    "inline_keyboard": [[{"text": "Подключить Instagram", "url": url}]],
                },
            },
        )

    async def send_facebook_connect_button(self, tg_id: int, state: str) -> None:
        url = f"{self.settings.connect_url_base}/auth/facebook/start?state={state}"
        await self._post(
            "sendMessage",
            {
                "chat_id": tg_id,
                "text": (
                    "Для анализа конкурентов нужно подключить Facebook-аккаунт, "
                    "у которого есть доступ к Facebook Page, связанной с Instagram Business/Creator account."
                ),
                "reply_markup": {
                    "inline_keyboard": [[{"text": "Подключить Facebook", "url": url}]],
                },
            },
        )

    async def send_start_buttons(self, tg_id: int, instagram_state: str, facebook_state: str) -> None:
        instagram_url = f"{self.settings.connect_url_base}/connect?state={instagram_state}"
        facebook_url = f"{self.settings.connect_url_base}/auth/facebook/start?state={facebook_state}"
        await self._post(
            "sendMessage",
            {
                "chat_id": tg_id,
                "text": "Выбери, что подключить:",
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "Подключить Instagram — мой аккаунт", "url": instagram_url}],
                        [{"text": "Подключить Facebook — конкуренты", "url": facebook_url}],
                    ],
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


async def create_facebook_auth_session(session: AsyncSession, tg_id: int) -> str:
    state = generate_state()
    auth_session = FacebookAuthSession(
        tg_id=tg_id,
        state_hash=hash_state(state),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    session.add(auth_session)
    await session.flush()
    return state


async def handle_start(session: AsyncSession, tg_user: TelegramUser, client: TelegramClient) -> None:
    instagram_state = await create_auth_session(session, tg_user.tg_id)
    facebook_state = await create_facebook_auth_session(session, tg_user.tg_id)
    await session.commit()
    await client.send_start_buttons(tg_user.tg_id, instagram_state, facebook_state)


async def handle_start_or_connect(session: AsyncSession, tg_user: TelegramUser, client: TelegramClient) -> None:
    state = await create_auth_session(session, tg_user.tg_id)
    await session.commit()
    await client.send_connect_button(tg_user.tg_id, state)


async def handle_connect_facebook(session: AsyncSession, tg_user: TelegramUser, client: TelegramClient) -> None:
    state = await create_facebook_auth_session(session, tg_user.tg_id)
    await session.commit()
    await client.send_facebook_connect_button(tg_user.tg_id, state)


async def get_latest_instagram_account(session: AsyncSession, tg_id: int) -> InstagramAccount | None:
    result = await session.execute(
        select(InstagramAccount)
        .where(
            InstagramAccount.tg_id == tg_id,
            InstagramAccount.access_token_encrypted.isnot(None),
            InstagramAccount.access_token_encrypted != "",
        )
        .order_by(InstagramAccount.connected_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_latest_facebook_connection(session: AsyncSession, tg_id: int) -> FacebookConnection | None:
    result = await session.execute(
        select(FacebookConnection)
        .where(
            FacebookConnection.tg_id == tg_id,
            FacebookConnection.user_access_token_encrypted.isnot(None),
            FacebookConnection.user_access_token_encrypted != "",
        )
        .order_by(FacebookConnection.connected_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_selected_facebook_page(session: AsyncSession, tg_id: int) -> FacebookPage | None:
    result = await session.execute(
        select(FacebookPage)
        .where(
            FacebookPage.tg_id == tg_id,
            FacebookPage.is_selected.is_(True),
            FacebookPage.instagram_business_account_id.isnot(None),
            FacebookPage.instagram_business_account_id != "",
        )
        .order_by(FacebookPage.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def send_status(session: AsyncSession, tg_user: TelegramUser, client: TelegramClient) -> None:
    instagram_account = await get_latest_instagram_account(session, tg_user.tg_id)
    facebook_connection = await get_latest_facebook_connection(session, tg_user.tg_id)
    selected_page = await get_selected_facebook_page(session, tg_user.tg_id)
    linked_instagram = f"@{selected_page.instagram_username}" if selected_page and selected_page.instagram_username else "none"
    await session.commit()
    await client.send_message(
        tg_user.tg_id,
        (
            f"Instagram: {'connected' if instagram_account else 'not connected'}\n"
            f"Facebook: {'connected' if facebook_connection else 'not connected'}\n"
            f"Selected Page: {(selected_page.page_name if selected_page else None) or 'none'}\n"
            f"Linked Instagram: {linked_instagram}"
        ),
    )


async def handle_competitor_request(session: AsyncSession, tg_user: TelegramUser, text: str, client: TelegramClient) -> None:
    username = normalize_instagram_username(text)
    if username is None:
        await session.commit()
        await client.send_message(
            tg_user.tg_id,
            "Не понял аккаунт. Пришли @username или ссылку вида https://www.instagram.com/username/",
        )
        return

    page = await get_selected_facebook_page(session, tg_user.tg_id)
    if page is None:
        state = await create_facebook_auth_session(session, tg_user.tg_id)
        await session.commit()
        await client.send_facebook_connect_button(tg_user.tg_id, state)
        return

    job = CompetitorAnalysisJob(
        tg_id=tg_user.tg_id,
        facebook_connection_id=page.facebook_connection_id,
        facebook_page_id=page.id,
        viewer_instagram_business_account_id=page.instagram_business_account_id,
        competitor_username=username,
    )
    session.add(job)
    await session.commit()
    await client.send_message(
        tg_user.tg_id,
        f"Принял: @{username}. Собираю открытые данные аккаунта и готовлю краткий анализ.",
    )

    from app.services.competitor_jobs import run_competitor_analysis_job

    asyncio.create_task(run_competitor_analysis_job(job.id))


async def handle_update(update: dict[str, Any], client: TelegramClient) -> None:
    message = update.get("message") or {}
    text = (message.get("text") or "").strip()
    sender = message.get("from")
    if not sender or not text:
        return

    async with AsyncSessionLocal() as session:
        tg_user = await upsert_telegram_user(session, sender)
        if text.startswith("/start"):
            await handle_start(session, tg_user, client)
        elif text.startswith("/connect_facebook"):
            await handle_connect_facebook(session, tg_user, client)
        elif text.startswith("/connect_instagram") or text.startswith("/connect"):
            await handle_start_or_connect(session, tg_user, client)
        elif text.startswith("/status"):
            await send_status(session, tg_user, client)
        elif text.startswith("/"):
            await session.commit()
            await client.send_message(tg_user.tg_id, "Доступные команды: /start, /connect_instagram, /connect_facebook, /status")
        else:
            await handle_competitor_request(session, tg_user, text, client)


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
