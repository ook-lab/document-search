"""
pipeline_batch: Gemini Batch API を用いた非同期・高品質パイプライン直接抽出処理。

毎時30分に定期実行され、
1. 実行中（submitted）のバッチ状態を確認し、未完了があれば結果取り込みのみ行い『返答待ち』として終了。
2. 完了したバッチがあれば1ページずつ検証・抽出して pipeline_batch_pages に保存し、
   全ページ成功したファイルのみ raw.pdf_md_content を更新して completed とする。
3. 未完了バッチが1件も無ければ、rag-prepare から次の対象を取得し、
   1ファイル=1バッチ（1ページ=1依頼）として Gemini 3.8 flash に送信（1回あたり最大7ファイル）。
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
from PIL import Image, ImageOps
from flask import Blueprint, jsonify, request
from loguru import logger as loguru_logger

try:
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_auth_requests
except ImportError:
    google_id_token = None
    google_auth_requests = None

from direct_extract_common import (
    DirectExtractPageResult,
    DirectExtractValidationError,
    PageBlock,
    _DIRECT_EXTRACT_PROMPT,
    render_page_to_png_bytes,
    validate_direct_extract_result,
    _synthesize_structured_markdown_from_blocks,
)
from dms.common.connectors.google_drive import GoogleDriveConnector
from dms.common.database.client import DatabaseClient

logger = logging.getLogger(__name__)

pipeline_batch_bp = Blueprint('pipeline_batch', __name__)
pipeline_batch_lock = threading.Lock()

# 1回の処理で新しく送信する最大ファイル（バッチ）数
MAX_NEW_BATCH_FILES_PER_RUN = 7

# 終了状態のジョブステータス一覧
_TERMINAL_JOB_STATES = frozenset({
    "JOB_STATE_SUCCEEDED", "SUCCEEDED",
    "JOB_STATE_FAILED", "FAILED",
    "JOB_STATE_CANCELLED", "CANCELLED",
    "JOB_STATE_EXPIRED", "EXPIRED",
})

# 対応ファイル形式の MIME タイプ定義（明示的列挙）
_PDF_MIME_TYPES = frozenset({
    "application/pdf",
})

_IMAGE_MIME_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/webp",
    "image/tiff",
    "image/bmp",
})

_WORD_MIME_TYPES = frozenset({
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/msword",  # .doc
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template",  # .dotx
    "application/vnd.ms-word.document.macroEnabled.12",  # .docm
    "application/vnd.ms-word.template.macroEnabled.12",  # .dotm
    "application/vnd.oasis.opendocument.text",  # .odt
    "application/vnd.oasis.opendocument.text-template",  # .ott
    "application/rtf",  # .rtf
    "text/rtf",  # .rtf
})

_SPREADSHEET_MIME_TYPES = frozenset({
    "application/vnd.google-apps.spreadsheet",
})

_SUPPORTED_MIME_TYPES = _PDF_MIME_TYPES | _IMAGE_MIME_TYPES | _WORD_MIME_TYPES | _SPREADSHEET_MIME_TYPES

_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.tif', '.tiff'}


def _is_image_file(name: str) -> bool:
    return Path(name).suffix.lower() in _IMAGE_EXTS


def _image_to_pdf(img_path: Path, pdf_path: Path) -> None:
    """写真・画像を 1ページの PDF に変換する。
    写真から作るページ画像が A4 の PDF を Matrix(3,3) で画像にした時の大きさ
    （A4=595.28x841.89pt の3倍≒1786x2526px。横長なら縦横入れ替え）の枠に収まるよう、
    大きい写真も小さい写真も、縦横比を保ったまま枠いっぱい（2つの比の小さい方の倍率）に拡大・縮小する。
    後続の render_page_to_png_bytes(doc, p_idx) による fitz.Matrix(3, 3) 処理と合わせて
    A4の3倍画像の枠内に収まる解像度のページ画像が生成される。
    """
    with Image.open(img_path) as raw_img:
        img = ImageOps.exif_transpose(raw_img)
        orig_w, orig_h = img.size

        # A4長辺 841.89pt の 3倍 (約2525.67px ≒ 2526px)、短辺 595.28pt の 3倍 (約1785.84px ≒ 1786px) の枠とする
        max_long_px = 841.89 * 3.0
        max_short_px = 595.28 * 3.0
        orig_long = max(orig_w, orig_h)
        orig_short = min(orig_w, orig_h)

        scale = min(max_long_px / orig_long, max_short_px / orig_short)
        target_w = max(1, round(orig_w * scale))
        target_h = max(1, round(orig_h * scale))
        if (target_w, target_h) != (orig_w, orig_h):
            resample_filter = getattr(Image, "Resampling", Image).LANCZOS
            img = img.resize((target_w, target_h), resample=resample_filter)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

    # 後続の render_page_to_png_bytes が fitz.Matrix(3, 3) でレンダリングした際に
    # target_w x target_h (px) になるよう、PDF ページのサイズ (pt) を target / 3.0 に設定する
    page_w_pt = target_w / 3.0
    page_h_pt = target_h / 3.0

    doc = fitz.open()
    page = doc.new_page(width=page_w_pt, height=page_h_pt)
    page.insert_image(page.rect, stream=img_bytes)
    doc.save(str(pdf_path))
    doc.close()


def _convert_word_to_pdf(src_path: Path, dest_pdf_path: Path, work_dir: Path) -> None:
    """LibreOffice (soffice) を用いて Word/文書ファイルを PDF に変換する。
    フォールバック絶対禁止。失敗時は RuntimeError を送出する。
    """
    lo_profile_dir = work_dir / "lo_profile"
    lo_profile_dir.mkdir(parents=True, exist_ok=True)
    profile_url = f"file:///{lo_profile_dir.as_posix().lstrip('/')}"

    cmd = [
        "soffice",
        "--headless",
        f"-env:UserInstallation={profile_url}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(work_dir),
        str(src_path),
    ]
    logger.info("Converting document to PDF via LibreOffice: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as te:
        raise RuntimeError(f"LibreOfficeによるPDF変換がタイムアウトしました (120秒): {te}") from te
    except FileNotFoundError as fnfe:
        raise RuntimeError(f"LibreOffice (soffice) がインストールされていないか実行できません: {fnfe}") from fnfe
    except Exception as e:
        raise RuntimeError(f"LibreOffice実行エラー: {e}") from e

    if proc.returncode != 0:
        err_msg = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"LibreOffice PDF変換に失敗しました (exit code {proc.returncode}): {err_msg}")

    # LibreOffice は <src_stem>.pdf を出力する
    converted_pdf = work_dir / f"{src_path.stem}.pdf"
    if not converted_pdf.is_file() or converted_pdf.stat().st_size == 0:
        raise RuntimeError(f"LibreOfficeによる変換後PDFファイルが見つからないか空です: {converted_pdf.name}")

    if converted_pdf != dest_pdf_path:
        if dest_pdf_path.exists():
            dest_pdf_path.unlink()
        converted_pdf.rename(dest_pdf_path)


def _fetch_cloud_run_id_token(audience: str) -> str:
    """google.oauth2.id_token.fetch_id_token を用いて target audience 向けの ID トークンを取得する。"""
    if google_auth_requests is None or google_id_token is None:
        raise RuntimeError("google-auth ライブラリが利用できません")
    auth_req = google_auth_requests.Request()
    return google_id_token.fetch_id_token(auth_req, audience)


TEMPORARY_503_PREFIX = "[TEMPORARY_503] "


def is_gemini_503_error(err: Any) -> bool:
    """Gemini API や Google 側の一時的不調（HTTP 503 UNAVAILABLE 等）由来のエラーかを判定する。

    例外オブジェクトのステータスコード/属性、原因例外、エラーメッセージ文字列のいずれからでも判定可能。
    """
    if err is None:
        return False

    # 1. 印のチェック
    if isinstance(err, str) and "[TEMPORARY_503]" in err:
        return True

    # 2. 例外オブジェクトの属性・原因例外の検査
    if isinstance(err, BaseException):
        for attr in ("code", "status_code", "http_status"):
            val = getattr(err, attr, None)
            if val == 503 or val == "503":
                return True
        resp = getattr(err, "response", None)
        if resp is not None:
            sc = getattr(resp, "status_code", None) or getattr(resp, "status", None)
            if sc == 503 or sc == "503":
                return True
        cause = getattr(err, "__cause__", None)
        if cause is not None and is_gemini_503_error(cause):
            return True
        ctx = getattr(err, "__context__", None)
        if ctx is not None and is_gemini_503_error(ctx):
            return True

    # 3. 辞書型の場合（API レスポンス辞書など）
    if isinstance(err, dict):
        if err.get("code") == 503 or err.get("status") == "UNAVAILABLE":
            return True
        error_dict = err.get("error")
        if isinstance(error_dict, dict):
            if error_dict.get("code") == 503 or error_dict.get("status") == "UNAVAILABLE":
                return True

    # 4. 文字列表現のパターン検査
    s = str(err)
    if "[TEMPORARY_503]" in s:
        return True

    # 代表的な 503 エラーパターン
    if "Authentication backend unavailable" in s:
        return True
    if "The service is currently unavailable" in s:
        return True
    if "503 UNAVAILABLE" in s or "503 Unavailable" in s:
        return True
    if "503 Service Unavailable" in s or "503 Server Error" in s:
        return True
    if "'code': 503" in s or '"code": 503' in s or "code: 503" in s:
        return True

    # 正規表現: 503 かつ (unavailable | backend | service)
    if re.search(r"\b503\b", s) and re.search(r"(unavailable|backend|service)", s, re.IGNORECASE):
        return True

    return False


def format_503_error(err: Any) -> str:
    """エラーメッセージを記録用にフォーマットする。

    503 由来のエラーの場合は先頭に [TEMPORARY_503] を付与する（既にあれば二重付与しない）。
    """
    err_str = str(err) if err is not None else ""
    if is_gemini_503_error(err):
        if not err_str.startswith("[TEMPORARY_503]"):
            return f"{TEMPORARY_503_PREFIX}{err_str}"
    return err_str


def _record_meta_pipeline_error(db: DatabaseClient, raw_table: str, raw_id: str, error_msg: str) -> None:
    """09_unified_documents_meta にパイプライン失敗理由と日時を記録する。"""
    formatted_msg = format_503_error(error_msg)
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        upd = (
            db.client.table("09_unified_documents_meta")
            .update({
                "ix_pipeline_error": formatted_msg,
                "ix_pipeline_error_at": now_iso,
                "updated_at": now_iso,
            })
            .eq("raw_table", raw_table)
            .eq("raw_id", raw_id)
            .select("raw_id")
            .execute()
        )
        if not upd.data:
            raise RuntimeError(f"meta 行が存在しない (raw_table={raw_table}, raw_id={raw_id})")
    except Exception as e:
        logger.error("Failed to record meta pipeline error for %s/%s: %s", raw_table, raw_id, e)
        raise


def _clear_meta_pipeline_error(db: DatabaseClient, raw_table: str, raw_id: str) -> None:
    """09_unified_documents_meta のパイプライン失敗理由をクリアする。"""
    try:
        upd = (
            db.client.table("09_unified_documents_meta")
            .update({
                "ix_pipeline_error": None,
                "ix_pipeline_error_at": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("raw_table", raw_table)
            .eq("raw_id", raw_id)
            .select("raw_id")
            .execute()
        )
        if not upd.data:
            raise RuntimeError(f"meta 行が存在しない (raw_table={raw_table}, raw_id={raw_id})")
    except Exception as e:
        logger.error("Failed to clear meta pipeline error for %s/%s: %s", raw_table, raw_id, e)
        raise


def _record_batch_file_error(
    db: DatabaseClient,
    raw_table: str,
    raw_id: str,
    drive_file_id: Optional[str],
    job_name: str,
    error_msg: str,
    total_pages: int = 0,
) -> None:
    """pipeline_batch_files に state='error' を記録（または更新）する。"""
    formatted_msg = format_503_error(error_msg)
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        db.client.table("pipeline_batch_files").upsert(
            {
                "raw_table": raw_table,
                "raw_id": raw_id,
                "drive_file_id": drive_file_id,
                "total_pages": total_pages,
                "job_name": job_name,
                "state": "error",
                "submitted_at": now_iso,
                "finished_at": now_iso,
                "error": formatted_msg,
            },
            on_conflict="raw_table,raw_id",
        ).execute()
    except Exception as e:
        logger.error("Failed to record pipeline_batch_files error for %s/%s: %s", raw_table, raw_id, e)


def _save_batch_completed_result(
    db: DatabaseClient,
    raw_table: str,
    raw_id: str,
    full_md: str,
    now_iso: Optional[str] = None,
    page_markdowns: Optional[Dict[int, str]] = None,
) -> None:
    """通常のバッチ完了時および再合成時共通の保存処理。
    RAW テーブルの pdf_md_content / pdf_md_updated_at を更新し、
    pipeline_batch_files の state を completed に更新し、
    09_unified_documents_meta のパイプラインエラーをクリアする。
    """
    if not now_iso:
        now_iso = datetime.now(timezone.utc).isoformat()

    # RAW テーブル保存（同じ列: pdf_md_content, pdf_md_updated_at）
    upd_raw = (
        db.client.table(raw_table)
        .update({
            "pdf_md_content": full_md,
            "pdf_md_updated_at": now_iso,
        })
        .eq("id", raw_id)
        .select("id")
        .execute()
    )
    if not upd_raw.data:
        raise RuntimeError(f"RAWテーブルの更新対象が見つかりません (table={raw_table}, id={raw_id})")

    # pipeline_batch_files を completed に更新
    db.client.table("pipeline_batch_files").update({
        "state": "completed",
        "finished_at": now_iso,
        "error": None,
    }).eq("raw_table", raw_table).eq("raw_id", raw_id).execute()

    # pipeline_batch_pages の各ページの markdown 列も更新（page_markdowns が指定されている場合）
    if page_markdowns:
        for p_idx, p_md in page_markdowns.items():
            db.client.table("pipeline_batch_pages").update({
                "markdown": p_md,
            }).eq("raw_table", raw_table).eq("raw_id", raw_id).eq("page_index", p_idx).execute()

    # 09_unified_documents_meta のエラーをクリア
    _clear_meta_pipeline_error(db, raw_table, raw_id)


def _verify_oidc_token(req: Any) -> Optional[Tuple[Any, int]]:
    """Google OIDC ID トークン検証。
    成功時は None、失敗時は (jsonify(...), status_code) を返す。
    """
    batch_audience = os.environ.get("PIPELINE_BATCH_AUDIENCE")
    invoker_email = os.environ.get("PIPELINE_BATCH_INVOKER_EMAIL")
    if not batch_audience or not invoker_email:
        logger.error("Batch pipeline: PIPELINE_BATCH_AUDIENCE or PIPELINE_BATCH_INVOKER_EMAIL is not set")
        return jsonify({
            "error": "Server configuration error: PIPELINE_BATCH_AUDIENCE or PIPELINE_BATCH_INVOKER_EMAIL is not configured"
        }), 500

    auth_header = req.headers.get("Authorization", "").strip()
    if not auth_header.startswith("Bearer "):
        logger.warning("Batch pipeline: Missing or invalid Authorization header")
        return jsonify({"error": "Missing or invalid Authorization header"}), 401

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        logger.warning("Batch pipeline: Empty bearer token")
        return jsonify({"error": "Empty bearer token"}), 401

    if google_id_token is None or google_auth_requests is None:
        logger.error("Batch pipeline: google-auth library is not available")
        return jsonify({"error": "Server configuration error: google-auth is not installed"}), 500

    try:
        auth_req = google_auth_requests.Request()
        id_info = google_id_token.verify_oauth2_token(token, auth_req, audience=batch_audience)
    except Exception as e:
        logger.warning("Batch pipeline: Token verification failed: %s", e)
        return jsonify({"error": f"Invalid token: {e}"}), 401

    if not id_info.get("email_verified"):
        logger.warning("Batch pipeline: Token email is not verified")
        return jsonify({"error": "Forbidden: email not verified"}), 403

    token_email = id_info.get("email")
    if token_email != invoker_email:
        logger.warning(
            "Batch pipeline: Token email '%s' does not match allowed invoker '%s'",
            token_email, invoker_email
        )
        return jsonify({"error": "Forbidden: email mismatch"}), 403

    return None


@pipeline_batch_bp.route('/api/batch/pipeline_direct', methods=['POST'])
@pipeline_batch_bp.route('/pipeline-lab/api/batch/pipeline_direct', methods=['POST'])
def batch_pipeline_direct():
    """Gemini Batch API を用いた一括パイプライン直接抽出エンドポイント。Google OIDC 認証。"""
    auth_err = _verify_oidc_token(request)
    if auth_err is not None:
        return auth_err

    # 同時実行防止: プロセス内ロック
    if not pipeline_batch_lock.acquire(blocking=False):
        logger.warning("Batch pipeline: Another batch process is already running")
        return jsonify({"error": "Conflict: another batch pipeline job is already running"}), 409

    try:
        return _run_pipeline_batch_process()
    finally:
        pipeline_batch_lock.release()


@pipeline_batch_bp.route('/api/batch/resynthesize', methods=['POST'])
@pipeline_batch_bp.route('/pipeline-lab/api/batch/resynthesize', methods=['POST'])
def batch_resynthesize():
    """保存済みのページの読み取り結果 (pipeline_batch_pages の blocks) から、
    そのファイルの Markdown を現在の合成処理で作り直して保存し直す管理用エンドポイント。
    """
    auth_err = _verify_oidc_token(request)
    if auth_err is not None:
        return auth_err

    body = request.get_json(silent=True) or {}
    raw_table = body.get("raw_table")
    raw_id = body.get("raw_id")

    if not raw_table or not isinstance(raw_table, str) or not raw_table.strip():
        return jsonify({"error": "リクエスト本文に有効な 'raw_table' が必要です"}), 400
    if not raw_id or not isinstance(raw_id, str) or not raw_id.strip():
        return jsonify({"error": "リクエスト本文に有効な 'raw_id' が必要です"}), 400

    raw_table = raw_table.strip()
    raw_id = raw_id.strip()

    db = DatabaseClient(use_service_role=True)

    # 1. pipeline_batch_files の確認
    try:
        file_res = (
            db.client.table("pipeline_batch_files")
            .select("raw_table, raw_id, total_pages, state")
            .eq("raw_table", raw_table)
            .eq("raw_id", raw_id)
            .execute()
        )
        if not file_res.data:
            return jsonify({
                "error": f"pipeline_batch_files に対象レコードが見つかりません (raw_table={raw_table}, raw_id={raw_id})"
            }), 404
        file_row = file_res.data[0]
    except Exception as e:
        logger.error("Failed to query pipeline_batch_files for resynthesize: %s", e)
        return jsonify({"error": f"Database query failed: {e}"}), 500

    raw_total_pages = file_row.get("total_pages")
    if not isinstance(raw_total_pages, int) or raw_total_pages < 1:
        return jsonify({
            "error": f"total_pages が不正です (期待値: 1以上の整数, 実際: {raw_total_pages!r})"
        }), 400
    total_pages: int = raw_total_pages

    # 2. pipeline_batch_pages の確認・取得
    try:
        pages_res = (
            db.client.table("pipeline_batch_pages")
            .select("page_index, blocks")
            .eq("raw_table", raw_table)
            .eq("raw_id", raw_id)
            .execute()
        )
        pages_rows = list(pages_res.data or [])
    except Exception as e:
        logger.error("Failed to query pipeline_batch_pages for resynthesize: %s", e)
        return jsonify({"error": f"Database query failed: {e}"}), 500

    pages_by_idx: Dict[int, Any] = {}
    for p_row in pages_rows:
        p_idx = p_row.get("page_index")
        if isinstance(p_idx, int):
            pages_by_idx[p_idx] = p_row.get("blocks")

    # 全ページが保存されているか検証（欠損・推測埋めは絶対禁止）
    missing_pages = [i for i in range(total_pages) if i not in pages_by_idx]
    if missing_pages:
        err_msg = f"そのファイルの全ページが保存されていません (全 {total_pages} ページ中、未保存ページ: {missing_pages})"
        logger.error("Resynthesize failed for %s/%s: %s", raw_table, raw_id, err_msg)
        return jsonify({
            "error": err_msg,
            "total_pages": total_pages,
            "missing_pages": missing_pages,
        }), 400

    # 3. 各ページの blocks から現在の合成処理で Markdown を再合成
    page_markdowns: Dict[int, str] = {}
    for p_idx in range(total_pages):
        raw_blocks = pages_by_idx[p_idx]
        if not raw_blocks or not isinstance(raw_blocks, list):
            err_msg = f"ページ {p_idx} の blocks が存在しないか配列ではありません"
            logger.error(err_msg)
            return jsonify({"error": err_msg}), 400

        try:
            page_blocks = [PageBlock.model_validate(b) for b in raw_blocks]
        except Exception as ve:
            err_msg = f"ページ {p_idx} の blocks スキーマ復元に失敗しました: {ve}"
            logger.error(err_msg)
            return jsonify({"error": err_msg}), 400

        try:
            page_md, tables_data, ui_summary = _synthesize_structured_markdown_from_blocks(page_blocks)
        except Exception as se:
            err_msg = f"ページ {p_idx} のMarkdown再合成エラー: {se}"
            logger.error(err_msg)
            return jsonify({"error": err_msg}), 400

        if not page_md or not page_md.strip():
            err_msg = f"ページ {p_idx} の再合成Markdownが空です"
            logger.error(err_msg)
            return jsonify({"error": err_msg}), 400

        page_markdowns[p_idx] = page_md

    # 4. 全ページ順番どおりに結合（'## Page {n}' 見出し、'\n\n' 区切り）
    full_md_parts = [f"## Page {i + 1}\n\n{page_markdowns[i]}" for i in range(total_pages)]
    full_md = "\n\n".join(full_md_parts)

    # 5. 通常のバッチ完了時と同じ関数・同じ列で保存
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        _save_batch_completed_result(
            db=db,
            raw_table=raw_table,
            raw_id=raw_id,
            full_md=full_md,
            now_iso=now_iso,
            page_markdowns=page_markdowns,
        )
        logger.info(
            "Successfully resynthesized and saved RAW MD for %s/%s (pages=%d)",
            raw_table, raw_id, total_pages
        )
    except Exception as save_err:
        err_msg = f"Markdownの保存に失敗しました: {save_err}"
        logger.error("Resynthesize save failed for %s/%s: %s", raw_table, raw_id, err_msg)
        return jsonify({"error": err_msg}), 500

    return jsonify({
        "success": True,
        "raw_table": raw_table,
        "raw_id": raw_id,
        "total_pages": total_pages,
    }), 200


def _run_pipeline_batch_process():
    db = DatabaseClient(use_service_role=True)

    # Gemini クライアント初期化（GOOGLE_AI_PAID_API_KEY のみ）
    api_key = os.environ.get("GOOGLE_AI_PAID_API_KEY")
    if not api_key:
        logger.error("Batch pipeline: GOOGLE_AI_PAID_API_KEY is not set")
        return jsonify({"error": "Server configuration error: GOOGLE_AI_PAID_API_KEY is not configured"}), 500

    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError as e:
        logger.error("Batch pipeline: google-genai is not installed: %s", e)
        return jsonify({"error": f"google-genai import failed: {e}"}), 500

    client = genai.Client(
        api_key=api_key,
        http_options=genai_types.HttpOptions(timeout=180000),
    )

    # ---------------------------------------------------------
    # 2. pipeline_batch_files の state='submitted' を確認
    # ---------------------------------------------------------
    try:
        sub_res = (
            db.client.table("pipeline_batch_files")
            .select("raw_table, raw_id, drive_file_id, total_pages, job_name, state, submitted_at")
            .eq("state", "submitted")
            .execute()
        )
        submitted_rows = list(sub_res.data or [])
    except Exception as e:
        logger.error("Failed to query pipeline_batch_files: %s", e)
        return jsonify({"error": f"Database query failed: {e}"}), 500

    has_pending_job = False
    completed_batches: List[Dict[str, Any]] = []
    saved_files: List[Dict[str, Any]] = []
    failed_files: List[Dict[str, Any]] = []
    status_check_errors: List[Dict[str, Any]] = []

    for row in submitted_rows:
        rt = row.get("raw_table")
        rid = row.get("raw_id")
        job_name = row.get("job_name")
        raw_total_pages = row.get("total_pages")
        if not isinstance(raw_total_pages, int) or raw_total_pages < 1:
            err_reason = f"total_pages が不正です (期待値: 1以上の整数, 実際: {raw_total_pages!r})"
            logger.error("Invalid total_pages for %s/%s: %s", rt, rid, err_reason)
            now_iso = datetime.now(timezone.utc).isoformat()
            db.client.table("pipeline_batch_files").update({
                "state": "error",
                "finished_at": now_iso,
                "error": err_reason,
            }).eq("raw_table", rt).eq("raw_id", rid).execute()
            failed_entry = {"raw_table": rt, "raw_id": rid, "reason": err_reason}
            try:
                _record_meta_pipeline_error(db, rt, rid, err_reason)
            except Exception as e:
                failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
            failed_files.append(failed_entry)
            continue

        total_pages: int = raw_total_pages

        try:
            batch_job = client.batches.get(name=job_name)
        except Exception as e:
            logger.error("client.batches.get failed for %s (%s/%s): %s", job_name, rt, rid, e)
            # そのファイルは submitted のまま（次回再確認）とし、失敗を status_check_errors に記録
            status_check_errors.append({
                "raw_table": rt,
                "raw_id": rid,
                "job_name": job_name,
                "reason": str(e),
            })
            continue

        st_val = getattr(batch_job.state, "name", None) or str(batch_job.state)
        st_val_upper = st_val.upper()

        if st_val_upper not in _TERMINAL_JOB_STATES:
            # PENDING / RUNNING / QUEUED 等の未完了
            has_pending_job = True
            continue

        # 完了したバッチ
        completed_batches.append({
            "raw_table": rt,
            "raw_id": rid,
            "job_name": job_name,
            "state": st_val_upper,
        })
        now_iso = datetime.now(timezone.utc).isoformat()

        if st_val_upper in ("JOB_STATE_SUCCEEDED", "SUCCEEDED"):
            # 結果取り込み: batch_job.dest.inlined_responses のみを使用（推測・フォールバックなし）
            dest_obj = getattr(batch_job, "dest", None)
            inlined_responses = getattr(dest_obj, "inlined_responses", None) if dest_obj is not None else None

            if not inlined_responses or not isinstance(inlined_responses, list):
                fail_reason = f"バッチジョブは成功しましたが batch_job.dest.inlined_responses が空または取得できませんでした (job={job_name})"
                logger.error(fail_reason)
                db.client.table("pipeline_batch_files").update({
                    "state": "error",
                    "finished_at": now_iso,
                    "error": fail_reason,
                }).eq("raw_table", rt).eq("raw_id", rid).execute()
                failed_entry = {"raw_table": rt, "raw_id": rid, "reason": fail_reason}
                try:
                    _record_meta_pipeline_error(db, rt, rid, fail_reason)
                except Exception as e:
                    failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
                failed_files.append(failed_entry)
                continue

            # 各ページの検証・取り込み
            page_results_by_idx: Dict[int, Tuple[str, List[Dict[str, Any]]]] = {}
            extract_error: Optional[str] = None

            for resp_seq, resp_item in enumerate(inlined_responses):
                # metadata の page_index を検証（AI本文による補完や変換失敗のpassは禁止）
                meta_dict = getattr(resp_item, "metadata", None)
                if not meta_dict or not isinstance(meta_dict, dict) or "page_index" not in meta_dict:
                    extract_error = f"応答 #{resp_seq} に metadata または page_index が存在しません"
                    break

                try:
                    p_idx = int(meta_dict["page_index"])
                except (ValueError, TypeError) as conv_err:
                    extract_error = f"応答 #{resp_seq} の metadata['page_index'] ('{meta_dict.get('page_index')}') を整数に変換できません: {conv_err}"
                    break

                # 同じ page_index の応答が重複して来た場合はエラー
                if p_idx in page_results_by_idx:
                    extract_error = f"ページ {p_idx} の応答が重複して受信されました (応答 #{resp_seq})"
                    break

                item_err = getattr(resp_item, "error", None)
                if item_err:
                    extract_error = f"ページ {p_idx} のGemini応答エラー: {item_err}"
                    break

                gen_resp = getattr(resp_item, "response", None)
                if not gen_resp:
                    extract_error = f"ページ {p_idx} のレスポンスが存在しません"
                    break

                raw_text = getattr(gen_resp, "text", None)
                if not raw_text or not raw_text.strip():
                    extract_error = f"ページ {p_idx} のレスポンス本文が空です"
                    break

                try:
                    parsed = DirectExtractPageResult.model_validate_json(raw_text)
                except Exception as ve:
                    extract_error = f"ページ {p_idx} のスキーマ検証エラー: {ve}"
                    break

                try:
                    validate_direct_extract_result(parsed)
                except DirectExtractValidationError as de:
                    extract_error = f"ページ {p_idx} のブロック検証エラー: {de.message}"
                    break

                try:
                    page_md, tables_data, ui_summary = _synthesize_structured_markdown_from_blocks(parsed.blocks)
                except Exception as se:
                    extract_error = f"ページ {p_idx} のMarkdown合成エラー: {se}"
                    break

                blocks_dump = [b.model_dump() for b in parsed.blocks]
                page_results_by_idx[p_idx] = (page_md, blocks_dump)

            # 全ページ揃っているか確認
            if not extract_error:
                missing_pages = [i for i in range(total_pages) if i not in page_results_by_idx]
                if missing_pages:
                    extract_error = f"全 {total_pages} ページ中、未取得ページがあります: {missing_pages}"

            # ページの Markdown が空でないか確認（空ならエラー）
            if not extract_error:
                for i in range(total_pages):
                    p_md_text = page_results_by_idx[i][0]
                    if not p_md_text or not p_md_text.strip():
                        extract_error = f"ページ {i} の生成Markdownが空です"
                        break

            if extract_error:
                logger.error("Extract failed for %s/%s: %s", rt, rid, extract_error)
                formatted_extract_err = format_503_error(extract_error)
                db.client.table("pipeline_batch_files").update({
                    "state": "error",
                    "finished_at": now_iso,
                    "error": formatted_extract_err,
                }).eq("raw_table", rt).eq("raw_id", rid).execute()
                failed_entry = {"raw_table": rt, "raw_id": rid, "reason": formatted_extract_err}
                try:
                    _record_meta_pipeline_error(db, rt, rid, formatted_extract_err)
                except Exception as e:
                    failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
                failed_files.append(failed_entry)
            else:
                # 全ページ成功: pipeline_batch_pages に全ページを一括保存（失敗したファイルのページは保存しない）
                try:
                    page_now = datetime.now(timezone.utc).isoformat()
                    pages_to_insert = [
                        {
                            "raw_table": rt,
                            "raw_id": rid,
                            "page_index": p_i,
                            "markdown": page_results_by_idx[p_i][0],
                            "blocks": page_results_by_idx[p_i][1],
                            "created_at": page_now,
                        }
                        for p_i in range(total_pages)
                    ]
                    db.client.table("pipeline_batch_pages").upsert(
                        pages_to_insert,
                        on_conflict="raw_table,raw_id,page_index"
                    ).execute()
                except Exception as pages_save_err:
                    save_err_msg = f"pipeline_batch_pages への保存に失敗: {pages_save_err}"
                    logger.error(save_err_msg)
                    db.client.table("pipeline_batch_files").update({
                        "state": "error",
                        "finished_at": now_iso,
                        "error": save_err_msg,
                    }).eq("raw_table", rt).eq("raw_id", rid).execute()
                    failed_entry = {"raw_table": rt, "raw_id": rid, "reason": save_err_msg}
                    try:
                        _record_meta_pipeline_error(db, rt, rid, save_err_msg)
                    except Exception as e:
                        failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
                    failed_files.append(failed_entry)
                    continue

                # 全ページ順番どおりに結合（'## Page {n}' 見出し、'\n\n' 区切り）
                full_md_parts = [f"## Page {i + 1}\n\n{page_results_by_idx[i][0]}" for i in range(total_pages)]
                full_md = "\n\n".join(full_md_parts)

                # RAW 保存（通常バッチ完了時と再合成時で共通の関数・共通の列を使用）
                try:
                    _save_batch_completed_result(
                        db=db,
                        raw_table=rt,
                        raw_id=rid,
                        full_md=full_md,
                        now_iso=now_iso,
                    )
                    saved_files.append({"raw_table": rt, "raw_id": rid, "pages": total_pages})
                    logger.info("Successfully extracted and saved RAW MD for %s/%s (pages=%d)", rt, rid, total_pages)
                except Exception as save_raw_err:
                    fail_msg = f"RAWテーブルへのMD保存に失敗: {save_raw_err}"
                    logger.error(fail_msg)
                    db.client.table("pipeline_batch_files").update({
                        "state": "error",
                        "finished_at": now_iso,
                        "error": fail_msg,
                    }).eq("raw_table", rt).eq("raw_id", rid).execute()
                    failed_entry = {"raw_table": rt, "raw_id": rid, "reason": fail_msg}
                    try:
                        _record_meta_pipeline_error(db, rt, rid, fail_msg)
                    except Exception as e:
                        failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
                    failed_files.append(failed_entry)

        else:
            # FAILED / EXPIRED / CANCELLED
            job_err = getattr(batch_job, "error", None)
            fail_reason = f"Geminiバッチジョブが終了ステータス '{st_val_upper}' で失敗しました (job={job_name})"
            if job_err:
                fail_reason += f": {job_err}"
            formatted_fail_reason = format_503_error(fail_reason) if (is_gemini_503_error(job_err) or is_gemini_503_error(fail_reason)) else fail_reason
            logger.error(formatted_fail_reason)
            db.client.table("pipeline_batch_files").update({
                "state": "error",
                "finished_at": now_iso,
                "error": formatted_fail_reason,
            }).eq("raw_table", rt).eq("raw_id", rid).execute()
            failed_entry = {"raw_table": rt, "raw_id": rid, "reason": formatted_fail_reason}
            try:
                _record_meta_pipeline_error(db, rt, rid, formatted_fail_reason)
            except Exception as e:
                failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
            failed_files.append(failed_entry)

    # ---------------------------------------------------------
    # 2.5 バッチ状態取得に失敗したファイルがある場合は 502 エラーで終了（新規ファイルは送らない）
    # ---------------------------------------------------------
    if status_check_errors:
        logger.error("Batch status check failed with %d error(s): %s", len(status_check_errors), status_check_errors)
        summary = {
            "success": False,
            "error": "Batch status check failed",
            "status_check_errors": status_check_errors,
            "completed_batches": completed_batches,
            "saved_files": saved_files,
            "failed_files": failed_files,
            "submitted_files": [],
        }
        logger.error("[PIPELINE_BATCH_SUMMARY] %s", json.dumps(summary, ensure_ascii=False))
        return jsonify(summary), 502

    # ---------------------------------------------------------
    # 3. 未完了バッチがある場合は新規ファイルを送らず終了（返答待ち）
    # ---------------------------------------------------------
    submitted_files: List[Dict[str, Any]] = []

    if has_pending_job:
        logger.info("Batch pipeline: Pending batch job exists. Skipping new submissions (waiting for response).")
        summary = {
            "success": True,
            "is_waiting_for_response": True,
            "status_check_errors": [],
            "completed_batches": completed_batches,
            "saved_files": saved_files,
            "failed_files": failed_files,
            "submitted_files": [],
        }
        logger.info("[PIPELINE_BATCH_SUMMARY] %s", json.dumps(summary, ensure_ascii=False))
        return jsonify(summary), 200

    # ---------------------------------------------------------
    # 4. 未完了バッチが1件も無ければ新しいファイルを送る
    # ---------------------------------------------------------
    rag_prepare_base = os.environ.get("RAG_PREPARE_BASE", "").strip().rstrip("/")
    if not rag_prepare_base:
        err_msg = "環境変数 RAG_PREPARE_BASE が未設定です"
        logger.error(err_msg)
        return jsonify({"error": err_msg}), 500

    # rag-prepare の内部APIから対象取得
    try:
        target_token = _fetch_cloud_run_id_token(rag_prepare_base)
    except Exception as e:
        logger.error("Failed to obtain ID token for RAG_PREPARE_BASE: %s", e)
        return jsonify({"error": f"Failed to obtain ID token for RAG_PREPARE_BASE: {e}"}), 500

    import urllib.request
    target_api_url = f"{rag_prepare_base}/api/internal/pending_pipeline_targets"
    target_req = urllib.request.Request(
        target_api_url,
        headers={"Authorization": f"Bearer {target_token}"},
        method="GET"
    )
    try:
        with urllib.request.urlopen(target_req, timeout=30) as target_resp:
            resp_data = json.loads(target_resp.read().decode("utf-8"))
            targets = resp_data.get("targets") or []
    except Exception as e:
        logger.error("Failed to fetch pending pipeline targets from %s: %s", target_api_url, e)
        return jsonify({"error": f"Failed to fetch pending pipeline targets: {e}"}), 500

    # pipeline_batch_files のうち submitted / completed の行がある文書は対象外。
    # state が error の行は、503 由来であれば再送対象（除外しない）。
    # 503 以外の永続的エラーは除外して止める。
    try:
        existing_res = db.client.table("pipeline_batch_files").select("raw_table, raw_id, state, error").execute()
        excluded_pairs = set()
        for r in (existing_res.data or []):
            st = r.get("state")
            err = r.get("error")
            if st in ("submitted", "completed"):
                excluded_pairs.add((r.get("raw_table"), r.get("raw_id")))
            elif st == "error" and not is_gemini_503_error(err):
                # 503 以外の恒久的エラーは再送対象から除外して止める
                excluded_pairs.add((r.get("raw_table"), r.get("raw_id")))
    except Exception as e:
        logger.error("Failed to query existing pipeline_batch_files: %s", e)
        return jsonify({"error": f"Database query failed: {e}"}), 500

    eligible_targets = [
        t for t in targets
        if (t.get("raw_table"), t.get("raw_id")) not in excluded_pairs
    ]

    drive = GoogleDriveConnector()

    for target in eligible_targets:
        if len(submitted_files) >= MAX_NEW_BATCH_FILES_PER_RUN:
            break
        rt = target.get("raw_table")
        rid = target.get("raw_id")
        drive_file_id = target.get("resolved_drive_id")
        if not rt or not rid or not drive_file_id:
            missing_fields = []
            if not rt:
                missing_fields.append("raw_table")
            if not rid:
                missing_fields.append("raw_id")
            if not drive_file_id:
                missing_fields.append("resolved_drive_id")
            err_reason = f"パイプライン対象の必須項目が欠損しています: {', '.join(missing_fields)} (target={target})"
            logger.error(err_reason)
            failed_entry = {"raw_table": rt or "UNKNOWN", "raw_id": rid or "UNKNOWN", "reason": err_reason}
            if rt and rid:
                try:
                    _record_meta_pipeline_error(db, rt, rid, err_reason)
                except Exception as e:
                    failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
            failed_files.append(failed_entry)
            continue

        # Drive から PDF を取得
        temp_dir = tempfile.mkdtemp(prefix="pipeline_batch_")
        try:
            temp_path = Path(temp_dir)
            try:
                meta = drive.service.files().get(
                    fileId=drive_file_id,
                    fields="name,mimeType",
                    supportsAllDrives=True
                ).execute()
                filename = meta.get("name")
                mime_type = meta.get("mimeType")
                if not filename or not mime_type:
                    err_reason = f"Driveファイルのメタデータ不足 (name={filename}, mimeType={mime_type}, fileId={drive_file_id})"
                    logger.error(err_reason)
                    failed_entry = {"raw_table": rt, "raw_id": rid, "reason": err_reason}
                    try:
                        _record_meta_pipeline_error(db, rt, rid, err_reason)
                    except Exception as e:
                        failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
                    failed_files.append(failed_entry)
                    continue

                # 対応形式の判定（マイム型で明示的に列挙、それ以外は対応外の形式として失敗）
                if mime_type in _PDF_MIME_TYPES:
                    doc_format = "pdf"
                elif mime_type in _IMAGE_MIME_TYPES:
                    doc_format = "image"
                elif mime_type in _WORD_MIME_TYPES:
                    doc_format = "word"
                elif mime_type in _SPREADSHEET_MIME_TYPES:
                    doc_format = "spreadsheet"
                else:
                    err_reason = f"対応外のファイル形式です (name={filename}, mime={mime_type})"
                    logger.error(err_reason)
                    failed_entry = {"raw_table": rt, "raw_id": rid, "reason": err_reason}
                    try:
                        _record_meta_pipeline_error(db, rt, rid, err_reason)
                    except Exception as e:
                        failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
                    _record_batch_file_error(db, rt, rid, drive_file_id, "UNSUPPORTED_FORMAT", err_reason)
                    failed_files.append(failed_entry)
                    continue

                try:
                    downloaded = drive.download_file(drive_file_id, filename, temp_path)
                except Exception as dl_ex:
                    if doc_format == "spreadsheet":
                        err_reason = f"GoogleスプレッドシートのPDFエクスポートに失敗しました (fileId={drive_file_id}): {dl_ex}"
                        fail_code = "EXPORT_FAILED"
                    else:
                        err_reason = f"Drive からのファイルダウンロードに失敗しました (fileId={drive_file_id}): {dl_ex}"
                        fail_code = "DOWNLOAD_FAILED"
                    logger.error(err_reason)
                    failed_entry = {"raw_table": rt, "raw_id": rid, "reason": err_reason}
                    try:
                        _record_meta_pipeline_error(db, rt, rid, err_reason)
                    except Exception as e:
                        failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
                    _record_batch_file_error(db, rt, rid, drive_file_id, fail_code, err_reason)
                    failed_files.append(failed_entry)
                    continue

                if not downloaded:
                    if doc_format == "spreadsheet":
                        err_reason = f"GoogleスプレッドシートのPDFエクスポートに失敗しました (fileId={drive_file_id})"
                        fail_code = "EXPORT_FAILED"
                    else:
                        err_reason = f"Drive からのファイルダウンロードに失敗しました (fileId={drive_file_id})"
                        fail_code = "DOWNLOAD_FAILED"
                    logger.error(err_reason)
                    failed_entry = {"raw_table": rt, "raw_id": rid, "reason": err_reason}
                    try:
                        _record_meta_pipeline_error(db, rt, rid, err_reason)
                    except Exception as e:
                        failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
                    _record_batch_file_error(db, rt, rid, drive_file_id, fail_code, err_reason)
                    failed_files.append(failed_entry)
                    continue

                dl_path = Path(downloaded)
                pdf_path = temp_path / "input.pdf"
                if doc_format == "image":
                    _image_to_pdf(dl_path, pdf_path)
                elif doc_format == "word":
                    # Word は soffice --headless --convert-to pdf で PDF に変換してから今の PDF と同じ処理をする
                    # 変換失敗はそのファイルの失敗として明示記録
                    _convert_word_to_pdf(dl_path, pdf_path, temp_path)
                elif doc_format in ("pdf", "spreadsheet"):
                    # スプレッドシートは Drive API の export で既に PDF として書き出されているため、今の PDF と同じ処理
                    if dl_path != pdf_path:
                        dl_path.rename(pdf_path)

                doc = fitz.open(str(pdf_path))
                page_count = len(doc)
            except Exception as dl_err:
                err_reason = f"Driveファイル取得またはPDF変換エラー: {dl_err}"
                logger.error(err_reason)
                failed_entry = {"raw_table": rt, "raw_id": rid, "reason": err_reason}
                try:
                    _record_meta_pipeline_error(db, rt, rid, err_reason)
                except Exception as e:
                    failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
                _record_batch_file_error(db, rt, rid, drive_file_id, "CONVERT_FAILED", err_reason)
                failed_files.append(failed_entry)
                continue

            if page_count < 1:
                doc.close()
                err_reason = "PDFのページ数が0です"
                failed_entry = {"raw_table": rt, "raw_id": rid, "reason": err_reason}
                try:
                    _record_meta_pipeline_error(db, rt, rid, err_reason)
                except Exception as e:
                    failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
                failed_files.append(failed_entry)
                continue

            # 全ページを高解像度画像（fitz.Matrix(3,3) PNG）に変換
            page_images: List[bytes] = []
            render_error = None
            for p_idx in range(page_count):
                try:
                    img_bytes = render_page_to_png_bytes(doc, p_idx)
                    page_images.append(img_bytes)
                except Exception as re:
                    render_error = f"ページ {p_idx} のレンダリング(Matrix 3x3)失敗: {re}"
                    break
            doc.close()

            if render_error:
                logger.error(render_error)
                failed_entry = {"raw_table": rt, "raw_id": rid, "reason": render_error}
                try:
                    _record_meta_pipeline_error(db, rt, rid, render_error)
                except Exception as e:
                    failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
                failed_files.append(failed_entry)
                continue

            # 1ファイル分の画像合計サイズ検証（Batch API inline上限 20MB）
            total_img_size = sum(len(b) for b in page_images)
            INLINE_LIMIT_BYTES = 20 * 1024 * 1024  # 20MB
            use_files_api = total_img_size > INLINE_LIMIT_BYTES

            if use_files_api:
                logger.info(
                    "File %s/%s total image size %d bytes exceeds 20MB inline limit. "
                    "Uploading each page via Files API.",
                    rt, rid, total_img_size,
                )

            # 1ページ=1依頼（InlinedRequest）を作成
            # 20MB超の場合は各ページ画像を Files API にアップロードしてファイル参照を使用する
            inlined_requests = []
            uploaded_files_to_delete: List[Any] = []  # 送信後に Files API から削除するオブジェクト
            files_api_error: Optional[str] = None

            for p_idx, img_bytes in enumerate(page_images):
                if use_files_api:
                    # Files API にアップロード（失敗はそのファイル全体の失敗とする）
                    try:
                        import io
                        upload_result = client.files.upload(
                            file=io.BytesIO(img_bytes),
                            config=genai_types.UploadFileConfig(
                                display_name=f"{rt[:10]}-{rid[:8]}-p{p_idx}",
                                mime_type="image/png",
                            ),
                        )
                        uploaded_files_to_delete.append(upload_result)
                        img_part = genai_types.Part.from_uri(
                            file_uri=upload_result.uri,
                            mime_type="image/png",
                        )
                    except Exception as upload_err:
                        files_api_error = (
                            f"ページ {p_idx} の Files API アップロードに失敗しました: {upload_err}"
                        )
                        logger.error(
                            "Files API upload failed for %s/%s page %d: %s",
                            rt, rid, p_idx, upload_err,
                        )
                        break
                else:
                    img_part = genai_types.Part.from_bytes(data=img_bytes, mime_type="image/png")

                img_part.media_resolution = genai_types.PartMediaResolution(
                    level=genai_types.PartMediaResolutionLevel.MEDIA_RESOLUTION_ULTRA_HIGH
                )
                inlined_requests.append(
                    genai_types.InlinedRequest(
                        contents=[
                            img_part,
                            _DIRECT_EXTRACT_PROMPT,
                        ],
                        config=genai_types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=DirectExtractPageResult,
                        ),
                        metadata={
                            "raw_table": rt,
                            "raw_id": rid,
                            "page_index": str(p_idx),
                        },
                    )
                )

            if files_api_error:
                # Files API アップロード失敗: 既にアップロード済みの一時ファイルを削除
                for uf in uploaded_files_to_delete:
                    try:
                        client.files.delete(name=uf.name)
                    except Exception as del_err:
                        logger.error(
                            "Files API 一時ファイルの削除に失敗しました (name=%s): %s",
                            uf.name, del_err,
                        )
                # Files API アップロード失敗時の error レコードを upsert で記録・上書き
                formatted_files_err = format_503_error(files_api_error)
                now_iso = datetime.now(timezone.utc).isoformat()
                db.client.table("pipeline_batch_files").upsert(
                    {
                        "raw_table": rt,
                        "raw_id": rid,
                        "drive_file_id": drive_file_id,
                        "total_pages": page_count,
                        "job_name": "FILES_API_UPLOAD_FAILED",
                        "state": "error",
                        "submitted_at": now_iso,
                        "finished_at": now_iso,
                        "error": formatted_files_err,
                    },
                    on_conflict="raw_table,raw_id",
                ).execute()
                failed_entry = {"raw_table": rt, "raw_id": rid, "reason": formatted_files_err}
                try:
                    _record_meta_pipeline_error(db, rt, rid, formatted_files_err)
                except Exception as e:
                    failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
                failed_files.append(failed_entry)
                continue

            # そのファイルだけの Gemini バッチを client.batches.create で1つ作成
            disp_name = f"pipe-{rt[:10]}-{rid[:8]}"
            try:
                batch_job = client.batches.create(
                    model="gemini-3.8-flash",
                    src=inlined_requests,
                    config={
                        "display_name": disp_name,
                    },
                )
                job_name = batch_job.name
            except Exception as batch_create_err:
                # バッチ作成失敗: アップロード済み Files API ファイルを削除
                for uf in uploaded_files_to_delete:
                    try:
                        client.files.delete(name=uf.name)
                    except Exception as del_err:
                        logger.error(
                            "Files API 一時ファイルの削除に失敗しました (name=%s): %s",
                            uf.name, del_err,
                        )
                err_reason = f"Gemini バッチ作成失敗: {batch_create_err}"
                formatted_batch_err = format_503_error(err_reason)
                logger.error(formatted_batch_err)
                failed_entry = {"raw_table": rt, "raw_id": rid, "reason": formatted_batch_err}
                try:
                    _record_meta_pipeline_error(db, rt, rid, formatted_batch_err)
                except Exception as e:
                    failed_entry["record_error"] = f"失敗理由の記録に失敗: {e}"
                _record_batch_file_error(
                    db, rt, rid, drive_file_id, "BATCH_CREATE_FAILED", formatted_batch_err, total_pages=page_count
                )
                failed_files.append(failed_entry)
                continue

            # pipeline_batch_files に state='submitted' で記録（新規および再送時は同じ行を upsert で上書き）
            now_iso = datetime.now(timezone.utc).isoformat()
            db.client.table("pipeline_batch_files").upsert(
                {
                    "raw_table": rt,
                    "raw_id": rid,
                    "drive_file_id": drive_file_id,
                    "total_pages": page_count,
                    "job_name": job_name,
                    "state": "submitted",
                    "submitted_at": now_iso,
                    "finished_at": None,
                    "error": None,
                },
                on_conflict="raw_table,raw_id",
            ).execute()

            submitted_files.append({
                "raw_table": rt,
                "raw_id": rid,
                "total_pages": page_count,
                "job_name": job_name,
                "via_files_api": use_files_api,
            })
            logger.info(
                "Submitted batch for %s/%s (job=%s, pages=%d, via_files_api=%s, submitted_count=%d/%d)",
                rt, rid, job_name, page_count, use_files_api,
                len(submitted_files), MAX_NEW_BATCH_FILES_PER_RUN,
            )

            # 送信したバッチ（ファイル）数が上限（最大7ファイル）に達したら終了
            if len(submitted_files) >= MAX_NEW_BATCH_FILES_PER_RUN:
                logger.info("Submitted files count (%d) reached limit (%d). Stopping submission loop.", len(submitted_files), MAX_NEW_BATCH_FILES_PER_RUN)
                break

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    summary = {
        "success": True,
        "is_waiting_for_response": False,
        "status_check_errors": [],
        "completed_batches": completed_batches,
        "saved_files": saved_files,
        "failed_files": failed_files,
        "submitted_files": submitted_files,
    }
    logger.info("[PIPELINE_BATCH_SUMMARY] %s", json.dumps(summary, ensure_ascii=False))
    return jsonify(summary), 200
