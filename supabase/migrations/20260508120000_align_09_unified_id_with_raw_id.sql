-- 09_unified_documents.id を raw 行の id と同一に揃える（新規 INSERT もアプリ側で id = raw_id を付与）。
-- 子テーブル（10_ix / 10_report_candidates / meta.doc_id）を先に付け替え、FK 違反を避ける。

-- 1) meta: doc_id FK が旧 09.id を指している間は 09 の PK を変えられないため、一旦 NULL
UPDATE public."09_unified_documents_meta" um
SET doc_id = NULL
WHERE um.doc_id IN (
  SELECT ud.id
  FROM public."09_unified_documents" ud
  WHERE ud.raw_id IS NOT NULL
    AND ud.id IS DISTINCT FROM ud.raw_id
);

-- 2) 検索インデックス
UPDATE public."10_ix_search_index" t
SET doc_id = ud.raw_id
FROM public."09_unified_documents" ud
WHERE t.doc_id = ud.id
  AND ud.raw_id IS NOT NULL
  AND ud.id IS DISTINCT FROM ud.raw_id;

-- 3) 日報候補
UPDATE public."10_report_candidates" r
SET doc_id = ud.raw_id
FROM public."09_unified_documents" ud
WHERE r.doc_id = ud.id
  AND ud.raw_id IS NOT NULL
  AND ud.id IS DISTINCT FROM ud.raw_id;

-- 4) 09 主キー（他行がすでに id = raw_id を掴んでいる場合はスキップ）
UPDATE public."09_unified_documents" ud
SET id = ud.raw_id
WHERE ud.raw_id IS NOT NULL
  AND ud.id IS DISTINCT FROM ud.raw_id
  AND NOT EXISTS (
    SELECT 1
    FROM public."09_unified_documents" x
    WHERE x.id = ud.raw_id
      AND x.id IS DISTINCT FROM ud.id
  );

-- 5) meta.doc_id を 09.id に再同期（raw キーで結合）
UPDATE public."09_unified_documents_meta" um
SET doc_id = ud.id
FROM public."09_unified_documents" ud
WHERE um.raw_table = ud.raw_table
  AND um.raw_id = ud.raw_id
  AND um.doc_id IS DISTINCT FROM ud.id;

COMMENT ON COLUMN public."09_unified_documents".id IS
  'ドキュメントの唯一の身元。必ず raw_id と同一 UUID（raw 行の主キーをそのまま使う）。';

-- ランダム UUID の自動採番は禁止（省略時に raw とずれるのを防ぐ）
ALTER TABLE public."09_unified_documents"
  ALTER COLUMN id DROP DEFAULT;

ALTER TABLE public."09_unified_documents"
  DROP CONSTRAINT IF EXISTS chk_09_id_matches_raw_id;

ALTER TABLE public."09_unified_documents"
  ADD CONSTRAINT chk_09_id_matches_raw_id CHECK (id = raw_id);
