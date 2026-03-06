"""
Database Client
Supabaseデータベースへの接続と操作を管理
"""
import re
from typing import Dict, Any, List, Optional
import asyncio
from loguru import logger
from supabase import create_client, Client
from shared.common.config.settings import settings


# =============================================================================
# Phase 3: owner_id 必須化
# =============================================================================

class OwnerIdRequiredError(Exception):
    """owner_id が必須のテーブルに owner_id なしで INSERT しようとした"""
    pass


# owner_id を必須とするテーブルとカラム名のマッピング
OWNER_ID_REQUIRED_TABLES = {
    'pipeline_meta': 'owner_id',
    '10_ix_search_index': 'owner_id',
    'Rawdata_RECEIPT_shops': 'owner_id',
    'MASTER_Rules_transaction_dict': 'created_by',
    '99_lg_correction_history': 'corrector_id',
}


class DatabaseClient:
    """Supabaseデータベースクライアント"""

    def __init__(
        self,
        use_service_role: bool = False,
        access_token: str = None
    ):
        """Supabaseクライアントの初期化

        Args:
            use_service_role: Trueの場合、Service Role Keyを使用（RLSをバイパス）
                              ※ Admin UI からは使用禁止。Worker専用。
            access_token: Supabase Auth のアクセストークン（authenticated ロールで接続）
                          指定された場合、use_service_role は無視される
        """
        # Fail-fast: SUPABASE_URL は必須
        if not settings.SUPABASE_URL:
            raise ValueError("環境変数が不足しています: SUPABASE_URL")

        # 認証トークンが指定された場合（Admin UI 向け）
        if access_token:
            if not settings.SUPABASE_KEY:
                raise ValueError("環境変数が不足しています: SUPABASE_KEY (access_token使用時に必要)")

            # 認証済みセッションでクライアントを作成
            # supabase-py では、auth.set_session() を使用
            self.client: Client = create_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_KEY
            )
            # アクセストークンをヘッダーに設定
            self.client.postgrest.auth(access_token)
            self._is_authenticated = True
            self._is_service_role = False
            logger.info("Using Supabase key role=authenticated (access_token)")

        # Service Role Key使用の場合（Worker 向け）
        elif use_service_role:
            if not settings.SUPABASE_SERVICE_ROLE_KEY:
                raise ValueError("環境変数が不足しています: SUPABASE_SERVICE_ROLE_KEY (use_service_role=True時に必要)")
            api_key = settings.SUPABASE_SERVICE_ROLE_KEY

            self.client: Client = create_client(
                settings.SUPABASE_URL,
                api_key
            )
            self._is_authenticated = False
            self._is_service_role = True
            logger.info("Using Supabase key role=service_role")

        # 匿名アクセス（anon key）
        else:
            if not settings.SUPABASE_KEY:
                raise ValueError("環境変数が不足しています: SUPABASE_KEY (anon接続時に必要)")
            api_key = settings.SUPABASE_KEY

            self.client: Client = create_client(
                settings.SUPABASE_URL,
                api_key
            )
            self._is_authenticated = False
            self._is_service_role = False
            logger.info("Using Supabase key role=anon")

    @property
    def is_authenticated(self) -> bool:
        """認証済みセッションかどうか"""
        return self._is_authenticated

    @property
    def is_service_role(self) -> bool:
        """Service Role での接続かどうか"""
        return self._is_service_role

    def _validate_owner_id(self, table: str, data: Dict[str, Any]) -> None:
        """
        service_role 経路での owner_id 必須チェック（第三防衛線）

        service_role では RLS がバイパスされるため、
        コード側で owner_id の存在を保証する必要がある。

        Args:
            table: テーブル名
            data: INSERT/UPSERT するデータ

        Raises:
            OwnerIdRequiredError: owner_id が必須のテーブルに owner_id なしで INSERT しようとした場合
        """
        # service_role 経路のみチェック（authenticated は RLS が保護）
        if not self._is_service_role:
            return

        if table in OWNER_ID_REQUIRED_TABLES:
            owner_col = OWNER_ID_REQUIRED_TABLES[table]
            if owner_col not in data or data[owner_col] is None:
                raise OwnerIdRequiredError(
                    f"Table '{table}' requires '{owner_col}' but it was not provided. "
                    f"service_role connections must explicitly set owner_id."
                )

    @property
    def supabase(self) -> Client:
        """Supabase クライアントを取得（後方互換性のため）"""
        return self.client
    
    def get_document_by_file_url(self, file_url: str) -> Optional[Dict[str, Any]]:
        """
        file_url で文書を検索

        Args:
            file_url: Google Drive の URL

        Returns:
            既存の文書レコード、存在しない場合は None
        """
        try:
            response = self.client.table('09_unified_documents').select('*').eq('file_url', file_url).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            print(f"Error getting document by file_url: {e}")
            return None
    
    async def insert_document(self, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        文書をデータベースに挿入

        Args:
            table: テーブル名
            data: 挿入するデータ

        Returns:
            挿入されたレコード

        Raises:
            OwnerIdRequiredError: service_role で owner_id 必須テーブルに owner_id なしで INSERT した場合
        """
        # Phase 3: owner_id 必須チェック（第三防衛線）
        self._validate_owner_id(table, data)

        # embeddingをPostgreSQLのvector型形式に変換
        if 'embedding' in data and data['embedding'] is not None:
            embedding_list = data['embedding']
            if isinstance(embedding_list, list):
                # ベクトル形式の文字列に変換: [0.1,0.2,0.3]
                data = data.copy()  # 元のdataを変更しないようにコピー
                data['embedding'] = '[' + ','.join(str(x) for x in embedding_list) + ']'

        response = self.client.table(table).insert(data).execute()
        return response.data[0] if response.data else {}

    async def upsert_document(
        self,
        table: str,
        data: Dict[str, Any],
        conflict_column: str = 'id',
        force_update: bool = False,
        preserve_fields: List[str] = None
    ) -> Dict[str, Any]:
        """
        ドキュメントをupsert（既存レコードがあれば更新、なければ挿入）

        Args:
            table: テーブル名
            data: 挿入・更新するデータ
            conflict_column: 重複判定に使うカラム名（デフォルト: id）
            force_update: Trueの場合、全てのフィールドを強制的に更新（再処理時用）
            preserve_fields: force_update=Trueの時でも既存値を保持するフィールドのリスト

        Returns:
            挿入・更新されたレコード

        Raises:
            OwnerIdRequiredError: service_role で owner_id 必須テーブルに owner_id なしで INSERT した場合
        """
        # Phase 3: owner_id 必須チェック（第三防衛線）
        # 新規挿入時に owner_id が必要なため、upsert 前にチェック
        self._validate_owner_id(table, data)

        # embeddingをPostgreSQLのvector型形式に変換
        if 'embedding' in data and data['embedding'] is not None:
            embedding_list = data['embedding']
            if isinstance(embedding_list, list):
                data = data.copy()
                data['embedding'] = '[' + ','.join(str(x) for x in embedding_list) + ']'

        # 既存レコードを取得
        existing = self.client.table(table).select('*').eq(conflict_column, data[conflict_column]).execute()

        if existing.data:
            # 既存レコードがある場合
            existing_record = existing.data[0]
            update_data = {}

            if force_update:
                # 再処理モード：基本的に全て更新するが、preserve_fieldsは既存値を保持
                preserve_fields = preserve_fields or []

                for key, value in data.items():
                    if key in preserve_fields:
                        # 保持対象フィールドは既存値が有効な場合のみ保持
                        if existing_record.get(key) not in [None, '', [], {}]:
                            continue  # 既存値を保持（更新しない）
                    update_data[key] = value
            else:
                # 通常モード：空欄・nullの項目のみ更新
                for key, value in data.items():
                    if existing_record.get(key) in [None, '', [], {}]:
                        update_data[key] = value

            if update_data:
                # 更新するデータがある場合のみUPDATE
                response = self.client.table(table).update(update_data).eq(conflict_column, data[conflict_column]).execute()
                return response.data[0] if response.data else {}
            else:
                # 更新不要の場合は既存レコードを返す
                return existing_record
        else:
            # 既存レコードがない場合：新規挿入
            response = self.client.table(table).insert(data).execute()
            return response.data[0] if response.data else {}

    async def search_documents(
        self,
        query: str,
        embedding: List[float],
        limit: int = 50,
        sources: Optional[List[str]] = None,
        persons: Optional[List[str]] = None,
        category: Optional[List[str]] = None,
        date_filter: Optional[str] = None,
        threshold: float = 0.4,
        date_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        ハイブリッド検索: 09_unified_documents + 10_ix_search_index を使用。

        Args:
            query: 検索クエリ
            embedding: クエリのembeddingベクトル
            limit: 取得する最大件数
            sources: ソースフィルタ（例: ['GOOGLE_GMAIL', 'GOOGLE_CLASSROOM']）
            persons: person フィルタ（複数可、例: ['エマ', '太郎']）
            category: カテゴリフィルタ（複数可、例: ['school', 'work']）
            date_filter: 日付フィルタ ('today', 'this_week', 'this_month', 'recent')
            threshold: 足切りスコア閾値
            date_range: クエリ改善で得た日付レンジ "YYYY-MM-DD..YYYY-MM-DD"（time_score 計算に使用）

        Returns:
            検索結果のリスト（final_score 降順）
        """
        try:
            rpc_params = {
                "query_text": query,
                "query_embedding": embedding,
                "match_threshold": -1.0,
                "match_count": limit,
                "vector_weight": 0.7,
                "fulltext_weight": 0.3,
                "filter_sources": sources,
                "filter_chunk_types": None,
                "filter_persons": persons,
                "filter_category": category,
            }

            print(f"[DEBUG] unified_search_v2 呼び出し: query='{query}', sources={sources}, persons={persons}, category={category}")
            response = self.client.rpc("unified_search_v2", rpc_params).execute()
            results = response.data if response.data else []

            print(f"[DEBUG] unified_search_v2 結果: {len(results)} 件")

            # 日付フィルタリング
            if date_filter:
                results = self._apply_date_filter(results, date_filter)
                print(f"[DEBUG] 日付フィルタ適用後: {len(results)} 件 (filter={date_filter})")

            # 結果を整形
            final_results = []
            for result in results:
                # document_date: post_at (Gmail/Classroom) か start_at (Calendar) の日付部分
                raw_date = result.get('post_at') or result.get('start_at')
                document_date = raw_date[:10] if isinstance(raw_date, str) and len(raw_date) >= 10 else None

                doc_result = {
                    'id':           result.get('doc_id'),
                    'title':        result.get('title'),
                    'source':       result.get('source'),
                    'person':       result.get('person'),
                    'category':     result.get('category'),
                    'from_name':    result.get('from_name'),
                    'from_email':   result.get('from_email'),
                    'snippet':      result.get('snippet'),
                    'post_at':      result.get('post_at'),
                    'start_at':     result.get('start_at'),
                    'end_at':       result.get('end_at'),
                    'due_date':     result.get('due_date'),
                    'location':     result.get('location'),
                    'file_url':     result.get('file_url'),
                    'ui_data':      result.get('ui_data'),
                    'meta':         result.get('meta'),
                    'indexed_at':   result.get('indexed_at'),
                    'document_date': document_date,  # リランク用に保持

                    # チャンク情報
                    'chunk_content': result.get('best_chunk_text'),
                    'chunk_id':      result.get('best_chunk_id'),
                    'chunk_index':   result.get('best_chunk_index'),
                    'chunk_type':    result.get('best_chunk_type'),

                    # スコア
                    'similarity':          result.get('combined_score', 0),
                    'raw_similarity':      result.get('raw_similarity', 0),
                    'weighted_similarity': result.get('weighted_similarity', 0),
                    'fulltext_score':      result.get('fulltext_score', 0),
                    'title_matched':       result.get('title_matched', False),
                    'chunk_score':         result.get('combined_score', 0),
                    'large_chunk_id':      result.get('doc_id'),
                    'small_chunk_id':      result.get('best_chunk_id'),
                }
                final_results.append(doc_result)

            # キーワード・日付を先に抽出（チャンクフィルタとリランクで共用）
            target_date = self._extract_date(query)
            keywords    = self._extract_keywords(query)

            # 各ドキュメントのヒットチャンク全体を取得
            if self._is_service_role or self._is_authenticated:
                print(f"[DEBUG] ヒットチャンク詳細を取得中...")
                for doc_result in final_results:
                    doc_id = doc_result.get('id')
                    if not doc_id:
                        continue
                    try:
                        chunks_response = (
                            self.client.table('10_ix_search_index')
                            .select('id, chunk_index, chunk_text, chunk_type, chunk_weight')
                            .eq('doc_id', doc_id)
                            .order('chunk_weight', desc=True)
                            .execute()
                        )
                        if chunks_response.data:
                            raw_chunks = chunks_response.data
                            filtered = self._filter_chunks_for_context(
                                raw_chunks,
                                query=query,
                                best_chunk_id=doc_result.get('chunk_id'),
                                keywords=keywords,
                            )
                            doc_result['all_chunks'] = filtered
                            print(f"[DEBUG] doc {doc_id[:8]}: {len(raw_chunks)}個取得 → {len(filtered)}個に絞り込み")
                        else:
                            doc_result['all_chunks'] = []
                    except Exception as e:
                        print(f"[WARNING] チャンク取得エラー (doc_id={doc_id}): {e}")
                        doc_result['all_chunks'] = []
            else:
                print(f"[DEBUG] anon 接続のため all_chunks 取得をスキップ")
                for doc_result in final_results:
                    doc_result['all_chunks'] = []

            # キーワードブースト（similarity に加算）
            for doc in final_results:
                if keywords:
                    kw_boost = self._calculate_keyword_match_score(
                        doc.get('title', ''), keywords, query
                    )
                    doc['similarity'] += kw_boost * 0.2

            # 足切り（date_rangeがある時だけ threshold を緩める）
            from shared.common.config.settings import settings
            delta = settings.DATE_RANGE_THRESHOLD_DELTA
            effective_threshold = threshold - (float(delta) if date_range else 0.0)
            print(f"[DEBUG] 足切り前 全{len(final_results)}件: " + " / ".join(
                f"sim={r.get('similarity',0):.3f} {str(r.get('title',''))[:20]}"
                for r in sorted(final_results, key=lambda x: x.get('similarity',0), reverse=True)
            ))
            cutoff = [r for r in final_results if r.get('similarity', 0) >= effective_threshold]
            final_results = cutoff if cutoff else final_results[:3]

            # similarity 降順にソート（キーワードブースト後の順位確定）
            final_results.sort(key=lambda x: x.get('similarity', 0), reverse=True)

            # rank-based 正規化 + εだけ similarity を混ぜる
            eps = settings.REL_SIM_MIX_EPS
            n = len(final_results)
            for rank, doc in enumerate(final_results):  # similarity 降順
                rel_rank = 1.0 - (rank / (n - 1)) if n > 1 else 1.0
                if eps > 0.0:
                    sim_norm = max(0.0, min(1.0, doc.get('similarity', 0)))
                    rel = (1.0 - eps) * rel_rank + eps * sim_norm
                else:
                    rel = rel_rank
                rel = max(0.0, min(1.0, rel))
                ts = self._calc_time_score(doc.get('document_date'), date_range)
                doc['time_score'] = ts
                doc['rel'] = rel
                doc['final_score'] = max(0.0, min(1.0, 0.75 * rel + 0.25 * ts))
                if ts > 0:
                    doc['is_date_matched'] = True

            final_results.sort(key=lambda x: x.get('final_score', 0), reverse=True)

            print(f"[DEBUG] 最終検索結果: {len(final_results)} 件（unified_search_v2）")
            print(f"[DEBUG] date_range={date_range}, キーワード: {keywords}")
            print(f"[DEBUG] 足切り base={threshold} delta={delta} effective={effective_threshold:.3f} / eps={eps}")
            print(f"[DEBUG] 足切り通過: {len(cutoff)} 件 → {len(final_results)} 件")
            for i, doc in enumerate(final_results[:3]):
                print(f"[DEBUG] top{i+1}: sim={doc.get('similarity',0):.3f} rel={doc.get('rel',0):.3f} ts={doc.get('time_score',0):.2f} final={doc.get('final_score',0):.3f} title={str(doc.get('title',''))[:30]}")

            return final_results

        except Exception as e:
            print(f"[ERROR] unified_search_v2 失敗: {e}")
            import traceback
            traceback.print_exc()
            raise

    def search_documents_sync(
        self,
        query: str,
        embedding: List[float],
        limit: int = 50,
        sources: Optional[List[str]] = None,
        persons: Optional[List[str]] = None,
        category: Optional[List[str]] = None,
        date_filter: Optional[str] = None,
        threshold: float = 0.4,
        date_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        同期版の search_documents（Flask エンドポイント用）

        Args:
            query: 検索クエリ
            embedding: クエリのembeddingベクトル
            limit: 取得する最大件数
            sources: ソースフィルタ（例: ['GOOGLE_GMAIL']）
            persons: person フィルタ（複数可）
            category: カテゴリフィルタ（複数可）
            date_filter: 日付フィルタ ('today', 'this_week', 'this_month', 'recent')
            threshold: 足切りスコア閾値
            date_range: クエリ改善で得た日付レンジ "YYYY-MM-DD..YYYY-MM-DD"

        Returns:
            検索結果のリスト
        """
        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    result = loop.run_until_complete(
                        self.search_documents(query, embedding, limit, sources, persons, category, date_filter, threshold, date_range)
                    )
                    return result
            except RuntimeError:
                pass

            return asyncio.run(
                self.search_documents(query, embedding, limit, sources, persons, category, date_filter, threshold, date_range)
            )
        except Exception as e:
            print(f"[ERROR] search_documents_sync 失敗: {e}")
            import traceback
            traceback.print_exc()
            raise

    def _filter_chunks_for_context(
        self,
        chunks: List[Dict[str, Any]],
        query: str,
        best_chunk_id: Optional[str],
        keywords: List[str],
        non_table_weight_threshold: float = 1.0,
    ) -> List[Dict[str, Any]]:
        """
        コンテキスト構築用チャンクのフィルタリング。

        - table_X チャンク: chunk_text にキーワードが含まれるもの。
          一致なしの場合は best_chunk_id のみ（フォールバック）。
        - それ以外: chunk_weight >= non_table_weight_threshold のもの全件。
        """
        table_chunks = [c for c in chunks if (c.get('chunk_type') or '').startswith('table_')]
        other_chunks = [c for c in chunks if not (c.get('chunk_type') or '').startswith('table_')]

        # ── テーブルチャンク ──────────────────────────────────────────────
        relevant_tables = []
        if keywords and table_chunks:
            for chunk in table_chunks:
                content = chunk.get('chunk_text') or ''
                if any(kw in content for kw in keywords):
                    relevant_tables.append(chunk)

            if not relevant_tables:
                # キーワードマッチなし → best_chunk_id にフォールバック
                best = [c for c in table_chunks if str(c.get('id')) == str(best_chunk_id)] if best_chunk_id else []
                relevant_tables = best if best else table_chunks[:1]
        else:
            relevant_tables = table_chunks[:1] if table_chunks else []

        # ── その他チャンク ────────────────────────────────────────────────
        filtered_other = [
            c for c in other_chunks
            if (c.get('chunk_weight') or 0) >= non_table_weight_threshold
        ]

        return relevant_tables + filtered_other

    def _apply_date_filter(self, results: List[Dict[str, Any]], date_filter: str) -> List[Dict[str, Any]]:
        """
        日付フィルタを適用

        Args:
            results: 検索結果のリスト
            date_filter: フィルタタイプ ('today', 'this_week', 'this_month', 'recent')

        Returns:
            フィルタリングされた結果
        """
        from datetime import datetime, timedelta

        now = datetime.now()
        filtered_results = []

        for result in results:
            document_date_str = result.get('document_date')

            # document_dateが存在しない場合はスキップ
            if not document_date_str:
                # 'recent' の場合は、indexed_at でフィルタリング
                if date_filter == 'recent':
                    indexed_at_str = result.get('indexed_at')
                    if indexed_at_str:
                        try:
                            indexed_at = datetime.fromisoformat(indexed_at_str.replace('Z', '+00:00'))
                            if (now - indexed_at).days <= 30:
                                filtered_results.append(result)
                        except:
                            pass
                continue

            try:
                # document_dateをパース (YYYY-MM-DD形式を想定)
                document_date = datetime.strptime(document_date_str, '%Y-%m-%d')
            except:
                # パースできない場合はスキップ
                continue

            # フィルタタイプに応じて判定
            if date_filter == 'today':
                if document_date.date() == now.date():
                    filtered_results.append(result)

            elif date_filter == 'this_week':
                # 今週の月曜日を計算
                week_start = now - timedelta(days=now.weekday())
                week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
                if document_date >= week_start:
                    filtered_results.append(result)

            elif date_filter == 'this_month':
                if document_date.year == now.year and document_date.month == now.month:
                    filtered_results.append(result)

            elif date_filter == 'recent':
                # 過去30日以内
                if (now - document_date).days <= 30:
                    filtered_results.append(result)

        return filtered_results

    async def _fallback_vector_search(
        self,
        embedding: List[float],
        limit: int,
        workspace: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        フォールバック用のベクトル検索（エラー時に使用）

        Args:
            embedding: クエリのembeddingベクトル
            limit: 取得する最大件数
            workspace: ワークスペースフィルタ

        Returns:
            検索結果のリスト
        """
        try:
            rpc_params = {
                "query_embedding": embedding,
                "match_threshold": 0.0,
                "match_count": min(limit, 5)
            }

            response = self.client.rpc("match_documents", rpc_params).execute()
            raw = response.data if response.data else []

            print(f"[DEBUG] フォールバック検索結果: {len(raw)} 件")

            # match_documents の戻り値を search_documents と同じ形式に変換
            results = []
            for r in raw:
                results.append({
                    'id': r.get('document_id'),
                    'file_name': r.get('file_name'),
                    'doc_type': r.get('doc_type'),
                    'workspace': r.get('workspace'),
                    'similarity': r.get('similarity', 0),
                    'chunk_content': r.get('chunk_text'),
                    'all_chunks': [],
                })
            return results

        except Exception as e:
            print(f"[ERROR] フォールバック検索エラー: {e}")
            return []

    def _calc_time_score(self, document_date: Optional[str], date_range: Optional[str]) -> float:
        """
        document_date と date_range の距離から time_score（0〜1）を計算する。

        Args:
            document_date: 文書日付（YYYY-MM-DD）
            date_range: クエリ改善で得た日付レンジ "YYYY-MM-DD..YYYY-MM-DD"（空文字または None なら 0）

        Returns:
            time_score:
                レンジ内   → 1.0
                ±7日      → 0.7
                ±30日     → 0.3
                それ以上   → 0.0
                日付なし   → 0.0
        """
        if not document_date or not date_range or '..' not in date_range:
            return 0.0
        try:
            from datetime import date as date_type, timedelta
            start_str, end_str = date_range.split('..', 1)
            start = date_type.fromisoformat(start_str.strip())
            # end_str が空（"2026-03-05.." のように終端なし）→ 遠い未来として扱う
            end = date_type.fromisoformat(end_str.strip()) if end_str.strip() else date_type(9999, 12, 31)
            doc_dt = date_type.fromisoformat(document_date[:10])

            print(f"[DEBUG] time_score input: date_range={date_range!r}, document_date={document_date!r}, start={start}, end={end}, doc_dt={doc_dt}")

            if start <= doc_dt <= end:
                return 1.0
            # レンジ端までの距離（doc_dt < start → start側、doc_dt > end → end側）
            dist = (start - doc_dt).days if doc_dt < start else (doc_dt - end).days
            if dist <= 7:
                return 0.7
            elif dist <= 30:
                return 0.3
            return 0.0
        except Exception as e:
            print(f"[DEBUG] time_score parse error: date_range={date_range!r}, document_date={document_date!r}, err={e}")
            return 0.0

    def _check_date_match(self, doc: Dict[str, Any], target_date: str) -> float:
        """
        日付スコアブースト（加算方式）

        ① content_dates ブースト（近傍含む）と ② テキストマッチは独立して加算。
          完全一致:   +0.3
          1〜7日差:   +0.15
          8〜14日差:  +0.05
        ② テキストマッチ: +0.3

        Args:
            doc: ドキュメント
            target_date: ターゲット日付（YYYY-MM-DD形式、例：2025-12-04）

        Returns:
            マッチスコア（最大 +0.6）
        """
        from datetime import date as date_type
        try:
            parts = target_date.split('-')
            month = int(parts[1])
            day = int(parts[2])
            target_dt = date_type(int(parts[0]), month, day)
        except:
            return 0.0

        # テキスト内の日付パターン（表記揺れ + F3正規化済みYYYY-MM-DD の月日部分）
        date_patterns = [
            f"{month}/{day}",           # 3/5
            f"{month:02d}/{day:02d}",   # 03/05
            f"{month}月{day}日",         # 3月5日
            f"-{month:02d}-{day:02d}",  # -03-05（YYYY-MM-DD の一部にマッチ）
        ]

        boost = 0.0

        # テキスト（chunk_content / title）→ +0.3
        text_fields = ['chunk_content', 'title']
        for field in text_fields:
            text = doc.get(field, '')
            if text:
                for pattern in date_patterns:
                    if pattern in text:
                        print(f"[DEBUG] テキストマッチ: {doc.get('title')} '{pattern}' in {field}")
                        boost += 0.3
                        break
                else:
                    continue
                break

        return boost

    def _calculate_keyword_match_score(
        self,
        title: str,
        keywords: List[str],
        query: str
    ) -> float:
        """
        title とクエリの一致度を計算

        Args:
            title: ドキュメントタイトル
            keywords: 抽出されたキーワードのリスト
            query: 元のクエリ

        Returns:
            一致度スコア（0.0～1.0）
        """
        # クエリから記号を除去して正規化
        normalized_query = query.replace('？', '').replace('?', '').replace('の内容は', '').replace('内容', '').strip()

        # 完全一致（括弧付き単語がそのまま含まれる）
        for kw in keywords:
            if '（' in kw or '(' in kw:
                if kw in title:
                    return 1.0

        # マッチしたキーワードの数をカウント
        matched_keywords = []
        for kw in keywords:
            if kw in title:
                matched_keywords.append(kw)

        if not matched_keywords:
            return 0.0

        # マッチ数に応じてスコアを設定
        match_count = len(matched_keywords)
        total_keywords = len(keywords)

        if match_count == total_keywords:
            # すべてのキーワードがマッチ（ただし完全一致ではない）
            return 0.95
        elif match_count >= 2:
            # 2つ以上マッチ
            return 0.90
        else:
            # 1つだけマッチ
            return 0.85

    def _extract_date(self, query: str) -> Optional[str]:
        """
        クエリから日付を抽出（YYYY-MM-DD形式に正規化）

        Args:
            query: 検索クエリ

        Returns:
            正規化された日付文字列（YYYY-MM-DD）、または None
        """
        from datetime import datetime

        # 現在の年を取得
        current_year = datetime.now().year

        # パターン0: YYYY-MM-DD形式（例：2026-03-09）
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', query)
        if match:
            try:
                date_obj = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                return date_obj.strftime('%Y-%m-%d')
            except ValueError:
                pass

        # パターン1: MM/DD形式（例：12/4）
        match = re.search(r'(\d{1,2})/(\d{1,2})', query)
        if match:
            month = int(match.group(1))
            day = int(match.group(2))
            try:
                date_obj = datetime(current_year, month, day)
                return date_obj.strftime('%Y-%m-%d')
            except ValueError:
                pass

        # パターン2: MM月DD日形式（例：12月4日）
        match = re.search(r'(\d{1,2})月(\d{1,2})日', query)
        if match:
            month = int(match.group(1))
            day = int(match.group(2))
            try:
                date_obj = datetime(current_year, month, day)
                return date_obj.strftime('%Y-%m-%d')
            except ValueError:
                pass

        return None

    def _extract_keywords(self, query: str) -> List[str]:
        """
        クエリから重要なキーワードを抽出

        Args:
            query: 検索クエリ

        Returns:
            抽出されたキーワードのリスト
        """
        keywords = []

        # 括弧内の文字を抽出（例：「学年通信（29）」→「29」「学年通信」）
        bracket_matches = re.findall(r'[（(]([^）)]+)[）)]', query)
        keywords.extend(bracket_matches)

        # 括弧を含む単語全体を抽出（例：「学年通信（29）」）
        bracket_words = re.findall(r'[\w一-龠ぁ-んァ-ヶー]+[（(][^）)]+[）)]', query)
        keywords.extend(bracket_words)

        # 助詞を除去してクリーニング
        cleaned_query = query
        particles = ['の', 'は', 'を', 'が', 'に', 'へ', 'と', 'から', 'まで', 'で', '？', '?']
        for particle in particles:
            cleaned_query = cleaned_query.replace(particle, ' ')

        # 名詞的な単語を抽出（漢字・カタカナが2文字以上）
        words = re.findall(r'[一-龠ァ-ヶー]{2,}', cleaned_query)
        keywords.extend(words)

        # 重複削除・空白除去して返す
        keywords = [kw.strip() for kw in keywords if kw.strip()]
        return list(set(keywords))

    def get_documents_for_review(
        self,
        limit: int = 100,
        search_query: Optional[str] = None,
        person: Optional[str] = None,
        source: Optional[str] = None,
        category: Optional[str] = None,
        review_status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        レビュー対象のドキュメントを取得（pipeline_meta ベース）

        Args:
            limit: 取得する最大件数
            search_query: 検索クエリ（ID で部分一致）
            person: person フィルタ
            source: source フィルタ
            category: category フィルタ
            review_status: 'reviewed', 'pending', 'all', または None

        Returns:
            ドキュメントのリスト（更新日時降順）
        """
        try:
            query = self.client.table('pipeline_meta').select('*')

            # Gmail は除外
            query = query.neq('raw_table', '01_gmail_01_raw')

            if person:
                query = query.eq('person', person)
            if source:
                query = query.eq('source', source)
            if category:
                query = query.eq('category', category)

            if review_status == 'reviewed':
                query = query.not_.is_('latest_correction_id', 'null')
            elif review_status == 'pending':
                query = query.is_('latest_correction_id', 'null')

            if search_query:
                if len(search_query) == 36 or len(search_query) == 8:
                    response_id = query.ilike('id', f'{search_query}%').limit(limit).execute()
                    if response_id.data:
                        return response_id.data

            response = (
                query
                .order('updated_at', desc=True)
                .limit(limit)
                .execute()
            )
            return response.data if response.data else []

        except Exception as e:
            print(f"Error getting documents for review: {e}")
            return []

    def mark_document_reviewed(
        self,
        doc_id: str,
        reviewed_by: Optional[str] = None
    ) -> bool:
        """
        ドキュメントをレビュー済みとしてマークする
        （is_reviewed カラムは削除されたため、review_status で管理）

        Args:
            doc_id: ドキュメントID
            reviewed_by: レビュー担当者のメールアドレス（オプション）

        Returns:
            成功したかどうか
        """
        try:
            from datetime import datetime

            update_data = {
                'review_status': 'reviewed'
            }
            if reviewed_by:
                update_data['reviewed_by'] = reviewed_by

            response = (
                self.client.table('pipeline_meta')
                .update(update_data)
                .eq('id', doc_id)
                .execute()
            )
            return bool(response.data)
        except Exception as e:
            print(f"Error marking document as reviewed: {e}")
            return False

    def mark_document_unreviewed(
        self,
        doc_id: str
    ) -> bool:
        """
        ドキュメントを未レビュー状態に戻す
        （is_reviewed カラムは削除されたため、review_status で管理）

        Args:
            doc_id: ドキュメントID

        Returns:
            成功したかどうか
        """
        try:
            update_data = {
                'review_status': 'pending',
                'reviewed_by': None
            }

            response = (
                self.client.table('pipeline_meta')
                .update(update_data)
                .eq('id', doc_id)
                .execute()
            )
            return bool(response.data)
        except Exception as e:
            print(f"Error marking document as unreviewed: {e}")
            return False

    def get_review_progress(self) -> Dict[str, Any]:
        """
        レビュー進捗状況を取得
        （is_reviewed カラムは削除されたため、review_status で管理）

        Returns:
            進捗情報の辞書
        """
        try:
            # 未レビューの件数（latest_correction_id IS NULL）
            unreviewed_response = (
                self.client.table('pipeline_meta')
                .select('*', count='exact')
                .is_('latest_correction_id', 'null')
                .execute()
            )
            unreviewed_count = unreviewed_response.count if unreviewed_response else 0

            # レビュー済みの件数（latest_correction_id IS NOT NULL）
            reviewed_response = (
                self.client.table('pipeline_meta')
                .select('*', count='exact')
                .not_.is_('latest_correction_id', 'null')
                .execute()
            )
            reviewed_count = reviewed_response.count if reviewed_response else 0

            # 総件数
            total_count = unreviewed_count + reviewed_count

            # 進捗率
            progress_percent = (reviewed_count / total_count * 100) if total_count > 0 else 0

            return {
                'total': total_count,
                'reviewed': reviewed_count,
                'unreviewed': unreviewed_count,
                'progress_percent': round(progress_percent, 2)
            }
        except Exception as e:
            print(f"Error getting review progress: {e}")
            return {
                'total': 0,
                'reviewed': 0,
                'unreviewed': 0,
                'progress_percent': 0
            }

    def get_available_workspaces(self) -> List[str]:
        """
        利用可能な person 一覧を取得（09_unified_documents ベース）

        Returns:
            person 名のリスト（重複なし、ソート済み）
        """
        try:
            response = self.client.table('09_unified_documents').select('person').execute()
            persons = {doc.get('person') for doc in response.data if doc.get('person')}
            return sorted(list(persons))
        except Exception as e:
            print(f"Error getting available persons: {e}")
            return []

    def get_available_doc_types(self) -> List[str]:
        """
        利用可能な source 一覧を取得（09_unified_documents ベース）

        Returns:
            source 名のリスト（重複なし、ソート済み）
        """
        try:
            response = self.client.table('09_unified_documents').select('source').execute()
            sources = {doc.get('source') for doc in response.data if doc.get('source')}
            return sorted(list(sources))
        except Exception as e:
            print(f"Error getting available sources: {e}")
            return []

    def get_workspace_hierarchy(self) -> Dict[str, Dict[str, List[str]]]:
        """
        person → source → [category] の3階層構造を取得（09_unified_documents ベース）

        Returns:
            {person: {source: [category, ...]}} の辞書
        """
        try:
            response = self.client.table('09_unified_documents').select('person, source, category').execute()

            hierarchy: Dict[str, Dict[str, set]] = {}
            for doc in response.data:
                person = doc.get('person')
                source = doc.get('source')
                cat    = doc.get('category')
                if person and source:
                    hierarchy.setdefault(person, {}).setdefault(source, set())
                    if cat:
                        hierarchy[person][source].add(cat)

            result = {
                p: {s: sorted(list(cats)) for s, cats in sorted(srcs.items())}
                for p, srcs in sorted(hierarchy.items())
            }

            print(f"[DEBUG] person 階層構造: {len(result)} persons")
            return result

        except Exception as e:
            print(f"Error getting person hierarchy: {e}")
            return {}

    def get_document_by_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """
        IDでドキュメントを取得

        Args:
            doc_id: ドキュメントID

        Returns:
            ドキュメント、存在しない場合は None
        """
        try:
            response = self.client.table('pipeline_meta').select('*').eq('id', doc_id).execute()
            if response.data:
                doc = response.data[0]
                # 09_unified_documents から title (file_name 相当) を付与
                try:
                    ud = (
                        self.client.table('09_unified_documents')
                        .select('title, ui_data, meta, from_name, from_email, snippet, body, post_at')
                        .eq('raw_id', doc['raw_id'])
                        .eq('raw_table', doc['raw_table'])
                        .limit(1)
                        .execute()
                    )
                    if ud.data:
                        ud_row = ud.data[0]
                        doc['file_name'] = ud_row.get('title')
                        doc['stage_g_structured_data'] = ud_row.get('ui_data')
                        doc['ud_meta'] = ud_row.get('meta')
                        # Gmail 表示用フィールド
                        if doc.get('raw_table') == '01_gmail_01_raw':
                            doc['display_subject']      = ud_row.get('title') or ''
                            doc['display_sender']       = ud_row.get('from_name') or ''
                            doc['display_sender_email'] = ud_row.get('from_email') or ''
                            doc['display_sent_at']      = ud_row.get('post_at') or ''
                            doc['display_snippet']      = ud_row.get('body') or ud_row.get('snippet') or ''
                except Exception:
                    pass
                return doc
            return None
        except Exception as e:
            print(f"Error getting document by id: {e}")
            return None

    def update_document_metadata(
        self,
        doc_id: str,
        new_metadata: Dict[str, Any],
        new_doc_type: Optional[str] = None
    ) -> bool:
        """
        ドキュメントのメタデータを更新

        注意: doc_type は投稿時のみ設定可能。プログラムからの変更は禁止。
        修正履歴を記録する場合は record_correction() を使用してください。

        Args:
            doc_id: ドキュメントID
            new_metadata: 新しいメタデータ
            new_doc_type: 無視される（doc_type はプログラムから変更不可）

        Returns:
            成功したかどうか
        """
        try:
            update_data = {'metadata': new_metadata}

            response = (
                self.client.table('pipeline_meta')
                .update(update_data)
                .eq('id', doc_id)
                .execute()
            )
            return bool(response.data)
        except Exception as e:
            print(f"Error updating document metadata: {e}")
            return False

    def record_correction(
        self,
        doc_id: str,
        new_metadata: Dict[str, Any],
        new_doc_type: Optional[str] = None,
        corrector_email: Optional[str] = None,
        notes: Optional[str] = None
    ) -> bool:
        """
        ドキュメントのメタデータを更新し、修正履歴を記録

        Phase 2: トランザクション管理・ロールバック機能

        Args:
            doc_id: ドキュメントID
            new_metadata: 新しいメタデータ
            new_doc_type: 無視される（doc_type はプログラムから変更不可）
            corrector_email: 修正者のメールアドレス（オプション）
            notes: 修正に関するメモ（オプション）

        Returns:
            成功したかどうか
        """
        from loguru import logger

        try:
            # Step 1: 現在のドキュメントを取得
            current_doc = self.get_document_by_id(doc_id)
            if not current_doc:
                logger.error(f"[record_correction] Document not found: {doc_id}")
                return False

            old_metadata = current_doc.get('metadata', {})
            old_doc_type = current_doc.get('doc_type')

            logger.info(f"[record_correction] 現在のドキュメント取得成功: doc_id={doc_id}, doc_type={old_doc_type}")

            # Step 2: correction_history に修正履歴を記録
            correction_data = {
                'document_id': doc_id,
                'old_metadata': old_metadata,
                'new_metadata': new_metadata,
                'corrector_email': corrector_email,
                'correction_type': 'manual',
                'notes': notes
            }

            logger.info(f"[record_correction] correction_history へ挿入開始")

            correction_response = (
                self.client.table('99_lg_correction_history')
                .insert(correction_data)
                .execute()
            )

            if not correction_response.data:
                logger.error(f"[record_correction] Failed to insert correction history")
                logger.error(f"[record_correction] Response: {correction_response}")
                return False

            correction_id = correction_response.data[0]['id']
            logger.info(f"[record_correction] ✅ 修正履歴を記録: correction_id={correction_id}")

            # Step 3: documents テーブルを更新
            update_data = {
                'metadata': new_metadata,
                'latest_correction_id': correction_id
            }
            # doc_type はプログラムからの変更禁止（Supabase で直接修正すること）

            # year, month のトップレベルカラムへの同期は削除（metadata 内で管理）

            logger.info(f"[record_correction] pipeline_meta 更新開始: doc_id={doc_id}")

            document_response = (
                self.client.table('pipeline_meta')
                .update(update_data)
                .eq('id', doc_id)
                .execute()
            )

            if not document_response.data:
                logger.error(f"[record_correction] Failed to update document")
                logger.error(f"[record_correction] Response: {document_response}")
                return False

            logger.info(f"[record_correction] ✅ ドキュメント更新成功: doc_id={doc_id}")
            return True

        except Exception as e:
            logger.error(f"[record_correction] Error recording correction: {e}", exc_info=True)
            return False

    def rollback_document(self, doc_id: str) -> bool:
        """
        ドキュメントのメタデータを最新の修正前の状態にロールバック

        Phase 2: トランザクション管理・ロールバック機能

        Args:
            doc_id: ドキュメントID

        Returns:
            成功したかどうか
        """
        try:
            # Step 1: 現在のドキュメントを取得
            current_doc = self.get_document_by_id(doc_id)
            if not current_doc:
                print(f"Error: Document not found: {doc_id}")
                return False

            latest_correction_id = current_doc.get('latest_correction_id')
            if not latest_correction_id:
                print(f"Error: No correction history found for document: {doc_id}")
                return False

            # Step 2: 最新の修正履歴を取得
            correction_response = (
                self.client.table('99_lg_correction_history')
                .select('*')
                .eq('id', latest_correction_id)
                .execute()
            )

            if not correction_response.data:
                print(f"Error: Correction history not found: {latest_correction_id}")
                return False

            correction = correction_response.data[0]
            old_metadata = correction['old_metadata']

            # Step 3: documentsテーブルを修正前の状態に戻す
            update_data = {
                'metadata': old_metadata,
                'latest_correction_id': None  # ロールバック後は修正履歴をクリア
            }

            document_response = (
                self.client.table('pipeline_meta')
                .update(update_data)
                .eq('id', doc_id)
                .execute()
            )

            if not document_response.data:
                print("Error: Failed to rollback document")
                return False

            print(f"✅ ロールバック成功: doc_id={doc_id}, correction_id={latest_correction_id}")
            return True

        except Exception as e:
            print(f"Error rolling back document: {e}")
            import traceback
            traceback.print_exc()
            return False

    def get_correction_history(
        self,
        doc_id: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        ドキュメントの修正履歴を取得

        Phase 2: トランザクション管理・ロールバック機能

        Args:
            doc_id: ドキュメントID
            limit: 取得する最大件数

        Returns:
            修正履歴のリスト（新しい順）
        """
        try:
            response = (
                self.client.table('99_lg_correction_history')
                .select('*')
                .eq('document_id', doc_id)
                .order('corrected_at', desc=True)
                .limit(limit)
                .execute()
            )
            return response.data if response.data else []
        except Exception as e:
            print(f"Error getting correction history: {e}")
            return []

    def get_processed_file_ids(self) -> List[str]:
        """
        既に処理済みのファイルIDリストを取得（file_id カラムから直接取得）

        Returns:
            処理済みファイルの Google Drive file ID のリスト
        """
        try:
            response = (
                self.client.table('08_file_only_01_raw')
                .select('file_id')
                .not_.is_('file_id', 'null')
                .execute()
            )

            if response.data:
                file_ids = [doc['file_id'] for doc in response.data if doc.get('file_id')]
                print(f"Supabaseから {len(file_ids)} 件の処理済みファイルIDを取得しました")
                return file_ids
            return []

        except Exception as e:
            print(f"Error getting processed file IDs: {e}")
            return []

    def check_duplicate_hash(self, content_hash: str) -> bool:
        """
        content_hashが既にデータベースに存在するかチェック

        Args:
            content_hash: SHA256ハッシュ値

        Returns:
            True: 重複あり（既に存在する）
            False: 重複なし（新規）
        """
        # 新スキーマ（08_file_only_01_raw 等）には content_hash カラムがない。
        # inbox_monitor.py は file_id ベースの重複検出に移行が必要。
        # 現時点では重複なしとして扱う（安全側に倒す）。
        return False

    def delete_document(self, doc_id: str) -> bool:
        """
        ドキュメントをデータベースから削除

        Args:
            doc_id: 削除するドキュメントのID

        Returns:
            True: 削除成功
            False: 削除失敗
        """
        try:
            # ドキュメントを削除（ON DELETE CASCADEにより関連データも削除される）
            response = (
                self.client.table('pipeline_meta')
                .delete()
                .eq('id', doc_id)
                .execute()
            )

            if response.data:
                print(f"✅ ドキュメントを削除しました: {doc_id}")
                return True
            else:
                print(f"⚠️  ドキュメントが見つかりませんでした: {doc_id}")
                return False

        except Exception as e:
            print(f"❌ ドキュメント削除エラー: {e}")
            return False