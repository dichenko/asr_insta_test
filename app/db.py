from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Base

settings = get_settings()

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE competitor_analysis_jobs ALTER COLUMN viewer_instagram_account_id DROP NOT NULL"))
        await conn.execute(text("ALTER TABLE competitor_analysis_jobs ADD COLUMN IF NOT EXISTS facebook_connection_id BIGINT"))
        await conn.execute(text("ALTER TABLE competitor_analysis_jobs ADD COLUMN IF NOT EXISTS facebook_page_id BIGINT"))
        await conn.execute(text("ALTER TABLE competitor_analysis_jobs ADD COLUMN IF NOT EXISTS viewer_instagram_business_account_id TEXT"))
        await conn.execute(text("ALTER TABLE competitor_snapshots ADD COLUMN IF NOT EXISTS tg_id BIGINT"))
        await conn.execute(text("ALTER TABLE competitor_snapshots ADD COLUMN IF NOT EXISTS competitor_username TEXT"))
        await conn.execute(text("ALTER TABLE competitor_snapshots ADD COLUMN IF NOT EXISTS profile_json JSONB"))
        await conn.execute(text("ALTER TABLE competitor_snapshots ADD COLUMN IF NOT EXISTS media_json JSONB"))
        await conn.execute(text("ALTER TABLE competitor_snapshots ADD COLUMN IF NOT EXISTS summary_metrics_json JSONB"))


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
