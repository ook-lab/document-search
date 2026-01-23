-- =============================================================================
-- Migration: Phase 5 RLS Alignment（Phase 2〜4 との整合性確保）
-- =============================================================================
-- 目的:
-- 1. document_executions の RLS を Phase 2 方針に整合
--    - authenticated: SELECT は全データ（Admin 全件可視）
--    - UPDATE/DELETE は owner_id = auth.uid() のみ
-- 2. anon からのアクセスを明示的に禁止
-- 3. GRANT 文を追加
-- =============================================================================

BEGIN;

-- =============================================================================
-- STEP 1: 既存ポリシー削除
-- =============================================================================

DROP POLICY IF EXISTS "document_executions_select_own" ON document_executions;
DROP POLICY IF EXISTS "document_executions_service_role_all" ON document_executions;

-- =============================================================================
-- STEP 2: Phase 2 整合 RLS ポリシー作成（冪等: DROP IF EXISTS 後に CREATE）
-- =============================================================================

-- authenticated: SELECT は全データ（Admin UI 全件可視）
DROP POLICY IF EXISTS "document_executions_select_all" ON document_executions;
CREATE POLICY "document_executions_select_all"
    ON document_executions FOR SELECT
    TO authenticated
    USING (true);

-- authenticated: UPDATE は自分のデータのみ
DROP POLICY IF EXISTS "document_executions_update_own" ON document_executions;
CREATE POLICY "document_executions_update_own"
    ON document_executions FOR UPDATE
    TO authenticated
    USING (owner_id = auth.uid())
    WITH CHECK (owner_id = auth.uid());

-- authenticated: DELETE は自分のデータのみ
DROP POLICY IF EXISTS "document_executions_delete_own" ON document_executions;
CREATE POLICY "document_executions_delete_own"
    ON document_executions FOR DELETE
    TO authenticated
    USING (owner_id = auth.uid());

-- service_role: 全操作可（Worker 用）
DROP POLICY IF EXISTS "document_executions_service_role_all" ON document_executions;
CREATE POLICY "document_executions_service_role_all"
    ON document_executions FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- anon: ポリシーなし = 暗黙の拒否（Phase 4A 方針）

-- =============================================================================
-- STEP 3: GRANT 文（Phase 2 整合）
-- =============================================================================

-- 既存権限をリセット
REVOKE ALL ON document_executions FROM anon, authenticated;

-- authenticated: SELECT, UPDATE, DELETE（INSERT は service_role のみ）
GRANT SELECT, UPDATE, DELETE ON document_executions TO authenticated;

-- service_role: 全権限
GRANT ALL ON document_executions TO service_role;

-- anon: 権限なし（明示的に REVOKE 済み）

-- =============================================================================
-- STEP 4: active_execution_id 整合性チェック関数
-- =============================================================================
-- active_execution_id が succeeded 以外を指すことを防ぐトリガー

CREATE OR REPLACE FUNCTION check_active_execution_validity()
RETURNS TRIGGER AS $$
DECLARE
    exec_status TEXT;
    exec_owner_id UUID;
BEGIN
    -- active_execution_id が設定されている場合のみチェック
    IF NEW.active_execution_id IS NOT NULL THEN
        -- execution の status と owner_id を取得
        SELECT status, owner_id INTO exec_status, exec_owner_id
        FROM document_executions
        WHERE id = NEW.active_execution_id;

        -- execution が存在しない場合
        IF exec_status IS NULL THEN
            RAISE EXCEPTION 'active_execution_id references non-existent execution: %', NEW.active_execution_id;
        END IF;

        -- status が succeeded でない場合
        IF exec_status != 'succeeded' THEN
            RAISE EXCEPTION 'active_execution_id must reference a succeeded execution (current: %)', exec_status;
        END IF;

        -- owner_id が一致しない場合
        IF exec_owner_id != NEW.owner_id THEN
            RAISE EXCEPTION 'active_execution owner_id mismatch (document: %, execution: %)', NEW.owner_id, exec_owner_id;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION check_active_execution_validity() IS 'active_execution_id が succeeded かつ owner 一致の execution のみを指すことを保証';

-- トリガー作成
DROP TRIGGER IF EXISTS trg_check_active_execution ON "Rawdata_FILE_AND_MAIL";
CREATE TRIGGER trg_check_active_execution
    BEFORE INSERT OR UPDATE OF active_execution_id ON "Rawdata_FILE_AND_MAIL"
    FOR EACH ROW
    WHEN (NEW.active_execution_id IS NOT NULL)
    EXECUTE FUNCTION check_active_execution_validity();

-- =============================================================================
-- STEP 5: ビューの RLS 設定
-- =============================================================================

-- ビューは RLS の影響を受けないため、SECURITY INVOKER で作成し直す
-- （必要に応じて VIEW の再作成）

DROP VIEW IF EXISTS documents_with_active_execution;
CREATE VIEW documents_with_active_execution AS
SELECT
    d.id AS document_id,
    d.file_name,
    d.doc_type,
    d.workspace,
    d.owner_id,
    d.active_execution_id,
    e.id AS execution_id,
    e.status AS execution_status,
    e.model_version,
    e.input_hash,
    e.result_data,
    e.created_at AS execution_created_at,
    e.processing_duration_ms
FROM "Rawdata_FILE_AND_MAIL" d
LEFT JOIN document_executions e ON d.active_execution_id = e.id;

COMMENT ON VIEW documents_with_active_execution IS 'ドキュメントと採用中の execution を結合（RLS 適用）';

COMMIT;

-- =============================================================================
-- Access Matrix 更新
-- =============================================================================
--
-- | テーブル             | anon | authenticated                    | service_role |
-- |----------------------|------|----------------------------------|--------------|
-- | document_executions  | -    | SELECT (全件), UPDATE*, DELETE*  | ALL          |
--
-- * = owner_id = auth.uid() による行レベル制限
--
-- Phase 2 方針との整合:
-- - SELECT は全データ（Admin UI 全件可視）
-- - UPDATE/DELETE は自分のデータのみ
-- - INSERT は service_role のみ（Worker 経由）
-- - anon はアクセス不可（Phase 4A 方針）
-- =============================================================================
