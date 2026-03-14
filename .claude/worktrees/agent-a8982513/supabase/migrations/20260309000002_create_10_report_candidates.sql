-- ============================================================
-- 10_report_candidates
-- 日報検索用の最小単位テーブル
-- 1行 = 1予定 / 1課題 / 1提出物 / 1注意事項 / 1持ち物群 / 1記事文脈
--
-- 生成元: G18 ステージ
-- 検索元: daily-report サービス
-- ============================================================

CREATE TABLE IF NOT EXISTS "10_report_candidates" (

    -- ── 主キー ────────────────────────────────────────────
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- ── 元文書参照 ────────────────────────────────────────
    doc_id      UUID        NOT NULL,   -- 09_unified_documents.id
    raw_id      UUID,                   -- 元RAWテーブルのid
    raw_table   TEXT,                   -- 元RAWテーブル名 (02_gcal_01_raw 等)

    -- ── 共通属性 ──────────────────────────────────────────
    person          TEXT,               -- 担当者 (育哉, 宜紀 等)
    source          TEXT,               -- Googleカレンダー / gmail / 早稲アカオンライン 等
    category        TEXT,
    source_priority SMALLINT DEFAULT 5, -- 1(高)〜9(低): Calendar=1, Classroom=2, Gmail=5

    -- ── レコード種別 ──────────────────────────────────────
    -- event / task / submission / notice / item_to_bring / exam /
    -- lesson / timetable_slot / schedule_item / homework_item /
    -- checklist_item / article_context / promotion / irrelevant
    record_type TEXT        NOT NULL,
    subtype     TEXT,                   -- より細かい分類 (class_period, daily_task 等)

    -- ── 内容 ──────────────────────────────────────────────
    title           TEXT,
    summary         TEXT,               -- embedding 用に正規化したテキスト
    details_json    JSONB,              -- 元データの詳細 (payload)

    -- ── 日付系 ────────────────────────────────────────────
    date_primary    DATE,               -- 主たる日付（検索の主キー）
    date_start      TIMESTAMPTZ,        -- 開始日時
    date_end        TIMESTAMPTZ,        -- 終了日時
    due_date        DATE,               -- 締切日
    date_confidence TEXT DEFAULT 'high',-- high / medium / low

    -- ── 日報制御 ──────────────────────────────────────────
    is_report_worthy    BOOLEAN DEFAULT TRUE,   -- 日報に載せるか
    is_actionable       BOOLEAN DEFAULT FALSE,  -- 行動が必要か
    report_priority     SMALLINT DEFAULT 5,     -- 1(高)〜9(低)
    status              TEXT DEFAULT 'pending', -- pending / done / skipped
    is_completed        BOOLEAN DEFAULT FALSE,

    -- ── 検索系 ────────────────────────────────────────────
    embedding   vector(1536),           -- OpenAI text-embedding-3-small
    topic_key   TEXT,                   -- 同一イベントの重複束ね用キー

    -- ── 追跡 ──────────────────────────────────────────────
    origin_stage    TEXT,               -- G18 等
    origin_path     TEXT,               -- g5_timeline / g5_actions / g17_table 等
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── インデックス ────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS rc_date_primary_idx
    ON "10_report_candidates" (date_primary);

CREATE INDEX IF NOT EXISTS rc_person_date_idx
    ON "10_report_candidates" (person, date_primary);

CREATE INDEX IF NOT EXISTS rc_record_type_idx
    ON "10_report_candidates" (record_type);

CREATE INDEX IF NOT EXISTS rc_doc_id_idx
    ON "10_report_candidates" (doc_id);

CREATE INDEX IF NOT EXISTS rc_due_date_idx
    ON "10_report_candidates" (due_date)
    WHERE due_date IS NOT NULL;

CREATE INDEX IF NOT EXISTS rc_incomplete_idx
    ON "10_report_candidates" (due_date, is_completed)
    WHERE is_completed = FALSE AND due_date IS NOT NULL;

-- ── RLS ────────────────────────────────────────────────────
ALTER TABLE "10_report_candidates" ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role full access"
    ON "10_report_candidates" FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "authenticated read"
    ON "10_report_candidates" FOR SELECT
    TO authenticated
    USING (true);

-- ── updated_at 自動更新 ─────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER rc_updated_at
    BEFORE UPDATE ON "10_report_candidates"
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ── コメント ───────────────────────────────────────────────
COMMENT ON TABLE "10_report_candidates"
    IS '日報検索用最小単位。1行=1予定/1課題/1提出物/1注意事項。G18により09_unified_documentsから生成。';

COMMENT ON COLUMN "10_report_candidates".record_type
    IS 'event/task/submission/notice/item_to_bring/exam/lesson/timetable_slot/schedule_item/homework_item/checklist_item/article_context/promotion/irrelevant';

COMMENT ON COLUMN "10_report_candidates".date_primary
    IS 'その候補が最も強く関係する日付。日報の日別ページ振り分けに使用。';

COMMENT ON COLUMN "10_report_candidates".source_priority
    IS '1(最高)〜9(最低): Calendar=1, Classroom=2, Document=3, Gmail=5, 不明=9';
