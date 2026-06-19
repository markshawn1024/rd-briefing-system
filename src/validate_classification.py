"""
Racing Dispatch F1 — classification JSON validator (v0.5.1).

Validates outputs/classified_articles.json against the v0.5.1 schema.
Does not call any external API.
"""

import json
import sys
from pathlib import Path

from classification_prompt import CONTENT_TYPES, DAILY_PRIORITIES, RD_CATEGORIES

ROOT = Path(__file__).resolve().parent.parent
INPUT_JSON = ROOT / "outputs" / "classified_articles.json"

TOP_LEVEL_KEYS = (
    "generated_for",
    "article_count",
    "event_clusters",
    "excluded_articles",
    "editor_notes",
)

CLUSTER_REQUIRED_KEYS = (
    "event_id",
    "event_title_cn",
    "primary_category",
    "secondary_categories",
    "importance_score",
    "cross_verification_score",
    "source_credibility_score",
    "recommended_for_daily",
    "reason",
    "summary_cn",
    "suggested_headline_cn",
    "articles",
    "content_type",
    "rumor",
    "daily_priority",
    "writing_angle_cn",
    "risk_note_cn",
)

SCORE_FIELDS = (
    "importance_score",
    "cross_verification_score",
    "source_credibility_score",
)


def validate(data: dict) -> list[str]:
    """Return a list of validation error messages."""
    errors: list[str] = []

    if not isinstance(data, dict):
        return ["Root value must be a JSON object."]

    for key in TOP_LEVEL_KEYS:
        if key not in data:
            errors.append(f"Missing top-level key: {key}")

    clusters = data.get("event_clusters")
    if clusters is None:
        errors.append("Missing top-level key: event_clusters")
        return errors

    if not isinstance(clusters, list):
        errors.append("event_clusters must be a list.")
        return errors

    for i, cluster in enumerate(clusters):
        prefix = f"event_clusters[{i}]"
        if not isinstance(cluster, dict):
            errors.append(f"{prefix}: must be an object.")
            continue

        for key in CLUSTER_REQUIRED_KEYS:
            if key not in cluster:
                errors.append(f"{prefix}: missing required key '{key}'.")

        primary = cluster.get("primary_category")
        if primary is not None and primary not in RD_CATEGORIES:
            errors.append(
                f"{prefix}: primary_category '{primary}' is not a valid RD category."
            )

        for field in SCORE_FIELDS:
            value = cluster.get(field)
            if value is not None and not (
                isinstance(value, int) and 1 <= value <= 5
            ):
                errors.append(
                    f"{prefix}: {field} must be an integer between 1 and 5, got {value!r}."
                )

        daily_priority = cluster.get("daily_priority")
        if daily_priority is not None and daily_priority not in DAILY_PRIORITIES:
            errors.append(
                f"{prefix}: daily_priority must be one of A/B/C/D, got {daily_priority!r}."
            )

        content_type = cluster.get("content_type")
        if content_type is not None and content_type not in CONTENT_TYPES:
            errors.append(
                f"{prefix}: content_type '{content_type}' is not allowed."
            )

        rumor = cluster.get("rumor")
        if rumor is not None and not isinstance(rumor, bool):
            errors.append(f"{prefix}: rumor must be true or false.")

        articles = cluster.get("articles")
        if articles is not None and not isinstance(articles, list):
            errors.append(f"{prefix}: articles must be a list.")

    excluded = data.get("excluded_articles")
    if excluded is not None and not isinstance(excluded, list):
        errors.append("excluded_articles must be a list.")

    notes = data.get("editor_notes")
    if notes is not None and not isinstance(notes, list):
        errors.append("editor_notes must be a list.")

    return errors


def main() -> int:
    if not INPUT_JSON.exists():
        print(
            "outputs/classified_articles.json 不存在，请先把 ChatGPT 输出保存到该文件。"
        )
        return 1

    try:
        with INPUT_JSON.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"JSON parse error: {exc}")
        return 1

    errors = validate(data)
    if errors:
        print("Classification JSON validation failed:")
        for err in errors:
            print(f"  - {err}")
        return 1

    clusters = data.get("event_clusters", [])
    excluded = data.get("excluded_articles", [])
    ab_count = sum(
        1
        for c in clusters
        if isinstance(c, dict) and c.get("daily_priority") in ("A", "B")
    )

    print("Classification JSON validation passed.")
    print(f"event_clusters: {len(clusters)}")
    print(f"excluded_articles: {len(excluded)}")
    print(f"recommended A/B clusters: {ab_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
