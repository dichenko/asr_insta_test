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
                    "Подготовь очень краткое тестовое summary на русском языке.\n\n"
                    "Верни только 4 пункта:\n"
                    "1. Название аккаунта: username или id, если username отсутствует.\n"
                    "2. Количество постов: сколько recent_media передано в данных.\n"
                    "3. Частота публикаций: оцени по timestamp последних постов, если timestamp есть; если данных мало, напиши что оценка ограничена.\n"
                    "4. Самый популярный пост: выбери пост с максимальным like_count + comments_count, укажи ссылку permalink. Если permalink нет, укажи id поста.\n\n"
                    "Не добавляй общие рекомендации. Не выдумывай числа и ссылки. Если данных нет, явно напиши что поле недоступно.\n"
                    f"Here is the data:\n{payload}"
                ),
            },
        ],
    )
    return response.output_text.strip()
