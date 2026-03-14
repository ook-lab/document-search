-- daily_reports → 11_daily_reports にリネーム
-- ※ まだ daily_reports が存在しない場合は CREATE のみ実行

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables WHERE tablename = 'daily_reports' AND schemaname = 'public') THEN
        ALTER TABLE daily_reports RENAME TO "11_daily_reports";
    ELSE
        CREATE TABLE IF NOT EXISTS "11_daily_reports" (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            base_date       DATE        NOT NULL UNIQUE,   -- レポート起点日（JST）
            generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            report_json     JSONB       NOT NULL,          -- 8ページ分の完成レポート
            version         INT         NOT NULL DEFAULT 1
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS dr_base_date_idx
    ON "11_daily_reports" (base_date DESC);

ALTER TABLE "11_daily_reports" ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = '11_daily_reports' AND policyname = 'service_role full access'
    ) THEN
        CREATE POLICY "service_role full access"
            ON "11_daily_reports" FOR ALL TO service_role
            USING (true) WITH CHECK (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = '11_daily_reports' AND policyname = 'authenticated read'
    ) THEN
        CREATE POLICY "authenticated read"
            ON "11_daily_reports" FOR SELECT TO authenticated
            USING (true);
    END IF;
END $$;

COMMENT ON TABLE "11_daily_reports"
    IS '日次レポート（8ページ）。毎日1件、base_date でユニーク。';
