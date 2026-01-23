# Phase 4B: owner_id 整合性チェック

## 概要

Phase 4B では owner_id の漏れや不整合を CI で継続的に検出し、再発を防止します。
コード変更によって「また owner_id が漏れた」状態を、CI で即座に検出します。

## 不変条件（Invariants）

以下の条件が常に成立することを検証します。

### 1. NULL 禁止

全ての必須テーブルで owner_id（または相当カラム）が NULL であってはならない。

| テーブル | カラム | 説明 |
|----------|--------|------|
| Rawdata_FILE_AND_MAIL | owner_id | ドキュメントの所有者 |
| 10_ix_search_index | owner_id | チャンクの所有者（親と一致） |
| Rawdata_RECEIPT_shops | owner_id | 店舗データの所有者 |
| MASTER_Rules_transaction_dict | created_by | ルール作成者 |
| 99_lg_correction_history | corrector_id | 修正者 |

### 2. 親子一致

子テーブルの owner_id は、親テーブルの owner_id と一致する必要がある。

| 子テーブル | 親テーブル | 結合キー |
|-----------|-----------|---------|
| 10_ix_search_index | Rawdata_FILE_AND_MAIL | document_id = id |

### 3. SYSTEM_OWNER_ID 閾値

バッチ処理等で使用する `SYSTEM_OWNER_ID` (00000000-0000-0000-0000-000000000000) の使用は、
異常検知のため閾値を設定し監視する。

| テーブル | 閾値（警告） | 閾値（失敗） | 理由 |
|----------|-------------|-------------|------|
| Rawdata_FILE_AND_MAIL | 100 | 200 | ユーザーデータは本来のオーナーを持つべき |
| 10_ix_search_index | 500 | 1000 | チャンクは多いため緩め |
| Rawdata_RECEIPT_shops | 50 | 100 | |
| MASTER_Rules_transaction_dict | 20 | 40 | マスタは少ないはず |
| 99_lg_correction_history | 50 | 100 | |

## テストファイル

`tests/test_phase4b_owner_integrity.py`

### テストクラス

1. **TestOwnerIdDefinitions** (ユニット)
   - 定義の整合性を検証
   - Supabase 不要

2. **TestOwnerIdIntegrityChecks** (統合)
   - 実際のデータベースで不変条件を検証
   - `pytest -m integration` で実行

3. **TestOwnerIntegritySummary** (統合)
   - 整合性レポートを生成
   - CI のサマリーとして使用

## 実行方法

### ローカル（ユニットテストのみ）

```bash
pytest tests/test_phase4b_owner_integrity.py -v -m "not integration"
```

### ローカル（Supabase 起動済み）

```bash
# 環境変数を設定
export SUPABASE_URL=http://127.0.0.1:54321
export SUPABASE_SERVICE_ROLE_KEY=<your-service-role-key>

# 全テスト実行
pytest tests/test_phase4b_owner_integrity.py -v

# 統合テストのみ
pytest tests/test_phase4b_owner_integrity.py -v -m integration
```

### CI（GitHub Actions）

```yaml
- name: Start Supabase
  run: supabase start

- name: Run integrity checks
  env:
    SUPABASE_URL: http://127.0.0.1:54321
    SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
  run: pytest tests/test_phase4b_owner_integrity.py -v -m integration
```

## 違反時の対応

### NULL 違反が検出された場合

1. **原因特定**: どの処理で NULL が挿入されたか調査
2. **修正**: DatabaseClient の `_validate_owner_id` をバイパスしていないか確認
3. **データ修復**: 該当行に適切な owner_id を設定（または削除）

```sql
-- NULL 行を特定
SELECT id, file_name, created_at
FROM "Rawdata_FILE_AND_MAIL"
WHERE owner_id IS NULL;

-- 修復（SYSTEM_OWNER_ID で一時的に埋める）
UPDATE "Rawdata_FILE_AND_MAIL"
SET owner_id = '00000000-0000-0000-0000-000000000000'
WHERE owner_id IS NULL;
```

### 親子不一致が検出された場合

1. **原因特定**: 子レコード作成時に親の owner_id を参照していない
2. **修正**: 子レコード作成ロジックで親の owner_id を継承するよう修正

```sql
-- 不一致を特定
SELECT
    c.id as chunk_id,
    c.owner_id as chunk_owner,
    p.id as doc_id,
    p.owner_id as doc_owner
FROM "10_ix_search_index" c
JOIN "Rawdata_FILE_AND_MAIL" p ON c.document_id = p.id
WHERE c.owner_id != p.owner_id;

-- 修復
UPDATE "10_ix_search_index" c
SET owner_id = p.owner_id
FROM "Rawdata_FILE_AND_MAIL" p
WHERE c.document_id = p.id
AND c.owner_id != p.owner_id;
```

### SYSTEM_OWNER_ID 閾値超過の場合

1. **調査**: なぜバッチ処理データが多いのか確認
2. **マイグレーション**: 適切なユーザーへの owner_id 付け替えを検討
3. **閾値調整**: 正当な理由があれば閾値を調整（ドキュメントに理由を記載）

## SYSTEM_OWNER_ID について

`SYSTEM_OWNER_ID` (00000000-0000-0000-0000-000000000000) は以下の用途で使用：

- バッチ処理で作成されたデータ（後でユーザーに紐付けが必要）
- マイグレーションで owner_id を一時的に埋める場合
- テストデータ

**注意**: 本番運用では SYSTEM_OWNER_ID の使用を最小限に抑え、
可能な限り実際のユーザー ID を使用すること。

## 関連ドキュメント

- [Phase 3: owner_id ポリシー](phase3_owner_id_policy.md)
- [Phase 4A: Public API 契約](phase4a_public_api_contract.md)
