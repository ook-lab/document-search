-- カテゴリ推移 セットカテゴリー登録テーブル
-- 複数のカテゴリをひとつの名前でまとめて推移グラフ・表に表示するための設定

CREATE TABLE IF NOT EXISTS public."Kakeibo_Trend_Category_Sets" (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    set_name   text        NOT NULL,
    cat_level  text        NOT NULL DEFAULT 'major' CHECK (cat_level IN ('major', 'mid', 'small')),
    categories text[]      NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE  public."Kakeibo_Trend_Category_Sets"            IS 'カテゴリ推移用セットカテゴリー定義';
COMMENT ON COLUMN public."Kakeibo_Trend_Category_Sets".set_name   IS 'セット名（例: 外食系）';
COMMENT ON COLUMN public."Kakeibo_Trend_Category_Sets".cat_level  IS '分類レベル: major/mid/small';
COMMENT ON COLUMN public."Kakeibo_Trend_Category_Sets".categories IS '含めるカテゴリ名の配列';
