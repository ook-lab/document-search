-- =============================================================================
-- Migration: owner_id NOT NULL 制約
-- =============================================================================
-- 目的: owner_id 欠落を DB レベルで即死させる（第一防衛線）
-- =============================================================================

BEGIN;

-- =============================================================================
-- STEP 0: SYSTEM ユーザーの確保
-- =============================================================================
-- フォールバック UUID: 00000000-0000-0000-0000-000000000000
-- auth.users に存在しない場合は作成する

DO $$
DECLARE
    system_user_id UUID := '00000000-0000-0000-0000-000000000000';
    user_exists BOOLEAN;
BEGIN
    -- システムユーザーが auth.users に存在するか確認
    SELECT EXISTS (
        SELECT 1 FROM auth.users WHERE id = system_user_id
    ) INTO user_exists;

    IF NOT user_exists THEN
        RAISE NOTICE 'システムユーザーを auth.users に作成します: %', system_user_id;

        -- auth.users に最小限のレコードを作成
        INSERT INTO auth.users (
            id,
            instance_id,
            aud,
            role,
            email,
            encrypted_password,
            email_confirmed_at,
            created_at,
            updated_at,
            confirmation_token,
            recovery_token,
            email_change_token_new,
            email_change
        ) VALUES (
            system_user_id,
            '00000000-0000-0000-0000-000000000000',
            'authenticated',
            'authenticated',
            'system@internal.localhost',
            '', -- パスワードなし（ログイン不可）
            NOW(),
            NOW(),
            NOW(),
            '',
            '',
            '',
            ''
        );

        RAISE NOTICE 'システムユーザーを作成しました';
    ELSE
        RAISE NOTICE 'システムユーザーは既に存在します: %', system_user_id;
    END IF;
END $$;

-- =============================================================================
-- STEP 1: 既存データの owner_id を設定
-- =============================================================================
DO $$
DECLARE
    system_user_id UUID := '00000000-0000-0000-0000-000000000000';
    owner_mapping RECORD;
    updated_count INT;
BEGIN
    RAISE NOTICE 'SYSTEM_OWNER_ID: %', system_user_id;

    -- 推定ロジック:
    -- 1. owner_mapping テーブルが存在する場合、workspace/source_type から推定
    -- 2. 推定できない場合は SYSTEM_OWNER_ID にフォールバック
    --
    -- owner_mapping テーブルの例:
    -- CREATE TABLE IF NOT EXISTS _migration_owner_mapping (
    --     workspace TEXT,
    --     source_type TEXT,
    --     owner_id UUID NOT NULL
    -- );
    -- INSERT INTO _migration_owner_mapping VALUES
    --     ('business', NULL, 'user-a-uuid'),
    --     ('household', NULL, 'user-b-uuid'),
    --     (NULL, 'gmail', 'user-c-uuid');

    -- owner_mapping テーブルが存在するかチェック
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = '_migration_owner_mapping'
    ) THEN
        RAISE NOTICE 'owner_mapping テーブルを使用して owner_id を推定します';

        -- workspace ベースの推定
        UPDATE "Rawdata_FILE_AND_MAIL" doc
        SET owner_id = mapping.owner_id
        FROM _migration_owner_mapping mapping
        WHERE doc.owner_id IS NULL
        AND (
            (mapping.workspace IS NOT NULL AND doc.workspace = mapping.workspace)
            OR (mapping.source_type IS NOT NULL AND doc.source_type = mapping.source_type)
        );

        GET DIAGNOSTICS updated_count = ROW_COUNT;
        RAISE NOTICE 'owner_mapping により % 件の owner_id を設定', updated_count;
    ELSE
        RAISE NOTICE 'owner_mapping テーブルが存在しないため、推定をスキップ';
    END IF;

    -- 推定できなかったデータに SYSTEM_OWNER_ID を設定

    -- Rawdata_FILE_AND_MAIL
    UPDATE "Rawdata_FILE_AND_MAIL"
    SET owner_id = system_user_id
    WHERE owner_id IS NULL;

    GET DIAGNOSTICS updated_count = ROW_COUNT;
    IF updated_count > 0 THEN
        RAISE NOTICE 'Rawdata_FILE_AND_MAIL: % 件に SYSTEM_OWNER_ID を設定', updated_count;
    END IF;

    -- 10_ix_search_index
    -- 親ドキュメントの owner_id を継承（可能な場合）
    UPDATE "10_ix_search_index" idx
    SET owner_id = doc.owner_id
    FROM "Rawdata_FILE_AND_MAIL" doc
    WHERE idx.document_id = doc.id
    AND idx.owner_id IS NULL
    AND doc.owner_id IS NOT NULL;

    GET DIAGNOSTICS updated_count = ROW_COUNT;
    IF updated_count > 0 THEN
        RAISE NOTICE '10_ix_search_index: % 件に親ドキュメントの owner_id を継承', updated_count;
    END IF;

    -- 残りはシステムユーザー
    UPDATE "10_ix_search_index"
    SET owner_id = system_user_id
    WHERE owner_id IS NULL;

    GET DIAGNOSTICS updated_count = ROW_COUNT;
    IF updated_count > 0 THEN
        RAISE NOTICE '10_ix_search_index: % 件に SYSTEM_OWNER_ID を設定', updated_count;
    END IF;

    -- Rawdata_RECEIPT_shops
    UPDATE "Rawdata_RECEIPT_shops"
    SET owner_id = system_user_id
    WHERE owner_id IS NULL;

    GET DIAGNOSTICS updated_count = ROW_COUNT;
    IF updated_count > 0 THEN
        RAISE NOTICE 'Rawdata_RECEIPT_shops: % 件に SYSTEM_OWNER_ID を設定', updated_count;
    END IF;

    -- MASTER_Rules_transaction_dict
    UPDATE "MASTER_Rules_transaction_dict"
    SET created_by = system_user_id
    WHERE created_by IS NULL;

    GET DIAGNOSTICS updated_count = ROW_COUNT;
    IF updated_count > 0 THEN
        RAISE NOTICE 'MASTER_Rules_transaction_dict: % 件に SYSTEM_OWNER_ID を設定', updated_count;
    END IF;

    -- 99_lg_correction_history
    UPDATE "99_lg_correction_history"
    SET corrector_id = system_user_id
    WHERE corrector_id IS NULL;

    GET DIAGNOSTICS updated_count = ROW_COUNT;
    IF updated_count > 0 THEN
        RAISE NOTICE '99_lg_correction_history: % 件に SYSTEM_OWNER_ID を設定', updated_count;
    END IF;

