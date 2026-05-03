from flask import Blueprint, jsonify, request

from google_drive_connector import GoogleDriveConnector

drive_browser_bp = Blueprint("drive_browser", __name__)

PDF_MIME = "application/pdf"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"


def _normalize_item(item):
    mime_type = item.get("mimeType")
    shortcut = item.get("shortcutDetails") or {}
    target_id = shortcut.get("targetId")
    target_mime_type = shortcut.get("targetMimeType")
    effective_mime_type = target_mime_type if mime_type == SHORTCUT_MIME else mime_type
    effective_id = target_id if mime_type == SHORTCUT_MIME and target_id else item.get("id")
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "mimeType": mime_type,
        "size": item.get("size"),
        "modifiedTime": item.get("modifiedTime"),
        "driveId": item.get("driveId"),
        "parents": item.get("parents", []),
        "targetId": target_id,
        "targetMimeType": target_mime_type,
        "targetResourceKey": shortcut.get("targetResourceKey"),
        "effectiveId": effective_id,
        "effectiveMimeType": effective_mime_type,
        "isShortcut": mime_type == SHORTCUT_MIME,
    }


def _list_all_files(service, **list_args):
    items = []
    page_token = None
    while True:
        if page_token:
            list_args["pageToken"] = page_token
        result = service.files().list(**list_args).execute()
        items.extend(result.get("files", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            return items


@drive_browser_bp.route("/roots", methods=["POST"])
def list_roots():
    try:
        drive = GoogleDriveConnector()
        about = drive.service.about().get(fields="user(emailAddress,displayName)").execute()
        root = drive.service.files().get(fileId="root", fields="id,name", supportsAllDrives=True).execute()
        drives_result = drive.service.drives().list(pageSize=100, fields="drives(id,name)").execute()
        shared_drives = sorted(drives_result.get("drives", []), key=lambda item: item.get("name", "").lower())
        return jsonify(
            {
                "user": about.get("user", {}),
                "rootFolderId": root.get("id", "root"),
                "rootName": root.get("name", "マイドライブ"),
                "sharedDrives": shared_drives,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@drive_browser_bp.route("/list", methods=["POST"])
def list_drive_items():
    data = request.json or {}
    folder_id = (data.get("folder_id") or "root").strip() or "root"
    source = (data.get("source") or "my_drive").strip()
    drive_id = (data.get("drive_id") or "").strip()
    try:
        drive = GoogleDriveConnector()

        fields = (
            "nextPageToken,files(id,name,mimeType,size,modifiedTime,driveId,parents,"
            "shortcutDetails(targetId,targetMimeType,targetResourceKey))"
        )

        if source == "shared_with_me":
            query = "sharedWithMe=true and trashed=false"
            list_args = {
                "q": query,
                "fields": fields,
                "supportsAllDrives": True,
                "includeItemsFromAllDrives": True,
                "corpora": "user",
                "pageSize": 1000,
            }
        else:
            query = f"'{folder_id}' in parents and trashed=false"
            list_args = {
                "q": query,
                "fields": fields,
                "supportsAllDrives": True,
                "includeItemsFromAllDrives": True,
                "pageSize": 1000,
            }
            if source == "shared_drive" and drive_id:
                list_args["corpora"] = "drive"
                list_args["driveId"] = drive_id
            elif source == "all_drives":
                list_args["corpora"] = "allDrives"
            else:
                list_args["corpora"] = "user"

        raw_items = _list_all_files(drive.service, **list_args)
        items = [_normalize_item(item) for item in raw_items]
        items.sort(
            key=lambda item: (
                item.get("effectiveMimeType") != FOLDER_MIME,
                item.get("effectiveMimeType") != PDF_MIME,
                item.get("name", "").lower(),
            )
        )
        return jsonify({"items": items, "folder_id": folder_id, "source": source, "drive_id": drive_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
