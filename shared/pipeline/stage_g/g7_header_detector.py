"""
G7: Header Detector（ヘッダー検出）

データ型の境界（テキスト vs 数値）を重視したヘッダー検出。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ver 2.0: グローバル列連番化

パネルの列番号は重複する（P0のcol=1とP1のcol=1は別物理列）。
パネル順に列を連番化して、真のグローバル列インデックスを構築する。

例: P0(1列) + P1(3列) + P2(2列) + P3(1列) = 7列 → C0〜C6

出力:
  - header_map:
    panels: {...}
    global_col_header_rows: [...]
    global_row_header_cols: [...]
    global_col_map: {"0_1": 0, "1_1": 1, ...}  # "{panel_id}_{physical_col}" → global_col
    global_col_count: 7
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import json
import json_repair
import re
import time
from typing import Dict, Any, List, Optional, Tuple, Set
from loguru import logger


class G7HeaderDetector:
    """G7: 構造だけを見てヘッダーの位置を検出する"""

    DEFAULT_MODEL = "gemini-2.5-flash-lite"

    def __init__(self, llm_client=None, model: str = None):
        self.llm = llm_client
        self.model = model or self.DEFAULT_MODEL

    def process(self, tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        g7_start = time.time()
        logger.info(f"[G7] ヘッダー検出開始: {len(tables)}表")

        for table in tables:
            ref_id = table.get("ref_id", "?")
            cells = table.get("cells_flat") or table.get("cells") or []

            if not cells:
                logger.warning(f"[G7] セルなし: {ref_id}")
                table["header_map"] = {
                    "panels": {},
                    "global_col_header_rows": [],
                    "global_row_header_cols": [],
                    "global_col_map": {},
                    "global_col_count": 0,
                }
                continue

            header_map = self._detect_headers(cells, ref_id)
            table["header_map"] = header_map

            # --- グローバルヘッダー出力 ---
            gchr = header_map.get("global_col_header_rows", [])
            grhc = header_map.get("global_row_header_cols", [])
            total_cols = header_map.get("global_col_count", 0)
            logger.info(f"[G7] {ref_id}: global_col_header_rows={gchr}")
            logger.info(f"[G7] {ref_id}: global_row_header_cols={grhc}")
            logger.info(f"[G7] {ref_id}: global_col_count={total_cols}")

            # --- パネル別ログ（デバッグ用） ---
            panels = header_map.get("panels", {})
            logger.info(f"[G7] {ref_id}: {len(panels)}パネル (参考)")
            for pk in sorted(panels.keys()):
                cfg = panels[pk]
                ch_rows = cfg.get("col_header_rows", [])
                rh_cols_local = cfg.get("row_header_cols", [])
                rh_cols_global = cfg.get("row_header_cols_global", [])
                pid = pk.lstrip("P")
                panel_cells = [c for c in cells if str(c.get("panel_id", 0) or 0) == pid]
                total = len(panel_cells)
                max_row = max((c.get("row", 0) for c in panel_cells), default=0) if panel_cells else 0
                n_cols = len(set(c.get("col", 0) for c in panel_cells)) if panel_cells else 0
                logger.info(
                    f"[G7]   {pk}: {max_row+1}行x{n_cols}列 "
                    f"col_header_rows={ch_rows}, "
                    f"row_header_cols={rh_cols_local}(local) → {rh_cols_global}(global), "
                    f"セル数={total}"
                )

            # --- グローバルグリッド構築 ---
            gchr_set = set(gchr)
            grhc_set = set(grhc)
            gcm = self._parse_col_map(header_map.get("global_col_map", {}))
            max_row = max((c.get("row", 0) for c in cells), default=0)

            grid = [["" for _ in range(total_cols)] for _ in range(max_row + 1)]
            for c in cells:
                pid = str(c.get("panel_id", 0) or 0)
                r = c.get("row", 0)
                col = c.get("col", 0)
                gc = gcm.get((pid, col))
                if gc is not None and r <= max_row and gc < total_cols:
                    txt = str(c.get("text", "")).strip()
                    grid[r][gc] = txt
            num_rows = len(grid)

            # --- ヘッダー空欄の最近傍補完（パネル内制約） ---
            self._nearest_fill_grid(grid, gchr_set, grhc_set, gcm, total_cols)

            # --- 全Cの col_header 一覧 ---
            logger.info(f"[G7] col_header一覧 (header_rows={sorted(gchr_set)}, 全{total_cols}列):")
            for ci in range(total_cols):
                tag = " [row_header列]" if ci in grhc_set else ""
                parts = []
                for hr in sorted(gchr_set):
                    if hr < num_rows and ci < len(grid[hr]):
                        parts.append(grid[hr][ci] if grid[hr][ci] else "(空)")
                    else:
                        parts.append("(範囲外)")
                col_val = " ".join(p for p in parts if p != "(空)" and p != "(範囲外)") or "(なし)"
                logger.info(f"[G7]   C{ci}: {parts} → '{col_val}'{tag}")

            # --- 全Rの row_header 一覧 ---
            logger.info(f"[G7] row_header一覧 (header_cols={sorted(grhc_set)}):")
            for ri in range(num_rows):
                tag = " [col_header行]" if ri in gchr_set else ""
                parts = []
                for rc in sorted(grhc_set):
                    if rc < len(grid[ri]):
                        parts.append(grid[ri][rc] if grid[ri][rc] else "(空)")
                    else:
                        parts.append("(範囲外)")
                row_val = " ".join(p for p in parts if p != "(空)" and p != "(範囲外)") or "(なし)"
                logger.info(f"[G7]   R{ri}: {parts} → '{row_val}'{tag}")

            # --- 全セル出力（グローバル列で表示） ---
            def cell_global_col(c):
                pid = str(c.get("panel_id", 0) or 0)
                return gcm.get((pid, c.get("col", 0)), 9999)

            all_cells_sorted = sorted(
                cells, key=lambda x: (x.get("row", 0), cell_global_col(x))
            )
            for c in all_cells_sorted:
                r = c.get("row", 0)
                pid = str(c.get("panel_id", "?"))
                col = c.get("col", 0)
                gc = gcm.get((pid, col), col)
                txt = str(c.get("text", "")).strip()
                is_ch = r in gchr_set
                is_rh = gc in grhc_set
                role = "ColHdr" if is_ch else ("RowHdr" if is_rh else "Data")
                logger.info(f"[G7]     R{r}C{gc} (P{pid}) [{role}]: {txt}")

        elapsed = time.time() - g7_start
        logger.info(f"[G7] ヘッダー検出完了: {elapsed:.2f}s")
        return tables

    # ------------------------------------------------------------------
    # グローバル列マップ
    # ------------------------------------------------------------------

    @staticmethod
    def _build_global_col_map(
        by_panel: Dict[str, List[Dict]],
    ) -> Tuple[Dict[Tuple[str, int], int], Dict[str, List[int]], int]:
        """
        パネル順に列を連番化して (panel_id, physical_col) → global_col のマップを構築。

        Returns:
            (global_col_map, panel_unique_cols, global_col_count)
        """
        panel_unique_cols: Dict[str, List[int]] = {}
        for pid in sorted(by_panel.keys(), key=lambda x: int(x) if x.isdigit() else 0):
            panel_unique_cols[pid] = sorted(set(c.get("col", 0) for c in by_panel[pid]))

        global_col_map: Dict[Tuple[str, int], int] = {}
        idx = 0
        for pid in sorted(by_panel.keys(), key=lambda x: int(x) if x.isdigit() else 0):
            for col in panel_unique_cols[pid]:
                global_col_map[(pid, col)] = idx
                idx += 1

        return global_col_map, panel_unique_cols, idx

    @staticmethod
    def _parse_col_map(serialized: Dict[str, int]) -> Dict[Tuple[str, int], int]:
        """JSON文字列キーを (pid, col) タプルキーに戻す"""
        result = {}
        for k, v in serialized.items():
            parts = k.rsplit("_", 1)
            if len(parts) == 2:
                result[(parts[0], int(parts[1]))] = v
        return result

    # ------------------------------------------------------------------
    # ヘッダー検出
    # ------------------------------------------------------------------

    def _detect_headers(self, cells: List[Dict], ref_id: str) -> Dict:
        """1テーブルのセルからヘッダー位置を検出"""

        by_panel: Dict[str, List[Dict]] = {}
        for c in cells:
            pid = str(c.get("panel_id", 0) or 0)
            by_panel.setdefault(pid, []).append(c)

        global_col_map, panel_unique_cols, global_col_count = self._build_global_col_map(by_panel)

        grid_text = self._build_grid_view(by_panel)

        if not self.llm:
            logger.warning(f"[G7] LLMなし → デフォルト(row=0, col=0)")
            result = self._default_map(by_panel)
            return self._globalize_headers(result, panel_unique_cols, global_col_map, global_col_count)

        prompt = self._build_prompt(grid_text)
        logger.debug(f"[G7] プロンプト: {len(prompt)}文字")

        try:
            response = self.llm.call_model(
                tier="default",
                prompt=prompt,
                model_name=self.model,
            )
        except Exception as e:
            logger.error(f"[G7] LLM例外: {e}")
            result = self._default_map(by_panel)
            return self._globalize_headers(result, panel_unique_cols, global_col_map, global_col_count)

        if not response.get("success"):
            logger.error(f"[G7] LLM失敗: {response.get('error')}")
            result = self._default_map(by_panel)
            return self._globalize_headers(result, panel_unique_cols, global_col_map, global_col_count)

        content = response.get("content", "")
        logger.debug(f"[G7] AI応答:\n{content[:600]}")

        parsed = self._parse_response(content, by_panel)
        return self._globalize_headers(parsed, panel_unique_cols, global_col_map, global_col_count)

    def _globalize_headers(
        self,
        header_map: Dict,
        panel_unique_cols: Dict[str, List[int]],
        global_col_map: Dict[Tuple[str, int], int],
        global_col_count: int,
    ) -> Dict:
        """
        パネルローカル座標をグローバル座標に変換。

        - col_header_rows: 行番号は既に物理座標 → そのまま union
        - row_header_cols: ローカル列 → (pid, physical_col) → global_col_map で変換
        """
        panels = header_map.get("panels", {})
        global_col_header_rows = set()
        global_row_header_cols = set()

        for pk, cfg in panels.items():
            pid = pk.lstrip("P")
            unique_cols = panel_unique_cols.get(pid, [])

            for r in cfg.get("col_header_rows", []):
                global_col_header_rows.add(r)

            global_rh = []
            for local_c in cfg.get("row_header_cols", []):
                if local_c < len(unique_cols):
                    physical_col = unique_cols[local_c]
                    global_c = global_col_map.get((pid, physical_col))
                    if global_c is not None:
                        global_rh.append(global_c)
                        global_row_header_cols.add(global_c)
                    else:
                        logger.warning(
                            f"[G7] {pk} col {local_c}→physical {physical_col} がマップにない"
                        )
                else:
                    logger.warning(
                        f"[G7] {pk} row_header_cols={local_c} が範囲外 "
                        f"(unique_cols={unique_cols})"
                    )
            cfg["row_header_cols_global"] = global_rh

        # global_col_map を JSON シリアライズ可能な形式に変換
        serializable_map = {
            f"{pid}_{col}": gc for (pid, col), gc in global_col_map.items()
        }

        header_map["global_col_header_rows"] = sorted(global_col_header_rows)
        header_map["global_row_header_cols"] = sorted(global_row_header_cols)
        header_map["global_col_map"] = serializable_map
        header_map["global_col_count"] = global_col_count
        return header_map

    # ------------------------------------------------------------------
    # グローバルグリッドのヘッダー補完
    # ------------------------------------------------------------------

    def _nearest_fill_grid(
        self,
        grid: List[List[str]],
        ch_rows: Set[int],
        rh_cols: Set[int],
        gcm: Dict[Tuple[str, int], int],
        total_cols: int,
    ) -> None:
        """グローバルグリッドのヘッダー空欄をパネル内制約で補完"""
        num_rows = len(grid)

        # global_col → panel_id の逆引きマップ構築
        col_to_panel: Dict[int, str] = {}
        for (pid, _), gc in gcm.items():
            col_to_panel[gc] = pid

        # 列ヘッダー行: 同パネル内で最も近い非空セルの値をコピー
        for hr in ch_rows:
            if hr >= num_rows:
                continue
            row_data = grid[hr]
            for ci in range(total_cols):
                if ci in rh_cols:
                    continue
                if row_data[ci]:
                    continue
                my_panel = col_to_panel.get(ci)
                nearest_val = ""
                nearest_dist = total_cols + 1
                for ki in range(total_cols):
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
                        f"[G7] nearest fill col_header: "
                        f"R{hr}C{ci} ← '{nearest_val}' (dist={nearest_dist})"
                    )

        # 行ヘッダー列: 上下で最も近い非空セルの値をコピー
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
                        f"[G7] nearest fill row_header: "
                        f"R{ri}C{rc} ← '{nearest_val}' (dist={nearest_dist})"
                    )

    # ------------------------------------------------------------------
    # LLM向けグリッドビュー（パネルローカル座標のまま）
    # ------------------------------------------------------------------

    def _build_grid_view(self, by_panel: Dict[str, List[Dict]]) -> str:
        """パネルごとにグリッド形式のテキストを構築（列はローカル連番に圧縮）"""
        sections = []

        for pid in sorted(by_panel.keys(), key=lambda x: int(x) if x.isdigit() else 0):
            panel_cells = by_panel[pid]

            unique_cols = sorted(set(c.get("col", 0) for c in panel_cells))
            col_map = {gc: lc for lc, gc in enumerate(unique_cols)}

            max_row = max((c.get("row", 0) for c in panel_cells), default=0)
            num_cols = len(unique_cols)

            grid = [["" for _ in range(num_cols)] for _ in range(max_row + 1)]
            for c in panel_cells:
                r = c.get("row", 0)
                local_col = col_map[c.get("col", 0)]
                text = str(c.get("text", "")).replace("\n", " ").strip()
                if r <= max_row:
                    grid[r][local_col] = text

            lines = [f"=== Panel P{pid} ({max_row + 1} rows x {num_cols} cols) ==="]
            for r_idx, row in enumerate(grid):
                cells_str = " | ".join(
                    f"C{c_idx}:{cell!r}" for c_idx, cell in enumerate(row)
                )
                lines.append(f"  R{r_idx}: {cells_str}")

            sections.append("\n".join(lines))

        return "\n\n".join(sections)

    def _build_prompt(self, grid_text: str) -> str:
        """構造解析プロンプト。データ型の境界を重視。"""
        return f"""あなたは表の構造を鑑定する専門家です。
