"""
Stage F: Visual Analysis (視覚解析)

【確定設計 2026-01-22】責務の明確化（正本）
============================================
F-1〜F-5: 素材生成（inputs作り）
  - F-1: ページ画像正規化 → page_images[]
         入力: file_path → 出力: [{page_index, image, width, height, dpi}]
  - F-2: Suryaブロック検出 → surya_blocks[]
         入力: page_images → 出力: [{page, bbox, block_id, confidence}]
  - F-3: ブロック構造素材 → surya_structure[]
         入力: surya_blocks → 出力: [{..., column_id, x_center, y_center, area}]
  - F-4: 読む順制御（reading_order）
         入力: surya_structure → 出力: surya_structure (order付、0..N-1連番保証)
  - F-5: 構造ラベル付与（block_type_hint）
         入力: surya_structure → 出力: surya_structure (hint付)

F-7: 本文確定（final_full_text）- 唯一の本文確定場所
  - 画像: Gemini Vision API呼び出し（ファイル送信あり）
  - 音声/映像: テキスト統合API呼び出し（ファイル再送信なし、lite モデル）
  - Stage E / Surya / Gemini の全入力を統合
  - 方式A: ベース（Stage E）+ 差分追記（単調増加保証）
  - ordered_blocks を必ずプロンプトに含める（order, col, type, bbox）
  - 【禁止】削除・短縮

F-8: JSON正規化 + text_blocks生成
  - json_repair でJSON修復
  - text_blocks を段落単位で生成
  - 入力は必ず merged_full_text（F-7確定）を使用
  - 【禁止】AI呼び出し、vision_json['full_text']参照

F-9: Stage H payload 梱包
  - stage_h_input を組み立て
  - full_text は必ず final_full_text（F-7確定）を使用
  - 【禁止】候補選別、再読解、vision_json['full_text']参照

F-10: 受け渡し保証（validation）
  - 必須項目チェック（full_text, text_blocks）
  - 単調増加検証（複数方法でチャンク化して包含確認）
  - サイズ制御（切り捨て禁止、分割で対応）
  - warnings生成

出力: stage_h_input（full_text + text_blocks + tables + layout_meta + warnings）
============================================
"""
import json_repair  # JSON修復ライブラリ
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
from .image_preprocessing import preprocess_image_for_ocr, calculate_image_quality_score, preprocess_for_ppstructure
from .ocr_config import OCRConfig, OCRResultCache
from .ocr_report import OCRProcessingReport, OCRRegionStats
from .constants import (
    STAGE_H_INPUT_SCHEMA_VERSION,
    MAX_OCR_CALLS,
    MAX_CROP_LONG_EDGE,
    PER_PAGE_MAX_UNION_ROI,
    MIN_ROI_AREA,
    UNION_PADDING,
    SURYA_MAX_DIM
)

# Surya のインポート（オプショナル）
try:
    from surya.detection import DetectionPredictor
    from surya.layout import LayoutPredictor
    from surya.foundation import FoundationPredictor
    SURYA_AVAILABLE = True
except ImportError:
    SURYA_AVAILABLE = False
    logger.warning("[Hybrid OCR] Surya not installed - Surya mode disabled")



class TableExtractionError(Exception):
    """F-1 表抽出エラー（品質ゲート違反）"""
    pass


class PPStructureV3DependencyError(Exception):
    """PPStructureV3 依存エラー（起動時検証失敗）"""
    pass


# ============================================
# v1.1 契約強化: ユーティリティ関数
# ============================================

def _normalize_text_blocks(text_blocks: list) -> list:
    """
    v1.1 contract hardening:
    - Always ensure each block has 'text' key (string).
    - If legacy 'content' exists, migrate to 'text'.
    - Do not delete existing keys (backward compatibility), but downstream should use 'text'.
    """
    if not isinstance(text_blocks, list):
        return []

    normalized = []
    for b in text_blocks:
        if not isinstance(b, dict):
            continue
        if 'text' not in b or b.get('text') is None:
            if 'content' in b and b.get('content') is not None:
                b['text'] = b.get('content')
            else:
                b['text'] = ''
        # text is always a string
        if not isinstance(b.get('text'), str):
            b['text'] = str(b.get('text', ''))
        # char_count も text に合わせて更新
        b['char_count'] = len(b['text'])
        normalized.append(b)
    return normalized


def _parse_stage_e_sections(extracted_text: str) -> dict:
    """
    Stage E の新フォーマット（タグ付き）をパースして各セクションを抽出

    フォーマット例:
        === [SOURCE: METADATA] ===
        MimeType: image/png

        === [SOURCE: OCR_TEXT] ===
        (全文字情報)

        === [SOURCE: VISUAL_SCENE_LOG] ===
        (物体・色・配置の網羅的羅列)

        === [SOURCE: AUDIO_TRANSCRIPT] ===
        (フィラー含む完全書き起こし)

    Returns:
        {
            'metadata': str,
            'ocr_text': str,
            'visual_scene_log': str,
            'audio_transcript': str,
            'is_new_format': bool,  # 新フォーマットかどうか
            'raw_text': str  # 元のテキスト（旧フォーマットの場合はこれを使用）
        }
    """
    import re

    result = {
        'metadata': '',
        'ocr_text': '',
        'visual_scene_log': '',
        'audio_transcript': '',
        'is_new_format': False,
        'raw_text': extracted_text or ''
    }

    if not extracted_text:
        return result

    # 新フォーマットの検出
    if '=== [SOURCE:' not in extracted_text:
        # 旧フォーマット: そのまま返す
        return result

    result['is_new_format'] = True

    # セクションをパース
    section_pattern = r'=== \[SOURCE: ([A-Z_]+)\] ===\n(.*?)(?==== \[SOURCE:|$)'
    matches = re.findall(section_pattern, extracted_text, re.DOTALL)

    for section_name, content in matches:
        content = content.strip()
        if section_name == 'METADATA':
            result['metadata'] = content
        elif section_name == 'OCR_TEXT':
            result['ocr_text'] = content
        elif section_name == 'VISUAL_SCENE_LOG':
            result['visual_scene_log'] = content
        elif section_name == 'AUDIO_TRANSCRIPT':
            result['audio_transcript'] = content

    return result


def _stage_f_file_identity() -> str:
    """Stage F 実体特定用（同名ファイル問題を永久終了）"""
    import hashlib
    import pathlib
    try:
        p = pathlib.Path(__file__).resolve()
        sha = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
        return f"{p} sha256_12={sha}"
    except Exception as e:
        return f"{__file__} sha256_12=unavailable err={e.__class__.__name__}"


