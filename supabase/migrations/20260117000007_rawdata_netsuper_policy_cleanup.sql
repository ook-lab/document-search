-- 20260117000007_rawdata_netsuper_policy_cleanup.sql
-- Purpose: Remove legacy policies from Rawdata_NETSUPER_items, keep only owner-based policy.

BEGIN;

-- Drop all legacy policies on Rawdata_NETSUPER_items
DROP POLICY IF EXISTS "Allow all operations" ON public."Rawdata_NETSUPER_items";
DROP POLICY IF EXISTS "Allow all operations for authenticated users" ON public."Rawdata_NETSUPER_items";
DROP POLICY IF EXISTS "Allow authenticated users full access to 80_rd_products" ON public."Rawdata_NETSUPER_items";
DROP POLICY IF EXISTS "Allow service role full access to 80_rd_products" ON public."Rawdata_NETSUPER_items";
DROP POLICY IF EXISTS "Enable read access for all users" ON public."Rawdata_NETSUPER_items";

-- Keep only: rawdata_netsuper_select_own (already created in previous migration)

-- Add service_role policy for backend operations (drop first if exists)
DROP POLICY IF EXISTS rawdata_netsuper_service_role_all ON public."Rawdata_NETSUPER_items";
CREATE POLICY rawdata_netsuper_service_role_all
  ON public."Rawdata_NETSUPER_items"
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);

COMMIT;
