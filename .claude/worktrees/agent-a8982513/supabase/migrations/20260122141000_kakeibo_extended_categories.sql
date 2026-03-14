-- ============================================================
-- 分類項目の拡張
-- 大分類、中分類、小分類、店、所属、人、文脈
-- ============================================================

-- 既存カラムをリネームまたは新規作成
DO $$
BEGIN
    -- manual_category_major → category_major
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = 'Kakeibo_Manual_Edits'
        AND column_name = 'manual_category_major'
    ) THEN
        ALTER TABLE public."Kakeibo_Manual_Edits"
            RENAME COLUMN manual_category_major TO category_major;
        RAISE NOTICE 'Renamed manual_category_major to category_major';
    ELSIF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = 'Kakeibo_Manual_Edits'
        AND column_name = 'category_major'
    ) THEN
        ALTER TABLE public."Kakeibo_Manual_Edits"
            ADD COLUMN category_major text;
        RAISE NOTICE 'Created category_major column';
    ELSE
        RAISE NOTICE 'category_major already exists, skipping';
    END IF;

    -- manual_category_minor → category_mid
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = 'Kakeibo_Manual_Edits'
        AND column_name = 'manual_category_minor'
    ) THEN
        ALTER TABLE public."Kakeibo_Manual_Edits"
            RENAME COLUMN manual_category_minor TO category_mid;
        RAISE NOTICE 'Renamed manual_category_minor to category_mid';
    ELSIF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = 'Kakeibo_Manual_Edits'
        AND column_name = 'category_mid'
    ) THEN
        ALTER TABLE public."Kakeibo_Manual_Edits"
            ADD COLUMN category_mid text;
        RAISE NOTICE 'Created category_mid column';
    ELSE
        RAISE NOTICE 'category_mid already exists, skipping';
    END IF;
END $$;

-- 新しいカラムを追加
ALTER TABLE public."Kakeibo_Manual_Edits"
    ADD COLUMN IF NOT EXISTS category_small text,
    ADD COLUMN IF NOT EXISTS category_shop text,
    ADD COLUMN IF NOT EXISTS category_belonging text,
    ADD COLUMN IF NOT EXISTS category_person text,
    ADD COLUMN IF NOT EXISTS category_context text;

-- コメント
COMMENT ON COLUMN public."Kakeibo_Manual_Edits".category_major IS '大分類（食費、交通費など）';
COMMENT ON COLUMN public."Kakeibo_Manual_Edits".category_mid IS '中分類（外食、スーパーなど）';
COMMENT ON COLUMN public."Kakeibo_Manual_Edits".category_small IS '小分類（ランチ、夕食など）';
COMMENT ON COLUMN public."Kakeibo_Manual_Edits".category_shop IS '店（〇〇店など）';
COMMENT ON COLUMN public."Kakeibo_Manual_Edits".category_belonging IS '所属（会社、家庭など）';
COMMENT ON COLUMN public."Kakeibo_Manual_Edits".category_person IS '人（自分、妻、子供など）';
COMMENT ON COLUMN public."Kakeibo_Manual_Edits".category_context IS '文脈（出張、旅行、イベントなど）';
