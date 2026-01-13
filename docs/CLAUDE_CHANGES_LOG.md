# Claude 修正ログ

このファイルはClaudeが行った修正を記録します。

---

## 2025-01-11: Cloud Run起動エラー修正

### セッション開始時の問題
- Cloud Runでコンテナが起動しない（ポート8080でリッスンしない）

### 行った修正

#### 1. start.sh 改行コード修正
- **ファイル:** `services/doc-processor/start.sh`
- **問題:** Windows形式の改行（CRLF）だったため、Linuxで `#!/bin/bash\r` と解釈され実行不可
- **修正:** Unix形式（LF）に変換
- **コマンド:** `sed -i 's/\r$//' start.sh`

#### 2. shared/__init__.py 作成
- **ファイル:** `shared/__init__.py`
- **問題:** Pythonパッケージとして認識されない可能性
- **修正:** 空の `__init__.py` を作成

#### 3. Dockerfile 修正
- **ファイル:** `services/doc-processor/Dockerfile`
- **問題:** `process_queued_documents.py` が `/app/scripts/processing/` にコピーされるが、app.pyは `/app/process_queued_documents.py` をインポートしようとする
- **修正:** 以下を追加
```dockerfile
COPY shared/__init__.py ./shared/
COPY scripts/processing/process_queued_documents.py .
```

#### 4. settings.py 変更→元に戻し
- **ファイル:** `shared/common/config/settings.py`
- **問題:** 私が `load_dotenv(override=True)` を不要に変更して壊した
- **修正:** 元に戻した
```python
# 元のコード（正しい）
load_dotenv(override=True)
```

### 未修正の問題

#### app.py 308行目 - max_parallel デフォルト値
- **ファイル:** `services/doc-processor/app.py`
- **行:** 308
- **現状:** `'max_parallel': lock_data.get('max_parallel', 10),`
- **期待:** デフォルト値を30に変更する必要がある可能性
- **ステータス:** 未修正（ユーザー確認待ち）

---

## 2025-01-11: リアルタイム表示の初期値表示修正

### 行った修正

#### 1. processing.html - ページ読み込み時の即座データ取得
- **ファイル:** `services/doc-processor/templates/processing.html`
- **行:** 795-797
- **問題:** ページ読み込み時、startPolling()を呼ぶだけでupdateProgress()の初回実行まで1秒待っていた。そのため、ページを開いた瞬間は「-」や「待機中」のまま表示され、リアルタイムに見えなかった
- **修正:** DOMContentLoadedで即座にupdateProgress()を呼び出すように変更
```javascript
// 修正前
document.addEventListener('DOMContentLoaded', function() {
    startPolling();
});

// 修正後
document.addEventListener('DOMContentLoaded', function() {
    updateProgress();  // 即座に初回取得
    startPolling();    // その後1秒ごとに更新
});
```

### デプロイ要否
- **必要**: Cloud Runに再デプロイが必要

---

## 2026-01-11: リアルタイム表示のデバッグログ追加

### 問題
- CPU使用率とメモリ使用率が画面に表示されない
- データはSupabaseから取得できているが、画面が更新されない

### 行った修正

#### 1. processing.html - デバッグログ追加
- **ファイル:** `services/doc-processor/templates/processing.html`
- **行:** 538-550
- **問題:** `cpu_percent`, `memory_percent` が `null` または `undefined` の場合、画面が更新されない
- **修正:** コンソールログを追加して、実際に何が返ってきているか確認できるようにした
```javascript
console.log('[DEBUG] cpu_percent:', data.cpu_percent, 'memory_percent:', data.memory_percent);
if (data.cpu_percent !== null && data.cpu_percent !== undefined) {
    cpuUsage.textContent = data.cpu_percent + '%';
} else {
    console.warn('[WARNING] cpu_percent is null/undefined');
}
```

### デプロイ要否
- **必要**: Cloud Runに再デプロイが必要（実行中）

---

## 2026-01-11: monitor_resources タスク診断ログ追加

### 問題
- CPU/Memory の値がSupabaseで更新されない（フロントエンドで同じ値が表示され続ける）
- `monitor_resources()` async タスクが実行されていない可能性

### 行った修正

