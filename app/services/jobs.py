import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.models import AnalysisJob, AnalysisReport, InsightSnapshot, InstagramAccount
from app.services.crypto import decrypt_token
from app.services.instagram import InstagramClient
from app.services.llm import generate_instagram_report
from app.services.telegram import TelegramClient

logger = logging.getLogger(__name__)


def _safe_error(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {str(exc)[:500]}"


async def run_instagram_analysis_job(job_id: uuid.UUID) -> None:
    settings = get_settings()
    telegram = TelegramClient(settings)

    async with AsyncSessionLocal() as session:
        job = await session.get(AnalysisJob, job_id)
        if job is None:
            logger.error("Analysis job not found: %s", job_id)
            return
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await session.commit()

    try:
        async with AsyncSessionLocal() as session:
            job = await session.get(AnalysisJob, job_id)
            if job is None or job.instagram_account_id is None:
                raise RuntimeError("Analysis job has no Instagram account")
            account = await session.get(InstagramAccount, job.instagram_account_id)
            if account is None:
                raise RuntimeError("Instagram account not found")

            access_token = decrypt_token(account.access_token_encrypted)

        instagram = InstagramClient(access_token=access_token, settings=settings)
        until = date.today()
        since = until - timedelta(days=30)

        profile: dict[str, Any] = {}
        recent_media: list[dict[str, Any]] = []
        account_insights: dict[str, Any] = {}
        media_insights: list[dict[str, Any]] = []
        api_errors: list[dict[str, Any]] = []

        try:
            profile = await instagram.fetch_instagram_profile()
        except Exception as exc:
            api_errors.append({"scope": "profile", "error": _safe_error(exc)})

        try:
            recent_media = await instagram.fetch_recent_media(limit=25)
        except Exception as exc:
            api_errors.append({"scope": "recent_media", "error": _safe_error(exc)})

        try:
            account_insights, errors = await instagram.fetch_account_insights(since=since, until=until)
            api_errors.extend(errors)
        except Exception as exc:
            api_errors.append({"scope": "account_insights", "error": _safe_error(exc)})

        for item in recent_media[:10]:
            media_id = item.get("id")
            if not media_id:
                continue
            try:
                item_insights, errors = await instagram.fetch_media_insights(str(media_id))
                media_insights.append(item_insights)
                api_errors.extend(errors)
            except Exception as exc:
                api_errors.append({"scope": "media_insights", "media_id": media_id, "error": _safe_error(exc)})

        llm_data = {
            "profile": profile,
            "account_insights": account_insights,
            "recent_media": recent_media,
            "media_insights": media_insights,
            "api_errors": api_errors,
        }
        report_text = await generate_instagram_report(llm_data, settings=settings)

        async with AsyncSessionLocal() as session:
            job = await session.get(AnalysisJob, job_id)
            if job is None or job.instagram_account_id is None:
                raise RuntimeError("Analysis job disappeared before saving result")
            snapshot = InsightSnapshot(
                instagram_account_id=job.instagram_account_id,
                profile_json=profile,
                account_insights_json=account_insights,
                media_json=recent_media,
                media_insights_json=media_insights,
                api_errors_json=api_errors,
            )
            report = AnalysisReport(
                job_id=job.id,
                tg_id=job.tg_id,
                instagram_account_id=job.instagram_account_id,
                llm_model=settings.openai_model,
                report_text=report_text,
            )
            account = await session.get(InstagramAccount, job.instagram_account_id)
            if account:
                account.last_analysis_at = datetime.now(timezone.utc)
            job.status = "success"
            job.finished_at = datetime.now(timezone.utc)
            session.add_all([snapshot, report])
            await session.commit()

        await telegram.send_message(job.tg_id, "Готово. Вот анализ твоего Instagram:")
        await telegram.send_long_message(job.tg_id, report_text)
        await telegram.send_message(
            job.tg_id,
            (
                "Можешь прислать ссылку на аккаунт конкурента или его @username — "
                "я соберу открытые данные и сделаю краткий анализ.\n\n"
                "Пример: @example или https://www.instagram.com/example/"
            ),
        )

    except Exception as exc:
        safe_message = _safe_error(exc)
        logger.exception("Analysis job failed: %s", job_id)
        async with AsyncSessionLocal() as session:
            job = await session.get(AnalysisJob, job_id)
            if job is not None:
                job.status = "failed"
                job.error_message = safe_message
                job.finished_at = datetime.now(timezone.utc)
                await session.commit()
                await telegram.send_message(
                    job.tg_id,
                    "Не удалось завершить анализ. Возможные причины: аккаунт не является профессиональным, "
                    "не хватает разрешений или Instagram временно не вернул данные.",
                )
