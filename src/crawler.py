"""Fetch source listing pages and extract candidate article titles and links."""

import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from cleaner import (
    blocked_slug_reason,
    clean_url,
    dedupe_key,
    normalize_title,
    title_from_slug,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

MIN_TITLE_LENGTH = 18
EXTENDED_TITLE_LENGTH = 25
MAX_TITLE_LENGTH = 300
MAX_CANDIDATES_PER_SOURCE = 30
FILTERED_SAMPLE_LIMIT = 80

ROUND_CALENDAR_NAV_RE = re.compile(r"^ROUND\s*\d+", re.I)
GUARDIAN_ARTICLE_RE = re.compile(r"^/sport/\d{4}/[a-z]{3}/\d{1,2}/.+", re.I)

GLOBAL_BLOCKED_PATH_FRAGMENTS = (
    "/tag/",
    "/tags/",
    "/team/",
    "/teams/",
    "/driver/",
    "/drivers/",
    "/video/",
    "/videos/",
    "/gallery/",
    "/photos/",
    "/results/",
    "/standings/",
    "/calendar/",
    "/tickets/",
    "/store/",
    "/shop/",
    "/login",
    "/subscribe",
    "/newsletter",
    "/privacy",
    "/terms/",
    "/about",
    "/contact/",
    "/hospitality",
    "/archive",
)

BLOCKED_TITLE_KEYWORDS = (
    "Hospitality",
    "Schedule",
    "Results",
    "Standings",
    "Driver Standings",
    "Team Standings",
    "Archive",
    "Tickets",
    "Store",
    "Subscribe",
    "Sign in",
    "Chevron Dropdown",
)

MEDIA_ARTICLE_MARKERS = (
    "/f1/",
    "/formula-1/",
    "/news/",
    "/article/",
    "/articles/",
    "/2026/",
)

FORMULA1_SOURCE_IDS = frozenset({"f1-official-latest", "formula1_latest"})

FIA_SERIES_SOURCE_IDS = frozenset(
    {
        "fia-f2-latest",
        "fia-f3-latest",
        "f1-academy-latest",
    }
)

FIA_SERIES_BLOCKED_FRAGMENTS = (
    "/standings",
    "/calendar",
    "/results",
    "/tickets",
    "/teams",
    "/drivers",
)

NEEDS_MORE_DEBUG_IDS = frozenset({"fia-f1-news"})

JS_RENDERED_TEAM_IDS = frozenset(
    {
        "mercedes-f1-news",
        "ferrari-f1-news",
        "mclaren-f1-news",
        "aston-martin-f1-news",
        "haas-f1-news",
    }
)

RELAXED_NEWS_SLUG_TEAM_IDS = frozenset(
    {
        "alpine-f1-news",
        "cadillac-f1-news",
    }
)

TEAM_SOURCE_IDS = frozenset(
    {
        "mercedes-f1-news",
        "ferrari-f1-news",
        "mclaren-f1-news",
        "aston-martin-f1-news",
        "alpine-f1-news",
        "williams-f1-news",
        "haas-f1-news",
        "cadillac-f1-news",
    }
)

TEAM_ARTICLE_MARKERS = (
    "/news/",
    "/articles/",
    "/formula1/news",
    "/racing/news",
)

TEAM_BLOCKED_FRAGMENTS = (
    "/partner",
    "/partners",
    "/shop",
    "/store",
    "/ticket",
    "/tickets",
    "/about",
    "/careers",
    "/merchandise",
    "/fan-zone",
    "/fans",
)

RN365_SOURCE_IDS = frozenset({"rn365-f1-news", "rn365-tech"})
RN365_BLOCKED_SLUGS = frozenset(
    {
        "calendar",
        "standings",
        "drivers",
        "interview",
        "video",
        "podcast",
        "my-rn365",
        "f1-news",
        "tech",
        "newsletter",
        "privacy",
        "terms",
        "about",
        "contact",
    }
)

PLANETF1_BLOCKED_FRAGMENTS = (
    "/terms-conditions",
    "/privacy",
    "/privacy-policy",
    "/cookies",
    "/cookie-policy",
    "/contact",
    "/about",
    "/advertise",
    "/authors",
    "/tags",
    "/teams",
    "/drivers",
    "/standings",
    "/calendar",
)

B_PLUS_SOURCE_IDS = frozenset(
    {
        "rn365-f1-news",
        "rn365-tech",
        "planetf1",
        "gpfans-f1",
        "crash-f1",
        "speedcafe-f1",
        "gp247-f1",
        "gpblog-f1",
    }
)

MOTORSPORT_F1_SOURCE_IDS = frozenset({"motorsport-f1"})

MOTORSPORT_BLOCKED_PATH_FRAGMENTS = (
    "/motogp/",
    "/indycar/",
    "/wec/",
    "/formula-e/",
    "/nascar",
    "/rally/",
    "/imsa/",
    "/motocross/",
    "/supercars/",
    "/gt/",
    "/lemans/",
    "/f2/",
    "/f3/",
    "/general/",
)

AUTOSPORT_F1_SOURCE_IDS = frozenset({"autosport-f1"})

AUTOSPORT_BLOCKED_PATH_FRAGMENTS = (
    "/motogp/",
    "/indycar/",
    "/wec/",
    "/formula-e/",
    "/nascar",
    "/rally/",
    "/imsa/",
    "/nls/",
    "/btcc/",
    "/gt/",
    "/f2/",
    "/f3/",
)

NON_F1_CATEGORY_TITLE_KEYWORDS = (
    "MotoGP",
    "NASCAR",
    "IndyCar",
    "WEC",
    "Formula E",
    "Rally",
    "NLS",
    "Supercars",
    "IMSA",
    "Le Mans",
)


@dataclass
class CandidateArticle:
    title: str
    clean_title: str
    url: str
    source_url: str
    is_title_from_slug: bool


@dataclass
class CrawlStats:
    raw_link_count: int
    filtered_count: int
    filtered_out_count: int
    filtered_links_sample: list[dict]
    possible_js_rendered: bool = False
    needs_more_debug: bool = False


@dataclass
class CrawlResult:
    candidates: list[CandidateArticle]
    stats: CrawlStats


def _url_path_lower(url: str) -> str:
    return urlparse(url).path.lower()


def _path_segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _host_lower(url: str) -> str:
    return urlparse(url).netloc.lower().lstrip("www.")


def _is_listing_page_path(path: str, suffixes: tuple[str, ...]) -> bool:
    normalized = path.rstrip("/")
    return any(normalized.endswith(suffix) for suffix in suffixes)


def _global_blocked_path_reason(url: str) -> Optional[str]:
    lower = url.lower()
    path = _url_path_lower(url)
    for fragment in GLOBAL_BLOCKED_PATH_FRAGMENTS:
        if fragment in path or fragment in lower:
            return f"global_blocked_path:{fragment}"
    return None


def _basic_whitespace(text: str) -> str:
    return " ".join(text.split())


def resolve_raw_title(anchor_title: str, url: str) -> tuple[str, bool]:
    """Return raw title text and whether it was generated from the URL slug."""
    normalized = _basic_whitespace(anchor_title)
    if normalized:
        return normalized, False
    slug_title = title_from_slug(url)
    if slug_title:
        return slug_title, True
    return "", False


def _is_round_calendar_navigation(title: str, url: str) -> bool:
    """Filter ROUND N nav labels only on race-calendar style URLs."""
    if not title or not ROUND_CALENDAR_NAV_RE.match(title.strip()):
        return False
    path = _url_path_lower(url)
    return "/racing/" in path or "/season/races/" in path


def _min_title_length(source_id: str, url: str) -> int:
    if source_id in RN365_SOURCE_IDS:
        return EXTENDED_TITLE_LENGTH
    if source_id == "speedcafe-f1":
        path = _url_path_lower(url)
        if path.startswith("/f1/") or path.startswith("/news/"):
            return EXTENDED_TITLE_LENGTH
    return MIN_TITLE_LENGTH


def get_title_filter_reason(
    anchor_title: str,
    url: str = "",
    source_id: str = "",
) -> Optional[str]:
    """Return a filter reason when the title is rejected, else None."""
    raw_title, _ = resolve_raw_title(anchor_title, url)
    if not raw_title:
        return "empty_title"

    clean_title = normalize_title(raw_title)
    sid = (source_id or "").strip()

    if _is_round_calendar_navigation(clean_title, url):
        return "round_calendar_navigation"

    min_len = _min_title_length(sid, url)
    if len(clean_title) < min_len:
        return "title_too_short"
    if len(clean_title) > MAX_TITLE_LENGTH:
        return "title_too_long"

    title_lower = clean_title.lower()
    for keyword in BLOCKED_TITLE_KEYWORDS:
        if keyword.lower() in title_lower:
            return f"blocked_title_keyword:{keyword}"

    if "podcast" in title_lower or "podcast" in _url_path_lower(url):
        if sid in RN365_SOURCE_IDS:
            return "rn365_podcast_content"

    if sid in MOTORSPORT_F1_SOURCE_IDS or sid in AUTOSPORT_F1_SOURCE_IDS:
        for keyword in NON_F1_CATEGORY_TITLE_KEYWORDS:
            if keyword.lower() in title_lower:
                return f"non_f1_category_title:{keyword}"
        raw_lower = raw_title.lower()
        for keyword in NON_F1_CATEGORY_TITLE_KEYWORDS:
            if keyword.lower() in raw_lower:
                return f"non_f1_category_title:{keyword}"

    return None


def is_valid_article_title(title: str, url: str = "", source_id: str = "") -> bool:
    return get_title_filter_reason(title, url, source_id) is None


def _formula1_url_reason(path: str) -> Optional[str]:
    if "/en/latest/article/" not in path:
        return "formula1_requires_latest_article_path"
    return None


def _fia_f1_url_reason(path: str) -> Optional[str]:
    if "/news/" not in path:
        return "fia_requires_news_path"
    for fragment in ("/documents/", "/events/", "/calendar/"):
        if fragment in path:
            return f"fia_blocked_path:{fragment}"
    if len(_path_segments(path)) < 3:
        return "fia_insufficient_path_depth"
    return None


def _fia_series_url_reason(path: str) -> Optional[str]:
    for fragment in FIA_SERIES_BLOCKED_FRAGMENTS:
        if fragment in path:
            return f"fia_series_blocked_path:{fragment}"
    if "/latest/" in path:
        segments = _path_segments(path)
        if len(segments) >= 2 and segments[-1] != "latest":
            return None
    if len(_path_segments(path)) >= 3:
        return None
    return "fia_series_not_article_path"


def _reuters_url_reason(path: str) -> Optional[str]:
    if "/sports/formula1/" not in path:
        return "reuters_requires_sports_formula1_path"
    if len(_path_segments(path)) < 4:
        return "reuters_insufficient_path_depth"
    return None


def _ap_url_reason(path: str) -> Optional[str]:
    if _is_listing_page_path(path, ("/hub/formula-one", "/hub/formula-one/")):
        return "ap_hub_listing_page"
    if "/article/" in path:
        return None
    if "/hub/formula-one/" in path and len(_path_segments(path)) >= 4:
        return None
    return "ap_not_article_path"


def _relaxed_team_news_slug_reason(path: str) -> Optional[str]:
    if not path.startswith("/news/"):
        return "team_news_slug_missing_news_path"
    if _is_listing_page_path(path, ("/news", "/news/")):
        return "team_listing_page"
    segments = _path_segments(path)
    if len(segments) < 2:
        return "team_news_slug_missing_slug"
    return None


def _standard_team_url_reason(path: str) -> Optional[str]:
    if not any(marker in path for marker in TEAM_ARTICLE_MARKERS):
        return "team_missing_article_marker"
    for fragment in TEAM_BLOCKED_FRAGMENTS:
        if fragment in path:
            return f"team_blocked_path:{fragment}"
    if _is_listing_page_path(
        path,
        (
            "/news",
            "/articles",
            "/formula1/news",
            "/racing/news",
        ),
    ):
        return "team_listing_page"
    if len(_path_segments(path)) < 3:
        return "team_insufficient_path_depth"
    return None


def _rn365_url_reason(url: str, path: str) -> Optional[str]:
    host = _host_lower(url)
    if "racingnews365.com" not in host:
        return "rn365_wrong_host"
    if "/podcast" in path:
        return "rn365_podcast_path"
    segments = _path_segments(path)
    if len(segments) != 1:
        return "rn365_requires_root_slug"
    slug = segments[0].lower()
    if slug in RN365_BLOCKED_SLUGS:
        return f"rn365_listing_slug:{slug}"
    return None


def _guardian_url_reason(path: str) -> Optional[str]:
    if "/sport/live/" in path:
        return "guardian_live_blog"
    if GUARDIAN_ARTICLE_RE.match(path):
        return None
    if path.startswith("/sport/formulaone") or path.startswith("/sport/formula-one"):
        return "guardian_not_article_path"
    if "/sport/" in path and "/live/" in path:
        return "guardian_live_blog"
    return "guardian_not_article_path"


def _planetf1_url_reason(url: str, path: str) -> Optional[str]:
    host = _host_lower(url)
    if "planetf1.com" not in host:
        return "planetf1_wrong_host"

    for fragment in PLANETF1_BLOCKED_FRAGMENTS:
        if fragment in path:
            return f"planetf1_blocked_path:{fragment}"

    segments = _path_segments(path)
    if not segments:
        return "planetf1_no_path"

    blocked_segments = {
        "news",
        "driver",
        "drivers",
        "teams",
        "standings",
        "calendar",
        "live",
        "tags",
        "authors",
        "advertise",
    }
    if len(segments) == 1 and segments[0] in blocked_segments:
        return f"planetf1_listing:{segments[0]}"
    for segment in segments:
        if segment in blocked_segments:
            return f"planetf1_blocked_segment:{segment}"
    return None


def _gpfans_url_reason(path: str) -> Optional[str]:
    if not path.startswith("/en/f1-news"):
        return "gpfans_missing_f1_news_path"
    if _is_listing_page_path(path, ("/en/f1-news", "/en/f1-news/")):
        return "gpfans_listing_page"
    blocked = ("standings", "calendar", "drivers", "teams", "tags", "privacy")
    for segment in _path_segments(path):
        if segment in blocked:
            return f"gpfans_blocked_segment:{segment}"
    segments = _path_segments(path)
    if len(segments) < 3:
        return "gpfans_insufficient_path_depth"
    return None


def _crash_url_reason(path: str) -> Optional[str]:
    if not path.startswith("/f1/news"):
        return "crash_missing_f1_news_path"
    if _is_listing_page_path(path, ("/f1/news", "/f1/news/")):
        return "crash_listing_page"
    segments = _path_segments(path)
    if len(segments) < 3:
        return "crash_insufficient_path_depth"
    return None


def _speedcafe_url_reason(path: str) -> Optional[str]:
    if _is_listing_page_path(path, ("/f1", "/f1/", "/news", "/news/")):
        return "speedcafe_listing_page"
    blocked = ("podcasts", "gallery", "results", "network")
    for segment in _path_segments(path):
        if segment in blocked:
            return f"speedcafe_blocked_segment:{segment}"
    if path.startswith("/f1/") and len(_path_segments(path)) >= 2:
        return None
    if path.startswith("/news/") and len(_path_segments(path)) >= 2:
        return None
    return "speedcafe_not_article_path"


def _gp247_url_reason(path: str) -> Optional[str]:
    if not path.startswith("/formula-1-news/"):
        return "gp247_missing_formula_1_news_path"
    if _is_listing_page_path(path, ("/formula-1-news", "/formula-1-news/")):
        return "gp247_listing_page"
    if len(_path_segments(path)) < 2:
        return "gp247_insufficient_path_depth"
    return None


def _gpblog_url_reason(path: str) -> Optional[str]:
    if path.startswith("/en/news/") and len(_path_segments(path)) >= 3:
        if not _is_listing_page_path(path, ("/en/news", "/en/news/")):
            return None
    if path.startswith("/en/formula-1/") and len(_path_segments(path)) >= 3:
        return None
    blocked = (
        "/en/videos",
        "/en/standing",
        "/en/f1-calendar",
        "/en/f1-teams",
        "/en/f1-drivers",
        "/en/podcast",
    )
    for fragment in blocked:
        if path.startswith(fragment):
            return f"gpblog_blocked_path:{fragment}"
    if _is_listing_page_path(path, ("/en/news", "/en/news/")):
        return "gpblog_listing_page"
    return "gpblog_not_article_path"


def _motorsport_f1_url_reason(path: str) -> Optional[str]:
    if "/f1/news/" not in path:
        return "motorsport_requires_f1_news_path"
    for fragment in MOTORSPORT_BLOCKED_PATH_FRAGMENTS:
        if fragment in path:
            return f"motorsport_blocked_path:{fragment}"
    if len(_path_segments(path)) < 3:
        return "motorsport_insufficient_path_depth"
    return None


def _autosport_f1_url_reason(path: str) -> Optional[str]:
    if "/f1/news/" not in path:
        return "autosport_requires_f1_news_path"
    for fragment in AUTOSPORT_BLOCKED_PATH_FRAGMENTS:
        if fragment in path:
            return f"autosport_blocked_path:{fragment}"
    if len(_path_segments(path)) < 3:
        return "autosport_insufficient_path_depth"
    return None


def _generic_media_url_reason(path: str) -> Optional[str]:
    if not any(marker in path for marker in MEDIA_ARTICLE_MARKERS):
        return "media_missing_article_marker"
    segments = _path_segments(path)
    if len(segments) < 2:
        return "media_insufficient_path_depth"
    last = segments[-1]
    if last in ("f1", "formula-1", "formula1", "news", "latest", "index.html"):
        return f"media_listing_segment:{last}"
    return None


def get_url_filter_reason(url: str, source_id: str) -> Optional[str]:
    """Return a filter reason when the URL is rejected, else None."""
    if not url:
        return "empty_url"

    global_reason = _global_blocked_path_reason(url)
    if global_reason:
        return global_reason

    slug_reason = blocked_slug_reason(url)
    if slug_reason:
        return slug_reason

    path = _url_path_lower(url)
    sid = (source_id or "").strip()

    if sid == "guardian-f1":
        return _guardian_url_reason(path)

    if sid in FORMULA1_SOURCE_IDS:
        return _formula1_url_reason(path)

    if sid == "fia-f1-news":
        return _fia_f1_url_reason(path)

    if sid in FIA_SERIES_SOURCE_IDS:
        return _fia_series_url_reason(path)

    if sid == "reuters-f1":
        return _reuters_url_reason(path)

    if sid == "ap-f1":
        return _ap_url_reason(path)

    if sid in RELAXED_NEWS_SLUG_TEAM_IDS:
        return _relaxed_team_news_slug_reason(path)

    if sid in TEAM_SOURCE_IDS:
        return _standard_team_url_reason(path)

    if sid in RN365_SOURCE_IDS:
        return _rn365_url_reason(url, path)

    if sid == "planetf1":
        return _planetf1_url_reason(url, path)

    if sid == "gpfans-f1":
        return _gpfans_url_reason(path)

    if sid == "crash-f1":
        return _crash_url_reason(path)

    if sid == "speedcafe-f1":
        return _speedcafe_url_reason(path)

    if sid == "gp247-f1":
        return _gp247_url_reason(path)

    if sid == "gpblog-f1":
        return _gpblog_url_reason(path)

    if sid in MOTORSPORT_F1_SOURCE_IDS:
        return _motorsport_f1_url_reason(path)

    if sid in AUTOSPORT_F1_SOURCE_IDS:
        return _autosport_f1_url_reason(path)

    if sid in B_PLUS_SOURCE_IDS:
        return "b_plus_unhandled_source"

    return _generic_media_url_reason(path)


def is_likely_article_url(url: str, source_id: str) -> bool:
    return get_url_filter_reason(url, source_id) is None


def _same_site(href: str, base_netloc: str) -> bool:
    parsed = urlparse(href)
    if not parsed.netloc:
        return True
    base = base_netloc.lower().lstrip("www.")
    host = parsed.netloc.lower().lstrip("www.")
    return host == base or host.endswith(f".{base}")


def fetch_page_html(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    user_agent: str = DEFAULT_USER_AGENT,
) -> Optional[str]:
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )
        if response.status_code >= 400:
            logger.warning("Crawl failed for %s: HTTP %s", url, response.status_code)
            return None
        return response.text
    except requests.RequestException as exc:
        logger.warning("Crawl failed for %s: %s", url, exc)
        return None


