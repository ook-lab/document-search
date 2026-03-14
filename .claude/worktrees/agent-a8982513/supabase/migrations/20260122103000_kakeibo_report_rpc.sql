CREATE OR REPLACE FUNCTION public.fn_kakeibo_report_agg(
    p_start_date date,
    p_end_date   date,
    p_group_by   text
)
RETURNS TABLE (
    group_key text,
    amount_sum bigint,
    tx_count bigint
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    WITH src AS (
        SELECT
            date,
            amount_abs,
            institution,
            merchant_key,
            category_major_final,
            category_minor_final
        FROM public."view_kakeibo_enriched_transactions"
        WHERE date >= p_start_date
          AND date <= p_end_date
          AND amount_abs IS NOT NULL
          AND is_target_final = true
          AND is_transfer_final = false
          AND amount < 0  -- 支出のみ
    ),
    keyed AS (
        SELECT
            CASE
                WHEN p_group_by = 'category_major' THEN COALESCE(category_major_final, '未分類')
                WHEN p_group_by = 'category_minor' THEN COALESCE(category_minor_final, '未分類')
                WHEN p_group_by = 'institution'    THEN COALESCE(institution, '未設定')
                WHEN p_group_by = 'merchant'       THEN COALESCE(merchant_key, '不明')
                WHEN p_group_by = 'month'          THEN to_char(date_trunc('month', date)::date, 'YYYY-MM')
                ELSE 'category_major'
            END AS group_key,
            amount_abs
        FROM src
    )
    SELECT
        keyed.group_key,
        SUM(keyed.amount_abs)::bigint AS amount_sum,
        COUNT(*)::bigint AS tx_count
    FROM keyed
    GROUP BY keyed.group_key
    ORDER BY amount_sum DESC;
END;
$$;
