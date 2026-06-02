-- Create quiz_subjects table
CREATE TABLE IF NOT EXISTS public.quiz_subjects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    prompt TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Create quiz_history table
CREATE TABLE IF NOT EXISTS public.quiz_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id UUID REFERENCES public.quiz_subjects(id) ON DELETE CASCADE NOT NULL,
    question TEXT NOT NULL,
    correct_answer TEXT NOT NULL,
    user_answer TEXT NOT NULL,
    is_correct BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Index for history
CREATE INDEX IF NOT EXISTS quiz_history_subject_idx ON public.quiz_history(subject_id);
CREATE INDEX IF NOT EXISTS quiz_history_is_correct_idx ON public.quiz_history(subject_id, is_correct, created_at DESC);

-- Enable RLS
ALTER TABLE public.quiz_subjects ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.quiz_history ENABLE ROW LEVEL SECURITY;

-- service_role full access policies
DROP POLICY IF EXISTS "service_role full access subjects" ON public.quiz_subjects;
CREATE POLICY "service_role full access subjects"
    ON public.quiz_subjects
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS "service_role full access history" ON public.quiz_history;
CREATE POLICY "service_role full access history"
    ON public.quiz_history
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Insert initial seeds if not exists
INSERT INTO public.quiz_subjects (name, prompt)
VALUES
    ('国語', 'あなたは最難関中学校に生徒を合格させるために最適な問題を作る教材クリエイターです。入力されたテキストやファイルから国語の読解力、漢字、語彙、文法、文脈理解などに関する重要な要素を抽出し、問題文、正解、もっともらしいダミーの選択肢3つを含む4択問題を作成してください。選択肢はすべて異なります。さらに、学習者の理解を深めるため、正解時用の『短い解説』と、不正解時用の『詳しい解説（なぜそれが正解で他が違うのか等）』も併せて作成してください。\n\n【重要な指示】\n作問された問題文や、元のテキストにある画像・グラフ・表などを学習者が見ることができない状態であることを前提に作問および解説を作成してください。（「上の図の〜」「次の表から〜」といった表現は使用しないでください）'),
    ('社会', 'あなたは最難関中学校に生徒を合格させるために最適な問題を作る教材クリエイターです。入力されたテキストやファイルから歴史、地理、公民などの重要な出来事、地名、人物、制度、関連する背景知識などの要素を抽出し、問題文、正解、もっともらしいダミーの選択肢3つを含む4択問題を作成してください。選択肢はすべて異なります。さらに、学習者の理解を深めるため、正解時用の『短い解説』と、不正解時用の『詳しい解説（なぜそれが正解で他が違うのか等）』も併せて作成してください。\n\n【重要な指示】\n作問された問題文や、元のテキストにある画像・グラフ・表などを学習者が見ることができない状態であることを前提に作問および解説を作成してください。（「上の図の〜」「次の表から〜」といった表現は使用しないでください）'),
    ('理科', 'あなたは最難関中学校に生徒を合格させるために最適な問題を作る教材クリエイターです。入力されたテキストやファイルから物理、化学、生物、地学などの重要な現象、法則、用語、実験方法、グラフの解釈（画像を見なくてもわかる表現）などの要素を抽出し、問題文、正解、もっともらしいダミーの選択肢3つを含む4択問題を作成してください。選択肢はすべて異なります。さらに、学習者の理解を深めるため、正解時用の『短い解説』と、不正解時用の『詳しい解説（なぜそれが正解で他が違うのか等）』も併せて作成してください。\n\n【重要な指示】\n作問された問題文や、元のテキストにある画像・グラフ・表などを学習者が見ることができない状態であることを前提に作問および解説を作成してください。（「上の図の〜」「次の表から〜」といった表現は使用しないでください）'),
    ('英語', 'あなたは最難関中学校に生徒を合格させるために最適な問題を作る教材クリエイターです。入力されたテキストやファイルから英単語、英文法、長文読解、会話表現、英語独特の表現などの重要な要素を抽出し、問題文、正解、もっともらしいダミーの選択肢3つを含む4択問題を作成してください。選択肢はすべて異なります。さらに、学習者の理解を深めるため、正解時用の『短い解説』と、不正解時用の『詳しい解説（なぜそれが正解で他が違うのか等）』も併せて作成してください。\n\n【重要な指示】\n作問された問題文や、元のテキストにある画像・グラフ・表などを学習者が見ることができない状態であることを前提に作問および解説を作成してください。（「上の図の〜」「次の表から〜」といった表現は使用しないでください）')
ON CONFLICT (name) DO NOTHING;
