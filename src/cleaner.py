"""URL cleaning and title normalization utilities."""

import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from dateutil import parser as date_parser

logger = logging.getLogger(__name__)

TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "gclsrc",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "_ga",
        "_gl",
        "ref",
        "ref_src",
        "ref_url",
        "spm",
        "igshid",
        "si",
    }
)

TITLE_PREFIX_NOISE = (
    "Tech",
    "LATEST",
    "RN365 Podcast",
    "RacingNews365 Review",
)

BLOCKED_SLUGS = frozenset(
    {
        "terms-conditions",
        "privacy-policy",
        "cookie-policy",
        "cookies-policy",
        "contact-us",
        "about-us",
        "subscribe",
        "newsletter",
        "tickets",
        "standings",
        "calendar",
        "drivers",
        "teams",
        "videos",
        "gallery",
        "privacy",
        "terms",
        "cookies",
        "contact",
        "about",
    }
)

RN365_DATE_SUFFIX_RE = re.compile(
    r"\d{1,2}\s+"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2}:\d{2}\s*(?:AM|PM)\d*\s*$",
    re.IGNORECASE,
)

DUPLICATE_JUNK_RE = re.compile(r"Formula\s*\d+\s*d", re.IGNORECASE)

COMPACT_DATE_RE = re.compile(r"^\d{8}$")
ISO_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

GENERAL_RAW_TEXT_TRUNCATE_MARKERS = (
    "Related Articles",
    "More News",
    "Latest News",
    "Most read",
    "Join the conversation",
    "Download the F1 calendar",
    "Subscribe",
    "Comments",
    "Next Up",
    "Share this article",
    "Recommended",
    "You may also like",
)

RN365_RAW_TEXT_TRUNCATE_MARKERS = GENERAL_RAW_TEXT_TRUNCATE_MARKERS + (
    "Never miss a moment",
    "Keep up to date with the latest Formula 1 news",
    "Follow RacingNews365",
    "In this article",
    "Also interesting",
    "Get the latest F1 news from RacingNews365",
)

FORMULA1_RAW_TEXT_TRUNCATE_MARKERS = (
    "Related Articles",
    "Next Up",
    "More News",
    "Latest News",
)

MOTORSPORT_RAW_TEXT_TRUNCATE_MARKERS = (
    "We want your opinion",
    "Share Or Save This Story",
    "Top Comments",
    "Comments",
    "Subscribe",
)


def is_tracking_param(key: str) -> bool:
    lower = key.lower()
    if lower in TRACKING_PARAMS:
        return True
    if lower.startswith("utm_"):
        return True
    if lower in ("utm", "campaign", "source", "medium"):
        return True
    return False


def clean_url(url: str) -> str:
    if not url or not url.strip():
        return url

    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return url.strip()

    query = parse_qs(parsed.query, keep_blank_values=False)
    cleaned_query = {k: v for k, v in query.items() if not is_tracking_param(k)}
    new_query = urlencode(cleaned_query, doseq=True)

    path = parsed.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.params,
            new_query,
            "",
        )
    )


def dedupe_key(url: str) -> str:
    cleaned = clean_url(url)
    parsed = urlparse(cleaned)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.netloc.lower()}{path}"


def _path_segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def is_blocked_slug(slug: str) -> bool:
    if not slug:
        return True
    normalized = slug.lower().replace("_", "-").strip()
    if normalized in BLOCKED_SLUGS:
        return True
    for blocked in BLOCKED_SLUGS:
        if normalized == blocked or normalized.startswith(f"{blocked}-"):
            return True
    return False


def blocked_slug_reason(url: str) -> Optional[str]:
    """Return a reason string if any URL path segment is a blocked slug."""
    for segment in _path_segments(urlparse(url).path):
        if is_blocked_slug(segment):
            return f"blocked_slug:{segment.lower()}"
    return None


