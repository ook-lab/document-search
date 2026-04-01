CREATE TABLE IF NOT EXISTS doda_jobs (
    id              uuid        DEFAULT gen_random_uuid() PRIMARY KEY,
    category        text        NOT NULL,
    category_label  text        NOT NULL,
    url             text        NOT NULL UNIQUE,
    company_name    text,
    job_title       text,
    salary          text,
    location        text,
    employment_type text,
    industry        text,
    job_type        text,
    description     text,
    requirements    text,
    working_hours   text,
    holidays        text,
    features        text,
    raw_data        jsonb,
    fetched_at      timestamptz DEFAULT now(),
    created_at      timestamptz DEFAULT now()
);

-- 取得日時でのソート用インデックス
CREATE INDEX IF NOT EXISTS doda_jobs_fetched_at_idx ON doda_jobs (fetched_at DESC);
CREATE INDEX IF NOT EXISTS doda_jobs_category_idx   ON doda_jobs (category);
