"""
B-42: Multi-Column Report Processor（業務帳票・多段組専用）

同一項目（順位・氏名・校舎・点数）が横に複数セット並んでいる
マルチカラム帳票を攻略するための特化型プロセッサ。

処理フロー:
1. カラム・セグメンテーション（垂直スライス）
2. アンカーベースのレコード結合（Y軸アラインメント）
3. マルチライン・セルのマージ（氏名と校舎の親子関係）
4. 不規則なヘッダー・フッターの除外
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple
from loguru import logger
import statistics
import math


class B42MultiColumnReportProcessor:
    """B-42: Multi-Column Report Processor（業務帳票・多段組専用）"""

    # ガター検出の閾値（pt）
    GUTTER_THRESHOLD = 30.0

    # Y座標の許容誤差（pt）
    Y_TOLERANCE = 3.0

    # アンカー（順位）のパターン（数字のみ）
    ANCHOR_PATTERN = r'^\d+$'

    def process(self, file_path: Path, masked_pages=None) -> Dict[str, Any]:
        """
        マルチカラム帳票から構造化データを抽出

        Args:
            file_path: PDFファイルパス

        Returns:
            {
                'is_structured': bool,
                'data_type': str,                # 'report_multicolumn'
                'records': [...],                # レコードリスト
                'columns': [...],                # カラム情報
                'tags': {...},                   # メタ情報
                'purged_image_path': str         # テキスト消去後の画像
            }
        """
        logger.info(f"[B-42] ========== Multi-Column Report処理開始 ==========")
        logger.info(f"[B-42] 入力ファイル: {file_path.name}")

        try:
            import pdfplumber
            import re
        except ImportError:
            logger.error("[B-42] pdfplumber がインストールされていません")
            return self._error_result("pdfplumber not installed")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                logger.info(f"[B-42] PDF情報:")
                logger.info(f"[B-42]   ├─ ページ数: {len(pdf.pages)}")
                logger.info(f"[B-42]   ├─ メタデータ: {pdf.metadata}")

                all_records = []
                all_columns = []
                all_words = []  # 削除対象の全単語

                _masked = set(masked_pages or [])
                for page_num, page in enumerate(pdf.pages):
                    if _masked and page_num in _masked:
                        logger.debug(f"[B-42] ページ{page_num+1}: マスク → スキップ")
                        continue
                    logger.info(f"[B-42] ========== ページ {page_num + 1}/{len(pdf.pages)} 処理中 ==========")
                    logger.info(f"[B-42]   ├─ ページサイズ: {page.width:.1f} x {page.height:.1f} pt")

                    # 1. カラム・セグメンテーション
                    columns = self._detect_columns(page)
                    logger.info(f"[B-42]   ├─ カラム検出: {len(columns)}個")

                    # カラムの詳細ログ
                    for col_idx, col_bbox in enumerate(columns):
                        x0, y0, x1, y1 = col_bbox
                        width = x1 - x0
                        logger.info(f"[B-42]   │   ├─ カラム {col_idx + 1}: "
                                  f"x={x0:.1f}-{x1:.1f} (幅={width:.1f}pt)")

                    # 2. 各カラムからレコードを抽出
                    page_records = 0
                    for col_idx, column_bbox in enumerate(columns):
                        records = self._extract_records_from_column(
                            page, column_bbox, page_num, col_idx
                        )
                        all_records.extend(records)
                        page_records += len(records)
                        logger.info(f"[B-42]   │   ├─ カラム {col_idx + 1}: {len(records)}レコード抽出")

                        # レコードサンプル
                        if records and col_idx == 0:  # 最初のカラムの最初のレコードのみ
                            sample = records[0]
                            logger.info(f"[B-42]   │   │   └─ サンプル: rank={sample.get('rank')}, "
                                      f"name={sample.get('name')}, "
                                      f"org={sample.get('organization')}, "
                                      f"score={sample.get('score')}")

                    all_columns.extend(columns)
                    logger.info(f"[B-42]   ├─ ページ総レコード: {page_records}個")

                    # 削除用：ページ全体の単語を収集
                    page_words = page.extract_words(
                        x_tolerance=3,
                        y_tolerance=3,
                        keep_blank_chars=False
                    )
                    for word in page_words:
                        all_words.append({
                            'page': page_num,
                            'text': word['text'],
                            'bbox': (word['x0'], word['top'], word['x1'], word['bottom'])
                        })
                    logger.info(f"[B-42]   └─ 単語（削除対象）: {len(page_words)}個")

                # メタ情報
                tags = {
                    'source': 'stage_b',
                    'processor': 'b42_multicolumn_report',
                    'page_count': len(pdf.pages),
                    'column_count': len(all_columns),
                    'record_count': len(all_records)
                }

                # 全ページの集計ログ
                logger.info(f"[B-42] ========== 抽出結果サマリー ==========")
                logger.info(f"[B-42] 総カラム数: {len(all_columns)}個")
                logger.info(f"[B-42] 総レコード数: {len(all_records)}個")
                logger.info(f"[B-42] 削除対象単語総数: {len(all_words)}個")

                # レコードサンプル（先頭5件）
                if all_records:
                    logger.info(f"[B-42] レコードサンプル（先頭5件）:")
                    for idx, rec in enumerate(all_records[:5]):
                        logger.info(f"[B-42]   {idx + 1}. rank={rec.get('rank')}, "
                                  f"name={rec.get('name')}, "
                                  f"org={rec.get('organization')}, "
                                  f"score={rec.get('score')}")
                else:
                    logger.warning(f"[B-42] レコードが抽出されませんでした")

                # purged PDF 生成
                logger.info(f"[B-42] ========== テキスト削除処理開始 ==========")
                purged_pdf_path = self._purge_extracted_text(file_path, all_words)
                logger.info(f"[B-42] purged PDF 生成完了: {purged_pdf_path.name}")

                return {
                    'is_structured': True,
                    'data_type': 'report_multicolumn',
                    'records': all_records,
                    'columns': all_columns,
                    'tags': tags,
                    'all_words': all_words,
                    'purged_pdf_path': str(purged_pdf_path)
                }

        except Exception as e:
            logger.error(f"[B-42] ========== 処理エラー ==========", exc_info=True)
            logger.error(f"[B-42] エラー詳細: {e}")
            return self._error_result(str(e))

    def _detect_columns(self, page) -> List[Tuple[float, float, float, float]]:
        """
        カラム・セグメンテーション（垂直スライス）

        【修正 2026-02-18】
        旧実装は chars の x0/x1 を「点」として 1pt ビンに打ち density==0 をガター扱い。
        → 文字領域でも点と点の間が全部 0 になるため、ページ左余白（x=0〜min_text_x）が
          ガター誤認され、先頭カラムが (0,0,0,h) の幅0 bbox になって within_bbox がクラッシュ。

        本実装の修正ポイント:
        1. chars の [x0,x1] 区間を「占有（面）」として塗りつぶす（点→面）
        2. 文字の実在範囲 [min_text_x, max_text_x] の外側に触れるガターは除外（余白≠ガター）
        3. 幅0/極小カラムを生成しない（_add_column で幅チェック）

        Args:
            page: pdfplumberのPageオブジェクト

        Returns:
            [(x0, y0, x1, y1), ...] カラムのbboxリスト
        """
        logger.info(f"[B-42] カラム検出開始")

        page_width = float(page.width)
        page_height = float(page.height)

        chars = page.chars
        if not chars:
            logger.warning(f"[B-42] 文字が見つかりません。ページ全体を1カラムとして扱います")
            return [(0.0, 0.0, page_width, page_height)]

        logger.info(f"[B-42]   ├─ 文字数: {len(chars)}個")

        # 文字の実在範囲（余白を判別するために使用）
        min_text_x = min(float(c.get('x0', 0.0)) for c in chars)
        max_text_x = max(float(c.get('x1', 0.0)) for c in chars)
        logger.info(f"[B-42]   ├─ テキスト実在範囲: x={min_text_x:.1f} ~ {max_text_x:.1f}")

        # chars の [x0, x1] 区間を占有として塗りつぶす（点ではなく面）
        W = int(math.ceil(page_width))
        occ = [0] * (W + 1)
        for c in chars:
            cx0 = float(c.get('x0', 0.0))
            cx1 = float(c.get('x1', 0.0))
            if cx1 <= cx0:
                continue
            s = max(0, int(math.floor(cx0)))
            e = min(W, int(math.ceil(cx1)))
            for x in range(s, e):
                occ[x] = 1

        # ガター（空白領域）を検出（ページ全体をスキャン）
        gutters = []
        in_gutter = False
        gutter_start = None

        for x in range(W):
            if occ[x] == 0:
                if not in_gutter:
                    gutter_start = x
                    in_gutter = True
            else:
                if in_gutter and gutter_start is not None:
                    gutter_width = x - gutter_start
                    if gutter_width >= self.GUTTER_THRESHOLD:
                        gutters.append((float(gutter_start), float(x)))
                    in_gutter = False
                    gutter_start = None

        if in_gutter and gutter_start is not None:
            gutter_width = W - gutter_start
            if gutter_width >= self.GUTTER_THRESHOLD:
                gutters.append((float(gutter_start), float(W)))

        # ページ端（文字実在範囲外）に接するガターは「余白」なので除外
        # = ガターが min_text_x より左、または max_text_x より右から始まる/終わる場合は除外
        EPS = 1.0
        filtered = [
            (start, end) for (start, end) in gutters
            if start >= min_text_x - EPS and end <= max_text_x + EPS
        ]
        logger.info(f"[B-42]   ├─ ガター検出: {len(gutters)}個 → 余白除外後: {len(filtered)}個 (閾値={self.GUTTER_THRESHOLD}pt)")
        for idx, (start, end) in enumerate(filtered):
            logger.info(f"[B-42]   │   ├─ ガター {idx + 1}: x={start:.1f}-{end:.1f} (幅={end - start:.1f}pt)")

        if not filtered:
            logger.info(f"[B-42]   └─ 有効ガターなし。ページ全体を1カラムとして扱います")
            return [(0.0, 0.0, page_width, page_height)]

        # ガターでページを分割（幅0/極小カラムは作らない）
        columns: List[Tuple[float, float, float, float]] = []

        def _add_column(x0: float, x1: float):
            if x1 - x0 <= 1.0:
                logger.debug(f"[B-42]   幅0/極小カラムをスキップ: x={x0:.1f}-{x1:.1f}")
                return
            columns.append((float(x0), 0.0, float(x1), page_height))

        _add_column(0.0, filtered[0][0])
        for i in range(len(filtered) - 1):
            _add_column(filtered[i][1], filtered[i + 1][0])
        _add_column(filtered[-1][1], page_width)

        if not columns:
            logger.warning(f"[B-42] 有効カラムが生成できません。ページ全体を1カラムとして扱います")
            return [(0.0, 0.0, page_width, page_height)]

        logger.info(f"[B-42]   └─ カラム分割完了: {len(columns)}カラム")
        return columns

    def _extract_records_from_column(
        self,
        page,
        column_bbox: Tuple[float, float, float, float],
        page_num: int,
        col_idx: int
    ) -> List[Dict[str, Any]]:
        """
        カラム内からレコードを抽出（アンカーベース）

        Args:
            page: pdfplumberのPageオブジェクト
            column_bbox: カラムのbbox (x0, y0, x1, y1)
            page_num: ページ番号
            col_idx: カラムインデックス

        Returns:
            [{
                'rank': str,
                'name': str,
                'organization': str,
                'score': str,
                'page': int,
                'column_index': int
            }, ...]
        """
        import re

        logger.info(f"[B-42] カラム {col_idx + 1} のレコード抽出開始")

        x0, y0, x1, y1 = column_bbox

        # 幅0/極小 bbox は pdfplumber が例外を投げるためスキップ（二重安全）
        if x1 <= x0 + 1.0:
            logger.warning(f"[B-42]   カラム {col_idx + 1}: 無効bbox ({x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f}) → スキップ")
            return []

        # カラム内の文字を抽出
        cropped = page.within_bbox((x0, y0, x1, y1))
        chars = cropped.chars

        if not chars:
            logger.warning(f"[B-42]   カラム {col_idx + 1}: 文字が見つかりません")
            return []

        logger.info(f"[B-42]   ├─ 文字数: {len(chars)}個")

        # 文字をY座標でグループ化（行を作成）
        lines = self._group_chars_by_y(chars)
        logger.info(f"[B-42]   ├─ 行数: {len(lines)}行")

        # アンカー（順位）を検出
        anchors = []
        for line in lines:
            text = line['text'].strip()
            # 数字のみの行をアンカーとする
            if re.match(self.ANCHOR_PATTERN, text):
                anchors.append({
                    'rank': text,
                    'y': line['y'],
                    'line_index': line['index']
                })

        logger.info(f"[B-42]   ├─ アンカー（順位）: {len(anchors)}個")
        if anchors:
            sample_ranks = [a['rank'] for a in anchors[:5]]
            logger.info(f"[B-42]   │   └─ サンプル: {', '.join(sample_ranks)}")

        if not anchors:
            logger.warning(f"[B-42]   └─ カラム {col_idx + 1}: アンカー（順位）が見つかりません")
            return []

        # 各アンカーからレコードを構築
        records = []
        for i, anchor in enumerate(anchors):
            # 次のアンカーまでのY範囲を決定
            y_start = anchor['y']
            y_end = anchors[i + 1]['y'] if i + 1 < len(anchors) else y1

            # この範囲内の文字を収集
            record_chars = [c for c in chars if y_start <= c['top'] <= y_end]

            # レコードを構築
            record = self._build_record(
                record_chars, anchor['rank'], page_num, col_idx
            )

            if record:
                records.append(record)

        logger.info(f"[B-42]   └─ レコード構築完了: {len(records)}個")

        return records

    def _group_chars_by_y(self, chars: List[Dict]) -> List[Dict[str, Any]]:
        """
        文字をY座標でグループ化（行を作成）

        Args:
            chars: 文字リスト

        Returns:
            [{
                'index': int,
                'y': float,
                'text': str,
                'chars': [...]
            }, ...]
        """
        if not chars:
            return []

        # Y座標でソート
        chars_sorted = sorted(chars, key=lambda c: (c['top'], c['x0']))

        # グループ化
        lines = []
        current_line = []
        prev_y = None

        for char in chars_sorted:
            if prev_y is None or abs(char['top'] - prev_y) < self.Y_TOLERANCE:
                current_line.append(char)
            else:
                if current_line:
                    lines.append({
                        'index': len(lines),
                        'y': statistics.mean([c['top'] for c in current_line]),
                        'text': ''.join([c['text'] for c in current_line]),
                        'chars': current_line
                    })
                current_line = [char]
            prev_y = char['top']

        if current_line:
            lines.append({
                'index': len(lines),
                'y': statistics.mean([c['top'] for c in current_line]),
                'text': ''.join([c['text'] for c in current_line]),
                'chars': current_line
            })

        return lines

    def _build_record(
        self,
        record_chars: List[Dict],
        rank: str,
        page_num: int,
        col_idx: int
    ) -> Dict[str, Any]:
        """
        文字リストからレコードを構築

        Args:
            record_chars: レコード範囲内の文字リスト
            rank: 順位
            page_num: ページ番号
            col_idx: カラムインデックス

        Returns:
            {
                'rank': str,
                'name': str,
                'organization': str,
                'score': str,
                'page': int,
                'column_index': int
            }
        """
        import re

        # 行をグループ化
        lines = self._group_chars_by_y(record_chars)

        # 順位行を除外
        lines = [line for line in lines if line['text'].strip() != rank]

        if not lines:
            return None

        # フォントサイズで分類（大きい＝氏名、小さい＝校舎、数字＝点数）
        name_candidates = []
        org_candidates = []
        score_candidates = []

        for line in lines:
            text = line['text'].strip()
            if not text:
                continue

            # フォントサイズの平均を計算
            avg_size = statistics.mean([c.get('size', 10) for c in line['chars']])

            # 数字のみ（点数）
            if re.match(r'^\d+$', text):
                score_candidates.append({'text': text, 'size': avg_size, 'y': line['y']})
            # それ以外
            else:
                # フォントサイズで氏名と校舎を分離
                if avg_size > 8:  # 閾値は調整可能
                    name_candidates.append({'text': text, 'size': avg_size, 'y': line['y']})
                else:
                    org_candidates.append({'text': text, 'size': avg_size, 'y': line['y']})

        # 最も大きいフォントサイズの行を氏名とする
        name = ''
        if name_candidates:
            name = max(name_candidates, key=lambda x: x['size'])['text']
        elif org_candidates:
            # 校舎候補しかない場合、最初のものを氏名とする
            name = org_candidates[0]['text']
            org_candidates = org_candidates[1:]

        # 校舎（氏名の次に大きいもの、または最初のもの）
        organization = ''
        if org_candidates:
            organization = org_candidates[0]['text']

        # 点数（最後のもの）
        score = ''
        if score_candidates:
            score = score_candidates[-1]['text']

        return {
            'rank': rank,
            'name': name,
            'organization': organization,
            'score': score,
            'page': page_num,
            'column_index': col_idx
        }

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
                        graphics=False
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
    def _error_result(self, error_message: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'is_structured': False,
            'error': error_message,
            'data_type': 'unknown',
            'records': [],
            'columns': [],
            'tags': {},
            'all_words': [],
            'purged_pdf_path': ''
        }
