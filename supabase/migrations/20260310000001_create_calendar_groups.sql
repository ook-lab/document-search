CREATE TABLE IF NOT EXISTS calendar_groups (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_email  TEXT        NOT NULL,
    name         TEXT        NOT NULL,
    color        TEXT        NOT NULL DEFAULT '#4285F4',
    base_ids     TEXT[]      NOT NULL DEFAULT '{}',
    sort_order   INT         NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS cg_owner_idx ON calendar_groups (owner_email);

ALTER TABLE calendar_groups ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role full access" ON calendar_groups;
CREATE POLICY "service_role full access"
    ON calendar_groups FOR ALL TO service_role
    USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "authenticated read own" ON calendar_groups;
CREATE POLICY "authenticated read own"
    ON calendar_groups FOR SELECT TO authenticated
    USING (owner_email = auth.jwt() ->> 'email');

COMMENT ON TABLE calendar_groups IS 'カレンダーグループ設定（my-calendar-app）';
