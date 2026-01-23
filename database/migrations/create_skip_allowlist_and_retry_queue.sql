-- ============================================================
-- スキップallowlist + 再処理キュー
--
-- A. allowlistスキップ: skipped への遷移を許可制に
-- B. 再処理キュー: failed を自動回収してリトライ
--
-- 実行: Supabase SQL Editor で実行
-- ============================================================

-- ############################################################
-- PART A: スキップ allowlist
-- ############################################################

-- ============================================================
-- A-1: skip関連カラムを追加
-- ============================================================

ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS skip_code text;

ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS skip_reason text;

ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS skipped_at timestamptz;

-- ============================================================
-- A-2: skip_guard 関数（skipped遷移時にallowlistチェック）
-- ============================================================

CREATE OR REPLACE FUNCTION public.guard_skipped_status()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    -- allowlist（固定配列、将来テーブル化可能）
    allowed_codes text[] := ARRAY[
        'SOURCE_UNSUPPORTED',   -- 対応外形式
        'PERMISSION_DENIED',    -- 権限不足
        'CORRUPT_FILE',         -- ファイル破損
        'DUPLICATE',            -- 重複
        'USER_REQUEST',         -- 人手判断でスキップ
        'TOO_LARGE',            -- サイズ超過
        'EMPTY_CONTENT',        -- 内容が空
        'UNSUPPORTED_LANGUAGE'  -- 非対応言語
    ];
BEGIN
    -- skipped への遷移時のみチェック
    IF NEW.processing_status = 'skipped' AND
       (OLD.processing_status IS NULL OR OLD.processing_status != 'skipped') THEN

        -- 条件1: skip_code が NULL でない
        IF NEW.skip_code IS NULL THEN
            NEW.processing_status := 'failed';
            NEW.error_message := 'skipped rejected: skip_code is NULL';
            NEW.failed_stage := 'skip_guard';
            NEW.failed_at := now();
            RAISE NOTICE 'Document % skipped rejected: skip_code is NULL', NEW.id;
            RETURN NEW;
        END IF;

        -- 条件2: skip_code が allowlist 内
        IF NOT (NEW.skip_code = ANY(allowed_codes)) THEN
            NEW.processing_status := 'failed';
            NEW.error_message := 'skipped rejected: skip_code "' || NEW.skip_code || '" not in allowlist';
            NEW.failed_stage := 'skip_guard';
            NEW.failed_at := now();
            RAISE NOTICE 'Document % skipped rejected: invalid skip_code %', NEW.id, NEW.skip_code;
            RETURN NEW;
        END IF;

        -- 条件を満たした場合: skipped_at を記録
        NEW.skipped_at := now();
    END IF;

    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- ============================================================
-- A-3: skip_guard トリガを作成
-- ============================================================

DROP TRIGGER IF EXISTS trg_guard_skipped ON public."Rawdata_FILE_AND_MAIL";

CREATE TRIGGER trg_guard_skipped
BEFORE UPDATE ON public."Rawdata_FILE_AND_MAIL"
FOR EACH ROW
EXECUTE FUNCTION public.guard_skipped_status();


-- ############################################################
-- PART B: 再処理キュー (retry_queue)
-- ############################################################

-- ============================================================
-- B-1: retry_queue テーブルを作成
-- ============================================================

CREATE TABLE IF NOT EXISTS public.retry_queue (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rawdata_id uuid NOT NULL UNIQUE REFERENCES public."Rawdata_FILE_AND_MAIL"(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'leased', 'done', 'dead')),
    retry_count int NOT NULL DEFAULT 0,
    max_retries int NOT NULL DEFAULT 5,
    next_retry_at timestamptz NOT NULL DEFAULT now(),
    last_error text,
    leased_until timestamptz,
    leased_by text,  -- ワーカー識別子（オプション）
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- インデックス（ワーカー取得用）
CREATE INDEX IF NOT EXISTS idx_retry_queue_next_retry
ON public.retry_queue (status, next_retry_at)
WHERE status = 'queued';

-- ============================================================
-- B-2: failed 時に自動 enqueue するトリガ関数
-- ============================================================

CREATE OR REPLACE FUNCTION public.enqueue_failed_for_retry()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    -- failed への遷移時のみ
    IF NEW.processing_status = 'failed' AND
       (OLD.processing_status IS NULL OR OLD.processing_status != 'failed') THEN

        -- retry_queue に upsert
        INSERT INTO public.retry_queue (rawdata_id, last_error, next_retry_at)
        VALUES (NEW.id, NEW.error_message, now() + interval '5 minutes')
        ON CONFLICT (rawdata_id) DO UPDATE SET
            status = CASE
                WHEN retry_queue.status = 'dead' THEN 'dead'  -- dead は復活させない
                ELSE 'queued'
            END,
            last_error = EXCLUDED.last_error,
            next_retry_at = CASE
                WHEN retry_queue.status = 'dead' THEN retry_queue.next_retry_at
                ELSE now() + (interval '5 minutes' * power(2, retry_queue.retry_count))
            END,
            updated_at = now();

        RAISE NOTICE 'Document % enqueued for retry', NEW.id;
    END IF;

    RETURN NEW;
END;
$$;

-- ============================================================
-- B-3: enqueue トリガを作成（AFTER UPDATE）
-- ============================================================

