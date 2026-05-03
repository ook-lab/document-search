"""CLI: 検索結果の URL を 1 行 1 URL で標準出力に出す（NotebookLM へそのまま貼り付け用）."""

from __future__ import annotations

import argparse
import sys

from fetch_urls import fetch_youtube_search_urls, urls_as_text
from fetch_web import fetch_web_search_urls


def main() -> int:
    p = argparse.ArgumentParser(
        description="YouTube または Web（DuckDuckGo）検索の上位から URL を 1 行 1 本で出力します。"
    )
    p.add_argument("query", help="検索語（スペース区切りは引用で囲む）")
    p.add_argument(
        "-m",
        "--mode",
        choices=("youtube", "web"),
        default="youtube",
        help="youtube（既定）または web",
    )
    p.add_argument(
        "-n",
        "--max-results",
        type=int,
        default=20,
        metavar="N",
        help="取得する最大件数（1〜50、既定 20）",
    )
    args = p.parse_args()
    try:
        if args.mode == "web":
            urls = fetch_web_search_urls(args.query, args.max_results)
        else:
            urls = fetch_youtube_search_urls(args.query, args.max_results)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    if not urls:
        print("結果がありません。", file=sys.stderr)
        return 2
    print(urls_as_text(urls))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
