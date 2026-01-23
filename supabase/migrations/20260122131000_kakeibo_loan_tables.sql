-- ============================================================
-- ローン管理テーブル群
-- ============================================================

-- ============================================================
-- 1) ローン口座マスタ
-- ============================================================
CREATE TABLE IF NOT EXISTS public."Kakeibo_Loan_Accounts" (
    loan_id text PRIMARY KEY,                    -- 例: 'MORTGAGE_A', 'CARD_LOAN_1'
    loan_name text NOT NULL,                     -- 表示名
    loan_type text NOT NULL,                     -- 'mortgage' | 'card_loan'
    institution text,                            -- 金融機関名
    initial_balance numeric NOT NULL DEFAULT 0, -- 初期残高（借入時の元本）
    interest_rate numeric(5,3),                  -- 金利（例: 0.875 = 0.875%）
    start_date date,                             -- ローン開始日
    end_date date,                               -- 完済予定日
    is_active boolean NOT NULL DEFAULT true,    -- アクティブフラグ
    note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- トリガ
DROP TRIGGER IF EXISTS tr_loan_accounts_set_timestamp ON public."Kakeibo_Loan_Accounts";
CREATE TRIGGER tr_loan_accounts_set_timestamp
    BEFORE UPDATE ON public."Kakeibo_Loan_Accounts"
    FOR EACH ROW
    EXECUTE PROCEDURE public.fn_kakeibo_set_updated_at();

ALTER TABLE public."Kakeibo_Loan_Accounts" ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public."Kakeibo_Loan_Accounts" IS 'ローン口座マスタ（住宅ローン、カードローン等）';
COMMENT ON COLUMN public."Kakeibo_Loan_Accounts".loan_type IS 'mortgage: 住宅ローン（残高は手動スナップショット）, card_loan: カードローン（残高は自動計算）';

-- ============================================================
-- 2) ローン仕訳ルール
-- 銀行明細のパターンからローン口座への紐付けルール
-- ============================================================
CREATE TABLE IF NOT EXISTS public."Kakeibo_Loan_Posting_Rules" (
    rule_id bigserial PRIMARY KEY,
    loan_id text NOT NULL REFERENCES public."Kakeibo_Loan_Accounts"(loan_id),
    priority integer NOT NULL DEFAULT 100,       -- 小さいほど強い
    is_enabled boolean NOT NULL DEFAULT true,
    match_type text NOT NULL,                    -- 'exact'|'contains'|'prefix'|'regex'
    pattern text NOT NULL,
    institution text NULL,                       -- 金融機関フィルタ
    amount_min numeric NULL,
    amount_max numeric NULL,
    posting_type text NOT NULL,                  -- 'borrow' | 'repay' | 'interest'
    note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- トリガ
DROP TRIGGER IF EXISTS tr_loan_posting_rules_set_timestamp ON public."Kakeibo_Loan_Posting_Rules";
CREATE TRIGGER tr_loan_posting_rules_set_timestamp
    BEFORE UPDATE ON public."Kakeibo_Loan_Posting_Rules"
    FOR EACH ROW
    EXECUTE PROCEDURE public.fn_kakeibo_set_updated_at();

ALTER TABLE public."Kakeibo_Loan_Posting_Rules" ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public."Kakeibo_Loan_Posting_Rules" IS 'ローン仕訳ルール（銀行明細→ローン口座への紐付け）';
COMMENT ON COLUMN public."Kakeibo_Loan_Posting_Rules".posting_type IS 'borrow: 借入（残高増）, repay: 返済（残高減）, interest: 利息支払い';

-- ============================================================
-- 3) ローン残高スナップショット
-- 住宅ローンなど、自動計算が難しい場合の手動残高記録
-- ============================================================
CREATE TABLE IF NOT EXISTS public."Kakeibo_Loan_Balance_Snapshots" (
    snapshot_id bigserial PRIMARY KEY,
    loan_id text NOT NULL REFERENCES public."Kakeibo_Loan_Accounts"(loan_id),
    snapshot_date date NOT NULL,
    balance numeric NOT NULL,                    -- その時点での残高
    principal_paid numeric,                      -- 元金返済額（参考）
    interest_paid numeric,                       -- 利息支払額（参考）
    source text,                                 -- 'manual' | 'statement' | 'calculated'
    note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT "Kakeibo_Loan_Balance_Snapshots_uniq" UNIQUE (loan_id, snapshot_date)
);

ALTER TABLE public."Kakeibo_Loan_Balance_Snapshots" ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public."Kakeibo_Loan_Balance_Snapshots" IS 'ローン残高スナップショット（住宅ローン等の手動記録用）';
COMMENT ON COLUMN public."Kakeibo_Loan_Balance_Snapshots".source IS 'manual: 手入力, statement: 明細書から転記, calculated: 計算値';

-- ============================================================
-- 4) インデックス
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_loan_posting_rules_loan_id ON public."Kakeibo_Loan_Posting_Rules"(loan_id);
CREATE INDEX IF NOT EXISTS idx_loan_posting_rules_enabled ON public."Kakeibo_Loan_Posting_Rules"(is_enabled) WHERE is_enabled = true;
CREATE INDEX IF NOT EXISTS idx_loan_balance_snapshots_loan_date ON public."Kakeibo_Loan_Balance_Snapshots"(loan_id, snapshot_date DESC);
