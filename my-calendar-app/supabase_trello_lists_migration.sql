-- Trelloボード管理テーブル
CREATE TABLE IF NOT EXISTS trello_boards (
  id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  owner_email TEXT NOT NULL DEFAULT 'ookubo.y@workspace-o.com',
  board_id    TEXT NOT NULL UNIQUE,
  board_name  TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- Trelloリスト→status対応テーブル
CREATE TABLE IF NOT EXISTS trello_lists (
  id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  board_id    TEXT NOT NULL,
  list_id     TEXT NOT NULL UNIQUE,
  list_name   TEXT,
  status      TEXT NOT NULL DEFAULT 'todo'
                CHECK (status IN ('todo', 'doing', 'done')),
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS trello_lists_list_id_idx  ON trello_lists(list_id);
CREATE INDEX IF NOT EXISTS trello_lists_board_id_idx ON trello_lists(board_id);
