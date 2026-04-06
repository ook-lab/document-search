# プロジェクト標準: Python 3.12
FROM python:3.12-slim

WORKDIR /app

# [重要] 共通モジュールの依存関係などをインストール
COPY services/drive-duplicate-checker/requirements.txt /app/services/drive-duplicate-checker/requirements.txt
RUN pip install --no-cache-dir -r /app/services/drive-duplicate-checker/requirements.txt && \
    pip install --no-cache-dir gunicorn

# [重要] 共通モジュール (shared パッケージ) をコピー
COPY shared/ /app/shared/

# 今回のサービス本体をコピー
COPY services/drive-duplicate-checker/ /app/services/drive-duplicate-checker/

# インポートが通るようにパスを通す
ENV PYTHONPATH=/app
ENV PORT=8080

# 標準のgunicornコマンドで起動 (性能と安定性を確保)
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 300 --chdir services/drive-duplicate-checker app:app
