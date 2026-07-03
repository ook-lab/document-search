# NAS同期システム - フェーズ1: データベース環境

このディレクトリは、同期システムの中心となるメタデータ管理データベース（PostgreSQL）を構築するための環境です。

## 🚀 起動手順

1. このディレクトリに移動します。
   ```bash
   cd services/nas-sync
   ```

2. 必要なディレクトリ（初期化SQLの配置用）があるか確認します。
* `db/schema.sql` が配置されている必要があります。

3. Dockerコンテナをバックグラウンドで起動します。
   ```bash
   docker-compose up -d
   ```

## 🔍 検証方法

コンテナが起動すると、`db/schema.sql` が自動的に読み込まれてテーブルが作成されます。
以下のコマンドで、テーブルが正常に作られているか確認できます。

```bash
# PostgreSQLのコンテナ内に入ってテーブル一覧を表示
docker exec -it nas-sync-postgres psql -U sync_user -d sync_metadata -c "\dt"
```

正しく実行されていれば、`activity_logs`, `api_tokens`, `devices`, `files`, `sync_folders` の5つのテーブルが表示されます。

## 🔑 接続情報（APIサーバー開発用）

* **ホスト**: `localhost` (またはNASのTailscale IP)
* **ポート**: `5432`
* **データベース名**: `sync_metadata`
* **ユーザー名**: `sync_user`
* **パスワード**: `sync_secure_password_2026`
