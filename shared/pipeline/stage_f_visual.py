"""
Stage F: Dual-Vision Analysis (独立読解 10段階)

【設計 2026-01-26】コスト最適化 + 高精度維持のための新設計

核心: 「FはEの答えを知らない状態で、ゼロから視覚情報を暴き出す」

============================================
F-1〜F-5: 構造の下地作り（AIなし、Surya + スクリプト）
  - F-1: Image Normalization (正規化)
  - F-2: Surya Block Detection (領域検出)
  - F-3: Coordinate Quantization (座標量子化) ← トークン削減の肝
  - F-4: Logical Reading Order (読む順序の確定)
  - F-5: Block Classification (構造ラベル付与)

F-6〜F-8: 独立・二重読解（AIの本番）
  - F-6: Blind Prompting (プロンプト注入) ← Stage E結果を遮断
  - F-7: Dual Read - Path A (テキストの鬼 / 2.0 Flash or 2.5 Flash-Lite)
  - F-8: Dual Read - Path B (視覚の鬼 / 2.5 Flash)

F-9〜F-10: 検証・パッキング
  - F-9: Result Convergence (抽出結果の集約)
  - F-10: Payload Validation (契約保証)
============================================
"""
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger
from PIL import Image

from shared.ai.llm_client.llm_client import LLMClient
from .constants import (
    STAGE_F_OUTPUT_SCHEMA_VERSION,
    F1_TARGET_DPI,
    SURYA_MAX_DIM,
    QUANTIZE_GRID_SIZE,
    F7_MODEL_IMAGE,
    F7_MODEL_AV,
    F8_MODEL,
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

    def __init__(self, llm_client: LLMClient, enable_surya: bool = True):
        """
        Args:
            llm_client: LLMクライアント
            enable_surya: Suryaを有効化（デフォルト: True）
        """
        self.llm_client = llm_client
        self.enable_surya = enable_surya and SURYA_AVAILABLE

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
        e2_table_bboxes: List[Dict] = None
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

            # F-6: Blind Prompting（このチャンク用の地図生成）
            if progress_callback:
                progress_callback(f"F-6 ({chunk_idx + 1}/{total_chunks})")
            block_map = self._f6_create_block_map(classified_blocks)

            # F-7 & F-8: Dual Read（このチャンクのみ、並列実行）
            if progress_callback:
                progress_callback(f"F-7/F-8 ({chunk_idx + 1}/{total_chunks})")

            path_a_result = {}
            path_b_result = {}

            # このチャンク（ページ）の列境界情報を取得
            page_column_boundaries = self._page_column_boundaries.get(chunk_start_page, [0, QUANTIZE_GRID_SIZE])

            with ThreadPoolExecutor(max_workers=2) as executor:
                future_a = executor.submit(
                    self._f7_path_a_chunk_extraction,
                    chunk_pages, block_map, chunk_idx, chunk_start_page, page_column_boundaries
                )
                future_b = executor.submit(
                    self._f8_path_b_chunk_analysis,
                    chunk_pages, block_map, chunk_idx, chunk_start_page
                )

                for future in as_completed([future_a, future_b]):
                    try:
                        if future == future_a:
                            path_a_result = future.result()
                            logger.info(f"[F-7] チャンク{chunk_idx + 1} Path A 完了")
                        else:
                            path_b_result = future.result()
                            logger.info(f"[F-8] チャンク{chunk_idx + 1} Path B 完了")
                    except Exception as e:
                        logger.error(f"[F-7/F-8] チャンク{chunk_idx + 1} エラー: {e}")
                        chunk_warnings.append(f"CHUNK_{chunk_idx}_ERROR: {str(e)}")

            # チャンク結果を蓄積
            aggregated_full_texts.append(path_a_result.get("full_text_ordered", ""))

            # ブロックIDにチャンク情報を付加して蓄積
            for block in path_a_result.get("extracted_texts", []):
                block["chunk_idx"] = chunk_idx
                block["original_page"] = chunk_start_page + block.get("page", 0)
                aggregated_blocks.append(block)

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
        # F-9: Result Convergence（全チャンク結果をマージ）
        # ============================================
        if progress_callback:
            progress_callback("F-9")

        logger.info("=" * 50)
        logger.info("[F-9] 全チャンク結果のマージ開始")

        # -----------------------------------------
        # アンカーベースのパケット配列を生成
        # -----------------------------------------
        anchors = self._build_anchor_packets(
            aggregated_blocks,
            aggregated_tables,
            aggregated_structured_candidates
        )

        logger.info(f"[F-9] アンカー生成: {len(anchors)}個")

        # 後方互換のため従来形式も保持
        merged_result = {
            # 新形式: アンカーベース
            "anchors": anchors,
            # 旧形式: 互換性のため残す
            "text_source": {
                "full_text": "\n\n".join(aggregated_full_texts),
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
                "anchor_count": len(anchors)
            }
        }

        total_text_len = len(merged_result["text_source"]["full_text"])
        logger.info(f"[F-9] マージ完了: 総テキスト{total_text_len}文字, 総表{len(aggregated_tables)}件, アンカー{len(anchors)}個")

        # ============================================
        # F-10: Payload Validation
        # ============================================
        if progress_callback:
            progress_callback("F-10")

        validated_payload = self._f10_validate(merged_result, post_body)

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
    # F-3.5: Intelligent Filtering & Column Detection
    # ============================================
    def _f35_filter_and_columnize(self, blocks: List[Dict]) -> List[Dict]:
        """
        F-3.5: ゴミ掃除 + 列自動判別 + 行統合

        【トークン削減の肝】AIに渡す前の物理的なデータ圧縮

        1. ゴミ除去: 極小ブロック、空ブロックを物理削除
        2. 列検出: X座標ヒストグラムで「空白の縦ライン」を特定
        3. 列分類: ブロックを列グループに分類
        4. 行統合: 同じ列・近いY座標のブロックを1行に結合
        """
        f35_start = time.time()
        original_count = len(blocks)
        logger.info(f"[F-3.5] Intelligent Filtering 開始: {original_count}ブロック")

        if not blocks:
            return []

        # ============================================
        # Step 1: ゴミ掃除（Filtering）
        # ============================================
        MIN_BLOCK_SIZE = 10  # 最小サイズ（量子化後）
        MIN_BLOCK_AREA = 200  # 最小面積

        filtered = []
        garbage_count = 0

        for block in blocks:
            bbox = block['bbox']
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            area = w * h

            # 極小ブロックを除外
            if w < MIN_BLOCK_SIZE or h < MIN_BLOCK_SIZE:
                garbage_count += 1
                continue
            if area < MIN_BLOCK_AREA:
                garbage_count += 1
                continue

            filtered.append(block)

        logger.info(f"  ├─ ゴミ除去: {garbage_count}ブロック削除 → 残り{len(filtered)}ブロック")

        # ============================================
        # Step 2: 列自動判別（X-axis Histogram Analysis）
        # ============================================
        # ページごとに列を検出
        pages = {}
        for block in filtered:
            page = block.get('page', 0)
            if page not in pages:
                pages[page] = []
            pages[page].append(block)

        all_columnized = []

        for page_idx, page_blocks in pages.items():
            # X座標の密度ヒストグラムを作成（1000グリッド）
            x_histogram = [0] * QUANTIZE_GRID_SIZE
            # 縦線検出用: 各X座標での最大ブロック高さ
            x_max_height = [0] * QUANTIZE_GRID_SIZE

            for block in page_blocks:
                bbox = block['bbox']
                x_start = max(0, int(bbox[0]))
                x_end = min(QUANTIZE_GRID_SIZE, int(bbox[2]))
                block_height = bbox[3] - bbox[1]
                block_width = x_end - x_start

                for x in range(x_start, x_end):
                    x_histogram[x] += 1
                    # 細いブロック（幅10以下）の高さを記録（罫線候補）
                    if block_width <= 10:
                        x_max_height[x] = max(x_max_height[x], block_height)

            # 列境界を検出（空白 OR 罫線）
            column_boundaries = [0]  # 左端
            MIN_GAP_WIDTH = 20  # 最小空白幅
            MIN_LINE_HEIGHT = 300  # 罫線とみなす最小高さ（1000グリッド中）

            x = 0
            while x < QUANTIZE_GRID_SIZE:
                # パターン1: 空白の縦ライン（連続した0の領域）
                if x_histogram[x] == 0:
                    gap_start = x
                    while x < QUANTIZE_GRID_SIZE and x_histogram[x] == 0:
                        x += 1
                    gap_width = x - gap_start
                    if gap_width >= MIN_GAP_WIDTH:
                        # 空白の中央を境界とする
                        boundary = gap_start + gap_width // 2
                        if boundary not in column_boundaries:
                            column_boundaries.append(boundary)
                    continue

                # パターン2: 罫線（細くて高いブロック）
                if x_max_height[x] >= MIN_LINE_HEIGHT and x_histogram[x] <= 3:
                    line_start = x
                    while x < QUANTIZE_GRID_SIZE and x_max_height[x] >= MIN_LINE_HEIGHT and x_histogram[x] <= 3:
                        x += 1
                    line_width = x - line_start
                    if line_width <= 15:  # 罫線は細い（15px以下）
                        # 罫線の中央を境界とする
                        boundary = line_start + line_width // 2
                        if boundary not in column_boundaries and boundary > 50 and boundary < 950:
                            column_boundaries.append(boundary)
                    continue

                x += 1

            column_boundaries.append(QUANTIZE_GRID_SIZE)  # 右端
            column_boundaries = sorted(set(column_boundaries))  # 重複除去・ソート
            num_columns = len(column_boundaries) - 1

            logger.info(f"  ├─ ページ{page_idx}: {num_columns}列検出 (境界: {column_boundaries})")

            # 列境界情報を保存（F-7の列クロッピングで使用）
            self._page_column_boundaries[page_idx] = column_boundaries

            # ============================================
            # Step 3: 列ごとの分類（Vertical Splitting）
            # ============================================
            columns = {}  # column_id -> list of blocks

            for block in page_blocks:
                bbox = block['bbox']
                x_center = (bbox[0] + bbox[2]) / 2

                # どの列に属するか判定
                column_id = 0
                for i in range(len(column_boundaries) - 1):
                    if column_boundaries[i] <= x_center < column_boundaries[i + 1]:
                        column_id = i
                        break

                block['column_id'] = column_id
                block['num_columns'] = num_columns
                block['x_center'] = x_center
                block['y_center'] = (bbox[1] + bbox[3]) / 2

                if column_id not in columns:
                    columns[column_id] = []
                columns[column_id].append(block)

            # ============================================
            # Step 4: ページ完結型の列統合（Page-Complete Column Merging）
            # ============================================
            # 【核心】1列 = 1テーブル（ページ内で完結）
            # 隙間があっても、同じ列なら全て1つに統合する
            # ============================================

            page_column_blocks = []

            for col_id in sorted(columns.keys()):
                col_blocks = columns[col_id]

                if not col_blocks:
                    continue

                # Y座標でソート（読み順）
                col_blocks.sort(key=lambda b: b['y_center'])

                # 列内の全ブロックを1つに統合
                merged_column = self._merge_column_blocks(
                    col_blocks, page_idx, col_id, num_columns,
                    column_boundaries[col_id], column_boundaries[col_id + 1]
                )
                page_column_blocks.append(merged_column)

            logger.info(f"  ├─ ページ{page_idx}: {len(columns)}列 → {len(page_column_blocks)}ブロック（ページ完結）")
            all_columnized.extend(page_column_blocks)

        f35_elapsed = time.time() - f35_start
        reduction_rate = (1 - len(all_columnized) / original_count) * 100 if original_count > 0 else 0
        logger.info(f"[F-3.5完了] {original_count} → {len(all_columnized)}ブロック "
                    f"({reduction_rate:.1f}%削減), {f35_elapsed:.2f}秒")

        return all_columnized

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
    # F-6: Blind Prompting
    # ============================================
    def _f6_create_block_map(self, blocks: List[Dict]) -> str:
        """
        F-6: Blind Prompting用の地図JSON生成
        Stage Eの結果は一切含めない
        """
        f6_start = time.time()
        logger.info("[F-6] Blind Prompting - Block Map 生成")

        # 軽量な地図JSONを生成
        block_map = []
        for block in blocks:
            block_map.append({
                "id": block['block_id'],
                "order": block.get('reading_order', 0),
                "type": block.get('block_type_hint', 'body_hint'),
                "col": block.get('column_id', 0),
                "box": block['bbox']  # 量子化済み座標
            })

        map_json = json.dumps(block_map, ensure_ascii=False, separators=(',', ':'))

        f6_elapsed = time.time() - f6_start
        logger.info(f"[F-6完了] {len(block_map)}ブロック, {len(map_json)}文字, {f6_elapsed:.2f}秒")

        return map_json

    # ============================================
    # F-7: Dual Read - Path A (Text Extraction)
    # ============================================
    def _f7_path_a_text_extraction(
        self,
        file_path: Path,
        block_map: str,
        page_images: List[Dict]
    ) -> Dict[str, Any]:
        """
        F-7 Path A: テキストの鬼（gemini-2.0-flash）
        """
        f7_start = time.time()
        logger.info("[F-7] Path A - Text Extraction 開始")

        prompt = self._build_f7_prompt(block_map)

        try:
            response = self.llm_client.generate_with_vision(
                prompt=prompt,
                image_path=str(file_path),
                model=F7_MODEL_IMAGE,
                max_tokens=F7_F8_MAX_TOKENS,
                temperature=F7_F8_TEMPERATURE,
                response_format="json"
            )

            # JSON パース
            try:
                result = json.loads(response)
            except json.JSONDecodeError:
                import json_repair
                result = json_repair.repair_json(response, return_objects=True)

            f7_elapsed = time.time() - f7_start
            logger.info(f"[F-7完了] Path A: {len(response)}文字, {f7_elapsed:.2f}秒")

            return result

        except Exception as e:
            logger.error(f"[F-7] Path A エラー: {e}")
            return {"error": str(e), "extracted_texts": [], "tables": []}

    def _build_f7_prompt(self, block_map: str) -> str:
        """F-7用プロンプト構築（表抽出強化版）"""
        return f"""# F-7: Text Extraction / テキストの鬼

## Mission
画像内の全ての文字を、書き起こしレベルで抽出せよ。
**特に表データは1セルも漏らさず完全に抽出すること。**

## ブロック地図（Surya検出結果）
```json
{block_map}
```

**注意**: この地図は位置情報のみ。文字の内容はあなたが画像を見て読み取ってください。
座標はあくまで目安です。ブロック境界を数ピクセルはみ出している文字も読み取ってください。
**table_hint のブロックは特に注意して、全てのセルを読み取ること。**

## 抽出の優先順位
1. 通常のテキスト（本文、見出し）- reading_order順に
2. **表データ（最重要）** - 全ての行・全てのセルを完全抽出
3. 見落としやすい文字（小さな注釈、ロゴ内文字、手書き、スタンプ）
4. 記号（©, ®, %, ※ 等は拾う。装飾図形は無視）
5. 数値・日付・金額（精密に）

## 表抽出の絶対ルール（最重要）

### ⚠️ 表は必ず全行をrows配列に展開（要約禁止）

❌ **絶対にやってはいけないこと**:
```json
{{"table_title": "成績優秀者", "data_summary": "1位は山田（520点）、2位は田中..."}}
```

✅ **正しい抽出方法（全行を展開 + カラムナ形式）**:
```json
{{
  "table_title": "成績優秀者",
  "table_type": "ranking",
  "columns": ["順位", "氏名", "点数"],
  "rows": [
    ["1", "山田太郎", "520"],
    ["2", "田中花子", "515"],
    ["3", "鈴木一郎", "510"]
  ]
}}
```

### 表として認識すべきパターン
- **視覚的な表**: 罫線で囲まれた表
- **罫線なしの表**: 空白/タブで区切られた列データ
- **ランキング・順位表**: 「1位: ○○」「2位: △△」→ 全員分を rows に
- **Key-Valueペア**: 「項目名: 値」の繰り返し → 2列の表に
- **カンマ区切りデータ**: 「A, B, C」→ 各要素を別々の行に展開

### table_type の種類
- `visual_table`: 画像内に視覚的に見える表
- `structured_data`: 文章中の構造化可能なデータ
- `ranking`: ランキング・順位表
- `requirements`: 要件・条件リスト
- `item_list`: 商品・項目リスト
- `pricing`: 価格情報
- `schedule`: スケジュール・時間割
- `metadata`: メタデータ（文字数、件数など）

### セル内のカンマ・セミコロンは展開
❌ NG: `{{"参加者": "山田, 田中, 鈴木"}}`
✅ OK: 別々の行に展開（カラムナ形式）
```json
{{"columns": ["氏名"], "rows": [["山田"], ["田中"], ["鈴木"]]}}
```

## 出力形式（カラムナフォーマット厳守）

**重要**: 表データは `columns` と `rows`（値のみ）で出力。トークン節約のため辞書リスト形式は禁止。

```json
{{
  "extracted_texts": [
    {{"block_id": "p0_b0", "reading_order": 0, "block_type": "heading", "text": "...", "confidence": "high"}}
  ],
  "tables": [
    {{
      "block_id": "p0_b5",
      "table_title": "表のタイトル",
      "table_type": "visual_table",
      "columns": ["列1", "列2", "列3"],
      "rows": [
        ["データ1-1", "データ1-2", "データ1-3"],
        ["データ2-1", "データ2-2", "データ2-3"]
      ],
      "row_count": 2,
      "col_count": 3,
      "caption": "表の説明（あれば）"
    }}
  ],
  "missed_texts": [
    {{"location": "右下隅", "text": "...", "reason": "ブロック外"}}
  ],
  "full_text_ordered": "各ブロックをreading_order順に、\\n\\nで区切って結合した全文"
}}
```

**禁止（辞書リスト = トークン浪費）**:
```json
"rows": [{{"列1": "値", "列2": "値"}}, ...]
```

## 禁止事項
- 要約禁止、省略禁止、推測禁止（読めない文字は[判読不可]）
- **表の一部だけを抽出することは禁止（全行必須）**
- **data_summary でテキスト要約することは禁止**
"""

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
        block_map: str,
        page_images: List[Dict]
    ) -> Dict[str, Any]:
        """
        F-8 Path B: 視覚の鬼（gemini-2.5-flash）
        """
        f8_start = time.time()
        logger.info("[F-8] Path B - Visual Analysis 開始")

        prompt = self._build_f8_prompt(block_map)

        try:
            response = self.llm_client.generate_with_vision(
                prompt=prompt,
                image_path=str(file_path),
                model=F8_MODEL,
                max_tokens=F7_F8_MAX_TOKENS,
                temperature=F7_F8_TEMPERATURE,
                response_format="json"
            )

            # JSON パース
            try:
                result = json.loads(response)
            except json.JSONDecodeError:
                import json_repair
                result = json_repair.repair_json(response, return_objects=True)

            f8_elapsed = time.time() - f8_start
            logger.info(f"[F-8完了] Path B: {len(response)}文字, {f8_elapsed:.2f}秒")

            return result

        except Exception as e:
            logger.error(f"[F-8] Path B エラー: {e}")
            return {"error": str(e), "tables": [], "diagrams": [], "layout_analysis": {}}

    def _build_f8_prompt(self, block_map: str) -> str:
        """F-8用プロンプト構築（表構造解析強化版）"""
        return f"""# F-8: Visual Structure / 視覚・構造の鬼

## Mission
画像・表・図解の構造と関係性を解析せよ。
文字の書き起こしはPath Aが担当。あなたは**構造・関係性・視覚的意味**に集中。
**特に表の構造（セル結合、ヘッダー、データ型）を完璧に解析すること。**

## ブロック地図（Surya検出結果）
```json
{block_map}
```

## 解析対象

### 1. 表の構造解析（最重要）

#### 検出すべき情報
- **セル結合**: colspan（横結合）、rowspan（縦結合）の正確な位置と範囲
- **ヘッダー構造**: 何行目までがヘッダーか、多段ヘッダーの場合その構造
- **データ型推定**: 各列が以下のいずれか
  - `text`: テキスト（氏名、項目名など）
  - `number`: 数値（個数、順位など）
  - `currency`: 金額（円、ドルなど）
  - `date`: 日付
  - `time`: 時刻
  - `percentage`: パーセンテージ
- **小計・合計行**: 太字や背景色で強調された集計行の位置（row index）
- **空白セルの意味**: 「データなし」か「上と同じ（ditto）」か

#### 表の種類（table_type）を判定
- `list_type`: リスト型（縦方向にデータが並ぶ）
  - ランキング表、名簿、成績一覧、商品リスト
  - 各行が1つのエントリ（人、商品、項目）を表す
- `matrix_type`: マトリクス型（横軸に日付や項目が並ぶ）
  - 時間割、月間予定表、週間スケジュール
  - 行と列の交差点にデータがある

### 2. 構造化可能なデータの検出
視覚的に表として表示されていなくても、以下のパターンを検出：
- **Key-Valueペア**: 「項目名: 値」の繰り返し
- **ランキング・順位表**: 「1位: ○○」「2位: △△」
- **カンマ区切りデータ**: 「A, B, C」のような並列データ
- **罫線なしの表**: 空白/タブで区切られた列データ

### 3. 図解・フローチャート
- 要素間の接続、矢印の向き
- 階層構造、グループ化
- 条件分岐（Yes/No）

### 4. グラフ・チャート
- グラフ種類（棒、折れ線、円、散布図）
- 軸ラベル、凡例
- データ傾向（増加、減少、ピーク位置）

### 5. レイアウト
- 段組構造、強調パターン
- セクション区切り

## 出力形式
```json
{{
  "tables": [
    {{
      "block_id": "p0_b5",
      "table_type": "list_type",
      "structure": {{
        "header_rows": 1,
        "total_rows": 10,
        "total_cols": 5,
        "merged_cells": [
          {{"row": 0, "col": 0, "rowspan": 2, "colspan": 1, "content_hint": "項目名"}}
        ],
        "column_types": ["text", "text", "currency", "currency", "percentage"],
        "summary_rows": [9],
        "has_footer": true
      }},
      "semantic_role": "四半期売上比較表",
      "data_quality": {{
        "empty_cells": 2,
        "ditto_cells": 0,
        "needs_verification": false
      }}
    }}
  ],
  "structured_data_candidates": [
    {{
      "location": "本文中段",
      "pattern": "key_value_pairs",
      "suggested_headers": ["項目", "内容"],
      "estimated_rows": 5,
      "source_text_hint": "提出期限: 2025-01-15..."
    }}
  ],
  "diagrams": [
    {{
      "block_id": "p0_b8",
      "type": "flowchart",
      "elements_count": 5,
      "connections_count": 4,
      "has_conditions": true,
      "semantic_role": "申請承認フロー"
    }}
  ],
  "charts": [
    {{
      "block_id": "p0_b12",
      "type": "bar_chart",
      "x_axis": "月",
      "y_axis": "売上（万円）",
      "data_points_approx": 12,
      "trend": "Q3で急増、Q4で減少"
    }}
  ],
  "layout_analysis": {{
    "column_structure": "2-column",
    "sections": [
      {{"name": "ヘッダー", "blocks": ["p0_b0"], "purpose": "タイトル"}},
      {{"name": "本文", "blocks": ["p0_b1", "p0_b2"], "purpose": "説明文"}},
      {{"name": "表エリア", "blocks": ["p0_b5"], "purpose": "データ表示"}}
    ],
    "emphasis_patterns": ["見出しは青色太字", "重要数値は赤色"]
  }}
}}
```

## 禁止事項
- 文字の書き起こし禁止（それはPath Aの仕事）
- 推測による補完禁止
- **表の行数・列数を間違えることは許されない（正確にカウント）**
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

    def _f7_path_a_chunk_extraction(
        self,
        chunk_pages: List[Dict],
        block_map: str,
        chunk_idx: int,
        chunk_start_page: int,
        column_boundaries: List[int] = None
    ) -> Dict[str, Any]:
        """
        F-7 Path A: チャンク用テキスト抽出（gemini-2.0-flash）
        【列クロッピング対応】MAX_TOKENSエラー回避のため、列ごとに分割処理

        Args:
            chunk_pages: このチャンクのページ画像リスト
            block_map: このチャンク用のブロック地図
            chunk_idx: チャンクインデックス
            chunk_start_page: このチャンクの開始ページ番号
            column_boundaries: 列境界座標リスト [0, 250, 500, 1000] 等
        """
        f7_start = time.time()
        num_columns = len(column_boundaries) - 1 if column_boundaries and len(column_boundaries) > 1 else 1
        logger.info(f"[F-7] Path A - チャンク{chunk_idx + 1} Text Extraction 開始 ({num_columns}列)")

        # 元画像を取得
        if not chunk_pages or 'image' not in chunk_pages[0]:
            logger.error(f"[F-7] チャンク{chunk_idx + 1} 画像データなし")
            return {"error": "No image data", "extracted_texts": [], "tables": [], "full_text_ordered": ""}

        original_image: Image.Image = chunk_pages[0]['image']
        img_width, img_height = original_image.size

        # 列境界がない場合は従来通り全体処理
        if not column_boundaries or len(column_boundaries) < 2 or num_columns == 1:
            return self._f7_process_single_image(
                original_image, block_map, chunk_idx, chunk_start_page, f7_start
            )

        # ============================================
        # 列ごとのクロッピング＆API呼び出し
        # ============================================
        all_extracted_texts = []
        all_tables = []
        all_full_texts = []
        column_errors = []

        for col_idx in range(num_columns):
            col_start = column_boundaries[col_idx]
            col_end = column_boundaries[col_idx + 1]

            # 量子化座標（0-1000）を実ピクセルに変換
            px_start = int(col_start * img_width / QUANTIZE_GRID_SIZE)
            px_end = int(col_end * img_width / QUANTIZE_GRID_SIZE)

            # クロッピング（左, 上, 右, 下）
            cropped_image = original_image.crop((px_start, 0, px_end, img_height))

            logger.info(f"[F-7] 列{col_idx + 1}/{num_columns} クロッピング: x={px_start}-{px_end} ({cropped_image.size[0]}x{cropped_image.size[1]})")

            # 列画像を一時ファイルに保存
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=f'_chunk{chunk_idx}_col{col_idx}.png', delete=False) as f:
                cropped_image.save(f, format='PNG')
                temp_col_path = Path(f.name)

            try:
                # 列用プロンプト
                col_prompt = self._build_f7_column_prompt(block_map, chunk_idx, chunk_start_page, col_idx, num_columns)

                response = self.llm_client.generate_with_vision(
                    prompt=col_prompt,
                    image_path=str(temp_col_path),
                    model=F7_MODEL_IMAGE,
                    max_tokens=F7_F8_MAX_TOKENS,
                    temperature=F7_F8_TEMPERATURE,
                    response_format="json"
                )

                # JSON パース
                try:
                    col_result = json.loads(response)
                except json.JSONDecodeError:
                    import json_repair
                    col_result = json_repair.repair_json(response, return_objects=True)

                logger.info(f"[F-7完了] 列{col_idx + 1}/{num_columns}: {len(response)}文字")

                # トークン使用量を収集
                if hasattr(self.llm_client, 'last_usage') and self.llm_client.last_usage:
                    usage = self.llm_client.last_usage.copy()
                    usage['chunk_idx'] = chunk_idx
                    usage['column_idx'] = col_idx
                    self._f7_usage.append(usage)

                # 結果を蓄積（列情報を付加）
                for text_block in col_result.get("extracted_texts", []):
                    text_block["column_idx"] = col_idx
                    all_extracted_texts.append(text_block)

                for table in col_result.get("tables", []):
                    table["column_idx"] = col_idx
                    all_tables.append(table)

                all_full_texts.append(col_result.get("full_text_ordered", ""))

            except Exception as e:
                logger.error(f"[F-7] 列{col_idx + 1}/{num_columns} エラー: {e}")
                column_errors.append(f"col{col_idx}: {str(e)}")

            finally:
                # 一時ファイル削除
                try:
                    temp_col_path.unlink()
                except:
                    pass

        # 全列の結果を統合
        f7_elapsed = time.time() - f7_start
        logger.info(f"[F-7完了] チャンク{chunk_idx + 1} 全{num_columns}列完了: {f7_elapsed:.2f}秒")

        return {
            "extracted_texts": all_extracted_texts,
            "tables": all_tables,
            "full_text_ordered": "\n\n".join(all_full_texts),
            "column_count": num_columns,
            "errors": column_errors if column_errors else None
        }

    def _f7_process_single_image(
        self,
        image: Image.Image,
        block_map: str,
        chunk_idx: int,
        chunk_start_page: int,
        f7_start: float
    ) -> Dict[str, Any]:
        """F-7: 単一画像処理（列分割なし）"""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=f'_chunk{chunk_idx}.png', delete=False) as f:
            image.save(f, format='PNG')
            temp_image_path = Path(f.name)

        prompt = self._build_f7_chunk_prompt(block_map, chunk_idx, chunk_start_page, 1)

        try:
            response = self.llm_client.generate_with_vision(
                prompt=prompt,
                image_path=str(temp_image_path),
                model=F7_MODEL_IMAGE,
                max_tokens=F7_F8_MAX_TOKENS,
                temperature=F7_F8_TEMPERATURE,
                response_format="json"
            )

            try:
                result = json.loads(response)
            except json.JSONDecodeError:
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

    def _build_f7_column_prompt(self, block_map: str, chunk_idx: int, start_page: int, col_idx: int, total_cols: int) -> str:
        """F-7列クロッピング用プロンプト構築"""
        base_prompt = self._build_f7_prompt(block_map)

        column_info = f"""
