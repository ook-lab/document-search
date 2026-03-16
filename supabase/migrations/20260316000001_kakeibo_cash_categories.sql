-- 現金計算専用カテゴリカラムを追加
-- 明細一覧の category_major/category_mid とは独立した別カラム

ALTER TABLE public."Kakeibo_Manual_Edits"
    ADD COLUMN IF NOT EXISTS cash_cat_major text,
    ADD COLUMN IF NOT EXISTS cash_cat_mid   text;

COMMENT ON COLUMN public."Kakeibo_Manual_Edits".cash_cat_major IS '現金計算専用 大分類（クレジット/振込/引落/引出し/借入/返済/収入/預り金/雑費）';
COMMENT ON COLUMN public."Kakeibo_Manual_Edits".cash_cat_mid   IS '現金計算専用 中分類（自由入力）';
