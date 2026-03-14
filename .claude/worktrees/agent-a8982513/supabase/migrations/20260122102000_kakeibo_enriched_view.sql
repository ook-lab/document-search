-- ============================================================
-- 1) merchant_key 正規化関数
-- ============================================================
CREATE OR REPLACE FUNCTION public.fn_kakeibo_normalize_merchant(raw_text text)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE
    t text;
BEGIN
    IF raw_text IS NULL THEN RETURN NULL; END IF;
    t := upper(raw_text);
    t := replace(t, '　', ' ');
    t := regexp_replace(t, '[^A-Z0-9一-龥ぁ-んァ-ヶー ]', ' ', 'g');
    t := regexp_replace(t, '\s+', ' ', 'g');
    t := btrim(t);
    IF length(t) < 2 THEN RETURN NULL; END IF;
    RETURN t;
END;
$$;

-- ============================================================
-- 2) 仕訳済みView (Priority適用済み完全版)
-- ============================================================
CREATE OR REPLACE VIEW public."view_kakeibo_enriched_transactions" AS
WITH base AS (
    SELECT
        t.*,
        public.fn_kakeibo_normalize_merchant(t.content) AS merchant_key
    FROM public."Rawdata_BANK_transactions" t
),
rule_hit AS (
    SELECT
        b.id,
        r.rule_id,
        r.priority,
        r.category_major AS rule_category_major,
        r.category_minor AS rule_category_minor,
        r.is_target AS rule_is_target,
        r.is_transfer AS rule_is_transfer
    FROM base b
    JOIN public."Kakeibo_CategoryRules" r
      ON r.is_enabled = true
      AND (r.institution IS NULL OR r.institution = b.institution)
      AND (
          (r.match_type = 'exact'    AND b.content = r.pattern) OR
          (r.match_type = 'contains' AND b.content ILIKE '%' || r.pattern || '%') OR
          (r.match_type = 'prefix'   AND b.content ILIKE r.pattern || '%') OR
          (r.match_type = 'regex'    AND b.content ~* r.pattern)
      )
),
rule_pick AS (
    -- ★ priority を最優先で評価して1つ選ぶ
    SELECT DISTINCT ON (id)
        id,
        rule_category_major,
        rule_category_minor,
        rule_is_target,
        rule_is_transfer
    FROM rule_hit
    ORDER BY id, priority ASC, rule_id ASC
),
ai_pick AS (
    SELECT
        merchant_key,
        category_major AS ai_category_major,
        category_minor AS ai_category_minor,
        confidence,
        model
    FROM public."Kakeibo_AI_CategoryCache"
)
SELECT
    b.id,
    b.date,
    b.institution,
    b.content,
    b.memo,
    b.amount,
    abs(b.amount) AS amount_abs,
    b.created_at,
    b.updated_at,
    b.merchant_key,
    -- 優先順位: ルール > AIキャッシュ > 元CSV
    COALESCE(rp.rule_category_major, ap.ai_category_major, b.category_major) AS category_major_final,
    COALESCE(rp.rule_category_minor, ap.ai_category_minor, b.category_minor) AS category_minor_final,
    COALESCE(rp.rule_is_target, b.is_target) AS is_target_final,
    COALESCE(rp.rule_is_transfer, b.is_transfer) AS is_transfer_final,
    ap.confidence AS ai_confidence,
    ap.model AS ai_model
FROM base b
LEFT JOIN rule_pick rp ON rp.id = b.id
LEFT JOIN ai_pick ap ON ap.merchant_key = b.merchant_key;
