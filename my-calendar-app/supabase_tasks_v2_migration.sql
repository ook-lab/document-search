-- tasks テーブルに列追加
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS archived      BOOLEAN     NOT NULL DEFAULT false;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completed_at  TIMESTAMPTZ;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source        TEXT        DEFAULT 'manual';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS sync_updated_at TIMESTAMPTZ;

-- trello_card_id の重複防止（重複がなければ通る）
ALTER TABLE tasks ADD CONSTRAINT tasks_trello_card_id_unique UNIQUE (trello_card_id);
