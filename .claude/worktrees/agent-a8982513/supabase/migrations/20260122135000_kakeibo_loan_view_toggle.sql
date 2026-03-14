-- ============================================================
-- 明細一覧/ローン管理の表示切り替えフラグ
-- ============================================================

-- Kakeibo_Manual_Edits に表示先フラグを追加
-- NULL: 自動判定（カードローン→ローン管理、それ以外→明細一覧）
-- 'loan': 強制的にローン管理へ
-- 'list': 強制的に明細一覧へ
ALTER TABLE public."Kakeibo_Manual_Edits"
ADD COLUMN IF NOT EXISTS view_target text NULL;

COMMENT ON COLUMN public."Kakeibo_Manual_Edits".view_target IS
  '表示先: NULL=自動判定, ''loan''=ローン管理, ''list''=明細一覧';
