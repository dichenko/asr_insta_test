import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import AnalysisJob, AuthSession, InstagramAccount
from app.routes.connect import _html
from app.services.crypto import encrypt_token
from app.services.instagram import InstagramClient
from app.services.jobs import run_instagram_analysis_job
from app.services.oauth_cookie import OAUTH_SESSION_COOKIE, verify_auth_session_cookie
from app.services.telegram import TelegramClient

router = APIRouter(prefix="/auth/instagram")


def _html_delete_cookie(title: str, body: str) -> HTMLResponse:
    response = _html(title, body)
    response.delete_cookie(OAUTH_SESSION_COOKIE)
    return response


async def get_valid_auth_session_from_cookie(request: Request, session: AsyncSession) -> AuthSession | None:
    auth_session_id = verify_auth_session_cookie(request.cookies.get(OAUTH_SESSION_COOKIE))
    if auth_session_id is None:
        return None

    auth_session = await session.get(AuthSession, auth_session_id)
    if auth_session is None:
        return None
    if auth_session.status != "pending" or auth_session.used_at is not None:
        return None
    if auth_session.expires_at <= datetime.now(timezone.utc):
        auth_session.status = "expired"
        await session.commit()
        return None
    return auth_session


@router.get("/start", response_model=None)
async def start_instagram_oauth(request: Request, session: AsyncSession = Depends(get_session)) -> RedirectResponse | HTMLResponse:
    auth_session = await get_valid_auth_session_from_cookie(request, session)
    if auth_session is None:
        return _html_delete_cookie("Link expired", "<p>This connection link has expired. Please return to Telegram and request a new link.</p>")

    settings = get_settings()
    params = {
        "force_reauth": "true",
        "client_id": settings.instagram_client_id,
        "redirect_uri": settings.instagram_redirect_uri,
        "response_type": "code",
        "scope": ",".join(settings.scope_list),
    }
    oauth_url = "https://www.instagram.com/oauth/authorize?" + urllib.parse.urlencode(params)
    return RedirectResponse(oauth_url, status_code=302)


@router.get("/callback", response_class=HTMLResponse)
async def instagram_callback(
    background_tasks: BackgroundTasks,
    request: Request,
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    if error:
        return _html_delete_cookie("Instagram error", f"<p>Instagram authorization failed: {error_description or error}</p>")
    if not code:
        return _html_delete_cookie("Invalid callback", "<p>Authorization code is missing.</p>")

    auth_session = await get_valid_auth_session_from_cookie(request, session)
    if auth_session is None:
        return _html_delete_cookie("Invalid session", "<p>Connection session is missing or expired. Please return to Telegram and request a new link.</p>")

    auth_session.status = "used"
    auth_session.used_at = datetime.now(timezone.utc)
    await session.flush()

    settings = get_settings()
    instagram = InstagramClient(settings=settings)
    try:
        short_token_data = await instagram.exchange_code_for_short_lived_token(code)
    except Exception:
        auth_session.status = "failed"
        await session.commit()
        return _html_delete_cookie("Token exchange failed", "<p>Could not connect Instagram. Please return to Telegram and request a new link.</p>")

    access_token = short_token_data.get("access_token")
    if not access_token:
        auth_session.status = "failed"
        await session.commit()
        return _html_delete_cookie("Token exchange failed", "<p>Instagram did not return an access token.</p>")

    token_data = short_token_data
    long_lived_data = await instagram.exchange_for_long_lived_token(access_token)
    if long_lived_data and long_lived_data.get("access_token"):
        token_data = long_lived_data
        access_token = long_lived_data["access_token"]

    expires_in = token_data.get("expires_in")
    token_expires_at = None
    if isinstance(expires_in, int):
        token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    instagram_with_token = InstagramClient(access_token=access_token, settings=settings)
    try:
        profile = await instagram_with_token.fetch_instagram_profile()
    except Exception:
        profile = {"id": str(short_token_data.get("user_id") or "unknown"), "username": None}

    instagram_user_id = str(profile.get("id") or short_token_data.get("user_id") or uuid.uuid4())
    existing_result = await session.execute(
        select(InstagramAccount).where(
            InstagramAccount.tg_id == auth_session.tg_id,
            InstagramAccount.instagram_user_id == instagram_user_id,
        )
    )
    account = existing_result.scalar_one_or_none()
    if account is None:
        account = InstagramAccount(tg_id=auth_session.tg_id, instagram_user_id=instagram_user_id, access_token_encrypted="")
        session.add(account)

    account.username = profile.get("username")
    account.account_type = profile.get("account_type")
    account.media_count = profile.get("media_count")
    account.followers_count = profile.get("followers_count")
    account.access_token_encrypted = encrypt_token(access_token)
    account.token_expires_at = token_expires_at
    account.scopes = settings.scope_list

    await session.flush()
    job = AnalysisJob(tg_id=auth_session.tg_id, instagram_account_id=account.id)
    session.add(job)
    await session.commit()

    telegram = TelegramClient(settings)
    await telegram.send_message(auth_session.tg_id, "Instagram подключен. Собираю Insights и готовлю анализ. Обычно это занимает до 1-2 минут.")
    background_tasks.add_task(run_instagram_analysis_job, job.id)

    return_link = ""
    if settings.telegram_bot_username:
        return_link = f'<p><a href="https://t.me/{settings.telegram_bot_username}">Return to Telegram</a></p>'
    return _html_delete_cookie(
        "Instagram connected",
        f"<p>You can return to Telegram. The analysis will be sent to you there.</p>{return_link}",
    )