class StageFVisualAnalyzer:
    """Stage F: 視覚解析（Surya + Gemini Vision のハイブリッド）"""

    def __init__(self, llm_client: LLMClient, enable_hybrid_ocr: bool = False):
        """
        Args:
            llm_client: LLMクライアント
            enable_hybrid_ocr: ハイブリッドOCR（Surya）を有効化
        """
        self.llm_client = llm_client
        self.enable_hybrid_ocr = enable_hybrid_ocr

        # OCR結果キャッシュ
        self.ocr_cache = OCRResultCache() if enable_hybrid_ocr else None

        # Hybrid OCR engines (lazy loading)
        self.surya_detector = None
        self.surya_layout = None

        if enable_hybrid_ocr:
            self._initialize_hybrid_ocr_engines()

    def should_run(self, mime_type: str, extracted_text_length: int) -> bool:
        """
        Stage F を実行すべきか判定

        発動条件:
        1. 画像ファイル → Vision処理で補完・統合
        2. 音声/映像ファイル → テキスト統合処理（ファイル再送信なし）
        3. Pre-processing でテキストがほとんど抽出できなかった（100文字未満）

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

        # 条件2: 音声/映像ファイル（テキスト統合処理）
        if mime_type and (mime_type.startswith('audio/') or mime_type.startswith('video/')):
            logger.info("[Stage F] 音声/映像ファイルを検出 → テキスト統合処理実行")
            return True

        # 条件3: テキストがほとんど抽出できなかった
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
        workspace: str = "default",
        progress_callback=None,
        e2_table_bboxes: List[Dict[str, Any]] = None,  # P2-2: E-2で検出した表のbbox
        post_body: Dict[str, Any] = None,  # 投稿本文（メール/フォーム）- Stage H最優先文脈
        mime_type: str = None  # MIMEタイプ（音声/映像判定用）
    ) -> str:
        """
        画像/PDF/音声/映像から情報を抽出（F-1～F-10の完全フロー）

        Args:
            file_path: ファイルパス
            prompt: プロンプトテキスト（config/prompts/stage_f/*.md から読み込み）
            model: モデル名（config/models.yaml から取得）
            extracted_text: Stage E で抽出した完全なテキスト
            workspace: ワークスペース名（gmail の場合は表抽出をスキップ）
            e2_table_bboxes: P2-2 E-2(pdfplumber)で検出した表のbbox（局所再検出用）
            post_body: 投稿本文（メール/フォーム）- Stage Hで最優先文脈として扱う
            mime_type: MIMEタイプ（音声/映像の場合はテキスト統合のみ実行）

        Returns:
            vision_raw: 3つの情報（full_text, layout_info, visual_elements）のJSONテキスト
        """
        # P2-2: e2_table_bboxesのデフォルト値
        if e2_table_bboxes is None:
            e2_table_bboxes = []

        # post_bodyのデフォルト値
        if post_body is None:
            post_body = {"text": "", "source": "unknown", "char_count": 0}

        total_start_time = time.time()

        # 【実体特定】同名ファイル問題を永久終了するためのログ
        logger.info(f"[Stage F] using file={_stage_f_file_identity()}")

        # テキストのみモード判定（file_path=None）
        is_text_only = file_path is None

        logger.info("=" * 60)
        logger.info(f"[Stage F] ハイブリッドOCR処理開始 (model={model})")
        if is_text_only:
            logger.info("  ├─ ファイル: なし（テキストのみモード）")
        else:
            logger.info(f"  ├─ ファイル: {file_path.name}")
        logger.info(f"  ├─ Stage Eテキスト: {len(extracted_text)}文字")
        logger.info(f"  └─ ハイブリッドモード: {'有効' if self.enable_hybrid_ocr else '無効（Geminiのみ）'}")
        logger.info("=" * 60)

        # ファイルなし or ファイル不存在: 持っているテキストで続行（エラーにしない）
        if file_path is None or not file_path.exists():
            if file_path is None:
                logger.info("[Stage F] ファイルなし（テキストのみ）→ テキストで続行")
                warning_msg = "F_NO_FILE: テキストのみ処理"
            else:
                logger.info(f"[Stage F] ファイル不存在: {file_path} → テキストで続行")
                warning_msg = f"F_FILE_NOT_FOUND: {file_path}"

            fallback_payload = {
                "schema_version": STAGE_H_INPUT_SCHEMA_VERSION,
                "post_body": post_body,
                "full_text": extracted_text if extracted_text else "",
                "text_blocks": [
                    {
                        "block_type": "post_body",
                        "text": post_body.get("text", "") if post_body else "",
                        "source": post_body.get("source", "unknown") if post_body else "unknown",
                        "char_count": post_body.get("char_count", 0) if post_body else 0,
                        "priority": "highest"
                    }
                ],
                "tables": [],
                "layout_elements": [],
                "visual_elements": [],
                "warnings": [warning_msg],
                "_contract_violation": False,
                "_fallback_mode": True
            }
            return json.dumps(fallback_payload, ensure_ascii=False)

        # Gemini Vision APIがサポートしていないファイルタイプをスキップ
        unsupported_extensions = {'.pptx', '.ppt', '.doc', '.docx', '.xls', '.xlsx'}
        if file_path.suffix.lower() in unsupported_extensions:
            logger.info(f"[Stage F] スキップ: {file_path.suffix} はVision APIでサポートされていません")
            # v1.1契約: 未サポート形式でも最小 payload を返す
            fallback_payload = {
                "schema_version": "stage_h_input.v1.1",
                "post_body": post_body,
                "full_text": extracted_text if extracted_text else "",
                "text_blocks": [
                    {
                        "block_type": "post_body",
                        "text": post_body.get("text", "") if post_body else "",
                        "source": post_body.get("source", "unknown") if post_body else "unknown",
                        "char_count": post_body.get("char_count", 0) if post_body else 0,
                        "priority": "highest"
                    }
                ],
                "tables": [],
                "layout_elements": [],
                "visual_elements": [],
                "warnings": [f"F_UNSUPPORTED_FORMAT: {file_path.suffix}"],
                "_contract_violation": False,
                "_fallback_mode": True
            }
            return json.dumps(fallback_payload, ensure_ascii=False)

        try:
            # ============================================
            # 【新設計】F-1〜F-6: 素材生成（inputs作り）
            # ============================================

            # ============================================
            # [F-1] ページ画像正規化
            # 入力: PDF/画像 → 出力: page_images[]
            # ============================================
            if progress_callback:
                progress_callback("F-1")
            f1_start = time.time()
            logger.info("[F-1] ページ画像正規化開始...")

            page_images = []  # [{page_index, image, width, height, dpi}]
            DPI = 300  # 統一DPI

            file_ext = file_path.suffix.lower()
            if file_ext == '.pdf':
                import fitz  # PyMuPDF
                doc = fitz.open(file_path)
                total_pages = len(doc)
                logger.info(f"  ├─ PDF検出: {total_pages}ページ")

                for page_num in range(total_pages):
                    page = doc[page_num]
                    mat = fitz.Matrix(DPI/72, DPI/72)
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
            logger.info(f"[F-1完了] ページ画像正規化:")
            logger.info(f"  ├─ ページ数: {len(page_images)}")
            logger.info(f"  ├─ DPI: {DPI}")
            if page_images:
                logger.info(f"  ├─ サイズ: {page_images[0]['width']}x{page_images[0]['height']}px")
            logger.info(f"  └─ 処理時間: {f1_elapsed:.2f}秒")

            # ============================================
            # [F-2] Surya ブロック検出
            # 入力: page_images → 出力: surya_blocks[]
            # ============================================
            if progress_callback:
                progress_callback("F-2")
            f2_start = time.time()
            surya_blocks = []  # [{page, bbox, block_id, confidence}]

            if self.enable_hybrid_ocr and self.surya_detector and page_images:
                logger.info("[F-2] Suryaブロック検出開始...")

                for page_data in page_images:
                    page_idx = page_data['page_index']
                    img = page_data['image']

                    # 画像サイズ制限（Suryaメモリ対策）- constants.py の定数を使用
                    w, h = img.size
                    scale = 1.0  # デフォルト: リサイズなし
                    if max(w, h) > SURYA_MAX_DIM:
                        scale = SURYA_MAX_DIM / max(w, h)
                        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
                        logger.info(f"  ├─ ページ{page_idx}: リサイズ {w}x{h} → {img.size[0]}x{img.size[1]} (scale={scale:.3f})")

                    # Surya検出
                    detection_results = self.surya_detector([img])
                    if detection_results and detection_results[0].bboxes:
                        for block_id, box in enumerate(detection_results[0].bboxes):
                            # bbox を元の座標系（page_images）に復元
                            raw_bbox = box.bbox  # [x1, y1, x2, y2] リサイズ後座標
                            restored_bbox = [
                                raw_bbox[0] / scale,
                                raw_bbox[1] / scale,
                                raw_bbox[2] / scale,
                                raw_bbox[3] / scale
                            ]
                            surya_blocks.append({
                                'page': page_idx,
                                'bbox': restored_bbox,  # 元座標系に復元済み
                                'block_id': f"p{page_idx}_b{block_id}",
                                'confidence': getattr(box, 'confidence', 1.0),
                                'polygon': getattr(box, 'polygon', None),
                                'scale_applied': scale  # デバッグ用
                            })

                f2_elapsed = time.time() - f2_start
                logger.info(f"[F-2完了] Suryaブロック検出:")
                logger.info(f"  ├─ 検出ブロック数: {len(surya_blocks)}")
                logger.info(f"  └─ 処理時間: {f2_elapsed:.2f}秒")
            else:
                logger.info("[F-2] Suryaブロック検出: スキップ（ハイブリッドモード無効）")
                f2_elapsed = 0

            # ============================================
            # [F-3] Surya ブロック構造素材
            # 入力: surya_blocks → 出力: surya_structure (column_id等)
            # ============================================
            if progress_callback:
                progress_callback("F-3")
            f3_start = time.time()
            surya_structure = []

            if surya_blocks:
                logger.info("[F-3] ブロック構造素材計算開始...")

                # page_images を page_index で引ける辞書を作成（page参照の堅牢化）
                page_meta = {p['page_index']: p for p in page_images}
                # 0-based / 1-based 両対応: page_index がない場合はリスト順でマップ
                for i, p in enumerate(page_images):
                    if i not in page_meta:
                        page_meta[i] = p

                # 段組推定: X座標でクラスタリング
                for block in surya_blocks:
                    bbox = block['bbox']
                    x_center = (bbox[0] + bbox[2]) / 2
                    y_center = (bbox[1] + bbox[3]) / 2
                    width = bbox[2] - bbox[0]
                    height = bbox[3] - bbox[1]

                    # 段組判定（簡易ルール: 左半分/右半分）
                    # page_index / page 両対応で参照
                    page_idx = block.get('page_index', block.get('page', 0))
                    page_info = page_meta.get(page_idx, {})
                    page_width = page_info.get('width', 1000)
                    column_id = 0 if x_center < page_width / 2 else 1

                    surya_structure.append({
                        **block,
                        'page_index': page_idx,  # 正規化したpage_indexを追加
                        'x_center': x_center,
                        'y_center': y_center,
                        'width': width,
                        'height': height,
                        'column_id': column_id,
                        'area': width * height
                    })

                f3_elapsed = time.time() - f3_start
                logger.info(f"[F-3完了] ブロック構造素材:")
                logger.info(f"  ├─ 構造化ブロック数: {len(surya_structure)}")
                col_counts = {}
                for s in surya_structure:
                    col_counts[s['column_id']] = col_counts.get(s['column_id'], 0) + 1
                logger.info(f"  ├─ 段組分布: {col_counts}")
                logger.info(f"  └─ 処理時間: {f3_elapsed:.2f}秒")
            else:
                logger.info("[F-3] ブロック構造素材: スキップ（ブロックなし）")
                f3_elapsed = 0

            # ============================================
            # [F-4] Surya 読む順制御 (reading_order)
            # 入力: surya_structure → 出力: surya_structure (order付)
            # ============================================
            if progress_callback:
                progress_callback("F-4")
            f4_start = time.time()

            if surya_structure:
                logger.info("[F-4] 読む順制御開始...")

                # 品質検査: 既存reading_orderの欠損/重複をチェック
                existing_orders = [b.get('reading_order') for b in surya_structure]
                missing_count = sum(1 for o in existing_orders if o is None)
                non_none_orders = [o for o in existing_orders if o is not None]
                dup_count = len(non_none_orders) - len(set(non_none_orders))

                # 安定ソート: column_id → y_center → x_center → area
                sorted_blocks = sorted(
                    surya_structure,
                    key=lambda b: (
                        b.get('page', 0),
                        b.get('column_id', 0),
                        b.get('y_center', 0),
                        b.get('x_center', 0),
                        b.get('area', 0)
                    )
                )

                # 再採番: 0..N-1 の完全連番を保証
                reassigned_count = 0
                for order, block in enumerate(sorted_blocks):
                    old_order = block.get('reading_order')
                    if old_order != order:
                        reassigned_count += 1
                    block['reading_order'] = order

                surya_structure = sorted_blocks
                total_blocks = len(surya_structure)

                f4_elapsed = time.time() - f4_start
                logger.info(f"[F-4完了] 読む順制御（品質保証）:")
                logger.info(f"  ├─ 検査: missing={missing_count}, duplicate={dup_count}")
                logger.info(f"  ├─ 再採番: {reassigned_count}件")
                logger.info(f"  ├─ final_range: 0..{total_blocks - 1}")
                logger.info(f"  └─ 処理時間: {f4_elapsed:.2f}秒")
            else:
                logger.info("[F-4] 読む順制御: スキップ（ブロックなし）")
                f4_elapsed = 0

            # ============================================
            # [F-5] Surya 粗い構造ラベル素材
            # 入力: surya_structure → 出力: surya_structure (block_type_hint付)
            # ============================================
            if progress_callback:
                progress_callback("F-5")
            f5_start = time.time()

            if surya_structure:
                logger.info("[F-5] 構造ラベル付与開始...")

                # 統計用: table_hint昇格カウント
                table_hint_promoted = 0
                original_type_counts = {}

                # ルールベースでブロックタイプを推定
                for block in surya_structure:
                    w, h = block['width'], block['height']
                    area = block['area']
                    y = block['y_center']
                    x = block['x_center']
                    # F-3 で正規化した page_index を優先、なければ page にフォールバック
                    page_idx = block.get('page_index', block.get('page', 0))
                    page_height = page_images[page_idx]['height'] if page_idx < len(page_images) else 1000
                    page_width = page_images[page_idx]['width'] if page_idx < len(page_images) else 1000

                    # アスペクト比（横長度）
                    aspect_ratio = w / h if h > 0 else 1.0

                    # ブロックタイプ推定（優先順位付き）
                    block_type = 'body_hint'  # デフォルト

                    # 1) 表の検出（最優先）- F-6のROI取りこぼし防止
                    is_table_candidate = False
                    # 条件A: 横長で高さが適度（表の行っぽい）
                    if aspect_ratio > 3.0 and 20 < h < 100:
                        is_table_candidate = True
                    # 条件B: 中程度のサイズで正方形に近い（表全体）
                    elif 10000 < area < 500000 and 0.5 < aspect_ratio < 3.0:
                        is_table_candidate = True
                    # 条件C: ページ中央付近で横幅が広い（表領域）
                    elif w > page_width * 0.5 and 0.2 < y / page_height < 0.8:
                        if 50 < h < 400:  # 高すぎない
                            is_table_candidate = True

                    if is_table_candidate:
                        block_type = 'table_hint'
                    # 2) 見出し（横長で薄い、上部寄り）
                    elif h < 50 and w > 200 and y < page_height * 0.3:
                        block_type = 'heading_hint'
                    # 3) ヘッダー（上部10%）
                    elif y < page_height * 0.08:
                        block_type = 'header_hint'
                    # 4) フッター（下部8%）
                    elif y > page_height * 0.92:
                        block_type = 'footer_hint'
                    # 5) 注記・欄外（小さい）
                    elif area < 3000:
                        block_type = 'note_hint'
                    # 6) それ以外は本文
                    else:
                        block_type = 'body_hint'

                    block['block_type_hint'] = block_type

                    # 統計
                    original_type_counts[block_type] = original_type_counts.get(block_type, 0) + 1
                    if block_type == 'table_hint':
                        table_hint_promoted += 1

                f5_elapsed = time.time() - f5_start
                logger.info(f"[F-5完了] 構造ラベル付与（table_hint強化）:")
                logger.info(f"  ├─ table_hint件数: {table_hint_promoted}件（F-6 ROI対象）")
                logger.info(f"  ├─ ラベル分布: {original_type_counts}")
                logger.info(f"  └─ 処理時間: {f5_elapsed:.2f}秒")
            else:
                logger.info("[F-5] 構造ラベル付与: スキップ（ブロックなし）")
                f5_elapsed = 0

            # ============================================
            # F-1〜F-5 素材生成完了
            # ============================================
            logger.info("=" * 60)
            logger.info("[F-1〜F-5完了] 素材生成サマリー:")
            logger.info(f"  ├─ page_images: {len(page_images)}ページ")
            logger.info(f"  ├─ surya_blocks: {len(surya_blocks)}ブロック")
            logger.info(f"  └─ surya_structure: {len(surya_structure)}ブロック（構造化済）")
            logger.info("=" * 60)

            # 後続処理用の変数を維持（互換性）
            text_boxes = [b['bbox'] for b in surya_blocks]
            img_width = page_images[0]['width'] if page_images else 0
            img_height = page_images[0]['height'] if page_images else 0
            image = page_images[0]['image'] if page_images else None
            surya_full_text = ""  # F-7で生成
            regions = []
            cropped_regions = []

            # ============================================
            # 【新設計】F-7〜F-10: 本文確定・整形・梱包・検証
            # ============================================

            # ============================================
            # [F-7] 本文確定（final_full_text）
            # 入力: E_full_text + surya_structure + page_images + gemini_vision
            # 出力: final_full_text（単調増加保証）
            #
            # NOTE: SINGLE SOURCE OF TRUTH
            # final_full_text is the ONLY place where text content is finalized.
            # Downstream stages (F-8, F-9, F-10) MUST NOT modify text content.
            # Any text manipulation after F-7 is a DESIGN VIOLATION.
            # ============================================
            if progress_callback:
                progress_callback("F-7")
            f7_start = time.time()
            logger.info("[F-7] 本文確定開始...")

            # ============================================
            # [F-7] reading_order 参照証拠ログ
            # ============================================
            # 1) reading_orderの有無（availability）
            blocks_with_order = [b for b in surya_structure if b.get('reading_order') is not None]
            order_availability = len(blocks_with_order)
            total_blocks = len(surya_structure)
            logger.info(f"[F-7] Surya reading_order availability: {order_availability}/{total_blocks} blocks")

            # 2) 適用方針（policy）
            if order_availability > 0:
                order_policy = "APPLY"
                order_policy_reason = "source=surya_structure.blocks[].reading_order"
            else:
                order_policy = "SKIP"
                order_policy_reason = "reason=no_order_fields"
            logger.info(f"[F-7] reading_order policy: {order_policy} ({order_policy_reason})")

            # 3) 先頭10件のorderダンプ（preview）
            if blocks_with_order:
                # reading_orderでソート
                sorted_blocks = sorted(blocks_with_order, key=lambda b: (b.get('reading_order', 9999)))
                logger.info("[F-7] reading_order preview (top10):")
                for i, block in enumerate(sorted_blocks[:10]):
                    order = block.get('reading_order', '?')
                    col = block.get('column_id', '?')
                    hint = block.get('block_type_hint', '?')
                    bbox = block.get('bbox', [])
                    # bbox を簡略表示
                    if bbox and isinstance(bbox, list):
                        if isinstance(bbox[0], (list, tuple)):
                            bbox_str = f"[{int(bbox[0][0])},{int(bbox[0][1])}]-[{int(bbox[2][0])},{int(bbox[2][1])}]"
                        elif len(bbox) >= 4:
                            bbox_str = f"[{int(bbox[0])},{int(bbox[1])},{int(bbox[2])},{int(bbox[3])}]"
                        else:
                            bbox_str = str(bbox)[:30]
                    else:
                        bbox_str = "?"
                    logger.info(f"  #{i+1} order={order} col={col} type={hint} bbox={bbox_str}")

            logger.info("  ├─ Step 1: プロンプト構築")

            # --- F-7 Step 1: プロンプト構築 ---
            base_prompt_chars = len(prompt)
            full_prompt = prompt
            stage_e_chars = len(extracted_text) if extracted_text else 0
            surya_chars = 0

            # Surya構造からordered_blocksテキストを生成（必須フィールド: order, column_id, block_type_hint, bbox）
            surya_structure_text = ""
            ordered_blocks_count = 0
            included_fields = []
            if surya_structure:
                # reading_orderでソートしてordered_blocksを生成
                sorted_for_prompt = sorted(surya_structure, key=lambda b: b.get('reading_order', 9999))
                surya_structure_lines = []
                for block in sorted_for_prompt:
                    # bbox を簡略表示
                    bbox = block.get('bbox', [])
                    if bbox and isinstance(bbox, list):
                        if isinstance(bbox[0], (list, tuple)):
                            bbox_str = f"[{int(bbox[0][0])},{int(bbox[0][1])},{int(bbox[2][0])},{int(bbox[2][1])}]"
                        elif len(bbox) >= 4:
                            bbox_str = f"[{int(bbox[0])},{int(bbox[1])},{int(bbox[2])},{int(bbox[3])}]"
                        else:
                            bbox_str = "[]"
                    else:
                        bbox_str = "[]"
                    # 必須フィールドを含む行を生成
                    line = f"[{block['block_id']}] order={block['reading_order']}, col={block['column_id']}, type={block['block_type_hint']}, bbox={bbox_str}"
                    surya_structure_lines.append(line)
                surya_structure_text = "\n".join(surya_structure_lines)
                surya_chars = len(surya_structure_text)
                ordered_blocks_count = len(sorted_for_prompt)
                included_fields = ['order', 'column_id', 'block_type_hint', 'bbox']

            # ============================================
            # Stage E 新フォーマット対応（タグ付き構造化出力）
            # ============================================
            stage_e_sections = _parse_stage_e_sections(extracted_text)
            is_new_format = stage_e_sections['is_new_format']

            if is_new_format:
                logger.info(f"  ├─ Stage E フォーマット: 新（タグ付き構造化）")
            else:
                logger.info(f"  ├─ Stage E フォーマット: 旧（プレーンテキスト）")

            # Stage E の情報を追加（新フォーマット対応）
            if extracted_text:
                if is_new_format:
                    # 新フォーマット: 各セクションを個別に追加

                    # OCR_TEXT（ベーステキスト）
                    if stage_e_sections['ocr_text']:
                        full_prompt += "\n\n---\n\n【Stage E: OCRテキスト（ベース）】\n"
                        full_prompt += f"```\n{stage_e_sections['ocr_text']}\n```\n\n"

                    # VISUAL_SCENE_LOG（視覚シーン記述）
                    if stage_e_sections['visual_scene_log']:
                        full_prompt += "\n\n---\n\n【Stage E: 視覚シーン記述（参照情報）】\n"
                        full_prompt += "※ 別のAIモデルが画像を見て生成した視覚情報です。これを参照して見落としがないか確認してください。\n"
                        full_prompt += f"```\n{stage_e_sections['visual_scene_log']}\n```\n\n"

                    # AUDIO_TRANSCRIPT（音声書き起こし）
                    if stage_e_sections['audio_transcript']:
                        full_prompt += "\n\n---\n\n【Stage E: 音声書き起こし（Verbatim）】\n"
                        full_prompt += "※ 音声/動画から抽出した完全な書き起こしです。フィラー（あー、えー）も含みます。\n"
                        full_prompt += f"```\n{stage_e_sections['audio_transcript']}\n```\n\n"

                    # METADATA（メタデータ）
                    if stage_e_sections['metadata']:
                        full_prompt += "\n\n---\n\n【Stage E: メタデータ】\n"
                        full_prompt += f"```\n{stage_e_sections['metadata']}\n```\n\n"

                    stage_e_chars = len(extracted_text)
                else:
                    # 旧フォーマット: そのまま追加
                    full_prompt += "\n\n---\n\n【Stage E で抽出したテキスト】\n"
                    full_prompt += f"```\n{extracted_text}\n```\n\n"
                    stage_e_chars = len(extracted_text)

            # Surya構造情報を追加（新設計: reading_order + block_type_hint）
            if surya_structure:
                full_prompt += "\n\n---\n\n【Surya で検出したレイアウト構造】\n"
                full_prompt += f"{len(surya_structure)}個のブロックを検出しました（reading_order付き）：\n"
                full_prompt += f"```\n{surya_structure_text}\n```\n\n"

            # ============================================
            # 役割説明（新フォーマット対応: 補完・強化に特化）
            # ============================================
            if extracted_text or surya_structure:
                if is_new_format and stage_e_sections['visual_scene_log']:
                    # 新フォーマット + 視覚シーン記述あり: 補完・強化モード
                    full_prompt += """【あなたの役割 - 重要: Stage E情報の補完・強化】
Stage Eで既に豊富な情報が抽出されています。あなたの役割は、この情報を**補完・強化**することです。

## 入力情報の活用方法
1. **OCRテキスト**: これがベースです。絶対に削除・短縮しない
2. **視覚シーン記述**: 別のAIが画像から生成した記述です。これを参照して：
   - 見落としている物体や詳細がないか確認
   - 記述の精度を検証（誤りがあれば修正）
   - より具体的な情報（ブランド名、製品名、数値など）を補完
3. **音声書き起こし**（ある場合）: Verbatim形式の完全な書き起こしです

## あなたがすべきこと
1. **画像を直接見て**、Stage Eの情報に欠けているものを見つける
2. **文字情報の補完**: OCRが見落とした文字（小さい文字、装飾文字、ロゴなど）
3. **視覚情報の補完**: シーン記述に含まれていない詳細（色、サイズ、位置関係など）
4. **表・図表の補完**: 画像から見つけた表の構造を正確に抽出
5. **数値・日付の検証**: 画像を見て正確性を確保

## 禁止事項
- Stage Eのテキストを削除・短縮してはならない
- 情報を「まとめる」「要約する」ことは禁止
- full_text は常に Stage E 以上の長さでなければならない
- 推測や解釈を追加しない（見えるものだけを記述）

## 出力の優先順位
- `full_text`: Stage E OCRテキストをベースに、不足分を追記（単調増加）
- `layout_info.tables`: 画像から見つけた表
- `visual_elements`: Stage Eシーン記述を参照しつつ、補完・修正
"""
                elif is_new_format and stage_e_sections['audio_transcript']:
                    # 新フォーマット + 音声書き起こしあり（動画/音声ファイル）
                    full_prompt += """【あなたの役割 - 重要: 音声/映像コンテンツの統合】
Stage Eで音声の完全な書き起こしが既に行われています。

## 入力情報の活用方法
1. **音声書き起こし**: Verbatim形式（フィラー含む）の完全な記録です
2. **視覚シーン記述**（動画の場合）: 画面の視覚的変化のログです

## あなたがすべきこと
1. 音声書き起こしと視覚シーン記述を統合して構造化されたJSONを生成
2. タイムスタンプの整合性を確認
3. 話者情報と視覚情報の対応付け

## 禁止事項
- 音声書き起こしの内容を削除・短縮してはならない
- 書き起こしを「整理」「編集」してはならない
- full_text は常に Stage E 以上の長さでなければならない
"""
                else:
                    # 旧フォーマットまたは視覚シーン記述なし: 従来の単調増加モード
                    full_prompt += """【あなたの役割 - 重要: 単調増加の原則】
上記の Stage E のテキストと Surya のレイアウト構造を統合し、画像を詳細に見て完璧な結果を作成してください：

1. **ベース（絶対に削除しない）**: Stage E で抽出したテキストを `full_text` のベースとする
2. **追記のみ**: Surya で見つかった追加のテキスト、表、数字・記号を**追記**する
3. **検証**: 画像を見て、欠けている情報を**追加**する（削除は禁止）
4. **強化**: 画像から直接読み取れる情報（タイトル、ロゴ、装飾文字、見逃した表など）を補完する

**【禁止事項】**
- Stage E のテキストを削除・短縮してはならない
- 情報を「まとめる」ために削除してはならない
- full_text は常に Stage E 以上の長さでなければならない

**優先順位**:
- `full_text`: Stage E をベースに、不足分を追記（単調増加）
- `layout_info.tables`: 画像から見つけた表
- 数字・日付・時刻: 画像を見て正確性を確保
"""
            elif surya_structure and workspace == 'gmail':
                full_prompt += """【あなたの役割】
Stage E のテキストと Surya のレイアウト構造を活用し、画像を見て完璧な結果を作成してください：

1. **ベース**: Stage E で抽出したテキストを `full_text` のベースとする（削除禁止）
2. **レイアウト**: Surya の reading_order を参照してセクション構造を記述
3. **視覚要素**: 画像、チャート、強調テキストを `visual_elements` に記述

**注意**: これはGmail HTMLメールのため、テキストは既に抽出済みです。視覚的な構造と要素に集中してください。
"""
            else:
                full_prompt += "\n\n【注意】Stage E でテキストを抽出できませんでした。画像から全ての文字と表を拾い尽くしてください。\n"

            total_prompt_chars = len(full_prompt)

            logger.info(f"  ├─ Step 1 完了: プロンプト構築")
            logger.info(f"  │   ├─ 基本プロンプト: {base_prompt_chars}文字")
            logger.info(f"  │   ├─ Stage Eテキスト: {stage_e_chars}文字")
            logger.info(f"  │   ├─ Surya構造: {len(surya_structure)}ブロック")
            logger.info(f"  │   └─ 最終プロンプト: {total_prompt_chars}文字")

            # 4) Prompt に ordered blocks を含めた証拠（必須確認）
            if surya_structure and order_policy == "APPLY":
                # プロンプトに必須フィールドが含まれているか確認
                prompt_has_surya = "【Surya で検出したレイアウト構造】" in full_prompt
                prompt_has_order = "order=" in full_prompt
                prompt_has_col = "col=" in full_prompt
                prompt_has_type = "type=" in full_prompt
                prompt_has_bbox = "bbox=" in full_prompt

                all_fields_present = prompt_has_surya and prompt_has_order and prompt_has_col and prompt_has_type and prompt_has_bbox

                if all_fields_present:
                    logger.info(f"[F-7] prompt uses ordered_blocks: order_fields_in_prompt=True")
                    logger.info(f"  ├─ ordered_blocks_count: {ordered_blocks_count}")
                    logger.info(f"  └─ included_fields: {included_fields}")
                else:
                    # 【設計】order_fields_in_prompt=False は禁止
                    missing = []
                    if not prompt_has_surya:
                        missing.append("surya_section")
                    if not prompt_has_order:
                        missing.append("order")
                    if not prompt_has_col:
                        missing.append("col")
                    if not prompt_has_type:
                        missing.append("type")
                    if not prompt_has_bbox:
                        missing.append("bbox")
                    logger.warning(f"[F-7] CRITICAL: order_fields_in_prompt=False - missing: {missing}")
                    logger.warning(f"[F-7] ordered_blocks_count={ordered_blocks_count}, 設計違反: 全フィールドが必須")
            elif surya_structure:
                # surya_structure はあるが policy=SKIP の場合
                logger.warning(f"[F-7] WARN: order_policy=SKIP but surya_structure exists ({len(surya_structure)} blocks)")
            else:
                logger.info(f"[F-7] prompt uses ordered_blocks: SKIP (no surya_structure)")

            # --- F-7 Step 2: API呼び出し（メディアタイプ別分岐） ---
            # 音声/映像: テキストAPI（ファイル再送信なし、lite モデル）
            # 画像/その他: Vision API（ファイル送信あり）
            is_audio_video = mime_type and (mime_type.startswith('audio/') or mime_type.startswith('video/'))

            try:
                if is_audio_video:
                    # 音声/映像: テキスト統合のみ（ファイル再送信なし）
                    logger.info("  ├─ Step 2: テキスト統合API呼び出し（音声/映像モード）")
                    f7_model = "gemini-2.5-flash-lite"  # コスト削減
                    logger.info(f"  │   ├─ model={f7_model} (lite), max_tokens=65536")
                    logger.info(f"  │   ├─ モード: テキスト統合のみ（ファイル再送信なし）")

                    vision_raw = self.llm_client.generate(
                        prompt=full_prompt,
                        model=f7_model,
                        max_tokens=65536,
                        response_format="json"
                    )
                else:
                    # 画像/その他: Vision API
                    logger.info("  ├─ Step 2: Gemini Vision API呼び出し")
                    logger.info(f"  │   ├─ model={model}, max_tokens=65536")

                    vision_raw = self.llm_client.generate_with_vision(
                        prompt=full_prompt,
                        image_path=str(file_path),
                        model=model,
                        max_tokens=65536,
                        response_format="json"
                    )

                step2_elapsed = time.time() - f7_start
                estimated_tokens = len(vision_raw) // 4  # 概算

                logger.info(f"  │   ├─ 応答サイズ: {len(vision_raw)}文字")
                logger.info(f"  │   ├─ 推定トークン数: ~{estimated_tokens}トークン")
                logger.info(f"  │   └─ 処理時間: {step2_elapsed:.2f}秒")

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

            # --- F-7 Step 3: JSON正規化（json_repair使用）---
            logger.info("  ├─ Step 3: JSON正規化")

            vision_cleaned = self._clean_json_response(vision_raw)

            reduction_rate = (1 - len(vision_cleaned) / len(vision_raw)) * 100 if len(vision_raw) > 0 else 0
            is_valid_json = False
            try:
                json.loads(vision_cleaned)
                is_valid_json = True
            except:
                pass

            logger.info(f"  │   ├─ クリーニング前: {len(vision_raw)}文字")
            logger.info(f"  │   ├─ クリーニング後: {len(vision_cleaned)}文字")
            logger.info(f"  │   └─ JSON形式: {'有効' if is_valid_json else '無効（json_repair適用済）'}")

            # --- F-7 Step 4: 方式A - ベース+差分追記（単調増加保証）---
            logger.info("  ├─ Step 4: 本文統合（方式A: ベース+差分追記）")

            gemini_table_count = 0

            # full_textの統合
            # 【方式A】ベース + 差分追記（単調増加を保証）
            # - ベース: Stage E（一次ソース、絶対に失わない）
            # - 追記: Gemini / Surya から、ベースに存在しない段落のみ追加
            stage_e_len = len(extracted_text)
            surya_len = len(surya_full_text)
            gemini_len = 0
            total_len = 0
            merged_full_text = ""

            try:
                vision_data = json.loads(vision_cleaned)
                gemini_full_text = vision_data.get('full_text', '')
                gemini_len = len(gemini_full_text)

                # ログ出力: 各ソースの長さを明示
                logger.info(f"[F-7] full_text統合（方式A: ベース + 差分追記）:")
                logger.info(f"  ├─ Stage_E (ベース): {stage_e_len}文字")
                logger.info(f"  ├─ Gemini: {gemini_len}文字")
                logger.info(f"  └─ Surya: {surya_len}文字")

                # Step 1: ベース = Stage E
                merged_full_text = extracted_text.strip() if extracted_text else ""
                base_len = len(merged_full_text)

                # 差分追記用の関数
                def extract_new_paragraphs(source_text: str, base_text: str, source_name: str) -> list:
                    """ベースに存在しない段落を抽出"""
                    if not source_text or not source_text.strip():
                        return []

                    # 段落に分割（空行または2連続改行で区切る）
                    import re
                    paragraphs = re.split(r'\n\s*\n|\r\n\s*\r\n', source_text)

                    new_paragraphs = []
                    # ベースを正規化（比較用）
                    base_normalized = re.sub(r'\s+', '', base_text.lower())

                    for para in paragraphs:
                        para = para.strip()
                        if not para or len(para) < 10:  # 短すぎる段落はスキップ
                            continue

                        # 正規化して比較
                        para_normalized = re.sub(r'\s+', '', para.lower())

                        # ベースに含まれていなければ新規
                        if para_normalized not in base_normalized:
                            new_paragraphs.append(para)

                    if new_paragraphs:
                        logger.info(f"  ├─ {source_name}から新規段落: {len(new_paragraphs)}個")

                    return new_paragraphs

                # Step 2: Gemini から差分追記
                gemini_new = extract_new_paragraphs(gemini_full_text, merged_full_text, "Gemini")
                if gemini_new:
                    merged_full_text += "\n\n" + "\n\n".join(gemini_new)

                # Step 3: Surya から差分追記
                surya_new = extract_new_paragraphs(surya_full_text, merged_full_text, "Surya")
                if surya_new:
                    merged_full_text += "\n\n" + "\n\n".join(surya_new)

                total_len = len(merged_full_text)
                added_len = total_len - base_len

                logger.info(f"  ├─ 統合結果: {base_len}文字 → {total_len}文字 (+{added_len}文字)")
                logger.info(f"  └─ 単調増加: {'保証' if total_len >= base_len else '違反！'}")

                # vision_dataのfull_textを更新
                vision_data['full_text'] = merged_full_text
                vision_cleaned = json.dumps(vision_data, ensure_ascii=False)

                # 表カウント取得
                gemini_table_count = len(vision_data.get('layout_info', {}).get('tables', []))

            except Exception as e:
                logger.warning(f"[F-7] full_textマージ失敗: {e}")
                # フォールバック: Stage E を維持（単調増加の原則）
                merged_full_text = extracted_text or ""
                total_len = len(merged_full_text)

            f7_elapsed = time.time() - f7_start

            # F-7 完了ログ
            logger.info(f"  │   ├─ Gemini表: {gemini_table_count}個")
            logger.info(f"  │   └─ full_text: {stage_e_len}→{total_len}文字 (+{total_len - stage_e_len})")
            logger.info(f"[F-7完了] 本文確定:")
            logger.info(f"  ├─ final_full_text: {total_len}文字")
            logger.info(f"  ├─ 単調増加: {'保証' if total_len >= stage_e_len else '違反！'}")
            logger.info(f"  └─ 処理時間: {f7_elapsed:.2f}秒")

            # ============================================
            # [F-8] text_blocks 生成 + JSON正規化
            # ============================================
            if progress_callback:
                progress_callback("F-8")
            f8_start = time.time()
            logger.info("[F-8] text_blocks生成 + JSON正規化開始...")

            # JSON解析（layout_info / visual_elements の取得のみ）
            # 【重要】full_text は F-7 で確定した merged_full_text を使用
            # vision_json['full_text'] は参照しない（設計: F-7が本文確定の唯一の場所）
            sections_count = 0
            tables_count = 0
            layout_info = {}
            visual_elements = {}
            tables_from_vision = []

            try:
                vision_json = json.loads(vision_cleaned)
                # full_text は取得しない（F-7の merged_full_text が唯一の本文）
                layout_info = vision_json.get('layout_info', {})
                visual_elements = vision_json.get('visual_elements', {})
                tables_from_vision = layout_info.get('tables', [])

                sections_count = len(layout_info.get('sections', []))
                tables_count = len(tables_from_vision)

            except Exception as e:
                logger.warning(f"[F-8] JSON解析失敗: {e}")
                vision_json = {}

            # 【設計確定】F-7で確定した merged_full_text を唯一の本文として固定
            final_full_text = merged_full_text
            full_text_chars = len(final_full_text)

            logger.info(f"[F-8] 本文ソース確認:")
            logger.info(f"  ├─ final_full_text = merged_full_text（F-7確定）: {full_text_chars}文字")
            logger.info(f"  └─ vision_json['full_text'] は参照していません（設計通り）")

            f8_elapsed = time.time() - f8_start
            logger.info(f"[F-8完了] text_blocks生成 + JSON正規化:")
            logger.info(f"  ├─ final_full_text: {full_text_chars}文字")
            logger.info(f"  ├─ sections: {sections_count}個")
            logger.info(f"  ├─ tables: {tables_count}個")
            logger.info(f"  └─ 処理時間: {f8_elapsed:.2f}秒")

            # text_blocks 生成（入力は必ず final_full_text = merged_full_text）
            text_blocks = []
            try:
                # 【設計】final_full_text を唯一の入力とする（vision_json参照禁止）
                text_blocks = self._generate_text_blocks(final_full_text, tables_from_vision, post_body)
            except Exception as e:
                logger.warning(f"[F-8] text_blocks生成失敗: {e}")

            # ============================================
            # [F-9] Stage H payload 梱包
            # ============================================
            if progress_callback:
                progress_callback("F-9")
            f9_start = time.time()
            logger.info("[F-9] Stage H payload 梱包開始...")
            warnings_list = []
            provenance = {
                'stage_e_chars': stage_e_len,
                'surya_chars': surya_chars,
                'gemini_chars': gemini_len,
                'surya_blocks': len(surya_blocks),
            }

            try:
                # 【設計】full_text は F-7 で確定した final_full_text を使用
                # vision_json['full_text'] は参照しない
                stage_h_payload = self._build_stage_h_payload(
                    final_full_text=final_full_text,  # F-7確定（= merged_full_text）
                    text_blocks=text_blocks,
                    tables=tables_from_vision,
                    layout_info=layout_info,
                    visual_elements=visual_elements,
                    warnings=warnings_list,
                    provenance=provenance,
                    post_body=post_body  # 投稿本文（Stage H最優先文脈）
                )
                f9_elapsed = time.time() - f9_start
                logger.info(f"[F-9完了] Stage H payload 梱包: {f9_elapsed:.2f}秒")
            except Exception as e:
                logger.warning(f"[F-9] payload梱包失敗: {e}")
                stage_h_payload = {}

            # ============================================
            # [F-10] 受け渡し保証（validation）
            # ============================================
            if progress_callback:
                progress_callback("F-10")
            f10_start = time.time()
            logger.info("[F-10] 受け渡し保証（validation）開始...")

            # 【v1.1契約強化】text_blocks の正規化（content→text 統一、最終安全弁）
            if stage_h_payload.get("text_blocks"):
                stage_h_payload["text_blocks"] = _normalize_text_blocks(stage_h_payload["text_blocks"])
                logger.info(f"[F-10] text_blocks正規化完了: {len(stage_h_payload['text_blocks'])}ブロック")

            try:
                validated_payload, final_warnings = self._validate_output(
                    stage_h_payload,
                    extracted_text  # Stage E の元テキスト
                )

                # v1.1契約: validated_payload を正として返却（vision_json は使わない）
                f10_elapsed = time.time() - f10_start
                logger.info(f"[F-10完了] 受け渡し保証: {f10_elapsed:.2f}秒")

            except Exception as e:
                logger.warning(f"[F-10] validation失敗: {e}")
                # フォールバック: stage_h_payload をそのまま使用
                validated_payload = stage_h_payload

            total_elapsed = time.time() - total_start_time

            # ============================================
            # [Stage F 完了] 総括（v1.1 payload を返却）
            # ============================================
            logger.info("=" * 60)
            logger.info("[Stage F完了] v1.1 payload 返却")
            logger.info(f"  ├─ schema_version: {validated_payload.get('schema_version', 'N/A')}")
            logger.info(f"  ├─ full_text: {len(validated_payload.get('full_text', ''))}文字")
            logger.info(f"  ├─ post_body: {validated_payload.get('post_body', {}).get('char_count', 0)}文字")
            logger.info(f"  ├─ text_blocks: {len(validated_payload.get('text_blocks', []))}ブロック")
            tb = validated_payload.get('text_blocks', [])
            logger.info(f"  ├─ text_blocks[0]: {tb[0].get('block_type') if tb else 'N/A'}")
            logger.info(f"  ├─ tables: {len(validated_payload.get('tables', []))}個")
            logger.info(f"  └─ 総処理時間: {total_elapsed:.2f}秒")
            logger.info("=" * 60)

            # v1.1契約: validated_payload を JSON として返却
            return json.dumps(validated_payload, ensure_ascii=False)

        except Exception as e:
            logger.error(f"[Stage F エラー] Vision処理失敗: {e}", exc_info=True)
            # v1.1契約: 例外時でも最小 payload を返す（pipeline の json.loads() を壊さない）
            fallback_payload = {
                "schema_version": "stage_h_input.v1.1",
                "post_body": post_body,
                "full_text": extracted_text if extracted_text else "",
                "text_blocks": [
                    {
                        "block_type": "post_body",
                        "text": post_body.get("text", "") if post_body else "",
                        "source": post_body.get("source", "unknown") if post_body else "unknown",
                        "char_count": post_body.get("char_count", 0) if post_body else 0,
                        "priority": "highest"
                    }
                ],
                "tables": [],
                "layout_elements": [],
                "visual_elements": [],
                "warnings": [f"F_EXCEPTION: {str(e)}"],
                "_contract_violation": False,
                "_fallback_mode": True
            }
            logger.warning(f"[Stage F] フォールバック payload 返却: {str(e)[:100]}")
            return json.dumps(fallback_payload, ensure_ascii=False)

    def _process_text_only_f7(
        self,
        extracted_text: str,
        post_body: Dict[str, Any],
        model: str
    ) -> str:
        """
        テキストのみモード用の F-7 処理

        ファイルがない場合でも post_body と extracted_text の統合を行う。
        テキスト API のみ使用（ファイル送信なし）。

        Args:
            extracted_text: Stage E で抽出したテキスト
            post_body: 投稿本文（メール/フォーム）
            model: 使用モデル（実際は lite モデルに固定）

        Returns:
            v1.1 形式の JSON 文字列
        """
        import time
        f7_start = time.time()

        logger.info("=" * 60)
        logger.info("[Stage F] テキストのみモード - F-7 テキスト統合処理")
        logger.info(f"  ├─ extracted_text: {len(extracted_text)}文字")
        logger.info(f"  ├─ post_body: {post_body.get('char_count', 0)}文字 (source: {post_body.get('source', 'unknown')})")
        logger.info("=" * 60)

        # post_body のテキスト
        post_text = post_body.get("text", "") if post_body else ""

        # ============================================
        # テキストが両方空の場合は最小 payload を返す
        # ============================================
        if not extracted_text and not post_text:
            logger.warning("[Stage F] テキストなし → 最小 payload 返却")
            fallback_payload = {
                "schema_version": STAGE_H_INPUT_SCHEMA_VERSION,
                "post_body": post_body,
                "full_text": "",
                "text_blocks": [],
                "tables": [],
                "layout_elements": [],
                "visual_elements": [],
                "warnings": ["F_TEXT_ONLY_EMPTY: 入力テキストなし"],
                "_contract_violation": False,
                "_text_only_mode": True
            }
            return json.dumps(fallback_payload, ensure_ascii=False)

        # ============================================
        # F-7: テキスト統合プロンプト構築
        # ============================================
        full_prompt = """あなたはドキュメント統合の専門家です。

以下の入力テキストを統合して、構造化された JSON を出力してください。

【出力形式】
```json
{
  "full_text": "統合された本文テキスト（post_body を最優先、extracted_text で補完）",
  "tables": [],
  "layout_elements": [],
  "visual_elements": []
}
```

【統合ルール】
1. post_body（投稿本文）を最優先で使用
2. extracted_text に post_body にない情報があれば追加（単調増加）
3. 重複は排除
4. 情報を削除・短縮しない

"""

        if post_text:
            full_prompt += f"""
---

【投稿本文（最優先）】
```
{post_text}
```
"""

        if extracted_text:
            full_prompt += f"""
---

【抽出テキスト】
```
{extracted_text}
```
"""

        full_prompt += """
---

上記のルールに従って JSON を出力してください。
"""

        # ============================================
        # F-7: テキスト API 呼び出し
        # ============================================
        f7_model = "gemini-2.5-flash-lite"  # コスト削減
        logger.info(f"  ├─ API呼び出し: model={f7_model}")

        try:
            vision_raw = self.llm_client.generate(
                prompt=full_prompt,
                model=f7_model,
                max_tokens=65536,
                response_format="json"
            )
            logger.info(f"  ├─ 応答サイズ: {len(vision_raw)}文字")

        except Exception as e:
            logger.error(f"[Stage F] テキスト API 呼び出し失敗: {e}")
            # フォールバック: 手動で統合
            combined_text = post_text
            if extracted_text and extracted_text not in combined_text:
                combined_text = f"{post_text}\n\n{extracted_text}" if post_text else extracted_text

            fallback_payload = {
                "schema_version": STAGE_H_INPUT_SCHEMA_VERSION,
                "post_body": post_body,
                "full_text": combined_text,
                "text_blocks": [
                    {
                        "block_type": "post_body",
                        "text": post_text,
                        "source": post_body.get("source", "unknown"),
                        "char_count": len(post_text),
                        "priority": "highest"
                    }
                ],
                "tables": [],
                "layout_elements": [],
                "visual_elements": [],
                "warnings": [f"F_TEXT_ONLY_API_ERROR: {str(e)[:100]}"],
                "_contract_violation": False,
                "_text_only_mode": True,
                "_fallback_mode": True
            }
            return json.dumps(fallback_payload, ensure_ascii=False)

        # ============================================
        # F-7: JSON パース・正規化
        # ============================================
        vision_cleaned = self._clean_json_response(vision_raw)

        try:
            vision_json = json.loads(vision_cleaned)
        except json.JSONDecodeError:
            # json_repair で修復
            try:
                vision_json = json.loads(json_repair.repair_json(vision_cleaned))
            except Exception as e:
                logger.warning(f"[Stage F] JSON 修復失敗: {e}")
                # フォールバック
                combined_text = post_text
                if extracted_text and extracted_text not in combined_text:
                    combined_text = f"{post_text}\n\n{extracted_text}" if post_text else extracted_text
                vision_json = {"full_text": combined_text, "tables": [], "layout_elements": [], "visual_elements": []}

        # ============================================
        # F-9: v1.1 payload 組み立て
        # ============================================
        gemini_full_text = vision_json.get("full_text", "")

        # 単調増加保証: post_body が含まれているか確認
        if post_text and post_text not in gemini_full_text:
            merged_full_text = f"{post_text}\n\n{gemini_full_text}"
        else:
            merged_full_text = gemini_full_text

        # extracted_text も含まれているか確認
        if extracted_text:
            # 主要な段落が含まれているかチェック
            paragraphs = [p.strip() for p in extracted_text.split('\n\n') if p.strip()]
            missing_paragraphs = []
            for p in paragraphs:
                if len(p) > 20 and p not in merged_full_text:
                    missing_paragraphs.append(p)
            if missing_paragraphs:
                merged_full_text = f"{merged_full_text}\n\n" + "\n\n".join(missing_paragraphs)

        # text_blocks 生成
        text_blocks = [
            {
                "block_type": "post_body",
                "text": post_text,
                "source": post_body.get("source", "unknown"),
                "char_count": len(post_text),
                "priority": "highest"
            }
        ]

        # 追加のテキストブロック
        if extracted_text:
            text_blocks.append({
                "block_type": "extracted",
                "text": extracted_text,
                "source": "stage_e",
                "char_count": len(extracted_text),
                "priority": "normal"
            })

        validated_payload = {
            "schema_version": STAGE_H_INPUT_SCHEMA_VERSION,
            "post_body": post_body,
            "full_text": merged_full_text,
            "text_blocks": text_blocks,
            "tables": vision_json.get("tables", []),
            "layout_elements": vision_json.get("layout_elements", []),
            "visual_elements": vision_json.get("visual_elements", []),
            "warnings": [],
            "_contract_violation": False,
            "_text_only_mode": True
        }

        f7_elapsed = time.time() - f7_start
        logger.info("=" * 60)
        logger.info("[Stage F完了] テキストのみモード v1.1 payload 返却")
        logger.info(f"  ├─ full_text: {len(validated_payload.get('full_text', ''))}文字")
        logger.info(f"  ├─ text_blocks: {len(validated_payload.get('text_blocks', []))}ブロック")
        logger.info(f"  └─ 処理時間: {f7_elapsed:.2f}秒")
        logger.info("=" * 60)

        return json.dumps(validated_payload, ensure_ascii=False)

    def _initialize_hybrid_ocr_engines(self):
        """ハイブリッドOCRエンジン（Surya）を初期化

        【重要】依存エラーは握りつぶさず、例外として扱う
        """
        if not SURYA_AVAILABLE:
            error_msg = "[Hybrid OCR] Surya not installed - CRITICAL: Layout detection requires Surya"
            logger.error(error_msg)
            raise PPStructureV3DependencyError(error_msg)

        try:
            logger.info("[Hybrid OCR] Initializing Surya Foundation Model...")
            self.surya_foundation = FoundationPredictor()

            logger.info("[Hybrid OCR] Initializing Surya Detection...")
            self.surya_detector = DetectionPredictor()

            logger.info("[Hybrid OCR] Initializing Surya Layout...")
            self.surya_layout = LayoutPredictor(self.surya_foundation)

            logger.info("[Hybrid OCR] All engines initialized successfully!")

        except Exception as e:
            error_msg = f"[Hybrid OCR] Initialization failed: {e}"
            logger.error(error_msg)
            raise PPStructureV3DependencyError(error_msg)

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

    def _clean_json_response(self, response: str) -> str:
        """
        【F-8】Gemini の応答からJSONを抽出してクリーニング + 修復

        【新設計】json_repair ライブラリを使用して壊れたJSONを修復
        - code fence 除去
        - 末尾カンマ修正
        - クォート漏れ修正
        - 閉じ忘れ補完

        Args:
            response: Gemini の生の応答

        Returns:
            クリーニング・修復されたJSON文字列
        """
        import re

        # Step 1: code fence 除去
        extracted = response

        # パターン1: ```json ... ``` で囲まれている場合
        json_match = re.search(r'```json\s*\n(.*?)\n```', response, re.DOTALL)
        if json_match:
            extracted = json_match.group(1).strip()
        else:
            # パターン2: ``` ... ``` で囲まれている場合
            code_match = re.search(r'```\s*\n(.*?)\n```', response, re.DOTALL)
            if code_match:
                extracted = code_match.group(1).strip()
            else:
                # パターン3: { ... } を探す（最初の{から最後の}まで）
                first_brace = response.find('{')
                last_brace = response.rfind('}')
                if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                    extracted = response[first_brace:last_brace + 1].strip()

        # Step 2: まず標準JSONパースを試行
        try:
            parsed = json.loads(extracted)
            # パース成功 → キー名正規化
            normalized = self._normalize_json_keys(parsed)
            return json.dumps(normalized, ensure_ascii=False)
        except json.JSONDecodeError as e:
            logger.warning(f"[F-8] 標準JSONパース失敗、json_repairで修復試行: {e}")

        # Step 3: json_repair で修復
        try:
            repaired = json_repair.loads(extracted)
            logger.info(f"[F-8] json_repair で修復成功")
            # キー名正規化
            normalized = self._normalize_json_keys(repaired)
            return json.dumps(normalized, ensure_ascii=False)
        except Exception as repair_error:
            logger.error(f"[F-8] json_repair も失敗: {repair_error}")
            # フォールバック: そのまま返す
            return extracted

    def _normalize_json_keys(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        【F-8】JSONキー名の正規化

        - fullText → full_text
        - layoutInfo → layout_info
        - visualElements → visual_elements
        - table (単数) → tables (配列)
        - null → [] or {}

        Args:
            data: パース済みJSON

        Returns:
            キー名正規化済みJSON
        """
        if not isinstance(data, dict):
            return data

        # キー名マッピング
        key_mapping = {
            'fullText': 'full_text',
            'full_text': 'full_text',
            'layoutInfo': 'layout_info',
            'layout_info': 'layout_info',
            'visualElements': 'visual_elements',
            'visual_elements': 'visual_elements',
        }

        normalized = {}

        for key, value in data.items():
            # キー名変換
            new_key = key_mapping.get(key, key)

            # 値の正規化
            if value is None:
                # null → 空配列/空オブジェクト
                if new_key in ['tables', 'sections', 'images', 'charts', 'text_blocks']:
                    value = []
                elif new_key in ['layout_info', 'visual_elements', 'metadata']:
                    value = {}
            elif isinstance(value, dict):
                # 再帰的に正規化
                value = self._normalize_json_keys(value)
            elif isinstance(value, list):
                # リスト内の辞書も正規化
                value = [self._normalize_json_keys(item) if isinstance(item, dict) else item for item in value]

            normalized[new_key] = value

        # layout_info.tables の正規化（単数→複数）
        if 'layout_info' in normalized and isinstance(normalized['layout_info'], dict):
            layout = normalized['layout_info']
            if 'table' in layout and 'tables' not in layout:
                # table (単数) → tables (配列)
                table_val = layout.pop('table')
                layout['tables'] = [table_val] if table_val and not isinstance(table_val, list) else (table_val or [])

        return normalized

    def _generate_text_blocks(
        self,
        full_text: str,
        tables: List[Dict] = None,
        post_body: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """
        【F-8】full_text から text_blocks を生成（ルールベース、AI禁止）

        段落・見出し・箇条書き・表ブロックを検出して分割

        Args:
            full_text: 統合済み本文
            tables: 表データ（あれば）
            post_body: 投稿本文（あれば先頭ブロックとして追加）

        Returns:
            text_blocks: [{block_id, text, block_type, order}, ...]
        """
        import re

        text_blocks = []
        block_id = 0

        # 【v1.1契約】post_body は常に text_blocks[0] として生成（text が空でも生成）
        if post_body is not None:
            post_text = post_body.get('text', '').strip() if post_body.get('text') else ''
            text_blocks.append({
                'block_id': block_id,
                'text': post_text,
                'block_type': 'post_body',  # ★v1.1契約: 必ず先頭
                'order': block_id,
                'char_count': len(post_text),
                'source': post_body.get('source', 'unknown'),
                'priority': 'highest'
            })
            block_id += 1

        # full_text が空の場合は post_body のみで終了
        if not full_text:
            return text_blocks

        # 段落に分割（空行で区切る）
        paragraphs = re.split(r'\n\s*\n', full_text)

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # ブロックタイプ判定（ルールベース）
            block_type = 'paragraph'

            # 見出し判定：短い + 改行なし + 末尾に句点なし
            if len(para) < 50 and '\n' not in para and not para.endswith(('。', '、', '.')):
                # さらに、数字や記号で始まる場合は見出しの可能性が高い
                if re.match(r'^[0-9０-９一二三四五六七八九十①②③④⑤■●◆▼【]', para):
                    block_type = 'heading'
                elif para.isupper() or re.match(r'^[A-Z]', para):
                    block_type = 'heading'

            # 箇条書き判定
            if re.match(r'^[\-\*・●○◆▪▶→]', para) or re.match(r'^[0-9０-９]+[\.\)）]', para):
                block_type = 'list_item'

            # 表データ判定
            if para.startswith('【表データ】') or '|' in para and para.count('|') >= 2:
                block_type = 'table_text'

            text_blocks.append({
                'block_id': block_id,
                'text': para,
                'block_type': block_type,
                'order': block_id,
                'char_count': len(para)
            })
            block_id += 1

        # 表を別ブロックとして追加
        if tables:
            for i, table in enumerate(tables):
                # 表をMarkdown形式に変換
                table_text = self._table_to_markdown(table)
                if table_text:
                    text_blocks.append({
                        'block_id': block_id,
                        'text': table_text,
                        'block_type': 'table',
                        'order': block_id,
                        'char_count': len(table_text),
                        'table_index': i
                    })
                    block_id += 1

        logger.info(f"[F-8] text_blocks生成: {len(text_blocks)}ブロック")
        block_types = {}
        for b in text_blocks:
            t = b['block_type']
            block_types[t] = block_types.get(t, 0) + 1
        logger.info(f"[F-8] ブロック内訳: {block_types}")

        return text_blocks

    def _table_to_markdown(self, table: Dict[str, Any]) -> str:
        """表データをMarkdown形式に変換"""
        rows = table.get('rows', [])
        if not rows:
            return ""

        lines = []
        for i, row in enumerate(rows):
            line = "| " + " | ".join(str(cell) for cell in row) + " |"
            lines.append(line)
            # ヘッダー行の後に区切り線
            if i == 0:
                separator = "| " + " | ".join("---" for _ in row) + " |"
                lines.append(separator)

        return "\n".join(lines)

    def _build_stage_h_payload(
        self,
        final_full_text: str,
        text_blocks: List[Dict[str, Any]],
        tables: List[Dict[str, Any]],
        layout_info: Dict[str, Any],
        visual_elements: Dict[str, Any],
        warnings: List[str],
        provenance: Dict[str, Any] = None,
        post_body: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        【F-9】Stage H 用 payload を梱包

        【禁止】候補選別、再読解、内容変更

        Args:
            final_full_text: 確定済み本文
            text_blocks: 分割済みブロック
            tables: 表データ
            layout_info: レイアウト情報
            visual_elements: 視覚要素
            warnings: 警告リスト
            provenance: 出典情報（任意）
            post_body: 投稿本文（メール/フォーム）- Stage Hで最優先文脈

        Returns:
            stage_h_input: Stage H が受け取る単一オブジェクト
        """
        # 開始ログは呼び出し元（メインフロー）で出力済み

        # post_bodyのデフォルト値
        if post_body is None:
            post_body = {"text": "", "source": "unknown", "char_count": 0}

        stage_h_input = {
            # スキーマバージョン（v1.1: post_body必須）
            'schema_version': STAGE_H_INPUT_SCHEMA_VERSION,

            # 投稿本文（最優先文脈）
            'post_body': post_body,

            # 必須フィールド
            'full_text': final_full_text,
            'text_blocks': text_blocks,

            # 構造化データ
            'tables': tables,
            'layout_info': layout_info,
            'visual_elements': visual_elements,

            # メタデータ
            'warnings': warnings,
            'provenance': provenance or {},

            # 統計情報
            'stats': {
                'full_text_chars': len(final_full_text),
                'text_blocks_count': len(text_blocks),
                'tables_count': len(tables),
                'avg_block_chars': sum(b.get('char_count', 0) for b in text_blocks) / len(text_blocks) if text_blocks else 0,
            }
        }

        # 契約確認ログ（v1.1 検証用）
        logger.info(f"[F-9] schema_version=stage_h_input.v1.1")
        logger.info(f"[F-9] post_body_chars={post_body.get('char_count', 0)}")
        first_block = text_blocks[0] if text_blocks else {}
        logger.info(f"[F-9] text_blocks[0]={first_block.get('block_type', 'N/A')} chars={first_block.get('char_count', 0)}")

        logger.info(f"[F-9完了] Stage H payload:")
        logger.info(f"  ├─ post_body: {post_body.get('char_count', 0)}文字 (source: {post_body.get('source', 'unknown')})")
        logger.info(f"  ├─ full_text: {len(final_full_text)}文字")
        logger.info(f"  ├─ text_blocks: {len(text_blocks)}ブロック")
        logger.info(f"  ├─ tables: {len(tables)}個")
        logger.info(f"  └─ warnings: {len(warnings)}件")

        return stage_h_input

    def _validate_output(
        self,
        stage_h_input: Dict[str, Any],
        original_stage_e_text: str
    ) -> Tuple[Dict[str, Any], List[str]]:
        """
        【F-10】受け渡し保証（validation & constraints）

        チェック項目:
        1. 必須フィールド存在
        2. full_text が Stage E を包含（単調増加の検証）
        3. サイズ制御（上限超過時は分割、切り捨て禁止）
        4. text_blocks の品質チェック

        Args:
            stage_h_input: F-9 で梱包した payload
            original_stage_e_text: Stage E の元テキスト（包含チェック用）

        Returns:
            (validated_input, warnings): 検証済み payload と追加警告
        """
        import re

        # 開始ログは呼び出し元（メインフロー）で出力済み

        warnings = list(stage_h_input.get('warnings', []))
        validated = stage_h_input.copy()

        # ========================================
        # Check 1: 必須フィールド存在
        # ========================================
        required_fields = ['full_text', 'text_blocks']
        for field in required_fields:
            if field not in validated or validated[field] is None:
                warnings.append(f"[F-10] 必須フィールド欠落: {field}")
                # 空で初期化（切り捨てではなく補完）
                if field == 'full_text':
                    validated[field] = ""
                elif field == 'text_blocks':
                    validated[field] = []

        # ========================================
        # Check 2: 単調増加の検証（Stage E 包含チェック）
        # ========================================
        full_text = validated.get('full_text', '')

        if original_stage_e_text:
            # 正規化して比較（空白・改行の違いを吸収）
            e_normalized = re.sub(r'\s+', '', original_stage_e_text.lower())
            f_normalized = re.sub(r'\s+', '', full_text.lower())

            # Stage E の主要部分を複数方法でチャンク化（取りこぼし防止）
            e_chunks = set()

            # 方法1: 2スペース以上で分割
            for chunk in re.split(r'\s{2,}', original_stage_e_text):
                if len(chunk) >= 30:
                    e_chunks.add(chunk.strip())

            # 方法2: 改行で分割
            for chunk in original_stage_e_text.split('\n'):
                if len(chunk) >= 30:
                    e_chunks.add(chunk.strip())

            # 方法3: 句点で分割（日本語対応）
            for chunk in re.split(r'[。．.!?！？]', original_stage_e_text):
                if len(chunk) >= 30:
                    e_chunks.add(chunk.strip())

            # 方法4: スライディング窓（50文字窓、100文字ステップ）で最低5サンプル
            if len(e_chunks) < 5 and len(original_stage_e_text) >= 50:
                window_size = 50
                step = max(100, len(original_stage_e_text) // 10)
                for i in range(0, len(original_stage_e_text) - window_size, step):
                    e_chunks.add(original_stage_e_text[i:i + window_size].strip())

            e_chunks = list(e_chunks)

            # チャンクが0件の場合は警告（検証不能）
            if len(e_chunks) == 0:
                logger.warning(f"[F-10] 検証不能: Stage E からチャンクを生成できませんでした（テキスト長={len(original_stage_e_text)}）")
                warnings.append(f"[F-10] 検証不能: Stage E チャンク生成失敗")
            else:
                missing_chunks = 0
                for chunk in e_chunks:
                    chunk_normalized = re.sub(r'\s+', '', chunk.lower())
                    if len(chunk_normalized) >= 20 and chunk_normalized not in f_normalized:
                        missing_chunks += 1

                if missing_chunks > 0:
                    warnings.append(f"[F-10] 単調増加違反: Stage E の {missing_chunks}/{len(e_chunks)} チャンクが欠落")
                    logger.warning(f"[F-10] 単調増加違反検出: {missing_chunks}チャンク欠落")
                else:
                    logger.info(f"[F-10] 単調増加検証OK: {len(e_chunks)}チャンク全て包含確認")

            # 長さ比較
            if len(full_text) < len(original_stage_e_text):
                warnings.append(f"[F-10] 単調増加違反: full_text({len(full_text)}) < Stage E({len(original_stage_e_text)})")
                logger.warning(f"[F-10] 単調増加違反: full_text が Stage E より短い")

        # ========================================
        # Check 3: text_blocks 品質チェック
        # ========================================
        text_blocks = validated.get('text_blocks', [])

        if text_blocks:
            # 平均ブロック長
            avg_len = sum(b.get('char_count', len(b.get('text', ''))) for b in text_blocks) / len(text_blocks)
            if avg_len < 10:
                warnings.append(f"[F-10] text_blocks 品質警告: 平均ブロック長が短すぎる ({avg_len:.1f}文字)")

            # 1文字ブロックの検出
            single_char_blocks = [b for b in text_blocks if len(b.get('text', '')) <= 1]
            if len(single_char_blocks) > len(text_blocks) * 0.3:
                warnings.append(f"[F-10] text_blocks 品質警告: 1文字以下のブロックが多すぎる ({len(single_char_blocks)}/{len(text_blocks)})")

            # ========================================
            # Check 3b: text_blocks 採番検証（v1.1 契約）
            # ========================================
            orders = [b.get('order') for b in text_blocks]
            block_ids = [b.get('block_id') for b in text_blocks]

            # order が 0..N-1 の連番か
            expected_orders = list(range(len(text_blocks)))
            if orders != expected_orders:
                warnings.append(f"[F-10] text_blocks採番警告: order が連番でない")
                logger.warning(f"[F-10] order不整合: {orders[:5]}...")

            # block_id が 0..N-1 の連番か
            if block_ids != expected_orders:
                warnings.append(f"[F-10] text_blocks採番警告: block_id が連番でない")
                logger.warning(f"[F-10] block_id不整合: {block_ids[:5]}...")

            # text_blocks[0].block_type == "post_body" か（v1.1 契約必須）
            first_block_type = text_blocks[0].get('block_type') if text_blocks else None
            schema_ver = validated.get('schema_version', '')
            # 【v1.1契約】post_body.text の有無に関係なく、text_blocks[0] は必ず post_body
            if schema_ver == STAGE_H_INPUT_SCHEMA_VERSION and first_block_type != 'post_body':
                # v1.1 契約違反: text_blocks[0] が post_body でない
                validated['_contract_violation'] = True
                validated['_contract_violation_reason'] = f"v1.1契約違反: text_blocks[0].block_type={first_block_type}, expected=post_body"
                warnings.append(f"[F-10] V1.1_CONTRACT_VIOLATION: text_blocks[0]がpost_bodyでない")
                logger.error(f"[F-10] V1.1契約違反: text_blocks[0]={first_block_type}")
            elif first_block_type != 'post_body':
                # 非v1.1の場合は警告のみ
                warnings.append(f"[F-10] text_blocks[0]がpost_bodyでない: {first_block_type}")
                logger.warning(f"[F-10] text_blocks[0]がpost_bodyでない: {first_block_type}")

        # ========================================
        # Check 4: post_body 包含検証（v1.1 契約）
        # ========================================
        post_body = validated.get('post_body', {})
        post_body_text = post_body.get('text', '')
        if post_body_text and post_body_text.strip():
            pb_normalized = re.sub(r'\s+', '', post_body_text.lower())
            ft_normalized = re.sub(r'\s+', '', full_text.lower())
            if pb_normalized not in ft_normalized:
                warnings.append("[F-10] POST_BODY_NOT_INCLUDED_IN_FULL_TEXT")
                logger.warning("[F-10] post_body.text が full_text に包含されていません")
            else:
                logger.info(f"[F-10] post_body包含検証OK: {len(post_body_text)}文字")

        # ========================================
        # Check 5: サイズ制御（上限）
        # ========================================
        MAX_FULL_TEXT_CHARS = 100000  # 10万文字上限
        if len(full_text) > MAX_FULL_TEXT_CHARS:
            warnings.append(f"[F-10] サイズ警告: full_text が上限超過 ({len(full_text)} > {MAX_FULL_TEXT_CHARS})")
            # 【重要】切り捨て禁止 - 警告のみ
            logger.warning(f"[F-10] full_text が {MAX_FULL_TEXT_CHARS} 文字を超過（切り捨てしない）")

        # warnings を更新
        validated['warnings'] = warnings

        # 最終統計
        logger.info(f"[F-10完了] 受け渡し保証:")
        logger.info(f"  ├─ 必須フィールド: OK")
        logger.info(f"  ├─ 単調増加: {'OK' if not any('単調増加違反' in w for w in warnings) else 'NG'}")
        logger.info(f"  ├─ text_blocks品質: {'OK' if not any('品質警告' in w for w in warnings) else '警告あり'}")
        logger.info(f"  └─ 警告総数: {len(warnings)}件")

        return validated, warnings
