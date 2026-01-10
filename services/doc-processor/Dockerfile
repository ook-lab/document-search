# ベースイメージを使用（依存関係インストール済み）
FROM asia-northeast1-docker.pkg.dev/consummate-yew-479020-u2/cloud-run-source-deploy/doc-processor-base:latest

WORKDIR /app

# アプリケーションファイルをコピー
COPY services/doc-processor/app.py .
COPY services/doc-processor/templates/ ./templates/
# プロジェクトルートからの共通モジュール
COPY shared/common/ ./shared/common/
COPY shared/ai/ ./shared/ai/
# ドキュメント処理用のファイル
COPY scripts/processing/process_queued_documents.py .
COPY shared/pipeline/ ./shared/pipeline/
COPY shared/kakeibo/ ./shared/kakeibo/

# ポート環境変数を設定
ENV PORT=8080

# 起動スクリプトをコピーして実行権限を付与
COPY services/doc-processor/start.sh .
RUN chmod +x start.sh

# サーバーを起動
CMD ["./start.sh"]
