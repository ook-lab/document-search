

  ## 📁 ディレクトリ構造

  services/              デプロイ可能なサービス
  ├── doc-processor/     ドキュメント処理（OCR、構造化、Embedding）
  ├── doc-search/        RAG検索
  ├── netsuper-search/   ネットスーパー商品検索
  └── data-ingestion/    データ取り込み

  shared/                共通ライブラリ
  ├── common/            基礎ユーティリティ
  ├── ai/                AI/LLM機能
  ├── pipeline/          処理パイプライン
  └── kakeibo/           家計簿機能

  scripts/               バッチ処理スクリプト
  ├── processing/        ドキュメント処理系
  ├── reset/             データリセット系
  ├── email/             メール管理系
  └── utils/             その他ユーティリティ

  ## 🚀 デプロイ方法

  cd services/doc-processor
  ./deploy.sh