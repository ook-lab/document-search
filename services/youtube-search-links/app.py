"""Cloud Run: YouTube / Web 検索 → URL 一覧（NotebookLM 貼り付け用）."""

from __future__ import annotations

from flask import Flask, render_template, request

from fetch_urls import fetch_youtube_search_urls, urls_as_text
from fetch_web import fetch_web_search_urls

app = Flask(__name__)


@app.route("/", methods=["GET", "POST"])
def index():
    query = ""
    max_results = 20
    search_mode = "youtube"
    output_text = ""
    error = ""
    if request.method == "POST":
        query = (request.form.get("query") or "").strip()
        search_mode = (request.form.get("search_mode") or "youtube").strip()
        if search_mode not in ("youtube", "web"):
            search_mode = "youtube"
        try:
            max_results = int(request.form.get("max_results") or 20)
        except ValueError:
            max_results = 20
        max_results = max(1, min(max_results, 50))
        if not query:
            error = "検索語を入力してください。"
        else:
            try:
                if search_mode == "web":
                    urls = fetch_web_search_urls(query, max_results)
                    output_text = urls_as_text(urls) if urls else ""
                    if not urls:
                        error = "該当するページがありませんでした。"
                else:
                    urls = fetch_youtube_search_urls(query, max_results)
                    output_text = urls_as_text(urls) if urls else ""
                    if not urls:
                        error = "該当する動画がありませんでした。"
            except RuntimeError as e:
                error = str(e) or "取得に失敗しました。"
    return render_template(
        "index.html",
        query=query,
        max_results=max_results,
        search_mode=search_mode,
        output_text=output_text,
        error=error,
    )
