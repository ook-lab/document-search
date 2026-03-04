-- ============================================================
-- 02_gcal_01_raw にカラム追加
-- Google Calendar API の全フィールドを根こそぎ取り込む
-- ============================================================

ALTER TABLE public."02_gcal_01_raw"

  -- 会議・通話
  ADD COLUMN IF NOT EXISTS hangout_link           TEXT,      -- Google Meet 直接 URL
  ADD COLUMN IF NOT EXISTS conference_data        JSONB,     -- {conferenceSolution, entryPoints, conferenceId}

  -- 添付ファイル
  ADD COLUMN IF NOT EXISTS attachments            JSONB,     -- [{fileUrl, title, mimeType, fileId, iconLink}]

  -- 空き時間
  ADD COLUMN IF NOT EXISTS transparency           TEXT,      -- 'opaque'(予定あり) | 'transparent'(空き扱い)

  -- 通知
  ADD COLUMN IF NOT EXISTS reminders              JSONB,     -- {useDefault, overrides: [{method, minutes}]}

  -- 繰り返し例外
  ADD COLUMN IF NOT EXISTS original_start_time   JSONB,     -- {dateTime/date, timeZone} 繰り返しイベントの元の開始時刻

  -- データ完全性フラグ
  ADD COLUMN IF NOT EXISTS attendees_omitted      BOOLEAN,   -- 参加者リストが切り捨てられているか
  ADD COLUMN IF NOT EXISTS end_time_unspecified   BOOLEAN,   -- 終了時刻未指定（タスク型イベント等）

  -- ゲスト権限
  ADD COLUMN IF NOT EXISTS guests_can_invite_others    BOOLEAN,
  ADD COLUMN IF NOT EXISTS guests_can_modify           BOOLEAN,
  ADD COLUMN IF NOT EXISTS guests_can_see_other_guests BOOLEAN,
  ADD COLUMN IF NOT EXISTS anyone_can_add_self         BOOLEAN,

  -- その他フラグ
  ADD COLUMN IF NOT EXISTS private_copy           BOOLEAN,
  ADD COLUMN IF NOT EXISTS locked                 BOOLEAN,

  -- イベント種別（focusTime / outOfOffice / workingLocation / birthday / default）
  ADD COLUMN IF NOT EXISTS event_type             TEXT,
  ADD COLUMN IF NOT EXISTS focus_time_properties  JSONB,
  ADD COLUMN IF NOT EXISTS out_of_office_properties  JSONB,
  ADD COLUMN IF NOT EXISTS working_location_properties JSONB,

  -- カスタムプロパティ
  ADD COLUMN IF NOT EXISTS extended_properties    JSONB,     -- {private: {}, shared: {}}

  -- 発信元
  ADD COLUMN IF NOT EXISTS source_title           TEXT;      -- イベント発信元タイトル（payload.source.title）

DO $$
BEGIN
  RAISE NOTICE '02_gcal_01_raw フィールド拡張完了';
END $$;
