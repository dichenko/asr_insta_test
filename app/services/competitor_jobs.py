import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.models import CompetitorAnalysisJob, CompetitorAnalysisReport, CompetitorSnapshot, FacebookConnection, FacebookPage
from app.services.competitor_metrics import build_competitor_summary
from app.services.crypto import decrypt_token
from app.services.facebook_graph import FacebookGraphClient
from app.services.llm import generate_competitor_report
from app.services.telegram import TelegramClient

logger = logging.getLogger(__name__)

NO_FACEBOOK_PAGE_MESSAGE = (
    "Для анализа конкурентов нужно подключить Facebook-аккаунт, у которого есть доступ к Facebook Page, "
    "связанной с Instagram Business/Creator account."
)
NO_COMPETITOR_DATA_MESSAGE = (
    "Не удалось получить данные по этому аккаунту. Возможные причины: аккаунт приватный, не Business/Creator, "
    "username указан неверно или Meta API не отдал данные."
)
LLM_FAILED_MESSAGE = "Данные конкурента получил, но не смог подготовить LLM-резюме. Попробуй позже."
WRONG_FLOW_MESSAGE = "Business Discovery was called through the wrong endpoint or wrong API flow. Expected graph.facebook.com with Facebook Login token."


class CompetitorDataUnavailableError(RuntimeError):
    pass


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        body = _safe_response_body(exc.response)
        message = f"HTTPStatusError: status_code={exc.response.status_code} body={body}"[:1000]
        if "Tried accessing nonexisting field (business_discovery)" in message:
            return WRONG_FLOW_MESSAGE
        return message
    if isinstance(exc, httpx.HTTPError):
        return exc.__class__.__name__
    message = _redact_text(str(exc))
    return f"{exc.__class__.__name__}: {message[:500]}"


def _safe_response_body(response: httpx.Response) -> Any:
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text[:1000]
    return _redact_value(body)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(message: str) -> str:
    message = re.sub(r"access_token=[^&\s]+", "access_token=[redacted]", message)
    message = re.sub(r"client_secret=[^&\s]+", "client_secret=[redacted]", message)
    message = re.sub(r"fb_exchange_token=[^&\s]+", "fb_exchange_token=[redacted]", message)
    message = re.sub(r"code=[^&\s]+", "code=[redacted]", message)
    message = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer [redacted]", message)
    message = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-[redacted]", message)
    message = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "[telegram-token-redacted]", message)
    return message


async def run_competitor_analysis_job(job_id: uuid.UUID) -> None:
    settings = get_settings()
    telegram = TelegramClient(settings)

    async with AsyncSessionLocal() as session:
        job = await session.get(CompetitorAnalysisJob, job_id)
        if job is None:
            logger.error("Competitor analysis job not found: %s", job_id)
            return
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            job = await session.get(CompetitorAnalysisJob, job_id)
            if job is None:
                raise RuntimeError("Competitor analysis job disappeared")
            if job.facebook_connection_id is None or job.facebook_page_id is None or not job.viewer_instagram_business_account_id:
                await _mark_failed(job_id, "Facebook connection/page is required for Business Discovery")
                await telegram.send_message(job.tg_id, NO_FACEBOOK_PAGE_MESSAGE)
                return

            connection = await session.get(FacebookConnection, job.facebook_connection_id)
            page = await session.get(FacebookPage, job.facebook_page_id)
            if connection is None or page is None:
                await _mark_failed(job_id, "Facebook connection or selected Page not found")
                await telegram.send_message(job.tg_id, NO_FACEBOOK_PAGE_MESSAGE)
                return

            tg_id = job.tg_id
            competitor_username = job.competitor_username
            viewer_ig_user_id = job.viewer_instagram_business_account_id
            user_access_token = decrypt_token(connection.user_access_token_encrypted)
            page_access_token = decrypt_token(page.page_access_token_encrypted) if page.page_access_token_encrypted else None

        api_errors: list[dict[str, Any]] = []
        try:
            response = await _fetch_business_discovery_with_fallback(
                settings=settings,
                user_access_token=user_access_token,
                page_access_token=page_access_token,
                viewer_ig_user_id=viewer_ig_user_id,
                competitor_username=competitor_username,
                api_errors=api_errors,
            )
            discovery = response.get("business_discovery")
            if not isinstance(discovery, dict) or not discovery:
                raise CompetitorDataUnavailableError("Meta API returned no business_discovery data")
        except Exception as exc:
            safe_message = _safe_error(exc)
            logger.warning(
                "Business Discovery failed: job_id=%s username=%s error=%s",
                job_id,
                competitor_username,
                safe_message,
            )
            api_errors.append({"scope": "business_discovery", "error": safe_message})
            await _mark_failed_with_snapshot(job_id, api_errors, NO_COMPETITOR_DATA_MESSAGE, telegram)
            return

        summary = build_competitor_summary(discovery)
        profile = _build_competitor_profile(discovery, competitor_username)
        media = _extract_media(discovery)
        recent_media_compact = _build_recent_media_compact(discovery)

        async with AsyncSessionLocal() as session:
            job = await session.get(CompetitorAnalysisJob, job_id)
            if job is None:
                raise RuntimeError("Competitor analysis job disappeared before saving result")

            snapshot = CompetitorSnapshot(
                job_id=job.id,
                tg_id=tg_id,
                competitor_username=competitor_username,
                competitor_account_id=None,
                profile_json=profile,
                media_json=media,
                summary_metrics_json=summary,
                raw_json=discovery,
                summary_json=summary,
                api_errors_json=api_errors,
            )
            connection = await session.get(FacebookConnection, job.facebook_connection_id)
            if connection:
                connection.last_used_at = datetime.now(timezone.utc)
            session.add(snapshot)
            await session.commit()

        llm_data = {
            "competitor_profile": profile,
            "summary_metrics": summary,
            "recent_media_compact": recent_media_compact,
            "api_errors": api_errors,
        }
        try:
            report_text = await generate_competitor_report(llm_data, settings=settings)
        except Exception as exc:
            safe_message = _safe_error(exc)
            logger.warning("Competitor LLM report failed: job_id=%s error=%s", job_id, safe_message)
            await _mark_failed(job_id, safe_message)
            await telegram.send_message(tg_id, LLM_FAILED_MESSAGE)
            return

        async with AsyncSessionLocal() as session:
            job = await session.get(CompetitorAnalysisJob, job_id)
            if job is None:
                raise RuntimeError("Competitor analysis job disappeared before saving report")
            report = CompetitorAnalysisReport(
                job_id=job.id,
                tg_id=tg_id,
                competitor_account_id=None,
                llm_model=settings.openai_model,
                report_text=report_text,
            )
            job.status = "success"
            job.finished_at = datetime.now(timezone.utc)
            session.add(report)
            await session.commit()

        await telegram.send_long_message(tg_id, report_text)

    except Exception as exc:
        safe_message = _safe_error(exc)
        logger.warning("Competitor analysis job failed: job_id=%s error=%s", job_id, safe_message)
        await _mark_failed(job_id, safe_message)
        async with AsyncSessionLocal() as session:
            job = await session.get(CompetitorAnalysisJob, job_id)
            if job is not None:
                await telegram.send_message(job.tg_id, NO_COMPETITOR_DATA_MESSAGE)


