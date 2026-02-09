"""
A-3: Entry Point（処理開始）

Stage A のオーケストレーター。
A-5（書類種類判断）と A-6（サイズ測定）を実行し、
結果を統合して返す。
"""

from pathlib import Path
from typing import Dict, Any
from loguru import logger

from .a5_type_analyzer import A5TypeAnalyzer
from .a6_dimension_measurer import A6DimensionMeasurer


class A3EntryPoint:
    """A-3: Entry Point（処理開始）"""

    def __init__(self):
        """Stage A オーケストレーター初期化"""
        self.type_analyzer = A5TypeAnalyzer()
        self.dimension_measurer = A6DimensionMeasurer()

    def process(self, file_path: str | Path) -> Dict[str, Any]:
        """
        Stage A 処理を実行

        Args:
            file_path: PDFファイルパス

        Returns:
            {
                'document_type': str,  # WORD, INDESIGN, EXCEL, SCAN
                'page_count': int,
                'dimensions': {
                    'width': float,
                    'height': float,
                    'unit': 'pt'
                },
                'dimensions_mm': {
                    'width': float,
                    'height': float,
                    'unit': 'mm'
                },
                'is_multi_size': bool,
                'raw_metadata': dict,
                'confidence': str,
                'reason': str
            }
        """
        file_path = Path(file_path)

        logger.info("=" * 60)
        logger.info("[Stage A] 入口処理開始（書類の判断）")
        logger.info(f"  ├─ ファイル: {file_path.name}")
        logger.info("=" * 60)

        # ファイル存在確認
        if not file_path.exists():
            logger.error(f"[Stage A] ファイルが存在しません: {file_path}")
            return self._error_result(f"File not found: {file_path}")

        if file_path.suffix.lower() != '.pdf':
            logger.error(f"[Stage A] PDFファイルではありません: {file_path}")
            return self._error_result(f"Not a PDF file: {file_path}")

        try:
            # A-5: 書類種類判断
            logger.info("[Stage A] A-5: 書類種類判断")
            type_result = self.type_analyzer.analyze(file_path)

            # A-6: サイズ測定
            logger.info("[Stage A] A-6: サイズ測定")
            dimension_result = self.dimension_measurer.measure(file_path)

            # 結果を統合
            result = {
                'success': True,
                'document_type': type_result['document_type'],
                'page_count': dimension_result['page_count'],
                'dimensions': dimension_result['dimensions'],
                'dimensions_mm': dimension_result['dimensions_mm'],
                'is_multi_size': dimension_result['is_multi_size'],
                'raw_metadata': type_result['raw_metadata'],
                'confidence': type_result['confidence'],
                'reason': type_result['reason']
            }

            logger.info("=" * 60)
            logger.info("[Stage A完了] 入口処理結果:")
            logger.info(f"  ├─ 書類種類: {result['document_type']} (信頼度: {result['confidence']})")
            logger.info(f"  ├─ 判定理由: {result['reason']}")
            logger.info(f"  ├─ ページ数: {result['page_count']}")
            logger.info(f"  ├─ サイズ: {result['dimensions']['width']:.2f} x {result['dimensions']['height']:.2f} pt")
            logger.info(f"  │          ({result['dimensions_mm']['width']:.2f} x {result['dimensions_mm']['height']:.2f} mm)")
            logger.info(f"  └─ マルチサイズ: {'はい' if result['is_multi_size'] else 'いいえ'}")
            logger.info("=" * 60)

            return result

        except Exception as e:
            logger.error(f"[Stage A エラー] 処理失敗: {e}", exc_info=True)
            return self._error_result(str(e))

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'success': False,
            'error': error_message,
            'document_type': 'UNKNOWN',
            'page_count': 0,
            'dimensions': {'width': 0, 'height': 0, 'unit': 'pt'},
            'dimensions_mm': {'width': 0, 'height': 0, 'unit': 'mm'},
            'is_multi_size': False,
            'raw_metadata': {},
            'confidence': 'NONE',
            'reason': 'エラーにより判定不能'
        }
