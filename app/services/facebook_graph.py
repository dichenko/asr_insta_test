from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import Settings, get_settings


class FacebookGraphClient:
    def __init__(self, access_token: str | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.access_token = access_token
        self.graph_base = f"https://graph.facebook.com/{self.settings.facebook_api_version}"

    async def exchange_code_for_token(self, code: str) -> dict[str, Any]:
        if not self.settings.facebook_app_id or not self.settings.facebook_app_secret:
            raise RuntimeError("FACEBOOK_APP_ID and FACEBOOK_APP_SECRET are required")
        params = {
            "client_id": self.settings.facebook_app_id,
            "redirect_uri": self.settings.facebook_redirect_uri,
            "client_secret": self.settings.facebook_app_secret,
            "code": code,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.graph_base}/oauth/access_token", params=params)
            response.raise_for_status()
            return response.json()

    async def exchange_for_long_lived_token(self, token: str) -> dict[str, Any]:
        if not self.settings.facebook_app_id or not self.settings.facebook_app_secret:
            raise RuntimeError("FACEBOOK_APP_ID and FACEBOOK_APP_SECRET are required")
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": self.settings.facebook_app_id,
            "client_secret": self.settings.facebook_app_secret,
            "fb_exchange_token": token,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.graph_base}/oauth/access_token", params=params)
            response.raise_for_status()
            return response.json()

    async def fetch_me(self) -> dict[str, Any]:
        return await self._get("me", {"fields": "id,name"})

    async def fetch_pages(self) -> list[dict[str, Any]]:
        data = await self._get(
            "me/accounts",
            {"fields": "id,name,access_token,instagram_business_account{id,username}"},
        )
        return [item for item in data.get("data", []) if isinstance(item, dict)]

    async def fetch_business_discovery(
        self,
        viewer_ig_user_id: str,
        competitor_username: str,
        media_limit: int = 25,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(media_limit, 50))
        fields = (
            f"business_discovery.username({competitor_username})"
            "{id,username,name,biography,website,profile_picture_url,"
            "followers_count,follows_count,media_count,"
            f"media.limit({safe_limit})"
            "{id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count}}"
        )
        return await self._get(viewer_ig_user_id, {"fields": fields})

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.access_token:
            raise RuntimeError("Facebook access token is required")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.graph_base}/{path.lstrip('/')}",
                params=params or {},
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            response.raise_for_status()
            return response.json()


def token_expires_at(token_data: dict[str, Any]) -> datetime | None:
    expires_in = token_data.get("expires_in")
    if isinstance(expires_in, int):
        return datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return None