以下のグリッドデータを見て、各パネルの「ヘッダー行」と「ヘッダー列」を特定してください。

## 判定ガイドライン

1. **行ヘッダー専用パネルの識別（最重要）**:
   - 1列だけのパネルで「偏差値」「70」「69」「68」のように行ラベルが縦に並んでいる場合、それは**行ヘッダー専用パネル**です。
   - そのパネルは `row_header_cols=[0]`, `col_header_rows=[]` としてください。
   - **このような行ヘッダー専用パネルが存在する場合、隣接するデータパネルは `row_header_cols=[]`（空配列）にしてください。** データパネル内のテキスト列（学校名など）はデータであり、行ヘッダーではありません。行のラベルは行ヘッダー専用パネルが担います。

2. **列ヘッダー行の識別**:
   - 「2/3」「第6回」「1月」「2/4~」のような日付・回次表記が最上部の行にある場合、それは列ヘッダー(col_header_rows)です。
   - 行ヘッダー専用パネルには col_header_rows は不要（空配列）です。

3. **行ヘッダー列の判断（行ヘッダー専用パネルが無い場合のみ）**:
   - 行ヘッダー専用パネルが無い場合に限り、各パネルの最左列(C0)を row_header_cols として検討してください。
   - 複数列を row_header_cols にするのは、左→右に階層分類がある場合のみです。

