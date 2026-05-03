import os
import uuid

from werkzeug.utils import secure_filename

from google_drive_connector import GoogleDriveConnector


def download_drive_pdf(drive_file_id, dest_dir, prefix=None):
    drive_file_id = (drive_file_id or "").strip()
    if not drive_file_id:
        raise ValueError("Google Drive File ID を入力してください。")

    drive = GoogleDriveConnector()
    file_meta = drive.service.files().get(
        fileId=drive_file_id,
        fields="name,mimeType",
        supportsAllDrives=True,
    ).execute()

    original_name = file_meta.get("name", "drive_file.pdf")
    mime_type = (file_meta.get("mimeType") or "").strip()
    if mime_type != "application/pdf":
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
