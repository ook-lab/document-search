"""
Stage F: Visual Analysis (視覚解析)

Hybrid方式: Surya + PaddleOCR + Gemini Vision（3段階）
- Step 1 (F-1): PaddleOCR で表構造を抽出
- Step 2 (F-2～F-5): Surya + PaddleOCR で高精度テキスト認識
  - F-2: Suryaでレイアウト解析・Bounding Box取得
  - F-3: 画像切り出し
  - F-4: PaddleOCRで日本語テキスト認識
  - F-5: テキスト統合
- Step 3 (F-6～F-10): Gemini Vision で全体解析・統合
  - F-6: プロンプト構築
  - F-7: Gemini Vision API呼び出し
  - F-8: JSON クリーニング
  - F-9: 全結果マージ
  - F-10: 最終検証・出力

- 役割: 人間が見たままの視覚情報をそのまま捉える（OCR、レイアウト認識）
- モデル: 設定ファイルで指定（config/models.yaml）
- プロンプト: 設定ファイルで指定（config/prompts/stage_f/*.md）
- 出力: 3種類のデータ（full_text, layout_info, visual_elements）
"""
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from loguru import logger
import json
import time
import numpy as np
from PIL import Image

from shared.ai.llm_client.llm_client import LLMClient
from shared.ai.llm_client.exceptions import MaxTokensExceededError
import cv2
from .image_preprocessing import preprocess_image_for_ocr, calculate_image_quality_score
from .ocr_config import OCRConfig, OCRResultCache, PaddleOCRVersionAdapter
from .ocr_report import OCRProcessingReport, OCRRegionStats

# Surya のインポート（オプショナル）
try:
    from surya.detection import DetectionPredictor
    from surya.layout import LayoutPredictor
    from surya.foundation import FoundationPredictor
    SURYA_AVAILABLE = True
except ImportError:
    SURYA_AVAILABLE = False
    logger.warning("[Hybrid OCR] Surya not installed - Surya mode disabled")

# PaddleOCR のインポート（オプショナル）
try:
    from paddleocr import PPStructureV3 as PPStructure, PaddleOCR
    PADDLEOCR_AVAILABLE = True
except ImportError:
    PADDLEOCR_AVAILABLE = False
    logger.warning("[Hybrid OCR] PaddleOCR not installed - PaddleOCR mode disabled")


