from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "production"
    app_base_url: str = "https://api.instagram-ai.liven8n.site"
    port: int = 8000
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://instagram_ai:password@postgres:5432/instagram_ai"

    telegram_bot_token: str = ""
    telegram_polling_enabled: bool = True
    telegram_polling_timeout: int = 30
    telegram_bot_username: str | None = None

    instagram_client_id: str = "1034890592555471"
    instagram_client_secret: str = ""
    instagram_redirect_uri: str = "https://api.instagram-ai.liven8n.site/auth/instagram/callback"
    instagram_scopes: str = "instagram_business_basic,instagram_business_manage_insights"
    instagram_api_version: str = "v23.0"

    facebook_app_id: str = ""
    facebook_app_secret: str = ""
    facebook_redirect_uri: str = "https://api.instagram-ai.liven8n.site/auth/facebook/callback"
    facebook_scopes: str = "public_profile,pages_show_list,pages_read_engagement,instagram_basic,instagram_manage_insights"
    facebook_api_version: str = "v23.0"

    openai_api_key: str = ""
    openai_model: str = "gpt-5.4"

    token_encryption_key: str = Field(default="")

    @property
    def connect_url_base(self) -> str:
        return self.app_base_url.rstrip("/")

    @property
    def scope_list(self) -> list[str]:
        return [scope.strip() for scope in self.instagram_scopes.split(",") if scope.strip()]

    @property
    def facebook_scope_list(self) -> list[str]:
        return [scope.strip() for scope in self.facebook_scopes.split(",") if scope.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
