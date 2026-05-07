-- 読み込みコンテキスト（バックグラウンド情報）をエディタで編集し、AI 向け JSON/MD とともに保存

CREATE TABLE IF NOT EXISTS public.reading_context_profiles (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id          UUID        NOT NULL,
    person_name       TEXT        NOT NULL DEFAULT '',
    title             TEXT        NOT NULL DEFAULT '',
    editor_document   JSONB       NOT NULL DEFAULT '{"version":1,"tables":[],"text_sections":[]}'::jsonb,
    ai_payload_json   JSONB,
    ai_payload_md     TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS reading_context_profiles_owner_idx
    ON public.reading_context_profiles (owner_id DESC);

CREATE INDEX IF NOT EXISTS reading_context_profiles_owner_updated_idx
    ON public.reading_context_profiles (owner_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS reading_context_profiles_owner_person_idx
    ON public.reading_context_profiles (owner_id, person_name, updated_at DESC);

COMMENT ON TABLE public.reading_context_profiles IS '読み込みコンテキスト: エディタ状態 + AI 向け JSON/Markdown';
COMMENT ON COLUMN public.reading_context_profiles.person_name IS 'このコンテキストが紐づく人の名前';
COMMENT ON COLUMN public.reading_context_profiles.editor_document IS '{"version":1,"tables":[pair|grid],"text_sections":[{title,body}]}';
COMMENT ON COLUMN public.reading_context_profiles.ai_payload_json IS '保存時にサーバが生成したフラットな構造（AI 投入用）';
COMMENT ON COLUMN public.reading_context_profiles.ai_payload_md IS '保存時にサーバが生成した Markdown（AI 投入用）';

ALTER TABLE public.reading_context_profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role full access" ON public.reading_context_profiles;
CREATE POLICY "service_role full access"
    ON public.reading_context_profiles
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS "authenticated select own" ON public.reading_context_profiles;
CREATE POLICY "authenticated select own"
    ON public.reading_context_profiles
    FOR SELECT
    TO authenticated
    USING (owner_id = auth.uid());

DROP POLICY IF EXISTS "authenticated insert own" ON public.reading_context_profiles;
CREATE POLICY "authenticated insert own"
    ON public.reading_context_profiles
    FOR INSERT
    TO authenticated
    WITH CHECK (owner_id = auth.uid());

DROP POLICY IF EXISTS "authenticated update own" ON public.reading_context_profiles;
CREATE POLICY "authenticated update own"
    ON public.reading_context_profiles
    FOR UPDATE
    TO authenticated
    USING (owner_id = auth.uid())
    WITH CHECK (owner_id = auth.uid());

DROP POLICY IF EXISTS "authenticated delete own" ON public.reading_context_profiles;
CREATE POLICY "authenticated delete own"
    ON public.reading_context_profiles
    FOR DELETE
    TO authenticated
    USING (owner_id = auth.uid());
