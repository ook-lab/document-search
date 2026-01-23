-- =============================================================================
-- Migration: Add owner_id for Row-Level Security
-- =============================================================================
-- 目的: auth.uid() による行レベルアクセス制御を可能にする
-- =============================================================================

-- Rawdata_FILE_AND_MAIL に owner_id を追加
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES auth.users(id);

-- 10_ix_search_index に owner_id を追加（document_id 経由で制御も可能だが直接持つ方が高速）
ALTER TABLE "10_ix_search_index"
ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES auth.users(id);

-- 99_lg_correction_history に corrector_id を追加（auth.uid() 用）
ALTER TABLE "99_lg_correction_history"
ADD COLUMN IF NOT EXISTS corrector_id UUID REFERENCES auth.users(id);

-- Rawdata_RECEIPT_shops に owner_id を追加
ALTER TABLE "Rawdata_RECEIPT_shops"
ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES auth.users(id);

-- Rawdata_RECEIPT_items は receipt_id 経由で制御するため追加不要

-- MASTER_Rules_transaction_dict に created_by を追加（学習データの所有者）
ALTER TABLE "MASTER_Rules_transaction_dict"
ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES auth.users(id);

-- インデックス作成
CREATE INDEX IF NOT EXISTS idx_rawdata_file_owner ON "Rawdata_FILE_AND_MAIL"(owner_id);
CREATE INDEX IF NOT EXISTS idx_search_index_owner ON "10_ix_search_index"(owner_id);
CREATE INDEX IF NOT EXISTS idx_receipt_shops_owner ON "Rawdata_RECEIPT_shops"(owner_id);
