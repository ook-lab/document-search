# 1. Python のベースイメージを使う
FROM python:3.12-slim

# 2. 必要なシステムツール（Tesseract, Popplerなど）をインストール
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-jpn \
    libtesseract-dev \
    poppler-utils \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# 3. 作業ディレクトリを設定
WORKDIR /app

# 4. プロジェクトルート全体をコピー（親ディレクトリから）
# ビルドコンテキストはプロジェクトルート (document_management_system/) です
# まずrequirements.txtだけコピーしてインストール（キャッシュ効率化）
COPY G_processor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 必要なモジュールとアプリケーションファイルをコピー
COPY G_processor/app.py .
COPY G_processor/templates/ ./templates/
# プロジェクトルートからの共通モジュール
COPY A_common/ ./A_common/
COPY C_ai_common/ ./C_ai_common/
# ドキュメント処理用のファイル
COPY process_queued_documents.py .
COPY G_unified_pipeline/ ./G_unified_pipeline/
COPY K_kakeibo/ ./K_kakeibo/

# 6. ポート環境変数を設定
ENV PORT=8080

# 7. 起動スクリプトをコピーして実行権限を付与
COPY G_processor/start.sh .
RUN chmod +x start.sh

# 8. サーバーとバックグラウンド処理を起動
CMD ["./start.sh"]
