"""
Stage F: Dual-Vision Analysis (独立読解 11段階)

【設計 2026-01-31】Ver 6.5 AI/PROGRAM完全分離モード

核心: 「FはEの答えを知らない状態で、ゼロから視覚情報を暴き出す」

============================================
F-1〜F-5: 構造の下地作り（AIなし、Surya + スクリプト）
  - F-1: Image Normalization (正規化)
  - F-2: Surya Block Detection (領域検出)
  - F-3: Coordinate Quantization (座標量子化) ← トークン削減の肝
  - F-4: Logical Reading Order (読む順序の確定)
  - F-5: Block Classification (構造ラベル付与)

F-6〜F-10: 独立・二重読解（AIの本番）
  - F-6: Blind Prompting (プロンプト注入) ← Stage E結果を遮断
  - F-7: Text Extraction (AI) ← 文字+座標のみ出力、ID判断禁止
  - F-7.5: Coordinate Mapping (PROGRAM) ← 座標距離でID紐付け
  - F-8: Dual Read - Path B (視覚の鬼 / 2.5 Flash)
  - F-9: Physical Sorting + Address Tagging (PROGRAM)
  - F-9.5: AI Rescue for Ambiguous Data (2.5 Flash-Lite)
  - F-10: Stage E Scrubbing (正本化)

【Ver 6.5 変更点】
  - F-7 (AI): 画像内の全文字を座標付きで出力（ID判断は一切させない）
  - F-7.5 (PROGRAM): Surya座標との最短距離でID紐付け（閾値超えはnull）
  - F-8 後行: 構造解析でヘッダー座標を確定
  - F-9 (PROGRAM): Surya bbox + F-8ヘッダーで物理仕分け + 住所タグ付け
  - F-9.5: 低信頼度データのみ AI レスキュー
  - F-10: Stage E の正確なテキストで洗い替え
============================================
"""
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger
from PIL import Image

from shared.ai.llm_client.llm_client import LLMClient
from shared.ai.llm_client.exceptions import MaxTokensExceededError

# 【Ver 7.0】Vision API OCR パイプライン
try:
    from .stage_f7_to_f10 import F7toF10Pipeline, VISION_API_AVAILABLE
    VISION_PIPELINE_AVAILABLE = VISION_API_AVAILABLE
except ImportError:
    VISION_PIPELINE_AVAILABLE = False
    logger.warning("[Stage F] Vision API pipeline not available")

from .constants import (
    STAGE_F_OUTPUT_SCHEMA_VERSION,
    F1_TARGET_DPI,
    SURYA_MAX_DIM,
    QUANTIZE_GRID_SIZE,
    F7_MODEL_IMAGE,
    F7_MODEL_AV,
    F8_MODEL,
    F95_MODEL,
    F10_MODEL,
    F7_F8_MAX_TOKENS,
    F7_F8_TEMPERATURE,
    CHUNK_SIZE_PAGES,
)

# Surya のインポート（オプショナル）
try:
    from surya.detection import DetectionPredictor
    SURYA_AVAILABLE = True
except ImportError:
    SURYA_AVAILABLE = False
    logger.warning("[Stage F] Surya not installed - F-2 will be skipped")


