"""Quality filters for candidate and exported articles."""

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from cleaner import try_parse_published_datetime

RECENCY_DAYS = 7

ROUNDUP_KEYWORDS = (
    "f1 news today",
    "latest f1 news",
    "news roundup",
    "daily roundup",
    "round-up",
    "read more",
)

GP247_SENSITIVE_KEYWORDS = (
    "unplugged",
    "racing pride",
    "is lewis gay",
    "lgbtq",
    "pride ceo",
)

GP247_GAY_WORD_RE = re.compile(r"\bgay\b", re.I)


def _quality_haystack(title: str, url: str, clean_title: str = "") -> str:
    return f"{title} {clean_title} {url}".lower()


def _is_gp247_source(source_id: str = "", source_name: str = "") -> bool:
    sid = (source_id or "").strip()
    name = (source_name or "").lower()
    return (
        sid == "gp247-f1"
        or "grand prix 247" in name
        or "grandprix247" in name
    )


def get_roundup_filter_reason(
    title: str,
    url: str,
    clean_title: str = "",
) -> Optional[str]:
    haystack = _quality_haystack(title, url, clean_title)
    for keyword in ROUNDUP_KEYWORDS:
        if keyword in haystack:
            return "roundup_page"
    return None


def get_gp247_sensitive_filter_reason(
    title: str,
    url: str,
    source_id: str = "",
    source_name: str = "",
    clean_title: str = "",
) -> Optional[str]:
    if not _is_gp247_source(source_id, source_name):
        return None

    haystack = _quality_haystack(title, url, clean_title)
    for keyword in GP247_SENSITIVE_KEYWORDS:
        if keyword in haystack:
            return "manual_review_sensitive_or_long_interview"
    if GP247_GAY_WORD_RE.search(haystack):
        return "manual_review_sensitive_or_long_interview"
    return None


def get_crawl_quality_filter_reason(
    title: str,
    url: str,
    source_id: str = "",
    clean_title: str = "",
) -> Optional[str]:
    roundup_reason = get_roundup_filter_reason(title, url, clean_title)
    if roundup_reason:
        return roundup_reason
    return get_gp247_sensitive_filter_reason(
        title,
        url,
        source_id=source_id,
        clean_title=clean_title,
    )


def get_export_filter_reason(article: dict) -> str:
    title = article.get("title", "")
    clean_title = article.get("clean_title", "")
    url = article.get("url", "")
    source_name = article.get("source_name", "")
    source_id = article.get("source_id", "")

    roundup_reason = get_roundup_filter_reason(title, url, clean_title)
    if roundup_reason:
        return roundup_reason

    sensitive_reason = get_gp247_sensitive_filter_reason(
        title,
        url,
        source_id=source_id,
        source_name=source_name,
        clean_title=clean_title,
    )
    if sensitive_reason:
        return sensitive_reason

    if article.get("extraction_status") != "ok":
        return ""

    published_time = article.get("published_time", "")
    published_dt = try_parse_published_datetime(published_time)
    if published_dt is None:
        return ""

    now = datetime.now(timezone.utc)
    if published_dt.tzinfo is None:
        published_dt = published_dt.replace(tzinfo=timezone.utc)
    else:
        published_dt = published_dt.astimezone(timezone.utc)

    if now - published_dt > timedelta(days=RECENCY_DAYS):
        return "too_old"

    return ""
