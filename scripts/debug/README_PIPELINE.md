# 統合デバッグパイプライン

全パイプライン（A→B→D→E→F→G）を実行し、各ステージの結果をローカルに保存するデバッグツール。

## パイプライン構成

```
A: Document Type Detection (書類種別判定)
  ↓
B: Format-Specific Physical Structuring (物理構造化)
  ↓
D: Visual Structure Analysis (ビジュアル構造解析)
  ↓
E: Vision Extraction & AI Structuring (Vision抽出とAI構造化)
  ↓
F: Data Fusion & Normalization (データ融合と正規化)
  ↓
G: UI Optimized Structuring (UI最適化構造化)
```

## 基本的な使い方

### 全行程を実行

```bash
python scripts/debug/run_debug_pipeline.py test001 --pdf path/to/document.pdf
```

- 各ステージの結果は `debug_output/test001/` に保存されます
- キャッシュがあるステージはスキップされます

### 特定のステージだけを再実行

```bash
# Stage Eだけを再実行（Stage Dのキャッシュを使用）
python scripts/debug/run_debug_pipeline.py test001 --stage E --force
```

### ステージ範囲を指定して実行

```bash
# Stage DからGまで実行
python scripts/debug/run_debug_pipeline.py test001 --start D --end G --force

# Stage Fから最後まで再実行
python scripts/debug/run_debug_pipeline.py test001 --start F --force
```

### タグ付きで保存（バージョン比較用）

```bash
# "v2_experimental" というタグ付きで保存
python scripts/debug/run_debug_pipeline.py test001 --stage E --tag "v2_experimental" --force
```

結果は `test001_stage_e_v2_experimental.json` として保存されます。

## 出力ファイル

### ステージ別結果ファイル

- `{uuid}_stage_a.json`: Stage A の結果（書類種別判定）
- `{uuid}_stage_b.json`: Stage B の結果（物理構造化）
- `{uuid}_stage_d.json`: Stage D の結果（表検出）
- `{uuid}_stage_e.json`: Stage E の結果（Vision抽出）
- `{uuid}_stage_f.json`: Stage F の結果（データ融合）
- `{uuid}_stage_g.json`: Stage G の結果（UI最適化）

### 特別な出力ファイル

- `{uuid}_ui_data.json`: Stage G のUI用データ（フロントエンド直接使用可能）

## キャッシュ機能

- 各ステージの結果はJSON形式で保存されます
- 2回目以降の実行では、キャッシュがあるステージは自動的にスキップされます
- `--force` フラグでキャッシュを無視して強制実行できます

### キャッシュのバックアップ

既存のキャッシュファイルがある場合、上書き前に `.json.bak` として自動バックアップされます。

## オプション一覧

| オプション | 説明 | 例 |
|-----------|------|-----|
| `uuid` | 処理対象のUUID（任意の識別子） | `test001` |
| `--pdf` | PDFファイルパス | `--pdf document.pdf` |
| `--stage` | 対象ステージ（mode=onlyの場合） | `--stage E` |
| `--start` | 開始ステージ | `--start D` |
| `--end` | 終了ステージ | `--end G` |
| `--mode` | 実行モード（all/only/from） | `--mode all` |
| `--force` | キャッシュを無視して強制実行 | `--force` |
| `--tag` | 結果ファイルにタグを付ける | `--tag "v2_test"` |
| `--output-dir` | 出力ディレクトリ | `--output-dir my_output` |

## 実行例

### 1. 初回実行（全ステージ）

```bash
python scripts/debug/run_debug_pipeline.py doc_001 --pdf samples/sample.pdf
```

### 2. Stage Eの再実行（パラメータ調整後）

```bash
python scripts/debug/run_debug_pipeline.py doc_001 --stage E --force
```

### 3. Stage FとGのみ再実行

```bash
python scripts/debug/run_debug_pipeline.py doc_001 --start F --end G --force
```

### 4. 複数バージョンの比較

```bash
# バージョン1
python scripts/debug/run_debug_pipeline.py doc_001 --stage E --tag "v1_baseline" --force

# バージョン2（パラメータ変更後）
python scripts/debug/run_debug_pipeline.py doc_001 --stage E --tag "v2_improved" --force

# 結果を比較
diff debug_output/doc_001/doc_001_stage_e_v1_baseline.json \
     debug_output/doc_001/doc_001_stage_e_v2_improved.json
```

## トラブルシューティング

### キャッシュが破損している

```bash
# キャッシュを削除して再実行
rm -rf debug_output/test001
python scripts/debug/run_debug_pipeline.py test001 --pdf document.pdf
```

### 特定のステージでエラーが発生

```bash
# そのステージのみを強制再実行
python scripts/debug/run_debug_pipeline.py test001 --stage D --force
```

### 結果を確認

```bash
# JSONファイルを整形して表示
cat debug_output/test001/test001_stage_g.json | jq .
```

## パイプライン詳細

### Stage A: Document Type Detection
- **入力**: PDFファイル
- **処理**: メタデータ解析、サイズ測定
- **出力**: document_type（GOODNOTES, WORD, EXCEL, etc.）

### Stage B: Format-Specific Physical Structuring
- **入力**: PDFファイル、Stage A結果
- **処理**: フォーマット特化型テキスト抽出、B-90レイヤー削除
- **出力**: physical_chars、purged_pdf_path、purged_image_paths

### Stage D: Visual Structure Analysis
- **入力**: Stage Bのpurged PDF/images
- **処理**: ベクトル罫線抽出、ラスター罫線検出、表領域特定
- **出力**: table_images、non_table_images、cell_maps

### Stage E: Vision Extraction & AI Structuring
- **入力**: Stage Dの画像
- **処理**: OCRスカウティング、Gemini 2.5による構造化
- **出力**: table_content（Markdown）、context_content（JSON）

### Stage F: Data Fusion & Normalization
- **入力**: Stage B + Stage E結果
- **処理**: データ融合、日付正規化、表結合
- **出力**: merged_events、normalized_tables、unified_text

### Stage G: UI Optimized Structuring
- **入力**: Stage F結果
- **処理**: 表のJSON変換、ブロック整頓、ノイズ除去
- **出力**: ui_data（sections, tables, timeline, actions, notices）

## 開発者向け情報

### 新しいステージの追加

1. `shared/pipeline/stage_x/` に新しいステージを作成
2. `x1_controller.py` でコントローラーを実装
3. `run_debug_pipeline.py` の `STAGES` リストに追加
4. パイプライン処理部分に新しいステージを追加

### デバッグのヒント

- `--force` フラグで常に最新のコードを実行
- `--tag` でバージョン管理
- JSONファイルを直接編集してテストデータを作成可能
