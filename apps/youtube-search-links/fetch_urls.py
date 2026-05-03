"""YouTube 検索結果から動画 URL を上から順に取得する共通処理."""

from __future__ import annotations

import subprocess
import sys
from typing import List


def fetch_youtube_search_urls(query: str, max_results: int = 20) -> List[str]:
    """検索語と件数で YouTube 検索し、表示順の watch URL のリストを返す。"""
    q = (query or "").strip()
    if not q:
        return []
    n = max(1, min(int(max_results), 50))
    search = f"ytsearch{n}:{q}"
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--flat-playlist",
        "--print",
        "%(url)s",
        "--no-warnings",
        "--quiet",
        search,
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(err or f"yt-dlp exited with {proc.returncode}")
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    return lines


def urls_as_text(urls: List[str]) -> str:
    return "\n".join(urls)
