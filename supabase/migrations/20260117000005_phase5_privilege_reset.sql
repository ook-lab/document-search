-- =============================================================================
-- Migration: 20260117000005_phase5_privilege_reset.sql
-- Purpose: Enforce "anon_rpc_only" by hard-resetting all privileges
-- =============================================================================
-- Problem: Previous GRANTs remain, anon has INSERT/UPDATE/DELETE on many tables.
-- Solution: REVOKE all, then GRANT minimal (RPC only for anon).
-- =============================================================================

BEGIN;

-- =============================================================================
-- STEP 0: Revoke schema usage first (will re-grant minimal later)
-- =============================================================================
REVOKE ALL ON SCHEMA public FROM anon, authenticated, public;

-- =============================================================================
-- STEP 1: Hard reset - Tables / Sequences / Functions in public schema
-- =============================================================================
REVOKE ALL PRIVILEGES ON ALL TABLES    IN SCHEMA public FROM anon, authenticated, public;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated, public;
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated, public;

-- =============================================================================
-- STEP 2: Prevent future drift - Default privileges
-- =============================================================================
-- Objects created later by postgres role will NOT auto-grant to these roles.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE ALL ON TABLES FROM anon, authenticated, public;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM anon, authenticated, public;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE ALL ON FUNCTIONS FROM anon, authenticated, public;

-- =============================================================================
-- STEP 3: Minimal grants - schema USAGE only (no table rights)
-- =============================================================================
GRANT USAGE ON SCHEMA public TO anon, authenticated;

-- =============================================================================
-- STEP 4: anon = RPC only (explicit allowlist)
-- =============================================================================
-- These are the ONLY functions anon can execute.
-- Add more here ONLY if truly needed for public API.
GRANT EXECUTE ON FUNCTION public.public_search(TEXT, vector(1536), FLOAT, INT) TO anon;
GRANT EXECUTE ON FUNCTION public.public_search_with_fulltext(TEXT, vector(1536), FLOAT, INT, FLOAT, FLOAT) TO anon;

-- =============================================================================
-- STEP 5: authenticated = RPC + minimal table access via RLS
-- =============================================================================
-- authenticated can also use public search functions
GRANT EXECUTE ON FUNCTION public.public_search(TEXT, vector(1536), FLOAT, INT) TO authenticated;
GRANT EXECUTE ON FUNCTION public.public_search_with_fulltext(TEXT, vector(1536), FLOAT, INT, FLOAT, FLOAT) TO authenticated;

-- Grant SELECT on tables that authenticated users need (RLS will filter rows)
-- Core document tables
GRANT SELECT ON "Rawdata_FILE_AND_MAIL" TO authenticated;
GRANT SELECT ON "Rawdata_RECEIPT_shops" TO authenticated;
GRANT SELECT ON "Rawdata_RECEIPT_items" TO authenticated;
GRANT SELECT ON "10_ix_search_index" TO authenticated;

-- Master tables (read-only for authenticated)
GRANT SELECT ON "MASTER_Categories_expense" TO authenticated;
GRANT SELECT ON "MASTER_Categories_product" TO authenticated;
GRANT SELECT ON "MASTER_Categories_purpose" TO authenticated;
GRANT SELECT ON "MASTER_Product_category_mapping" TO authenticated;
GRANT SELECT ON "MASTER_Rules_expense_mapping" TO authenticated;
GRANT SELECT ON "MASTER_Rules_transaction_dict" TO authenticated;

-- Execution tracking tables
GRANT SELECT ON "document_executions" TO authenticated;
GRANT SELECT ON "run_executions" TO authenticated;
GRANT SELECT ON "ops_requests" TO authenticated;

-- Log tables (read-only)
GRANT SELECT ON "99_lg_correction_history" TO authenticated;
GRANT SELECT ON "99_lg_image_proc_log" TO authenticated;
GRANT SELECT ON "80_rd_price_history" TO authenticated;

-- =============================================================================
-- STEP 6: authenticated write permissions (where RLS enforces ownership)
-- =============================================================================
-- Update own documents
GRANT UPDATE ON "Rawdata_FILE_AND_MAIL" TO authenticated;
GRANT DELETE ON "Rawdata_FILE_AND_MAIL" TO authenticated;

-- Update own receipts
GRANT UPDATE ON "Rawdata_RECEIPT_shops" TO authenticated;
GRANT UPDATE ON "Rawdata_RECEIPT_items" TO authenticated;

-- Insert/update own execution records
GRANT UPDATE, DELETE ON "document_executions" TO authenticated;

-- Insert corrections (corrector_id check in RLS)
GRANT INSERT ON "99_lg_correction_history" TO authenticated;

-- Insert transaction rules (created_by check in RLS)
GRANT INSERT, UPDATE ON "MASTER_Rules_transaction_dict" TO authenticated;

-- Insert ops requests
GRANT INSERT ON "ops_requests" TO authenticated;

-- Search index management (owner_id check in RLS)
GRANT INSERT, DELETE ON "10_ix_search_index" TO authenticated;

COMMIT;

-- =============================================================================
-- Verification (run manually after migration):
-- =============================================================================
-- SELECT grantee, table_name, privilege_type
-- FROM information_schema.role_table_grants
-- WHERE table_schema = 'public'
--   AND grantee IN ('anon', 'authenticated', 'public')
-- ORDER BY grantee, table_name, privilege_type;
