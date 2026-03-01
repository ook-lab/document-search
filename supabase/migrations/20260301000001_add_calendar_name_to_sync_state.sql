-- calendar_sync_state にカレンダー名カラムを追加
ALTER TABLE public.calendar_sync_state
    ADD COLUMN IF NOT EXISTS calendar_name TEXT;
