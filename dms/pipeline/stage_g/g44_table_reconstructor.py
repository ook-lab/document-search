"""
F56: Table Reconstructor（表再構成）

責務: F55の検出結果に従って表を分割・再構成するだけ。
判断（検出）は一切しない。F55の結果を盲目的に使う。

分割ルール:
- 行方向分割 (row_split): common_top 行を各ブロックの先頭に、common_bottom 行を末尾に複製して付ける
- 列方向分割 (col_split): common_left 列を各ブロックの左端に、common_right 列を右端に複製して付ける
- 分割なし: 入力をそのまま単要素リストで返す
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
import re
from loguru import logger

from dms.pipeline.stage_g.merged_cell_grid import prune_blank_body_rows

_DAY_ROW_RE = re.compile(r"^\d{1,2}\s*[（(]")


def _infer_header_depth(data: List[List]) -> int:
    """日付行・データ行の直前までをヘッダー行数とする（時間割の朝/1〜6 行を保持）。"""
    for i, row in enumerate(data[:6]):
        if not isinstance(row, (list, tuple)) or not row:
            continue
        c0 = "" if row[0] is None else str(row[0]).strip()
        if _DAY_ROW_RE.match(c0):
            return max(1, i)
    return min(2, len(data)) if len(data) >= 2 else 1


def _merge_header_row_labels(data: List[List], depth: int, ncols: int) -> List[str]:
    labels: List[str] = []
    for c in range(ncols):
        parts: List[str] = []
        for r in range(min(depth, len(data))):
            row = data[r]
            if not isinstance(row, (list, tuple)) or c >= len(row):
                continue
            t = "" if row[c] is None else str(row[c]).strip()
            if t:
                parts.append(t)
        labels.append(" / ".join(parts) if parts else "")
    return labels


class G44TableReconstructor:
    """F56: Table Reconstructor（表再構成専用）"""

    _DETECTION_EMPTY: Dict[str, Any] = {
        "row_split": False, "row_blocks": None, "row_common_top": None, "row_common_bottom": None,
        "col_split": False, "col_blocks": None, "col_common_left": None, "col_common_right": None,
    }

    def __init__(self, next_stage=None):
        """
        Args:
            next_stage: 次のステージ（G32 → G62 チェーン）のインスタンス
        """
        self.next_stage = next_stage

    def process(self, e13_result: Dict[str, Any], year_context=None, table_log_dir: Optional[Path] = None) -> Dict[str, Any]:
        """
        F55の結果を受け取り、各表を再構成してF58に渡す。

        Args:
            e13_result: F55の出力（structured_tables, detections を含む）
            year_context: 年度コンテキスト（次ステージへ引き継ぎ）
            table_log_dir: F57/G62 専用ログの親ディレクトリ（任意）

        Returns:
            {
                'success': bool,
                'e14_reconstructed': [{table_id, sub_tables}, ...],
                'structured_tables': list,
                'f47_result': ...  # next_stage がある場合
                'f46_result': ...  # G32 出口（線意味 structured_output）
            }
        """
        logger.info("[G44] ========== 表再構成開始 ==========")
        structured_tables = e13_result.get('structured_tables', [])
        detections = e13_result.get('detections', [])  # list（structured_tables と同順）
        logger.info(f"[G44] 入力表数: {len(structured_tables)}個")

        e14_reconstructed = []
        for idx, table in enumerate(structured_tables):
            table_id = table.get('table_id', '')
            headers = table.get('headers', [])
            rows = table.get('rows', [])
            full = []
            if headers:
                full.append(headers)
            full.extend(rows)

            detection = detections[idx] if idx < len(detections) else dict(self._DETECTION_EMPTY)
            sub_tables = self.reconstruct(full, detection)
            parent_meta = table.get('metadata') if isinstance(table.get('metadata'), dict) else {}
            # サブテーブルIDを付与（例: P0_D1_S1, P0_D1_S2）
            for s_idx, sub in enumerate(sub_tables, 1):
                sub['sub_table_id'] = f"{table_id}_S{s_idx}"
                sub_meta = dict(parent_meta) if parent_meta else {}
                sub_meta["f56_split_axis"] = sub.get("split_axis", "none")
                sub_data = sub.get("data") or []
                split_meta = sub.get("metadata") if isinstance(sub.get("metadata"), dict) else {}
                if split_meta:
                    sub_meta.update(split_meta)
                if sub.get("split_axis") == "col" and sub_data and not split_meta.get("header_rows"):
                    sub_meta["column_headers"] = [
                        "" if c is None else str(c).strip()
                        for c in sub_data[0]
                    ]
                    sub_meta.pop("display_column_labels", None)
                    sub_meta.pop("horizontal_merges", None)
                    sub_meta["header_rows"] = [0]
                    sub_meta["data_start_row"] = 1
                sub["metadata"] = sub_meta
            e14_reconstructed.append({'table_id': table_id, 'sub_tables': sub_tables})

            logger.info(f"[G44] 表[{idx}] {table_id}: {len(sub_tables)}サブテーブル")
            for sub_idx, sub in enumerate(sub_tables, 1):
                group_name = sub.get('group_name', '')
                split_axis = sub.get('split_axis', 'none')
                sub_data = sub.get('data', [])
                label = f"{group_name} ({split_axis})" if group_name else f"ブロック{sub_idx} ({split_axis})"
                logger.info(f"  サブテーブル {sub_idx}/{len(sub_tables)}: {label} → {len(sub_data)}行")
                for row_idx, row in enumerate(sub_data, 1):
                    logger.info(f"    Row {row_idx}: {row}")
            logger.info("")

        logger.info("[G44] ========== 表再構成完了 ==========")

        result = {
            'success': True,
            'e14_reconstructed': e14_reconstructed,
            'structured_tables': structured_tables,
        }

        if self.next_stage:
            logger.info("[G44] → 次のステージ（F57→F58）を呼び出します")
            chain_out = self.next_stage.process(
                e14_reconstructed,
                year_context=year_context,
                table_log_dir=table_log_dir,
                chain_context=e13_result,
            )
            if isinstance(chain_out, dict) and "f47_result" in chain_out:
                result["f46_result"] = chain_out.get("f46_result")
                result["f47_result"] = chain_out["f47_result"]
            else:
                result["f47_result"] = chain_out

        return result

    def reconstruct(
        self,
        table: List[List],
        detection: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        F55の検出結果に従って表を分割・再構成する。

        Args:
            table: 2次元配列（表データ）
            detection: G41RepeatingHeaderDetector.detect() の戻り値

        Returns:
            [
                {
                    "data": List[List],
                    "group_name": str,
                    "split_axis": "row" | "col" | "none",
                },
                ...
            ]
        """
        if detection.get("row_split"):
            return self._apply_row_split(table, detection)

        if detection.get("col_split"):
            return self._apply_col_split(table, detection)

        return [{"data": table, "group_name": "", "split_axis": "none"}]

    # =========================================================================
    # 行方向分割
    # =========================================================================

    def _apply_row_split(self, table: List[List], detection: Dict) -> List[Dict]:
        blocks = detection["row_blocks"]          # [{"start": int, "end": int}, ...]
        common_top = detection.get("row_common_top") or []      # 行インデックスリスト
        common_bottom = detection.get("row_common_bottom") or []  # 行インデックスリスト

        results = []
        for i, block in enumerate(blocks):
            row_start = block["start"]
            row_end = block["end"]

            # common_top 行 ＋ ブロック本体行 ＋ common_bottom 行のインデックスを結合
            row_indices = common_top + list(range(row_start, row_end + 1)) + common_bottom
            sub_table = [list(table[r]) for r in row_indices if r < len(table)]
            sub_table = prune_blank_body_rows(sub_table, data_start_row=1)

            if not sub_table:
                continue

            group_name = self._pick_name_from_row(table, row_start)
            results.append({
                "data": sub_table,
                "group_name": group_name or f"ブロック{i + 1}",
                "split_axis": "row",
            })
            logger.info(
                f"[G44] 行ブロック{i + 1}: 行{row_start}〜{row_end}, "
                f"common_top={common_top}, common_bottom={common_bottom}, group={group_name}"
            )

        if not results:
            logger.warning("[G44] 行分割: 全ブロックが空。元表をそのまま返さずスキップ。")
        return results

    # =========================================================================
    # 列方向分割
    # =========================================================================

    def _apply_col_split(self, table: List[List], detection: Dict) -> List[Dict]:
        blocks = detection["col_blocks"]           # [{"start": int, "end": int}, ...]
        common_left = detection.get("col_common_left") or []    # 列インデックスリスト
        common_right = detection.get("col_common_right") or []  # 列インデックスリスト
        parent_ncols = max((len(row) for row in table), default=0)

        results = []
        for i, block in enumerate(blocks):
            col_start = block["start"]
            col_end = block["end"]

            # common_left 列 ＋ ブロック本体列 ＋ common_right 列のインデックスを結合
            col_indices = common_left + list(range(col_start, col_end + 1)) + common_right
            sub_table = [
                [row[c] if c < len(row) else None for c in col_indices]
                for row in table
            ]
            hdr_depth = _infer_header_depth(sub_table)
            sub_table = prune_blank_body_rows(sub_table, data_start_row=hdr_depth)

            if not sub_table:
                continue

            group_name = self._pick_name_from_col(table, col_start)
            ncols_sub = max((len(r) for r in sub_table), default=0)
            hdr_labels = _merge_header_row_labels(sub_table, hdr_depth, ncols_sub)
            results.append({
                "data": sub_table,
                "group_name": group_name or f"ブロック{i + 1}",
                "split_axis": "col",
                "metadata": {
                    "header_rows": list(range(hdr_depth)),
                    "data_start_row": hdr_depth,
                    "column_headers": hdr_labels,
                    "display_column_labels": list(hdr_labels),
                    "parent_table_col_count": parent_ncols,
                    "extract_col_start": col_start,
                    "extract_col_end": col_end,
                    "extract_col_common_left": list(common_left),
                },
            })
            logger.info(
                f"[G44] 列ブロック{i + 1}: 列{col_start}〜{col_end}, "
                f"common_left={common_left}, common_right={common_right}, group={group_name}"
            )

        if not results:
            logger.warning("[G44] 列分割: 全ブロックが空。元表をそのまま返さずスキップ。")
        return results

    # =========================================================================
    # ユーティリティ
    # =========================================================================

    def _pick_name_from_row(self, table: List[List], row_idx: int) -> str:
        """ブロック先頭行の左3列から最初の非空値をグループ名として返す"""
        if row_idx >= len(table):
            return ""
        row = table[row_idx]
        for c in range(min(3, len(row))):
            v = row[c]
            if v is not None and str(v).strip():
                return str(v).strip()
        return ""

    @staticmethod
    def _row_is_only_auto_column_names(row: List[Any]) -> bool:
        nonempty = [c for c in row if c is not None and str(c).strip()]
        if not nonempty:
            return False
        return all(re.match(r"^(Col|列)\d+$", str(c).strip()) for c in nonempty)

    def _pick_name_from_col(self, table: List[List], col_idx: int) -> str:
        """上数行の該当列からブロック名。自動列名行はスキップし、月ラベル行を優先。"""
        month_re = re.compile(r"\d{1,2}\s*月")
        for r in range(min(10, len(table))):
            row = table[r]
            if not isinstance(row, (list, tuple)) or col_idx >= len(row):
                continue
            if self._row_is_only_auto_column_names(row):
                continue
            v = row[col_idx]
            if v is None or not str(v).strip():
                continue
            s = str(v).strip()
            if month_re.search(s):
                return s
        for r in range(min(10, len(table))):
            row = table[r]
            if not isinstance(row, (list, tuple)) or col_idx >= len(row):
                continue
            if self._row_is_only_auto_column_names(row):
                continue
            v = row[col_idx]
            if v is not None and str(v).strip():
                return str(v).strip()
        return ""