4. **折り返し表のパターン**:
   - 典型的な折り返し表は `[行ヘッダーパネル] [データパネル] [データパネル] [行ヘッダーパネル]` の構成です。
   - データパネル同士は同じ列構成（テキスト列+数値列の繰り返し等）を持ち、同じ col_header_rows パターンを適用してください。

## グリッドデータ
{grid_text}

## 出力形式（JSONのみ、説明不要）
```json
{{
  "panels": {{
    "P0": {{"col_header_rows": [行番号, ...], "row_header_cols": [列番号, ...]}},
    "P1": {{"col_header_rows": [行番号, ...], "row_header_cols": [列番号, ...]}}
  }}
}}
```

注意:
- 行番号・列番号は R0, C0 の数字部分（0始まり）
- ヘッダーが複数行/列にまたがる場合は全て列挙
- ヘッダーが見つからない場合は空配列 []
- パネルごとに独立した行(R)・列(C)番号で回答してください"""

    def _parse_response(self, content: str, by_panel: Dict) -> Dict:
        """AI応答をパースして header_map を返す"""
        json_str = None
        for pattern in [r"```json\s*(.*?)```", r"```\s*(.*?)```"]:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                json_str = match.group(1).strip()
                break

        if not json_str:
            match = re.search(r"\{[\s\S]*\}", content, re.DOTALL)
            json_str = match.group(0).strip() if match else content.strip()

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError:
            try:
                parsed = json_repair.loads(json_str)
            except Exception as e:
                logger.error(f"[G7] JSON解析失敗: {e}")
                return self._default_map(by_panel)

        if not isinstance(parsed, dict):
            return self._default_map(by_panel)

        panels = parsed.get("panels", {})
        if not panels and "col_header_rows" in parsed:
            panels = {"P0": parsed}

        result = {}
        for pk, pv in panels.items():
            if not isinstance(pv, dict):
                continue
            result[str(pk)] = {
                "col_header_rows": [int(r) for r in pv.get("col_header_rows", [])],
                "row_header_cols": [int(c) for c in pv.get("row_header_cols", [])],
            }

        for pid in by_panel:
            pk = f"P{pid}"
            if pk not in result:
                result[pk] = {"col_header_rows": [0], "row_header_cols": [0]}

        return {"panels": result}

    def _default_map(self, by_panel: Dict) -> Dict:
        """LLMなし/失敗時のデフォルト: 各パネル row=0, col=0"""
        panels = {}
        for pid in by_panel:
            panels[f"P{pid}"] = {"col_header_rows": [0], "row_header_cols": [0]}
        return {"panels": panels}
