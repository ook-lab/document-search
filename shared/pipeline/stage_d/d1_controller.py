"""
D-1: Stage D Controller（Orchestrator）

Stage D の各コンポーネントを統合し、視覚構造解析を実行する。

パイプライン:
D-3: Vector Line Extractor（ベクトル罫線抽出）
  ↓
D-5: Raster Line Detector（ラスター罫線検出）
  ↓
D-8: Grid Analyzer（格子解析）
  ↓
D-9: Cell Identifier（セル特定）
  ↓
D-10: Image Slicer（画像分割）
"""

from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger

from .d3_vector_line_extractor import D3VectorLineExtractor
from .d5_raster_line_detector import D5RasterLineDetector
from .d8_grid_analyzer import D8GridAnalyzer
from .d9_cell_identifier import D9CellIdentifier
from .d10_image_slicer import D10ImageSlicer


class D1Controller:
    """D-1: Stage D Controller（Orchestrator）"""

    def __init__(self):
        """D-1 コントローラー初期化"""
        self.d3_vector = D3VectorLineExtractor()
        self.d5_raster = D5RasterLineDetector()
        self.d8_grid = D8GridAnalyzer()
        self.d9_cell = D9CellIdentifier()
        self.d10_slicer = D10ImageSlicer()

    def process(
        self,
        pdf_path: Path,
        purged_image_path: Optional[Path] = None,
        page_num: int = 0,
        output_dir: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        Stage D 視覚構造解析を実行

        Args:
            pdf_path: PDFファイルパス
            purged_image_path: B-90生成のテキスト消去済み画像パス（オプション）
            page_num: ページ番号（0始まり）
            output_dir: 出力ディレクトリ（Noneの場合は元ファイルと同じ）

        Returns:
            {
                'success': bool,
                'page_index': int,
                'tables': [
                    {
                        'table_id': 'T1',
                        'bbox': [x0, y0, x1, y1],
                        'image_path': 'path/to/d10_table_T1.png',
                        'cell_map': [...]
                    }
                ],
                'non_table_image_path': 'path/to/d10_background.png',
                'metadata': {},
                'debug': {
                    'vector_lines': {},
                    'raster_lines': {},
                    'grid_result': {},
                    'cell_result': {}
                }
            }
        """
        logger.info("=" * 60)
        logger.info("[D-1] Stage D 視覚構造解析開始")
        logger.info(f"  ├─ PDF: {pdf_path.name}")
        logger.info(f"  └─ ページ: {page_num + 1}")
        logger.info("=" * 60)

        try:
            # 出力ディレクトリの決定
            if output_dir is None:
                output_dir = pdf_path.parent / f"{pdf_path.stem}_stage_d"
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            # D-3: ベクトル罫線抽出
            logger.info("\n[D-1] ステップ1: ベクトル罫線抽出（D-3）")
            vector_result = self.d3_vector.extract(pdf_path, page_num)

            # D-5: ラスター罫線検出（画像がある場合のみ）
            raster_result = None
            if purged_image_path and purged_image_path.exists():
                logger.info("\n[D-1] ステップ2: ラスター罫線検出（D-5）")
                raster_result = self.d5_raster.detect(purged_image_path)
            else:
                logger.info("\n[D-1] ステップ2: ラスター罫線検出（スキップ: 画像なし）")

            # D-8: 格子解析
            logger.info("\n[D-1] ステップ3: 格子解析（D-8）")
            grid_result = self.d8_grid.analyze(vector_result, raster_result)

            # D-9: セル特定
            logger.info("\n[D-1] ステップ4: セル特定（D-9）")
            cell_result = self.d9_cell.identify(grid_result)

            # D-10: 画像分割
            slice_result = None
            if purged_image_path and purged_image_path.exists():
                logger.info("\n[D-1] ステップ5: 画像分割（D-10）")
                slice_result = self.d10_slicer.slice(
                    purged_image_path,
                    grid_result,
                    cell_result,
                    output_dir
                )
            else:
                logger.info("\n[D-1] ステップ5: 画像分割（スキップ: 画像なし）")
                slice_result = {
                    'page_index': page_num,
                    'tables': [],
                    'non_table_image_path': '',
                    'metadata': {}
                }

            # 結果を統合
            result = {
                'success': True,
                'page_index': page_num,
                'tables': slice_result.get('tables', []),
                'non_table_image_path': slice_result.get('non_table_image_path', ''),
                'metadata': slice_result.get('metadata', {}),
                'debug': {
                    'vector_lines': vector_result,
                    'raster_lines': raster_result,
                    'grid_result': grid_result,
                    'cell_result': cell_result
                }
            }

            logger.info("=" * 60)
            logger.info("[D-1] Stage D 完了")
            logger.info(f"  ├─ 表領域: {len(result['tables'])}個")
            logger.info(f"  ├─ セル数: {len(cell_result.get('cells', []))}個")
            logger.info(f"  └─ 非表画像: {'あり' if result['non_table_image_path'] else 'なし'}")
            logger.info("=" * 60)

            return result

        except Exception as e:
            logger.error(f"[D-1] 処理エラー: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'page_index': page_num,
                'tables': [],
                'non_table_image_path': '',
                'metadata': {},
                'debug': {}
            }