DROP TRIGGER IF EXISTS trg_enqueue_failed ON public."Rawdata_FILE_AND_MAIL";

CREATE TRIGGER trg_enqueue_failed
AFTER UPDATE ON public."Rawdata_FILE_AND_MAIL"
FOR EACH ROW
EXECUTE FUNCTION public.enqueue_failed_for_retry();

-- ============================================================
-- B-4: ワーカー用関数: lease_retry_jobs
-- 安全にジョブを取得（重複取得防止）
-- ============================================================

CREATE OR REPLACE FUNCTION public.lease_retry_jobs(
    batch_size int DEFAULT 10,
    lease_seconds int DEFAULT 300
)
RETURNS TABLE (
    rawdata_id uuid,
    retry_count int,
    last_error text
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    WITH candidates AS (
        SELECT rq.rawdata_id, rq.retry_count, rq.last_error
        FROM public.retry_queue rq
        WHERE rq.status = 'queued'
          AND rq.next_retry_at <= now()
          AND (rq.leased_until IS NULL OR rq.leased_until < now())
        ORDER BY rq.next_retry_at
        LIMIT batch_size
        FOR UPDATE SKIP LOCKED
    ),
    updated AS (
        UPDATE public.retry_queue rq
        SET status = 'leased',
            leased_until = now() + (lease_seconds || ' seconds')::interval,
            updated_at = now()
        FROM candidates c
        WHERE rq.rawdata_id = c.rawdata_id
        RETURNING rq.rawdata_id, rq.retry_count, rq.last_error
    )
    SELECT * FROM updated;
END;
$$;

-- ============================================================
-- B-5: ワーカー用関数: mark_retry_done
-- リトライ成功時に呼び出し
-- ============================================================

CREATE OR REPLACE FUNCTION public.mark_retry_done(p_rawdata_id uuid)
RETURNS boolean
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE public.retry_queue
    SET status = 'done',
        leased_until = NULL,
        updated_at = now()
    WHERE rawdata_id = p_rawdata_id;

    RETURN FOUND;
END;
$$;

-- ============================================================
-- B-6: ワーカー用関数: mark_retry_failed
-- リトライ失敗時に呼び出し（指数バックオフ）
-- ============================================================

CREATE OR REPLACE FUNCTION public.mark_retry_failed(
    p_rawdata_id uuid,
    p_error text DEFAULT NULL
)
RETURNS boolean
LANGUAGE plpgsql
AS $$
DECLARE
    v_retry_count int;
    v_max_retries int;
    v_backoff interval;
BEGIN
    -- 現在のリトライ回数を取得
    SELECT retry_count, max_retries INTO v_retry_count, v_max_retries
    FROM public.retry_queue
    WHERE rawdata_id = p_rawdata_id;

    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;

    v_retry_count := v_retry_count + 1;

    -- 最大リトライ回数を超えたら dead に
    IF v_retry_count >= v_max_retries THEN
        UPDATE public.retry_queue
        SET status = 'dead',
            retry_count = v_retry_count,
            last_error = COALESCE(p_error, last_error),
            leased_until = NULL,
            updated_at = now()
        WHERE rawdata_id = p_rawdata_id;

        RAISE NOTICE 'Document % marked as dead after % retries', p_rawdata_id, v_retry_count;
    ELSE
        -- 指数バックオフ: 5分 * 2^retry_count
        v_backoff := (interval '5 minutes') * power(2, v_retry_count);

        UPDATE public.retry_queue
        SET status = 'queued',
            retry_count = v_retry_count,
            last_error = COALESCE(p_error, last_error),
            next_retry_at = now() + v_backoff,
            leased_until = NULL,
            updated_at = now()
        WHERE rawdata_id = p_rawdata_id;

        RAISE NOTICE 'Document % retry % scheduled for %', p_rawdata_id, v_retry_count, now() + v_backoff;
    END IF;

    RETURN TRUE;
END;
$$;

-- ============================================================
-- B-7: 権限付与
-- ============================================================

GRANT SELECT, INSERT, UPDATE ON public.retry_queue TO anon, authenticated;
GRANT EXECUTE ON FUNCTION public.lease_retry_jobs(int, int) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION public.mark_retry_done(uuid) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION public.mark_retry_failed(uuid, text) TO anon, authenticated;


-- ############################################################
-- 検証クエリ
-- ############################################################

-- カラム確認
SELECT 'skip関連カラム' AS check_item, column_name AS name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'Rawdata_FILE_AND_MAIL'
  AND column_name IN ('skip_code', 'skip_reason', 'skipped_at');

-- トリガ確認
SELECT 'トリガ' AS check_item, tgname AS name
FROM pg_trigger
WHERE tgrelid = 'public."Rawdata_FILE_AND_MAIL"'::regclass
  AND tgname LIKE 'trg_%';

-- retry_queue テーブル確認
SELECT 'retry_queue' AS check_item, column_name AS name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'retry_queue';

-- 関数確認
SELECT 'RPC関数' AS check_item, proname AS name
FROM pg_proc
JOIN pg_namespace ON pg_namespace.oid = pg_proc.pronamespace
WHERE nspname = 'public'
  AND proname IN ('guard_skipped_status', 'enqueue_failed_for_retry',
                  'lease_retry_jobs', 'mark_retry_done', 'mark_retry_failed');
