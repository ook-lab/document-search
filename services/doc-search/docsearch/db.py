"""Supabase 検索専用クライアント（unified_search_v2 経路のみ）。"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timedelta, date as date_type
from typing import Any, Dict, List, Optional

from loguru import logger
from supabase import Client, create_client

from docsearch.config import settings


def _coerce_embedding_list(val: Any) -> Optional[List[float]]:
    """PostgREST / pgvector の embedding を float リストへ。"""
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        try:
            return [float(x) for x in val]
        except (TypeError, ValueError):
            return None
    if isinstance(val, str):
        s = val.strip()
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        if not s:
            return None
        try:
            return [float(x.strip()) for x in s.split(",") if x.strip()]
        except ValueError:
            return None
    return None


def _cosine_similarity(q: List[float], v: List[float]) -> float:
    if len(q) != len(v) or not q:
        return 0.0
    dot = sum(a * b for a, b in zip(q, v))
    nq = math.sqrt(sum(a * a for a in q))
    nv = math.sqrt(sum(b * b for b in v))
    if nq == 0.0 or nv == 0.0:
        return 0.0
    return dot / (nq * nv)


class DocSearchDB:
    def __init__(self, *, use_service_role: bool = True):
        if not settings.SUPABASE_URL:
            raise ValueError("SUPABASE_URL が未設定です")
        if use_service_role:
            if not settings.SUPABASE_SERVICE_ROLE_KEY:
                raise ValueError("SUPABASE_SERVICE_ROLE_KEY が未設定です")
            self.client: Client = create_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_SERVICE_ROLE_KEY,
            )
            self._is_service_role = True
        else:
            raise ValueError("doc-search は service_role 接続のみ想定です")

    def get_workspace_hierarchy(self) -> Dict[str, Dict[str, List[str]]]:
        """09 から person→source→category の階層を構築。PostgREST の 1000 行既定を超える場合はページング。"""
        try:
            hierarchy: Dict[str, Dict[str, set]] = {}
            offset = 0
            page_size = 1000
            while True:
                response = (
                    self.client.table("09_unified_documents")
                    .select("person, source, category")
                    .range(offset, offset + page_size - 1)
                    .execute()
                )
                batch = response.data or []
                for doc in batch:
                    person = (doc.get("person") or "").strip()
                    source = (doc.get("source") or "").strip()
                    cat = doc.get("category")
                    if not person or not source:
                        continue
                    hierarchy.setdefault(person, {}).setdefault(source, set())
                    if cat:
                        hierarchy[person][source].add(cat)
                if len(batch) < page_size:
                    break
                offset += page_size
            return {
                p: {s: sorted(list(cats)) for s, cats in sorted(srcs.items())}
                for p, srcs in sorted(hierarchy.items())
            }
        except Exception as e:
            logger.error("get_workspace_hierarchy: {}", e)
            return {}

    def _apply_date_filter(self, results: List[Dict[str, Any]], date_filter: str) -> List[Dict[str, Any]]:
        now = datetime.now()
        filtered_results: List[Dict[str, Any]] = []
        for result in results:
            document_date_str = result.get("document_date")
            if not document_date_str:
                if date_filter == "recent":
                    indexed_at_str = result.get("indexed_at")
                    if indexed_at_str:
                        try:
                            indexed_at = datetime.fromisoformat(indexed_at_str.replace("Z", "+00:00"))
                            if (now - indexed_at).days <= 30:
                                filtered_results.append(result)
                        except Exception:
                            pass
                continue
            try:
                document_date = datetime.strptime(document_date_str, "%Y-%m-%d")
            except Exception:
                continue
            if date_filter == "today":
                if document_date.date() == now.date():
                    filtered_results.append(result)
            elif date_filter == "this_week":
                week_start = now - timedelta(days=now.weekday())
                week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
                if document_date >= week_start:
                    filtered_results.append(result)
            elif date_filter == "this_month":
                if document_date.year == now.year and document_date.month == now.month:
                    filtered_results.append(result)
            elif date_filter == "recent":
                if (now - document_date).days <= 30:
                    filtered_results.append(result)
        return filtered_results

    def _parse_yyyy_mm_dd(self, s: Optional[Any]) -> Optional[str]:
        if s is None:
            return None
        if isinstance(s, datetime):
            return s.date().isoformat()
        if isinstance(s, date_type) and not isinstance(s, datetime):
            return s.isoformat()
        if not isinstance(s, str):
            s = str(s)
        if not s.strip():
            return None
        t = s.strip()
        if len(t) >= 10:
            t = t[:10]
        try:
            _ = date_type.fromisoformat(t)
            return t
        except Exception:
            return None

    def _coerce_meta_dict(self, meta: Any) -> Dict[str, Any]:
        if meta is None:
            return {}
        if isinstance(meta, dict):
            return meta
        if isinstance(meta, str) and meta.strip():
            try:
                o = json.loads(meta)
                return o if isinstance(o, dict) else {}
            except Exception:
                return {}
        return {}

    def _read_date_signals_from_ix(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """09.ix_date_signals のみ読む（検索側で日付を組み立てない）。"""
        raw = row.get("ix_date_signals")
        if isinstance(raw, str) and raw.strip():
            try:
                raw = json.loads(raw)
            except Exception:
                raw = None
        out: Dict[str, Any] = {
            "normalized_dates": [],
            "normalized_ranges": [],
            "partial_dates": [],
        }
        if not isinstance(raw, dict):
            return out
        nd = raw.get("normalized_dates")
        if isinstance(nd, list):
            seen: set[str] = set()
            for x in nd:
                iso = self._parse_yyyy_mm_dd(str(x))
                if iso:
                    seen.add(iso)
            out["normalized_dates"] = sorted(seen)
        nr = raw.get("normalized_ranges")
        if isinstance(nr, list):
            for r in nr:
                if not isinstance(r, dict):
                    continue
                s = self._parse_yyyy_mm_dd(str(r.get("start") or ""))
                e = self._parse_yyyy_mm_dd(str(r.get("end") or ""))
                if s and e:
                    out["normalized_ranges"].append(
                        {
                            "start": s,
                            "end": e,
                            "source_text": str(r.get("source_text") or ""),
                        }
                    )
        pd = raw.get("partial_dates")
        if isinstance(pd, list):
            for p in pd:
                if not isinstance(p, dict):
                    continue
                try:
                    out["partial_dates"].append(
                        {
                            "year": int(p.get("year")),
                            "month": int(p.get("month")),
                            "day": p.get("day"),
                            "text": str(p.get("text") or ""),
                            "granularity": str(p.get("granularity") or "month"),
                        }
                    )
                except Exception:
                    continue
        return out

    async def search_documents(
        self,
        query: str,
        embedding: List[float],
        limit: int = 50,
        sources: Optional[List[str]] = None,
        persons: Optional[List[str]] = None,
        category: Optional[List[str]] = None,
        date_filter: Optional[str] = None,
        threshold: float = 0.4,
        date_range: Optional[str] = None,
        filter_date_start: Optional[date_type] = None,
        filter_date_end: Optional[date_type] = None,
        calendar_filter_date_start: Optional[date_type] = None,
        calendar_filter_date_end: Optional[date_type] = None,
        rpc_match_count: Optional[int] = None,
        enumeration_recall: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        構成との対応:
        （1）範囲フィルタ＋RPC の日付窓＋ベクトルで候補を取り、このあと類似度しきい値で足切りし、順序は類似度のみ
            （件数上限は RPC の match_count と呼び出し側の truncate）。
        （2）主軸日付への近さによる加点・final_score 再構成は行わず、呼び出し側（例: app._apply_date_match_bonus）に委ねる。
        （3）サービスロール時は各文書の全インデックスチャンクと質問ベクトルの類似度を付け、その最大を文書の similarity とする。

        enumeration_recall: 列挙・一覧系の問い。RPC の match_count を広げて候補文書の取りこぼしを減らす。
        """
        mc = rpc_match_count if rpc_match_count is not None else settings.UNIFIED_SEARCH_RPC_MATCH_COUNT
        if enumeration_recall and rpc_match_count is None:
            mc = min(max(mc * 2, 120), 280)
        rpc_params = {
            "query_text": query,
            "query_embedding": embedding,
            "match_threshold": -1.0,
            "match_count": mc,
            "vector_weight": 0.7,
            "fulltext_weight": 0.3,
            "filter_sources": sources,
            "filter_chunk_types": None,
            "filter_persons": persons,
            "filter_category": category,
            "filter_date_start": filter_date_start.isoformat() if filter_date_start else None,
            "filter_date_end": filter_date_end.isoformat() if filter_date_end else None,
            "calendar_filter_date_start": calendar_filter_date_start.isoformat()
            if calendar_filter_date_start
            else None,
            "calendar_filter_date_end": calendar_filter_date_end.isoformat() if calendar_filter_date_end else None,
        }
        logger.debug("unified_search_v2: sources={} persons={}", sources, persons)
        response = self.client.rpc("unified_search_v2", rpc_params).execute()
        results = response.data if response.data else []
        if date_filter:
            results = self._apply_date_filter(results, date_filter)

        final_results: List[Dict[str, Any]] = []
        for result in results:
            raw_date = result.get("post_at") or result.get("start_at")
            document_date = raw_date[:10] if isinstance(raw_date, str) and len(raw_date) >= 10 else None
            date_signals = self._read_date_signals_from_ix(result)
            doc_id = result.get("doc_id")
            final_results.append(
                {
                    "id": doc_id,
                    "title": result.get("title"),
                    "source": result.get("source"),
                    "person": result.get("person"),
                    "category": result.get("category"),
                    "from_name": result.get("from_name"),
                    "from_email": result.get("from_email"),
                    "snippet": result.get("snippet"),
                    "post_at": result.get("post_at"),
                    "start_at": result.get("start_at"),
                    "end_at": result.get("end_at"),
                    "due_date": result.get("due_date"),
                    "location": result.get("location"),
                    "file_url": result.get("file_url"),
                    "ui_data": result.get("ui_data"),
                    "meta": result.get("meta"),
                    "date_signals": date_signals,
                    "ix_search_dates": result.get("ix_search_dates") or [],
                    "indexed_at": result.get("indexed_at"),
                    "document_date": document_date,
                    "document_body": "",
                    "chunk_content": result.get("best_chunk_text"),
                    "chunk_id": result.get("best_chunk_id"),
                    "chunk_index": result.get("best_chunk_index"),
                    "chunk_type": result.get("best_chunk_type"),
                    # 検索側の合成スコア。画面に出す類似度とは別に、参照用で残す。
                    "rpc_hybrid_score": float(result.get("combined_score") or 0),
                    "similarity": result.get("combined_score", 0),
                    "raw_similarity": result.get("raw_similarity", 0),
                    "weighted_similarity": result.get("weighted_similarity", 0),
                    "fulltext_score": result.get("fulltext_score", 0),
                    "title_matched": result.get("title_matched", False),
                    "chunk_score": result.get("combined_score", 0),
                    "large_chunk_id": result.get("doc_id"),
                    "small_chunk_id": result.get("best_chunk_id"),
                }
            )

        if self._is_service_role:
            for doc_result in final_results:
                doc_id = doc_result.get("id")
                if not doc_id:
                    continue
                if doc_result.get("source") == "Googleカレンダー":
                    doc_result["document_body"] = ""
                    doc_result["index_chunks_all"] = []
                    doc_result["max_chunk_vector_similarity"] = None
                    continue
                try:
                    body_response = (
                        self.client.table("09_unified_documents")
                        .select("body")
                        .eq("id", doc_id)
                        .limit(1)
                        .execute()
                    )
                    if body_response.data:
                        doc_result["document_body"] = (body_response.data[0].get("body") or "")
                    else:
                        doc_result["document_body"] = ""
                except Exception as e:
                    logger.warning("body fetch doc_id={}: {}", doc_id, e)
                    doc_result["document_body"] = ""

                try:
                    chunks_response = (
                        self.client.table("10_ix_search_index")
                        .select("id, chunk_index, chunk_text, chunk_type, chunk_weight, embedding")
                        .eq("doc_id", doc_id)
                        .order("chunk_weight", desc=True)
                        .execute()
                    )
                    if chunks_response.data:
                        raw_chunks = chunks_response.data
                        qemb = _coerce_embedding_list(embedding)
                        enriched: List[Dict[str, Any]] = []
                        for ch in raw_chunks:
                            row = {k: v for k, v in ch.items() if k != "embedding"}
                            cvec = _coerce_embedding_list(ch.get("embedding"))
                            if qemb and cvec and len(qemb) == len(cvec):
                                row["chunk_vector_similarity"] = _cosine_similarity(qemb, cvec)
                            else:
                                row["chunk_vector_similarity"] = None
                            enriched.append(row)
                        doc_result["index_chunks_all"] = sorted(
                            enriched,
                            key=lambda x: (
                                x.get("chunk_index") if x.get("chunk_index") is not None else 0,
                                str(x.get("id") or ""),
                            ),
                        )
                        sims_mc = [
                            float(x["chunk_vector_similarity"])
                            for x in enriched
                            if x.get("chunk_vector_similarity") is not None
                        ]
                        doc_result["max_chunk_vector_similarity"] = max(sims_mc) if sims_mc else None
                    else:
                        doc_result["index_chunks_all"] = []
                        doc_result["max_chunk_vector_similarity"] = None
                except Exception as e:
                    logger.warning("chunk fetch doc_id={}: {}", doc_id, e)
                    doc_result["index_chunks_all"] = []
                    doc_result["max_chunk_vector_similarity"] = None
        else:
            for doc_result in final_results:
                doc_result["index_chunks_all"] = []
                doc_result["max_chunk_vector_similarity"] = None

        for doc in final_results:
            rpc = float(doc.get("rpc_hybrid_score", doc.get("similarity", 0)) or 0)
            doc["rpc_hybrid_score"] = rpc
            mcv = doc.get("max_chunk_vector_similarity")
            if mcv is not None:
                try:
                    # 文書の類似度 = その文書内のチャンクの類似度の最大
                    doc["similarity"] = float(mcv)
                    doc["similarity_basis"] = "max_chunk_in_doc"
                except (TypeError, ValueError):
                    doc["similarity"] = None
                    doc["similarity_basis"] = "no_chunk_similarity"
            else:
                # チャンクごとの類似度が一つも計算できない文書は数を付けない
                doc["similarity"] = None
                doc["similarity_basis"] = "no_chunk_similarity"

        delta = settings.DATE_RANGE_THRESHOLD_DELTA
        effective_threshold = threshold - (float(delta) if date_range else 0.0)
        cutoff = [
            r
            for r in final_results
            if r.get("similarity") is not None and float(r["similarity"]) >= effective_threshold
        ]
        final_results = cutoff
        final_results.sort(key=lambda x: float(x["similarity"]), reverse=True)

        # 並び順は類似度のみ。日付は app 側 _apply_date_match_bonus で加点する。
        for doc in final_results:
            sim = doc.get("similarity")
            try:
                doc["final_score"] = float(sim) if sim is not None else None
            except (TypeError, ValueError):
                doc["final_score"] = None
            doc.setdefault("time_score", 0.0)
            doc.pop("rel", None)

        return final_results

    def search_documents_sync(
        self,
        query: str,
        embedding: List[float],
        limit: int = 50,
        sources: Optional[List[str]] = None,
        persons: Optional[List[str]] = None,
        category: Optional[List[str]] = None,
        date_filter: Optional[str] = None,
        threshold: float = 0.4,
        date_range: Optional[str] = None,
        filter_date_start: Optional[date_type] = None,
        filter_date_end: Optional[date_type] = None,
        calendar_filter_date_start: Optional[date_type] = None,
        calendar_filter_date_end: Optional[date_type] = None,
        rpc_match_count: Optional[int] = None,
        enumeration_recall: bool = False,
    ) -> List[Dict[str, Any]]:
        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    return loop.run_until_complete(
                        self.search_documents(
                            query,
                            embedding,
                            limit,
                            sources,
                            persons,
                            category,
                            date_filter,
                            threshold,
                            date_range,
                            filter_date_start,
                            filter_date_end,
                            calendar_filter_date_start,
                            calendar_filter_date_end,
                            rpc_match_count,
                            enumeration_recall,
                        )
                    )
            except RuntimeError:
                pass
            return asyncio.run(
                self.search_documents(
                    query,
                    embedding,
                    limit,
                    sources,
                    persons,
                    category,
                    date_filter,
                    threshold,
                    date_range,
                    filter_date_start,
                    filter_date_end,
                    calendar_filter_date_start,
                    calendar_filter_date_end,
                    rpc_match_count,
                    enumeration_recall,
                )
            )
        except Exception as e:
            logger.exception("search_documents_sync failed: {}", e)
            raise
