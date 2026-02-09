"""
E-1: Stage E Controller（Orchestrator）

Stage E の各コンポーネントを統合し、視覚抽出（OCR & AI構造化）を実行する。

パイプライン:
E-1: OCR Scouter（文字数測定）
  ↓
E-5: Text Block Visualizer（ブロック認識）
  ↓
分岐判定（文字密度・用途）
  ├─ E-20: Context Extractor（地の文用 - Gemini 2.5 Flash-lite）
  └─ E-30: Table Structure Extractor（表用 - Gemini 2.5 Flash）
"""

from pathlib import Path
from typing import Dict, Any, Optional, List
from loguru import logger

from .e1_ocr_scouter import E1OcrScouter
from .e5_text_block_visualizer import E5TextBlockVisualizer
from .e20_context_extractor import E20ContextExtractor
from .e30_table_structure_extractor import E30TableStructureExtractor


class E1Controller:
    """E-1: Stage E Controller（Orchestrator）"""

    def __init__(
        self,
        gemini_api_key: Optional[str] = None
    ):
        """
        E-1 コントローラー初期化

        Args:
            gemini_api_key: Google AI API Key
        """
        self.scouter = E1OcrScouter()
        self.visualizer = E5TextBlockVisualizer()
        self.context_extractor = E20ContextExtractor(api_key=gemini_api_key)
        self.table_extractor = E30TableStructureExtractor(api_key=gemini_api_key)

    def process(
        self,
        stage_d_result: Dict[str, Any],
        gemini_api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Stage E 視覚抽出を実行

        Args:
            stage_d_result: Stage D の実行結果
            gemini_api_key: Google AI API Key（オプション）

        Returns:
            {
                'success': bool,
                'non_table_content': dict,  # 地の文の抽出結果
                'table_contents': [dict],   # 表の抽出結果
                'metadata': {
                    'total_tokens': int,
                    'models_used': [str]
                }
            }
        """
        logger.info("=" * 60)
        logger.info("[E-1] Stage E 視覚抽出開始")
        logger.info("=" * 60)

        try:
            # API Key を更新（必要な場合）
            if gemini_api_key:
                self.context_extractor.api_key = gemini_api_key
                self.table_extractor.api_key = gemini_api_key

            total_tokens = 0
            models_used = []

            # 非表領域の処理
            non_table_content = {}
            non_table_image = stage_d_result.get('non_table_image_path')

            if non_table_image and Path(non_table_image).exists():
                logger.info("\n[E-1] ステップ1: 非表領域の処理")
                non_table_content = self._process_non_table(
                    Path(non_table_image)
                )
                if non_table_content.get('success'):
                    total_tokens += non_table_content.get('tokens_used', 0)
                    model = non_table_content.get('model_used')
                    if model and model not in models_used:
                        models_used.append(model)
            else:
                logger.info("\n[E-1] ステップ1: 非表領域の処理（スキップ: 画像なし）")

            # 表領域の処理
            table_contents = []
            tables = stage_d_result.get('tables', [])

            if tables:
                logger.info(f"\n[E-1] ステップ2: 表領域の処理（{len(tables)}個）")
                for table in tables:
                    table_result = self._process_table(table)
                    if table_result.get('success'):
                        table_contents.append(table_result)
                        total_tokens += table_result.get('tokens_used', 0)
                        model = table_result.get('model_used')
                        if model and model not in models_used:
                            models_used.append(model)
            else:
                logger.info("\n[E-1] ステップ2: 表領域の処理（スキップ: 表なし）")

            logger.info("=" * 60)
            logger.info("[E-1] Stage E 完了")
            logger.info(f"  ├─ 非表領域: {'処理済み' if non_table_content else '未処理'}")
            logger.info(f"  ├─ 表領域: {len(table_contents)}個")
            logger.info(f"  ├─ 総トークン: 約{total_tokens}")
            logger.info(f"  └─ 使用モデル: {', '.join(models_used)}")
            logger.info("=" * 60)

            return {
                'success': True,
                'non_table_content': non_table_content,
                'table_contents': table_contents,
                'metadata': {
                    'total_tokens': total_tokens,
                    'models_used': models_used
                }
            }

        except Exception as e:
            logger.error(f"[E-1] 処理エラー: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'non_table_content': {},
                'table_contents': [],
                'metadata': {}
            }

    def _process_non_table(
        self,
        image_path: Path
    ) -> Dict[str, Any]:
        """
        非表領域（地の文）を処理

        Args:
            image_path: 非表領域画像パス

        Returns:
            処理結果
        """
        logger.info(f"[E-1] 非表領域処理: {image_path.name}")

        # Step 1: OCR Scouter（文字数測定）
        scout_result = self.scouter.scout(image_path)

        if scout_result.get('should_skip'):
            logger.info("[E-1] 文字数が少ないためスキップ")
            return {
                'success': False,
                'skip_reason': 'low_char_count',
                'scout_result': scout_result
            }

        # Step 2: Text Block Visualizer（ブロック認識）
        block_result = self.visualizer.detect_blocks(
            image_path,
            scout_result.get('extracted_text')
        )

        # ブロックヒントを生成
        block_hint = self.visualizer.generate_prompt_hint(
            block_result.get('blocks', [])
        )

        # Step 3: Context Extractor（Gemini 2.5 Flash-lite）
        extract_result = self.context_extractor.extract(
            image_path,
            block_hint=block_hint
        )

        # 結果を統合
        return {
            **extract_result,
            'scout_result': scout_result,
            'block_result': block_result
        }

    def _process_table(
        self,
        table_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        表領域を処理

        Args:
            table_info: Stage D からの表情報

        Returns:
            処理結果
        """
        table_id = table_info.get('table_id', 'Unknown')
        image_path = Path(table_info.get('image_path', ''))

        if not image_path.exists():
            logger.warning(f"[E-1] 表画像が存在しません: {table_id}")
            return {
                'success': False,
                'table_id': table_id,
                'error': 'Image not found'
            }

        logger.info(f"[E-1] 表処理: {table_id}")

        # Step 1: OCR Scouter（文字数測定）
        scout_result = self.scouter.scout(image_path)

        if scout_result.get('should_skip'):
            logger.info(f"[E-1] {table_id}: 文字数が少ないためスキップ")
            return {
                'success': False,
                'table_id': table_id,
                'skip_reason': 'low_char_count',
                'scout_result': scout_result
            }

        # Step 2: Table Structure Extractor（Gemini 2.5 Flash）
        cell_map = table_info.get('cell_map', [])
        extract_result = self.table_extractor.extract(
            image_path,
            cell_map=cell_map
        )

        # 結果を統合
        return {
            **extract_result,
            'table_id': table_id,
            'scout_result': scout_result
        }
