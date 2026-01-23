-- ============================================================
-- 商品名ルールテーブル（取得名→商品名の変換ルール）
-- ============================================================

CREATE TABLE IF NOT EXISTS public."Kakeibo_Product_Name_Rules" (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    ocr_name text NOT NULL,           -- 取得名（OCR読み取り値）
    product_name text NOT NULL,       -- 商品名（正規化した名前）
    shop_name text,                   -- 店舗名（店舗別ルール用、NULLは全店舗共通）
    priority int DEFAULT 0,           -- 優先度（高いほど優先）
    use_count int DEFAULT 0,          -- 使用回数（学習用）
    is_active boolean DEFAULT true,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),

    UNIQUE(ocr_name, shop_name)       -- 同じ取得名+店舗は1つのみ
);

-- コメント
COMMENT ON TABLE public."Kakeibo_Product_Name_Rules" IS '商品名変換ルール（OCR取得名→正規商品名）';
COMMENT ON COLUMN public."Kakeibo_Product_Name_Rules".ocr_name IS 'OCRで読み取った商品名（キー）';
COMMENT ON COLUMN public."Kakeibo_Product_Name_Rules".product_name IS '正規化された商品名';
COMMENT ON COLUMN public."Kakeibo_Product_Name_Rules".shop_name IS '店舗名（店舗別ルール用）';

-- インデックス
CREATE INDEX IF NOT EXISTS idx_product_name_rules_ocr ON public."Kakeibo_Product_Name_Rules" (ocr_name);
CREATE INDEX IF NOT EXISTS idx_product_name_rules_shop ON public."Kakeibo_Product_Name_Rules" (shop_name) WHERE shop_name IS NOT NULL;

-- 更新日時トリガー
CREATE OR REPLACE FUNCTION update_product_name_rules_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_product_name_rules_updated ON public."Kakeibo_Product_Name_Rules";
CREATE TRIGGER trg_product_name_rules_updated
    BEFORE UPDATE ON public."Kakeibo_Product_Name_Rules"
    FOR EACH ROW EXECUTE FUNCTION update_product_name_rules_timestamp();
