# システムアーキテクチャ

統合ドキュメント処理・検索システムの技術詳細

---

## 目次

1. [システム全体像](#システム全体像)
2. [統合パイプライン（Stage E-K）](#統合パイプラインstage-e-k)
3. [Config-based設計](#config-based設計)
4. [データベーススキーマ](#データベーススキーマ)
5. [AI/MLモデル構成](#aimlモデル構成)
6. [コード構造](#コード構造)

---

## システム全体像

### アーキテクチャ図

```
┌──────────────────────────────────────────────────────────┐
│                  データソース層                           │
│  Google Drive │ Gmail │ Google Classroom │ ローカル      │
└────────────┬─────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────┐
│            services/data-ingestion (データ取り込み層)                  │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐         │
│  │   Drive    │  │   Gmail    │  │ Classroom  │         │
│  │ Connector  │  │ Connector  │  │ Connector  │         │
│  └────────────┘  └────────────┘  └────────────┘         │
│                                                          │
│  監視スクリプト: inbox_monitor.py                         │
└────────────┬─────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────┐
│         shared/pipeline (処理パイプライン層)            │
│                                                          │
│  Stage E → F → G → H → I → J → K                        │
│  (前処理～ベクトル化までの7段階)                           │
│                                                          │
│  ConfigLoader: models.yaml, prompts.yaml読み込み         │
└────────────┬─────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────┐
│                   データ永続化層                          │
│  Supabase (PostgreSQL + pgvector)                       │
│  ┌─────────────────────┐  ┌─────────────────────┐      │
│  │ Rawdata_FILE_AND_MAIL│  │   search_index      │      │
│  │ (メタデータ+Stage出力)│  │ (チャンク+ベクトル)  │      │
│  └─────────────────────┘  └─────────────────────┘      │
└────────────┬─────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────┐
│              services/doc-search (API層)                         │
│  Flask 3.0 REST API                                     │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐       │
│  │ /api/search│  │ /api/answer│  │ /api/health│       │
│  └────────────┘  └────────────┘  └────────────┘       │
└──────────────────────────────────────────────────────────┘
```

---

## 統合パイプライン（Stage E-K）

### 処理フロー

```
┌─────────────────────────────────────────────────────────┐
│  Stage E: Pre-processing (前処理)                        │
│  ─────────────────────────────────────                  │
│  • ファイルMIME判定                                      │
│  • 5つのPDFエンジンで並列抽出                             │
│    E1: PyPDF2, E2: pdfminer, E3: PyMuPDF               │
│    E4: pdfplumber, E5: 統合                            │
│  • 画像ファイルの識別                                    │
│  ─────────────────────────────────────                  │
│  出力: stage_e1_text ~ stage_e5_text                    │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│  Stage F: Visual Analysis (Vision解析)                  │
│  ─────────────────────────────────────                  │
│  • Gemini 2.5 Flash/Pro でOCR                          │
│  • レイアウト情報抽出（見出し、表、リスト）                │
│  • 視覚要素の認識                                        │
│  ─────────────────────────────────────                  │
│  モデル: classroom=Flash, flyer=Pro                     │
│  出力: stage_f_text_ocr, stage_f_layout_ocr,           │
│        stage_f_visual_elements                         │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│  Stage G: Text Formatting (テキスト整形)                 │
│  ─────────────────────────────────────                  │
│  • Gemini 2.5 Flash でMarkdown整形                      │
│  • 不要な空白・改行の除去                                │
│  • 表・リストの構造化                                    │
│  ─────────────────────────────────────                  │
│  モデル: Flash                                          │
│  出力: formatted_text (内部利用)                         │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│  Stage H: Structuring (構造化)                          │
│  ─────────────────────────────────────                  │
│  • Gemini 2.5 Flash で高精度JSON抽出                    │
│  • メタデータ生成（日付、タグ、人物など）                 │
│  • doc_type別スキーマ適用                                │
│  ─────────────────────────────────────                  │
│  モデル: Flash                                          │
│  出力: stage_h_normalized, stage_i_structured           │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│  Stage I: Synthesis (統合・要約)                         │
│  ─────────────────────────────────────────              │
│  • Gemini 2.5 Flash で要約生成                          │
│  • タグの自動生成                                        │
│  • Stage H の結果とマージ                                │
│  ─────────────────────────────────────                  │
│  モデル: Flash                                          │
│  出力: summary, tags (metadataにマージ)                 │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│  Stage J: Chunking (チャンク化)                          │
│  ─────────────────────────────────────                  │
│  • MetadataChunker でチャンク分割                        │
│  • サイズ: 300-1000文字、オーバーラップ: 100文字         │
│  • メタデータ付与                                        │
│  ─────────────────────────────────────                  │
│  モデル: なし（ルールベース）                             │
│  出力: stage_j_chunks_json                              │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│  Stage K: Embedding (ベクトル化)                         │
│  ─────────────────────────────────────                  │
│  • OpenAI text-embedding-3-small でベクトル化           │
│  • search_indexテーブルに保存                            │
│  • 既存チャンクの削除・更新                               │
│  ─────────────────────────────────────                  │
│  モデル: text-embedding-3-small (1536次元)              │
│  出力: search_index テーブル                            │
└─────────────────────────────────────────────────────────┘
```

### 重要な実装詳細

#### 1. ドキュメント更新方式（pipeline.py 398-418行目）

**現在の実装（UPDATE方式）:**

```python
if existing_document_id:
    logger.info(f"[DB更新] 既存ドキュメント更新: {existing_document_id}")
    update_data = {k: v for k, v in doc_data.items() if k != 'id'}
    result = self.db.client.table('Rawdata_FILE_AND_MAIL').update(update_data).eq('id', existing_document_id).execute()
```

**重要:** 以前のDELETE→INSERT方式は**ドキュメント消失のリスク**があったため、UPDATE方式に変更しました。これにより：
- `created_at`が保持される
- エラー時もレコードが残る
- トランザクション的に安全

#### 2. Stage出力の保存（pipeline.py 378-388行目）

全ステージの出力をデータベースに保存：

```python
doc_data = {
    # ... 基本情報 ...

    # Stage E: 前処理（5エンジン）
    'stage_e1_text': sanitized_extracted_text,  # PyPDF2（未実装、E4の値）
    'stage_e2_text': sanitized_extracted_text,  # pdfminer（未実装、E4の値）
    'stage_e3_text': sanitized_extracted_text,  # PyMuPDF（未実装、E4の値）
    'stage_e4_text': sanitized_extracted_text,  # pdfplumber/画像OCR
    'stage_e5_text': sanitized_extracted_text,  # 最終統合（現在はE4と同じ）

    # Stage F: Vision解析
    'stage_f_text_ocr': stage_f_text_ocr,
    'stage_f_layout_ocr': stage_f_layout_ocr,
    'stage_f_visual_elements': stage_f_visual_elements,

    # Stage H, I, J
    'stage_h_normalized': sanitized_combined_text,
    'stage_i_structured': json.dumps(stageH_result, ensure_ascii=False, indent=2),
    'stage_j_chunks_json': json.dumps(chunks, ensure_ascii=False, indent=2)
}
```

**目的:**
- デバッグ時に各ステージの出力を確認可能
- ステージ間の品質チェック
- 将来的なステージの改善に活用

---

## Config-based設計

### 設計思想

コードとプロンプト・モデル設定を分離し、**コード変更なしで動作を調整可能**にする。

### 3つの設定ファイル

#### 1. models.yaml

各ステージで使用するAIモデルを定義。

**ファイルパス:** `shared/pipeline/config/models.yaml`

```yaml
models:
  # Stage F: Visual Analysis (視覚解析)
  # flyer: 商品写真の視覚理解が重要なため Pro を使用
  # classroom: 日常的なお知らせの正確なOCRが目的のため Flash で十分
  stage_f:
    default: "gemini-2.5-flash"
    flyer: "gemini-2.5-pro"
    classroom: "gemini-2.5-flash"
    flash_lite: "gemini-2.5-flash-lite"  # 家計簿用（最軽量）

  # Stage G: Text Formatting (書式整形)
  stage_g:
    default: "gemini-2.5-flash"
    flyer: "gemini-2.5-pro"
    classroom: "gemini-2.5-flash"
    flash_lite: "gemini-2.5-flash-lite"

  # Stage H: Structuring (構造化)
  stage_h:
    default: "gemini-2.5-flash"
    flyer: "gemini-2.5-flash"
    classroom: "gemini-2.5-flash"
    flash_lite: "gemini-2.5-flash-lite"

  # Stage I: Synthesis (統合・要約)
  stage_i:
    default: "gemini-2.5-flash"
    flyer: "gemini-2.5-flash"
    classroom: "gemini-2.5-flash"

  # Stage K: Embedding (ベクトル化)
  stage_k:
    default: "text-embedding-3-small"
    flyer: "text-embedding-3-small"
    classroom: "text-embedding-3-small"
```

**特徴:**
- `flyer` だけ Stage F で Pro を使用（商品写真の視覚理解が重要）
- `classroom` は Flash（コスト効率重視）
- 将来的に新しいモデル（Gemini 2.5 Pro など）への切り替えが容易

#### 2. pipeline_routing.yaml

workspace と doc_type に基づいてプロンプト・モデルをルーティング。

**ファイルパス:** `shared/pipeline/config/pipeline_routing.yaml`

```yaml
routing:
  # workspace ベースのルート（優先順位1）
  by_workspace:
    ikuya_classroom:
      description: "育哉のClassroomワークスペース"
      schema: "classroom"
      stages:
        stage_f:
          prompt_key: "classroom"
          model_key: "classroom"
        stage_h:
          prompt_key: "classroom"
          model_key: "classroom"

  # doc_type ベースのルート（優先順位2）
  by_doc_type:
    physical_shop:
      description: "実店舗のチラシ"
      schema: "flyer"
      stages:
        stage_f:
          prompt_key: "flyer"
          model_key: "flyer"
```

**ルーティング優先順位:**
1. workspace（最優先）
2. doc_type
3. default（フォールバック）

#### 3. prompts.yaml

全ステージのプロンプトを一元管理（**15個のMDファイルを統合**）。

**ファイルパス:** `shared/pipeline/config/prompts.yaml`

**構造:**

```yaml
prompts:
  stage_f:
    classroom: |
      あなたはGoogle Classroom課題ドキュメントの視覚解析を専門とするAIアシスタントです。

      Stage E で抽出したテキストを基準として、画像を詳細に見て、完璧な3つの情報を作成してください。
      ...

    default: |
      あなたはドキュメントから視覚情報を抽出する専門家です。
      ...

    flyer: |
      あなたはスーパーマーケットのチラシから視覚情報を抽出する専門家です。
      ...

  stage_g:
    classroom: |
      ...

  stage_h:
    classroom: |
      ...
```

**特徴:**
- **統合前:** 15個の個別MDファイル（`prompts/stage_f/stage_f_classroom.md` など）
- **統合後:** 1つのYAMLファイルに集約
- YAMLのリテラルブロックスタイル（`|`）で複数行テキストを保持
- config_loader.py で自動読み込み

**読み込み処理（config_loader.py 39-47行目）:**

```python
# プロンプト設定を読み込み
prompts_file = self.config_dir / "prompts.yaml"
if prompts_file.exists():
    prompts_data = self._load_yaml(prompts_file)
    self.prompts_config = prompts_data.get('prompts', {})
    logger.info(f"✅ prompts.yaml を読み込みました")
else:
    self.prompts_config = {}
    logger.warning(f"⚠️ prompts.yaml が見つかりません。MDファイルから読み込みます")
```

**プロンプト取得処理（config_loader.py 101-129行目）:**

```python
def get_prompt(self, stage: str, prompt_key: str) -> str:
    # prompts.yaml から読み込み
    if self.prompts_config and stage in self.prompts_config:
        if prompt_key in self.prompts_config[stage]:
            prompt = self.prompts_config[stage][prompt_key]
            logger.debug(f"プロンプト読み込み: {stage}/{prompt_key} ({len(prompt)}文字)")
            return prompt

    # フォールバック: MDファイルから読み込み（後方互換性）
    prompt_file = self.config_dir / "prompts" / stage / f"{stage}_{prompt_key}.md"
    ...
```

---

## データベーススキーマ

### テーブル構造

#### 1. Rawdata_FILE_AND_MAIL

ドキュメントのメタデータと全ステージの処理結果を保存。

**主要カラム:**

```sql
CREATE TABLE "Rawdata_FILE_AND_MAIL" (
    id UUID PRIMARY KEY,
    file_name TEXT,
    source_id TEXT,
    workspace TEXT,
    doc_type TEXT,

    -- Stage E 出力（5エンジン）
    stage_e1_text TEXT,      -- PyPDF2（未実装、E4の値）
    stage_e2_text TEXT,      -- pdfminer（未実装、E4の値）
    stage_e3_text TEXT,      -- PyMuPDF（未実装、E4の値）
    stage_e4_text TEXT,      -- pdfplumber/画像OCR
    stage_e5_text TEXT,      -- 最終統合

    -- Stage F 出力（Vision解析）
    stage_f_text_ocr TEXT,
    stage_f_layout_ocr TEXT,
    stage_f_visual_elements TEXT,

    -- Stage H, I, J 出力
    stage_h_normalized TEXT,
    stage_i_structured TEXT,
    stage_j_chunks_json JSONB,

    -- メタデータ
    metadata JSONB,
    tags TEXT[],

    -- タイムスタンプ
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

**インデックス:**

```sql
CREATE INDEX idx_workspace ON "Rawdata_FILE_AND_MAIL"(workspace);
CREATE INDEX idx_doc_type ON "Rawdata_FILE_AND_MAIL"(doc_type);
CREATE INDEX idx_tags ON "Rawdata_FILE_AND_MAIL" USING GIN(tags);
```

#### 2. search_index

検索用チャンクとベクトル埋め込みを保存。

```sql
CREATE TABLE search_index (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES "Rawdata_FILE_AND_MAIL"(id) ON DELETE CASCADE,
    chunk_index INTEGER,
    chunk_text TEXT,
    embedding VECTOR(1536),  -- OpenAI text-embedding-3-small
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ベクトル検索用インデックス（IVFFlat）
CREATE INDEX ON search_index USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

**ベクトル検索関数:**

```sql
CREATE OR REPLACE FUNCTION match_documents(
    query_embedding VECTOR(1536),
    match_count INT DEFAULT 5,
    filter JSONB DEFAULT '{}'
) RETURNS TABLE(...) AS $$
    SELECT ...
    FROM search_index
    ORDER BY embedding <=> query_embedding
    LIMIT match_count;
$$ LANGUAGE sql;
```

---

## AI/MLモデル構成

### 使用モデル一覧

| ステージ | デフォルト | flyer | classroom | 用途 |
|---------|-----------|-------|-----------|------|
| Stage F | Gemini 2.5 Flash | **Gemini 2.5 Pro** | Gemini 2.5 Flash | Vision解析 |
| Stage G | Gemini 2.5 Flash | Gemini 2.5 Pro | Gemini 2.5 Flash | テキスト整形 |
| Stage H | Gemini 2.5 Flash | Gemini 2.5 Flash | Gemini 2.5 Flash | 構造化 |
| Stage I | Gemini 2.5 Flash | Gemini 2.5 Flash | Gemini 2.5 Flash | 要約 |
| Stage K | OpenAI text-embedding-3-small | 同左 | 同左 | ベクトル化 |

**モデル選択の理由:**

- **Gemini 2.5 Flash**: 高速・安価、JSON生成に優れる
- **Gemini 2.5 Pro** (flyerのみ): 商品写真の視覚理解が重要
- **OpenAI Embeddings**: 検索精度が高い、pgvectorとの親和性

### コスト最適化

- **classroom**: Flash で十分（テキスト中心のお知らせ）
- **flyer**: Pro を使用（商品写真の詳細な視覚理解が必要）
- 複雑な図表は別パイプラインで処理（この統一パイプラインは日常的なお知らせ処理用）

---

## コード構造

### 主要モジュール

#### 1. UnifiedDocumentPipeline (pipeline.py)

パイプライン全体を統括。

**重要メソッド:**

```python
class UnifiedDocumentPipeline:
    async def process_document(
        self,
        file_path: Path,
        source_id: str,
        file_name: str,
        workspace: str,
        doc_type: str,
        existing_document_id: Optional[str] = None
    ) -> Dict[str, Any]:
        # ConfigLoaderでルート取得
        route_config = self.config_loader.get_route_config(
            doc_type=doc_type,
            workspace=workspace
        )

        # Stage E-K を順次実行
        stage_e_result = self.stage_e.process(...)
        stage_f_result = await self.stage_f.process(...)
        ...

        # UPDATE方式でDB保存（DELETE→INSERT ではない）
        if existing_document_id:
            update_data = {k: v for k, v in doc_data.items() if k != 'id'}
            result = self.db.client.table('Rawdata_FILE_AND_MAIL').update(update_data).eq('id', existing_document_id).execute()
```

#### 2. ConfigLoader (config_loader.py)

YAML設定を読み込み、ルーティング処理。

**重要メソッド:**

```python
class ConfigLoader:
    def __init__(self, config_dir: Optional[Path] = None):
        self.models_config = self._load_yaml(self.config_dir / "models.yaml")
        self.routes_config = self._load_yaml(self.config_dir / "pipeline_routing.yaml")

        # prompts.yaml を読み込み
        prompts_file = self.config_dir / "prompts.yaml"
        if prompts_file.exists():
            prompts_data = self._load_yaml(prompts_file)
            self.prompts_config = prompts_data.get('prompts', {})

    def get_prompt(self, stage: str, prompt_key: str) -> str:
        # prompts.yaml から読み込み
        if self.prompts_config and stage in self.prompts_config:
            if prompt_key in self.prompts_config[stage]:
                return self.prompts_config[stage][prompt_key]

        # フォールバック: MDファイルから読み込み
        ...
```

---

## まとめ

**システムの特徴:**

✅ **マルチソース対応**: Drive/Gmail/Classroom から自動取り込み
✅ **7段階処理**: Stage E-K による高品質な処理
✅ **Config-based設計**: コード変更なしで調整可能
✅ **柔軟なモデル選択**: doc_type/workspace ごとに最適なモデルを使用
✅ **完全なトレーサビリティ**: 全ステージ出力をDBに保存
✅ **安全な更新**: UPDATE方式（DELETE→INSERT ではない）

**再構築に必要な情報:**

このドキュメントと README.md があれば、システム全体を再構築可能です。
- 設定ファイル3つ（models.yaml, pipeline_routing.yaml, prompts.yaml）
- データベーススキーマ（migrations/add_stage_output_columns.sql）
- コード構造（shared/common, shared/pipeline, services/doc-search）
- 認証情報（.env, google_credentials.json）

詳細なセットアップ手順は [README.md](README.md) を参照してください。
