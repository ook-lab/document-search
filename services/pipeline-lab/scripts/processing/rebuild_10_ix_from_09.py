"""
09_unified_documents の正本（body / ui_data / title）から 10_ix_search_index を再生成する。

派生データの作り直し。バックアップからの復元ではない。

【使い方】
  python scripts/rebuild_10_ix_from_09.py              # dry-run（件数と先頭数件）
  python scripts/rebuild_10_ix_from_09.py --execute    # 全 09 を処理
  python scripts/rebuild_10_ix_from_09.py --execute --limit 5
  python scripts/rebuild_10_ix_from_09.py --execute --raw-table 05_ikuya_waseaca_01_raw
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_lab_dir = Path(__file__).resolve().parent.parent.parent  # services/pipeline-lab/
if str(_lab_dir) not in sys.path:
    sys.path.insert(0, str(_lab_dir))

import requests
from loguru import logger

from dms.common.config.model_tiers import get_model_config
from dms.common.config.settings import settings
from dms.common.database.client import DatabaseClient
from dms.common.utils.chunking import TextChunker
from dms.pipeline.stage_k_embedding import StageKEmbedding


class _EmbeddingOnlyClient:
    """Gemini gemini-embedding-2 による埋め込みクライアント。"""

    def __init__(self) -> None:
        import os
        key = (os.environ.get("GOOGLE_AI_PAID_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("GOOGLE_AI_PAID_API_KEY が未設定です")
        from google import genai
        self._client = genai.Client(api_key=key)
        self._model = "gemini-embedding-2"
        self._dimensions = 1536

    def generate_embedding(self, text: str, log_context=None) -> List[float]:  # noqa: ANN001
        _ = log_context
        if not text or not str(text).strip():
            raise ValueError("空のテキストはembedding化できません")
        from google.genai import types
        content_payload = f"title: none | text: {str(text).strip()}"
        response = self._client.models.embed_content(
            model=self._model,
            contents=content_payload,
            config=types.EmbedContentConfig(output_dimensionality=self._dimensions),
        )
        if not response.embeddings or len(response.embeddings) != 1:
            cnt = len(response.embeddings) if response.embeddings else 0
            raise ValueError(f"Invalid embedding response: expected 1 embedding, got {cnt}")
        emb = response.embeddings[0]
        values = getattr(emb, "values", None)
        if values is None or len(values) != self._dimensions:
            dim = len(values) if values else 0
            raise ValueError(f"Invalid embedding dimensionality: expected {self._dimensions}, got {dim}")
        return list(values)


def _ui_data_dict(ui_data: Any) -> Optional[dict]:
    if ui_data is None:
        return None
    if isinstance(ui_data, dict):
        return ui_data
    if isinstance(ui_data, str) and ui_data.strip():
        try:
            return json.loads(ui_data)
        except json.JSONDecodeError:
            return None
    return None


def extract_searchable_text(ud: Dict[str, Any]) -> str:
    """09 行から検索用に並べるプレーンテキストを組み立てる。"""
    body = (ud.get("body") or "").strip()
    if body:
        return body

    ui = _ui_data_dict(ud.get("ui_data"))
    if ui:
        parts: List[str] = []
        for sec in ui.get("sections") or []:
            if isinstance(sec, dict):
                t = (sec.get("title") or "").strip()
                b = (sec.get("body") or "").strip()
                if t or b:
                    parts.append(f"{t}\n{b}".strip())
        for ev in ui.get("timeline") or []:
            if isinstance(ev, dict):
                line = " ".join(
                    str(x).strip()
                    for x in (
                        ev.get("event"),
                        ev.get("date"),
                        ev.get("location"),
                        ev.get("description"),
                    )
                    if x
                ).strip()
                if line:
                    parts.append(line)
        merged = "\n\n".join(parts).strip()
        if merged:
            return merged

    title = (ud.get("title") or "").strip()
    snippet = (ud.get("snippet") or "").strip()
    if title or snippet:
        return "\n\n".join(x for x in (title, snippet) if x).strip()
    return ""


def fetch_09_rows(db: DatabaseClient, *, raw_table: Optional[str], limit: Optional[int]) -> List[Dict[str, Any]]:
    q = db.client.table("09_unified_documents").select(
        "id,raw_table,body,ui_data,person,classification1,classification2,classification3,title,snippet"
    )
    if raw_table:
        q = q.eq("raw_table", raw_table)
    if limit is not None:
        q = q.limit(limit)
    res = q.execute()
    return list(res.data or [])


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild 10_ix from 09 canonical text")
    parser.add_argument("--execute", action="store_true", help="実際に 10_ix を書き込む")
    parser.add_argument("--limit", type=int, default=None, help="処理件数上限")
    parser.add_argument("--raw-table", type=str, default=None, help="raw_table で絞り込み")
    args = parser.parse_args()

    db = DatabaseClient(use_service_role=True)
    rows = fetch_09_rows(db, raw_table=args.raw_table, limit=args.limit)
    logger.info("09 取得: {} 件", len(rows))

    if not args.execute:
        for r in rows[:15]:
            tid = str(r.get("id", ""))
            text = extract_searchable_text(r)
            logger.info(
                "  {} | {} | chars={}",
                tid[:8],
                r.get("raw_table"),
                len(text),
            )
        if len(rows) > 15:
            logger.info("  ... 他 {} 件", len(rows) - 15)
        logger.info("[dry-run] --execute で 10_ix を再生成します")
        return 0

    embed_client = _EmbeddingOnlyClient()
    stage_k = StageKEmbedding(embed_client, db)
    chunker = TextChunker(chunk_size=800, chunk_overlap=100)

    ok = 0
    skipped = 0
    failed = 0

    for ud in rows:
        doc_id = str(ud["id"])
        text = extract_searchable_text(ud)
        if not text:
            logger.warning("skip (no text): {} raw_table={}", doc_id, ud.get("raw_table"))
            skipped += 1
            continue
        raw_chunks = chunker.split_text(text)
        chunks = [
            {
                "chunk_index": c["chunk_index"],
                "chunk_text": c["chunk_text"],
                "chunk_type": "rebuild_from_09_plain",
                "search_weight": 1.0,
            }
            for c in raw_chunks
        ]
        if not chunks:
            skipped += 1
            continue
        try:
            result = stage_k.embed_and_save(
                doc_id,
                chunks,
                person=ud.get("person"),
                classification1=ud.get("classification1"),
                classification2=ud.get("classification2"),
                classification3=ud.get("classification3"),
                delete_existing=True,
            )
            if result.get("success"):
                ok += 1
                logger.info("OK {} chunks={}", doc_id, result.get("saved_count"))
            else:
                failed += 1
                logger.error("PARTIAL {} errors={}", doc_id, result.get("errors"))
        except Exception as e:
            failed += 1
            logger.error("FAIL {}: {}", doc_id, e)

    logger.info("done ok={} skipped={} failed={}", ok, skipped, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
