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
        except (TypeError, ValueError) as e:
            raise ValueError(f"埋め込みベクトルの要素をfloatに変換できません: {e}") from e
    if isinstance(val, str):
        s = val.strip()
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        if not s:
            return None
        try:
            return [float(x.strip()) for x in s.split(",") if x.strip()]
        except ValueError as e:
            raise ValueError(f"埋め込み文字列をfloatに変換できません: {e}") from e
    raise ValueError(f"埋め込みベクトルの型が不正です: {type(val).__name__}")


def _cosine_similarity(q: List[float], v: List[float]) -> float:
    if len(q) != len(v) or not q:
        return 0.0
    dot = sum(a * b for a, b in zip(q, v))
    nq = math.sqrt(sum(a * a for a in q))
    nv = math.sqrt(sum(b * b for b in v))
    if nq == 0.0 or nv == 0.0:
        return 0.0
    return dot / (nq * nv)


_CLASSROOM_RAW_TABLES = (
    "03_ema_classroom_01_raw",
    "04_ikuya_classroom_01_raw",
)


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
        """09 から person→source→category の階層を構築。PostgREST の 1000 行既定を超える場合はページング。
        3段目の値はクラスルーム(raw_table が 03_ema_classroom_01_raw / 04_ikuya_classroom_01_raw)は
        classification2（コース名）、それ以外は classification3。
        """
        try:
            hierarchy: Dict[str, Dict[str, set]] = {}
            offset = 0
            page_size = 1000
            while True:
                response = (
                    self.client.table("09_unified_documents")
                    .select("id, raw_table, person, classification1, classification2, classification3")
                    .range(offset, offset + page_size - 1)
                    .execute()
                )
                batch = response.data or []
                for doc in batch:
                    doc_id = doc.get("id")
                    person = (doc.get("person") or "").strip()
                    source = (doc.get("classification1") or "").strip()
                    c2 = (doc.get("classification2") or "").strip()
                    c3 = (doc.get("classification3") or "").strip()
                    raw_table = (doc.get("raw_table") or "").strip()

                    if not person:
                        logger.error("get_workspace_hierarchy: person が未設定です (id={})", doc_id)
                        continue
                    if not source:
                        logger.error("get_workspace_hierarchy: classification1 が未設定です (id={})", doc_id)
                        continue

                    # 3段目の値: クラスルームは classification2(コース名)、それ以外は classification3
                    if raw_table in _CLASSROOM_RAW_TABLES:
                        if not c2:
                            logger.error(
                                "get_workspace_hierarchy: クラスルーム行に classification2(コース名) が未設定です (id={})",
                                doc_id,
                            )
                            continue
                        cat = c2
                    else:
                        cat = c3

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
            raise

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
                        except Exception as e:
                            logger.error("indexed_at parse error: {}", e)
                            raise
                continue
            try:
                document_date = datetime.strptime(document_date_str, "%Y-%m-%d")
            except Exception as e:
                logger.error("document_date parse error: {}", e)
                raise
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

    def _coerce_meta_dict(self, meta: Any) -> Optional[Dict[str, Any]]:
        if meta is None:
            return None
        if isinstance(meta, dict):
            return meta
        if isinstance(meta, str):
            try:
                o = json.loads(meta)
            except Exception as e:
                raise ValueError(
                    f"メタデータのJSON解析に失敗しました: type={type(meta).__name__}, content={meta[:100]!r}"
                ) from e
            if isinstance(o, dict):
                return o
            raise ValueError(
                f"メタデータが辞書ではありません: type={type(o).__name__}, content={repr(o)[:100]}"
            )
        raise ValueError(
            f"メタデータが辞書ではありません: type={type(meta).__name__}, content={repr(meta)[:100]}"
        )

    def _read_date_signals_from_ix(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """09.ix_date_signals のみ読む（検索側で日付を組み立てない）。"""
        raw = row.get("ix_date_signals")
        if isinstance(raw, str) and raw.strip():
            raw = json.loads(raw)
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
                            "granularity": str(p["granularity"]),
                        }
                    )
                except Exception as e:
                    print(f"[WARN] partial_dates エントリのパース失敗: {p!r} ({e})", flush=True)
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
            c1 = result.get("classification1")
            c2 = result.get("classification2")
            c3 = result.get("classification3")
            if c1 == "Googleカレンダー":
                raw_date = result.get("start_at")
            else:
                raw_date = result.get("post_at")
            document_date = raw_date[:10] if isinstance(raw_date, str) and len(raw_date) >= 10 else None
            date_signals = self._read_date_signals_from_ix(result)
            doc_id = result.get("doc_id")
            meta_dict = self._coerce_meta_dict(result.get("meta"))
            comb = result.get("combined_score")
            raw_s = result.get("raw_similarity")
            weighted_s = result.get("weighted_similarity")
            ft_s = result.get("fulltext_score")
            title_m = result.get("title_matched")
            final_results.append(
                {
                    "id": doc_id,
                    "title": result.get("title"),
                    "source": c1,
                    "person": result.get("person"),
                    "category": c3,
                    "classification1": c1,
                    "classification2": c2,
                    "classification3": c3,
                    "raw_table": result.get("raw_table"),
                    "from_name": result.get("from_name"),
                    "from_email": result.get("from_email"),
                    "snippet": result.get("snippet"),
                    "post_at": result.get("post_at"),
                    "start_at": result.get("start_at"),
                    "end_at": result.get("end_at"),
                    "due_date": result.get("due_date"),
                    "location": result.get("location"),
                    "file_url": result.get("file_url"),
                    "file_name": meta_dict.get("file_name") if meta_dict else None,
                    "ui_data": result.get("ui_data"),
                    "meta": meta_dict,
                    "date_signals": date_signals,
                    "ix_search_dates": result.get("ix_search_dates"),
                    "indexed_at": result.get("indexed_at"),
                    "document_date": document_date,
                    "document_body": None,
                    "chunk_content": result.get("best_chunk_text"),
                    "chunk_id": result.get("best_chunk_id"),
                    "chunk_index": result.get("best_chunk_index"),
                    "chunk_type": result.get("best_chunk_type"),
                    "post_body_similarity": None,
                    "attachment_similarity": None,
                    # 検索側の合成スコア。画面に出す類似度とは別に、参照用で残す。
                    "rpc_hybrid_score": float(comb) if comb is not None else None,
                    "similarity": float(comb) if comb is not None else None,
                    "raw_similarity": float(raw_s) if raw_s is not None else None,
                    "weighted_similarity": float(weighted_s) if weighted_s is not None else None,
                    "fulltext_score": float(ft_s) if ft_s is not None else None,
                    "title_matched": title_m if title_m is not None else False,
                    "chunk_score": float(comb) if comb is not None else None,
                    "large_chunk_id": result.get("doc_id"),
                    "small_chunk_id": result.get("best_chunk_id"),
                }
            )

        if self._is_service_role:
            for doc_result in final_results:
                doc_id = doc_result.get("id")
                if not doc_id:
                    continue
                if doc_result.get("classification1") == "Googleカレンダー":
                    doc_result["document_body"] = None
                    doc_result["index_chunks_all"] = []
                    doc_result["max_chunk_vector_similarity"] = None
                    doc_result["post_body_similarity"] = None
                    doc_result["attachment_similarity"] = None
                    continue
                try:
                    body_response = (
                        self.client.table("09_unified_documents")
                        .select("body, raw_table")
                        .eq("id", doc_id)
                        .limit(1)
                        .execute()
                    )
                    if body_response.data:
                        doc_result["document_body"] = body_response.data[0].get("body")
                        if not doc_result.get("raw_table"):
                            doc_result["raw_table"] = body_response.data[0].get("raw_table")
                    else:
                        doc_result["document_body"] = None
                except Exception as e:
                    logger.error("body fetch doc_id={}: {}", doc_id, e)
                    raise

                try:
                    chunks_response = (
                        self.client.table("10_ix_search_index")
                        .select("id, chunk_index, chunk_text, chunk_type, chunk_weight, embedding_v2")
                        .eq("doc_id", doc_id)
                        .order("chunk_weight", desc=True)
                        .execute()
                    )
                    if chunks_response.data:
                        raw_chunks = chunks_response.data
                        qemb = _coerce_embedding_list(embedding)
                        enriched: List[Dict[str, Any]] = []
                        for ch in raw_chunks:
                            ctype = ch.get("chunk_type")
                            if not ctype or not str(ctype).strip():
                                raise ValueError(
                                    f"契約違反: チャンクの種別 (chunk_type) が取得できません: doc_id={doc_id}, chunk_id={ch.get('id')}"
                                )
                            row = {k: v for k, v in ch.items() if k != "embedding_v2"}
                            cvec = _coerce_embedding_list(ch.get("embedding_v2"))
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
                        post_body_sims = [
                            float(x["chunk_vector_similarity"])
                            for x in enriched
                            if x.get("chunk_type") == "post_body" and x.get("chunk_vector_similarity") is not None
                        ]
                        non_post_body_sims = [
                            float(x["chunk_vector_similarity"])
                            for x in enriched
                            if x.get("chunk_type") != "post_body" and x.get("chunk_vector_similarity") is not None
                        ]
                        doc_result["post_body_similarity"] = max(post_body_sims) if post_body_sims else None
                        doc_result["attachment_similarity"] = max(non_post_body_sims) if non_post_body_sims else None

                        all_sims = [
                            float(x["chunk_vector_similarity"])
                            for x in enriched
                            if x.get("chunk_vector_similarity") is not None
                        ]
                        doc_result["max_chunk_vector_similarity"] = max(all_sims) if all_sims else None
                    else:
                        raise ValueError(f"契約違反: チャンクが存在しないか種別が取得できません: doc_id={doc_id}")
                except Exception as e:
                    logger.error("chunk fetch doc_id={}: {}", doc_id, e)
                    raise
        else:
            for doc_result in final_results:
                doc_result["index_chunks_all"] = []
                doc_result["max_chunk_vector_similarity"] = None
                doc_result["post_body_similarity"] = None
                doc_result["attachment_similarity"] = None

        for doc in final_results:
            rpc = doc.get("rpc_hybrid_score")
            doc["rpc_hybrid_score"] = float(rpc) if rpc is not None else None
            mcv = doc.get("max_chunk_vector_similarity")
            if mcv is not None:
                try:
                    # 文書の類似度 = その文書内のチャンクの類似度の最大
                    doc["similarity"] = float(mcv)
                    doc["similarity_basis"] = "max_chunk_in_doc"
                except (TypeError, ValueError) as e:
                    raise ValueError(f"類似度が数値ではありません: doc_id={doc.get('id')}, max_chunk_vector_similarity={mcv!r}") from e
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
            except (TypeError, ValueError) as e:
                raise ValueError(f"類似度が数値ではありません: doc_id={doc.get('id')}, similarity={sim!r}") from e
            if "time_score" not in doc:
                print(f"[WARN] doc id={doc.get('id')!r} has no time_score field", flush=True)
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

    def search_documents_by_keywords(
        self,
        keywords: List[str],
        persons: Optional[List[str]] = None,
        sources: Optional[List[str]] = None,
        categories: Optional[List[str]] = None,
        filter_date_start: Optional[date_type] = None,
        filter_date_end: Optional[date_type] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        09_unified_documents からキーワード（body または title の ILIKE）に一致するレコードを全件検索する。
        - 100件で打ち切らずページングにより該当を全件取得。
        - 投稿日（post_at）の新しい順（降順）で返す。
        - カレンダー（classification1 = 'Googleカレンダー'）は除外。
        - ベクトル検索と同じ絞り込み（人・ソース・3段目・日付範囲）を適用。
        - 3段目はクラスルーム(03/04)は classification2、それ以外は classification3 で照合（get_workspace_hierarchy と同じ契約）。
        - フォールバック絶対禁止（欠損・失敗時は例外を再送出）。
        """
        if not keywords:
            raise ValueError("search_documents_by_keywords: keywords が空です")

        # カレンダーは除外
        effective_sources = [s for s in sources if s != "Googleカレンダー"] if sources else None
        if sources is not None and not effective_sources:
            return []

        try:
            base_query = self.client.table("09_unified_documents").select(
                "id, raw_table, person, classification1, classification2, classification3, "
                "title, body, snippet, post_at, start_at, end_at, due_date, location, "
                "file_url, meta, indexed_at, ui_data, ix_date_signals, ix_search_dates"
            ).neq("classification1", "Googleカレンダー")

            if persons:
                base_query = base_query.in_("person", persons)
            if effective_sources:
                base_query = base_query.in_("classification1", effective_sources)

            conditions: List[str] = []
            for kw in keywords:
                clean_kw = kw.strip().replace(",", " ").replace("(", " ").replace(")", " ")
                if clean_kw:
                    conditions.append(f"body.ilike.%{clean_kw}%,title.ilike.%{clean_kw}%")

            if conditions:
                base_query = base_query.or_(",".join(conditions))

            offset = 0
            page_size = 1000
            rows: List[Dict[str, Any]] = []
            while True:
                response = base_query.range(offset, offset + page_size - 1).execute()
                batch = response.data or []
                rows.extend(batch)
                if len(batch) < page_size:
                    break
                offset += page_size

            results: List[Dict[str, Any]] = []
            for row in rows:
                doc_id = row.get("id")
                raw_table = (row.get("raw_table") or "").strip()
                person = (row.get("person") or "").strip()
                c1 = (row.get("classification1") or "").strip()
                c2 = (row.get("classification2") or "").strip()
                c3 = (row.get("classification3") or "").strip()
                title = str(row.get("title") or "")
                body = str(row.get("body") or "")

                # 1. キーワード一致確認
                title_lower = title.lower()
                body_lower = body.lower()
                matched = any(kw.lower() in title_lower or kw.lower() in body_lower for kw in keywords)
                if not matched:
                    continue

                # 2. 人・ソース絞り込み
                if persons and person not in persons:
                    continue
                if effective_sources and c1 not in effective_sources:
                    continue

                # 3. 3段目絞り込み（クラスルームは classification2、それ以外は classification3）
                if categories:
                    if raw_table in _CLASSROOM_RAW_TABLES:
                        cat_val = c2
                    else:
                        cat_val = c3
                    if not cat_val or cat_val not in categories:
                        continue

                # 4. 日付範囲絞り込み
                # 投稿日(post_at)がある文書は範囲内かチェック。投稿日がない文書は除外せず保持する（確定事項2）。
                raw_date = row.get("post_at")
                doc_date_str = self._parse_yyyy_mm_dd(raw_date)
                if doc_date_str:
                    try:
                        d_val = date_type.fromisoformat(doc_date_str)
                        if filter_date_start and d_val < filter_date_start:
                            continue
                        if filter_date_end and d_val > filter_date_end:
                            continue
                    except Exception as e:
                        logger.error("search_documents_by_keywords: post_at parse error: {}", e)
                        raise

                # 整形
                meta_dict = self._coerce_meta_dict(row.get("meta"))
                date_signals = self._read_date_signals_from_ix(row)
                title_m = any(kw.lower() in title_lower for kw in keywords)

                results.append(
                    {
                        "id": doc_id,
                        "title": row.get("title"),
                        "source": c1,
                        "person": person,
                        "category": c2 if raw_table in _CLASSROOM_RAW_TABLES else c3,
                        "classification1": c1,
                        "classification2": c2,
                        "classification3": c3,
                        "raw_table": raw_table,
                        "from_name": row.get("from_name"),
                        "from_email": row.get("from_email"),
                        "snippet": row.get("snippet"),
                        "post_at": row.get("post_at"),
                        "start_at": row.get("start_at"),
                        "end_at": row.get("end_at"),
                        "due_date": row.get("due_date"),
                        "location": row.get("location"),
                        "file_url": row.get("file_url"),
                        "file_name": meta_dict.get("file_name") if meta_dict else None,
                        "ui_data": row.get("ui_data"),
                        "meta": meta_dict,
                        "date_signals": date_signals,
                        "ix_search_dates": row.get("ix_search_dates"),
                        "indexed_at": row.get("indexed_at"),
                        "document_date": doc_date_str,
                        "document_body": body,
                        "chunk_content": row.get("snippet"),
                        "chunk_id": None,
                        "chunk_index": None,
                        "chunk_type": None,
                        "post_body_similarity": None,
                        "attachment_similarity": None,
                        "rpc_hybrid_score": None,
                        "similarity": None,
                        "raw_similarity": None,
                        "weighted_similarity": None,
                        "fulltext_score": None,
                        "title_matched": title_m,
                        "is_keyword_matched": True,
                        "chunk_score": None,
                        "large_chunk_id": doc_id,
                        "small_chunk_id": None,
                        "final_score": None,
                        "index_chunks_all": [],
                        "max_chunk_vector_similarity": None,
                        "similarity_basis": "keyword_match",
                    }
                )

            # 投稿日の新しい順（降順 DESC、日付あり優先、日付なし末尾）でソート
            def _post_at_sort_key(r: Dict[str, Any]) -> Tuple[int, str]:
                pa = r.get("post_at")
                s = str(pa).strip() if pa is not None else ""
                if s:
                    return (1, s)
                return (0, "")

            results.sort(key=_post_at_sort_key, reverse=True)
            return results
        except Exception as e:
            logger.error("search_documents_by_keywords error: {}", e)
            raise

