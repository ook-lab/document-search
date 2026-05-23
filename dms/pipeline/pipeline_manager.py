"""
パイプライン管理モジュール

PipelineManager クラスを提供
全ステージ（A→B→D→E→F）と 09 統一書き込みまでの実行管理（検索用チャンク・埋め込みは本クラス外）

役割：
- パイプライン実行のオーケストレーション
- `pipeline_meta` から raw を参照してジョブ実行（キュー dequeue/ack/nack・進捗更新は行わない）
- StateManager（processing_lock）
"""
import asyncio
import mimetypes
import os
import re
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from dms.common.database.client import DatabaseClient
from dms.common.connectors.google_drive import GoogleDriveConnector
from dms.logging import TaskLogger  # Per-Task Logging

# 新しいステージベース（A→B→D→E→F → 09 統一書き込み）
from dms.pipeline.stage_a import A3EntryPoint
from dms.pipeline.stage_b import B1Controller
from dms.pipeline.stage_d import D1Controller
from dms.pipeline.stage_e import E1Controller
from dms.pipeline.stage_f import F1Controller
from dms.pipeline.stage_g import G11Controller
from dms.pipeline.stage_g.g31_unified_writer import G31UnifiedWriter

from dms.processing.state_manager import get_state_manager
from dms.processing.execution_policy import ExecutionPolicy, get_execution_policy