def _collapse_duplicate_title(title: str) -> str:
    if len(title) < 40:
        return title

    lowered = title.lower()
    probe_len = min(24, len(title) // 3)
    probe = lowered[:probe_len]
    repeat_at = lowered.find(probe, probe_len)
    if repeat_at > probe_len and repeat_at < len(title) - 15:
        return title[:repeat_at].strip()

    cleaned = DUPLICATE_JUNK_RE.sub("", title).strip()
    return cleaned or title


def normalize_title(title: str) -> str:
    """
    Clean list-page title noise while preserving original casing where possible.
    """
    if not title:
        return ""

    text = " ".join(title.split())

    for prefix in TITLE_PREFIX_NOISE:
        if text.startswith(prefix):
            remainder = text[len(prefix):]
            if not remainder or remainder[0].isspace():
                text = remainder.strip()
            elif len(prefix) >= 4:
                text = remainder.strip()

    text = RN365_DATE_SUFFIX_RE.sub("", text).strip()
    text = _collapse_duplicate_title(text)
    return " ".join(text.split())


def title_from_slug(url: str) -> str:
    """
    Build a title from the article slug. Returns empty string for blocked slugs.
    """
    segments = _path_segments(urlparse(url).path)
    if not segments:
        return ""

    slug = segments[-1]
    if slug.isdigit() and len(segments) >= 2:
        slug = segments[-2]

    if is_blocked_slug(slug):
        return ""

    slug_text = slug.replace("-", " ").replace("_", " ")
    return normalize_title(slug_text)


def normalize_datetime(value: Optional[str]) -> Optional[str]:
    """
    Normalize extracted publish times to ISO-style strings when possible.
    Returns None for empty input; returns the original string when parsing fails.
    """
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    if COMPACT_DATE_RE.fullmatch(text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"

    try:
        dt = date_parser.parse(text)
    except (ValueError, TypeError, OverflowError):
        return text

    if ISO_DATE_ONLY_RE.fullmatch(text):
        return dt.date().isoformat()

    if re.fullmatch(
        r"\d{1,2}\s+"
        r"(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        r"\s+\d{4}",
        text,
        flags=re.IGNORECASE,
    ):
        return dt.date().isoformat()

    if re.fullmatch(
        r"(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        r"\s+\d{1,2},\s+\d{4}",
        text,
        flags=re.IGNORECASE,
    ):
        return dt.date().isoformat()

    if text.endswith("Z") and dt.tzinfo is not None:
        utc_dt = dt.astimezone(timezone.utc)
        normalized = utc_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        if "." not in text and "." in normalized:
            normalized = normalized.split(".")[0] + "Z"
        return normalized

    return dt.isoformat()


def try_parse_published_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse a publish time string into datetime; return None when parsing fails."""
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    normalized = normalize_datetime(text)
    for candidate in (normalized, text):
        if not candidate:
            continue
        try:
            return date_parser.parse(candidate)
        except (ValueError, TypeError, OverflowError):
            continue
    return None


def _truncate_markers_for_source(source_name: Optional[str]) -> tuple[str, ...]:
    name = (source_name or "").lower()
    if "racingnews365" in name or "rn365" in name:
        return RN365_RAW_TEXT_TRUNCATE_MARKERS
    if "formula 1" in name or "formula1" in name:
        return FORMULA1_RAW_TEXT_TRUNCATE_MARKERS
    if "motorsport" in name or "autosport" in name:
        return GENERAL_RAW_TEXT_TRUNCATE_MARKERS + MOTORSPORT_RAW_TEXT_TRUNCATE_MARKERS
    return GENERAL_RAW_TEXT_TRUNCATE_MARKERS


def _line_should_truncate(line: str, marker: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    marker_lower = marker.lower()
    if marker_lower not in lowered:
        return False
    if lowered == marker_lower:
        return True
    if lowered.startswith(marker_lower):
        return True
    if len(stripped) <= max(80, len(marker) * 3):
        return True
    return False


def _truncate_at_markers(text: str, markers: tuple[str, ...]) -> str:
    lines = text.splitlines()
    output: list[str] = []
    for line in lines:
        if any(_line_should_truncate(line, marker) for marker in markers):
            break
        output.append(line)
    return "\n".join(output)


def _dedupe_repeated_lines(lines: list[str], min_repeat: int = 3) -> list[str]:
    counts = Counter(line.strip() for line in lines if line.strip())
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append("")
            continue
        if counts[stripped] >= min_repeat:
            if stripped in seen:
                continue
            seen.add(stripped)
        result.append(line)
    return result


def _remove_consecutive_duplicate_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    previous: Optional[str] = None
    for line in lines:
        key = line.strip()
        if key and key == previous:
            continue
        result.append(line)
        previous = key if key else previous
    return result


def _collapse_blank_lines(text: str) -> str:
    lines = text.splitlines()
    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and previous_blank:
            continue
        collapsed.append(line.rstrip())
        previous_blank = is_blank
    return "\n".join(collapsed).strip()


def clean_raw_text(
    text: Optional[str],
    source_name: Optional[str] = None,
) -> str:
    """
    Remove obvious site-template noise from extracted article bodies.
    Never raises; returns an empty string for empty input.
    """
    if not text:
        return ""

    try:
        working = str(text).replace("\r\n", "\n").replace("\r", "\n")
        lines = working.splitlines()
        source_lower = (source_name or "").lower()

        if "motorsport" in source_lower or "autosport" in source_lower:
            lines = _dedupe_repeated_lines(lines, min_repeat=3)
            lines = [
                line
                for line in lines
                if not line.strip().startswith("Photos from ")
            ]

        working = "\n".join(lines)
        working = _truncate_at_markers(working, _truncate_markers_for_source(source_name))
        lines = _remove_consecutive_duplicate_lines(working.splitlines())
        return _collapse_blank_lines("\n".join(lines))
    except Exception as exc:
        logger.debug("clean_raw_text failed for source %s: %s", source_name, exc)
        return " ".join(str(text).split())
