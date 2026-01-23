-- 20260117000008_master_product_rls_select.sql
-- Purpose: MASTER_Product_* tables are readable by authenticated (SELECT only),
--          writable only by service_role (via bypassrls). Enforce via RLS + privileges.

BEGIN;

-- 1) Enable RLS (keep data globally readable to authenticated via policy)
ALTER TABLE public."MASTER_Product_classify"   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."MASTER_Product_generalize" ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."MASTER_Product_normalize"  ENABLE ROW LEVEL SECURITY;

-- 2) Remove any existing policies for cleanliness (idempotent)
DROP POLICY IF EXISTS master_product_classify_select_all    ON public."MASTER_Product_classify";
DROP POLICY IF EXISTS master_product_generalize_select_all ON public."MASTER_Product_generalize";
DROP POLICY IF EXISTS master_product_normalize_select_all  ON public."MASTER_Product_normalize";

-- 3) Create SELECT policies for authenticated (global read)
CREATE POLICY master_product_classify_select_all
  ON public."MASTER_Product_classify"
  FOR SELECT
  TO authenticated
  USING (true);

CREATE POLICY master_product_generalize_select_all
  ON public."MASTER_Product_generalize"
  FOR SELECT
  TO authenticated
  USING (true);

CREATE POLICY master_product_normalize_select_all
  ON public."MASTER_Product_normalize"
  FOR SELECT
  TO authenticated
  USING (true);

-- 4) Privilege tightening: authenticated is SELECT only on these tables
REVOKE INSERT, UPDATE, DELETE ON public."MASTER_Product_classify"   FROM authenticated;
REVOKE INSERT, UPDATE, DELETE ON public."MASTER_Product_generalize" FROM authenticated;
REVOKE INSERT, UPDATE, DELETE ON public."MASTER_Product_normalize"  FROM authenticated;

GRANT SELECT ON public."MASTER_Product_classify"   TO authenticated;
GRANT SELECT ON public."MASTER_Product_generalize" TO authenticated;
GRANT SELECT ON public."MASTER_Product_normalize"  TO authenticated;

COMMIT;
