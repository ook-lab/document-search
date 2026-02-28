-- calendar_presets: カレンダーごとのプリセット（時間割・場所略称など）
CREATE TABLE IF NOT EXISTS public.calendar_presets (
    calendar_id  TEXT        PRIMARY KEY,
    preset_text  TEXT        NOT NULL DEFAULT '',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- updated_at 自動更新
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_calendar_presets_updated_at
    BEFORE UPDATE ON public.calendar_presets
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- service role のみ読み書き（RLS 無効でサービスロールに限定）
ALTER TABLE public.calendar_presets ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_all" ON public.calendar_presets
    USING (true)
    WITH CHECK (true);
