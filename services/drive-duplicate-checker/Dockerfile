# 軽量なPythonイメージ
FROM python:3.10-slim

# 作業ディレクトリ
WORKDIR /app

# 依存関係のコピーとインストール
# (ローカルスキャン用ライブラリも一応含めますが、クラウドではUIのみとして動きます)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ソースコードのコピー
COPY . .

# 環境変数の設定 (クラウド/ローカル判別用)
ENV RUN_MODE=CLOUD

# ポート番号 8082 (Cloud Runの標準 $PORT に合わせるため 8080 にするか、コード側で $PORT を見るようにします)
EXPOSE 8080

# 起動コマンド (PORT環境変数に対応させる)
CMD ["python", "app.py"]
