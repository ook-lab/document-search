import os
import sys
import time
import json
import logging
import threading
from typing import Any, Dict, List
from flask import Flask, render_template, request, jsonify

try:
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_auth_requests
except ImportError:
    id_token = None
    google_auth_requests = None

# ワークスペースルートをパスに追加
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from standalone import (
    RAG_PREPARE_VECTORIZE_RAW_TABLES,
    RagServiceDB,
    fetch_pending_search_data_prep_docs,
    is_gemini_503_error,
)
from standalone.queries import compute_search_data_prep_counts

batch_vectorize_lock = threading.Lock()


def resolve_pipeline_lab_base() -> str:
    """
    pipeline-lab のベース URL（末尾スラッシュなし）。

    優先順: PIPELINE_LAB_BASE 環境変数
    → ローカル（K_SERVICE なし）: http://127.0.0.1:{PIPELINE_LAB_PORT|5055}
    → Cloud Run（K_SERVICE あり）: 推測は廃止し PIPELINE_LAB_BASE の明示設定を必須とする（未設定時は空文字）。
    """
    explicit = os.environ.get('PIPELINE_LAB_BASE', '').strip().rstrip('/')
    if explicit:
        return explicit
    if not os.environ.get('K_SERVICE'):
        port = (os.environ.get('PIPELINE_LAB_PORT') or '5055').strip()
        return f'http://127.0.0.1:{port}'
    return ''

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# インポートの遅延実行とエラーハンドリング
def get_indexer_tools():
    try:
        from standalone.indexer import RagPrepareSearchIndexer

        return RagPrepareSearchIndexer, RagServiceDB
    except ImportError as e:
        logger.error(f"Import Error: {e}")
        return None, None

@app.route('/')
def index():
    _, DbClass = get_indexer_tools()
    if not DbClass:
        return "System Configuration Error: Missing dependencies.", 500

    pending_docs = []
    list_error = None
    try:
        db = DbClass()
        raw_tables = list(RAG_PREPARE_VECTORIZE_RAW_TABLES)
        pending_docs, list_error = fetch_pending_search_data_prep_docs(
            db.client, raw_tables, include_vectorized=True
        )
        if list_error:
            logger.error("検索データ準備 一覧: %s", list_error)
    except Exception as e:
        logger.error(f"Failed to fetch pending docs: {e}")
        list_error = str(e)

    pipeline_lab = resolve_pipeline_lab_base()
    pipeline_lab_error = None
    if not pipeline_lab:
        pipeline_lab_error = (
            "環境変数 PIPELINE_LAB_BASE が設定されていません。"
            "Cloud Run の環境変数を設定してください。"
        )
        logger.error(pipeline_lab_error)

    counts = compute_search_data_prep_counts(pending_docs)

    return render_template(
        "search_data_prep.html",
        docs=pending_docs,
        counts=counts,
        list_error=list_error,
        pipeline_lab_base=pipeline_lab,
        pipeline_lab_error=pipeline_lab_error,
        process_post_url="/process",
    )

def _run_search_index_register():
    data = request.get_json(silent=True) or {}
    unified_doc_id = str(data.get("unified_doc_id") or "").strip()
    raw_table = (data.get("raw_table") or "").strip()
    raw_id = (data.get("raw_id") or "").strip()
    if not unified_doc_id and not (raw_table and raw_id):
        return jsonify({"success": False, "error": "Missing unified_doc_id or (raw_table, raw_id)"}), 400

    IndexerClass, _ = get_indexer_tools()
    if not IndexerClass:
        return jsonify({'success': False, 'error': 'System dependencies not loaded'}), 500

    indexer = IndexerClass()
    try:
        success, err_msg = indexer.process_document(
            unified_doc_id or None,
            raw_table=raw_table or None,
            raw_id=raw_id or None,
        )
    except Exception as e:
        success = False
        err_msg = str(e)

    if success:
        clr_ok, clr_err = indexer.clear_vectorize_error(
            raw_table=raw_table or None,
            raw_id=raw_id or None,
            doc_id=unified_doc_id or None,
        )
        if not clr_ok:
            logger.error(
                "Failed to clear vectorize error in DB (manual process): raw_table=%s, raw_id=%s, doc_id=%s: %s",
                raw_table, raw_id, unified_doc_id, clr_err
            )
            return jsonify({
                'success': True,
                'raw_table': raw_table or None,
                'raw_id': raw_id or None,
                'unified_doc_id': unified_doc_id or None,
                'clear_error': clr_err or "エラー理由なし（clear_vectorize_error の契約違反）",
            })
        return jsonify({'success': True})

    failure_reason = err_msg or "失敗理由なし（process_document の契約違反）"
    rec_ok, rec_err = indexer.record_vectorize_error(
        raw_table=raw_table or None,
        raw_id=raw_id or None,
        error_message=failure_reason,
        doc_id=unified_doc_id or None,
    )
    if not rec_ok:
        logger.error(
            "Failed to record vectorize error to DB (manual process): raw_table=%s, raw_id=%s, doc_id=%s: %s",
            raw_table, raw_id, unified_doc_id, rec_err
        )
        return jsonify({
            'success': False,
            'error': failure_reason,
            'record_error': f"Failed to record error to DB: {rec_err}",
        })

    return jsonify({'success': False, 'error': failure_reason})


