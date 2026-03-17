-- カードローンルール
-- 内容・口座・金額符号でマッチし、現金計算への計上方法とローン種別を決定する

CREATE TABLE IF NOT EXISTS public."Kakeibo_Card_Loan_Rules" (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_name           text        NOT NULL,
    content_pattern     text        NOT NULL DEFAULT '',
    institution_pattern text        NOT NULL DEFAULT '',
    amount_sign         text        NOT NULL DEFAULT 'any' CHECK (amount_sign IN ('+', '-', 'any')),
    cash_cat_major      text,                          -- NULL の場合は現金計算に計上しない
    loan_type           text CHECK (loan_type IN ('借入', '返済', NULL)),
    exclude             boolean     NOT NULL DEFAULT false,  -- true = 計算対象外（どこにも出ない）
    is_active           boolean     NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE  public."Kakeibo_Card_Loan_Rules"               IS 'カードローン取引の現金計算・ローン管理への振り分けルール';
COMMENT ON COLUMN public."Kakeibo_Card_Loan_Rules".exclude        IS 'true=計算対象外（現金計算にも明細一覧にも計上しない）';
COMMENT ON COLUMN public."Kakeibo_Card_Loan_Rules".loan_type      IS '借入 or 返済（ローン管理への種別）';

-- シードデータ（既定ルール9件）
INSERT INTO public."Kakeibo_Card_Loan_Rules"
    (rule_name, content_pattern, institution_pattern, amount_sign, cash_cat_major, loan_type, exclude)
VALUES
    ('楽天銀行 借入',             'ラクテンギンコウ',               '楽天銀行(宜紀)',  '+', '借入', '借入', false),
    ('楽天銀行 返済',             'ラクテンギンコウ',               '楽天銀行(宜紀)',  '-', '返済', '返済', false),
    ('住信SBI 借入',              '借入 カードローン',              '住信SBI(宜紀)',   '+', '借入', '借入', false),
    ('住信SBI 約定返済',          '約定返済 カードローン',          '住信SBI(宜紀)',   '-', '返済', '返済', false),
    ('住信SBI 金額指定返済',      '金額指定返済 カードローン',      '住信SBI(宜紀)',   '-', '返済', '返済', false),
    ('三井住友 借入（＋側）',     'カードローン',                   '三井住友銀行',    '+', '借入', '借入', false),
    ('三井住友 借入（－除外）',   'カードローン',                   '三井住友銀行',    '-', NULL,   NULL,   true),
    ('三井住友 パソコン振替＋除外', 'パソコン振替 001フツウ2071609', '三井住友銀行',   '+', NULL,   NULL,   true),
    ('三井住友 パソコン振替－返済', 'パソコン振替 001フツウ2287310', '三井住友銀行',   '-', '返済', '返済', false)
ON CONFLICT DO NOTHING;
