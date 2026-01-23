"""
ExecutionPolicy - 実行可否判断のSSOT

【設計原則】
- Worker が処理を実行する前に必ずこの関数を通す
- DB の状態のみを見て判断（環境変数・設定で止めない）
- 分散ガードを根絶し、「止める条件」をコード上でも1か所に固定

【STOP判定のSSOT構造】
    ops_requests テーブル（SSOT）
        ↓ ops.py requests --apply で適用
    worker_state.stop_requested（派生キャッシュ）
        ↓ Worker が読み取り
    ExecutionPolicy.can_execute()

- ops_requests が真実（SSOT）
- worker_state.stop_requested は派生キャッシュ（ops.py のみが書き込み）
- Worker は両方を読むが、stop_requested には書き込まない
- 将来的に worker_state.stop_requested は廃止予定（2025年Q2目標）

【使い方】
    from shared.processing.execution_policy import ExecutionPolicy

    policy = ExecutionPolicy()
    result = policy.can_execute(doc_id=doc_id)  # または workspace=ws

    if not result.allowed:
        logger.warning(f"実行拒否: {result.deny_code} - {result.deny_reason}")
        return

    # 実行OK
    await process_document(doc)

【deny_code 一覧】
- STOP_REQUESTED: 停止要求あり（ops_requests または worker_state）
- PAUSED: workspace が一時停止中
- SKIPPED: ドキュメントがスキップ対象（skip_code が設定済み）
- LEASED: 他のWorkerが処理中（リース取得済み）
- RETRY_LIMIT: リトライ上限到達（attempt_count >= MAX_ATTEMPTS）
- NOT_FOUND: ドキュメントが存在しない
- INVALID_STATUS: 処理対象外のステータス

【DB参照カラム（Rawdata_FILE_AND_MAIL）】
- id, processing_status, workspace: 基本情報
- skip_code: スキップ対象判定（NOT NULL なら処理しない）
- attempt_count: リトライ上限判定（>= MAX_ATTEMPTS なら処理しない）
"""
from dataclasses import dataclass
from typing import Optional, List
from loguru import logger


# リトライ上限（この回数以上試行されたドキュメントは処理しない）
MAX_ATTEMPTS = 5

# ExecutionPolicy が参照する Rawdata_FILE_AND_MAIL のカラム
REQUIRED_COLUMNS = ['id', 'processing_status', 'workspace', 'skip_code', 'attempt_count']


@dataclass
class ExecutionResult:
    """実行可否判断の結果"""
    allowed: bool
    deny_code: Optional[str] = None
    deny_reason: Optional[str] = None

    def __bool__(self):
        return self.allowed


