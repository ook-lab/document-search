# ドキュメント管理システム

Supabase + マルチAIモデル（Gemini, OpenAI）を使用した、統合ドキュメント処理・検索システムです。

## 概要

Google Drive/Gmail/Classroomから取得したドキュメント（PDF、画像、テキスト）を7つのステージ（E→K）で処理し、ベクトル検索可能な形式でSupabaseに保存します。

**主な機能:**
- **マルチソース対応**: Google Drive, Gmail, Classroom からの自動取り込み
- **統合処理パイプライン**: Stage E（前処理）→ K（ベクトル化）の7段階処理
- **ベクトル検索**: OpenAI Embeddings + Supabase pgvector による高精度検索
- **AI回答生成**: Gemini 2.5 Flash による自然な回答
- **柔軟な設定**: ドキュメントタイプ・ワークスペースごとにモデル・プロンプトを切り替え

---

## システム構成

```
┌─────────────────┐
│  データソース    │
│ Drive/Gmail/    │
│  Classroom      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  services/data-ingestion    │  ← データ取り込み
│  (監視・取得)    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ G_unified_      │  ← 統合処理パイプライン
│  pipeline       │     Stage E-K（7段階）
│  (処理・保存)    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Supabase      │  ← データベース
│  (pgvector)     │     - Rawdata_FILE_AND_MAIL
│                 │     - search_index
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  services/doc-search    │  ← 検索・回答API
│  (Flask)        │
└─────────────────┘
```

---

## 前提条件

### 必要な環境

- Python 3.12+
- Supabase アカウント
- Google Cloud プロジェクト（Drive/Gmail/Classroom API有効化）
- OpenAI API キー
- Google AI Studio API キー

### 環境変数

`.env` ファイルに以下を設定：

```bash
# Supabase
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_service_role_key

# OpenAI
OPENAI_API_KEY=your_openai_api_key

# Google AI (Gemini)
GOOGLE_API_KEY=your_gemini_api_key

# Google Drive/Gmail/Classroom
GOOGLE_CREDENTIALS_PATH=_runtime/credentials/google_credentials.json
```

---

## セットアップ手順

### 1. リポジトリの準備

```bash
cd /path/to/document_management_system
```

### 2. 仮想環境の作成とアクティベート

```bash
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# venv\Scripts\activate  # Windows
```

### 3. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

### 4. Supabase データベースのセットアップ

**重要:** データベーススキーマは Supabase で直接作成されています。新規環境の場合、[SQL_REFERENCE.md](SQL_REFERENCE.md) を参照してください。

既存環境からのマイグレーション、または追加設定が必要な場合、Supabase SQL Editor で以下を実行：

```bash
# 1. pgvector 拡張機能（必須）
database/migrations/enable_pgvector.sql

# 2. Stage 出力カラムの追加（必要に応じて）
migrations/add_stage_output_columns.sql

# 3. 検索関数
J_resources/sql/add_match_documents_function.sql

# 4. オプション: チラシ/家計簿機能
database/migrations/create_flyer_schema.sql  # チラシ機能
shared/kakeibo/schema.sql                         # 家計簿機能
```

詳細な手順と全SQLファイルの説明は [SQL_REFERENCE.md](SQL_REFERENCE.md) を参照。

**主要テーブル:**
- `Rawdata_FILE_AND_MAIL`: ドキュメントのメタデータとStage E-K処理結果
- `search_index`: 検索用チャンクとベクトル埋め込み（1536次元）
- `Rawdata_RECEIPT_*`, `Rawdata_FLYER_*`, `Rawdata_NETSUPER_*`: サブシステム用テーブル

### 5. Google認証情報の設定

1. Google Cloud Console でプロジェクトを作成
2. Drive API, Gmail API, Classroom API を有効化
3. サービスアカウントを作成し、JSONキーをダウンロード
4. `_runtime/credentials/google_credentials.json` に配置

```bash
mkdir -p _runtime/credentials
mv ~/Downloads/your-credentials.json _runtime/credentials/google_credentials.json
```

---

## 使い方

### ドキュメントの処理

#### 方法1: 自動監視（推奨）

Google Driveの特定フォルダを監視し、新規ファイルを自動処理：

```bash
python services/data-ingestion/monitoring/inbox_monitor.py
```

設定: `services/data-ingestion/monitoring/config.yaml`

#### 方法2: 手動処理

特定のドキュメントIDを指定して処理：

```bash
python process_specific_docs.py
```

`process_specific_docs.py` の `doc_ids` リストを編集：

```python
doc_ids = [
    'your-document-id-1',
    'your-document-id-2'
]
```

#### 方法3: キュー処理

`processing_status='pending'` のドキュメントを一括処理：

```bash
python process_queued_documents.py --limit 10
```

### 統合パイプライン（Stage E-K）

7つのステージで順次処理：

```
E: 前処理 → F: Vision解析 → G: テキスト整形 →
H: 構造化 → I: 統合・要約 → J: チャンク化 → K: ベクトル化
```

**各ステージの出力はDBに保存:**
- `stage_e1_text` ~ `stage_e5_text`: 前処理（5エンジン）
- `stage_f_text_ocr`, `stage_f_layout_ocr`, `stage_f_visual_elements`: Vision解析
- `stage_h_normalized`: 構造化入力テキスト
- `stage_i_structured`: 構造化データ（JSON）
- `stage_j_chunks_json`: チャンク（JSON）

詳細は [ARCHITECTURE.md](ARCHITECTURE.md) 参照。

