-- ============================================================
-- 振替を含むファイナンシングView
-- 通常の家計簿Viewでは振替（is_transfer=true）は除外されるが、
-- ローン残高管理などのために振替も見たい場合に使う
-- ============================================================

CREATE OR REPLACE VIEW public."view_kakeibo_financing_transactions" AS
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
    b.institution AS institution_original,
    -- ファイナンシング特有の情報
    CASE
        WHEN COALESCE(rp.is_transfer, b.is_transfer) = true THEN 'transfer'
        ELSE 'expense'
    END AS transaction_type
FROM base b
LEFT JOIN manual m ON m.transaction_id = b.id
LEFT JOIN rule_pick rp ON rp.id = b.id
LEFT JOIN ai_pick ap ON ap.merchant_key = b.merchant_key
WHERE COALESCE(m.is_excluded, false) = false;

COMMENT ON VIEW public."view_kakeibo_financing_transactions" IS '振替を含む全トランザクションビュー（ローン管理用）';
