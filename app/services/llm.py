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


def _compact_competitor_data(data: dict[str, Any]) -> dict[str, Any]:
    recent_media = data.get("recent_media_compact") or []
    compact_media: list[dict[str, Any]] = []
    for item in recent_media[:25]:
        if not isinstance(item, dict):
            continue
        caption_short = item.get("caption_short")
        if isinstance(caption_short, str) and len(caption_short) > 300:
            caption_short = caption_short[:300] + "..."
        compact_media.append(
            {
                "id": item.get("id"),
                "permalink": item.get("permalink"),
                "timestamp": item.get("timestamp"),
                "caption_short": caption_short,
                "media_type": item.get("media_type"),
                "media_product_type": item.get("media_product_type"),
                "like_count": item.get("like_count"),
                "comments_count": item.get("comments_count"),
                "interactions": item.get("interactions"),
            }
        )
    return {
        "competitor_profile": data.get("competitor_profile") or {},
        "summary_metrics": data.get("summary_metrics") or {},
        "recent_media_compact": compact_media,
        "api_errors": data.get("api_errors") or [],
    }


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


async def generate_competitor_report(data: dict[str, Any], settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required")

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    payload = json.dumps(_compact_competitor_data(data), ensure_ascii=False, separators=(",", ":"))
    response = await client.responses.create(
        model=settings.openai_model,
        input=[
            {
                "role": "system",
                "content": (
                    "Ты senior Instagram marketing analyst. Отвечай на русском языке. Анализируй только "
                    "открытые данные конкурента, возвращенные официальным Instagram API. Не называй эти "
                    "данные приватными Insights, не придумывай числа, ссылки или причины отсутствия данных."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Подготовь очень короткое MVP-summary по конкуренту.\n\n"
                    "Структура ответа строго из 5 пунктов:\n"
                    "1. Аккаунт: @username — name, если доступно.\n"
                    "2. Размер аккаунта: followers_count и media_count.\n"
                    "3. Публикации: сколько постов проанализировано и примерная частота.\n"
                    "4. Самый популярный пост: permalink + likes/comments/interactions.\n"
                    "5. Короткий вывод: 2-3 предложения о том, какой контент сработал лучше всего по доступным данным.\n\n"
                    "Явно скажи, что анализ основан на открытых данных, возвращенных Instagram API. "
                    "Если данных не хватает, напиши это прямо. Не добавляй длинные рекомендации.\n"
                    f"Данные:\n{payload}"
                ),
            },
        ],
    )
    return response.output_text.strip()
