-- ============================================================
-- ルールテーブルに金額条件と金融機関オーバーライドを追加
-- ============================================================

-- 金額条件カラムの追加
ALTER TABLE public."Kakeibo_CategoryRules"
    ADD COLUMN IF NOT EXISTS amount_min numeric NULL,
    ADD COLUMN IF NOT EXISTS amount_max numeric NULL;

-- 金融機関オーバーライドカラムの追加（ルール適用時にinstitutionを書き換える）
ALTER TABLE public."Kakeibo_CategoryRules"
    ADD COLUMN IF NOT EXISTS institution_override text NULL;

COMMENT ON COLUMN public."Kakeibo_CategoryRules".amount_min IS '金額条件（下限）。NULLの場合は条件なし';
COMMENT ON COLUMN public."Kakeibo_CategoryRules".amount_max IS '金額条件（上限）。NULLの場合は条件なし';
COMMENT ON COLUMN public."Kakeibo_CategoryRules".institution_override IS 'ルール適用時に金融機関名を上書き（例：住宅ローンA→家賃）';
