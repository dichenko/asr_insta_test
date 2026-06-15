from collections import Counter
from datetime import datetime
from typing import Any


def build_competitor_summary(discovery: dict[str, Any]) -> dict[str, Any]:
    media = _extract_media(discovery)
    followers_count = _to_int(discovery.get("followers_count"))
    follows_count = _to_int(discovery.get("follows_count"))
    media_count = _to_int(discovery.get("media_count"))

    compact_posts = [_compact_post(item) for item in media]
    posts_analyzed = len(compact_posts)
    interactions = [post["interactions"] for post in compact_posts]
    total_interactions = sum(interactions)

    avg_likes = _average([post["like_count"] for post in compact_posts])
    avg_comments = _average([post["comments_count"] for post in compact_posts])
    avg_interactions = _average(interactions)
    avg_engagement_rate = None
    if followers_count and followers_count > 0 and avg_interactions is not None:
        avg_engagement_rate = round(avg_interactions / followers_count * 100, 2)

    parsed_timestamps = [_parse_timestamp(post.get("timestamp")) for post in compact_posts if post.get("timestamp")]
    parsed_timestamps = [timestamp for timestamp in parsed_timestamps if timestamp is not None]
    parsed_timestamps.sort(reverse=True)

    top_post = None
    if compact_posts:
        top_post = max(compact_posts, key=lambda post: post["interactions"])

    return {
        "posts_analyzed": posts_analyzed,
        "followers_count": followers_count,
        "follows_count": follows_count,
        "media_count": media_count,
        "avg_likes": avg_likes,
        "avg_comments": avg_comments,
        "avg_interactions": avg_interactions,
        "avg_engagement_rate": avg_engagement_rate,
        "posting_frequency_text": _posting_frequency_text(parsed_timestamps, posts_analyzed),
        "top_post_by_interactions": top_post,
        "media_type_distribution": dict(Counter(post.get("media_type") or "unknown" for post in compact_posts)),
        "latest_post_at": parsed_timestamps[0].isoformat() if parsed_timestamps else None,
        "oldest_post_at": parsed_timestamps[-1].isoformat() if parsed_timestamps else None,
        "total_interactions": total_interactions,
    }


def _extract_media(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    media = discovery.get("media") or {}
    if isinstance(media, dict):
        data = media.get("data") or []
        return [item for item in data if isinstance(item, dict)]
    if isinstance(media, list):
        return [item for item in media if isinstance(item, dict)]
    return []


def _compact_post(item: dict[str, Any]) -> dict[str, Any]:
    like_count = _to_int(item.get("like_count")) or 0
    comments_count = _to_int(item.get("comments_count")) or 0
    caption = item.get("caption")
    caption_short = caption[:300] + "..." if isinstance(caption, str) and len(caption) > 300 else caption
    return {
        "id": item.get("id"),
        "permalink": item.get("permalink"),
        "timestamp": item.get("timestamp"),
        "caption_short": caption_short,
        "media_type": item.get("media_type"),
        "media_product_type": item.get("media_product_type"),
        "like_count": like_count,
        "comments_count": comments_count,
        "interactions": like_count + comments_count,
    }


def _posting_frequency_text(timestamps: list[datetime], posts_analyzed: int) -> str:
    if len(timestamps) < 2:
        return "Недостаточно данных для оценки"
    newest = timestamps[0]
    oldest = timestamps[-1]
    days = max((newest - oldest).days, 1)
    posts_per_week = round(posts_analyzed / days * 7, 1)
    return f"{posts_analyzed} постов за {days} дня, примерно {posts_per_week} поста в неделю"


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _average(values: list[int]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
