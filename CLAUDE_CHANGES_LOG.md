# Claude 修正ログ

このファイルはClaudeが行った修正を記録します。

---

## 2025-01-11: Cloud Run起動エラー修正

### セッション開始時の問題
- Cloud Runでコンテナが起動しない（ポート8080でリッスンしない）

### 行った修正

#### 1. start.sh 改行コード修正
- **ファイル:** `services/doc-processor/start.sh`
- **問題:** Windows形式の改行（CRLF）だったため、Linuxで `#!/bin/bash\r` と解釈され実行不可
- **修正:** Unix形式（LF）に変換
- **コマンド:** `sed -i 's/\r$//' start.sh`

#### 2. shared/__init__.py 作成
- **ファイル:** `shared/__init__.py`
- **問題:** Pythonパッケージとして認識されない可能性
- **修正:** 空の `__init__.py` を作成

#### 3. Dockerfile 修正
- **ファイル:** `services/doc-processor/Dockerfile`
- **問題:** `process_queued_documents.py` が `/app/scripts/processing/` にコピーされるが、app.pyは `/app/process_queued_documents.py` をインポートしようとする
- **修正:** 以下を追加
```dockerfile
COPY shared/__init__.py ./shared/
COPY scripts/processing/process_queued_documents.py .
```

#### 4. settings.py 変更→元に戻し
- **ファイル:** `shared/common/config/settings.py`
- **問題:** 私が `load_dotenv(override=True)` を不要に変更して壊した
- **修正:** 元に戻した
```python
# 元のコード（正しい）
load_dotenv(override=True)
```

### 未修正の問題

#### app.py 308行目 - max_parallel デフォルト値
- **ファイル:** `services/doc-processor/app.py`
- **行:** 308
- **現状:** `'max_parallel': lock_data.get('max_parallel', 10),`
- **期待:** デフォルト値を30に変更する必要がある可能性
- **ステータス:** 未修正（ユーザー確認待ち）

---

## テンプレート（今後の修正記録用）

### YYYY-MM-DD: [修正の概要]

#### 行った修正

##### 1. [修正名]
- **ファイル:**
- **問題:**
- **修正:**

#### 未修正の問題
-

---
