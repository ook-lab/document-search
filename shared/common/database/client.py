"""
Database Client
Supabaseデータベースへの接続と操作を管理
"""
import re
from typing import Dict, Any, List, Optional
import asyncio
from supabase import create_client, Client
from shared.common.config.settings import settings


class DatabaseClient:
    """Supabaseデータベースクライアント"""

    def __init__(self, use_service_role: bool = False):
        """Supabaseクライアントの初期化

        Args:
            use_service_role: Trueの場合、Service Role Keyを使用（RLSをバイパス）
        """
        if not settings.SUPABASE_URL:
            raise ValueError("SUPABASE_URL が設定されていません")

        # Service Role Key使用の場合
        if use_service_role:
            if not settings.SUPABASE_SERVICE_ROLE_KEY:
                raise ValueError("SUPABASE_SERVICE_ROLE_KEY が設定されていません")
            api_key = settings.SUPABASE_SERVICE_ROLE_KEY
        else:
            if not settings.SUPABASE_KEY:
                raise ValueError("SUPABASE_KEY が設定されていません")
            api_key = settings.SUPABASE_KEY

        self.client: Client = create_client(
            settings.SUPABASE_URL,
            api_key
        )
    
    def get_document_by_source_id(self, source_id: str) -> Optional[Dict[str, Any]]:
        """
        source_id で文書を検索
        
        Args:
            source_id: Google Drive のファイルID
        
        Returns:
            既存の文書レコード、存在しない場合は None
        """
        try:
            response = self.client.table('Rawdata_FILE_AND_MAIL').select('*').eq('source_id', source_id).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            print(f"Error getting document by source_id: {e}")
            return None
    
    async def insert_document(self, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        文書をデータベースに挿入

        Args:
            table: テーブル名
            data: 挿入するデータ

        Returns:
            挿入されたレコード
        """
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
        conflict_column: str = 'source_id',
        force_update: bool = False,
        preserve_fields: List[str] = None
    ) -> Dict[str, Any]:
        """
        ドキュメントをupsert（既存レコードがあれば更新、なければ挿入）

        Args:
            table: テーブル名
            data: 挿入・更新するデータ
            conflict_column: 重複判定に使うカラム名（デフォルト: source_id）
            force_update: Trueの場合、全てのフィールドを強制的に更新（再処理時用）
            preserve_fields: force_update=Trueの時でも既存値を保持するフィールドのリスト

        Returns:
            挿入・更新されたレコード
        """
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
        doc_types: Optional[List[str]] = None,
        date_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        2階層ハイブリッド検索：小チャンク検索 + 大チャンク回答（重複排除＆Rerank対応）

        【検索フロー】
        1. 小チャンク（300文字）でベクトル + 全文検索 → 関連ドキュメントを検出
        2. ドキュメント単位で重複排除（最高スコアのみ） → 重複を削減
        3. 大チャンク（全文）で回答生成 → 最も詳細な情報を使用

        【フィルタの考え方】
        - 階層構造（workspace > doc_type）はフロントエンドで維持
        - データベース検索はdoc_typeのみで絞り込み
        - 理由: workspace内の全doc_typeがON = workspaceがON（冗長なため）

        Args:
            query: 検索クエリ
            embedding: クエリのembeddingベクトル
            limit: 取得する最大件数
            doc_types: ドキュメントタイプフィルタ（配列、複数選択可能）

        Returns:
            検索結果のリスト（小チャンク検索スコア順、回答は大チャンク）
        """
        try:
            # 日付フィルタは all_mentioned_dates 配列で検索するため、
            # filter_year, filter_month の抽出は不要
            # 代わりに、クエリに含まれる日付は全文検索でマッチ

            # DB関数を呼び出し（unified_search_v2: 高度な重み付けスコアリング）
            rpc_params = {
                "query_text": query,
                "query_embedding": embedding,
                "match_threshold": 0.0,
                "match_count": limit,  # 指定された件数を取得
                "vector_weight": 0.7,  # ベクトル検索70% - 意味的類似度を重視
                "fulltext_weight": 0.3,  # キーワード検索30% - 完全一致の補助
                "filter_doc_types": doc_types,  # doc_typeのみで絞り込み
                "filter_chunk_types": None,  # 全chunk_typeを対象（title, summary, display_subject等）
                "filter_workspace": None  # 全workspaceを対象
            }

            print(f"[DEBUG] unified_search_v2 呼び出し: query='{query}', doc_types={doc_types}")
            response = self.client.rpc("unified_search_v2", rpc_params).execute()
            results = response.data if response.data else []

            print(f"[DEBUG] unified_search_v2 結果: {len(results)} 件")

            # 日付フィルタリング
            if date_filter:
                results = self._apply_date_filter(results, date_filter)
                print(f"[DEBUG] 日付フィルタ適用後: {len(results)} 件 (filter={date_filter})")

            # 結果を整形（unified_search_v2: 高度な検索結果）
            final_results = []
            for result in results:
                doc_result = {
                    'id': result.get('document_id'),
                    'file_name': result.get('file_name'),
                    'doc_type': result.get('doc_type'),
                    'workspace': result.get('workspace'),
                    'document_date': result.get('document_date'),
                    'metadata': result.get('metadata'),
                    'summary': result.get('summary'),

                    # 回答用：添付ファイルテキスト（unified_search_v2のattachment_text）
                    'content': result.get('attachment_text'),  # 添付ファイルテキスト
                    'large_chunk_id': result.get('document_id'),  # ドキュメントID

                    # ヒットしたチャンク情報（重み付けされた最適チャンク）
                    'chunk_content': result.get('best_chunk_text'),  # ヒットした最適チャンク
                    'chunk_id': result.get('best_chunk_id'),  # チャンクID
                    'chunk_index': result.get('best_chunk_index'),  # チャンクインデックス
                    'chunk_type': result.get('best_chunk_type'),  # チャンクタイプ（title, summary等）

                    # 検索スコア（unified_search_v2の詳細スコア）
                    'similarity': result.get('combined_score', 0),  # 統合スコア
                    'raw_similarity': result.get('raw_similarity', 0),  # 生の類似度
                    'weighted_similarity': result.get('weighted_similarity', 0),  # 重み付け類似度
                    'fulltext_score': result.get('fulltext_score', 0),  # 全文検索スコア
                    'title_matched': result.get('title_matched', False),  # タイトルマッチフラグ

                    # 後方互換性
                    'chunk_score': result.get('combined_score', 0),
                    'small_chunk_id': result.get('best_chunk_id')
                }

                # ✅ Classroom表示用の追加フィールド（存在する場合のみ追加）
                if 'source_type' in result:
                    doc_result['source_type'] = result.get('source_type')
                if 'source_url' in result:
                    doc_result['source_url'] = result.get('source_url')
                if 'attachment_text' in result:
                    doc_result['attachment_text'] = result.get('attachment_text')
                if 'created_at' in result:
                    doc_result['created_at'] = result.get('created_at')
                if 'display_subject' in result:
                    doc_result['display_subject'] = result.get('display_subject')
                if 'display_sender' in result:
                    doc_result['display_sender'] = result.get('display_sender')
                if 'classroom_sender_email' in result:
                    doc_result['classroom_sender_email'] = result.get('classroom_sender_email')
                if 'display_sent_at' in result:
                    doc_result['display_sent_at'] = result.get('display_sent_at')
                if 'display_post_text' in result:
                    doc_result['display_post_text'] = result.get('display_post_text')
                if 'display_type' in result:
                    doc_result['display_type'] = result.get('display_type')

                final_results.append(doc_result)

            # ✅ 各ドキュメントのヒットチャンク全体を取得
            print(f"[DEBUG] ヒットチャンク詳細を取得中...")
            for doc_result in final_results:
                document_id = doc_result.get('id')
                if not document_id:
                    continue

                try:
                    # このドキュメントの全チャンクを取得（重要度順、上限なし）
                    chunks_response = (
                        self.client.table('10_ix_search_index')
                        .select('chunk_index, chunk_content, chunk_type, chunk_metadata, search_weight')
                        .eq('document_id', document_id)
                        .order('search_weight', desc=True)  # 重要度順
                        .execute()
                    )

                    if chunks_response.data:
                        doc_result['all_chunks'] = chunks_response.data
                        print(f"[DEBUG] ドキュメント {document_id[:8]}: {len(chunks_response.data)}個のチャンク取得")
                    else:
                        doc_result['all_chunks'] = []

                except Exception as e:
                    print(f"[WARNING] チャンク取得エラー (document_id={document_id}): {e}")
                    doc_result['all_chunks'] = []

            print(f"[DEBUG] 最終検索結果: {len(final_results)} 件（unified_search_v2）")
            print(f"[DEBUG] 検索戦略: 重み付けスコアリング + chunk_type優先順位 + タイトルマッチ")

            return final_results

        except Exception as e:
            print(f"2-tier hybrid search error: {e}")
            import traceback
            traceback.print_exc()

            # フォールバック: 従来のベクトル検索（workspaceフィルタなし）
            print("[WARNING] フォールバックモード: match_documents を使用")
            return await self._fallback_vector_search(embedding, limit, None)

    def search_documents_sync(
        self,
        query: str,
        embedding: List[float],
        limit: int = 50,
        doc_types: Optional[List[str]] = None,
        date_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        同期版のsearch_documents（Flaskエンドポイント用）

        非同期版のsearch_documentsをasyncio.run()で実行するラッパー。
        これにより、Flask（同期フレームワーク）から適切に非同期処理を呼び出せる。

        Args:
            query: 検索クエリ
            embedding: クエリのembeddingベクトル
            limit: 取得する最大件数
            doc_types: ドキュメントタイプフィルタ（配列、複数選択可能）
            date_filter: 日付フィルタ ('today', 'this_week', 'this_month', 'recent')

        Returns:
            検索結果のリスト
        """
        try:
            # 既存のイベントループがある場合は取得、なければ新規作成
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # ループが既に動作している場合は新しいループを作成
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    result = loop.run_until_complete(
                        self.search_documents(query, embedding, limit, doc_types, date_filter)
                    )
                    return result
            except RuntimeError:
                # イベントループが存在しない場合
                pass

            # asyncio.run()を使用（推奨される方法）
            return asyncio.run(
                self.search_documents(query, embedding, limit, doc_types, date_filter)
            )
        except Exception as e:
            print(f"Sync wrapper error: {e}")
            import traceback
            traceback.print_exc()
            return []

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
                # 'recent' の場合は、作成日時 (created_at) でフィルタリング
                if date_filter == 'recent':
                    created_at_str = result.get('created_at')
                    if created_at_str:
                        try:
                            created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                            # 過去30日以内
                            if (now - created_at).days <= 30:
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
            if workspace:
                rpc_params["filter_workspace"] = workspace

            response = self.client.rpc("match_documents", rpc_params).execute()
            results = response.data if response.data else []

            print(f"[DEBUG] フォールバック検索結果: {len(results)} 件")
            return results

        except Exception as e:
            print(f"Fallback search error: {e}")
            return []

    def _check_date_match(self, doc: Dict[str, Any], target_date: str) -> float:
        """
        ドキュメントのメタデータと本文から target_date を検索
        年を無視して、月・日だけでマッチング（例：12-04）

        Args:
            doc: ドキュメント
            target_date: ターゲット日付（YYYY-MM-DD形式、例：2025-12-04）

        Returns:
            マッチスコア（0.0～0.5）
        """
        # 年を除いた月・日のみを抽出（例：2025-12-04 → 12-04）
        try:
            parts = target_date.split('-')
            month = int(parts[1])
            day = int(parts[2])
        except:
            return 0.0

        # 検索パターン：「12/4」「12月4日」「12-04」など
        date_patterns = [
            f"{month}/{day}",           # 12/4
            f"{month:02d}/{day:02d}",   # 12/04
            f"{month}月{day}日",         # 12月4日
            f"{month:02d}-{day:02d}",   # 12-04
        ]

        # 1. 本文（content, summary, attachment_text）を検索 - 最優先
        text_fields = ['content', 'summary', 'attachment_text']
        for field in text_fields:
            text = doc.get(field, '')
            if text:
                for pattern in date_patterns:
                    if pattern in text:
                        print(f"[DEBUG] 本文で日付マッチ: {doc.get('file_name')} に '{pattern}' が含まれる")
                        return 0.5  # 本文マッチで +0.5 ブースト（最優先）

        # 2. メタデータの weekly_schedule をチェック
        metadata = doc.get('metadata', {})
        weekly_schedule = metadata.get('weekly_schedule', [])
        if isinstance(weekly_schedule, list):
            for day_item in weekly_schedule:
                if isinstance(day_item, dict):
                    date = day_item.get('date', '')
                    if date:
                        try:
                            doc_month = int(date.split('-')[1])
                            doc_day = int(date.split('-')[2])
                            if month == doc_month and day == doc_day:
                                return 0.3  # メタデータマッチで +0.3 ブースト
                        except:
                            pass

        # 3. document_date をチェック
        document_date = doc.get('document_date', '')
        if document_date:
            try:
                doc_month = int(str(document_date).split('-')[1])
                doc_day = int(str(document_date).split('-')[2])
                if month == doc_month and day == doc_day:
                    return 0.2
            except:
                pass

        return 0.0

    def _calculate_keyword_match_score(
        self,
        file_name: str,
        keywords: List[str],
        query: str
    ) -> float:
        """
        file_name とクエリの一致度を計算

        Args:
            file_name: ファイル名
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
                if kw in file_name:
                    return 1.0  # 「学年通信（29）」が完全一致

        # マッチしたキーワードの数をカウント
        matched_keywords = []
        for kw in keywords:
            if kw in file_name:
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
        workspace: Optional[str] = None,
        file_type: Optional[str] = None,
        review_status: Optional[str] = None,
        exclude_workspace: Optional[str] = None,
        doc_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        レビュー対象のドキュメントを取得

        通常モード（search_query=None）: 未レビューのドキュメントのみ取得
        検索モード（search_query指定）: レビュー状態に関係なく全件から検索

        Args:
            limit: 取得する最大件数
            search_query: 検索クエリ（IDまたはファイル名で部分一致）
            workspace: ワークスペースフィルタ（'business', 'personal', またはNone）
            file_type: ファイルタイプフィルタ（'pdf', 'email', またはNone）
            review_status: レビューステータスフィルタ（'reviewed', 'pending', 'all', またはNone）
            exclude_workspace: 除外するワークスペース（'gmail'など）
            doc_type: ドキュメントタイプフィルタ（'DM-mail', 'JOB-mail'など）

        Returns:
            ドキュメントのリスト（更新日時降順）
        """
        try:
            query = self.client.table('Rawdata_FILE_AND_MAIL').select('*')

            # File typeフィルタを適用
            if file_type:
                query = query.eq('file_type', file_type)

            # Workspaceフィルタを適用
            if workspace:
                query = query.eq('workspace', workspace)

            # Workspace除外フィルタを適用
            if exclude_workspace:
                query = query.neq('workspace', exclude_workspace)

            # Doc typeフィルタを適用
            if doc_type:
                query = query.eq('doc_type', doc_type)

            # Review statusフィルタを適用
            if review_status == 'reviewed':
                query = query.eq('review_status', 'reviewed')
            elif review_status == 'pending':
                # review_statusが'pending'またはNULLの場合
                query = query.or_('review_status.eq.pending,review_status.is.null')

            if search_query:
                # 検索モード: レビュー状態に関係なく検索
                # IDでの完全一致検索を試みる
                if len(search_query) == 36 or len(search_query) == 8:  # UUID形式またはID先頭8文字
                    # IDで検索（部分一致）
                    response_id = query.ilike('id', f'{search_query}%').limit(limit).execute()
                    if response_id.data:
                        return response_id.data

                # ファイル名で部分一致検索
                response = (
                    query
                    .ilike('file_name', f'%{search_query}%')
                    .order('updated_at', desc=True)
                    .limit(limit)
                    .execute()
                )
                return response.data if response.data else []
            else:
                # 通常モード: 全ドキュメントを取得（is_reviewed カラムは削除）
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
                'review_status': 'reviewed'  # review_status カラムを使用
            }
            if reviewed_by:
                update_data['reviewed_by'] = reviewed_by

            response = (
                self.client.table('Rawdata_FILE_AND_MAIL')
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
                'review_status': 'pending',  # review_status カラムを使用
                'reviewed_by': None
            }

            response = (
                self.client.table('Rawdata_FILE_AND_MAIL')
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
            # 未レビューの件数（review_status = 'pending' または NULL）
            unreviewed_response = (
                self.client.table('Rawdata_FILE_AND_MAIL')
                .select('*', count='exact')
                .in_('review_status', ['pending', None])
                .execute()
            )
            unreviewed_count = unreviewed_response.count if unreviewed_response else 0

            # レビュー済みの件数（review_status = 'reviewed'）
            reviewed_response = (
                self.client.table('Rawdata_FILE_AND_MAIL')
                .select('*', count='exact')
                .eq('review_status', 'reviewed')
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
        データベース内の利用可能なワークスペース一覧を取得

        Returns:
            ワークスペース名のリスト（重複なし、ソート済み）
        """
        try:
            # 全ドキュメントからworkspaceを取得
            response = self.client.table('Rawdata_FILE_AND_MAIL').select('workspace').execute()

            workspaces = set()
            for doc in response.data:
                ws = doc.get('workspace')
                if ws:  # NoneやNULLを除外
                    workspaces.add(ws)

            return sorted(list(workspaces))
        except Exception as e:
            print(f"Error getting available workspaces: {e}")
            return []

    def get_available_doc_types(self) -> List[str]:
        """
        データベース内の利用可能なドキュメントタイプ一覧を取得

        Returns:
            ドキュメントタイプ名のリスト（重複なし、ソート済み）
        """
        try:
            # 全ドキュメントからdoc_typeを取得
            response = self.client.table('Rawdata_FILE_AND_MAIL').select('doc_type').execute()

            doc_types = set()
            for doc in response.data:
                dt = doc.get('doc_type')
                if dt:  # NoneやNULLを除外
                    doc_types.add(dt)

            return sorted(list(doc_types))
        except Exception as e:
            print(f"Error getting available doc_types: {e}")
            return []

    def get_workspace_hierarchy(self) -> Dict[str, List[str]]:
        """
        workspace別のdoc_type階層構造を取得

        Returns:
            {workspace: [doc_type1, doc_type2, ...]} の辞書
        """
        try:
            # workspaceとdoc_typeの組み合わせを取得
            response = self.client.table('Rawdata_FILE_AND_MAIL').select('workspace, doc_type').execute()

            hierarchy = {}
            for doc in response.data:
                workspace = doc.get('workspace')
                doc_type = doc.get('doc_type')

                if workspace and doc_type:
                    if workspace not in hierarchy:
                        hierarchy[workspace] = set()
                    hierarchy[workspace].add(doc_type)

            # setをソート済みリストに変換
            result = {ws: sorted(list(types)) for ws, types in hierarchy.items()}

            # workspaceもソート
            result = dict(sorted(result.items()))

            print(f"[DEBUG] workspace階層構造: {len(result)} workspaces")
            return result

        except Exception as e:
            print(f"Error getting workspace hierarchy: {e}")
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
            response = self.client.table('Rawdata_FILE_AND_MAIL').select('*').eq('id', doc_id).execute()
            if response.data:
                return response.data[0]
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
        ドキュメントのメタデータと文書タイプを更新

        注意: この関数は修正履歴を記録しません。
        修正履歴を記録する場合は record_correction() を使用してください。

        Args:
            doc_id: ドキュメントID
            new_metadata: 新しいメタデータ
            new_doc_type: 新しい文書タイプ（オプション）

        Returns:
            成功したかどうか
        """
        try:
            update_data = {'metadata': new_metadata}
            if new_doc_type:
                update_data['doc_type'] = new_doc_type

            response = (
                self.client.table('Rawdata_FILE_AND_MAIL')
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
            new_doc_type: 新しい文書タイプ（オプション）
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
            if new_doc_type and new_doc_type != old_doc_type:
                update_data['doc_type'] = new_doc_type
                logger.info(f"[record_correction] doc_type変更: {old_doc_type} → {new_doc_type}")

            # year, month のトップレベルカラムへの同期は削除（metadata 内で管理）

            logger.info(f"[record_correction] Rawdata_FILE_AND_MAIL 更新開始: doc_id={doc_id}")

            document_response = (
                self.client.table('Rawdata_FILE_AND_MAIL')
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
                self.client.table('Rawdata_FILE_AND_MAIL')
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
        既に処理済みのファイルIDリストを取得

        Returns:
            処理済みファイルのsource_id（Google Drive file ID）のリスト
        """
        try:
            # source_idが存在するすべてのドキュメントを取得
            response = (
                self.client.table('Rawdata_FILE_AND_MAIL')
                .select('source_id')
                .not_.is_('source_id', 'null')
                .execute()
            )

            # source_idのリストを抽出
            if response.data:
                file_ids = [doc['source_id'] for doc in response.data if doc.get('source_id')]
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
        try:
            # content_hashが一致するドキュメントを検索
            response = (
                self.client.table('Rawdata_FILE_AND_MAIL')
                .select('id, file_name, content_hash')
                .eq('content_hash', content_hash)
                .limit(1)
                .execute()
            )

            if response.data:
                # 重複が見つかった
                existing_doc = response.data[0]
                print(f"⚠️  重複検知: content_hash={content_hash[:16]}... は既に存在します")
                print(f"   既存ファイル: {existing_doc.get('file_name', 'Unknown')}")
                return True

            # 重複なし
            return False

        except Exception as e:
            print(f"Error checking duplicate hash: {e}")
            # エラー時は安全側に倒して重複なしとして扱う
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
                self.client.table('Rawdata_FILE_AND_MAIL')
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