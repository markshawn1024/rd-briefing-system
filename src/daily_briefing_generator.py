"""Generate Racing Dispatch daily briefing drafts from classified events.

This module is fully local: it reads the manually produced classification JSON
and writes Markdown and JSON drafts without calling any external service.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INPUT_JSON = ROOT / "outputs" / "classified_articles.json"
OUTPUT_MARKDOWN = ROOT / "outputs" / "daily_briefing_draft.md"
OUTPUT_JSON = ROOT / "outputs" / "daily_briefing_draft.json"
SOURCE_FILE_LABEL = "outputs/classified_articles.json"

TOP_LEVEL_REQUIRED = (
    "generated_for",
    "article_count",
    "event_clusters",
    "excluded_articles",
    "editor_notes",
)

CLUSTER_REQUIRED = (
    "event_id",
    "event_title_cn",
    "primary_category",
    "secondary_categories",
    "content_type",
    "rumor",
    "importance_score",
    "cross_verification_score",
    "source_credibility_score",
    "daily_priority",
    "recommended_for_daily",
    "reason",
    "summary_cn",
    "suggested_headline_cn",
    "writing_angle_cn",
    "risk_note_cn",
    "articles",
)

ARTICLE_REQUIRED = (
    "title",
    "source_name",
    "source_tier",
    "url",
    "published_time",
)

EVENT_OUTPUT_FIELDS = CLUSTER_REQUIRED
SCORE_FIELDS = (
    "importance_score",
    "cross_verification_score",
    "source_credibility_score",
)
TEXT_FIELDS = (
    "event_id",
    "event_title_cn",
    "primary_category",
    "content_type",
    "daily_priority",
    "reason",
    "summary_cn",
    "suggested_headline_cn",
    "writing_angle_cn",
    "risk_note_cn",
)


class BriefingInputError(ValueError):
    """Raised when the classification input cannot be used safely."""


def load_classification(input_path: Path = INPUT_JSON) -> dict:
    """Load classified_articles.json with user-friendly errors."""
    if not input_path.exists():
        raise BriefingInputError(
            f"输入文件不存在：{input_path}\n"
            "请先生成并校验 outputs/classified_articles.json。"
        )

    try:
        with input_path.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise BriefingInputError(
            f"输入文件不是有效 JSON：第 {exc.lineno} 行，第 {exc.colno} 列，"
            f"{exc.msg}。"
        ) from exc
    except OSError as exc:
        raise BriefingInputError(f"无法读取输入文件：{exc}") from exc

    errors = validate_classification_structure(data)
    if errors:
        details = "\n".join(f"  - {error}" for error in errors)
        raise BriefingInputError(f"classified_articles.json 结构不完整：\n{details}")

    return data


def validate_classification_structure(data: object) -> list[str]:
    """Return structural errors that would prevent reliable draft generation."""
    if not isinstance(data, dict):
        return ["顶层必须是 JSON 对象。"]

    errors: list[str] = []
    for key in TOP_LEVEL_REQUIRED:
        if key not in data:
            errors.append(f"缺少顶层字段 '{key}'。")

    if errors:
        return errors

    if not isinstance(data["generated_for"], str) or not data["generated_for"].strip():
        errors.append("generated_for 必须是非空字符串。")

    article_count = data["article_count"]
    if not isinstance(article_count, int) or isinstance(article_count, bool) or article_count < 0:
        errors.append("article_count 必须是非负整数。")

    clusters = data["event_clusters"]
    if not isinstance(clusters, list):
        errors.append("event_clusters 必须是列表。")
        clusters = []

    if not isinstance(data["excluded_articles"], list):
        errors.append("excluded_articles 必须是列表。")

    notes = data["editor_notes"]
    if not isinstance(notes, list):
        errors.append("editor_notes 必须是列表。")
    elif any(not isinstance(note, str) for note in notes):
        errors.append("editor_notes 中的每一项都必须是字符串。")

    for index, cluster in enumerate(clusters):
        prefix = f"event_clusters[{index}]"
        if not isinstance(cluster, dict):
            errors.append(f"{prefix} 必须是对象。")
            continue

        missing = [key for key in CLUSTER_REQUIRED if key not in cluster]
        for key in missing:
            errors.append(f"{prefix} 缺少字段 '{key}'。")
        if missing:
            continue

        for field in TEXT_FIELDS:
            if not isinstance(cluster[field], str):
                errors.append(f"{prefix}.{field} 必须是字符串。")

        if not isinstance(cluster["secondary_categories"], list):
            errors.append(f"{prefix}.secondary_categories 必须是列表。")
        elif any(not isinstance(value, str) for value in cluster["secondary_categories"]):
            errors.append(f"{prefix}.secondary_categories 只能包含字符串。")

        if not isinstance(cluster["recommended_for_daily"], bool):
            errors.append(f"{prefix}.recommended_for_daily 必须是布尔值。")
        if not isinstance(cluster["rumor"], bool):
            errors.append(f"{prefix}.rumor 必须是布尔值。")

        if cluster["daily_priority"] not in ("A", "B", "C", "D"):
            errors.append(f"{prefix}.daily_priority 必须是 A/B/C/D。")

        for field in SCORE_FIELDS:
            value = cluster[field]
            if not (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 1 <= value <= 5
            ):
                errors.append(f"{prefix}.{field} 必须是 1–5 的整数。")

        cluster_articles = cluster["articles"]
        if not isinstance(cluster_articles, list):
            errors.append(f"{prefix}.articles 必须是列表。")
            continue

        for article_index, article in enumerate(cluster_articles):
            article_prefix = f"{prefix}.articles[{article_index}]"
            if not isinstance(article, dict):
                errors.append(f"{article_prefix} 必须是对象。")
                continue
            for key in ARTICLE_REQUIRED:
                if key not in article:
                    errors.append(f"{article_prefix} 缺少字段 '{key}'。")
                elif not isinstance(article[key], str):
                    errors.append(f"{article_prefix}.{key} 必须是字符串。")

    return errors


def _event_sort_key(cluster: dict) -> tuple:
    priority_order = {"A": 0, "B": 1, "C": 2, "D": 3}
    return (
        priority_order[cluster["daily_priority"]],
        -cluster["importance_score"],
        -cluster["cross_verification_score"],
        -cluster["source_credibility_score"],
        cluster["event_id"],
    )


def select_events(clusters: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split recommended events into headline A/B events and backup C events."""
    headline_events = [
        cluster
        for cluster in clusters
        if cluster["recommended_for_daily"] is True
        and cluster["daily_priority"] in ("A", "B")
    ]
    backup_events = [
        cluster
        for cluster in clusters
        if cluster["recommended_for_daily"] is True
        and cluster["daily_priority"] == "C"
    ]
    return (
        sorted(headline_events, key=_event_sort_key),
        sorted(backup_events, key=_event_sort_key),
    )


