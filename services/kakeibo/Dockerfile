FROM python:3.11-slim

WORKDIR /app

# 1. 必要なツールをインストール
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 2. ライブラリ定義をコピー
COPY requirements.txt /app/
COPY kakeibo_ui/requirements.txt /app/kakeibo_ui/

# 3. ライブラリをインストール
# (もしkakeibo_ui固有のものがない場合は下の行でエラーになるので、なければ空ファイルを作っておいてください)
RUN pip install --no-cache-dir -r requirements.txt
RUN if [ -f kakeibo_ui/requirements.txt ]; then pip install --no-cache-dir -r kakeibo_ui/requirements.txt; fi

# 4. ソースコードをコピー
# sharedフォルダも必要ならコピーします
COPY shared/ /app/shared/
COPY kakeibo_ui/ /app/kakeibo_ui/

# 5. 【重要】Pythonが 'shared' などを読み込めるようにパスを通す
ENV PYTHONPATH=/app
ENV FLASK_APP=kakeibo_ui/app.py
ENV PORT=8080

# 6. ポート設定
EXPOSE 8080

# 7. 起動コマンド
# ディレクトリ構造に合わせて、kakeibo_uiフォルダの中のapp.pyを呼び出します
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120", "kakeibo_ui.app:app"]
