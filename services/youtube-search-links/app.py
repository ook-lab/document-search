"""Cloud Run: YouTube（検索・チャンネル・再生リスト）/ Web 検索 → URL 一覧."""

from __future__ import annotations

from flask import Flask, render_template, request

from fetch_urls import (
    effective_web_period,
    effective_youtube_upload_dates,
    fetch_youtube_flat_urls,
    fetch_youtube_search_urls,
    normalize_channel_videos_url,
    urls_as_text,
)
from fetch_web import fetch_web_search_urls

app = Flask(__name__)


@app.route("/", methods=["GET", "POST"])
def index():
    search_mode = "youtube"
    youtube_source = "search"
    query = ""
    source_url = ""
    date_from = ""
    date_to = ""
    period_preset = ""
    yt_order = "newest"
    max_results = 20
    output_text = ""
    error = ""

    if request.method == "POST":
        search_mode = (request.form.get("search_mode") or "youtube").strip()
        if search_mode not in ("youtube", "web"):
            search_mode = "youtube"
        youtube_source = (request.form.get("youtube_source") or "search").strip()
        if youtube_source not in ("search", "channel", "playlist"):
            youtube_source = "search"

        query = (request.form.get("query") or "").strip()
        source_url = (request.form.get("source_url") or "").strip()
        date_from = (request.form.get("date_from") or "").strip()
        date_to = (request.form.get("date_to") or "").strip()
        period_preset = (request.form.get("period_preset") or "").strip()
        yt_order = (request.form.get("yt_order") or "newest").strip()
        if yt_order not in ("newest", "original"):
            yt_order = "newest"

        try:
            max_results = int(request.form.get("max_results") or 20)
        except ValueError:
            max_results = 20
        max_results = max(1, min(max_results, 200))

        playlist_reverse = yt_order == "newest"
        yt_df, yt_dt = effective_youtube_upload_dates(date_from, date_to, period_preset)
        web_da, web_db, web_tl = effective_web_period(date_from, date_to, period_preset)

        try:
            if search_mode == "web":
                if not query:
                    error = "検索語を入力してください。"
                else:
                    urls = fetch_web_search_urls(
                        query,
                        max_results,
                        timelimit=web_tl,
                        date_after=web_da,
                        date_before=web_db,
                    )
                    output_text = urls_as_text(urls) if urls else ""
                    if not urls:
                        error = "該当するページがありませんでした。"
            elif youtube_source == "search":
                if not query:
                    error = "検索語を入力してください。"
                else:
                    urls = fetch_youtube_search_urls(
                        query,
                        max_results,
                        dateafter=yt_df,
                        datebefore=yt_dt,
                    )
                    output_text = urls_as_text(urls) if urls else ""
                    if not urls:
                        error = "該当する動画がありませんでした。"
            elif youtube_source == "channel":
                if not source_url:
                    error = "チャンネル URL を入力してください。"
                else:
                    ch_url = normalize_channel_videos_url(source_url)
                    urls = fetch_youtube_flat_urls(
                        ch_url,
                        max_results,
                        dateafter=yt_df,
                        datebefore=yt_dt,
                        playlist_reverse=playlist_reverse,
                    )
                    output_text = urls_as_text(urls) if urls else ""
                    if not urls:
                        error = "条件に合う動画がありませんでした。"
            else:
                if not source_url:
                    error = "再生リストの URL を入力してください。"
                else:
                    urls = fetch_youtube_flat_urls(
                        source_url,
                        max_results,
                        dateafter=yt_df,
                        datebefore=yt_dt,
                        playlist_reverse=playlist_reverse,
                    )
                    output_text = urls_as_text(urls) if urls else ""
                    if not urls:
                        error = "条件に合う動画がありませんでした。"
        except RuntimeError as e:
            error = str(e) or "取得に失敗しました。"

    return render_template(
        "index.html",
        search_mode=search_mode,
        youtube_source=youtube_source,
        query=query,
        source_url=source_url,
        date_from=date_from,
        date_to=date_to,
        period_preset=period_preset,
        yt_order=yt_order,
        max_results=max_results,
        output_text=output_text,
        error=error,
    )
