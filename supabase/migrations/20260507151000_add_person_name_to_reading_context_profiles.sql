ALTER TABLE public.reading_context_profiles
ADD COLUMN IF NOT EXISTS person_name TEXT;

UPDATE public.reading_context_profiles
SET person_name = '未設定'
WHERE person_name IS NULL OR btrim(person_name) = '';

ALTER TABLE public.reading_context_profiles
ALTER COLUMN person_name SET NOT NULL;

CREATE INDEX IF NOT EXISTS reading_context_profiles_owner_person_idx
ON public.reading_context_profiles (owner_id, person_name, updated_at DESC);
