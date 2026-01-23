-- ============================================================
-- レシートと銀行取引の紐付けテーブル
-- ============================================================

CREATE TABLE IF NOT EXISTS public."Kakeibo_Receipt_Links" (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    receipt_id uuid NOT NULL,      -- Rawdata_RECEIPT_shops.id
    transaction_id text NOT NULL,  -- Rawdata_BANK_transactions.id
    created_at timestamptz DEFAULT now(),

    UNIQUE(receipt_id),  -- 1レシート1取引
    UNIQUE(transaction_id)  -- 1取引1レシート
);

-- コメント
COMMENT ON TABLE public."Kakeibo_Receipt_Links" IS 'レシートと銀行取引の紐付け';
COMMENT ON COLUMN public."Kakeibo_Receipt_Links".receipt_id IS 'レシートID（Rawdata_RECEIPT_shops.id）';
COMMENT ON COLUMN public."Kakeibo_Receipt_Links".transaction_id IS '銀行取引ID（Rawdata_BANK_transactions.id）';

-- インデックス
CREATE INDEX IF NOT EXISTS idx_receipt_links_receipt ON public."Kakeibo_Receipt_Links" (receipt_id);
CREATE INDEX IF NOT EXISTS idx_receipt_links_transaction ON public."Kakeibo_Receipt_Links" (transaction_id);

-- Manual Editsにレシート紐付けカラムを追加
ALTER TABLE public."Kakeibo_Manual_Edits"
    ADD COLUMN IF NOT EXISTS has_receipt boolean DEFAULT false,
    ADD COLUMN IF NOT EXISTS receipt_id uuid;

COMMENT ON COLUMN public."Kakeibo_Manual_Edits".has_receipt IS 'レシート紐付け済みフラグ';
COMMENT ON COLUMN public."Kakeibo_Manual_Edits".receipt_id IS '紐付けられたレシートID';
