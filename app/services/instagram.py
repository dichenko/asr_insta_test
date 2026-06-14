import logging
from datetime import date
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class InstagramClient:
    def __init__(self, access_token: str | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.access_token = access_token
        self.graph_base = f"https://graph.instagram.com/{self.settings.instagram_api_version}"

    async def exchange_code_for_short_lived_token(self, code: str) -> dict[str, Any]:
        payload = {
            "client_id": self.settings.instagram_client_id,
            "client_secret": self.settings.instagram_client_secret,
            "grant_type": "authorization_code",
            "redirect_uri": self.settings.instagram_redirect_uri,
            "code": code,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post("https://api.instagram.com/oauth/access_token", data=payload)
            response.raise_for_status()
            return response.json()

    async def exchange_for_long_lived_token(self, short_lived_token: str) -> dict[str, Any] | None:
        params = {
            "grant_type": "ig_exchange_token",
            "client_secret": self.settings.instagram_client_secret,
            "access_token": short_lived_token,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(f"{self.graph_base}/access_token", params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            logger.warning("Long-lived Instagram token exchange failed safely: %s", exc.__class__.__name__)
            return None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.access_token:
            raise RuntimeError("Instagram access token is required")
        merged = {"access_token": self.access_token}
        if params:
            merged.update(params)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.graph_base}/{path.lstrip('/')}", params=merged)
            response.raise_for_status()
            return response.json()

    async def _get_with_field_fallbacks(self, path: str, field_sets: list[list[str]], extra: dict[str, Any] | None = None) -> dict[str, Any]:
        last_exc: Exception | None = None
        for fields in field_sets:
            params = {"fields": ",".join(fields)}
            if extra:
                params.update(extra)
            try:
                return await self._get(path, params)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                logger.info("Instagram field set failed for %s; retrying with fewer fields", path)
        if last_exc:
            raise last_exc
        raise RuntimeError("No Instagram field sets configured")

    async def fetch_instagram_profile(self) -> dict[str, Any]:
        field_sets = [
            ["id", "user_id", "username", "account_type", "media_count", "followers_count", "follows_count", "profile_picture_url"],
            ["id", "username", "account_type", "media_count"],
            ["id", "username"],
        ]
        return await self._get_with_field_fallbacks("me", field_sets)

    async def fetch_recent_media(self, limit: int = 25) -> list[dict[str, Any]]:
        field_sets = [
            ["id", "caption", "media_type", "media_url", "thumbnail_url", "permalink", "timestamp", "like_count", "comments_count"],
            ["id", "caption", "media_type", "permalink", "timestamp", "like_count", "comments_count"],
            ["id", "caption", "media_type", "timestamp"],
            ["id"],
        ]
        data = await self._get_with_field_fallbacks("me/media", field_sets, {"limit": limit})
        return data.get("data", [])

    async def fetch_account_insights(self, since: date, until: date) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        metrics = ["reach", "views", "profile_views", "website_clicks", "accounts_engaged", "total_interactions", "follower_count"]
        result: dict[str, Any] = {}
        errors: list[dict[str, Any]] = []
        for metric in metrics:
            try:
                data = await self._get(
                    "me/insights",
                    {
                        "metric": metric,
                        "period": "day",
                        "since": since.isoformat(),
                        "until": until.isoformat(),
                    },
                )
                result[metric] = data.get("data", data)
            except httpx.HTTPStatusError as exc:
                errors.append({"scope": "account_insights", "metric": metric, "status_code": exc.response.status_code})
            except httpx.HTTPError as exc:
                errors.append({"scope": "account_insights", "metric": metric, "error": exc.__class__.__name__})
        return result, errors

    async def fetch_media_insights(self, media_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        metrics = ["reach", "views", "total_interactions", "saved", "shares"]
        result: dict[str, Any] = {"media_id": media_id, "metrics": {}}
        errors: list[dict[str, Any]] = []
        for metric in metrics:
            try:
                data = await self._get(f"{media_id}/insights", {"metric": metric})
                result["metrics"][metric] = data.get("data", data)
            except httpx.HTTPStatusError as exc:
                errors.append({"scope": "media_insights", "media_id": media_id, "metric": metric, "status_code": exc.response.status_code})
            except httpx.HTTPError as exc:
                errors.append({"scope": "media_insights", "media_id": media_id, "metric": metric, "error": exc.__class__.__name__})
        return result, errors
