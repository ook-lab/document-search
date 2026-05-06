-- ============================================================
-- Classroom GAS 同期: anon キーでの PostgREST アクセス
-- ============================================================
-- 背景:
--   Supabase は service_role を「ブラウザ等のクライアント」からの送信を拒否することがある。
--   Google Apps Script の UrlFetchApp はその対象になりうる。
--   そのため GAS では publishable（anon）キーを使い、テーブル権限で必要な操作だけ許可する。
--
-- 注意:
--   anon に SELECT/INSERT を付与すると、プロジェクトの anon キーを知る相手が
--   同じ REST 操作を再現できる。キーはスクリプトプロパティに閉じ、流出時はキーローテーションすること。

GRANT USAGE ON SCHEMA public TO anon, authenticated;

GRANT SELECT, INSERT ON public."03_ema_classroom_01_raw" TO anon, authenticated;
GRANT SELECT, INSERT ON public."04_ikuya_classroom_01_raw" TO anon, authenticated;
GRANT SELECT, INSERT ON public.pipeline_meta TO anon, authenticated;
