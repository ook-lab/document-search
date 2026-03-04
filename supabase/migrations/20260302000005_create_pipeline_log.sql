-- ============================================================
-- pipeline_log テーブル作成（02_log 相当）
-- ワーカーの動作記録・エラー・ゲート判定
-- 01_raw の各行に対して 1:1 で対応
-- ============================================================

CREATE TABLE IF NOT EXISTS public.pipeline_log (

  -- Identity
  id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
  raw_id          UUID        NOT NULL,
  raw_table       TEXT        NOT NULL,
  UNIQUE (raw_id, raw_table),

  -- ワーカーリース
  lease_owner          TEXT,
  lease_until          TIMESTAMPTZ,
  attempt_count        INT         NOT NULL DEFAULT 0,

  -- ゲート判定
  gate_decision        TEXT,
  gate_block_code      TEXT,
  gate_block_reason    TEXT,
  gate_policy_version  TEXT,

  -- エラー記録
  last_error_reason    TEXT,
  last_worker          TEXT,
  last_attempt_at      TIMESTAMPTZ,
  failed_at            TIMESTAMPTZ,
  error_message        TEXT,

  -- 完了記録
  completed_at         TIMESTAMPTZ,

  -- 修正履歴参照
  latest_correction_id BIGINT,

  -- System
  created_at           TIMESTAMPTZ DEFAULT now(),
  updated_at           TIMESTAMPTZ DEFAULT now()

);

CREATE INDEX IF NOT EXISTS idx_pl_raw           ON public.pipeline_log (raw_table, raw_id);
CREATE INDEX IF NOT EXISTS idx_pl_lease         ON public.pipeline_log (lease_until);
CREATE INDEX IF NOT EXISTS idx_pl_gate          ON public.pipeline_log (gate_decision);

DO $$
BEGIN
  RAISE NOTICE 'pipeline_log テーブル作成完了';
END $$;