def _run_date_signals_single():
    data = request.get_json(silent=True) or {}
    unified_doc_id = str(data.get("unified_doc_id") or "").strip()
    raw_table = (data.get("raw_table") or "").strip()
    raw_id = (data.get("raw_id") or "").strip()
    if not unified_doc_id and not (raw_table and raw_id):
        return jsonify({"success": False, "error": "Missing unified_doc_id or (raw_table, raw_id)"}), 400
    IndexerClass, _ = get_indexer_tools()
    if not IndexerClass:
        return jsonify({'success': False, 'error': 'System dependencies not loaded'}), 500
    indexer = IndexerClass()
    success, err_msg = indexer.process_date_signals_for_document(
        unified_doc_id or None,
        raw_table=raw_table or None,
        raw_id=raw_id or None,
    )
    if success:
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': err_msg or '失敗理由なし（process_document の契約違反）'})


def _run_date_signals_backfill():
    data = request.get_json(silent=True) or {}
    limit = int(data.get("limit") or 200)
    person = (data.get("person") or "").strip() or None
    source = (data.get("source") or "").strip() or None
    force = bool(data.get("force"))
    IndexerClass, _ = get_indexer_tools()
    if not IndexerClass:
        return jsonify({'success': False, 'error': 'System dependencies not loaded'}), 500
    indexer = IndexerClass()
    result = indexer.backfill_date_signals(limit=limit, person=person, source=source, force=force)
    return jsonify(result)

@app.route('/process', methods=['POST'])
def process():
    return _run_search_index_register()


@app.route('/process-date-signals', methods=['POST'])
def process_date_signals():
    return _run_date_signals_single()


@app.route('/backfill-date-signals', methods=['POST'])
def backfill_date_signals():
    return _run_date_signals_backfill()

@app.route('/skip-ix', methods=['POST'])
def skip_ix():
    data = request.get_json(silent=True) or {}
    raw_table = (data.get("raw_table") or "").strip()
    raw_id = (data.get("raw_id") or "").strip()
    if not raw_table or not raw_id:
        return jsonify({"success": False, "error": "Missing raw_table or raw_id"}), 400
    try:
        from standalone.ud_meta import UD_META_TABLE
        from datetime import datetime, timezone
        db = RagServiceDB()
        now_iso = datetime.now(timezone.utc).isoformat()
        db.client.table(UD_META_TABLE).update(
            {"ix_skip_pdf": True, "updated_at": now_iso}
        ).eq("raw_table", raw_table).eq("raw_id", raw_id).select("raw_id").execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error("skip_ix failed: %s", e, exc_info=True)
        return jsonify({'success': False, 'error': str(e)})


@app.route('/reset-ix-all', methods=['POST'])
def reset_ix_all():
    IndexerClass, _ = get_indexer_tools()
    if not IndexerClass:
        return jsonify({'success': False, 'error': 'System dependencies not loaded'}), 500
    indexer = IndexerClass()
    result = indexer.reset_all_ix_vectorized_at(list(RAG_PREPARE_VECTORIZE_RAW_TABLES))
    return jsonify(result)


def _verify_google_oidc(audience_env: str, invoker_email_env: str, log_prefix: str):
    """Google OIDC ID トークンを検証する共通ヘルパー。エラー時は (jsonify_response, status_code) を返し、成功時は None を返す。"""
    batch_audience = os.environ.get(audience_env)
    invoker_email = os.environ.get(invoker_email_env)
    if not batch_audience or not invoker_email:
        logger.error(
            "%s: Server configuration error. %s or %s is not set",
            log_prefix, audience_env, invoker_email_env
        )
        return jsonify({
            "error": f"Server configuration error: {audience_env} or {invoker_email_env} is not configured"
        }), 500

    auth_header = request.headers.get("Authorization", "").strip()
    if not auth_header.startswith("Bearer "):
        logger.warning("%s: Missing or invalid Authorization header", log_prefix)
        return jsonify({"error": "Missing or invalid Authorization header"}), 401

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        logger.warning("%s: Empty bearer token", log_prefix)
        return jsonify({"error": "Empty bearer token"}), 401

    if id_token is None or google_auth_requests is None:
        logger.error("%s: google-auth library is not available", log_prefix)
        return jsonify({"error": "Server configuration error: google-auth is not installed"}), 500

    try:
        auth_req = google_auth_requests.Request()
        id_info = id_token.verify_oauth2_token(token, auth_req, audience=batch_audience)
    except Exception as e:
        logger.warning("%s: Token verification failed: %s", log_prefix, e)
        return jsonify({"error": f"Invalid token: {e}"}), 401

    if not id_info.get("email_verified"):
        logger.warning("%s: Token email is not verified", log_prefix)
        return jsonify({"error": "Forbidden: email not verified"}), 403

    token_email = id_info.get("email")
    if token_email != invoker_email:
        logger.warning(
            "%s: Token email '%s' does not match allowed invoker '%s'",
            log_prefix, token_email, invoker_email
        )
        return jsonify({"error": "Forbidden: email mismatch"}), 403

    return None


