-- daily_reports: 日次レポート保存テーブル
-- 毎日1件（base_date でユニーク）、report_json に8ページ分を保持

CREATE TABLE IF NOT EXISTS daily_reports (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    base_date    DATE        NOT NULL UNIQUE,  -- レポート起点日（JST）
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    report_json  JSONB       NOT NULL,         -- 8ページ分の完成レポート
    version      INT         NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS daily_reports_base_date_idx
    ON daily_reports (base_date DESC);

-- RLS: service_role のみ書き込み、authenticated も読み取り可
ALTER TABLE daily_reports ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role full access"
    ON daily_reports FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "authenticated read"
    ON daily_reports FOR SELECT
    TO authenticated
    USING (true);

COMMENT ON TABLE daily_reports IS '日次レポート（8ページ）- 毎日1回 ReportGenerator により生成';
COMMENT ON COLUMN daily_reports.base_date    IS 'レポート起点日（JST、1ページ目 = この日）';
COMMENT ON COLUMN daily_reports.report_json  IS '{"base_date":"...","pages":[{page_no,date,schedule,homework,...}]}';
