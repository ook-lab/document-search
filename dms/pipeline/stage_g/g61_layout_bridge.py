"""
G61: G26 意味推定 → G62 配置のブリッジ（LLM なし・配置ロジックなし）。

分割後 ``e14_reconstructed`` と ``semantic_inference`` を G62 に渡す。
罫線の bbox フィルタ結果（``line_meanings``）はデバッグログ用のみ（配置は使わない）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from dms.pipeline.stage_g.g61_chain_logs import dedicated_g61_chain_log_paths
from dms.pipeline.stage_g.g26_semantic_estimator import G26SemanticEstimator

_LOG_TAG = "[G61]"


def _pick_d_digest_row(
    digest: Dict[str, Any],
    index: int,
    table_id: str,
    source_page: Any,
) -> Optional[Dict[str, Any]]:
    rows = (digest or {}).get("tables") or []
    if not rows:
        return None
    if index < len(rows):
        return rows[index]
    sp: Optional[int] = None
    if source_page is not None and str(source_page).strip() != "":
        try:
            sp = int(source_page)
        except (TypeError, ValueError):
            sp = None
    if sp is not None:
        for r in rows:
            pi = r.get("page_index")
            if pi is not None:
                try:
                    if int(pi) == sp:
                        return r
                except (TypeError, ValueError):
                    continue
    tid = str(table_id or "")
    for r in rows:
        ou = str(r.get("origin_uid") or "")
        dtid = str(r.get("table_id") or "")
        if tid and (tid in ou or dtid in tid):
            return r
    return rows[0] if len(rows) == 1 else None


def _line_intersects_bbox_norm(line: Dict[str, Any], bbox: List[float], margin: float = 0.008) -> bool:
    if len(bbox) < 4:
        return False
    x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
    x0 -= margin
    y0 -= margin
    x1 += margin
    y1 += margin
    mx = (float(line["x0"]) + float(line["x1"])) / 2.0
    my = (float(line["y0"]) + float(line["y1"])) / 2.0
    return x0 <= mx <= x1 and y0 <= my <= y1


def _build_assigned_lines(
    digest: Dict[str, Any],
    d_row: Optional[Dict[str, Any]],
    detection: Dict[str, Any],
) -> List[Dict[str, Any]]:
    assigned: List[Dict[str, Any]] = []
    ai = (digest or {}).get("line_semantics_ai") or {}
    ai_lines = ai.get("lines") if isinstance(ai, dict) else None
    digest_lines = (digest or {}).get("lines") or []

    if digest_lines and ai_lines is None:
        from dms.pipeline.stage_g.g26_line_semantics import G26SemanticAIError

        raise G26SemanticAIError("g26_line_semantics_required")

    bbox = (
        list(d_row["bbox"])
        if d_row and isinstance(d_row.get("bbox"), (list, tuple)) and len(d_row["bbox"]) >= 4
        else None
    )
    if isinstance(ai_lines, list):
        for ln in ai_lines:
            if not isinstance(ln, dict):
                continue
            if bbox and not _line_intersects_bbox_norm(ln, bbox):
                continue
            assigned.append(
                {
                    "kind": "ruling",
                    "role": str(ln.get("role") or "unknown"),
                    "source": "g26_page_understanding",
                    "detail": {
                        "line_id": ln.get("line_id"),
                        "orientation": ln.get("orientation"),
                        "segment_norm": [ln.get("x0"), ln.get("y0"), ln.get("x1"), ln.get("y1")],
                        "meaning": ln.get("meaning"),
                        "confidence": ln.get("confidence"),
                    },
                }
            )

    if d_row and bbox:
        assigned.append(
            {
                "kind": "geometry",
                "role": "table_bounding_box",
                "source": "stage_d",
                "detail": {
                    "page_index": d_row.get("page_index"),
                    "d_table_id": d_row.get("table_id"),
                    "origin_uid": d_row.get("origin_uid"),
                    "bbox_normalized": bbox,
                },
            }
        )
    if detection.get("row_split"):
        assigned.append(
            {
                "kind": "logic",
                "role": "repeated_header_row_block_boundary",
                "source": "g41",
                "detail": {"meaning": "行方向の繰り返しヘッダーに基づく論理ブロック境界"},
            }
        )
    if detection.get("col_split"):
        assigned.append(
            {
                "kind": "logic",
                "role": "repeated_header_col_block_boundary",
                "source": "g41",
                "detail": {"meaning": "列方向の繰り返しヘッダーに基づく論理ブロック境界"},
            }
        )
    return assigned


class G61LayoutBridgeProcessor:
    """G26 ``semantic_inference`` を検証し G62 を呼ぶ。"""

    @staticmethod
    def _semantic_covers_e14(
        semantic_inference: Any,
        e14_reconstructed: List[Dict[str, Any]],
    ) -> bool:
        if not isinstance(semantic_inference, dict):
            return False
        by = semantic_inference.get("by_sub_table")
        if not isinstance(by, dict):
            return False
        for entry in e14_reconstructed:
            tid = str(entry.get("table_id") or "")
            for sub in entry.get("sub_tables") or []:
                if not isinstance(sub, dict):
                    continue
                data = list(sub.get("data") or [])
                if not data:
                    continue
                stid = str(sub.get("sub_table_id") or "")
                key = f"{tid}::{stid}" if stid else f"{tid}::"
                if key not in by:
                    return False
        return bool(e14_reconstructed)

    def __init__(self, document_id=None, next_stage=None):
        self.document_id = document_id
        self.next_stage = next_stage

    def process(
        self,
        e14_reconstructed: List[Dict[str, Any]],
        year_context: Optional[int] = None,
        table_log_dir: Optional[Path] = None,
        chain_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        g61_log, g62_log = dedicated_g61_chain_log_paths(table_log_dir)
        sink_id = None
        if g61_log:
            Path(g61_log).parent.mkdir(parents=True, exist_ok=True)
            sink_id = logger.add(
                g61_log,
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: _LOG_TAG in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._process_impl(
                e14_reconstructed,
                year_context=year_context,
                g61_log_path=g61_log,
                g62_log_path=g62_log,
                chain_context=chain_context,
            )
        finally:
            if sink_id is not None:
                logger.remove(sink_id)

    def _process_impl(
        self,
        e14_reconstructed: List[Dict[str, Any]],
        *,
        year_context: Optional[int],
        g61_log_path: Optional[str],
        g62_log_path: Optional[str],
        chain_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        digest = (chain_context or {}).get("stage_d_line_digest") or {}
        digest_ok = bool(isinstance(digest, dict) and digest.get("available"))

        detections = (chain_context or {}).get("detections") or []
        structured_tables = (chain_context or {}).get("structured_tables") or []
        per_table_debug: List[Dict[str, Any]] = []
        for i, entry in enumerate(e14_reconstructed):
            tid = entry.get("table_id", f"T{i}")
            st = structured_tables[i] if i < len(structured_tables) else {}
            source_page = st.get("source_page") if isinstance(st, dict) else None
            det = detections[i] if i < len(detections) else {}
            if not isinstance(det, dict):
                det = {}
            d_row = _pick_d_digest_row(digest if isinstance(digest, dict) else {}, i, tid, source_page)
            line_meanings = _build_assigned_lines(
                digest if isinstance(digest, dict) else {}, d_row, det
            )
            per_table_debug.append(
                {
                    "table_id": tid,
                    "line_meanings": line_meanings,
                    "applied": bool(line_meanings),
                    "d_digest_matched": d_row is not None,
                }
            )

        logger.info(f"{_LOG_TAG} ========== G62 ブリッジ開始 ==========")
        if digest_ok:
            ai_n = len((digest.get("line_semantics_ai") or {}).get("lines") or [])
            logger.info(
                f"{_LOG_TAG} digest: tables={len(digest.get('tables') or [])} lines={ai_n}"
            )

        bridge_tokens = 0
        usage_sums = {"prompt": 0, "candidates": 0, "thoughts": 0}

        if self.next_stage:
            precomputed = (chain_context or {}).get("semantic_inference")
            if self._semantic_covers_e14(precomputed, e14_reconstructed):
                semantic_payload = precomputed
                logger.info(f"{_LOG_TAG} semantic_inference 再利用（G26 正本）")
            else:
                logger.warning(f"{_LOG_TAG} semantic_inference 不足 → G26 再実行")
                estimator = G26SemanticEstimator(document_id=self.document_id)
                semantic_payload, infer_tokens, usage_sums = estimator.infer_all(
                    e14_reconstructed, year_context, chain_context=chain_context
                )
                bridge_tokens += int(infer_tokens)
        else:
            semantic_payload = {"by_sub_table": {}, "model_name": None}

        g61_payload = {
            "success": True,
            "tokens_used": bridge_tokens,
            "model_name": semantic_payload.get("model_name"),
            "debug": {
                "line_semantics": {
                    "stage_d_digest_available": digest_ok,
                    "tables": per_table_debug,
                },
                "table_count": len(e14_reconstructed),
            },
            "dedicated_log": g61_log_path,
        }

        if not self.next_stage:
            logger.warning(f"{_LOG_TAG} next_stage なし → G62 スキップ")
            return {
                "g61_result": g61_payload,
                "g62_result": {
                    "success": True,
                    "table_analyses": [],
                    "tokens_used": 0,
                },
                "f46_result": g61_payload,
                "f47_result": {
                    "success": True,
                    "table_analyses": [],
                    "tokens_used": 0,
                },
            }

        logger.info(f"{_LOG_TAG} → G62 配置")
        g62_out = self.next_stage.process(
            e14_reconstructed,
            year_context=year_context,
            log_file=g62_log_path,
            table_log_dir=None,
            semantic_inference=semantic_payload,
        )
        if not isinstance(g62_out, dict):
            raise RuntimeError("G62 returned non-dict")

        logger.info(f"{_LOG_TAG} ========== G62 ブリッジ完了 ==========")

        return {
            "g61_result": g61_payload,
            "g62_result": g62_out,
            "f46_result": g61_payload,
            "f47_result": g62_out,
        }
