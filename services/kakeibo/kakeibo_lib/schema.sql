-- ========================================
-- 家計簿システム データベーススキーマ
-- ========================================
--
-- 注意: このファイルは参照用ドキュメントです。
-- 実際のテーブルはSupabaseで管理されています。
-- 新規環境構築時はSupabaseのテーブル作成機能を使用してください。
--
-- テーブル命名規則:
--   Rawdata_*     : 生データを格納するテーブル
--   MASTER_*      : マスタデータテーブル
--   99_lg_*       : ログテーブル
--
-- ========================================

-- 必要な拡張機能を有効化
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ========================================
-- 1. レシート店舗テーブル（親）
-- ========================================
CREATE TABLE IF NOT EXISTS "Rawdata_RECEIPT_shops" (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 取引情報
    transaction_date DATE NOT NULL,
    shop_name TEXT NOT NULL,

    -- 金額情報
    total_amount_check INTEGER,           -- レシート記載の合計
    subtotal_amount INTEGER,              -- 小計
    tax_8_amount INTEGER,                 -- 8%消費税額
    tax_10_amount INTEGER,                -- 10%消費税額
    tax_8_subtotal INTEGER,               -- 8%対象額（税抜）
    tax_10_subtotal INTEGER,              -- 10%対象額（税抜）

    -- ファイル情報
    image_path TEXT,                      -- Google Drive上のパス
    drive_file_id TEXT,                   -- Google DriveファイルID
    source_folder TEXT,                   -- ソースフォルダ（INBOX_EASY / INBOX_HARD）

    -- 処理情報
    ocr_model TEXT,                       -- 使用したGeminiモデル
    workspace TEXT DEFAULT 'household',   -- ワークスペース
    is_verified BOOLEAN DEFAULT FALSE,    -- 手動確認済みフラグ

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_receipt_shops_date ON "Rawdata_RECEIPT_shops"(transaction_date);
CREATE INDEX IF NOT EXISTS idx_receipt_shops_shop ON "Rawdata_RECEIPT_shops"(shop_name);

-- ========================================
-- 2. レシート明細テーブル（子）
-- ========================================
CREATE TABLE IF NOT EXISTS "Rawdata_RECEIPT_items" (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 親レシートへの参照
    receipt_id UUID NOT NULL REFERENCES "Rawdata_RECEIPT_shops"(id) ON DELETE CASCADE,

    -- 行情報
    line_number INTEGER NOT NULL,
    line_type TEXT DEFAULT 'ITEM',        -- ITEM, DISCOUNT, SUBTOTAL等
    ocr_raw_text TEXT,                    -- OCR生テキスト

    -- 商品情報（OCRデータ）
    product_name TEXT NOT NULL,
    item_name TEXT,                       -- product_nameの別名
    quantity INTEGER DEFAULT 1,
    unit_price INTEGER,
    displayed_amount INTEGER,             -- レシート記載の表示金額
    discount_text TEXT,                   -- 値引き表記

    -- 税金計算情報
    base_price INTEGER,                   -- 本体価格（税抜）
    tax_amount INTEGER,                   -- 税額
    tax_included_amount INTEGER,          -- 税込価格
    tax_display_type TEXT,                -- 外税 or 内税
    tax_rate INTEGER,                     -- 税率（8 or 10）

    -- 標準化データ
    official_name TEXT,                   -- 正式商品名
    category_id UUID,                     -- カテゴリID（MASTER_Categories_expense参照）
    situation_id UUID,                    -- シチュエーションID
    std_unit_price INTEGER,               -- 標準化された単価
    std_amount INTEGER,                   -- 標準化された金額
    needs_review BOOLEAN DEFAULT FALSE,   -- レビュー必要フラグ

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_receipt_items_receipt ON "Rawdata_RECEIPT_items"(receipt_id);
CREATE INDEX IF NOT EXISTS idx_receipt_items_product ON "Rawdata_RECEIPT_items"(product_name);

-- ========================================
-- 3. 画像処理ログテーブル
-- ========================================
CREATE TABLE IF NOT EXISTS "99_lg_image_proc_log" (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- ファイル情報
    file_name TEXT UNIQUE NOT NULL,       -- ファイル名（ユニークキー）
    drive_file_id TEXT,

    -- 処理結果
    receipt_id UUID REFERENCES "Rawdata_RECEIPT_shops"(id),
    status TEXT NOT NULL,                 -- success, failed, manual_review
    error_message TEXT,
    transaction_ids UUID[],               -- 生成された明細のID配列

    -- 処理情報
    ocr_model TEXT,                       -- 使用したモデル名
    retry_count INTEGER DEFAULT 0,

    processed_at TIMESTAMPTZ DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_proc_log_status ON "99_lg_image_proc_log"(status);
CREATE INDEX IF NOT EXISTS idx_proc_log_file ON "99_lg_image_proc_log"(file_name);

-- ========================================
-- 4. カテゴリマスタ（費目）
-- ========================================
-- 注意: 実際のテーブル名は MASTER_Categories_expense
CREATE TABLE IF NOT EXISTS "MASTER_Categories_expense" (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,            -- 食費, 日用品等
    is_expense BOOLEAN DEFAULT TRUE,      -- TRUE=費用, FALSE=移動
    parent_id UUID REFERENCES "MASTER_Categories_expense"(id),
    sort_order INTEGER DEFAULT 0,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ========================================
-- 5. 商品辞書（正規化ルール）
-- ========================================
-- 注意: 実際のテーブル名は MASTER_Rules_transaction_dict
CREATE TABLE IF NOT EXISTS "MASTER_Rules_transaction_dict" (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_keyword TEXT NOT NULL,            -- レシート上の表記
    official_name TEXT NOT NULL,          -- 正式名称
    category_id UUID REFERENCES "MASTER_Categories_expense"(id),
    tax_rate INTEGER DEFAULT 10,          -- 8 or 10
    notes TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(raw_keyword, official_name)
);

-- ========================================
-- 関連テーブル（参考）
-- ========================================
-- 以下のテーブルも関連して使用されます:
--
-- MASTER_Categories_product  : 商品カテゴリマスタ（ネットスーパー用）
-- MASTER_Stores              : 店舗マスタ
-- Rawdata_FILE_AND_MAIL      : ファイル・メール管理（文書管理システム）
-- 10_ix_search_index         : 検索インデックス
--
-- ========================================
