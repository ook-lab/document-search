# Document Management System

## This project is NOT a runnable web app.

- Running `python app.py` does **NOT** execute any document processing.
- Web APIs are **read-only / enqueue-only** and NEVER perform processing.
- All processing is executed **exclusively by the Worker** via DB instructions.

---

## Architecture（処理は Worker のみ）

```
┌─────────────────────────────────────────────────────────┐
│                      Supabase DB                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │ops_requests │  │run_executions│  │10_ix_search_index│ │
│  │ (入力)      │  │ (実行記録)   │  │ (検索用)         │ │
│  └──────┬──────┘  └──────▲──────┘  └────────▲────────┘  │
└─────────┼───────────────┼──────────────────┼───────────┘
          │               │                  │
          ▼               │                  │
┌─────────────────┐       │                  │
│   Worker CLI    │───────┴──────────────────┘
│ （唯一の処理者） │
└─────────────────┘

┌─────────────────┐
│ Search API      │   ← 検索専用。処理しない。
│ (read-only)     │
└─────────────────┘
```

**Rule**: Web = enqueue/search only. Worker = processing only.

---

## Operational Flow（処理は起動しない）

### Step 1: DB にリクエスト投入

```sql
INSERT INTO ops_requests (request_type, parameters, status)
VALUES ('process_document', '{"document_id": "xxx"}', 'pending');
```

### Step 2: Worker 実行

```bash
python scripts/ops.py requests --apply   # リクエスト適用
python -m scripts.processing.worker      # Worker 実行
```

### Step 3: 結果確認

```sql
SELECT * FROM run_executions ORDER BY created_at DESC LIMIT 10;
SELECT COUNT(*) FROM "10_ix_search_index";
```

---

## 運用管理（ops.py）

```bash
python scripts/ops.py stats                          # 統計
python scripts/ops.py stop                           # 停止要求
python scripts/ops.py release-lease --workspace X   # リース解放
python scripts/ops.py requests --apply              # 要求適用
```

---

## Search API（検索専用 - 処理しない）

```bash
python services/doc-search/app.py
```

http://localhost:5001 で検索 UI を提供。

> **⚠️ これを起動しても処理は実行されない。Worker を動かさない限り何も処理されない。**

---

## 環境変数

### 必須

```bash
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_KEY=your_service_role_key
GOOGLE_API_KEY=your_gemini_api_key
```

### 任意

```bash
OPENAI_API_KEY=your_openai_api_key  # Embeddings用
```

---

## Setup

```bash
pip install -r requirements.txt
supabase db push  # マイグレーション適用
```

仮想環境は任意。

---

## Project Structure

```
├── scripts/
│   ├── ops.py                    # 運用管理CLI（SSOT）
│   └── processing/
│       └── worker.py             # Worker（処理実行）
│
├── services/
│   └── doc-search/               # Search API（検索専用）
│
├── shared/
│   └── pipeline/                 # Stage E-K
│
└── supabase/
    └── migrations/               # DB定義（SSOT）
```

---

## Pipeline（Stage E-K）

```
E: 前処理 → F: Vision → G: 整形 → H: 構造化 → I: 統合 → J: チャンク → K: ベクトル
```

Worker が DB の pending ドキュメントを取得し、Stage E-K を実行。

---

## Troubleshooting

| 症状 | 原因 | 対処 |
|---|---|---|
| Worker が動かない | SUPABASE_KEY が anon key | Service Role Key に変更 |
| 検索結果 0件 | search_index が空 | Worker 実行を確認 |
| API エラー | GOOGLE_API_KEY 未設定 | .env を確認 |

---

## Security

- Service Role Key は厳重管理
- `.env` は `.gitignore` に追加
- 本番は `debug=False`