def _copy_event(cluster: dict) -> dict:
    return {field: cluster[field] for field in EVENT_OUTPUT_FIELDS}


def build_json_draft(
    data: dict,
    headline_events: list[dict],
    backup_events: list[dict],
    generated_at: str,
) -> dict:
    """Build the machine-readable briefing draft."""
    return {
        "generated_for": data["generated_for"],
        "generated_at": generated_at,
        "source_file": SOURCE_FILE_LABEL,
        "article_count": data["article_count"],
        "source_event_count": len(data["event_clusters"]),
        "selected_event_count": len(headline_events),
        "a_priority_count": sum(
            event["daily_priority"] == "A" for event in headline_events
        ),
        "b_priority_count": sum(
            event["daily_priority"] == "B" for event in headline_events
        ),
        "backup_event_count": len(backup_events),
        "excluded_article_count": len(data["excluded_articles"]),
        "headline_events": [_copy_event(event) for event in headline_events],
        "backup_events": [_copy_event(event) for event in backup_events],
        "editor_notes": list(data["editor_notes"]),
    }


def _display_text(value: str, fallback: str = "未提供") -> str:
    value = value.strip()
    return value if value else fallback


def _headline(event: dict) -> str:
    return _display_text(
        event["suggested_headline_cn"],
        _display_text(event["event_title_cn"]),
    )


def _summarize_exclusion_reasons(excluded_articles: list[dict]) -> str:
    if not excluded_articles:
        return "无。"

    text = " ".join(
        str(article.get("exclude_reason", "")).lower()
        for article in excluded_articles
        if isinstance(article, dict)
    )
    categories = []
    rules = (
        (("旧闻", "历史", "回顾", "时效"), "旧闻或回顾性内容"),
        (("roundup", "聚合", "索引", "落地页"), "roundup 或聚合页"),
        (("广告", "推广", "宣传", "商业", "seo", "博彩"), "广告或推广内容"),
        (("正文", "提取", "403", "信息不足"), "正文不可用或信息不足"),
        (("低价值", "信息量", "轻量", "价值较低"), "低价值内容"),
        (("与f1", "非f1", "formula e"), "非 F1 内容"),
        (("无法验证", "无法核实", "缺少独立", "证据不足"), "缺少验证"),
    )
    for keywords, label in rules:
        if any(keyword in text for keyword in keywords):
            categories.append(label)

    if not categories:
        return "其他编辑过滤原因。"
    return "、".join(categories) + "。"


