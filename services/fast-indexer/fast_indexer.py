import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from shared.ai.embeddings.embeddings import EmbeddingClient
from shared.common.database.client import DatabaseClient

from fast_index_scope import FAST_INDEX_RAW_TABLES

logger = logging.getLogger(__name__)

DRIVE_URL_RE = re.compile(r"/d/([a-zA-Z0-9_-]+)")


class FastIndexer:
    def __init__(self):
        self.db = DatabaseClient(use_service_role=True)
        self.embedder = EmbeddingClient()

    def process_document(self, pipeline_id: str) -> tuple[bool, Optional[str]]:
        """OCR をスキップし、既存テキストから 10_ix_search_index のみ更新する。"""
        try:
            res = (
                self.db.client.table("pipeline_meta")
                .select("*")
                .eq("id", pipeline_id)
                .single()
                .execute()
            )
            if not res.data:
                logger.error("Document not found: %s", pipeline_id)
                return False, "pipeline_meta が見つかりません"

            doc = res.data
            rt = doc.get("raw_table") or ""
            if rt not in FAST_INDEX_RAW_TABLES:
                logger.error(
                    "fast-indexer は raw_table が許可セット内の pipeline_meta のみ対象です: %s",
                    rt,
                )
                return False, "この raw_table は fast-indexer の対象外です"

            raw_id = doc.get("raw_id")
            if not raw_id:
                logger.error("pipeline_meta.raw_id がありません: %s", pipeline_id)
                return False, "raw_id がありません"

            full_markdown, md_err = self._resolve_markdown(doc)
            if not full_markdown:
                logger.error("インデックス用の本文がありません: %s", pipeline_id)
                return False, md_err or "インデックス用の本文がありません（09 / raw / md_content）"

            ud_res = (
                self.db.client.table("09_unified_documents")
                .select("id, person, source, category")
                .eq("raw_id", raw_id)
                .eq("raw_table", rt)
                .limit(1)
                .execute()
            )
            ud_rows = ud_res.data or []
            if not ud_rows:
                logger.error(
                    "09_unified_documents に行がありません raw_id=%s raw_table=%s",
                    raw_id,
                    rt,
                )
                return False, "09_unified_documents に行がありません（先に統合行が必要です）"

            unified = ud_rows[0]
            unified_doc_id = unified["id"]
            person = unified.get("person") or doc.get("person")
            source = unified.get("source") or doc.get("source")
            category = unified.get("category") or doc.get("raw_table")

            self.db.client.table("09_unified_documents").update({"body": full_markdown}).eq(
                "id", unified_doc_id
            ).execute()

            chunks = self._plain_chunks(full_markdown, chunk_size=1200)
            self.db.client.table("10_ix_search_index").delete().eq("doc_id", unified_doc_id).execute()

            for i, chunk_text in enumerate(chunks):
                chunk_text = (chunk_text or "").replace("\u0000", "").strip()
                if not chunk_text:
                    continue
                vector = self.embedder.generate_embedding(chunk_text)
                self.db.client.table("10_ix_search_index").insert(
                    {
                        "doc_id": unified_doc_id,
                        "person": person,
                        "source": source,
                        "category": category,
                        "chunk_index": i,
                        "chunk_text": chunk_text,
                        "chunk_type": "fast_index_plain",
                        "chunk_weight": 1.0,
                        "embedding": vector,
                    }
                ).execute()

            now_iso = datetime.now(timezone.utc).isoformat()
            self.db.client.table("pipeline_meta").update(
                {
                    "processing_status": "completed",
                    "completed_at": now_iso,
                    "text_embedded": True,
                    "text_embedded_at": now_iso,
                }
            ).eq("id", pipeline_id).execute()

            logger.info("Successfully fast-indexed: %s", pipeline_id)
            return True, None

        except Exception as e:
            logger.error("Fast index failed: %s", e, exc_info=True)
            return False, str(e)

    def _drive_id_from_pm_or_raw(self, pm: Dict[str, Any]) -> Optional[str]:
        did = (pm.get("drive_file_id") or "").strip()
        if did:
            return did
        raw_table = pm.get("raw_table")
        raw_id = pm.get("raw_id")
        if not raw_table or not raw_id:
            return None
        try:
            raw = (
                self.db.client.table(raw_table)
                .select("file_url")
                .eq("id", raw_id)
                .single()
                .execute()
                .data
            )
        except Exception:
            return None
        if not raw:
            return None
        fu = raw.get("file_url")
        if not fu:
            return None
        m = DRIVE_URL_RE.search(str(fu))
        return m.group(1) if m else None

    def _resolve_markdown(self, pm: Dict[str, Any]) -> tuple[str, Optional[str]]:
        md = (pm.get("md_content") or "").strip()
        if md:
            return md, None

        raw_table = pm.get("raw_table")
        raw_id = pm.get("raw_id")
        if raw_table and raw_id:
            try:
                ud = (
                    self.db.client.table("09_unified_documents")
                    .select("body")
                    .eq("raw_id", raw_id)
                    .eq("raw_table", raw_table)
                    .limit(1)
                    .execute()
                )
                if ud.data and (ud.data[0].get("body") or "").strip():
                    return str(ud.data[0]["body"]).strip(), None
            except Exception as e:
                logger.warning("09 からの body 取得に失敗: %s", e)

            try:
                raw_row = (
                    self.db.client.table(raw_table)
                    .select("*")
                    .eq("id", raw_id)
                    .single()
                    .execute()
                    .data
                    or {}
                )
                body = (
                    raw_row.get("description")
                    or raw_row.get("content")
                    or raw_row.get("body")
                    or ""
                )
                if str(body).strip():
                    return str(body).strip(), None
            except Exception as e:
                logger.warning("raw からの本文取得に失敗: %s", e)

        drive_id = self._drive_id_from_pm_or_raw(pm)
        if drive_id:
            msg = (
                "PDF（Drive）由来の本文はこのサービスでは未対応です。"
                " pipeline_meta.md_content を設定するか、別経路でテキスト化してください。"
            )
            logger.error("%s drive_file_id=%s", msg, drive_id)
            return "", msg
        return "", None

    @staticmethod
    def _plain_chunks(text: str, chunk_size: int = 1200) -> List[str]:
        t = text.strip()
        if not t:
            return []
        return [t[i : i + chunk_size] for i in range(0, len(t), chunk_size)]
