-- =============================================================================
-- Migration: RLS Policies with auth.uid() and owner_id
-- =============================================================================
-- 目的: UPDATE/DELETE は自分のデータのみ、SELECT は全データ
-- =============================================================================

BEGIN;

-- =============================================================================
-- STEP 1: RLS 有効化
-- =============================================================================

ALTER TABLE "Rawdata_FILE_AND_MAIL" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "10_ix_search_index" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "Rawdata_RECEIPT_shops" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "Rawdata_RECEIPT_items" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "99_lg_correction_history" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "99_lg_image_proc_log" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "MASTER_Rules_transaction_dict" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "MASTER_Categories_product" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "MASTER_Categories_purpose" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "MASTER_Categories_expense" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "MASTER_Rules_expense_mapping" ENABLE ROW LEVEL SECURITY;

-- =============================================================================
-- STEP 2: 既存ポリシー削除
-- =============================================================================

-- Rawdata_FILE_AND_MAIL
DROP POLICY IF EXISTS "anon_select_rawdata" ON "Rawdata_FILE_AND_MAIL";
DROP POLICY IF EXISTS "authenticated_select_rawdata" ON "Rawdata_FILE_AND_MAIL";
DROP POLICY IF EXISTS "authenticated_update_rawdata" ON "Rawdata_FILE_AND_MAIL";
DROP POLICY IF EXISTS "authenticated_delete_rawdata" ON "Rawdata_FILE_AND_MAIL";
DROP POLICY IF EXISTS "authenticated_update_own_rawdata" ON "Rawdata_FILE_AND_MAIL";
DROP POLICY IF EXISTS "authenticated_delete_own_rawdata" ON "Rawdata_FILE_AND_MAIL";
DROP POLICY IF EXISTS "service_role_all_rawdata" ON "Rawdata_FILE_AND_MAIL";

-- 10_ix_search_index
DROP POLICY IF EXISTS "anon_select_search_index" ON "10_ix_search_index";
DROP POLICY IF EXISTS "authenticated_select_search_index" ON "10_ix_search_index";
DROP POLICY IF EXISTS "authenticated_insert_search_index" ON "10_ix_search_index";
DROP POLICY IF EXISTS "authenticated_delete_search_index" ON "10_ix_search_index";
DROP POLICY IF EXISTS "authenticated_insert_own_search_index" ON "10_ix_search_index";
DROP POLICY IF EXISTS "authenticated_delete_own_search_index" ON "10_ix_search_index";
DROP POLICY IF EXISTS "service_role_all_search_index" ON "10_ix_search_index";

-- Rawdata_RECEIPT_shops
DROP POLICY IF EXISTS "authenticated_select_receipt_shops" ON "Rawdata_RECEIPT_shops";
DROP POLICY IF EXISTS "authenticated_update_own_receipt_shops" ON "Rawdata_RECEIPT_shops";
DROP POLICY IF EXISTS "service_role_all_receipt_shops" ON "Rawdata_RECEIPT_shops";

-- Rawdata_RECEIPT_items
DROP POLICY IF EXISTS "authenticated_select_receipt_items" ON "Rawdata_RECEIPT_items";
DROP POLICY IF EXISTS "authenticated_update_receipt_items" ON "Rawdata_RECEIPT_items";
DROP POLICY IF EXISTS "authenticated_update_own_receipt_items" ON "Rawdata_RECEIPT_items";
DROP POLICY IF EXISTS "service_role_all_receipt_items" ON "Rawdata_RECEIPT_items";

-- 99_lg_correction_history
DROP POLICY IF EXISTS "authenticated_select_correction" ON "99_lg_correction_history";
DROP POLICY IF EXISTS "authenticated_insert_correction" ON "99_lg_correction_history";
DROP POLICY IF EXISTS "authenticated_insert_own_correction" ON "99_lg_correction_history";
DROP POLICY IF EXISTS "service_role_all_correction" ON "99_lg_correction_history";

-- 99_lg_image_proc_log
DROP POLICY IF EXISTS "authenticated_select_image_proc_log" ON "99_lg_image_proc_log";
DROP POLICY IF EXISTS "service_role_all_image_proc_log" ON "99_lg_image_proc_log";

-- MASTER_Rules_transaction_dict
DROP POLICY IF EXISTS "authenticated_select_master_rules_trans" ON "MASTER_Rules_transaction_dict";
DROP POLICY IF EXISTS "authenticated_insert_master_rules_trans" ON "MASTER_Rules_transaction_dict";
DROP POLICY IF EXISTS "authenticated_update_master_rules_trans" ON "MASTER_Rules_transaction_dict";
DROP POLICY IF EXISTS "authenticated_insert_own_master_rules_trans" ON "MASTER_Rules_transaction_dict";
DROP POLICY IF EXISTS "authenticated_update_own_master_rules_trans" ON "MASTER_Rules_transaction_dict";
DROP POLICY IF EXISTS "service_role_all_master_rules_trans" ON "MASTER_Rules_transaction_dict";