@app.route('/api/internal/pending_pipeline_targets', methods=['GET'])
def get_pending_pipeline_targets():
    """パイプライン処理対象のドキュメント一覧を返す内部API（Google OIDC 認証）。
    対象: rag-prepare 一覧で『パイプライン処理』が出る（resolved_drive_id あり）
          かつ display_segment=='pending_md' かつ row_error なし かつ 新列 ix_pipeline_error が NULL。
    順序: display_post_at の新しい順（無いものは最後、同順位は raw_table, raw_id 順）。
    """
    auth_err = _verify_google_oidc(
        "RAG_PREPARE_BATCH_AUDIENCE",
        "RAG_PREPARE_BATCH_INVOKER_EMAIL",
        "Pending pipeline targets",
    )
    if auth_err:
        return auth_err

    _, DbClass = get_indexer_tools()
    if not DbClass:
        return jsonify({"error": "System dependencies not loaded"}), 500

    try:
        import functools
        db = DbClass()
        raw_tables = list(RAG_PREPARE_VECTORIZE_RAW_TABLES)
        all_docs, list_err = fetch_pending_search_data_prep_docs(
            db.client, raw_tables, include_vectorized=False
        )
        if list_err:
            logger.error("Pending pipeline targets: Failed to fetch pending docs: %s", list_err)
            return jsonify({"error": f"Failed to fetch pending docs: {list_err}"}), 500

        candidate_docs = [
            d for d in all_docs
            if d.get("resolved_drive_id")
            and d.get("display_segment") == "pending_md"
            and not d.get("row_error")
            and (not d.get("ix_pipeline_error") or is_gemini_503_error(d.get("ix_pipeline_error")))
        ]

        def _cmp_targets(a: Dict[str, Any], b: Dict[str, Any]) -> int:
            pa = a.get("display_post_at") or ""
            pb = b.get("display_post_at") or ""
            if bool(pa) != bool(pb):
                return -1 if pa else 1
            if pa != pb:
                return -1 if pa > pb else 1
            rta = a.get("raw_table") or ""
            rtb = b.get("raw_table") or ""
            if rta != rtb:
                return -1 if rta < rtb else 1
            ria = a.get("raw_id") or ""
            rib = b.get("raw_id") or ""
            if ria != rib:
                return -1 if ria < rib else 1
            return 0

        candidate_docs.sort(key=functools.cmp_to_key(_cmp_targets))

        targets = [
            {
                "raw_table": d.get("raw_table"),
                "raw_id": d.get("raw_id"),
                "resolved_drive_id": d.get("resolved_drive_id"),
                "display_post_at": d.get("display_post_at") or None,
                "title": d.get("title"),
            }
            for d in candidate_docs
        ]

        return jsonify({
            "count": len(targets),
            "targets": targets,
        }), 200

    except Exception as e:
        logger.error("Pending pipeline targets: Unexpected error: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/batch/vectorize', methods=['POST'])
def batch_vectorize():
    # 1. 認証: Google OIDC 検証
    auth_err = _verify_google_oidc(
        "RAG_PREPARE_BATCH_AUDIENCE",
        "RAG_PREPARE_BATCH_INVOKER_EMAIL",
        "Batch vectorize",
    )
    if auth_err:
        return auth_err

    # 2. 同時実行防止: プロセス内ロック
    if not batch_vectorize_lock.acquire(blocking=False):
        logger.warning("Batch vectorize: Another batch process is already running")
        return jsonify({"error": "Conflict: another batch vectorize job is already running"}), 409

    try:
        start_time = time.monotonic()
        IndexerClass, DbClass = get_indexer_tools()
        if not IndexerClass or not DbClass:
            return jsonify({"error": "System dependencies not loaded"}), 500

        db = DbClass()
        raw_tables = list(RAG_PREPARE_VECTORIZE_RAW_TABLES)
        all_docs, list_err = fetch_pending_search_data_prep_docs(
            db.client, raw_tables, include_vectorized=False
        )
        if list_err:
            logger.error("Batch vectorize: Failed to fetch pending docs: %s", list_err)
            return jsonify({"error": f"Failed to fetch pending docs: {list_err}"}), 500

        # 自動処理の対象: 画面で『ベクトル化登録』ボタンが押せて未登録の文書と完全に同じ条件
        # （fetch_pending_search_data_prep_docs の結果で、row_error なし、display_segment != 'pending_md'、is_vectorized False）
        # に加え、ix_vectorize_error が NULL のもの、または 503 由来の一時的失敗のもの。
        candidate_docs = [
            d for d in all_docs
            if not d.get("row_error")
            and d.get("display_segment") != "pending_md"
            and not d.get("is_vectorized")
            and (not d.get("ix_vectorize_error") or is_gemini_503_error(d.get("ix_vectorize_error")))
        ]
        candidate_count = len(candidate_docs)

        MAX_DOCS = int(os.environ.get("BATCH_VECTORIZE_MAX_DOCS", "50"))
        MAX_DURATION_SEC = float(os.environ.get("BATCH_VECTORIZE_MAX_DURATION_SEC", "240.0"))

        batch_targets = candidate_docs[:MAX_DOCS]
        target_count = len(batch_targets)

        indexer = IndexerClass()
        processed_count = 0
        success_count = 0
        failures: List[Dict[str, Any]] = []
        clear_errors: List[Dict[str, Any]] = []
        timed_out_remaining = 0

        for i, doc in enumerate(batch_targets):
            # 制限時間を超えたら次の文書に進まず打ち切る（残りは次回）
            elapsed = time.monotonic() - start_time
            if elapsed > MAX_DURATION_SEC:
                logger.warning(
                    "Batch vectorize: Execution timed out after %.2f seconds (limit %s s). Stopping before index %d.",
                    elapsed, MAX_DURATION_SEC, i
                )
                timed_out_remaining = len(batch_targets) - i
                break

            processed_count += 1
            u_id = doc.get("unified_doc_id")
            r_table = doc.get("raw_table")
            r_id = doc.get("raw_id")

            try:
                success, err_msg = indexer.process_document(
                    u_id or None,
                    raw_table=r_table or None,
                    raw_id=r_id or None,
                )
            except Exception as e:
                success = False
                err_msg = str(e)

            if success:
                success_count += 1
                clr_ok, clr_err = indexer.clear_vectorize_error(
                    raw_table=r_table or None,
                    raw_id=r_id or None,
                    doc_id=u_id or None,
                )
                if not clr_ok:
                    logger.error(
                        "Batch vectorize: Failed to clear error in DB for %s/%s: %s",
                        r_table, r_id, clr_err
                    )
                    clear_errors.append({
                        "raw_table": r_table,
                        "raw_id": r_id,
                        "clear_error": clr_err or "エラー理由なし（clear_vectorize_error の契約違反）",
                    })
            else:
                fail_reason = err_msg or "失敗理由なし（process_document の契約違反）"
                rec_ok, rec_err = indexer.record_vectorize_error(
                    raw_table=r_table or None,
                    raw_id=r_id or None,
                    error_message=fail_reason,
                    doc_id=u_id or None,
                )
                fail_item: Dict[str, Any] = {
                    "raw_table": r_table,
                    "raw_id": r_id,
                    "reason": fail_reason,
                }
                if not rec_ok:
                    logger.error(
                        "Batch vectorize: Failed to record error to DB for %s/%s: %s",
                        r_table, r_id, rec_err
                    )
                    fail_item["record_error"] = f"Failed to record error to DB: {rec_err}"
                failures.append(fail_item)

        total_elapsed = round(time.monotonic() - start_time, 2)
        summary = {
            "candidate_count": candidate_count,
            "target_count": target_count,
            "processed_count": processed_count,
            "success_count": success_count,
            "failure_count": len(failures),
            "failures": failures,
            "clear_errors": clear_errors,
            "timed_out_remaining": timed_out_remaining,
            "elapsed_seconds": total_elapsed,
        }

        logger.info("[RAG_BATCH_SUMMARY] %s", json.dumps(summary, ensure_ascii=False))
        return jsonify(summary), 200

    finally:
        batch_vectorize_lock.release()


@app.route('/api/health', methods=['GET'])
def health_check():
    IndexerClass, DbClass = get_indexer_tools()
    return jsonify({
        'status': 'ok',
        'service': 'rag-prepare',
        'dependencies_loaded': bool(IndexerClass and DbClass)
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), threaded=True)