class StageFVisualAnalyzer:
    """Stage F: 独立読解 10段階（Dual-Vision Analysis）"""

    def __init__(self, llm_client: LLMClient, enable_surya: bool = True, use_vision_api_ocr: bool = True):
        """
        Args:
            llm_client: LLMクライアント
            enable_surya: Suryaを有効化（デフォルト: True）
            use_vision_api_ocr: 【Ver 7.0】Vision API OCRを使用（デフォルト: True）
                               Trueの場合、F7〜F10で検出OCR（Vision API）を使用
                               Falseの場合、従来のLLM OCRを使用
        """
        self.llm_client = llm_client
        self.enable_surya = enable_surya and SURYA_AVAILABLE

        # 【Ver 7.0】Vision API OCR モード
        self.use_vision_api_ocr = use_vision_api_ocr and VISION_PIPELINE_AVAILABLE
        if self.use_vision_api_ocr:
            self._vision_pipeline = F7toF10Pipeline(llm_client)
            logger.info("[Stage F] Vision API OCR mode enabled (Ver 7.0)")
        else:
            self._vision_pipeline = None
            logger.info("[Stage F] Legacy LLM OCR mode")

        # Surya detector (lazy loading)
        self._surya_detector = None

        # トークン使用量の収集用
        self._f7_usage: List[Dict[str, Any]] = []
        self._f8_usage: List[Dict[str, Any]] = []

        # 列境界情報の保存用（F-3.5で検出、F-7で使用）
        # {page_idx: [0, 250, 500, 750, 1000]} の形式
        self._page_column_boundaries: Dict[int, List[int]] = {}

    @property
    def surya_detector(self):
        """Surya detector の遅延初期化"""
        if self._surya_detector is None and self.enable_surya:
            try:
                self._surya_detector = DetectionPredictor()
                logger.info("[Stage F] Surya detector initialized")
            except Exception as e:
                logger.warning(f"[Stage F] Surya initialization failed: {e}")
                self.enable_surya = False
        return self._surya_detector

    def process(
        self,
        file_path: Optional[Path],
        mime_type: str,
        requires_vision: bool = False,
        requires_transcription: bool = False,
        post_body: Optional[Dict[str, Any]] = None,
        progress_callback=None,
        # 以下: pipeline.py から渡される追加引数（2026-01-28 統合）
        prompt: str = None,
        model: str = None,
        extracted_text: str = None,
        workspace: str = None,
        e2_table_bboxes: List[Dict] = None,
        stage_e_metadata: Dict[str, Any] = None  # 【Ver 6.4】座標付き文字情報
    ) -> Dict[str, Any]:
        """
        Stage F メイン処理（10ステップ）

        Args:
            file_path: ファイルパス（添付なしの場合はNone）
            mime_type: MIMEタイプ
            requires_vision: Vision処理が必要か（Stage Eから）
            requires_transcription: 音声書き起こしが必要か（Stage Eから）
            post_body: 投稿本文
            progress_callback: 進捗コールバック
            prompt: YAMLから読み込んだプロンプト（F7/F8で使用）
            model: YAMLから読み込んだモデル（F7/F8で使用）
            extracted_text: Stage Eで抽出済みのテキスト
            workspace: ワークスペース
            e2_table_bboxes: Stage Eで検出した表のbbox座標

        Returns:
            Stage F 出力（Stage G への入力）
        """
        total_start = time.time()

        # トークン使用量をリセット
        self._f7_usage = []
        self._f8_usage = []

        # 【Ver 6.4】Stage E の座標付き文字情報を保存（F-10で使用）
        self._e_content = extracted_text or ''
        self._e_physical_chars = []
        if stage_e_metadata:
            self._e_physical_chars = stage_e_metadata.get('physical_chars', [])
            if self._e_physical_chars:
                logger.info(f"  ├─ Stage E物理証拠: {len(self._e_physical_chars)}文字")

        # 予備知識を保存（プロンプトで使用、Stage E内容は排除）
        self._file_name = file_path.name if file_path else None
        self._doc_type = self._infer_doc_type(mime_type, file_path)
        self._workspace = workspace

        logger.info("=" * 60)
        logger.info("[Stage F] 独立読解 10段階 開始")
        logger.info(f"  ├─ ファイル: {file_path.name if file_path else 'なし'}")
        logger.info(f"  ├─ MIMEタイプ: {mime_type}")
        logger.info(f"  ├─ requires_vision: {requires_vision}")
        logger.info(f"  └─ requires_transcription: {requires_transcription}")
        logger.info("=" * 60)

        # 添付なし or 処理不要 → 空のpayloadを返す
        if file_path is None or (not requires_vision and not requires_transcription):
            logger.info("[Stage F] スキップ（添付なし or 処理不要）")
            return self._create_empty_payload(post_body)

        # ファイル存在確認
        if not file_path.exists():
            logger.error(f"[Stage F] ファイルが存在しません: {file_path}")
            return self._create_empty_payload(post_body, error=f"File not found: {file_path}")

        # メディアタイプ判定
        is_image = mime_type.startswith('image/') if mime_type else False
        is_audio = mime_type.startswith('audio/') if mime_type else False
        is_video = mime_type.startswith('video/') if mime_type else False
        is_document = mime_type in {'application/pdf'} or mime_type.startswith('text/')

        try:
            # ============================================
            # 音声/動画: F-7 のみ実行（Transcription特化）
            # ============================================
            if is_audio or is_video:
                return self._process_audio_video(
                    file_path, mime_type, is_video, post_body, progress_callback
                )

            # ============================================
            # 画像/PDF: F-1〜F-10 フル実行
            # ============================================
            return self._process_image_document(
                file_path, mime_type, is_document, post_body, progress_callback
            )

        except Exception as e:
            logger.error(f"[Stage F] 処理エラー: {e}", exc_info=True)
            return self._create_empty_payload(post_body, error=str(e))

        finally:
            total_elapsed = time.time() - total_start
            logger.info(f"[Stage F] 総処理時間: {total_elapsed:.2f}秒")

    def _process_audio_video(
        self,
        file_path: Path,
        mime_type: str,
        is_video: bool,
        post_body: Optional[Dict],
        progress_callback
    ) -> Dict[str, Any]:
        """音声/動画処理（F-7のみ）"""
        logger.info("[Stage F] 音声/動画モード → F-7 Transcription のみ実行")

        if progress_callback:
            progress_callback("F-7")

        # F-7: Transcription（gemini-2.5-flash-lite）
        f7_result = self._f7_transcription(file_path, mime_type, is_video)

        # F-9: 結果集約（音声/動画は F-7 のみ）
        if progress_callback:
            progress_callback("F-9")

        return {
            "schema_version": STAGE_F_OUTPUT_SCHEMA_VERSION,
            "post_body": post_body or {},
            "path_a_result": f7_result,
            "path_b_result": {},  # 音声/動画は Path B なし
            "media_type": "video" if is_video else "audio",
            "processing_mode": "transcription_only",
            "warnings": []
        }

    def _process_image_document(
        self,
        file_path: Path,
        mime_type: str,
        is_document: bool,
        post_body: Optional[Dict],
        progress_callback
    ) -> Dict[str, Any]:
        """
        画像/ドキュメント処理（F-1〜F-10）

        【チャンク処理】
        MAX_TOKENSエラー回避のため、5ページごとに分割処理を行う。
        これにより100ページ超のPDFでも安定して処理可能。
        """

        # ============================================
        # F-1: Image Normalization（全ページ）
        # ============================================
        if progress_callback:
            progress_callback("F-1")
        all_page_images = self._f1_normalize(file_path, is_document)
        total_pages = len(all_page_images)

        logger.info(f"[Stage F] 総ページ数: {total_pages}, チャンクサイズ: {CHUNK_SIZE_PAGES}")

        # ============================================
        # 5ページごとのチャンクに分割
        # ============================================
        page_chunks = [
            all_page_images[i:i + CHUNK_SIZE_PAGES]
            for i in range(0, total_pages, CHUNK_SIZE_PAGES)
        ]
        total_chunks = len(page_chunks)

        logger.info(f"[Stage F] チャンク数: {total_chunks}")

        # チャンク処理結果の蓄積用
        aggregated_full_texts = []
        aggregated_blocks = []
        aggregated_tables = []
        aggregated_diagrams = []
        aggregated_charts = []
        aggregated_structured_candidates = []
        chunk_warnings = []

        # ============================================
        # チャンクごとに F-2〜F-8 を実行
        # ============================================
        for chunk_idx, chunk_pages in enumerate(page_chunks):
            chunk_start_page = chunk_idx * CHUNK_SIZE_PAGES
            chunk_end_page = chunk_start_page + len(chunk_pages) - 1

            logger.info("=" * 50)
            logger.info(f"[Stage F] チャンク {chunk_idx + 1}/{total_chunks} 処理中")
            logger.info(f"  ├─ ページ範囲: {chunk_start_page + 1}〜{chunk_end_page + 1}")
            logger.info(f"  └─ ページ数: {len(chunk_pages)}")

            # F-2: Surya Block Detection（このチャンクのみ）
            if progress_callback:
                progress_callback(f"F-2 ({chunk_idx + 1}/{total_chunks})")
            surya_blocks = self._f2_detect_blocks(chunk_pages)

            # F-3: Coordinate Quantization
            if progress_callback:
                progress_callback(f"F-3 ({chunk_idx + 1}/{total_chunks})")
            quantized_blocks = self._f3_quantize(surya_blocks, chunk_pages)

            # F-3.5: Intelligent Filtering & Column Detection（トークン削減の肝）
            if progress_callback:
                progress_callback(f"F-3.5 ({chunk_idx + 1}/{total_chunks})")
            filtered_blocks = self._f35_filter_and_columnize(quantized_blocks)

            # F-4: Logical Reading Order
            if progress_callback:
                progress_callback(f"F-4 ({chunk_idx + 1}/{total_chunks})")
            ordered_blocks = self._f4_reading_order(filtered_blocks)

            # F-5: Block Classification
            if progress_callback:
                progress_callback(f"F-5 ({chunk_idx + 1}/{total_chunks})")
            classified_blocks = self._f5_classify(ordered_blocks)

            # F-6: ID焼き込み画像生成（Ver 4.0 - 座標はAIに渡さない）
            if progress_callback:
                progress_callback(f"F-6 ({chunk_idx + 1}/{total_chunks})")

            # IDマッピングをシステム内部に保持（AIには渡さない）
            id_mapping = self._f6_store_id_mapping(classified_blocks)
            self._id_mapping = id_mapping  # インスタンス変数に保存

            # ============================================
            # 【Ver 7.0】F7のみVision API OCR、F8以降は従来のLLM
            # ============================================
            path_a_result = {}
            path_b_result = {}
            f8_headers = {'x_headers': [], 'y_headers': [], 'header_coords': {}}

            # このチャンク（ページ）の列境界情報を取得
            page_column_boundaries = self._page_column_boundaries.get(chunk_start_page, [0, QUANTIZE_GRID_SIZE])

            # ============================================
            # F-7: OCR（Vision API または LLM）
            # ============================================
            if progress_callback:
                progress_callback(f"F-7 ({chunk_idx + 1}/{total_chunks})")

            if self.use_vision_api_ocr and self._vision_pipeline:
                # 【Ver 7.0】Vision API OCR（LLMによるOCRは行わない）
                # 修正①：'image'キーを使用（pil_imageではない）
                # 修正②：画像なしはエラー（スキップ禁止）
                if not chunk_pages or 'image' not in chunk_pages[0]:
                    raise RuntimeError(f"[F-7] チャンク{chunk_idx + 1} 画像がありません - OCR不可")

                try:
                    from io import BytesIO
                    pil_img = chunk_pages[0]['image']  # 修正①：'image'キー
                    img_bytes = BytesIO()
                    pil_img.save(img_bytes, format='PNG')
                    img_bytes.seek(0)

                    # 一時ファイルに保存してVision API実行
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                        tmp.write(img_bytes.read())
                        tmp_path = Path(tmp.name)

                    try:
                        # F7のみ実行（Vision API OCR）
                        from .vision_api_extractor import VisionAPIExtractor
                        extractor = VisionAPIExtractor()
                        f7_result = extractor.extract_with_document_detection(
                            tmp_path, pil_img.width, pil_img.height
                        )

                        # 結果を既存形式に変換
                        path_a_result = self._convert_f7_vision_result(
                            f7_result, classified_blocks, chunk_idx
                        )
                        logger.info(f"[F-7] チャンク{chunk_idx + 1} Vision API OCR完了: {len(f7_result.get('tokens', []))}tokens")

                    finally:
                        tmp_path.unlink(missing_ok=True)

                except Exception as e:
                    logger.error(f"[F-7] チャンク{chunk_idx + 1} Vision APIエラー: {e}")
                    chunk_warnings.append(f"CHUNK_{chunk_idx}_F7_VISION_ERROR: {str(e)}")
                    raise  # 修正②：エラーを上位に伝播（フォールバックしない）

            else:
                # 従来モード: LLM OCR
                try:
                    path_a_result = self._f7_path_a_chunk_extraction_v62(
                        chunk_pages, classified_blocks, chunk_idx, chunk_start_page,
                        page_column_boundaries
                    )
                    logger.info(f"[F-7] チャンク{chunk_idx + 1} LLM OCR完了")

                except MaxTokensExceededError as mte:
                    logger.error(f"[F-7] チャンク{chunk_idx + 1} MAX_TOKENS到達")
                    chunk_warnings.append(f"CHUNK_{chunk_idx}_F7_MAX_TOKENS: {len(mte.partial_output)}文字で切断")
                    try:
                        import json_repair
                        path_a_result = json_repair.repair_json(mte.partial_output, return_objects=True)
                        if isinstance(path_a_result, dict):
                            path_a_result['_max_tokens_partial'] = True
                    except Exception as parse_err:
                        path_a_result = {"error": str(mte), "_max_tokens_partial": True}

                except Exception as e:
                    logger.error(f"[F-7] チャンク{chunk_idx + 1} エラー: {e}")
                    chunk_warnings.append(f"CHUNK_{chunk_idx}_F7_ERROR: {str(e)}")

            # ============================================
            # F-8: LLM構造解析（F7成功時のみ実行）
            # 修正③：F7が失敗した場合はF8を実行しない
            # ============================================
            if path_a_result and not path_a_result.get("error"):
                if progress_callback:
                    progress_callback(f"F-8 ({chunk_idx + 1}/{total_chunks})")

                try:
                    path_b_result = self._f8_path_b_chunk_analysis(
                        chunk_pages, classified_blocks, chunk_idx, chunk_start_page
                    )
                    logger.info(f"[F-8] チャンク{chunk_idx + 1} 構造解析完了")

                    f8_headers = self._extract_headers_from_f8(path_b_result)
                    logger.info(f"[F-8] ヘッダー情報: X={len(f8_headers.get('x_headers', []))}個, Y={len(f8_headers.get('y_headers', []))}個")

                except Exception as e:
                    logger.error(f"[F-8] チャンク{chunk_idx + 1} エラー: {e}")
                    chunk_warnings.append(f"CHUNK_{chunk_idx}_F8_ERROR: {str(e)}")
                    f8_headers = {'x_headers': [], 'y_headers': [], 'header_coords': {}}
            else:
                logger.warning(f"[F-8] チャンク{chunk_idx + 1} F7が失敗のためスキップ")
                path_b_result = {}
                f8_headers = {'x_headers': [], 'y_headers': [], 'header_coords': {}}

            # チャンク結果を蓄積
            chunk_text = path_a_result.get("full_text_ordered", "")
            aggregated_full_texts.append(chunk_text)
            logger.info(f"[F-9蓄積] チャンク{chunk_idx + 1}: {len(chunk_text)}文字を蓄積 (累計{sum(len(t) for t in aggregated_full_texts)}文字)")

            # 【Ver 6.4】F-7の生テキスト + 座標情報を蓄積（F-9でマッピング）
            raw_texts = path_a_result.get("raw_texts", [])
            block_coords = path_a_result.get("block_coords", {})
            for item in raw_texts:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    block_id = str(item[0]).strip()
                    text = str(item[1]).strip()
                    coords = block_coords.get(block_id, {})
                    aggregated_blocks.append({
                        "block_id": block_id,
                        "text": text,
                        "chunk_idx": chunk_idx,
                        "original_page": chunk_start_page,
                        "coords": coords
                    })

            # 表データをマージして蓄積
            merged_tables = self._merge_chunk_tables(path_a_result, path_b_result, chunk_idx, chunk_start_page)
            aggregated_tables.extend(merged_tables)

            # 構造化データ候補を蓄積
            for candidate in path_b_result.get("structured_data_candidates", []):
                candidate["chunk_idx"] = chunk_idx
                aggregated_structured_candidates.append(candidate)

            # ダイアグラム・チャートを蓄積
            aggregated_diagrams.extend(path_b_result.get("diagrams", []))
            aggregated_charts.extend(path_b_result.get("charts", []))

            logger.info(f"[Stage F] チャンク{chunk_idx + 1} 完了: テキスト{len(path_a_result.get('full_text_ordered', ''))}文字, 表{len(merged_tables)}件")

        # ============================================
        # 【Ver 6.2】F-9: 物理仕分け + 住所タグの幾何学的割当
        # ============================================
        if progress_callback:
            progress_callback("F-9")

        logger.info("=" * 50)
        logger.info("[F-9] 【Ver 6.2】物理仕分け + 住所タグ割当開始")

        # F-8 からヘッダー座標を収集（最後のチャンクの結果を使用）
        f8_headers = self._extract_headers_from_f8(path_b_result) if path_b_result else {}

        # F-9: 物理仕分け + 住所タグ
        f9_result, low_confidence_items = self._f9_physical_sorting(
            aggregated_blocks,
            aggregated_tables,
            f8_headers,
            aggregated_structured_candidates
        )

        logger.info(f"[F-9] 物理仕分け完了: {len(f9_result.get('tagged_texts', []))}テキスト, 低信頼度{len(low_confidence_items)}件")

        # ============================================
        # 【Ver 6.2】F-9.5: 低信頼度住所のAIレスキュー
        # ============================================
        if low_confidence_items and progress_callback:
            progress_callback("F-9.5")

        rescued_count = 0
        if low_confidence_items:
            logger.info(f"[F-9.5] 【Ver 6.2】AIレスキュー開始: {len(low_confidence_items)}件")

            rescued_items = self._f95_ai_rescue(
                low_confidence_items,
                f8_headers,
                chunk_pages[0]['image'] if chunk_pages else None
            )
            rescued_count = len(rescued_items)

            # 救済されたアイテムをマージ
            f9_result['tagged_texts'].extend(rescued_items)

            logger.info(f"[F-9.5] AIレスキュー完了: {rescued_count}件 (IDs: {[i.get('id', '?') for i in rescued_items[:5]]}...)")

        # 最終蓄積テキストを構築
        final_full_text = "\n\n".join(aggregated_full_texts)
        logger.info(f"[F-9] 結合テキスト: {len(final_full_text)}文字")

        # 【Ver 6.7】F9の結果をF10に渡す（アンカーはF10の後で作成）
        # ★重要: ここではアンカーを作らない！F10で浄化した後に作成する
        merged_result = {
            # Ver 6.2: tagged_texts を直接格納
            "tagged_texts": f9_result.get('tagged_texts', []),
            "x_headers": f9_result.get('x_headers', []),
            "y_headers": f9_result.get('y_headers', []),
            "header_coords": f8_headers.get('header_coords', {}),
            # 旧形式: 互換性のため残す（ただしF10で上書きされる）
            "text_source": {
                "full_text": final_full_text,
                "blocks": aggregated_blocks,
                "missed_texts": []
            },
            "tables": aggregated_tables,
            "structured_data_candidates": aggregated_structured_candidates,
            "visual_source": {
                "diagrams": aggregated_diagrams,
                "charts": aggregated_charts,
                "layout": {}
            },
            "metadata": {
                "path_a_model": F7_MODEL_IMAGE,
                "path_b_model": F8_MODEL,
                "table_count": len(aggregated_tables),
                "total_table_rows": sum(t.get("row_count", 0) for t in aggregated_tables),
                "total_pages": total_pages,
                "chunk_count": total_chunks,
                "chunk_size": CHUNK_SIZE_PAGES,
                "f9_physical_count": len(f9_result.get('tagged_texts', [])) - rescued_count,
                "f95_rescued_count": rescued_count
            }
        }

        logger.info(f"[F-9→F-10] F9結果をF10に引き渡し（アンカー作成はF10後）")

        total_text_len = len(merged_result["text_source"]["full_text"])
        logger.info(f"[F-9] 完了: 総テキスト{total_text_len}文字, 物理決定{len(f9_result.get('tagged_texts', [])) - rescued_count}件, AI救済{rescued_count}件")

        # ============================================
        # 【Ver 6.2】F-10: Stage Eによる最終洗い替え
        # ============================================
        if progress_callback:
            progress_callback("F-10")

        # Stage E のテキストを取得（pipeline から渡される）
        e_content = getattr(self, '_e_content', '') or ''

        validated_payload = self._f10_stage_e_scrubbing(merged_result, e_content, post_body)

        # チャンク処理情報を追加
        validated_payload["processing_mode"] = "dual_vision_chunked"
        validated_payload["chunk_info"] = {
            "total_pages": total_pages,
            "chunk_size": CHUNK_SIZE_PAGES,
            "chunk_count": total_chunks
        }
        validated_payload["warnings"].extend(chunk_warnings)

        return validated_payload

    # ============================================
    # F-1: Image Normalization
    # ============================================
    def _f1_normalize(self, file_path: Path, is_document: bool) -> List[Dict]:
        """
        F-1: ページ画像正規化
        PDF → 各ページを300dpiで画像化
        画像 → そのまま読み込み
        """
        f1_start = time.time()
        logger.info("[F-1] Image Normalization 開始")

        page_images = []
        DPI = F1_TARGET_DPI

        file_ext = file_path.suffix.lower()

        if file_ext == '.pdf':
            import fitz  # PyMuPDF
            doc = fitz.open(file_path)
            total_pages = len(doc)
            logger.info(f"  ├─ PDF: {total_pages}ページ")

            for page_num in range(total_pages):
                page = doc[page_num]
                mat = fitz.Matrix(DPI / 72, DPI / 72)
                pix = page.get_pixmap(matrix=mat)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                page_images.append({
                    'page_index': page_num,
                    'image': img,
                    'width': pix.width,
                    'height': pix.height,
                    'dpi': DPI
                })
            doc.close()
        else:
            # 画像ファイル
            img = Image.open(file_path).convert("RGB")
            page_images.append({
                'page_index': 0,
                'image': img,
                'width': img.size[0],
                'height': img.size[1],
                'dpi': DPI
            })

        f1_elapsed = time.time() - f1_start
        logger.info(f"[F-1完了] {len(page_images)}ページ, {f1_elapsed:.2f}秒")

        return page_images

    # ============================================
    # F-2: Surya Block Detection
    # ============================================
    def _f2_detect_blocks(self, page_images: List[Dict]) -> List[Dict]:
        """
        F-2: Suryaブロック検出
        """
        f2_start = time.time()
        surya_blocks = []

        if not self.enable_surya or not page_images:
            logger.info("[F-2] Surya スキップ（無効 or ページなし）")
            return surya_blocks

        logger.info("[F-2] Surya Block Detection 開始")

        for page_data in page_images:
            page_idx = page_data['page_index']
            img = page_data['image']

            # リサイズ（Suryaメモリ対策）
            w, h = img.size
            scale = 1.0
            if max(w, h) > SURYA_MAX_DIM:
                scale = SURYA_MAX_DIM / max(w, h)
                img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)

            # Surya検出
            try:
                detection_results = self.surya_detector([img])
                if detection_results and detection_results[0].bboxes:
                    for block_id, box in enumerate(detection_results[0].bboxes):
                        raw_bbox = box.bbox
                        # 元の座標系に復元
                        restored_bbox = [
                            raw_bbox[0] / scale,
                            raw_bbox[1] / scale,
                            raw_bbox[2] / scale,
                            raw_bbox[3] / scale
                        ]
                        surya_blocks.append({
                            'page': page_idx,
                            'bbox': restored_bbox,
                            'block_id': f"p{page_idx}_b{block_id}",
                            'confidence': getattr(box, 'confidence', 1.0)
                        })
            except Exception as e:
                logger.warning(f"[F-2] Surya検出エラー (page {page_idx}): {e}")

        f2_elapsed = time.time() - f2_start
        logger.info(f"[F-2完了] {len(surya_blocks)}ブロック検出, {f2_elapsed:.2f}秒")

        return surya_blocks

    # ============================================
    # F-3: Coordinate Quantization
    # ============================================
    def _f3_quantize(self, surya_blocks: List[Dict], page_images: List[Dict]) -> List[Dict]:
        """
        F-3: 座標量子化（1000×1000グリッド）
        トークン削減の肝
        """
        f3_start = time.time()
        logger.info("[F-3] Coordinate Quantization 開始")

        page_dims = {p['page_index']: (p['width'], p['height']) for p in page_images}
        quantized = []

        for block in surya_blocks:
            page_idx = block['page']
            bbox = block['bbox']
            w, h = page_dims.get(page_idx, (1000, 1000))

            # 1000×1000 グリッドに量子化
            q_bbox = [
                int(bbox[0] * QUANTIZE_GRID_SIZE / w),
                int(bbox[1] * QUANTIZE_GRID_SIZE / h),
                int(bbox[2] * QUANTIZE_GRID_SIZE / w),
                int(bbox[3] * QUANTIZE_GRID_SIZE / h)
            ]

            quantized.append({
                **block,
                'bbox_original': bbox,
                'bbox': q_bbox,  # 量子化後の座標
                'page_width': w,
                'page_height': h
            })

        f3_elapsed = time.time() - f3_start
        logger.info(f"[F-3完了] {len(quantized)}ブロック量子化, {f3_elapsed:.2f}秒")

        return quantized

    # ============================================
    # F-3.5: 三つの物理的根拠（空白・罫線・密度）による動的分離
    # ============================================

    def _f35_filter_and_columnize(self, blocks: List[Dict]) -> List[Dict]:
        """
        F-3.5: 物理的根拠に基づく動的分離（Ver 3.2）

        【設計原則】
        1. ノイズ除去: 極小ブロックを物理削除（190→90程度）
        2. 分割軸の決定: 空白→罫線→密度の順に、縦OR横の一方を選択
        3. 排他的分割: 網の目状禁止、一方向のスライスのみ
        4. 横方向パッキング: 分割パッチ内で横のみ結合（縦結合禁止）
        5. ヘッダー/インデックス複製: 全パッチに基準列/行を随伴
        """
        import numpy as np

        f35_start = time.time()
        original_count = len(blocks)
        logger.info(f"[F-3.5] 動的分離開始: {original_count}ブロック")

        if not blocks:
            return []

        # ============================================
        # 【Ver 3.5】ページ別に全ブロックを保持（密度分析用）
        # ============================================
        all_pages = {}  # フィルタ前の全ブロック（密度分析用）
        for block in blocks:
            page = block.get('page', 0)
            if page not in all_pages:
                all_pages[page] = []
            all_pages[page].append(block)

        # ============================================
        # Step 1: ノイズブロックの物理削除（出力用）
        # 【Ver 3.5】閾値を大幅緩和 - 小さい文字ブロックも保持
        # ============================================
        MIN_BLOCK_SIZE = 5   # 最小サイズ（量子化後）← 15から緩和
        MIN_BLOCK_AREA = 50  # 最小面積 ← 300から緩和（偏差値「65」等を保持）

        filtered = []
        garbage_count = 0

        for block in blocks:
            bbox = block['bbox']
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            area = w * h

            # 本当のゴミ（点やドット）だけ除外
            if w < MIN_BLOCK_SIZE and h < MIN_BLOCK_SIZE:
                garbage_count += 1
                continue
            if area < MIN_BLOCK_AREA:
                garbage_count += 1
                continue

            filtered.append(block)

        logger.info(f"  ├─ ノイズ除去: {garbage_count}ブロック削除 → 残り{len(filtered)}ブロック")

        # フィルタ後をページ別に整理（後処理用）
        pages = {}
        for block in filtered:
            page = block.get('page', 0)
            if page not in pages:
                pages[page] = []
            pages[page].append(block)

        all_processed = []

        for page_idx in all_pages.keys():
            # フィルタ後のブロック（後処理用）
            page_blocks = pages.get(page_idx, [])
            # フィルタ前の全ブロック（密度分析用）
            all_page_blocks = all_pages[page_idx]

            # ============================================
            # Step 2: 分割軸の決定（全ブロックで密度分析）
            # 【Ver 4.0】F-3.5の決定は「神の宣告」- F-7で上書き不可
            # ============================================
            split_axis, split_positions, split_reason = self._determine_split_axis(all_page_blocks)
            logger.info(f"  ├─ 密度分析: {len(all_page_blocks)}ブロック使用（フィルタ前）")
            logger.info(f"  ├─ ページ{page_idx}: 分割軸={split_axis}, 理由={split_reason}")

            # 【Ver 4.0】神の宣告をインスタンス変数に保存（F-7で参照、上書き禁止）
            self._current_split_axis = split_axis
            self._current_split_positions = split_positions
            self._current_split_reason = split_reason

            # 列境界情報を保存（互換性のため維持）
            if split_axis == 'vertical':
                self._page_column_boundaries[page_idx] = [0] + split_positions + [QUANTIZE_GRID_SIZE]
            else:
                self._page_column_boundaries[page_idx] = [0, QUANTIZE_GRID_SIZE]

            # ============================================
            # Step 3: 排他的分割（縦か横の一方のみ）
            # 【Ver 6.4】ブロック座標死守: どのパスでも全ブロック維持
            # ============================================
            logger.info(f"  ├─ 【Ver 6.4診断】Step 3開始: page_blocks={len(page_blocks)}個, split_axis={split_axis}")

            if split_axis == 'none':
                # 【Ver 6.4】分割なし = 全ブロックそのまま維持（パージ禁止）
                packed = self._horizontal_packing(page_blocks, page_idx)
                logger.info(f"  ├─ 【Ver 6.4診断】split_axis=none: {len(page_blocks)}個 → {len(packed)}個（パージなし）")
                all_processed.extend(packed)

            elif split_axis == 'vertical':
                # 縦分割（左右に分ける）: インデックス列を複製
                split_result = self._vertical_split_with_index(
                    page_blocks, split_positions, page_idx
                )
                logger.info(f"  ├─ 【Ver 6.4診断】split_axis=vertical: {len(page_blocks)}個 → {len(split_result)}個")
                all_processed.extend(split_result)

            else:  # horizontal
                # 横分割（上下に分ける）: ヘッダー行を複製
                split_result = self._horizontal_split_with_header(
                    page_blocks, split_positions, page_idx
                )
                logger.info(f"  ├─ 【Ver 6.4診断】split_axis=horizontal: {len(page_blocks)}個 → {len(split_result)}個")
                all_processed.extend(split_result)

        f35_elapsed = time.time() - f35_start
        reduction_rate = (1 - len(all_processed) / original_count) * 100 if original_count > 0 else 0
        logger.info(f"[F-3.5完了] {original_count} → {len(all_processed)}ブロック "
                    f"({reduction_rate:.1f}%削減), {f35_elapsed:.2f}秒")
        logger.info(f"  └─ 【Ver 6.4確認】ブロック座標死守: {len(all_processed)}個のSuryaブロックを維持")

        return all_processed

    def _determine_split_axis(self, blocks: List[Dict]) -> tuple:
        """
        【Ver 4.0】汎用グリッド制圧エンジン

        設計原則:
        1. 80%ルール: 表専有面積が80%以下なら分割しない
        2. ヘッダー最小化: ヘッダー数が少ない軸で分割
        3. 6本制限: 境界線は両端含め最大6本（5領域）

        Returns:
            (axis, positions, reason)
            axis: 'vertical' | 'horizontal' | 'none'
            positions: 分割位置のリスト（内部境界のみ、両端除く）
            reason: 決定理由
        """
        import numpy as np

        if not blocks:
            return ('none', [], 'ブロックなし')

        # ============================================
        # STEP 0: 表の専有面積を計算（80%ルール）
        # ============================================
        all_x = [b['bbox'][0] for b in blocks] + [b['bbox'][2] for b in blocks]
        all_y = [b['bbox'][1] for b in blocks] + [b['bbox'][3] for b in blocks]

        table_x_min, table_x_max = min(all_x), max(all_x)
        table_y_min, table_y_max = min(all_y), max(all_y)

        table_width = table_x_max - table_x_min
        table_height = table_y_max - table_y_min
        table_area = table_width * table_height
        page_area = QUANTIZE_GRID_SIZE * QUANTIZE_GRID_SIZE

        occupancy = table_area / page_area if page_area > 0 else 0

        logger.debug(f"    表専有率: {occupancy*100:.1f}% (W:{table_width}, H:{table_height})")

        # 80%以下なら分割不要
        if occupancy <= 0.80:
            return ('none', [], f'専有率{occupancy*100:.0f}%≤80%、分割不要')

        # ============================================
        # STEP 1: ヒストグラム構築（密度分析）
        # ============================================
        MIN_GAP = 20  # 最小ガター幅（緩和）
        MAX_INTERNAL_BOUNDARIES = 4  # 内部境界は最大4本（両端含め6本）

        x_histogram = np.zeros(QUANTIZE_GRID_SIZE)
        y_histogram = np.zeros(QUANTIZE_GRID_SIZE)
        x_line_height = np.zeros(QUANTIZE_GRID_SIZE)
        y_line_width = np.zeros(QUANTIZE_GRID_SIZE)

        for block in blocks:
            bbox = block['bbox']
            x_start, y_start = max(0, int(bbox[0])), max(0, int(bbox[1]))
            x_end, y_end = min(QUANTIZE_GRID_SIZE, int(bbox[2])), min(QUANTIZE_GRID_SIZE, int(bbox[3]))
            w, h = x_end - x_start, y_end - y_start

            x_histogram[x_start:x_end] += 1
            y_histogram[y_start:y_end] += 1

            if w <= 10:
                x_line_height[x_start:x_end] = np.maximum(x_line_height[x_start:x_end], h)
            if h <= 10:
                y_line_width[y_start:y_end] = np.maximum(y_line_width[y_start:y_end], w)

        # 【診断】X軸ヒストグラム全出力
        hist_str = "".join([f"[{i}]{int(x_histogram[i])}" for i in range(QUANTIZE_GRID_SIZE)])
        logger.info(f"  ├─ [X軸ヒストグラム] {hist_str}")

        # ============================================
        # 【Ver 5.6】STEP 2: 純粋空白（Gap）のみで分割
        # 密度分析・罫線検出を完全廃止。histogram=0の領域のみを分割軸とする。
        # ============================================
        v_gaps = self._find_gaps(x_histogram, MIN_GAP, MAX_INTERNAL_BOUNDARIES)
        h_gaps = self._find_gaps(y_histogram, MIN_GAP, MAX_INTERNAL_BOUNDARIES)

        # 【Ver 5.6】罫線・密度谷は使用しない
        logger.info(f"  ├─ [Ver 5.6] 純粋空白のみで分割判定")
        logger.info(f"  ├─ [診断] v_gaps（空白のみ）: {v_gaps}")
        logger.info(f"  ├─ [診断] h_gaps（空白のみ）: {h_gaps}")

        # 【Ver 5.6】Gapのみを候補として使用（罫線・密度谷は無視）
        v_candidates = [pos for pos, width in v_gaps]
        h_candidates = [pos for pos, width in h_gaps]

        logger.info(f"  ├─ [診断] v_candidates（純粋空白）: {v_candidates}")
        logger.info(f"  ├─ [診断] h_candidates（純粋空白）: {h_candidates}")

        # ============================================
        # 【Ver 5.6】STEP 3: 空白がなければ分割しない
        # AIに大きな表を1枚で渡し、ヘッダーを自律的に特定させる
        # ============================================
        has_v = len(v_candidates) > 0
        has_h = len(h_candidates) > 0

        if not has_v and not has_h:
            # 【Ver 5.6】空白なし = 分割なし = AIに丸投げ
            logger.info(f"  ├─ [Ver 5.6] 純粋空白なし → 分割せずAIに丸投げ")
            return ('none', [], '純粋空白なし（Ver5.6: AI自律モード）')

        # 空白がある場合のみ分割
        if has_v and has_h:
            # 両方ある場合は垂直優先（列方向の空白を使う）
            axis = 'vertical'
            positions = v_candidates
            reason = f'純粋空白検出（垂直{len(v_candidates)}本）'
        elif has_v:
            axis = 'vertical'
            positions = v_candidates
            reason = f'純粋空白検出（垂直{len(v_candidates)}本）'
        else:
            axis = 'horizontal'
            positions = h_candidates
            reason = f'純粋空白検出（水平{len(h_candidates)}本）'

        return (axis, positions, reason)

    def _merge_boundary_candidates(
        self,
        gaps: List[tuple],
        lines: List[int],
        valleys: List[tuple],
        max_count: int
    ) -> List[int]:
        """空白・罫線・密度谷の候補を統合（重複除去・優先順位付き）"""
        candidates = []
        used_positions = set()
        MERGE_THRESHOLD = 30  # この距離以内は同一境界とみなす

        def add_if_new(pos):
            for used in used_positions:
                if abs(pos - used) < MERGE_THRESHOLD:
                    return False
            used_positions.add(pos)
            candidates.append(pos)
            return True

        # 優先1: 空白ガター（最も信頼性が高い）
        for pos, width in gaps:
            if len(candidates) >= max_count:
                break
            add_if_new(pos)

        # 優先2: 罫線
        for pos in lines:
            if len(candidates) >= max_count:
                break
            add_if_new(pos)

        # 優先3: 密度谷
        for pos, depth in valleys:
            if len(candidates) >= max_count:
                break
            add_if_new(pos)

        return sorted(candidates)

    def _count_density_peaks(self, histogram) -> int:
        """ヒストグラムからピーク（情報の柱）の数を数える"""
        import numpy as np

        if len(histogram) == 0 or np.max(histogram) == 0:
            return 0

        # スムージング
        kernel_size = 20
        kernel = np.ones(kernel_size) / kernel_size
        smoothed = np.convolve(histogram, kernel, mode='same')

        # 閾値以上の連続区間をカウント
        threshold = np.max(smoothed) * 0.2  # 最大値の20%以上
        in_peak = False
        peak_count = 0

        for val in smoothed:
            if val >= threshold and not in_peak:
                in_peak = True
                peak_count += 1
            elif val < threshold:
                in_peak = False

        return peak_count

    def _find_gaps(self, histogram, min_gap: int, max_count: int) -> List[tuple]:
        """ヒストグラムから空白ギャップを検出"""
        import numpy as np
        gaps = []
        in_gap = False
        gap_start = 0

        for i, val in enumerate(histogram):
            if val == 0 and not in_gap:
                in_gap = True
                gap_start = i
            elif val > 0 and in_gap:
                gap_width = i - gap_start
                if gap_width >= min_gap:
                    center = gap_start + gap_width // 2
                    if 50 < center < QUANTIZE_GRID_SIZE - 50:
                        gaps.append((center, gap_width))
                in_gap = False

        # 幅が広い順にソート
        gaps.sort(key=lambda x: x[1], reverse=True)
        return gaps[:max_count]

    def _find_lines(self, line_sizes, min_size: int) -> List[int]:
        """罫線位置を検出"""
        lines = []
        in_line = False
        line_start = 0

        for i, size in enumerate(line_sizes):
            if size >= min_size and not in_line:
                in_line = True
                line_start = i
            elif size < min_size and in_line:
                center = (line_start + i) // 2
                if 50 < center < QUANTIZE_GRID_SIZE - 50:
                    lines.append(center)
                in_line = False

        return lines

    def _find_density_valleys(self, histogram, max_count: int) -> List[tuple]:
        """
        【Ver 3.7修正】密度の谷を検出（隣接マージ版）

        問題: 同じ谷の隣接ピクセル(85,86,87,88)を別々に検出してしまう
        解決: 検出後に隣接する谷をマージし、真に独立した谷のみ返す
        """
        import numpy as np

        if len(histogram) < 100:
            return []

        # 移動平均でスムージング
        kernel_size = 30  # 大きめのカーネルで安定化
        smoothed = np.convolve(histogram, np.ones(kernel_size)/kernel_size, mode='same')

        # 全体の統計を取得
        mean_val = np.mean(smoothed)
        max_val = np.max(smoothed)

        # 局所最小値を検出（閾値を緩和）
        raw_valleys = []
        MIN_VALLEY_SEPARATION = 80  # 谷と谷の最小間隔（量子化座標）

        for i in range(60, len(smoothed) - 60):
            # 前後40ピクセルより低い = より広い範囲で谷を検出
            if smoothed[i] < smoothed[i-40] and smoothed[i] < smoothed[i+40]:
                # 深さ = 周囲との差
                depth = min(smoothed[i-40], smoothed[i+40]) - smoothed[i]
                # 相対的な深さも考慮（平均値の10%以上の落ち込み）
                relative_depth = (mean_val - smoothed[i]) / mean_val if mean_val > 0 else 0

                if depth > 0.3 or relative_depth > 0.1:
                    raw_valleys.append((i, depth, relative_depth))

        # 【重要】隣接する谷をマージ
        merged_valleys = []
        raw_valleys.sort(key=lambda x: x[0])  # 位置順でソート

        for pos, depth, rel_depth in raw_valleys:
            # 既存の谷と近すぎないかチェック
            is_new = True
            for j, (existing_pos, existing_depth, _) in enumerate(merged_valleys):
                if abs(pos - existing_pos) < MIN_VALLEY_SEPARATION:
                    # 近い谷がある場合、深い方を採用
                    if depth > existing_depth:
                        merged_valleys[j] = (pos, depth, rel_depth)
                    is_new = False
                    break

            if is_new:
                merged_valleys.append((pos, depth, rel_depth))

        # 深さ順でソートして返す
        merged_valleys.sort(key=lambda x: x[1], reverse=True)

        logger.debug(f"    [密度谷] raw={len(raw_valleys)}個 → merged={len(merged_valleys)}個")

        return [(pos, depth) for pos, depth, _ in merged_valleys[:max_count]]

    def _horizontal_packing(self, blocks: List[Dict], page_idx: int) -> List[Dict]:
        """
        【Ver 6.4】横パッキング完全廃止 → パススルー

        ============================================
        【廃止理由】2026-01-31
        横パッキングは246個のブロックを9個の巨大な塊に統合し、
        以下の致命的な問題を引き起こしていた:
          1. 情報の消失（AIが巨大な塊を読み飛ばす）
          2. MAX_TOKENS 回避の失敗
          3. 「空白の通り道」判定の誤り

        【新方針】
        Suryaが検出した246個のブロックを1つも統合せず、
        そのままの粒度で後続処理に渡す。
        ============================================

        Args:
            blocks: Suryaが検出したブロック（統合禁止）
            page_idx: ページ番号

        Returns:
            入力ブロックをそのまま返す（メタデータのみ補完）
        """
        result = []

        for idx, block in enumerate(blocks):
            # 必要なメタデータを補完（既存があれば維持）
            processed = block.copy()

            # block_id がなければ生成
            if not processed.get('block_id'):
                processed['block_id'] = f"p{page_idx}_b{idx}"

            # ページ情報を補完
            processed['page'] = page_idx
            if 'page_width' not in processed:
                processed['page_width'] = 1000
            if 'page_height' not in processed:
                processed['page_height'] = 1000

            # パッキングフラグは False（統合していない）
            processed['is_row_packed'] = False
            processed['block_count'] = 1

            result.append(processed)

        logger.info(f"  ├─ 【Ver 6.4】パッキング廃止: {len(blocks)}ブロック → {len(result)}ブロック（そのまま）")
        return result

    def _vertical_split_with_index(self, blocks: List[Dict], positions: List[int], page_idx: int) -> List[Dict]:
        """縦分割（インデックス列複製付き）"""
        boundaries = [0] + positions + [QUANTIZE_GRID_SIZE]
        num_columns = len(boundaries) - 1

        # 列ごとにブロックを分類
        columns = {i: [] for i in range(num_columns)}

        for block in blocks:
            x_center = (block['bbox'][0] + block['bbox'][2]) / 2
            for i in range(num_columns):
                if boundaries[i] <= x_center < boundaries[i + 1]:
                    columns[i].append(block)
                    break

        # インデックス列（左端）を特定
        index_column = columns.get(0, [])

        result = []
        for col_id in range(num_columns):
            col_blocks = columns[col_id]
            if not col_blocks:
                continue

            # 横方向パッキング
            packed = self._horizontal_packing(col_blocks, page_idx)

            for block in packed:
                block['column_id'] = col_id
                block['num_columns'] = num_columns
                block['has_index_column'] = col_id > 0  # 2列目以降はインデックス列を随伴
                block['col_boundaries'] = [boundaries[col_id], boundaries[col_id + 1]]

            result.extend(packed)

        # 列境界を保存
        self._page_column_boundaries[page_idx] = boundaries

        logger.info(f"  ├─ 縦分割: {num_columns}列, インデックス列={len(index_column)}ブロック")
        return result

    def _horizontal_split_with_header(self, blocks: List[Dict], positions: List[int], page_idx: int) -> List[Dict]:
        """横分割（ヘッダー行複製付き）"""
        boundaries = [0] + positions + [QUANTIZE_GRID_SIZE]
        num_sections = len(boundaries) - 1

        # セクションごとにブロックを分類
        sections = {i: [] for i in range(num_sections)}

        for block in blocks:
            y_center = (block['bbox'][1] + block['bbox'][3]) / 2
            for i in range(num_sections):
                if boundaries[i] <= y_center < boundaries[i + 1]:
                    sections[i].append(block)
                    break

        # ヘッダー行（最上段）を特定
        header_section = sections.get(0, [])

        result = []
        for sec_id in range(num_sections):
            sec_blocks = sections[sec_id]
            if not sec_blocks:
                continue

            # 横方向パッキング
            packed = self._horizontal_packing(sec_blocks, page_idx)

            for block in packed:
                block['section_id'] = sec_id
                block['num_sections'] = num_sections
                block['has_header_row'] = sec_id > 0  # 2セクション目以降はヘッダー行を随伴
                block['section_boundaries'] = [boundaries[sec_id], boundaries[sec_id + 1]]

            result.extend(packed)

        # 水平分割の場合、列境界は1列扱い
        self._page_column_boundaries[page_idx] = [0, QUANTIZE_GRID_SIZE]

        logger.info(f"  ├─ 横分割: {num_sections}セクション, ヘッダー行={len(header_section)}ブロック")
        return result

    def _merge_column_blocks(
        self,
        col_blocks: List[Dict],
        page_idx: int,
        column_id: int,
        num_columns: int,
        col_x_start: int,
        col_x_end: int
    ) -> Dict:
        """
        【ページ完結型】列内の全ブロックを1つのテーブルブロックに統合

        Args:
            col_blocks: この列に属する全ブロック（Y座標でソート済み）
            page_idx: ページ番号
            column_id: 列番号
            num_columns: このページの総列数
            col_x_start: 列の左端X座標
            col_x_end: 列の右端X座標

        Returns:
            統合されたブロック（1ページ1列1ブロック）
        """
        if len(col_blocks) == 1:
            block = col_blocks[0]
            block['table_id'] = f"TBL_P{page_idx}_C{column_id}"
            block['is_page_complete'] = True
            block['row_count'] = 1
            return block

        # 全ブロックのバウンディングボックスを統合
        x_min = min(b['bbox'][0] for b in col_blocks)
        y_min = min(b['bbox'][1] for b in col_blocks)
        x_max = max(b['bbox'][2] for b in col_blocks)
        y_max = max(b['bbox'][3] for b in col_blocks)

        # 行データを構築（Y座標順）
        rows = []
        for block in col_blocks:
            rows.append({
                'block_id': block['block_id'],
                'y': block['y_center'],
                'bbox': block['bbox']
            })

        # ページ跨ぎの判定（下端に近いか）
        is_at_page_bottom = y_max > 900  # 1000グリッド中900以上
        is_at_page_top = y_min < 100     # 1000グリッド中100以下

        return {
            'page': page_idx,
            'block_id': f"col_P{page_idx}_C{column_id}",
            'table_id': f"TBL_P{page_idx}_C{column_id}",
            'bbox': [x_min, y_min, x_max, y_max],
            'bbox_original': col_blocks[0].get('bbox_original'),
            'page_width': col_blocks[0].get('page_width'),
            'page_height': col_blocks[0].get('page_height'),
            'column_id': column_id,
            'num_columns': num_columns,
            'col_x_range': [col_x_start, col_x_end],
            'x_center': (x_min + x_max) / 2,
            'y_center': (y_min + y_max) / 2,
            # ページ完結型メタデータ
            'is_page_complete': True,
            'row_count': len(col_blocks),
            'rows': rows,
            'original_block_ids': [b['block_id'] for b in col_blocks],
            # ページ跨ぎ用フラグ（Stage Hで使用）
            'is_continued_from_prev': is_at_page_top,  # 前ページから続く可能性
            'is_continued_to_next': is_at_page_bottom,  # 次ページに続く可能性
            'stitch_hint': {
                'prev_page': page_idx - 1 if is_at_page_top else None,
                'next_page': page_idx + 1 if is_at_page_bottom else None,
                'column_id': column_id
            }
        }

    def _merge_row_blocks(self, row_blocks: List[Dict], page_idx: int, column_id: int) -> Dict:
        """
        同じ行のブロックを1つにマージ（レガシー互換用）
        """
        if len(row_blocks) == 1:
            return row_blocks[0]

        # X座標でソート
        row_blocks.sort(key=lambda b: b['bbox'][0])

        # バウンディングボックスを統合
        x_min = min(b['bbox'][0] for b in row_blocks)
        y_min = min(b['bbox'][1] for b in row_blocks)
        x_max = max(b['bbox'][2] for b in row_blocks)
        y_max = max(b['bbox'][3] for b in row_blocks)

        # 元のblock_idを結合
        merged_id = "+".join(b['block_id'] for b in row_blocks)

        return {
            'page': page_idx,
            'block_id': f"merged_{merged_id}",
            'bbox': [x_min, y_min, x_max, y_max],
            'bbox_original': row_blocks[0].get('bbox_original'),
            'page_width': row_blocks[0].get('page_width'),
            'page_height': row_blocks[0].get('page_height'),
            'confidence': min(b.get('confidence', 1.0) for b in row_blocks),
            'column_id': column_id,
            'x_center': (x_min + x_max) / 2,
            'y_center': (y_min + y_max) / 2,
            'merged_count': len(row_blocks),
            'original_blocks': [b['block_id'] for b in row_blocks]
        }

    # ============================================
    # F-4: Logical Reading Order
    # ============================================
    def _f4_reading_order(self, blocks: List[Dict]) -> List[Dict]:
        """
        F-4: 読む順序の確定
        F-3.5で検出された列を使用して正確にソート
        """
        f4_start = time.time()
        logger.info("[F-4] Logical Reading Order 開始")

        if not blocks:
            return []

        # F-3.5で既にcolumn_id, x_center, y_centerが設定されている場合はそのまま使用
        for block in blocks:
            if 'x_center' not in block:
                bbox = block['bbox']
                block['x_center'] = (bbox[0] + bbox[2]) / 2
            if 'y_center' not in block:
                bbox = block['bbox']
                block['y_center'] = (bbox[1] + bbox[3]) / 2
            if 'column_id' not in block:
                # フォールバック: 左右2分割
                block['column_id'] = 0 if block['x_center'] < QUANTIZE_GRID_SIZE / 2 else 1

        # ソート: page → column → y → x
        sorted_blocks = sorted(
            blocks,
            key=lambda b: (
                b.get('page', 0),
                b.get('column_id', 0),
                b.get('y_center', 0),
                b.get('x_center', 0)
            )
        )

        # reading_order を付与
        for order, block in enumerate(sorted_blocks):
            block['reading_order'] = order

        f4_elapsed = time.time() - f4_start
        logger.info(f"[F-4完了] {len(sorted_blocks)}ブロック順序確定, {f4_elapsed:.2f}秒")

        return sorted_blocks

    # ============================================
    # F-5: Block Classification
    # ============================================
    def _f5_classify(self, blocks: List[Dict]) -> List[Dict]:
        """
        F-5: 構造ラベル付与
        ルールベースでブロックタイプを推定
        """
        f5_start = time.time()
        logger.info("[F-5] Block Classification 開始")

        for block in blocks:
            bbox = block['bbox']
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            y = block.get('y_center', 500)
            area = w * h
            aspect_ratio = w / h if h > 0 else 1.0

            # ブロックタイプ推定
            block_type = 'body_hint'

            # 表の検出
            if aspect_ratio > 3.0 and 20 < h < 150:
                block_type = 'table_hint'
            elif 10000 < area < 500000 and 0.5 < aspect_ratio < 3.0:
                block_type = 'table_hint'
            # 見出し
            elif h < 80 and w > 200 and y < 200:
                block_type = 'heading_hint'
            # ヘッダー
            elif y < 80:
                block_type = 'header_hint'
            # フッター
            elif y > 920:
                block_type = 'footer_hint'
            # 注記
            elif area < 5000:
                block_type = 'note_hint'

            block['block_type_hint'] = block_type

        f5_elapsed = time.time() - f5_start
        type_counts = {}
        for b in blocks:
            t = b.get('block_type_hint', 'unknown')
            type_counts[t] = type_counts.get(t, 0) + 1
        logger.info(f"[F-5完了] ラベル分布: {type_counts}, {f5_elapsed:.2f}秒")

        return blocks

    # ============================================
    # F-6: ID焼き込み画像生成（Ver 4.0 - 座標排除）
    # ============================================
    def _f6_burn_ids_to_image(
        self,
        image: Image.Image,
        blocks: List[Dict],
        page_idx: int = 0
    ) -> Image.Image:
        """
        F-6: ブロックIDを画像に直接焼き込む

        【Ver 4.0】座標データはAIに渡さない。
        代わりに、IDを視覚的に画像に描画し、AIは「見たまま」判断する。

        Args:
            image: 元画像（PIL Image）
            blocks: ブロックリスト（block_id, bbox含む）
            page_idx: ページ番号

        Returns:
            ID焼き込み済み画像
        """
        from PIL import ImageDraw, ImageFont
        import io

        f6_start = time.time()
        logger.info(f"[F-6] ID焼き込み開始: {len(blocks)}ブロック")

        # 画像をコピー（元画像を変更しない）
        img_with_ids = image.copy()
        draw = ImageDraw.Draw(img_with_ids)

        # フォント設定（システムフォントを使用）
        try:
            font = ImageFont.truetype("arial.ttf", 14)
        except:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
            except:
                font = ImageFont.load_default()

        img_width, img_height = image.size

        # 各ブロックにIDを描画
        for block in blocks:
            block_id = block.get('block_id', '')
            bbox = block.get('bbox', [0, 0, 0, 0])

            # 量子化座標を実座標に変換
            page_width = block.get('page_width', QUANTIZE_GRID_SIZE)
            page_height = block.get('page_height', QUANTIZE_GRID_SIZE)

            x1 = int(bbox[0] * img_width / QUANTIZE_GRID_SIZE)
            y1 = int(bbox[1] * img_height / QUANTIZE_GRID_SIZE)
            x2 = int(bbox[2] * img_width / QUANTIZE_GRID_SIZE)
            y2 = int(bbox[3] * img_height / QUANTIZE_GRID_SIZE)

            # IDラベル（短縮形: p0_r5 → #5）
            short_id = block_id.split('_')[-1] if '_' in block_id else block_id
            label = f"#{short_id}"

            # 背景付きでIDを描画（視認性確保）
            text_bbox = draw.textbbox((x1, y1), label, font=font)
            padding = 2
            draw.rectangle(
                [text_bbox[0] - padding, text_bbox[1] - padding,
                 text_bbox[2] + padding, text_bbox[3] + padding],
                fill='yellow'
            )
            draw.text((x1, y1), label, fill='red', font=font)

            # ブロック境界を薄く描画（デバッグ用、オプション）
            # draw.rectangle([x1, y1, x2, y2], outline='blue', width=1)

        f6_elapsed = time.time() - f6_start
        logger.info(f"[F-6完了] {len(blocks)}個のID焼き込み, {f6_elapsed:.2f}秒")
        logger.info(f"  └─ 【Ver 6.4確認】AIに渡すID数: {len(blocks)}個（Surya検出ブロック全数）")

        return img_with_ids

    def _f6_store_id_mapping(self, blocks: List[Dict]) -> Dict[str, Dict]:
        """
        ID→座標マッピングをシステム内部に保持（AIには渡さない）

        Returns:
            {block_id: {bbox, page, column_id, ...}}
        """
        mapping = {}
        for block in blocks:
            block_id = block.get('block_id', '')
            mapping[block_id] = {
                'bbox': block.get('bbox'),
                'page': block.get('page', 0),
                'column_id': block.get('column_id', 0),
                'row_id': block.get('row_id', 0),
            }
        return mapping

    # ============================================
    # F-7: Dual Read - Path A (Text Extraction)
    # 【Ver 4.0】座標排除 - ID焼き込み画像のみ使用
    # ============================================
    def _f7_path_a_text_extraction(
        self,
        file_path: Path,
        page_images: List[Dict] = None
    ) -> Dict[str, Any]:
        """
        F-7 Path A: 構造マッピング（gemini-2.0-flash）

        【Ver 4.0】座標データは渡さない。ID焼き込み画像を見て判断。
        """
        f7_start = time.time()
        logger.info("[F-7] Path A - 構造マッピング開始（座標排除版）")

        prompt = self._build_f7_prompt()

        try:
            response = self.llm_client.generate_with_vision(
                prompt=prompt,
                image_path=str(file_path),
                model=F7_MODEL_IMAGE,
                max_tokens=F7_F8_MAX_TOKENS,
                temperature=F7_F8_TEMPERATURE,
                response_format="json"
            )

            # 【Ver 6.4】生成物ログ出力（MAX_TOKENS途切れ対応）
            logger.info(f"[F-7] ===== 生成物ログ開始 =====")
            logger.info(f"[F-7] レスポンス長: {len(response) if response else 0}文字")
            logger.info(f"[F-7] 生レスポンス:\n{response if response else '(empty)'}")
            logger.info(f"[F-7] ===== 生成物ログ終了 =====")

            # JSON パース
            try:
                result = json.loads(response)
            except json.JSONDecodeError as jde:
                logger.warning(f"[F-7] JSONパース失敗（MAX_TOKENS?）: {jde}")
                logger.warning(f"[F-7] 途切れたレスポンス末尾: ...{response[-500:] if response and len(response) > 500 else response}")
                import json_repair
                result = json_repair.repair_json(response, return_objects=True)

            f7_elapsed = time.time() - f7_start
            logger.info(f"[F-7完了] Path A: {len(response)}文字, {f7_elapsed:.2f}秒")

            return result

        except MaxTokensExceededError as mte:
            logger.error(f"[F-7] Path A MAX_TOKENS到達: {mte}")
            logger.info(f"[F-7] ===== MAX_TOKENS部分出力ログ開始 =====")
            logger.info(f"[F-7] 部分出力（全文）:\n{mte.partial_output}")
            logger.info(f"[F-7] ===== MAX_TOKENS部分出力ログ終了 =====")
            try:
                import json_repair
                result = json_repair.repair_json(mte.partial_output, return_objects=True)
                result['_max_tokens_partial'] = True
                return result
            except:
                return {"error": str(mte), "extracted_texts": [], "tables": [], "_max_tokens_partial": True}

        except Exception as e:
            logger.error(f"[F-7] Path A エラー: {e}")
            return {"error": str(e), "extracted_texts": [], "tables": []}

    def _parse_id_text_header(self, id_text_str: str) -> str:
        """
        【Ver 6.2】"ID:テキスト" 形式からテキスト部分を抽出

        Args:
            id_text_str: "1:2/1" 形式の文字列、または "none"

        Returns:
            テキスト部分（例: "2/1"）、または空文字
        """
        if not id_text_str or id_text_str.lower() == "none":
            return ""

        if ":" in id_text_str:
            # "ID:テキスト" 形式 → テキスト部分を返す
            parts = id_text_str.split(":", 1)
            return parts[1].strip() if len(parts) > 1 else ""
        else:
            # ":" がない場合はそのまま返す（互換性のため）
            return id_text_str.strip()

    def _infer_doc_type(self, mime_type: str, file_path: Path) -> str:
        """MIMEタイプとファイル名からドキュメントタイプを推測"""
        if not mime_type:
            return "unknown"

        if mime_type == 'application/pdf':
            # ファイル名からヒントを得る
            name = file_path.name.lower() if file_path else ""
            if '成績' in name or 'score' in name or '偏差値' in name:
                return "成績表・偏差値表"
            elif '時間割' in name or 'schedule' in name or 'timetable' in name:
                return "時間割・スケジュール"
            elif '通信' in name or 'news' in name or 'letter' in name:
                return "お知らせ・通信"
            return "PDF文書"
        elif mime_type.startswith('image/'):
            return "画像"
        elif mime_type.startswith('text/'):
            return "テキスト"
        return "その他"

    def _build_f7_prompt(self) -> str:
        """
        【Ver 6.6】無限ループ防止版 - 推測禁止・終端条件明示

        AIの仕事: 画像に実在する文字だけを座標付きで出力
        禁止事項: 推測、補完、表の復元、同一文字の反復生成
        """
        return """あなたは「抽出器」です。推測・補完・並べ替え・表の復元は禁止です。
画像に存在する文字だけを、見える範囲から抽出してください。
画像に存在しない行・列・項目・繰り返しを生成してはいけません。

## 出力形式（JSONのみ、説明文禁止）
{
  "texts": [
    {"text": "<string>", "bbox": [x0, y0, x1, y1]}
  ],
  "stop_reason": "<string>"
}

## 基本設計
1) これは“レイアウト理解タスク”ではない、“テキスト抽出”タスクだ。
2) 「意味的一貫性」ではなく、単純な文字の「列挙」を要求しています。
3) 「一覧ではない。」全体を考えることは禁止。常に部分の抽出だけを考えろ。
4) 全体を考えないのだから、「抜けがある」かどうかを想像するのは禁止。
5) 「位置はバラバラ」なので、相対的な空白位置にデータがあるはずという考えは禁止

## 最重要ルール
6) 画像に確実に見える文字列だけを出力する。不確実なら出力しない。
7) 読み込んだテキストを、絶対に視覚的に再生成しない。
8) 縦方向か、横方向か、一定のルートをたどって読み続ける。縦横ランダムに読まない。
9) 縦に読む場合は左端から、左右の座標を固定して上端から下端まで読み、右へ少しずらして、また左右の座標を固定して上端から下端まで読むことを繰り返す。
10) 横に読む場合は上端から、上下の座標を固定して左端から右端まで読み、下へ少しずらして、また上下の座標を固定して左端から右端まで読むことを繰り返す。
11) y座標を一定刻みで増やして「表の続きを生成」する行為は禁止。画像の根拠がないのにbboxを規則的に増やして出力してはいけない。
12) 全ての座標にテキストがあることを前提にしない。
13) 多くの場所は空欄である。空欄であったら文字生成をスキップさせる。
14) bboxは必ず「その文字が存在する場所」を囲む。推測で座標を作らない。
15) bboxは必ず重ならない。
16) 文字列の正規化（学校名の推測、漢字変換、表記ゆれ統合）禁止。見たままを出力。
17) 赤/黄色のIDラベルは無視（読み取り対象外）。

## 座標ガード（最重要）
A) bbox は必ず「2つの座標ペア」だけを持つこと：
   bbox = [x0, y0, x1, y1]
   それ以外の形式（点、3点、ポリゴン、配列のネスト、bboxが無い等）は出力禁止。

B) 座標値の上限：
   x0, y0, x1, y1 のいずれも 1001 以上を禁止する。
   1001以上の値を出しそうになったら、そこで直ちに stop_reason="COORD_GUARD" で終了せよ。

C) 完全同一 bbox の反復禁止（絶対）：
   同一ページ内で、完全に同じ bbox [x0,y0,x1,y1] を2回以上出力してはいけない。
   既出 bbox を出しそうになった時点で、直ちに stop_reason="REPEAT_GUARD" で終了せよ。

## 停止条件（いずれかで終了）
- 画像からこれ以上確実な文字が見つからない → stop_reason="COMPLETE"
- textsが2000件 → "LIMIT_REACHED"
- 反復ガード（同一text100回 or 同一bbox2回）→ "REPEAT_GUARD"
- 座標上限超過（1001以上）→ "COORD_GUARD"

このタスクは「網羅」より「真実性」を優先する。見えないものは欠損として良い。
"""

    # ============================================
    # ============================================
    # F-7.5: Coordinate Mapping (PROGRAM)
    # ============================================
    def _f7_5_coordinate_mapping(
        self,
        f7_texts: List[Dict],
        block_coords: Dict[str, Dict],
        distance_threshold: int = 50
    ) -> List[Dict]:
        """
        【Ver 6.6】F-7.5: 座標ベースのID紐付け（プログラム処理）

        AIが出力した「文字+bbox」と、Suryaの「ID+座標」を
        最短距離アルゴリズムで紐付ける。AIによる判断は一切なし。

        Args:
            f7_texts: F7が出力した [{text, bbox: [x0,y0,x1,y1]}, ...]
            block_coords: Suryaの {block_id: {x, y, bbox}, ...}
            distance_threshold: この距離以上はnullとする（ピクセル）

        Returns:
            [{"id": "p0_b5", "text": "開成", "distance": 12.3}, ...]
        """
        import math
        f75_start = time.time()
        logger.info(f"[F-7.5] 座標マッチング開始: {len(f7_texts)}文字 × {len(block_coords)}ブロック")

        if not f7_texts or not block_coords:
            logger.warning("[F-7.5] 入力が空")
            return []

        mapped_results = []

        for text_item in f7_texts:
            text = text_item.get('text', '')

            # bbox形式 [x0, y0, x1, y1] または 旧形式 {x, y}
            bbox = text_item.get('bbox')
            if bbox and isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                tx = (bbox[0] + bbox[2]) / 2  # 中心x
                ty = (bbox[1] + bbox[3]) / 2  # 中心y
            else:
                tx = text_item.get('x', 0)
                ty = text_item.get('y', 0)

            if not text:
                continue

            # 最短距離のブロックを探す
            nearest_id = None
            min_distance = float('inf')

            for block_id, coords in block_coords.items():
                bx = coords.get('x', 0)
                by = coords.get('y', 0)

                # ユークリッド距離
                distance = math.sqrt((tx - bx) ** 2 + (ty - by) ** 2)

                if distance < min_distance:
                    min_distance = distance
                    nearest_id = block_id

            # 【Ver 6.9】閾値判定廃止 - 常に最近傍ブロックを採用
            mapped_results.append({
                'id': nearest_id,
                'text': text,
                'distance': min_distance,
                'x': tx,
                'y': ty,
                'bbox': bbox,
                '_over_threshold': False  # 常にFalse
            })

        # 統計ログ
        matched = sum(1 for r in mapped_results if r['id'] is not None)
        unmatched = len(mapped_results) - matched
        avg_distance = sum(r['distance'] for r in mapped_results) / len(mapped_results) if mapped_results else 0

        f75_elapsed = time.time() - f75_start
        logger.info(f"[F-7.5完了] マッチ={matched}, 未マッチ={unmatched}, 平均距離={avg_distance:.1f}px, {f75_elapsed:.2f}秒")

        # 【Ver 6.5】全文ログ出力
        logger.info(f"[F-7.5] ===== マッピング結果ログ開始 =====")
        logger.info(f"[F-7.5] 結果件数: {len(mapped_results)}件")
        logger.info(f"[F-7.5] マッピング結果（全文）:\n{json.dumps(mapped_results, ensure_ascii=False, indent=2)}")
        logger.info(f"[F-7.5] ===== マッピング結果ログ終了 =====")

        return mapped_results

    # ============================================
    # F-7: Transcription (音声/動画用)
    # ============================================
    def _f7_transcription(
        self,
        file_path: Path,
        mime_type: str,
        is_video: bool
    ) -> Dict[str, Any]:
        """
        F-7: 音声/動画の書き起こし（gemini-2.5-flash-lite）
        """
        f7_start = time.time()
        media_type = "動画" if is_video else "音声"
        logger.info(f"[F-7] Transcription ({media_type}) 開始")

        prompt = self._build_transcription_prompt(is_video)

        try:
            import google.generativeai as genai

            # ファイルアップロード
            logger.info(f"  ├─ ファイルアップロード中: {file_path.name}")
            uploaded_file = genai.upload_file(path=str(file_path), mime_type=mime_type)

            # 処理完了待機
            while uploaded_file.state.name == "PROCESSING":
                time.sleep(2)
                uploaded_file = genai.get_file(uploaded_file.name)

            if uploaded_file.state.name == "FAILED":
                raise ValueError(f"ファイル処理失敗: {uploaded_file.state.name}")

            # モデル初期化
            model = genai.GenerativeModel(F7_MODEL_AV)

            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]

            generation_config = genai.GenerationConfig(
                max_output_tokens=F7_F8_MAX_TOKENS,
                temperature=F7_F8_TEMPERATURE
            )

            # 生成
            response = model.generate_content(
                [prompt, uploaded_file],
                generation_config=generation_config,
                safety_settings=safety_settings,
                request_options={"timeout": 600}
            )

            transcript = ""
            visual_log = ""

            if response.candidates and response.candidates[0].content.parts:
                raw_text = response.candidates[0].content.parts[0].text

                # JSON パース試行
                try:
                    result = json.loads(raw_text)
                    transcript = result.get('transcript', raw_text)
                    visual_log = result.get('visual_log', '')
                except:
                    transcript = raw_text

            # ファイル削除
            try:
                genai.delete_file(name=uploaded_file.name)
            except:
                pass

            f7_elapsed = time.time() - f7_start
            logger.info(f"[F-7完了] Transcription: {len(transcript)}文字, {f7_elapsed:.2f}秒")

            return {
                "transcript": transcript,
                "visual_log": visual_log if is_video else "",
                "media_type": media_type,
                "model": F7_MODEL_AV
            }

        except Exception as e:
            logger.error(f"[F-7] Transcription エラー: {e}")
            return {"error": str(e), "transcript": "", "visual_log": ""}

    def _build_transcription_prompt(self, is_video: bool) -> str:
        """Transcription用プロンプト"""
        base = """# 音声/映像 完全書き起こし

## Mission
一言一句完全な書き起こしを行ってください。

## 重要な指示
- 「あー」「えー」「うーん」などのフィラーも全て書き起こす
- 言い淀み、言い直しもそのまま記録
- 笑い声、咳払いは [笑い]、[咳払い] のように記録
- 沈黙が長い場合は [沈黙 約5秒] のように記録
- 複数人の場合は話者を識別（話者A、話者B）
- 聞き取れない部分は [聞き取り不明] と記載

## 禁止事項
- 要約は絶対に禁止
- 文章の整理や言い換えは禁止
- 内容の省略は禁止

## 出力形式
```json
{
  "transcript": "[00:00] 話者A: えー、本日は...",
  "visual_log": ""
}
```
"""
        if is_video:
            base += """
## 動画の場合: visual_log も記録
```json
{
  "transcript": "...",
  "visual_log": "[00:00] 黒背景、中央にロゴ\\n[00:03] オフィス会議室が映る..."
}
```
"""
        return base

    # ============================================
    # F-8: Dual Read - Path B (Visual Analysis)
    # ============================================
    def _f8_path_b_visual_analysis(
        self,
        file_path: Path,
        page_images: List[Dict] = None
    ) -> Dict[str, Any]:
        """
        【Ver 4.0】F-8 Path B: 構造解析（座標排除版）
        """
        f8_start = time.time()
        logger.info("[F-8] Path B - Visual Analysis 開始（座標排除版）")

        prompt = self._build_f8_prompt()

        try:
            response = self.llm_client.generate_with_vision(
                prompt=prompt,
                image_path=str(file_path),
                model=F8_MODEL,
                max_tokens=F7_F8_MAX_TOKENS,
                temperature=F7_F8_TEMPERATURE,
                response_format="json"
            )

            # 【Ver 6.4】生成物ログ出力（MAX_TOKENS途切れ対応）
            logger.info(f"[F-8] ===== 生成物ログ開始 =====")
            logger.info(f"[F-8] レスポンス長: {len(response) if response else 0}文字")
            logger.info(f"[F-8] 生レスポンス:\n{response if response else '(empty)'}")
            logger.info(f"[F-8] ===== 生成物ログ終了 =====")

            # JSON パース
            try:
                result = json.loads(response)
            except json.JSONDecodeError as jde:
                logger.warning(f"[F-8] JSONパース失敗（MAX_TOKENS?）: {jde}")
                logger.warning(f"[F-8] 途切れたレスポンス末尾: ...{response[-500:] if response and len(response) > 500 else response}")
                import json_repair
                result = json_repair.repair_json(response, return_objects=True)

            f8_elapsed = time.time() - f8_start
            logger.info(f"[F-8完了] Path B: {len(response)}文字, {f8_elapsed:.2f}秒")

            return result

        except Exception as e:
            logger.error(f"[F-8] Path B エラー: {e}")
            return {"error": str(e), "tables": [], "diagrams": [], "layout_analysis": {}}

    def _build_f8_prompt(self) -> str:
        """
        【Ver 6.6】F-8: ヘッダー候補抽出（例削除・推測禁止・null許可版）

        禁止: 推測、補完、一般知識による埋め合わせ、固有名詞生成
        許可: 不確実なら空配列、根拠付きヘッダーのみ
        """
        return """あなたは「ヘッダー候補抽出器」です。推測・補完・一般知識による埋め合わせは禁止です。
画像内に実在する文字列だけを根拠として、表の見出し候補を抽出してください。
画像に存在しない固有名詞・学校名・地名などを生成してはいけません。

## タスク
1) x_headers: 表の列見出し候補（上段に並ぶ見出し）
2) y_headers: 表の行見出し候補（左側に並ぶ見出し）
3) table_structure: 表の構造情報（行数・列数・セル結合）
※「候補」であり、確証がない場合は空配列でよい。

## 絶対ルール
- 推測で補完しない。見えていないものは出さない。
- 画像に出現しない文字列を生成しない。
- 既知の典型（学校名一覧、都道府県、科目など）で埋めない。
- 迷ったらnull/[]を返す（欠損は正しい）。
- 重複候補を増やさない（同一文字列は1回まで）。

## 根拠ルール
x_headers/y_headersの各要素には「根拠bbox」を必ず付ける。
根拠が提示できない候補は出力禁止。

## 出力形式（JSONのみ、説明文禁止）
{{
  "x_headers": [
    {{"text": "<string>", "bbox": [x0,y0,x1,y1], "confidence": 0.0-1.0}}
  ],
  "y_headers": [
    {{"text": "<string>", "bbox": [x0,y0,x1,y1], "confidence": 0.0-1.0}}
  ],
  "table_structure": {{
    "header_rows": <int>,
    "total_rows": <int or null>,
    "total_cols": <int or null>,
    "table_type": "list_type" | "matrix_type" | null
  }},
  "notes": ["不確実なため空配列にした", "見出しが判別不能"]
}}

## 停止/NULL方針
- 表の見出しが明確に存在しない、または判定不能ならx_headers=[], y_headers=[]とする。
- その場合notesに理由を書く（短文でよい）。
- 文字の書き起こしは別工程が担当。構造のみに集中する。
"""

    # ============================================
    # チャンク処理用メソッド（MAX_TOKENSエラー回避）
    # ============================================

    def _save_chunk_as_temp_image(self, chunk_pages: List[Dict], chunk_idx: int) -> Path:
        """
        チャンク内の画像を一時ファイルとして保存
        複数ページの場合は縦に結合した1枚の画像として保存
        """
        import tempfile

        if len(chunk_pages) == 1:
            # 1ページのみ: そのまま保存
            img = chunk_pages[0]['image']
        else:
            # 複数ページ: 縦に結合
            images = [p['image'] for p in chunk_pages]
            total_height = sum(img.size[1] for img in images)
            max_width = max(img.size[0] for img in images)

            combined = Image.new('RGB', (max_width, total_height), (255, 255, 255))
            y_offset = 0
            for img in images:
                combined.paste(img, (0, y_offset))
                y_offset += img.size[1]

            img = combined

        # 一時ファイルに保存
        temp_file = tempfile.NamedTemporaryFile(
            suffix=f'_chunk{chunk_idx}.png',
            delete=False
        )
        img.save(temp_file.name, 'PNG')
        temp_file.close()

        return Path(temp_file.name)

    # ============================================
    # ============================================
    # 【Ver 4.0】泥棒ロジック（_detect_gutters）完全削除済み
    # F-7は自律判断を行わない。F-3.5の決定を無条件実行するのみ。
    # ============================================

    def _smart_crop_patches(
        self,
        image: Image.Image,
        column_boundaries: List[int],
        overlap: int = 50
    ) -> List[Dict]:
        """
        【Ver 4.0】F-3.5の決定を「神の宣告」として無条件実行

        ============================================
        泥棒ロジック完全排除版
        ============================================

        このメソッドは「自律判断」を一切行わない。
        F-3.5が決定した split_axis と boundaries だけを信じ、
        ただ切る。それ以外のことは1行もしない。
        """
        img_width, img_height = image.size
        patches = []

        # ============================================
        # 【Ver 3.8】F-3.5の決定を取得（絶対命令・上書き禁止）
        # ============================================
        split_axis = getattr(self, '_current_split_axis', 'none')
        split_positions = getattr(self, '_current_split_positions', [])

        # 【Ver 3.8】フォールバック削除 - F-3.5の決定のみを信じる
        # column_boundaries引数は無視。お節介な再計算は一切しない。

        logger.info(f"[SmartCrop] 【Ver 3.8】F-3.5絶対命令: split_axis={split_axis}")
        logger.info(f"[SmartCrop] split_positions: {split_positions}")
        logger.info(f"[SmartCrop] column_boundaries（引数）: {column_boundaries}")

        # ============================================
        # 分割なしの場合: 画像全体を1枚で返す
        # ============================================
        if split_axis == 'none' or not split_positions:
            logger.info("[SmartCrop] 分割なし → 全体を1枚で返却")
            return [{
                'image': image,
                'type': 'full',
                'info': '全体（F-3.5: 分割不要）',
                'patch_index': 0,
                'total_patches': 1,
                'is_continuation': False
            }]

        # ============================================
        # 【Ver 5.0】垂直分割 - 単純矩形スライス
        # 座標の歪みを排除。シンプルに縦に切るだけ。
        # ============================================
        if split_axis == 'vertical':
            # 量子化座標を実座標に変換
            boundaries_px = [0]
            for pos in split_positions:
                px = int(pos * img_width / QUANTIZE_GRID_SIZE)
                # 重複排除・有効範囲チェック
                if px > boundaries_px[-1] + 50 and px < img_width - 50:
                    boundaries_px.append(px)
            boundaries_px.append(img_width)

            num_columns = len(boundaries_px) - 1

            # 【診断ログ】全境界線を表示
            logger.info(f"[SmartCrop] ======== 【Ver 5.0】単純矩形垂直分割 ========")
            logger.info(f"[SmartCrop] split_positions（量子化）: {split_positions}")
            logger.info(f"[SmartCrop] boundaries_px（実座標）: {boundaries_px}")
            logger.info(f"[SmartCrop] 画像サイズ: {img_width}x{img_height}")
            logger.info(f"[SmartCrop] 生成予定列数: {num_columns}")

            # 最低2列は必要（1列なら分割なしと同じ）
            if num_columns < 2:
                logger.warning(f"[SmartCrop] 列数不足({num_columns}) → 全体を1枚で返却")
                return [{
                    'image': image,
                    'type': 'full',
                    'info': '全体（境界線不足）',
                    'patch_index': 0,
                    'total_patches': 1,
                    'is_continuation': False
                }]

            # ============================================
            # 【Ver 5.0】単純矩形スライス
            # 各パッチはフルハイトの縦帯として切り出す
            # ヘッダーもインデックスも切らない（全部見える状態で渡す）
            # ============================================
            for col_idx in range(num_columns):
                x_start = boundaries_px[col_idx]
                x_end = boundaries_px[col_idx + 1]

                # のりしろ付きで切り出し
                crop_start = max(0, x_start - overlap) if col_idx > 0 else 0
                crop_end = min(img_width, x_end + overlap) if col_idx < num_columns - 1 else img_width

                # 単純な矩形切り出し（フルハイト）
                col_img = image.crop((crop_start, 0, crop_end, img_height))
                col_width = crop_end - crop_start

                logger.info(f"[SmartCrop] 列{col_idx + 1}/{num_columns}: x={x_start}〜{x_end} (crop:{crop_start}〜{crop_end}, {col_width}x{img_height})")

                patches.append({
                    'image': col_img,
                    'type': 'v_column_simple',
                    'info': f'垂直列{col_idx + 1}/{num_columns} (x:{x_start}-{x_end})',
                    'patch_index': col_idx,
                    'total_patches': num_columns,
                    'is_continuation': col_idx > 0,
                    'x_range': (x_start, x_end),
                    'crop_range': (crop_start, crop_end)
                })

            logger.info(f"[SmartCrop] ======== 単純矩形分割完了: {len(patches)}パッチ ========")
            return patches

        # ============================================
        # 水平分割（横に切る = 上下に分ける）
        # 縦方向の検討は1行も許さない
        # ============================================
        if split_axis == 'horizontal':
            # 量子化座標を実座標に変換
            boundaries_px = [0]
            for pos in split_positions:
                px = int(pos * img_height / QUANTIZE_GRID_SIZE)
                boundaries_px.append(px)
            boundaries_px.append(img_height)

            num_sections = len(boundaries_px) - 1
            logger.info(f"[SmartCrop] 水平分割: {num_sections}セクション at {boundaries_px}")

            # ヘッダー行（最上部）
            header_height = boundaries_px[1] if len(boundaries_px) > 1 else img_height
            header_row = image.crop((0, 0, img_width, min(header_height + overlap, img_height)))

            for sec_idx in range(num_sections):
                y_start = max(0, boundaries_px[sec_idx] - overlap)
                y_end = min(img_height, boundaries_px[sec_idx + 1] + overlap)

                sec_img = image.crop((0, y_start, img_width, y_end))
                sec_height = y_end - y_start

                if sec_idx == 0:
                    # 最初のセクションはそのまま
                    patches.append({
                        'image': sec_img,
                        'type': 'h_section',
                        'info': f'水平セクション{sec_idx + 1}/{num_sections}',
                        'patch_index': sec_idx,
                        'total_patches': num_sections,
                        'is_continuation': False
                    })
                else:
                    # 2セクション目以降: ヘッダー行を上に結合
                    # ヘッダーの高さをセクションに合わせてスケール（最大20%まで）
                    scaled_header_height = min(int(sec_height * 0.2), header_row.height)
                    scaled_header = header_row.resize(
                        (img_width, scaled_header_height),
                        Image.Resampling.LANCZOS
                    )

                    combined_height = scaled_header.height + sec_img.height
                    combined = Image.new('RGB', (img_width, combined_height), (255, 255, 255))
                    combined.paste(scaled_header, (0, 0))
                    combined.paste(sec_img, (0, scaled_header.height))

                    patches.append({
                        'image': combined,
                        'type': 'h_section_with_header',
                        'info': f'水平セクション{sec_idx + 1}/{num_sections}（ヘッダー付き）',
                        'patch_index': sec_idx,
                        'total_patches': num_sections,
                        'is_continuation': True
                    })

            logger.info(f"[SmartCrop] 水平分割完了: {len(patches)}パッチ")
            return patches

        # ここに到達することはない（安全弁）
        logger.warning(f"[SmartCrop] 不明なsplit_axis: {split_axis}")
        return [{
            'image': image,
            'type': 'full',
            'info': '全体（フォールバック）',
            'patch_index': 0,
            'total_patches': 1,
            'is_continuation': False
        }]

    # ============================================
    # 【Ver 6.2】F-8 → F-7 構造先行モデル
    # ============================================

    def _extract_headers_from_f8(self, f8_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        【Ver 6.6】F-8 の結果からヘッダー情報を抽出（万能受け入れ版）

        F-8 が検出した表構造からヘッダー（X軸・Y軸）を抽出し、
        各ヘッダーの物理座標（BBox）も含めて返す。

        【Ver 6.6】辞書型・文字列型・数値型すべてを受け入れる。

        Args:
            f8_result: F-8 の出力

        Returns:
            {
                'x_headers': ['2/1', '2/2', ...],
                'y_headers': ['74', '73', ...],
                'header_coords': {
                    '2/1': {'x': 100, 'y': 50, 'bbox': [...]},
                    '74': {'x': 30, 'y': 200, 'bbox': [...]},
                    ...
                }
            }
        """
        result = {
            'x_headers': [],
            'y_headers': [],
            'header_coords': {}
        }

        # ヘッダー抽出ヘルパー関数（どんな形式でもテキストと座標を抜き出す）
        def extract_text_and_bbox(item):
            """辞書・文字列・数値のいずれでもテキストとbboxを抽出"""
            if isinstance(item, dict):
                # 辞書型なら text / value キーと bbox キーを探す
                text = item.get('text') or item.get('value') or ''
                bbox = item.get('bbox')
                return str(text).strip(), bbox
            elif isinstance(item, str):
                # 文字列ならそのまま
                return item.strip(), None
            elif isinstance(item, (int, float)):
                # 数値なら文字列化
                return str(item), None
            return "", None

        def register_header(text: str, bbox, target_list: list, header_coords: dict):
            """ヘッダーをリストと座標マップに登録"""
            if text and text not in target_list:
                target_list.append(text)
                if bbox and isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                    cx = (bbox[0] + bbox[2]) / 2
                    cy = (bbox[1] + bbox[3]) / 2
                    header_coords[text] = {'x': cx, 'y': cy, 'bbox': list(bbox)}

        # 【Ver 6.6】トップレベルのヘッダーを優先取得（辞書型も受け入れ）
        top_x = f8_result.get('x_headers', [])
        top_y = f8_result.get('y_headers', [])

        for xh in (top_x or []):
            text, bbox = extract_text_and_bbox(xh)
            register_header(text, bbox, result['x_headers'], result['header_coords'])

        for yh in (top_y or []):
            text, bbox = extract_text_and_bbox(yh)
            register_header(text, bbox, result['y_headers'], result['header_coords'])

        # F-8 の表構造からも抽出（トップレベルで不足している場合の補完）
        tables = f8_result.get('tables', [])
        for table in tables:
            # X軸ヘッダー（列見出し）
            x_headers = table.get('column_headers', []) or table.get('x_headers', [])
            for xh in (x_headers or []):
                text, bbox = extract_text_and_bbox(xh)
                register_header(text, bbox, result['x_headers'], result['header_coords'])

            # Y軸ヘッダー（行見出し）
            y_headers = table.get('row_headers', []) or table.get('y_headers', [])
            for yh in (y_headers or []):
                text, bbox = extract_text_and_bbox(yh)
                register_header(text, bbox, result['y_headers'], result['header_coords'])

        logger.info(f"[F-8→F-7] ヘッダー抽出(改良版): X={len(result['x_headers'])}個, Y={len(result['y_headers'])}個")
        logger.debug(f"[F-8→F-7] Xヘッダー: {result['x_headers']}")
        logger.debug(f"[F-8→F-7] Yヘッダー: {result['y_headers']}")
        return result

    def _f7_path_a_chunk_extraction_v62(
        self,
        chunk_pages: List[Dict],
        blocks: List[Dict],
        chunk_idx: int,
        chunk_start_page: int,
        column_boundaries: List[int] = None
    ) -> Dict[str, Any]:
        """
        【Ver 6.5】F-7 + F-7.5: 文字読み取り + 座標マッピング

        ┌─────────────────────────────────────────────────┐
        │ F-7 (AI): 画像内の全文字を座標付きで出力        │
        │   出力: {"texts": [{"text": "...", "x": n, "y": m}, ...]}
        │                                                 │
        │ F-7.5 (PROGRAM): 座標距離でSurya IDと紐付け     │
        │   入力: F-7出力 + Surya block_coords            │
        │   出力: [{"id": "p0_b5", "text": "...", ...}, ...]
        └─────────────────────────────────────────────────┘

        Args:
            chunk_pages: ページ画像リスト
            blocks: Suryaで検出したブロック（座標情報付き）
            chunk_idx: チャンクインデックス
            chunk_start_page: 開始ページ番号
            column_boundaries: 列境界

        Returns:
            抽出結果（mapped_texts + block_coords）
        """
        import tempfile
        f7_start = time.time()
        logger.info(f"[F-7] 【Ver 6.4】チャンク{chunk_idx + 1} 文字読み取り開始")
        logger.info(f"  ├─ 【Ver 6.4確認】受け取ったブロック数: {len(blocks)}個")

        if not chunk_pages or 'image' not in chunk_pages[0]:
            logger.error(f"[F-7] チャンク{chunk_idx + 1} 画像データなし")
            return {"error": "No image data", "raw_texts": [], "block_coords": {}, "full_text_ordered": ""}

        original_image: Image.Image = chunk_pages[0]['image']

        # ID焼き込み画像を生成
        id_burned_image = self._f6_burn_ids_to_image(original_image, blocks, chunk_start_page)

        # ブロックの座標マップを構築（F-9で使用）
        block_coords = {}
        for block in blocks:
            block_id = block.get('block_id', '')
            bbox = block.get('bbox')
            if block_id and bbox:
                if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                    x_center = (bbox[0] + bbox[2]) / 2
                    y_center = (bbox[1] + bbox[3]) / 2
                    block_coords[block_id] = {'x': x_center, 'y': y_center, 'bbox': bbox}

        # 一時ファイルに保存してAIに渡す
        with tempfile.NamedTemporaryFile(suffix=f'_chunk{chunk_idx}_v64.png', delete=False) as f:
            id_burned_image.save(f, format='PNG')
            temp_path = Path(f.name)

        try:
            # Ver 6.4 プロンプト: AIは文字を読むだけ
            prompt = self._build_f7_prompt()

            response = self.llm_client.generate_with_vision(
                prompt=prompt,
                image_path=str(temp_path),
                model=F7_MODEL_IMAGE,
                max_tokens=F7_F8_MAX_TOKENS,
                temperature=F7_F8_TEMPERATURE,
                response_format="json"
            )

            # 【Ver 6.4】生成物ログ出力（MAX_TOKENS途切れ対応）
            logger.info(f"[F-7] ===== 生成物ログ開始 =====")
            logger.info(f"[F-7] レスポンス長: {len(response) if response else 0}文字")
            logger.info(f"[F-7] 生レスポンス:\n{response if response else '(empty)'}")
            logger.info(f"[F-7] ===== 生成物ログ終了 =====")

            # JSONパース
            try:
                result = json.loads(response)
            except json.JSONDecodeError as jde:
                logger.warning(f"[F-7] JSONパース失敗（MAX_TOKENS?）: {jde}")
                logger.warning(f"[F-7] 途切れたレスポンス末尾: ...{response[-500:] if response and len(response) > 500 else response}")
                import json_repair
                result = json_repair.repair_json(response, return_objects=True)

            f7_elapsed = time.time() - f7_start
            raw_texts = result.get('texts', [])
            logger.info(f"[F-7] 【Ver 6.5】AIテキスト読み取り完了: {f7_elapsed:.2f}秒, {len(raw_texts)}件")

            # ============================================
            # 【Ver 6.5】F-7.5: 座標ベースのID紐付け（PROGRAM）
            # AIは文字+座標を出力、プログラムがID紐付けを行う
            # ============================================
            mapped_texts = self._f7_5_coordinate_mapping(raw_texts, block_coords)

            # 結果を整形
            result['raw_texts'] = raw_texts
            result['mapped_texts'] = mapped_texts
            result['block_coords'] = block_coords
            result['_v65_f75_mapped'] = True

            # メインループ互換: extracted_texts と full_text_ordered をセット
            extracted_texts = []
            full_text_parts = []
            for item in mapped_texts:
                block_id = item.get('id')
                text = item.get('text', '')
                coords = {'x': item.get('x', 0), 'y': item.get('y', 0)}

                extracted_texts.append({
                    'block_id': block_id if block_id else 'null',
                    'text': text,
                    'coords': coords,
                    'page': chunk_start_page,
                    '_distance': item.get('distance', 0),
                    '_over_threshold': item.get('_over_threshold', False)
                })
                if text:
                    full_text_parts.append(text)

            result['extracted_texts'] = extracted_texts
            result['full_text_ordered'] = '\n'.join(full_text_parts)

            logger.info(f"[F-7.5] 【Ver 6.5】マッピング完了: {len(extracted_texts)}件")

            return result

        except MaxTokensExceededError as mte:
            # 【Ver 6.5】MAX_TOKENS到達時も部分出力をログに記録
            logger.error(f"[F-7] MAX_TOKENS到達: {mte}")
            logger.info(f"[F-7] ===== MAX_TOKENS部分出力ログ開始 =====")
            logger.info(f"[F-7] 部分出力長: {len(mte.partial_output)}文字")
            logger.info(f"[F-7] 部分出力（全文）:\n{mte.partial_output}")
            logger.info(f"[F-7] ===== MAX_TOKENS部分出力ログ終了 =====")

            # 部分出力でもパースを試みる
            try:
                import json_repair
                result = json_repair.repair_json(mte.partial_output, return_objects=True)
                if isinstance(result, dict) and 'texts' in result:
                    raw_texts = result.get('texts', [])
                    logger.info(f"[F-7] 部分出力からテキスト復元成功: {len(raw_texts)}件")

                    # F-7.5でマッピング
                    mapped_texts = self._f7_5_coordinate_mapping(raw_texts, block_coords)
                    result['mapped_texts'] = mapped_texts
                    result['block_coords'] = block_coords
                    result['_max_tokens_partial'] = True
                    result['_v65_f75_mapped'] = True

                    # extracted_texts を構築
                    extracted_texts = []
                    full_text_parts = []
                    for item in mapped_texts:
                        extracted_texts.append({
                            'block_id': item.get('id') or 'null',
                            'text': item.get('text', ''),
                            'coords': {'x': item.get('x', 0), 'y': item.get('y', 0)},
                            'page': chunk_start_page
                        })
                        if item.get('text'):
                            full_text_parts.append(item['text'])

                    result['extracted_texts'] = extracted_texts
                    result['full_text_ordered'] = '\n'.join(full_text_parts)
                    return result
            except Exception as parse_err:
                logger.warning(f"[F-7] 部分出力のパース失敗: {parse_err}")

            return {"error": str(mte), "raw_texts": [], "mapped_texts": [], "block_coords": block_coords, "full_text_ordered": "", "_max_tokens_partial": True}

        except Exception as e:
            logger.error(f"[F-7] 【Ver 6.4】エラー: {e}")
            return {"error": str(e), "raw_texts": [], "block_coords": {}, "full_text_ordered": ""}

        finally:
            try:
                temp_path.unlink()
            except:
                pass

    def _map_texts_to_headers_by_coords(
        self,
        ai_texts: List[List[str]],
        block_coords: Dict[str, Dict],
        f8_headers: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        【Ver 6.2 核心】プログラムによる座標マッピング

        AIが読んだテキストを、Surya座標を使って最短距離のヘッダーに紐付ける。
        AIの判断は一切信用しない。物理座標のみを信じる。

        Args:
            ai_texts: AIが読んだ [[ID, テキスト], ...] の配列
            block_coords: {block_id: {x, y, bbox}} の座標マップ
            f8_headers: F-8 から抽出したヘッダー情報

        Returns:
            tagged_texts: [{text, x_header, y_header, ...}, ...]
        """
        tagged_texts = []
        header_coords = f8_headers.get('header_coords', {})
        x_headers = f8_headers.get('x_headers', [])
        y_headers = f8_headers.get('y_headers', [])

        for item in ai_texts:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue

            block_id = str(item[0]).strip()
            text = str(item[1]).strip()

            if not text:
                continue

            # 短縮ID（#5 → p0_r5 など）を正規化
            normalized_id = self._normalize_block_id(block_id)

            # このブロックの座標を取得
            coords = block_coords.get(normalized_id) or block_coords.get(block_id)
            if not coords:
                # 座標がない場合は untagged として扱う
                tagged_texts.append({
                    'id': block_id,
                    'text': text,
                    'x_header': '',
                    'y_header': '',
                    'type': 'untagged',
                    '_no_coords': True
                })
                continue

            text_x = coords['x']
            text_y = coords['y']

            # ============================================
            # 最短距離でX軸ヘッダーを決定
            # ============================================
            nearest_x_header = ''
            min_x_distance = float('inf')

            for xh in x_headers:
                if xh in header_coords:
                    xh_x = header_coords[xh]['x']
                    distance = abs(text_x - xh_x)
                    if distance < min_x_distance:
                        min_x_distance = distance
                        nearest_x_header = xh

            # ============================================
            # 最短距離でY軸ヘッダーを決定
            # ============================================
            nearest_y_header = ''
            min_y_distance = float('inf')

            for yh in y_headers:
                if yh in header_coords:
                    yh_y = header_coords[yh]['y']
                    distance = abs(text_y - yh_y)
                    if distance < min_y_distance:
                        min_y_distance = distance
                        nearest_y_header = yh

            # ヘッダーが見つかったかどうかで type を決定
            if nearest_x_header or nearest_y_header:
                item_type = 'cell'
            else:
                item_type = 'untagged'

            tagged_texts.append({
                'id': block_id,
                'text': text,
                'x_header': nearest_x_header,
                'y_header': nearest_y_header,
                'type': item_type,
                '_x_distance': min_x_distance if nearest_x_header else None,
                '_y_distance': min_y_distance if nearest_y_header else None
            })

            logger.debug(f"[F-7座標] #{block_id}='{text}' → X='{nearest_x_header}'(d={min_x_distance:.1f}), Y='{nearest_y_header}'(d={min_y_distance:.1f})")

        return tagged_texts

    def _normalize_block_id(self, short_id: str) -> str:
        """
        短縮ID（#5）を正規化されたblock_id（p0_r5）に変換

        現在の _id_mapping を使用して逆引き
        """
        if not hasattr(self, '_id_mapping') or not self._id_mapping:
            return short_id

        # _id_mapping は {block_id: {bbox, page, ...}} 形式
        # short_id が "5" の場合、"p0_r5" や "r5" を探す
        for block_id in self._id_mapping.keys():
            if block_id.endswith(f'_{short_id}') or block_id.endswith(f'r{short_id}'):
                return block_id

        return short_id

    def _f7_path_a_chunk_extraction(
        self,
        chunk_pages: List[Dict],
        blocks: List[Dict],
        chunk_idx: int,
        chunk_start_page: int,
        column_boundaries: List[int] = None
    ) -> Dict[str, Any]:
        """
        【Ver 4.0】F-7 Path A: 座標排除・ID焼き込み版

        【設計原則】
        1. 座標データはAIに渡さない
        2. ID焼き込み画像を使用
        3. AIは「見たまま」構造をマッピング
        """
        import tempfile
        f7_start = time.time()
        logger.info(f"[F-7] Path A - チャンク{chunk_idx + 1} ID焼き込み版開始")

        # 元画像を取得
        if not chunk_pages or 'image' not in chunk_pages[0]:
            logger.error(f"[F-7] チャンク{chunk_idx + 1} 画像データなし")
            return {"error": "No image data", "extracted_texts": [], "tables": [], "full_text_ordered": ""}

        original_image: Image.Image = chunk_pages[0]['image']

        # 【Ver 4.0】ID焼き込み画像を生成
        id_burned_image = self._f6_burn_ids_to_image(original_image, blocks, chunk_start_page)

        # スマートクロッピングでパッチを生成（ID焼き込み済み画像から）
        patches = self._smart_crop_patches(id_burned_image, column_boundaries, overlap=50)

        # 各パッチを処理
        all_extracted_texts = []
        all_tables = []
        all_full_texts = []
        patch_errors = []

        for patch_idx, patch in enumerate(patches):
            patch_img = patch['image']
            patch_type = patch['type']
            patch_info = patch['info']
            is_continuation = patch.get('is_continuation', False)

            logger.info(f"[F-7] パッチ{patch_idx + 1}/{len(patches)}: {patch_info} ({patch_img.size[0]}x{patch_img.size[1]}) 継続={is_continuation}")

            # 一時ファイルに保存
            with tempfile.NamedTemporaryFile(suffix=f'_chunk{chunk_idx}_patch{patch_idx}.png', delete=False) as f:
                patch_img.save(f, format='PNG')
                temp_path = Path(f.name)

            try:
                # 【Ver 4.0】座標なしプロンプト
                prompt = self._build_f7_smart_prompt(
                    chunk_idx, chunk_start_page, patch_info, patch_type, len(patches),
                    patch_index=patch_idx, is_continuation=is_continuation
                )

                response = self.llm_client.generate_with_vision(
                    prompt=prompt,
                    image_path=str(temp_path),
                    model=F7_MODEL_IMAGE,
                    max_tokens=F7_F8_MAX_TOKENS,
                    temperature=F7_F8_TEMPERATURE,
                    response_format="json"
                )

                # 【Ver 6.4】生成物ログ出力（MAX_TOKENS途切れ対応）
                logger.info(f"[F-7] ===== 生成物ログ開始（パッチ{patch_idx + 1}） =====")
                logger.info(f"[F-7] レスポンス長: {len(response) if response else 0}文字")
                logger.info(f"[F-7] 生レスポンス:\n{response if response else '(empty)'}")
                logger.info(f"[F-7] ===== 生成物ログ終了 =====")

                # JSON パース
                try:
                    result = json.loads(response)
                except json.JSONDecodeError as jde:
                    logger.warning(f"[F-7] JSONパース失敗（MAX_TOKENS?）: {jde}")
                    logger.warning(f"[F-7] 途切れたレスポンス末尾: ...{response[-500:] if response and len(response) > 500 else response}")
                    import json_repair
                    result = json_repair.repair_json(response, return_objects=True)

                # 【診断】Gemini応答の中身を確認
                logger.info(f"[F-7完了] パッチ{patch_idx + 1}/{len(patches)}: {len(response)}文字")
                logger.info(f"[F-7診断] result keys: {list(result.keys()) if isinstance(result, dict) else 'NOT A DICT'}")

                # ============================================
                # 【Ver 6.4】最軽量形式の展開（texts のみ）
                # AIは [ID, テキスト] ペアのみを出力
                # ヘッダー割り当てはF-9が座標ベースで実行
                # ============================================
                if 'texts' in result and isinstance(result.get('texts'), list):
                    raw_texts = result['texts']
                    extracted_texts = []
                    full_text_parts = []

                    for item in raw_texts:
                        if isinstance(item, (list, tuple)) and len(item) >= 2:
                            block_id = str(item[0]).strip()
                            text = str(item[1]).strip()
                            coords = block_coords.get(block_id, {})
                            extracted_texts.append({
                                'block_id': block_id,
                                'text': text,
                                'coords': coords,
                                'page': chunk_start_page,
                                'patch_idx': patch_idx,
                                'type': 'cell'  # F-9 で座標ベースで再分類される
                            })
                            if text:
                                full_text_parts.append(text)

                    result['extracted_texts'] = extracted_texts
                    result['raw_texts'] = raw_texts
                    result['full_text_ordered'] = '\n'.join(full_text_parts)
                    result['_v64_format'] = True

                    all_extracted_texts.extend(extracted_texts)
                    all_full_texts.append(result['full_text_ordered'])

                    logger.info(f"[F-7] 【Ver 6.4】パッチ{patch_idx + 1}: {len(extracted_texts)}ブロック抽出")

                    # 【Ver 6.4】Ver 5.0 の後続処理をスキップ（重複防止）
                    continue

                # ============================================
                # 【Ver 6.2 互換】CSV型JSON形式の展開（ID:テキスト溶接対応）
                # cols + rows 形式を tagged_texts 形式に変換
                # ============================================
                elif 'cols' in result and 'rows' in result:
                    cols = result.get('cols', [])
                    rows = result.get('rows', [])

                    # Ver 6.2 形式かどうかを判定（id, xh, yh 列の存在）
                    is_v62 = 'id' in cols and 'xh' in cols and 'yh' in cols
                    logger.info(f"[F-7診断] 【Ver 6.2】CSV型JSON検出: {len(cols)}列 x {len(rows)}行 (v6.2={is_v62})")

                    if is_v62:
                        # ============================================
                        # 【Ver 6.2】ID:テキスト溶接形式の展開
                        # ============================================
                        id_idx = cols.index('id')
                        text_idx = cols.index('text')
                        xh_idx = cols.index('xh')
                        yh_idx = cols.index('yh')
                        type_idx = cols.index('type') if 'type' in cols else -1

                        tagged_texts = []
                        untagged_texts = []
                        x_headers = []
                        y_headers = []

                        for row in rows:
                            if not isinstance(row, list) or len(row) < 4:
                                continue

                            item_id = str(row[id_idx]).strip() if len(row) > id_idx else ""
                            text_val = str(row[text_idx]).strip() if len(row) > text_idx else ""
                            xh_val = str(row[xh_idx]).strip() if len(row) > xh_idx else ""
                            yh_val = str(row[yh_idx]).strip() if len(row) > yh_idx else ""
                            type_val = str(row[type_idx]).strip().lower() if type_idx >= 0 and len(row) > type_idx else "cell"

                            if not text_val:
                                continue

                            # ヘッダーを収集
                            if type_val == "x_header":
                                x_headers.append(text_val)
                                continue
                            elif type_val == "y_header":
                                y_headers.append(text_val)
                                continue
                            elif type_val in ("title", "note"):
                                untagged_texts.append({
                                    "id": item_id,
                                    "text": text_val,
                                    "type": type_val
                                })
                                continue

                            # セルデータ: "ID:テキスト" 形式をパース
                            x_header = self._parse_id_text_header(xh_val)
                            y_header = self._parse_id_text_header(yh_val)

                            tagged_texts.append({
                                "id": item_id,
                                "text": text_val,
                                "x_header": x_header,
                                "y_header": y_header,
                                "xh_raw": xh_val,  # 生の "ID:テキスト" を保持（デバッグ用）
                                "yh_raw": yh_val
                            })

                        # ヘッダーをresultに追加
                        if x_headers:
                            result['x_headers'] = x_headers
                        if y_headers:
                            result['y_headers'] = y_headers
                        result['tagged_texts'] = tagged_texts
                        result['untagged_texts'] = untagged_texts
                        result['_v62_format'] = True  # Ver 6.2 フラグ

                        logger.info(f"[F-7診断] 【Ver 6.2】展開完了: x_headers={len(x_headers)}, y_headers={len(y_headers)}, tagged={len(tagged_texts)}, untagged={len(untagged_texts)}")

                    else:
                        # ============================================
                        # 【Ver 6.1 互換】従来形式の展開
                        # ============================================
                        text_idx = cols.index('text') if 'text' in cols else 0
                        x_h_idx = cols.index('x_h') if 'x_h' in cols else 1
                        y_h_idx = cols.index('y_h') if 'y_h' in cols else 2
                        type_idx = cols.index('type') if 'type' in cols else 3

                        tagged_texts = []
                        untagged_texts = []

                        for row in rows:
                            if not isinstance(row, list) or len(row) < 1:
                                continue

                            text_val = row[text_idx] if len(row) > text_idx else ""
                            x_h_val = row[x_h_idx] if len(row) > x_h_idx else ""
                            y_h_val = row[y_h_idx] if len(row) > y_h_idx else ""
                            type_val = row[type_idx] if len(row) > type_idx else "tagged"

                            if not text_val:
                                continue

                            if type_val == "tagged" and (x_h_val or y_h_val):
                                tagged_texts.append({
                                    "text": str(text_val),
                                    "x_header": str(x_h_val),
                                    "y_header": str(y_h_val)
                                })
                            else:
                                untagged_texts.append({
                                    "text": str(text_val),
                                    "type": str(type_val) if type_val != "tagged" else "note"
                                })

                        result['tagged_texts'] = tagged_texts
                        result['untagged_texts'] = untagged_texts

                        logger.info(f"[F-7診断] 【Ver 6.1】展開完了: tagged={len(tagged_texts)}, untagged={len(untagged_texts)}")

                logger.info(f"[F-7診断] x_headers: {len(result.get('x_headers', []))}個")
                logger.info(f"[F-7診断] y_headers: {len(result.get('y_headers', []))}個")
                logger.info(f"[F-7診断] tagged_texts: {len(result.get('tagged_texts', []))}個")
                logger.info(f"[F-7診断] untagged_texts: {len(result.get('untagged_texts', []))}個")
                logger.info(f"[F-7診断] cells(旧形式): {len(result.get('cells', []))}個")
                logger.info(f"[F-7診断] texts(旧形式): {len(result.get('texts', []))}個")

                # トークン使用量を収集
                if hasattr(self.llm_client, 'last_usage') and self.llm_client.last_usage:
                    usage = self.llm_client.last_usage.copy()
                    usage['chunk_idx'] = chunk_idx
                    usage['patch_idx'] = patch_idx
                    self._f7_usage.append(usage)

                # ============================================
                # 【Ver 5.0】ヘッダータグ付きテキストの収集
                # グリッド思考を完全廃止。各テキストにヘッダーをタグ付け。
                # ============================================

                # ヘッダー情報を収集
                x_headers = result.get("x_headers", [])
                y_headers = result.get("y_headers", [])

                # タグ付きテキストを収集
                for tagged in result.get("tagged_texts", []):
                    if isinstance(tagged, dict) and tagged.get("text"):
                        text_block = {
                            "id": tagged.get("id", ""),
                            "text": str(tagged["text"]),
                            "x_header": tagged.get("x_header", ""),
                            "y_header": tagged.get("y_header", ""),
                            "patch_idx": patch_idx,
                            "patch_info": patch_info,
                            "type": "tagged"
                        }
                        all_extracted_texts.append(text_block)

                # タグなしテキスト（タイトル、注釈など）を収集
                for untagged in result.get("untagged_texts", []):
                    if isinstance(untagged, dict) and untagged.get("text"):
                        text_block = {
                            "id": untagged.get("id", ""),
                            "text": str(untagged["text"]),
                            "x_header": "",
                            "y_header": "",
                            "patch_idx": patch_idx,
                            "patch_info": patch_info,
                            "type": untagged.get("type", "note")
                        }
                        all_extracted_texts.append(text_block)

                # 【後方互換】旧形式(cells/texts)もサポート
                for cell in result.get("cells", []):
                    if isinstance(cell, dict) and cell.get("value"):
                        text_block = {
                            "id": cell.get("id", ""),
                            "text": str(cell["value"]),
                            "x_header": cell.get("h_header", ""),
                            "y_header": cell.get("v_header", ""),
                            "patch_idx": patch_idx,
                            "patch_info": patch_info,
                            "type": "cell"
                        }
                        all_extracted_texts.append(text_block)

                for text_item in result.get("texts", []):
                    if isinstance(text_item, dict) and text_item.get("value"):
                        text_block = {
                            "id": text_item.get("id", ""),
                            "text": str(text_item["value"]),
                            "x_header": "",
                            "y_header": "",
                            "patch_idx": patch_idx,
                            "patch_info": patch_info,
                            "type": text_item.get("type", "text")
                        }
                        all_extracted_texts.append(text_block)

                # 【Ver 5.6】ヘッダー情報とtagged_textsを統合テーブルとして保存
                if x_headers or y_headers:
                    # このパッチのtagged_textsを収集
                    patch_tagged_texts = [
                        t for t in all_extracted_texts
                        if t.get("patch_idx") == patch_idx and t.get("type") == "tagged"
                    ]
                    all_tables.append({
                        "type": "ver5_tagged_table",
                        "block_id": f"ver5_patch{patch_idx}",
                        "x_headers": x_headers,
                        "y_headers": y_headers,
                        "tagged_texts": patch_tagged_texts,
                        "patch_idx": patch_idx,
                        "patch_info": patch_info,
                        "table_type": "deviation_table"  # 偏差値表
                    })

                # full_text_ordered の構築
                patch_text_parts = [t.get("text", "") for t in all_extracted_texts if t.get("patch_idx") == patch_idx]
                patch_full_text = "\n".join(patch_text_parts)
                all_full_texts.append(patch_full_text)

                # 【診断】収集結果
                patch_texts_count = len([t for t in all_extracted_texts if t.get('patch_idx') == patch_idx])
                patch_tables_count = len([t for t in all_tables if t.get('patch_idx') == patch_idx])
                logger.info(f"[F-7診断] パッチ{patch_idx + 1} 収集完了:")
                logger.info(f"  ├─ all_extracted_texts に追加: {patch_texts_count}個")
                logger.info(f"  ├─ all_tables に追加: {patch_tables_count}個")
                logger.info(f"  ├─ x_headers: {x_headers}")
                logger.info(f"  └─ y_headers: {y_headers[:5]}... (計{len(y_headers)}個)" if len(y_headers) > 5 else f"  └─ y_headers: {y_headers}")

            except Exception as e:
                logger.error(f"[F-7] パッチ{patch_idx + 1}/{len(patches)} エラー: {e}")
                patch_errors.append(f"patch{patch_idx}: {str(e)}")

            finally:
                try:
                    temp_path.unlink()
                except:
                    pass

        f7_elapsed = time.time() - f7_start
        logger.info(f"[F-7完了] チャンク{chunk_idx + 1} 全{len(patches)}パッチ完了: {f7_elapsed:.2f}秒")

        # 【診断】最終集計
        logger.info(f"[F-7診断] === 最終集計 ===")
        logger.info(f"  ├─ all_extracted_texts 総数: {len(all_extracted_texts)}")
        logger.info(f"  ├─ all_tables 総数: {len(all_tables)}")
        logger.info(f"  ├─ all_full_texts 総文字数: {sum(len(t) for t in all_full_texts)}")
        tagged_count = len([t for t in all_extracted_texts if t.get('type') == 'tagged'])
        logger.info(f"  └─ うち tagged タイプ: {tagged_count}")

        return {
            "extracted_texts": all_extracted_texts,
            "tables": all_tables,
            "full_text_ordered": "\n\n".join(all_full_texts),
            "patch_count": len(patches),
            "errors": patch_errors if patch_errors else None
        }

    def _build_f7_smart_prompt(
        self,
        chunk_idx: int,
        start_page: int,
        patch_info: str,
        patch_type: str,
        total_patches: int,
        patch_index: int = 0,
        is_continuation: bool = False
    ) -> str:
        """
        【Ver 6.4】F-7 軽量化プロンプト

        AIの仕事: 黄色ラベル(#ID)の横の文字を読むだけ
        プログラムの仕事: 座標からヘッダーを自動計算
        """
        base_prompt = self._build_f7_prompt()

        # 【Ver 6.4】L字型パッチの複雑な説明を削除
        # ヘッダー特定はプログラムが座標ベースで行うため、AIには不要

        context = f"""
## 画像情報
- ページ: {start_page + 1}
- パッチ: {patch_index + 1}/{total_patches}
"""
        return base_prompt + context

    def _f7_process_single_image(
        self,
        image: Image.Image,
        chunk_idx: int,
        chunk_start_page: int,
        f7_start: float
    ) -> Dict[str, Any]:
        """【Ver 4.0】F-7: 単一画像処理（座標排除版）"""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=f'_chunk{chunk_idx}.png', delete=False) as f:
            image.save(f, format='PNG')
            temp_image_path = Path(f.name)

        prompt = self._build_f7_chunk_prompt(chunk_idx, chunk_start_page, 1)

        try:
            response = self.llm_client.generate_with_vision(
                prompt=prompt,
                image_path=str(temp_image_path),
                model=F7_MODEL_IMAGE,
                max_tokens=F7_F8_MAX_TOKENS,
                temperature=F7_F8_TEMPERATURE,
                response_format="json"
            )

            # 【Ver 6.4】生成物ログ出力（MAX_TOKENS途切れ対応）
            logger.info(f"[F-7] ===== 生成物ログ開始（チャンク{chunk_idx + 1}） =====")
            logger.info(f"[F-7] レスポンス長: {len(response) if response else 0}文字")
            logger.info(f"[F-7] 生レスポンス:\n{response if response else '(empty)'}")
            logger.info(f"[F-7] ===== 生成物ログ終了 =====")

            try:
                result = json.loads(response)
            except json.JSONDecodeError as jde:
                logger.warning(f"[F-7] JSONパース失敗（MAX_TOKENS?）: {jde}")
                logger.warning(f"[F-7] 途切れたレスポンス末尾: ...{response[-500:] if response and len(response) > 500 else response}")
                import json_repair
                result = json_repair.repair_json(response, return_objects=True)

            f7_elapsed = time.time() - f7_start
            logger.info(f"[F-7完了] チャンク{chunk_idx + 1} Path A: {len(response)}文字, {f7_elapsed:.2f}秒")

            if hasattr(self.llm_client, 'last_usage') and self.llm_client.last_usage:
                usage = self.llm_client.last_usage.copy()
                usage['chunk_idx'] = chunk_idx
                self._f7_usage.append(usage)

            return result

        except Exception as e:
            logger.error(f"[F-7] チャンク{chunk_idx + 1} Path A エラー: {e}")
            return {"error": str(e), "extracted_texts": [], "tables": [], "full_text_ordered": ""}

        finally:
            try:
                temp_image_path.unlink()
            except:
                pass

    def _build_f7_column_prompt(self, chunk_idx: int, start_page: int, col_idx: int, total_cols: int) -> str:
        """【Ver 6.4】F-7列用プロンプト（最小化版）"""
        base_prompt = self._build_f7_prompt()

        # 【Ver 6.4】最小限のコンテキストのみ
        column_info = f"""
## 画像情報
- ページ: {start_page + 1}, 列: {col_idx + 1}/{total_cols}
"""
        return base_prompt + column_info

    def _build_f7_chunk_prompt(self, chunk_idx: int, start_page: int, page_count: int) -> str:
        """【Ver 6.4】F-7チャンク用プロンプト（最小化版）"""
        base_prompt = self._build_f7_prompt()

        # 【Ver 6.4】最小限のコンテキストのみ
        chunk_info = f"""
## 画像情報
- ページ: {start_page + 1}〜{start_page + page_count}
"""
        return base_prompt + chunk_info

    def _f8_path_b_chunk_analysis(
        self,
        chunk_pages: List[Dict],
        blocks: List[Dict],
        chunk_idx: int,
        chunk_start_page: int
    ) -> Dict[str, Any]:
        """
        【Ver 4.0】F-8 Path B: 座標排除版・構造解析

        Args:
            chunk_pages: このチャンクのページ画像リスト
            blocks: ブロックリスト（座標はAIに渡さない）
            chunk_idx: チャンクインデックス
            chunk_start_page: このチャンクの開始ページ番号
        """
        f8_start = time.time()
        logger.info(f"[F-8] Path B - チャンク{chunk_idx + 1} Visual Analysis 開始（座標排除版）")

        # チャンク画像を一時ファイルに保存
        temp_image_path = self._save_chunk_as_temp_image(chunk_pages, chunk_idx)

        prompt = self._build_f8_chunk_prompt(chunk_idx, chunk_start_page, len(chunk_pages))

        try:
            response = self.llm_client.generate_with_vision(
                prompt=prompt,
                image_path=str(temp_image_path),
                model=F8_MODEL,
                max_tokens=F7_F8_MAX_TOKENS,
                temperature=F7_F8_TEMPERATURE,
                response_format="json"
            )

            # 【Ver 6.4】生成物ログ出力（MAX_TOKENS途切れ対応）
            logger.info(f"[F-8] ===== 生成物ログ開始（チャンク{chunk_idx + 1}） =====")
            logger.info(f"[F-8] レスポンス長: {len(response) if response else 0}文字")
            logger.info(f"[F-8] 生レスポンス:\n{response if response else '(empty)'}")
            logger.info(f"[F-8] ===== 生成物ログ終了 =====")

            # JSON パース
            try:
                result = json.loads(response)
            except json.JSONDecodeError as jde:
                logger.warning(f"[F-8] JSONパース失敗（MAX_TOKENS?）: {jde}")
                logger.warning(f"[F-8] 途切れたレスポンス末尾: ...{response[-500:] if response and len(response) > 500 else response}")
                import json_repair
                result = json_repair.repair_json(response, return_objects=True)

            f8_elapsed = time.time() - f8_start
            logger.info(f"[F-8完了] チャンク{chunk_idx + 1} Path B: {len(response)}文字, {f8_elapsed:.2f}秒")

            # トークン使用量を収集
            if hasattr(self.llm_client, 'last_usage') and self.llm_client.last_usage:
                usage = self.llm_client.last_usage.copy()
                usage['chunk_idx'] = chunk_idx
                self._f8_usage.append(usage)
                logger.info(f"[F-8] トークン使用量: prompt={usage.get('prompt_tokens', 0)}, completion={usage.get('completion_tokens', 0)}")

            return result

        except Exception as e:
            logger.error(f"[F-8] チャンク{chunk_idx + 1} Path B エラー: {e}")
            return {"error": str(e), "tables": [], "diagrams": [], "charts": [], "structured_data_candidates": []}

        finally:
            # 一時ファイル削除
            try:
                temp_image_path.unlink()
            except:
                pass

    def _build_f8_chunk_prompt(self, chunk_idx: int, start_page: int, page_count: int) -> str:
        """【Ver 4.0】F-8チャンク用プロンプト構築（座標排除版）"""
        base_prompt = self._build_f8_prompt()

        chunk_info = f"""
## チャンク情報
- チャンク番号: {chunk_idx + 1}
- ページ範囲: {start_page + 1}〜{start_page + page_count}ページ目
"""
        return base_prompt + chunk_info

    def _merge_chunk_tables(
        self,
        path_a_result: Dict[str, Any],
        path_b_result: Dict[str, Any],
        chunk_idx: int,
        chunk_start_page: int
    ) -> List[Dict[str, Any]]:
        """
        チャンク内の表データをマージ

        Path A（テキスト内容）と Path B（構造情報）を統合し、
        チャンク情報を付加して返す
        """
        path_a_tables = path_a_result.get("tables", [])
        path_b_tables = path_b_result.get("tables", [])

        merged_tables = []
        for a_table in path_a_tables:
            block_id = a_table.get("block_id", "")

            # Path B から対応する構造情報を探す
            b_structure = {}
            for b_table in path_b_tables:
                if b_table.get("block_id") == block_id:
                    b_structure = b_table
                    break

            # columns/headers どちらも受け付ける（カラムナ形式優先）
            columns = a_table.get("columns") or a_table.get("headers", [])
            rows = a_table.get("rows", [])

            merged_table = {
                "block_id": f"chunk{chunk_idx}_{block_id}",  # チャンク情報を付加
                "chunk_idx": chunk_idx,
                "chunk_start_page": chunk_start_page,
                "table_title": a_table.get("table_title", ""),
                "table_type": a_table.get("table_type", b_structure.get("table_type", "visual_table")),
                "columns": columns,
                "rows": rows,
                "row_count": a_table.get("row_count", len(rows)),
                "col_count": a_table.get("col_count", len(columns)),
                "caption": a_table.get("caption", ""),
                # Path B からの構造情報
                "structure": b_structure.get("structure", {}),
                "semantic_role": b_structure.get("semantic_role", ""),
                "data_quality": b_structure.get("data_quality", {}),
                # 【Ver 5.6】ヘッダータグ付きテキストを保持
                "x_headers": a_table.get("x_headers", []),
                "y_headers": a_table.get("y_headers", []),
                "tagged_texts": a_table.get("tagged_texts", [])
            }
            merged_tables.append(merged_table)

        return merged_tables

    # ============================================
    # F-9: Result Convergence
    # ============================================
    def _f9_merge_results(
        self,
        path_a: Dict[str, Any],
        path_b: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        F-9: 抽出結果の集約
        Path AとPath Bの結果をマージ
        特に表データは Path A (テキスト) + Path B (構造) を統合
        """
        f9_start = time.time()
        logger.info("[F-9] Result Convergence 開始")

        # Path A の表データ（テキスト内容）
        path_a_tables = path_a.get("tables", [])
        # Path B の表データ（構造情報）
        path_b_tables = path_b.get("tables", [])
        # Path B の構造化データ候補
        structured_candidates = path_b.get("structured_data_candidates", [])

        # 表データの統合: Path A のテキスト + Path B の構造
        merged_tables = []
        for a_table in path_a_tables:
            block_id = a_table.get("block_id", "")

            # Path B から対応する構造情報を探す
            b_structure = {}
            for b_table in path_b_tables:
                if b_table.get("block_id") == block_id:
                    b_structure = b_table
                    break

            # columns/headers どちらも受け付ける（カラムナ形式優先）
            columns = a_table.get("columns") or a_table.get("headers", [])
            rows = a_table.get("rows", [])

            merged_table = {
                "block_id": block_id,
                "table_title": a_table.get("table_title", ""),
                "table_type": a_table.get("table_type", b_structure.get("table_type", "visual_table")),
                "columns": columns,  # カラムナ形式で統一
                "rows": rows,
                "row_count": a_table.get("row_count", len(rows)),
                "col_count": a_table.get("col_count", len(columns)),
                "caption": a_table.get("caption", ""),
                # Path B からの構造情報
                "structure": b_structure.get("structure", {}),
                "semantic_role": b_structure.get("semantic_role", ""),
                "data_quality": b_structure.get("data_quality", {})
            }
            merged_tables.append(merged_table)

        # 表の統計
        total_rows = sum(t.get("row_count", 0) for t in merged_tables)
        total_tables = len(merged_tables)

        logger.info(f"[F-9] 表統合: {total_tables}テーブル, 合計{total_rows}行")

        merged = {
            "text_source": {
                "full_text": path_a.get("full_text_ordered", ""),
                "blocks": path_a.get("extracted_texts", []),
                "missed_texts": path_a.get("missed_texts", [])
            },
            "tables": merged_tables,  # 統合済み表データ
            "structured_data_candidates": structured_candidates,
            "visual_source": {
                "diagrams": path_b.get("diagrams", []),
                "charts": path_b.get("charts", []),
                "layout": path_b.get("layout_analysis", {})
            },
            "metadata": {
                "path_a_model": F7_MODEL_IMAGE,
                "path_b_model": F8_MODEL,
                "table_count": total_tables,
                "total_table_rows": total_rows
            }
        }

        f9_elapsed = time.time() - f9_start
        logger.info(f"[F-9完了] マージ完了, {f9_elapsed:.2f}秒")

        # 【Ver 6.6】F-9 出力ログ（全文字出力）
        logger.info(f"[F-9] ===== 生成物ログ開始 =====")
        full_text = merged.get('text_source', {}).get('full_text', '')
        logger.info(f"[F-9] full_text長: {len(full_text)}文字")
        if full_text:
            logger.info(f"[F-9] full_text全文:\n{full_text}")
        logger.info(f"[F-9] テーブル数: {len(merged.get('tables', []))}")
        for i, tbl in enumerate(merged.get('tables', [])):
            rows = tbl.get('rows', [])
            cols = tbl.get('columns', [])
            logger.info(f"[F-9]   表{i+1}: {len(cols)}列 x {len(rows)}行")
            for j, row in enumerate(rows):
                logger.info(f"[F-9]     行{j+1}: {row}")
        logger.info(f"[F-9] ===== 生成物ログ終了 =====")

        return merged

    # ============================================
    # F-9 Helper: アンカーパケット生成
    # ============================================
    def _build_anchor_packets(
        self,
        text_blocks: List[Dict],
        tables: List[Dict],
        structured_candidates: List[Dict]
    ) -> List[Dict[str, Any]]:
        """
        アンカーベースのパケット配列を生成

        テキストブロックと表を統一的なアンカー形式に変換し、
        Stage Gでの振り分け（H1/H2）を容易にする

        Args:
            text_blocks: F-7から抽出されたテキストブロック
            tables: F-7/F-8からマージされた表データ
            structured_candidates: F-8で検出された構造化候補

        Returns:
            アンカーパケット配列:
            [
                {"anchor_id": "B-001", "type": "text", "content": "...", "page": 1},
                {"anchor_id": "B-002", "type": "table", "title": "...", "columns": [...], "rows": [...], "is_heavy": true}
            ]
        """
        anchors = []
        anchor_index = 1

        # 表のblock_idを収集（テキストから除外するため）
        table_block_ids = set(t.get("block_id", "") for t in tables)

        # テキストブロックをアンカー化
        for block in text_blocks:
            block_id = block.get("block_id", "")

            # 表として既に処理されているブロックはスキップ
            if block_id in table_block_ids:
                continue

            text = block.get("text", "")
            # 【Ver 6.9】テキスト長フィルタ廃止 - 1文字でも採用
            if not text:
                continue

            anchors.append({
                "anchor_id": f"B-{anchor_index:03d}",
                "original_block_id": block_id,
                "type": "text",
                "block_type": block.get("block_type", "paragraph"),
                "content": text,
                "page": block.get("original_page", block.get("page", 0)),
                "reading_order": block.get("reading_order", 0),
                "confidence": block.get("confidence", "medium"),
                "is_heavy": False  # テキストは常に軽量
            })
            anchor_index += 1

        # 表をアンカー化
        for table in tables:
            block_id = table.get("block_id", "")
            rows = table.get("rows", [])
            columns = table.get("columns", []) or table.get("headers", [])

            # 重い表の判定（20行以上 or 5列以上）
            is_heavy = len(rows) >= 20 or len(columns) >= 5

            anchors.append({
                "anchor_id": f"B-{anchor_index:03d}",
                "original_block_id": block_id,
                "type": "table",
                # 【Ver 6.7】デフォルトをgrid_tableに変更（H1がスキップしないように）
                "table_type": table.get("table_type", "grid_table"),
                "title": table.get("table_title", ""),
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "col_count": len(columns),
                "page": table.get("chunk_start_page", 0),
                "is_heavy": is_heavy,
                "structure": table.get("structure", {}),
                "semantic_role": table.get("semantic_role", "")
            })
            anchor_index += 1

        # 構造化候補をアンカー化（表として検出されなかったが構造化可能なデータ）
        for candidate in structured_candidates:
            anchors.append({
                "anchor_id": f"B-{anchor_index:03d}",
                "type": "structured_candidate",
                "candidate_type": candidate.get("type", "key_value"),
                "content": candidate.get("content", {}),
                "page": candidate.get("page", 0),
                "is_heavy": False
            })
            anchor_index += 1

        # reading_order でソート（テキストの場合）
        anchors.sort(key=lambda x: (x.get("page", 0), x.get("reading_order", 0)))

        logger.info(f"[F-9] アンカー生成: text={sum(1 for a in anchors if a['type'] == 'text')}, "
                   f"table={sum(1 for a in anchors if a['type'] == 'table')}, "
                   f"heavy={sum(1 for a in anchors if a.get('is_heavy', False))}")

        return anchors

    # ============================================
    # 【Ver 6.6】F-9: 物理仕分け + 住所タグの幾何学的割当（即決版）
    # ============================================
    def _f9_physical_sorting(
        self,
        blocks: List[Dict],
        tables: List[Dict],
        f8_headers: Dict[str, Any],
        structured_candidates: List[Dict]
    ) -> Tuple[Dict[str, Any], List[Dict]]:
        """
        【Ver 6.6】F-9: 物理仕分け + 住所タグの幾何学的割当（即決版）

        プログラムによる数学的な座標計算でヘッダーを決定。
        【Ver 6.6】「2番目との差」による迷いを廃止し、最近傍を即採用。
        距離が離れすぎている場合（LIMIT_DIST以上）のみ低信頼度とする。

        Args:
            blocks: テキストブロックリスト
            tables: 表リスト
            f8_headers: F-8 から抽出したヘッダー情報
            structured_candidates: 構造化候補

        Returns:
            (result, low_confidence_items)
            result: {tagged_texts, x_headers, y_headers}
            low_confidence_items: 空（AI救済は使わない）
        """
        # ============================================
        # 【Ver 6.8】F8絶対信頼モード
        # F8が出したヘッダーは全て採用。プログラムによる検閲禁止。
        # ============================================
        x_headers = f8_headers.get('x_headers', [])
        y_headers = f8_headers.get('y_headers', [])
        header_coords = f8_headers.get('header_coords', {})

        logger.info(f"[F-9] 【Ver 6.8】F8絶対信頼: X={len(x_headers)}個, Y={len(y_headers)}個")
        logger.info(f"[F-9] X軸ヘッダー: {x_headers}")
        logger.info(f"[F-9] Y軸ヘッダー（先頭10件）: {y_headers[:10]}{'...' if len(y_headers) > 10 else ''}")

        tagged_texts = []

        # Y軸ヘッダーの範囲を計算（タイトル/注釈を区別するため）
        # X軸の範囲判定は廃止（列が少ない表でも全データを拾う）
        PADDING_Y = 50  # Y方向の余裕を広めに
        y_coords = [header_coords[h]['y'] for h in y_headers if h in header_coords]

        if y_coords:
            valid_y_range = (min(y_coords) - PADDING_Y, max(y_coords) + PADDING_Y)
        else:
            valid_y_range = (0, 1000)  # Y軸ヘッダーがない場合は全域をセル扱い

        logger.info(f"[F-9] Y軸有効範囲: {valid_y_range}")

        cell_count = 0
        text_count = 0

        for block in blocks:
            text = block.get('text', '').strip()
            block_id = block.get('block_id', block.get('id', ''))

            if not text:
                continue

            # ヘッダー自体はスキップ
            if text in x_headers or text in y_headers:
                continue

            # 座標取得
            coords = block.get('coords', {})
            bbox = coords.get('bbox') or block.get('bbox')

            if coords and 'x' in coords and 'y' in coords:
                text_x = coords['x']
                text_y = coords['y']
            elif bbox and isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                text_x = (bbox[0] + bbox[2]) / 2
                text_y = (bbox[1] + bbox[3]) / 2
            else:
                # 座標がない場合はテキスト（untagged）として扱う
                tagged_texts.append({
                    'id': block_id,
                    'text': text,
                    'x_header': '',
                    'y_header': '',
                    'type': 'untagged',
                    '_reason': 'no_coords',
                    'page': block.get('original_page', 0)
                })
                text_count += 1
                continue

            # ============================================
            # 【Ver 6.8】Y軸範囲のみで判定（X軸制限廃止）
            # Y軸ヘッダーの範囲内 → セル（最近傍ヘッダーに割当）
            # Y軸ヘッダーの範囲外 → テキスト（タイトル・注釈）
            # ============================================
            is_in_table_y = valid_y_range[0] <= text_y <= valid_y_range[1]

            if is_in_table_y:
                # ============================================
                # Y軸範囲内: セル（G1行き）として最近傍ヘッダーを探索
                # X軸は無条件で最近傍を採用（距離制限なし）
                # ============================================
                nearest_x = ''
                min_x_dist = float('inf')
                for xh in x_headers:
                    if xh in header_coords:
                        dist = abs(text_x - header_coords[xh]['x'])
                        if dist < min_x_dist:
                            min_x_dist = dist
                            nearest_x = xh

                # X軸ヘッダーがない場合、座標なしでも最初のヘッダーを使う
                if not nearest_x and x_headers:
                    nearest_x = x_headers[0]
                    min_x_dist = 0

                nearest_y = ''
                min_y_dist = float('inf')
                for yh in y_headers:
                    if yh in header_coords:
                        dist = abs(text_y - header_coords[yh]['y'])
                        if dist < min_y_dist:
                            min_y_dist = dist
                            nearest_y = yh

                # Y軸ヘッダーがない場合、座標なしでも最初のヘッダーを使う
                if not nearest_y and y_headers:
                    nearest_y = y_headers[0]
                    min_y_dist = 0

                tagged_texts.append({
                    'id': block_id,
                    'text': text,
                    'x_header': nearest_x,
                    'y_header': nearest_y,
                    'type': 'cell',
                    '_x_distance': min_x_dist,
                    '_y_distance': min_y_dist,
                    '_physical_decision': True,
                    'bbox': bbox,
                    'coords': coords,
                    'page': block.get('original_page', 0)
                })
                cell_count += 1

            else:
                # ============================================
                # Y軸範囲外: テキスト（G2行き）として確定
                # タイトル（上部）・注釈（下部）がここに分類される
                # ============================================
                tagged_texts.append({
                    'id': block_id,
                    'text': text,
                    'x_header': '',
                    'y_header': '',
                    'type': 'untagged',
                    '_reason': f"Y={text_y:.0f} not in {valid_y_range}",
                    'bbox': bbox,
                    'coords': coords,
                    'page': block.get('original_page', 0)
                })
                text_count += 1

        logger.info(f"[F-9] 【Ver 6.7】グリッド・バリア結果: セル(G1)={cell_count}件, テキスト(G2)={text_count}件")

        # 【Ver 6.6】F-9 生成物ログ（全文字出力）
        logger.info(f"[F-9] ===== 生成物ログ開始 =====")
        logger.info(f"[F-9] tagged_texts: {len(tagged_texts)}件")
        logger.info(f"[F-9] x_headers: {x_headers}")
        logger.info(f"[F-9] y_headers: {y_headers}")
        # 全件出力
        for i, tt in enumerate(tagged_texts):
            logger.info(f"[F-9]   [{i+1}] x={tt.get('x_header','')}, y={tt.get('y_header','')}, text='{tt.get('text','')}'")
        logger.info(f"[F-9] ===== 生成物ログ終了 =====")

        # 【Ver 6.7】AI救済は使わない（空リストを返す）
        return {
            'tagged_texts': tagged_texts,
            'x_headers': x_headers,
            'y_headers': y_headers
        }, []

    # ============================================
    # 【Ver 6.2】F-9.5: 低信頼度住所のAIレスキュー
    # ============================================
    def _f95_ai_rescue(
        self,
        low_confidence_items: List[Dict],
        f8_headers: Dict[str, Any],
        image: Image.Image = None
    ) -> List[Dict]:
        """
        【Ver 6.2】F-9.5: 低信頼度住所のAIレスキュー

        プログラムで決定できなかった曖昧なデータを、
        AIに画像を見せて最終判断させる。
        介入件数を透明にログ出力。

        Args:
            low_confidence_items: 低信頼度アイテムリスト
            f8_headers: F-8 から抽出したヘッダー情報
            image: 元画像（AIに見せる用）

        Returns:
            救済されたtagged_textsリスト
        """
        if not low_confidence_items:
            return []

        # 【Ver 6.6】安全弁: 件数が多すぎる場合はAI処理をスキップ（MAX_TOKENS防止）
        MAX_AI_RESCUE_ITEMS = 50
        if len(low_confidence_items) > MAX_AI_RESCUE_ITEMS:
            logger.warning(f"[F-9.5] 【安全弁発動】救済対象が多すぎます({len(low_confidence_items)}件 > {MAX_AI_RESCUE_ITEMS}件)")
            logger.warning(f"[F-9.5] AI処理をスキップし、最近傍ヘッダーを強制採用します。")
            rescued_items = []
            for item in low_confidence_items:
                # 候補の1番目を強制採用（候補がない場合は空文字）
                x_cand = item.get('x_candidates', [''])[0] if item.get('x_candidates') else ''
                y_cand = item.get('y_candidates', [''])[0] if item.get('y_candidates') else ''

                rescued_items.append({
                    'id': item.get('id', ''),
                    'text': item.get('text', ''),
                    'x_header': x_cand,
                    'y_header': y_cand,
                    'type': 'cell' if (x_cand and y_cand) else 'untagged',
                    '_ai_rescued': False,
                    '_fallback': True,
                    '_original_reason': item.get('reason', '')
                })
            logger.info(f"[F-9.5] フォールバック完了: {len(rescued_items)}件を強制採用")
            return rescued_items

        rescued_items = []
        x_headers = f8_headers.get('x_headers', [])
        y_headers = f8_headers.get('y_headers', [])

        # AIに渡すプロンプト
        rescue_prompt = f"""# 住所特定レスキュー（Ver 6.6）

x_headers / y_headers が空の場合、推測で新規ヘッダーを生成してはいけない。
空は「不明」を意味する。対応する header_id は null のままにする。

以下のテキストについて、画像上の位置関係からX軸・Y軸ヘッダーを特定してください。

## 利用可能なヘッダー
- X軸ヘッダー: {json.dumps(x_headers, ensure_ascii=False)}
- Y軸ヘッダー: {json.dumps(y_headers, ensure_ascii=False)}

## 判定対象
{json.dumps([{'id': i['id'], 'text': i['text']} for i in low_confidence_items], ensure_ascii=False, indent=2)}

## 出力形式
```json
{{"rescued": [{{"id": "22", "x_header": "2/1", "y_header": "74"}}]}}
```
特定できない場合は null。説明不要。
"""

        try:
            if image:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix='_f95_rescue.png', delete=False) as f:
                    image.save(f, format='PNG')
                    temp_path = Path(f.name)

                response = self.llm_client.generate_with_vision(
                    prompt=rescue_prompt,
                    image_path=str(temp_path),
                    model=F95_MODEL,  # Ver 6.2: gemini-2.5-flash-lite
                    max_tokens=2000,
                    temperature=0.1,
                    response_format="json"
                )

                try:
                    temp_path.unlink()
                except:
                    pass
            else:
                # 画像がない場合はテキストのみで判断
                response = self.llm_client.generate(
                    prompt=rescue_prompt,
                    model=F95_MODEL,  # Ver 6.2: gemini-2.5-flash-lite
                    max_tokens=2000,
                    temperature=0.1,
                    response_format="json"
                )

            # 【Ver 6.4】生成物ログ出力（MAX_TOKENS途切れ対応）
            logger.info(f"[F-9.5] ===== 生成物ログ開始 =====")
            logger.info(f"[F-9.5] レスポンス長: {len(response) if response else 0}文字")
            logger.info(f"[F-9.5] 生レスポンス:\n{response if response else '(empty)'}")
            logger.info(f"[F-9.5] ===== 生成物ログ終了 =====")

            # JSONパース
            try:
                result = json.loads(response)
            except json.JSONDecodeError as jde:
                logger.warning(f"[F-9.5] JSONパース失敗（MAX_TOKENS?）: {jde}")
                logger.warning(f"[F-9.5] 途切れたレスポンス末尾: ...{response[-500:] if response and len(response) > 500 else response}")
                import json_repair
                result = json_repair.repair_json(response, return_objects=True)

            # 救済結果を処理
            for rescued in result.get('rescued', []):
                item_id = str(rescued.get('id', ''))
                x_h = rescued.get('x_header', '')
                y_h = rescued.get('y_header', '')

                # 元のアイテムを探す
                original = next((i for i in low_confidence_items if str(i.get('id', '')) == item_id), None)
                if original:
                    rescued_items.append({
                        'id': item_id,
                        'text': original.get('text', ''),
                        'x_header': x_h,
                        'y_header': y_h,
                        'type': 'cell' if (x_h and y_h) else 'untagged',
                        '_ai_rescued': True,
                        '_original_reason': original.get('reason', '')
                    })

            logger.info(f"[F-9.5] AI介入: {len(low_confidence_items)}件中{len(rescued_items)}件を救済")

        except Exception as e:
            logger.warning(f"[F-9.5] AIレスキュー失敗: {e}")
            # フォールバック: 最も近いヘッダーを採用
            for item in low_confidence_items:
                rescued_items.append({
                    'id': item.get('id', ''),
                    'text': item.get('text', ''),
                    'x_header': item.get('x_candidates', [''])[0] if item.get('x_candidates') else '',
                    'y_header': item.get('y_candidates', [''])[0] if item.get('y_candidates') else '',
                    'type': 'untagged',
                    '_ai_rescued': False,
                    '_fallback': True
                })

        return rescued_items

    # ============================================
    # 【Ver 6.4】F-10: 正本化 + 異常座標特定
    # ============================================
    def _f10_stage_e_scrubbing(
        self,
        merged_result: Dict[str, Any],
        e_content: str,
        post_body: Optional[Dict]
    ) -> Dict[str, Any]:
        """
        【Ver 6.4】F-10: 正本化と異常座標特定

        役割1: Stage E のデジタル文字で F7 の読みを強制上書き（洗い替え）
        役割2: 証拠がない/矛盾する箇所を anomaly_report として Stage G に渡す

        Args:
            merged_result: F-9 の出力
            e_content: Stage E のテキスト（互換性用）
            post_body: 投稿本文

        Returns:
            {
                scrubbed_data: 洗い替え成功データ（信頼度100%）,
                anomaly_report: Stage G への外科手術指示書
            }
        """
        f10_start = time.time()
        logger.info("[F-10] 【Ver 6.4】正本化 + 異常検知開始")

        tagged_texts = merged_result.get('tagged_texts', [])
        x_headers = merged_result.get('x_headers', [])
        y_headers = merged_result.get('y_headers', [])
        header_coords = merged_result.get('header_coords', {})

        # Stage E から座標付き文字リストを取得
        physical_chars = getattr(self, '_e_physical_chars', [])

        scrubbed_data = []
        anomaly_report = []
        PROXIMITY_THRESHOLD = 20  # ピクセル距離の閾値

        # ============================================
        # Step 1: 物理証拠による自動洗い替え（第一防衛線）
        # ============================================
        if physical_chars:
            logger.info(f"[F-10] 物理証拠あり: {len(physical_chars)}文字 → 洗い替え実行")

            # E文字の座標インデックスを構築
            e_char_index = {}  # {(page, x_bucket, y_bucket): [chars]}
            BUCKET_SIZE = 50  # 座標のバケットサイズ
            for ec in physical_chars:
                page = ec.get('page', 0)
                bbox = ec.get('bbox', [0, 0, 0, 0])
                cx = (bbox[0] + bbox[2]) // 2
                cy = (bbox[1] + bbox[3]) // 2
                bucket_key = (page, cx // BUCKET_SIZE, cy // BUCKET_SIZE)
                if bucket_key not in e_char_index:
                    e_char_index[bucket_key] = []
                e_char_index[bucket_key].append(ec)

            # 各 tagged_text を検証・洗い替え
            for tt in tagged_texts:
                tt_text = tt.get('text', '')
                tt_coords = tt.get('coords', {})
                tt_bbox = tt_coords.get('bbox') or tt.get('bbox')
                page = tt.get('page', 0)

                if not tt_bbox:
                    # 座標がない → 異常
                    anomaly_report.append({
                        'id': tt.get('id', ''),
                        'text': tt_text,
                        'reason': 'no_bbox',
                        'page': page,
                        'bbox': None
                    })
                    continue

                # この tagged_text の座標範囲
                if isinstance(tt_bbox, (list, tuple)) and len(tt_bbox) >= 4:
                    tt_x1, tt_y1, tt_x2, tt_y2 = tt_bbox[:4]
                    tt_cx = (tt_x1 + tt_x2) / 2
                    tt_cy = (tt_y1 + tt_y2) / 2
                else:
                    anomaly_report.append({
                        'id': tt.get('id', ''),
                        'text': tt_text,
                        'reason': 'invalid_bbox',
                        'page': page,
                        'bbox': tt_bbox
                    })
                    continue

                # 近傍のE文字を収集
                nearby_e_chars = []
                for dx in [-1, 0, 1]:
                    for dy in [-1, 0, 1]:
                        bucket_key = (page, int(tt_cx) // BUCKET_SIZE + dx, int(tt_cy) // BUCKET_SIZE + dy)
                        nearby_e_chars.extend(e_char_index.get(bucket_key, []))

                # 枠内に収まるE文字をフィルタ
                chars_in_bbox = []
                for ec in nearby_e_chars:
                    ec_bbox = ec.get('bbox', [0, 0, 0, 0])
                    ec_cx = (ec_bbox[0] + ec_bbox[2]) / 2
                    ec_cy = (ec_bbox[1] + ec_bbox[3]) / 2

                    # 枠内判定（または近傍判定）
                    in_x = tt_x1 - PROXIMITY_THRESHOLD <= ec_cx <= tt_x2 + PROXIMITY_THRESHOLD
                    in_y = tt_y1 - PROXIMITY_THRESHOLD <= ec_cy <= tt_y2 + PROXIMITY_THRESHOLD

                    if in_x and in_y:
                        chars_in_bbox.append({
                            'text': ec.get('text', ''),
                            'x': ec_bbox[0],
                            'bbox': ec_bbox
                        })

                if chars_in_bbox:
                    # 洗い替え成功: E文字で上書き
                    sorted_chars = sorted(chars_in_bbox, key=lambda c: c['x'])
                    scrubbed_text = ''.join(c['text'] for c in sorted_chars)

                    scrubbed_data.append({
                        'id': tt.get('id', ''),
                        'text': scrubbed_text,
                        'original_ocr': tt_text,
                        'x_header': tt.get('x_header', ''),
                        'y_header': tt.get('y_header', ''),
                        'type': tt.get('type', 'cell'),
                        '_scrubbed': True,
                        '_e_char_count': len(chars_in_bbox),
                        'bbox': tt_bbox
                    })
                else:
                    # 証拠欠落: E文字が枠内にない → 異常
                    anomaly_report.append({
                        'id': tt.get('id', ''),
                        'text': tt_text,
                        'reason': 'no_evidence_in_bbox',
                        'page': page,
                        'bbox': tt_bbox,
                        'x_header': tt.get('x_header', ''),
                        'y_header': tt.get('y_header', '')
                    })

            logger.info(f"[F-10] 洗い替え完了: 成功{len(scrubbed_data)}件, 異常{len(anomaly_report)}件")

        else:
            # ============================================
            # フォールバック: 物理証拠なし（画像PDF等）
            # ============================================
            logger.info("[F-10] 物理証拠なし → 全件を異常扱い（Stage G で再読）")

            for tt in tagged_texts:
                # 【Ver 6.9 Fix】変数を毎回リセット（Fill-down Error防止）
                best_match = None  # ★必ずリセット

                # 類似度マッチングで暫定洗い替えを試みる
                tt_text = tt.get('text', '')
                if e_content:
                    best_match = self._find_best_e_match(tt_text, set(e_content.split()))

                # 【Ver 6.9 Fix】コピーを作成してから変更（参照渡し問題防止）
                tt_copy = tt.copy()
                if best_match and best_match != tt_text:
                    tt_copy['text'] = best_match
                    tt_copy['_fallback_scrubbed'] = True

                scrubbed_data.append(tt_copy)

                # 物理証拠がない場合は全て要検証
                # 【Ver 6.9 Fix】tt_copy を参照するように修正
                if not tt_copy.get('_fallback_scrubbed'):
                    anomaly_report.append({
                        'id': tt_copy.get('id', ''),
                        'text': tt_text,
                        'reason': 'no_physical_evidence',
                        'page': tt_copy.get('page', 0),
                        'bbox': tt_copy.get('bbox')
                    })

        # ============================================
        # Step 2: 【Ver 6.7】F10浄化済みデータからアンカー作成 + 裏口閉鎖
        # ============================================
        f10_elapsed = time.time() - f10_start

        # 【Ver 6.7】F10の浄化済みデータからアンカーを作成（これがG1/G2への唯一の入口）
        # セル（cell）と テキスト（untagged）を分離してアンカー化
        final_anchors = []
        anchor_index = 1

        # セルデータ（type=cell）→ 表アンカー
        cell_items = [d for d in scrubbed_data if d.get('type') == 'cell']
        if cell_items:
            # x_header/y_headerでグループ化して表を構成
            final_anchors.append({
                "anchor_id": f"TBL_{anchor_index:03d}",
                "type": "table",
                # 【Ver 6.7】H1にスキップさせないため grid_table と名乗る
                "table_type": "grid_table",
                "tagged_texts": cell_items,
                "x_headers": x_headers,
                "y_headers": y_headers,
                "row_count": len(set(c.get('y_header', '') for c in cell_items)),
                "col_count": len(x_headers),
                "is_heavy": len(cell_items) >= 100,
                "_v67_f10_anchored": True
            })
            anchor_index += 1

        # テキストデータ（type=untagged）→ テキストアンカー
        text_items = [d for d in scrubbed_data if d.get('type') == 'untagged']
        for txt_item in text_items:
            final_anchors.append({
                "anchor_id": f"TXT_{anchor_index:03d}",
                "type": "text",
                "content": txt_item.get('text', ''),
                "page": txt_item.get('page', 0),
                "is_heavy": False,
                "_v67_f10_anchored": True
            })
            anchor_index += 1

        logger.info(f"[F-10] 【Ver 6.7】アンカー作成: 表{len([a for a in final_anchors if a['type']=='table'])}件, テキスト{len([a for a in final_anchors if a['type']=='text'])}件")

        # 【Ver 6.7】裏口閉鎖: F7の生データを消し、F10の結果のみを残す
        # extracted_texts形式への変換（後方互換用、ただし中身はF10の浄化済み）
        f10_extracted_texts = []
        full_text_parts = []
        for item in scrubbed_data:
            f10_extracted_texts.append({
                'block_id': item.get('id', ''),
                'text': item.get('text', ''),
                'coords': item.get('bbox', []),
                'page': item.get('page', 0),
                'x_header': item.get('x_header', ''),
                'y_header': item.get('y_header', ''),
                'type': item.get('type', 'untagged')
            })
            if item.get('text'):
                full_text_parts.append(item['text'])

        payload = {
            "schema_version": STAGE_F_OUTPUT_SCHEMA_VERSION,
            "post_body": post_body or {},
            "path_a_result": {
                # ★裏口閉鎖: F7の生データ(raw_texts/blocks)は入れず、F10の結果のみ
                "tagged_texts": scrubbed_data,
                "extracted_texts": f10_extracted_texts,  # 互換形式（中身はF10）
                "full_text_ordered": "\n".join(full_text_parts),
                "x_headers": x_headers,
                "y_headers": y_headers,
                "tables": [],  # 古いテーブル構造は廃棄（アンカーに統合済み）
                "_v64_scrubbed": True,
                "_v67_f7_raw_removed": True  # 裏口閉鎖フラグ
            },
            "path_b_result": merged_result.get("visual_source", {}),
            "anchors": final_anchors,  # ★F10浄化済みデータから作成したアンカー
            # 【Ver 6.4】Stage G への外科手術指示書
            "anomaly_report": anomaly_report,
            "metadata": {
                **merged_result.get("metadata", {}),
                "f10_physical_chars": len(physical_chars) if physical_chars else 0,
                "f10_scrubbed_count": len(scrubbed_data),
                "f10_anomaly_count": len(anomaly_report),
                "f10_elapsed": f10_elapsed,
                "f10_anchor_count": len(final_anchors)
            },
            "warnings": []
        }

        logger.info(f"[F-10] 完了: {f10_elapsed:.2f}秒, 洗替{len(scrubbed_data)}件, 異常{len(anomaly_report)}件")
        logger.info(f"[F-10] 【Ver 6.7】裏口閉鎖完了: F7生データ削除、全出力をF10結果に統一")

        # 【Ver 6.6】F-10 出力ログ（全文字出力）
        logger.info(f"[F-10] ===== 生成物ログ開始 =====")
        logger.info(f"[F-10] tagged_texts数: {len(scrubbed_data)}")
        logger.info(f"[F-10] x_headers: {x_headers}")
        logger.info(f"[F-10] y_headers: {y_headers}")
        # 全件出力
        for i, tt in enumerate(scrubbed_data):
            logger.info(f"[F-10]   [{i+1}] x={tt.get('x_header','')}, y={tt.get('y_header','')}, text='{tt.get('text','')}'")
        # anomaly_report 全件
        if anomaly_report:
            logger.info(f"[F-10] 異常レポート:")
            for i, ar in enumerate(anomaly_report):
                logger.info(f"[F-10]   [{i+1}] id={ar.get('id')}, reason={ar.get('reason')}, text='{ar.get('text','')}'")
        logger.info(f"[F-10] ===== 生成物ログ終了 =====")

        return payload

    def _find_nearest_header(
        self,
        coord: float,
        headers: List[str],
        header_coords: Dict[str, Dict],
        axis: str
    ) -> str:
        """
        座標から最も近いヘッダーを見つける

        Args:
            coord: 対象座標（x または y）
            headers: ヘッダーリスト
            header_coords: ヘッダー座標マップ
            axis: 'x' または 'y'

        Returns:
            最も近いヘッダー名
        """
        nearest = ''
        min_dist = float('inf')

        for h in headers:
            if h in header_coords:
                h_coord = header_coords[h].get(axis, 0)
                dist = abs(coord - h_coord)
                if dist < min_dist:
                    min_dist = dist
                    nearest = h

        # ヘッダー座標がない場合は、ヘッダーリストの順序で推定
        if not nearest and headers:
            # 簡易的に等間隔で配置されていると仮定
            interval = 1000 / (len(headers) + 1)
            for i, h in enumerate(headers):
                h_coord = interval * (i + 1)
                dist = abs(coord - h_coord)
                if dist < min_dist:
                    min_dist = dist
                    nearest = h

        return nearest

    def _f10_fallback_scrubbing(self, tagged_texts: List[Dict], e_content: str) -> int:
        """
        フォールバック: 従来の類似度マッチングによる洗い替え
        """
        if not e_content:
            return 0

        e_words = set()
        for line in e_content.split('\n'):
            line = line.strip()
            if line:
                e_words.add(line)
                for word in line.split():
                    if len(word) >= 2:
                        e_words.add(word)

        scrub_count = 0
        for tt in tagged_texts:
            ocr_text = tt.get('text', '')
            if not ocr_text:
                continue

            best_match = self._find_best_e_match(ocr_text, e_words)
            if best_match and best_match != ocr_text:
                tt['original_ocr'] = ocr_text
                tt['text'] = best_match
                tt['_scrubbed'] = True
                scrub_count += 1

        return scrub_count

    def _find_best_e_match(self, ocr_text: str, e_words: set) -> Optional[str]:
        """
        OCRテキストに最も類似するStage E単語を見つける

        Args:
            ocr_text: OCRテキスト
            e_words: Stage Eの単語セット

        Returns:
            最も類似する単語、または None
        """
        if not ocr_text or not e_words:
            return None

        best_match = None
        best_score = 0

        ocr_chars = set(ocr_text)

        for e_word in e_words:
            # 完全一致
            if ocr_text == e_word:
                return e_word

            # 部分一致
            if ocr_text in e_word:
                score = len(ocr_text) / len(e_word)
                if score > best_score and score >= 0.5:
                    best_score = score
                    best_match = e_word
                continue

            if e_word in ocr_text:
                score = len(e_word) / len(ocr_text) * 0.9
                if score > best_score and score >= 0.5:
                    best_score = score
                    best_match = e_word
                continue

            # 文字の重複率（Jaccard係数）
            e_chars = set(e_word)
            intersection = len(ocr_chars & e_chars)
            union = len(ocr_chars | e_chars)
            if union > 0:
                score = intersection / union * 0.8
                if score > best_score and score >= 0.6:
                    best_score = score
                    best_match = e_word

        return best_match

    # ============================================
    # F-10: Payload Validation（後方互換）
    # ============================================
    def _f10_validate(
        self,
        merged_result: Dict[str, Any],
        post_body: Optional[Dict]
    ) -> Dict[str, Any]:
        """
        F-10: 契約保証（Payload Validation）
        - 必須項目チェック
        - 表データの完全性検証
        - 文字数検証
        """
        f10_start = time.time()
        logger.info("[F-10] Payload Validation 開始")

        warnings = []

        # 1. full_text の存在確認
        full_text = merged_result.get("text_source", {}).get("full_text", "")
        full_text_len = len(full_text)
        if not full_text:
            warnings.append("F10_WARN: full_text is empty")
        logger.info(f"[F-10] full_text: {full_text_len}文字")

        # 2. 表データの完全性検証
        tables = merged_result.get("tables", [])
        table_warnings = self._validate_tables(tables)
        warnings.extend(table_warnings)

        # 3. ブロックの検証
        blocks = merged_result.get("text_source", {}).get("blocks", [])
        blocks_text_len = sum(len(b.get("text", "")) for b in blocks)
        logger.info(f"[F-10] blocks: {len(blocks)}個, 合計{blocks_text_len}文字")

        # 4. 文字数の整合性チェック（警告のみ、エラーにはしない）
        if blocks_text_len > 0 and full_text_len > 0:
            # full_text は blocks の統合なので、概ね同じ長さになるはず
            diff_ratio = abs(full_text_len - blocks_text_len) / max(full_text_len, blocks_text_len)
            if diff_ratio > 0.5:  # 50%以上の差異は警告
                warnings.append(f"F10_WARN: full_text({full_text_len}) と blocks合計({blocks_text_len}) の差異が大きい")

        # 5. 表データの統計
        table_count = len(tables)
        total_rows = sum(t.get("row_count", 0) for t in tables)
        tables_with_columns = sum(1 for t in tables if t.get("columns") or t.get("headers"))

        logger.info(f"[F-10] 表統計: {table_count}テーブル, {total_rows}行, columns付き={tables_with_columns}")

        # 6. トークン使用量の集計
        f7_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": F7_MODEL_IMAGE}
        for u in self._f7_usage:
            f7_total["prompt_tokens"] += u.get("prompt_tokens", 0)
            f7_total["completion_tokens"] += u.get("completion_tokens", 0)
            f7_total["total_tokens"] += u.get("total_tokens", 0)

        f8_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model": F8_MODEL}
        for u in self._f8_usage:
            f8_total["prompt_tokens"] += u.get("prompt_tokens", 0)
            f8_total["completion_tokens"] += u.get("completion_tokens", 0)
            f8_total["total_tokens"] += u.get("total_tokens", 0)

        logger.info(f"[F-10] F7トークン合計: prompt={f7_total['prompt_tokens']}, completion={f7_total['completion_tokens']}")
        logger.info(f"[F-10] F8トークン合計: prompt={f8_total['prompt_tokens']}, completion={f8_total['completion_tokens']}")

        # 7. 最終payload構築
        payload = {
            "schema_version": STAGE_F_OUTPUT_SCHEMA_VERSION,
            "post_body": post_body or {},
            "full_text": full_text,
            "text_blocks": blocks,
            "tables": tables,
            "structured_data_candidates": merged_result.get("structured_data_candidates", []),
            "visual_elements": {
                "diagrams": merged_result.get("visual_source", {}).get("diagrams", []),
                "charts": merged_result.get("visual_source", {}).get("charts", []),
            },
            "layout_analysis": merged_result.get("visual_source", {}).get("layout", {}),
            "metadata": {
                **merged_result.get("metadata", {}),
                "full_text_char_count": full_text_len,
                "blocks_count": len(blocks),
                "table_count": table_count,
                "total_table_rows": total_rows,
            },
            "media_type": "image",
            "processing_mode": "dual_vision",
            "warnings": warnings,
            "llm_usage": {
                "F7": f7_total,
                "F8": f8_total
            }
        }

        f10_elapsed = time.time() - f10_start
        logger.info(f"[F-10完了] Validation完了, warnings={len(warnings)}, {f10_elapsed:.2f}秒")

        return payload

    def _validate_tables(self, tables: List[Dict]) -> List[str]:
        """表データの完全性を検証（カラムナ形式対応）"""
        warnings = []

        for i, table in enumerate(tables):
            table_id = table.get("block_id", f"table_{i}")

            # columns/headers どちらも受け付ける（カラムナ形式優先）
            columns = table.get("columns") or table.get("headers", [])
            rows = table.get("rows", [])

            if not columns and not rows:
                warnings.append(f"F10_TABLE_WARN: {table_id} has no columns and no rows")
                continue

            # 列数の整合性チェック
            if columns:
                col_count = len(columns)
                for row_idx, row in enumerate(rows):
                    if isinstance(row, list) and len(row) != col_count:
                        warnings.append(f"F10_TABLE_WARN: {table_id} row {row_idx} has {len(row)} cols, expected {col_count}")

            # data_summary の検出（禁止パターン）
            if "data_summary" in table:
                warnings.append(f"F10_TABLE_ERROR: {table_id} uses data_summary (PROHIBITED)")

            # 辞書リスト形式の検出（禁止パターン）
            if rows and isinstance(rows[0], dict):
                warnings.append(f"F10_TABLE_ERROR: {table_id} uses dict rows (PROHIBITED - use columnar format)")

            # 空の rows チェック
            if not rows:
                warnings.append(f"F10_TABLE_WARN: {table_id} has columns but no rows")

            # セル内のカンマ検出（構造化不十分の可能性）
            for row_idx, row in enumerate(rows):
                if isinstance(row, list):
                    for col_idx, cell in enumerate(row):
                        if isinstance(cell, str) and ", " in cell and len(cell.split(", ")) > 2:
                            warnings.append(f"F10_TABLE_HINT: {table_id} row {row_idx} col {col_idx} may need further expansion (contains comma-separated data)")

        return warnings

    # ============================================
    # 【Ver 7.0】Vision API パイプライン結果変換
    # ============================================
    def _convert_vision_pipeline_result(
        self,
        pipeline_result: Dict[str, Any],
        chunk_idx: int
    ) -> Dict[str, Any]:
        """
        Vision APIパイプラインの結果を既存のpath_a_result形式に変換

        Args:
            pipeline_result: F7toF10Pipelineの出力
            chunk_idx: チャンクインデックス

        Returns:
            既存形式のpath_a_result
        """
        f10_result = pipeline_result.get("f10", {})
        final_tokens = f10_result.get("final_tokens", [])

        # raw_texts形式に変換 [[block_id, text], ...]
        raw_texts = []
        block_coords = {}

        for token in final_tokens:
            block_id = token.get("id", "")
            text = token.get("text", "")
            bbox = token.get("bbox", [])

            if block_id and text:
                raw_texts.append([block_id, text])
                if bbox:
                    block_coords[block_id] = {
                        "x": (bbox[0] + bbox[2]) / 2,
                        "y": (bbox[1] + bbox[3]) / 2,
                        "bbox": bbox
                    }

        # full_text_ordered を構築（物理ソート済み）
        full_text = " ".join([token.get("text", "") for token in final_tokens])

        return {
            "raw_texts": raw_texts,
            "block_coords": block_coords,
            "full_text_ordered": full_text,
            "chunk_idx": chunk_idx,
            "ocr_mode": "vision_api",
            "f7_stats": pipeline_result.get("f7", {}),
            "f75_stats": pipeline_result.get("f75", {}),
            "f8_stats": pipeline_result.get("f8", {}),
            "f9_stats": pipeline_result.get("f9", {}),
            "f10_stop_reason": f10_result.get("stop_reason", "UNKNOWN"),
            "anomaly_report": f10_result.get("anomaly_report", [])
        }

    def _convert_f7_vision_result(
        self,
        f7_result: Dict[str, Any],
        classified_blocks: List[Dict],
        chunk_idx: int
    ) -> Dict[str, Any]:
        """
        【Ver 7.0】Vision API OCR結果をpath_a_result形式に変換

        F7のみVision API、F8以降は従来LLMを使う場合の変換

        【Ver 7.1】座標正規化修正:
        - F7の座標（ピクセル）を1000×1000グリッドに正規化
        - Suryaブロック（F3で量子化済み）との座標系統一
        - Stage E（1000×1000）との照合を可能に

        Args:
            f7_result: VisionAPIExtractorの出力
            classified_blocks: Suryaブロック（F3で1000×1000に量子化済み）
            chunk_idx: チャンクインデックス

        Returns:
            既存形式のpath_a_result（座標は1000×1000正規化済み）
        """
        import math

        tokens = f7_result.get("tokens", [])
        page_size = f7_result.get("page_size", {"w": 1000, "h": 1000})

        # 【Ver 7.1】画像サイズを取得（正規化用）
        img_w = page_size.get("w", 1000)
        img_h = page_size.get("h", 1000)
        logger.info(f"[F7→変換] 画像サイズ: {img_w}x{img_h} → 1000x1000正規化")

        # Suryaブロックを辞書に変換（bboxは既に1000×1000座標）
        block_dict = {}
        for block in classified_blocks:
            bid = block.get('block_id', '')
            if bid:
                block_dict[bid] = block.get('bbox', [0, 0, 100, 100])

        # トークンを最寄りのブロックにマッピング
        raw_texts = []
        block_coords = {}
        full_texts = []
        mapped_count = 0
        unmapped_tokens = []

        logger.info(f"[F7.5] マッピング開始: {len(tokens)}tokens × {len(block_dict)}blocks")

        # 【Ver 7.1】F7で読み取った全文字をログ出力
        logger.info(f"[F7] ===== OCR結果（全{len(tokens)}tokens） =====")
        for i, token in enumerate(tokens):
            t_text = token.get("text", "")
            t_bbox = token.get("bbox", [0, 0, 0, 0])
            t_conf = token.get("conf", 1.0)
            logger.info(f"[F7] token[{i}]: text=\"{t_text}\" bbox={t_bbox} conf={t_conf:.3f}")
        logger.info(f"[F7] ===== OCR結果ここまで =====")

        for token in tokens:
            text = token.get("text", "")
            bbox = token.get("bbox", [0, 0, 0, 0])

            if not text:
                continue

            # 【Ver 7.1】トークン座標を1000×1000に正規化
            norm_bbox = [
                int(bbox[0] * 1000 / img_w) if img_w > 0 else 0,
                int(bbox[1] * 1000 / img_h) if img_h > 0 else 0,
                int(bbox[2] * 1000 / img_w) if img_w > 0 else 0,
                int(bbox[3] * 1000 / img_h) if img_h > 0 else 0
            ]

            # 正規化した中心座標で比較
            t_cx = (norm_bbox[0] + norm_bbox[2]) / 2
            t_cy = (norm_bbox[1] + norm_bbox[3]) / 2

            # 最寄りのブロックを探す（両方とも1000×1000座標）
            best_block_id = None
            min_dist = float('inf')

            for bid, b_bbox in block_dict.items():
                b_cx = (b_bbox[0] + b_bbox[2]) / 2
                b_cy = (b_bbox[1] + b_bbox[3]) / 2
                dist = math.sqrt((t_cx - b_cx)**2 + (t_cy - b_cy)**2)

                if dist < min_dist:
                    min_dist = dist
                    best_block_id = bid

            if best_block_id:
                raw_texts.append([best_block_id, text])
                # 【Ver 7.1】正規化座標を保存
                block_coords[best_block_id] = {
                    "x": t_cx,
                    "y": t_cy,
                    "bbox": norm_bbox,  # 1000×1000座標
                    "bbox_original": bbox  # 元のピクセル座標（デバッグ用）
                }
                mapped_count += 1
            else:
                unmapped_tokens.append({
                    "text": text,
                    "bbox": norm_bbox,
                    "reason": "no_block_found"
                })

            full_texts.append(text)

        # 【Ver 7.1】F7.5マッピング結果を全て出力
        logger.info(f"[F7.5] ===== マッピング結果（全{len(raw_texts)}件） =====")
        for i, (block_id, text) in enumerate(raw_texts):
            coords = block_coords.get(block_id, {})
            bbox = coords.get("bbox", [])
            logger.info(f"[F7.5] mapped[{i}]: block_id=\"{block_id}\" text=\"{text}\" bbox={bbox}")
        logger.info(f"[F7.5] ===== マッピング結果ここまで =====")

        logger.info(f"[F7.5] マッピング完了: mapped={mapped_count}, unmapped={len(unmapped_tokens)}, blocks={len(block_dict)}")
        if unmapped_tokens:
            logger.info(f"[F7.5] ===== unmapped（全{len(unmapped_tokens)}件） =====")
            for i, u in enumerate(unmapped_tokens):
                logger.info(f"[F7.5] unmapped[{i}]: text=\"{u['text']}\" bbox={u['bbox']} reason={u['reason']}")
            logger.info(f"[F7.5] ===== unmappedここまで =====")

        # Y座標でソートしてfull_text_orderedを構築
        sorted_tokens = sorted(tokens, key=lambda t: (
            t.get("bbox", [0, 0, 0, 0])[1],
            t.get("bbox", [0, 0, 0, 0])[0]
        ))
        full_text_ordered = " ".join([t.get("text", "") for t in sorted_tokens])

        logger.info(f"[F7.5] full_text_ordered: {len(full_text_ordered)}文字")

        return {
            "raw_texts": raw_texts,
            "block_coords": block_coords,
            "full_text_ordered": full_text_ordered,
            "chunk_idx": chunk_idx,
            "ocr_mode": "vision_api",
            "token_count": len(tokens),
            "mapped_count": mapped_count,
            "unmapped_count": len(unmapped_tokens),
            "unmapped_tokens": unmapped_tokens,
            "stats": f7_result.get("stats", {})
        }

    # ============================================
    # ユーティリティ
    # ============================================
    def _create_empty_payload(
        self,
        post_body: Optional[Dict],
        error: str = None
    ) -> Dict[str, Any]:
        """空のpayloadを生成"""
        payload = {
            "schema_version": STAGE_F_OUTPUT_SCHEMA_VERSION,
            "post_body": post_body or {},
            "path_a_result": {},
            "path_b_result": {},
            "media_type": "none",
            "processing_mode": "skipped",
            "warnings": []
        }
        if error:
            payload["warnings"].append(f"F_ERROR: {error}")
        return payload
