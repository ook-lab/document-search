from flask import Blueprint, jsonify, request

from google_drive_connector import GoogleDriveConnector

drive_browser_bp = Blueprint("drive_browser", __name__)

PDF_MIME = "application/pdf"
FOLDER_MIME = "application/vnd.google-apps.folder"


@drive_browser_bp.route("/list", methods=["POST"])
def list_drive_items():
    data = request.json or {}
    folder_id = (data.get("folder_id") or "root").strip() or "root"
    try:
        drive = GoogleDriveConnector()
        query = (
            f"'{folder_id}' in parents and trashed=false and "
            f"(mimeType='{FOLDER_MIME}' or mimeType='{PDF_MIME}')"
        )
        result = drive.service.files().list(
            q=query,
            fields="files(id,name,mimeType,size,modifiedTime)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora="allDrives",
            pageSize=200,
        ).execute()
        items = result.get("files", [])
        items.sort(key=lambda item: (item.get("mimeType") != FOLDER_MIME, item.get("name", "").lower()))
        return jsonify({"items": items, "folder_id": folder_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
