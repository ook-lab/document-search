"""
G-14: Table Reconstructor（表再構成）

責務: G-13の検出結果に従って表を分割・再構成するだけ。
判断（検出）は一切しない。G-13の結果を盲目的に使う。

分割ルール:
- 行方向分割 (row_split): common_top 行を各ブロックの先頭に、common_bottom 行を末尾に複製して付ける
- 列方向分割 (col_split): common_left 列を各ブロックの左端に、common_right 列を右端に複製して付ける
- 分割なし: 入力をそのまま単要素リストで返す
"""

from typing import Dict, Any, List
from loguru import logger


class G14TableReconstructor:
    """G-14: Table Reconstructor（表再構成専用）"""

    _DETECTION_EMPTY: Dict[str, Any] = {
        "row_split": False, "row_blocks": None, "row_common_top": None, "row_common_bottom": None,
        "col_split": False, "col_blocks": None, "col_common_left": None, "col_common_right": None,
    }

    def __init__(self, next_stage=None):
        """
        Args:
            next_stage: 次のステージ（G-17）のインスタンス
        """
        self.next_stage = next_stage

    def process(self, g13_result: Dict[str, Any], year_context=None) -> Dict[str, Any]:
        """
        G-13の結果を受け取り、各表を再構成してG-17に渡す。

        Args:
            g13_result: G-13の出力（structured_tables, detections を含む）
            year_context: 年度コンテキスト（次ステージへ引き継ぎ）

        Returns:
            {
                'success': bool,
                'g14_reconstructed': [{table_id, sub_tables}, ...],
                'structured_tables': list,
                'g17_result': ...  # next_stage がある場合
            }
        """
        logger.info("[G-14] ========== 表再構成開始 ==========")
        structured_tables = g13_result.get('structured_tables', [])
        detections = g13_result.get('detections', [])  # list（structured_tables と同順）
        logger.info(f"[G-14] 入力表数: {len(structured_tables)}個")

        g14_reconstructed = []
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
            # サブテーブルIDを付与（例: P0_D1_S1, P0_D1_S2）
            for s_idx, sub in enumerate(sub_tables, 1):
                sub['sub_table_id'] = f"{table_id}_S{s_idx}"
            g14_reconstructed.append({'table_id': table_id, 'sub_tables': sub_tables})

            logger.info(f"[G-14] 表[{idx}] {table_id}: {len(sub_tables)}サブテーブル")
            for sub_idx, sub in enumerate(sub_tables, 1):
                group_name = sub.get('group_name', '')
                split_axis = sub.get('split_axis', 'none')
                sub_data = sub.get('data', [])
                label = f"{group_name} ({split_axis})" if group_name else f"ブロック{sub_idx} ({split_axis})"
                logger.info(f"  サブテーブル {sub_idx}/{len(sub_tables)}: {label} → {len(sub_data)}行")
                for row_idx, row in enumerate(sub_data, 1):
                    logger.info(f"    Row {row_idx}: {row}")
            logger.info("")

        logger.info("[G-14] ========== 表再構成完了 ==========")

        result = {
            'success': True,
            'g14_reconstructed': g14_reconstructed,
            'structured_tables': structured_tables,
        }

        if self.next_stage:
            logger.info("[G-14] → 次のステージ（G-17）を呼び出します")
            g17_result = self.next_stage.process(g14_reconstructed, year_context=year_context)
            result['g17_result'] = g17_result

        return result

    def reconstruct(
        self,
        table: List[List],
        detection: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        G-13の検出結果に従って表を分割・再構成する。

        Args:
            table: 2次元配列（表データ）
            detection: G13RepeatingHeaderDetector.detect() の戻り値

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

            if not sub_table:
                continue

            group_name = self._pick_name_from_row(table, row_start)
            results.append({
                "data": sub_table,
                "group_name": group_name or f"ブロック{i + 1}",
                "split_axis": "row",
            })
            logger.info(
                f"[G-14] 行ブロック{i + 1}: 行{row_start}〜{row_end}, "
                f"common_top={common_top}, common_bottom={common_bottom}, group={group_name}"
            )

        if not results:
            logger.warning("[G-14] 行分割: 全ブロックが空。元表をそのまま返さずスキップ。")
        return results

    # =========================================================================
    # 列方向分割
    # =========================================================================

    def _apply_col_split(self, table: List[List], detection: Dict) -> List[Dict]:
        blocks = detection["col_blocks"]           # [{"start": int, "end": int}, ...]
        common_left = detection.get("col_common_left") or []    # 列インデックスリスト
        common_right = detection.get("col_common_right") or []  # 列インデックスリスト

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

            if not sub_table:
                continue

            group_name = self._pick_name_from_col(table, col_start)
            results.append({
                "data": sub_table,
                "group_name": group_name or f"ブロック{i + 1}",
                "split_axis": "col",
            })
            logger.info(
                f"[G-14] 列ブロック{i + 1}: 列{col_start}〜{col_end}, "
                f"common_left={common_left}, common_right={common_right}, group={group_name}"
            )

        if not results:
            logger.warning("[G-14] 列分割: 全ブロックが空。元表をそのまま返さずスキップ。")
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

    def _pick_name_from_col(self, table: List[List], col_idx: int) -> str:
        """上3行の該当列から最初の非空値をグループ名として返す"""
        for r in range(min(3, len(table))):
            if col_idx < len(table[r]):
                v = table[r][col_idx]
                if v is not None and str(v).strip():
                    return str(v).strip()
        return ""