def extract_candidates_from_html(
    html: str,
    source_url: str,
    source_id: str = "",
    max_candidates: int = MAX_CANDIDATES_PER_SOURCE,
) -> CrawlResult:
    """Parse HTML and return same-site links that look like news articles."""
    soup = BeautifulSoup(html, "lxml")
    base_parsed = urlparse(source_url)
    base_netloc = base_parsed.netloc
    sid = (source_id or "").strip()
    sample_limit = FILTERED_SAMPLE_LIMIT

    raw_links: list[tuple[str, str, str]] = []
    seen_raw: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue

        absolute = urljoin(source_url, href)
        cleaned = clean_url(absolute)
        parsed = urlparse(cleaned)

        if parsed.scheme not in ("http", "https"):
            continue
        if not _same_site(cleaned, base_netloc):
            continue

        key = dedupe_key(cleaned)
        if key in seen_raw:
            continue
        seen_raw.add(key)

        title = _basic_whitespace(anchor.get_text(strip=True))
        raw_links.append((title, cleaned, key))

    raw_link_count = len(raw_links)

    eligible: list[CandidateArticle] = []
    seen_eligible: set[str] = set()
    filtered_links_sample: list[dict] = []

    def _record_filtered(title: str, url: str, reason: str) -> None:
        if len(filtered_links_sample) >= sample_limit:
            return
        filtered_links_sample.append(
            {
                "title": title,
                "url": url,
                "filter_reason": reason,
            }
        )

    for anchor_title, cleaned, key in raw_links:
        url_reason = get_url_filter_reason(cleaned, sid)
        if url_reason:
            _record_filtered(anchor_title, cleaned, url_reason)
            continue

        raw_title, is_from_slug = resolve_raw_title(anchor_title, cleaned)
        if not raw_title:
            _record_filtered(anchor_title, cleaned, "empty_title")
            continue

        clean_title = normalize_title(raw_title)
        title_reason = get_title_filter_reason(anchor_title, cleaned, sid)
        if title_reason:
            _record_filtered(raw_title, cleaned, title_reason)
            continue

        if key in seen_eligible:
            _record_filtered(clean_title, cleaned, "duplicate_eligible_key")
            continue

        seen_eligible.add(key)
        eligible.append(
            CandidateArticle(
                title=raw_title,
                clean_title=clean_title,
                url=cleaned,
                source_url=source_url,
                is_title_from_slug=is_from_slug,
            )
        )

    for article in eligible[max_candidates:]:
        _record_filtered(
            article.clean_title,
            article.url,
            "exceeded_max_per_source",
        )

    candidates = eligible[:max_candidates]
    kept_links_count = len(candidates)

    return CrawlResult(
        candidates=candidates,
        stats=CrawlStats(
            raw_link_count=raw_link_count,
            filtered_count=kept_links_count,
            filtered_out_count=raw_link_count - kept_links_count,
            filtered_links_sample=filtered_links_sample,
            possible_js_rendered=sid in JS_RENDERED_TEAM_IDS,
            needs_more_debug=sid in NEEDS_MORE_DEBUG_IDS,
        ),
    )


def crawl_source(
    source_url: str,
    source_id: str = "",
    timeout: int = DEFAULT_TIMEOUT,
    max_candidates: int = MAX_CANDIDATES_PER_SOURCE,
) -> CrawlResult:
    """Fetch a source listing page and extract candidate articles."""
    sid = (source_id or "").strip()
    html = fetch_page_html(source_url, timeout=timeout)
    if not html:
        return CrawlResult(
            candidates=[],
            stats=CrawlStats(
                raw_link_count=0,
                filtered_count=0,
                filtered_out_count=0,
                filtered_links_sample=[],
                possible_js_rendered=sid in JS_RENDERED_TEAM_IDS,
                needs_more_debug=sid in NEEDS_MORE_DEBUG_IDS,
            ),
        )
    return extract_candidates_from_html(
        html,
        source_url,
        source_id=sid,
        max_candidates=max_candidates,
    )
