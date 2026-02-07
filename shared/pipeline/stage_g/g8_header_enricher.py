"""
G8: Header Enricher（ヘッダー紐付け強化）

G7の header_map を使い、各データセルに対応するヘッダー値を機械的に合成する。
AI不要。Python のみの決定的処理。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ver 2.1: global_col_map 対応

G7が構築した global_col_map を使い、(panel_id, physical_col) → global_col に変換。
パネル列の重複を解消し、真のグローバルグリッドでヘッダーを紐付ける。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import time
from typing import Dict, Any, List, Set, Tuple
from loguru import logger


class G8HeaderEnricher:
    """G8: global_col_map によるヘッダー紐付け"""

    def process(self, tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        g8_start = time.time()
        logger.info(f"[G8] ヘッダー紐付け開始: {len(tables)}表")

        for table in tables:
            ref_id = table.get("ref_id", "?")
            cells = table.get("cells_flat") or table.get("cells") or []
            header_map = table.get("header_map", {})

            if not cells:
                logger.warning(f"[G8] セルなし: {ref_id}")
                table["cells_enriched"] = []
                continue

            gchr = header_map.get("global_col_header_rows", [])
            grhc = header_map.get("global_row_header_cols", [])

            if not gchr and not grhc:
                logger.warning(f"[G8] global headers なし: {ref_id} → cells_flat をそのまま使用")
                table["cells_enriched"] = cells
                continue

            enriched = self._enrich_cells(cells, header_map)
            table["cells_enriched"] = enriched

            # --- 全体集計 ---
            total_cells = len(enriched)
            header_cells = sum(1 for c in enriched if c.get("is_header", False))
            data_cells = [c for c in enriched if not c.get("is_header", False) and str(c.get("text", "")).strip()]
            data_count = len(data_cells)
            with_col = sum(1 for c in data_cells if c.get("col_header") is not None)
            with_row = sum(1 for c in data_cells if c.get("row_header") is not None)
            logger.info(
                f"[G8] {ref_id}: 全{total_cells}セル "
                f"(ヘッダー={header_cells}, データ={data_count}, "
                f"col_header付={with_col}/{data_count}, "
                f"row_header付={with_row}/{data_count})"
            )

            # --- パネル別集計（参考） ---
            by_panel_enriched: Dict[str, List[Dict]] = {}
            for c in enriched:
                pid = str(c.get("panel_id", 0) or 0)
                by_panel_enriched.setdefault(pid, []).append(c)

            for pid in sorted(by_panel_enriched.keys(), key=lambda x: int(x) if x.isdigit() else 0):
                pcells = by_panel_enriched[pid]
                p_header = sum(1 for c in pcells if c.get("is_header", False))
                p_data = [c for c in pcells if not c.get("is_header", False) and str(c.get("text", "")).strip()]
                p_data_count = len(p_data)
                p_col = sum(1 for c in p_data if c.get("col_header") is not None)
                p_row = sum(1 for c in p_data if c.get("row_header") is not None)
                logger.info(
                    f"[G8]   P{pid}(参考): ヘッダー={p_header}, データ={p_data_count}, "
                    f"col_header付={p_col}/{p_data_count}, "
                    f"row_header付={p_row}/{p_data_count}"
                )

            # --- 全セル出力（1セルも省略しない） ---
            for c in enriched:
                pid = c.get("panel_id", 0)
                r = c.get("row", 0)
                gc = c.get("global_col", c.get("col", 0))
                is_hdr = c.get("is_header", False)
                txt = str(c.get("text", "")).strip()
                ch = c.get("col_header")
                rh = c.get("row_header")
                role = "Hdr" if is_hdr else "Data"
                logger.info(
                    f"[G8]   P{pid} R{r}C{gc} [{role}] "
                    f"col_header={ch}, row_header={rh}, "
                    f"text={txt}"
                )

        elapsed = time.time() - g8_start
        logger.info(f"[G8] ヘッダー紐付け完了: {elapsed:.2f}s")
        return tables

    @staticmethod
    def _parse_col_map(serialized: Dict[str, int]) -> Dict[Tuple[str, int], int]:
        """JSON文字列キーを (pid, col) タプルキーに戻す"""
        result = {}
        for k, v in serialized.items():
            parts = k.rsplit("_", 1)
            if len(parts) == 2:
                result[(parts[0], int(parts[1]))] = v
        return result

    def _enrich_cells(self, cells: List[Dict], header_map: Dict) -> List[Dict]:
        """global_col_map を使い、各データセルにヘッダー値を付与"""
        ch_rows = set(header_map.get("global_col_header_rows", []))
        rh_cols = set(header_map.get("global_row_header_cols", []))
        total_cols = header_map.get("global_col_count", 0)
        gcm = self._parse_col_map(header_map.get("global_col_map", {}))

        logger.info(
            f"[G8] グローバルヘッダー: "
            f"col_header_rows={sorted(ch_rows)}, "
            f"row_header_cols={sorted(rh_cols)}, "
            f"global_col_count={total_cols}"
        )

        # --- グローバルグリッド構築 ---
        grid = self._build_global_grid(cells, gcm, total_cols)
        num_rows = len(grid)

        # --- ヘッダー空欄の最近傍コピー（パネル内制約） ---
        self._nearest_fill(grid, ch_rows, rh_cols, gcm)

        # --- 全C の col_header 一覧 ---
        logger.info(f"[G8] col_header一覧 (header_rows={sorted(ch_rows)}, 全{total_cols}列):")
        for ci in range(total_cols):
            tag = " [row_header列]" if ci in rh_cols else ""
            parts = []
            for hr in sorted(ch_rows):
                if hr < num_rows and ci < len(grid[hr]):
                    parts.append(grid[hr][ci] if grid[hr][ci] else "(空)")
                else:
                    parts.append("(範囲外)")
            col_val = " ".join(p for p in parts if p != "(空)" and p != "(範囲外)") or "(なし)"
            logger.info(f"[G8]   C{ci}: {parts} → '{col_val}'{tag}")

        # --- 全R の row_header 一覧 ---
        logger.info(f"[G8] row_header一覧 (header_cols={sorted(rh_cols)}):")
        for ri in range(num_rows):
            tag = " [col_header行]" if ri in ch_rows else ""
            parts = []
            for rc in sorted(rh_cols):
                if rc < len(grid[ri]):
                    parts.append(grid[ri][rc] if grid[ri][rc] else "(空)")
                else:
                    parts.append("(範囲外)")
            row_val = " ".join(p for p in parts if p != "(空)" and p != "(範囲外)") or "(なし)"
            logger.info(f"[G8]   R{ri}: {parts} → '{row_val}'{tag}")

        # --- 各セルを enrichment ---
        enriched = []
        for c in cells:
            pid = str(c.get("panel_id", 0) or 0)
            row = c.get("row", 0)
            col = c.get("col", 0)
            global_col = gcm.get((pid, col), col)
            text = str(c.get("text", "")).strip()

            is_header = row in ch_rows or global_col in rh_cols

            enriched_cell = dict(c)
            enriched_cell["is_header"] = is_header
            enriched_cell["global_col"] = global_col

            if is_header or not text:
                enriched_cell["enriched_text"] = text
                enriched_cell["col_header"] = None
                enriched_cell["row_header"] = None
                enriched.append(enriched_cell)
                continue

            # 列ヘッダー取得: global_col_header_rows の同じ global_col のテキスト
            col_header_parts = []
            for hr in sorted(ch_rows):
                if hr < len(grid) and global_col < len(grid[hr]) and grid[hr][global_col]:
                    col_header_parts.append(grid[hr][global_col])
            col_header = " ".join(col_header_parts) if col_header_parts else None

            # 行ヘッダー取得: global_row_header_cols の同じ row のテキスト（重複除去）
            row_header_parts = []
            for rc in sorted(rh_cols):
                if row < len(grid) and rc < len(grid[row]) and grid[row][rc]:
                    row_header_parts.append(grid[row][rc])
            # ミラー列（左右同値）の重複除去
            unique_rh = list(dict.fromkeys(row_header_parts))
            row_header = " ".join(unique_rh) if unique_rh else None

            # enriched_text 合成
            prefix_parts = []
            if col_header:
                prefix_parts.append(col_header)
            if row_header:
                prefix_parts.append(row_header)

            if prefix_parts:
                prefix = " | ".join(prefix_parts)
                enriched_text = f"[ {prefix} ] {text}"
            else:
                enriched_text = text

            enriched_cell["enriched_text"] = enriched_text
            enriched_cell["col_header"] = col_header
            enriched_cell["row_header"] = row_header
            enriched.append(enriched_cell)

        return enriched

    def _build_global_grid(
        self,
        cells: List[Dict],
        gcm: Dict[Tuple[str, int], int],
        total_cols: int,
    ) -> List[List[str]]:
        """
        global_col_map を使って真のグローバルグリッドを構築。
        (panel_id, physical_col) → global_col でマッピング。
        """
        max_row = max((c.get("row", 0) for c in cells), default=0)

        if total_cols == 0:
            total_cols = max(gcm.values(), default=0) + 1 if gcm else 1

        grid = [["" for _ in range(total_cols)] for _ in range(max_row + 1)]
        for c in cells:
            pid = str(c.get("panel_id", 0) or 0)
            r = c.get("row", 0)
            col = c.get("col", 0)
            gc = gcm.get((pid, col))
            if gc is not None and r <= max_row and gc < total_cols:
                text = str(c.get("text", "")).strip()
                grid[r][gc] = text

        return grid

    def _nearest_fill(
        self,
        grid: List[List[str]],
        ch_rows: Set[int],
        rh_cols: Set[int],
        gcm: Dict[Tuple[str, int], int] = None,
    ) -> None:
        """ヘッダー空欄の最近傍コピー（パネル内制約付き）"""
        num_rows = len(grid)

        # global_col → panel_id の逆引きマップ構築
        col_to_panel: Dict[int, str] = {}
        if gcm:
            for (pid, _), gc in gcm.items():
                col_to_panel[gc] = pid

        # 列ヘッダー行: 同パネル内で最も近い非空セルの値をコピー
        for hr in ch_rows:
            if hr >= num_rows:
                continue
            row_data = grid[hr]
            num_cols = len(row_data)
            for ci in range(num_cols):
                if ci in rh_cols:
                    continue
                if row_data[ci]:
                    continue
                my_panel = col_to_panel.get(ci)
                nearest_val = ""
                nearest_dist = num_cols + 1
                for ki in range(num_cols):
                    if ki == ci or ki in rh_cols or not row_data[ki]:
                        continue
                    # パネル制約: 同パネルの列のみ候補
                    if my_panel is not None and col_to_panel.get(ki) != my_panel:
                        continue
                    dist = abs(ki - ci)
                    if dist < nearest_dist:
                        nearest_dist = dist
                        nearest_val = row_data[ki]
                if nearest_val:
                    row_data[ci] = nearest_val
                    logger.info(
                        f"[G8] nearest fill col_header: "
                        f"R{hr}C{ci} ← '{nearest_val}' (dist={nearest_dist})"
                    )

        # 行ヘッダー列: 空セルに上下で最も近い非空セルの値をコピー
        for rc in rh_cols:
            for ri in range(num_rows):
                if ri in ch_rows:
                    continue
                if rc >= len(grid[ri]):
                    continue
                if grid[ri][rc]:
                    continue
                nearest_val = ""
                nearest_dist = num_rows + 1
                for ki in range(num_rows):
                    if ki == ri or ki in ch_rows:
                        continue
                    if rc < len(grid[ki]) and grid[ki][rc]:
                        dist = abs(ki - ri)
                        if dist < nearest_dist:
                            nearest_dist = dist
                            nearest_val = grid[ki][rc]
                if nearest_val:
                    grid[ri][rc] = nearest_val
                    logger.info(
                        f"[G8] nearest fill row_header: "
                        f"R{ri}C{rc} ← '{nearest_val}' (dist={nearest_dist})"
                    )
