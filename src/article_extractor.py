"""Fetch article detail pages and extract structured fields."""

import json
import logging
from typing import Any, Optional
from urllib.parse import urljoin

import requests
import trafilatura
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 20
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

EMPTY_RESULT = {
    "canonical_url": "",
    "extracted_title": "",
    "published_time": "",
    "summary": "",
    "raw_text": "",
    "extraction_status": "failed",
    "extraction_error": "",
}


def _clean_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return " ".join(str(value).split())


def _failed_result(error: str) -> dict:
    result = dict(EMPTY_RESULT)
    result["extraction_error"] = _clean_text(error)
    return result


def _fetch_html(url: str) -> tuple[Optional[str], Optional[str]]:
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=FETCH_TIMEOUT,
            allow_redirects=True,
        )
        if response.status_code >= 400:
            return None, f"HTTP {response.status_code}"
        return response.text, None
    except requests.RequestException as exc:
        return None, str(exc)


def _meta_content(soup: BeautifulSoup, *, property: str = "", name: str = "") -> str:
    tag = None
    if property:
        tag = soup.find("meta", attrs={"property": property})
    elif name:
        tag = soup.find("meta", attrs={"name": name})
    if not tag:
        return ""
    return _clean_text(tag.get("content", ""))


def _extract_canonical_url(soup: BeautifulSoup, page_url: str) -> str:
    link = soup.find("link", rel="canonical")
    if not link:
        return ""
    href = (link.get("href") or "").strip()
    if not href:
        return ""
    return _clean_text(urljoin(page_url, href))


def _extract_title(soup: BeautifulSoup) -> str:
    og_title = _meta_content(soup, property="og:title")
    if og_title:
        return og_title

    h1 = soup.find("h1")
    if h1:
        h1_text = _clean_text(h1.get_text())
        if h1_text:
            return h1_text

    if soup.title and soup.title.string:
        return _clean_text(soup.title.string)

    return ""


def _find_date_published(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        date_value = value.get("datePublished")
        if date_value:
            return str(date_value)
        for nested in value.values():
            found = _find_date_published(nested)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_date_published(item)
            if found:
                return found
    return None


def _extract_json_ld_date_published(soup: BeautifulSoup) -> str:
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        found = _find_date_published(data)
        if found:
            return _clean_text(found)
    return ""


def _extract_time_datetime(soup: BeautifulSoup) -> str:
    time_tag = soup.find("time", attrs={"datetime": True})
    if not time_tag:
        return ""
    return _clean_text(time_tag.get("datetime", ""))


def _extract_published_time(soup: BeautifulSoup) -> str:
    for getter in (
        lambda: _meta_content(soup, property="article:published_time"),
        lambda: _meta_content(soup, name="pubdate"),
        lambda: _meta_content(soup, name="publishdate"),
        lambda: _extract_time_datetime(soup),
        lambda: _extract_json_ld_date_published(soup),
    ):
        value = getter()
        if value:
            return value
    return ""


def _extract_summary(soup: BeautifulSoup) -> str:
    og_description = _meta_content(soup, property="og:description")
    if og_description:
        return og_description
    return _meta_content(soup, name="description")


def _extract_raw_text(html: str, page_url: str) -> str:
    try:
        text = trafilatura.extract(
            html,
            url=page_url,
            include_comments=False,
            include_tables=False,
        )
    except Exception as exc:
        logger.debug("trafilatura failed for %s: %s", page_url, exc)
        return ""
    return _clean_text(text) if text else ""


def extract_article_details(url: str) -> dict:
    """
    Fetch an article page and extract canonical URL, title, time, summary, and body.
    Never raises; returns extraction_status=\"failed\" on fetch/parse errors.
    """
    if not url or not url.strip():
        return _failed_result("empty_url")

    page_url = url.strip()
    html, fetch_error = _fetch_html(page_url)
    if fetch_error or not html:
        logger.warning("Article fetch failed for %s: %s", page_url, fetch_error)
        return _failed_result(fetch_error or "empty_response")

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as exc:
        logger.warning("Article parse failed for %s: %s", page_url, exc)
        return _failed_result(str(exc))

    return {
        "canonical_url": _extract_canonical_url(soup, page_url),
        "extracted_title": _extract_title(soup),
        "published_time": _extract_published_time(soup),
        "summary": _extract_summary(soup),
        "raw_text": _extract_raw_text(html, page_url),
        "extraction_status": "ok",
        "extraction_error": "",
    }
