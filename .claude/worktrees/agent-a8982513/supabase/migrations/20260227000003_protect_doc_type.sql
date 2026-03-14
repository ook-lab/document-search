-- ============================================================
-- doc_type 保護トリガー
-- ============================================================
-- doc_type は投稿（INSERT）時のみ設定可能。
-- UPDATE での変更はいかなる場合も禁止。
-- Supabase ダッシュボードから直接修正すること。
-- ============================================================

CREATE OR REPLACE FUNCTION fn_protect_doc_type()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.doc_type IS DISTINCT FROM OLD.doc_type THEN
        RAISE EXCEPTION '[doc_type] プログラムからの変更は禁止です。Supabase ダッシュボードで直接修正してください。(old=%, new=%)',
            OLD.doc_type, NEW.doc_type;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_protect_doc_type ON "Rawdata_FILE_AND_MAIL";

CREATE TRIGGER trg_protect_doc_type
BEFORE UPDATE ON "Rawdata_FILE_AND_MAIL"
FOR EACH ROW
EXECUTE FUNCTION fn_protect_doc_type();

DO $$
BEGIN
    RAISE NOTICE '✅ trg_protect_doc_type 設置完了: UPDATE による doc_type 変更を全面禁止';
END $$;
