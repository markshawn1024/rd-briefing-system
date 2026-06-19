#!/usr/bin/env python3
"""CLI helper to test article detail extraction for a single URL."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from article_extractor import extract_article_details  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(
            "Usage: python scripts/test_article_extractor.py <url>",
            file=sys.stderr,
        )
        return 1

    url = sys.argv[1].strip()
    if not url:
        print("Error: URL must not be empty.", file=sys.stderr)
        return 1

    result = extract_article_details(url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("extraction_status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
