"""
統合ドキュメント処理パイプライン (Stage E-K) - 設定ベース版

設計書: DESIGN_UNIFIED_PIPELINE.md v2.0 に準拠
処理順序: Stage E → F → G → H → I → J → K

特徴:
- doc_type / workspace に応じて自動的にプロンプトとモデルを切り替え
- config/ 内の YAML と Markdown ファイルで設定管理
"""
from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger

from C_ai_common.llm_client.llm_client import LLMClient
from A_common.database.client import DatabaseClient
from A_common.connectors.google_drive import GoogleDriveConnector

from .config_loader import ConfigLoader
from .stage_e_preprocessing import StageEPreprocessor
from .stage_f_visual import StageFVisualAnalyzer
from .stage_h_structuring import StageHStructuring
from .stage_h_kakeibo import StageHKakeibo
from .stage_i_synthesis import StageISynthesis
from .stage_j_chunking import StageJChunking
from .stage_k_embedding import StageKEmbedding

# 家計簿専用のDB保存ハンドラー (オプショナル)
try:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    from K_kakeibo.kakeibo_db_handler import KakeiboDBHandler
    KAKEIBO_AVAILABLE = True
except ImportError:
    logger.warning("K_kakeibo module not available, kakeibo features will be disabled")
    KakeiboDBHandler = None
    KAKEIBO_AVAILABLE = False


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
        self.stage_e = StageEPreprocessor(self.llm_client)
        self.stage_f = StageFVisualAnalyzer(self.llm_client, enable_hybrid_ocr=enable_hybrid_ocr)
        self.stage_h = StageHStructuring(self.llm_client)
        self.stage_h_kakeibo = StageHKakeibo(self.db)  # 家計簿専用Stage H
        self.stage_i = StageISynthesis(self.llm_client)
        self.stage_j = StageJChunking()
        self.stage_k = StageKEmbedding(self.llm_client, self.db)

        # 家計簿専用のDB保存ハンドラー
        self.kakeibo_db_handler = KakeiboDBHandler(self.db) if KAKEIBO_AVAILABLE else None

        logger.info(f"✅ UnifiedDocumentPipeline 初期化完了（設定ベース, ハイブリッドOCR={'有効' if enable_hybrid_ocr else '無効'}）")

    async def process_document(
        self,
        file_path: Path,
        file_name: str,
        doc_type: str,
        workspace: str,
        mime_type: str,
        source_id: str,
        existing_document_id: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None
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

        Returns:
            処理結果 {'success': bool, 'document_id': str, ...}
        """
        try:
            logger.info(f"📄 ドキュメント処理開始: {file_name} (doc_type={doc_type}, workspace={workspace})")

            # ============================================
            # Stage E: Pre-processing
            # ============================================
            logger.info("[Stage E] Pre-processing開始...")

            # extra_metadata から既に抽出済みのテキスト（attachment_text）を取得
            # HTMLファイル等、Ingestion時にテキスト抽出済みの場合に使用
            pre_extracted_text = extra_metadata.get('attachment_text', '') if extra_metadata else ''

            stage_e_result = self.stage_e.extract_text(
                file_path,
                mime_type,
                pre_extracted_text=pre_extracted_text
            )

            # Stage E の結果をチェック
            if not stage_e_result.get('success'):
                error_msg = f"Stage E失敗: {stage_e_result.get('error', 'テキスト抽出エラー')}"
                logger.error(f"[Stage E失敗] {error_msg}")
                return {'success': False, 'error': error_msg}

            extracted_text = stage_e_result.get('content', '')
            # ログ出力は Stage E 内で既に実施済み

            # ============================================
            # Stage F: Visual Analysis (gemini-2.5-pro で完璧に仕上げる)
            # ============================================
            # 設定から Stage F のプロンプトとモデルを取得
            stage_f_config = self.config.get_stage_config('stage_f', doc_type, workspace)
            prompt_f = stage_f_config['prompt']
            model_f = stage_f_config['model']

            logger.info(f"[Stage F] Visual Analysis開始... (model={model_f})")
            vision_raw = self.stage_f.process(
                file_path=file_path,
                prompt=prompt_f,
                model=model_f,
                extracted_text=extracted_text,
                workspace=workspace
            )
            logger.info(f"[Stage F完了] Vision結果: {len(vision_raw)}文字")

            # ============================================
            # Stage F 結果パース: JSON から構造化情報を取得
            # ============================================
            import json
            try:
                vision_json = json.loads(vision_raw)
                ocr_text = vision_json.get('full_text', '')
                stage_f_structure = {
                    'sections': vision_json.get('layout_info', {}).get('sections', []),
                    'tables': vision_json.get('layout_info', {}).get('tables', []),
                    'visual_elements': vision_json.get('visual_elements', {}),
                    'full_text': ocr_text
                }

                # combined_textの構築（複数ソースから統合）
                text_parts = []

                # 1. 投稿文テキスト（Classroom等のメタデータから）
                if extra_metadata:
                    display_post_text = extra_metadata.get('display_post_text', '')
                    if display_post_text and display_post_text.strip():
                        text_parts.append(f"[投稿文]\n{display_post_text}")
                        logger.info(f"[Stage F→H] display_post_text追加: {len(display_post_text)}文字")

                # 2. OCR抽出テキスト
                if ocr_text and ocr_text.strip():
                    text_parts.append(f"[OCR抽出テキスト]\n{ocr_text}")

                # 3. 画像の視覚的説明（visual_elements.notes）
                visual_elements = vision_json.get('visual_elements', {})
                notes = visual_elements.get('notes', [])
                if notes:
                    notes_text = '\n'.join(notes)
                    text_parts.append(f"[画像の視覚的説明]\n{notes_text}")
                    logger.info(f"[Stage F→H] visual_elements.notes追加: {len(notes_text)}文字")

                # 統合テキスト生成
                combined_text = '\n\n'.join(text_parts)

                logger.info(f"[Stage F→H] 構造化情報を抽出:")
                logger.info(f"  ├─ combined_text: {len(combined_text)}文字")
                logger.info(f"  ├─ OCR full_text: {len(ocr_text)}文字")
                logger.info(f"  ├─ sections: {len(stage_f_structure.get('sections', []))}個")
                logger.info(f"  └─ tables: {len(stage_f_structure.get('tables', []))}個")
            except json.JSONDecodeError as e:
                logger.warning(f"[Stage F→H] JSON解析失敗: {e}")
                combined_text = vision_raw
                stage_f_structure = None

            # 空のコンテンツをチェック（空のドキュメントは警告のみ、エラーではない）
            if not combined_text or not combined_text.strip():
                logger.warning(f"[Stage F→H] 統合テキストが空です（テキストのないドキュメントの可能性）")
                combined_text = ""  # 空文字列として継続

            # ============================================
            # Stage H: Structuring
            # ============================================
            # 設定から Stage H のプロンプトとモデルを取得
            stage_h_config = self.config.get_stage_config('stage_h', doc_type, workspace)
            custom_handler = stage_h_config.get('custom_handler')

            # 家計簿専用処理の場合
            if custom_handler == 'kakeibo':
                logger.info(f"[Stage H] 家計簿構造化開始... (custom_handler=kakeibo)")

                # Stage F の出力を辞書に変換（combined_text が JSON 文字列の場合）
                import json
                import re
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

                # 家計簿専用のDB保存
                if self.kakeibo_db_handler:
                    logger.info("[DB保存] 家計簿データをDBに保存...")
                    kakeibo_save_result = self.kakeibo_db_handler.save_receipt(
                        stage_h_output=stageH_result,
                        file_name=file_name,
                        drive_file_id=source_id,
                        model_name=stage_h_config['model'],
                        source_folder=workspace
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

            # 通常の Stage H 処理
            else:
                prompt_h = stage_h_config['prompt']
                model_h = stage_h_config['model']

                logger.info(f"[Stage H] 構造化開始... (model={model_h})")
                stageH_result = self.stage_h.process(
                    file_name=file_name,
                    doc_type=doc_type,
                    workspace=workspace,
                    combined_text=combined_text,
                    prompt=prompt_h,
                    model=model_h,
                    stage_f_structure=stage_f_structure  # 構造化情報を渡す
                )

                # Stage H の結果をチェック
                if not stageH_result or not isinstance(stageH_result, dict):
                    error_msg = "Stage H失敗: 構造化結果が不正です"
                    logger.error(f"[Stage H失敗] {error_msg}")
                    return {'success': False, 'error': error_msg}

                # フォールバック結果の処理（テキストが空のドキュメントの場合）
                stageH_metadata = stageH_result.get('metadata', {})
                if stageH_metadata.get('extraction_failed'):
                    logger.warning("[Stage H警告] テキストが空のドキュメントです（フォールバック結果を使用）")
                    # エラーではなく、空のメタデータとして継続

                document_date = stageH_result.get('document_date')
                tags = stageH_result.get('tags', [])
                logger.info(f"[Stage H完了]")

            # ============================================
            # Stage I: Synthesis
            # ============================================
            # 設定から Stage I のプロンプトとモデルを取得
            stage_i_config = self.config.get_stage_config('stage_i', doc_type, workspace)

            # skip フラグがある場合はスキップ
            if stage_i_config.get('skip'):
                logger.info("[Stage I] スキップ (skip=true)")
                summary = ""
                relevant_date = None
            else:
                prompt_i = stage_i_config['prompt']
                model_i = stage_i_config['model']

                logger.info(f"[Stage I] 統合・要約開始... (model={model_i})")
                stageI_result = self.stage_i.process(
                    combined_text=combined_text,
                    stageH_result=stageH_result,
                    prompt=prompt_i,
                    model=model_i
                )

                # Stage I の結果をチェック
                if not stageI_result or not isinstance(stageI_result, dict):
                    error_msg = "Stage I失敗: 統合・要約結果が不正です"
                    logger.error(f"[Stage I失敗] {error_msg}")
                    return {'success': False, 'error': error_msg}

                title = stageI_result.get('title', '')
                summary = stageI_result.get('summary', '')

                # フォールバック結果の処理（テキストが空のドキュメントの場合）
                if summary == '処理に失敗しました':
                    logger.warning("[Stage I警告] テキストが空のドキュメントです（フォールバック結果を使用）")
                    summary = ''  # 空の要約として継続

                relevant_date = stageI_result.get('relevant_date')

                # カレンダーイベントとタスクを取得
                calendar_events = stageI_result.get('calendar_events', [])
                tasks = stageI_result.get('tasks', [])

                # metadataに追加
                stageH_metadata['calendar_events'] = calendar_events
                stageH_metadata['tasks'] = tasks

                logger.info(f"[Stage I完了] calendar_events={len(calendar_events)}件, tasks={len(tasks)}件")

                # ============================================
                # Google Drive ファイル名更新（タイトルに基づく）
                # ============================================
                if title and source_id:
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
            chunks = self.stage_j.process(
                display_subject=extra_metadata.get('display_subject', file_name) if extra_metadata else file_name,
                summary=summary,
                tags=tags,
                document_date=document_date,
                metadata=stageH_metadata
            )
            logger.info(f"[Stage J完了] チャンク数: {len(chunks)}")

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
                                import json
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
                        # sections + tables を layout OCR として保存
                        import json
                        stage_f_layout_ocr = json.dumps({
                            'sections': stage_f_structure.get('sections', []),
                            'tables': stage_f_structure.get('tables', [])
                        }, ensure_ascii=False, indent=2)
                        # visual_elements をそのまま保存
                        stage_f_visual_elements = json.dumps(
                            stage_f_structure.get('visual_elements', {}),
                            ensure_ascii=False,
                            indent=2
                        )
                except Exception as e:
                    logger.warning(f"[DB保存警告] Stage F出力のパースに失敗: {e}")

                # Stage Eが空の場合、Stage Fのfull_textをE4/E5に使用
                if not sanitized_extracted_text and stage_f_text_ocr:
                    logger.info("[DB保存] Stage Eが空のため、Stage Fのfull_textをE4/E5に使用")
                    sanitized_extracted_text = stage_f_text_ocr

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
                    # 各ステージの出力を保存
                    # E1-E3: 現在は未実装のため、E4と同じ値を保存（将来的に個別エンジンを実装予定）
                    'stage_e1_text': sanitized_extracted_text,  # Stage E-1: PyPDF2（未実装、E4の値を使用）
                    'stage_e2_text': sanitized_extracted_text,  # Stage E-2: pdfminer（未実装、E4の値を使用）
                    'stage_e3_text': sanitized_extracted_text,  # Stage E-3: PyMuPDF（未実装、E4の値を使用）
                    'stage_e4_text': sanitized_extracted_text,  # Stage E-4: pdfplumber/画像OCR
                    'stage_e5_text': sanitized_extracted_text,  # Stage E-5: 最終統合（現在はE4と同じ）
                    'stage_f_text_ocr': stage_f_text_ocr,        # Stage F: Text OCR
                    'stage_f_layout_ocr': stage_f_layout_ocr,    # Stage F: Layout OCR
                    'stage_f_visual_elements': stage_f_visual_elements,  # Stage F: Visual Elements
                    'stage_h_normalized': sanitized_combined_text,  # Stage H への入力テキスト
                    'stage_i_structured': json.dumps(stageH_result, ensure_ascii=False, indent=2) if stageH_result else None,  # Stage H の出力
                    'stage_j_chunks_json': json.dumps(chunks, ensure_ascii=False, indent=2)  # Stage J の出力
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

            return {
                'success': True,
                'document_id': document_id,
                'summary': summary,
                'tags': tags,
                'chunks_count': stage_k_result.get('saved_count', 0)
            }

        except Exception as e:
            logger.error(f"[パイプラインエラー] {e}", exc_info=True)
            return {'success': False, 'error': str(e)}
