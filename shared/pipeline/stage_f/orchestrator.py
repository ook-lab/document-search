"""
Stage F: Visual Analysis Orchestrator (司令塔)

【Ver 10.7】Fは物理：位置だけ。意味理解は次段。ピクセル座標統一。
  E-6: Vision OCR (stage_e/e6_vision_ocr.py)
  E-7: 文字結合 (stage_e/e7_text_merger.py)
  E-8: bbox正規化 (stage_e/e8_bbox_normalizer.py)
  F-1: 罫線観測 (f1_grid_detector.py) - 候補全件保持、モデル不要
  F-2: 構造解析 (f2_structure_analyzer.py) - 物理条件のみでgrid構築
  F-3: 物理仕分け (f3_cell_assigner.py) - セル住所付け、モデル不要
  ※ F-4 (AIレスキュー) は削除済み
"""

import os
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from loguru import logger
from io import BytesIO
import tempfile

from shared.ai.llm_client.llm_client import LLMClient

# Document AI（Form Parser用）
try:
    from google.cloud import documentai_v1 as documentai
    DOCUMENTAI_AVAILABLE = True
except ImportError:
    documentai = None
    DOCUMENTAI_AVAILABLE = False

from .f1_grid_detector import F1GridDetector
from .f2_structure_analyzer import F2StructureAnalyzer
from .f3_cell_assigner import F3CellAssigner

# Stage G (Ver 9.0)
from ..stage_g import G3Scrub, G4Assemble, G5Audit, G6Packager

from ..stage_e import E6VisionOCR, E7TextMerger, E8BboxNormalizer

from ..constants import (
    STAGE_F_OUTPUT_SCHEMA_VERSION,
    CHUNK_SIZE_PAGES,
)


