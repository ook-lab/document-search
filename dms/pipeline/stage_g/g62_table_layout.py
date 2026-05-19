"""
F58: 表レイアウト（配置）専用モジュール。

**分割**: 行・列ブロックへの分割は **F56**。G32 / G62 はその ``sub_tables`` を消費するだけ。
**意味**（``row_analysis`` / ``col_analysis`` / ``table_semantics``）は **F57** の LLM 正本。
**本モジュール**はその結果だけを読み、ヘッダー行・``data_start_row``・``col_map`` などを組み立てる。
**LLM は使わない**。結合セル由来の欠損は左方向フィルで補う（グリッド列幅は表全体の最大列に合わせてパディング）。

クラス名 ``G62TableLayoutProcessor`` は歴史的経緯によるもの（中身は配置処理）。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
import re

from dms.pipeline.stage_g.g61_chain_logs import dedicated_g61_chain_log_paths
from dms.pipeline.stage_g.g26_semantic_estimator import _strip_auto_column_rows
from dms.pipeline.stage_g.merged_cell_grid import (
    apply_merged_cell_resolution,
    is_merge_placeholder,
    prune_blank_body_rows,
    row_starts_with_circled_marker,
)

# 5A / 6B など「学級略称」→ multi_section の table_id を base_5A にする（_S1 より安定）
_MULTI_SEC_CLASS_TABLE_SUFFIX = re.compile(r"^[0-9０-９]{1,2}[A-Za-z]$")


def _cell_text(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _drop_body_rows_identical_to_header(
    data: List[List],
    *,
    data_start_row: int,
) -> List[List]:
    """データ部にヘッダー行と全列同一の行が混入した場合のみ除去する。"""
    dsr = max(0, int(data_start_row))
    if dsr < 1 or len(data) <= dsr:
        return data
    header = data[0]
    if not isinstance(header, (list, tuple)):
        return data
    out = [list(r) for r in data[:dsr]]
    for row in data[dsr:]:
        if not isinstance(row, (list, tuple)):
            out.append(list(row))
            continue
        if len(row) == len(header) and all(
            _cell_text(a) == _cell_text(b) for a, b in zip(row, header)
        ):
            continue
        out.append(list(row))
    return out


def _f58_multi_section_display_table_id(base_table_id: str, section: Dict[str, Any], sec_idx: int) -> str:
    gn = (section.get("group_name") or "").strip()
    if gn:
        gn_ascii = gn.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        if _MULTI_SEC_CLASS_TABLE_SUFFIX.match(gn_ascii):
            return f"{base_table_id}_{gn_ascii}"
    return f"{base_table_id}_S{sec_idx}"


class G62TableLayoutProcessor:
    """F58: 表の配置（ヘッダー・col_map・結合セル補完）。意味推定は F57。"""

    def __init__(self, document_id=None, model_name: str = "gemini-2.5-flash-lite"):
        self.document_id = document_id
        self.model_name = model_name
        # 配置専用のため LLM は未使用。将来オプションで使う場合に備えキーのみ保持。
        self.model = None

    def _resolve_f47_log_file(self, log_file, table_log_dir) -> Optional[str]:
        if log_file:
            return str(log_file)
        if table_log_dir:
            _, f47 = dedicated_g61_chain_log_paths(Path(table_log_dir))
            return f47
        return None

    # =========================================================================
    # エントリーポイント
    # =========================================================================

    def process(
        self,
        e14_reconstructed: List[Dict[str, Any]],
        year_context: Optional[int] = None,
        log_file=None,
        table_log_dir=None,
        *,
        semantic_inference: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        G44 の再構成済み表に対し、G32 の意味推定に基づいて配置（ヘッダー・col_map）を組み立てる。

        Args:
            e14_reconstructed: F56出力
                [{'table_id': str, 'sub_tables': [{'data': list, 'group_name': str, 'split_axis': str}]}, ...]
            year_context: 年度ヒント（ログ用。配置ロジック本体では未使用）
            log_file: G62 専用ログファイルパス（オプション）
            table_log_dir: 指定時、専用ログ未指定なら g62_table_ai_processor.log をこの配下に作成
            semantic_inference: G32 意味推定（必須・`by_sub_table`）。配置はこの結果のみを前提に行う。

        Returns:
            {
                'success': bool,
                'table_analyses': list,
                'tokens_used': int,
                'input_tokens': int,
                'output_tokens': int,
            }
        """
        eff_log = self._resolve_f47_log_file(log_file, table_log_dir)
        _sink_id = None
        if eff_log:
            Path(eff_log).parent.mkdir(parents=True, exist_ok=True)
            _sink_id = logger.add(
                eff_log,
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[G62]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
            logger.info(f"[G62] 専用ログ: {eff_log}")
        try:
            return self._process_impl(
                e14_reconstructed,
                year_context,
                semantic_inference=semantic_inference,
            )
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _process_impl(
        self,
        e14_reconstructed: List[Dict[str, Any]],
        year_context: Optional[int] = None,
        *,
        semantic_inference: Dict[str, Any],
    ) -> Dict[str, Any]:
        """process() の実装本体"""
        self._f47_session_input = 0
        self._f47_session_output = 0
        logger.info("[G62] ========== 配置処理開始（LLM なし）==========")
        logger.info(f"[G62] 年度コンテキスト: {year_context if year_context else 'なし'}")
        self.year_context = year_context
        logger.info(f"[G62] 入力表数: {len(e14_reconstructed)}個")

        if not e14_reconstructed:
            logger.info(
                f"[G62] COST | session_total input_tokens=0 output_tokens=0 "
                f"total_tokens=0 session_id={self.document_id!r} (入力表なし)"
            )
            return {
                'success': True,
                'table_analyses': [],
                'tokens_used': 0,
                'input_tokens': 0,
                'output_tokens': 0,
            }

        table_analyses = []
        total_tokens = 0

        for i, entry in enumerate(e14_reconstructed, 1):
            table_id = entry.get('table_id', f'Table_{i}')
            sub_tables = entry.get('sub_tables', [])
            logger.info(f"\n[G62] 表 {i}/{len(e14_reconstructed)}: {table_id} ({len(sub_tables)}サブテーブル)")

            result, tokens = self._process_sub_tables(
                table_id,
                sub_tables,
                semantic_inference=semantic_inference,
            )
            total_tokens += tokens

            # 複数セクションを個別の表として展開
            if result.get('table_type') == 'multi_section':
                sections = result.get('sections', [])
                logger.info(
                    f"[G62] 出力を {len(sections)} セクションに展開（1 サブテーブル = 1 UI セクション）"
                )

                for sec_idx, section in enumerate(sections, 1):
                    # G65 結合ゲート用: 別クラスの時間割など「別表」を同種だから結合しない。
                    # 同一 e14 エントリの multi_section 行分割に限り True（G65 はこれを見てのみ縦結合）。
                    sec_md = dict(section.get('metadata') or {}) if isinstance(section, dict) else {}
                    sec_md['f58_row_split_sequence'] = True
                    sec_md['f58_row_split_base_id'] = str(table_id)
                    sec_md['f58_row_split_index'] = int(sec_idx)
                    inner = dict(section) if isinstance(section, dict) else {'data': [], 'metadata': {}}
                    inner['metadata'] = sec_md
                    display_tid = _f58_multi_section_display_table_id(table_id, section, sec_idx)
                    section_table = {
                        'table_id': display_tid,
                        'table_type': section.get('table_type', 'structured'),
                        'description': section.get('title', f'セクション{sec_idx}'),
                        'sections': [inner],
                        'metadata': sec_md,
                    }
                    table_analyses.append(section_table)
                    logger.info(f"  ├─ {section_table['table_id']}: {section_table['description']} ({len(section.get('data', []))}行)")
            else:
                result['table_id'] = table_id
                table_analyses.append(result)

        logger.info(
            f"\n[G62] 完了: {len(table_analyses)}表, "
            f"input_tokens={self._f47_session_input} output_tokens={self._f47_session_output}"
        )
        logger.info(
            f"[G62] COST | session_total input_tokens={self._f47_session_input} "
            f"output_tokens={self._f47_session_output} "
            f"total_tokens={total_tokens} session_id={self.document_id!r} (配置のみ・G62 LLM なし)"
        )

        return {
            'success': True,
            'table_analyses': table_analyses,
            'tokens_used': total_tokens,
            'input_tokens': self._f47_session_input,
            'output_tokens': self._f47_session_output,
        }

    # =========================================================================
    # サブテーブルごとの配置（G44 出力 + G32 意味推定）
    # =========================================================================

    def _process_sub_tables(
        self,
        table_id: str,
        sub_tables: List[Dict[str, Any]],
        *,
        semantic_inference: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], int]:
        """G44 のサブテーブルを G32 結果に基づき配置（col_map 等）する。"""
        if not sub_tables:
            return {'table_type': 'empty', 'sections': []}, 0

        if len(sub_tables) > 1:
            logger.info(
                f"[G62] G44 由来のサブテーブルが {len(sub_tables)} 個（行・列ブロック分割は F56）"
            )

        tokens = 0
        processed_sections = []
        total_records = 0

        for sub_idx, sub in enumerate(sub_tables):
            group_name = sub.get("group_name", "")
            split_axis = sub.get("split_axis", "none")
            # G32 の infer と同一の行フィルタ（ここだけズレると row_analysis 長が合わない）
            original_row_count = len(sub.get("data") or [])
            sub_data = _strip_auto_column_rows(list(sub.get("data") or []))
            if len(sub_data) < original_row_count:
                logger.info(f"[G62] 自動列名のみの行を除外: {original_row_count}行 → {len(sub_data)}行")
            dsr_pre = int((sub.get("metadata") or {}).get("data_start_row") or 1)
            sub_data = prune_blank_body_rows(sub_data, data_start_row=dsr_pre)

            if not sub_data:
                continue

            nrows = len(sub_data)
            ncols = max((len(r) for r in sub_data), default=0)

            logger.info(f"[G62] 入力データ全行:")
            for row_idx, row in enumerate(sub_data):
                logger.info(f"[G62]   行{row_idx}: {row}")

            label = f"{sub_idx + 1}/{len(sub_tables)}: {group_name} ({split_axis})" if group_name else str(sub_idx + 1)
            logger.info(f"\n[G62]   サブセクション {label}")

            stid = str(sub.get("sub_table_id") or "")
            infer_key = f"{table_id}::{stid}" if stid else f"{table_id}::"
            by = semantic_inference.get("by_sub_table")
            if not isinstance(by, dict):
                raise RuntimeError("semantic_inference.by_sub_table must be a dict")

            pre = by.get(infer_key)
            if pre is None:
                raise RuntimeError(
                    f"G62 missing G32 semantic_inference for key={infer_key!r} "
                    f"(known keys: {list(by.keys())!r})"
                )
            if not pre.get("success"):
                raise RuntimeError(
                    f"G32 semantic_inference invalid for key={infer_key!r}: success={pre.get('success')!r}"
                )
            if not pre.get("table_semantics") or not isinstance(pre.get("table_semantics"), dict):
                raise RuntimeError(
                    f"G32 semantic_inference missing table_semantics for key={infer_key!r}"
                )

            ra_f46 = pre.get("row_analysis") or []
            ca_f46 = pre.get("col_analysis") or []
            if nrows and len(ra_f46) < nrows:
                raise RuntimeError(
                    f"[G62] G32 row_analysis too short key={infer_key!r}: "
                    f"got {len(ra_f46)} need {nrows}"
                )
            if ncols and len(ca_f46) < ncols:
                raise RuntimeError(
                    f"[G62] G32 col_analysis too short key={infer_key!r}: "
                    f"got {len(ca_f46)} need {ncols}"
                )
            commonality = {
                "success": True,
                "row_analysis": ra_f46[:nrows] if nrows else ra_f46,
                "col_analysis": ca_f46[:ncols] if ncols else ca_f46,
            }
            logger.info(
                f"[G62] 行・列分析は G32 正本を使用し配置のみ key={infer_key!r}"
            )

            sub_meta = sub.get("metadata") if isinstance(sub.get("metadata"), dict) else {}
            if sub_meta.get("lr_rebuilt"):
                header_info = self._header_info_from_f51_metadata(
                    sub_data, sub_meta, ncols, col_analysis=ca_f46
                )
            else:
                header_info = self._detect_headers_from_commonality(sub_data, commonality, ncols)
            dsr = int(header_info.get("data_start_row") or 0)
            resolved_data, horizontal_merges = apply_merged_cell_resolution(
                sub_data,
                data_start_row=dsr,
                row_label_col=header_info.get("row_label_col"),
            )
            resolved_data = _drop_body_rows_identical_to_header(
                resolved_data, data_start_row=dsr
            )

            table_semantics = pre["table_semantics"]

            # human-readable タイトルを生成（chunk_content の先頭に使用）
            sem_target = table_semantics.get('target') or ''
            sem_type_ja = table_semantics.get('type_ja') or ''
            if sem_target and sem_type_ja:
                semantic_title = f"{sem_target} {sem_type_ja}"
            elif sem_type_ja:
                semantic_title = sem_type_ja
            else:
                semantic_title = f"{table_id} - {group_name}" if group_name else table_id

            title = f"{table_id} - {group_name}" if group_name else table_id
            sec_meta_out = {
                    'header_rows': header_info['header_rows'],
                    'row_label_col': header_info['row_label_col'],
                    'data_start_row': header_info['data_start_row'],
                    'col_map': header_info['col_map'],
                    'header_meanings': header_info.get('header_meanings', {}),
                    'row_range': [0, len(resolved_data) - 1],
                    'horizontal_merges': horizontal_merges,
                    'implicit_headers': header_info.get('implicit_headers', []),
                    'split_from': group_name,
                    'split_axis': split_axis,
                    'primary_header_candidate': group_name,
                    'table_semantics': table_semantics,
                    'col_analysis': ca_f46[:ncols] if ncols else ca_f46,
                    **{
                        k: v
                        for k, v in (sub.get('metadata') or {}).items()
                        if k
                        in (
                            'lr_merged_vertical_contract',
                            'vertical_merge_judge',
                            'vertical_merge_mode',
                            'vertical_merge_confidence',
                            'vertical_merge_rationale',
                            'correspondence_summary',
                            'vertical_merges_ai',
                            'logical_row_count',
                            'geometry_evidence',
                            'lr_rebuilt',
                            'source_rows',
                            'output_rows',
                            'column_headers',
                            'display_column_labels',
                            'f56_split_axis',
                        )
                    },
                }
            if split_axis == "col" and resolved_data:
                sec_meta_out["column_headers"] = [
                    _cell_text(c) for c in resolved_data[0]
                ]
                while len(sec_meta_out["column_headers"]) < ncols:
                    sec_meta_out["column_headers"].append("")
                sec_meta_out["column_headers"] = sec_meta_out["column_headers"][:ncols]
                sec_meta_out.pop("horizontal_merges", None)
            processed_sections.append({
                'title': title,
                'group_name': group_name or '',
                'semantic_title': semantic_title,
                'table_type': header_info.get('table_type', 'structured'),
                'description': pre.get('description', ''),
                'data': resolved_data,
                'metadata': sec_meta_out,
            })
            hi_labels = header_info.get("display_column_labels") or header_info.get("column_headers")
            if hi_labels:
                processed_sections[-1]["metadata"]["display_column_labels"] = list(hi_labels)
                processed_sections[-1]["metadata"]["column_headers"] = list(hi_labels)
            total_records += len(sub_data)

        if len(processed_sections) == 0:
            return {'table_type': 'empty', 'sections': []}, tokens

        return {
            'table_type': 'multi_section' if len(processed_sections) > 1 else processed_sections[0].get('table_type', 'structured'),
            'description': f"{len(processed_sections)}個のセクションを含む表" if len(processed_sections) > 1 else processed_sections[0].get('description', ''),
            'sections': processed_sections,
            'metadata': {
                'total_sections': len(processed_sections),
                'total_records': total_records
            }
        }, tokens

    # =========================================================================
    # ヘッダー / col_map（G32 の行・列分析を配置に写す）
    # =========================================================================

    def _header_info_from_f51_metadata(
        self,
        section_data: List[List],
        meta: Dict[str, Any],
        grid_max_cols: int,
        *,
        col_analysis: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """G28 再構成済み表は upstream metadata を正本とする（G32 category 推定で上書きしない）。"""
        if "header_rows" in meta:
            header_rows = list(meta.get("header_rows") or [])
        else:
            header_rows = [0]
        dsr_raw = meta.get("data_start_row")
        if isinstance(dsr_raw, int):
            data_start_row = dsr_raw
        elif header_rows:
            data_start_row = max(header_rows) + 1
        else:
            data_start_row = 0
        row_label_col = meta.get("row_label_col")
        col_headers = [_cell_text(c) for c in (meta.get("column_headers") or [])]
        if not col_headers and header_rows and section_data:
            h0 = header_rows[0]
            if 0 <= h0 < len(section_data):
                col_headers = [_cell_text(c) for c in section_data[h0]]
        while len(col_headers) < grid_max_cols:
            col_headers.append("")
        col_headers = col_headers[:grid_max_cols]
        # G44 列分割サブ表は _merge_header_row_labels が正本。
        # col_analysis は元表（親）から引き継いだもので列マッピングが異なるため使わない。
        if meta.get("extract_col_start") is None:
            col_headers = self._fill_empty_column_headers(col_headers, col_analysis)
        header_data_cols = max(0, int(grid_max_cols) - 1)
        rest = list(col_headers[1:]) if len(col_headers) > 1 else list(col_headers)
        if header_data_cols and len(rest) < header_data_cols:
            rest.extend([None] * (header_data_cols - len(rest)))
        elif header_data_cols and len(rest) > header_data_cols:
            rest = rest[:header_data_cols]
        filled_headers = {"f51_header": self._fill_forward(rest)}
        col_map = self._build_col_map(filled_headers)
        logger.info(
            f"[G62] G28 正本ヘッダー: header_rows={header_rows} "
            f"data_start={data_start_row} columns={col_headers!r}"
        )
        return {
            "table_type": "structured",
            "header_rows": header_rows,
            "header_meanings": {"0": "f51_header"},
            "row_label_col": row_label_col,
            "data_start_row": data_start_row,
            "col_map": col_map,
            "filled_headers": filled_headers,
            "implicit_headers": [],
            "column_headers": col_headers,
            "display_column_labels": list(col_headers),
        }

    def _detect_headers_from_commonality(
        self,
        section_data: List[List],
        commonality: Dict[str, Any],
        grid_max_cols: int,
    ) -> Dict[str, Any]:
        """
        G32 の ``row_analysis`` / ``col_analysis`` からヘッダー行・データ開始行・``col_map`` を組み立てる。

        Returns:
            {
                'table_type': str,
                'header_rows': List[int],
                'header_meanings': Dict[str, str],
                'row_label_col': Optional[int],
                'data_start_row': int,
                'col_map': Dict[int, Dict],
                'filled_headers': Dict[str, List],
                'implicit_headers': List[Dict],
            }
        """
        row_analysis = commonality.get('row_analysis', [])
        col_analysis = commonality.get('col_analysis', [])

        # ヘッダー行は列 1.. を見る。表全体の最大列数に合わせて右端を None 埋めし結合穴を左埋めしやすくする。
        header_data_cols = max(0, int(grid_max_cols) - 1)

        # Step 1: ヘッダー行を特定（「カテゴリー名」の行）
        header_rows = []
        header_meanings = {}
        data_start_row = 0

        for ra in row_analysis:
            row_idx = int(ra['row_index'])
            if ra['abstraction_level'] == 'category_name':
                if row_idx < len(section_data) and row_starts_with_circled_marker(
                    section_data[row_idx]
                ):
                    if data_start_row == 0 or row_idx < data_start_row:
                        data_start_row = row_idx
                    continue
                header_rows.append(row_idx)
                header_meanings[str(row_idx)] = ra['common_type']
            else:
                # 最初の「具体的な値」行がデータ開始
                if data_start_row == 0 or row_idx < data_start_row:
                    data_start_row = row_idx

        header_rows = [
            r
            for r in header_rows
            if r < len(section_data)
            and not row_starts_with_circled_marker(section_data[r])
        ]
        if header_rows:
            data_start_row = max(header_rows) + 1
        elif section_data:
            data_start_row = 0

        # ヘッダー行がない場合（暗黙的ヘッダー）
        implicit_headers = []
        if not header_rows:
            logger.info("[G62] 明示的ヘッダー行なし → 暗黙的ヘッダーを生成")
            data_start_row = 0
            # 列分析から暗黙的ヘッダーを生成
            for ca in col_analysis:
                if ca['abstraction_level'] == 'category_name':
                    implicit_headers.append({
                        'col_index': int(ca['col_index']),
                        'type': ca['common_type']
                    })

        # Step 2: 行ラベル列を特定（「カテゴリー名」の列）
        row_label_col = None
        for ca in col_analysis:
            if ca['abstraction_level'] == 'category_name':
                # 最初のカテゴリー名列を行ラベル列とする
                # （複数ある場合は、データ列に挟まれたものを優先）
                row_label_col = int(ca['col_index'])
                break

        # Step 3: 列ヘッダーを構築
        # ★重要: 最初の列（col 0 = 行ラベル列）は列ヘッダーに含めない
        filled_headers = {}
        for row_idx in header_rows:
            if row_idx < len(section_data):
                raw_row = section_data[row_idx]
                # ★ raw_row[1:] で最初の列をスキップ（行データを列ヘッダーに転置しない）
                if len(raw_row) > 1 or header_data_cols > 0:
                    rest = list(raw_row[1:]) if len(raw_row) > 1 else []
                    if len(rest) < header_data_cols:
                        rest.extend([None] * (header_data_cols - len(rest)))
                    elif header_data_cols and len(rest) > header_data_cols:
                        rest = rest[:header_data_cols]
                    filled = self._fill_forward(rest)
                    meaning = header_meanings.get(str(row_idx), f'header_{row_idx}')
                    filled_headers[meaning] = filled

        # Step 4: 列座標マップを構築
        col_map = self._build_col_map(filled_headers)

        # 暗黙的ヘッダーがある場合、col_mapに追加
        if implicit_headers:
            for ih in implicit_headers:
                col_idx = ih['col_index']
                if col_idx not in col_map:
                    col_map[col_idx] = {}
                col_map[col_idx]['implicit_type'] = ih['type']

        logger.info(f"[G62] ヘッダー検出: header_rows={header_rows}, row_label_col={row_label_col}, data_start={data_start_row}")
        if implicit_headers:
            logger.info(f"[G62] 暗黙的ヘッダー: {implicit_headers}")

        col_headers = [""] * grid_max_cols
        if header_rows:
            h0 = header_rows[0]
            if 0 <= h0 < len(section_data):
                col_headers = [_cell_text(c) for c in section_data[h0]]
        elif implicit_headers:
            for ih in implicit_headers:
                ci = int(ih["col_index"])
                if 0 <= ci < grid_max_cols:
                    col_headers[ci] = _cell_text(ih.get("type"))
        while len(col_headers) < grid_max_cols:
            col_headers.append("")
        col_headers = col_headers[:grid_max_cols]
        col_headers = self._fill_empty_column_headers(col_headers, col_analysis)

        return {
            'table_type': 'structured',
            'header_rows': header_rows,
            'header_meanings': header_meanings,
            'row_label_col': row_label_col,
            'data_start_row': data_start_row,
            'col_map': col_map,
            'filled_headers': filled_headers,
            'implicit_headers': implicit_headers,
            'column_headers': col_headers,
            'display_column_labels': list(col_headers),
        }

    # =========================================================================
    # データ再構造化
    # =========================================================================

    # =========================================================================
    # ユーティリティ
    # =========================================================================

    _GENERIC_COL_ANALYSIS_LABELS = frozenset(
        {"授業名", "科目", "列", "表", "項目", "内容", "金額", "項目名", "見出し", "列見出し"}
    )

    @staticmethod
    def _fill_empty_column_headers(
        col_headers: List[str],
        col_analysis: Optional[List[Dict[str, Any]]],
    ) -> List[str]:
        """空列のみ G32 common_type で補う。抽象的な「授業名」等で extract 見出しを上書きしない。"""
        out = list(col_headers)
        if not col_analysis:
            return out
        for ca in col_analysis:
            if not isinstance(ca, dict):
                continue
            try:
                ci = int(ca["col_index"])
            except (KeyError, TypeError, ValueError):
                continue
            if ci < 0 or ci >= len(out) or out[ci]:
                continue
            label = _cell_text(ca.get("common_type"))
            if not label or label in G62TableLayoutProcessor._GENERIC_COL_ANALYSIS_LABELS:
                continue
            if ca.get("abstraction_level") == "category_name" and label in (
                G62TableLayoutProcessor._GENERIC_COL_ANALYSIS_LABELS
            ):
                continue
            out[ci] = label
        return out

    def _fill_forward(self, row: List) -> List:
        """結合セル由来の欠損を左の非欠損セルで埋める（横方向）。"""
        result: List[Any] = []
        last: Any = None
        for val in row:
            if is_merge_placeholder(val):
                result.append(last)
            else:
                last = val
                result.append(val)
        return result

    def _build_col_map(self, filled_headers: Dict[str, List]) -> Dict[int, Dict]:
        """
        各列の座標マップを構築
        {col_index: {meaning: value, ...}}
        """
        if not filled_headers:
            return {}

        col_count = max((len(v) for v in filled_headers.values()), default=0)
        col_map = {}

        for col_idx in range(col_count):
            coord = {}
            for meaning, row in filled_headers.items():
                if col_idx < len(row) and row[col_idx] is not None:
                    coord[meaning] = row[col_idx]
            col_map[col_idx] = coord

        return col_map

    def _error_result(self, msg: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'success': False,
            'error': msg,
            'table_analyses': [],
            'tokens_used': 0,
            'input_tokens': int(getattr(self, '_f47_session_input', 0) or 0),
            'output_tokens': int(getattr(self, '_f47_session_output', 0) or 0),
        }
