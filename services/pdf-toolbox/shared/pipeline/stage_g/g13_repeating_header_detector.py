"""
G-13: Repeating Header Detector（繰り返しヘッダー検出）

責務: 「繰り返しがあるか／どこで割れるか」を返すだけ。分割（切り出し）処理は一切しない。

2系統で検出:
A) 並列ヘッダー（周期数列）
   - 横分割 (row groups): col0〜2 のいずれかで、行方向に周期 p>=3 が 2〜4回繰り返す
   - 縦分割 (col groups): row0〜2 のいずれかで、列方向に周期 p>=3 が 2〜4回繰り返す
B) 分割位置ヘッダー（署名一致）
   - 横分割: row0〜2 のいずれかの署名（col2〜最終列）が row3+ で再出現
   - 縦分割: col0〜2 のいずれかの署名（row2〜最終行）が col4+ で再出現

優先順位:
1. 行方向（横分割）を先に検出。成立したら列方向はスキップ。
2. 行方向不成立なら列方向を検出。

出力:
{
    "row_split": bool,
    "row_blocks": [{"start": int, "end": int}, ...] | None,
    "row_common_top": list[int] | None,     # 各ブロック先頭に複製する行インデックス群
    "row_common_bottom": list[int] | None,  # 各ブロック末尾に複製する行インデックス群（tail=1 の場合）
    "col_split": bool,
    "col_blocks": [{"start": int, "end": int}, ...] | None,
    "col_common_left": list[int] | None,    # 各ブロック左端に複製する列インデックス群
    "col_common_right": list[int] | None,   # 各ブロック右端に複製する列インデックス群（tail=1 の場合）
}
"""

from typing import Dict, Any, List, Optional, Tuple
from loguru import logger


