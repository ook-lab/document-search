"""CLI: 検索・チャンネル・再生リスト・Web の URL を 1 行 1 本で出力（NotebookLM 用）."""

from __future__ import annotations

import argparse
import sys

from fetch_urls import (
    fetch_youtube_flat_urls,
    fetch_youtube_search_urls,
    normalize_channel_videos_url,
    urls_as_text,
)
from fetch_web import fetch_web_search_urls


def main() -> int:
    p = argparse.ArgumentParser(
        description="YouTube（キーワード / チャンネル / 再生リスト）または Web 検索の URL を出力します。"
    )
    p.add_argument(
        "-m",
        "--mode",
        choices=("youtube-search", "web", "youtube-channel", "youtube-playlist"),
        default="youtube-search",
        help="取得モード（既定: youtube-search）",
    )
    p.add_argument(
        "query",
        nargs="?",
        default="",
        help="検索語（youtube-search / web）",
    )
    p.add_argument(
        "--url",
        dest="source_url",
        default="",
        metavar="URL",
        help="チャンネルまたは再生リストの URL（youtube-channel / youtube-playlist）",
    )
    p.add_argument(
        "-n",
        "--max-results",
        type=int,
        default=20,
        metavar="N",
        help="最大件数（1〜200、既定 20）",
    )
    p.add_argument("--date-after", dest="date_after", default=None, metavar="YYYY-MM-DD", help="アップロード日・以降（任意）")
    p.add_argument("--date-before", dest="date_before", default=None, metavar="YYYY-MM-DD", help="アップロード日・以前（任意）")
    p.add_argument(
        "--order",
        choices=("newest", "original"),
        default="newest",
        help="チャンネル・再生リストの並び（既定: newest = playlist-reverse）",
    )
    args = p.parse_args()

    rev = args.order == "newest"
    try:
        if args.mode == "web":
            q = (args.query or "").strip()
            if not q:
                print("検索語を指定してください。", file=sys.stderr)
                return 2
            urls = fetch_web_search_urls(q, args.max_results)
        elif args.mode == "youtube-search":
            q = (args.query or "").strip()
            if not q:
                print("検索語を指定してください。", file=sys.stderr)
                return 2
            urls = fetch_youtube_search_urls(q, args.max_results)
        elif args.mode == "youtube-channel":
            u = (args.source_url or "").strip()
            if not u:
                print("--url でチャンネル URL を指定してください。", file=sys.stderr)
                return 2
            ch = normalize_channel_videos_url(u)
            urls = fetch_youtube_flat_urls(
                ch,
                args.max_results,
                dateafter=args.date_after,
                datebefore=args.date_before,
                playlist_reverse=rev,
            )
        else:
            u = (args.source_url or "").strip()
            if not u:
                print("--url で再生リスト URL を指定してください。", file=sys.stderr)
                return 2
            urls = fetch_youtube_flat_urls(
                u,
                args.max_results,
                dateafter=args.date_after,
                datebefore=args.date_before,
                playlist_reverse=rev,
            )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    if not urls:
        print("結果がありません。", file=sys.stderr)
        return 3
    print(urls_as_text(urls))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
