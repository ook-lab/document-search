"""
B-14: Goodnotes Processor（Goodnotes PDF専用）

Goodnotes由来のPDFから、デジタル入力テキストを抽出し、
手書きが存在する領域を特定する。

目的:
1. デジタルテキスト（テキストツール入力）の完全抽出
2. 手書き領域（Annots）の特定
3. Stage E での手書き読み取りのためのヒント生成
"""

from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from loguru import logger
import pdfplumber

try:
    import fitz
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False


class B14GoodnotesProcessor:
    """B-14: Goodnotes Processor（Goodnotes PDF専用）"""

    def __init__(self):
        """Goodnotes Processor 初期化"""
        pass

    def process(
        self,
        file_path: Path
    ) -> Dict[str, Any]:
        """
        Goodnotes PDFを処理

        Args:
            file_path: PDFファイルパス

        Returns:
            {
                'is_structured': bool,
                'data_type': str,
                'digital_texts': list,      # デジタル入力テキスト
                'handwritten_zones': list,  # 手書き領域
                'logical_blocks': list,     # 論理ブロック
                'tags': dict
            }
        """
        logger.info(f"[B-14] ========== Goodnotes処理開始 ==========")
        logger.info(f"[B-14] 入力ファイル: {file_path.name}")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                logger.info(f"[B-14] PDF情報:")
                logger.info(f"[B-14]   ├─ ページ数: {len(pdf.pages)}")
                logger.info(f"[B-14]   ├─ メタデータ: {pdf.metadata}")

                digital_texts = []
                handwritten_zones = []
                logical_blocks = []
                all_words = []  # 削除対象の全単語

                for page_num, page in enumerate(pdf.pages):
                    logger.info(f"[B-14] ページ {page_num + 1}/{len(pdf.pages)} を処理中")
                    logger.info(f"[B-14]   ├─ ページサイズ: {page.width:.1f} x {page.height:.1f} pt")

                    # デジタルテキストを抽出
                    page_digital_texts = self._extract_digital_text(page, page_num)
                    digital_texts.extend(page_digital_texts)
                    logger.info(f"[B-14]   ├─ デジタルテキスト: {len(page_digital_texts)}文字")

                    # 手書き領域を特定
                    page_handwritten_zones = self._detect_handwritten_zones(page, page_num)
                    handwritten_zones.extend(page_handwritten_zones)
                    logger.info(f"[B-14]   ├─ 手書き領域: {len(page_handwritten_zones)}個")

                    # 手書き領域の詳細ログ
                    for idx, zone in enumerate(page_handwritten_zones):
                        bbox = zone.get('bbox', [0, 0, 0, 0])
                        width = bbox[2] - bbox[0] if len(bbox) >= 4 else 0
                        height = bbox[3] - bbox[1] if len(bbox) >= 4 else 0
                        logger.info(f"[B-14]   │   ├─ 領域 {idx+1}: type={zone.get('subtype', 'unknown')}, "
                                  f"size={width:.1f}x{height:.1f} pt, bbox={bbox}")

                    # 論理ブロックを生成
                    page_blocks = self._create_logical_blocks(
                        page_digital_texts,
                        page_num,
                        page.width,
                        page.height
                    )
                    logical_blocks.extend(page_blocks)
                    logger.info(f"[B-14]   ├─ 論理ブロック: {len(page_blocks)}個")

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
                    logger.info(f"[B-14]   └─ 単語（削除対象）: {len(page_words)}個")

                # 全ページの集計ログ
                logger.info(f"[B-14] ========== 抽出結果サマリー ==========")
                logger.info(f"[B-14] デジタルテキスト総数: {len(digital_texts)}文字")
                logger.info(f"[B-14] 手書き領域総数: {len(handwritten_zones)}個")
                logger.info(f"[B-14] 論理ブロック総数: {len(logical_blocks)}個")
                logger.info(f"[B-14] 削除対象単語総数: {len(all_words)}個")

                # デジタルテキストのサンプル出力
                if digital_texts:
                    combined_text = ''.join([t.get('text', '') for t in digital_texts])
                    sample_text = combined_text[:200] if len(combined_text) > 200 else combined_text
                    logger.info(f"[B-14] デジタルテキストサンプル（先頭200文字）:")
                    logger.info(f"[B-14]   「{sample_text}」")
                else:
                    logger.warning(f"[B-14] デジタルテキストが抽出されませんでした")

                # 論理ブロックのサンプル
                if logical_blocks:
                    first_block = logical_blocks[0]
                    block_text = first_block.get('text', '')[:100]
                    logger.info(f"[B-14] 論理ブロックサンプル（1個目、先頭100文字）:")
                    logger.info(f"[B-14]   「{block_text}」")

                # purged PDF 生成
                logger.info(f"[B-14] ========== テキスト削除処理開始 ==========")
                purged_pdf_path = self._purge_extracted_text(file_path, all_words)
                logger.info(f"[B-14] purged PDF 生成完了: {purged_pdf_path.name}")

                return {
                    'is_structured': True,
                    'data_type': 'goodnotes',
                    'digital_texts': digital_texts,
                    'handwritten_zones': handwritten_zones,
                    'logical_blocks': logical_blocks,
                    'all_words': all_words,
                    'purged_pdf_path': str(purged_pdf_path),
                    'tags': {
                        'source': 'stage_b',
                        'processor': 'b14_goodnotes',
                        'has_handwriting': len(handwritten_zones) > 0,
                        'digital_text_count': len(digital_texts)
                    }
                }

        except Exception as e:
            logger.error(f"[B-14] ========== 処理エラー ==========", exc_info=True)
            logger.error(f"[B-14] エラー詳細: {e}")
            return {
                'is_structured': False,
                'error': str(e),
                'data_type': 'goodnotes',
                'all_words': [],
                'purged_pdf_path': ''
            }

    def _extract_digital_text(
        self,
        page,
        page_num: int
    ) -> List[Dict[str, Any]]:
        """
        デジタル入力テキストを抽出

        Args:
            page: pdfplumber page object
            page_num: ページ番号

        Returns:
            デジタルテキストリスト
        """
        digital_texts = []

        # テキスト抽出（文字単位）
        chars = page.chars if hasattr(page, 'chars') else []

        for char in chars:
            # Goodnotes のテキストツールで入力された文字を抽出
            # fontname や size で判別可能な場合も
            digital_texts.append({
                'page': page_num,
                'text': char.get('text', ''),
                'bbox': [
                    char.get('x0', 0),
                    char.get('top', 0),
                    char.get('x1', 0),
                    char.get('bottom', 0)
                ],
                'fontname': char.get('fontname', ''),
                'size': char.get('size', 0)
            })

        return digital_texts

    def _detect_handwritten_zones(
        self,
        page,
        page_num: int
    ) -> List[Dict[str, Any]]:
        """
        手書き領域を特定

        Args:
            page: pdfplumber page object
            page_num: ページ番号

        Returns:
            手書き領域リスト
        """
        handwritten_zones = []

        # PDF の Annots（注釈）を確認
        # Goodnotes の手書きは Annots レイヤーに存在する
        try:
            if hasattr(page, 'annots'):
                for annot in page.annots:
                    # Ink Annotation（手書きストローク）を特定
                    if annot.get('Subtype') == '/Ink':
                        # バウンディングボックスを取得
                        rect = annot.get('Rect')
                        if rect:
                            handwritten_zones.append({
                                'page': page_num,
                                'bbox': rect,
                                'type': 'handwritten',
                                'subtype': 'ink'
                            })

            # Lines/Curves の密集度から手書きエリアを推定（補完的手法）
            lines = page.lines if hasattr(page, 'lines') else []
            curves = page.curves if hasattr(page, 'curves') else []

            # 線が密集しているエリアを手書きとして扱う
            # TODO: より高度なクラスタリング実装
            if len(lines) > 100 or len(curves) > 100:
                # 簡易的に全体を1つのゾーンとする
                handwritten_zones.append({
                    'page': page_num,
                    'bbox': [0, 0, page.width, page.height],
                    'type': 'handwritten',
                    'subtype': 'dense_strokes',
                    'confidence': 'low'
                })

        except Exception as e:
            logger.warning(f"[B-14] 手書き領域検出エラー: {e}")

        return handwritten_zones

    def _create_logical_blocks(
        self,
        digital_texts: List[Dict[str, Any]],
        page_num: int,
        page_width: float,
        page_height: float
    ) -> List[Dict[str, Any]]:
        """
        デジタルテキストから論理ブロックを生成

        Args:
            digital_texts: デジタルテキストリスト
            page_num: ページ番号
            page_width: ページ幅
            page_height: ページ高さ

        Returns:
            論理ブロックリスト
        """
        if not digital_texts:
            return []

        # テキストを結合
        combined_text = ''.join([t.get('text', '') for t in digital_texts])

        # バウンディングボックスを計算
        if digital_texts:
            all_x0 = [t['bbox'][0] for t in digital_texts]
            all_y0 = [t['bbox'][1] for t in digital_texts]
            all_x1 = [t['bbox'][2] for t in digital_texts]
            all_y1 = [t['bbox'][3] for t in digital_texts]

            bbox = [
                min(all_x0),
                min(all_y0),
                max(all_x1),
                max(all_y1)
            ]
        else:
            bbox = [0, 0, page_width, page_height]

        # 正規化座標
        bbox_normalized = [
            bbox[0] / page_width,
            bbox[1] / page_height,
            bbox[2] / page_width,
            bbox[3] / page_height
        ]

        return [{
            'page': page_num,
            'text': combined_text,
            'bbox': bbox,
            'bbox_normalized': bbox_normalized,
            'type': 'digital_text'
        }]

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
        logger.info(f"[B-14] テキスト削除処理開始")
        logger.info(f"[B-14]   ├─ 削除対象単語: {len(all_words)}個")
        logger.info(f"[B-14]   └─ 表構造データ: {len(structured_tables) if structured_tables else 0}個")

        try:
            import fitz
        except ImportError:
            logger.error("[B-14] PyMuPDF がインストールされていません")
            return file_path

        try:
            doc = fitz.open(str(file_path))
            logger.info(f"[B-14] PDF読み込み完了: {len(doc)}ページ")

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
                    logger.info(f"[B-14] ページ {page_num + 1}: {len(page_words)}単語を削除")
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
                    logger.info(f"[B-14] ページ {page_num + 1}: {len(page_tables)}表の罫線を削除")
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
            purged_pdf_path = purged_dir / f"b14_{file_path.stem}_purged.pdf"

            doc.save(str(purged_pdf_path))
            doc.close()

            logger.info(f"[B-14] ========== テキスト削除完了 ==========")
            logger.info(f"[B-14] 削除した単語: {deleted_words}個")
            if deleted_table_graphics > 0:
                logger.info(f"[B-14] 削除した表罫線: {deleted_table_graphics}個（抽出済みのため）")
            else:
                logger.info(f"[B-14] 表罫線: 保持（Stage D 検出用）")
            logger.info(f"[B-14] purged PDF 保存先: {purged_pdf_path}")

            return purged_pdf_path

        except Exception as e:
            logger.error(f"[B-14] テキスト削除エラー", exc_info=True)
            logger.error(f"[B-14] エラー詳細: {e}")
            return file_path