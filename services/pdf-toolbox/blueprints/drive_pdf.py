import os
import uuid

from werkzeug.utils import secure_filename

from google_drive_connector import GoogleDriveConnector

PDF_MIME = "application/pdf"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

# MD埋め込みツール: Drive から直接開く対象（PDFに加え画像）
EMBEDDER_IMAGE_MIMES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
    }
)
_EMBEDDER_ALLOWED_MIMES = frozenset({PDF_MIME}) | EMBEDDER_IMAGE_MIMES


def download_drive_embedder_source(drive_file_id, dest_dir, prefix=None):
    """PDF または画像（JPEG/PNG/WebP/GIF）をダウンロード。MD埋め込み用。"""
    drive_file_id = (drive_file_id or "").strip()
    if not drive_file_id:
        raise ValueError("Google Drive File ID を入力してください。")

    drive = GoogleDriveConnector()
    file_meta = drive.service.files().get(
        fileId=drive_file_id,
        fields="id,name,mimeType,shortcutDetails(targetId,targetMimeType)",
        supportsAllDrives=True,
    ).execute()

    original_name = file_meta.get("name", "drive_file")
    mime_type = (file_meta.get("mimeType") or "").strip()

    if mime_type == SHORTCUT_MIME:
        shortcut = file_meta.get("shortcutDetails") or {}
        target_id = shortcut.get("targetId")
        target_mime_type = (shortcut.get("targetMimeType") or "").strip()
        if not target_id:
            raise ValueError("Driveショートカットのリンク先を取得できませんでした。")
        if target_mime_type not in _EMBEDDER_ALLOWED_MIMES:
            raise ValueError("PDFまたは画像へのショートカットのみ対応です。")
        drive_file_id = target_id
        file_meta = drive.service.files().get(
            fileId=drive_file_id,
            fields="id,name,mimeType",
            supportsAllDrives=True,
        ).execute()
        original_name = file_meta.get("name", original_name)
        mime_type = (file_meta.get("mimeType") or "").strip()

    if mime_type not in _EMBEDDER_ALLOWED_MIMES:
        raise ValueError(
            "PDF または画像（JPEG/PNG/WebP/GIF）のみ対応です。"
            "Google ドキュメント形式は PDF エクスポートまたは画像として保存してください。"
        )

    os.makedirs(dest_dir, exist_ok=True)
    base_name = secure_filename(original_name) or "document"
    lower = base_name.lower()
    if mime_type == PDF_MIME and not lower.endswith(".pdf"):
        base_name = f"{base_name}.pdf"
    elif mime_type in EMBEDDER_IMAGE_MIMES:
        _ext_for_mime = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        want_ext = _ext_for_mime.get(mime_type, ".img")
        if not any(lower.endswith(x) for x in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
            base_name = f"{base_name}{want_ext}"

    token = prefix or uuid.uuid4().hex[:8]
    safe_filename = f"{token}_{base_name}"
    tmp_name = f"_dl_{uuid.uuid4().hex[:16]}_{base_name}"
    local_path = drive.download_file(drive_file_id, tmp_name, dest_dir)
    final_path = os.path.join(dest_dir, safe_filename)

    if os.path.abspath(local_path) != os.path.abspath(final_path):
        if os.path.exists(final_path):
            os.remove(final_path)
        os.rename(local_path, final_path)

    return {
        "drive_file_id": drive_file_id,
        "filename": base_name,
        "safe_filename": safe_filename,
        "path": final_path,
        "mime_type": mime_type,
    }


def download_drive_pdf(drive_file_id, dest_dir, prefix=None):
    drive_file_id = (drive_file_id or "").strip()
    if not drive_file_id:
        raise ValueError("Google Drive File ID を入力してください。")

    drive = GoogleDriveConnector()
    file_meta = drive.service.files().get(
        fileId=drive_file_id,
        fields="id,name,mimeType,shortcutDetails(targetId,targetMimeType)",
        supportsAllDrives=True,
    ).execute()

    original_name = file_meta.get("name", "drive_file.pdf")
    mime_type = (file_meta.get("mimeType") or "").strip()

    if mime_type == SHORTCUT_MIME:
        shortcut = file_meta.get("shortcutDetails") or {}
        target_id = shortcut.get("targetId")
        target_mime_type = (shortcut.get("targetMimeType") or "").strip()
        if not target_id:
            raise ValueError("Driveショートカットのリンク先を取得できませんでした。")
        if target_mime_type != PDF_MIME:
            raise ValueError("PDFへのショートカットのみ対応です。")
        drive_file_id = target_id
        file_meta = drive.service.files().get(
            fileId=drive_file_id,
            fields="id,name,mimeType",
            supportsAllDrives=True,
        ).execute()
        original_name = file_meta.get("name", original_name)
        mime_type = (file_meta.get("mimeType") or "").strip()

    if mime_type != PDF_MIME:
        raise ValueError("PDF のみ対応です。Google ドキュメント形式はPDFにエクスポートしてから指定してください。")

    os.makedirs(dest_dir, exist_ok=True)
    base_name = secure_filename(original_name) or "document.pdf"
    if not base_name.lower().endswith(".pdf"):
        base_name = f"{base_name}.pdf"

    token = prefix or uuid.uuid4().hex[:8]
    safe_filename = f"{token}_{base_name}"
    tmp_name = f"_dl_{uuid.uuid4().hex[:16]}_{base_name}"
    local_path = drive.download_file(drive_file_id, tmp_name, dest_dir)
    final_path = os.path.join(dest_dir, safe_filename)

    if os.path.abspath(local_path) != os.path.abspath(final_path):
        if os.path.exists(final_path):
            os.remove(final_path)
        os.rename(local_path, final_path)

    return {
        "drive_file_id": drive_file_id,
        "filename": base_name,
        "safe_filename": safe_filename,
        "path": final_path,
    }
