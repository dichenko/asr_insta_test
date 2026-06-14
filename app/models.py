import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(Text)
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tg_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("telegram_users.tg_id", ondelete="CASCADE"), nullable=False, index=True)
    state_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InstagramAccount(Base):
    __tablename__ = "instagram_accounts"
    __table_args__ = (UniqueConstraint("tg_id", "instagram_user_id", name="uq_instagram_account_tg_user"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("telegram_users.tg_id", ondelete="CASCADE"), nullable=False, index=True)
    instagram_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str | None] = mapped_column(Text)
    account_type: Mapped[str | None] = mapped_column(Text)
    media_count: Mapped[int | None] = mapped_column(Integer)
    followers_count: Mapped[int | None] = mapped_column(Integer)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_analysis_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    reports: Mapped[list["AnalysisReport"]] = relationship(back_populates="instagram_account")


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tg_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("telegram_users.tg_id", ondelete="CASCADE"), nullable=False, index=True)
    instagram_account_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("instagram_accounts.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InsightSnapshot(Base):
    __tablename__ = "insight_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instagram_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("instagram_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_json: Mapped[dict | None] = mapped_column(JSONB)
    account_insights_json: Mapped[dict | None] = mapped_column(JSONB)
    media_json: Mapped[list[dict] | None] = mapped_column(JSONB)
    media_insights_json: Mapped[list[dict] | None] = mapped_column(JSONB)
    api_errors_json: Mapped[list[dict] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisReport(Base):
    __tablename__ = "analysis_reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("analysis_jobs.id", ondelete="CASCADE"), nullable=False)
    tg_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("telegram_users.tg_id", ondelete="CASCADE"), nullable=False, index=True)
    instagram_account_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("instagram_accounts.id", ondelete="SET NULL"))
    llm_model: Mapped[str] = mapped_column(Text, nullable=False)
    report_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    instagram_account: Mapped[InstagramAccount | None] = relationship(back_populates="reports")