## 列情報（重要）
- この画像は **元ページの{col_idx + 1}列目（全{total_cols}列中）** を切り抜いたものです
- ページ番号: {start_page + 1}ページ目
- **この列内の全テキスト・全表データを完全に抽出してください**

**注意**: 切り抜き画像のため、一部のテキストが左右で途切れている可能性があります。
見える部分は全て正確に読み取ってください。
"""
        return base_prompt + column_info

    def _build_f7_chunk_prompt(self, block_map: str, chunk_idx: int, start_page: int, page_count: int) -> str:
        """F-7チャンク用プロンプト構築"""
        base_prompt = self._build_f7_prompt(block_map)

        chunk_info = f"""
## チャンク情報
- チャンク番号: {chunk_idx + 1}
- ページ範囲: {start_page + 1}〜{start_page + page_count}ページ目
- このチャンクには{page_count}ページ分の画像が縦に結合されています

**注意**: 各ページの境界を意識して、ページ跨ぎの表やテキストも正確に抽出してください。
"""
        return base_prompt + chunk_info

    def _f8_path_b_chunk_analysis(
        self,
        chunk_pages: List[Dict],
        block_map: str,
        chunk_idx: int,
        chunk_start_page: int
    ) -> Dict[str, Any]:
        """
        F-8 Path B: チャンク用構造解析（gemini-2.5-flash）

        Args:
            chunk_pages: このチャンクのページ画像リスト
            block_map: このチャンク用のブロック地図
            chunk_idx: チャンクインデックス
            chunk_start_page: このチャンクの開始ページ番号
        """
        f8_start = time.time()
        logger.info(f"[F-8] Path B - チャンク{chunk_idx + 1} Visual Analysis 開始")

        # チャンク画像を一時ファイルに保存
        temp_image_path = self._save_chunk_as_temp_image(chunk_pages, chunk_idx)

        prompt = self._build_f8_chunk_prompt(block_map, chunk_idx, chunk_start_page, len(chunk_pages))

        try:
            response = self.llm_client.generate_with_vision(
                prompt=prompt,
                image_path=str(temp_image_path),
                model=F8_MODEL,
                max_tokens=F7_F8_MAX_TOKENS,
                temperature=F7_F8_TEMPERATURE,
                response_format="json"
            )

            # JSON パース
            try:
                result = json.loads(response)
            except json.JSONDecodeError:
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

    def _build_f8_chunk_prompt(self, block_map: str, chunk_idx: int, start_page: int, page_count: int) -> str:
        """F-8チャンク用プロンプト構築"""
        base_prompt = self._build_f8_prompt(block_map)

        chunk_info = f"""
## チャンク情報
- チャンク番号: {chunk_idx + 1}
- ページ範囲: {start_page + 1}〜{start_page + page_count}ページ目
- このチャンクには{page_count}ページ分の画像が縦に結合されています

**注意**: ページ境界を跨ぐ表の構造も正確に解析してください。
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
                "data_quality": b_structure.get("data_quality", {})
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
            if not text or len(text.strip()) < 3:
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
                "table_type": table.get("table_type", "visual_table"),
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
    # F-10: Payload Validation
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