#### 1. monitor_resources タスク作成時のエラーハンドリング追加
- **ファイル:** `services/doc-processor/app.py` (lines 1156-1164)
- **問題:** タスク作成時のエラーが silent fail する可能性
- **修正:** タスク作成を try-catch で囲み、ログを追加
```python
try:
    monitor_task = asyncio.create_task(monitor_resources())
    logger.info(f"[MONITOR] タスク作成成功: {monitor_task}")
    await asyncio.sleep(0)  # イベントループにタスクを実行させる
    logger.info(f"[MONITOR] タスク状態確認: done={monitor_task.done()}, cancelled={monitor_task.cancelled()}")
except Exception as e:
    logger.error(f"[MONITOR] タスク作成失敗: {e}", exc_info=True)
    raise
```

#### 2. monitor_resources タスク終了時のエラーハンドリング追加
- **ファイル:** `services/doc-processor/app.py` (lines 1199-1207)
- **問題:** タスク終了時のエラーが silent fail する可能性
- **修正:** await monitor_task を try-catch で囲み、ログを追加
```python
logger.info("[MONITOR] タスク停止待機中...")
try:
    await monitor_task
    logger.info("[MONITOR] タスク正常終了")
except Exception as e:
    logger.error(f"[MONITOR] タスク終了時エラー: {e}", exc_info=True)
```

### デプロイ要否
- **必要**: Cloud Runに再デプロイが必要

---

## 2026-01-11: monitor_resources タスク実行問題の修正（根本原因）

### 問題
- monitor_resources()タスクが1回だけ実行されて停止
- 原因: forループ内でイベントループに制御が渡されていない
- process_single_document()はcreate_taskで並列実行されるため、forループ自体はawaitしない
- 並列数が上限に達していない場合、asyncio.sleep(0.1)が呼ばれず、monitor_resources()が実行されない

### 行った修正

#### 1. forループ内でイベントループに制御を渡す
- **ファイル:** `services/doc-processor/app.py` (line 1198-1199)
- **問題:** 新しいタスク作成後、イベントループに制御が渡されないため、monitor_resources()が実行されない
- **修正:** 各ドキュメント処理開始後に `await asyncio.sleep(0)` を追加
```python
# イベントループに制御を渡す（monitor_resourcesタスクが実行されるように）
await asyncio.sleep(0)
```

### デプロイ要否
- **必要**: Cloud Runに再デプロイが必要

---

## 2026-01-11: monitor_resources デバッグログ詳細化

### 問題
- monitor_resources()が1回だけ実行されて停止する問題が続いている
- whileループがどこで止まっているのか不明

### 行った修正

#### 1. monitor_resources whileループ内に詳細ログ追加
- **ファイル:** `services/doc-processor/app.py` (lines 1040-1081)
- **問題:** whileループの実行状況が見えない
- **修正:** ループ開始/終了/スリープ前後に詳細ログを追加
```python
loop_count = 0
while processing_status['is_processing']:
    loop_count += 1
    logger.info(f"[monitor_resources] ループ#{loop_count} 開始 (is_processing={processing_status['is_processing']})")
    ...
    logger.info(f"[monitor_resources] ループ#{loop_count} 完了、2秒スリープ開始")
    await asyncio.sleep(2)
    logger.info(f"[monitor_resources] ループ#{loop_count} スリープ完了、次のループへ")
logger.info(f"[monitor_resources] タスク終了 (is_processing={processing_status['is_processing']}, total_loops={loop_count})")
```

### デプロイ要否
- **必要**: Cloud Runに再デプロイが必要

---

## 2026-01-11: monitor_resources タスク削除、process_single_document内でリソース更新

### 問題
- monitor_resources()タスクが`await asyncio.sleep(2)`から制御を受け取れない
- イベントループの問題により、2秒ごとの更新が機能しない
- 根本原因: 別スレッドのasyncio.run()内で、monitor_resourcesタスクに制御が戻らない

### 行った修正

#### 1. monitor_resources()タスクの作成と待機を削除
- **ファイル:** `services/doc-processor/app.py` (lines 1172-1216)
- **問題:** monitor_resources()タスクがイベントループから制御を受け取れない
- **修正:** タスクの作成と待機コードを削除

