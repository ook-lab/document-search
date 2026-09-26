-- ============================================================
-- 10_ix_search_index: Gemini (gemini-embedding-2, 1536次元) 切り替え用カラム追加
-- 既存列・RPC・インデックスは変更しない
-- ============================================================

ALTER TABLE public."10_ix_search_index"
  ADD COLUMN IF NOT EXISTS embedding_v2 vector(1536),
  ADD COLUMN IF NOT EXISTS embedding_v2_error text,
  ADD COLUMN IF NOT EXISTS embedding_v2_at timestamptz;

COMMENT ON COLUMN public."10_ix_search_index".embedding_v2 IS 'Gemini gemini-embedding-2 (1536次元) ベクトル';
COMMENT ON COLUMN public."10_ix_search_index".embedding_v2_error IS 'Gemini ベクトル化失敗時のエラー理由';
COMMENT ON COLUMN public."10_ix_search_index".embedding_v2_at IS 'Gemini ベクトル化成功・更新日時';
