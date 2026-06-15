import logging
import urllib.parse
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import FacebookAuthSession, FacebookConnection, FacebookPage
from app.routes.connect import _html
from app.services.crypto import encrypt_token, hash_state
from app.services.facebook_graph import FacebookGraphClient, token_expires_at
from app.services.telegram import TelegramClient

router = APIRouter(prefix="/auth/facebook")
logger = logging.getLogger(__name__)


async def get_valid_facebook_auth_session(session: AsyncSession, state: str) -> FacebookAuthSession | None:
    result = await session.execute(select(FacebookAuthSession).where(FacebookAuthSession.state_hash == hash_state(state)))
    auth_session = result.scalar_one_or_none()
    if auth_session is None:
        return None
    if auth_session.status != "pending":
        return None
    now = datetime.now(timezone.utc)
    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        auth_session.status = "expired"
        await session.commit()
        return None
    return auth_session


@router.get("/start", response_model=None)
async def start_facebook_oauth(state: str = Query(...), session: AsyncSession = Depends(get_session)) -> RedirectResponse | HTMLResponse:
    auth_session = await get_valid_facebook_auth_session(session, state)
    if auth_session is None:
        return _html("Link expired", "<p>This Facebook connection link has expired. Please return to Telegram and request a new link.</p>")

    settings = get_settings()
    if not settings.facebook_app_id:
        return _html("Facebook is not configured", "<p>FACEBOOK_APP_ID is not configured.</p>")
    params = {
        "client_id": settings.facebook_app_id,
        "redirect_uri": settings.facebook_redirect_uri,
        "state": state,
        "scope": ",".join(settings.facebook_scope_list),
        "response_type": "code",
    }
    oauth_url = f"https://www.facebook.com/{settings.facebook_api_version}/dialog/oauth?" + urllib.parse.urlencode(params)
    logger.info(
        "Facebook OAuth start: oauth_host=%s client_id=%s redirect_uri=%r scopes=%s state_len=%s",
        "www.facebook.com",
        settings.facebook_app_id,
        settings.facebook_redirect_uri,
        settings.facebook_scope_list,
        len(state),
    )
    return RedirectResponse(oauth_url, status_code=302)


