-- Rawdata_RECEIPT_shops に不足カラムを追加
ALTER TABLE public."Rawdata_RECEIPT_shops"
    ADD COLUMN IF NOT EXISTS is_cash    boolean DEFAULT false,
    ADD COLUMN IF NOT EXISTS tax_type   text;
