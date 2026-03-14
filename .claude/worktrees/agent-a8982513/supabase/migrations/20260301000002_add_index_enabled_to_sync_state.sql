-- calendar_sync_state に検索インデックス有効フラグを追加
ALTER TABLE public.calendar_sync_state
    ADD COLUMN IF NOT EXISTS index_enabled BOOLEAN NOT NULL DEFAULT false;
