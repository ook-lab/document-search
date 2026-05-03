"""YouTube: 検索・チャンネル・再生リストから動画 URL を取得 (yt-dlp)."""

from __future__ import annotations

import re
import subprocess
import sys
from typing import List, Optional
from urllib.parse import urlparse, urlunparse

_MAX_ITEMS = 200
_PLAYLIST_SCAN_CAP = 2000


def urls_as_text(urls: List[str]) -> str:
    return "\n".join(urls)


def _has_list_param(url: str) -> bool:
    return bool(re.search(r"[?&]list=", url, re.I))


def _is_watch_url(path: str) -> bool:
    return path.rstrip("/") == "/watch" or path.endswith("/watch")


def normalize_date_ytdlp(s: Optional[str]) -> Optional[str]:
    if not s or not str(s).strip():
        return None
    t = str(s).strip().replace("-", "")
    if len(t) != 8 or not t.isdigit():
        raise RuntimeError(
            f"日付の形式が不正です: {s!r}（YYYY-MM-DD または YYYYMMDD）"
        )
    return t


def normalize_channel_videos_url(url: str) -> str:
    u = url.strip()
    if not u:
        return u
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    parsed = urlparse(u)
    path = parsed.path.rstrip("/")
    if _has_list_param(u) and (
        _is_watch_url(path) or "/playlist" in path.lower() or path.rstrip("/").endswith("playlist")
    ):
        raise RuntimeError(
            "この URL は再生リスト用です。取得モードで「YouTube 再生リスト」を選んでください。"
        )

    for suf in ("/videos", "/shorts", "/streams", "/live"):
        if path.endswith(suf):
            return u

    if path and path != "/" and (
        "/@" in path or "/channel/" in path or re.search(r"^/c/", path, re.I) or "/user/" in path
    ):
        new_path = path + "/videos"
        return urlunparse(
            (parsed.scheme or "https", parsed.netloc, new_path, "", "", "")
        )
    return u


def fetch_youtube_search_urls(query: str, max_results: int = 20) -> List[str]:
    q = (query or "").strip()
    if not q:
        return []
    n = max(1, min(int(max_results), _MAX_ITEMS))
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
    return _run_ytdlp_url_command(cmd)


def fetch_youtube_flat_urls(
    page_url: str,
    max_results: int,
    *,
    dateafter: Optional[str] = None,
    datebefore: Optional[str] = None,
    playlist_reverse: bool = False,
) -> List[str]:
    u = (page_url or "").strip()
    if not u:
        return []

    n = max(1, min(int(max_results), _MAX_ITEMS))
    daf = (dateafter or "").strip() or None
    dbf = (datebefore or "").strip() or None
    da = normalize_date_ytdlp(daf) if daf else None
    db = normalize_date_ytdlp(dbf) if dbf else None
    if da and db and da > db:
        raise RuntimeError("開始日が終了日より後になっています。")

    if da or db:
        window = min(_PLAYLIST_SCAN_CAP, max(n * 40, n + 80))
    else:
        window = n

    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--flat-playlist",
    ]
    if playlist_reverse:
        cmd.append("--playlist-reverse")
    cmd.extend(
        [
            "--playlist-items",
            f"1:{window}",
        ]
    )
    if da:
        cmd.extend(["--dateafter", da])
    if db:
        cmd.extend(["--datebefore", db])
    cmd.extend(
        [
            "--print",
            "%(url)s",
            "--no-warnings",
            "--quiet",
            u,
        ]
    )
    lines = _run_ytdlp_url_command(cmd)
    return lines[:n]


def _run_ytdlp_url_command(cmd: List[str]) -> List[str]:
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
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
