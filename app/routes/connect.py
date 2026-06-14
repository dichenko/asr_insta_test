from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import AuthSession
from app.services.crypto import hash_state

router = APIRouter()


def _html(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 680px; margin: 56px auto; padding: 0 20px; line-height: 1.5; }}
    .button {{ display: inline-block; margin-top: 16px; padding: 12px 18px; background: #111; color: white; border: 0; border-radius: 6px; cursor: pointer; font: inherit; }}
  </style>
</head>
<body><h1>{title}</h1>{body}</body>
</html>"""
    )


async def get_valid_auth_session(session: AsyncSession, state: str) -> AuthSession | None:
    result = await session.execute(select(AuthSession).where(AuthSession.state_hash == hash_state(state)))
    auth_session = result.scalar_one_or_none()
    if auth_session is None:
        return None
    if auth_session.status != "pending" or auth_session.used_at is not None:
        return None
    if auth_session.expires_at <= datetime.now(timezone.utc):
        auth_session.status = "expired"
        await session.commit()
        return None
    return auth_session


@router.get("/connect", response_class=HTMLResponse)
async def connect_page(state: str = Query(...), session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    auth_session = await get_valid_auth_session(session, state)
    if auth_session is None:
        return _html("Link expired", "<p>This connection link has expired. Please return to Telegram and request a new link.</p>")

    settings = get_settings()
    start_url = f"{settings.connect_url_base}/auth/instagram/start?state={state}"

    return _html(
        "Подключение Instagram",
        f"""
<script src="https://telegram.org/js/telegram-web-app.js"></script>

<p>Сейчас откроется официальный экран Instagram.</p>
<p>Если Instagram попросит логин и пароль, это нормально: вход происходит на официальной странице Instagram, наш сервис не получает ваш пароль.</p>
<p>Если ссылка открылась внутри приложения Instagram или показывает странный экран, вернитесь назад и откройте эту страницу в Chrome или Safari.</p>

<button class="button" type="button" onclick="openInstagramAuth()">
  Продолжить через Instagram
</button>

<script>
function openInstagramAuth() {{
  const url = "{start_url}";

  if (window.Telegram && Telegram.WebApp && Telegram.WebApp.openLink) {{
    Telegram.WebApp.openLink(url);
  }} else {{
    window.location.href = url;
  }}
}}
</script>
""",
    )
