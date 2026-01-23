-- ============================================================
-- 住宅ローンA/B/Cとカードローンのルール
-- ============================================================

-- 住宅ローンA（¥98,631）→ 住まい/家賃
INSERT INTO public."Kakeibo_CategoryRules" (
    priority, is_enabled, match_type, pattern, institution,
    amount_min, amount_max,
    category_major, category_minor, is_target, is_transfer,
    institution_override, note
) VALUES (
    10, true, 'contains', '住宅ローンA', NULL,
    98630, 98632,  -- ほぼ固定額なので±1円
    '住まい', '家賃', true, false,
    '家賃', '住宅ローンA（institution→家賃に読み替え）'
);

-- 住宅ローンB（¥59,790）→ 住まい/家賃
INSERT INTO public."Kakeibo_CategoryRules" (
    priority, is_enabled, match_type, pattern, institution,
    amount_min, amount_max,
    category_major, category_minor, is_target, is_transfer,
    institution_override, note
) VALUES (
    10, true, 'contains', '住宅ローンB', NULL,
    59789, 59791,  -- ほぼ固定額なので±1円
    '住まい', '家賃', true, false,
    '家賃', '住宅ローンB（institution→家賃に読み替え）'
);

-- 住宅ローンC（¥18,500）→ 住まい/家賃
INSERT INTO public."Kakeibo_CategoryRules" (
    priority, is_enabled, match_type, pattern, institution,
    amount_min, amount_max,
    category_major, category_minor, is_target, is_transfer,
    institution_override, note
) VALUES (
    10, true, 'contains', '住宅ローンC', NULL,
    18499, 18501,  -- ほぼ固定額なので±1円
    '住まい', '家賃', true, false,
    '家賃', '住宅ローンC（institution→家賃に読み替え）'
);

-- カードローン（借入/返済）→ 振替として除外
-- パターン1: カードローン借入
INSERT INTO public."Kakeibo_CategoryRules" (
    priority, is_enabled, match_type, pattern, institution,
    category_major, category_minor, is_target, is_transfer,
    note
) VALUES (
    5, true, 'contains', 'カードローン', NULL,
    '振替', 'カードローン', false, true,
    'カードローンの借入/返済は振替扱い（家計に影響しない）'
);

-- カードローン（別表記対応）
INSERT INTO public."Kakeibo_CategoryRules" (
    priority, is_enabled, match_type, pattern, institution,
    category_major, category_minor, is_target, is_transfer,
    note
) VALUES (
    5, true, 'regex', '(ローン.*借入|ローン.*返済|借入.*ローン|返済.*ローン)', NULL,
    '振替', 'カードローン', false, true,
    'カードローン関連（正規表現）'
);
