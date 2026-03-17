-- 現金計算専用 分類ルール
-- content_pattern + institution_pattern + amount_sign に一致した明細に cash_cat_major/mid を自動付与

CREATE TABLE IF NOT EXISTS public."Kakeibo_Cash_Category_Rules" (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_name        text        NOT NULL,
    content_pattern  text        NOT NULL DEFAULT '',
    institution_pattern text     NOT NULL DEFAULT '',
    amount_sign      text        NOT NULL DEFAULT 'any' CHECK (amount_sign IN ('+', '-', 'any')),
    cash_cat_major   text,
    cash_cat_mid     text,
    is_active        boolean     NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE  public."Kakeibo_Cash_Category_Rules"                    IS '現金計算専用カテゴリ自動付与ルール';
COMMENT ON COLUMN public."Kakeibo_Cash_Category_Rules".amount_sign        IS '対象金額の符号: + / - / any';
COMMENT ON COLUMN public."Kakeibo_Cash_Category_Rules".cash_cat_major     IS '大分類（クレジット/振込/引落/引出し/借入/返済/収入/預り金/雑費）';