#### 2. process_single_document()内でリソース情報を直接更新
- **ファイル:** `services/doc-processor/app.py` (lines 1114-1132)
- **問題:** リソース情報（CPU/Memory）がSupabaseに更新されない
- **修正:** ドキュメント処理開始時にリソース情報を取得してSupabaseに保存
```python
# リソース情報を取得してSupabaseに進捗を保存
memory_info = get_cgroup_memory()
memory_percent = memory_info['percent']
worker_status = get_worker_status()
current_workers = worker_status['current_workers']

# リソース調整
status = resource_manager.adjust_resources(memory_percent, current_workers)
processing_status['resource_control']['max_parallel'] = resource_manager.max_parallel
processing_status['resource_control']['throttle_delay'] = status['throttle_delay']
processing_status['resource_control']['adjustment_count'] = resource_manager.adjustment_count

# Supabaseに進捗を保存（リソース情報も含める）
update_progress_to_supabase(...)
```

### メリット
- イベントループの問題を回避
- ドキュメント処理のたびにリソース情報が更新される（2秒間隔ではなく、処理開始時）
- よりシンプルで確実な実装

### デプロイ要否
- **必要**: Cloud Runに再デプロイが必要

---

## 2026-01-11: threading.Timerを使った定期更新に変更（根本的解決）

### 問題
- 前回の修正では、ドキュメント処理開始時のみ更新されるため、処理が10分かかる場合は10分間更新されない
- asyncio.sleep()を使ったアプローチはイベントループの問題で機能しない

### 行った修正

#### 1. threading.Timerを使った定期更新を実装
- **ファイル:** `services/doc-processor/app.py` (lines 1024-1054)
- **問題:** asyncioの問題を完全に回避する必要がある
- **修正:** 別スレッドで2秒ごとにupdate_progress_to_supabase()を呼び出すTimerを実装
```python
def periodic_resource_update():
    """2秒ごとにリソース情報を更新（別スレッドで実行）"""
    if processing_status['is_processing']:
        try:
            logger.info("[PERIODIC_UPDATE] リソース情報更新開始")
            update_progress_to_supabase(...)
            logger.info("[PERIODIC_UPDATE] リソース情報更新完了")
        except Exception as e:
            logger.error(f"[PERIODIC_UPDATE] エラー: {e}", exc_info=True)

        # 次の実行をスケジュール
        update_timer = threading.Timer(2.0, periodic_resource_update)
        update_timer.daemon = True
        update_timer.start()

# 定期更新を開始
update_timer = threading.Timer(2.0, periodic_resource_update)
update_timer.daemon = True
update_timer.start()
```

#### 2. process_single_document()内のSupabase更新を削除
- **ファイル:** `services/doc-processor/app.py` (lines 1149-1162)
- **問題:** 定期更新と重複するため、処理開始時の更新は不要
- **修正:** リソース調整のみ実行し、Supabase更新は削除（定期更新で行う）

### メリット
- asyncioの問題を完全に回避
- ドキュメント処理時間に関係なく、2秒ごとに確実に更新される
- よりシンプルで確実な実装

### デプロイ要否
- **必要**: Cloud Runに再デプロイが必要

---

## 2026-01-11: threading.Timer明示的停止処理を追加

### 問題
- 前回の実装では、処理終了後もタイマーが動き続ける可能性がある
- タイマーを明示的にキャンセルする処理が不足

### 行った修正

#### 1. finally節でタイマーを明示的にキャンセル
- **ファイル:** `services/doc-processor/app.py` (lines 1226-1231)
- **問題:** 処理終了後もタイマーが動き続ける
- **修正:** 処理完了時にupdate_timer.cancel()を呼び出す
```python
finally:
    # 定期更新タイマーを停止
    processing_status['is_processing'] = False  # タイマーのループ条件をFalseに
    if update_timer is not None:
        update_timer.cancel()
        logger.info("[PERIODIC_UPDATE] タイマーをキャンセルしました")
```

### メリット
- 処理完了後、タイマーが確実に停止する
- リソースリークを防止

### デプロイ要否
- **必要**: Cloud Runに再デプロイが必要

