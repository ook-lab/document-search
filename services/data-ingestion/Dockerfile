# Python 3.12ベースイメージ
FROM python:3.12-slim

# 作業ディレクトリを /app に設定
WORKDIR /app

# 依存パッケージをインストール
# services/data-ingestion/requirements.txt が個別にあるため、それを使用
COPY services/data-ingestion/requirements.txt /app/services/data-ingestion/requirements.txt
RUN pip install --no-cache-dir -r /app/services/data-ingestion/requirements.txt

# 共通モジュール (shared パッケージ) をコピー
COPY shared/ /app/shared/

# スクリプト (scripts パッケージ) をコピー
# data-ingestion から呼び出される scripts/processing/* が含まれる
COPY scripts/ /app/scripts/

# data-ingestion サービス本体をコピー（パッケージ名として有効なようにアンダースコアに変換）
COPY services/data-ingestion/ /app/services/data_ingestion/

# Pythonパッケージとして認識させるための __init__.py を作成
RUN touch services/__init__.py

# PYTHONPATH を設定してどこからでも import を可能にする
ENV PYTHONPATH=/app
ENV PORT=8080

# サーバーを起動 (gunicornを使用)
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 300 services.data_ingestion.app:app
