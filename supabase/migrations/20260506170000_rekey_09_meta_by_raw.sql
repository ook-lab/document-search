-- 09_unified_documents_meta を doc_id 主キーから raw 主キーへ変更する。
-- 目的: 03/04/05/08 の raw 行が存在した時点で meta 行を持てるようにする。

-- 1) 列追加（なければ）
ALTER TABLE public."09_unified_documents_meta"
  ADD COLUMN IF NOT EXISTS raw_id UUID,
  ADD COLUMN IF NOT EXISTS raw_table TEXT;

-- 先に旧PK(doc_id)を外し、raw 基準へ切り替えるため doc_id を nullable にする
ALTER TABLE public."09_unified_documents_meta"
  DROP CONSTRAINT IF EXISTS "09_unified_documents_meta_pkey";

ALTER TABLE public."09_unified_documents_meta"
  ALTER COLUMN doc_id DROP NOT NULL;

-- 2) 既存データの raw キーを 09 から逆引きで補完
UPDATE public."09_unified_documents_meta" um
SET raw_id = ud.raw_id,
    raw_table = ud.raw_table
FROM public."09_unified_documents" ud
WHERE um.doc_id = ud.id
  AND (um.raw_id IS NULL OR um.raw_table IS NULL);

-- 3) 対象外（03/04/05/08 以外）を削除
DELETE FROM public."09_unified_documents_meta"
WHERE raw_table IS NOT NULL
  AND raw_table NOT IN (
    '03_ema_classroom_01_raw',
    '04_ikuya_classroom_01_raw',
    '05_ikuya_waseaca_01_raw',
    '08_file_only_01_raw'
  );

-- 4) raw 行（03/04/05/08）全件を meta に補完
INSERT INTO public."09_unified_documents_meta" (raw_table, raw_id, doc_id, ix_vectorized_at, updated_at)
SELECT '03_ema_classroom_01_raw', r.id, NULL, NULL, now()
FROM public."03_ema_classroom_01_raw" r
WHERE NOT EXISTS (
  SELECT 1 FROM public."09_unified_documents_meta" um
  WHERE um.raw_table = '03_ema_classroom_01_raw' AND um.raw_id = r.id
);

INSERT INTO public."09_unified_documents_meta" (raw_table, raw_id, doc_id, ix_vectorized_at, updated_at)
SELECT '04_ikuya_classroom_01_raw', r.id, NULL, NULL, now()
FROM public."04_ikuya_classroom_01_raw" r
WHERE NOT EXISTS (
  SELECT 1 FROM public."09_unified_documents_meta" um
  WHERE um.raw_table = '04_ikuya_classroom_01_raw' AND um.raw_id = r.id
);

INSERT INTO public."09_unified_documents_meta" (raw_table, raw_id, doc_id, ix_vectorized_at, updated_at)
SELECT '05_ikuya_waseaca_01_raw', r.id, NULL, NULL, now()
FROM public."05_ikuya_waseaca_01_raw" r
WHERE NOT EXISTS (
  SELECT 1 FROM public."09_unified_documents_meta" um
  WHERE um.raw_table = '05_ikuya_waseaca_01_raw' AND um.raw_id = r.id
);

INSERT INTO public."09_unified_documents_meta" (raw_table, raw_id, doc_id, ix_vectorized_at, updated_at)
SELECT '08_file_only_01_raw', r.id, NULL, NULL, now()
FROM public."08_file_only_01_raw" r
WHERE NOT EXISTS (
  SELECT 1 FROM public."09_unified_documents_meta" um
  WHERE um.raw_table = '08_file_only_01_raw' AND um.raw_id = r.id
);

-- 5) raw キーを必須化し、主キーを raw に付け替え
ALTER TABLE public."09_unified_documents_meta"
  ALTER COLUMN raw_table SET NOT NULL,
  ALTER COLUMN raw_id SET NOT NULL;

ALTER TABLE public."09_unified_documents_meta"
  DROP CONSTRAINT IF EXISTS "09_unified_documents_meta_pkey";

