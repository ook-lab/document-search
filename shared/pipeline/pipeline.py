"""
統合ドキュメント処理パイプライン (Stage E-K) - 設定ベース版

設計書: DESIGN_UNIFIED_PIPELINE.md v2.0 に準拠
処理順序: Stage E → F → G → H1 → H2 → J → K

Stage概要:
- Stage E: Pre-processing（テキスト抽出）
- Stage F: Visual Analysis（視覚解析、gemini-2.5-pro）
         - 物理的OCR抽出、JSON出力（カラムナ形式）
- Stage G: Logical Refinement（論理的精錬、gemini-2.0-flash-lite）
         - 重複排除、REF_ID付与、unified_text生成
- Stage H1: Table Specialist（表処理専門）
         - 定型表・構造化表を先に処理
         - カラムナ形式→辞書リスト変換
         - H2への入力量削減のため表テキストを抽出
- Stage H2: Text Specialist（テキスト処理専門、gemini-2.0-flash）
         - 軽量化されたテキストで構造化 + 要約
         - calendar_events, tasks, title, summary を生成
         - audit_canonical_text（監査用正本）を生成
- Stage J: Chunking（チャンク化）
- Stage K: Embedding（ベクトル化）

特徴:
- doc_type / workspace に応じて自動的にプロンプトとモデルを切り替え
- config/ 内の YAML と Markdown ファイルで設定管理
- Stage G で REF_ID付き目録を生成し、後続ステージが参照可能
- H1 + H2 分割によりトークン消費を削減
"""
import asyncio
import json
from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger

from shared.ai.llm_client.llm_client import LLMClient
from shared.common.database.client import DatabaseClient
from shared.common.connectors.google_drive import GoogleDriveConnector

from .config_loader import ConfigLoader
from .stage_e import (
    StageEPreprocessor,          # E1
    E2TableDetector,             # E2
    E3OpenCVBlocks,              # E3
    E4CoordinateIntegrator,      # E4
    E5MaskGenerator,             # E5
    E6VisionOCR,                 # E6
    E7TextAggregator,            # E7
    E8VisionAggregator,          # E8
    E9TextReplacer,              # E9
    E11BboxNormalizer,           # E11
    StageEOrchestrator           # E1-E2-E3-E4-E5-E6-E7-E8-E9-E11統合
)
from .stage_f import StageFVisualAnalyzer  # 【Ver 11.0】F1→F2→F3→G3→G4→G5→G6（E6-E8はStage Eに移動）
from .stage_h import StageH1Table, StageH2Text  # Stage H1/H2
from .stage_h.h_kakeibo import StageHKakeibo  # 家計簿専用
from .stage_j_chunking import StageJChunking
from .stage_k_embedding import StageKEmbedding

# Phase 5: Execution versioning
from shared.processing.execution_manager import ExecutionManager, ExecutionContext

# 家計簿専用のDB保存ハンドラー (オプショナル)
try:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    from shared.kakeibo.kakeibo_db_handler import KakeiboDBHandler
    KAKEIBO_AVAILABLE = True
except ImportError:
    logger.warning("K_kakeibo module not available, kakeibo features will be disabled")
    KakeiboDBHandler = None
    KAKEIBO_AVAILABLE = False


# ============================================
# v1.1 契約: post_body は Rawdata_FILE_AND_MAIL.display_post_text から取得
# ============================================
def _build_post_body(raw_doc: dict | None) -> dict:
    """
    post_body を Rawdata_FILE_AND_MAIL.display_post_text から直接取得。
    GAS で classroom/gmail/drive 全てこのカラムに本文を保存している。

    Returns:
        { "text": str, "source": str, "char_count": int }
    """
    if not isinstance(raw_doc, dict):
        return {"text": "", "source": "no_raw_doc", "char_count": 0}

    text = (raw_doc.get("display_post_text") or "").strip()
    if text:
        return {"text": text, "source": "rawdata.display_post_text", "char_count": len(text)}

    return {"text": "", "source": "empty", "char_count": 0}


