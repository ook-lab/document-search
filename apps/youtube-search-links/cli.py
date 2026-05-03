"""CLI: 検索・チャンネル・再生リスト・Web の URL を 1 行 1 本で出力（NotebookLM 用）."""

from __future__ import annotations

import argparse
import sys

from fetch_urls import (
    effective_web_period,
    effective_youtube_upload_dates,
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
    p.add_argument(
        "--date-after",
        dest="date_after",
        default=None,
        metavar="YYYY-MM-DD",
        help="開始日（任意）",
    )
    p.add_argument(
        "--date-before",
        dest="date_before",
        default=None,
        metavar="YYYY-MM-DD",
        help="終了日（任意）",
    )
    p.add_argument(
        "--period",
        choices=("d", "w", "m", "y"),
        default=None,
        help="日付未指定時のみ有効：ざっくり期間（Web=timelimit、YouTube=おおよその公開日）",
    )
    p.add_argument(
        "--order",
        choices=("newest", "original"),
        default="newest",
        help="チャンネル・再生リストの並び（既定: newest）",
    )
    args = p.parse_args()

    rev = args.order == "newest"
    yt_df, yt_dt = effective_youtube_upload_dates(
        args.date_after, args.date_before, args.period
    )
    web_da, web_db, web_tl = effective_web_period(
        args.date_after, args.date_before, args.period
    )

    try:
        if args.mode == "web":
            q = (args.query or "").strip()
            if not q:
                print("検索語を指定してください。", file=sys.stderr)
                return 2
            urls = fetch_web_search_urls(
                q,
                args.max_results,
                timelimit=web_tl,
                date_after=web_da,
                date_before=web_db,
            )
        elif args.mode == "youtube-search":
            q = (args.query or "").strip()
            if not q:
                print("検索語を指定してください。", file=sys.stderr)
                return 2
            urls = fetch_youtube_search_urls(
                q,
                args.max_results,
                dateafter=yt_df,
                datebefore=yt_dt,
            )
        elif args.mode == "youtube-channel":
            u = (args.source_url or "").strip()
            if not u:
                print("--url でチャンネル URL を指定してください。", file=sys.stderr)
                return 2
            ch = normalize_channel_videos_url(u)
            urls = fetch_youtube_flat_urls(
                ch,
                args.max_results,
                dateafter=yt_df,
                datebefore=yt_dt,
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
                dateafter=yt_df,
                datebefore=yt_dt,
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