class G13RepeatingHeaderDetector:
    """G-13: Repeating Header Detector（繰り返しヘッダー検出専用）"""

    _EMPTY: Dict[str, Any] = {
        "row_split": False, "row_blocks": None, "row_common_top": None, "row_common_bottom": None,
        "col_split": False, "col_blocks": None, "col_common_left": None, "col_common_right": None,
    }

    def __init__(self, next_stage=None):
        """
        Args:
            next_stage: 次のステージ（G-14）のインスタンス
        """
        self.next_stage = next_stage

    def process(self, g11_result: Dict[str, Any], year_context=None) -> Dict[str, Any]:
        """
        G-11の結果を受け取り、各表の繰り返しヘッダーを検出してG-14に渡す。

        Args:
            g11_result: G-11の出力（structured_tables を含む）
            year_context: 年度コンテキスト（次ステージへ引き継ぎ）

        Returns:
            {
                'success': bool,
                'structured_tables': list,
                'detections': {table_id: detection_dict},
                'g14_result': ...  # next_stage がある場合
            }
        """
        logger.info("[G-13] ========== 繰り返しヘッダー検出開始 ==========")
        structured_tables = g11_result.get('structured_tables', [])
        logger.info(f"[G-13] 入力表数: {len(structured_tables)}個")

        # 入力表の詳細ログ
        if structured_tables:
            logger.info("")
            logger.info("[G-13] 入力表の詳細:")
            for idx, table in enumerate(structured_tables, 1):
                table_id = table.get('table_id', 'Unknown')
                headers = table.get('headers', [])
                rows = table.get('rows', [])
                logger.info(f"  Table {idx} ({table_id}):")
                logger.info(f"    ├─ headers: {len(headers)}列")
                logger.info(f"    │   {headers}")
                logger.info(f"    ├─ rows: {len(rows)}行")
                if rows:
                    logger.info(f"    └─ 全行:")
                    for row_idx, row in enumerate(rows, 1):
                        logger.info(f"        Row {row_idx}: {row}")
                else:
                    logger.info(f"    └─ データ行なし")
            logger.info("")

        detections = []  # structured_tables と同順のリスト（table_id 重複に対応）
        for idx, table in enumerate(structured_tables):
            table_id = table.get('table_id', '')

            headers = table.get('headers', [])
            rows = table.get('rows', [])
            full = []
            if headers:
                full.append(headers)
            full.extend(rows)

            detection = self.detect(full)
            detections.append(detection)

            logger.info(f"[G-13] 表[{idx}] {table_id} 検出結果:")
            logger.info(f"  ├─ row_split: {detection['row_split']}")
            if detection['row_split']:
                logger.info(f"  │   ├─ row_blocks: {detection['row_blocks']}")
                logger.info(f"  │   ├─ row_common_top: {detection['row_common_top']}")
                logger.info(f"  │   └─ row_common_bottom: {detection['row_common_bottom']}")
            logger.info(f"  └─ col_split: {detection['col_split']}")
            if detection['col_split']:
                logger.info(f"      ├─ col_blocks: {detection['col_blocks']}")
                logger.info(f"      ├─ col_common_left: {detection['col_common_left']}")
                logger.info(f"      └─ col_common_right: {detection['col_common_right']}")

        logger.info("")
        logger.info("[G-13] ========== 繰り返しヘッダー検出完了 ==========")

        result = {
            'success': True,
            'structured_tables': structured_tables,
            'detections': detections,
        }

        if self.next_stage:
            logger.info("[G-13] → 次のステージ（G-14）を呼び出します")
            g14_result = self.next_stage.process(result, year_context=year_context)
            result['g14_result'] = g14_result

        return result

    def detect(self, table: List[List]) -> Dict[str, Any]:
        """
        繰り返しヘッダーを検出する。

        Returns:
            {
                "row_split": bool,
                "row_blocks": [{"start": int, "end": int}, ...] | None,
                "row_common_top": list[int] | None,
                "row_common_bottom": list[int] | None,
                "col_split": bool,
                "col_blocks": [{"start": int, "end": int}, ...] | None,
                "col_common_left": list[int] | None,
                "col_common_right": list[int] | None,
            }
        """
        if not table or len(table) < 3:
            return dict(self._EMPTY)

        # Step 1: 行方向（横分割）を先に検出
        row_result = self._detect_row_split(table)
        if row_result is not None:
            logger.info(
                f"[G-13] 行方向分割検出: {len(row_result['blocks'])}ブロック "
                f"(source={row_result['source']}, common_top={row_result['common_top']}, "
                f"common_bottom={row_result.get('common_bottom', [])})"
            )
            return {
                "row_split": True,
                "row_blocks": row_result["blocks"],
                "row_common_top": row_result["common_top"],
                "row_common_bottom": row_result.get("common_bottom", []),
                "col_split": False,
                "col_blocks": None,
                "col_common_left": None,
                "col_common_right": None,
            }

        # Step 2: 列方向（縦分割）を検出
        col_result = self._detect_col_split(table)
        if col_result is not None:
            logger.info(
                f"[G-13] 列方向分割検出: {len(col_result['blocks'])}ブロック "
                f"(source={col_result['source']}, common_left={col_result['common_left']}, "
                f"common_right={col_result.get('common_right', [])})"
            )
            return {
                "row_split": False,
                "row_blocks": None,
                "row_common_top": None,
                "row_common_bottom": None,
                "col_split": True,
                "col_blocks": col_result["blocks"],
                "col_common_left": col_result["common_left"],
                "col_common_right": col_result.get("common_right", []),
            }

        logger.info("[G-13] 繰り返しパターンなし → 分割不要")
        return dict(self._EMPTY)

    # =========================================================================
    # 行方向（横分割）検出
    # =========================================================================

    def _detect_row_split(self, table: List[List]) -> Optional[Dict]:
        """
        行方向の分割を検出する。

        Returns: {"blocks": [...], "common_top": [...], "source": str} or None
        """
        num_rows = len(table)
        num_cols = max((len(row) for row in table), default=0)

        # A) 並列ヘッダー: col0〜2 のいずれかで行方向の周期数列を探す
        best: Optional[Dict] = None
        for c in range(min(3, num_cols)):
            col_vals = [
                str(table[r][c]).strip()
                if c < len(table[r]) and table[r][c] is not None and str(table[r][c]).strip()
                else ''
                for r in range(num_rows)
            ]
            res = self._find_period(col_vals)
            if res is None:
                continue
            start, p, k = res["start"], res["period_len"], res["repeat_count"]
            gap = res.get("gap", 0)
            match_starts = res.get("match_starts", [start + i * p for i in range(k)])
            logger.info(
                f"[G-13] 並列ヘッダー候補（行方向）col{c}: "
                f"seq={col_vals}, base={col_vals[start:start + p]}, "
                f"start={start}, period={p}, repeat={k}, gap={gap}, "
                f"match_starts={match_starts}"
            )
            score = (k, p)
            if best is None or score > best["_score"]:
                if gap > 0 and start >= gap:
                    # ギャップ行はセクションヘッダー → 各ブロックに含める
                    common_top = list(range(start - gap))
                    blocks = [
                        {"start": ms - gap, "end": ms + p - 1}
                        for ms in match_starts
                    ]
                    last_end = blocks[-1]["end"]
                    common_bottom = [last_end + 1] if last_end + 1 < num_rows else []
                else:
                    common_top = list(range(start))
                    blocks = [
                        {"start": ms, "end": ms + p - 1}
                        for ms in match_starts
                    ]
                    tail = len(col_vals) - (match_starts[-1] + p)
                    common_bottom = [match_starts[-1] + p] if tail == 1 else []
                best = {
                    "_score": score,
                    "blocks": blocks,
                    "common_top": common_top,
                    "common_bottom": common_bottom,
                    "source": f"parallel_header_col{c}_p{p}_k{k}",
                }

        if best:
            best.pop("_score")
            logger.info(
                f"[G-13] 並列ヘッダー（行方向）採用: {best['source']}, "
                f"blocks={best['blocks']}, common_top={best['common_top']}, "
                f"common_bottom={best['common_bottom']}"
            )
            return best

        # B) 分割位置ヘッダー: row0〜2 の署名（col2〜）が row3+ で再出現
        if num_rows < 4 or num_cols <= 2:
            return None

        for r in range(min(3, num_rows)):
            base_sig = tuple(
                str(table[r][c]).strip()
                if c < len(table[r]) and table[r][c] is not None else ''
                for c in range(2, num_cols)
            )
            if all(v == '' for v in base_sig):
                continue
            for k in range(3, num_rows):
                k_sig = tuple(
                    str(table[k][c]).strip()
                    if c < len(table[k]) and table[k][c] is not None else ''
                    for c in range(2, num_cols)
                )
                if k_sig == base_sig:
                    logger.info(
                        f"[G-13] 分割位置ヘッダー（行方向）発見: "
                        f"row{r} の署名が row{k} に一致"
                    )
                    logger.info(f"  row{r} sig (col2〜): {list(base_sig)}")
                    logger.info(f"  row{k} sig (col2〜): {list(k_sig)}")
                    return {
                        "blocks": [
                            {"start": 0, "end": k - 1},
                            {"start": k, "end": num_rows - 1},
                        ],
                        "common_top": [],
                        "common_bottom": [],
                        "source": f"split_position_row{r}_match_at_row{k}",
                    }

        return None

    # =========================================================================
    # 列方向（縦分割）検出
    # =========================================================================

    def _detect_col_split(self, table: List[List]) -> Optional[Dict]:
        """
        列方向の分割を検出する。

        Returns: {"blocks": [...], "common_left": [...], "source": str} or None
        """
        num_rows = len(table)
        num_cols = max((len(row) for row in table), default=0)

        # A) 並列ヘッダー: row0〜2 のいずれかで列方向の周期数列を探す（col1以降）
        best: Optional[Dict] = None
        for r in range(min(3, num_rows)):
            row_vals = [
                str(table[r][c]).strip()
                if c < len(table[r]) and table[r][c] is not None and str(table[r][c]).strip()
                else ''
                for c in range(1, num_cols)
            ]
            res = self._find_period(row_vals)
            if res is None:
                continue
            start_offset, p, k = res["start"], res["period_len"], res["repeat_count"]
            start_col = 1 + start_offset  # col0 スキップ分を補正
            logger.info(
                f"[G-13] 並列ヘッダー候補（列方向）row{r}: "
                f"seq={row_vals}, base={row_vals[start_offset:start_offset + p]}, "
                f"start_col={start_col}, period={p}, repeat={k}"
            )
            score = (k, p)
            if best is None or score > best["_score"]:
                match_starts_rel = res.get("match_starts", [start_offset + i * p for i in range(k)])
                actual_match_starts = [1 + ms for ms in match_starts_rel]
                common_left = list(range(start_col))  # 0 〜 start_col-1
                blocks = [
                    {"start": start_col + i * p, "end": start_col + (i + 1) * p - 1}
                    for i in range(k)
                ]
                tail = len(row_vals) - (match_starts_rel[-1] + p)
                common_right = [actual_match_starts[-1] + p] if tail == 1 else []
                best = {
                    "_score": score,
                    "blocks": blocks,
                    "common_left": common_left,
                    "common_right": common_right,
                    "source": f"parallel_header_row{r}_p{p}_k{k}",
                }

        if best:
            best.pop("_score")
            logger.info(
                f"[G-13] 並列ヘッダー（列方向）採用: {best['source']}, "
                f"blocks={best['blocks']}, common_left={best['common_left']}, "
                f"common_right={best['common_right']}"
            )
            return best

        # B) 分割位置ヘッダー: col0〜2 の署名（row2〜）が col4+ で再出現
        if num_rows <= 2 or num_cols < 5:
            return None

        for c in range(min(3, num_cols)):
            base_sig = tuple(
                str(table[r][c]).strip()
                if c < len(table[r]) and table[r][c] is not None else ''
                for r in range(2, num_rows)
            )
            if all(v == '' for v in base_sig):
                continue
            for j in range(4, num_cols):
                j_sig = tuple(
                    str(table[r][j]).strip()
                    if j < len(table[r]) and table[r][j] is not None else ''
                    for r in range(2, num_rows)
                )
                if j_sig == base_sig:
                    logger.info(
                        f"[G-13] 分割位置ヘッダー（列方向）発見: "
                        f"col{c} の署名が col{j} に一致"
                    )
                    logger.info(f"  col{c} sig (row2〜): {list(base_sig)}")
                    logger.info(f"  col{j} sig (row2〜): {list(j_sig)}")
                    return {
                        "blocks": [
                            {"start": 0, "end": j - 1},
                            {"start": j, "end": num_cols - 1},
                        ],
                        "common_left": [],
                        "common_right": [],
                        "source": f"split_position_col{c}_match_at_col{j}",
                    }

        return None

    # =========================================================================
    # 周期検出（共通）
    # =========================================================================

    def _find_period(
        self,
        seq: List[str],
        min_period: int = 3,
        max_repeats: int = 4,
        min_repeats: int = 2,
        allowed_gap: int = 0,
    ) -> Optional[Dict]:
        """
        シーケンスから完全一致の周期ブロックを検出する。

        採用優先順位: repeat_count 最大 → period_len 最大 → start 最小（左/上寄り）

        Args:
            seq: 検査する文字列リスト
            min_period: 最小周期長（デフォルト3）
            max_repeats: 最大繰り返し回数（デフォルト4）
            min_repeats: 採用に必要な最小繰り返し回数（デフォルト2）
            allowed_gap: 繰り返しブロック間の許容隙間（デフォルト1）

        Returns:
            {"start": int, "period_len": int, "repeat_count": int, "gap": int} or None
        """
        n = len(seq)
        best: Optional[Dict] = None  # {"_score": ..., "start": ..., ...}

        for start in range(n):
            for p in range(min_period, n - start + 1):
                if start + p * min_repeats > n:
                    break

                base = tuple(seq[start:start + p])
                if all(v == '' for v in base):
                    continue

                match_starts = [start]  # 各一致の実際の開始位置を追跡
                repeats = 1
                pos = start + p
                last_gap = 0

                while repeats < max_repeats:
                    matched = False
                    for gap in range(allowed_gap + 1):
                        s2 = pos + gap
                        if s2 + p <= n and tuple(seq[s2:s2 + p]) == base:
                            last_gap = gap
                            pos = s2 + p
                            repeats += 1
                            match_starts.append(s2)  # 実際の開始位置を記録
                            matched = True
                            break
                    if not matched:
                        break

                if repeats < min_repeats:
                    continue

                # 繰り返し末尾から配列末尾までの余りが2以上なら偶然一致として弾く
                # 例: EEABCDABCDABCDABCD → 余り0 ✓
                #     EEABCDABCDABCDABCDe → 余り1 ✓（最終1行は許容）
                #     EEABCABCDEFGHij → 余り多数 ✗
                tail = n - (start + repeats * p)
                if tail > 1:
                    continue

                # 優先: (repeat_count desc, period_len desc, start asc)
                score = (repeats, p, -start)
                if best is None or score > best["_score"]:
                    best = {
                        "_score": score,
                        "start": start,
                        "period_len": p,
                        "repeat_count": repeats,
                        "gap": last_gap,
                        "match_starts": match_starts,  # 実際の開始位置リスト
                    }

        if best is None:
            return None
        return {k: v for k, v in best.items() if k != "_score"}
