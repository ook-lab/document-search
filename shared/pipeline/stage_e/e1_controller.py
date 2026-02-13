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
from .e21_non_table_vision_ocr import E21NonTableVisionOcr
from .e30_table_structure_extractor import E30TableStructureExtractor
from .e31_table_vision_ocr import E31TableVisionOcr
from .e32_table_cell_merger import E32TableCellMerger

try:
    import fitz  # PyMuPDF
    from PIL import Image
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logger.warning("[E-1] PyMuPDF/PIL が必要です（ページレンダリング用）")


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
        self.non_table_vision_ocr = E21NonTableVisionOcr()
        self.table_extractor = E30TableStructureExtractor(api_key=gemini_api_key)
        self.table_cell_ocr = E31TableVisionOcr()
        self.table_cell_merger = E32TableCellMerger()

    def process(
        self,
        purged_pdf_path: str,
        stage_d_result: Dict[str, Any],
        output_dir: Optional[Path] = None,
        gemini_api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Stage E 視覚抽出を実行（ページ単位処理）

        Args:
            purged_pdf_path: E が読む PDF（B3 等の purged PDF）
            stage_d_result: Stage D の実行結果（table/non-table 画像）
            output_dir: 作業ディレクトリ（page_*.png 生成先）
            gemini_api_key: Google AI API Key（オプション）

        Returns:
            {
                'success': bool,
                'non_table_content': dict,  # 地の文の抽出結果
                'table_contents': [dict],   # 表の抽出結果
                'page_scout': {             # ページ単位スカウト結果
                    'pages_total': int,
                    'pages_processed': int,
                    'per_page': [dict],
                    'total_words': int,
                    'total_chars': int
                },
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

            # ステップ0: ページ単位スカウト（白紙ページも記録）
            # E は自分の入力PDF だけを見て page_count を確定する
            page_scout = {}

            logger.info("\n[E-1] ステップ0: 全ページスカウト（自力確定）")

            if not purged_pdf_path or not Path(purged_pdf_path).exists():
                logger.error(f"[E-1] purged_pdf_path が無効: {purged_pdf_path}")
                logger.error("[E-1] ページスカウトスキップ")
            elif not PYMUPDF_AVAILABLE:
                logger.error("[E-1] PyMuPDF が利用できません → ページスカウトスキップ")
            else:
                try:
                    # 1) purged_pdf を開いて page_count を確定
                    doc = fitz.open(purged_pdf_path)
                    page_count = len(doc)
                    doc.close()
                    logger.info(f"[E-1] page_count 確定: {page_count}")

                    # 2) purged_images_dir を決定（output_dir 配下）
                    if output_dir:
                        purged_images_dir = Path(output_dir) / "purged_images"
                    else:
                        purged_images_dir = Path(purged_pdf_path).parent / "purged_images"

                    purged_images_dir.mkdir(parents=True, exist_ok=True)
                    logger.info(f"[E-1] purged_images_dir: {purged_images_dir}")

                    # 3) 全ページをレンダリングして page_*.png を揃える
                    logger.info(f"[E-1] 全ページレンダリング開始（{page_count}ページ）")
                    self._render_all_pages(
                        purged_pdf_path,
                        purged_images_dir,
                        page_count
                    )

                    # 4) scout_all_pages を必ず呼ぶ
                    logger.info(f"[E-1] 全ページスカウト実行: {page_count}ページ")
                    page_scout = self.scouter.scout_all_pages(
                        purged_images_dir,
                        page_count
                    )
                    logger.info(f"  ├─ 総ページ数: {page_scout.get('pages_total', 0)}")
                    logger.info(f"  ├─ 処理済み: {page_scout.get('pages_processed', 0)}")
                    logger.info(f"  └─ 総文字数: {page_scout.get('total_chars', 0)}")

                except Exception as e:
                    logger.error(f"[E-1] ページスカウト準備エラー: {e}", exc_info=True)

            # ページ番号を取得
            page = stage_d_result.get('page_index', 0)

            # 非表領域の処理
            non_table_content = {}
            non_table_image = stage_d_result.get('non_table_image_path')

            if non_table_image and Path(non_table_image).exists():
                logger.info("\n[E-1] ステップ1: 非表領域の処理")
                non_table_content = self._process_non_table(
                    Path(non_table_image),
                    page=page
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
                    # 各表のページ情報を取得（なければデフォルトページを使用）
                    table_page = table.get('page', page)
                    table_result = self._process_table(table, page=table_page)
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
            logger.info(f"  ├─ ページ処理: {page_scout.get('pages_processed', 0)}/{page_scout.get('pages_total', 0)}")
            logger.info(f"  ├─ 総トークン: 約{total_tokens}")
            logger.info(f"  └─ 使用モデル: {', '.join(models_used)}")
            logger.info("=" * 60)

            return {
                'success': True,
                'non_table_content': non_table_content,
                'table_contents': table_contents,
                'page_scout': page_scout,
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
                'page_scout': {},
                'metadata': {}
            }

    def _process_non_table(
        self,
        image_path: Path,
        page: int = 0
    ) -> Dict[str, Any]:
        """
        非表領域（地の文）を処理

        Args:
            image_path: 非表領域画像パス
            page: ページ番号

        Returns:
            処理結果
        """
        logger.info(f"[E-1] 非表領域処理: {image_path.name}, page={page}")

        # Step 1: OCR Scouter（文字数測定）
        # include_words は文字が多い時だけ True（should_skip==False の場合）
        # ここで should_skip を得るため、まず include_words=False で軽量スカウト
        scout_result = self.scouter.scout(image_path, include_words=False)
        should_skip = scout_result.get('should_skip', True)

        # 文字が多い場合のみ単語座標を取得（include_words=True で再スカウト）
        if not should_skip:
            scout_result = self.scouter.scout(image_path, include_words=True)

        words = scout_result.get('words', [])

        # Step 2: Text Block Visualizer（ブロック認識）常時実行
        block_result = self.visualizer.detect_blocks(
            image_path,
            scout_result.get('extracted_text', '')
        )
        blocks = block_result.get('blocks', [])
        block_hint = self.visualizer.generate_prompt_hint(blocks)

        # Step 3: E-21 Vision OCR（条件付き：文字が多い時だけ）
        vision_text = ""
        if not should_skip:
            logger.info("[E-1] E-21 非表 Vision OCR 実行")
            vision_text = self.non_table_vision_ocr.extract_text(image_path)
        else:
            logger.info("[E-1] E-21 非表 Vision OCR スキップ（文字少）→ E-22 は画像のみで続行")

        # Step 4: E-22 Context Extractor（常時実行）
        extract_result = self.context_extractor.extract(
            image_path,
            page=page,
            words=words,
            blocks=blocks,
            block_hint=block_hint,
            vision_text=vision_text if vision_text else None
        )

        # 結果を統合
        return {
            **extract_result,
            'scout_result': scout_result,
            'block_result': block_result
        }

    def _render_all_pages(
        self,
        purged_pdf_path: str,
        purged_images_dir: Path,
        page_count: int
    ) -> None:
        """
        全ページをレンダリングして page_*.png を生成

        Args:
            purged_pdf_path: purged PDF のパス
            purged_images_dir: purged_images ディレクトリ
            page_count: 総ページ数
        """
        try:
            doc = fitz.open(purged_pdf_path)

            for page_idx in range(page_count):
                page_img_path = purged_images_dir / f"e1_page_{page_idx}.png"

                # 既存の画像は上書きせずスキップ（高速化）
                if page_img_path.exists():
                    logger.debug(f"[E-1] e1_page_{page_idx}.png 既存（スキップ）")
                    continue

                # ページをレンダリング
                page = doc.load_page(page_idx)
                pix = page.get_pixmap(dpi=150)  # 150 DPI でレンダリング

                # PNG として保存
                pix.save(str(page_img_path))
                logger.info(f"[E-1] e1_page_{page_idx}.png 生成完了")

            doc.close()

        except Exception as e:
            logger.error(f"[E-1] ページレンダリングエラー: {e}", exc_info=True)
            raise

    def _process_table(
        self,
        table_info: Dict[str, Any],
        page: int = 0
    ) -> Dict[str, Any]:
        """
        表領域を処理（E-30 → E-31 → E-32 固定順）

        構造→値の依存順を守る：
          Step 1: E-30 構造抽出（セルbbox確定）
          Step 2: E-31 セルOCR（構造に従ってセルcrop → Vision API）
          Step 3: E-32 合成（構造 + OCR テキスト → 完成表）

        Args:
            table_info: Stage D からの表情報
            page: ページ番号

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

        logger.info(f"[E-1] 表処理: {table_id}, page={page}")

        cell_map = table_info.get('cell_map', [])
        table_idx = table_info.get('table_index')

        # Step 1: E-30 構造抽出（常時）
        logger.info(f"[E-1] {table_id}: Step1 E-30 構造抽出")
        struct_result = self.table_extractor.extract_structure(
            image_path,
            cell_map=cell_map,
            page_index=page,
            table_index=table_idx
        )

        if not struct_result.get('success'):
            return {
                'success': False,
                'table_id': table_id,
                'error': struct_result.get('error', 'E-30 failed')
            }

        # Step 2: E-31 セルOCR（構造が確定した後に実行）
        logger.info(f"[E-1] {table_id}: Step2 E-31 セルOCR")
        ocr_result = self.table_cell_ocr.extract_cells(
            image_path,
            struct_result.get('cells', [])
        )

        # Step 3: E-32 合成（常時）
        logger.info(f"[E-1] {table_id}: Step3 E-32 合成")
        merged = self.table_cell_merger.merge(struct_result, ocr_result)

        # Stage D の table_id を最終 ID として設定
        merged['table_id'] = table_id
        return merged
