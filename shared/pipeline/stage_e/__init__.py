"""
Stage E: Vision Extraction & AI Structuring（視覚抽出・AI構造化）

パイプライン（非表）:
  E-20（Vision OCR）→ E-21（Context Extractor - Gemini Flash-lite）

パイプライン（表）:
  E-30（構造専用 - Gemini Flash）→ E-31（セルOCR - Vision API）→ E-32（合成）
"""

from .e1_ocr_scouter import E1OcrScouter
from .e5_text_block_visualizer import E5TextBlockVisualizer
from .e20_non_table_vision_ocr import E20NonTableVisionOcr
from .e21_context_extractor import E21ContextExtractor
from .e30_table_structure_extractor import E30TableStructureExtractor
from .e31_table_vision_ocr import E31TableVisionOcr
from .e32_table_cell_merger import E32TableCellMerger


class E1Controller:
    """Stage E チェーン（旧 Controller）"""

    def __init__(self, gemini_api_key=None):
        """
        チェーン構築: 表処理 E-30 → E-31 → E-32

        Args:
            gemini_api_key: Google AI API Key
        """
        self.scouter = E1OcrScouter()
        self.visualizer = E5TextBlockVisualizer()
        self.non_table_vision_ocr = E20NonTableVisionOcr()
        self.context_extractor = E21ContextExtractor(api_key=gemini_api_key)

        # ★表処理チェーン: E-32 → E-31 → E-30
        table_cell_merger = E32TableCellMerger()
        table_cell_ocr = E31TableVisionOcr(next_stage=table_cell_merger)
        self.table_extractor = E30TableStructureExtractor(
            api_key=gemini_api_key,
            next_stage=table_cell_ocr
        )

    def process(
        self,
        purged_pdf_path,
        stage_d_result,
        output_dir=None,
        gemini_api_key=None
    ):
        """
        Stage E 処理実行（簡略版: 表処理のみチェーン化）

        Args:
            purged_pdf_path: purged PDF パス
            stage_d_result: Stage D の結果
            output_dir: 出力ディレクトリ
            gemini_api_key: API Key（オプション）

        Returns:
            Stage E の処理結果
        """
        from loguru import logger
        from pathlib import Path

        logger.info("=" * 60)
        logger.info("[E-1] Stage E 視覚抽出開始（チェーン）")
        logger.info("=" * 60)

        # 簡略版: 非表と表の基本処理のみ
        page = stage_d_result.get('page_index', 0)
        non_table_content = {}
        table_contents = []

        # 非表領域処理（チェーン化なし）
        non_table_image = stage_d_result.get('non_table_image_path')
        if non_table_image and Path(non_table_image).exists():
            scout_result = self.scouter.scout(Path(non_table_image), include_words=False)
            if scout_result.get('char_count', 0) > 0:
                non_table_content = self.context_extractor.extract(
                    Path(non_table_image),
                    page=page,
                    words=[],
                    blocks=[],
                    block_hint="",
                    vision_text=None
                )

        # 表領域処理（チェーン化済み）
        tables = stage_d_result.get('tables', [])

        logger.info("=" * 80)
        logger.info(f"[E-1] 表領域処理開始: {len(tables)}個の表")
        logger.info("=" * 80)

        for idx, table in enumerate(tables, 1):
            table_id = table.get('table_id', 'Unknown')
            image_path = Path(table.get('image_path', ''))

            logger.info(f"[E-1] 表 {idx}/{len(tables)}: {table_id}")
            logger.info(f"[E-1]   ├─ 画像: {image_path.name if image_path.exists() else '存在しない'}")

            if not image_path.exists():
                logger.warning(f"[E-1]   └─ 画像が存在しないためスキップ")
                continue

            # ★表の文字数測定
            logger.info(f"[E-1]   ├─ 文字数測定開始: {image_path.name}")
            scout_result = self.scouter.scout(image_path, include_words=False)
            char_count = scout_result.get('char_count', 0)
            density = scout_result.get('density', 'none')
            should_skip = scout_result.get('skip', True)

            logger.info(f"[E-1]   ├─ 文字数: {char_count}文字")
            logger.info(f"[E-1]   ├─ 密度: {density}")
            logger.info(f"[E-1]   ├─ スキップ判定: {should_skip}")

            if should_skip:
                logger.info(f"[E-1]   └─ 文字数不足のためスキップ")
                continue

            logger.info(f"[E-1]   └─ チェーン開始: E-30 → E-31 → E-32")
            # ★チェーン開始: E-30 → E-31 → E-32
            result = self.table_extractor.extract_structure(
                image_path,
                cell_map=table.get('cell_map', []),
                page_index=page,
                table_index=table.get('table_index')
            )
            result['table_id'] = table_id
            table_contents.append(result)

        logger.info("=" * 60)
        logger.info("[E-1] Stage E 完了（チェーン）")
        logger.info("=" * 60)

        return {
            'success': True,
            'non_table_content': non_table_content,
            'table_contents': table_contents,
            'page_scout': {},
            'metadata': {'total_tokens': 0, 'models_used': []}
        }


__all__ = [
    'E1Controller',
    'E1OcrScouter',
    'E5TextBlockVisualizer',
    'E20NonTableVisionOcr',
    'E21ContextExtractor',
    'E30TableStructureExtractor',
    'E31TableVisionOcr',
    'E32TableCellMerger',
]
