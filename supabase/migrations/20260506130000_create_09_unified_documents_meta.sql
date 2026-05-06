-- 補助テーブル: 検索データ準備などのステータスのみ（本文・チャンクは置かない）。
-- 正本は 01–05 系 raw、生成テキストは 09・10。pipeline_meta はパイプライン中間用（アプリの検索準備からは読まない）。

ALTER TABLE public."09_unified_documents"
  DROP COLUMN IF EXISTS search_vectorized_at;

DROP INDEX IF EXISTS public.idx_09_unified_search_vector_pending;

CREATE TABLE IF NOT EXISTS public."09_unified_documents_meta" (
  doc_id            UUID PRIMARY KEY
                      REFERENCES public."09_unified_documents"(id) ON DELETE CASCADE,
  ix_vectorized_at  TIMESTAMPTZ,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public."09_unified_documents_meta" IS
  '09 行に付随する補助テーブル。ステータス（時刻等）のみ。本文は raw / 09、チャンクは 10 を参照。';

COMMENT ON COLUMN public."09_unified_documents_meta".ix_vectorized_at IS
  'rag-prepare（fast-index）が 10_ix_search_index を更新した日時。';

CREATE INDEX IF NOT EXISTS idx_09_ud_meta_ix_pending
  ON public."09_unified_documents_meta" (doc_id)
  WHERE ix_vectorized_at IS NULL;

-- マイグレーション専用の一回限り: 既存 pipeline_meta.vectorized_at を移す（運用コードは pipeline_meta を参照しない）
INSERT INTO public."09_unified_documents_meta" (doc_id, ix_vectorized_at, updated_at)
SELECT ud.id, pm.vectorized_at, now()
FROM public."09_unified_documents" ud
INNER JOIN public.pipeline_meta pm
  ON ud.raw_id = pm.raw_id AND ud.raw_table = pm.raw_table
WHERE pm.vectorized_at IS NOT NULL
ON CONFLICT (doc_id) DO UPDATE SET
  ix_vectorized_at = COALESCE(
    public."09_unified_documents_meta".ix_vectorized_at,
    EXCLUDED.ix_vectorized_at
  ),
  updated_at = now();

DO $$
BEGIN
  RAISE NOTICE '09_unified_documents_meta を作成し pipeline_meta.vectorized_at をバックフィルしました';
END $$;
