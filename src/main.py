"""
Racing Dispatch F1 — Phase 1 news source collector.

Reads sources from data/sources.csv, audits reachability, crawls candidate
articles, cleans URLs, stores results in SQLite, and exports new articles to
outputs/articles_latest.json.
"""

import csv
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from crawler import crawl_source
from database import (
    DEFAULT_DB_PATH,
    get_connection,
    init_db,
    insert_article_if_new,
    update_source_check,
    upsert_source,
)
from source_audit import audit_url

ROOT = Path(__file__).resolve().parent.parent
SOURCES_CSV = ROOT / "data" / "sources.csv"
OUTPUT_JSON = ROOT / "outputs" / "articles_latest.json"
CRAWL_DEBUG_JSON = ROOT / "outputs" / "crawl_debug.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rd.main")

JUNK_URL_PATTERNS = (
    re.compile(r"/sport/live/", re.I),
    re.compile(r"terms[-_]conditions", re.I),
    re.compile(r"privacy[-_]policy", re.I),
    re.compile(r"cookie[-_]policy", re.I),
    re.compile(r"/privacy", re.I),
    re.compile(r"/terms", re.I),
)


def load_all_sources(csv_path: Path = SOURCES_CSV) -> list[dict]:
    """Load every configured source from data/sources.csv."""
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
            auto_crawl_raw = (row.get("auto_crawl") or "").strip().lower()
            auto_crawl = auto_crawl_raw not in ("false", "0", "no")
            sources.append(
                {
                    "source_id": (row.get("source_id") or "").strip() or None,
                    "name": name,
                    "url": url,
                    "tier": (row.get("source_tier") or "").strip() or None,
                    "type": (row.get("source_type") or "").strip() or None,
                    "crawl_method": (row.get("crawl_method") or "").strip() or None,
                    "crawl_status": (row.get("crawl_status") or "").strip() or None,
                    "notes": (row.get("notes") or "").strip() or None,
                    "auto_crawl": auto_crawl,
                }
            )
    return sources


def export_new_articles(
    new_articles: list[dict],
    output_path: Path = OUTPUT_JSON,
) -> None:
    """Write newly discovered articles to a JSON file for downstream use."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "count": len(new_articles),
        "articles": new_articles,
    }
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("Exported %d new articles to %s", len(new_articles), output_path)


def export_crawl_debug(
    crawl_debug: dict[str, dict],
    output_path: Path = CRAWL_DEBUG_JSON,
) -> None:
    """Write per-source crawl debug stats to JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        **crawl_debug,
    }
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("Exported crawl debug for %d sources to %s", len(crawl_debug), output_path)


def _is_junk_article(article: dict) -> bool:
    url = article.get("url", "")
    source_type = (article.get("source_type") or "").lower()
    if source_type == "team":
        return True
    return any(pattern.search(url) for pattern in JUNK_URL_PATTERNS)


def _print_run_summary(
    total_sources: int,
    auto_crawl_count: int,
    manual_skip_count: int,
    new_insert_count: int,
    articles: list[dict],
) -> None:
    junk_hits = [a for a in articles if _is_junk_article(a)]
    team_hits = [a for a in articles if (a.get("source_type") or "").lower() == "team"]

    print("\n=== 采集运行摘要 ===")
    print(f"总来源数: {total_sources}")
    print(f"auto_crawl=true 来源数: {auto_crawl_count}")
    print(f"manual_check 跳过来源数: {manual_skip_count}")
    print(f"本次新增入库数量: {new_insert_count}")
    print(f"articles_latest.json 总数: {len(articles)}")
    print(f"是否出现 team 类型: {'是' if team_hits else '否'}")
    print(
        f"是否出现 terms/privacy/live blog 等垃圾链接: "
        f"{'是' if junk_hits else '否'}"
    )
    if junk_hits:
        print("垃圾链接样例:")
        for item in junk_hits[:5]:
            print(f"  - {item.get('url')}")


