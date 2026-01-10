# SQL リファレンス

## 概要

このドキュメントは、ドキュメント管理システムのデータベーススキーマと重要なSQL設定を説明します。

**重要:** データベーススキーマは Supabase で直接作成されました。新規環境でデータベースを再構築する場合は、Supabaseからスキーマをエクスポートしてください。

---

## データベース構成

### 主要テーブル

#### 1. Rawdata_FILE_AND_MAIL
**用途:** Google Drive/Gmail/Classroom から取得したドキュメントのメタデータと処理結果を保存

**主要カラム:**
- `doc_id` (UUID) - ドキュメントID（主キー）
- `source_type` - ソースタイプ (drive/gmail/classroom)
- `workspace` - ワークスペース (ikuya_classroom/shopping など)
- `doc_type` - ドキュメントタイプ (flyer/classroom など)
- `processing_status` - 処理ステータス (pending/processing/completed/failed)
- `stage_e1_text` ~ `stage_e5_text` - Stage E 前処理結果（5エンジン）
- `stage_f_text_ocr` - Stage F テキストOCR
- `stage_f_layout_ocr` - Stage F レイアウトOCR
- `stage_f_visual_elements` - Stage F 視覚要素（JSON）
- `stage_h_normalized` - Stage H 構造化入力テキスト
- `stage_i_structured` - Stage I 構造化データ（JSON）
- `stage_j_chunks_json` - Stage J チャンク（JSON）
- `created_at`, `updated_at` - タイムスタンプ

#### 2. search_index
**用途:** ドキュメント検索用のチャンクとベクトル埋め込みを保存

**主要カラム:**
- `id` (UUID) - チャンクID（主キー）
- `doc_id` (UUID) - 元ドキュメントID（外部キー → Rawdata_FILE_AND_MAIL）
- `chunk_text` (TEXT) - チャンクテキスト
- `embedding` (vector(1536)) - OpenAI埋め込みベクトル（1536次元）
- `chunk_index` (INTEGER) - チャンク番号
- `chunk_type` (TEXT) - チャンクタイプ
- `created_at` - タイムスタンプ

**インデックス:**
- `embedding` カラムに対して pgvector の ivfflat インデックス（コサイン類似度検索用）

#### 3. Rawdata_RECEIPT_shops
**用途:** レシート店舗情報

**主要カラム:**
- `id` (UUID) - 店舗ID
- `shop_name` - 店舗名
- `purchase_date` - 購入日
- `total_amount` - 合計金額

#### 4. Rawdata_RECEIPT_items
**用途:** レシート商品明細

**主要カラム:**
- `id` (UUID) - 商品ID
- `receipt_id` (UUID) - レシートID（外部キー）
- `product_name` - 商品名
- `quantity` - 数量
- `price` - 価格

#### 5. Rawdata_FLYER_shops
**用途:** チラシ店舗情報

**主要カラム:**
- `id` (UUID) - 店舗ID
- `shop_name` - 店舗名
- `flyer_date` - チラシ日付

#### 6. Rawdata_FLYER_items
**用途:** チラシ商品情報

**主要カラム:**
- `id` (UUID) - 商品ID
- `flyer_id` (UUID) - チラシID（外部キー）
- `product_name` - 商品名
- `price` - 価格
- `discount_rate` - 割引率

#### 7. Rawdata_NETSUPER_items
**用途:** ネットスーパー商品情報

**主要カラム:**
- `id` (UUID) - 商品ID
- `product_name` - 商品名
- `jan_code` - JANコード
- `price` - 価格
- `category` - カテゴリ

---

## 重要なSQL設定ファイル

### 必須ファイル（5ファイル）

#### 1. database/migrations/enable_pgvector.sql
**用途:** pgvector 拡張機能を有効化（ベクトル検索に必須）

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

**実行タイミング:** データベース初期セットアップ時（最初に実行）

#### 2. J_resources/sql/add_match_documents_function.sql
**用途:** ベクトル検索関数 `match_documents()` を作成

**機能:**
- OpenAI埋め込みベクトルとコサイン類似度で検索
- 類似度スコア付きで結果を返す

**実行タイミング:** pgvector有効化後

#### 3. migrations/add_stage_output_columns.sql
**用途:** Stage E-K の処理結果を保存するカラムを Rawdata_FILE_AND_MAIL に追加

**追加カラム:**
- `stage_e1_text` ~ `stage_e5_text` (TEXT) - Stage E 前処理結果（5エンジン）
- `stage_f_text_ocr` (TEXT) - Stage F テキストOCR
- `stage_f_layout_ocr` (TEXT) - Stage F レイアウトOCR
- `stage_f_visual_elements` (JSONB) - Stage F 視覚要素
- `stage_h_normalized` (TEXT) - Stage H 正規化テキスト
- `stage_i_structured` (JSONB) - Stage I 構造化データ
- `stage_j_chunks_json` (JSONB) - Stage J チャンク