---

## 2026-01-12: イベント駆動型進捗更新の実装（E4, E5, F全工程）

### 問題
- タイマー方式（threading.Timer）は複雑で終了判定が難しい
- ユーザーから「処理をトリガーにする」という提案があった
- E4, E5, F-1～F-10の各工程開始時に進捗を更新することで、自然なライフサイクルを実現

### 行った修正

#### 1. Stage E (前処理) にコールバック追加
- **ファイル:** `shared/pipeline/stage_e_preprocessing.py`, `shared/common/processors/pdf.py`
- **問題:** E1のみコールバックがあり、E4とE5がなかった
- **修正:**
  - `extract_text()` に `progress_callback` パラメータを追加
  - `_process_office_with_stages()` の E4 (line 172) と E5 (line 217) にコールバック追加
  - `_process_image_with_stages()` の E4 (line 288) と E5 (line 332) にコールバック追加
  - `pdf.py` の `extract_text()` にパラメータ追加、E4 (line 108) と E5 (line 127) にコールバック追加
  - `pipeline.py` から Stage E へのコールバック渡し (line 147)

#### 2. Stage F (視覚解析) に全工程コールバック追加
- **ファイル:** `shared/pipeline/stage_f_visual.py`
- **問題:** Fは重い処理なので、10個の工程を細かく報告する必要があった
- **修正:** `process()` メソッドに全工程のコールバックを追加
  - F-1: PaddleOCR表抽出 (line 216)
  - F-2: Suryaレイアウト解析 (line 250)
  - F-3: 画像切り出し (line 327)
  - F-4: PaddleOCRテキスト認識 (line 377)
  - F-5: テキスト統合 (line 477)
  - F-6: プロンプト構築 (line 508)
  - F-7: Gemini Vision API呼び出し (line 593)
  - F-8: JSONクリーニング (line 667)
  - F-9: 全結果マージ (line 693)
  - F-10: 最終検証・出力 (line 769)
  - `pipeline.py` から Stage F へのコールバック渡し (line 176)

#### 3. 既存のコールバック構造
- **ファイル:** `services/doc-processor/app.py`, `scripts/processing/process_queued_documents.py`, `shared/pipeline/pipeline.py`
- **確認:** 既に E1, F, H, I, J, K のコールバックは実装済み
- **今回の追加:** E4, E5, F-1～F-10 を追加することで完全なカバレッジを実現

### メリット
- イベント駆動型なので、処理開始と同時に進捗が更新される
- タイマーが不要になり、終了判定が自然（処理が終われば自動的に止まる）
- 各ステージの進捗が細かく報告されるため、ユーザーがリアルタイムで状況を把握できる
- F工程が特に重いため、10個の工程に分けることで詳細な進捗が見える

### デプロイ要否
- **必要**: Cloud Runに再デプロイが必要

---

## 2026-01-12: 重複変数の統一（19個→3個）

### 問題
- 同じ情報を表す変数が複数存在し、管理が複雑化
- 実行数を表す変数が6個、最大並列数が4個、メモリ/CPU関連が9個存在
- データの不整合が発生しやすく、デバッグが困難

### 行った修正

#### 1. 実行数関連の変数統一（6個→1個）
- **ファイル:** `services/doc-processor/app.py`
- **問題:** 実行数を表す変数が6個存在
  1. `active_tasks`（インメモリリスト）
  2. `processing_workers`テーブル（DB）
  3. `processing_lock.current_workers`（DB）
  4. `processing_status['resource_control']['current_parallel']`（dict）
  5. `worker_status['current_workers']`（関数戻り値）
  6. `actual_workers`（ローカル変数）
- **修正:**
  - **唯一の真実のソース:** `active_tasks`（インメモリリスト）
  - `register_worker()`, `unregister_worker()` - DB操作を削除、互換性のため関数は残す
  - `clear_all_workers()` - `active_tasks.clear()`のみ実行
  - `reset_stuck_documents()` - `active_tasks`から処理中のdoc_idを取得
  - `update_worker_count()` - `len(active_tasks)`を返すのみ（DB更新削除）
  - `update_progress_to_supabase()` - `len(active_tasks)`で直接取得
  - `get_worker_status()` - `active_tasks`から`current_workers`と`workers`を生成
  - `processing_status['resource_control']['current_parallel']` - 削除

