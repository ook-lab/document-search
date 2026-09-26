#!/usr/bin/env python3
"""
reembed_10_ix_gemini.py

【背景・目的】
テーブル public."10_ix_search_index" の検索用ベクトル（embedding 列、vector(1536)、OpenAI text-embedding-3-small）
を Gemini の gemini-embedding-2（1536次元）に切り替えるためのデータ埋め込みスクリプトです。
第1段階として既存の検索を停止させずに、新しいカラム embedding_v2 に Gemini ベクトルを格納します。

【設計方針・重要制約】
- フォールバック絶対禁止:
  欠損や失敗を推測値、ゼロベクトル、旧ベクトル、別のAPIキーで黙って埋めて成功扱いにしません。
  失敗は明示的に DB の embedding_v2_error カラムに理由を記録し、標準出力・標準エラー出力に表示します。
- 認証情報:
  Secret Manager（GCPプロジェクト: personal-dev-platform-508202）から google-cloud-secret-manager と ADC で取得:
    - SUPABASE_URL
    - SUPABASE_SERVICE_ROLE_KEY
    - GOOGLE_AI_PAID_API_KEY
  ※ 埋め込みには GOOGLE_AI_PAID_API_KEY のみを使用します（GOOGLE_AI_API_KEY など他のキーは絶対に参照しません）。
  取得できなければ即座にエラー終了します。
- モデル・書式:
  google-genai (genai.Client(api_key=...)) を使用。
  model='gemini-embedding-2'、1リクエスト1チャンク。
  文字列書式: 'title: none | text: {chunk_text}'
  config: types.EmbedContentConfig(output_dimensionality=1536)
  レスポンスが1件かつ1536次元であることを検証。
- レート制御・エラーハンドリング:
  --rpm（既定 90）で1分あたりのリクエスト数を制限。
  HTTP 429 を受けたら、それまでの進捗を出力して終了コード非0（sys.exit(1)）で終了（翌日再実行で続きから）。
  HTTP 500 / 503 は同じ依頼を同じモデル・同じキーで指数バックオフ最大3回まで再送し、
  それでも失敗ならその行を失敗として embedding_v2_error に記録して続行。

【前提条件】
1. Google Cloud の Application Default Credentials (ADC) が設定されていること:
   $ gcloud auth application-default login
2. 依存パッケージがインストールされていること:
   $ pip install -r scripts/embedding/requirements.txt

【使い方】
# 1. ドライラン（対象件数と合計文字数のみ集計し終了。API呼び出し・DB書き込みなし）
python scripts/embedding/reembed_10_ix_gemini.py --dry-run

# 2. 動作確認（上限 5 件のみ実行）
python scripts/embedding/reembed_10_ix_gemini.py --limit 5

# 3. 本実行（既定: 90 RPM）
python scripts/embedding/reembed_10_ix_gemini.py

# 4. レートを調整して実行（例: 60 RPM）
python scripts/embedding/reembed_10_ix_gemini.py --rpm 60

# 5. 失敗行（embedding_v2_error が記録された行）を再試行
python scripts/embedding/reembed_10_ix_gemini.py --retry-failed
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from google.cloud import secretmanager
from supabase import Client, create_client

PROJECT_ID = "personal-dev-platform-508202"
TABLE_NAME = "10_ix_search_index"
EMBEDDING_MODEL = "gemini-embedding-2"
EMBEDDING_DIM = 1536


def fetch_secret(sm_client: secretmanager.SecretManagerServiceClient, secret_id: str, project_id: str = PROJECT_ID) -> str:
    """Secret Manager からシークレット値を取得。取得できない場合は即座にエラー終了。"""
    secret_path = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    try:
        response = sm_client.access_secret_version(request={"name": secret_path})
        val = response.payload.data.decode("utf-8").strip()
        if not val:
            raise ValueError(f"Secret '{secret_id}' payload is empty")
        return val
    except Exception as e:
        sys.stderr.write(f"[FATAL] Secret Manager からのシークレット '{secret_id}' (project: {project_id}) 取得に失敗しました: {e}\n")
        sys.stderr.write("[FATAL] フォールバックは禁止されています。スクリプトを終了します。\n")
        sys.exit(1)


class RateLimiter:
    """1分あたりのリクエスト数（RPM）を制御するリミッター"""

    def __init__(self, rpm: float) -> None:
        if rpm <= 0:
            raise ValueError(f"Invalid rpm: {rpm}. Must be > 0.")
        self.interval = 60.0 / float(rpm)
        self.last_call_time: Optional[float] = None

    def wait(self) -> None:
        now = time.monotonic()
        if self.last_call_time is not None:
            elapsed = now - self.last_call_time
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
        self.last_call_time = time.monotonic()


def extract_http_status_code(err: Exception) -> Optional[int]:
    """例外オブジェクトから HTTP ステータスコードを抽出"""
    code = getattr(err, "code", None)
    if isinstance(code, int):
        return code
    status_code = getattr(err, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    return None


def print_summary(
    total_processed: int,
    success_count: int,
    failed_count: int,
    skipped_count: int,
    is_interrupted: bool = False,
) -> None:
    """処理サマリーの標準出力"""
    print("\n" + "=" * 60)
    if is_interrupted:
        print("実行中断サマリー (HTTP 429 レート制限等による停止)")
    else:
        print("実行サマリー (Execution Summary)")
    print("=" * 60)
    print(f"処理件数 (Total Processed): {total_processed}")
    print(f"成功     (Success):         {success_count}")
    print(f"失敗     (Failed):          {failed_count}")
    print(f"スキップ (Skipped):         {skipped_count}")
    print("=" * 60 + "\n")


def fetch_target_rows_batch(
    supabase: Client,
    last_id: Optional[str],
    batch_size: int,
    retry_failed: bool,
) -> List[Dict[str, Any]]:
    """
    10_ix_search_index から対象レコードを id 順にバッチ取得する。
    keyset pagination (gt('id', last_id)) を使用して更新時のズレを防止。
    """
    query = supabase.table(TABLE_NAME).select("id, chunk_text")
    if retry_failed:
        # embedding_v2 IS NULL
        query = query.is_("embedding_v2", "null")
    else:
        # embedding_v2 IS NULL AND embedding_v2_error IS NULL
        query = query.is_("embedding_v2", "null").is_("embedding_v2_error", "null")

    query = query.order("id")
    if last_id is not None:
        query = query.gt("id", last_id)

    query = query.limit(batch_size)
    response = query.execute()
    return response.data or []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="10_ix_search_index の chunk_text を Gemini gemini-embedding-2 で再埋め込み")
    parser.add_argument("--dry-run", action="store_true", help="対象件数と chunk_text の合計文字数のみを集計・出力して終了（API呼び出し・書き込みなし）")
    parser.add_argument("--limit", type=int, default=None, help="処理件数の上限")
    parser.add_argument("--retry-failed", action="store_true", help="embedding_v2_error が記録された失敗行も対象に含める")
    parser.add_argument("--rpm", type=float, default=90.0, help="1分あたりのリクエスト数制限（既定: 90）")
    parser.add_argument("--batch-size", type=int, default=100, help="DBからのバッチフェッチ件数（既定: 100）")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # 1. 認証情報を Secret Manager (personal-dev-platform-508202) から取得
    # フォールバック絶対禁止: GOOGLE_AI_PAID_API_KEY のみ使用
    print(f"Secret Manager (project: {PROJECT_ID}) から認証情報を取得中...")
    sm_client = secretmanager.SecretManagerServiceClient()

    supabase_url = fetch_secret(sm_client, "SUPABASE_URL", PROJECT_ID)
    supabase_service_role_key = fetch_secret(sm_client, "SUPABASE_SERVICE_ROLE_KEY", PROJECT_ID)
    google_ai_paid_api_key = fetch_secret(sm_client, "GOOGLE_AI_PAID_API_KEY", PROJECT_ID)

    print("認証情報を正常に取得しました。")

    # Supabase クライアント初期化
    supabase: Client = create_client(supabase_url, supabase_service_role_key)

    # 2. ドライランモードの場合
    if args.dry_run:
        print("[DRY-RUN] 対象行の集計を開始します...")
        target_count = 0
        total_characters = 0
        last_id = None
        batch_size = args.batch_size

        while True:
            fetch_limit = batch_size
            if args.limit is not None:
                remaining = args.limit - target_count
                if remaining <= 0:
                    break
                fetch_limit = min(batch_size, remaining)

            rows = fetch_target_rows_batch(supabase, last_id, fetch_limit, args.retry_failed)
            if not rows:
                break

            for row in rows:
                target_count += 1
                last_id = row["id"]
                chunk_text = row.get("chunk_text") or ""
                total_characters += len(chunk_text)

                if args.limit is not None and target_count >= args.limit:
                    break

        print("\n" + "=" * 60)
        print("ドライラン集計結果 (Dry Run Results)")
        print("=" * 60)
        print(f"対象件数:               {target_count} 件" + (f" (上限 --limit {args.limit} 適用)" if args.limit else ""))
        print(f"chunk_text 合計文字数:   {total_characters} 文字")
        print("※ --dry-run のため、Gemini API 呼び出しおよび DB 更新は行いませんでした。")
        print("=" * 60 + "\n")
        sys.exit(0)

    # 3. 本実行モード
    # google-genai クライアントの初期化（GOOGLE_AI_PAID_API_KEY のみ使用）
    genai_client = genai.Client(api_key=google_ai_paid_api_key)

    rate_limiter = RateLimiter(args.rpm)

    total_processed = 0
    success_count = 0
    failed_count = 0
    skipped_count = 0
    last_id = None
    batch_size = args.batch_size

    print(f"Gemini gemini-embedding-2 による埋め込み生成を開始します (RPM={args.rpm})...")
    if args.limit is not None:
        print(f"処理上限: {args.limit} 件")
    if args.retry_failed:
        print("モード: --retry-failed (失敗行も再処理対象)")

    while True:
        fetch_limit = batch_size
        if args.limit is not None:
            remaining = args.limit - total_processed
            if remaining <= 0:
                break
            fetch_limit = min(batch_size, remaining)

        rows = fetch_target_rows_batch(supabase, last_id, fetch_limit, args.retry_failed)
        if not rows:
            break

        for row in rows:
            row_id = str(row["id"])
            last_id = row_id
            chunk_text = row.get("chunk_text")

            # chunk_text の検証（空なら埋め込みを作らず embedding_v2_error に記録）
            if chunk_text is None or not str(chunk_text).strip():
                error_msg = "chunk_text is empty or blank"
                print(f"[{total_processed + 1}] ID: {row_id} -> 失敗: {error_msg}")
                try:
                    supabase.table(TABLE_NAME).update({
                        "embedding_v2_error": error_msg
                    }).eq("id", row_id).execute()
                except Exception as dbe:
                    sys.stderr.write(f"[ERROR] DB update failed for row {row_id}: {dbe}\n")
                failed_count += 1
                total_processed += 1
                if args.limit is not None and total_processed >= args.limit:
                    break
                continue

            chunk_text_str = str(chunk_text).strip()
            content_payload = f"title: none | text: {chunk_text_str}"

            # API 呼び出し（レート制限およびリトライ処理）
            max_retries = 3
            backoff_delays = [1.0, 2.0, 4.0]
            attempt = 0
            embedding_vector: Optional[List[float]] = None
            row_error: Optional[str] = None

            while attempt <= max_retries:
                rate_limiter.wait()
                try:
                    response = genai_client.models.embed_content(
                        model=EMBEDDING_MODEL,
                        contents=content_payload,
                        config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM),
                    )
                    # 埋め込み結果の検証
                    if not response.embeddings or len(response.embeddings) != 1:
                        cnt = len(response.embeddings) if response.embeddings else 0
                        row_error = f"Invalid embedding response: expected 1 embedding, got {cnt}"
                        break

                    emb = response.embeddings[0]
                    values = getattr(emb, "values", None)
                    if values is None or len(values) != EMBEDDING_DIM:
                        dim = len(values) if values else 0
                        row_error = f"Invalid embedding dimension: expected {EMBEDDING_DIM}, got {dim}"
                        break

                    embedding_vector = values
                    row_error = None
                    break
                except Exception as e:
                    code = extract_http_status_code(e)
                    if code == 429:
                        print(
                            f"\n[FATAL] HTTP 429 (Rate Limit / Quota Exceeded) を検出しました (行 ID={row_id}): {e}",
                            file=sys.stderr,
                        )
                        # それまでの進捗を出力して終了コード非0で終了
                        print_summary(total_processed, success_count, failed_count, skipped_count, is_interrupted=True)
                        sys.exit(1)
                    elif code in (500, 503):
                        attempt += 1
                        if attempt <= max_retries:
                            delay = backoff_delays[attempt - 1]
                            print(
                                f"[{total_processed + 1}] ID: {row_id} -> HTTP {code} 検出。指数バックオフ {delay}s 待機 (試行 {attempt}/{max_retries})..."
                            )
                            time.sleep(delay)
                            continue
                        else:
                            row_error = f"HTTP {code} persistent error after {max_retries} retries: {e}"
                            break
                    elif code is None:
                        row_error = f"ステータス不明の API エラー: {e}"
                        break
                    else:
                        row_error = f"API error (code={code}): {e}"
                        break

            if embedding_vector is not None:
                # 成功時: embedding_v2 と embedding_v2_at を更新（embedding_v2_error は NULL にクリア）
                now_iso = datetime.now(timezone.utc).isoformat()
                try:
                    supabase.table(TABLE_NAME).update({
                        "embedding_v2": embedding_vector,
                        "embedding_v2_at": now_iso,
                        "embedding_v2_error": None,
                    }).eq("id", row_id).execute()
                    success_count += 1
                    print(f"[{total_processed + 1}] ID: {row_id} -> 成功 ({EMBEDDING_DIM}次元)")
                except Exception as dbe:
                    sys.stderr.write(f"[ERROR] DB update failed for row {row_id}: {dbe}\n")
                    failed_count += 1
            else:
                # 失敗時: embedding_v2_error を記録（フォールバック絶対禁止）
                if row_error is None:
                    raise RuntimeError(f"row_error is None for failed row {row_id}")
                fail_reason = row_error
                print(f"[{total_processed + 1}] ID: {row_id} -> 失敗: {fail_reason}")
                try:
                    supabase.table(TABLE_NAME).update({
                        "embedding_v2_error": fail_reason,
                    }).eq("id", row_id).execute()
                except Exception as dbe:
                    sys.stderr.write(f"[ERROR] Failed to record error to DB for row {row_id}: {dbe}\n")
                failed_count += 1

            total_processed += 1
            if args.limit is not None and total_processed >= args.limit:
                break

    # 処理終了時のサマリー出力
    print_summary(total_processed, success_count, failed_count, skipped_count)


if __name__ == "__main__":
    main()