**実行タイミング:** Rawdata_FILE_AND_MAIL テーブル作成後

#### 4. database/migrations/create_flyer_schema.sql
**用途:** チラシ関連テーブル（Rawdata_FLYER_shops, Rawdata_FLYER_items）を作成

**実行タイミング:** メインスキーマ作成後（チラシ機能を使用する場合）

#### 5. shared/kakeibo/schema.sql
**用途:** 家計簿システムのテーブルを作成

**実行タイミング:** 家計簿機能を使用する場合

---

## データベースセットアップ手順

### 新規環境構築

1. **pgvector 拡張機能を有効化**
   ```bash
   database/migrations/enable_pgvector.sql
   ```

2. **メインスキーマを作成**
   - Supabase SQL Editor から既存環境のスキーマをエクスポート
   - または Supabase UI でテーブルを手動作成

3. **Stage 出力カラムを追加**
   ```bash
   migrations/add_stage_output_columns.sql
   ```

4. **検索関数を作成**
   ```bash
   J_resources/sql/add_match_documents_function.sql
   ```

5. **オプション: サブシステムのスキーマ**
   - チラシ: `database/migrations/create_flyer_schema.sql`
   - 家計簿: `shared/kakeibo/schema.sql`

### スキーマのエクスポート方法

Supabase から現在のスキーマをエクスポートするには:

```sql
-- すべてのテーブル構造をエクスポート
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position;

-- テーブルのCREATE文を生成（pg_dump を使用）
-- Supabase CLI を使用:
supabase db dump --schema public > schema_export.sql
```

---

## ベクトル検索の仕組み

### 1. 埋め込み生成（Stage K）
```python
from shared/ai.embeddings.openai_embeddings import generate_openai_embedding

text = "検索対象のテキスト"
embedding = generate_openai_embedding(text)  # 1536次元ベクトル
```

### 2. search_index への保存
```python
supabase.table('search_index').insert({
    'doc_id': doc_id,
    'chunk_text': chunk_text,
    'embedding': embedding,
    'chunk_index': 0
}).execute()
```

### 3. 類似度検索
```python
# match_documents 関数を使用
results = supabase.rpc('match_documents', {
    'query_embedding': query_embedding,
    'match_threshold': 0.5,
    'match_count': 10
}).execute()
```

### 4. match_documents() 関数の仕様

**パラメータ:**
- `query_embedding` (vector(1536)) - 検索クエリのベクトル
- `match_threshold` (float) - 類似度閾値（0.0-1.0）
- `match_count` (int) - 返す結果の最大数

**戻り値:**
- `doc_id` - ドキュメントID
- `chunk_text` - チャンクテキスト
- `similarity` - コサイン類似度スコア

**検索方式:** コサイン類似度（1 - cosine_distance）

---

## データベース設計の重要ポイント

### 1. UPDATE方式の採用
**問題:** 以前は DELETE→INSERT 方式でドキュメントが消失するリスクがあった

**解決:** UPDATE 方式に変更（pipeline.py 398-418行目）
```python
# 既存レコードを UPDATE
supabase.table('Rawdata_FILE_AND_MAIL').update({
    'stage_e1_text': stage_e_result['e1'],
    'stage_f_text_ocr': stage_f_result['text_ocr'],
    # ...
}).eq('doc_id', doc_id).execute()
```

### 2. Stage出力の完全保存
**重要:** 全ステージ（E-K）の出力を DB に保存（pipeline.py 378-388行目）

**理由:**
- デバッグ・分析が容易
- 再処理不要
- 処理履歴の追跡

### 3. pgvector インデックス
**推奨設定:**
```sql
CREATE INDEX ON search_index
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
```

**パフォーマンス:**
- lists = sqrt(行数) が目安
- 10万行以上で顕著な効果

---

## トラブルシューティング

### pgvector が見つからない
```sql
-- 拡張機能を確認
SELECT * FROM pg_extension WHERE extname = 'vector';

-- 再インストール
CREATE EXTENSION IF NOT EXISTS vector;
```

### 検索結果が空
```sql
-- search_index にデータがあるか確認
SELECT COUNT(*) FROM search_index;

-- embedding カラムが NULL でないか確認
SELECT COUNT(*) FROM search_index WHERE embedding IS NOT NULL;
```

### Stage出力カラムが NULL
```sql
-- カラムが存在するか確認
SELECT column_name
FROM information_schema.columns
WHERE table_name = 'Rawdata_FILE_AND_MAIL'
  AND column_name LIKE 'stage_%';

-- migrations/add_stage_output_columns.sql を実行
```

---

## 関連ドキュメント

- [README.md](README.md) - システム全体の概要とセットアップ
- [ARCHITECTURE.md](ARCHITECTURE.md) - 技術詳細とアーキテクチャ
- [shared/pipeline/config/](shared/pipeline/config/) - パイプライン設定

---

**最終更新:** 2026-01-02
