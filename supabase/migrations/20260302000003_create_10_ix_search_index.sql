-- ============================================================
-- 10_ix_search_index テーブル作成
-- チャンク単位の検索インデックス（J書き込み + K embedding）
-- ============================================================

-- 既存テーブルを削除して作り直す
DROP TABLE IF EXISTS public."10_ix_search_index" CASCADE;

CREATE TABLE public."10_ix_search_index" (

  -- Identity
  id            UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
  doc_id        UUID        NOT NULL,  -- 09_unified_documents.id への参照

  -- 09 からの非正規化（絞り込み検索用・トリガーで自動同期）
  person        TEXT,
  source        TEXT,
  category      TEXT,

  -- J が書く
  chunk_index   INT         NOT NULL DEFAULT 0,
  chunk_text    TEXT        NOT NULL,

  -- K が書く
  embedding     vector(1536),

  -- System
  indexed_at    TIMESTAMPTZ DEFAULT now()

);

CREATE INDEX IF NOT EXISTS idx_10_ix_doc_id    ON public."10_ix_search_index" (doc_id);
CREATE INDEX IF NOT EXISTS idx_10_ix_person    ON public."10_ix_search_index" (person);
CREATE INDEX IF NOT EXISTS idx_10_ix_source    ON public."10_ix_search_index" (source);
CREATE INDEX IF NOT EXISTS idx_10_ix_category  ON public."10_ix_search_index" (category);

-- ベクトル検索用インデックス（embedding が入ってから有効になる）
CREATE INDEX IF NOT EXISTS idx_10_ix_embedding
  ON public."10_ix_search_index"
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

-- ============================================================
-- トリガー: 09_unified_documents の person / source / category
-- が更新されたら 10_ix_search_index に自動同期
-- ============================================================

CREATE OR REPLACE FUNCTION public.sync_10_from_09()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE public."10_ix_search_index"
  SET
    person   = NEW.person,
    source   = NEW.source,
    category = NEW.category
  WHERE doc_id = NEW.id;

  RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_sync_10_from_09
  AFTER UPDATE OF person, source, category
  ON public."09_unified_documents"
  FOR EACH ROW
  EXECUTE FUNCTION public.sync_10_from_09();

DO $$
BEGIN
  RAISE NOTICE '10_ix_search_index テーブル・トリガー作成完了';
END $$;
