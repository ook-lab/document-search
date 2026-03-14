-- ============================================================
-- 1) updated_at 自動更新用関数（衝突回避名）
-- ============================================================
CREATE OR REPLACE FUNCTION public.fn_kakeibo_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
 NEW.updated_at = NOW();
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 2) 生データテーブル (Rawdata_BANK_transactions)
-- ============================================================
CREATE TABLE IF NOT EXISTS public."Rawdata_BANK_transactions" (
 "id" text NOT NULL,
 "date" date NOT NULL,
 "content" text,
 "amount" bigint NOT NULL,
 "institution" text,
 "category_major" text,
 "category_minor" text,
 "memo" text,
 "is_target" boolean DEFAULT true,
 "is_transfer" boolean DEFAULT false,
 "created_at" timestamp with time zone DEFAULT now() NOT NULL,
 "updated_at" timestamp with time zone DEFAULT now() NOT NULL,
 CONSTRAINT "Rawdata_BANK_transactions_pkey" PRIMARY KEY ("id")
);

-- トリガ設定
DROP TRIGGER IF EXISTS tr_kakeibo_set_timestamp ON public."Rawdata_BANK_transactions";
CREATE TRIGGER tr_kakeibo_set_timestamp
 BEFORE UPDATE ON public."Rawdata_BANK_transactions"
 FOR EACH ROW
 EXECUTE PROCEDURE public.fn_kakeibo_set_updated_at();

-- RLS（将来のUI化に備えDefault Deny）
ALTER TABLE public."Rawdata_BANK_transactions" ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- 3) 旧互換用ビュー（念のため残す）
-- ============================================================
CREATE OR REPLACE VIEW public."view_monthly_expenses" AS
SELECT
 id, date, category_major, category_minor, content,
 ABS(amount) AS amount_abs,
 institution, memo, updated_at
FROM public."Rawdata_BANK_transactions"
WHERE amount < 0 AND is_transfer = false AND is_target = true;
