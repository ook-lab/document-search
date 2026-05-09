-- raw 削除時に 09_unified_documents_meta が残ると、検索データ準備一覧に
-- 「テキストのみ・無題・raw 行なし」の行が出続ける。fn_cascade に meta 削除を追加する。
-- 併せて既存の孤立 meta と、05 の中身が空の残骸行を削除する。

CREATE OR REPLACE FUNCTION public.fn_cascade_delete_raw()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  DELETE FROM public."09_unified_documents"
  WHERE raw_id = OLD.id AND raw_table = TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME;

  DELETE FROM public."09_unified_documents"
  WHERE raw_id = OLD.id AND raw_table = TG_TABLE_NAME;

  DELETE FROM public.pipeline_meta
  WHERE raw_id = OLD.id AND raw_table = TG_TABLE_NAME;

  DELETE FROM public."09_unified_documents_meta"
  WHERE raw_id = OLD.id AND raw_table = TG_TABLE_NAME;

  RETURN OLD;
END;
$$;

-- 孤立 meta（対応する raw が既に無い）
DELETE FROM public."09_unified_documents_meta" m
WHERE m.raw_table = '03_ema_classroom_01_raw'
  AND NOT EXISTS (
    SELECT 1 FROM public."03_ema_classroom_01_raw" r WHERE r.id = m.raw_id
  );

DELETE FROM public."09_unified_documents_meta" m
WHERE m.raw_table = '04_ikuya_classroom_01_raw'
  AND NOT EXISTS (
    SELECT 1 FROM public."04_ikuya_classroom_01_raw" r WHERE r.id = m.raw_id
  );

DELETE FROM public."09_unified_documents_meta" m
WHERE m.raw_table = '05_ikuya_waseaca_01_raw'
  AND NOT EXISTS (
    SELECT 1 FROM public."05_ikuya_waseaca_01_raw" r WHERE r.id = m.raw_id
  );

DELETE FROM public."09_unified_documents_meta" m
WHERE m.raw_table = '08_file_only_01_raw'
  AND NOT EXISTS (
    SELECT 1 FROM public."08_file_only_01_raw" r WHERE r.id = m.raw_id
  );

-- 早稲アカ 05: タイトル・ファイル名・URL がすべて空で、ベクトル未登録の meta だけ残っている行
-- （raw は存在するが UI 上は無題扱いになる残骸）
DELETE FROM public."05_ikuya_waseaca_01_raw" r
USING public."09_unified_documents_meta" m
WHERE m.raw_table = '05_ikuya_waseaca_01_raw'
  AND m.raw_id = r.id
  AND m.ix_vectorized_at IS NULL
  AND (r.title IS NULL OR btrim(r.title) = '')
  AND (r.file_name IS NULL OR btrim(r.file_name) = '')
  AND (r.file_url IS NULL OR btrim(r.file_url) = '');

DO $$
BEGIN
  RAISE NOTICE 'fn_cascade_delete_raw: 09_unified_documents_meta も連動削除するよう更新しました';
END $$;
