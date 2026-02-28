-- ============================================================
-- doc_type 保護トリガーを削除
-- ============================================================
-- Table Editor からの修正も不可能だったため廃止。
-- doc_type の変更はアプリ UI / API 経由で行う。
-- ============================================================

DROP TRIGGER IF EXISTS trg_protect_doc_type ON "Rawdata_FILE_AND_MAIL";
DROP FUNCTION IF EXISTS fn_protect_doc_type();

DO $$
BEGIN
    RAISE NOTICE '✅ trg_protect_doc_type 削除完了: doc_type の変更が可能になりました';
END $$;