END $$;

-- =============================================================================
-- STEP 2: NOT NULL 制約を追加
-- =============================================================================
-- STEP 0 でシステムユーザーが存在することを保証済み
-- STEP 1 で既存データの owner_id を設定済み

-- Rawdata_FILE_AND_MAIL.owner_id
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ALTER COLUMN owner_id SET NOT NULL;

-- 10_ix_search_index.owner_id
ALTER TABLE "10_ix_search_index"
ALTER COLUMN owner_id SET NOT NULL;

-- Rawdata_RECEIPT_shops.owner_id
ALTER TABLE "Rawdata_RECEIPT_shops"
ALTER COLUMN owner_id SET NOT NULL;

-- MASTER_Rules_transaction_dict.created_by
ALTER TABLE "MASTER_Rules_transaction_dict"
ALTER COLUMN created_by SET NOT NULL;

-- 99_lg_correction_history.corrector_id
ALTER TABLE "99_lg_correction_history"
ALTER COLUMN corrector_id SET NOT NULL;

-- =============================================================================
-- STEP 3: DEFAULT 設定（authenticated 経路用）
-- =============================================================================
-- authenticated 経路では auth.uid() がデフォルト値として使用される
-- ただし service_role 経路では RLS がバイパスされるため、
-- コード側で明示的に設定する必要がある

-- 注意: Supabase では auth.uid() を DEFAULT に設定できるが、
-- service_role 経路では NULL になるため、コード側で必ず指定する必要がある

-- 参考: DEFAULT auth.uid() は以下のように設定可能だが、
-- service_role 経路での問題を避けるため、今回は設定しない
-- ALTER TABLE "Rawdata_FILE_AND_MAIL"
-- ALTER COLUMN owner_id SET DEFAULT auth.uid();

COMMIT;

-- =============================================================================
-- 確認クエリ
-- =============================================================================
-- 以下のクエリで制約が正しく設定されたか確認できます:
--
-- SELECT
--     table_name,
--     column_name,
--     is_nullable,
--     column_default
-- FROM information_schema.columns
-- WHERE table_schema = 'public'
-- AND column_name IN ('owner_id', 'created_by', 'corrector_id')
-- ORDER BY table_name;
