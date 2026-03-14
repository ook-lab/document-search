-- ============================================================
-- ローン管理View群
-- ============================================================

-- ============================================================
-- 1) ローン仕訳明細View
-- 銀行明細にローン口座を紐付けた結果
-- ============================================================
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
-- ローンルールマッチング
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

COMMENT ON VIEW public."view_kakeibo_loan_entries" IS 'ローン仕訳明細（銀行明細にローン口座を紐付け）';

-- ============================================================
-- 2) ローン残高View（カードローン用：自動計算）
-- ============================================================
CREATE OR REPLACE VIEW public."view_kakeibo_card_loan_balances" AS
WITH loan_entries AS (
    SELECT
        loan_id,
        date,
        balance_impact
    FROM public."view_kakeibo_loan_entries"
    WHERE loan_id IS NOT NULL
),
cumulative AS (
    SELECT
        loan_id,
        date,
        balance_impact,
        SUM(balance_impact) OVER (
            PARTITION BY loan_id
            ORDER BY date, balance_impact DESC
            ROWS UNBOUNDED PRECEDING
        ) AS running_balance
    FROM loan_entries
)
SELECT
    la.loan_id,
    la.loan_name,
    la.loan_type,
    la.initial_balance,
    COALESCE(c.latest_balance, 0) AS calculated_balance,
    la.initial_balance + COALESCE(c.latest_balance, 0) AS current_balance,
    c.latest_date
FROM public."Kakeibo_Loan_Accounts" la
LEFT JOIN LATERAL (
    SELECT
        running_balance AS latest_balance,
        date AS latest_date
    FROM cumulative
    WHERE loan_id = la.loan_id
    ORDER BY date DESC, running_balance DESC
    LIMIT 1
) c ON true
WHERE la.loan_type = 'card_loan' AND la.is_active = true;

COMMENT ON VIEW public."view_kakeibo_card_loan_balances" IS 'カードローン残高（トランザクションから自動計算）';

-- ============================================================
-- 3) ローン残高View（住宅ローン用：スナップショットベース）
-- ============================================================
CREATE OR REPLACE VIEW public."view_kakeibo_mortgage_balances" AS
WITH latest_snapshot AS (
    SELECT DISTINCT ON (loan_id)
        loan_id,
        snapshot_date,
        balance,
        principal_paid,
        interest_paid,
        source
    FROM public."Kakeibo_Loan_Balance_Snapshots"
    ORDER BY loan_id, snapshot_date DESC
)
SELECT
    la.loan_id,
    la.loan_name,
    la.loan_type,
    la.initial_balance,
    la.interest_rate,
    la.start_date,
    la.end_date,
    ls.snapshot_date AS balance_date,
    ls.balance AS current_balance,
    ls.principal_paid,
    ls.interest_paid,
    ls.source AS balance_source,
    -- 完済までの概算（簡易計算）
    CASE
        WHEN la.end_date IS NOT NULL AND la.end_date > CURRENT_DATE
        THEN la.end_date - CURRENT_DATE
        ELSE NULL
    END AS days_remaining
FROM public."Kakeibo_Loan_Accounts" la
LEFT JOIN latest_snapshot ls ON ls.loan_id = la.loan_id
WHERE la.loan_type = 'mortgage' AND la.is_active = true;

COMMENT ON VIEW public."view_kakeibo_mortgage_balances" IS '住宅ローン残高（スナップショットベース）';

-- ============================================================
-- 4) 統合ローン残高View
-- ============================================================
CREATE OR REPLACE VIEW public."view_kakeibo_all_loan_balances" AS
SELECT
    loan_id,
    loan_name,
    loan_type,
    initial_balance,
    calculated_balance AS current_balance,
    latest_date AS balance_date,
    'calculated' AS balance_source
FROM public."view_kakeibo_card_loan_balances"
UNION ALL
SELECT
    loan_id,
    loan_name,
    loan_type,
    initial_balance,
    current_balance,
    balance_date,
    balance_source
FROM public."view_kakeibo_mortgage_balances";

COMMENT ON VIEW public."view_kakeibo_all_loan_balances" IS '全ローン残高統合View';
