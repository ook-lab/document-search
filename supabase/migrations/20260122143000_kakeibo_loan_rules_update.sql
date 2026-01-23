-- ============================================================
-- カードローンルールの追加・更新
-- ============================================================

-- 1) 住信SBIカードローン返済（金額指定返済）
INSERT INTO public."Kakeibo_Loan_Posting_Rules" (
    loan_id, priority, is_enabled, match_type, pattern,
    institution, posting_type, note
) VALUES (
    'CARD_LOAN_SBI', 5, true, 'contains', '金額指定返済 カードローン',
    '住信SBI(宜紀)', 'repay', '住信SBIカードローン返済（金額指定返済）'
) ON CONFLICT DO NOTHING;

-- 2) 三井住友カードローン - 既存ルールを無効化
UPDATE public."Kakeibo_Loan_Posting_Rules"
SET is_enabled = false, note = note || '（無効化：符号判定ルールに置き換え）'
WHERE loan_id = 'CARD_LOAN_SMBC'
  AND pattern = 'カードローン'
  AND amount_sign IS NULL;

-- 3) 三井住友カードローン借入（プラス金額のみ）
INSERT INTO public."Kakeibo_Loan_Posting_Rules" (
    loan_id, priority, is_enabled, match_type, pattern,
    institution, amount_sign, posting_type, note
) VALUES (
    'CARD_LOAN_SMBC', 10, true, 'contains', 'カードローン',
    '三井住友銀行', '+', 'borrow', '三井住友カードローン借入（プラス金額）'
) ON CONFLICT DO NOTHING;

-- 4) 三井住友カードローン返済（マイナス金額）
INSERT INTO public."Kakeibo_Loan_Posting_Rules" (
    loan_id, priority, is_enabled, match_type, pattern,
    institution, amount_sign, posting_type, note
) VALUES (
    'CARD_LOAN_SMBC', 10, true, 'contains', 'カードローン',
    '三井住友銀行', '-', 'repay', '三井住友カードローン返済（マイナス金額）'
) ON CONFLICT DO NOTHING;
