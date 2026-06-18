"""URL cleaning and title normalization utilities."""

import re
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

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
