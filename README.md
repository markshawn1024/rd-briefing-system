# Racing Dispatch F1 — News Source Collector (Phase 1)

第一版新闻源采集：从配置的 F1 新闻网站读取候选标题与链接，清洗 URL，写入 SQLite，并导出本次新增文章。

## 目录结构

```
rd-briefing-system/
├── data/
│   ├── sources.csv      # 新闻源配置
│   └── rd_news.db       # SQLite 数据库（运行后生成）
├── outputs/
│   └── articles_latest.json  # 本次运行新增文章（运行后生成）
├── src/
│   ├── main.py          # 主入口与流水线编排
│   ├── source_audit.py  # 检测来源 URL 是否可访问
│   ├── crawler.py       # 抓取页面并提取候选文章
│   ├── cleaner.py       # URL 清洗（去除追踪参数）
│   └── database.py      # SQLite 读写
├── requirements.txt
└── README.md
```

## 功能范围（Phase 1）

| 步骤 | 说明 |
|------|------|
| 1 | 读取 `data/sources.csv` 中的 F1 新闻源 |
| 2 | 测试每个来源是否可访问 |
| 3 | 抓取来源页面中的候选新闻标题与链接 |
| 4 | 清洗 URL（去除 `utm_*`、`fbclid`、`gclid` 等） |
| 5 | 写入 `data/rd_news.db` |
| 6 | 导出本次新增文章到 `outputs/articles_latest.json` |

**暂不包含：** AI 分类、UI、社交媒体抓取。

## 环境要求

- Python 3.11+
- 网络访问（用于请求新闻源网站）

## 安装

```bash
cd rd-briefing-system
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 运行

```bash
python src/main.py
```

或在项目根目录：

```bash
python -m src.main
```

（若使用 `-m`，需确保项目根在 `PYTHONPATH` 中，或从 `src` 目录执行 `python main.py`。）

## 配置新闻源

编辑 `data/sources.csv`：

```csv
name,url,language,notes
Motorsport.com F1,https://www.motorsport.com/f1/,en,Main F1 section
```

## 输出说明

### `data/rd_news.db`

- **sources** — 新闻源元数据及最近一次可达性检查结果
- **articles** — 已发现文章（`url` 唯一，重复抓取会更新 `last_seen_at`）

### `outputs/articles_latest.json`

本次运行中**首次入库**的文章列表，示例结构：

```json
{
  "generated_at": "2026-06-19T12:00:00+00:00",
  "count": 42,
  "articles": [
    {
      "title": "Example headline",
      "url": "https://example.com/f1/news/example",
      "source_name": "Motorsport.com F1",
      "source_url": "https://www.motorsport.com/f1/",
      "first_seen_at": "2026-06-19T12:00:00+00:00"
    }
  ]
}
```

## 模块说明

| 模块 | 职责 |
|------|------|
| `cleaner.py` | `clean_url()` — 去除追踪参数并规范化 URL |
| `source_audit.py` | `audit_url()` — HTTP 可达性检测 |
| `crawler.py` | `crawl_source()` — 解析列表页，启发式提取文章链接 |
| `database.py` | 建表、来源 upsert、文章去重插入 |
| `main.py` | 串联上述步骤 |

## Manual AI Editorial Workflow

当前项目暂不接 OpenAI API。用户手动将 prompt 上传到 ChatGPT 对话框完成分类，再保存 JSON 并在本地校验。

### 步骤

1. **运行 crawler**

   ```bash
   python src/main.py
   ```

2. **生成 ChatGPT prompt**

   ```bash
   python src/classification_prompt.py
   ```

3. **上传或复制** `outputs/classification_prompt.txt` 到 ChatGPT 对话框。

4. **要求 ChatGPT 输出严格 JSON**（不要 Markdown 代码块，不要额外说明文字）。

5. **将 ChatGPT 输出保存为** `outputs/classified_articles.json`。

6. **本地校验**

   ```bash
   python src/validate_classification.py
   ```

7. **如果校验通过**，再人工编辑日报内容。

### 相关输出文件

| 文件 | 说明 |
|------|------|
| `outputs/articles_latest.json` | 爬虫与提取阶段产出的候选文章 |
| `outputs/classification_prompt.txt` | 供 ChatGPT 使用的分类与聚类 prompt |
| `outputs/classified_articles.json` | ChatGPT 返回的分类 JSON（手动保存） |

## 后续阶段（规划）

- 自动 API 分类（当前暂不启用）
- Web UI / 简报生成
- 社交媒体与 RSS 扩展
