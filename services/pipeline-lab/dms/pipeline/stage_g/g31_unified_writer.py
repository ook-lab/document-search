"""
09 統一書き込み（Unified Writer）
パイプライン処理済みデータを 09_unified_documents に書き込む。
Calendar / Classroom / File 共通のゲート。

- 新規 INSERT では 09.id は raw 行の id と同一 UUID とする（10 / meta 参照の一本化）。
- Classroom / File: F 末尾の ui_data を受け取り書き込む
- Calendar: レビューUIスキップ、生データから軽量 ui_data を生成して書き込む
"""
from typing import Dict, Any, Optional
from loguru import logger


# ソーステーブル定数
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
            raw_table: 参照元テーブル名（例: '05_ikuya_waseaca_01_raw'）
            ui_data:   レビューUI（G11 / `F60UIDeliveryController`）出力（Calendar は None → 軽量版を自動生成）

        Returns:
            {'success': bool, 'doc_id': str}
        """
        logger.info(f"[09-write] 書き込み開始: raw_table={raw_table}")

        try:
            if raw_table == RAW_GCAL:
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
                doc.pop('id', None)
                self.db.client.table('09_unified_documents').update(doc).eq('id', doc_id).execute()
                logger.info(f"[09-write] UPDATE 完了: doc_id={doc_id}")
            else:
                # 09.id は raw 行の id と同一（検索・meta・10 参照の一本化）
                doc['id'] = str(raw_data['id'])
                result = self.db.client.table('09_unified_documents').insert(doc).execute()
                doc_id = result.data[0]['id']
                logger.info(f"[09-write] INSERT 完了: doc_id={doc_id}")

            return {'success': True, 'doc_id': doc_id}

        except Exception as e:
            logger.error(f"[09-write] 書き込みエラー: {e}")
            return {'success': False, 'error': str(e)}

    # ------------------------------------------------------------------
    # Google Calendar マッパー（レビューUI経由なし）
    # ------------------------------------------------------------------
    def _map_gcal(self, raw: Dict) -> Dict:
        # Calendar は ui_data をパイプラインから受け取らないため、軽量版を生成
        ui_data = self._build_gcal_ui_data(raw)
        return {
            'raw_id':       str(raw['id']),
            'raw_table':    RAW_GCAL,
            'person':       raw.get('person'),
            'classification1': raw.get('source'),
            'classification2': None,
            'classification3': raw.get('category'),
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
        # 03–05: 1=ソース 2=コース名 3=カテゴリー（raw 列と対応）
        return {
            'raw_id':     str(raw['id']),
            'raw_table':  raw_table,
            'person':     raw.get('person'),
            'classification1': raw.get('source'),
            'classification2': raw.get('course_name'),
            'classification3': raw.get('category'),
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
            'classification1': raw.get('source'),
            'classification2': None,
            'classification3': raw.get('category'),
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