async def _fetch_business_discovery_with_fallback(
    *,
    settings,
    user_access_token: str,
    page_access_token: str | None,
    viewer_ig_user_id: str,
    competitor_username: str,
    api_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    user_client = FacebookGraphClient(access_token=user_access_token, settings=settings)
    try:
        return await user_client.fetch_business_discovery(
            viewer_ig_user_id=viewer_ig_user_id,
            competitor_username=competitor_username,
            media_limit=25,
        )
    except httpx.HTTPStatusError as exc:
        api_errors.append({"scope": "business_discovery_user_token", "error": _safe_error(exc)})
        if not page_access_token:
            raise

    page_client = FacebookGraphClient(access_token=page_access_token, settings=settings)
    return await page_client.fetch_business_discovery(
        viewer_ig_user_id=viewer_ig_user_id,
        competitor_username=competitor_username,
        media_limit=25,
    )


async def _mark_failed_with_snapshot(
    job_id: uuid.UUID,
    api_errors: list[dict[str, Any]],
    user_message: str,
    telegram: TelegramClient,
) -> None:
    error_message = api_errors[-1]["error"] if api_errors else "Business Discovery failed"
    async with AsyncSessionLocal() as session:
        job = await session.get(CompetitorAnalysisJob, job_id)
        if job is None:
            return
        job.status = "failed"
        job.error_message = error_message
        job.finished_at = datetime.now(timezone.utc)
        session.add(
            CompetitorSnapshot(
                job_id=job.id,
                tg_id=job.tg_id,
                competitor_username=job.competitor_username,
                competitor_account_id=None,
                profile_json=None,
                media_json=None,
                summary_metrics_json=None,
                raw_json=None,
                summary_json=None,
                api_errors_json=api_errors,
            )
        )
        await session.commit()
        await telegram.send_message(job.tg_id, user_message)


async def _mark_failed(job_id: uuid.UUID, error_message: str) -> None:
    async with AsyncSessionLocal() as session:
        job = await session.get(CompetitorAnalysisJob, job_id)
        if job is None:
            return
        job.status = "failed"
        job.error_message = error_message
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()


def _build_competitor_profile(discovery: dict[str, Any], fallback_username: str) -> dict[str, Any]:
    return {
        "id": discovery.get("id"),
        "username": discovery.get("username") or fallback_username,
        "name": discovery.get("name"),
        "biography": discovery.get("biography"),
        "website": discovery.get("website"),
        "profile_picture_url": discovery.get("profile_picture_url"),
    }


def _extract_media(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    media = discovery.get("media") or {}
    items = media.get("data") if isinstance(media, dict) else media
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _build_recent_media_compact(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in _extract_media(discovery):
        like_count = _optional_int(item.get("like_count")) or 0
        comments_count = _optional_int(item.get("comments_count")) or 0
        caption = item.get("caption")
        caption_short = caption[:300] + "..." if isinstance(caption, str) and len(caption) > 300 else caption
        compact.append(
            {
                "id": item.get("id"),
                "permalink": item.get("permalink"),
                "timestamp": item.get("timestamp"),
                "caption_short": caption_short,
                "media_type": item.get("media_type"),
                "media_product_type": item.get("media_product_type"),
                "like_count": like_count,
                "comments_count": comments_count,
                "interactions": like_count + comments_count,
            }
        )
    return compact


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