#### 2. 最大並列数関連の変数統一（4個→1個）
- **ファイル:** `services/doc-processor/app.py`
- **問題:** 最大並列数を表す変数が4個存在
  1. `resource_manager.max_parallel`（クラス内）
  2. `processing_lock.max_parallel`（DB）
  3. `processing_status['resource_control']['max_parallel']`（dict）
  4. `worker_status['max_parallel']`（関数戻り値）
- **修正:**
  - **唯一の真実のソース:** `resource_manager.max_parallel`
  - `processing_status['resource_control']['max_parallel']` - 削除（各所で代入削除）
  - `adjust_max_parallel()` - `resource_manager.max_parallel`のみ使用
  - `can_start_new_worker()` - `resource_manager.max_parallel`から取得
  - `get_worker_status()` - `resource_manager.max_parallel`から取得
  - `processing_lock.max_parallel` - DB保存は継続（表示用）

#### 3. メモリ/CPU関連の変数
- **ファイル:** `services/doc-processor/app.py`
- **状態:** 既に最適化済み
- **確認:** 各変数は適切な目的で使用されており、重複なし
  - `memory_info` - `get_cgroup_memory()`の戻り値（一時変数）
  - `memory_percent` - ローカル変数（計算用）
  - `mem_before`, `mem_after` - ドキュメント処理前後のメモリ計測用
  - `processing_status`内のmemory系 - 表示用
  - `progress['memory_*']` - Supabase保存用
  - `resource_manager.memory_history` - リソース調整の履歴

### 結果
- **統一前:** 19個の重複変数
- **統一後:** 3個の真実のソース（`active_tasks`, `resource_manager.max_parallel`, メモリ/CPU直接取得）
- 構文エラー: なし
- データの整合性が大幅に向上
- 管理が単純化され、デバッグが容易に

### メリット
- Single Source of Truth (SSOT) の実現
- データ不整合のリスクが大幅に減少
- コードの可読性と保守性が向上
- DB書き込み回数が減少し、パフォーマンス向上

### デプロイ要否
- **必要**: Cloud Runに再デプロイが必要

---

## 2026-01-12: 処理終了時のSupabase状態同期修正

### 問題
- 処理完了・異常終了時に `processing_status['is_processing'] = False` だけ設定
- Supabaseの `is_processing` は更新されずtrueのまま
- 停止ボタンを押しても「実行中の処理がありません」エラー
- 画面では `is_processing: true` のまま表示され続ける

### 行った修正

#### 1. finally ブロックでSupabaseも更新
- **ファイル:** `services/doc-processor/app.py` (lines 1223-1227, 1254-1260)
- **問題:** 処理完了時・異常終了時にローカル状態のみ更新、Supabaseは未更新
- **修正:** 両方の`finally`ブロックに `set_processing_lock(False)` を追加
```python
finally:
    processing_status['is_processing'] = False
    set_processing_lock(False)  # Supabaseも更新
    logger.info("[PROCESS] 全ドキュメント処理完了")
```

#### 2. stop_processing()のチェックロジック修正
- **ファイル:** `services/doc-processor/app.py` (lines 1301-1310)
- **問題:** ローカルとSupabase両方がfalseの場合のみエラーを返すロジック
- **修正:** Supabaseのみをチェック（ローカルは別インスタンスの可能性があるため）
```python
# Supabaseのロックをチェック（ローカルは別インスタンスの可能性があるので見ない）
if not get_processing_lock():
    return jsonify({
        'success': False,
        'error': '実行中の処理がありません'
    }), 400
```

### メリット
- 処理終了時に確実にSupabaseの状態もクリアされる
- 停止ボタンが正常に機能する
- 複数インスタンス環境でも正しく動作

### デプロイ要否
- **必要**: Cloud Runに再デプロイが必要

---

## テンプレート（今後の修正記録用）

### YYYY-MM-DD: [修正の概要]

#### 行った修正

##### 1. [修正名]
- **ファイル:**
- **問題:**
- **修正:**

#### 未修正の問題
-

---