class StageFVisualAnalyzer:
    """Stage F: Visual Analysis (Ver 10.6) - 物理位置確定のみ"""

    def __init__(self, llm_client: LLMClient, enable_surya: bool = False):
        self.llm_client = llm_client

        # Stage E
        self._e6_ocr = E6VisionOCR()
        self._e7_merger = E7TextMerger(llm_client)
        self._e8_normalizer = E8BboxNormalizer()

        # Stage F (Ver 9.0)
        # Form Parser設定（Document AI）
        self._document_ai_client = None
        self._form_parser_enabled = False

        if DOCUMENTAI_AVAILABLE:
            project_id = os.environ.get('GCP_PROJECT_ID')
            location = os.environ.get('GCP_LOCATION', 'us')
            processor_id = os.environ.get('DOCUMENT_AI_FORM_PARSER_PROCESSOR_ID')

            if project_id and processor_id:
                try:
                    self._document_ai_client = documentai.DocumentProcessorServiceClient()
                    self._f1_detector = F1GridDetector(document_ai_client=self._document_ai_client)
                    self._f1_detector.configure_form_parser(
                        project_id=project_id,
                        location=location,
                        processor_id=processor_id
                    )
                    self._form_parser_enabled = True
                    logger.info(f"[Ver 9.0] Form Parser有効: project={project_id}, processor={processor_id}")
                except Exception as e:
                    logger.warning(f"[Ver 9.0] Form Parser初期化失敗: {e}")
                    self._f1_detector = F1GridDetector()
            else:
                logger.info("[Ver 9.0] Form Parser未設定（DOCUMENT_AI_FORM_PARSER_PROCESSOR_ID）")
                self._f1_detector = F1GridDetector()
        else:
            logger.info("[Ver 9.0] Document AI未インストール（pip install google-cloud-documentai）")
            self._f1_detector = F1GridDetector()

        self._f2_analyzer = F2StructureAnalyzer(llm_client)
        self._f3_assigner = F3CellAssigner()

        # Stage G
        self._g3_scrub = G3Scrub()
        self._g4_assemble = G4Assemble()
        self._g5_audit = G5Audit()
        self._g6_packager = G6Packager()

        logger.info("[Ver 10.6] E6→E7→E8→F1→F2→F3→G3→G4→G5→G6")

        self._e_content = ''
        self._e_physical_chars = []
        self._file_name = None
        self._doc_type = None
        self._workspace = None

    def process(
        self,
        file_path: Optional[Path],
        mime_type: str,
        requires_vision: bool = False,
        requires_transcription: bool = False,
        post_body: Optional[Dict[str, Any]] = None,
        progress_callback=None,
        prompt: str = None,
        model: str = None,
        extracted_text: str = None,
        workspace: str = None,
        e2_table_bboxes: List[Dict] = None,
        stage_e_metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Stage F メイン処理"""
        total_start = time.time()

        self._e_content = extracted_text or ''
        self._e_physical_chars = []
        if stage_e_metadata:
            self._e_physical_chars = stage_e_metadata.get('physical_chars', [])
            if self._e_physical_chars:
                logger.info(f"  ├─ Stage E物理証拠: {len(self._e_physical_chars)}文字")

        self._file_name = file_path.name if file_path else None
        self._doc_type = self._infer_doc_type(mime_type, file_path)
        self._workspace = workspace
        self._e2_table_bboxes = e2_table_bboxes or []

        logger.info("=" * 60)
        logger.info("[Stage F] Ver 10.6 処理開始")
        logger.info(f"  ├─ ファイル: {file_path.name if file_path else 'なし'}")
        logger.info(f"  ├─ MIMEタイプ: {mime_type}")
        logger.info(f"  ├─ requires_vision: {requires_vision}")
        logger.info(f"  └─ requires_transcription: {requires_transcription}")
        logger.info("=" * 60)

        if file_path is None or (not requires_vision and not requires_transcription):
            logger.info("[Stage F] スキップ")
            return self._create_empty_payload(post_body)

        if not file_path.exists():
            logger.error(f"[Stage F] ファイルなし: {file_path}")
            return self._create_empty_payload(post_body, error=f"File not found: {file_path}")

        is_audio = mime_type.startswith('audio/') if mime_type else False
        is_video = mime_type.startswith('video/') if mime_type else False
        is_document = mime_type in {'application/pdf'} or mime_type.startswith('text/')

        try:
            if is_audio or is_video:
                return self._process_audio_video(file_path, mime_type, is_video, post_body, progress_callback)

            return self._process_document(file_path, mime_type, is_document, post_body, progress_callback)

        except Exception as e:
            logger.error(f"[Stage F] エラー: {e}", exc_info=True)
            return self._create_empty_payload(post_body, error=str(e))

        finally:
            logger.info(f"[Stage F] 総処理時間: {time.time() - total_start:.2f}秒")

    def _process_audio_video(self, file_path, mime_type, is_video, post_body, progress_callback):
        """音声/動画は Ver 9.0 では未対応（別パイプラインで処理）"""
        logger.warning("[Stage F] 音声/動画は Ver 9.0 未対応")
        return self._create_empty_payload(post_body, error="Audio/Video not supported in Ver 9.0")

    def _process_document(self, file_path, mime_type, is_document, post_body, progress_callback):
        warnings_list = []
        all_page_images = self._convert_to_images(file_path, is_document)
        total_pages = len(all_page_images)
        logger.info(f"[Stage F] 総ページ数: {total_pages}")

        page_chunks = [all_page_images[i:i + CHUNK_SIZE_PAGES] for i in range(0, total_pages, CHUNK_SIZE_PAGES)]
        total_chunks = len(page_chunks)

        aggregated_blocks = []
        aggregated_grid = None
        aggregated_structure = None
        header_info = {}

        for chunk_idx, chunk_pages in enumerate(page_chunks):
            chunk_start_page = chunk_idx * CHUNK_SIZE_PAGES
            logger.info(f"[Stage F] チャンク {chunk_idx + 1}/{total_chunks}")

            pil_img = chunk_pages[0]['image']
            img_w, img_h = pil_img.width, pil_img.height
            page_size = {'w': img_w, 'h': img_h}

            img_bytes = BytesIO()
            pil_img.save(img_bytes, format='PNG')
            img_bytes.seek(0)

            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                tmp.write(img_bytes.read())
                tmp_path = Path(tmp.name)

            try:
                # E-6: Vision OCR
                if progress_callback:
                    progress_callback(f"E-6 ({chunk_idx + 1}/{total_chunks})")
                e6_result = self._e6_ocr.extract(tmp_path, img_w, img_h)
                if not e6_result.get('success'):
                    warnings_list.append(f"E6_ERROR: {e6_result.get('error')}")
                    continue
                vision_tokens = e6_result.get('vision_tokens', [])

                # E-7: 文字結合（Vision Glue & Repair）
                if progress_callback:
                    progress_callback(f"E-7 ({chunk_idx + 1}/{total_chunks})")
                merged_tokens = self._e7_merger.merge(vision_tokens, image_path=str(tmp_path))

                # E-8: bbox正規化
                if progress_callback:
                    progress_callback(f"E-8 ({chunk_idx + 1}/{total_chunks})")
                normalized_tokens = self._e8_normalizer.normalize(merged_tokens, page_size)

                # blocksに変換
                chunk_blocks = []
                for i, token in enumerate(normalized_tokens):
                    chunk_blocks.append({
                        "block_id": f"p{chunk_idx}_b{i}",
                        "text": token.get('text', ''),
                        "chunk_idx": chunk_idx,
                        "original_page": chunk_start_page,
                        "coords": {
                            "bbox": token.get('bbox', [0, 0, 0, 0]),
                            "x": token.get('x', 0),
                            "y": token.get('y', 0)
                        },
                        "bbox": token.get('bbox', [0, 0, 0, 0])
                    })
                aggregated_blocks.extend(chunk_blocks)

                # F-1: 罫線観測（Ver 10.7: data_rectマスク + ピクセル座標統一）
                if progress_callback:
                    progress_callback(f"F-1 ({chunk_idx + 1}/{total_chunks})")

                # e2_table_bboxesからdata_rectを生成（あれば）
                f1_data_rect = None
                if self._e2_table_bboxes:
                    f1_data_rect = self._build_data_rect(self._e2_table_bboxes, page_size)

                f1_result = self._f1_detector.detect(
                    pdf_path=file_path if file_path.suffix.lower() == '.pdf' else None,
                    page_image=pil_img,
                    page_num=chunk_start_page,
                    page_size=page_size,
                    data_rect=f1_data_rect,
                    use_form_parser=self._form_parser_enabled  # 互換性のため残す
                )

                # F1結果を取得（観測専用モード + Panel候補 + separator候補）
                line_candidates = f1_result.get('line_candidates', {'horizontal': [], 'vertical': []})
                table_bbox_candidate = f1_result.get('table_bbox_candidate')
                panel_candidates = f1_result.get('panel_candidates', [])
                separator_candidates_all = f1_result.get('separator_candidates_all', [])
                separator_candidates_ranked = f1_result.get('separator_candidates_ranked', [])
                f1_source = f1_result.get('source', 'none')

                # ログ出力
                h_count = len(line_candidates.get('horizontal', []))
                v_count = len(line_candidates.get('vertical', []))
                h_solid = len([l for l in line_candidates.get('horizontal', []) if l.get('style') == 'solid'])
                h_dashed = len([l for l in line_candidates.get('horizontal', []) if l.get('style') == 'dashed'])
                logger.info(f"[F-1] 罫線観測完了:")
                logger.info(f"[F-1]   source={f1_source}")
                logger.info(f"[F-1]   horizontal={h_count}本 (solid={h_solid}, dashed={h_dashed})")
                logger.info(f"[F-1]   vertical={v_count}本")
                logger.info(f"[F-1]   panel_candidates={len(panel_candidates)}パネル")
                logger.info(f"[F-1]   separator_candidates: all={len(separator_candidates_all)}, ranked={len(separator_candidates_ranked)}")
                # panel_candidates の要約（bbox・score）
                for pi, pc in enumerate(panel_candidates):
                    pb = pc.get('bbox', [0, 0, 0, 0])
                    ps = pc.get('score', 0)
                    pe = pc.get('evidence', {})
                    reasons = pe.get('reason', []) if isinstance(pe, dict) else []
                    logger.info(f"[F-1]     panel[{pi}]: bbox=[{pb[0]:.0f},{pb[1]:.0f},{pb[2]:.0f},{pb[3]:.0f}], score={ps:.2f}, reasons={reasons}")
                if table_bbox_candidate:
                    logger.info(f"[F-1]   table_bbox_candidate={table_bbox_candidate}")

                # 警告があればログ出力
                for warn in f1_result.get('warnings', []):
                    logger.warning(f"[F-1] {warn}")

                # F-2: 構造解析（幾何中心モード）
                if progress_callback:
                    progress_callback(f"F-2 ({chunk_idx + 1}/{total_chunks})")

                f2_start_time = time.time()

                # バイパス情報生成（オプション）
                bypass_prompt, bypass_meta = None, None
                # ※ 新F2では幾何中心なのでバイパスは参考程度

                logger.info(f"[F-2呼出] f1_source='{f1_source}', tokens={len(chunk_blocks)}")

                f2_result = self._f2_analyzer.analyze(
                    line_candidates=line_candidates,
                    tokens=chunk_blocks,
                    page_image=pil_img,
                    page_size=page_size,
                    table_bbox_candidate=table_bbox_candidate,
                    panel_candidates=panel_candidates,  # F1からのpanel候補
                    separator_candidates_all=separator_candidates_all,    # F1からの全separator候補
                    separator_candidates_ranked=separator_candidates_ranked,  # F1からのseparator候補（score順）
                    doc_type=self._doc_type,
                    bypass_prompt=bypass_prompt,
                    bypass_meta=bypass_meta
                )
                aggregated_structure = f2_result

                # F2結果からgridを取得
                aggregated_grid = f2_result.get('grid')
                has_table = f2_result.get('has_table', False)

                # F2ログ出力
                f2_elapsed = time.time() - f2_start_time
                row_centers_count = len(f2_result.get('row_centers', []))
                row_boundaries_count = len(f2_result.get('row_boundaries', []))
                col_boundaries_count = len(f2_result.get('col_boundaries', []))
                panels_count = len(f2_result.get('panels', []))
                grids_count = len(f2_result.get('grids', []))

                logger.info(f"[F-2完了] has_table={has_table}, elapsed={f2_elapsed:.2f}s")
                logger.info(f"[F-2完了]   row_centers={row_centers_count}, row_boundaries={row_boundaries_count}, col_boundaries={col_boundaries_count}")
                logger.info(f"[F-2完了]   panels={panels_count}, grids={grids_count}")

                if aggregated_grid:
                    logger.info(f"[F-2完了]   grid[0]: {aggregated_grid.get('row_count', 0)}行 x {aggregated_grid.get('col_count', 0)}列")

                # ヘッダー情報の抽出（F3向け）
                if has_table:
                    if aggregated_grid:
                        header_info = self._f2_analyzer.extract_headers_from_structure(
                            aggregated_grid, f2_result, chunk_blocks
                        )
                    else:
                        # gridなしだがhas_table=True → panels/table_bboxから推定
                        header_info = {}
                        if table_bbox_candidate:
                            header_info['table_bbox'] = table_bbox_candidate
                else:
                    header_info = {}

            finally:
                tmp_path.unlink(missing_ok=True)

        # F-3: 物理仕分け
        if progress_callback:
            progress_callback("F-3")
        logger.info("[F-3] 物理仕分け開始")

        # has_table=False の場合のみスキップ（gridがNoneでもhas_table=TrueならF3へ進む）
        if not has_table:
            logger.info("[F-3] has_table=False → F3スキップ")
            f3_result = {
                'cells': [],
                'rows': [],
                'cols': [],
                'outside_table_tokens': aggregated_blocks,
                'x_headers': [],
                'y_headers': []
            }

            # G3以降へジャンプ
            if progress_callback:
                progress_callback("G-3")
            logger.info("[G-3] Scrub開始（表なしモード）")

            g3_result = self._g3_scrub.scrub(
                structured_table=f3_result,
                logical_structure=aggregated_structure or {},
                e_physical_chars=self._e_physical_chars,
                f1_quality=0.0
            )

            if progress_callback:
                progress_callback("G-4")
            logger.info("[G-4] Assemble開始")

            g4_result = self._g4_assemble.assemble(
                scrubbed_core=g3_result,
                logical_structure=aggregated_structure,
                metadata={"total_pages": total_pages, "total_chunks": total_chunks, "has_table": False}
            )

            if progress_callback:
                progress_callback("G-5")
            logger.info("[G-5] Audit開始")

            payload = self._g5_audit.audit(
                assembled_payload=g4_result,
                post_body=post_body,
                metadata={"total_pages": total_pages, "total_chunks": total_chunks, "has_table": False}
            )
            payload["warnings"].extend(warnings_list)
            return payload

        # ━━━ Ver 10.8: 全トークンをF3に渡す（フィルタ撤廃）━━━
        # F3内部で「セル内/セル外」をタグ付けする方式に変更
        # table_bboxは参照情報としてF3に渡す（フィルタには使わない）
        table_bbox = (header_info or {}).get('table_bbox')

        logger.info(f"[F-3] 全トークンをF3に渡す: {len(aggregated_blocks)}件")
        if table_bbox:
            logger.info(f"[F-3]   table_bbox={table_bbox} (参照情報)")

        # F2のpanels情報をF3に渡す
        f2_panels = (aggregated_structure or {}).get('panels', [])

        f3_result, low_confidence = self._f3_assigner.assign(
            grid=aggregated_grid or {},
            tokens=aggregated_blocks,  # 全トークンを渡す
            structure=aggregated_structure or {},
            panels=f2_panels
        )

        # ============================================
        # G3: Scrub（唯一の書き換えゾーン）
        # ============================================
        if progress_callback:
            progress_callback("G-3")
        logger.info("[G-3] Scrub開始")

        f1_quality = aggregated_grid.get('quality', 1.0) if aggregated_grid else 1.0
        g3_result = self._g3_scrub.scrub(
            structured_table=f3_result,
            logical_structure=aggregated_structure or {},
            e_physical_chars=self._e_physical_chars,
            f1_quality=f1_quality
        )

        # ============================================
        # G4: Assemble（read-only組み立て）
        # ============================================
        if progress_callback:
            progress_callback("G-4")
        logger.info("[G-4] Assemble開始")

        g4_result = self._g4_assemble.assemble(
            scrubbed_core=g3_result,
            logical_structure=aggregated_structure,
            metadata={"total_pages": total_pages, "total_chunks": total_chunks}
        )

        # ============================================
        # G5: Audit（検算・品質・確定 = 唯一の正本出口）
        # ============================================
        if progress_callback:
            progress_callback("G-5")
        logger.info("[G-5] Audit開始")

        payload = self._g5_audit.audit(
            assembled_payload=g4_result,
            post_body=post_body,
            metadata={"total_pages": total_pages, "total_chunks": total_chunks}
        )
        payload["warnings"].extend(warnings_list)

        return payload

    def _convert_to_images(self, file_path, is_document):
        from PIL import Image
        if is_document and file_path.suffix.lower() == '.pdf':
            try:
                from pdf2image import convert_from_path
                images = convert_from_path(str(file_path), dpi=150)
                return [{"page_num": i, "image": img} for i, img in enumerate(images)]
            except Exception as e:
                logger.error(f"PDF変換エラー: {e}")
                return []
        else:
            try:
                img = Image.open(file_path)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                return [{"page_num": 0, "image": img}]
            except Exception as e:
                logger.error(f"画像読み込みエラー: {e}")
                return []

    def _build_data_rect(self, e2_table_bboxes: List[Dict], page_size: Dict[str, int]) -> Optional[Dict]:
        """e2_table_bboxesからF1用のdata_rectを生成（Ver 10.7）"""
        if not e2_table_bboxes:
            return None

        # 全bboxの和集合を計算
        x0_min, y0_min = float('inf'), float('inf')
        x1_max, y1_max = float('-inf'), float('-inf')

        for tb in e2_table_bboxes:
            bbox = tb.get('bbox') or tb.get('table_bbox')
            if not bbox or len(bbox) != 4:
                continue
            x0_min = min(x0_min, bbox[0])
            y0_min = min(y0_min, bbox[1])
            x1_max = max(x1_max, bbox[2])
            y1_max = max(y1_max, bbox[3])

        if x0_min == float('inf'):
            return None

        # マージン追加（表の外側の罫線も拾えるように）
        margin = max(page_size.get('w', 0), page_size.get('h', 0)) * 0.02
        data_rect = {
            'x0': max(0, x0_min - margin),
            'y0': max(0, y0_min - margin),
            'x1': min(page_size.get('w', x1_max + margin), x1_max + margin),
            'y1': min(page_size.get('h', y1_max + margin), y1_max + margin)
        }
        logger.info(f"[Orchestrator] data_rect生成: {data_rect}")
        return data_rect

    def _infer_doc_type(self, mime_type, file_path):
        if not mime_type:
            return "unknown"
        if mime_type == 'application/pdf':
            name = file_path.name.lower() if file_path else ""
            if '成績' in name or 'score' in name:
                return "成績表"
            elif '時間割' in name or 'schedule' in name:
                return "時間割"
            return "PDF文書"
        elif mime_type.startswith('image/'):
            return "画像"
        return "その他"

    def _create_empty_payload(self, post_body, error=None):
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
