-- 20260928000005_course_filter_by_raw_table.sql
-- 絞り込み3段目の照合を「classification2 と classification3 のどちらかに一致」から、
-- 「クラスルーム(03/04)の行は classification2、それ以外の行は classification3」と取り込み元で決まる形に改める。
-- 本番定義を pg_get_functiondef で取得し、000004 で入れた条件だけを置換する。前提が崩れていれば例外で中止する。削除系文は含めない。

DO $$
DECLARE
    fn_count int;
    def      text;
    new_def  text;
    old_cond text := '(ud.classification3 = ANY(filter_category) OR ud.classification2 = ANY(filter_category))';
    new_cond text := '(CASE WHEN ud.raw_table IN (''03_ema_classroom_01_raw'', ''04_ikuya_classroom_01_raw'') THEN ud.classification2 ELSE ud.classification3 END) = ANY(filter_category)';
    hits     int;
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

    hits := (length(def) - length(replace(def, old_cond, ''))) / length(old_cond);
    IF hits = 0 THEN
        RAISE EXCEPTION 'unified_search_v2 に置換対象 "%" がありません', old_cond;
    END IF;

    new_def := replace(def, old_cond, new_cond);
    EXECUTE new_def;
    RAISE NOTICE 'unified_search_v2: % 箇所の絞り込み条件を取り込み元別の照合に置換しました', hits;
END $$;
