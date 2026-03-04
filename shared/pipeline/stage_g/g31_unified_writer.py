"""
G31: Unified Writer
パイプライン処理済みデータを 09_unified_documents に書き込む。
全ソース（Gmail / Calendar / Classroom / File）共通のゲート。

- Gmail / Classroom / File: A→G 経由で ui_data を受け取り書き込む
- Calendar: G スキップ、生データから軽量 ui_data を生成して書き込む
"""
from typing import Dict, Any, Optional
from loguru import logger


# ソーステーブル定数
RAW_GMAIL      = '01_gmail_01_raw'
RAW_GCAL       = '02_gcal_01_raw'
RAW_EMA_CLASS  = '03_ema_classroom_01_raw'
RAW_IKU_CLASS  = '04_ikuya_classroom_01_raw'
RAW_IKU_WASE   = '05_ikuya_waseaca_01_raw'
RAW_FILE       = '08_file_only_01_raw'

CLASSROOM_TABLES = {RAW_EMA_CLASS, RAW_IKU_CLASS, RAW_IKU_WASE}


class G31UnifiedWriter:
    """09_unified_documents への書き込みゲート"""

    def __init__(self, db_client):
        self.db = db_client

    def process(
        self,
        raw_data: Dict[str, Any],
        raw_table: str,
        ui_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        09_unified_documents に書き込む。

        Args:
            raw_data:  01_raw テーブルの1行分データ
            raw_table: 参照元テーブル名（例: '01_gmail_01_raw'）
            ui_data:   G1 出力（Calendar は None → 軽量版を自動生成）

        Returns:
            {'success': bool, 'doc_id': str}
        """
        logger.info(f"[G31] 書き込み開始: raw_table={raw_table}")

        try:
            if raw_table == RAW_GMAIL:
                doc = self._map_gmail(raw_data, ui_data)
            elif raw_table == RAW_GCAL:
                doc = self._map_gcal(raw_data)
            elif raw_table in CLASSROOM_TABLES:
                doc = self._map_classroom(raw_data, raw_table, ui_data)
            elif raw_table == RAW_FILE:
                doc = self._map_file(raw_data, ui_data)
            else:
                return {'success': False, 'error': f'未対応の raw_table: {raw_table}'}

            # 既存行があれば UPDATE、なければ INSERT
            existing = (
                self.db.client
                .table('09_unified_documents')
                .select('id')
                .eq('raw_id', str(raw_data['id']))
                .eq('raw_table', raw_table)
                .execute()
            )

            if existing.data:
                doc_id = existing.data[0]['id']
                self.db.client.table('09_unified_documents').update(doc).eq('id', doc_id).execute()
                logger.info(f"[G31] UPDATE 完了: doc_id={doc_id}")
            else:
                result = self.db.client.table('09_unified_documents').insert(doc).execute()
                doc_id = result.data[0]['id']
                logger.info(f"[G31] INSERT 完了: doc_id={doc_id}")

            return {'success': True, 'doc_id': doc_id}

        except Exception as e:
            logger.error(f"[G31] 書き込みエラー: {e}")
            return {'success': False, 'error': str(e)}

    # ------------------------------------------------------------------
    # Gmail マッパー
    # ------------------------------------------------------------------
    def _map_gmail(self, raw: Dict, ui_data: Optional[Dict]) -> Dict:
        return {
            'raw_id':     str(raw['id']),
            'raw_table':  RAW_GMAIL,
            'person':     raw.get('person'),
            'source':     raw.get('source'),
            'category':   raw.get('category'),
            'title':      raw.get('header_subject'),
            'file_url':   raw.get('source_url'),
            'from_email': raw.get('from_email'),
            'from_name':  raw.get('from_name'),
            'snippet':    raw.get('snippet'),
            'post_at':    raw.get('sent_at'),
            # NULL（Gmail にはカレンダー・課題の概念なし）
            'start_at':   None,
            'end_at':     None,
            'location':   None,
            'due_date':   None,
            'post_type':  None,
            'ui_data':    ui_data,
            'meta': {
                'thread_id':          raw.get('thread_id'),
                'header_to':          raw.get('header_to'),
                'header_cc':          raw.get('header_cc'),
                'header_in_reply_to': raw.get('header_in_reply_to'),
                'header_references':  raw.get('header_references'),
                'attachments':        raw.get('attachments'),
            },
        }

    # ------------------------------------------------------------------
    # Google Calendar マッパー（G スキップ）
    # ------------------------------------------------------------------
    def _map_gcal(self, raw: Dict) -> Dict:
        # Calendar は ui_data を G から受け取らないため、軽量版を生成
        ui_data = self._build_gcal_ui_data(raw)
        return {
            'raw_id':       str(raw['id']),
            'raw_table':    RAW_GCAL,
            'person':       raw.get('person'),
            'source':       raw.get('source'),
            'category':     raw.get('category'),
            'title':        raw.get('summary'),
            'file_url':     raw.get('source_url'),
            'from_email':   raw.get('organizer_email'),
            'from_name':    raw.get('organizer_name'),
            'snippet':      None,        # Calendar に投稿プレビューなし
            'post_at':      None,        # Calendar に投稿日時なし
            'start_at':     raw.get('start_at'),
            'end_at':       raw.get('end_at'),
            'location':     raw.get('location'),
            'due_date':     None,        # Calendar に締切なし
            'post_type':    None,
            'ui_data':      ui_data,
            'meta': {
                'attendees':          raw.get('attendees'),
                'recurrence':         raw.get('recurrence'),
                'recurring_event_id': raw.get('recurring_event_id'),
                'creator_email':      raw.get('creator_email'),
                'creator_name':       raw.get('creator_name'),
                'visibility':         raw.get('visibility'),
                'calendar_id':        raw.get('calendar_id'),
            },
        }

    def _build_gcal_ui_data(self, raw: Dict) -> Dict:
        """Calendar 用の軽量 ui_data を生成（G スキップ補完）"""
        timeline = []
        if raw.get('summary'):
            timeline.append({
                'event':       raw.get('summary'),
                'date':        str(raw.get('start_at', '')),
                'location':    raw.get('location'),
                'description': raw.get('description'),
            })
        sections = []
        if raw.get('description'):
            sections.append({
                'title': raw.get('summary', ''),
                'body':  raw.get('description', ''),
            })
        return {
            'sections': sections,
            'tables':   [],
            'timeline': timeline,
            'actions':  [],
            'notices':  [],
        }

    # ------------------------------------------------------------------
    # Classroom マッパー（ema / ikuya / waseaca 共通）
    # ------------------------------------------------------------------
    def _map_classroom(self, raw: Dict, raw_table: str, ui_data: Optional[Dict]) -> Dict:
        return {
            'raw_id':     str(raw['id']),
            'raw_table':  raw_table,
            'person':     raw.get('person'),
            'source':     raw.get('source'),
            'category':   raw.get('course_name'),   # course_name → category
            'title':      raw.get('title'),
            'file_url':   raw.get('file_url'),
            'from_email': raw.get('creator_email'),
            'from_name':  raw.get('creator_name'),
            'snippet':    None,
            'post_at':    raw.get('created_at'),
            'start_at':   None,
            'end_at':     None,
            'location':   None,
            'due_date':   raw.get('due_date'),
            'post_type':  raw.get('post_type'),
            'ui_data':    ui_data,
            'meta': {
                'course_id':  raw.get('course_id'),
                'topic_id':   raw.get('topic_id'),
                'topic_name': raw.get('topic_name'),
                'due_time':   raw.get('due_time'),
                'file_name':  raw.get('file_name'),
                'post_id':    raw.get('post_id'),
                'updated_at': str(raw['updated_at']) if raw.get('updated_at') else None,
            },
        }

    # ------------------------------------------------------------------
    # File マッパー
    # ------------------------------------------------------------------
    def _map_file(self, raw: Dict, ui_data: Optional[Dict]) -> Dict:
        return {
            'raw_id':     str(raw['id']),
            'raw_table':  RAW_FILE,
            'person':     raw.get('person'),
            'source':     raw.get('source'),
            'category':   raw.get('category'),
            'title':      raw.get('file_name'),
            'file_url':   raw.get('file_url'),
            'from_email': None,
            'from_name':  None,
            'snippet':    None,
            'post_at':    None,
            'start_at':   None,
            'end_at':     None,
            'location':   None,
            'due_date':   None,
            'post_type':  None,
            'ui_data':    ui_data,
            'meta': {
                'file_id':       raw.get('file_id'),
                'file_size':     raw.get('file_size'),
                'original_path': raw.get('original_path'),
                'mime_type':     raw.get('mime_type'),
            },
        }
