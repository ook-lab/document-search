# ベースイメージを使用（依存関係インストール済み）
FROM asia-northeast1-docker.pkg.dev/consummate-yew-479020-u2/cloud-run-source-deploy/doc-processor-base:latest

WORKDIR /app

# アプリケーションファイルをコピー
COPY G_processor/app.py .
COPY G_processor/templates/ ./templates/
# プロジェクトルートからの共通モジュール
COPY A_common/ ./A_common/
COPY C_ai_common/ ./C_ai_common/
# ドキュメント処理用のファイル
COPY process_queued_documents.py .
COPY G_unified_pipeline/ ./G_unified_pipeline/
COPY K_kakeibo/ ./K_kakeibo/

# ポート環境変数を設定
ENV PORT=8080

# 起動スクリプトをコピーして実行権限を付与
COPY G_processor/start.sh .
RUN chmod +x start.sh

# サーバーを起動
CMD ["./start.sh"]
