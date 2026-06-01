-- math_problems テーブル作成
CREATE TABLE IF NOT EXISTS public.math_problems (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    display_id TEXT UNIQUE NOT NULL,
    school_name TEXT NOT NULL,
    year INTEGER,
    category TEXT NOT NULL,
    sub_category TEXT,
    difficulty INTEGER CHECK (difficulty BETWEEN 1 AND 5),
    problem_markdown TEXT NOT NULL,
    explanation_markdown TEXT NOT NULL,
    strategy_summary TEXT,
    owner_id UUID DEFAULT 'd1b18b1c-a4dc-4b2e-97af-5153a85e685c'::uuid,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- インデックス作成
CREATE INDEX IF NOT EXISTS idx_math_problems_category ON public.math_problems(category);
CREATE INDEX IF NOT EXISTS idx_math_problems_difficulty ON public.math_problems(difficulty);
CREATE INDEX IF NOT EXISTS idx_math_problems_display_id ON public.math_problems(display_id);

-- RLS (Row Level Security) の設定
ALTER TABLE public.math_problems ENABLE ROW LEVEL SECURITY;

-- 簡易的なポリシー (owner_id に基づくアクセス制限、または認証済みユーザーならすべて許可)
CREATE POLICY "Allow all operations for owner" ON public.math_problems
    FOR ALL USING (auth.role() = 'authenticated') WITH CHECK (auth.role() = 'authenticated');