-- MASTER_Categories_*
DROP POLICY IF EXISTS "authenticated_select_master_cat_product" ON "MASTER_Categories_product";
DROP POLICY IF EXISTS "service_role_all_master_cat_product" ON "MASTER_Categories_product";
DROP POLICY IF EXISTS "authenticated_select_master_cat_purpose" ON "MASTER_Categories_purpose";
DROP POLICY IF EXISTS "service_role_all_master_cat_purpose" ON "MASTER_Categories_purpose";
DROP POLICY IF EXISTS "authenticated_select_master_cat_expense" ON "MASTER_Categories_expense";
DROP POLICY IF EXISTS "service_role_all_master_cat_expense" ON "MASTER_Categories_expense";
DROP POLICY IF EXISTS "authenticated_select_master_rules_expense" ON "MASTER_Rules_expense_mapping";
DROP POLICY IF EXISTS "service_role_all_master_rules_expense" ON "MASTER_Rules_expense_mapping";

-- =============================================================================
-- STEP 3: 新規ポリシー作成
-- =============================================================================

-- -----------------------------------------------
-- Rawdata_FILE_AND_MAIL
-- anon: SELECT (doc-search API)
-- authenticated: SELECT (全データ), UPDATE/DELETE (自分のデータのみ)
-- -----------------------------------------------
CREATE POLICY "anon_select_rawdata"
ON "Rawdata_FILE_AND_MAIL" FOR SELECT TO anon
USING (true);

CREATE POLICY "authenticated_select_rawdata"
ON "Rawdata_FILE_AND_MAIL" FOR SELECT TO authenticated
USING (true);

-- UPDATE: 自分のデータのみ (owner_id = auth.uid())
-- WITH CHECK: 更新後も自分のデータである必要がある（owner_id の書き換え防止）
CREATE POLICY "authenticated_update_own_rawdata"
ON "Rawdata_FILE_AND_MAIL" FOR UPDATE TO authenticated
USING (owner_id = auth.uid())
WITH CHECK (owner_id = auth.uid());

-- DELETE: 自分のデータのみ
CREATE POLICY "authenticated_delete_own_rawdata"
ON "Rawdata_FILE_AND_MAIL" FOR DELETE TO authenticated
USING (owner_id = auth.uid());

-- service_role: 全操作可（Worker用）
CREATE POLICY "service_role_all_rawdata"
ON "Rawdata_FILE_AND_MAIL" FOR ALL TO service_role
USING (true)
WITH CHECK (true);

-- -----------------------------------------------
-- 10_ix_search_index
-- anon: SELECT (doc-search API)
-- authenticated: SELECT (全データ), INSERT/DELETE (自分のデータのみ)
-- -----------------------------------------------
CREATE POLICY "anon_select_search_index"
ON "10_ix_search_index" FOR SELECT TO anon
USING (true);

CREATE POLICY "authenticated_select_search_index"
ON "10_ix_search_index" FOR SELECT TO authenticated
USING (true);

-- INSERT: owner_id を自分に設定する必要がある
CREATE POLICY "authenticated_insert_own_search_index"
ON "10_ix_search_index" FOR INSERT TO authenticated
WITH CHECK (owner_id = auth.uid());

-- DELETE: 自分のデータのみ
CREATE POLICY "authenticated_delete_own_search_index"
ON "10_ix_search_index" FOR DELETE TO authenticated
USING (owner_id = auth.uid());

-- service_role: 全操作可
CREATE POLICY "service_role_all_search_index"
ON "10_ix_search_index" FOR ALL TO service_role
USING (true)
WITH CHECK (true);

-- -----------------------------------------------
-- Rawdata_RECEIPT_shops
-- authenticated: SELECT (全データ), UPDATE (自分のデータのみ)
-- ※ INSERT/DELETE は Worker が行う
-- -----------------------------------------------
CREATE POLICY "authenticated_select_receipt_shops"
ON "Rawdata_RECEIPT_shops" FOR SELECT TO authenticated
USING (true);

CREATE POLICY "authenticated_update_own_receipt_shops"
ON "Rawdata_RECEIPT_shops" FOR UPDATE TO authenticated
USING (owner_id = auth.uid())
WITH CHECK (owner_id = auth.uid());

CREATE POLICY "service_role_all_receipt_shops"
ON "Rawdata_RECEIPT_shops" FOR ALL TO service_role
USING (true)
WITH CHECK (true);

