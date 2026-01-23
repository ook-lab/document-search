-- 20260117000006_rawdata_rls_owner.sql
-- Purpose: Enable RLS on Rawdata_* tables and restrict authenticated access by owner_id = auth.uid().

BEGIN;

-- 0) Add owner_id if missing (use uuid; align with existing owner_id pattern)
ALTER TABLE public."Rawdata_NETSUPER_items" ADD COLUMN IF NOT EXISTS owner_id uuid;
ALTER TABLE public."Rawdata_FLYER_items"    ADD COLUMN IF NOT EXISTS owner_id uuid;
ALTER TABLE public."Rawdata_FLYER_shops"    ADD COLUMN IF NOT EXISTS owner_id uuid;

-- 1) Enable RLS
ALTER TABLE public."Rawdata_NETSUPER_items" ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."Rawdata_FLYER_items"    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."Rawdata_FLYER_shops"    ENABLE ROW LEVEL SECURITY;

-- 2) Drop existing policies (idempotent)
DROP POLICY IF EXISTS rawdata_netsuper_select_own ON public."Rawdata_NETSUPER_items";
DROP POLICY IF EXISTS rawdata_flyer_items_select_own ON public."Rawdata_FLYER_items";
DROP POLICY IF EXISTS rawdata_flyer_shops_select_own ON public."Rawdata_FLYER_shops";

-- 3) Create minimal SELECT policies for authenticated
CREATE POLICY rawdata_netsuper_select_own
  ON public."Rawdata_NETSUPER_items"
  FOR SELECT
  TO authenticated
  USING (owner_id = auth.uid());

CREATE POLICY rawdata_flyer_items_select_own
  ON public."Rawdata_FLYER_items"
  FOR SELECT
  TO authenticated
  USING (owner_id = auth.uid());

CREATE POLICY rawdata_flyer_shops_select_own
  ON public."Rawdata_FLYER_shops"
  FOR SELECT
  TO authenticated
  USING (owner_id = auth.uid());

COMMIT;