@router.get("/callback", response_class=HTMLResponse)
async def facebook_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    if error:
        return _html("Facebook error", f"<p>Facebook authorization failed: {error_description or error}</p>")
    if not code or not state:
        return _html("Invalid callback", "<p>Authorization code or state is missing.</p>")

    settings = get_settings()
    logger.info(
        "Facebook OAuth callback: host=%r path=%r query_keys=%s configured_redirect_uri=%r code_len=%s state_len=%s",
        request.headers.get("host"),
        request.url.path,
        sorted(request.query_params.keys()),
        settings.facebook_redirect_uri,
        len(code),
        len(state),
    )

    auth_session = await get_valid_facebook_auth_session(session, state)
    if auth_session is None:
        return _html("Invalid link", "<p>This Facebook connection link is invalid or expired. Please return to Telegram and request a new link.</p>")

    auth_session.status = "used"
    auth_session.used_at = datetime.now(timezone.utc)
    await session.flush()

    graph = FacebookGraphClient(settings=settings)
    try:
        short_token_data = await graph.exchange_code_for_token(code)
        access_token = short_token_data.get("access_token")
        if not access_token:
            raise RuntimeError("Facebook did not return an access token")
        token_data = short_token_data
        long_lived_data = await graph.exchange_for_long_lived_token(access_token)
        if long_lived_data.get("access_token"):
            token_data = long_lived_data
            access_token = long_lived_data["access_token"]

        graph_with_token = FacebookGraphClient(access_token=access_token, settings=settings)
        profile = await graph_with_token.fetch_me()
        pages = await graph_with_token.fetch_pages()
    except Exception as exc:
        logger.warning("Facebook OAuth callback failed safely: %s", exc.__class__.__name__)
        auth_session.status = "failed"
        await session.commit()
        return _html("Facebook connection failed", "<p>Could not connect Facebook. Please return to Telegram and request a new link.</p>")

    facebook_user_id = str(profile.get("id") or "")
    if not facebook_user_id:
        auth_session.status = "failed"
        await session.commit()
        return _html("Facebook connection failed", "<p>Facebook did not return a user id.</p>")

    existing_result = await session.execute(
        select(FacebookConnection).where(
            FacebookConnection.tg_id == auth_session.tg_id,
            FacebookConnection.facebook_user_id == facebook_user_id,
        )
    )
    connection = existing_result.scalar_one_or_none()
    if connection is None:
        connection = FacebookConnection(
            tg_id=auth_session.tg_id,
            facebook_user_id=facebook_user_id,
            user_access_token_encrypted="",
        )
        session.add(connection)

    connection.facebook_name = profile.get("name")
    connection.user_access_token_encrypted = encrypt_token(access_token)
    connection.token_expires_at = token_expires_at(token_data)
    connection.scopes = settings.facebook_scope_list
    connection.updated_at = datetime.now(timezone.utc)
    await session.flush()

    pages_with_ig: list[FacebookPage] = []
    for page_data in pages:
        page_id = str(page_data.get("id") or "")
        if not page_id:
            continue
        page_result = await session.execute(
            select(FacebookPage).where(
                FacebookPage.tg_id == auth_session.tg_id,
                FacebookPage.page_id == page_id,
            )
        )
        page = page_result.scalar_one_or_none()
        if page is None:
            page = FacebookPage(
                tg_id=auth_session.tg_id,
                facebook_connection_id=connection.id,
                page_id=page_id,
            )
            session.add(page)

        ig_account = page_data.get("instagram_business_account") or {}
        page.page_name = page_data.get("name")
        page.facebook_connection_id = connection.id
        page.page_access_token_encrypted = encrypt_token(page_data["access_token"]) if page_data.get("access_token") else None
        page.instagram_business_account_id = str(ig_account.get("id")) if ig_account.get("id") else None
        page.instagram_username = ig_account.get("username")
        page.raw_json = _safe_page_json(page_data)
        page.updated_at = datetime.now(timezone.utc)
        if page.instagram_business_account_id:
            pages_with_ig.append(page)

    existing_pages_result = await session.execute(select(FacebookPage).where(FacebookPage.tg_id == auth_session.tg_id))
    for page in existing_pages_result.scalars():
        page.is_selected = False

    selected_page = pages_with_ig[0] if pages_with_ig else None
    if selected_page is not None:
        selected_page.is_selected = True

    await session.commit()

    telegram = TelegramClient(settings)
    if selected_page is None:
        await telegram.send_message(
            auth_session.tg_id,
            (
                "Facebook подключен, но я не нашел Page, связанную с Instagram Business/Creator account. "
                "Проверь, что Instagram аккаунт профессиональный и привязан к Facebook Page."
            ),
        )
    elif len(pages_with_ig) == 1:
        await telegram.send_message(
            auth_session.tg_id,
            f"Facebook подключен. Выбрана Page: {selected_page.page_name or selected_page.page_id}, Instagram: @{selected_page.instagram_username or selected_page.instagram_business_account_id}.",
        )
    else:
        page_lines = "\n".join(
            f"- {page.page_name or page.page_id}: @{page.instagram_username or page.instagram_business_account_id}"
            for page in pages_with_ig
        )
        await telegram.send_message(
            auth_session.tg_id,
            (
                "Facebook подключен. Я нашел несколько Pages с Instagram Business/Creator account и пока выбрал первую.\n\n"
                f"{page_lines}\n\n"
                "TODO: ручной выбор Page будет добавлен позже."
            ),
        )

    return_link = ""
    if settings.telegram_bot_username:
        return_link = f'<p><a href="https://t.me/{settings.telegram_bot_username}">Return to Telegram</a></p>'
    return _html("Facebook connected", f"<p>You can return to Telegram. Competitor analysis is now available if a linked Instagram account was found.</p>{return_link}")


def _safe_page_json(page_data: dict) -> dict:
    cleaned = dict(page_data)
    if "access_token" in cleaned:
        cleaned["access_token"] = "[redacted]"
    return cleaned
