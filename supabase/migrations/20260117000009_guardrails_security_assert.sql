-- 20260117000009_guardrails_security_assert.sql
-- Purpose: Guardrails - fail fast if security invariants drift.

BEGIN;

CREATE OR REPLACE FUNCTION public.assert_security_invariants()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_cnt int;
BEGIN
  -- A) anon/public must have zero table privileges in public schema
  SELECT count(*) INTO v_cnt
  FROM information_schema.role_table_grants
  WHERE table_schema = 'public'
    AND grantee IN ('anon','public');

  IF v_cnt <> 0 THEN
    RAISE EXCEPTION 'SECURITY_INVARIANT_FAILED: anon/public has table grants (%).', v_cnt;
  END IF;

  -- B) Rawdata_* must have RLS enabled
  SELECT count(*) INTO v_cnt
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname='public'
    AND c.relkind='r'
    AND c.relname IN ('Rawdata_NETSUPER_items','Rawdata_FLYER_items','Rawdata_FLYER_shops')
    AND c.relrowsecurity = false;

  IF v_cnt <> 0 THEN
    RAISE EXCEPTION 'SECURITY_INVARIANT_FAILED: Rawdata_* has RLS disabled (%).', v_cnt;
  END IF;

  -- C) MASTER_Product_* must have RLS enabled
  SELECT count(*) INTO v_cnt
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname='public'
    AND c.relkind='r'
    AND c.relname IN ('MASTER_Product_classify','MASTER_Product_generalize','MASTER_Product_normalize')
    AND c.relrowsecurity = false;

  IF v_cnt <> 0 THEN
    RAISE EXCEPTION 'SECURITY_INVARIANT_FAILED: MASTER_Product_* has RLS disabled (%).', v_cnt;
  END IF;

  -- D) Optional: ensure no duplicate policy names per table (basic sanity)
  SELECT count(*) INTO v_cnt
  FROM (
    SELECT schemaname, tablename, policyname, count(*) AS c
    FROM pg_policies
    WHERE schemaname='public'
    GROUP BY schemaname, tablename, policyname
    HAVING count(*) > 1
  ) t;

  IF v_cnt <> 0 THEN
    RAISE EXCEPTION 'SECURITY_INVARIANT_FAILED: duplicate policies detected (%).', v_cnt;
  END IF;

END;
$$;

COMMIT;
