# 1. Python のベースイメージを使う
FROM python:3.12-slim

# 2. 必要なシステムツール（Tesseract, Popplerなど）をインストール
# ここが Buildpacks ではできなかった部分です！
# 修正点: libgl1-mesa-glx は古いので libgl1 に変更しました
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
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 必要なモジュールとアプリケーションファイルをコピー
COPY services/doc-search/app.py .
COPY services/doc-search/templates/ ./templates/
# プロジェクトルートからの共通モジュール
COPY shared/common/ ./shared/common/
COPY shared/ai/ ./shared/ai/
# ドキュメント処理用のファイル
COPY scripts/processing/process_queued_documents.py .
COPY shared/pipeline/ ./shared/pipeline/

# Note: PlaywrightはローカルのGmail取り込みのみで使用。Cloud Runでは不要。

# 6. ポート環境変数を設定（念のため）
ENV PORT=8080

# 7. サーバーを起動（gunicornを使用）
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
