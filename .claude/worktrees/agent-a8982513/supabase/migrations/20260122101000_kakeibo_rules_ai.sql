-- ============================================================
-- 1) カテゴリルールテーブル
-- ============================================================
CREATE TABLE IF NOT EXISTS public."Kakeibo_CategoryRules" (
    rule_id bigserial PRIMARY KEY,
    priority integer NOT NULL DEFAULT 100,      -- 小さいほど強い
    is_enabled boolean NOT NULL DEFAULT true,
    match_type text NOT NULL,                   -- 'exact'|'contains'|'prefix'|'regex'
    pattern text NOT NULL,
    institution text NULL,
    category_major text NULL,
    category_minor text NULL,
    is_target boolean NULL,
    is_transfer boolean NULL,
    note text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- トリガ
DROP TRIGGER IF EXISTS tr_kakeibo_rules_set_timestamp ON public."Kakeibo_CategoryRules";
CREATE TRIGGER tr_kakeibo_rules_set_timestamp
    BEFORE UPDATE ON public."Kakeibo_CategoryRules"
    FOR EACH ROW
    EXECUTE PROCEDURE public.fn_kakeibo_set_updated_at();

ALTER TABLE public."Kakeibo_CategoryRules" ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- 2) AI分類キャッシュ（ユニーク店名単位）
-- ============================================================
CREATE TABLE IF NOT EXISTS public."Kakeibo_AI_CategoryCache" (
    cache_id bigserial PRIMARY KEY,
    merchant_key text NOT NULL,                 -- 正規化キー
    category_major text NOT NULL,
    category_minor text NULL,
    confidence numeric(4,3) NULL,
    model text NULL,
    decided_by text NOT NULL DEFAULT 'ai',
    evidence text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT "Kakeibo_AI_CategoryCache_merchant_key_uniq" UNIQUE ("merchant_key")
);

-- トリガ
DROP TRIGGER IF EXISTS tr_kakeibo_ai_cache_set_timestamp ON public."Kakeibo_AI_CategoryCache";
CREATE TRIGGER tr_kakeibo_ai_cache_set_timestamp
    BEFORE UPDATE ON public."Kakeibo_AI_CategoryCache"
    FOR EACH ROW
    EXECUTE PROCEDURE public.fn_kakeibo_set_updated_at();

ALTER TABLE public."Kakeibo_AI_CategoryCache" ENABLE ROW LEVEL SECURITY;
