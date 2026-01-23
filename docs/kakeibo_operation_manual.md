# 家計簿システム 運用マニュアル

## カテゴリ体系

| 大項目 | 中項目（例） | 備考 |
|--------|-------------|------|
| 食費 | 食料品, 外食, コンビニ, カフェ | 日々の食事 |
| 日用品 | ドラッグストア, 雑貨, 百均 | 消耗品 |
| 交通費 | 電車, タクシー, 駐車場, ガソリン | |
| 住まい | 家賃, 電気, ガス, 水道, ネット | 固定費 |
| 娯楽 | 趣味, 旅行, 映画, サブスク | 変動費（楽しみ） |
| 通販 | Amazon, 楽天, その他通販 | 詳細分類を諦める項目 |
| 特別な支出 | 家具家電, 冠婚葬祭, 医療費 | 年に数回の大きな出費 |
| 未分類 | (null) | AIまたは手動で判断待ち |

---

## Amazon問題の解決（運用ルール）

### やってはいけないこと

- 銀行の「Amazon 1,980円」という明細を見て、無理やり「これは本だ」「これは洗剤だ」と推測する
- 銀行明細を削除して、Amazonの注文履歴から手入力しようとする（金額がズレて合わなくなります）

### 正しい運用ルール

1. **銀行明細（Supabase）は「支払いの事実」として絶対視する**
   - たとえ中身が何であれ、「Amazonに〇〇円払った」という事実として `大項目:通販` で計上
   - これで「月の総支出」は1円もズレない

2. **内訳が気になるときは「ドリルダウン」する**
   - 家計簿上は「今月はAmazonで3万円使った」でOK
   - 「使いすぎ？」と思ったら、SupabaseではなくAmazonの注文履歴画面を見に行く

---

## 月次ルーティン

### 1. CSV配置
各銀行・カード会社のサイトからCSVをダウンロードし、Google Driveの `import` フォルダに配置

### 2. 同期実行 (GAS)
- トリガー設定していれば自動実行
- 手動の場合：GASエディタで `syncCsvToSupabase` を実行
- `processed` フォルダに移動したことを確認

### 3. AI分類 & レポート (Python)

```bash
# 環境変数設定
$env:PYTHONPATH = $PWD

# AI分類（未分類があれば）
python scripts/ops/kakeibo_ai_classify_merchants.py --mode openai --limit 200

# レポート作成（例：1月分）
python scripts/ops/generate_kakeibo_report_cli.py --from 2026-01-01 --to 2026-01-31 --group-by category_major
```

### 4. 振り返り
- 生成された `reports/kakeibo_report_xxxx.xlsx` と円グラフを確認
- 使いすぎた項目をチェック

---

## コマンドリファレンス

### レポート生成

```bash
# 大項目別
python scripts/ops/generate_kakeibo_report_cli.py --from 2026-01-01 --to 2026-01-31 --group-by category_major

# 中項目別
python scripts/ops/generate_kakeibo_report_cli.py --from 2026-01-01 --to 2026-01-31 --group-by category_minor

# 金融機関別
python scripts/ops/generate_kakeibo_report_cli.py --from 2026-01-01 --to 2026-01-31 --group-by institution

# 店舗別
python scripts/ops/generate_kakeibo_report_cli.py --from 2026-01-01 --to 2026-01-31 --group-by merchant

# 月別推移（年間）
python scripts/ops/generate_kakeibo_report_cli.py --from 2026-01-01 --to 2026-12-31 --group-by month
```

### AI仕訳

```bash
# ドライラン（AIなし）
python scripts/ops/kakeibo_ai_classify_merchants.py --mode null --limit 10

# OpenAI使用
python scripts/ops/kakeibo_ai_classify_merchants.py --mode openai --limit 200
```

---

## トラブルシューティング

### GASでエラーが出る
- `CONFIG` のフォルダID、Supabase URL、API Keyを確認
- service_role key（anon keyではない）を使用しているか確認

### レポートが空になる
- 対象期間にデータがあるか確認
- `is_target_final = true` かつ `is_transfer_final = false` の明細があるか確認

### Pythonで `ModuleNotFoundError`
- `$env:PYTHONPATH = $PWD` を実行してから再試行
