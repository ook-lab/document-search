-- ============================================================
-- ローン初期データ
-- ============================================================

-- ============================================================
-- 1) ローン口座マスタ
-- ============================================================

-- 住宅ローンA（住信SBI 宜紀名義、2万円以上の返済）
INSERT INTO public."Kakeibo_Loan_Accounts" (
    loan_id, loan_name, loan_type, institution,
    initial_balance, interest_rate,
    is_active, note
) VALUES (
    'MORTGAGE_A', '住宅ローンA', 'mortgage', '住信SBI(宜紀)',
    0, NULL,
    true, '宜紀名義・2万円以上の返済'
) ON CONFLICT (loan_id) DO NOTHING;

-- 住宅ローンB（住信SBI 宜紀名義、2万円以下の返済）
INSERT INTO public."Kakeibo_Loan_Accounts" (
    loan_id, loan_name, loan_type, institution,
    initial_balance, interest_rate,
    is_active, note
) VALUES (
    'MORTGAGE_B', '住宅ローンB', 'mortgage', '住信SBI(宜紀)',
    0, NULL,
    true, '宜紀名義・2万円以下の返済'
) ON CONFLICT (loan_id) DO NOTHING;

-- 住宅ローンC（住信SBI 香屋子名義）
INSERT INTO public."Kakeibo_Loan_Accounts" (
    loan_id, loan_name, loan_type, institution,
    initial_balance, interest_rate,
    is_active, note
) VALUES (
    'MORTGAGE_C', '住宅ローンC', 'mortgage', '住信SBI(香屋子)',
    0, NULL,
    true, '香屋子名義'
) ON CONFLICT (loan_id) DO NOTHING;

-- カードローン1（住信SBIネット銀行）
INSERT INTO public."Kakeibo_Loan_Accounts" (
    loan_id, loan_name, loan_type, institution,
    initial_balance, interest_rate,
    is_active, note
) VALUES (
    'CARD_LOAN_SBI', 'カードローン（住信SBI）', 'card_loan', '住信SBI(宜紀)',
    0, NULL,
    true, '住信SBIネット銀行カードローン'
) ON CONFLICT (loan_id) DO NOTHING;

-- カードローン2（三井住友銀行）
INSERT INTO public."Kakeibo_Loan_Accounts" (
    loan_id, loan_name, loan_type, institution,
    initial_balance, interest_rate,
    is_active, note
) VALUES (
    'CARD_LOAN_SMBC', 'カードローン（三井住友）', 'card_loan', '三井住友銀行',
    0, NULL,
    true, '三井住友銀行カードローン'
) ON CONFLICT (loan_id) DO NOTHING;

-- カードローン3（楽天銀行）
INSERT INTO public."Kakeibo_Loan_Accounts" (
    loan_id, loan_name, loan_type, institution,
    initial_balance, interest_rate,
    is_active, note
) VALUES (
    'CARD_LOAN_RAKUTEN', 'カードローン（楽天）', 'card_loan', '楽天銀行(宜紀)',
    0, NULL,
    true, '楽天銀行カードローン'
) ON CONFLICT (loan_id) DO NOTHING;

-- ============================================================
-- 2) ローン仕訳ルール
-- ============================================================

-- 住宅ローンC返済（香屋子名義 - 金額にかかわらず。先にマッチさせる）
INSERT INTO public."Kakeibo_Loan_Posting_Rules" (
    loan_id, priority, is_enabled, match_type, pattern,
    institution, posting_type, note
) VALUES (
    'MORTGAGE_C', 5, true, 'contains', '約定返済 円 住宅',
    '住信SBI(香屋子)', 'repay', '住宅ローンC返済（香屋子名義）'
);

-- 住宅ローンB返済（宜紀名義・2万円未満）
INSERT INTO public."Kakeibo_Loan_Posting_Rules" (
    loan_id, priority, is_enabled, match_type, pattern,
    institution, amount_min, amount_max, posting_type, note
) VALUES (
    'MORTGAGE_B', 10, true, 'contains', '約定返済 円 住宅',
    '住信SBI(宜紀)', 0, 19999, 'repay', '住宅ローンB返済（宜紀・2万円未満）'
);

-- 住宅ローンA返済（宜紀名義・2万円以上）
INSERT INTO public."Kakeibo_Loan_Posting_Rules" (
    loan_id, priority, is_enabled, match_type, pattern,
    institution, amount_min, amount_max, posting_type, note
) VALUES (
    'MORTGAGE_A', 10, true, 'contains', '約定返済 円 住宅',
    '住信SBI(宜紀)', 20000, NULL, 'repay', '住宅ローンA返済（宜紀・2万円以上）'
);

-- ============================================================
-- カードローン（住信SBI）
-- 明細の文字列で借入・返済を区別
-- ============================================================
INSERT INTO public."Kakeibo_Loan_Posting_Rules" (
    loan_id, priority, is_enabled, match_type, pattern,
    institution, posting_type, note
) VALUES (
    'CARD_LOAN_SBI', 10, true, 'contains', '借入 カードローン',
    '住信SBI(宜紀)', 'borrow', '住信SBI カードローン借入'
);

INSERT INTO public."Kakeibo_Loan_Posting_Rules" (
    loan_id, priority, is_enabled, match_type, pattern,
    institution, posting_type, note
) VALUES (
    'CARD_LOAN_SBI', 10, true, 'contains', '返済 カードローン',
    '住信SBI(宜紀)', 'repay', '住信SBI カードローン返済'
);

-- ============================================================
-- カードローン（三井住友銀行）
-- 「カードローン」→ 常に借入（マイナスでも借入）
-- ※「パソコン振替」返済ルールは amount_sign 追加後に投入
-- ============================================================
INSERT INTO public."Kakeibo_Loan_Posting_Rules" (
    loan_id, priority, is_enabled, match_type, pattern,
    institution, posting_type, note
) VALUES (
    'CARD_LOAN_SMBC', 10, true, 'contains', 'カードローン',
    '三井住友銀行', 'borrow', '三井住友カードローン（常に借入扱い）'
);

-- ============================================================
-- カードローン（楽天銀行）
-- ※符号判定ルールは amount_sign 追加後に投入
-- ============================================================

-- ============================================================
-- 3) 残高スナップショット（住宅ローン用）
-- 運用開始時に手動で入力してください
-- 例:
-- INSERT INTO public."Kakeibo_Loan_Balance_Snapshots"
--     (loan_id, snapshot_date, balance, source, note)
-- VALUES
--     ('MORTGAGE_A', '2026-01-01', 残高, 'manual', 'メモ');
-- ============================================================
