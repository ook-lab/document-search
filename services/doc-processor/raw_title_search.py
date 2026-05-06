"""raw の title / file_name / subject でキーワード検索（09 を使わない）。"""

from __future__ import annotations

from typing import Any, List, Optional, Set


def raw_ids_for_title_keyword(db: Any, q: str) -> Optional[List[str]]:
    """キーワードに一致する raw.id のリスト。一致なしなら空リスト、q 空なら None。"""
    safe = (q or "").replace("%", "").replace(",", " ").strip()
    if not safe:
        return None
    pattern = f"%{safe}%"
    found: Set[str] = set()
    specs = [
        ("01_gmail_01_raw", lambda: db.client.table("01_gmail_01_raw").select("id").ilike("header_subject", pattern)),
        ("02_gcal_01_raw", lambda: db.client.table("02_gcal_01_raw").select("id").ilike("summary", pattern)),
        (
            "03_ema_classroom_01_raw",
            lambda: db.client.table("03_ema_classroom_01_raw")
            .select("id")
            .or_(f"title.ilike.{pattern},file_name.ilike.{pattern}"),
        ),
        (
            "04_ikuya_classroom_01_raw",
            lambda: db.client.table("04_ikuya_classroom_01_raw")
            .select("id")
            .or_(f"title.ilike.{pattern},file_name.ilike.{pattern}"),
        ),
        (
            "05_ikuya_waseaca_01_raw",
            lambda: db.client.table("05_ikuya_waseaca_01_raw")
            .select("id")
            .or_(f"title.ilike.{pattern},file_name.ilike.{pattern}"),
        ),
        (
            "08_file_only_01_raw",
            lambda: db.client.table("08_file_only_01_raw")
            .select("id")
            .or_(f"title.ilike.{pattern},file_name.ilike.{pattern}"),
        ),
    ]
    for rt, build_q in specs:
        try:
            res = build_q().execute()
            for row in res.data or []:
                if row.get("id"):
                    found.add(str(row["id"]))
        except Exception:
            continue
    return list(found)
