-- tasks テーブルのスキーマ変更
-- title → card_name にリネーム
ALTER TABLE tasks RENAME COLUMN title TO card_name;

-- status カラムを削除
ALTER TABLE tasks DROP COLUMN IF EXISTS status;

-- completed_at も status 依存なので削除
ALTER TABLE tasks DROP COLUMN IF EXISTS completed_at;

-- list_name / board_name / board_id カラムを追加
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS list_name  TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS board_id   TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS board_name TEXT;
