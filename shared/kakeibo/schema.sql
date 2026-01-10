-- ========================================
-- 家計簿システム データベーススキーマ
-- ========================================

-- 必要な拡張機能を有効化
CREATE EXTENSION IF NOT EXISTS "btree_gist";  -- UUID の GIST インデックス用
CREATE EXTENSION IF NOT EXISTS "pg_trgm";     -- テキスト検索用

-- 1. シチュエーション（文脈）マスタ
CREATE TABLE IF NOT EXISTS money_situations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,  -- '日常', '家族旅行', '出張', '教育'
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 2. イベント期間定義
CREATE TABLE IF NOT EXISTS money_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,  -- '沖縄旅行 2024夏'
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    situation_id UUID REFERENCES money_situations(id),
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW(),

    -- 期間の重複チェック制約（同一シチュエーション内）
    CONSTRAINT no_overlapping_events EXCLUDE USING gist (
        daterange(start_date, end_date, '[]') WITH &&,
        situation_id WITH =
    )
);

-- 3. カテゴリ（費目）マスタ
CREATE TABLE IF NOT EXISTS money_categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,  -- '食費', '交通費', 'カード引落'
    is_expense BOOLEAN DEFAULT TRUE,  -- FALSE = 移動（集計対象外）
    parent_id UUID REFERENCES money_categories(id),  -- 階層構造用
    created_at TIMESTAMP DEFAULT NOW()
);

-- 4. 商品辞書（正規化ルール）
CREATE TABLE IF NOT EXISTS money_product_dictionary (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_keyword TEXT NOT NULL,  -- レシート上の表記（例: 'ｷﾞｭｳﾆｭｳ'）
    official_name TEXT NOT NULL,  -- 正式名称（例: '牛乳'）
    category_id UUID REFERENCES money_categories(id),
    tax_rate INTEGER DEFAULT 10,  -- 8 or 10 (%)
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),

    -- 複合ユニーク制約
    UNIQUE(raw_keyword, official_name)
);

-- インデックス（部分一致検索用）
CREATE INDEX IF NOT EXISTS idx_product_raw_keyword ON money_product_dictionary USING gin(raw_keyword gin_trgm_ops);

-- 5. エイリアステーブル（表記ゆれ吸収）
CREATE TABLE IF NOT EXISTS money_aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    input_word TEXT UNIQUE NOT NULL,  -- 間違った表記
    correct_word TEXT NOT NULL,  -- 正しい表記
    created_at TIMESTAMP DEFAULT NOW()
);

-- 6. トランザクション（明細）
CREATE TABLE IF NOT EXISTS money_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 基本情報
    transaction_date DATE NOT NULL,
    shop_name TEXT NOT NULL,

    -- 商品情報
    product_name TEXT NOT NULL,
    quantity INTEGER DEFAULT 1,
    unit_price INTEGER NOT NULL,  -- 単価（税込）
    total_amount INTEGER NOT NULL,  -- 合計（税込）

    -- 分類
    category_id UUID REFERENCES money_categories(id),
    situation_id UUID REFERENCES money_situations(id),

    -- メタデータ
    image_path TEXT,  -- Google Drive上のパス
    drive_file_id TEXT,
    notes TEXT,

    -- OCR処理情報
    ocr_model TEXT,  -- 使用したGeminiモデル（gemini-2.5-flash / gemini-2.5-flash-lite）
    source_folder TEXT,  -- ソースフォルダ（INBOX_EASY / INBOX_HARD）

    -- 処理情報
    is_verified BOOLEAN DEFAULT FALSE,  -- 手動確認済みフラグ
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_transactions_date ON money_transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_transactions_shop ON money_transactions USING gin(shop_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_transactions_situation ON money_transactions(situation_id);

-- 7. 画像処理ログ（重複防止）
CREATE TABLE IF NOT EXISTS money_image_processing_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    file_name TEXT UNIQUE NOT NULL,  -- '20241027_001.jpg'
    drive_file_id TEXT,

    status TEXT NOT NULL,  -- 'success', 'failed', 'manual_review', 'duplicate_receipt'
    error_message TEXT,

    transaction_ids UUID[],  -- 生成された明細のID配列

    ocr_model TEXT,  -- 使用したGeminiモデル名

    processed_at TIMESTAMP DEFAULT NOW(),
    retry_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_processing_log_status ON money_image_processing_log(status);

-- ========================================
-- 初期マスタデータ投入
-- ========================================

-- シチュエーション
INSERT INTO money_situations (name, description) VALUES
    ('日常', '通常の生活費'),
    ('家族旅行', '家族での旅行・レジャー'),
    ('出張', '仕事関連の出張'),
    ('教育', '子どもの教育関連費用')
ON CONFLICT (name) DO NOTHING;

-- カテゴリ
INSERT INTO money_categories (name, is_expense) VALUES
    ('食費', TRUE),
    ('日用品', TRUE),
    ('交通費', TRUE),
    ('医療費', TRUE),
    ('娯楽費', TRUE),
    ('カード引落', FALSE),  -- 集計対象外
    ('移動', FALSE)  -- 口座間移動など
ON CONFLICT (name) DO NOTHING;

-- ========================================
-- ビュー（集計用）
-- ========================================

-- 日次集計ビュー
CREATE OR REPLACE VIEW v_daily_summary AS
SELECT
    transaction_date,
    s.name AS situation,
    c.name AS category,
    COUNT(*) AS item_count,
    SUM(total_amount) AS total
FROM money_transactions t
LEFT JOIN money_situations s ON t.situation_id = s.id
LEFT JOIN money_categories c ON t.category_id = c.id
WHERE c.is_expense = TRUE  -- 集計対象のみ
GROUP BY transaction_date, s.name, c.name
ORDER BY transaction_date DESC;

-- 月次集計ビュー
CREATE OR REPLACE VIEW v_monthly_summary AS
SELECT
    DATE_TRUNC('month', transaction_date) AS month,
    s.name AS situation,
    c.name AS category,
    COUNT(*) AS item_count,
    SUM(total_amount) AS total
FROM money_transactions t
LEFT JOIN money_situations s ON t.situation_id = s.id
LEFT JOIN money_categories c ON t.category_id = c.id
WHERE c.is_expense = TRUE
GROUP BY month, s.name, c.name
ORDER BY month DESC;

-- ========================================
-- RLS (Row Level Security) 設定
-- ========================================
-- 必要に応じて有効化

-- ALTER TABLE money_transactions ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY "Allow authenticated users to view transactions"
--     ON money_transactions FOR SELECT
--     TO authenticated
--     USING (true);
