"""
Kakeibo 用 Google Drive クライアント
Kakeibo で必要な操作のみを実装（shared.common.connectors 不要）
"""
import io
import os
from pathlib import Path
from typing import Dict, List, Optional, Union

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive"]


class DriveClient:
    """Google Drive API クライアント（Kakeibo専用）"""

    def __init__(self):
        self._creds = None
        self.service = self._authenticate()

    def _authenticate(self):
        # 1. ADC（Cloud Run / GCP環境）
        try:
            import google.auth
            creds, _ = google.auth.default(scopes=SCOPES)
            self._creds = creds
            return build("drive", "v3", credentials=creds)
        except Exception:
            pass

        # 2. サービスアカウントファイル（ローカル開発）
        cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if cred_path and Path(cred_path).exists():
            creds = service_account.Credentials.from_service_account_file(
                cred_path, scopes=SCOPES
            )
            self._creds = creds
            return build("drive", "v3", credentials=creds)

        raise RuntimeError(
            "Google Drive 認証情報が見つかりません。"
            "GOOGLE_APPLICATION_CREDENTIALS を設定するか、Cloud Run で実行してください。"
        )

    # ── ファイル一覧 ─────────────────────────────────────────

    def list_files_in_folder(self, folder_id: str) -> List[Dict]:
        """フォルダ内のファイル一覧（フォルダ除外・全件取得）"""
        query = (
            f"'{folder_id}' in parents "
            "and trashed=false "
            "and mimeType != 'application/vnd.google-apps.folder'"
        )
        files = []
        page_token = None
        while True:
            kwargs = dict(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, size)",
                pageSize=1000,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora="allDrives",
            )
            if page_token:
                kwargs["pageToken"] = page_token
            result = self.service.files().list(**kwargs).execute()
            files.extend(result.get("files", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return files

    # ── ダウンロード ─────────────────────────────────────────

    def download_file(
        self, file_id: str, file_name: str, dest_dir: Union[str, Path]
    ) -> Optional[str]:
        """ファイルをローカルにダウンロードしてパスを返す"""
        dest_path = Path(dest_dir) / file_name
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            request = self.service.files().get_media(
                fileId=file_id, supportsAllDrives=True
            )
            with open(dest_path, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
            return str(dest_path)
        except Exception as e:
            print(f"[DriveClient] download_file error ({file_name}): {e}")
            raise

    # ── 移動 ─────────────────────────────────────────────────

    def move_file(self, file_id: str, new_folder_id: str) -> None:
        """ファイルを別フォルダに移動"""
        file_meta = self.service.files().get(
            fileId=file_id, fields="parents", supportsAllDrives=True
        ).execute()
        prev_parents = ",".join(file_meta.get("parents", []))
        self.service.files().update(
            fileId=file_id,
            addParents=new_folder_id,
            removeParents=prev_parents,
            fields="id, parents",
            supportsAllDrives=True,
        ).execute()

    # ── 画像取得（プレビュー用）───────────────────────────────

    def get_file_bytes(self, file_id: str) -> bytes:
        """ファイルをバイト列で取得（レシート画像プレビュー用）
        httplib2のSSL接続使い回し問題を避けるため requests を使用。
        """
        import requests
        from google.auth.transport.requests import Request as GoogleAuthRequest

        self._creds.refresh(GoogleAuthRequest())
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&supportsAllDrives=true"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {self._creds.token}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content