class PipelineManager:
    """
    パイプライン管理クラス

    全ステージ（A→B→D→E→F）の実行を統括
    StateManagerを通じて状態を一元管理
    """

    VIDEO_EXTENSIONS = ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv', '.m4v', '.mpeg', '.mpg']

    def __init__(self, use_service_role: bool = False, db: DatabaseClient = None):
        """
        PipelineManager の初期化

        Args:
            use_service_role: Trueの場合、Service Role Keyを使用（RLSをバイパス）
                              Worker実行時は必ずTrueを指定すること
            db: 外部から注入する DatabaseClient（指定された場合は use_service_role は無視）
        """
        # 外部注入されたDBがあればそれを使用、なければ新規作成
        if db is not None:
            self.db = db
        else:
            self.db = DatabaseClient(use_service_role=use_service_role)

        # パイプライン初期化: A→B→D→E→F（H/I は本オーケストレータ外。09 統一書き込みまで）
        # Stage A-F: 新しいアーキテクチャ
        self.stage_a = A3EntryPoint()
        self.stage_b = B1Controller()
        self.stage_d = D1Controller()
        self.stage_e = E1Controller()
        self.stage_f = F1Controller()
        # レビュー用 ui_data（G11）は F 完了後に document_id 付きで都度インスタンス化

        logger.info("✅ パイプライン初期化完了: A→B→D→E→F（+ 09 統一書き込み）")

        self.drive = GoogleDriveConnector()
        self.temp_dir = Path("./temp")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # StateManagerを取得（SSOT）
        self.state_manager = get_state_manager()

        # ExecutionPolicy（実行可否判断のSSOT）
        self.execution_policy = get_execution_policy()

        # loguruハンドラーID（将来のバッチ UI ログ転送用に予約）
        self._log_handler_id = None

    def get_document_by_id(self, doc_id: str) -> Dict[str, Any] | None:
        """pipeline_meta.id でドキュメントを取得"""
        try:
            result = self.db.client.table('pipeline_meta').select('*').eq('id', doc_id).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"get_document_by_id error: {e}")
            return None

    async def process_single_document(self, doc_id: str, preserve_workspace: bool = True) -> bool:
        """
        pipeline_meta.id で単一ドキュメントを処理（CLI用）

        Args:
            doc_id: pipeline_meta.id
            preserve_workspace: 未使用（後方互換のため残す）

        Returns:
            処理成功ならTrue
        """
        policy_result = self.execution_policy.can_execute(doc_id=doc_id)
        if not policy_result.allowed:
            logger.warning(f"実行拒否: {policy_result.deny_code} - {policy_result.deny_reason}")
            return False

        if not self.state_manager.start_processing(1):
            logger.warning("処理開始に失敗しました（既に処理中か、ロック取得失敗）")
            return False

        try:
            return await self.process_pipeline_meta_job(doc_id)
        finally:
            self.state_manager.finish_processing()

    # ------------------------------------------------------------------
    # Stage D / E マルチページ結果マージ
    # ------------------------------------------------------------------

    @staticmethod
    @staticmethod
    def _strip_sandwich_layer(pdf_path: Path, work_dir: Path) -> Path:
        """MD_SANDWICH不可視テキスト層がある場合は除去したPDFを返す。なければそのまま返す。"""
        MARKER_START = '<<<MD_SANDWICH_START>>>'
        MARKER_END = '<<<MD_SANDWICH_END>>>'
        try:
            import fitz
            doc = fitz.open(str(pdf_path))
            modified = False
            for page in doc:
                text = page.get_text()
                if MARKER_START not in text or MARKER_END not in text:
                    continue
                # fontsize=6 のスパン（サンドイッチ専用フォント）だけ redact
                for block in page.get_text('dict')['blocks']:
                    if block.get('type') != 0:
                        continue
                    for line in block['lines']:
                        for span in line['spans']:
                            if abs(span.get('size', 0) - 6.0) < 0.5:
                                page.add_redact_annot(fitz.Rect(span['bbox']))
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=False)
                modified = True
                logger.info(f"[PM] MD_SANDWICH層を除去しました: {pdf_path.name}")
            if modified:
                clean_path = work_dir / f'_clean_{pdf_path.name}'
                doc.save(str(clean_path))
                doc.close()
                return clean_path
            doc.close()
        except Exception as e:
            logger.warning(f"[PM] MD_SANDWICH除去失敗（スキップ）: {e}")
        return pdf_path

    def _metadata_process_pdf_page_index(pipeline_meta: Optional[Dict[str, Any]]) -> Optional[int]:
        """
        pipeline_meta.metadata.process_pdf_page_index（0始まり）があればそのページだけを処理する。

        UI / API は pipeline_meta 行の metadata JSON に
        {"process_pdf_page_index": <int>} を書き込む。
        """
        if not pipeline_meta:
            return None
        md = pipeline_meta.get('metadata')
        if not isinstance(md, dict):
            return None
        raw = md.get('process_pdf_page_index')
        if raw is None:
            return None
        try:
            idx = int(raw)
        except (TypeError, ValueError):
            logger.warning(f"[PM] metadata.process_pdf_page_index が整数ではありません: {raw!r}")
            return None
        return idx

    @staticmethod
    def _merge_d_results(d_results: list) -> dict:
        """
        複数ページの D 結果を E1Controller が読める単一 dict にまとめる。

        - tables: 全ページ分を結合（各 table に image_path あり → E がそのまま処理）
        - non_table_image_paths: ページごとの背景画像パスリスト
          （E1Controller はこのリストをループして非表領域を処理する）
        - non_table_image_path: 後方互換のため先頭ページ分を残す
        """
        if not d_results:
            return {}
        if len(d_results) == 1:
            d = dict(d_results[0])
            # 単一ページでもリスト形式を追加
            if d.get('non_table_image_path'):
                d['non_table_image_paths'] = [d['non_table_image_path']]
            return d

        base = dict(d_results[0])
        all_tables = []
        non_table_image_paths = []

        for page_idx, dr in enumerate(d_results):
            page_index = dr.get('page_index', page_idx)
            for table in (dr.get('tables', []) or []):
                t = dict(table)
                # ページ番号をtable_idに付与して一意化（例: D1 → P0_D1）
                t['table_id'] = f"P{page_index}_{t.get('table_id', 'D1')}"
                all_tables.append(t)
            img = dr.get('non_table_image_path')
            if img:
                non_table_image_paths.append(img)

        base['tables'] = all_tables
        base['non_table_image_paths'] = non_table_image_paths
        base['page_count'] = len(d_results)
        return base

    @staticmethod
    def _split_pdf_single_page(src_pdf: Path, page_index: int, dest_pdf: Path) -> None:
        """元 PDF の1ページだけを dest に書き出す（PyMuPDF）。

        insert_pdf だけの新規ドキュメントは Info 辞書が空になりやすく、
        Stage A のメタデータ判定が UNKNOWN/LOW → Gatekeeper 遮断になる。
        元 PDF のドキュメントメタデータを可能な範囲で引き継ぐ。
        """
        import fitz

        _META_KEYS = (
            'format', 'title', 'author', 'subject', 'keywords',
            'creator', 'producer', 'creationDate', 'modDate', 'trapped',
        )

        src = fitz.open(str(src_pdf))
        sub = fitz.open()
        if page_index < 0 or page_index >= len(src):
            src.close()
            sub.close()
            raise ValueError(f"page_index out of range: {page_index} (pages={len(src)})")
        sub.insert_pdf(src, from_page=page_index, to_page=page_index)
        try:
            raw = src.metadata or {}
            to_set = {
                k: str(raw[k]).strip()
                for k in _META_KEYS
                if k in raw and raw[k] is not None and str(raw[k]).strip()
            }
            if to_set:
                sub.set_metadata(to_set)
                logger.debug(
                    f"[_split_pdf_single_page] copied {len(to_set)} metadata field(s) onto single-page slice"
                )
        except Exception as e:
            logger.warning(f"[_split_pdf_single_page] metadata copy failed (non-fatal): {e}")
        src.close()
        dest_pdf.parent.mkdir(parents=True, exist_ok=True)
        sub.save(str(dest_pdf))
        sub.close()

    _G_PIPELINE_META_KEYS = ('g11_output', 'g14_output', 'g17_output', 'g21_output', 'g22_output')

    @classmethod
    def _g_intermediate_subset(cls, final_metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """レビューUI（g11〜g22 キー）の中間出力だけを 09_unified_documents.meta 用に抽出する。"""
        if not final_metadata:
            return {}
        return {k: final_metadata[k] for k in cls._G_PIPELINE_META_KEYS if final_metadata.get(k)}

    def _merge_g_outputs_into_unified_meta(self, unified_doc_id: str, final_metadata: Dict[str, Any]) -> None:
        g_meta = self._g_intermediate_subset(final_metadata)
        if not g_meta:
            return
        try:
            existing = self.db.client.table('09_unified_documents').select('meta').eq('id', unified_doc_id).execute()
            current_meta = (existing.data[0].get('meta') or {}) if existing.data else {}
            self.db.client.table('09_unified_documents').update(
                {'meta': {**current_meta, **g_meta}}
            ).eq('id', unified_doc_id).execute()
            logger.info('[PM-09] レビューUI中間メタを 09_unified_documents.meta にマージ完了')
        except Exception as e:
            logger.warning(f'[PM-09] 09_unified_documents.meta マージエラー（継続）: {e}')

    def _write_unified_09(
        self,
        raw_doc: Dict[str, Any],
        raw_table: str,
        ui_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        return G31UnifiedWriter(self.db).process(raw_data=raw_doc, raw_table=raw_table, ui_data=ui_data)

    def _report_stage_progress(self, progress: float, log_message: str = None):
        """進捗は StateManager（メモリ→processing_lock 同期）のみ。pipeline_meta のキュー列は更新しない。"""
        if log_message:
            self.state_manager.update_progress(stage=log_message, stage_progress=progress)

    @staticmethod
    def _map_raw_to_pipeline_doc(raw_doc: dict, raw_table: str) -> dict:
        """
        raw テーブルのレコードをパイプライン互換形式に変換。

        パイプラインステージ（Stage A-G）は display_* / doc_type / person 等の
        フィールドを参照するため、各 raw テーブルのカラムをマッピングする。
        """
        if raw_table == '05_ikuya_waseaca_01_raw':
            return {
                'id':                raw_doc.get('id'),
                'file_name':         raw_doc.get('file_name'),
                'file_url':          raw_doc.get('file_url'),
                'title':             raw_doc.get('title', ''),
                'display_subject':   raw_doc.get('title', ''),
                'display_post_text': raw_doc.get('description', ''),
                'display_sender':    raw_doc.get('creator_name', ''),
                'display_sent_at':   raw_doc.get('created_at'),
                'doc_type':          raw_doc.get('source', '早稲アカオンライン'),
                'person':            raw_doc.get('person'),
                'organizations':     None,
                'workspace':         'waseda_academy',
                'mimeType':          None,
                'screenshot_url':    None,
            }
        # デフォルト: そのまま返す（将来の raw テーブル追加に備え）
        return dict(raw_doc)

    async def process_pipeline_meta_job(self, meta_id: str) -> bool:
        """
        pipeline_meta 行をルーティング情報として A→F→09 を実行する。

        pipeline_meta の processing_status / リース / dequeue・ack・nack は更新しない。
        """
        import shutil
        import gc

        # --------------------------------------------------
        # 1. pipeline_meta 取得
        # --------------------------------------------------
        try:
            meta_res = self.db.client.table('pipeline_meta').select('*').eq('id', meta_id).single().execute()
            meta = meta_res.data
            if not meta:
                logger.error(f"[PM] pipeline_meta not found: {meta_id}")
                return False
        except Exception as e:
            logger.error(f"[PM] pipeline_meta 取得エラー: {e}")
            return False

        raw_id    = meta['raw_id']
        raw_table = meta['raw_table']

        # --------------------------------------------------
        # 2. raw テーブルから文書データ取得
        # --------------------------------------------------
        try:
            raw_res = self.db.client.table(raw_table).select('*').eq('id', str(raw_id)).single().execute()
            raw_doc = raw_res.data
            if not raw_doc:
                logger.error(f"[PM] raw_doc not found: {raw_table}/{raw_id}")
                return False
        except Exception as e:
            logger.error(f"[PM] raw_doc 取得エラー: {e}")
            return False

        doc       = self._map_raw_to_pipeline_doc(raw_doc, raw_table)
        file_name = doc.get('file_name', 'unknown')
        title     = doc.get('title', '') or '(タイトル未生成)'

        logger.info(f"[PM] ジョブ処理開始: meta_id={meta_id}")
        logger.info(f"[PM]   raw_table={raw_table}, raw_id={raw_id}")
        logger.info(f"[PM]   title={title}, file_url={doc.get('file_url')}")

        doc_temp_dir = self.temp_dir / str(raw_id)
        doc_temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            # --------------------------------------------------
            # 添付ファイルあり / なし で分岐
            # --------------------------------------------------
            if doc.get('file_url'):
                result = await self._process_pipeline_meta_with_attachment(
                    doc, meta_id, raw_table, raw_doc, doc_temp_dir, pipeline_meta=meta
                )
            else:
                result = await self._process_pipeline_meta_text_only(
                    doc, meta_id, raw_table, raw_doc
                )

            if result.get('success'):
                logger.info(f"[PM] ジョブ完了: meta_id={meta_id}")
                return True
            else:
                error = result.get('error') or ''
                logger.error(f"[PM] ジョブ失敗: meta_id={meta_id} - {error}")
                return False

        except Exception as e:
            logger.error(f"[PM] 例外: {e}", exc_info=True)
            return False

        finally:
            if doc_temp_dir.exists():
                try:
                    shutil.rmtree(doc_temp_dir)
                    logger.info(f"[PM] temp 削除完了: {doc_temp_dir}")
                except Exception as cleanup_err:
                    gc.collect()
                    try:
                        shutil.rmtree(doc_temp_dir)
                    except Exception:
                        logger.warning(f"[PM] temp 削除失敗（無視）: {cleanup_err}")

    async def _process_pipeline_meta_with_attachment(
        self,
        doc: Dict[str, Any],
        meta_id: str,
        raw_table: str,
        raw_doc: Dict[str, Any],
        doc_temp_dir: Path,
        pipeline_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        添付ファイルあり（PDF）のパイプライン処理（pipeline_meta 向け）

        レビューUI中間を pipeline_meta に保存し、
        09_unified_documents へ raw_table 指定で書き込む。

        pipeline_meta.metadata.process_pdf_page_index（0始まり）が設定されている場合、
        ダウンロードした PDF のうちその1ページだけを切り出し、A→F は常にその1枚のみを入力とする
        （全文書を分析してから1ページを使う動作ではない）。
        """
        file_name = doc.get('file_name', 'unknown')
        file_url  = doc.get('file_url', '')

        # Drive ファイルID を抽出
        drive_file_id = None
        if file_url:
            match = re.search(r'/d/([a-zA-Z0-9_-]+)', file_url)
            if match:
                drive_file_id = match.group(1)

        if not drive_file_id:
            return {'success': False, 'error': 'file_url から Drive ファイルID を取得できません'}

        file_extension = Path(file_name).suffix.lower()
        if file_extension in self.VIDEO_EXTENSIONS:
            logger.info(f"[PM] 動画ファイルをスキップ: {file_name}")
            return {'success': True}

        # ダウンロード
        self._report_stage_progress(0.1, 'ダウンロード')
        try:
            self.drive.download_file(drive_file_id, file_name, str(doc_temp_dir))
            local_path = doc_temp_dir / file_name
        except Exception as e:
            error_str = str(e)
            if 'File not found' in error_str or '404' in error_str:
                logger.warning(f"[PM] Drive にファイルなし。テキストのみ処理にフォールバック")
                return await self._process_pipeline_meta_text_only(doc, meta_id, raw_table, raw_doc)
            return {'success': False, 'error': f'ダウンロード失敗: {e}'}

        mime_type = doc.get('mimeType')
        if not mime_type:
            mime_type, _ = mimetypes.guess_type(file_name)
        if not mime_type:
            mime_type = 'application/octet-stream'

        try:
            _b_log_dir = doc_temp_dir

            working_pdf_path = self._strip_sandwich_layer(local_path, doc_temp_dir)
            selected_source_page_0 = self._metadata_process_pdf_page_index(pipeline_meta)

            if file_extension == '.pdf':
                try:
                    import fitz
                except ImportError:
                    return {
                        'success': False,
                        'error': 'PAGE_BY_PAGE_REQUIRES_PYMUPDF: install PyMuPDF (fitz)',
                    }
                probe = fitz.open(str(local_path))
                total_src_pages = len(probe)
                probe.close()
                if total_src_pages == 0:
                    return {'success': False, 'error': 'PDF にページがありません'}
                if total_src_pages > 1:
                    if selected_source_page_0 is None:
                        return {
                            'success': False,
                            'error': (
                                'MULTI_PAGE_PDF_FORBIDDEN: 1ジョブでは複数ページの PDF を扱いません。'
                                'pipeline_meta.metadata.process_pdf_page_index に '
                                f'0〜{total_src_pages - 1} の整数（処理する1ページのインデックス）を設定してください。'
                            ),
                        }
                    if selected_source_page_0 < 0 or selected_source_page_0 >= total_src_pages:
                        return {
                            'success': False,
                            'error': (
                                f'process_pdf_page_index が範囲外です: {selected_source_page_0} '
                                f'（0 以上 {total_src_pages - 1} 以下）'
                            ),
                        }
                    working_pdf_path = doc_temp_dir / 'pm_job_selected_page_input.pdf'
                    self._split_pdf_single_page(
                        local_path, selected_source_page_0, working_pdf_path
                    )
                    logger.info(
                        f'[PM] 1ページのみ処理: 元PDF index={selected_source_page_0}/'
                        f'{total_src_pages} → 1枚PDFで A→F'
                    )
                elif selected_source_page_0 is not None and selected_source_page_0 != 0:
                    return {
                        'success': False,
                        'error': (
                            f'process_pdf_page_index は1ページPDFでは 0 のみ有効です: '
                            f'{selected_source_page_0}'
                        ),
                    }

            self._report_stage_progress(0.35, '書類種別判定')
            logger.info('[PM-Stage A] 書類種別判定開始')
            stage_a_result = self.stage_a.process(str(working_pdf_path))
            if not stage_a_result or not stage_a_result.get('success'):
                detail = (stage_a_result or {}).get('error', '')
                return {
                    'success': False,
                    'error': f'Stage A失敗: {detail}' if detail else 'Stage A失敗',
                }

            self._report_stage_progress(0.40, '物理構造抽出')
            logger.info('[PM-Stage B] 物理構造抽出開始')
            stage_b_result = self.stage_b.process(
                file_path=str(working_pdf_path),
                a_result=stage_a_result,
                log_dir=_b_log_dir,
            )

            if selected_source_page_0 is not None and stage_a_result:
                stage_a_result['process_pdf_source_page_index'] = selected_source_page_0
                stage_a_result['process_pdf_job_scope'] = 'single_selected_page'

            if not stage_b_result or not stage_b_result.get('success'):
                b_error = (stage_b_result or {}).get('error') or ''
                return {'success': False, 'error': f'Stage B失敗: {b_error}'}

            purged_pdf_path = stage_b_result.get('purged_pdf_path')
            if not purged_pdf_path:
                return {'success': False, 'error': 'Stage B失敗: purged_pdf_path が生成されませんでした'}

            # Stage D / E: マルチページ
            _page_type_map = stage_a_result.get('page_type_map', {})
            if _page_type_map:
                content_pages = sorted(
                    idx for idx, ptype in _page_type_map.items()
                    if ptype != 'COVER'
                )
            else:
                content_pages = [0]

            all_d_results = []
            for page_idx, page_num in enumerate(content_pages):
                page_output_dir = doc_temp_dir / f"page_{page_num}"
                page_output_dir.mkdir(parents=True, exist_ok=True)

                progress_d = 0.45 + (page_idx / len(content_pages)) * 0.05
                self._report_stage_progress(progress_d, f'視覚構造解析 p{page_num+1}')
                logger.info(f"[PM-Stage D] ページ {page_num+1} 視覚構造解析")

                d_result = self.stage_d.process(
                    pdf_path=Path(purged_pdf_path),
                    purged_image_path=None,
                    page_num=page_num,
                    output_dir=page_output_dir,
                )
                if not d_result or not d_result.get('success'):
                    detail = (d_result or {}).get('error', '')
                    logger.warning(f"[PM-Stage D] ページ {page_num+1} 失敗: {detail} → スキップ")
                    continue
                all_d_results.append(d_result)

            if not all_d_results:
                return {'success': False, 'error': 'Stage D失敗: 全ページが失敗しました'}

            stage_d_result = self._merge_d_results(all_d_results)

            # Stage E
            self._report_stage_progress(0.50, 'AI抽出')
            logger.info(f"[PM-Stage E] AI抽出開始: {len(all_d_results)}ページ分")
            stage_e_result = self.stage_e.process(
                purged_pdf_path=purged_pdf_path,
                stage_d_result=stage_d_result,
                output_dir=doc_temp_dir,
                stage_b_result=stage_b_result,
                session_id=meta_id,
            )
            if not stage_e_result or not stage_e_result.get('success'):
                detail = (stage_e_result or {}).get('error', '')
                return {'success': False, 'error': f'Stage E失敗: {detail}' if detail else 'Stage E失敗'}

            # Stage F
            self._report_stage_progress(0.55, 'データ統合')
            logger.info("[PM-Stage F] データ統合開始")
            stage_f_result = self.stage_f.process(
                stage_a_result=stage_a_result,
                stage_b_result=stage_b_result,
                stage_d_result=stage_d_result,
                stage_e_result=stage_e_result,
                rawdata_record=doc,
                session_id=meta_id,
            )
            if not stage_f_result or not stage_f_result.get('success'):
                detail = (stage_f_result or {}).get('error', '')
                return {'success': False, 'error': f'Stage F失敗: {detail}' if detail else 'Stage F失敗'}

            # Stage G: レビュー用 ui_data（F17 統合結果が正本）
            self._report_stage_progress(0.58, 'レビューUI組立')
            logger.info("[PM-Stage F] レビュー用 ui_data 組立開始")
            ui_builder = G11Controller(document_id=meta_id)
            ui_result = ui_builder.process(stage_f_result=stage_f_result, log_dir=doc_temp_dir)
            if not ui_result or not ui_result.get('success'):
                detail = (ui_result or {}).get('error', '')
                return {'success': False, 'error': f'レビューUI組立失敗: {detail}' if detail else 'レビューUI組立失敗'}

            ui_data        = ui_result.get('ui_data', {})
            final_metadata = ui_result.get('final_metadata', {})

            if not final_metadata:
                return {'success': False, 'error': 'レビューUI組立: final_metadata が生成されませんでした'}

            u09_result = self._write_unified_09(raw_doc, raw_table, ui_data)
            if not u09_result.get('success'):
                logger.warning(f"[PM-09] 09_unified_documents 書き込み失敗: {u09_result.get('error')}")
            unified_doc_id = u09_result.get('doc_id')
            if unified_doc_id:
                self._merge_g_outputs_into_unified_meta(unified_doc_id, final_metadata)

            self._report_stage_progress(0.95, '09反映完了')
            logger.info("[PM] A→B→D→E→F と 09 反映まで成功")
            return {'success': True}

        finally:
            pass  # temp 削除は process_pipeline_meta_job の finally で行う

    async def _process_pipeline_meta_text_only(
        self,
        doc: Dict[str, Any],
        meta_id: str,
        raw_table: str,
        raw_doc: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        テキストのみドキュメントのパイプライン処理（pipeline_meta 向け）

        F 末尾の ui_data / final_metadata を pipeline_meta に保存し、
        09_unified_documents へ raw_table 指定で書き込む。
        """
        file_name = doc.get('file_name', 'text_only')

        if not any([
            doc.get('display_subject'),
            doc.get('display_post_text'),
            doc.get('display_sender'),
            doc.get('display_sent_at'),
        ]):
            return {'success': False, 'error': 'テキストが空です'}

        # Stage F（テキストのみ: A/B/D/E スキップ）
        self._report_stage_progress(0.2, 'データ統合')
        logger.info("[PM-Stage F] テキストのみ: データ統合開始")
        stage_f_result = self.stage_f.process(rawdata_record=doc, session_id=meta_id)
        if not stage_f_result or not stage_f_result.get('success'):
            detail = (stage_f_result or {}).get('error', '')
            return {'success': False, 'error': f'Stage F失敗: {detail}' if detail else 'Stage F失敗'}

        # F 末尾: レビュー用 ui_data（F60）
        self._report_stage_progress(0.40, 'レビューUI組立')
        logger.info("[PM-Stage F] テキストのみ: レビュー用 ui_data 組立開始")
        ui_builder = G11Controller(document_id=meta_id)
        ui_result = ui_builder.process(stage_f_result=stage_f_result)
        if not ui_result or not ui_result.get('success'):
            detail = (ui_result or {}).get('error', '')
            return {'success': False, 'error': f'レビューUI組立失敗: {detail}' if detail else 'レビューUI組立失敗'}

        ui_data        = ui_result.get('ui_data', {})
        final_metadata = ui_result.get('final_metadata', {})

        if not final_metadata:
            return {'success': False, 'error': 'レビューUI組立: final_metadata が生成されませんでした'}

        u09_result = self._write_unified_09(raw_doc, raw_table, ui_data)
        if not u09_result.get('success'):
            logger.warning(f"[PM-09] 09_unified_documents 書き込み失敗: {u09_result.get('error')}")
        unified_doc_id = u09_result.get('doc_id')
        if unified_doc_id:
            self._merge_g_outputs_into_unified_meta(unified_doc_id, final_metadata)

        self._report_stage_progress(0.95, '09反映完了')
        logger.info("[PM] F と 09 反映まで成功（テキストのみ）")
        return {'success': True}


# continuous_processing_loop は削除済み
# 【設計原則】常駐禁止 - キュー連携は行わない。単一 meta_id / document_id での実行は呼び出し側で行う。
