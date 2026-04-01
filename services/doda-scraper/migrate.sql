-- doda_jobs テーブル拡張マイグレーション
-- Supabase SQL Editor で実行してください

-- ① raw_text カラム（未存在の場合）
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS raw_text text;

-- ② 給与の数値化・分解
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS salary_min          integer;
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS salary_max          integer;
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS salary_system       text;    -- monthly / annual
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS base_salary_monthly integer; -- 固定残業を除いた基本給（月額）
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS fixed_overtime_pay  integer; -- 固定残業代（月額）
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS fixed_overtime_hours integer; -- 固定残業時間数
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS has_incentive       boolean;

-- ③ 勤務時間・休日・働き方
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS annual_holidays     integer; -- 年間休日日数
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS avg_overtime_hours  integer; -- 月平均残業時間
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS is_remote_allowed   boolean;
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS remote_type         text;    -- full / partial / none
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS is_flex_time        boolean;
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS probation_months    integer;

-- ④ 応募要件・スキル
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS is_managerial          boolean;
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS is_inexperienced_ok    boolean;
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS management_exp_required boolean;
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS english_level           text;    -- none / daily / business / native
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS required_exp_years      integer;

-- ⑤ 企業属性
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS listing_status          text;    -- unlisted / listed_prime / listed_growth / ipo_preparing
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS company_employee_count  integer;
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS company_average_age     numeric(4,1);
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS foreign_employee_ratio  integer; -- %

-- ⑥ タグ（スキル・福利厚生）
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS skill_tags   text[];
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS benefit_tags text[];

-- ⑦ 拡張メタデータ（企業固有の数値・情報）
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS metadata jsonb DEFAULT '{}';

-- ⑧ 構造化処理の完了フラグ
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS structured_at timestamptz;

-- ⑨ 実質年収平均の生成カラム（salary_min/max 両方ある場合のみ算出）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'doda_jobs' AND column_name = 'salary_average'
    ) THEN
        ALTER TABLE doda_jobs ADD COLUMN salary_average integer
            GENERATED ALWAYS AS (
                CASE
                    WHEN salary_min IS NOT NULL AND salary_max IS NOT NULL
                    THEN (salary_min + salary_max) / 2
                    ELSE COALESCE(salary_min, salary_max)
                END
            ) STORED;
    END IF;
END $$;

-- ⑩ 実質基本給の生成カラム（固定残業代を除いた月額ベースの年収換算）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'doda_jobs' AND column_name = 'real_base_annual'
    ) THEN
        ALTER TABLE doda_jobs ADD COLUMN real_base_annual integer
            GENERATED ALWAYS AS (
                CASE
                    WHEN base_salary_monthly IS NOT NULL
                    THEN base_salary_monthly * 12
                    WHEN salary_min IS NOT NULL AND fixed_overtime_pay IS NOT NULL
                    THEN salary_min - (fixed_overtime_pay * 12)
                    ELSE NULL
                END
            ) STORED;
    END IF;
END $$;

-- ===== インデックス =====

-- 給与検索
CREATE INDEX IF NOT EXISTS doda_jobs_salary_min_idx     ON doda_jobs (salary_min);
CREATE INDEX IF NOT EXISTS doda_jobs_salary_max_idx     ON doda_jobs (salary_max);
CREATE INDEX IF NOT EXISTS doda_jobs_salary_average_idx ON doda_jobs (salary_average);
CREATE INDEX IF NOT EXISTS doda_jobs_real_base_idx      ON doda_jobs (real_base_annual);

-- 休日・残業
CREATE INDEX IF NOT EXISTS doda_jobs_holidays_idx       ON doda_jobs (annual_holidays);
CREATE INDEX IF NOT EXISTS doda_jobs_overtime_idx       ON doda_jobs (avg_overtime_hours);

-- フラグ系
CREATE INDEX IF NOT EXISTS doda_jobs_remote_idx         ON doda_jobs (is_remote_allowed);
CREATE INDEX IF NOT EXISTS doda_jobs_managerial_idx     ON doda_jobs (is_managerial);
CREATE INDEX IF NOT EXISTS doda_jobs_english_idx        ON doda_jobs (english_level);
CREATE INDEX IF NOT EXISTS doda_jobs_listing_idx        ON doda_jobs (listing_status);

-- タグ配列（GIN: 配列内要素の高速検索）
CREATE INDEX IF NOT EXISTS doda_jobs_skill_tags_idx     ON doda_jobs USING GIN (skill_tags);
CREATE INDEX IF NOT EXISTS doda_jobs_benefit_tags_idx   ON doda_jobs USING GIN (benefit_tags);

-- メタデータ（GIN: JSONB内キーの高速検索）
CREATE INDEX IF NOT EXISTS doda_jobs_metadata_idx       ON doda_jobs USING GIN (metadata);

-- 未構造化レコードを素早く取得するためのインデックス
CREATE INDEX IF NOT EXISTS doda_jobs_structured_at_idx  ON doda_jobs (structured_at) WHERE structured_at IS NULL;

-- ===== 企業マスタテーブル =====
CREATE TABLE IF NOT EXISTS doda_companies (
    id              uuid        DEFAULT gen_random_uuid() PRIMARY KEY,
    name            text        NOT NULL UNIQUE,
    listing_status  text,       -- unlisted / listed_prime / listed_growth / ipo_preparing
    employee_count  integer,
    established_year integer,
    capital         bigint,
    industry        text,
    website_url     text,
    metadata        jsonb       DEFAULT '{}',
    created_at      timestamptz DEFAULT now(),
    updated_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS doda_companies_name_idx ON doda_companies (name);

-- jobs テーブルに企業ID FK
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS company_id uuid REFERENCES doda_companies(id);
CREATE INDEX IF NOT EXISTS doda_jobs_company_id_idx ON doda_jobs (company_id);

-- ===== エージェント/経路マスタ =====
CREATE TABLE IF NOT EXISTS doda_agents (
    id          uuid    DEFAULT gen_random_uuid() PRIMARY KEY,
    name        text    NOT NULL UNIQUE,
    agent_type  text,   -- partner_agent / career_advisor / doda_direct
    created_at  timestamptz DEFAULT now()
);

ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS agent_id uuid REFERENCES doda_agents(id);
CREATE INDEX IF NOT EXISTS doda_jobs_agent_id_idx ON doda_jobs (agent_id);

-- ===== 全文検索用ビュー（pg_bigm が有効な場合はインデックス追加を推奨） =====
-- pg_bigm 拡張が有効な場合: CREATE INDEX doda_jobs_fts_idx ON doda_jobs USING GIN (raw_text gin_bigm_ops);
-- 標準 tsvector での代替:
ALTER TABLE doda_jobs ADD COLUMN IF NOT EXISTS fts_vector tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple'::regconfig,
            coalesce(job_title, '') || ' ' ||
            coalesce(company_name, '') || ' ' ||
            coalesce(description, '') || ' ' ||
            coalesce(requirements, '')
        )
    ) STORED;

CREATE INDEX IF NOT EXISTS doda_jobs_fts_idx ON doda_jobs USING GIN (fts_vector);