def run(
    sources_csv: Path = SOURCES_CSV,
    db_path: Path = DEFAULT_DB_PATH,
    output_path: Path = OUTPUT_JSON,
) -> int:
    """Execute the full Phase 1 collection pipeline."""
    run_started = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    all_sources = load_all_sources(sources_csv)
    auto_sources = [s for s in all_sources if s["auto_crawl"]]
    manual_skip_count = len(all_sources) - len(auto_sources)

    logger.info(
        "Loaded %d sources (%d auto, %d manual) from %s",
        len(all_sources),
        len(auto_sources),
        manual_skip_count,
        sources_csv,
    )

    conn = get_connection(db_path)
    init_db(conn)

    new_articles: list[dict] = []
    crawl_debug: dict[str, dict] = {}

    for source in all_sources:
        name = source["name"]
        url = source["url"]
        source_key = source.get("source_id") or name

        source_id = upsert_source(
            conn,
            name=name,
            url=url,
            language=source.get("language"),
            notes=source.get("notes"),
        )

        if not source["auto_crawl"]:
            print(f"skipped_manual_check: {name}")
            crawl_debug[source_key] = {
                "source_name": name,
                "source_url": url,
                "raw_links_count": 0,
                "kept_links_count": 0,
                "filtered_links_sample": [],
                "skipped": True,
                "skip_reason": "manual_check",
            }
            continue

        logger.info("Processing source: %s (%s)", name, url)

        audit = audit_url(url)
        status = "ok" if audit.ok else (audit.error or "error")
        update_source_check(conn, source_id, status)

        if not audit.ok:
            logger.warning("Skipping crawl for unreachable source: %s", url)
            crawl_debug[source_key] = {
                "source_name": name,
                "source_url": url,
                "raw_links_count": 0,
                "kept_links_count": 0,
                "filtered_links_sample": [],
                "skipped": True,
                "skip_reason": audit.error or status,
            }
            print(
                f"[{source_key}] "
                f"raw_links_count=0 "
                f"kept_links_count=0 "
                f"skipped=True "
                f"skip_reason={audit.error or status}"
            )
            continue

        crawl_result = crawl_source(url, source_id=source_key)
        candidates = crawl_result.candidates
        stats = crawl_result.stats

        crawl_debug[source_key] = {
            "source_name": name,
            "source_url": url,
            "raw_links_count": stats.raw_link_count,
            "kept_links_count": stats.filtered_count,
            "filtered_links_sample": stats.filtered_links_sample,
            "skipped": False,
            "skip_reason": "",
        }

        new_insert_count = 0
        for candidate in candidates:
            is_new = insert_article_if_new(
                conn,
                source_id=source_id,
                title=candidate.clean_title,
                url=candidate.url,
            )
            if is_new:
                new_insert_count += 1
                new_articles.append(
                    {
                        "title": candidate.title,
                        "clean_title": candidate.clean_title,
                        "url": candidate.url,
                        "source_name": name,
                        "source_tier": source.get("tier"),
                        "source_type": source.get("type"),
                        "source_url": url,
                        "first_seen_at": datetime.now(timezone.utc)
                        .replace(microsecond=0)
                        .isoformat(),
                        "is_title_from_slug": candidate.is_title_from_slug,
                    }
                )

        print(
            f"[{source_key}] "
            f"raw_links_count={stats.raw_link_count} "
            f"kept_links_count={stats.filtered_count} "
            f"skipped=False "
            f"skip_reason="
        )

    export_new_articles(new_articles, output_path)
    export_crawl_debug(crawl_debug)
    conn.close()

    _print_run_summary(
        total_sources=len(all_sources),
        auto_crawl_count=len(auto_sources),
        manual_skip_count=manual_skip_count,
        new_insert_count=len(new_articles),
        articles=new_articles,
    )

    logger.info(
        "Run complete — %d new articles since %s",
        len(new_articles),
        run_started,
    )
    return len(new_articles)


def main() -> None:
    try:
        count = run()
        sys.exit(0 if count >= 0 else 1)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    except Exception:
        logger.exception("Collection run failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
