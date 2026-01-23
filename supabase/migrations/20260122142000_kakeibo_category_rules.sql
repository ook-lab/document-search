-- ============================================================
-- 分類ルールテーブル
-- contentパターンから自動分類を提案
-- ============================================================

CREATE TABLE IF NOT EXISTS public."Kakeibo_Category_Rules" (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    content_pattern text NOT NULL UNIQUE,  -- マッチングパターン（部分一致）
    category_major text,      -- 大分類
    category_mid text,        -- 中分類
    category_small text,      -- 小分類
    category_shop text,       -- 店
    category_belonging text,  -- 所属
    category_person text,     -- 人
    category_context text,    -- 文脈
    priority int DEFAULT 0,   -- 優先度（高いほど優先）
    is_active boolean DEFAULT true,
    use_count int DEFAULT 0,  -- 使用回数（学習用）
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

-- コメント
COMMENT ON TABLE public."Kakeibo_Category_Rules" IS '分類ルール（パターン→分類の自動提案）';
COMMENT ON COLUMN public."Kakeibo_Category_Rules".content_pattern IS '取引内容にマッチするパターン（部分一致）';
COMMENT ON COLUMN public."Kakeibo_Category_Rules".priority IS '優先度（複数マッチ時、高い方を採用）';
COMMENT ON COLUMN public."Kakeibo_Category_Rules".use_count IS '適用回数（よく使うルールの優先度調整用）';

-- インデックス
CREATE INDEX IF NOT EXISTS idx_category_rules_active
    ON public."Kakeibo_Category_Rules" (is_active, priority DESC) WHERE is_active = true;

-- 更新日時トリガー
CREATE OR REPLACE FUNCTION update_category_rules_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_category_rules_updated ON public."Kakeibo_Category_Rules";
CREATE TRIGGER trg_category_rules_updated
    BEFORE UPDATE ON public."Kakeibo_Category_Rules"
    FOR EACH ROW EXECUTE FUNCTION update_category_rules_timestamp();

-- 使用回数インクリメント用RPC
CREATE OR REPLACE FUNCTION increment_category_rule_usage(p_pattern text)
RETURNS void AS $$
BEGIN
    UPDATE public."Kakeibo_Category_Rules"
    SET use_count = use_count + 1
    WHERE content_pattern = p_pattern;
END;
$$ LANGUAGE plpgsql;
