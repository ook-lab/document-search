-- ============================================================
-- 自動除外ルールテーブル
-- クレジットカード引き落としなど、デフォルトで計算対象外にする取引
-- content + institution の組み合わせでマッチング
-- ============================================================

CREATE TABLE IF NOT EXISTS public."Kakeibo_Auto_Exclude_Rules" (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    rule_name text NOT NULL,
    content_pattern text NOT NULL,      -- contentにマッチするパターン（部分一致）
    institution_pattern text NOT NULL,  -- institutionにマッチするパターン（部分一致）
    is_active boolean DEFAULT true,
    note text,
    created_at timestamptz DEFAULT now()
);

-- コメント
COMMENT ON TABLE public."Kakeibo_Auto_Exclude_Rules" IS '自動除外ルール（カード引き落とし等）- content+institutionの組み合わせ';

-- 初期データ：クレジットカード引き落としパターン（組み合わせ）
INSERT INTO public."Kakeibo_Auto_Exclude_Rules" (rule_name, content_pattern, institution_pattern, note) VALUES
    ('三井住友カード@三井住友', 'ミツイスミトモカ-ド', '三井住友銀行', 'SMBCカード引き落とし'),
    ('楽天カード@楽天', 'ラクテンカ-ドサ-ビス', '楽天銀行', '楽天カード引き落とし'),
    ('セゾン@三井住友', 'セゾン', '三井住友銀行', 'セゾンカード引き落とし'),
    ('イオンカード@三井住友', 'イオンフイナンシヤルサ-ビス', '三井住友銀行', 'イオンカード引き落とし'),
    ('ビューカード@三井住友', 'ビユ-カ-ド', '三井住友銀行', 'JR東日本ビューカード引き落とし'),
    ('東急カード@三井住友', 'トウキユウ カ-ド', '三井住友銀行', '東急カード引き落とし'),
    ('JCBカード@三井住友', 'ジエ-シ-ビ-', '三井住友銀行', 'JCBカード引き落とし')
ON CONFLICT DO NOTHING;

-- インデックス
CREATE INDEX IF NOT EXISTS idx_auto_exclude_rules_active
    ON public."Kakeibo_Auto_Exclude_Rules" (is_active) WHERE is_active = true;
