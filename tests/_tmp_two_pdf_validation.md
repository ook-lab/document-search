# two PDF validation


## shushu
path=H:\共有ドライブ\DataBase\Classroom\IKUYA-classroom\2025収支報告(新6年).pdf [1eBkcAj5QrAPv1-MW3UtFPGoY6kDYPKgU].pdf
success=True error=None
FAILURES (3):
  - B_T1 income-aligned rows=0 expected>=3
  - B_T1: 転入時追加納入 row must align with ③ on the right
  - B_T2 data rows=1 expected>=5

### visual_stream
0: non_table_paragraph sy=0.04809479806490196 ６学年保護者 各位 2026年4月 洗足学園小学校 校長 田中友樹
1: non_table_paragraph sy=0.13837205794422994 一年生を迎え、新しい一年が始まりました。 保護者の皆様には日頃学校教育にご協力を賜りましてありがとうございます。 ２０２
2: non_table_paragraph sy=0.23739196608142632 ２０２５年度 積立金 収支報告 （単位：円）
3: g_table sy=0.2635213893569603 idx=0
4: non_table_paragraph sy=0.5252962815948252 2025年度5学年児童在籍数 72 (３月時） 転学児童への返金 有 使途項目一覧 （１～６年）
5: g_table sy=0.5777347303002301 idx=1
6: non_table_paragraph sy=0.7102761319780398 【ご注意】 積立金は欠席や漢字検定の受検級などによって個々に支出する金額が異なります。 上記の一人当たりの金額は、児童在
7: non_table_paragraph sy=0.8824088166548893 ご不明な点がございましたら、小学校事務室 山腰までお問い合わせください。

### table B_T1 headers=['列1', '列2', '列3', '列4']
hr=[] dsr=0
<table class="md-embed-table"><tbody><tr><td></td><td></td><td></td><td></td></tr></tbody></table>

### table B_T2 headers=['項目', '内容']
hr=[] dsr=0
<table class="md-embed-table"><tbody><tr><td>①学習教材費&lt;配付用品&gt; ②特別学習活動費 ③校外活動費 ④学校生活管理費 ⑤宿泊行事費</td><td>ドリル・教科用教材など 漢字検定・行事・模擬テストなど 遠足・社会理科見学・生活科など 健康診断・災害共済掛金など 黒姫移動教室・夏の学校・修学旅行</td></tr></tbody></table>

## gakunen
path=H:\共有ドライブ\DataBase\Classroom\IKUYA-classroom\学年通信（47）.pdf [1nl6AtwVZbuW1ljqFRaUU3dHdBIzQjaoD].pdf
success=True error=None
OK

### visual_stream
0: non_table_paragraph sy=0.035439394941233625 洗足学園小学校６年生  HERO
1: non_table_paragraph sy=0.1387878787878788 2026年 47 4/24 発行
2: non_table_paragraph sy=0.24141413756091185 4/27 - 5/1 の予定
3: non_table_paragraph sy=0.27089350151293207 ・27日（月）朝いす出し
4: non_table_paragraph sy=0.2947610219319661 ・28日（火）朝算数、たてわり（5時間目、1〜6年生）
5: non_table_paragraph sy=0.31862856161714803 ・29日（水）昭和の日
6: non_table_paragraph sy=0.34249608203618215 ・30日（木）筆算検定
7: non_table_paragraph sy=0.3663636024552162 ・  1日（金）筆算返却
8: g_table_group sy=0.41351013178030305 idx=None
9: non_table_paragraph sy=0.7375851255474668 学級委員、決定！
10: g_table sy=0.7695706847632576 idx=2

### table P0_B1_5A headers=['日付', '5A', '科目・活動', '科目・活動', '科目・活動', '科目・活動', '科目・活動', '科目・活動']
hr=[0, 1] dsr=2
<table class="md-embed-table"><thead><tr><th></th><th>5A</th><th></th><th></th><th></th><th></th><th></th><th></th></tr><tr><th></th><th>朝</th><th>1</th><th>2</th><th>3</th><th>4</th><th>5</th><th>6</th></tr></thead><tbody><tr><td>27 （月）</td><td>いす 出し 朝読書</td><td>国語</td><td>国語</td><td>社会</td><td>算数</td><td>音楽</td><td>理科</td></tr><tr><td>28 （火）</td><td>朝算数</td><td>算数</td><td>道徳</td><td>音楽</td><td>国語</td><td>たて わり</td><td>社会</td></tr><tr><td>29 （水）</td><td>祝日・昭和の日</td><td>祝日・昭和の日</td><td>祝日・昭和の日</td><td>祝日・昭和の日</td><td>祝日・昭和の日</td><td>祝日・昭和の日</td><td>祝日・昭和の日</td></tr><tr><td>30 （木）</td><td>筆算 検定</td><td>体育</td><td>理科</td><td>算数</td><td>英語</td><td>社会</td><td>特活</td></tr><tr><td>1（金）</td><td>筆算 返却</td><td>実験</td><td>実験</td><td>社会</td><td>算数</td><td>国語</td><td>委員会 活動</td></tr></tbody></table>

### table P0_B1_5B headers=['日付', '5B', '科目・活動', '科目・活動', '科目・活動', '科目・活動', '科目・活動', '科目・活動']
hr=[0, 1] dsr=2
<table class="md-embed-table"><thead><tr><th></th><th>5B</th><th></th><th></th><th></th><th></th><th></th><th></th></tr><tr><th></th><th>朝</th><th>1</th><th>2</th><th>3</th><th>4</th><th>5</th><th>6</th></tr></thead><tbody><tr><td>27 （月）</td><td>いす 出し 朝読書</td><td>社会</td><td>算数</td><td>国語</td><td>国語</td><td>理科</td><td>音楽</td></tr><tr><td>28 （火）</td><td>朝算数</td><td>社会</td><td>算数</td><td>道徳</td><td>音楽</td><td>たて わり</td><td>国語</td></tr><tr><td>29 （水）</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr><tr><td>30 （木）</td><td>筆算 検定</td><td>理科</td><td>体育</td><td>英語</td><td>社会</td><td>算数</td><td>特活</td></tr><tr><td>1（金）</td><td>筆算 返却</td><td>国語</td><td>社会</td><td>実験</td><td>実験</td><td>算数</td><td>委員会 活動</td></tr></tbody></table>

### table P0_B2 headers=['6年A組', '役職', '6年B組']
hr=[0] dsr=1
<table class="md-embed-table"><thead><tr><th>6年A組</th><th></th><th>6年B組</th></tr></thead><tbody><tr><td>関口 葵</td><td>学級委員長</td><td>林 桃那</td></tr><tr><td>上條 真由</td><td>副学級委員長</td><td>山﨑 夢奏</td></tr><tr><td>蒲池 直仁、古賀 陽真俐、深井 聡太</td><td>学級委員</td><td>河内 晴琳、三浦 ソフィア、梁 凌翕</td></tr></tbody></table>