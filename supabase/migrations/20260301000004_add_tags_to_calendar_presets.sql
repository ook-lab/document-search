-- calendar_presets にタグ配列カラムを追加
ALTER TABLE public.calendar_presets
    ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';
