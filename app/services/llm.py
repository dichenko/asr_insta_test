import json
from typing import Any

from openai import AsyncOpenAI

from app.config import Settings, get_settings


def _compact_data(data: dict[str, Any]) -> dict[str, Any]:
    cleaned = {
        "profile": data.get("profile") or {},
        "account_insights": data.get("account_insights") or {},
        "recent_media": data.get("recent_media") or [],
        "media_insights": data.get("media_insights") or [],
        "api_errors": data.get("api_errors") or [],
    }
    for item in cleaned["recent_media"]:
        caption = item.get("caption")
        if isinstance(caption, str) and len(caption) > 500:
            item["caption"] = caption[:500] + "..."
    return cleaned


async def generate_instagram_report(data: dict[str, Any], settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required")

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    payload = json.dumps(_compact_data(data), ensure_ascii=False, separators=(",", ":"))
    response = await client.responses.create(
        model=settings.openai_model,
        input=[
            {
                "role": "system",
                "content": (
                    "You are a senior Instagram marketing analyst. Analyze Instagram Professional account insights "
                    "and recent content performance. Be practical, specific, and honest. If data is missing, "
                    "say so clearly and do not invent numbers."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Analyze this Instagram account data and prepare a concise report in Russian.\n\n"
                    "Required structure:\n"
                    "1. Executive summary\n"
                    "2. What is working well\n"
                    "3. What is weak or risky\n"
                    "4. Best-performing content patterns\n"
                    "5. Audience and engagement observations\n"
                    "6. Concrete recommendations for the next 14 days\n"
                    "7. 10 content ideas based on the data\n"
                    "8. Data limitations\n\n"
                    "Use clear language. Avoid generic advice. If the dataset is small or incomplete, mention that.\n"
                    f"Here is the data:\n{payload}"
                ),
            },
        ],
    )
    return response.output_text.strip()
