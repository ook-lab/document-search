-- ============================================================
-- doc_type 復元 migration
-- ============================================================
-- 原因: pipeline_manager.py の Stage A が doc_type を
--       PDF内部分類（WORD/REPORT/DTP 等）で上書きしていた。
-- 対象: doc_type が Stage A の分類値になっているレコード
-- ============================================================

-- Stage A が書き込む可能性のある値
-- （これらが残っている = 上書き被害）

DO $$
DECLARE
    stage_a_values TEXT[] := ARRAY[
        'WORD', 'EXCEL', 'REPORT', 'DTP',
        'GOOGLE_DOCS', 'GOOGLE_SHEETS', 'GOODNOTES',
        'SCAN', 'MIXED', 'UNKNOWN',
        'INDESIGN', 'ILLUSTRATOR', 'ERROR'
    ];
    n INT;
BEGIN
    -- ========================================================
    -- 1. waseda_academy → '早稲アカオンライン'
    -- ========================================================
    UPDATE "Rawdata_FILE_AND_MAIL"
    SET doc_type = '早稲アカオンライン'
    WHERE workspace = 'waseda_academy'
      AND doc_type = ANY(stage_a_values);
    GET DIAGNOSTICS n = ROW_COUNT;
    RAISE NOTICE '✅ waseda_academy: %件復元', n;

    -- ========================================================
    -- 2. gmail → 'DM-mail'
    --    (.env の GMAIL_DM_* のみ使用を確認)
    -- ========================================================
    UPDATE "Rawdata_FILE_AND_MAIL"
    SET doc_type = 'DM-mail'
    WHERE workspace = 'gmail'
      AND doc_type = ANY(stage_a_values);
    GET DIAGNOSTICS n = ROW_COUNT;
    RAISE NOTICE '✅ gmail (DM-mail): %件復元', n;

    -- ========================================================
    -- 3. shopping (tokubai チラシ) → 'physical shop'
    -- ========================================================
    UPDATE "Rawdata_FILE_AND_MAIL"
    SET doc_type = 'physical shop'
    WHERE workspace = 'shopping'
      AND doc_type = ANY(stage_a_values);
    GET DIAGNOSTICS n = ROW_COUNT;
    RAISE NOTICE '✅ shopping (physical shop): %件復元', n;

END $$;

-- 残存する異常値を確認（適用後に0件であることを確認）
SELECT workspace, doc_type, count(*)
FROM "Rawdata_FILE_AND_MAIL"
WHERE doc_type IN (
    'WORD', 'EXCEL', 'REPORT', 'DTP',
    'GOOGLE_DOCS', 'GOOGLE_SHEETS', 'GOODNOTES',
    'SCAN', 'MIXED', 'UNKNOWN',
    'INDESIGN', 'ILLUSTRATOR', 'ERROR'
)
GROUP BY workspace, doc_type
ORDER BY workspace, doc_type;