class ExecutionPolicy:
    """
    実行可否判断のSSOT

    Worker が処理を実行する前に必ずここを通る。
    DBの状態のみを見て判断し、分散ガードを防ぐ。
    """

    # 許可されるステータス
    # queued: キューに入った状態（メイン）
    # pending: 未キュー状態（CLI直接処理用）
    ALLOWED_STATUSES = {'queued', 'pending', None}

    def __init__(self, db_client=None):
        """
        Args:
            db_client: DatabaseClient インスタンス（省略時は内部で作成）

        Note:
            ExecutionPolicy は Worker 専用のため、service_role で接続する。
            これにより ops_requests / worker_state テーブルへのアクセスが可能になる。
        """
        if db_client is None:
            from shared.common.database.client import DatabaseClient
            db_client = DatabaseClient(use_service_role=True)
        self.db = db_client
        self._schema_validated = False

    def _validate_schema(self) -> None:
        """
        参照するカラムがDBに存在するか検証（初回のみ）

        存在しないカラムがあれば例外を投げて即停止（サイレント失敗禁止）
        """
        if self._schema_validated:
            return

        try:
            # 1行だけ取得してカラム存在を確認
            result = self.db.client.table('Rawdata_FILE_AND_MAIL').select(
                ', '.join(REQUIRED_COLUMNS)
            ).limit(1).execute()

            self._schema_validated = True
            logger.debug(f"[ExecutionPolicy] スキーマ検証OK: {REQUIRED_COLUMNS}")

        except Exception as e:
            error_msg = str(e)
            if 'does not exist' in error_msg:
                # カラムが存在しない
                raise RuntimeError(
                    f"[ExecutionPolicy] スキーマ不整合: {error_msg}\n"
                    f"参照カラム: {REQUIRED_COLUMNS}\n"
                    f"DBスキーマを確認してください。"
                )
            else:
                # その他のエラー（接続エラー等）は警告のみで継続
                logger.warning(f"[ExecutionPolicy] スキーマ検証スキップ: {e}")
                self._schema_validated = True

    def can_execute(
        self,
        doc_id: Optional[str] = None,
        workspace: Optional[str] = None
    ) -> ExecutionResult:
        """
        実行可否を判断する（唯一の判定点）

        Args:
            doc_id: ドキュメントID（単一ドキュメント処理時）
            workspace: ワークスペース名（バッチ処理時）

        Returns:
            ExecutionResult: 実行可否と拒否理由
        """
        # 1. 停止チェック（ops_requests - グローバル/workspace/doc_id スコープ）
        # ※ workspace の PAUSE も ops_requests で統一チェック済み
        stop_check = self._check_stop_request(workspace=workspace, doc_id=doc_id)
        if not stop_check.allowed:
            return stop_check

        # 2. ドキュメント固有のチェック
        if doc_id:
            doc_check = self._check_document(doc_id)
            if not doc_check.allowed:
                return doc_check

        return ExecutionResult(allowed=True)

    def can_execute_document(self, doc: dict) -> ExecutionResult:
        """
        ドキュメント辞書から実行可否を判断

        Args:
            doc: ドキュメント情報（id, workspace, processing_status等を含む）

        Returns:
            ExecutionResult: 実行可否と拒否理由
        """
        doc_id = doc.get('id')
        workspace = doc.get('workspace')

        if not doc_id:
            return ExecutionResult(
                allowed=False,
                deny_code='INVALID_DOC',
                deny_reason='ドキュメントIDがありません'
            )

        # 停止チェック（スコープ対応）
        # ※ workspace の PAUSE も ops_requests で統一チェック済み
        stop_check = self._check_stop_request(workspace=workspace, doc_id=doc_id)
        if not stop_check.allowed:
            return stop_check

        # ドキュメントステータスチェック（DBを叩かずに辞書から判断）
        status = doc.get('processing_status')
        if status not in self.ALLOWED_STATUSES:
            return ExecutionResult(
                allowed=False,
                deny_code='INVALID_STATUS',
                deny_reason=f'処理対象外のステータス: {status}'
            )

        # skip_code チェック（スキップ対象）
        skip_code = doc.get('skip_code')
        if skip_code:
            return ExecutionResult(
                allowed=False,
                deny_code='SKIPPED',
                deny_reason=f'スキップ対象（skip_code={skip_code}）'
            )

        # attempt_count チェック（リトライ上限）
        attempt_count = doc.get('attempt_count') or 0
        if attempt_count >= MAX_ATTEMPTS:
            return ExecutionResult(
                allowed=False,
                deny_code='RETRY_LIMIT',
                deny_reason=f'リトライ上限到達（attempt_count={attempt_count} >= {MAX_ATTEMPTS}）'
            )

        return ExecutionResult(allowed=True)

    def _check_stop_request(
        self,
        workspace: Optional[str] = None,
        doc_id: Optional[str] = None
    ) -> ExecutionResult:
        """停止要求をチェック（スコープ対応）

        【SSOT構造】
        1. ops_requests（SSOT）を先にチェック
           - グローバル停止（scope_type='global'）
           - ワークスペース停止（scope_type='workspace', scope_id=workspace）
           - ドキュメント停止（scope_type='document', scope_id=doc_id）
        2. worker_state.stop_requested（派生キャッシュ）も併せてチェック
        3. どれかに停止要求があれば停止

        注意: worker_state.stop_requested は派生キャッシュであり、SSOTではない。
              ops.py requests --apply が ops_requests の STOP を適用する際に更新される。
              Worker はここに書き込んではいけない（読み取りのみ）。
              将来的に廃止予定（2025年Q2目標）。
        """
        # 1. ops_requests（SSOT）をチェック
        try:
            # 対象となるスコープを構築
            scopes = [('global', None)]  # グローバルは常にチェック
            if workspace:
                scopes.append(('workspace', workspace))
            if doc_id:
                scopes.append(('document', doc_id))

            # 各スコープの停止要求をチェック
            for scope_type, scope_id in scopes:
                query = self.db.client.table('ops_requests').select(
                    'id, request_type, scope_type, scope_id'
                ).eq('status', 'queued').in_(
                    'request_type', ['STOP', 'PAUSE']
                ).eq('scope_type', scope_type)

                if scope_id:
                    query = query.eq('scope_id', scope_id)
                else:
                    query = query.is_('scope_id', 'null')

                result = query.limit(1).execute()

                if result.data:
                    req = result.data[0]
                    scope_desc = f"{req['scope_type']}"
                    if req.get('scope_id'):
                        scope_desc += f":{req['scope_id']}"
                    return ExecutionResult(
                        allowed=False,
                        deny_code='STOP_REQUESTED',
                        deny_reason=f"停止要求あり（SSOT: {req['request_type']} @ {scope_desc}）"
                    )
        except Exception as e:
            # ops_requests テーブルが存在しない場合は無視（派生キャッシュのみで判断）
            logger.debug(f"ops_requests チェックスキップ（テーブル未作成の可能性）: {e}")

        # 2. worker_state.stop_requested（派生キャッシュ）をチェック
        return self._check_stop_requested_cache()

    def _check_stop_requested_cache(self) -> ExecutionResult:
        """派生キャッシュ: worker_state.stop_requested をチェック

        注意: これは派生キャッシュであり、SSOTではない。
              ops.py requests --apply が ops_requests の STOP を適用する際に更新される。
              Worker はここに書き込んではいけない（読み取りのみ）。
              将来的に廃止予定（2025年Q2目標）。
        """
        try:
            result = self.db.client.table('worker_state').select(
                'stop_requested'
            ).eq('id', 1).limit(1).execute()

            if result.data and result.data[0].get('stop_requested'):
                return ExecutionResult(
                    allowed=False,
                    deny_code='STOP_REQUESTED',
                    deny_reason='停止要求あり（派生キャッシュ: worker_state.stop_requested=true）'
                )

            return ExecutionResult(allowed=True)

        except Exception as e:
            logger.warning(f"worker_state チェックエラー（継続）: {e}")
            return ExecutionResult(allowed=True)

    def _check_document(self, doc_id: str) -> ExecutionResult:
        """単一ドキュメントの実行可否をチェック"""
        # スキーマ検証（初回のみ）
        self._validate_schema()

        try:
            result = self.db.client.table('Rawdata_FILE_AND_MAIL').select(
                ', '.join(REQUIRED_COLUMNS)
            ).eq('id', doc_id).limit(1).execute()

            if not result.data:
                return ExecutionResult(
                    allowed=False,
                    deny_code='NOT_FOUND',
                    deny_reason=f'ドキュメントが見つかりません: {doc_id}'
                )

            doc = result.data[0]

            # ステータスチェック
            status = doc.get('processing_status')
            if status not in self.ALLOWED_STATUSES:
                return ExecutionResult(
                    allowed=False,
                    deny_code='INVALID_STATUS',
                    deny_reason=f'処理対象外のステータス: {status}'
                )

            # skip_code チェック（スキップ対象）
            skip_code = doc.get('skip_code')
            if skip_code:
                return ExecutionResult(
                    allowed=False,
                    deny_code='SKIPPED',
                    deny_reason=f'スキップ対象（skip_code={skip_code}）'
                )

            # attempt_count チェック（リトライ上限）
            attempt_count = doc.get('attempt_count') or 0
            if attempt_count >= MAX_ATTEMPTS:
                return ExecutionResult(
                    allowed=False,
                    deny_code='RETRY_LIMIT',
                    deny_reason=f'リトライ上限到達（attempt_count={attempt_count} >= {MAX_ATTEMPTS}）'
                )

            # ワークスペース一時停止チェック
            workspace = doc.get('workspace')
            if workspace:
                pause_check = self._check_workspace_pause(workspace)
                if not pause_check.allowed:
                    return pause_check

            return ExecutionResult(allowed=True)

        except Exception as e:
            logger.error(f"ドキュメントチェックエラー: {e}")
            return ExecutionResult(
                allowed=False,
                deny_code='CHECK_ERROR',
                deny_reason=f'チェック中にエラー: {e}'
            )


# シングルトンインスタンス
_execution_policy_instance: Optional[ExecutionPolicy] = None


def get_execution_policy() -> ExecutionPolicy:
    """ExecutionPolicy のシングルトンインスタンスを取得"""
    global _execution_policy_instance
    if _execution_policy_instance is None:
        _execution_policy_instance = ExecutionPolicy()
    return _execution_policy_instance
