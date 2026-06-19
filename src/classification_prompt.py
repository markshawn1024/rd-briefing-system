"""
Racing Dispatch F1 — classification prompt generator (v0.5).

Reads outputs/articles_latest.json and writes a ChatGPT-ready prompt to
outputs/classification_prompt.txt. Does not call any external API.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUT_JSON = ROOT / "outputs" / "articles_latest.json"
OUTPUT_PROMPT = ROOT / "outputs" / "classification_prompt.txt"

RAW_TEXT_EXCERPT_LEN = 1800

RD_CATEGORIES = [
    "01｜赛事动态 / 赛果 / 排位 / 冲刺赛",
    "02｜FIA文件 / 处罚 / 赛会程序",
    "03｜技术分析 / 赛车升级 / 规则解读",
    "04｜策略 / 轮胎 / 天气 / 赛道特性",
    "05｜积分榜 / 争冠形势 / 数据纪录",
    "06｜车队与车手动态",
    "07｜合约 / 转会 / 赞助 / 商业合作",
    "08｜车队运营 / 管理层 / 组织变动",
    "09｜F1治理 / 赛历政策 / 商业结构",
    "10｜青年赛事 / 青训体系 / 新秀观察",
    "11｜围场舆情 / 社区讨论",
    "12｜媒体内容 / 转播 / 影像资料",
    "13｜赛车游戏 / 模拟器 / 赛车文化 / 模型收藏",
    "14｜车手私人账号动态",
]

PROMPT_HEADER = """你是 Racing Dispatch 极速特派的 F1 新闻编辑助手。你的任务不是简单摘要，而是对候选文章进行中文编辑分类、事件聚类、重要性判断和日报入选建议。

## 分类体系

使用以下 14 个 RD 分类（primary_category 必须从中选一个，secondary_categories 最多选 2 个）：

{categories}

## 分类规则

- 每篇文章必须选择一个 primary_category。
- 可选 secondary_categories，最多 2 个。
- 如果文章是 roundup、旧闻、低价值重复内容、PR 稿、长访谈、敏感人工审核内容，要标记 should_include=false，并放入 excluded_articles。
- 如果内容涉及未经证实的传闻，要标记 rumor=true。
- 如果内容来自低等级来源但有价值，可以保留，但要降低 confidence。

## 事件聚类要求

不要只逐篇总结，而是把报道同一事件的多篇文章聚合成 event_cluster。

每个 event_cluster 需要包含：
- event_id：格式 E001、E002……
- event_title_cn：中文事件标题
- primary_category / secondary_categories
- importance_score：1–5
- cross_verification_score：1–5
- source_credibility_score：1–5（取 cluster 内最高来源可信度，或加权综合）
- articles：关联文章列表（含 title、source_name、source_tier、url、published_time）
- recommended_for_daily：true/false
- reason：入选或排除理由
- summary_cn：中文事件摘要
- suggested_headline_cn：建议日报标题

## 来源评分规则

### source_credibility_score（由 source_tier 映射）

| source_tier | source_credibility_score |
|-------------|--------------------------|
| S           | 5                        |
| A           | 4                        |
| B+          | 3                        |
| B           | 2                        |
| unknown     | 1                        |

### cross_verification_score

| 分数 | 含义 |
|------|------|
| 1 | 单一低等级来源，未交叉验证 |
| 2 | 单一可靠来源，或多个低等级来源 |
| 3 | 至少两个不同媒体来源支持 |
| 4 | 官方/高可信来源 + 媒体来源支持 |
| 5 | 多个高可信来源或官方文件确认 |

### importance_score

| 分数 | 含义 |
|------|------|
| 5 | 影响争冠、赛果、重大处罚、重大规则、重大合约/治理 |
| 4 | 重要车队/车手动向、技术升级、赛历/商业重大变化 |
| 3 | 普通新闻，有日报价值 |
| 2 | 信息较轻，适合备选 |
| 1 | 低价值、重复、边缘、只适合人工参考 |

## 输出格式

请输出严格 JSON，不要 Markdown 代码块，不要额外说明文字。

JSON 顶层结构：

{{
  "generated_for": "Racing Dispatch",
  "article_count": {article_count},
  "event_clusters": [],
  "excluded_articles": [],
  "editor_notes": []
}}

event_clusters 中每个对象：

{{
  "event_id": "E001",
  "event_title_cn": "",
  "primary_category": "",
  "secondary_categories": [],
  "importance_score": 0,
  "cross_verification_score": 0,
  "source_credibility_score": 0,
  "recommended_for_daily": true,
  "reason": "",
  "summary_cn": "",
  "suggested_headline_cn": "",
  "articles": [
    {{
      "title": "",
      "source_name": "",
      "source_tier": "",
      "url": "",
      "published_time": ""
    }}
  ]
}}

excluded_articles 中每个对象：

{{
  "title": "",
  "source_name": "",
  "url": "",
  "exclude_reason": ""
}}

## 输入文章数据

以下为 {article_count} 篇候选文章（已精简，raw_text_excerpt 为正文前 {excerpt_len} 字符）：

{articles_json}
"""


def _article_id(index: int) -> str:
    return f"A{index:03d}"


def slim_article(article: dict, index: int) -> dict:
    """Convert a full article record to the slim input format for the prompt."""
    raw_text = article.get("raw_text") or ""
    return {
        "id": _article_id(index),
        "title": article.get("title") or "",
        "clean_title": article.get("clean_title") or "",
        "source_name": article.get("source_name") or "",
        "source_tier": article.get("source_tier") or "unknown",
        "source_type": article.get("source_type") or "",
        "published_time": article.get("published_time") or "",
        "summary": article.get("summary") or "",
        "url": article.get("url") or "",
        "raw_text_excerpt": raw_text[:RAW_TEXT_EXCERPT_LEN],
    }


def load_articles(input_path: Path) -> list[dict]:
    if not input_path.exists():
        print(
            f"Error: input file not found: {input_path}\n"
            f"Run the main pipeline first to generate articles_latest.json.",
            file=sys.stderr,
        )
        sys.exit(1)

    with input_path.open(encoding="utf-8") as f:
        payload = json.load(f)

    articles = payload.get("articles")
    if articles is None:
        print(
            f"Error: {input_path} does not contain an 'articles' key.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not isinstance(articles, list):
        print(
            f"Error: 'articles' in {input_path} must be a list.",
            file=sys.stderr,
        )
        sys.exit(1)

    return articles


def build_prompt(articles: list[dict]) -> str:
    slim = [slim_article(a, i + 1) for i, a in enumerate(articles)]
    categories_block = "\n".join(f"- {cat}" for cat in RD_CATEGORIES)
    articles_json = json.dumps(slim, ensure_ascii=False, indent=2)

    return PROMPT_HEADER.format(
        categories=categories_block,
        article_count=len(slim),
        excerpt_len=RAW_TEXT_EXCERPT_LEN,
        articles_json=articles_json,
    )


def main() -> int:
    articles = load_articles(INPUT_JSON)
    prompt = build_prompt(articles)

    OUTPUT_PROMPT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PROMPT.write_text(prompt, encoding="utf-8")

    print(f"读取文章数量: {len(articles)}")
    print(f"prompt 输出路径: {OUTPUT_PROMPT}")
    print(f"prompt 字符数: {len(prompt)}")

    if len(articles) == 0:
        print("提示: articles 为空，prompt 中 article_count=0。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
