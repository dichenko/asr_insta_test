import urllib.parse

from app.config import Settings, get_settings


def build_instagram_oauth_url(state: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    params = {
        "force_reauth": "true",
        "client_id": settings.instagram_client_id,
        "redirect_uri": settings.instagram_redirect_uri,
        "response_type": "code",
        "scope": ",".join(settings.scope_list),
        "state": state,
    }
    return "https://api.instagram.com/oauth/authorize?" + urllib.parse.urlencode(params)
