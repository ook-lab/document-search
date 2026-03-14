-- Run this in Supabase SQL Editor

-- ユーザーごとの表示設定
CREATE TABLE IF NOT EXISTS user_preferences (
  email               TEXT PRIMARY KEY,
  selected_base_ids   TEXT[]  NOT NULL DEFAULT '{}',
  selected_group_ids  TEXT[]  NOT NULL DEFAULT '{}',
  cal_view_mode       JSONB   NOT NULL DEFAULT '{}',
  group_view_mode     JSONB   NOT NULL DEFAULT '{}',
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ユーザーごとのTrelloトークン
CREATE TABLE IF NOT EXISTS user_trello_tokens (
  email       TEXT PRIMARY KEY,
  token       TEXT NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
