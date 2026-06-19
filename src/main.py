"""
Racing Dispatch F1 — Phase 1 news source collector (v0.4.1).

Reads sources from data/sources.csv, audits reachability, crawls candidate
articles, extracts article details, cleans URLs, stores results in SQLite,
and exports new articles to outputs/articles_latest.json.
"""

import csv
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from article_extractor import extract_article_details
from article_filters import get_export_filter_reason
from crawler import crawl_source
from database import (
    DEFAULT_DB_PATH,
    get_connection,
    init_db,
    insert_article_if_new,
    update_article_extraction,
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


def _progress_label(title: str, clean_title: str, url: str) -> str:
    return clean_title or title or url


def _build_article_record(
    candidate,
    source: dict,
    source_url: str,
    first_seen_at: str,
    details: dict,
) -> dict:
    extraction_ok = details.get("extraction_status") == "ok"
    clean_title = (
        details.get("extracted_title") or candidate.clean_title
        if extraction_ok
        else candidate.clean_title
    )
    return {
        "title": candidate.title,
        "clean_title": clean_title,
        "url": candidate.url,
        "canonical_url": details.get("canonical_url", "") if extraction_ok else "",
        "source_name": source["name"],
        "source_tier": source.get("tier"),
        "source_type": source.get("type"),
        "source_url": source_url,
        "published_time_raw": details.get("published_time_raw", "") if extraction_ok else "",
        "published_time": details.get("published_time", "") if extraction_ok else "",
        "summary": details.get("summary", "") if extraction_ok else "",
        "raw_text": details.get("raw_text", "") if extraction_ok else "",
        "raw_text_length": details.get("raw_text_length", 0) if extraction_ok else 0,
        "raw_text_cleaned": details.get("raw_text_cleaned", False) if extraction_ok else False,
        "raw_text_truncated": details.get("raw_text_truncated", False) if extraction_ok else False,
        "first_seen_at": first_seen_at,
        "is_title_from_slug": candidate.is_title_from_slug,
        "extraction_status": details.get("extraction_status", "failed"),
        "extraction_error": details.get("extraction_error", ""),
        "filter_reason": "",
        "source_id": source.get("source_id"),
    }


def _extract_new_articles(
    conn,
    pending: list[dict],
) -> tuple[list[dict], int, int, int]:
    """Fetch detail pages for newly inserted articles."""
    total = len(pending)
    new_articles: list[dict] = []
    extraction_ok = 0
    extraction_failed = 0
    export_filtered = 0

    for index, item in enumerate(pending, start=1):
        candidate = item["candidate"]
        label = _progress_label(candidate.title, candidate.clean_title, candidate.url)
        print(f"extracting article {index}/{total}: {label}...")

        details = extract_article_details(candidate.url)
        extraction_ok_flag = details.get("extraction_status") == "ok"
        if extraction_ok_flag:
            extraction_ok += 1
            db_title = details.get("extracted_title") or candidate.clean_title
        else:
            extraction_failed += 1
            db_title = candidate.clean_title

        update_article_extraction(
            conn,
            candidate.url,
            title=db_title,
            canonical_url=details.get("canonical_url", "") if extraction_ok_flag else "",
            published_time=details.get("published_time", "") if extraction_ok_flag else "",
            published_time_raw=details.get("published_time_raw", "") if extraction_ok_flag else "",
            summary=details.get("summary", "") if extraction_ok_flag else "",
            raw_text=details.get("raw_text", "") if extraction_ok_flag else "",
            raw_text_length=details.get("raw_text_length", 0) if extraction_ok_flag else 0,
            raw_text_cleaned=details.get("raw_text_cleaned", False) if extraction_ok_flag else False,
            extraction_status=details.get("extraction_status", "failed"),
            extraction_error=details.get("extraction_error", ""),
        )

        record = _build_article_record(
            candidate,
            item["source"],
            item["source_url"],
            item["first_seen_at"],
            details,
        )
        filter_reason = get_export_filter_reason(record)
        if filter_reason:
            export_filtered += 1
            print(f"filtered export: {label} ({filter_reason})")
            continue

        new_articles.append(record)

    return new_articles, extraction_ok, extraction_failed, export_filtered


def _print_run_summary(
    total_sources: int,
    auto_crawl_count: int,
    manual_skip_count: int,
    new_candidate_count: int,
    extraction_ok_count: int,
    extraction_failed_count: int,
    export_filtered_count: int,
    exported_count: int,
    output_path: Path,
) -> None:
    print("\n=== 采集运行摘要 ===")
    print(f"总来源数: {total_sources}")
    print(f"auto_crawl=true 来源数: {auto_crawl_count}")
    print(f"manual_check 跳过来源数: {manual_skip_count}")
    print(f"本次新增候选文章数量: {new_candidate_count}")
    print(f"详情提取成功数量: {extraction_ok_count}")
    print(f"详情提取失败数量: {extraction_failed_count}")
    print(f"输出质量过滤跳过数量: {export_filtered_count}")
    print(f"articles_latest.json 输出数量: {exported_count}")
    print(f"articles_latest.json 输出路径: {output_path}")


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

    new_pending: list[dict] = []
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
                new_pending.append(
                    {
                        "candidate": candidate,
                        "source": source,
                        "source_url": url,
                        "first_seen_at": datetime.now(timezone.utc)
                        .replace(microsecond=0)
                        .isoformat(),
                    }
                )

        print(
            f"[{source_key}] "
            f"raw_links_count={stats.raw_link_count} "
            f"kept_links_count={stats.filtered_count} "
            f"skipped=False "
            f"skip_reason="
        )

    new_articles, extraction_ok_count, extraction_failed_count, export_filtered_count = (
        _extract_new_articles(
            conn,
            new_pending,
        )
    )

    export_new_articles(new_articles, output_path)
    export_crawl_debug(crawl_debug)
    conn.close()

    _print_run_summary(
        total_sources=len(all_sources),
        auto_crawl_count=len(auto_sources),
        manual_skip_count=manual_skip_count,
        new_candidate_count=len(new_pending),
        extraction_ok_count=extraction_ok_count,
        extraction_failed_count=extraction_failed_count,
        export_filtered_count=export_filtered_count,
        exported_count=len(new_articles),
        output_path=output_path,
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