class UnifiedDocumentPipeline:
    """統合ドキュメント処理パイプライン (Stage E-K) - 設定ベース版"""

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """
        テキストからnull文字を除去

        Args:
            text: 入力テキスト

        Returns:
            サニタイズ済みテキスト
        """
        if not text:
            return text
        # null文字 (\u0000) を除去
        return text.replace('\u0000', '')

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        db_client: Optional[DatabaseClient] = None,
        config_dir: Optional[Path] = None,
        enable_hybrid_ocr: Optional[bool] = None
    ):
        """
        Args:
            llm_client: LLMクライアント（Noneの場合は新規作成）
            db_client: データベースクライアント（Noneの場合は新規作成）
            config_dir: 設定ディレクトリ（デフォルト: G_unified_pipeline/config/）
            enable_hybrid_ocr: ハイブリッドOCR（Surya + PaddleOCR）を有効化（Noneの場合は設定ファイルから取得）
        """
        self.llm_client = llm_client or LLMClient()
        self.db = db_client or DatabaseClient(use_service_role=True)  # RLSバイパスのためService Role使用
        self.drive_connector = GoogleDriveConnector()  # Google Drive ファイル名更新用

        # 設定ローダーを初期化
        self.config = ConfigLoader(config_dir)

        # ハイブリッドOCRの有効/無効を決定
        if enable_hybrid_ocr is None:
            # 設定ファイルから取得（デフォルトの設定）
            enable_hybrid_ocr = self.config.get_hybrid_ocr_enabled('default')

        # 各ステージを初期化
        # Stage E（E1-E2-E3-E4-E5-E6-E7-E8-E9-E11統合）
        self.stage_e = StageEOrchestrator(
            llm_client=self.llm_client,
            stage_e_preprocessor=StageEPreprocessor(),          # E1
            e2_table_detector=E2TableDetector(),                # E2
            e3_opencv_blocks=E3OpenCVBlocks(),                  # E3
            e4_coordinate_integrator=E4CoordinateIntegrator(),  # E4
            e5_mask_generator=E5MaskGenerator(),                # E5
            e6_ocr=E6VisionOCR(),                               # E6
            e7_text_aggregator=E7TextAggregator(),              # E7
            e8_vision_aggregator=E8VisionAggregator(),          # E8
            e9_text_replacer=E9TextReplacer(),                  # E9
            e11_normalizer=E11BboxNormalizer()                  # E11
        )
        # Stage F（F1-F3 + G3-G6、E6-E8を削除）
        self.stage_f = StageFVisualAnalyzer(self.llm_client, enable_surya=enable_hybrid_ocr)
        # Stage H
        self.stage_h1 = StageH1Table(self.llm_client)  # Stage H1: 表処理専門
        self.stage_h2 = StageH2Text(self.llm_client)  # Stage H2: テキスト処理専門
        self.stage_h_kakeibo = StageHKakeibo(self.db)  # 家計簿専用
        self.stage_j = StageJChunking()
        self.stage_k = StageKEmbedding(self.llm_client, self.db)

        # 家計簿専用のDB保存ハンドラー
        self.kakeibo_db_handler = KakeiboDBHandler(self.db) if KAKEIBO_AVAILABLE else None

        logger.info(f"✅ UnifiedDocumentPipeline 初期化完了（E→F(Ver9.0)→H1→H2→J→K, ハイブリッドOCR={'有効' if enable_hybrid_ocr else '無効'}）")

    async def process_document(
        self,
        file_path: Path,
        file_name: str,
        doc_type: str,
        workspace: str,
        mime_type: str,
        source_id: str,
        existing_document_id: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
        progress_callback=None,
        owner_id: Optional[str] = None,
        enable_execution_tracking: bool = False
    ) -> Dict[str, Any]:
        """
        ドキュメントを処理（Stage E-K）

        Args:
            file_path: ファイルパス
            file_name: ファイル名
            doc_type: ドキュメントタイプ（設定ルーティングに使用）
            workspace: ワークスペース
            mime_type: MIMEタイプ
            source_id: ソースID
            existing_document_id: 更新する既存ドキュメントID（Noneの場合は新規作成）
            extra_metadata: 追加メタデータ（Classroom固有フィールドなど）
            progress_callback: 進捗コールバック
            owner_id: オーナーID（Phase 3 必須 for kakeibo）
            enable_execution_tracking: Phase 5 execution versioning を有効化

        Returns:
            処理結果 {'success': bool, 'document_id': str, ...}
        """
        # Phase 5: Execution tracking 初期化
        execution_context: Optional[ExecutionContext] = None
        execution_manager: Optional[ExecutionManager] = None
        start_time = None

        if enable_execution_tracking:
            import time
            start_time = time.time()
            execution_manager = ExecutionManager(self.db)

        try:
            logger.info(f"📄 ドキュメント処理開始: {file_name} (doc_type={doc_type}, workspace={workspace})")

            # ============================================
            # Stage E: E1-E8統合処理（PDF抽出 + Vision OCR）
            # ============================================
            logger.info("[Stage E] E1-E8統合処理開始...")
            if progress_callback:
                progress_callback("E1")

            # ドキュメント判定（PDFかどうか）
            is_document = mime_type and mime_type.startswith('application/pdf')

            # Stage E: E1-E8を実行
            stage_e_result = self.stage_e.process(
                file_path=file_path,
                mime_type=mime_type,
                is_document=is_document,
                progress_callback=progress_callback
            )

            # イベントループに制御を返す（並列タスク実行のため）
            await asyncio.sleep(0)

            # Stage E の結果をチェック
            if not stage_e_result.get('success'):
                error_msg = f"Stage E失敗: {stage_e_result.get('error', 'E1-E8処理エラー')}"
                logger.error(f"[Stage E失敗] {error_msg}")
                return {'success': False, 'error': error_msg}

            # Stage E の出力を取得
            normalized_tokens = stage_e_result.get('normalized_tokens', [])
            e_physical_chars = stage_e_result.get('e_physical_chars', [])
            extracted_text = stage_e_result.get('extracted_text', '')
            page_images = stage_e_result.get('page_images', [])
            stage_e_metadata = stage_e_result.get('metadata', {})
            e2_table_bboxes = stage_e_metadata.get('table_bboxes', [])

            logger.info(f"[Stage E完了] normalized_tokens={len(normalized_tokens)}, "
                       f"e_physical_chars={len(e_physical_chars)}, "
                       f"extracted_text={len(extracted_text)}文字, "
                       f"page_images={len(page_images)}ページ")
            # ログ出力は Stage E 内で既に実施済み

            # ============================================
            # Stage F: Visual Analysis (gemini-2.5-pro で完璧に仕上げる)
            # ============================================
            # post_body 作成（投稿本文 = Stage H 最優先文脈）
            # 【v1.1契約】Rawdata_FILE_AND_MAIL から本文を優先取得
            raw_doc = None
            if existing_document_id:
                try:
                    r = self.db.client.table("Rawdata_FILE_AND_MAIL").select(
                        "id, display_post_text, attachment_text"
                    ).eq("id", existing_document_id).limit(1).execute()
                    if r and getattr(r, "data", None):
                        raw_doc = r.data[0]
                        logger.info(f"[Stage F] raw_doc取得: id={existing_document_id}")
                except Exception as e:
                    logger.warning(f"[Stage F] raw_doc取得失敗: {e.__class__.__name__}: {e}")

            post_body = _build_post_body(raw_doc)
            logger.info(f"[Stage F] post_body作成: {post_body['char_count']}文字 (source: {post_body['source']})")

            # P0-4: Stage F 直前の存在チェック（ファイルがある場合のみ）
            if file_path is not None and not file_path.exists():
                error_msg = f"[P0-4] TEMP_PDF_MISSING: Stage F 入力ファイルが存在しません: {file_path}"
                logger.error(error_msg)
                return {
                    'success': False,
                    'error': error_msg,
                    'failure_stage': 'F',
                    'failure_reason': 'TEMP_PDF_MISSING'
                }

            # 設定から Stage F のプロンプトとモデルを取得
            stage_f_config = self.config.get_stage_config('stage_f', doc_type, workspace)
            prompt_f = stage_f_config['prompt']
            model_f = stage_f_config['model']

            # P0-1: 明示的に file_path を渡す（state 参照禁止）
            logger.info(f"[Stage F] Visual Analysis開始... (model={model_f})")
            if file_path is not None:
                logger.info(f"[P0-1] 入力ファイル: {file_path} (exists={file_path.exists()})")
            else:
                logger.info("[P0-1] 入力ファイル: なし（テキストのみ）")
            # P2-2: E-2のtable_bboxes情報をログ出力
            if e2_table_bboxes:
                logger.info(f"[P2-2] Stage Fへ渡す E-2 table_bboxes: {len(e2_table_bboxes)}個")

            if progress_callback:
                progress_callback("F")

            # Stage E が既に Vision 処理を完了しているため、常に vision を実行
            requires_vision = True
            requires_transcription = False

            # stage_e_metadata に physical_chars を追加
            stage_e_metadata['physical_chars'] = e_physical_chars

            # Stage F 呼び出し（Ver 11.0: E6-E8の出力を渡す）
            stage_f_result = self.stage_f.process(
                file_path=file_path,
                mime_type=mime_type or '',
                normalized_tokens=normalized_tokens,  # Stage E（E6-E8）の出力
                page_images=page_images,  # ページ画像
                requires_vision=requires_vision,
                requires_transcription=requires_transcription,
                post_body=post_body,
                progress_callback=progress_callback,
                # YAMLから読み込んだ設定を渡す
                prompt=prompt_f,
                model=model_f,
                extracted_text=extracted_text,
                workspace=workspace,
                e2_table_bboxes=e2_table_bboxes,
                stage_e_metadata=stage_e_metadata  # 【Ver 6.4】座標付き文字情報（physical_chars含む）
            )
            logger.info(f"[Stage F完了] Vision結果: {type(stage_f_result).__name__}")

            # イベントループに制御を返す（並列タスク実行のため）
            await asyncio.sleep(0)

            # ============================================
            # Stage F 結果処理（Dict型を直接使用 - dumps/loads排除）
            # ============================================
            try:
                # Stage F は Dict を直接返す（JSONの往復変換を排除）
                vision_json = stage_f_result
                # DB保存用にJSON文字列も保持（vision_raw）
                vision_raw = json.dumps(stage_f_result, ensure_ascii=False)

                # Stage F payload をそのまま使用（再構成禁止）
                stage_f_structure = vision_json
                schema_ver = vision_json.get('schema_version', '')

                # full_text をそのまま使用（混ぜ物合成禁止）
                # Ver 14.0: G5出力は path_a_result.full_text_ordered にテキストを格納
                _pa = vision_json.get('path_a_result', {})
                combined_text = _pa.get('full_text_ordered', '') or vision_json.get('full_text', '')
                post_body = vision_json.get('post_body', {})
                text_blocks = vision_json.get('text_blocks', [])

                _tables = _pa.get('tables', [])
                logger.info(f"[Stage F→H] データ受け渡し:")
                logger.info(f"  ├─ schema_version: {schema_ver}")
                logger.info(f"  ├─ full_text_ordered: {len(combined_text)}文字")
                logger.info(f"  ├─ post_body: {post_body.get('char_count', 0)}文字 (source: {post_body.get('source', 'unknown')})")
                logger.info(f"  ├─ text_blocks: {len(text_blocks)}ブロック")
                logger.info(f"  ├─ tables: {len(_tables)}個")
                for _t in _tables:
                    _rid = _t.get('ref_id', '?')
                    _hm = _t.get('header_map', {})
                    _ce = _t.get('cells_enriched', [])
                    _cf = _t.get('cells_flat', [])
                    _panels = _hm.get('panels', {})
                    logger.info(f"  │   {_rid}: cells_enriched={len(_ce)}, cells_flat={len(_cf)}, header_map panels={len(_panels)}")
                    # G7: パネルごとのヘッダー位置
                    for _pk, _pcfg in _panels.items():
                        logger.info(f"  │     {_pk}: col_header_rows={_pcfg.get('col_header_rows', [])}, row_header_cols={_pcfg.get('row_header_cols', [])}")
                    # G8: パネルごとのenrichment紐付け率
                    if _ce:
                        _by_panel = {}
                        for _c in _ce:
                            _pid = f"P{_c.get('panel_id', 0) or 0}"
                            if _pid not in _by_panel:
                                _by_panel[_pid] = {'total': 0, 'data': 0, 'col': 0, 'row': 0}
                            _by_panel[_pid]['total'] += 1
                            if not _c.get('is_header', False) and str(_c.get('text', '')).strip():
                                _by_panel[_pid]['data'] += 1
                                if _c.get('col_header'):
                                    _by_panel[_pid]['col'] += 1
                                if _c.get('row_header'):
                                    _by_panel[_pid]['row'] += 1
                        for _pid in sorted(_by_panel.keys()):
                            _s = _by_panel[_pid]
                            logger.info(f"  │     {_pid} enrichment: data={_s['data']}, col_header={_s['col']}/{_s['data']}, row_header={_s['row']}/{_s['data']}")
                logger.info(f"  └─ (G7/G8 enrichment {'済' if _tables and _tables[0].get('cells_enriched') else '未'})")
            except json.JSONDecodeError as e:
                logger.warning(f"[Stage F→H] JSON解析失敗: {e}")
                combined_text = vision_raw
                stage_f_structure = None

            # 空のコンテンツをチェック（空のドキュメントは警告のみ、エラーではない）
            if not combined_text or not combined_text.strip():
                logger.warning(f"[Stage F→H] 統合テキストが空です（テキストのないドキュメントの可能性）")
                combined_text = ""  # 空文字列として継続

            # ============================================
            # Stage G: Ver 9.0 では Stage F 内部で処理済み
            # ============================================
            # G3(Scrub)→G4(Assemble)→G5(Audit)→G6(Packager) は orchestrator.py 内で実行
            # stage_f_result には scrubbed_data (G5出力) が含まれる
            logger.info("[Stage G] Ver 9.0: Stage F 内部で処理済み（G3→G4→G5→G6）")

            # Stage F の path_a_result から情報を取得
            path_a_result = stage_f_structure.get('path_a_result', {})

            # 警告があれば出力
            for warning in stage_f_structure.get('warnings', []):
                logger.warning(f"[Stage F/G警告] {warning}")

            # ============================================
            # Stage H+I: 構造化 + 統合・要約
            # ============================================
            # custom_handler の確認（ルート設定から直接取得、model は取得しない）
            route_config = self.config.get_route_config(doc_type, workspace)
            stage_h_routing = route_config.get('stages', {}).get('stage_h', {})
            custom_handler = stage_h_routing.get('custom_handler')

            # 家計簿専用処理の場合（統合版は使わない）
            if custom_handler == 'kakeibo':
                # 家計簿の場合のみ stage_h_config を取得
                stage_h_config = self.config.get_stage_config('stage_h', doc_type, workspace)
                logger.info(f"[Stage H] 家計簿構造化開始... (custom_handler=kakeibo)")
                if progress_callback:
                    progress_callback("H")

                # Stage F の出力を辞書に変換（combined_text が JSON 文字列の場合）
                # ※ json, re はモジュールレベルでインポート済み
                try:
                    # Markdownのコードブロック (```json ... ```) を除去
                    json_text = combined_text.strip()
                    if json_text.startswith('```'):
                        # 最初と最後の```を除去
                        json_text = re.sub(r'^```(?:json)?\s*\n', '', json_text)
                        json_text = re.sub(r'\n```\s*$', '', json_text)

                    logger.debug(f"[Stage H] JSON パース前の最初の500文字:\n{json_text[:500]}")
                    stage_f_output = json.loads(json_text)
                except (json.JSONDecodeError, TypeError) as e:
                    logger.error(f"[Stage H] combined_text が JSON 形式ではありません: {e}")
                    logger.error(f"[Stage H] combined_text の内容:\n{combined_text[:1000]}")
                    raise ValueError("Stage F output must be JSON for kakeibo processing")

                # 家計簿専用 Stage H で処理
                stageH_result = self.stage_h_kakeibo.process(stage_f_output)

                # イベントループに制御を返す（並列タスク実行のため）
                await asyncio.sleep(0)

                # 家計簿専用のDB保存
                if self.kakeibo_db_handler:
                    # Phase 3: owner_id 必須チェック
                    if not owner_id:
                        raise ValueError("owner_id is required for kakeibo processing (Phase 3)")

                    logger.info("[DB保存] 家計簿データをDBに保存...")
                    kakeibo_save_result = self.kakeibo_db_handler.save_receipt(
                        stage_h_output=stageH_result,
                        file_name=file_name,
                        drive_file_id=source_id,
                        model_name=stage_h_config['model'],
                        source_folder=workspace,
                        owner_id=owner_id
                    )
                    logger.info(f"[DB保存完了] receipt_id={kakeibo_save_result['receipt_id']}")
                else:
                    logger.warning("K_kakeibo module not available, skipping kakeibo DB save")

                # 家計簿は Rawdata_FILE_AND_MAIL に保存せず、ここで終了
                return {
                    'success': True,
                    'receipt_id': kakeibo_save_result['receipt_id'],
                    'transaction_ids': kakeibo_save_result['transaction_ids'],
                    'log_id': kakeibo_save_result['log_id'],
                    'doc_type': 'kakeibo'
                }

            # ============================================
            # Stage H1 + H2: 分割処理（トークン消費削減版）
            # ============================================
            else:
                stage_hi_config = self.config.get_stage_config('stage_hi', doc_type, workspace)
                prompt_hi = stage_hi_config['prompt']
                model_hi = stage_hi_config['model']

                # -----------------------------------------
                # Ver 9.0: Stage F から直接データ取得
                # -----------------------------------------
                # アンカー配列を取得（G5出力）
                anchors = stage_f_structure.get('anchors', []) if stage_f_structure else []
                # 表データを取得（path_a_result内）
                tables = path_a_result.get('tables', [])

                logger.info(f"[Stage F→H] Ver 9.0: anchors={len(anchors)}件, tables={len(tables)}件")

                # -----------------------------------------
                # Stage H1: 表処理専門
                # -----------------------------------------
                logger.info(f"[Stage H1] 表処理開始... (表: {len(tables)}件)")
                if progress_callback:
                    progress_callback("H1")

                # G4の読み順済みテキストを取得（ドメイン検出用）
                all_tagged_texts = path_a_result.get('tagged_texts', [])
                logger.info(f"[Stage H1] all_tagged_texts: {len(all_tagged_texts)}件")

                h1_result = self.stage_h1.process(
                    table_inventory=tables,
                    all_tagged_texts=all_tagged_texts,
                    doc_type=doc_type,
                    workspace=workspace,
                    unified_text=combined_text
                )

                # H1の結果をログ
                h1_stats = h1_result.get('statistics', {})
                logger.info(f"[Stage H1完了] processed={h1_stats.get('processed', 0)}, skipped={h1_stats.get('skipped', 0)}")
                for _pt in h1_result.get('processed_tables', []):
                    _cols = _pt.get('columns', [])
                    _rows = _pt.get('rows', [])
                    logger.info(f"  ├─ {_pt.get('ref_id')}: columns={_cols}, rows={len(_rows)}行")
                    for _ri, _row in enumerate(_rows[:3]):
                        logger.info(f"  │   row[{_ri}]: {_row}")
                    if len(_rows) > 3:
                        logger.info(f"  │   ... 残り{len(_rows) - 3}行")
                logger.info(f"  └─ reduced_text: {len(h1_result.get('reduced_text', ''))}文字")

                # イベントループに制御を返す
                await asyncio.sleep(0)

                # -----------------------------------------
                # H2用テキスト（Ver 9.0: full_text_ordered使用）
                # -----------------------------------------
                reduced_text = path_a_result.get('full_text_ordered', '') or combined_text

                logger.info(f"[Stage H1→H2] テキスト: {len(reduced_text)}文字")

                # -----------------------------------------
                # Stage H2: テキスト処理専門
                # -----------------------------------------
                logger.info(f"[Stage H2] テキスト処理開始... (model={model_hi})")
                if progress_callback:
                    progress_callback("H2")

                stageHI_result = self.stage_h2.process(
                    file_name=file_name,
                    doc_type=doc_type,
                    workspace=workspace,
                    reduced_text=reduced_text,
                    prompt=prompt_hi,
                    model=model_hi,
                    h1_result=h1_result,
                    stage_f_structure=stage_f_structure,
                    stage_g_result=None  # Ver 9.0: 旧Stage G削除
                )

                # イベントループに制御を返す（並列タスク実行のため）
                await asyncio.sleep(0)

                # Stage H2 の結果をチェック
                if not stageHI_result or not isinstance(stageHI_result, dict):
                    error_msg = "Stage H2失敗: 結果が不正です"
                    logger.error(f"[Stage H2失敗] {error_msg}")
                    return {'success': False, 'error': error_msg}

                # 結果を変数に展開
                document_date = stageHI_result.get('document_date')
                tags = stageHI_result.get('tags', [])
                stageH_metadata = stageHI_result.get('metadata', {})
                title = stageHI_result.get('title', '')
                summary = stageHI_result.get('summary', '')
                relevant_date = stageHI_result.get('document_date')

                # カレンダーイベントとタスクを取得
                calendar_events = stageHI_result.get('calendar_events', [])
                tasks = stageHI_result.get('tasks', [])

                # metadataに追加
                stageH_metadata['calendar_events'] = calendar_events
                stageH_metadata['tasks'] = tasks

                # audit_canonical_text があれば metadata に追加
                audit_text = stageHI_result.get('audit_canonical_text', '')
                if audit_text:
                    stageH_metadata['audit_canonical_text'] = audit_text

                # H1処理統計を追加
                stageH_metadata['_h1_h2_split'] = True
                stageH_metadata['_h1_statistics'] = h1_stats

                # 【Ver 9.0】監査ログをメタデータに追加（change_log from G5）
                if stage_f_structure.get('change_log'):
                    stageH_metadata['_v90_change_log'] = stage_f_structure['change_log']
                if stage_f_structure.get('anomaly_report'):
                    stageH_metadata['_v90_anomaly_report'] = stage_f_structure['anomaly_report']

                logger.info(f"[Stage H1+H2完了] title={title[:30] if title else 'N/A'}..., "
                           f"calendar_events={len(calendar_events)}件, tasks={len(tasks)}件")

                # Stage H の結果オブジェクトを作成
                stageH_result = {
                    'document_date': document_date,
                    'tags': tags,
                    'metadata': stageH_metadata
                }

            # ============================================
            # Google Drive ファイル名更新（タイトルに基づく）
            # ============================================
            if title and source_id and file_name:
                # ファイル名から拡張子を抽出
                import os
                file_extension = os.path.splitext(file_name)[1]  # 例: ".pdf"

                # 新しいファイル名を生成（タイトル + 拡張子）
                new_file_name = title + file_extension

                # Google Drive のファイル名を更新
                try:
                    self.drive_connector.rename_file(source_id, new_file_name)
                    logger.info(f"[Google Drive] ファイル名更新成功: {new_file_name}")
                except Exception as e:
                    # ファイル名更新失敗はエラーログのみ（処理は継続）
                    logger.warning(f"[Google Drive] ファイル名更新失敗: {e}")

            # ============================================
            # Stage J: Chunking
            # ============================================
            logger.info("[Stage J] チャンク化開始...")
            if progress_callback:
                progress_callback("J")
            chunks = self.stage_j.process(
                display_subject=extra_metadata.get('display_subject', file_name) if extra_metadata else file_name,
                summary=summary,
                tags=tags,
                document_date=document_date,
                metadata=stageH_metadata
            )
            logger.info(f"[Stage J完了] チャンク数: {len(chunks)}")

            # イベントループに制御を返す（並列タスク実行のため）
            await asyncio.sleep(0)

            # ============================================
            # DB保存: Rawdata_FILE_AND_MAIL
            # ============================================
            document_id = existing_document_id
            try:
                # 既存ドキュメントの attachment_text, metadata, display_* フィールドを取得（nullで上書きしないため）
                existing_attachment_text = None
                existing_metadata = {}
                existing_display_fields = {}
                if existing_document_id:
                    try:
                        existing_doc = self.db.client.table('Rawdata_FILE_AND_MAIL').select(
                            'attachment_text, metadata, display_sender, display_sender_email, display_subject, display_sent_at, display_post_text'
                        ).eq('id', existing_document_id).execute()
                        if existing_doc.data and len(existing_doc.data) > 0:
                            doc = existing_doc.data[0]
                            existing_attachment_text = doc.get('attachment_text', '')
                            # 既存 metadata を保持（message_id, thread_id, subject など）
                            existing_metadata = doc.get('metadata', {})
                            if isinstance(existing_metadata, str):
                                existing_metadata = json.loads(existing_metadata)
                            # display_* フィールドを保持
                            existing_display_fields = {
                                'display_sender': doc.get('display_sender'),
                                'display_sender_email': doc.get('display_sender_email'),
                                'display_subject': doc.get('display_subject'),
                                'display_sent_at': doc.get('display_sent_at'),
                                'display_post_text': doc.get('display_post_text')
                            }
                            logger.debug(f"[DB保存] 既存attachment_text取得: {len(existing_attachment_text or '')}文字")
                            logger.debug(f"[DB保存] 既存metadata取得: {list(existing_metadata.keys())}")
                            logger.debug(f"[DB保存] 既存display_*フィールド取得: sender={existing_display_fields.get('display_sender')}, subject={existing_display_fields.get('display_subject')}")
                    except Exception as e:
                        logger.warning(f"[DB保存警告] 既存フィールド取得失敗: {e}")

                # テキストフィールドをサニタイズ（null文字を除去）
                sanitized_combined_text = self._sanitize_text(combined_text)
                sanitized_summary = self._sanitize_text(summary)
                sanitized_extracted_text = self._sanitize_text(extracted_text)

                # Stage F の出力をパース（JSONから各要素を抽出）
                stage_f_text_ocr = None
                stage_f_layout_ocr = None
                stage_f_visual_elements = None
                try:
                    if vision_raw and stage_f_structure:
                        # full_text を text OCR として保存
                        stage_f_text_ocr = self._sanitize_text(stage_f_structure.get('full_text', ''))

                        # sections/tables の取得
                        layout_info = stage_f_structure.get('layout_info', {})
                        sections = layout_info.get('sections', [])
                        tables = stage_f_structure.get('tables', [])

                        stage_f_layout_ocr = json.dumps({
                            'sections': sections,
                            'tables': tables
                        }, ensure_ascii=False, indent=2)

                        # visual_elements をそのまま保存
                        stage_f_visual_elements = json.dumps(
                            stage_f_structure.get('visual_elements', {}),
                            ensure_ascii=False,
                            indent=2
                        )
                except Exception as e:
                    logger.warning(f"[DB保存警告] Stage F出力のパースに失敗: {e}")

                # Stage Eが空の場合、Stage Fのfull_textを使用
                if not sanitized_extracted_text and stage_f_text_ocr:
                    logger.info("[DB保存] Stage Eが空のため、Stage Fのfull_textを使用")
                    sanitized_extracted_text = stage_f_text_ocr

                # Stage F アンカー配列を取得
                stage_f_anchors = None
                if stage_f_structure and 'anchors' in stage_f_structure:
                    stage_f_anchors = stage_f_structure.get('anchors', [])

                # Ver 9.0: Stage G結果はStage F内部で処理済み（G3→G4→G5→G6）
                # quality_detail と anomaly_report を保存
                stage_g_result_json = {
                    'quality_detail': stage_f_structure.get('quality_detail', {}),
                    'anomaly_report': stage_f_structure.get('anomaly_report', []),
                    'change_log': stage_f_structure.get('change_log', []),
                    'schema_version': stage_f_structure.get('schema_version', '')
                }

                # Stage H1 結果を取得
                stage_h1_tables_json = None
                if 'h1_result' in dir() and h1_result:
                    stage_h1_tables_json = {
                        'processed_tables': h1_result.get('processed_tables', []),
                        'extracted_metadata': h1_result.get('extracted_metadata', {}),
                        'statistics': h1_result.get('statistics', {})
                    }

                # titleをサニタイズ
                sanitized_title = self._sanitize_text(title)

                # attachment_text の決定ロジック
                # - Stage Eが正当にテキストを抽出した場合（sanitized_combined_text が空でない）→ 使用（正当な上書き）
                # - Stage Eが失敗した場合（sanitized_combined_text が空）→ 既存値を保持（nullで上書きしない）
                final_attachment_text = sanitized_combined_text
                if not sanitized_combined_text and existing_attachment_text:
                    final_attachment_text = existing_attachment_text
                    logger.info(f"[DB保存] Stage Eが空のため、既存attachment_textを保持: {len(final_attachment_text)}文字")

                # metadata のマージロジック
                # 既存の metadata（message_id, thread_id, subject など）を保持しつつ、
                # Stage H の metadata（LLMが生成した構造化データ）を追加
                final_metadata = {}
                if existing_document_id and existing_metadata:
                    # 既存の metadata をベースにする
                    final_metadata = existing_metadata.copy()
                    logger.info(f"[DB保存] 既存metadataを保持: {list(existing_metadata.keys())}")
                # Stage H の metadata を追加・更新
                if stageH_metadata:
                    final_metadata.update(stageH_metadata)
                    logger.info(f"[DB保存] Stage H metadataをマージ: {list(stageH_metadata.keys())}")

                doc_data = {
                    'source_id': source_id,
                    'source_type': 'unified_pipeline',
                    'file_name': file_name,
                    'workspace': workspace,
                    'doc_type': doc_type,
                    'title': sanitized_title,
                    'attachment_text': final_attachment_text,
                    'summary': sanitized_summary,
                    'tags': tags,
                    'document_date': document_date,
                    'metadata': final_metadata,
                    'processing_status': 'completed',
                    # 各ステージの出力を保存（新スキーマ 2026-01-27）
                    'stage_e_text': sanitized_extracted_text,  # Stage E: 物理抽出テキスト（E-1〜E-3統合）
                    'stage_f_text_ocr': stage_f_text_ocr,        # Stage F: Path A テキスト抽出
                    'stage_f_layout_ocr': stage_f_layout_ocr,    # Stage F: レイアウト情報
                    'stage_f_visual_elements': stage_f_visual_elements,  # Stage F: 視覚要素
                    'stage_f_anchors': json.dumps(stage_f_anchors, ensure_ascii=False) if stage_f_anchors else None,  # Stage F: アンカー配列
                    'stage_g_result': json.dumps(stage_g_result_json, ensure_ascii=False) if stage_g_result_json else None,  # Stage G: 統合精錬結果
                    'stage_h_normalized': reduced_text if 'reduced_text' in dir() else sanitized_combined_text,  # Stage H2: 軽量化済み入力
                    'stage_h1_tables': json.dumps(stage_h1_tables_json, ensure_ascii=False) if stage_h1_tables_json else None,  # Stage H1: 処理済み表
                    'stage_h_result': json.dumps(stageH_result, ensure_ascii=False, indent=2) if stageH_result else None,  # Stage H2: 構造化結果
                    'stage_j_chunks_json': json.dumps(chunks, ensure_ascii=False, indent=2)  # Stage J: チャンク化結果
                }

                # 既存ドキュメントの場合、display_* フィールドを保持（Gmail ingestion時に設定された値を上書きしないため）
                if existing_document_id and existing_display_fields:
                    for key, value in existing_display_fields.items():
                        if value is not None:  # Noneでない値のみ保持
                            doc_data[key] = value
                    logger.debug(f"[DB保存] display_*フィールドを保持: {list(existing_display_fields.keys())}")

                # extra_metadata をマージ
                if extra_metadata:
                    # display_*フィールドは最上位フィールドとして保存
                    display_fields = ['display_subject', 'display_sender', 'display_sender_email', 'display_sent_at', 'display_post_text', 'display_type']
                    for field in display_fields:
                        if field in extra_metadata and extra_metadata[field] is not None:
                            doc_data[field] = extra_metadata[field]
                            logger.debug(f"[DB保存] extra_metadataから{field}を設定: {extra_metadata[field]}")

                    # display_*以外のフィールドはmetadataにマージ
                    other_metadata = {k: v for k, v in extra_metadata.items() if k not in display_fields}
                    if other_metadata:
                        if isinstance(doc_data['metadata'], dict):
                            doc_data['metadata'].update(other_metadata)
                        else:
                            doc_data['metadata'] = other_metadata

                # 既存ドキュメントを更新 or 新規作成
                if existing_document_id:
                    logger.info(f"[DB更新] 既存ドキュメント更新: {existing_document_id}")
                    # IDを除外してUPDATE（IDは変更不可）
                    update_data = {k: v for k, v in doc_data.items() if k != 'id'}
                    result = self.db.client.table('Rawdata_FILE_AND_MAIL').update(update_data).eq('id', existing_document_id).execute()
                    if result.data and len(result.data) > 0:
                        document_id = result.data[0]['id']
                        logger.info(f"[DB更新完了] Rawdata_FILE_AND_MAIL ID: {document_id}")
                    else:
                        logger.error("[DB更新エラー] ドキュメント更新失敗")
                        return {'success': False, 'error': 'Document update failed'}
                else:
                    logger.info("[DB保存] 新規ドキュメント作成")
                    result = self.db.client.table('Rawdata_FILE_AND_MAIL').insert(doc_data).execute()
                    if result.data and len(result.data) > 0:
                        document_id = result.data[0]['id']
                        logger.info(f"[DB保存] Rawdata_FILE_AND_MAIL ID: {document_id}")
                    else:
                        logger.error("[DB保存エラー] ドキュメント作成失敗")
                        return {'success': False, 'error': 'Document creation failed'}

            except Exception as e:
                logger.error(f"[DB保存エラー] {e}")
                return {'success': False, 'error': str(e)}

            # ============================================
            # Stage K: Embedding
            # ============================================
            logger.info("[Stage K] ベクトル化開始...")
            if progress_callback:
                progress_callback("K")

            # 既存ドキュメントの場合は、古いチャンクを削除
            if existing_document_id:
                try:
                    logger.info(f"[Stage K] 既存チャンク削除: document_id={document_id}")
                    self.db.client.table('10_ix_search_index').delete().eq('document_id', document_id).execute()
                except Exception as e:
                    logger.warning(f"[Stage K 警告] 既存チャンク削除エラー（継続）: {e}")

            # 新しいチャンクを保存
            stage_k_result = self.stage_k.embed_and_save(document_id, chunks)

            # Stage K の結果をチェック（厳格モード: 1つでも失敗したら全体失敗）
            if not stage_k_result.get('success'):
                error_msg = f"Stage K失敗: {stage_k_result.get('failed_count', 0)}/{len(chunks)}チャンク保存失敗"
                logger.error(f"[Stage K失敗] {error_msg}")
                return {'success': False, 'error': error_msg}

            # 部分的失敗は警告として扱う（一部のチャンクは保存済み）
            failed_count = stage_k_result.get('failed_count', 0)
            saved_count = stage_k_result.get('saved_count', 0)
            if failed_count > 0:
                logger.warning(f"[Stage K警告] 部分的な失敗: {failed_count}/{len(chunks)}チャンク保存失敗（{saved_count}チャンクは保存済み）")
                # 失敗したが、一部は成功しているので継続

            logger.info(f"[Stage K完了] {stage_k_result.get('saved_count', 0)}/{len(chunks)}チャンク保存")

            # Phase 5: Execution tracking - 成功時
            if enable_execution_tracking and execution_manager and owner_id and document_id:
                import time
                duration_ms = int((time.time() - start_time) * 1000) if start_time else None

                # execution 作成（処理完了後に作成、即座に succeeded）
                try:
                    exec_ctx = execution_manager.create_execution(
                        document_id=document_id,
                        owner_id=owner_id,
                        input_text=combined_text if 'combined_text' in dir() else '',
                        model_version=stage_h_config.get('model') if 'stage_h_config' in dir() else None,
                        normalized_text=combined_text if 'combined_text' in dir() else ''
                    )
                    execution_manager.mark_succeeded(
                        execution_id=exec_ctx.execution_id,
                        result_data={
                            'summary': summary,
                            'tags': tags,
                            'document_date': document_date if 'document_date' in dir() else None,
                            'metadata': stageH_metadata if 'stageH_metadata' in dir() else {},
                            'chunks_count': stage_k_result.get('saved_count', 0)
                        },
                        processing_duration_ms=duration_ms
                    )
                    logger.info(f"[Phase 5] Execution 記録完了: {exec_ctx.execution_id[:8]}...")
                except Exception as exec_e:
                    logger.warning(f"[Phase 5] Execution 記録エラー（継続）: {exec_e}")

            return {
                'success': True,
                'document_id': document_id,
                'summary': summary,
                'tags': tags,
                'chunks_count': stage_k_result.get('saved_count', 0)
            }

        except Exception as e:
            logger.error(f"[パイプラインエラー] {e}", exc_info=True)

            # Phase 5: Execution tracking - 失敗時
            if enable_execution_tracking and execution_manager and owner_id:
                import time
                duration_ms = int((time.time() - start_time) * 1000) if start_time else None
                try:
                    # 既存 document_id がある場合のみ execution を記録
                    doc_id = existing_document_id or (document_id if 'document_id' in dir() else None)
                    if doc_id:
                        exec_ctx = execution_manager.create_execution(
                            document_id=doc_id,
                            owner_id=owner_id,
                            input_text='',  # 失敗時は入力が不明な場合がある
                            model_version=None
                        )
                        execution_manager.mark_failed(
                            execution_id=exec_ctx.execution_id,
                            error_code='PIPELINE_ERROR',
                            error_message=str(e),
                            processing_duration_ms=duration_ms
                        )
                        logger.info(f"[Phase 5] 失敗 Execution 記録: {exec_ctx.execution_id[:8]}...")
                except Exception as exec_e:
                    logger.warning(f"[Phase 5] 失敗 Execution 記録エラー（継続）: {exec_e}")

            return {'success': False, 'error': str(e)}
