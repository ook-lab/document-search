# Claude ミスログ

このファイルはClaudeが犯したミスを記録し、再発防止のために使用します。

---

## 2025-01-11: Cloud Runデプロイ時の環境変数消失

### 何が起きたか
- Cloud Runのコンテナ起動エラー（ポート8080でリッスンしない）を修正しようとした
- 修正後、ユーザーに再デプロイコマンドを提供した
- そのコマンドに `--set-env-vars "$env:SUPABASE_URL..."` が含まれていた
- PowerShellで環境変数が未設定だったため、空の値でCloud Runの既存環境変数を上書きしてしまった
- 結果：元々動いていたサービスが「SUPABASE_URLが設定されていません」エラーで動作しなくなった

### なぜミスしたか
1. **確認不足**: ユーザーのPowerShell環境に環境変数が設定されているか確認せずにコマンドを提供した
2. **破壊的コマンドの認識不足**: `--set-env-vars` は既存の環境変数を完全に上書きする破壊的な操作だと認識していなかった
3. **動作確認の欠如**: コマンドを提供する前に、そのコマンドが何を行うか十分に検証しなかった

### 今後どうするか
1. **環境変数を含むデプロイコマンドを提供する前に、ユーザーに環境変数が設定されているか確認を促す**
2. **既存の設定を上書きする可能性があるコマンドには警告を付ける**
3. **イメージのみ更新する場合は `--set-env-vars` を省略し、既存の環境変数を保持する方法を提案する**

### 正しいアプローチ
```powershell
# 環境変数を変更せずイメージのみ更新する場合
gcloud run deploy doc-processor --image <image> --region asia-northeast1

# 環境変数も設定する場合は、事前に確認
echo $env:SUPABASE_URL  # 空でないことを確認してから実行
```

---

## 2025-01-11: 推測による修正の繰り返し

### 何が起きたか
- Cloud Runのコンテナ起動エラーを解決しようとした
- ログを確認せずに推測で修正を試みた
- 1つ修正 → デプロイ → エラー → また1つ修正 → デプロイ... を繰り返した
- ユーザーに何度もデプロイ作業を強いた

### なぜミスしたか
1. **根本原因を特定せずに修正を開始した**: ログやエラーメッセージを十分に分析しなかった
2. **1つずつ小出しにした**: 全ての潜在的問題を一度に洗い出さなかった
3. **確証なく「これで解決する」と宣言した**

### 今後どうするか
1. **修正前に必ず根本原因を特定する**（ログ確認、コード追跡）
2. **全ての潜在的問題を一度にリストアップしてから修正する**
3. **「確実に解決する」とは言わない。代わりに「これらの問題を修正しました。他に問題があれば教えてください」と言う**

---

## 2025-01-11: settings.py の不要な変更

### 何が起きたか
- `load_dotenv(override=True)` を `if os.path.exists('.env'): load_dotenv(override=False)` に変更した
- これにより、ローカル環境で.envファイルが読み込まれなくなった
- 結果：「SUPABASE_URLが設定されていません」エラーが発生

### なぜミスしたか
1. **推測で修正した**: `load_dotenv(override=True)` が問題だと推測したが、実際は問題ではなかった
2. **load_dotenvの動作を誤解していた**: .envファイルが存在しない場合は何もしないことを理解していなかった
3. **動いているコードを不必要に変更した**

### 今後どうするか
1. **動いているコードは触らない**（問題の原因でないなら）
2. **ライブラリの動作を確認してから修正する**
3. **推測ではなく、エラーメッセージやログに基づいて修正する**

---

## 2025-01-11: 修正したと言ってデプロイしなかった

### 何が起きたか
- 並列数制御のコード修正（current_workers >= self.max_parallel への変更など）を行った
- 「修正しました」「完璧です」と言った
- しかしCloud Runへのデプロイ指示を出さなかった
- ユーザーが複数PCでハードリフレッシュしても何も変わらず
- 結果：ユーザーは修正が反映されたと思っていたが、実際は古いコードのまま動いていた

### なぜミスしたか
1. **ローカル修正とデプロイを混同した**: ファイルを修正しただけで完了と勘違いした
2. **Cloud Run環境であることを忘れた**: ローカル開発のように即座に反映されると思い込んだ
3. **最後まで責任を持たなかった**: 修正 → ビルド → デプロイ → 動作確認 の全工程を追わなかった

### 今後どうするか
1. **修正後は必ずデプロイ指示を出す**
2. **「修正しました」ではなく「修正しました。反映するには再デプロイが必要です」と言う**
3. **CLAUDE_CHANGES_LOG.md に「デプロイ要否」欄を追加し、未デプロイの修正を追跡する**

---

## 2025-01-11: その場しのぎで複数のデプロイ方法を作成

### 何が起きたか
- デプロイがうまくいかないと、別の方法を試した
- cloudbuild.yaml方式、--source .方式、deploy_to_cloud_run.sh など複数のルートが混在
- Dockerfileは「プロジェクトルートからビルド」前提なのに、services/doc-processorから --source . を実行するコマンドを提供した
- 結果：どの方法が正しいか分からなくなり、ビルドが不完全になる

### なぜミスしたか
1. **その場しのぎの解決**: 問題が起きると根本解決せず別ルートを作った
2. **一貫性の欠如**: 1つの正しい方法を確立せず、複数の方法を乱立させた
3. **Dockerfileとデプロイコマンドの整合性を確認しなかった**

### 今後どうするか
1. **1つの正しいデプロイ方法を確立し、それだけを使う**
2. **新しい方法を作る前に、既存の方法がなぜ動かないか根本原因を特定する**
3. **Dockerfileのパス設計とデプロイコマンドの整合性を常に確認する**

### 正しいデプロイ方法（確定）
```powershell
# 必ずプロジェクトルートから実行
cd C:\Users\ookub\document-management-system

# 1. 環境変数読み込み
Get-Content .env | ForEach-Object { if ($_ -match "^([^=]+)=(.*)$") { Set-Item -Path "Env:$($matches[1])" -Value $matches[2] } }

# 2. ビルド
gcloud builds submit --region=asia-northeast1 --config=cloudbuild.yaml --substitutions="_GOOGLE_AI_API_KEY=$env:GOOGLE_AI_API_KEY,_ANTHROPIC_API_KEY=$env:ANTHROPIC_API_KEY,_OPENAI_API_KEY=$env:OPENAI_API_KEY,_SUPABASE_URL=$env:SUPABASE_URL,_SUPABASE_KEY=$env:SUPABASE_KEY,_SUPABASE_SERVICE_ROLE_KEY=$env:SUPABASE_SERVICE_ROLE_KEY"

# 3. デプロイ
gcloud run deploy doc-processor --image asia-northeast1-docker.pkg.dev/consummate-yew-479020-u2/cloud-run-source-deploy/doc-processor:latest --region asia-northeast1 --allow-unauthenticated --service-account document-management-system@consummate-yew-479020-u2.iam.gserviceaccount.com --timeout 3600 --memory 16Gi --cpu 4
```

---

## テンプレート（今後のミス記録用）

### YYYY-MM-DD: [ミスの概要]

#### 何が起きたか
-

#### なぜミスしたか
1.

#### 今後どうするか
1.

---