### 検索・回答API

Flask APIサーバーを起動：

```bash
cd services/doc-search
python app.py
```

ブラウザで http://localhost:5001 にアクセス。

**APIエンドポイント:**
- `POST /api/search` - ベクトル検索
- `POST /api/answer` - AI回答生成
- `GET /api/health` - ヘルスチェック

---

## 設定ファイル

### config/models.yaml

各ステージで使用するAIモデルを定義：

```yaml
models:
  stage_f:
    default: "gemini-2.5-flash"
    flyer: "gemini-2.5-pro"       # チラシは視覚理解重視
    classroom: "gemini-2.5-flash" # Classroomはコスト重視
```

### config/pipeline_routing.yaml

workspace と doc_type に基づいてルーティング：

```yaml
routing:
  by_workspace:
    ikuya_classroom:
      schema: "classroom"
      stages:
        stage_f:
          prompt_key: "classroom"
          model_key: "classroom"
```

### config/prompts.yaml

全ステージのプロンプトを一元管理（15個のMDファイルを統合）：

```yaml
prompts:
  stage_f:
    classroom: |
      あなたはGoogle Classroom課題ドキュメントの...
    default: |
      あなたはドキュメントから視覚情報を...
```

---

## プロジェクト構成

```
document_management_system/
├── shared/common/                     # 共通モジュール
│   ├── database/                # Supabaseクライアント
│   ├── processors/              # PDF/Office処理
│   ├── connectors/              # Drive/Gmail/Classroom
│   └── processing/              # チャンク処理
│
├── services/data-ingestion/                  # データ取り込み
│   ├── gmail/                   # Gmail取り込み
│   ├── google_drive/            # Drive取り込み
│   ├── google_classroom/        # Classroom取り込み
│   └── monitoring/              # 監視スクリプト
│
├── shared/ai/                  # AI共通機能
│   ├── llm_client/              # LLMクライアント
│   └── embeddings/              # ベクトル埋め込み
│
├── shared/pipeline/          # 統合処理パイプライン
│   ├── stage_e_preprocessing.py   # Stage E: 前処理
│   ├── stage_f_visual.py          # Stage F: Vision解析
│   ├── stage_g_formatting.py      # Stage G: テキスト整形
│   ├── stage_h_structuring.py     # Stage H: 構造化
│   ├── stage_i_synthesis.py       # Stage I: 統合・要約
│   ├── stage_j_chunking.py        # Stage J: チャンク化
│   ├── stage_k_embedding.py       # Stage K: ベクトル化
│   ├── pipeline.py                # パイプライン本体
│   ├── config_loader.py           # 設定ローダー
│   └── config/                    # 設定ファイル
│       ├── models.yaml           # モデル定義
│       ├── pipeline_routing.yaml # ルーティング設定
│       └── prompts.yaml          # プロンプト（統合版）
│
├── services/doc-search/                  # Flask API
│   ├── app.py                   # メインアプリ
│   ├── templates/               # HTMLテンプレート
│   └── requirements.txt
│
├── migrations/                   # DBマイグレーション
│   └── add_stage_output_columns.sql
│
├── .env                         # 環境変数
├── README.md                    # このファイル
└── ARCHITECTURE.md              # 技術詳細
```

---

## トラブルシューティング

### プロンプトが見つからない

**症状:** `プロンプトが見つかりません: stage_f/classroom`

**原因:** prompts.yaml が読み込まれていない

**対処:**
```bash
# prompts.yaml の存在確認
ls shared/pipeline/config/prompts.yaml

# ConfigLoader のログを確認
# "✅ prompts.yaml を読み込みました" が表示されるはず
```

### ドキュメントが消失する

**症状:** 処理後にドキュメントが消える

**原因:** 古いバージョンの DELETE→INSERT 処理（修正済み）

**対処:** 最新版では UPDATE を使用しているため、この問題は発生しません

### Stage出力が空

**症状:** `stage_e1_text` などが NULL

**原因:** 古いバージョンのpipeline.py

**対処:** 最新版では全ステージ出力をDBに保存します（pipeline.py 378-388行目）

### Gemini API エラー

**症状:** `GOOGLE_API_KEY not found`

**対処:**
```bash
# .env ファイルを確認
cat .env | grep GOOGLE_API_KEY

# 環境変数が読み込まれているか確認
python -c "import os; print(os.getenv('GOOGLE_API_KEY'))"
```

---

## 技術スタック

- **Python**: 3.12+
- **データベース**: Supabase (PostgreSQL + pgvector)
- **AI/ML**:
  - Gemini 2.5 Flash/Pro (Vision解析・構造化・回答生成)
  - OpenAI text-embedding-3-small (1536次元ベクトル)
- **ベクトル検索**: pgvector (cosine類似度)
- **Web API**: Flask 3.0
- **ファイル処理**: PyPDF2, pdfplumber, python-docx, openpyxl
- **外部連携**: Google Drive API, Gmail API, Classroom API

---

## セキュリティ注意事項

- `.env` ファイルを `.gitignore` に追加
- `google_credentials.json` を公開リポジトリにコミットしない
- 本番環境では `debug=False` に設定
- Supabase の Service Role Key は慎重に管理

---

## サポート

詳細な技術情報は [ARCHITECTURE.md](ARCHITECTURE.md) を参照してください。

問題が発生した場合：
1. ログを確認（`logs/` ディレクトリ）
2. 環境変数の設定を確認
3. Supabase のテーブル構造を確認
4. 最新版にアップデート

