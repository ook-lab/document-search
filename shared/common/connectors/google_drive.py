"""
Google Drive コネクタ (サービスアカウント認証)

設計書: COMPLETE_IMPLEMENTATION_GUIDE_v3.md の 1.4節に基づき、Google Driveと通信する。
"""
import os
from typing import List, Dict, Any, Optional, Union
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload, MediaInMemoryUpload
from io import FileIO, BytesIO
from loguru import logger
from pathlib import Path

# 認証情報ファイルのパス (環境変数から取得、なければローカルのフォールバック)
CREDENTIALS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# ローカル開発用のフォールバックパス
_LOCAL_CREDENTIALS_PATHS = [
    os.path.join(os.path.dirname(__file__), '..', '..', '..', '.local', '_runtime', 'credentials', 'google_credentials.json'),
    os.path.join(os.path.dirname(__file__), '..', '..', '..', '_runtime', 'credentials', 'google_credentials.json'),
]

# 環境変数がない場合、ローカルパスを探す
if not CREDENTIALS_PATH:
    for path in _LOCAL_CREDENTIALS_PATHS:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            CREDENTIALS_PATH = abs_path
            break

SCOPES = ['https://www.googleapis.com/auth/drive']

class GoogleDriveConnector:
    """Google Drive APIクライアント"""
    
    def __init__(self):
        self.service = self._authenticate()
        # logger.info("Google Driveコネクタ初期化完了")
    
    def _authenticate(self):
        """サービスアカウント認証（環境変数ファイル -> ADC -> Streamlit Secrets の順で試行）"""
        # 1. 環境変数 (ローカル開発用: JSONファイルパス指定)
        if CREDENTIALS_PATH and os.path.exists(CREDENTIALS_PATH):
            try:
                creds = service_account.Credentials.from_service_account_file(
                    CREDENTIALS_PATH, scopes=SCOPES
                )
                logger.info(f"環境変数から認証成功: {CREDENTIALS_PATH}")
                return build('drive', 'v3', credentials=creds)
            except Exception as e:
                logger.warning(f"環境変数からの認証失敗: {e}")

        # 2. Application Default Credentials (ADC) (★Cloud Run用: これを追加！★)
        try:
            import google.auth
            # Cloud Run等の環境では自動的に認証情報を取得（ファイル不要）
            creds, project = google.auth.default(scopes=SCOPES)
            logger.info("ADC (Application Default Credentials) で認証成功")
            return build('drive', 'v3', credentials=creds)
        except Exception as e:
            logger.warning(f"ADC認証失敗: {e}")

        # 3. Streamlit Secrets (Streamlit Cloud用)
        try:
            import streamlit as st
            if hasattr(st, 'secrets') and 'gcp_service_account' in st.secrets:
                creds_dict = dict(st.secrets["gcp_service_account"])
                creds = service_account.Credentials.from_service_account_info(
                    creds_dict, scopes=SCOPES
                )
                logger.info("Streamlit Secretsから認証成功")
                return build('drive', 'v3', credentials=creds)
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Streamlit Secretsからの認証失敗: {e}")

        # 全て失敗した場合
        raise FileNotFoundError(
            f"認証情報が見つかりません。以下のいずれかを設定してください:\n"
            f"1. 環境変数 GOOGLE_APPLICATION_CREDENTIALS (現在: {CREDENTIALS_PATH})\n"
            f"2. Cloud Run のサービスアカウントに権限が付与されているか (ADC)\n"
            f"3. Streamlit Secrets が設定されているか"
        )

    def list_files_in_folder(self, folder_id: str, mime_type_filter: str = None) -> List[Dict[str, Any]]:
        """
        指定されたフォルダ内のファイルを一覧表示
        
        Args:
            folder_id: 親フォルダのID
            mime_type_filter: MIMEタイプによるフィルタリング（オプション）
            
        Returns:
            ファイルメタデータのリスト
        """
        # フォルダを除外するクエリを構築
        query = f"'{folder_id}' in parents and trashed=false"
        
        # デフォルトでフォルダを除外
        if mime_type_filter is None:
            query += " and mimeType != 'application/vnd.google-apps.folder'"
        else:
            query += f" and {mime_type_filter}"
        
        try:
            results = self.service.files().list(
                q=query,
                spaces='drive',
                fields='nextPageToken, files(id, name, mimeType, size)',
                supportsAllDrives=True,  # 共有ドライブ対応
                includeItemsFromAllDrives=True,  # 共有ドライブのアイテムを含む
                corpora='allDrives'  # すべてのドライブから検索
            ).execute()

            return results.get('files', [])
        except Exception as e:
            print(f"Error listing files: {e}")
            return []

    def download_file(self, file_id: str, file_name: str, dest_dir: Union[str, Path]) -> Optional[str]:
        """
        Google Driveからファイルをダウンロードし、一時パスを返す

        Args:
            file_id: ファイルのID
            file_name: ファイル名
            dest_dir: 保存先ディレクトリ

        Returns:
            ダウンロードされたファイルへのローカルパス（失敗時はNone）
        """
        try:
            logger.info(f"ファイルダウンロード開始: {file_name} (ID: {file_id})")

            dest_path = Path(dest_dir) / file_name
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # DriveのMIMEタイプとタイムスタンプをチェック
            # ★重要: 共有ドライブや他人所有のファイルにアクセスするためのフラグ
            file_metadata = self.service.files().get(
                fileId=file_id,
                fields='mimeType, modifiedTime',  # タイムスタンプ同期のためmodifiedTimeを追加
                supportsAllDrives=True
            ).execute()
            mime_type = file_metadata['mimeType']
            logger.info(f"ファイルMIMEタイプ: {mime_type}")

            request = None
            if mime_type == 'application/vnd.google-apps.document':
                # Google Docs -> DOCXとしてエクスポート
                request = self.service.files().export(
                    fileId=file_id,
                    mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    supportsAllDrives=True  # 共有ドライブ対応
                )
                dest_path = dest_path.with_suffix('.docx')
            elif mime_type == 'application/vnd.google-apps.spreadsheet':
                # Google Sheets -> XLSXとしてエクスポート
                request = self.service.files().export(
                    fileId=file_id,
                    mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    supportsAllDrives=True  # 共有ドライブ対応
                )
                dest_path = dest_path.with_suffix('.xlsx')
            elif mime_type == 'application/vnd.google-apps.presentation':
                # Google Slides -> PPTXとしてエクスポート
                request = self.service.files().export(
                    fileId=file_id,
                    mimeType='application/vnd.openxmlformats-officedocument.presentationml.presentation',
                    supportsAllDrives=True  # 共有ドライブ対応
                )
                dest_path = dest_path.with_suffix('.pptx')
            else:
                # 通常のファイル (PDF, DOCXなど) はダウンロード
                # ★重要: 共有ドライブや他人所有のファイルにアクセスするためのフラグ
                request = self.service.files().get_media(
                    fileId=file_id,
                    supportsAllDrives=True
                )

            with open(dest_path, 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while done is False:
                    status, done = downloader.next_chunk()
                    if status:
                        logger.debug(f"ダウンロード進捗: {int(status.progress() * 100)}%")

            # タイムスタンプ同期: DriveのmodifiedTimeをローカルファイルに適用
            if 'modifiedTime' in file_metadata:
                try:
                    from dateutil import parser as date_parser
                    dt = date_parser.parse(file_metadata['modifiedTime'])
                    epoch_time = dt.timestamp()
                    os.utime(str(dest_path), (epoch_time, epoch_time))
                    logger.debug(f"タイムスタンプ同期: {dest_path} -> {dt}")
                except Exception as ts_error:
                    logger.warning(f"タイムスタンプ同期失敗: {ts_error}")

            logger.info(f"ファイルダウンロード完了: {dest_path} ({dest_path.stat().st_size} bytes)")
            return str(dest_path)

        except Exception as e:
            # 404エラー（ファイル未存在）は想定内の動作なのでINFOレベルで記録
            error_str = str(e)
            is_404 = 'File not found' in error_str or '404' in error_str or 'not found' in error_str.lower()

            if is_404:
                logger.info(f"ファイルが見つかりません（想定内）: {file_name}")
                logger.debug(f"404詳細: {error_str}")
            else:
                # 404以外の実際のエラーはERRORレベルで記録
                logger.error("ファイルダウンロードエラー: " + file_name)
                logger.error(f"エラー内容: {error_str}")
                logger.error(f"エラータイプ: {type(e).__name__}")
                logger.debug("エラー詳細", exc_info=True)

            # エラーを再スローして呼び出し側で処理できるようにする
            raise

    def get_inbox_folder_id(self) -> Optional[str]:
        """
        環境変数からInBoxフォルダIDを取得

        Returns:
            InBoxフォルダID、設定されていない場合はNone
        """
        inbox_folder_id = os.getenv("INBOX_FOLDER_ID")
        if not inbox_folder_id:
            logger.warning("INBOX_FOLDER_ID が環境変数に設定されていません")
        return inbox_folder_id

    def get_archive_folder_id(self) -> Optional[str]:
        """
        環境変数からArchiveフォルダIDを取得

        Returns:
            ArchiveフォルダID、設定されていない場合はNone
        """
        archive_folder_id = os.getenv("ARCHIVE_FOLDER_ID")
        if not archive_folder_id:
            logger.warning("ARCHIVE_FOLDER_ID が環境変数に設定されていません")
        return archive_folder_id

    def list_inbox_files(
        self,
        folder_id: str,
        processed_file_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """
        InBoxフォルダ内の新規ファイルを取得

        Args:
            folder_id: InBoxフォルダのID
            processed_file_ids: 既に処理済みのファイルIDリスト

        Returns:
            未処理のファイルメタデータリスト
        """
        try:
            # フォルダ内の全ファイルを取得（PDFのみ）
            all_files = self.list_files_in_folder(
                folder_id,
                mime_type_filter="mimeType='application/pdf'"
            )

            # 未処理のファイルのみをフィルタリング
            new_files = [
                file for file in all_files
                if file['id'] not in processed_file_ids
            ]

            logger.info(f"InBox内の全ファイル数: {len(all_files)}, 新規ファイル数: {len(new_files)}")
            return new_files

        except Exception as e:
            logger.error(f"InBoxファイルリスト取得エラー: {e}")
            return []

    def move_file(self, file_id: str, new_folder_id: str) -> bool:
        """
        ファイルを別のフォルダに移動

        Args:
            file_id: 移動するファイルのID
            new_folder_id: 移動先フォルダのID

        Returns:
            成功した場合True、失敗した場合False
        """
        try:
            # 現在の親フォルダを取得
            file = self.service.files().get(
                fileId=file_id,
                fields='parents',
                supportsAllDrives=True  # 共有ドライブ対応
            ).execute()

            previous_parents = ",".join(file.get('parents', []))

            # ファイルを新しいフォルダに移動（古い親を削除し、新しい親を追加）
            self.service.files().update(
                fileId=file_id,
                addParents=new_folder_id,
                removeParents=previous_parents,
                fields='id, parents',
                supportsAllDrives=True  # 共有ドライブ対応
            ).execute()

            logger.info(f"ファイル移動成功: {file_id} -> {new_folder_id}")
            return True

        except Exception as e:
            logger.error(f"ファイル移動エラー ({file_id}): {e}")
            return False

    def rename_file(self, file_id: str, new_name: str) -> bool:
        """
        Google Driveのファイル名を変更

        Args:
            file_id: 変更するファイルのID
            new_name: 新しいファイル名（拡張子を含む）

        Returns:
            成功した場合True、失敗した場合False
        """
        try:
            # ファイル名を更新
            self.service.files().update(
                fileId=file_id,
                body={'name': new_name},
                fields='id, name',
                supportsAllDrives=True  # 共有ドライブ対応
            ).execute()

            logger.info(f"ファイル名変更成功: {file_id} -> {new_name}")
            return True

        except Exception as e:
            logger.error(f"ファイル名変更エラー ({file_id}): {e}")
            return False

    def upload_file(
        self,
        file_content: Union[bytes, str],
        file_name: str,
        mime_type: str,
        folder_id: Optional[str] = None,
        max_retries: int = 3
    ) -> Optional[str]:
        """
        ファイルをGoogle Driveにアップロード（共有ドライブ対応、リトライ機能付き）

        Args:
            file_content: ファイルの内容（バイトまたは文字列）
            file_name: ファイル名
            mime_type: MIMEタイプ（例: 'text/html', 'application/pdf'）
            folder_id: 保存先フォルダID（Noneの場合はルート）
            max_retries: 最大リトライ回数（デフォルト: 3）

        Returns:
            アップロードされたファイルのID、失敗時はNone
        """
        import time

        # ファイルメタデータ
        file_metadata = {'name': file_name}
        if folder_id:
            file_metadata['parents'] = [folder_id]

        # 文字列の場合はバイトに変換
        if isinstance(file_content, str):
            file_content = file_content.encode('utf-8')

        # リトライループ
        for attempt in range(max_retries):
            try:
                # メモリ上のデータからアップロード
                media = MediaInMemoryUpload(
                    file_content,
                    mimetype=mime_type,
                    resumable=True
                )

                # 共有ドライブ対応: supportsAllDrives=True を追加
                file = self.service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id, name, webViewLink',
                    supportsAllDrives=True
                ).execute()

                logger.info(f"ファイルアップロード成功: {file_name} (ID: {file['id']})")
                return file['id']

            except Exception as e:
                error_message = str(e)
                is_timeout = 'timeout' in error_message.lower() or 'timed out' in error_message.lower()

                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2  # 2秒、4秒、6秒...
                    logger.warning(
                        f"ファイルアップロードエラー ({file_name}): {error_message} "
                        f"- リトライ {attempt + 1}/{max_retries} ({wait_time}秒待機)"
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(f"ファイルアップロード失敗（最終試行） ({file_name}): {error_message}")
                    return None

    def upload_file_from_path(
        self,
        file_path: Union[str, Path],
        folder_id: Optional[str] = None,
        mime_type: Optional[str] = None
    ) -> Optional[str]:
        """
        ローカルファイルをGoogle Driveにアップロード（共有ドライブ対応）

        Args:
            file_path: ローカルファイルのパス
            folder_id: 保存先フォルダID（Noneの場合はルート）
            mime_type: MIMEタイプ（Noneの場合は自動判定）

        Returns:
            アップロードされたファイルのID、失敗時はNone
        """
        try:
            file_path = Path(file_path)
            if not file_path.exists():
                logger.error(f"ファイルが見つかりません: {file_path}")
                return None

            # ファイルメタデータ
            file_metadata = {'name': file_path.name}
            if folder_id:
                file_metadata['parents'] = [folder_id]

            # MIMEタイプの自動判定
            if mime_type is None:
                import mimetypes
                mime_type, _ = mimetypes.guess_type(str(file_path))
                mime_type = mime_type or 'application/octet-stream'

            # ファイルからアップロード
            media = MediaFileUpload(
                str(file_path),
                mimetype=mime_type,
                resumable=True
            )

            # 共有ドライブ対応: supportsAllDrives=True を追加
            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name, webViewLink',
                supportsAllDrives=True
            ).execute()

            logger.info(f"ファイルアップロード成功: {file_path.name} (ID: {file['id']})")
            return file['id']

        except Exception as e:
            logger.error(f"ファイルアップロードエラー ({file_path}): {e}")
            return None

    def trash_file(self, file_id: str) -> bool:
        """
        ファイルをゴミ箱に移動（安全な削除）

        Args:
            file_id: ゴミ箱に移動するファイルのID

        Returns:
            成功した場合True、失敗した場合False
        """
        try:
            # trashedフラグをTrueに設定してゴミ箱に移動
            self.service.files().update(
                fileId=file_id,
                body={'trashed': True},
                supportsAllDrives=True
            ).execute()

            logger.info(f"ファイルをゴミ箱に移動しました: {file_id}")
            return True

        except Exception as e:
            logger.error(f"ファイルのゴミ箱移動エラー ({file_id}): {e}")
            return False

    def delete_file_permanently(self, file_id: str) -> bool:
        """
        ファイルを完全に削除（復元不可能）

        Args:
            file_id: 完全に削除するファイルのID

        Returns:
            成功した場合True、失敗した場合False
        """
        from googleapiclient.errors import HttpError

        try:
            # 削除前にファイル情報を取得（デバッグ用）
            try:
                file_info = self.service.files().get(
                    fileId=file_id,
                    fields='id, name, trashed, parents, capabilities/canDelete',
                    supportsAllDrives=True
                ).execute()
                logger.debug(f"削除対象ファイル情報: name={file_info.get('name')}, trashed={file_info.get('trashed')}, canDelete={file_info.get('capabilities', {}).get('canDelete')}")
            except Exception as e:
                logger.warning(f"ファイル情報取得失敗 ({file_id}): {e}")

            # ファイル削除実行
            self.service.files().delete(
                fileId=file_id,
                supportsAllDrives=True
            ).execute()

            logger.info(f"ファイルを完全に削除しました: {file_id}")
            return True

        except HttpError as e:
            if e.resp.status == 404:
                logger.warning(f"ファイルが見つかりません (404): {file_id} - 既に削除されているか、アクセス権がない可能性があります")
            else:
                logger.error(f"ファイルの完全削除エラー ({file_id}): HTTP {e.resp.status} - {e}")
            return False
        except Exception as e:
            logger.error(f"ファイルの完全削除エラー ({file_id}): {e}")
            return False