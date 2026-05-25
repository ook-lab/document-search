"""
F51: 表の縦・横結合セルを論理行に再構成する。

pdfplumber geometry を優先し、失敗時のみ AI 判定へフォールバックする。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from dms.pipeline.stage_g.g36_lr_vertical_ai_judge import (
    G36_LR_VERTICAL_AI_CONTRACT,
    G36LRVerticalAIError,
    judge_lr_vertical_layout_ai,
    rebuild_grid_from_ai_correspondence,
)
from dms.pipeline.stage_g.g36_lr_merged_vertical_grid import (
    G36_AI_JUDGE_CONTRACT,
    G36_GEOMETRY_CONTRACT,
    G36_MECHANICAL_CONTRACT,
    LRMergedVerticalRebuildError,
    classify_vertical_merge_mode,
    is_lr_merged_vertical_candidate,
    rebuild_lr_merged_vertical_table,
)
from dms.pipeline.stage_g.g36_d_cell_matrix import try_d_cell_matrix_table_rec
from dms.pipeline.stage_g.merged_cell_grid import (
    _grid_max_cols,
    coalesce_wrapped_extract_rows,
    expand_multiline_cells_in_grid,
    unfold_left_label_amount_space_join,
    unfold_numbered_two_column_list,
)

_CIRCLED_MARK = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]")


class G36LRMergedVerticalError(G36LRVerticalAIError):
    pass


_B_PURGED_PREFIXES = ("b3_", "b4_", "b11_", "b12_", "b18_", "b30_", "b39_")


def resolve_geometry_pdf_path(pdf_path: str | Path) -> Path:
    """
    G36 の語座標取得用 PDF。purged（表テキスト白塗り）ではなく元 PDF を返す。
    """
    purged = Path(pdf_path)
    if not purged.is_file():
        return purged
    stem = purged.stem
    if stem.endswith("_purged"):
        stem = stem[: -len("_purged")]
    for prefix in _B_PURGED_PREFIXES:
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    parent = purged.parent
    candidates = [parent / f"{stem}.pdf"]
    if parent.name == "purged":
        candidates.insert(0, parent.parent / f"{stem}.pdf")
    for cand in candidates:
        if cand.is_file() and cand.resolve() != purged.resolve():
            return cand
    for cand in parent.glob(f"{stem}*.pdf"):
        if "purged" not in cand.name.lower() and cand.resolve() != purged.resolve():
            return cand
    return purged


_DATA_ROW_START_RE = re.compile(r"^[①②③④⑤⑥⑦⑧⑨⑩]")


def _preview_lacks_header_row(preview: List[List[Any]]) -> bool:
    """先頭 extract 行が列ラベルではなくデータ項目から始まる表。"""
    if not preview:
        return False
    row = preview[0]
    if not isinstance(row, (list, tuple)) or not row:
        return False
    c0 = "" if row[0] is None else str(row[0]).strip()
    return bool(c0 and _DATA_ROW_START_RE.match(c0))


def _apply_extract_header_row_from_preview(
    preview: List[List[Any]], ai: Dict[str, Any]
) -> Dict[str, Any]:
    """
    先頭 extract 行が丸数字項目から始まる表はヘッダー行が無い（pdfplumber 1行化）。
    AI が extract_header_row=0 を返しても上書きしない（創作見出しは付けない）。
    """
    if not _preview_lacks_header_row(preview):
        return ai
    out = dict(ai)
    out["extract_header_row"] = None
    out["header_cells"] = []
    return out


def _resolve_plumber_table(page: Any, table_rec: Dict[str, Any]) -> Any:
    idx = table_rec.get("b_plumber_index")
    if idx is None:
        idx = (table_rec.get("metadata") or {}).get("b_plumber_index")
    bbox = table_rec.get("bbox")
    tables = page.find_tables() or []
    if idx is not None and 0 <= int(idx) < len(tables):
        return tables[int(idx)]
    if bbox and len(bbox) >= 4:
        tx0, ty0, tx1, ty1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        for t in tables:
            bb = getattr(t, "bbox", None)
            if not bb:
                continue
            ox0, oy0, ox1, oy1 = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
            if abs(ox0 - tx0) < 2 and abs(oy0 - ty0) < 2 and abs(ox1 - tx1) < 2 and abs(oy1 - ty1) < 2:
                return t
    raise G36LRMergedVerticalError(
        f"g36_plumber_table_not_found: table_id={table_rec.get('table_id')}"
    )


def _apply_g36_ai(
    table_rec: Dict[str, Any],
    raw: List[List[Any]],
    *,
    document_id: Optional[str],
) -> bool:
    preview = expand_multiline_cells_in_grid(raw)
    contract_error: Optional[str] = None
    ai: Optional[Dict[str, Any]] = None
    for attempt in range(3):
        try:
            ai = judge_lr_vertical_layout_ai(
                table_preview=preview,
                geometry_evidence={},
                geometry_hint=None,
                document_id=document_id,
                contract_error=contract_error,
            )
            ai = _apply_extract_header_row_from_preview(preview, ai)
            break
        except G36LRVerticalAIError as exc:
            msg = str(exc)
            if attempt < 2 and ("slash_join" in msg or "slash_joined" in msg):
                contract_error = msg
                ai = None
                continue
            raise
    if ai is None:
        raise G36LRVerticalAIError("g36_ai_missing_result")
    if ai.get("layout_ai_contract") != G36_LR_VERTICAL_AI_CONTRACT:
        raise G36LRVerticalAIError("g36_ai_contract_missing")

    layout_kind = ai["layout_kind"]
    meta = dict(table_rec.get("metadata") or {})
    meta.update(
        {
            "vertical_merge_judge": G36_AI_JUDGE_CONTRACT,
            "vertical_merge_confidence": ai["confidence"],
            "vertical_merge_rationale": ai["rationale"],
            "correspondence_summary": ai.get("correspondence_summary"),
            "vertical_merges_ai": ai.get("vertical_merges"),
        }
    )

    if layout_kind in ("no_vertical_merge", "no_merge"):
        meta["vertical_merge_mode"] = "no_merge"
        meta["lr_rebuilt"] = False
        table_rec["metadata"] = meta
        return False

    # rowspan<=1 の無効マージを除去してから渡す
    raw_vmerges = ai.get("vertical_merges") or []
    valid_vmerges = [
        vm for vm in raw_vmerges
        if isinstance(vm, dict) and isinstance(vm.get("rowspan"), int) and vm["rowspan"] >= 2
    ]
    # 12列以上の広い表（並列時間割など）は列0（日付列）以外のマージを除去する
    # 時間割の科目行・授業名行が日付行と誤マージされて消失するのを防ぐ
    ncols_preview = _grid_max_cols(preview)
    if ncols_preview >= 12:
        wide_vmerges = [
            vm for vm in valid_vmerges
            if isinstance(vm.get("anchor_col"), int) and vm["anchor_col"] == 0
        ]
        if len(wide_vmerges) != len(valid_vmerges):
            logger.info(
                f"[G36-AI] table_id={table_rec.get('table_id')} — "
                f"広幅表マージ制限 {len(valid_vmerges)}→{len(wide_vmerges)} "
                f"(列0以外のマージを除外, ncols={ncols_preview})"
            )
            valid_vmerges = wide_vmerges
    if len(valid_vmerges) != len(raw_vmerges):
        logger.info(
            f"[G36-AI] table_id={table_rec.get('table_id')} — "
            f"無効マージ除去 {len(raw_vmerges)}→{len(valid_vmerges)}"
        )
        ai = dict(ai)
        ai["vertical_merges"] = valid_vmerges

    grid, h_merges, layout_meta = rebuild_grid_from_ai_correspondence(preview, ai, header_rows=1)

    # 行消失チェック: AI が説明なしに source 行を捨てた場合はリバート
    effective_header = 1 if ai.get("extract_header_row") is not None else 0
    source_data_rows = len(preview) - effective_header
    actual_logical = len(ai.get("logical_rows") or [])
    if source_data_rows > actual_logical:
        # full_resolution モードでは全 source 行が個別の logical_row として出力されるべきで
        # 列単位マージが source 行を吸収（丸ごと消去）することはない → explained_loss = 0。
        # 他モードではマージが行を吸収する場合があるため従来式を使用。
        if layout_kind == "full_resolution":
            explained_loss = 0
        else:
            explained_loss = sum(
                max(0, len(vm.get("source_extract_rows") or []) - 1)
                for vm in (ai.get("vertical_merges") or [])
                if isinstance(vm.get("source_extract_rows"), list)
            )
        unexplained = (source_data_rows - actual_logical) - explained_loss
        if unexplained > 0:
            logger.warning(
                f"[G36-AI] table_id={table_rec.get('table_id')} — "
                f"{unexplained} 行が説明なしで消失 "
                f"(source_data={source_data_rows}, logical={actual_logical}, "
                f"explained_by_merges={explained_loss}) → 元データを保持"
            )
            meta["vertical_merge_mode"] = "no_merge"
            meta["lr_rebuilt"] = False
            meta["g36_ai_row_loss_reverted"] = True
            table_rec["metadata"] = meta
            return False

    # 列消失チェック: LR展開で列が大幅に減った場合はリバート
    # 12列以上の広い表（5A/5B並列時間割など）で65%未満に縮小した場合は
    # クラスラベルが消失するためリバートして元の15列構造を保持する
    ncols_before = _grid_max_cols(preview)
    ncols_after = _grid_max_cols(grid)
    if ncols_before >= 12 and ncols_after < ncols_before * 0.65:
        logger.warning(
            f"[G36-AI] table_id={table_rec.get('table_id')} — "
            f"LR展開で列が大幅に減少 {ncols_before}→{ncols_after} "
            f"（クラスラベル消失の恐れ）→ 元データを保持"
        )
        meta["vertical_merge_mode"] = "no_merge"
        meta["lr_rebuilt"] = False
        meta["g36_ai_col_loss_reverted"] = True
        table_rec["metadata"] = meta
        return False

    meta.update(
        {
            "vertical_merge_mode": layout_kind,
            "lr_merged_vertical_contract": "lr_merged_vertical_v2",
            "lr_rebuilt": True,
            "logical_row_count": len(ai.get("logical_rows") or []),
            "source_rows": len(preview),
            "output_rows": len(grid),
            **layout_meta,
        }
    )
    if h_merges:
        meta["horizontal_merges"] = h_merges

    table_rec["data"] = [list(r) for r in grid]
    table_rec["metadata"] = meta
    logger.info(
        f"[G36-AI] rebuilt table_id={table_rec.get('table_id')} mode={layout_kind} "
        f"rows={len(grid)}"
    )
    return True


def _try_geometry_rebuild(
    table_rec: Dict[str, Any],
    page: Any,
    plumber_table: Any,
    *,
    probe_data: Optional[List[List[Any]]] = None,
) -> bool:
    """pdfplumber geometry で左縦結合＋右行対応を機械再構成（LLM 不使用）。"""
    probe = probe_data or (plumber_table.extract() if plumber_table else None) or table_rec.get("data") or []
    if not is_lr_merged_vertical_candidate(page, plumber_table, probe):
        logger.warning(
            f"[G36-GEO] skip (not candidate) table_id={table_rec.get('table_id')}"
        )
        return False
    mode, evidence = classify_vertical_merge_mode(page, plumber_table)
    judge_meta = {
        "vertical_merge_judge": G36_GEOMETRY_CONTRACT,
        "geometry_evidence": evidence,
    }
    out, meta = rebuild_lr_merged_vertical_table(
        page, plumber_table, mode=mode, judge_meta=judge_meta
    )
    table_rec["data"] = [list(r) for r in out]
    merged_meta = dict(table_rec.get("metadata") or {})
    merged_meta.update(meta)
    table_rec["metadata"] = merged_meta
    logger.info(
        f"[G36-GEO] rebuilt table_id={table_rec.get('table_id')} mode={mode} "
        f"rows={len(out)}"
    )
    return True


def _preprocess_grid(
    data: List[List[Any]],
    *,
    include_space_unfold: bool = True,
    bd_grid_aligned: bool = False,
) -> List[List[Any]]:
    g = expand_multiline_cells_in_grid(data, bd_grid_aligned=bd_grid_aligned)
    g = unfold_numbered_two_column_list(g)
    if include_space_unfold:
        g = unfold_left_label_amount_space_join(g)
    return g


def _mechanical_unfold_applied(before: List[List[Any]], after: List[List[Any]]) -> bool:
    """空白潰れ・①②…潰れを機械展開できたか（行数増＋①付きデータ行）。"""
    if len(after) <= len(before):
        return False
    for row in after:
        if not isinstance(row, (list, tuple)) or not row:
            continue
        if _CIRCLED_MARK.search(str(row[0] or "")):
            return True
    if len(after) > len(before):
        for row in after:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                if str(row[0] or "").strip() and str(row[1] or "").strip():
                    return True
    return False


def run_g36_lr_vertical_on_tables(
    tables: List[Dict[str, Any]],
    pdf_path: str | Path | None = None,
    *,
    document_id: Optional[str] = None,
    cell_bundle: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """structured data 上で結合セル再構成（geometry 優先 → AI）。"""
    rebuilt = 0
    pdf_path = Path(pdf_path) if pdf_path else None
    geometry_pdf = resolve_geometry_pdf_path(pdf_path) if pdf_path else None
    if geometry_pdf and pdf_path and geometry_pdf.resolve() != pdf_path.resolve():
        logger.info(
            f"[G36] geometry PDF: {geometry_pdf.name} (purged ではなく原文)"
        )
    doc = None
    if geometry_pdf and geometry_pdf.is_file():
        import pdfplumber

        doc = pdfplumber.open(geometry_pdf)

    try:
        for table_rec in tables:
            meta = dict(table_rec.get("metadata") or {})
            if meta.get("lr_rebuilt"):
                continue

            data = table_rec.get("data") or []
            meta = dict(table_rec.get("metadata") or {})
            d_matrix = bool(meta.get("d_cell_matrix"))
            data = table_rec.get("data") or []
            geo_seed = _preprocess_grid(
                data, include_space_unfold=False, bd_grid_aligned=d_matrix
            )

            if doc is not None:
                try:
                    page_i = int(table_rec.get("page") or 0)
                    page = doc.pages[page_i]
                    plumber_table = _resolve_plumber_table(page, table_rec)
                    if not is_lr_merged_vertical_candidate(
                        page, plumber_table, geo_seed
                    ):
                        logger.warning(
                            f"[G36-GEO] outer skip (not candidate) "
                            f"table_id={table_rec.get('table_id')}"
                        )
                    elif _try_geometry_rebuild(
                        table_rec, page, plumber_table, probe_data=geo_seed
                    ):
                        rebuilt += 1
                        continue
                except (LRMergedVerticalRebuildError, G36LRMergedVerticalError) as exc:
                    logger.warning(
                        f"[G36] geometry skip table_id={table_rec.get('table_id')}: {exc}"
                    )
                except Exception as exc:
                    logger.warning(
                        f"[G36] geometry error table_id={table_rec.get('table_id')}: {exc!r}"
                    )

            lr_vertical = False
            if doc is not None:
                try:
                    page_i = int(table_rec.get("page") or 0)
                    page = doc.pages[page_i]
                    plumber_table = _resolve_plumber_table(page, table_rec)
                    lr_vertical = is_lr_merged_vertical_candidate(
                        page, plumber_table, geo_seed
                    )
                except Exception:
                    lr_vertical = False

            expanded = _preprocess_grid(
                data,
                include_space_unfold=not lr_vertical,
                bd_grid_aligned=d_matrix,
            )
            if len(expanded) < 2:
                continue

            if lr_vertical:
                if _apply_g36_ai(table_rec, expanded, document_id=document_id):
                    rebuilt += 1
                continue

            if _mechanical_unfold_applied(data, expanded):
                table_rec["data"] = expanded
                merged_meta = dict(table_rec.get("metadata") or {})
                merged_meta["lr_rebuilt"] = True
                merged_meta["vertical_merge_judge"] = G36_MECHANICAL_CONTRACT
                ncol = max(
                    (len(r) for r in expanded if isinstance(r, (list, tuple))),
                    default=0,
                )
                if ncol == 2 and expanded and _CIRCLED_MARK.search(
                    str((expanded[0][0] if expanded[0] else "") or "")
                ):
                    merged_meta["data_start_row"] = 0
                    merged_meta["header_rows"] = []
                table_rec["metadata"] = merged_meta
                logger.info(
                    f"[G36-MECH] rebuilt table_id={table_rec.get('table_id')} "
                    f"rows={len(data)}->{len(expanded)}"
                )
                rebuilt += 1
                continue

            if _apply_g36_ai(table_rec, expanded, document_id=document_id):
                rebuilt += 1
    finally:
        if doc is not None:
            doc.close()

    logger.info(f"[G36] 完了: rebuilt={rebuilt} / {len(tables)}")
    return tables


def run_g36_on_structured_tables(
    structured_tables: List[Dict[str, Any]],
    pdf_path: str | Path,
    *,
    document_id: Optional[str] = None,
    cell_bundle: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    work: List[Dict[str, Any]] = []
    for st in structured_tables:
        meta = dict(st.get("metadata") or {})
        headers = st.get("headers") or []
        rows = st.get("rows") or []
        full: List[List[Any]] = []
        if headers:
            full.append(list(headers))
        full.extend([list(r) for r in rows])
        page = meta.get("page")
        if page is None and st.get("source_page") is not None:
            try:
                page = int(st.get("source_page")) - 1
            except (TypeError, ValueError):
                page = st.get("source_page")
        work.append(
            {
                "table_id": st.get("table_id"),
                "page": page,
                "b_plumber_index": meta.get("b_plumber_index", meta.get("index")),
                "bbox": meta.get("bbox"),
                "data": full,
                "metadata": meta,
            }
        )
    run_g36_lr_vertical_on_tables(
        work,
        pdf_path,
        document_id=document_id,
        cell_bundle=cell_bundle,
    )
    for st, rec in zip(structured_tables, work):
        meta_out = dict(rec.get("metadata") or {})
        st["metadata"] = meta_out
        data = rec.get("data") or []
        if not data:
            continue
        if not (
            meta_out.get("lr_rebuilt")
            or meta_out.get("wrap_rows_coalesced")
            or meta_out.get("d_cell_matrix")
        ):
            continue
        st["headers"] = list(data[0])
        st["rows"] = [list(r) for r in data[1:]]
    return structured_tables
