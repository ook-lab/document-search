# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 回答スタイル

質問にはまず直接回答すること。補足や追加作業はその後。

## コマンド

```bash
# ローカル全サービス起動
.\start_all.ps1

# ドキュメント処理（Worker）
python scripts/processing/process_queued_documents.py

# 運用CLI
python scripts/ops.py stats
python scripts/ops.py stop
python scripts/ops.py release-lease --workspace <id>
python scripts/ops.py reset-status --workspace <id> --apply

# デバッグパイプライン（A→B→D→E→F→G）
python scripts/debug/run_debug_pipeline.py <test_id> --pdf <path>
python scripts/debug/run_debug_pipeline.py <test_id> --stage E --force
python scripts/debug/run_debug_pipeline.py <test_id> --start D --end G --force

# テスト
python -m pytest tests/ -v

# GCPデプロイ
.\run_build.ps1
```

## アーキテクチャ

### 絶対ルール
- **Web API = enqueue/search のみ**。処理は一切しない
- **Worker CLI = 処理専用**。`process_queued_documents.py` のみがパイプラインを実行
- **shared/ は services/ をインポートしない**（逆方向の依存禁止）

### スタック
- Flask (Web API), Supabase (PostgreSQL + RLS), Gemini 2.5 (主LLM), loguru (ログ)
- pdfplumber + OpenCV (構造解析), Tesseract/EasyOCR (OCR)
- Cloud Run (デプロイ), Cloud Build (ビルド)

### パイプライン（Stage A→K）

```
A(書類種別判定) → B(物理構造抽出) → D(視覚構造解析) → E(AI抽出) → F(統合・正規化) → G(UI最適化) → H(構造化) → J(チャンキング) → K(埋め込み)
```

- 各ステージは `shared/pipeline/stage_x/` にコントローラー + サブコンポーネント
- B-90 (LayerPurge): テキスト白塗り → Stage E の二重読み取り防止
- D: ベクトル線 + ラスター線 → 表検出 → セルマップ → 画像分割
- E: 文字密度で分岐（Flash-lite: 地の文, Flash: 表）
- G: フロントエンドが即描画可能な ui_data を生成

### DB認証モデル
```python
DatabaseClient(use_service_role=True)   # Worker（RLS bypass）
DatabaseClient(use_service_role=False)  # Web API（RLS enforced）
```
owner_id は secured テーブルで必須。

### 設定
```python
from shared.common.config.settings import settings  # .env 自動読み込み
from shared.common.path_setup import setup_paths     # PYTHONPATH 設定
```

## Windows 注意
- パスは raw string: `r'C:\Users\...'`
- `python -c "..."` でパス引数を渡す場合も raw string 使用
