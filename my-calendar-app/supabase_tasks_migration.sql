-- tasks テーブル
CREATE TABLE IF NOT EXISTS tasks (
  id           UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  owner_email  TEXT NOT NULL DEFAULT 'ookubo.y@workspace-o.com',
  title        TEXT NOT NULL,
  description  TEXT,
  due_date     DATE,
  assignee     TEXT,
  status       TEXT NOT NULL DEFAULT 'todo'
                 CHECK (status IN ('todo', 'doing', 'done')),
  calendar_group_id UUID REFERENCES calendar_groups(id) ON DELETE SET NULL,
  trello_card_id    TEXT UNIQUE,
  trello_list_id    TEXT,
  google_event_id   TEXT,
  sort_order   INTEGER NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ DEFAULT now(),
  updated_at   TIMESTAMPTZ DEFAULT now()
);

-- updated_at 自動更新トリガー
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tasks_updated_at
  BEFORE UPDATE ON tasks
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- インデックス
CREATE INDEX IF NOT EXISTS tasks_owner_email_idx ON tasks(owner_email);
CREATE INDEX IF NOT EXISTS tasks_due_date_idx    ON tasks(due_date);
CREATE INDEX IF NOT EXISTS tasks_status_idx      ON tasks(status);
CREATE INDEX IF NOT EXISTS tasks_trello_card_idx ON tasks(trello_card_id);