ALTER TABLE public."09_unified_documents_meta"
  ADD CONSTRAINT "09_unified_documents_meta_pkey" PRIMARY KEY (raw_table, raw_id);

-- doc_id は 09 行が出来た後に紐づくため nullable のまま
-- 一意は維持（NULL は複数可）
ALTER TABLE public."09_unified_documents_meta"
  DROP CONSTRAINT IF EXISTS "09_unified_documents_meta_doc_id_key";
ALTER TABLE public."09_unified_documents_meta"
  ADD CONSTRAINT "09_unified_documents_meta_doc_id_key" UNIQUE (doc_id);

-- 6) raw 登録時に meta 行を作るトリガー
CREATE OR REPLACE FUNCTION public.ensure_09_meta_on_raw_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_TABLE_NAME IN (
    '03_ema_classroom_01_raw',
    '04_ikuya_classroom_01_raw',
    '05_ikuya_waseaca_01_raw',
    '08_file_only_01_raw'
  ) THEN
    INSERT INTO public."09_unified_documents_meta"(raw_table, raw_id, doc_id, ix_vectorized_at, updated_at)
    VALUES (TG_TABLE_NAME, NEW.id, NULL, NULL, now())
    ON CONFLICT (raw_table, raw_id) DO UPDATE
      SET updated_at = EXCLUDED.updated_at;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_09_meta_raw_03_insert ON public."03_ema_classroom_01_raw";
CREATE TRIGGER trg_09_meta_raw_03_insert
AFTER INSERT ON public."03_ema_classroom_01_raw"
FOR EACH ROW EXECUTE FUNCTION public.ensure_09_meta_on_raw_insert();

DROP TRIGGER IF EXISTS trg_09_meta_raw_04_insert ON public."04_ikuya_classroom_01_raw";
CREATE TRIGGER trg_09_meta_raw_04_insert
AFTER INSERT ON public."04_ikuya_classroom_01_raw"
FOR EACH ROW EXECUTE FUNCTION public.ensure_09_meta_on_raw_insert();

DROP TRIGGER IF EXISTS trg_09_meta_raw_05_insert ON public."05_ikuya_waseaca_01_raw";
CREATE TRIGGER trg_09_meta_raw_05_insert
AFTER INSERT ON public."05_ikuya_waseaca_01_raw"
FOR EACH ROW EXECUTE FUNCTION public.ensure_09_meta_on_raw_insert();

DROP TRIGGER IF EXISTS trg_09_meta_raw_08_insert ON public."08_file_only_01_raw";
CREATE TRIGGER trg_09_meta_raw_08_insert
AFTER INSERT ON public."08_file_only_01_raw"
FOR EACH ROW EXECUTE FUNCTION public.ensure_09_meta_on_raw_insert();

-- 7) 09 作成時に doc_id を紐づけるトリガー
CREATE OR REPLACE FUNCTION public.bind_09_doc_id_to_meta()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.raw_table IN (
    '03_ema_classroom_01_raw',
    '04_ikuya_classroom_01_raw',
    '05_ikuya_waseaca_01_raw',
    '08_file_only_01_raw'
  ) THEN
    INSERT INTO public."09_unified_documents_meta"(raw_table, raw_id, doc_id, ix_vectorized_at, updated_at)
    VALUES (NEW.raw_table, NEW.raw_id, NEW.id, NULL, now())
    ON CONFLICT (raw_table, raw_id) DO UPDATE
      SET doc_id = EXCLUDED.doc_id,
          updated_at = now();
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_bind_09_doc_id_to_meta_insert ON public."09_unified_documents";
CREATE TRIGGER trg_bind_09_doc_id_to_meta_insert
AFTER INSERT ON public."09_unified_documents"
FOR EACH ROW EXECUTE FUNCTION public.bind_09_doc_id_to_meta();

DO $$
BEGIN
  RAISE NOTICE '09_unified_documents_meta を raw基準へ変更し、03/04/05/08 で自動作成するようにしました';
END $$;

