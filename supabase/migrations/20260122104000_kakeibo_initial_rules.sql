-- ============================================================
-- 初期ルールセット: 振替・除外・メジャーな店舗
-- ============================================================

INSERT INTO public."Kakeibo_CategoryRules"
(priority, match_type, pattern, category_major, category_minor, is_target, is_transfer, note)
VALUES
  -- 1. クレカ引き落とし・振替（集計から除外）
  (10, 'contains', 'ｶｰﾄﾞ',         NULL, NULL, false, true, 'カード引き落としは振替扱い'),
  (10, 'contains', 'ﾌﾘｶｴ',         NULL, NULL, false, true, '口座間振替'),
  (10, 'contains', 'ﾄｳｼ',          NULL, NULL, false, true, '投資信託などは資産移動'),

  -- 2. インフラ・固定費（優先度高）
  (50, 'contains', 'ﾃﾞﾝｷ',         '住まい', '電気', true, false, '電気代'),
  (50, 'contains', 'ｶﾞｽ',          '住まい', 'ガス', true, false, 'ガス代'),
  (50, 'contains', 'ｽｲﾄﾞｳ',        '住まい', '水道', true, false, '水道代'),
  (50, 'contains', 'NTT',          '住まい', 'ネット', true, false, '通信費'),

  -- 3. コンビニ・スーパー（正規表現で一網打尽）
  (80, 'regex',    '(ｾﾌﾞﾝ|ﾛｰｿﾝ|ﾌｧﾐﾘｰﾏｰﾄ)', '食費', 'コンビニ', true, false, '主要コンビニ'),
  (80, 'regex',    '(ｲｵﾝ|ﾏｯｸｽﾊﾞﾘｭ|ｾｲﾕｳ|ﾗｲﾌ)', '食費', '食料品', true, false, '主要スーパー'),

  -- 4. ECサイト（Amazon問題の回避策）
  -- 「Amazonだから」といって本か食品かは不明 → 一旦「通販」で固定し、深追いしない
  (90, 'contains', 'AMAZON',       '通販', 'Amazon', true, false, 'Amazonは一括計上'),
  (90, 'contains', 'ｱﾏｿﾞﾝ',        '通販', 'Amazon', true, false, 'Amazonは一括計上'),
  (90, 'contains', 'ﾗｸﾃﾝ',         '通販', '楽天',   true, false, '楽天も一括計上')
ON CONFLICT DO NOTHING;
