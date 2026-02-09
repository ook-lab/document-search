# Document Management System - 全パイプラインアーキテクチャ

**最終更新**: 2026-02-10
**バージョン**: 2.0 (Gemini 2.5ベース)

---

## 目次

1. [プロジェクト概要](#プロジェクト概要)
2. [全体アーキテクチャ](#全体アーキテクチャ)
3. [Stage A: Document Type Detection](#stage-a-document-type-detection)
4. [Stage B: Format-Specific Physical Structuring](#stage-b-format-specific-physical-structuring)
5. [Stage D: Visual Structure Analysis](#stage-d-visual-structure-analysis)
6. [Stage E: Vision Extraction & AI Structuring](#stage-e-vision-extraction--ai-structuring)
7. [Stage F: Data Fusion & Normalization](#stage-f-data-fusion--normalization)
8. [Stage G: UI Optimized Structuring](#stage-g-ui-optimized-structuring)
9. [Integration: DB & API Connection](#integration-db--api-connection)
10. [Debug Pipeline](#debug-pipeline)
11. [ファイル構造](#ファイル構造)
12. [開発ガイド](#開発ガイド)

---

## プロジェクト概要

### 目的

PDFドキュメント（学校プリント、会議資料、レポート等）を自動的に解析し、構造化データとして抽出・保存するシステム。

### 主な特徴

- **マルチフォーマット対応**: Word, Excel, PowerPoint, InDesign, Goodnotes, スキャンPDF
- **ハイブリッド抽出**: デジタルテキスト抽出 + Vision AI
- **高精度表解析**: ベクトル罫線 + ラスター罫線の統合検出
- **AI構造化**: Gemini 2.5による文脈理解とデータ正規化
- **UI最適化**: フロントエンド直接描画可能なクリーンJSON

### 技術スタック

- **PDF処理**: pdfplumber, PyMuPDF (fitz)
- **画像処理**: OpenCV, PIL
- **OCR**: Tesseract, EasyOCR
- **AI**: Gemini 2.5 Flash / Flash-lite
- **DB**: PostgreSQL (Supabase) + JSONB
- **言語**: Python 3.11+

---

## 全体アーキテクチャ

### パイプライン全体図

```
┌─────────────────────────────────────────────────────────────────┐
│                        INPUT: PDF File                          │
└─────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ Stage A: Document Type Detection                                │
│   A3 Entry Point → A5 Type Analyzer + A6 Dimension Measurer     │
│   Output: document_type, page_size, metadata                    │
└─────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ Stage B: Format-Specific Physical Structuring                   │
│   B1 Controller → B3-B10/B14/B42 Processors → B90 Layer Purge   │
│   Output: physical_chars, purged_pdf, purged_images             │
└─────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ Stage D: Visual Structure Analysis                              │
│   D1 Controller → D3 Vector + D5 Raster → D8 Grid → D9 Cells    │
│   Output: table_images, non_table_images, cell_maps             │
└─────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ Stage E: Vision Extraction & AI Structuring (Gemini 2.5)        │
│   E1 Controller → E1 Scout → E5 Visualizer → E20/E30 Extractors │
│   Output: table_markdown, context_json, token_usage             │
└─────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ Stage F: Data Fusion & Normalization                            │
│   F1 Controller → F1 Merger → F3 Normalizer → F5 Joiner         │
│   Output: merged_events, normalized_dates, unified_tables       │
└─────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ Stage G: UI Optimized Structuring                               │
│   G1 Controller → G1 Reproducer → G3 Arranger → G5 Eliminator   │
│   Output: ui_data (sections, tables, timeline, actions)         │
└─────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                    OUTPUT: Structured Data                       │
│              (DB Storage + API + Frontend Ready)                │
└─────────────────────────────────────────────────────────────────┘
```

### データフロー

```
PDF → [A] document_type
    → [B] physical_chars + purged_pdf
    → [D] table_images + non_table_images + cell_maps
    → [E] table_markdown + context_json
    → [F] merged_data + normalized_dates
    → [G] ui_data (JSON)
    → [DB] stage_g_structured_data (JSONB)
```

---

## Stage A: Document Type Detection

### 概要

PDFファイルのメタデータとページサイズを解析し、ドキュメントの種類を判定する。

### パイプライン

```
A3 Entry Point
  ├─ A5: Type Analyzer (書類種別判定)
  └─ A6: Dimension Measurer (サイズ測定)
```

### A3: Entry Point

**ファイル**: `shared/pipeline/stage_a/a3_entry_point.py`

**役割**: Stage Aのオーケストレーター

**処理**:
1. A5（Type Analyzer）を実行
2. A6（Dimension Measurer）を実行
3. 結果を統合

**出力**:
```json
{
  "success": true,
  "document_type": "GOODNOTES",
  "page_count": 5,
  "dimensions": {"width": 595.0, "height": 842.0, "unit": "pt"},
  "dimensions_mm": {"width": 210.0, "height": 297.0, "unit": "mm"},
  "is_multi_size": false,
  "raw_metadata": {...},
  "confidence": "HIGH",
  "reason": "キーワード一致: goodnotes"
}
```

### A5: Type Analyzer

**ファイル**: `shared/pipeline/stage_a/a5_type_analyzer.py`

**役割**: PDFメタデータから書類種類を判定

**判定タイプ**:
- `GOODNOTES`: Goodnotes アプリ由来
- `WORD`: Microsoft Word 由来
- `EXCEL`: Microsoft Excel 由来
- `POWERPOINT`: Microsoft PowerPoint 由来
- `INDESIGN`: Adobe InDesign 由来
- `SCAN`: スキャナ/複合機由来
- `REPORT`: 多段組レポート

**判定ロジック**:
```python
# 優先順位（上から順）
1. GOODNOTES: "goodnotes", "good.*notes"
2. WORD: "microsoft.*word", "word", "winword"
3. EXCEL: "microsoft.*excel", "excel"
4. POWERPOINT: "microsoft.*powerpoint", "powerpoint"
5. INDESIGN: "adobe.*indesign", "indesign"
6. SCAN: "scan", "scanner", "ricoh", "canon", etc.
```

### A6: Dimension Measurer

**ファイル**: `shared/pipeline/stage_a/a6_dimension_measurer.py`

**役割**: ページサイズを測定

**測定項目**:
- ページ数
- ページサイズ（pt, mm）
- マルチサイズ検出（ページごとにサイズが異なるか）

---

## Stage B: Format-Specific Physical Structuring

### 概要

ドキュメントの種類に応じた専用プロセッサでデジタルテキストを抽出し、Stage Eでの二重読み取りを防ぐためにテキスト層を削除した画像を生成する。

### パイプライン

```
B1 Controller
  ├─ Native Processors (100%精度)
  │   ├─ B6: Native Word (.docx)
  │   ├─ B7: Native Excel (.xlsx)
  │   └─ B8: Native PowerPoint (.pptx)
  ├─ PDF Processors (座標解析)
  │   ├─ B3: PDF-Word
  │   ├─ B4: PDF-Excel
  │   ├─ B5: PDF-PowerPoint
  │   ├─ B10: DTP (InDesign/Scan)
  │   └─ B14: Goodnotes
  ├─ Specialized Processors
  │   └─ B42: Multi-Column Report
  └─ Post-Processing
      └─ B90: Layer Purge
```

### B1: Controller

**ファイル**: `shared/pipeline/stage_b/b1_controller.py`

**役割**: Stage Aの判定結果に基づいてプロセッサを自動選択・実行

**ルーティングロジック**:
```python
if document_type == 'GOODNOTES':
    return 'B14_GOODNOTES'
elif document_type == 'REPORT':
    return 'B42_MULTICOLUMN'
elif file_ext == '.docx':
    return 'B6_NATIVE_WORD'
elif file_ext == '.pdf' and document_type == 'WORD':
    return 'B3_PDF_WORD'
# ... 以下同様
```

**自動実行**:
- PDF処理後、自動的に B90 Layer Purge を実行

### ネイティブ処理（100%精度）

#### B6: Native Word

**ファイル**: `shared/pipeline/stage_b/b6_native_word.py`

**処理**:
- python-docx で .docx を直接解析
- 段落、表、スタイル情報を完全抽出

#### B7: Native Excel

**ファイル**: `shared/pipeline/stage_b/b7_native_excel.py`

**処理**:
- openpyxl で .xlsx を直接解析
- セル値、数式、書式を完全抽出

#### B8: Native PowerPoint

**ファイル**: `shared/pipeline/stage_b/b8_native_ppt.py`

**処理**:
- python-pptx で .pptx を直接解析
- スライド、テキストボックス、表を完全抽出

### PDF処理（座標解析）

#### B3: PDF-Word

**ファイル**: `shared/pipeline/stage_b/b3_pdf_word.py`

**処理**:
- pdfplumber で文字座標を抽出
- 段落構造を推定（Y座標のギャップ検出）

#### B4: PDF-Excel

**ファイル**: `shared/pipeline/stage_b/b4_pdf_excel.py`

**処理**:
- pdfplumber で罫線と文字を抽出
- セル構造を推定（罫線交点解析）

#### B5: PDF-PowerPoint

**ファイル**: `shared/pipeline/stage_b/b5_pdf_ppt.py`

**処理**:
- pdfplumber で文字座標を抽出
- スライド構造を推定（X/Y座標クラスタリング）

#### B10: DTP

**ファイル**: `shared/pipeline/stage_b/b10_dtp.py`

**処理**:
- InDesign由来PDFやスキャンPDFに対応
- 汎用的な座標解析

#### B14: Goodnotes

**ファイル**: `shared/pipeline/stage_b/b14_goodnotes_processor.py`

**処理**:
1. **デジタルテキスト抽出**: Goodnotesのテキストツールで入力された文字を抽出
2. **手書き領域検出**: PDF Annots（Ink Annotation）から手書きストロークを特定
3. **論理ブロック生成**: デジタルテキストをブロック化

**出力**:
```json
{
  "is_structured": true,
  "data_type": "goodnotes",
  "digital_texts": [
    {
      "page": 0,
      "text": "学校",
      "bbox": [100, 50, 150, 70],
      "fontname": "Arial",
      "size": 12
    }
  ],
  "handwritten_zones": [
    {
      "page": 0,
      "bbox": [200, 100, 400, 300],
      "type": "handwritten",
      "subtype": "ink"
    }
  ],
  "logical_blocks": [...]
}
```

### 特化型処理

#### B42: Multi-Column Report

**ファイル**: `shared/pipeline/stage_b/b42_multicolumn_report.py`

**処理**:
- 多段組み（2段組、3段組）のレポート専用
- カラム境界を検出してテキストを分離

### 後処理

#### B90: Layer Purge

**ファイル**: `shared/pipeline/stage_b/b90_layer_purge.py`

**役割**: テキスト層を削除した画像を生成（Stage Eでの二重読み取り防止）

**処理**:
1. 抽出済みテキストの座標を取得
2. PyMuPDF (fitz) でテキスト領域を白塗りマスク
3. 画像として保存

**出力**:
- `purged_pdf_path`: テキスト削除済みPDF
- `purged_image_paths`: ページごとの画像（.png）
- `mask_stats`: マスク統計

---

## Stage D: Visual Structure Analysis

### 概要

B90で生成されたテキスト削除済み画像から、罫線を検出して表構造を解析する。ベクトル罫線（DTP由来）とラスター罫線（スキャン由来）を統合して高精度な表検出を実現。

### パイプライン

```
D1 Controller
  ├─ D3: Vector Line Extractor (ベクトル罫線抽出)
  ├─ D5: Raster Line Detector (ラスター罫線検出)
  ├─ D8: Grid Analyzer (格子解析)
  ├─ D9: Cell Identifier (セル特定)
  └─ D10: Image Slicer (画像分割)
```

### D1: Controller

**ファイル**: `shared/pipeline/stage_d/d1_controller.py`

**役割**: Stage Dのオーケストレーター

**処理順序**: D3 → D5 → D8 → D9 → D10

### D3: Vector Line Extractor

**ファイル**: `shared/pipeline/stage_d/d3_vector_line_extractor.py`

**役割**: PDFからベクトル罫線を抽出

**処理**:
- pdfplumber で lines/rects を取得
- 短い装飾線をフィルタリング（min_line_length=10pt）
- 水平線/垂直線を分類

**出力**:
```json
{
  "vector_lines": {
    "horizontal": [
      {"x0": 100, "y": 200, "x1": 500, "page": 0}
    ],
    "vertical": [
      {"x": 100, "y0": 100, "y1": 400, "page": 0}
    ]
  }
}
```

### D5: Raster Line Detector

**ファイル**: `shared/pipeline/stage_d/d5_raster_line_detector.py`

**役割**: 画像からラスター罫線を検出（スキャンPDF対応）

**処理**:
1. OpenCV でグレースケール変換 + 二値化
2. モルフォロジー変換（水平/垂直カーネル）
3. HoughLinesP で線分検出
4. 座標正規化（0.0-1.0）

**技術**:
```python
# 水平線検出
horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (width // 30, 1))
detected = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel, iterations=2)
lines = cv2.HoughLinesP(detected, 1, np.pi/180, threshold=50, minLineLength=100, maxLineGap=10)
```

### D8: Grid Analyzer

**ファイル**: `shared/pipeline/stage_d/d8_grid_analyzer.py`

**役割**: D3/D5の罫線を統合して格子構造を解析

**処理**:
1. ベクトル線 + ラスター線を統合
2. 交点を計算（水平線 × 垂直線）
3. 交点密度から表領域を特定
4. 表のバウンディングボックスを生成

### D9: Cell Identifier

**ファイル**: `shared/pipeline/stage_d/d9_cell_identifier.py`

**役割**: 交点から個々のセル座標を算出

**処理**:
1. 交点をソート（X座標、Y座標）
2. R1C1 形式の cell_map を生成
3. セルごとの座標を記録

**出力**:
```json
{
  "cell_map": {
    "R1C1": {"x0": 100, "y0": 100, "x1": 200, "y1": 150},
    "R1C2": {"x0": 200, "y0": 100, "x1": 300, "y1": 150},
    ...
  }
}
```

### D10: Image Slicer

**ファイル**: `shared/pipeline/stage_d/d10_image_slicer.py`

**役割**: 表/非表領域を個別画像として分割

**処理**:
1. 表領域を切り出し（table_T1.png, table_T2.png, ...）
2. 非表領域画像を生成（表を白塗り: background_only.png）

**出力**:
```json
{
  "table_images": [
    {
      "table_id": "T1",
      "image_path": "/path/to/table_T1.png",
      "bbox": [100, 100, 500, 400],
      "cell_map": {...}
    }
  ],
  "non_table_images": [
    {
      "image_path": "/path/to/background_only.png",
      "page": 0
    }
  ]
}
```

---

## Stage E: Vision Extraction & AI Structuring

### 概要

Stage Dで分割された画像を Gemini 2.5 で解析し、表をMarkdown形式、地の文をJSON形式で抽出する。

### パイプライン（Ver 2.0 - Gemini 2.5ベース）

```
E1 Controller
  ├─ E1: OCR Scouter (文字密度測定)
  ├─ E5: Text Block Visualizer (ブロック認識)
  └─ ルーティング
      ├─ E20: Context Extractor (地の文用 - Gemini 2.5 Flash-lite)
      └─ E30: Table Structure Extractor (表用 - Gemini 2.5 Flash)
```

### E1: Controller

**ファイル**: `shared/pipeline/stage_e/e1_controller.py`

**役割**: Stage Eのオーケストレーター

**処理フロー**:
1. E1 OCR Scouter で文字密度測定
2. E5 Text Block Visualizer でブロック認識
3. 文字密度に応じてルーティング
   - 表画像 → E30 (Gemini 2.5 Flash)
   - 非表画像 → E20 (Gemini 2.5 Flash-lite)

### E1: OCR Scouter

**ファイル**: `shared/pipeline/stage_e/e1_ocr_scouter.py`

**役割**: 軽量OCRで文字密度を測定

**処理**:
- Tesseract または EasyOCR で文字数をカウント
- 密度レベルを判定

**閾値**:
- `none`: 0-9文字
- `low`: 10-99文字
- `medium`: 100-499文字
- `high`: 500文字以上

### E5: Text Block Visualizer

**ファイル**: `shared/pipeline/stage_e/e5_text_block_visualizer.py`

**役割**: OpenCVでテキストブロックを検出

**処理**:
1. グレースケール + 二値化
2. モルフォロジー変換（膨張・収縮）
3. 輪郭検出
4. ブロック座標（bbox）を取得

**出力**:
```json
{
  "text_blocks": [
    {
      "block_id": "B1",
      "bbox": [100, 50, 400, 100],
      "type": "text"
    }
  ]
}
```

### E20: Context Extractor

**ファイル**: `shared/pipeline/stage_e/e20_context_extractor.py`

**役割**: 地の文（非表領域）から予定・タスク・注意事項を抽出

**モデル**: Gemini 2.5 Flash-lite（高速・低コスト）

**プロンプト**:
```
この画像から以下を抽出してください：
1. 予定・イベント（日付と内容）
2. タスク・やること（期限と内容）
3. 注意事項・持ち物

JSON形式で出力：
{
  "schedule": [{"date": "2025-01-12", "event": "..."}],
  "tasks": [{"deadline": "2025-01-15", "task": "..."}],
  "notices": [{"category": "持ち物", "content": "..."}]
}
```

**出力**:
```json
{
  "schedule": [
    {"date": "2025-01-12", "event": "保護者会"}
  ],
  "tasks": [
    {"deadline": "2025-01-15", "task": "宿題提出"}
  ],
  "notices": [
    {"category": "持ち物", "content": "筆記用具"}
  ]
}
```

### E30: Table Structure Extractor

**ファイル**: `shared/pipeline/stage_e/e30_table_structure_extractor.py`

**役割**: 表を Markdown 形式で抽出

**モデル**: Gemini 2.5 Flash（高精度）

**プロンプト（座標ヒント付き）**:
```
この表画像をMarkdown形式で抽出してください。

【座標ヒント】
- R1C1: (100, 100) - (200, 150)
- R1C2: (200, 100) - (300, 150)
...

Markdown形式で出力：
| ヘッダー1 | ヘッダー2 |
|----------|----------|
| データ1  | データ2  |
```

**出力**:
```markdown
| 科目 | 点数 |
|------|------|
| 国語 | 85   |
| 数学 | 92   |
```

---

## Stage F: Data Fusion & Normalization

### 概要

Stage B（デジタル抽出）と Stage E（Vision抽出）の結果を融合し、日付を正規化し、表を結合する。

### パイプライン

```
F1 Controller
  ├─ F1: Data Fusion Merger (ハイブリッド統合)
  ├─ F3: Smart Date/Time Normalizer (日付正規化)
  └─ F5: Logical Table Joiner (表結合)
```

### F1: Controller

**ファイル**: `shared/pipeline/stage_f/f1_controller.py`

**役割**: Stage Fのオーケストレーター

**処理順序**: F1 Merger → F3 Normalizer → F5 Joiner

### F1: Data Fusion Merger

**ファイル**: `shared/pipeline/stage_f/f1_data_fusion_merger.py`

**役割**: Stage B と Stage E の結果を融合

**融合戦略**:
1. **Stage B（デジタル）をベースに構築**
   - pdfplumber で抽出したテキストが最優先
2. **Stage E（Vision）で補完**
   - デジタルテキストがない領域をVisionで埋める
3. **ページ・座標順に読み順を復元**
   - 座標でソートして論理的な読み順を生成

**出力**:
```json
{
  "merged_text": "デジタルテキスト + Visionテキスト",
  "merged_events": [...],
  "merged_tables": [...]
}
```

### F3: Smart Date/Time Normalizer

**ファイル**: `shared/pipeline/stage_f/f3_smart_date_normalizer.py`

**役割**: 曖昧な日付表現を ISO 8601 形式に変換

**モデル**: Gemini 2.5 Flash-lite

**処理例**:
- `1/12` → `2025-01-12`（年度補完）
- `来週月曜` → `2025-01-15`（基準日から計算）
- `3学期始業式` → `2025-01-08`（学校カレンダー推定）

**プロンプト**:
```
コンテキスト:
- 年度: 2025年
- 基準日: 2025-01-10
- ドキュメントタイプ: 学校プリント

以下の日付表現を ISO 8601 形式（YYYY-MM-DD）に変換してください：
- "1/12"
- "来週月曜"

JSON形式で出力：
{
  "normalized_dates": [
    {"original": "1/12", "normalized": "2025-01-12"},
    {"original": "来週月曜", "normalized": "2025-01-15"}
  ]
}
```

### F5: Logical Table Joiner

**ファイル**: `shared/pipeline/stage_f/f5_logical_table_joiner.py`

**役割**: 論理的に同一の表を結合

**処理**:
1. **多段組み表の結合**（B42由来）
   - 左カラム・右カラムを1つの表に統合
2. **ページ跨ぎ表の統合**
   - カラムヘッダーの整合性をチェック
   - 同一構造の表を縦に結合

---

## Stage G: UI Optimized Structuring

### 概要

Stage Fの統合データを、フロントエンドが追加処理なしで直接描画できる形式に変換する。

### パイプライン

```
G1 Controller
  ├─ G1: High-Fidelity Table Reproduction (表の完全再現)
  ├─ G3: Semantic Block Arrangement (ブロック整頓)
  └─ G5: Noise Elimination (ノイズ除去)
```

### G1: Controller

**ファイル**: `shared/pipeline/stage_g/g1_controller.py`

**役割**: Stage Gのオーケストレーター

**処理順序**: G1 Reproducer → G3 Arranger → G5 Eliminator

### G1: High-Fidelity Table Reproduction

**ファイル**: `shared/pipeline/stage_g/g1_table_reproducer.py`

**役割**: Markdown表を Pure JSON（headers[], rows[][]）に変換

**処理**:
1. Markdown表をパース
2. ヘッダー行を抽出 → `headers[]`
3. データ行を抽出 → `rows[][]`

**変換例**:

**入力（Markdown）**:
```markdown
| 科目 | 点数 |
|------|------|
| 国語 | 85   |
| 数学 | 92   |
```

**出力（JSON）**:
```json
{
  "table_id": "T1",
  "type": "ui_table",
  "columns": ["科目", "点数"],
  "data": [
    ["国語", "85"],
    ["数学", "92"]
  ],
  "row_count": 2,
  "col_count": 2
}
```

### G3: Semantic Block Arrangement

**ファイル**: `shared/pipeline/stage_g/g3_block_arranger.py`

**役割**: 意味的なブロック単位に整頓

**ブロックタイプ**:
- `text`: 通常のテキスト
- `heading`: 見出し
- `notice`: 注意事項
- `events`: イベント・予定
- `tasks`: タスク・やること

**処理**:
1. テキストを意味単位でブロック化
2. type を付与
3. display_order でソート

**出力**:
```json
{
  "sections": [
    {
      "type": "heading",
      "label": "今週の予定",
      "display_order": 1
    },
    {
      "type": "events",
      "items": [...],
      "display_order": 2
    }
  ]
}
```

### G5: Noise Elimination

**ファイル**: `shared/pipeline/stage_g/g5_noise_eliminator.py`

**役割**: ノイズ（AI推論過程、座標、システムログ）を完全除去

**削除対象**:
- AI推論過程（"I think...", "Based on...", etc.）
- 座標データ（bbox, normalized_bbox, etc.）
- システムログ（timestamps, debug info, etc.）
- 中間処理データ（raw_tokens, physical_chars, etc.）

**保持対象**:
- 表示に必要な「正解データ」のみ
  - sections
  - tables
  - timeline
  - actions
  - notices

**出力（ui_data）**:
```json
{
  "document_info": {
    "document_type": "学校プリント",
    "year_context": 2025
  },
  "sections": [...],
  "tables": [...],
  "timeline": [
    {"date": "2025-01-12", "event": "保護者会"}
  ],
  "actions": [
    {"item": "宿題提出", "deadline": "2025-01-15"}
  ],
  "notices": [
    {"category": "持ち物", "content": "筆記用具"}
  ],
  "metadata": {
    "section_count": 5,
    "table_count": 2,
    "event_count": 3,
    "task_count": 2,
    "notice_count": 1
  }
}
```

---

## Integration: DB & API Connection

### 概要

Stage Gの ui_data を PostgreSQL (JSONB) に保存し、REST API で提供する。

### DB保存

**ファイル**: `services/doc-review/services/document_service.py`

**関数**: `update_stage_g_result()`

**処理**:
```python
def update_stage_g_result(
    db_client,
    document_id: str,
    ui_data: Dict[str, Any]
) -> bool:
    response = db_client.client.table('Rawdata_FILE_AND_MAIL').update({
        'stage_g_structured_data': ui_data,
        'processing_status': 'completed'
    }).eq('id', document_id).execute()

    return response.data is not None
```

**テーブル**: `Rawdata_FILE_AND_MAIL`

**カラム**:
- `stage_g_structured_data` (JSONB): Stage G の ui_data
- `processing_status` (TEXT): 処理ステータス（'completed', 'pending', etc.）

### API提供

**ファイル**: `services/doc-review/blueprints/api.py`

**エンドポイント**: `GET /documents/<doc_id>`

**レスポンス**:
```json
{
  "id": "doc_123",
  "file_name": "学校プリント.pdf",
  "doc_type": "SCHOOL",
  "processing_status": "completed",
  "stage_g_structured_data": {
    "document_info": {...},
    "sections": [...],
    "tables": [...],
    "timeline": [...],
    "actions": [...],
    "notices": [...]
  }
}
```

### テストスクリプト

**ファイル**: `scripts/debug/test_stage_g.py`

**使用例**:
```bash
# Stage G を実行してDB保存
python scripts/debug/test_stage_g.py stage_f_result.json \
  --save-to-db \
  --document-id doc_123
```

**オプション**:
- `--save-to-db`: DB に保存する
- `--document-id`: ドキュメントID
- `--output`: 出力ディレクトリ

---

## Debug Pipeline

### 概要

全パイプライン（A→B→D→E→F→G）を実行し、各ステージの結果をローカルに保存するデバッグツール。

### ファイル

**スクリプト**: `scripts/debug/run_debug_pipeline.py`
**ドキュメント**: `scripts/debug/README_PIPELINE.md`

### 機能

1. **全ステージ統合実行**: A→B→D→E→F→G
2. **キャッシュ機能**: 2回目以降は変更されたステージのみ実行
3. **ステージ範囲指定**: `--start D --end G` で特定範囲のみ実行
4. **タグ付きバージョン管理**: `--tag "v2_test"` で複数バージョンを保存・比較
5. **自動バックアップ**: 既存ファイルを `.json.bak` として退避
6. **numpy型安全対応**: numpy配列を自動的にリストに変換

### 使用例

#### 全行程を実行
```bash
python scripts/debug/run_debug_pipeline.py test001 --pdf document.pdf
```

#### Stage Eだけを再実行
```bash
python scripts/debug/run_debug_pipeline.py test001 --stage E --force
```

#### Stage DからGまで実行
```bash
python scripts/debug/run_debug_pipeline.py test001 --start D --end G --force
```

#### バージョン比較
```bash
# バージョン1
python scripts/debug/run_debug_pipeline.py test001 --stage E --tag "v1_baseline" --force

# バージョン2
python scripts/debug/run_debug_pipeline.py test001 --stage E --tag "v2_improved" --force

# 差分確認
diff debug_output/test001/test001_stage_e_v1_baseline.json \
     debug_output/test001/test001_stage_e_v2_improved.json
```

### 出力ファイル

```
debug_output/test001/
├── test001_stage_a.json         # Stage A 結果
├── test001_stage_b.json         # Stage B 結果
├── test001_stage_d.json         # Stage D 結果
├── test001_stage_e.json         # Stage E 結果
├── test001_stage_f.json         # Stage F 結果
├── test001_stage_g.json         # Stage G 結果
└── test001_ui_data.json         # UI用データ（クリーン版）
```

### オプション一覧

| オプション | 説明 | 例 |
|-----------|------|-----|
| `uuid` | 処理対象のUUID（任意の識別子） | `test001` |
| `--pdf` | PDFファイルパス | `--pdf document.pdf` |
| `--stage` | 対象ステージ（A/B/D/E/F/G） | `--stage E` |
| `--start` | 開始ステージ | `--start D` |
| `--end` | 終了ステージ | `--end G` |
| `--mode` | 実行モード（all/only/from） | `--mode all` |
| `--force` | キャッシュを無視して強制実行 | `--force` |
| `--tag` | 結果ファイルにタグを付ける | `--tag "v2_test"` |
| `--output-dir` | 出力ディレクトリ | `--output-dir my_output` |

---

## ファイル構造

```
document-management-system/
├── shared/
│   └── pipeline/
│       ├── stage_a/
│       │   ├── __init__.py
│       │   ├── a3_entry_point.py         # Entry Point
│       │   ├── a5_type_analyzer.py       # 書類種別判定
│       │   └── a6_dimension_measurer.py  # サイズ測定
│       ├── stage_b/
│       │   ├── __init__.py
│       │   ├── b1_controller.py          # Controller
│       │   ├── b3_pdf_word.py            # PDF-Word
│       │   ├── b4_pdf_excel.py           # PDF-Excel
│       │   ├── b5_pdf_ppt.py             # PDF-PowerPoint
│       │   ├── b6_native_word.py         # Native Word
│       │   ├── b7_native_excel.py        # Native Excel
│       │   ├── b8_native_ppt.py          # Native PowerPoint
│       │   ├── b10_dtp.py                # DTP
│       │   ├── b14_goodnotes_processor.py # Goodnotes
│       │   ├── b42_multicolumn_report.py # Multi-Column
│       │   └── b90_layer_purge.py        # Layer Purge
│       ├── stage_d/
│       │   ├── __init__.py
│       │   ├── d1_controller.py          # Controller
│       │   ├── d3_vector_line_extractor.py # Vector Line
│       │   ├── d5_raster_line_detector.py  # Raster Line
│       │   ├── d8_grid_analyzer.py       # Grid Analyzer
│       │   ├── d9_cell_identifier.py     # Cell Identifier
│       │   └── d10_image_slicer.py       # Image Slicer
│       ├── stage_e/
│       │   ├── __init__.py
│       │   ├── e1_controller.py          # Controller
│       │   ├── e1_ocr_scouter.py         # OCR Scouter
│       │   ├── e5_text_block_visualizer.py # Block Visualizer
│       │   ├── e20_context_extractor.py  # Context Extractor
│       │   └── e30_table_structure_extractor.py # Table Extractor
│       ├── stage_f/
│       │   ├── __init__.py
│       │   ├── f1_controller.py          # Controller
│       │   ├── f1_data_fusion_merger.py  # Data Fusion
│       │   ├── f3_smart_date_normalizer.py # Date Normalizer
│       │   └── f5_logical_table_joiner.py  # Table Joiner
│       └── stage_g/
│           ├── __init__.py
│           ├── g1_controller.py          # Controller
│           ├── g1_table_reproducer.py    # Table Reproducer
│           ├── g3_block_arranger.py      # Block Arranger
│           └── g5_noise_eliminator.py    # Noise Eliminator
├── services/
│   └── doc-review/
│       ├── blueprints/
│       │   └── api.py                    # REST API
│       └── services/
│           └── document_service.py       # DB保存ロジック
├── scripts/
│   └── debug/
│       ├── run_debug_pipeline.py         # 統合デバッグパイプライン
│       ├── test_stage_g.py               # Stage Gテスト
│       └── README_PIPELINE.md            # デバッグツール使い方
└── docs/
    └── PIPELINE_ARCHITECTURE.md          # 本ドキュメント
```

---

## 開発ガイド

### 環境セットアップ

#### 必要なパッケージ

```bash
# PDF処理
pip install pdfplumber pymupdf

# 画像処理
pip install opencv-python pillow pdf2image

# OCR
pip install pytesseract easyocr

# AI
pip install google-generativeai

# ドキュメント処理
pip install python-docx openpyxl python-pptx

# ユーティリティ
pip install loguru numpy
```

#### Tesseract（OCR）のインストール

**Windows**:
```bash
# Chocolatey でインストール
choco install tesseract

# または公式インストーラーをダウンロード
# https://github.com/UB-Mannheim/tesseract/wiki
```

**macOS**:
```bash
brew install tesseract
```

**Linux**:
```bash
sudo apt-get install tesseract-ocr
```

### 新しいステージの追加

1. **ステージディレクトリを作成**
   ```bash
   mkdir shared/pipeline/stage_x
   ```

2. **コントローラーを実装**
   ```python
   # shared/pipeline/stage_x/x1_controller.py
   class X1Controller:
       def process(self, input_data):
           # 処理を実装
           return result
   ```

3. **__init__.py でエクスポート**
   ```python
   # shared/pipeline/stage_x/__init__.py
   from .x1_controller import X1Controller

   __all__ = ['X1Controller']
   ```

4. **デバッグパイプラインに追加**
   ```python
   # scripts/debug/run_debug_pipeline.py
   STAGES = ["A", "B", "D", "E", "F", "G", "X"]  # Xを追加

   # インポート追加
   from shared.pipeline.stage_x import X1Controller

   # インスタンス化
   self._stage_x = X1Controller()

   # パイプライン処理に追加
   stage = "X"
   stage_x_data = self.load_stage(stage)
   if self.should_run(...):
       stage_x_result = self._stage_x.process(...)
   ```

### コーディング規約

#### ファイル命名
- コントローラー: `{stage_number}1_controller.py`
- サブプロセッサ: `{stage_number}{sub_number}_{name}.py`
- 例: `d1_controller.py`, `d3_vector_line_extractor.py`

#### クラス命名
- コントローラー: `{Stage}{Number}Controller`
- サブプロセッサ: `{Stage}{Number}{Name}`
- 例: `D1Controller`, `D3VectorLineExtractor`

#### メソッド命名
- パブリックメソッド: `process()`, `analyze()`, `extract()`
- プライベートメソッド: `_extract_lines()`, `_detect_grid()`

#### ログ出力
```python
from loguru import logger

logger.info(f"[Stage X] 処理開始: {file_name}")
logger.warning(f"[Stage X] 警告: {message}")
logger.error(f"[Stage X] エラー: {error}", exc_info=True)
```

#### 出力形式
全てのステージは Dict[str, Any] を返す:
```python
return {
    'success': True,
    'data': {...},
    'metadata': {...}
}
```

### テスト

#### ユニットテスト
```bash
pytest tests/pipeline/test_stage_a.py
```

#### 統合テスト（デバッグパイプライン）
```bash
python scripts/debug/run_debug_pipeline.py test001 --pdf sample.pdf
```

#### 特定ステージのテスト
```bash
python scripts/debug/run_debug_pipeline.py test001 --stage E --force
```

### トラブルシューティング

#### PDFが読めない
- pdfplumber, PyMuPDF の両方を試す
- パスワード保護を確認
- 破損していないか確認

#### 罫線検出が失敗
- D3（ベクトル）と D5（ラスター）の両方を確認
- 閾値パラメータを調整
- 画像解像度を確認（150dpi推奨）

#### Gemini APIエラー
- APIキーを確認
- レート制限を確認
- プロンプトサイズを確認（画像+テキスト）

#### キャッシュが破損
```bash
# キャッシュを削除
rm -rf debug_output/test001

# 再実行
python scripts/debug/run_debug_pipeline.py test001 --pdf document.pdf
```

---

## まとめ

### アーキテクチャの特徴

1. **マルチソース抽出**: デジタル（Stage B）+ Vision（Stage E）のハイブリッド
2. **高精度表解析**: ベクトル + ラスター罫線の統合（Stage D）
3. **AI構造化**: Gemini 2.5による文脈理解（Stage E）
4. **UI最適化**: ノイズレス・即時描画可能なJSON（Stage G）
5. **デバッグ効率**: 全ステージ統合デバッグツール

### パイプライン全体の流れ（要約）

```
PDF
 → [A] 書類種別判定
 → [B] デジタルテキスト抽出 + テキスト層削除
 → [D] 表構造解析（罫線検出）
 → [E] Vision抽出 + AI構造化
 → [F] データ融合 + 日付正規化
 → [G] UI最適化
 → [DB] JSONB保存
 → [API] REST APIで提供
 → [Frontend] 即座に描画
```

### 次のステップ

1. **フロントエンド実装**: ui_data を Vue.js で描画
2. **バッチ処理**: 複数ファイルの一括処理
3. **リアルタイム処理**: ファイルアップロード直後に自動実行
4. **検索機能**: JSONB フルテキスト検索
5. **通知機能**: 期限付きタスクのリマインダー

---

**ドキュメント作成日**: 2026-02-10
**作成者**: Document Management System Team
**バージョン**: 2.0
