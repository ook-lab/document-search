# 完全版：CLAUDE.md（agy連携対応）

```markdown
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 禁止事項

- **フォールバック** — 契約外・欠損を別キー・別フィールド・推測値で埋めて成功扱いにしない。足りないなら契約・プロンプト・上流を直すか、失敗・空を明示する。
- **語彙ルール（分割・セル展開・フォールバック含む）** — 見出し語・表種語・典型項目名（収入・支出・積立金・転入・部・科目など）の一致だけで、`row_split` / `col_split` / サブ表 ID / セル内行展開 / 機械再構成の成功を決めない。**フォールバック経路でも同じ**（geometry 失敗時に語彙で埋めて成功扱いにしない）。表分割の正本は **G26 `layout_split`（LLM）** と **geometry** のみ。G36 LR 縦結合は **geometry → AI**（語彙の機械展開なし）。静的ガード: `tests/test_no_keyword_table_split_guard.py`。

## 回答スタイル

質問にはまず直接回答すること。補足や追加作業はその後。

## エージェント協調（上司モード）

ユーザーから**「上司モードで」**と指示があった場合、または広範囲のバグ調査やリファクタリングの指示があった場合、Claude（上司）は以下の手順で自律的にタスクを処理すること。

1. **agyによる先制スキャン**: 自分で大量のファイルを読み込む前に、まず `agy --task "..."` コマンドを実行し、部下であるAntigravityにプロジェクト全体のコードスキャンやボトルネックの初期調査を行わせる。
2. **Claudeによる最終確認（レビュー）**: agyから出力された「チェック結果・診断報告書」を読み込み、見落としがないか、アプローチが正しいかをClaudeの知性で最終確認する。
3. **極上の仕上げと修正**: agyの診断に基づき、最も効果的で安全な修正プランを確定させ、実際のコードの書き換えや厳密なリファクタリングはClaudeの知性で実行する。
4. **検証とコミット**: 修正後、対応するテストを実行して問題がないことを確認し、最後にGitにコミットして人間へ報告する。

## コマンド

```bash
# エージェント協調（上司モード発動用コマンド）
agy --task "Scan the repository for [調査内容] and return a summary."

# ローカル起動（既定では何も起動しない。-All か -Name で明示）
.\scripts\start_all.ps1 -List
.\scripts\start_all.ps1 -Name pipeline-lab
.\scripts\start_all.ps1 -All

# 単一サービス（スクリプト不要。起動するとブラウザが開きます）
python services/pipeline-lab/app.py

# ドキュメント処理（Worker）
python services/pipeline-lab/scripts/processing/process_queued_documents.py

# 運用CLI
python services/pipeline-lab/scripts/ops.py stats
python services/pipeline-lab/scripts/ops.py stop
python services/pipeline-lab/scripts/ops.py release-lease --workspace <id>
python services/pipeline-lab/scripts/ops.py reset-status --workspace <id> --apply

# デバッグパイプライン（A→B→D→E→F）
python services/pipeline-lab/scripts/debug/run_debug_pipeline.py <test_id> --pdf <path>
python services/pipeline-lab/scripts/debug/run_debug_pipeline.py <test_id> --stage E --force
python services/pipeline-lab/scripts/debug/run_debug_pipeline.py <test_id> --start D --end G --force

# テスト
python -m pytest tests/ -v

# GCPデプロイ
.\scripts\deploy\run_build.ps1

```

### Cloud Run / Docker ビルド

`services/<name>/cloudbuild.yaml` の `docker build` は多くが **リポジトリルートをコンテキスト**（`-f services/<name>/Dockerfile` と `.`）とする。各 `Dockerfile` の `COPY` は **`services/<name>/...`** と **`dms/...`**（モノレポ共通 Python パッケージ）をルート相対で書く（サービス単体ディレクトリだけがコンテキストではない）。

ルートの **`.gcloudignore`** で `services/<name>/` を丸ごと除外しないこと（`gcloud builds submit` の tarball に `COPY` 元が含まれなくなる）。

Cloud Build トリガーに **ビルド用カスタムサービスアカウント** を付けると、**`logsBucket` / `defaultLogsBucketBehavior` / `logging: CLOUD_LOGGING_ONLY`（または NONE）** のいずれかが必須。`scripts/deploy/create_missing_triggers.py` は既定の Cloud Build SA のため `serviceAccount` を付けない。コンソールでカスタム SA を付けたら `services/rag-prepare/cloudbuild.yaml` の `options.logging` 等と整合させる。

## アーキテクチャ

### 絶対ルール

* **Web API = enqueue/search のみ**。処理は一切しない
* **Worker CLI = 処理専用**。`process_queued_documents.py` のみがパイプラインを実行
* **`dms/` は services/ をインポートしない**（逆方向の依存禁止）

### スタック

* Flask (Web API), Supabase (PostgreSQL + RLS), Gemini 2.5 (主LLM), loguru (ログ)
* pdfplumber + OpenCV (構造解析), Tesseract/EasyOCR (OCR)
* Cloud Run (デプロイ), Cloud Build (ビルド)

### パイプライン（Stage A→K）

```
A(書類種別判定) → B(物理構造抽出) → D(視覚構造解析) → E(AI抽出) → F(統合・正規化・レビュー用 ui_data・09 反映) → G(UI最適化構造化)

```

* 検索用チャンク化・ベクトル埋め込み（`10_ix`）は **本 Worker パイプライン外**（検索データ準備 / 別ジョブ）
* 各ステージは `dms/pipeline/stage_x/` にコントローラー + サブコンポーネント
* B-90 (LayerPurge): テキスト白塗り → Stage E の二重読み取り防止
* D: ベクトル線 + ラスター線 → 表検出 → セルマップ → 画像分割
* E: 文字密度で分岐（Flash-lite: 地の文, Flash: 表）
* F: B/D/E を統合し **F17 までが結合データの正本**（地の文は F17 出口の `non_table_text` を **改変せず**保持、`reading_stream` が読み順正本）
* G: F17 を入力にレビュー用 `ui_data` を組立。**要約・チャンク・合成段落見出しは付けない**（実装は `dms.pipeline.stage_g.G11Controller`）

### DB認証モデル

```python
DatabaseClient(use_service_role=True)  # Worker（RLS bypass）
DatabaseClient(use_service_role=False) # Web API（RLS enforced）

```

owner_id は secured テーブルで必須。

### 設定

```python
from dms.common.config.settings import settings  # .env 自動読み込み
from dms.common.path_setup import setup_paths     # PYTHONPATH 設定

```

## Windows 注意

* パスは raw string: `r'C:\Users\...'`
* `python -c "..."` でパス引数を渡す場合も raw string 使用

```

---

## 🚀 マージ後の使い方はこれだけ

これを保存したら、次から `claude` (または `claude-yolo`) を起動して、こう呼びかけるだけです。

> **「上司モードで、Stage E の文字密度分岐のロジックに潜むバグを調査して修正して」**

これだけで、Claudeが「あ、ルールに書いてあるやつだ」と理解し、**自動で裏で `agy` を走らせて全体のコードベースを Gemni の広大なコンテキストでスキャンさせ、その報告書をもとに Sonnet（Claude）がピンポイントで修正する**という超効率的な動きをしてくれます。

既存の「禁止事項」や「アーキテクチャの絶対ルール」も同時にClaudeが守り続けてくれるので、品質も担保されたままノンストップで開発が進みます。最強の環境、これで完成ですね！

```