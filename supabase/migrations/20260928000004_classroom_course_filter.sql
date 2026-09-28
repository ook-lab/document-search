-- 20260928000004_classroom_course_filter.sql
-- 検索の絞り込み3段目を「クラスルーム=コース名(classification2)、それ以外=classification3」にする。
-- 1. クラスルーム行の classification3 に入っていた投稿種別（announcements 等。post_type 列と同内容）を外す。
-- 2. クラスルーム行の classification2 を raw.course_name の実値に揃える（raw に値が無い行は変更しない）。
-- 3. 以後「classification2 と classification3 の両方に値がある行は無い」を前提とするため、違反があれば中止する。
-- 4. 本番 unified_search_v2 の filter_category 条件を classification2 にも一致させる（本番定義を pg_get_functiondef で取得して置換）。
-- 前提が崩れている場合は例外で全体を中止する。削除系文は含めない。

UPDATE public."09_unified_documents"
   SET classification3 = NULL
 WHERE raw_table IN ('03_ema_classroom_01_raw', '04_ikuya_classroom_01_raw')
   AND classification3 IS NOT NULL;

UPDATE public."09_unified_documents" ud
   SET classification2 = r.course_name
  FROM public."03_ema_classroom_01_raw" r
 WHERE ud.raw_table = '03_ema_classroom_01_raw'
   AND ud.raw_id::text = r.id::text
   AND r.course_name IS NOT NULL
   AND ud.classification2 IS DISTINCT FROM r.course_name;

UPDATE public."09_unified_documents" ud
   SET classification2 = r.course_name
  FROM public."04_ikuya_classroom_01_raw" r
 WHERE ud.raw_table = '04_ikuya_classroom_01_raw'
   AND ud.raw_id::text = r.id::text
   AND r.course_name IS NOT NULL
   AND ud.classification2 IS DISTINCT FROM r.course_name;

DO $$
DECLARE
    both_count int;
    fn_count   int;
    def        text;
    new_def    text;
    old_cond   text := 'ud.classification3 = ANY(filter_category)';
    new_cond   text := '(ud.classification3 = ANY(filter_category) OR ud.classification2 = ANY(filter_category))';
    hits       int;
BEGIN
    SELECT count(*) INTO both_count
    FROM public."09_unified_documents"
    WHERE classification2 IS NOT NULL AND classification3 IS NOT NULL;
    IF both_count > 0 THEN
        RAISE EXCEPTION 'classification2 と classification3 の両方に値がある行が % 件あります', both_count;
    END IF;

    SELECT count(*) INTO fn_count
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.proname = 'unified_search_v2' AND n.nspname = 'public';
    IF fn_count <> 1 THEN
        RAISE EXCEPTION 'public.unified_search_v2 が % 個あります（1個であることが前提）', fn_count;
    END IF;

    SELECT pg_get_functiondef(p.oid) INTO def
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.proname = 'unified_search_v2' AND n.nspname = 'public';

    IF position('ud.classification2 = ANY(filter_category)' IN def) > 0 THEN
        RAISE EXCEPTION 'unified_search_v2 は既に classification2 で絞り込んでいます';
    END IF;

    hits := (length(def) - length(replace(def, old_cond, ''))) / length(old_cond);
    IF hits = 0 THEN
        RAISE EXCEPTION 'unified_search_v2 に置換対象 "%" がありません', old_cond;
    END IF;

    new_def := replace(def, old_cond, new_cond);
    EXECUTE new_def;
    RAISE NOTICE 'unified_search_v2: % 箇所の絞り込み条件に classification2 を追加しました', hits;
END $$;
