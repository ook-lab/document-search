-- ============================================================
-- エラー隠蔽禁止: 状態遷移ガード
--
-- 目的:
--   1. processing_status の許可値を制約
--   2. completed への遷移時に必須条件を検証
--   3. 条件未達なら failed へ自動落とし（理由記録）
--
-- 実行: Supabase SQL Editor で実行
-- ============================================================

-- ============================================================
-- STEP 1: 必要なカラムを追加（存在しなければ）
-- ============================================================

-- failed_stage: どのステージで失敗したか
ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS failed_stage text;

-- failed_at: 失敗した日時
ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS failed_at timestamptz;

-- ============================================================
-- STEP 2: processing_status のCHECK制約
-- ============================================================

-- 既存の制約があれば削除（エラーは無視）
ALTER TABLE public."Rawdata_FILE_AND_MAIL"
DROP CONSTRAINT IF EXISTS chk_processing_status;

-- 新しい制約を追加
ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD CONSTRAINT chk_processing_status
CHECK (processing_status IN ('pending', 'processing', 'completed', 'failed', 'skipped'));

-- ============================================================
-- STEP 3: completed 遷移ガード関数
--
-- 戦略: 「failedへ自動落とし」
--   - 条件未達の場合、completedを拒否してfailedに変更
--   - 理由を error_message に記録
--   - バッチ処理を止めずに失敗を記録
-- ============================================================

CREATE OR REPLACE FUNCTION public.guard_completed_status()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    chunk_count integer;
    error_reasons text[];
BEGIN
    -- completed への遷移時のみチェック
    IF NEW.processing_status = 'completed' AND
       (OLD.processing_status IS NULL OR OLD.processing_status != 'completed') THEN

        error_reasons := ARRAY[]::text[];

        -- 条件1: stage_j_chunks_json が存在する
        IF NEW.stage_j_chunks_json IS NULL THEN
            error_reasons := array_append(error_reasons, 'stage_j_chunks_json is NULL');
        END IF;

        -- 条件2: summary が存在する（空文字もNG）
        IF NEW.summary IS NULL OR trim(NEW.summary) = '' THEN
            error_reasons := array_append(error_reasons, 'summary is NULL or empty');
        END IF;

        -- 条件3: search_index にチャンクが1件以上存在
        SELECT count(*) INTO chunk_count
        FROM public.search_index
        WHERE doc_id = NEW.id;

        IF chunk_count = 0 THEN
            error_reasons := array_append(error_reasons,
                'no chunks in search_index (count=0)');
        END IF;

        -- エラーがあれば failed に落とす
        IF array_length(error_reasons, 1) > 0 THEN
            NEW.processing_status := 'failed';
            NEW.error_message := 'completed rejected: ' || array_to_string(error_reasons, '; ');
            NEW.failed_stage := 'completion_guard';
            NEW.failed_at := now();

            -- ログ出力（デバッグ用）
            RAISE NOTICE 'Document % rejected from completed: %',
                NEW.id, NEW.error_message;
        END IF;
    END IF;

    -- updated_at の自動更新
    NEW.updated_at := now();

    RETURN NEW;
END;
$$;

-- ============================================================
-- STEP 4: トリガを作成（BEFORE UPDATE）
-- ============================================================

-- 既存トリガがあれば削除
DROP TRIGGER IF EXISTS trg_guard_completed ON public."Rawdata_FILE_AND_MAIL";

-- 新しいトリガを作成
CREATE TRIGGER trg_guard_completed
BEFORE UPDATE ON public."Rawdata_FILE_AND_MAIL"
FOR EACH ROW
EXECUTE FUNCTION public.guard_completed_status();

-- ============================================================
-- STEP 5: 検証クエリ
-- ============================================================

-- 制約の確認
SELECT 'CHECK制約' AS check_item, conname AS name
FROM pg_constraint
WHERE conrelid = 'public."Rawdata_FILE_AND_MAIL"'::regclass
  AND contype = 'c';

-- トリガの確認
SELECT 'トリガ' AS check_item, tgname AS name
FROM pg_trigger
WHERE tgrelid = 'public."Rawdata_FILE_AND_MAIL"'::regclass
  AND tgname LIKE 'trg_%';

-- カラムの確認
SELECT 'カラム' AS check_item, column_name AS name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'Rawdata_FILE_AND_MAIL'
  AND column_name IN ('failed_stage', 'failed_at', 'error_message');

-- 現在の status 分布
SELECT 'status分布' AS check_item,
       processing_status AS name,
       count(*) AS count
FROM public."Rawdata_FILE_AND_MAIL"
GROUP BY processing_status;
