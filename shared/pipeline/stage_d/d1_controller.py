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
            pdf_path: PDFファイルパス（purged PDF）
            purged_image_path: 画像パス（オプション：指定なければ自動生成）
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

            # PDF→PNG 変換（画像が指定されていない場合）
            if purged_image_path is None or not purged_image_path.exists():
                logger.info("[D-1] Purged 画像がないため、PDFから自動生成します")
                purged_image_path = self._convert_pdf_to_image(pdf_path, page_num, output_dir)
                logger.info(f"[D-1] 画像生成完了: {purged_image_path.name}")
            else:
                logger.info(f"[D-1] 既存の画像を使用: {purged_image_path.name}")

            # D-3: ベクトル罫線抽出
            logger.info("\n[D-1] ステップ1: ベクトル罫線抽出（D-3）")
            vector_result = self.d3_vector.extract(pdf_path, page_num)

            # D-3結果のサマリー
            logger.info("[D-1] D-3結果サマリー:")
            logger.info(f"  ├─ 水平線: {len(vector_result.get('horizontal_lines', []))}本")
            logger.info(f"  ├─ 垂直線: {len(vector_result.get('vertical_lines', []))}本")
            logger.info(f"  ├─ 合計線: {len(vector_result.get('all_lines', []))}本")
            logger.info(f"  └─ ページサイズ: {vector_result.get('page_size', (0, 0))}")
            if vector_result.get('all_lines'):
                sample_lines = vector_result['all_lines'][:3]
                logger.debug(f"[D-1] ベクトル線サンプル（最初3本）: {sample_lines}")

            # D-5: ラスター罫線検出（画像がある場合のみ）
            raster_result = None
            if purged_image_path and purged_image_path.exists():
                logger.info("\n[D-1] ステップ2: ラスター罫線検出（D-5）")
                raster_result = self.d5_raster.detect(purged_image_path)

                # D-5結果のサマリー
                logger.info("[D-1] D-5結果サマリー:")
                logger.info(f"  ├─ 水平線: {len(raster_result.get('horizontal_lines', []))}本")
                logger.info(f"  ├─ 垂直線: {len(raster_result.get('vertical_lines', []))}本")
                logger.info(f"  ├─ 合計線: {len(raster_result.get('all_lines', []))}本")
                logger.info(f"  └─ 画像サイズ: {raster_result.get('image_size', (0, 0))}")
                if raster_result.get('all_lines'):
                    sample_lines = raster_result['all_lines'][:3]
                    logger.debug(f"[D-1] ラスター線サンプル（最初3本）: {sample_lines}")
            else:
                logger.info("\n[D-1] ステップ2: ラスター罫線検出（スキップ: 画像なし）")

            # D-8: 格子解析
            logger.info("\n[D-1] ステップ3: 格子解析（D-8）")
            grid_result = self.d8_grid.analyze(vector_result, raster_result)

            # D-8結果のサマリー
            logger.info("[D-1] D-8結果サマリー:")
            logger.info(f"  ├─ 交点数: {len(grid_result.get('intersections', []))}個")
            logger.info(f"  ├─ 表領域数: {len(grid_result.get('table_regions', []))}個")
            unified_lines = grid_result.get('unified_lines', {})
            logger.info(f"  ├─ 統合水平線: {len(unified_lines.get('horizontal', []))}本")
            logger.info(f"  └─ 統合垂直線: {len(unified_lines.get('vertical', []))}本")
            if grid_result.get('intersections'):
                sample_intersections = grid_result['intersections'][:5]
                logger.debug(f"[D-1] 交点サンプル（最初5個）: {sample_intersections}")
            if grid_result.get('table_regions'):
                for region in grid_result['table_regions']:
                    logger.info(f"[D-1] 表領域 {region.get('table_id')}: bbox={region.get('bbox')}, 交点={region.get('intersection_count')}")

            # D-9: セル特定
            logger.info("\n[D-1] ステップ4: セル特定（D-9）")
            cell_result = self.d9_cell.identify(grid_result)

            # D-9結果のサマリー
            logger.info("[D-1] D-9結果サマリー:")
            logger.info(f"  ├─ セル数: {len(cell_result.get('cells', []))}個")
            grid_info = cell_result.get('grid_info', {})
            logger.info(f"  ├─ 行数: {grid_info.get('rows', 0)}")
            logger.info(f"  └─ 列数: {grid_info.get('cols', 0)}")
            if cell_result.get('cells'):
                sample_cells = cell_result['cells'][:5]
                logger.debug(f"[D-1] セルサンプル（最初5個）:")
                for cell in sample_cells:
                    logger.debug(f"  {cell.get('cell_id')}: bbox={cell.get('bbox')}")

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

                # D-10結果のサマリー
                logger.info("[D-1] D-10結果サマリー:")
                logger.info(f"  ├─ 表画像数: {len(slice_result.get('tables', []))}枚")
                logger.info(f"  └─ 非表画像: {slice_result.get('non_table_image_path', 'なし')}")
                for table in slice_result.get('tables', []):
                    logger.info(f"[D-1] {table.get('table_id')}: {table.get('image_path')}")
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
            logger.info("[D-1] Stage D 完了サマリー")
            logger.info("=" * 60)
            logger.info("[D-1] 入力:")
            logger.info(f"  ├─ PDF: {pdf_path.name}")
            logger.info(f"  ├─ ページ: {page_num + 1}")
            logger.info(f"  └─ Purged画像: {'あり' if purged_image_path and purged_image_path.exists() else 'なし'}")

            logger.info("[D-1] 処理結果:")
            logger.info(f"  ├─ ベクトル線: {len(vector_result.get('all_lines', []))}本")
            logger.info(f"  ├─ ラスター線: {len(raster_result.get('all_lines', [])) if raster_result else 0}本")
            logger.info(f"  ├─ 交点: {len(grid_result.get('intersections', []))}個")
            logger.info(f"  ├─ 表領域: {len(result['tables'])}個")
            logger.info(f"  ├─ セル: {len(cell_result.get('cells', []))}個")
            logger.info(f"  └─ 非表画像: {'あり' if result['non_table_image_path'] else 'なし'}")

            if result['tables']:
                logger.info("[D-1] 表画像出力:")
                for table in result['tables']:
                    logger.info(f"  ├─ {table.get('table_id')}: {Path(table.get('image_path', '')).name}")

            logger.info(f"[D-1] 出力ディレクトリ: {output_dir}")
            logger.info("=" * 60)

            # デバッグ情報の詳細ダンプ
            import json
            logger.debug("[D-1] Debug情報 JSON dump:")
            try:
                debug_summary = {
                    'stage': 'D',
                    'page_index': page_num,
                    'vector_lines': {
                        'horizontal': len(vector_result.get('horizontal_lines', [])),
                        'vertical': len(vector_result.get('vertical_lines', [])),
                        'total': len(vector_result.get('all_lines', []))
                    },
                    'raster_lines': {
                        'horizontal': len(raster_result.get('horizontal_lines', [])) if raster_result else 0,
                        'vertical': len(raster_result.get('vertical_lines', [])) if raster_result else 0,
                        'total': len(raster_result.get('all_lines', [])) if raster_result else 0
                    },
                    'grid': {
                        'intersections': len(grid_result.get('intersections', [])),
                        'table_regions': len(grid_result.get('table_regions', [])),
                        'unified_horizontal_lines': len(grid_result.get('unified_lines', {}).get('horizontal', [])),
                        'unified_vertical_lines': len(grid_result.get('unified_lines', {}).get('vertical', []))
                    },
                    'cells': {
                        'total': len(cell_result.get('cells', [])),
                        'rows': cell_result.get('grid_info', {}).get('rows', 0),
                        'cols': cell_result.get('grid_info', {}).get('cols', 0)
                    },
                    'output': {
                        'table_images': len(result['tables']),
                        'non_table_image': bool(result['non_table_image_path'])
                    }
                }
                logger.debug(json.dumps(debug_summary, indent=2, ensure_ascii=False))
            except Exception as e:
                logger.warning(f"[D-1] Debug情報のダンプに失敗: {e}")

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

    def _convert_pdf_to_image(
        self,
        pdf_path: Path,
        page_num: int,
        output_dir: Path
    ) -> Path:
        """
        PDFの指定ページをPNG画像に変換

        Args:
            pdf_path: PDFファイルパス
            page_num: ページ番号（0始まり）
            output_dir: 出力ディレクトリ

        Returns:
            生成された画像のパス
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.error("[D-1] PyMuPDF がインストールされていません")
            raise ImportError("PyMuPDF is required. Run: pip install PyMuPDF")

        logger.info(f"[D-1] PDF→PNG 変換開始: ページ{page_num + 1}")

        # PDF を開く
        doc = fitz.open(pdf_path)

        if page_num >= len(doc):
            doc.close()
            raise ValueError(f"Page {page_num} does not exist (total pages: {len(doc)})")

        # ページをレンダリング
        page = doc.load_page(page_num)
        pix = page.get_pixmap(dpi=150)  # 150 DPI

        # 保存
        image_path = output_dir / f"d1_purged_page_{page_num}.png"
        pix.save(str(image_path))

        doc.close()

        logger.info(f"[D-1] PNG 生成完了: {image_path.name} ({pix.width}x{pix.height})")

        return image_path