-- -----------------------------------------------
-- Rawdata_RECEIPT_items
-- authenticated: SELECT (全データ), UPDATE (親レシートが自分のデータのみ)
-- -----------------------------------------------
CREATE POLICY "authenticated_select_receipt_items"
ON "Rawdata_RECEIPT_items" FOR SELECT TO authenticated
USING (true);

-- UPDATE: 親レシートの owner_id が自分である必要がある
CREATE POLICY "authenticated_update_own_receipt_items"
ON "Rawdata_RECEIPT_items" FOR UPDATE TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM "Rawdata_RECEIPT_shops" rs
        WHERE rs.id = "Rawdata_RECEIPT_items".receipt_id
        AND rs.owner_id = auth.uid()
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1 FROM "Rawdata_RECEIPT_shops" rs
        WHERE rs.id = "Rawdata_RECEIPT_items".receipt_id
        AND rs.owner_id = auth.uid()
    )
);

CREATE POLICY "service_role_all_receipt_items"
ON "Rawdata_RECEIPT_items" FOR ALL TO service_role
USING (true)
WITH CHECK (true);

-- -----------------------------------------------
-- 99_lg_correction_history
-- authenticated: SELECT (全データ), INSERT (corrector_id を自分に設定)
-- -----------------------------------------------
CREATE POLICY "authenticated_select_correction"
ON "99_lg_correction_history" FOR SELECT TO authenticated
USING (true);

-- INSERT: corrector_id を自分に設定する必要がある
CREATE POLICY "authenticated_insert_own_correction"
ON "99_lg_correction_history" FOR INSERT TO authenticated
WITH CHECK (corrector_id = auth.uid());

CREATE POLICY "service_role_all_correction"
ON "99_lg_correction_history" FOR ALL TO service_role
USING (true)
WITH CHECK (true);

-- -----------------------------------------------
-- 99_lg_image_proc_log
-- authenticated: SELECT のみ
-- -----------------------------------------------
CREATE POLICY "authenticated_select_image_proc_log"
ON "99_lg_image_proc_log" FOR SELECT TO authenticated
USING (true);

CREATE POLICY "service_role_all_image_proc_log"
ON "99_lg_image_proc_log" FOR ALL TO service_role
USING (true)
WITH CHECK (true);

-- -----------------------------------------------
-- MASTER_Rules_transaction_dict
-- authenticated: SELECT (全データ), INSERT/UPDATE (自分が作成したデータのみ)
-- -----------------------------------------------
CREATE POLICY "authenticated_select_master_rules_trans"
ON "MASTER_Rules_transaction_dict" FOR SELECT TO authenticated
USING (true);

-- INSERT: created_by を自分に設定する必要がある
CREATE POLICY "authenticated_insert_own_master_rules_trans"
ON "MASTER_Rules_transaction_dict" FOR INSERT TO authenticated
WITH CHECK (created_by = auth.uid());

-- UPDATE: 自分が作成したデータのみ
CREATE POLICY "authenticated_update_own_master_rules_trans"
ON "MASTER_Rules_transaction_dict" FOR UPDATE TO authenticated
USING (created_by = auth.uid())
WITH CHECK (created_by = auth.uid());

CREATE POLICY "service_role_all_master_rules_trans"
ON "MASTER_Rules_transaction_dict" FOR ALL TO service_role
USING (true)
WITH CHECK (true);

-- -----------------------------------------------
-- MASTER_Categories_* (SELECT only)
-- -----------------------------------------------
CREATE POLICY "authenticated_select_master_cat_product"
ON "MASTER_Categories_product" FOR SELECT TO authenticated
USING (true);

CREATE POLICY "service_role_all_master_cat_product"
ON "MASTER_Categories_product" FOR ALL TO service_role
USING (true)
WITH CHECK (true);

CREATE POLICY "authenticated_select_master_cat_purpose"
ON "MASTER_Categories_purpose" FOR SELECT TO authenticated
USING (true);

CREATE POLICY "service_role_all_master_cat_purpose"
ON "MASTER_Categories_purpose" FOR ALL TO service_role
USING (true)
WITH CHECK (true);

CREATE POLICY "authenticated_select_master_cat_expense"
ON "MASTER_Categories_expense" FOR SELECT TO authenticated
USING (true);

CREATE POLICY "service_role_all_master_cat_expense"
ON "MASTER_Categories_expense" FOR ALL TO service_role
USING (true)
WITH CHECK (true);

CREATE POLICY "authenticated_select_master_rules_expense"
ON "MASTER_Rules_expense_mapping" FOR SELECT TO authenticated
USING (true);

