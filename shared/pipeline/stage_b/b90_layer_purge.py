"""
B-90: Layer Purge Processor（テキスト消去・差分画像生成）

Stage Bで抽出に成功したテキスト情報を元に、
PDFからそれらの文字を「消去」したクリーンな画像を生成する。

目的:
1. Stage Eでの二重読み取りを防止
2. Vision APIへのノイズを最小化
3. 図版・画像のみを浮き彫りにする
"""

from pathlib import Path
from typing import Dict, Any, List, Tuple
from loguru import logger


class B90LayerPurgeProcessor:
    """B-90: Layer Purge Processor（テキスト消去・差分画像生成）"""

    def __init__(self):
        """Layer Purge Processor 初期化"""
        pass

    def purge(
        self,
        file_path: Path,
        b_result: Dict[str, Any],
        output_dir: Path = None,
        background_color: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    ) -> Dict[str, Any]:
        """
        テキストを消去した画像を生成

        Args:
            file_path: 元のPDFファイルパス
            b_result: Stage Bの実行結果（bbox情報を含む）
            output_dir: 出力ディレクトリ（Noneの場合は元ファイルと同じディレクトリ）
            background_color: 背景色（RGB, 0.0-1.0）デフォルトは白

        Returns:
            {
                'success': bool,
                'purged_image_path': str,    # 消去済み画像のパス
                'mask_stats': {
                    'total_pages': int,
                    'masked_area_percentage': float,
                    'bbox_count': int
                }
            }
        """
        logger.info(f"[B-90] Layer Purge処理開始: {file_path.name}")

        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.error("[B-90] PyMuPDF がインストールされていません")
            return self._error_result("PyMuPDF not installed")

        # 出力ディレクトリの決定
        if output_dir is None:
            output_dir = file_path.parent
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 出力ファイル名
        output_path = output_dir / f"{file_path.stem}_purged.pdf"
        image_output_dir = output_dir / f"{file_path.stem}_purged_images"
        image_output_dir.mkdir(parents=True, exist_ok=True)

        try:
            # PDFを開く
            doc = fitz.open(str(file_path))

            # Stage Bの結果からbbox情報を抽出
            bboxes = self._extract_bboxes_from_b_result(b_result, doc)

            logger.info(f"[B-90] 消去対象bbox: {len(bboxes)}個")

            # 統計情報
            total_area = 0
            page_area = 0
            total_pages = len(doc)

            # 各ページを処理
            for page_num in range(total_pages):
                page = doc[page_num]
                page_rect = page.rect
                page_area += page_rect.width * page_rect.height

                # このページのbboxをフィルタ
                page_bboxes = [b for b in bboxes if b['page'] == page_num]

                logger.info(f"[B-90]   ページ {page_num + 1}: {len(page_bboxes)}個のbboxを消去")

                # bboxを白塗り
                for bbox_info in page_bboxes:
                    bbox = bbox_info['bbox']
                    # fitz.Rect(x0, y0, x1, y1) 形式に変換
                    rect = fitz.Rect(bbox[0], bbox[1], bbox[2], bbox[3])

                    # 白い矩形を描画（テキストを隠す）
                    page.draw_rect(
                        rect,
                        color=None,  # 枠線なし
                        fill=background_color,
                        overlay=True  # 上書き
                    )

                    # 面積を計算
                    total_area += rect.width * rect.height

            # 消去済みPDFを保存
            doc.save(str(output_path))
            logger.info(f"[B-90] 消去済みPDF保存: {output_path}")

            # PDFを画像に変換
            image_paths = self._convert_pdf_to_images(output_path, image_output_dir)

            doc.close()

            # 統計情報
            mask_percentage = (total_area / page_area * 100) if page_area > 0 else 0

            mask_stats = {
                'total_pages': total_pages,
                'masked_area_percentage': mask_percentage,
                'bbox_count': len(bboxes)
            }

            logger.info(f"[B-90] Layer Purge完了")
            logger.info(f"  ├─ 消去率: {mask_percentage:.2f}%")
            logger.info(f"  └─ 画像出力: {len(image_paths)}枚")

            return {
                'success': True,
                'purged_pdf_path': str(output_path),
                'purged_image_paths': image_paths,
                'mask_stats': mask_stats
            }

        except Exception as e:
            logger.error(f"[B-90] 処理エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _extract_bboxes_from_b_result(
        self,
        b_result: Dict[str, Any],
        doc
    ) -> List[Dict[str, Any]]:
        """
        Stage Bの結果からbbox情報を抽出

        Args:
            b_result: Stage Bの実行結果
            doc: fitz.Document

        Returns:
            [{
                'page': int,
                'bbox': [x0, y0, x1, y1]
            }, ...]
        """
        bboxes = []

        # logical_blocks から抽出
        if 'logical_blocks' in b_result:
            for block in b_result['logical_blocks']:
                page_num = block.get('page', 0)
                bbox = block.get('bbox')

                if bbox:
                    # 正規化座標の場合は実座標に変換
                    if all(0 <= x <= 1 for x in bbox):
                        page = doc[page_num]
                        bbox = self._denormalize_bbox(bbox, page.rect)

                    bboxes.append({
                        'page': page_num,
                        'bbox': bbox
                    })

        # paragraphs から抽出（Native Word）
        if 'paragraphs' in b_result:
            # Native Wordにはbbox情報がないため、スキップ
            pass

        # sheets から抽出（Native Excel）
        if 'sheets' in b_result:
            # Native Excelにはbbox情報がないため、スキップ
            pass

        # records から抽出（B-42）
        if 'records' in b_result:
            # B-42のレコードには個別のbbox情報がないため、
            # columns情報から推定（オプション）
            pass

        return bboxes

    def _denormalize_bbox(
        self,
        normalized_bbox: List[float],
        page_rect: 'fitz.Rect'
    ) -> List[float]:
        """
        正規化座標（0.0-1.0）を実座標（pt）に変換

        Args:
            normalized_bbox: [x0, y0, x1, y1] (0.0-1.0)
            page_rect: ページの矩形

        Returns:
            [x0, y0, x1, y1] (pt)
        """
        x0, y0, x1, y1 = normalized_bbox
        return [
            x0 * page_rect.width,
            y0 * page_rect.height,
            x1 * page_rect.width,
            y1 * page_rect.height
        ]

    def _convert_pdf_to_images(
        self,
        pdf_path: Path,
        output_dir: Path,
        dpi: int = 150
    ) -> List[str]:
        """
        PDFを画像に変換

        Args:
            pdf_path: PDFファイルパス
            output_dir: 出力ディレクトリ
            dpi: 解像度

        Returns:
            画像ファイルパスのリスト
        """
        try:
            import fitz
        except ImportError:
            logger.warning("[B-90] PyMuPDF がないため、画像変換をスキップ")
            return []

        image_paths = []

        try:
            doc = fitz.open(str(pdf_path))

            for page_num in range(len(doc)):
                page = doc[page_num]

                # 画像としてレンダリング
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                pix = page.get_pixmap(matrix=mat)

                # PNG形式で保存
                image_path = output_dir / f"page_{page_num + 1}.png"
                pix.save(str(image_path))
                image_paths.append(str(image_path))

            doc.close()
            logger.info(f"[B-90] PDF→画像変換完了: {len(image_paths)}枚")

        except Exception as e:
            logger.warning(f"[B-90] 画像変換エラー: {e}")

        return image_paths

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'success': False,
            'error': error_message,
            'purged_pdf_path': '',
            'purged_image_paths': [],
            'mask_stats': {}
        }
