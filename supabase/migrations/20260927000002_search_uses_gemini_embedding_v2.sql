-- 20260927000002_search_uses_gemini_embedding_v2.sql
-- unified_search_v2 のベクトル評価を embedding（OpenAI）から embedding_v2（Gemini gemini-embedding-2, 1536次元）へ切り替える。
-- 本番の unified_search_v2 はリポジトリ内のどの定義（v15 等）とも一致しない（classification1..3 を返す手修正版）ため、
-- 本番の現行定義を pg_get_functiondef で取得し、ベクトル比較の列名だけを置換して再作成する。それ以外は一切変更しない。
-- 前提が崩れている（関数が1つでない／置換対象が無い／既に embedding_v2）場合は例外で中止し、何も変更しない。
-- 削除系文（DROP FUNCTION / DROP COLUMN / DROP TABLE / DELETE 等）は含めない。

-- 1. embedding_v2 に ivfflat (vector_cosine_ops, lists=100) インデックスを作成
-- 既定の maintenance_work_mem (32MB) では ivfflat 作成に不足する（61MB 必要）ため、このトランザクション内だけ引き上げる
SET LOCAL maintenance_work_mem = '128MB';
CREATE INDEX IF NOT EXISTS idx_10_ix_embedding_v2
ON "10_ix_search_index"
USING ivfflat (embedding_v2 vector_cosine_ops)
WITH (lists = 100);

-- 2. 本番の unified_search_v2 を、ベクトル比較の列だけ embedding_v2 に置換して再作成
DO $$
DECLARE
    fn_count  int;
    def       text;
    new_def   text;
    hits      int;
BEGIN
    SELECT count(*) INTO fn_count
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.proname = 'unified_search_v2' AND n.nspname = 'public';
    IF fn_count <> 1 THEN
        RAISE EXCEPTION 'public.unified_search_v2 が % 個あります（1個であることが前提）', fn_count;
    END IF;

    SELECT pg_get_functiondef(p.oid) INTO def
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.proname = 'unified_search_v2' AND n.nspname = 'public';

    IF position('embedding_v2' IN def) > 0 THEN
        RAISE EXCEPTION 'unified_search_v2 は既に embedding_v2 を参照しています';
    END IF;

    hits := (length(def) - length(replace(def, '.embedding <=> query_embedding', ''))) / length('.embedding <=> query_embedding');
    IF hits = 0 THEN
        RAISE EXCEPTION 'unified_search_v2 に置換対象 ".embedding <=> query_embedding" がありません';
    END IF;

    new_def := replace(def, '.embedding <=> query_embedding', '.embedding_v2 <=> query_embedding');
    EXECUTE new_def;
    RAISE NOTICE 'unified_search_v2: % 箇所を embedding_v2 に置換して再作成しました', hits;
END $$;