CREATE POLICY "service_role_all_master_rules_expense"
ON "MASTER_Rules_expense_mapping" FOR ALL TO service_role
USING (true)
WITH CHECK (true);

-- =============================================================================
-- STEP 4: GRANT 文
-- =============================================================================

-- 既存権限をリセット
REVOKE ALL ON "Rawdata_FILE_AND_MAIL" FROM anon, authenticated;
REVOKE ALL ON "10_ix_search_index" FROM anon, authenticated;
REVOKE ALL ON "Rawdata_RECEIPT_shops" FROM anon, authenticated;
REVOKE ALL ON "Rawdata_RECEIPT_items" FROM anon, authenticated;
REVOKE ALL ON "99_lg_correction_history" FROM anon, authenticated;
REVOKE ALL ON "99_lg_image_proc_log" FROM anon, authenticated;
REVOKE ALL ON "MASTER_Rules_transaction_dict" FROM anon, authenticated;
REVOKE ALL ON "MASTER_Categories_product" FROM anon, authenticated;
REVOKE ALL ON "MASTER_Categories_purpose" FROM anon, authenticated;
REVOKE ALL ON "MASTER_Categories_expense" FROM anon, authenticated;
REVOKE ALL ON "MASTER_Rules_expense_mapping" FROM anon, authenticated;

-- Rawdata_FILE_AND_MAIL
GRANT SELECT ON "Rawdata_FILE_AND_MAIL" TO anon;
GRANT SELECT, UPDATE, DELETE ON "Rawdata_FILE_AND_MAIL" TO authenticated;
GRANT ALL ON "Rawdata_FILE_AND_MAIL" TO service_role;

-- 10_ix_search_index
GRANT SELECT ON "10_ix_search_index" TO anon;
GRANT SELECT, INSERT, DELETE ON "10_ix_search_index" TO authenticated;
GRANT ALL ON "10_ix_search_index" TO service_role;

-- Rawdata_RECEIPT_shops
GRANT SELECT, UPDATE ON "Rawdata_RECEIPT_shops" TO authenticated;
GRANT ALL ON "Rawdata_RECEIPT_shops" TO service_role;

-- Rawdata_RECEIPT_items
GRANT SELECT, UPDATE ON "Rawdata_RECEIPT_items" TO authenticated;
GRANT ALL ON "Rawdata_RECEIPT_items" TO service_role;

-- 99_lg_correction_history
GRANT SELECT, INSERT ON "99_lg_correction_history" TO authenticated;
GRANT ALL ON "99_lg_correction_history" TO service_role;

-- 99_lg_image_proc_log
GRANT SELECT ON "99_lg_image_proc_log" TO authenticated;
GRANT ALL ON "99_lg_image_proc_log" TO service_role;

-- MASTER_Rules_transaction_dict
GRANT SELECT, INSERT, UPDATE ON "MASTER_Rules_transaction_dict" TO authenticated;
GRANT ALL ON "MASTER_Rules_transaction_dict" TO service_role;

-- MASTER_Categories_* (SELECT only)
GRANT SELECT ON "MASTER_Categories_product" TO authenticated;
GRANT ALL ON "MASTER_Categories_product" TO service_role;

GRANT SELECT ON "MASTER_Categories_purpose" TO authenticated;
GRANT ALL ON "MASTER_Categories_purpose" TO service_role;

GRANT SELECT ON "MASTER_Categories_expense" TO authenticated;
GRANT ALL ON "MASTER_Categories_expense" TO service_role;

GRANT SELECT ON "MASTER_Rules_expense_mapping" TO authenticated;
GRANT ALL ON "MASTER_Rules_expense_mapping" TO service_role;

COMMIT;

-- =============================================================================
-- RLS 権限サマリー
-- =============================================================================
--
-- | テーブル                        | anon   | authenticated                |
-- |---------------------------------|--------|------------------------------|
-- | Rawdata_FILE_AND_MAIL           | SELECT | SELECT, UPDATE*, DELETE*     |
-- | 10_ix_search_index              | SELECT | SELECT, INSERT*, DELETE*     |
-- | Rawdata_RECEIPT_shops           | -      | SELECT, UPDATE*              |
-- | Rawdata_RECEIPT_items           | -      | SELECT, UPDATE*              |
-- | 99_lg_correction_history        | -      | SELECT, INSERT*              |
-- | 99_lg_image_proc_log            | -      | SELECT                       |
-- | MASTER_Rules_transaction_dict   | -      | SELECT, INSERT*, UPDATE*     |
-- | MASTER_Categories_*             | -      | SELECT                       |
--
-- * = auth.uid() による行レベル制限あり
-- =============================================================================
