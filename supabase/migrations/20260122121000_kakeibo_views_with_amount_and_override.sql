-- ============================================================
-- Viewを更新：金額条件とinstitution_overrideのサポート
-- ============================================================

-- 既存Viewを削除
DROP VIEW IF EXISTS public."view_kakeibo_enriched_transactions";
DROP VIEW IF EXISTS public."view_kakeibo_all_transactions";

-- ============================================================
-- 1) 仕訳済みView（除外されていないデータのみ）
-- ============================================================
CREATE OR REPLACE VIEW public."view_kakeibo_enriched_transactions" AS
WITH base AS (
    SELECT
        t.*,
        public.fn_kakeibo_normalize_merchant(t.content) AS merchant_key
    FROM public."Rawdata_BANK_transactions" t
),
-- 手動修正を結合
manual AS (
    SELECT * FROM public."Kakeibo_Manual_Edits"
),
-- ルール適用（金額条件追加）
rule_hit AS (
    SELECT
        b.id,
        r.rule_id,
        r.priority,
        r.category_major,
        r.category_minor,
        r.is_target,
        r.is_transfer,
        r.institution_override
    FROM base b
    JOIN public."Kakeibo_CategoryRules" r
      ON r.is_enabled = true
      AND (r.institution IS NULL OR r.institution = b.institution)
      -- 金額条件（NULLならスルー）
      AND (r.amount_min IS NULL OR abs(b.amount) >= r.amount_min)
      AND (r.amount_max IS NULL OR abs(b.amount) <= r.amount_max)
      AND (
          (r.match_type = 'exact'    AND b.content = r.pattern) OR
          (r.match_type = 'contains' AND b.content ILIKE '%' || r.pattern || '%') OR
          (r.match_type = 'prefix'   AND b.content ILIKE r.pattern || '%') OR
          (r.match_type = 'regex'    AND b.content ~* r.pattern)
      )
),
rule_pick AS (
    SELECT DISTINCT ON (id) *
    FROM rule_hit
    ORDER BY id, priority ASC, rule_id ASC
),
ai_pick AS (
    SELECT * FROM public."Kakeibo_AI_CategoryCache"
)
SELECT
    b.id,
    b.date,
    -- institution_overrideがあれば上書き
    COALESCE(rp.institution_override, b.institution) AS institution,
    b.content,
    b.memo,
    b.amount,
    abs(b.amount) AS amount_abs,
    b.created_at,
    b.updated_at,
    b.merchant_key,

    -- 優先順位: 手動(Manual) > ルール(Rule) > AI > '未分類'
    COALESCE(m.manual_category_major, rp.category_major, ap.category_major, '未分類') AS category_major_final,
    COALESCE(m.manual_category_minor, rp.category_minor, ap.category_minor) AS category_minor_final,

    -- 除外フラグや振替フラグの解決
    COALESCE(m.is_excluded, false) AS is_excluded,
    COALESCE(rp.is_target, b.is_target) AS is_target_final,
    COALESCE(rp.is_transfer, b.is_transfer) AS is_transfer_final,

    ap.confidence AS ai_confidence,
    ap.model AS ai_model,
    m.note AS manual_note,

    -- 元のinstitutionも保持（デバッグ用）
    b.institution AS institution_original
FROM base b
LEFT JOIN manual m ON m.transaction_id = b.id
LEFT JOIN rule_pick rp ON rp.id = b.id
LEFT JOIN ai_pick ap ON ap.merchant_key = b.merchant_key
WHERE COALESCE(m.is_excluded, false) = false;

-- ============================================================
-- 2) 除外データも含む全件View（UI用）
-- ============================================================
CREATE OR REPLACE VIEW public."view_kakeibo_all_transactions" AS
WITH base AS (
    SELECT
        t.*,
        public.fn_kakeibo_normalize_merchant(t.content) AS merchant_key
    FROM public."Rawdata_BANK_transactions" t
),
manual AS (
    SELECT * FROM public."Kakeibo_Manual_Edits"
),
rule_hit AS (
    SELECT
        b.id,
        r.rule_id,
        r.priority,
        r.category_major,
        r.category_minor,
        r.is_target,
        r.is_transfer,
        r.institution_override
    FROM base b
    JOIN public."Kakeibo_CategoryRules" r
      ON r.is_enabled = true
      AND (r.institution IS NULL OR r.institution = b.institution)
      AND (r.amount_min IS NULL OR abs(b.amount) >= r.amount_min)
      AND (r.amount_max IS NULL OR abs(b.amount) <= r.amount_max)
      AND (
          (r.match_type = 'exact'    AND b.content = r.pattern) OR
          (r.match_type = 'contains' AND b.content ILIKE '%' || r.pattern || '%') OR
          (r.match_type = 'prefix'   AND b.content ILIKE r.pattern || '%') OR
          (r.match_type = 'regex'    AND b.content ~* r.pattern)
      )
),
rule_pick AS (
    SELECT DISTINCT ON (id) *
    FROM rule_hit
    ORDER BY id, priority ASC, rule_id ASC
),
ai_pick AS (
    SELECT * FROM public."Kakeibo_AI_CategoryCache"
)
SELECT
    b.id,
    b.date,
    COALESCE(rp.institution_override, b.institution) AS institution,
    b.content,
    b.memo,
    b.amount,
    abs(b.amount) AS amount_abs,
    b.merchant_key,
    COALESCE(m.manual_category_major, rp.category_major, ap.category_major, '未分類') AS category_major_final,
    COALESCE(m.manual_category_minor, rp.category_minor, ap.category_minor) AS category_minor_final,
    COALESCE(m.is_excluded, false) AS is_excluded,
    COALESCE(rp.is_target, b.is_target) AS is_target_final,
    COALESCE(rp.is_transfer, b.is_transfer) AS is_transfer_final,
    m.note AS manual_note,
    b.institution AS institution_original
FROM base b
LEFT JOIN manual m ON m.transaction_id = b.id
LEFT JOIN rule_pick rp ON rp.id = b.id
LEFT JOIN ai_pick ap ON ap.merchant_key = b.merchant_key;