def build_markdown_draft(
    data: dict,
    headline_events: list[dict],
    backup_events: list[dict],
    generated_at: str,
) -> str:
    """Render the human-editable Chinese Markdown draft."""
    a_count = sum(event["daily_priority"] == "A" for event in headline_events)
    b_count = sum(event["daily_priority"] == "B" for event in headline_events)

    lines = [
        "# Racing Dispatch 极速特派",
        "",
        "## F1 每日资讯简报",
        "",
        f"生成时间：{generated_at}",
        f"数据来源：{SOURCE_FILE_LABEL}",
        f"候选文章数：{data['article_count']}",
        f"入选事件数：{len(headline_events)}",
        f"A 级事件数：{a_count}",
        f"B 级事件数：{b_count}",
        "",
        "## 【今日重点】",
        "",
    ]

    if not headline_events:
        lines.extend(["本次没有符合条件的 A/B 级推荐事件。", ""])

    for index, event in enumerate(headline_events, start=1):
        lines.extend(
            [
                f"### {index:02d}｜{_headline(event)}",
                "",
                f"分类：{_display_text(event['primary_category'])}",
                f"优先级：{event['daily_priority']}",
                f"重要性：{event['importance_score']} / 5",
                f"交叉验证：{event['cross_verification_score']} / 5",
                f"来源可信度：{event['source_credibility_score']} / 5",
                f"内容类型：{_display_text(event['content_type'])}",
                "",
                "摘要：",
                _display_text(event["summary_cn"]),
                "",
                "为什么重要：",
                _display_text(event["reason"]),
                "",
                "写作角度：",
                _display_text(event["writing_angle_cn"]),
                "",
                "编辑风险：",
                _display_text(event["risk_note_cn"], "无特别风险提示。"),
            ]
        )

        if event["rumor"] is True:
            lines.extend(
                [
                    "",
                    "注意：该事件包含传闻或未经官方确认信息，发布时需谨慎表述。",
                ]
            )

        lines.extend(["", "主要来源：", ""])
        for article in event["articles"]:
            source_name = _display_text(article["source_name"], "未知来源")
            source_tier = _display_text(article["source_tier"], "unknown")
            published_time = _display_text(article["published_time"], "时间未知")
            lines.extend(
                [
                    f"- {source_name}｜{source_tier}｜{published_time}",
                    f"  {article['url']}",
                ]
            )
        lines.append("")

    lines.extend(["## 【备选素材】", ""])
    if backup_events:
        for event in backup_events:
            lines.extend(
                [
                    f"- {_headline(event)}",
                    f"  分类：{event['primary_category']}｜原因：{_display_text(event['reason'])}",
                ]
            )
    else:
        lines.append("本次没有符合条件的 C 级推荐事件。")

    excluded = data["excluded_articles"]
    lines.extend(
        [
            "",
            "## 【排除 / 不建议入选】",
            "",
            f"本次共有 {len(excluded)} 篇文章被排除。主要原因包括："
            f"{_summarize_exclusion_reasons(excluded)}",
            "",
            "## 【编辑备注】",
            "",
        ]
    )

    if data["editor_notes"]:
        lines.extend(f"- {note}" for note in data["editor_notes"])
    else:
        lines.append("无。")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(
    json_draft: dict,
    markdown_draft: str,
    markdown_path: Path = OUTPUT_MARKDOWN,
    json_path: Path = OUTPUT_JSON,
) -> None:
    """Create the output directory and write both draft formats."""
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown_draft, encoding="utf-8")
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(json_draft, f, ensure_ascii=False, indent=2)
        f.write("\n")


def run() -> dict:
    data = load_classification()
    headline_events, backup_events = select_events(data["event_clusters"])
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    json_draft = build_json_draft(
        data,
        headline_events,
        backup_events,
        generated_at,
    )
    markdown_draft = build_markdown_draft(
        data,
        headline_events,
        backup_events,
        generated_at,
    )
    write_outputs(json_draft, markdown_draft)
    return json_draft


def main() -> int:
    try:
        draft = run()
    except BriefingInputError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"错误：无法写入日报草稿：{exc}", file=sys.stderr)
        return 1

    print(f"读取 event_clusters 数量: {draft['source_event_count']}")
    print(f"入选 A/B 数量: {draft['selected_event_count']}")
    print(f"备选 C 数量: {draft['backup_event_count']}")
    print(f"excluded_articles 数量: {draft['excluded_article_count']}")
    print(f"Markdown 输出路径: {OUTPUT_MARKDOWN}")
    print(f"JSON 输出路径: {OUTPUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
