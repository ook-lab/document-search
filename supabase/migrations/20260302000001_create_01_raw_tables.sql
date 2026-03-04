-- ============================================================
-- 01_raw 層テーブル作成
-- 取り込みデータをソースに忠実な形で保存する層
-- 処理では読むだけ・書き足さない
-- ============================================================

-- 共通先頭カラム（全テーブル共通）:
--   id UUID, person TEXT, source TEXT, category TEXT

-- ============================================================
-- 01_gmail_01_raw
-- ============================================================
CREATE TABLE IF NOT EXISTS public."01_gmail_01_raw" (
  id                 UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  person             TEXT,
  source             TEXT,
  category           TEXT,

  message_id         TEXT UNIQUE NOT NULL,
  thread_id          TEXT,
  history_id         TEXT,
  internal_date      BIGINT,
  sent_at            TIMESTAMPTZ,
  size_estimate      INT,
  label_ids          TEXT[],
  snippet            TEXT,

  header_from        TEXT,
  from_name          TEXT,
  from_email         TEXT,
  header_to          TEXT,
  header_cc          TEXT,
  header_subject     TEXT,
  header_date        TEXT,
  header_message_id  TEXT,
  header_in_reply_to TEXT,
  header_references  TEXT,

  body_plain         TEXT,
  body_html          TEXT,
  mime_type          TEXT,

  source_url         TEXT,
  attachments        JSONB,

  ingested_at        TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 02_gcal_01_raw
-- ============================================================
CREATE TABLE IF NOT EXISTS public."02_gcal_01_raw" (
  id                 UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  person             TEXT,
  source             TEXT,
  category           TEXT,

  event_id           TEXT NOT NULL,
  calendar_id        TEXT NOT NULL,
  UNIQUE (event_id, calendar_id),

  i_cal_uid          TEXT,
  summary            TEXT,
  description        TEXT,
  location           TEXT,
  status             TEXT,
  visibility         TEXT,

  start_raw          JSONB,
  end_raw            JSONB,
  is_all_day         BOOLEAN,
  start_at           TIMESTAMPTZ,
  end_at             TIMESTAMPTZ,

  created_at         TIMESTAMPTZ,
  updated_at         TIMESTAMPTZ,

  creator_email      TEXT,
  creator_name       TEXT,
  organizer_email    TEXT,
  organizer_name     TEXT,

  attendees          JSONB,
  recurrence         TEXT[],
  recurring_event_id TEXT,

  source_url         TEXT,
  color_id           TEXT,
  sequence           INT,
  etag               TEXT,

  ingested_at        TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 03_ema_classroom_01_raw
-- ============================================================
CREATE TABLE IF NOT EXISTS public."03_ema_classroom_01_raw" (
  id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  person          TEXT,
  source          TEXT,
  category        TEXT,

  post_id         TEXT,
  post_type       TEXT,
  course_id       TEXT,
  course_name     TEXT,
  topic_id        TEXT,
  topic_name      TEXT,
  title           TEXT,
  description     TEXT,
  state           TEXT,
  due_date        DATE,
  due_time        TEXT,
  creator_email   TEXT,
  creator_name    TEXT,
  source_url      TEXT,
  created_at      TIMESTAMPTZ,
  updated_at      TIMESTAMPTZ,

  file_name       TEXT,
  file_url        TEXT,

  ingested_at     TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 04_ikuya_classroom_01_raw
-- ============================================================
CREATE TABLE IF NOT EXISTS public."04_ikuya_classroom_01_raw" (
  id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  person          TEXT,
  source          TEXT,
  category        TEXT,

  post_id         TEXT,
  post_type       TEXT,
  course_id       TEXT,
  course_name     TEXT,
  topic_id        TEXT,
  topic_name      TEXT,
  title           TEXT,
  description     TEXT,
  state           TEXT,
  due_date        DATE,
  due_time        TEXT,
  creator_email   TEXT,
  creator_name    TEXT,
  source_url      TEXT,
  created_at      TIMESTAMPTZ,
  updated_at      TIMESTAMPTZ,

  file_name       TEXT,
  file_url        TEXT,

  ingested_at     TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 05_ikuya_waseaca_01_raw
-- ============================================================
CREATE TABLE IF NOT EXISTS public."05_ikuya_waseaca_01_raw" (
  id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  person          TEXT,
  source          TEXT,
  category        TEXT,

  post_id         TEXT,
  post_type       TEXT,
  course_id       TEXT,
  course_name     TEXT,
  topic_id        TEXT,
  topic_name      TEXT,
  title           TEXT,
  description     TEXT,
  state           TEXT,
  due_date        DATE,
  due_time        TEXT,
  creator_email   TEXT,
  creator_name    TEXT,
  source_url      TEXT,
  created_at      TIMESTAMPTZ,
  updated_at      TIMESTAMPTZ,

  file_name       TEXT,
  file_url        TEXT,

  ingested_at     TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 08_file_only_01_raw
-- ============================================================
CREATE TABLE IF NOT EXISTS public."08_file_only_01_raw" (
  id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  person          TEXT,
  source          TEXT,
  category        TEXT,

  file_name       TEXT,
  file_url        TEXT,
  file_id         TEXT,
  mime_type       TEXT,
  file_size       BIGINT,
  original_path   TEXT,

  ingested_at     TIMESTAMPTZ DEFAULT now()
);

DO $$
BEGIN
  RAISE NOTICE '01_raw 層テーブル作成完了';
END $$;
