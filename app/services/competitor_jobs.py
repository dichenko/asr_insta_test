import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.models import (
    CompetitorAccount,
    CompetitorAnalysisJob,
    CompetitorAnalysisReport,
    CompetitorSnapshot,
    InstagramAccount,
)
from app.services.competitor_metrics import build_competitor_summary
from app.services.crypto import decrypt_token
from app.services.instagram import InstagramClient
from app.services.llm import generate_competitor_report
from app.services.telegram import TelegramClient

logger = logging.getLogger(__name__)

NO_COMPETITOR_DATA_MESSAGE = (
    "Не удалось получить данные по этому аккаунту. Возможно, аккаунт приватный, "
    "не Business/Creator или Instagram не отдал данные через API."
)
LLM_FAILED_MESSAGE = (
    "Данные конкурента получил, но не смог подготовить LLM-резюме. Попробуй позже."
)


class CompetitorDataUnavailableError(RuntimeError):
    pass


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTPStatusError: status_code={exc.response.status_code} body={_safe_response_body(exc.response)}"[:1000]
    if isinstance(exc, httpx.HTTPError):
        return exc.__class__.__name__
    message = str(exc)
    message = _redact_text(message)
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
            account = await session.get(InstagramAccount, job.viewer_instagram_account_id)
            if account is None:
                raise RuntimeError("Viewer Instagram account not found")

            tg_id = job.tg_id
            competitor_username = job.competitor_username
            viewer_account_id = job.viewer_instagram_account_id
            viewer_ig_user_id = account.instagram_user_id
            access_token = decrypt_token(account.access_token_encrypted)

        instagram = InstagramClient(access_token=access_token, settings=settings)
        api_errors: list[dict[str, Any]] = []

        try:
            response = await instagram.fetch_business_discovery(
                viewer_ig_user_id=viewer_ig_user_id,
                competitor_username=competitor_username,
                limit=25,
            )
            discovery = response.get("business_discovery")
            if not isinstance(discovery, dict) or not discovery:
                raise CompetitorDataUnavailableError("Instagram API returned no business_discovery data")
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
        recent_media_compact = _build_recent_media_compact(discovery)

        async with AsyncSessionLocal() as session:
            job = await session.get(CompetitorAnalysisJob, job_id)
            if job is None:
                raise RuntimeError("Competitor analysis job disappeared before saving result")

            competitor = await _upsert_competitor_account(
                session=session,
                tg_id=tg_id,
                viewer_instagram_account_id=viewer_account_id,
                username=competitor_username,
                discovery=discovery,
            )
            await session.flush()

            snapshot = CompetitorSnapshot(
                job_id=job.id,
                competitor_account_id=competitor.id,
                raw_json=discovery,
                summary_json=summary,
                api_errors_json=api_errors,
            )
            competitor.last_analysis_at = datetime.now(timezone.utc)
            session.add(snapshot)
            await session.commit()

        llm_data = {
            "competitor_profile": _build_competitor_profile(discovery, competitor_username),
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
            competitor_result = await session.execute(
                select(CompetitorAccount).where(
                    CompetitorAccount.tg_id == tg_id,
                    CompetitorAccount.viewer_instagram_account_id == viewer_account_id,
                    CompetitorAccount.username == competitor_username,
                )
            )
            competitor = competitor_result.scalar_one_or_none()
            report = CompetitorAnalysisReport(
                job_id=job.id,
                tg_id=tg_id,
                competitor_account_id=competitor.id if competitor else None,
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


async def _upsert_competitor_account(
    *,
    session,
    tg_id: int,
    viewer_instagram_account_id: int,
    username: str,
    discovery: dict[str, Any],
) -> CompetitorAccount:
    result = await session.execute(
        select(CompetitorAccount).where(
            CompetitorAccount.tg_id == tg_id,
            CompetitorAccount.viewer_instagram_account_id == viewer_instagram_account_id,
            CompetitorAccount.username == username,
        )
    )
    competitor = result.scalar_one_or_none()
    if competitor is None:
        competitor = CompetitorAccount(
            tg_id=tg_id,
            viewer_instagram_account_id=viewer_instagram_account_id,
            username=username,
        )
        session.add(competitor)

    competitor.instagram_user_id = _optional_str(discovery.get("id"))
    competitor.name = _optional_str(discovery.get("name"))
    competitor.biography = _optional_str(discovery.get("biography"))
    competitor.website = _optional_str(discovery.get("website"))
    competitor.profile_picture_url = _optional_str(discovery.get("profile_picture_url"))
    competitor.followers_count = _optional_int(discovery.get("followers_count"))
    competitor.follows_count = _optional_int(discovery.get("follows_count"))
    competitor.media_count = _optional_int(discovery.get("media_count"))
    return competitor


async def _mark_failed_with_snapshot(
    job_id: uuid.UUID,
    api_errors: list[dict[str, Any]],
    user_message: str,
    telegram: TelegramClient,
) -> None:
    error_message = api_errors[0]["error"] if api_errors else "Business Discovery failed"
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
                competitor_account_id=None,
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


def _build_recent_media_compact(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    media = discovery.get("media") or {}
    items = media.get("data") if isinstance(media, dict) else media
    if not isinstance(items, list):
        return []
    compact: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
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


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
