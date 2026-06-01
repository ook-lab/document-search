import os
import sys
import tempfile
from pathlib import Path
from typing import Optional
from loguru import logger

# PYTHONPATHにリポジトリルートを追加してdmsモジュールを読み込めるようにする
_here = Path(__file__).resolve().parent
_repo = _here.parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from dotenv import load_dotenv
load_dotenv(_repo / ".env")

from dms.common.connectors.google_drive import GoogleDriveConnector

class GoogleDriveHandler:
    """sansu-base用のGoogle Drive連携ハンドラ"""

    def __init__(self):
        try:
            self.drive = GoogleDriveConnector()
            logger.info("Google Drive Connector initialized successfully for sansu-base")
        except Exception as e:
            self.drive = None
            logger.error(f"Failed to initialize Google Drive Connector: {e}")

    def _get_or_create_root_folder(self) -> str:
        """「算数コアデータベース」フォルダのIDを取得または作成する"""
        if not self.drive:
            raise RuntimeError("Google Drive connector is not initialized")

        # 1. 環境変数からの取得を試みる
        folder_id = os.getenv("MATH_PROBLEMS_FOLDER_ID")
        if folder_id:
            logger.info(f"Using MATH_PROBLEMS_FOLDER_ID from env: {folder_id}")
            return folder_id

        # 2. Google Drive上での検索
        parent_id = os.getenv("FILE_UPLOAD_ROOT_FOLDER_ID") or "root"
        query = f"name = '算数コアデータベース' and mimeType = 'application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed = false"
        
        try:
            results = self.drive.service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name)',
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora='allDrives'
            ).execute()
            
            files = results.get('files', [])
            if files:
                folder_id = files[0]['id']
                logger.info(f"Found existing '算数コアデータベース' folder: {folder_id}")
                return folder_id
        except Exception as e:
            logger.error(f"Error searching folder on Google Drive: {e}")

        # 3. 存在しない場合は新規作成
        logger.info("Creating new '算数コアデータベース' folder...")
        try:
            # 親フォルダIDを指定して作成（Noneの場合はマイドライブ直下）
            actual_parent = None if parent_id == "root" else parent_id
            folder_id = self.drive.create_folder("算数コアデータベース", actual_parent)
            if not folder_id:
                raise RuntimeError("Failed to create folder '算数コアデータベース'")
            logger.info(f"Created new folder '算数コアデータベース' with ID: {folder_id}")
            return folder_id
        except Exception as e:
            logger.error(f"Failed to create folder: {e}")
            raise

    def _find_file_in_folder(self, folder_id: str, file_name: str) -> tuple[str, bool]:
        """指定フォルダ内のファイルを検索する。戻り値: (file_id, exists)"""
        query = f"'{folder_id}' in parents and name = '{file_name}' and trashed = false"
        try:
            results = self.drive.service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name)',
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora='allDrives'
            ).execute()
            files = results.get('files', [])
            if files:
                return files[0]['id'], True
        except Exception as e:
            logger.error(f"Error searching file '{file_name}': {e}")
        return "", False

    def append_problem_markdown(self, unit: str, problem_md: str) -> bool:
        """指定された単元（unit）のMarkdownファイルに問題データを追記する"""
        if not self.drive:
            logger.error("Google Drive connection is not available. Skipping append.")
            return False

        file_name = f"{unit}.md"
        
        try:
            # 1. 親フォルダIDの取得
            folder_id = self._get_or_create_root_folder()
            
            # 2. ファイルの検索
            file_id, exists = self._find_file_in_folder(folder_id, file_name)
            
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir) / file_name
                
                if exists:
                    # 3. ファイルが存在する場合はダウンロードして追記
                    logger.info(f"Downloading existing file '{file_name}' (ID: {file_id}) for appending...")
                    self.drive.download_file(file_id, file_name, tmp_dir)
                    
                    # 追記
                    with open(tmp_path, "a", encoding="utf-8") as f:
                        f.write("\n\n" + problem_md + "\n\n---\n")
                    
                    # 上書きアップロード
                    logger.info(f"Uploading updated file '{file_name}' back to Google Drive...")
                    success = self.drive.update_file_content(file_id, str(tmp_path), "text/markdown")
                    return success
                else:
                    # 4. ファイルが存在しない場合は新規作成してアップロード
                    logger.info(f"File '{file_name}' does not exist. Creating new file...")
                    initial_content = problem_md + "\n\n---\n"
                    with open(tmp_path, "w", encoding="utf-8") as f:
                        f.write(initial_content)
                    
                    new_file_id = self.drive.upload_file_from_path(
                        file_path=str(tmp_path),
                        folder_id=folder_id,
                        mime_type="text/markdown",
                        file_name=file_name
                    )
                    return new_file_id is not None
        except Exception as e:
            logger.error(f"Failed to append/create markdown in Google Drive: {e}")
            return False

    def upload_diagram(self, unit: str, file_path: str, file_name: str) -> Optional[str]:
        """指定された単元のフォルダ（または共通フォルダ）に図形画像をアップロードする"""
        if not self.drive:
            logger.error("Google Drive connection is not available. Skipping diagram upload.")
            return None
        try:
            folder_id = self._get_or_create_root_folder()
            logger.info(f"Uploading diagram '{file_name}' to Google Drive folder '{folder_id}'...")
            new_file_id = self.drive.upload_file_from_path(
                file_path=file_path,
                folder_id=folder_id,
                mime_type="image/png",
                file_name=file_name
            )
            return new_file_id
        except Exception as e:
            logger.error(f"Failed to upload diagram to Google Drive: {e}")
            return None


if __name__ == "__main__":
    # 簡単なテストスクリプト
    print("Testing GoogleDriveHandler...")
    handler = GoogleDriveHandler()
    if handler.drive:
        test_md = """# [TEST-001] テスト出題校 2026年
- 単元: テスト単元（テスト小単元）
- 難易度: ★★★☆☆
- 解法コア: テスト用ロジック

## 問題
これはテスト用の問題文です。

## 解説
これはテスト用の解説文です。"""
        success = handler.append_problem_markdown("テスト単元", test_md)
        print(f"Test result: {'SUCCESS' if success else 'FAILED'}")
    else:
        print("Driver not initialized.")
