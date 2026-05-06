"""
B-42: Multi-Column Report Processor（汎用物理構造抽出）

設計方針:
- 列の意味（順位/氏名等）や文字種（数字等）を一切仮定しない
- テキストボックス（pdfplumber word）を最小単位とし、分割・結合しない
- 表グリッド（col_edges, row_edges）を正本とし、セルへ機械的に割り当てる
- 行あたり要素数を計測し、列数との一致/不足/過剰を判定してログ出力
- 折り返し等の意味解釈は後段AIへ委譲
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
from loguru import logger
import statistics
import math


class B42MultiColumnReportProcessor:
    """B-42: Multi-Column Report Processor（汎用物理構造抽出）"""

    Y_TOLERANCE = 3.0

    TABLE_SETTINGS_LINES = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 3,
        "snap_x_tolerance": 3,
        "snap_y_tolerance": 3,
        "join_tolerance": 3,
        "join_x_tolerance": 3,
        "join_y_tolerance": 3,
        "edge_min_length": 3,
        "min_words_vertical": 3,
        "min_words_horizontal": 1,
        "intersection_tolerance": 3,
        "intersection_x_tolerance": 3,
        "intersection_y_tolerance": 3,
    }

    def process(self, file_path: Path, masked_pages=None, log_file=None) -> Dict[str, Any]:
        """
        マルチカラム帳票から物理的構造データを抽出

        Returns:
            {
                'is_structured': bool,
                'data_type': 'report_multicolumn',
                'records': [],                    # 意味推測なし。後段AIへ委譲
                'structured_tables': [...],       # グリッド付きテーブル一覧
                'table_count_per_page': {...},
                'logical_blocks': [...],
                'tags': {...},
                'all_words': [...],
                'purged_pdf_path': str,
            }
        """
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[B-42]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )

        logger.info(f"[B-42] ========== Multi-Column Report処理開始 ==========")
        logger.info(f"[B-42] 入力ファイル: {file_path.name}")

        try:
            import pdfplumber
        except ImportError:
            logger.error("[B-42] pdfplumber がインストールされていません")
            return self._error_result("pdfplumber not installed")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                logger.info(f"[B-42] PDF情報:")
                logger.info(f"[B-42]   ├─ ページ数: {len(pdf.pages)}")
                logger.info(f"[B-42]   ├─ メタデータ: {pdf.metadata}")

                all_words: List[Dict] = []
                structured_tables: List[Dict] = []
                table_count_per_page: Dict[int, int] = {}

                _masked = set(masked_pages or [])

                for page_num, page in enumerate(pdf.pages):
                    if _masked and page_num in _masked:
                        logger.debug(f"[B-42] ページ{page_num+1}: マスク → スキップ")
                        table_count_per_page[page_num] = 0
                        continue

                    logger.info(f"[B-42] ========== ページ {page_num+1}/{len(pdf.pages)} 処理中 ==========")
                    logger.info(
                        f"[B-42]   ├─ ページサイズ: {page.width:.1f} x {page.height:.1f} pt"
                    )
                    logger.info(
                        f"[B-42]   ├─ lines={len(page.lines or [])} "
                        f"rects={len(page.rects or [])} "
                        f"chars={len(page.chars or [])}"
                    )

                    # テキストボックス取得（最小単位、スペースで分割しない）
                    page_words = page.extract_words(
                        x_tolerance=2, y_tolerance=2, keep_blank_chars=True
                    )
                    logger.info(f"[B-42]   ├─ テキストボックス（words）: {len(page_words)}個")

                    # テーブル検出（グリッド確定）
                    page_tables = self._detect_tables(page, page_num)
                    logger.info(f"[B-42]   ├─ テーブル検出: {len(page_tables)}個")

                    # テーブルごとにグリッド割り当て
                    for tbl in page_tables:
                        tbl_with_grid = self._extract_grid(page_words, tbl)
                        structured_tables.append(tbl_with_grid)
                        self._log_table(tbl_with_grid)

                    table_count_per_page[page_num] = len(page_tables)

                    # purge用に全単語収集
                    for w in page_words:
                        all_words.append({
                            'page': page_num,
                            'text': w['text'],
                            'bbox': (w['x0'], w['top'], w['x1'], w['bottom'])
                        })
                    logger.info(f"[B-42]   └─ 単語（削除対象）: {len(page_words)}個")

                # サマリー
                logger.info(f"[B-42] ========== 抽出結果サマリー ==========")
                logger.info(f"[B-42] 総テーブル数: {len(structured_tables)}個")
                logger.info(
                    f"[B-42] ページ別テーブル数: "
                    f"{[(p, n) for p, n in sorted(table_count_per_page.items())]}"
                )
                logger.info(f"[B-42] 削除対象単語総数: {len(all_words)}個")

                logical_blocks = self._words_to_logical_blocks(all_words)
                logger.info(f"[B-42] logical_blocks: {len(logical_blocks)}件")
                for idx, lb in enumerate(logical_blocks):
                    logger.info(
                        f"[B-42]   block[{idx}] page={lb['page']} text={lb['text']!r}"
                    )

                logger.info(f"[B-42] ========== テキスト削除処理開始 ==========")
                purged_pdf_path = self._purge_extracted_text(
                    file_path, all_words, structured_tables
                )
                logger.info(f"[B-42] purged PDF 生成完了: {purged_pdf_path.name}")

                tags = {
                    'source': 'stage_b',
                    'processor': 'b42_multicolumn_report',
                    'page_count': len(pdf.pages),
                    'table_count': len(structured_tables),
                }

                if _sink_id is not None:
                    logger.remove(_sink_id)

                return {
                    'is_structured': True,
                    'data_type': 'report_multicolumn',
                    'records': [],  # 意味推測なし。後段AIへ委譲
                    'structured_tables': structured_tables,
                    'table_count_per_page': table_count_per_page,
                    'logical_blocks': logical_blocks,
                    'tags': tags,
                    'all_words': all_words,
                    'purged_pdf_path': str(purged_pdf_path),
                }

        except Exception as e:
            logger.error(f"[B-42] ========== 処理エラー ==========", exc_info=True)
            logger.error(f"[B-42] エラー詳細: {e}")
            if _sink_id is not None:
                logger.remove(_sink_id)
            return self._error_result(str(e))

    # ------------------------------------------------------------------
    # テーブル検出
    # ------------------------------------------------------------------

    def _detect_tables(self, page, page_num: int) -> List[Dict]:
        """
        テーブルを検出し、col_edges / row_edges を含む dict リストを返す。

        優先1: pdfplumber ネイティブ find_tables()
        優先2: 縦罫線・横罫線クラスタリング
        優先3: 文字Xヒストグラム
        """
        tables = self._try_find_tables_native(page, page_num)
        if tables:
            return tables

        logger.warning(f"[B-42]   find_tables=0 → 縦罫線クラスタリングへ")
        tables = self._find_tables_from_rules(page, page_num)
        if tables:
            return tables

        logger.warning(f"[B-42]   縦罫線クラスタリング=0 → 文字ヒストグラムへ")
        tables = self._find_tables_from_chars(page, page_num)
        if not tables:
            logger.error(f"[B-42]   テーブル検出失敗（全手法）: page={page_num}")
        return tables

    def _try_find_tables_native(self, page, page_num: int) -> List[Dict]:
        """
        pdfplumber find_tables() でテーブルを検出する。
        table.cells から col_edges / row_edges を確定する。
        """
        try:
            tables = page.find_tables(self.TABLE_SETTINGS_LINES)
        except Exception as e:
            logger.warning(f"[B-42]   find_tables失敗: {e}")
            return []

        if not tables:
            return []

        logger.info(f"[B-42]   find_tables検出: {len(tables)}個")

        result = []
        for i, table in enumerate(tables):
            try:
                bbox = tuple(float(v) for v in table.bbox)

                # table.cells から col_edges / row_edges を確定
                col_xs: set = set()
                row_ys: set = set()
                for cell in (table.cells or []):
                    cx0, ctop, cx1, cbottom = cell
                    col_xs.add(float(cx0))
                    col_xs.add(float(cx1))
                    row_ys.add(float(ctop))
                    row_ys.add(float(cbottom))

                col_edges = sorted(col_xs)
                row_edges = sorted(row_ys)

                # cells が取れない場合は bbox 両端だけ
                if len(col_edges) < 2:
                    col_edges = [bbox[0], bbox[2]]
                if len(row_edges) < 2:
                    row_edges = [bbox[1], bbox[3]]

                cols_count = len(col_edges) - 1
                rows_count = len(row_edges) - 1

                logger.info(
                    f"[B-42]   ├─ table[{i}] "
                    f"bbox=({bbox[0]:.1f},{bbox[1]:.1f},{bbox[2]:.1f},{bbox[3]:.1f}) "
                    f"rows={rows_count} cols={cols_count}"
                )

                result.append({
                    "page": page_num,
                    "table_index": i,
                    "bbox": bbox,
                    "col_edges": col_edges,
                    "row_edges": row_edges,
                    "cols_count": cols_count,
                    "rows_count": rows_count,
                    "source": "pdfplumber_lines",
                })

            except Exception as e:
                logger.warning(f"[B-42]   table[{i}] 処理エラー: {e}")
                continue

        return result

    def _find_tables_from_rules(self, page, page_num: int) -> List[Dict]:
        """
        縦罫線・横罫線をクラスタリングして col_edges / row_edges を確定する。
        """
        page_width = float(page.width)
        page_height = float(page.height)
        min_v_length = page_height * 0.10
        min_h_length = page_width * 0.10

        v_xs: List[float] = []  # 縦罫線 x 座標
        h_ys: List[float] = []  # 横罫線 y 座標

        for ln in (page.lines or []):
            lx0, ly0 = float(ln.get('x0', 0)), float(ln.get('y0', 0))
            lx1, ly1 = float(ln.get('x1', 0)), float(ln.get('y1', 0))
            if abs(lx1 - lx0) < 1.0 and abs(ly1 - ly0) >= min_v_length:
                v_xs.append((lx0 + lx1) / 2.0)
            elif abs(ly1 - ly0) < 1.0 and abs(lx1 - lx0) >= min_h_length:
                h_ys.append((ly0 + ly1) / 2.0)

        for rc in (page.rects or []):
            rx0, ry0 = float(rc.get('x0', 0)), float(rc.get('y0', 0))
            rx1, ry1 = float(rc.get('x1', 0)), float(rc.get('y1', 0))
            w, h = abs(rx1 - rx0), abs(ry1 - ry0)
            if h >= min_v_length:
                v_xs.extend([rx0, rx1])
            if w >= min_h_length:
                h_ys.extend([ry0, ry1])

        logger.info(
            f"[B-42]   縦罫線候補: {len(v_xs)}個 (min_len={min_v_length:.1f}pt) "
            f"横罫線候補: {len(h_ys)}個 (min_len={min_h_length:.1f}pt)"
        )

        col_edges = self._cluster_coords(v_xs, tolerance=2.0)
        row_edges = self._cluster_coords(h_ys, tolerance=2.0)

        if len(col_edges) < 2:
            logger.warning(f"[B-42]   縦罫線クラスタ不足（{len(col_edges)}）→ 検出不能")
            return []

        if len(row_edges) < 2:
            # 横罫線が取れない場合はテキストボックスの y 範囲で補完
            words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=True)
            if words:
                row_edges = [
                    min(float(w['top']) for w in words) - 2.0,
                    max(float(w['bottom']) for w in words) + 2.0,
                ]
                logger.info(f"[B-42]   横罫線なし → テキスト範囲で補完: y={row_edges[0]:.1f}-{row_edges[1]:.1f}")
            else:
                row_edges = [0.0, page_height]

        cols_count = len(col_edges) - 1
        rows_count = len(row_edges) - 1
        bbox = (col_edges[0], row_edges[0], col_edges[-1], row_edges[-1])

        logger.info(
            f"[B-42]   縦罫線クラスタ: {cols_count}列 横罫線クラスタ: {rows_count}行"
        )
        logger.info(f"[B-42]   col_edges: {[f'{x:.1f}' for x in col_edges]}")
        logger.info(f"[B-42]   row_edges: {[f'{y:.1f}' for y in row_edges]}")

        return [{
            "page": page_num,
            "table_index": 0,
            "bbox": bbox,
            "col_edges": col_edges,
            "row_edges": row_edges,
            "cols_count": cols_count,
            "rows_count": rows_count,
            "source": "rule_clusters",
        }]

    def _find_tables_from_chars(self, page, page_num: int) -> List[Dict]:
        """
        文字Xヒストグラムによるテーブル検出（最終フォールバック）。
        文字の[x0, x1]区間を1ptビンで塗り、空白バンドをガターとして列境界にする。
        """
        chars = page.chars or []
        if len(chars) < 30:
            logger.error(f"[B-42]   文字不足({len(chars)}) → テーブル検出不能")
            return []

        page_width = float(page.width)
        page_height = float(page.height)

        n_bins = int(page_width) + 2
        hist = [0] * n_bins
        all_x0, all_x1 = [], []

        for c in chars:
            cx0, cx1 = float(c.get('x0', 0)), float(c.get('x1', 0))
            if cx1 <= cx0:
                continue
            all_x0.append(cx0)
            all_x1.append(cx1)
            for b in range(int(cx0), min(int(math.ceil(cx1)), n_bins)):
                hist[b] += 1

        if not all_x0:
            return []

        min_x, max_x = min(all_x0), max(all_x1)
        GUTTER_TH = 5.0

        logger.info(
            f"[B-42]   文字Xヒストグラム: chars={len(chars)} "
            f"text_range=[{min_x:.1f}, {max_x:.1f}]"
        )

        gutters: List[Tuple[float, float]] = []
        in_g, gs = False, 0
        for b in range(int(min_x), min(int(math.ceil(max_x)) + 2, n_bins)):
            if hist[b] == 0:
                if not in_g:
                    in_g, gs = True, b
            else:
                if in_g:
                    if (b - gs) >= GUTTER_TH:
                        gutters.append((float(gs), float(b)))
                    in_g = False
        if in_g and (n_bins - gs) >= GUTTER_TH:
            gutters.append((float(gs), float(n_bins)))

        logger.info(f"[B-42]   ガター候補: {len(gutters)}個 (TH={GUTTER_TH}pt)")
        for i, (gx0, gx1) in enumerate(gutters):
            logger.info(f"[B-42]   ├─ ガター{i+1}: x={gx0:.1f}-{gx1:.1f} (幅={gx1-gx0:.1f}pt)")

        if not gutters:
            logger.error(f"[B-42]   ガターなし → テーブル検出不能")
            return []

        col_edges = [min_x] + [(g0 + g1) / 2 for g0, g1 in gutters] + [max_x]
        col_edges = sorted(set(col_edges))

        # 横方向はテキストボックスの y 範囲で補完
        words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=True)
        if words:
            y0 = min(float(w['top']) for w in words) - 2.0
            y1 = max(float(w['bottom']) for w in words) + 2.0
        else:
            y0, y1 = 0.0, page_height

        row_edges = [y0, y1]
        bbox = (col_edges[0], y0, col_edges[-1], y1)

        logger.info(f"[B-42]   文字ヒストグラム → {len(col_edges)-1}列")

        return [{
            "page": page_num,
            "table_index": 0,
            "bbox": bbox,
            "col_edges": col_edges,
            "row_edges": row_edges,
            "cols_count": len(col_edges) - 1,
            "rows_count": len(row_edges) - 1,
            "source": "char_histogram",
        }]

    # ------------------------------------------------------------------
    # グリッド割り当て
    # ------------------------------------------------------------------

    def _extract_grid(self, page_words: List[Dict], table_info: Dict) -> Dict:
        """
        テキストボックスをグリッドセルに機械的に割り当てる。

        各テキストボックスの中心座標 (cx, cy) を求め、
        col_edges / row_edges のどの区間に入るかで列・行を決定する。
        分割しない・結合しない。
        """
        bbox = table_info['bbox']
        col_edges = table_info['col_edges']
        row_edges = table_info['row_edges']
        cols_count = table_info['cols_count']
        rows_count = table_info['rows_count']
        page_num = table_info['page']

        # テーブル bbox 内のテキストボックスを絞り込む
        x0, y0, x1, y1 = bbox
        words_in_table = [
            w for w in page_words
            if float(w['x0']) >= x0 - 2 and float(w['x1']) <= x1 + 2
            and float(w['top']) >= y0 - 2 and float(w['bottom']) <= y1 + 2
        ]

        logger.info(
            f"[B-42]   table[{table_info['table_index']}] "
            f"テーブル内テキストボックス: {len(words_in_table)}個"
        )

        # グリッド初期化: cells[row][col] = [{text, bbox}, ...]
        cells = [[[] for _ in range(cols_count)] for _ in range(rows_count)]

        for w in words_in_table:
            cx = (float(w['x0']) + float(w['x1'])) / 2
            cy = (float(w['top']) + float(w['bottom'])) / 2
            col_i = self._find_interval(cx, col_edges)
            row_i = self._find_interval(cy, row_edges)

            if col_i is None or row_i is None:
                logger.debug(
                    f"[B-42]   範囲外テキストボックス: text={w['text']!r} "
                    f"cx={cx:.1f} cy={cy:.1f} → col={col_i} row={row_i}"
                )
                continue

            cells[row_i][col_i].append({
                'text': w['text'],
                'bbox': (
                    float(w['x0']), float(w['top']),
                    float(w['x1']), float(w['bottom'])
                ),
            })

        # 行ごとの統計
        rows = []
        for r in range(rows_count):
            row_cells = cells[r]
            elements_in_row = sum(len(cell) for cell in row_cells)
            filled_cells = sum(1 for cell in row_cells if cell)
            empty_cells = cols_count - filled_cells
            multi_cells = sum(1 for cell in row_cells if len(cell) > 1)

            rows.append({
                'row_index': r,
                'elements_in_row': elements_in_row,
                'filled_cells': filled_cells,
                'empty_cells': empty_cells,
                'multi_cells': multi_cells,
                'cells': row_cells,
            })

        return {
            **table_info,
            'rows': rows,
        }

    def _log_table(self, tbl: Dict) -> None:
        """テーブルの構造ログを出力する"""
        ti = tbl['table_index']
        bbox = tbl['bbox']
        logger.info(
            f"[B-42]   table[{ti}] "
            f"bbox=({bbox[0]:.1f},{bbox[1]:.1f},{bbox[2]:.1f},{bbox[3]:.1f}) "
            f"rows={tbl['rows_count']} cols={tbl['cols_count']} "
            f"source={tbl['source']}"
        )
        logger.info(f"[B-42]   col_edges: {[f'{x:.1f}' for x in tbl['col_edges']]}")
        logger.info(f"[B-42]   row_edges: {[f'{y:.1f}' for y in tbl['row_edges']]}")

        cols_count = tbl['cols_count']
        for row in tbl['rows']:
            ri = row['row_index']
            e = row['elements_in_row']
            f = row['filled_cells']
            em = row['empty_cells']
            m = row['multi_cells']

            if em == 0 and m == 0:
                status = "列通り"
            elif em > 0:
                empty_idxs = [c for c, cell in enumerate(row['cells']) if not cell]
                status = f"空欄あり col={empty_idxs}"
            else:
                multi_idxs = [c for c, cell in enumerate(row['cells']) if len(cell) > 1]
                status = f"複数要素 col={multi_idxs}"

            logger.info(
                f"[B-42]   │ row[{ri:3d}]: "
                f"elements={e} filled={f} empty={em} multi={m} → {status}"
            )
            for ci, cell in enumerate(row['cells']):
                for tb in cell:
                    b = tb['bbox']
                    logger.info(
                        f"[B-42]   │   col[{ci}] text={tb['text']!r} "
                        f"bbox=({b[0]:.1f},{b[1]:.1f},{b[2]:.1f},{b[3]:.1f})"
                    )

    # ------------------------------------------------------------------
    # ユーティリティ
    # ------------------------------------------------------------------

    @staticmethod
    def _find_interval(val: float, edges: List[float]) -> Optional[int]:
        """val がどの区間 [edges[i], edges[i+1]) に入るか返す。なければ None。"""
        for i in range(len(edges) - 1):
            if edges[i] - 0.5 <= val < edges[i + 1] + 0.5:
                return i
        return None

    @staticmethod
    def _cluster_coords(coords: List[float], tolerance: float = 2.0) -> List[float]:
        """座標リストを tolerance 内でクラスタリングして代表値リストを返す"""
        if not coords:
            return []
        sorted_c = sorted(coords)
        clusters = []
        cur = [sorted_c[0]]
        for x in sorted_c[1:]:
            if x - cur[-1] <= tolerance:
                cur.append(x)
            else:
                clusters.append(sum(cur) / len(cur))
                cur = [x]
        clusters.append(sum(cur) / len(cur))
        return clusters

    # ------------------------------------------------------------------
    # logical_blocks（B90用）
    # ------------------------------------------------------------------

    def _words_to_logical_blocks(self, all_words: List[Dict]) -> List[Dict]:
        """all_words を行単位の logical_blocks に集約する（B90用）"""
        if not all_words:
            return []

        sorted_words = sorted(
            all_words,
            key=lambda w: (w['page'], w['bbox'][1], w['bbox'][0])
        )

        blocks = []
        current_words = [sorted_words[0]]

        for w in sorted_words[1:]:
            prev = current_words[-1]
            same_page = w['page'] == prev['page']
            same_line = same_page and abs(w['bbox'][1] - prev['bbox'][1]) <= self.Y_TOLERANCE
            if same_line:
                current_words.append(w)
            else:
                blocks.append(self._flush_line(current_words))
                current_words = [w]

        if current_words:
            blocks.append(self._flush_line(current_words))

        return blocks

    def _flush_line(self, words: List[Dict]) -> Dict:
        """ワードリストを1つの logical_block にまとめる"""
        text = ' '.join(w['text'] for w in words)
        x0 = min(w['bbox'][0] for w in words)
        y0 = min(w['bbox'][1] for w in words)
        x1 = max(w['bbox'][2] for w in words)
        y1 = max(w['bbox'][3] for w in words)
        return {
            'page': words[0]['page'],
            'bbox': [x0, y0, x1, y1],
            'text': text,
            'merged_count': len(words),
            '_source': 'REPORT',
        }

    # ------------------------------------------------------------------
    # テキスト削除（purge）
    # ------------------------------------------------------------------

    def _purge_extracted_text(
        self,
        file_path: Path,
        all_words: List[Dict[str, Any]],
        structured_tables: List[Dict[str, Any]] = None
    ) -> Path:
        """
        抽出したテキストを PDF から直接削除

        フェーズ1: テキスト（words）を常に削除
        フェーズ2: 表の罫線（graphics）を条件付きで削除
          - structured_tables が抽出済み -> 削除（Stage D の二重検出を防ぐ）
          - structured_tables が空 -> 保持（Stage D が検出できるよう残す）
        """
        logger.info(f"[B-42] テキスト削除処理開始")
        logger.info(f"[B-42]   ├─ 削除対象単語: {len(all_words)}個")
        logger.info(f"[B-42]   └─ 表構造データ: {len(structured_tables) if structured_tables else 0}個")

        try:
            import fitz
        except ImportError:
            logger.error("[B-42] PyMuPDF がインストールされていません")
            return file_path

        try:
            doc = fitz.open(str(file_path))
            logger.info(f"[B-42] PDF読み込み完了: {len(doc)}ページ")

            words_by_page: Dict[int, List[Dict]] = {}
            for word in all_words:
                words_by_page.setdefault(word['page'], []).append(word)

            tables_by_page: Dict[int, List[Dict]] = {}
            if structured_tables:
                for table in structured_tables:
                    pn = table.get('page', 0)
                    tables_by_page.setdefault(pn, []).append(table)

            deleted_words = 0
            deleted_table_graphics = 0

            for page_num in range(len(doc)):
                page = doc[page_num]
                page_words = words_by_page.get(page_num, [])

                # フェーズ1: テキスト削除（常時）
                if page_words:
                    logger.info(f"[B-42] ページ {page_num + 1}: {len(page_words)}単語を削除")
                    for word in page_words:
                        page.add_redact_annot(fitz.Rect(word['bbox']))
                        deleted_words += 1
                    page.apply_redactions(
                        images=fitz.PDF_REDACT_IMAGE_NONE,
                        graphics=True
                    )

                # フェーズ2: 表罫線削除（表構造抽出済みの場合のみ）
                page_tables = tables_by_page.get(page_num, [])
                if page_tables:
                    logger.info(f"[B-42] ページ {page_num + 1}: {len(page_tables)}表の罫線を削除")
                    for table in page_tables:
                        bbox = table.get('bbox')
                        if bbox:
                            page.add_redact_annot(fitz.Rect(bbox))
                            deleted_table_graphics += 1
                    page.apply_redactions(
                        images=fitz.PDF_REDACT_IMAGE_NONE,
                        graphics=True
                    )

            purged_dir = file_path.parent / "purged"
            purged_dir.mkdir(parents=True, exist_ok=True)
            purged_pdf_path = purged_dir / f"b42_{file_path.stem}_purged.pdf"

            doc.save(str(purged_pdf_path))
            doc.close()

            logger.info(f"[B-42] ========== テキスト削除完了 ==========")
            logger.info(f"[B-42] 削除した単語: {deleted_words}個")
            if deleted_table_graphics > 0:
                logger.info(f"[B-42] 削除した表罫線: {deleted_table_graphics}個（抽出済みのため）")
            else:
                logger.info(f"[B-42] 表罫線: 保持（Stage D 検出用）")
            logger.info(f"[B-42] purged PDF 保存先: {purged_pdf_path}")

            return purged_pdf_path

        except Exception as e:
            logger.error(f"[B-42] テキスト削除エラー", exc_info=True)
            logger.error(f"[B-42] エラー詳細: {e}")
            return file_path

    # ------------------------------------------------------------------
    # エラー結果
    # ------------------------------------------------------------------

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        return {
            'is_structured': False,
            'error': error_message,
            'data_type': 'report_multicolumn',
            'records': [],
            'structured_tables': [],
            'table_count_per_page': {},
            'logical_blocks': [],
            'tags': {},
            'all_words': [],
            'purged_pdf_path': '',
        }
