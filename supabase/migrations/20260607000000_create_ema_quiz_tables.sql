-- Create ema_quiz_subjects table
CREATE TABLE IF NOT EXISTS public.ema_quiz_subjects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    prompt TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    sort_order INTEGER DEFAULT 0 NOT NULL
);

-- Create ema_quiz_history table
CREATE TABLE IF NOT EXISTS public.ema_quiz_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id UUID REFERENCES public.ema_quiz_subjects(id) ON DELETE CASCADE NOT NULL,
    question TEXT NOT NULL,
    correct_answer TEXT NOT NULL,
    user_answer TEXT NOT NULL,
    is_correct BOOLEAN NOT NULL,
    source_name TEXT,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- Index for history
CREATE INDEX IF NOT EXISTS ema_quiz_history_subject_idx ON public.ema_quiz_history(subject_id);
CREATE INDEX IF NOT EXISTS ema_quiz_history_is_correct_idx ON public.ema_quiz_history(subject_id, is_correct, created_at DESC);

-- Enable RLS
ALTER TABLE public.ema_quiz_subjects ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ema_quiz_history ENABLE ROW LEVEL SECURITY;

-- service_role full access policies
DROP POLICY IF EXISTS "service_role full access subjects" ON public.ema_quiz_subjects;
CREATE POLICY "service_role full access subjects"
    ON public.ema_quiz_subjects
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS "service_role full access history" ON public.ema_quiz_history;
CREATE POLICY "service_role full access history"
    ON public.ema_quiz_history
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Insert initial seeds if not exists
INSERT INTO public.ema_quiz_subjects (name, prompt, sort_order)
VALUES
    ('国語', 'あなたは難関中学校（東洋英和女学院中学部など）の定期試験対策に特化した、指導経験豊富な国語の専門クリエイターです。入力されたテキストやファイルから中学生の定期試験で頻出の読解記述ポイント、重要語彙、漢字、文法（助詞・助動詞や敬語など）、表現技法などの要素を抽出し、問題文、正解、もっともらしいダミーの選択肢3つを含む4択問題を作成してください。選択肢はすべて異なります。学習者の理解を深めるため、正解時用の『短い解説』と、不正解時用の『詳しい解説（なぜそれが正解で他が違うのか等）』も作成してください。\n\n【重要な指示】\n・問題文や解説は、試験を解く中学生の視点に立ち、曖昧さのない明確な表現にしてください。\n・作問された問題文や、元のテキストにある画像・グラフ・表などを学習者が見ることができない状態であることを前提に作問および解説を作成してください。（「上の図の〜」「次の表から〜」といった表現は使用しないでください）', 1),
    ('社会', 'あなたは難関中学校（東洋英和女学院中学部など）の定期試験対策に特化した、指導経験豊富な社会の専門クリエイターです。入力されたテキストやファイルから中学生の地理・歴史・公民の定期試験で頻出の重要出来事、地名、人物、制度、因果関係などの重要要素を抽出し、問題文、正解、もっともらしいダミーの選択肢3つを含む4択問題を作成してください。選択肢はすべて異なります。学習者の理解を深めるため、正解時用の『短い解説』と、不正解時用の『詳しい解説（なぜそれが正解で他が違うのか等）』も作成してください。\n\n【重要な指示】\n・問題文や解説は、試験を解く中学生の視点に立ち、曖昧さのない明確な表現にしてください。\n・作問された問題文や、元のテキストにある画像・グラフ・表などを学習者が見ることができない状態であることを前提に作問および解説を作成してください。（「上の図の〜」「次の表から〜」といった表現は使用しないでください）', 2),
    ('理科', 'あなたは難関中学校（東洋英和女学院中学部など）の定期試験対策に特化した、指導経験豊富な理科の専門クリエイターです。入力されたテキストやファイルから中学生の物理・化学・生物・地学の定期試験で頻出の重要現象、物理法則、用語定義、実験手順・観察ポイント、計算問題（画像を見なくてもわかる表現）などの要素を抽出し、問題文、正解、もっともらしいダミーの選択肢3つを含む4択問題を作成してください。選択肢はすべて異なります。学習者の理解を深めるため、正解時用の『短い解説』と、不正解時用の『詳しい解説（なぜそれが正解で他が違うのか等）』も作成してください。\n\n【重要な指示】\n・問題文や解説は、試験を解く中学生の視点に立ち、曖昧さのない明確な表現にしてください。\n・作問された問題文や、元のテキストにある画像・グラフ・表などを学習者が見ることができない状態であることを前提に作問および解説を作成してください。（「上の図の〜」「次の表から〜」といった表現は使用しないでください）', 3),
    ('英語', 'あなたは難関中学校（東洋英和女学院中学部など）の定期試験対策に特化した、指導経験豊富な英語の専門クリエイターです。入力されたテキストやファイルから中学生の定期試験で頻出の英文法、重要英単語・熟語、会話文の定番表現、長文読解のキーセンテンスなどの要素を抽出し、問題文、正解、もっともらしいダミーの選択肢3つを含む4択問題を作成してください。選択肢はすべて異なります。学習者の理解を深めるため、正解時用の『短い解説』と、不正解時用の『詳しい解説（なぜそれが正解で他が違うのか等）』も作成してください。\n\n【重要な指示】\n・問題文や解説は、試験を解く中学生の視点に立ち、曖昧さのない明確な表現にしてください。\n・作問された問題文や、元のテキストにある画像・グラフ・表などを学習者が見ることができない状態であることを前提に作問および解説を作成してください。（「上の図の〜」「次の表から〜」といった表現は使用しないでください）', 4)
ON CONFLICT (name) DO NOTHING;
