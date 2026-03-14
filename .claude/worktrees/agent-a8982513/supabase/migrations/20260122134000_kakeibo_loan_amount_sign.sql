-- ============================================================
-- ローンルールに金額符号判定を追加
-- プラス/マイナスで借入・返済を区別するため
-- ============================================================

-- 1) amount_sign カラムを追加
ALTER TABLE public."Kakeibo_Loan_Posting_Rules"
ADD COLUMN IF NOT EXISTS amount_sign text NULL;

COMMENT ON COLUMN public."Kakeibo_Loan_Posting_Rules".amount_sign IS
  '金額の符号条件: ''+'' = プラスのみ, ''-'' = マイナスのみ, NULL = 符号問わず';

-- 2) ビューを再作成（符号判定を追加）
CREATE OR REPLACE VIEW public."view_kakeibo_loan_entries" AS
WITH base AS (
    SELECT
        t.id,
        t.date,
        t.institution,
        t.content,
        t.amount,
        t.memo
    FROM public."Rawdata_BANK_transactions" t
),
-- 除外チェック
manual AS (
    SELECT transaction_id, is_excluded
    FROM public."Kakeibo_Manual_Edits"
),
-- ローンルールマッチング（符号判定を追加）
loan_rule_hit AS (
    SELECT
        b.id,
        r.rule_id,
        r.loan_id,
        r.priority,
        r.posting_type
    FROM base b
    JOIN public."Kakeibo_Loan_Posting_Rules" r
      ON r.is_enabled = true
      AND (r.institution IS NULL OR r.institution = b.institution)
      AND (r.amount_min IS NULL OR abs(b.amount) >= r.amount_min)
      AND (r.amount_max IS NULL OR abs(b.amount) <= r.amount_max)
      -- 符号判定を追加
      AND (r.amount_sign IS NULL
           OR (r.amount_sign = '+' AND b.amount > 0)
           OR (r.amount_sign = '-' AND b.amount < 0))
      AND (
          (r.match_type = 'exact'    AND b.content = r.pattern) OR
          (r.match_type = 'contains' AND b.content ILIKE '%' || r.pattern || '%') OR
          (r.match_type = 'prefix'   AND b.content ILIKE r.pattern || '%') OR
          (r.match_type = 'regex'    AND b.content ~* r.pattern)
      )
),
loan_rule_pick AS (
    SELECT DISTINCT ON (id) *
    FROM loan_rule_hit
    ORDER BY id, priority ASC, rule_id ASC
)
SELECT
    b.id AS transaction_id,
    b.date,
    b.institution,
    b.content,
    b.amount,
    b.memo,
    lrp.loan_id,
    la.loan_name,
    la.loan_type,
    lrp.posting_type,
    -- 残高への影響額を計算
    CASE
        WHEN lrp.posting_type = 'borrow' THEN abs(b.amount)      -- 借入は残高増
        WHEN lrp.posting_type = 'repay' THEN -abs(b.amount)      -- 返済は残高減
        WHEN lrp.posting_type = 'interest' THEN 0                -- 利息は残高に影響なし
        ELSE 0
    END AS balance_impact
FROM base b
INNER JOIN loan_rule_pick lrp ON lrp.id = b.id
LEFT JOIN public."Kakeibo_Loan_Accounts" la ON la.loan_id = lrp.loan_id
LEFT JOIN manual m ON m.transaction_id = b.id
WHERE COALESCE(m.is_excluded, false) = false;

COMMENT ON VIEW public."view_kakeibo_loan_entries" IS 'ローン仕訳明細（銀行明細にローン口座を紐付け、符号判定対応）';

-- ============================================================
-- 3) 符号判定が必要なルールを追加
-- ============================================================

-- 三井住友カードローン返済（パソコン振替でプラス金額）
INSERT INTO public."Kakeibo_Loan_Posting_Rules" (
    loan_id, priority, is_enabled, match_type, pattern,
    institution, amount_sign, posting_type, note
) VALUES (
    'CARD_LOAN_SMBC', 5, true, 'contains', 'パソコン振替 001フツウ2287310',
    '三井住友銀行', '+', 'repay', '三井住友カードローン返済（プラス金額）'
);

-- 楽天カードローン借入（プラス金額）
INSERT INTO public."Kakeibo_Loan_Posting_Rules" (
    loan_id, priority, is_enabled, match_type, pattern,
    institution, amount_sign, posting_type, note
) VALUES (
    'CARD_LOAN_RAKUTEN', 10, true, 'contains', 'ラクテンギンコウ',
    '楽天銀行(宜紀)', '+', 'borrow', '楽天カードローン借入（プラス金額）'
);

-- 楽天カードローン返済（マイナス金額）
INSERT INTO public."Kakeibo_Loan_Posting_Rules" (
    loan_id, priority, is_enabled, match_type, pattern,
    institution, amount_sign, posting_type, note
) VALUES (
    'CARD_LOAN_RAKUTEN', 10, true, 'contains', 'ラクテンギンコウ',
    '楽天銀行(宜紀)', '-', 'repay', '楽天カードローン返済（マイナス金額）'
);
