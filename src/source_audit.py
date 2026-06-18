"""Check whether configured news source URLs are reachable."""

import csv
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
SOURCES_CSV = ROOT / "data" / "sources.csv"
OUTPUT_CSV = ROOT / "outputs" / "source_audit_result.csv"

DEFAULT_TIMEOUT = 20
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

RSS_LINK_TYPES = frozenset(
    {
        "application/rss+xml",
        "application/atom+xml",
        "application/rdf+xml",
        "application/xml",
        "text/xml",
    }
)
RSS_HREF_PATTERN = re.compile(r"(?:rss|feed|atom)(?:\.xml)?", re.I)

CSV_FIELDS = [
    "source_id",
    "source_name",
    "source_tier",
    "source_url",
    "status",
    "status_code",
    "final_url",
    "has_rss",
    "rss_url",
    "link_count",
    "article_tag_count",
    "h1_count",
    "h2_count",
    "h3_count",
    "error",
]


@dataclass
class AuditResult:
    url: str
    ok: bool
    status_code: Optional[int]
    final_url: Optional[str]
    error: Optional[str]
    has_rss: bool = False
    rss_url: Optional[str] = None
    link_count: int = 0
    article_tag_count: int = 0
    h1_count: int = 0
    h2_count: int = 0
    h3_count: int = 0


def _find_rss_url(soup: BeautifulSoup, page_url: str) -> Optional[str]:
    """Detect RSS/Atom feed URL from link elements."""
    for link in soup.find_all("link", href=True):
        rel = link.get("rel") or []
        if isinstance(rel, str):
            rel = [rel]
        rel_lower = {r.lower() for r in rel}
        link_type = (link.get("type") or "").lower()
        href = link.get("href", "").strip()
        if not href:
            continue
        if "alternate" in rel_lower and link_type in RSS_LINK_TYPES:
            return urljoin(page_url, href)
        if RSS_HREF_PATTERN.search(href):
            return urljoin(page_url, href)

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if href and RSS_HREF_PATTERN.search(href):
            return urljoin(page_url, href)

    return None


def _analyze_html(html: str, page_url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    rss_url = _find_rss_url(soup, page_url)
    return {
        "has_rss": rss_url is not None,
        "rss_url": rss_url,
        "link_count": len(soup.find_all("a")),
        "article_tag_count": len(soup.find_all("article")),
        "h1_count": len(soup.find_all("h1")),
        "h2_count": len(soup.find_all("h2")),
        "h3_count": len(soup.find_all("h3")),
    }


def audit_url(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    user_agent: str = BROWSER_USER_AGENT,
) -> AuditResult:
    """
    Fetch a source URL and report accessibility plus basic page structure.

    Uses GET with redirects enabled. A 2xx or 3xx response is considered OK.
    """
    headers = {
        "User-Agent": user_agent,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )
        ok = response.status_code < 400
        if not ok:
            logger.warning("Source unreachable: %s — HTTP %s", url, response.status_code)
            return AuditResult(
                url=url,
                ok=False,
                status_code=response.status_code,
                final_url=response.url,
                error=f"HTTP {response.status_code}",
            )

        analysis = _analyze_html(response.text, response.url)
        return AuditResult(
            url=url,
            ok=True,
            status_code=response.status_code,
            final_url=response.url,
            error=None,
            **analysis,
        )
    except requests.RequestException as exc:
        logger.warning("Source fetch failed: %s — %s", url, exc)
        return AuditResult(
            url=url,
            ok=False,
            status_code=None,
            final_url=None,
            error=str(exc),
        )


def audit_sources(
    sources: list[dict],
    timeout: int = DEFAULT_TIMEOUT,
) -> list[AuditResult]:
    """Audit a list of source dicts (each must have a ``url`` key)."""
    return [audit_url(source["url"], timeout=timeout) for source in sources]


def load_sources(csv_path: Path = SOURCES_CSV) -> list[dict]:
    """Load news sources from data/sources.csv."""
    if not csv_path.exists():
        raise FileNotFoundError(f"Sources file not found: {csv_path}")

    sources: list[dict] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = (row.get("source_url") or row.get("url") or "").strip()
            name = (row.get("source_name") or row.get("name") or "").strip()
            if not url or not name:
                continue
            auto_crawl = (row.get("auto_crawl") or "").strip().lower()
            if auto_crawl in ("false", "0", "no"):
                continue
            sources.append(
                {
                    "source_id": (row.get("source_id") or "").strip(),
                    "source_name": name,
                    "name": name,
                    "url": url,
                    "source_tier": (row.get("source_tier") or "").strip(),
                    "tier": (row.get("source_tier") or "").strip() or None,
                }
            )
    return sources


def _result_to_row(source: dict, result: AuditResult) -> dict:
    return {
        "source_id": source.get("source_id") or "",
        "source_name": source.get("source_name") or source.get("name") or "",
        "source_tier": source.get("source_tier") or source.get("tier") or "",
        "source_url": source["url"],
        "status": "ok" if result.ok else "fail",
        "status_code": result.status_code if result.status_code is not None else "",
        "final_url": result.final_url or "",
        "has_rss": "true" if result.has_rss else "false",
        "rss_url": result.rss_url or "",
        "link_count": result.link_count,
        "article_tag_count": result.article_tag_count,
        "h1_count": result.h1_count,
        "h2_count": result.h2_count,
        "h3_count": result.h3_count,
        "error": result.error or "",
    }


def write_audit_csv(rows: list[dict], output_path: Path = OUTPUT_CSV) -> Path:
    """Write audit rows to CSV, creating parent directories as needed."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        sources = load_sources()
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

    csv_rows: list[dict] = []
    ok_count = 0

    for source in sources:
        result = audit_url(source["url"], timeout=DEFAULT_TIMEOUT)
        if result.ok:
            ok_count += 1
        csv_rows.append(_result_to_row(source, result))

    output_path = write_audit_csv(csv_rows)

    fail_count = len(sources) - ok_count
    print(f"总来源数: {len(sources)}")
    print(f"可达数量: {ok_count}")
    print(f"失败数量: {fail_count}")
    print(f"输出文件: {output_path}")

    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
