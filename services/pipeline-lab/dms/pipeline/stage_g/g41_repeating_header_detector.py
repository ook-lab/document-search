"""
F55: Repeating Header Detector（繰り返しヘッダー検出）

責務: 「繰り返しがあるか／どこで割れるか」を返すだけ。分割（切り出し）処理は一切しない。

**理解してから切る**: 分割方針の正本は **G26** の ``layout_split`` → ``g41_detection`` のみ。
本ステージは LLM しない。geometry・周期パターン・学級ラベル一致などでの分割上書きはしない。
G45 で D 罫線に基づく行物理分割済みならスキップ。G44 は G26 由来の detection を機械適用するだけ。

**禁止**: 見出し語・表種語（収入・支出・部・科目・5A/5B など）だけで col_split / row_split しない。

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

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import re
from loguru import logger

from dms.pipeline.stage_g.g41_ai_layout_splitter import (
    G41_LAYOUT_AI_CONTRACT,
    G41LayoutAIRequiredError,
    _promote_leading_label_column_to_common_left,
    suggest_ai_table_split,
)
from dms.pipeline.stage_g.g45_d_line_split import G45_D_LINE_SPLIT_CONTRACT
from dms.pipeline.stage_g.g26_semantic_estimator import build_g41_detection_from_entry


def _g26_layout_context(g24_result: Dict[str, Any], table_id: str) -> Optional[str]:
    """G26 表理解の要約（layout_split）のみ。分割ブロック座標の転用はしない。"""
    sem_by = (g24_result.get("semantic_inference") or {}).get("by_sub_table") or {}
    entry = sem_by.get(f"{table_id}::")
    if not isinstance(entry, dict):
        return None
    ls = entry.get("layout_split")
    if not isinstance(ls, dict):
        return None
    intent = str(ls.get("whole_table_intent") or entry.get("description") or "").strip()
    if not intent:
        return None
    return f"G26 表理解: {intent}"


class G41RepeatingHeaderDetector:
    """F55: Repeating Header Detector（繰り返しヘッダー検出専用）"""

    # 見出しセル内の「4月」「４ 月」等（横並び複数月カレンダー検出用）
    _MONTH_IN_CELL_RE = re.compile(r'(?:^|[^\d])(\d{1,2})\s*月')

    _EMPTY: Dict[str, Any] = {
        "row_split": False, "row_blocks": None, "row_common_top": None, "row_common_bottom": None,
        "col_split": False, "col_blocks": None, "col_common_left": None, "col_common_right": None,
    }

    def __init__(self, document_id=None, next_stage=None):
        """
        Args:
            document_id: コストログ用セッション ID
            next_stage: 次のステージ（F56）のインスタンス
        """
        self.document_id = document_id
        self.next_stage = next_stage

    def process(self, g24_result: Dict[str, Any], year_context=None, table_log_dir: Optional[Path] = None) -> Dict[str, Any]:
        """
        G24 の結果を受け取り、各表の繰り返しヘッダーを検出してF56に渡す。

        Args:
            g24_result: G24 の出力（structured_tables を含む）
            year_context: 年度コンテキスト（次ステージへ引き継ぎ）
            table_log_dir: E-56/E-57 専用ログの親ディレクトリ（任意）

        Returns:
            {
                'success': bool,
                'structured_tables': list,
                'detections': {table_id: detection_dict},
                'e14_result': ...  # next_stage がある場合
            }
        """
        logger.info("[G41] ========== 繰り返しヘッダー検出開始 ==========")
        structured_tables = g24_result.get('structured_tables', [])
        logger.info(f"[G41] 入力表数: {len(structured_tables)}個")

        # 入力表の詳細ログ
        if structured_tables:
            logger.info("")
            logger.info("[G41] 入力表の詳細:")
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

        sem_by = (
            (g24_result.get("semantic_inference") or {}).get("by_sub_table") or {}
        )
        detections = []
        for idx, table in enumerate(structured_tables):
            table_id = table.get('table_id', '')
            meta = table.get("metadata") if isinstance(table.get("metadata"), dict) else {}
            if meta.get("split_source") == G45_D_LINE_SPLIT_CONTRACT:
                detection = dict(self._EMPTY)
                detections.append(detection)
                logger.info(f"[G41] 表[{idx}] {table_id}: G45 物理分割済み → スキップ")
                continue

            headers = table.get('headers', [])
            rows = table.get('rows', [])
            full = []
            if headers:
                full.append(headers)
            full.extend(rows)

            sem_key = f"{table_id}::"
            sem_entry = sem_by.get(sem_key)
            if not isinstance(sem_entry, dict):
                raise RuntimeError(
                    f"[G41] G26 semantic_inference missing for {sem_key!r}"
                )
            detection = build_g41_detection_from_entry(sem_entry, full)
            detection = _promote_leading_label_column_to_common_left(full, detection)
            logger.info("[G41] G26 分割方針を採用 → G44 で機械分割")
            detections.append(detection)

            logger.info(f"[G41] 表[{idx}] {table_id} 検出結果:")
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
        logger.info("[G41] ========== 繰り返しヘッダー検出完了 ==========")

        result = {
            'success': True,
            'structured_tables': structured_tables,
            'detections': detections,
            'stage_d_line_digest': g24_result.get('stage_d_line_digest'),
            'd_line_split_contract': g24_result.get('d_line_split_contract'),
        }

        if self.next_stage:
            logger.info("[G41] → 次のステージ（F56）を呼び出します")
            e14_result = self.next_stage.process(result, year_context=year_context, table_log_dir=table_log_dir)
            result['e14_result'] = e14_result

        return result

    def detect(
        self,
        table: List[List],
        *,
        document_id: Optional[str] = None,
        layout_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """レイアウト AI（必須）で分割方針を決め、G44 が機械分割する。"""
        if not table or len(table) < 3:
            return dict(self._EMPTY)

        sid = document_id if document_id is not None else self.document_id
        ai_det = suggest_ai_table_split(
            table, document_id=sid, require=True, layout_context=layout_context
        )
        contract = ai_det.get("layout_ai_contract")
        if contract not in (G41_LAYOUT_AI_CONTRACT, "f55_layout_ai_v1"):
            raise G41LayoutAIRequiredError("g41_layout_ai_contract_missing")

        from dms.pipeline.stage_g.g41_ai_layout_splitter import (
            _promote_leading_label_column_to_common_left,
        )

        ai_det = _promote_leading_label_column_to_common_left(table, ai_det)
        logger.info("[G41] レイアウト AI 採用 → G44 で機械分割")
        return ai_det

    def _header_rows_suggest_multi_month_wide_layout(self, table: List[List]) -> bool:
        """
        先頭数行のいずれかに、別々のセルで「◯月」が3種類以上含まれる → 横並びの複数月表とみなす。
        この場合、col0〜2 の縦方向周期による行分割は誤爆のため棄却する材料に使う。
        列方向は周期ベースではなく、月セル位置からブロック境界を決める別経路を使う。
        """
        for r in range(min(5, len(table))):
            row = table[r]
            if not isinstance(row, (list, tuple)):
                continue
            months: set[str] = set()
            for cell in row:
                if cell is None:
                    continue
                s = str(cell).strip()
                for m in self._MONTH_IN_CELL_RE.finditer(s):
                    months.add(m.group(1))
            if len(months) >= 3:
                return True
        return False

    @staticmethod
    def _row_is_only_auto_column_names(row: List[Any]) -> bool:
        nonempty = [c for c in row if c is not None and str(c).strip()]
        if not nonempty:
            return False
        return all(re.match(r"^(Col|列)\d+$", str(c).strip()) for c in nonempty)

    def _detect_wide_multi_month_column_blocks(self, table: List[List]) -> Optional[Dict[str, Any]]:
        """
        横並び複数月グリッドで、どこかの行に「◯月」が列位置として並ぶとき、
        その列をアンカーに左から連続する列ブロックへ分割する（G44 の col_split 用）。

        カレンダー専用ではなく「月ラベルが横に並ぶ行事表」の入口として使う。
        """
        num_cols = max((len(r) for r in table), default=0)
        num_rows = len(table)
        if num_rows < 2 or num_cols < 6:
            return None

        best_anchors: Optional[List[Tuple[int, str]]] = None

        for r in range(min(8, num_rows)):
            row = table[r]
            if not isinstance(row, (list, tuple)):
                continue
            if self._row_is_only_auto_column_names(row):
                continue
            anchors: List[Tuple[int, str]] = []
            for c, cell in enumerate(row):
                if cell is None:
                    continue
                s = str(cell).strip()
                m = self._MONTH_IN_CELL_RE.search(s)
                if m:
                    anchors.append((c, m.group(1)))
            if len(anchors) < 3:
                continue
            if len({x[1] for x in anchors}) < 3:
                continue
            anchors.sort(key=lambda x: x[0])
            if any(anchors[i][0] >= anchors[i + 1][0] for i in range(len(anchors) - 1)):
                continue
            if best_anchors is None or len(anchors) > len(best_anchors):
                best_anchors = anchors

        if not best_anchors or len(best_anchors) < 3:
            return None

        anchors = sorted(best_anchors, key=lambda x: x[0])
        if len({x[1] for x in anchors}) < 3:
            return None

        common_left = list(range(anchors[0][0])) if anchors[0][0] > 0 else []

        blocks: List[Dict[str, int]] = []
        for i in range(len(anchors)):
            c0 = anchors[i][0]
            c1 = anchors[i + 1][0] - 1 if i + 1 < len(anchors) else num_cols - 1
            if c0 > c1:
                return None
            if c1 - c0 + 1 < 2:
                return None
            blocks.append({"start": c0, "end": c1})

        widths = [b["end"] - b["start"] + 1 for b in blocks]
        if max(widths) - min(widths) > 2:
            logger.info(f"[G41] 月アンカー列分割: 列幅ばらつきで棄却 widths={widths}")
            return None

        last_end = blocks[-1]["end"]
        common_right = list(range(last_end + 1, num_cols)) if last_end < num_cols - 1 else []

        return {
            "blocks": blocks,
            "common_left": common_left,
            "common_right": common_right,
            "source": "wide_multi_month_column_anchors",
        }

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
                f"[G41] 並列ヘッダー候補（行方向）col{c}: "
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
                f"[G41] 並列ヘッダー（行方向）採用: {best['source']}, "
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
                        f"[G41] 分割位置ヘッダー（行方向）発見: "
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
                f"[G41] 並列ヘッダー候補（列方向）row{r}: "
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
                f"[G41] 並列ヘッダー（列方向）採用: {best['source']}, "
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
                        f"[G41] 分割位置ヘッダー（列方向）発見: "
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

        採用優先順位: repeat_count 最大 → period_len 最大 → start 最小（左/上を優先）

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

                # 優先: (repeat_count desc, period_len desc, start が小さい方)
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