class StageFVisualAnalyzer:
    """Stage F: 視覚解析（Surya + PaddleOCR + Gemini Vision のハイブリッド）"""

    def __init__(self, llm_client: LLMClient, enable_hybrid_ocr: bool = False):
        """
        Args:
            llm_client: LLMクライアント
            enable_hybrid_ocr: ハイブリッドOCR（Surya + PaddleOCR）を有効化
        """
        self.llm_client = llm_client
        self.enable_hybrid_ocr = enable_hybrid_ocr

        
        # OCR結果キャッシュ
        self.ocr_cache = OCRResultCache() if enable_hybrid_ocr else None
        
        # Hybrid OCR engines (lazy loading)
        self.surya_detector = None
        self.surya_layout = None
        self.paddle_ocr = None
        self.paddle_structure = None  # 表抽出用

        if enable_hybrid_ocr:
            self._initialize_hybrid_ocr_engines()

    def should_run(self, mime_type: str, extracted_text_length: int) -> bool:
        """
        Stage F を実行すべきか判定

        発動条件:
        1. 画像ファイル
        2. Pre-processing でテキストがほとんど抽出できなかった（100文字未満）

        Args:
            mime_type: MIMEタイプ
            extracted_text_length: Stage E で抽出したテキストの長さ

        Returns:
            True: Stage F を実行すべき
        """
        # 条件1: 画像ファイル
        if mime_type and mime_type.startswith('image/'):
            logger.info("[Stage F] 画像ファイルを検出 → Vision処理実行")
            return True

        # 条件2: テキストがほとんど抽出できなかった
        if extracted_text_length < 100:
            logger.info(f"[Stage F] テキスト量が少ない({extracted_text_length}文字) → Vision処理実行")
            return True

        return False

    def analyze(self, file_path: Path) -> Dict[str, Any]:
        """
        画像/PDFから視覚情報を抽出（廃止予定メソッド）

        Args:
            file_path: ファイルパス

        Returns:
            {
                'success': bool,
                'vision_raw': str,
                'vision_json': dict,
                'char_count': int
            }
        """
        logger.info("[Stage F] Visual Analysis開始...")

        if not file_path.exists():
            logger.error(f"[Stage F エラー] ファイルが存在しません: {file_path}")
            return {
                'success': False,
                'vision_raw': '',
                'vision_json': None,
                'char_count': 0,
                'error': 'File not found'
            }

        try:
            # NOTE: この analyze() メソッドは廃止予定
            # 代わりに process() メソッドを使用してください
            vision_raw = self.llm_client.generate_with_vision(
                prompt="<deprecated>",
                image_path=str(file_path),
                model="gemini-2.5-flash",
                response_format="json"
            )

            logger.info(f"[Stage F完了] Vision結果: {len(vision_raw)}文字")

            vision_json = None
            try:
                vision_json = json.loads(vision_raw)
            except json.JSONDecodeError as e:
                logger.warning(f"[Stage F] JSON解析失敗: {e}")

            return {
                'success': True,
                'vision_raw': vision_raw,
                'vision_json': vision_json,
                'char_count': len(vision_raw)
            }

        except Exception as e:
            logger.error(f"[Stage F エラー] Vision処理失敗: {e}", exc_info=True)
            return {
                'success': False,
                'vision_raw': '',
                'vision_json': None,
                'char_count': 0,
                'error': str(e)
            }

    def process(
        self,
        file_path: Path,
        prompt: str,
        model: str,
        extracted_text: str = "",
        workspace: str = "default"
    ) -> str:
        """
        画像/PDFから視覚情報を抽出（F-1～F-10の完全フロー）

        Args:
            file_path: ファイルパス
            prompt: プロンプトテキスト（config/prompts/stage_f/*.md から読み込み）
            model: モデル名（config/models.yaml から取得）
            extracted_text: Stage E で抽出した完全なテキスト
            workspace: ワークスペース名（gmail の場合は表抽出をスキップ）

        Returns:
            vision_raw: 3つの情報（full_text, layout_info, visual_elements）のJSONテキスト
        """
        total_start_time = time.time()

        logger.info("=" * 60)
        logger.info(f"[Stage F] ハイブリッドOCR処理開始 (model={model})")
        logger.info(f"  ├─ ファイル: {file_path.name}")
        logger.info(f"  ├─ Stage Eテキスト: {len(extracted_text)}文字")
        logger.info(f"  └─ ハイブリッドモード: {'有効' if self.enable_hybrid_ocr else '無効（Geminiのみ）'}")
        logger.info("=" * 60)

        if not file_path.exists():
            logger.error(f"[Stage F エラー] ファイルが存在しません: {file_path}")
            return ""

        # Gemini Vision APIがサポートしていないファイルタイプをスキップ
        unsupported_extensions = {'.pptx', '.ppt', '.doc', '.docx', '.xls', '.xlsx'}
        if file_path.suffix.lower() in unsupported_extensions:
            logger.info(f"[Stage F] スキップ: {file_path.suffix} はVision APIでサポートされていません")
            return ""

        try:
            # ============================================
            # [F-1] PaddleOCR 表構造抽出
            # ============================================
            paddle_tables = []
            paddle_text_chars = 0
            total_cells = 0

            # Gmail処理では表抽出をスキップ（ハング問題回避）
            if workspace == 'gmail':
                logger.info("[F-1] PaddleOCR表抽出: スキップ（Gmail処理のため無効化）")
            elif self.enable_hybrid_ocr and PADDLEOCR_AVAILABLE:
                f1_start = time.time()
                logger.info("[F-1] PaddleOCR 表構造抽出開始...")

                paddle_tables = self._extract_tables_with_paddleocr(file_path)

                # 統計計算
                for table in paddle_tables:
                    rows = table.get('rows', [])
                    for row in rows:
                        total_cells += len(row)
                        paddle_text_chars += sum(len(cell) for cell in row)

                f1_elapsed = time.time() - f1_start
                logger.info(f"[F-1完了] PaddleOCR 表抽出:")
                logger.info(f"  ├─ 検出表数: {len(paddle_tables)}個")
                logger.info(f"  ├─ 総セル数: {total_cells}個")
                logger.info(f"  ├─ 抽出文字数: {paddle_text_chars}文字")
                logger.info(f"  └─ 処理時間: {f1_elapsed:.2f}秒")
            else:
                logger.info("[F-1] PaddleOCR表抽出: スキップ（ハイブリッドモード無効）")

            # ============================================
            # [F-2] Surya レイアウト解析
            # ============================================
            text_boxes = []
            img_width = 0
            img_height = 0
            avg_bbox_size = 0

            if self.enable_hybrid_ocr and self.surya_detector:
                f2_start = time.time()
                logger.info("[F-2] Suryaレイアウト解析開始...")

                # 画像読み込み（PDFの場合は最初のページを画像に変換）
                file_ext = str(file_path).lower().split('.')[-1]
                if file_ext == 'pdf':
                    import fitz  # PyMuPDF
                    doc = fitz.open(file_path)
                    page = doc[0]  # 最初のページ
                    # 高解像度でレンダリング (300 DPI)
                    mat = fitz.Matrix(300/72, 300/72)
                    pix = page.get_pixmap(matrix=mat)
                    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    doc.close()
                    logger.info(f"  ├─ PDF→画像変換完了: {pix.width}x{pix.height}px")
                else:
                    image = Image.open(file_path).convert("RGB")
                img_width, img_height = image.size

                # 画像サイズ制限（Suryaのメモリ問題対策）
                # 文字認識は別で元画像を使うのでリサイズOK
                MAX_DIMENSION = 2000  # 最大辺を2000pxに制限
                if max(img_width, img_height) > MAX_DIMENSION:
                    scale = MAX_DIMENSION / max(img_width, img_height)
                    new_width = int(img_width * scale)
                    new_height = int(img_height * scale)
                    image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
                    logger.info(f"  ├─ 画像リサイズ（Surya用）: {img_width}x{img_height} → {new_width}x{new_height}px")
                    img_width, img_height = new_width, new_height

                # Suryaでレイアウト検出
                detection_results = self.surya_detector([image])
                # PolygonBoxオブジェクトからbbox（[x1,y1,x2,y2]）を抽出
                text_boxes = [box.bbox for box in detection_results[0].bboxes] if detection_results and detection_results[0].bboxes else []

                # 平均領域サイズ計算
                if text_boxes:
                    bbox_sizes = [(bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) for bbox in text_boxes]
                    avg_bbox_size = sum(bbox_sizes) / len(bbox_sizes)

                f2_elapsed = time.time() - f2_start
                logger.info(f"[F-2完了] Suryaレイアウト解析:")
                logger.info(f"  ├─ 検出領域数: {len(text_boxes)}個")
                logger.info(f"  ├─ 画像サイズ: {img_width}x{img_height}px")
                logger.info(f"  ├─ 平均領域サイズ: {avg_bbox_size:.1f}px²")
                logger.info(f"  └─ 処理時間: {f2_elapsed:.2f}秒")
            else:
                logger.info("[F-2] Suryaレイアウト解析: スキップ（ハイブリッドモード無効）")
                # Geminiのみの場合でも画像サイズは取得
                try:
                    file_ext = str(file_path).lower().split('.')[-1]
                    if file_ext == 'pdf':
                        import fitz  # PyMuPDF
                        doc = fitz.open(file_path)
                        page = doc[0]
                        mat = fitz.Matrix(300/72, 300/72)
                        pix = page.get_pixmap(matrix=mat)
                        image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                        img_width, img_height = pix.width, pix.height
                        doc.close()
                    else:
                        image = Image.open(file_path).convert("RGB")
                        img_width, img_height = image.size
                except:
                    pass

            # ============================================
            # [F-3] 画像切り出し
            # ============================================
            cropped_regions = []
            min_w = min_h = max_w = max_h = avg_w = avg_h = 0

            # Gmail はレイアウト検出のみ、切り出し＆OCRはスキップ
            if workspace == 'gmail':
                logger.info("[F-3] 画像切り出し: スキップ（Gmail軽量モード: Suryaレイアウトのみ使用）")
            elif self.enable_hybrid_ocr and text_boxes:
                f3_start = time.time()
                logger.info("[F-3] 画像切り出し開始...")

                img_array = np.array(image)
                widths = []
                heights = []

                for idx, bbox in enumerate(text_boxes):
                    x1, y1, x2, y2 = map(int, [bbox[0], bbox[1], bbox[2], bbox[3]])
                    cropped = img_array[y1:y2, x1:x2]

                    w, h = x2 - x1, y2 - y1
                    widths.append(w)
                    heights.append(h)

                    cropped_regions.append({
                        'bbox': [x1, y1, x2, y2],
                        'image': cropped,
                        'region_id': idx
                    })

                # 統計計算
                if widths and heights:
                    min_w, max_w = min(widths), max(widths)
                    min_h, max_h = min(heights), max(heights)
                    avg_w = sum(widths) / len(widths)
                    avg_h = sum(heights) / len(heights)

                f3_elapsed = time.time() - f3_start
                logger.info(f"[F-3完了] 画像切り出し:")
                logger.info(f"  ├─ 切り出し領域数: {len(cropped_regions)}個")
                logger.info(f"  ├─ 最小領域サイズ: {min_w}x{min_h}px")
                logger.info(f"  ├─ 最大領域サイズ: {max_w}x{max_h}px")
                logger.info(f"  ├─ 平均領域サイズ: {avg_w:.0f}x{avg_h:.0f}px")
                logger.info(f"  └─ 処理時間: {f3_elapsed:.2f}秒")
            else:
                logger.info("[F-3] 画像切り出し: スキップ（ハイブリッドモード無効）")

            # ============================================
            # [F-4] PaddleOCR テキスト認識
            # ============================================
            regions = []
            recognized_regions = 0
            total_chars = 0
            confidence_scores = []
            low_conf_count = 0

            if self.enable_hybrid_ocr and cropped_regions and self.paddle_ocr:
                f4_start = time.time()
                logger.info(f"[F-4] PaddleOCRテキスト認識開始... ({len(cropped_regions)}領域)")

                for idx, region_data in enumerate(cropped_regions):
                    try:
                        # PaddleOCR 3.xではcls引数は廃止、use_textline_orientation初期化時に設定済み
                        # 画像品質評価
                        quality_score = calculate_image_quality_score(region_data['image'])
                        
                        # 画像前処理（品質スコアに応じて適用）
                        preprocessed_image = region_data['image']
                        if quality_score < 0.7:
                            preprocessed_image, preprocess_stats = preprocess_image_for_ocr(
                                region_data['image'],
                                apply_clahe=True,
                                apply_denoise=True,
                                apply_sharpen=True,
                                apply_binarize=False
                            )
                        else:
                            preprocessed_image, preprocess_stats = preprocess_image_for_ocr(
                                region_data['image'],
                                apply_clahe=True,
                                apply_denoise=False,
                                apply_sharpen=True,
                                apply_binarize=False
                            )
                        
                        result = self.paddle_ocr.ocr(preprocessed_image)
                        text_lines = []
                        region_confidences = []

                        # PaddleOCR 3.x: OCRResultオブジェクトを処理
                        if result and len(result) > 0:
                            ocr_result = result[0]
                            # PaddleOCR 3.x: OCRResultは辞書ライクオブジェクト
                            # rec_texts, rec_scoresは属性ではなく辞書キーでアクセス
                            if isinstance(ocr_result, dict) or hasattr(ocr_result, '__getitem__'):
                                rec_texts = ocr_result.get('rec_texts', []) if hasattr(ocr_result, 'get') else ocr_result['rec_texts'] if 'rec_texts' in ocr_result else []
                                rec_scores = ocr_result.get('rec_scores', []) if hasattr(ocr_result, 'get') else ocr_result['rec_scores'] if 'rec_scores' in ocr_result else []
                                if rec_texts:
                                    text_lines = list(rec_texts)
                                    region_confidences = list(rec_scores) if rec_scores else []
                            # 旧API互換（リスト形式）
                            elif isinstance(ocr_result, list):
                                for line in ocr_result:
                                    if line and len(line) >= 2 and line[1]:
                                        text_lines.append(line[1][0])
                                        region_confidences.append(line[1][1])

                        text = "\n".join(text_lines)
                        avg_confidence = sum(region_confidences) / len(region_confidences) if region_confidences else 0.0

                        if text.strip():
                            recognized_regions += 1
                            total_chars += len(text)
                            confidence_scores.append(avg_confidence)

                            if avg_confidence < 0.7:
                                low_conf_count += 1

                        regions.append({
                            'bbox': region_data['bbox'],
                            'text': text,
                            'confidence': avg_confidence,
                            'region_id': idx
                        })

                        # 進捗表示
                        if (idx + 1) % 10 == 0:
                            logger.info(f"[F-4] 進捗: {idx + 1}/{len(cropped_regions)}領域処理完了 ({(idx + 1)/len(cropped_regions)*100:.1f}%)")

                    except Exception as e:
                        logger.warning(f"[F-4] 領域 {idx} のOCR失敗: {e}")


                avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.0

                f4_elapsed = time.time() - f4_start
                logger.info(f"[F-4完了] PaddleOCRテキスト認識:")
                logger.info(f"  ├─ 認識領域数: {recognized_regions}/{len(cropped_regions)}個")
                logger.info(f"  ├─ 認識文字数: {total_chars}文字")
                logger.info(f"  ├─ 平均信頼度: {avg_confidence:.2%}")
                logger.info(f"  ├─ 低信頼度領域: {low_conf_count}個 (< 0.7)")
                logger.info(f"  └─ 処理時間: {f4_elapsed:.2f}秒")
            else:
                logger.info("[F-4] PaddleOCRテキスト認識: スキップ（ハイブリッドモード無効）")

            # ============================================
            # [F-5] テキスト統合
            # ============================================
            surya_full_text = ""
            before_regions = len(regions)
            after_regions = 0
            avg_line_length = 0

            if self.enable_hybrid_ocr and regions:
                f5_start = time.time()
                logger.info("[F-5] テキスト統合開始（読み順ソート）...")

                surya_full_text = self._combine_hybrid_results(regions)

                # 統計計算
                after_regions = len([r for r in regions if r['text'].strip()])
                lines = surya_full_text.split('\n')
                avg_line_length = sum(len(line) for line in lines) / len(lines) if lines else 0

                f5_elapsed = time.time() - f5_start
                logger.info(f"[F-5完了] テキスト統合:")
                logger.info(f"  ├─ 統合前領域数: {before_regions}個")
                logger.info(f"  ├─ 統合後領域数: {after_regions}個")
                logger.info(f"  ├─ 全文テキスト: {len(surya_full_text)}文字")
                logger.info(f"  ├─ 平均行長: {avg_line_length:.1f}文字/行")
                logger.info(f"  └─ 処理時間: {f5_elapsed:.2f}秒")
            else:
                logger.info("[F-5] テキスト統合: スキップ（ハイブリッドモード無効）")

            # ============================================
            # [F-6] プロンプト構築
            # ============================================
            f6_start = time.time()
            logger.info("[F-6] プロンプト構築開始...")

            base_prompt_chars = len(prompt)
            full_prompt = prompt
            stage_e_chars = 0
            paddle_chars = 0
            surya_chars = 0

            # Stage E のテキストを追加
            if extracted_text:
                full_prompt += "\n\n---\n\n【Stage E で抽出したテキスト】\n"
                full_prompt += f"```\n{extracted_text}\n```\n\n"
                stage_e_chars = len(extracted_text)

            # PaddleOCR の表を追加
            if paddle_tables:
                full_prompt += "\n\n---\n\n【PaddleOCR で抽出した表】\n"
                full_prompt += f"{len(paddle_tables)}個の表を検出しました：\n\n"
                for i, table in enumerate(paddle_tables, 1):
                    full_prompt += f"**表{i}:**\n"
                    full_prompt += "```\n"
                    for row in table['rows']:
                        full_prompt += " | ".join(row) + "\n"
                    full_prompt += "```\n\n"
                paddle_chars = paddle_text_chars

            # Suryaテキストを追加
            if surya_full_text:
                full_prompt += "\n\n---\n\n【Surya + PaddleOCR で抽出したテキスト】\n"
                full_prompt += f"```\n{surya_full_text}\n```\n\n"
                surya_chars = len(surya_full_text)

            # Suryaレイアウト情報を追加（Gmail軽量モード用）
            elif text_boxes and workspace == 'gmail':
                full_prompt += "\n\n---\n\n【Surya で検出したレイアウト構造】\n"
                full_prompt += f"{len(text_boxes)}個のテキスト領域を検出しました。\n"
                full_prompt += "画像を見て、これらの領域の内容とレイアウト構造を正確に記述してください。\n\n"

            # 役割説明を追加
            if extracted_text or paddle_tables or surya_full_text:
                full_prompt += """【あなたの役割】
上記の Stage E のテキスト、PaddleOCR の表、Surya のテキストを統合し、画像を詳細に見て完璧な結果を作成してください：

1. **ベース**: Stage E で抽出したテキストを `full_text` のベースとして使用する
2. **補完**: Surya/PaddleOCR で見つかった追加のテキスト、表、レイアウト情報を追加する
3. **検証**: 画像を見て、全ての情報が正しいか確認する
4. **強化**: 両方で欠けている部分を補完する（画像化されたタイトル、ロゴ、装飾文字、見逃した表など）

**重要な優先順位**:
- `full_text`: **Stage E のテキストをベースに**、Surya のテキストで補完・追加する
- `layout_info.tables`: PaddleOCR が抽出した表を必ず含め、さらに見逃した表があれば追加する
- `layout_info.sections`: Surya のレイアウト情報を活用し、セクション構造を正確に記述する
- 画像を詳細に見て、全ての文字と要素を漏らさず拾ってください
"""
            elif text_boxes and workspace == 'gmail':
                full_prompt += """【あなたの役割】
上記の Stage E のテキストと Surya のレイアウト構造を活用し、画像を見て完璧な結果を作成してください：

1. **ベース**: Stage E で抽出したテキストを `full_text` のベースとして使用する
2. **レイアウト**: Surya が検出したレイアウト構造を `layout_info.sections` に記述する
3. **視覚要素**: 画像、チャート、強調されたテキストなどを `visual_elements` に記述する
4. **検証**: 画像を見て、テキストとレイアウトが正確か確認する

**注意**: これはGmail HTMLメールのため、テキストは既に抽出済みです。視覚的な構造と要素に集中してください。
"""
            else:
                full_prompt += "\n\n【注意】Stage E でテキストを抽出できませんでした。画像から全ての文字と表を拾い尽くしてください。\n"

            total_prompt_chars = len(full_prompt)

            f6_elapsed = time.time() - f6_start
            logger.info(f"[F-6完了] プロンプト構築:")
            logger.info(f"  ├─ 基本プロンプト: {base_prompt_chars}文字")
            logger.info(f"  ├─ Stage Eテキスト: {stage_e_chars}文字")
            logger.info(f"  ├─ PaddleOCR表: {len(paddle_tables)}個 ({paddle_chars}文字)")
            logger.info(f"  ├─ Suryaテキスト: {surya_chars}文字")
            logger.info(f"  ├─ 最終プロンプト: {total_prompt_chars}文字")
            logger.info(f"  └─ 処理時間: {f6_elapsed:.2f}秒")

            # ============================================
            # [F-7] Gemini Vision API呼び出し
            # ============================================
            f7_start = time.time()
            logger.info(f"[F-7] Gemini Vision API呼び出し開始 (model={model}, max_tokens=65536)...")

            try:
                vision_raw = self.llm_client.generate_with_vision(
                    prompt=full_prompt,
                    image_path=str(file_path),
                    model=model,
                    max_tokens=65536,
                    response_format="json"
                )

                f7_elapsed = time.time() - f7_start
                estimated_tokens = len(vision_raw) // 4  # 概算
                chars_per_sec = len(vision_raw) / f7_elapsed if f7_elapsed > 0 else 0

                logger.info(f"[F-7完了] Gemini Vision API応答受信:")
                logger.info(f"  ├─ 応答サイズ: {len(vision_raw)}文字")
                logger.info(f"  ├─ 推定トークン数: ~{estimated_tokens}トークン")
                logger.info(f"  ├─ 処理時間: {f7_elapsed:.2f}秒")
                logger.info(f"  └─ レート: {chars_per_sec:.0f}文字/秒")

                logger.debug(f"[F-7] Gemini生応答（最初の500文字）: {vision_raw[:500]}")
                logger.debug(f"[F-7] Gemini生応答（最後の500文字）: {vision_raw[-500:]}")

            except MaxTokensExceededError as e:
                # MAX_TOKENSエラー: 途中で切れた出力をファイルに保存してエラーとして記録
                f7_elapsed = time.time() - f7_start

                # 途中で切れた出力をファイルに保存
                error_output_dir = Path("logs/max_tokens_errors")
                error_output_dir.mkdir(parents=True, exist_ok=True)
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                error_file = error_output_dir / f"max_tokens_error_{file_path.stem}_{timestamp}.txt"

                try:
                    with open(error_file, 'w', encoding='utf-8') as f:
                        f.write(f"=== MAX_TOKENS ERROR ===\n")
                        f.write(f"File: {file_path}\n")
                        f.write(f"Model: {model}\n")
                        f.write(f"Output size: {len(e.partial_output)} characters\n")
                        f.write(f"Estimated tokens: ~{len(e.partial_output) // 4}\n")
                        f.write(f"Finish reason: {e.finish_reason_name}\n")
                        f.write(f"Processing time: {f7_elapsed:.2f}s\n")
                        f.write(f"\n=== PARTIAL OUTPUT (FULL) ===\n\n")
                        f.write(e.partial_output)
                    logger.info(f"[F-7] 途中で切れた出力を保存しました: {error_file}")
                except Exception as save_error:
                    logger.error(f"[F-7] 出力ファイル保存失敗: {save_error}")

                logger.error("=" * 80)
                logger.error(f"[F-7 MAX_TOKENS エラー] 出力が途中で切れました")
                logger.error(f"  ├─ エラー内容: {e}")
                logger.error(f"  ├─ 途中で切れた出力サイズ: {len(e.partial_output)}文字")
                logger.error(f"  ├─ 推定トークン数: ~{len(e.partial_output) // 4}トークン")
                logger.error(f"  ├─ finish_reason: {e.finish_reason_name}")
                logger.error(f"  ├─ 処理時間: {f7_elapsed:.2f}秒")
                logger.error(f"  ├─ 全文保存先: {error_file}")
                logger.error(f"  └─ 対処法: プロンプトを短くするか、max_tokensを増やす")
                logger.error("=" * 80)
                logger.error(f"[F-7] 途中で切れた出力（最初の1000文字）:")
                logger.error(e.partial_output[:1000])
                logger.error(f"[F-7] 途中で切れた出力（最後の1000文字）:")
                logger.error(e.partial_output[-1000:])
                logger.error("=" * 80)

                # 途中で切れた出力を返さず、空文字を返してエラーとして扱う
                raise

            # ============================================
            # [F-8] JSON クリーニング
            # ============================================
            f8_start = time.time()
            logger.info("[F-8] JSONクリーニング開始...")

            vision_cleaned = self._clean_json_response(vision_raw)

            reduction_rate = (1 - len(vision_cleaned) / len(vision_raw)) * 100 if len(vision_raw) > 0 else 0
            is_valid_json = False
            try:
                json.loads(vision_cleaned)
                is_valid_json = True
            except:
                pass

            f8_elapsed = time.time() - f8_start
            logger.info(f"[F-8完了] JSONクリーニング:")
            logger.info(f"  ├─ クリーニング前: {len(vision_raw)}文字")
            logger.info(f"  ├─ クリーニング後: {len(vision_cleaned)}文字")
            logger.info(f"  ├─ 削減率: {reduction_rate:.1f}%")
            logger.info(f"  ├─ JSON形式: {'有効' if is_valid_json else '無効'}")
            logger.info(f"  └─ 処理時間: {f8_elapsed:.2f}秒")

            # ============================================
            # [F-9] 全結果マージ
            # ============================================
            f9_start = time.time()
            logger.info("[F-9] 全結果マージ開始...")

            # PaddleOCR表とVision結果をマージ
            paddle_table_count = len(paddle_tables)
            gemini_table_count = 0
            merged_table_count = 0
            duplicates = 0

            if paddle_tables:
                vision_cleaned = self._merge_paddle_and_vision(vision_cleaned, paddle_tables)

                # 統計取得
                try:
                    vision_data = json.loads(vision_cleaned)
                    merged_tables = vision_data.get('layout_info', {}).get('tables', [])
                    merged_table_count = len(merged_tables)
                    gemini_table_count = merged_table_count - paddle_table_count
                    duplicates = paddle_table_count + gemini_table_count - merged_table_count
                except:
                    pass

            # full_textの統合
            stage_e_len = len(extracted_text)
            surya_len = len(surya_full_text)
            gemini_len = 0
            total_len = 0
            merged_full_text = ""

            try:
                vision_data = json.loads(vision_cleaned)
                gemini_full_text = vision_data.get('full_text', '')
                gemini_len = len(gemini_full_text)

                # 最適なfull_textを選択してマージ
                if surya_full_text and gemini_full_text:
                    # Geminiのテキストに Surya のテキストが含まれていれば、Geminiを使用
                    if surya_full_text in gemini_full_text:
                        merged_full_text = gemini_full_text
                        total_len = gemini_len
                    else:
                        # Suryaのテキストを優先（より詳細な可能性）
                        merged_full_text = surya_full_text
                        total_len = surya_len
                elif surya_full_text:
                    merged_full_text = surya_full_text
                    total_len = surya_len
                elif gemini_full_text:
                    merged_full_text = gemini_full_text
                    total_len = gemini_len
                elif extracted_text:
                    # Stage Eのテキストをフォールバックとして使用
                    merged_full_text = extracted_text
                    total_len = stage_e_len

                # vision_dataのfull_textを更新
                vision_data['full_text'] = merged_full_text
                vision_cleaned = json.dumps(vision_data, ensure_ascii=False)

            except Exception as e:
                logger.warning(f"[F-9] full_textマージ失敗: {e}")
                total_len = len(vision_cleaned)

            f9_elapsed = time.time() - f9_start
            logger.info(f"[F-9完了] 全結果マージ:")
            logger.info(f"  ├─ PaddleOCR表: {paddle_table_count}個")
            logger.info(f"  ├─ Gemini表: {gemini_table_count}個")
            logger.info(f"  ├─ マージ後表数: {merged_table_count}個 (重複{duplicates}個削除)")
            logger.info(f"  ├─ full_text結合: Stage E({stage_e_len}) + Surya({surya_len}) + Gemini({gemini_len}) = {total_len}文字")
            logger.info(f"  └─ 処理時間: {f9_elapsed:.2f}秒")

            # ============================================
            # [F-10] 最終検証・出力
            # ============================================
            f10_start = time.time()
            logger.info("[F-10] 最終検証開始...")

            # 最終データ検証
            full_text_chars = 0
            sections_count = 0
            tables_count = 0
            layout_info_size = 0
            images_count = 0
            charts_count = 0
            visual_elements_size = 0
            total_json_size = len(vision_cleaned)

            try:
                vision_json = json.loads(vision_cleaned)
                full_text = vision_json.get('full_text', '')
                layout_info = vision_json.get('layout_info', {})
                visual_elements = vision_json.get('visual_elements', {})

                full_text_chars = len(full_text)
                sections_count = len(layout_info.get('sections', []))
                tables_count = len(layout_info.get('tables', []))
                layout_info_size = len(json.dumps(layout_info, ensure_ascii=False))

                images_count = len(visual_elements.get('images', []))
                charts_count = len(visual_elements.get('charts', []))
                visual_elements_size = len(json.dumps(visual_elements, ensure_ascii=False))

            except Exception as e:
                logger.warning(f"[F-10] JSON解析失敗: {e}")
                logger.debug(f"[F-10] クリーニング後（最初の1000文字）: {vision_cleaned[:1000]}")
                logger.debug(f"[F-10] クリーニング後（最後の500文字）: {vision_cleaned[-500:]}")

            f10_elapsed = time.time() - f10_start
            total_elapsed = time.time() - total_start_time

            logger.info(f"[F-10完了] 最終検証・出力:")
            logger.info(f"  ├─ full_text: {full_text_chars}文字")
            logger.info(f"  ├─ layout_info:")
            logger.info(f"  │   ├─ sections: {sections_count}個")
            logger.info(f"  │   ├─ tables: {tables_count}個")
            logger.info(f"  │   └─ 合計: {layout_info_size}文字 (JSON)")
            logger.info(f"  ├─ visual_elements:")
            logger.info(f"  │   ├─ images: {images_count}個")
            logger.info(f"  │   ├─ charts: {charts_count}個")
            logger.info(f"  │   └─ 合計: {visual_elements_size}文字 (JSON)")
            logger.info(f"  ├─ 最終JSONサイズ: {total_json_size}文字")
            logger.info(f"  ├─ F-10処理時間: {f10_elapsed:.2f}秒")
            logger.info(f"  └─ 総処理時間: {total_elapsed:.2f}秒")

            # ============================================
            # [Stage F 完了] 総括
            # ============================================
            logger.info("=" * 60)
            logger.info("[Stage F完了] ハイブリッドOCR処理完了")
            logger.info(f"  ├─ 入力: {file_path.name}")
            logger.info(f"  ├─ 出力: 3種類のデータ (full_text + layout_info + visual_elements)")
            logger.info(f"  ├─ 総文字数: {full_text_chars}文字")
            logger.info(f"  └─ 総処理時間: {total_elapsed:.2f}秒")
            logger.info("=" * 60)

            return vision_cleaned

        except Exception as e:
            logger.error(f"[Stage F エラー] Vision処理失敗: {e}", exc_info=True)
            return ""

    def _initialize_hybrid_ocr_engines(self):
        """ハイブリッドOCRエンジン（Surya + PaddleOCR）を初期化"""
        if not SURYA_AVAILABLE or not PADDLEOCR_AVAILABLE:
            logger.error("[Hybrid OCR] Required packages not installed")
            self.enable_hybrid_ocr = False
            return

        try:
            logger.info("[Hybrid OCR] Initializing Surya Foundation Model...")
            self.surya_foundation = FoundationPredictor()

            logger.info("[Hybrid OCR] Initializing Surya Detection...")
            self.surya_detector = DetectionPredictor()

            logger.info("[Hybrid OCR] Initializing Surya Layout...")
            self.surya_layout = LayoutPredictor(self.surya_foundation)

            logger.info("[Hybrid OCR] Initializing PaddleOCR (lang=japan) for text recognition...")
            self.paddle_ocr = PaddleOCR(lang='japan', use_textline_orientation=True)

            # PPStructureはキャッシュせず、都度初期化する（メモリリーク対策）
            self.paddle_structure = None
            logger.info("[Hybrid OCR] PPStructure: 都度初期化モード（メモリリーク対策）")

            logger.info("[Hybrid OCR] All engines initialized successfully!")
            
            # PaddleOCRバージョン検出
            paddle_version = PaddleOCRVersionAdapter.detect_version()


        except Exception as e:
            logger.error(f"[Hybrid OCR] Initialization failed: {e}")
            self.enable_hybrid_ocr = False

    def _extract_tables_with_paddleocr(self, file_path: Path) -> List[Dict[str, Any]]:
        """
        PaddleOCR で表構造を抽出（毎回初期化・削除でメモリリーク対策）

        Args:
            file_path: 画像/PDFファイルパス

        Returns:
            抽出された表のリスト [{"rows": [[]], "caption": ""}]
        """
        import gc
        import psutil
        import os
        import shutil
        import tempfile
        from PIL import Image

        # 毎回新しいPPStructureインスタンスを作成
        paddle_structure = None
        try:
            # ====== 診断情報収集 ======
            logger.info("=" * 60)
            logger.info("[F-1 診断] 詳細診断開始")

            # 1. ファイル情報
            file_exists = file_path.exists()
            file_size = file_path.stat().st_size if file_exists else 0
            file_name = file_path.name
            file_str = str(file_path)
            has_japanese = any(ord(c) > 127 for c in file_name)

            logger.info(f"[F-1 診断] ファイル情報:")
            logger.info(f"  ├─ パス: {file_str}")
            logger.info(f"  ├─ ファイル名: {file_name}")
            logger.info(f"  ├─ 存在: {file_exists}")
            logger.info(f"  ├─ サイズ: {file_size:,} bytes")
            logger.info(f"  ├─ 日本語文字含む: {has_japanese}")
            logger.info(f"  └─ 拡張子: {file_path.suffix}")

            # 2. 画像情報
            try:
                img = Image.open(file_path)
                img_size = img.size
                img_mode = img.mode
                img_format = img.format
                logger.info(f"[F-1 診断] 画像情報:")
                logger.info(f"  ├─ サイズ: {img_size[0]}x{img_size[1]}px")
                logger.info(f"  ├─ モード: {img_mode}")
                logger.info(f"  └─ フォーマット: {img_format}")
                img.close()
            except Exception as e:
                logger.error(f"[F-1 診断] 画像読み込み失敗: {e}")

            # 3. システムリソース
            process = psutil.Process(os.getpid())
            mem_info = process.memory_info()
            cpu_percent = process.cpu_percent(interval=0.1)
            logger.info(f"[F-1 診断] システムリソース:")
            logger.info(f"  ├─ メモリ使用量: {mem_info.rss / 1024 / 1024:.1f} MB")
            logger.info(f"  └─ CPU使用率: {cpu_percent:.1f}%")

            logger.info("=" * 60)

            # ====== メモリ診断開始 ======
            import time
            process = psutil.Process(os.getpid())

            mem_start = process.memory_info().rss / 1024 / 1024
            logger.info(f"[F-1 メモリ] 処理開始時: {mem_start:.1f} MB")

            # ====== PPStructure初期化 ======
            logger.debug("[F-1] PPStructure初期化開始...")
            init_start = time.time()
            paddle_structure = PPStructure(lang='japan', device='cpu')
            init_time = time.time() - init_start

            mem_after_init = process.memory_info().rss / 1024 / 1024
            mem_init_increase = mem_after_init - mem_start
            logger.debug(f"[F-1] PPStructure初期化完了 ({init_time:.2f}秒)")
            logger.info(f"[F-1 メモリ] 初期化後: {mem_after_init:.1f} MB (+{mem_init_increase:.1f} MB)")

            # ====== 診断テスト: ASCII名でコピー ======
            logger.info("[F-1 診断] テスト1: ASCII名で一時ファイル作成")
            with tempfile.NamedTemporaryFile(suffix=file_path.suffix, delete=False) as tmp:
                tmp_path = tmp.name
                shutil.copy(file_path, tmp_path)
                logger.info(f"  └─ 一時ファイル: {tmp_path}")

            # ====== predict実行（タイムアウト監視） ======
            logger.info("[F-1 診断] predict実行開始（ASCII名で）...")
            predict_start = time.time()

            try:
                result = list(paddle_structure.predict(tmp_path))
                predict_time = time.time() - predict_start

                mem_after_predict = process.memory_info().rss / 1024 / 1024
                mem_predict_increase = mem_after_predict - mem_after_init

                logger.info(f"[F-1 診断] predict成功！ ({predict_time:.2f}秒)")
                logger.info(f"  └─ 結果件数: {len(result)}件")
                logger.info(f"[F-1 メモリ] predict後: {mem_after_predict:.1f} MB (+{mem_predict_increase:.1f} MB)")

                # 一時ファイル削除
                os.unlink(tmp_path)

            except Exception as predict_error:
                predict_time = time.time() - predict_start
                logger.error(f"[F-1 診断] predict失敗 ({predict_time:.2f}秒): {predict_error}")
                os.unlink(tmp_path)
                raise

            tables = []
            for page_result in result:
                # 新しいAPI: page_result['table_res_list']から表を抽出
                table_list = page_result.get('table_res_list', []) if isinstance(page_result, dict) else getattr(page_result, 'table_res_list', [])
                for table_res in table_list:
                    # table_resから'html'属性を取得
                    html_content = table_res.get('html', '') if isinstance(table_res, dict) else getattr(table_res, 'html', '')
                    if html_content:
                        table_data = self._parse_html_table(html_content)
                        if table_data:
                            tables.append({
                                'rows': table_data,
                                'caption': f"PaddleOCR抽出表{len(tables) + 1}"
                            })

            return tables

        except Exception as e:
            logger.warning(f"[F-1] PaddleOCR 表抽出失敗: {e}")
            return []

        finally:
            # 必ずリソースを解放（メモリリーク対策）
            if paddle_structure is not None:
                logger.debug("[F-1] PPStructure リソース解放開始...")

                mem_before_cleanup = process.memory_info().rss / 1024 / 1024

                del paddle_structure
                gc.collect()  # ガベージコレクション強制実行

                mem_after_cleanup = process.memory_info().rss / 1024 / 1024
                mem_freed = mem_before_cleanup - mem_after_cleanup
                mem_leaked = mem_after_cleanup - mem_start

                logger.debug("[F-1] PPStructure リソース解放完了")
                logger.info(f"[F-1 メモリ] cleanup後: {mem_after_cleanup:.1f} MB (解放: {mem_freed:.1f} MB)")
                logger.info(f"[F-1 メモリ] 最終リーク量: {mem_leaked:.1f} MB")
                logger.info("=" * 60)

    def _parse_html_table(self, html: str) -> List[List[str]]:
        """
        HTML形式の表を行列に変換

        Args:
            html: HTML形式の表

        Returns:
            行列データ [[cell, cell], [cell, cell]]
        """
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, 'html.parser')
            table = soup.find('table')
            if not table:
                return []

            rows = []
            for tr in table.find_all('tr'):
                cells = []
                for td in tr.find_all(['td', 'th']):
                    cells.append(td.get_text(strip=True))
                if cells:
                    rows.append(cells)

            return rows

        except Exception as e:
            logger.warning(f"HTML表パース失敗: {e}")
            return []

    def _combine_hybrid_results(self, regions: List[Dict[str, Any]]) -> str:
        """
        ハイブリッドOCRの結果を統合（読み順に並び替え）

        Args:
            regions: 領域ごとの認識結果

        Returns:
            読み順に並べられた全テキスト
        """
        if not regions:
            return ""

        # Y座標でソート（上から下）、同じY範囲ならX座標でソート（左から右）
        sorted_regions = sorted(regions, key=lambda r: (r['bbox'][1], r['bbox'][0]))

        # テキストを結合
        texts = [r['text'] for r in sorted_regions if r['text'].strip()]
        return "\n\n".join(texts)

    def _merge_paddle_and_vision(
        self,
        vision_json_str: str,
        paddle_tables: List[Dict[str, Any]]
    ) -> str:
        """
        PaddleOCR の表と Vision API の結果をマージ

        Args:
            vision_json_str: Vision API の結果（JSON文字列）
            paddle_tables: PaddleOCR が抽出した表のリスト

        Returns:
            マージされたJSON文字列
        """
        try:
            vision_data = json.loads(vision_json_str)

            layout_info = vision_data.get('layout_info', {})
            vision_tables = layout_info.get('tables', [])

            # PaddleOCR の表を先頭に追加（高精度なので優先）
            merged_tables = paddle_tables + vision_tables

            # 重複削除（同じ内容の表を削除）
            unique_tables = []
            seen = set()
            for table in merged_tables:
                table_str = json.dumps(table.get('rows', []), sort_keys=True)
                if table_str not in seen:
                    seen.add(table_str)
                    unique_tables.append(table)

            layout_info['tables'] = unique_tables
            vision_data['layout_info'] = layout_info

            return json.dumps(vision_data, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.warning(f"[F-9] マージ失敗: {e}")
            return vision_json_str

    def _clean_json_response(self, response: str) -> str:
        """
        Gemini の応答からJSONを抽出してクリーニング

        Args:
            response: Gemini の生の応答

        Returns:
            クリーニングされたJSON文字列
        """
        import re

        # パターン1: ```json ... ``` で囲まれている場合
        json_match = re.search(r'```json\s*\n(.*?)\n```', response, re.DOTALL)
        if json_match:
            return json_match.group(1).strip()

        # パターン2: ``` ... ``` で囲まれている場合
        code_match = re.search(r'```\s*\n(.*?)\n```', response, re.DOTALL)
        if code_match:
            return code_match.group(1).strip()

        # パターン3: { ... } を探す（最初の{から最後の}まで）
        first_brace = response.find('{')
        last_brace = response.rfind('}')
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            return response[first_brace:last_brace + 1].strip()

        # パターン4: そのまま返す
        return response.strip()
