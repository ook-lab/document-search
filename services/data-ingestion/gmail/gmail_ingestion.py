"""
Gmailメール取り込みパイプライン

Gmail API → Google Drive（添付ファイル） → Supabase (pending)

処理フロー:
1. Gmail APIでメール一覧を取得（設定ファイルのクエリに基づく）
2. Supabaseで既存データをチェックして新着メールを抽出
3. 添付ファイルがあればGoogle Driveに保存
4. Supabaseに基本情報を登録（processing_status='pending'）
5. 別途 process_queued_documents.py で処理（PDF抽出、Stage E-K）
"""
import os
import sys
import hashlib
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from loguru import logger
import asyncio

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(root_dir))

# .envファイルを読み込む
from dotenv import load_dotenv
load_dotenv(root_dir / ".env")

# HTML to Screenshot utility
from shared.common.utils.html_screenshot import HTMLScreenshotGenerator

from shared.common.connectors.gmail_connector import GmailConnector
from shared.common.connectors.google_drive import GoogleDriveConnector
from shared.common.database.client import DatabaseClient


class GmailIngestionPipeline:
    """Gmailメール取り込みパイプライン"""

    def __init__(
        self,
        mail_type: str = "DM",
        user_email: Optional[str] = None,
        config_file: Optional[str] = None,
        attachment_folder_id: Optional[str] = None,
        email_folder_id: Optional[str] = None,
        owner_id: Optional[str] = None
    ):
        """
        Args:
            mail_type: メールタイプ（'DM', 'JOB'など）環境変数のプレフィックスに使用
            user_email: アクセス対象のメールアドレス（Noneの場合は環境変数から取得）
            config_file: 設定ファイルのパス（Noneの場合はデフォルト設定を使用）
            attachment_folder_id: 添付ファイル保存先のDriveフォルダID（Noneの場合は環境変数から取得）
            email_folder_id: メール本文HTML保存先のDriveフォルダID（Noneの場合は環境変数から取得）
            owner_id: オーナーID（Supabase Auth ユーザーID、省略時は環境変数から取得）
        """
        self.mail_type = mail_type.upper()

        # メールタイプに基づいて環境変数を取得
        self.user_email = user_email or os.getenv(f"GMAIL_{self.mail_type}_USER_EMAIL")
        self.attachment_folder_id = attachment_folder_id or os.getenv(f"GMAIL_{self.mail_type}_ATTACHMENT_FOLDER_ID")
        self.email_folder_id = email_folder_id or os.getenv(f"GMAIL_{self.mail_type}_EMAIL_FOLDER_ID")

        if not self.user_email:
            raise ValueError(f"user_emailが指定されていません。GMAIL_{self.mail_type}_USER_EMAILを.envに設定するか、引数で指定してください。")

        # Phase 3: owner_id を取得（必須）
        self.owner_id = owner_id or os.getenv('DEFAULT_OWNER_ID')
        if not self.owner_id:
            raise ValueError(
                "owner_id が指定されていません。引数で指定するか、DEFAULT_OWNER_ID を .env に設定してください。"
            )

        # 設定ファイルの読み込み
        self.config = self._load_config(config_file)

        # コネクタの初期化
        self.gmail = GmailConnector(user_email=self.user_email)
        self.drive = GoogleDriveConnector()
        self.db = DatabaseClient()

        # ラベルキャッシュ
        self._label_cache = None

        logger.info(f"GmailIngestionPipeline初期化完了")
        logger.info(f"  - Mail type: {self.mail_type}")
        logger.info(f"  - User email: {self.user_email}")
        logger.info(f"  - Email folder: {self.email_folder_id}")
        logger.info(f"  - Attachment folder: {self.attachment_folder_id}")

    def _load_config(self, config_file: Optional[Path] = None) -> Dict[str, Any]:
        """設定ファイルを読み込む"""
        if config_file is None:
            config_file = Path(__file__).parent / "config.yaml"

        if not config_file.exists():
            logger.warning(f"設定ファイルが見つかりません: {config_file}")
            logger.info("デフォルト設定を使用します")
            return self._get_default_config()

        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)

                # 空のファイルまたはコメントのみの場合、Noneが返される
                if config is None:
                    logger.info(f"設定ファイルが空です: {config_file}")
                    logger.info("デフォルト設定を使用します")
                    return self._get_default_config()

                logger.info(f"設定ファイルを読み込みました: {config_file}")
                return config
        except Exception as e:
            logger.error(f"設定ファイルの読み込みエラー: {e}")
            logger.info("デフォルト設定を使用します")
            return self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """デフォルト設定を返す（メールタイプに基づく）"""
        # 環境変数からラベル名を取得
        label = os.getenv(f"GMAIL_{self.mail_type}_LABEL", self.mail_type)
        processed_label = os.getenv(f"GMAIL_{self.mail_type}_PROCESSED_LABEL", "Processed")

        return {
            'gmail': {
                'query': f'label:{label}',
                'max_results': 100,
                'processed_label': processed_label,
                'remove_source_label_after_import': True
            },
            'import_settings': {
                'workspace': 'gmail',
                'doc_type': f'{self.mail_type}-mail',
                'person': ['宜紀'],  # 固定値
                'organization': [],
                'save_attachments': True,
                'attachment_extensions': ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.png', '.jpg', '.jpeg']
            }
        }

    def _get_labels(self) -> List[Dict[str, Any]]:
        """ラベル一覧を取得（キャッシュ付き）"""
        if self._label_cache is None:
            self._label_cache = self.gmail.list_labels()
        return self._label_cache

    def _get_label_id(self, label_name: str) -> Optional[str]:
        """ラベル名からラベルIDを取得"""
        labels = self._get_labels()
        for label in labels:
            if label.get('name') == label_name:
                return label.get('id')
        return None

    def _get_or_create_label(self, label_name: str) -> Optional[str]:
        """ラベルを取得または作成"""
        # 既存ラベルをチェック
        label_id = self._get_label_id(label_name)
        if label_id:
            return label_id

        # ラベルが存在しない場合は作成
        try:
            label_object = {
                'name': label_name,
                'labelListVisibility': 'labelShow',
                'messageListVisibility': 'show'
            }
            created_label = self.gmail.service.users().labels().create(
                userId='me',
                body=label_object
            ).execute()

            # キャッシュを更新
            self._label_cache = None

            logger.info(f"ラベル作成: {label_name}")
            return created_label.get('id')
        except Exception as e:
            logger.error(f"ラベル作成エラー: {e}")
            return None

    async def check_existing_messages(self, message_ids: List[str]) -> set:
        """
        Supabaseで既存のメッセージIDをチェック

        Args:
            message_ids: チェックするメッセージIDのリスト

        Returns:
            既に存在するメッセージIDのセット
        """
        try:
            # Rawdata_FILE_AND_MAIL テーブルで doc_type が一致するドキュメントを取得
            result = self.db.client.table('Rawdata_FILE_AND_MAIL').select('metadata').eq(
                'doc_type', self.config['import_settings']['doc_type']
            ).execute()

            # metadata->message_id を抽出
            existing_ids = set()
            if result.data:
                for doc in result.data:
                    metadata = doc.get('metadata', {})
                    if isinstance(metadata, dict):
                        msg_id = metadata.get('message_id')
                        if msg_id:
                            existing_ids.add(msg_id)

            logger.info(f"既存のメール: {len(existing_ids)}件")
            return existing_ids

        except Exception as e:
            logger.error(f"Supabase検索エラー: {e}")
            return set()

    def save_attachment_to_drive(
        self,
        attachment_data: bytes,
        filename: str,
        message_id: str
    ) -> Optional[str]:
        """
        添付ファイルをGoogle Driveに保存

        Args:
            attachment_data: 添付ファイルのバイトデータ
            filename: ファイル名
            message_id: メッセージID

        Returns:
            DriveのファイルID、失敗時はNone
        """
        # 安全なファイル名を生成
        safe_filename = "".join(c for c in filename if c.isalnum() or c in (' ', '-', '_', '.', '　')).strip()
        if not safe_filename:
            safe_filename = "attachment"

        # タイムスタンプ付きファイル名（重複防止）
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"{timestamp}_{safe_filename}"

        # MIMEタイプの推測（拡張子から）
        mime_type = 'application/octet-stream'
        ext = Path(filename).suffix.lower()
        mime_types = {
            '.pdf': 'application/pdf',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xls': 'application/vnd.ms-excel',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
        }
        mime_type = mime_types.get(ext, mime_type)

        # Driveにアップロード
        file_id = self.drive.upload_file(
            file_content=attachment_data,
            file_name=file_name,
            mime_type=mime_type,
            folder_id=self.attachment_folder_id
        )

        if file_id:
            logger.info(f"添付ファイルをDriveに保存: {file_name}")
        else:
            logger.error(f"添付ファイルの保存に失敗: {file_name}")

        return file_id

    async def generate_and_upload_png_from_html(
        self,
        html_content: str,
        message_id: str,
        subject: str
    ) -> Optional[str]:
        """
        HTMLからPNG画像を生成してGoogle Driveに保存（一時ファイル）

        Args:
            html_content: HTMLコンテンツ（CID画像が既にBASE64形式に変換済み）
            message_id: Gmail message ID
            subject: メール件名

        Returns:
            Drive file ID（失敗時はNone）
        """
        if not html_content:
            logger.warning(f"HTML本文が空: {message_id}")
            return None

        if not self.email_folder_id:
            logger.error("email_folder_idが設定されていません")
            return None

        try:
            # ファイル名を生成
            safe_subject = subject[:50].replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
            timestamp = message_id[:10]
            png_file_name = f"{timestamp}_{safe_subject}.png"

            # HTML → PNG変換
            logger.info(f"HTML→PNG変換開始: {png_file_name}")
            screenshot_gen = HTMLScreenshotGenerator(viewport_width=800, viewport_height=800)
            png_bytes = await screenshot_gen.html_to_screenshot(
                html_content=html_content,
                output_path=None,  # ローカル保存不要
                full_page=True
            )

            # PNGをDriveにアップロード（一時ファイル）
            file_id = self.drive.upload_file(
                file_content=png_bytes,
                file_name=png_file_name,
                mime_type='image/png',
                folder_id=self.email_folder_id
            )

            if file_id:
                logger.info(f"PNG画像をDriveに保存（一時）: {png_file_name}")
            else:
                logger.error(f"PNG画像のDriveアップロード失敗: {png_file_name}")

            return file_id

        except Exception as e:
            logger.error(f"HTML→PNG変換エラー: {e}")
            return None

    def save_email_html_to_drive(
        self,
        html_content: str,
        message_id: str,
        subject: str
    ) -> Optional[str]:
        """
        メール本文（HTML）をGoogle Driveに保存

        Args:
            html_content: HTMLコンテンツ（CID画像が既にBASE64形式に変換済み）
            message_id: Gmail message ID
            subject: メール件名

        Returns:
            Drive file ID（失敗時はNone）
        """
        if not html_content:
            logger.warning(f"HTML本文が空: {message_id}")
            return None

        if not self.email_folder_id:
            logger.error("email_folder_idが設定されていません")
            return None

        # Base64埋め込み画像をリサイズ（モバイル対応）
        try:
            from shared.common.utils.html_screenshot import HTMLScreenshotGenerator
            screenshot_gen = HTMLScreenshotGenerator()
            html_content = screenshot_gen._resize_embedded_images(html_content, max_height=300)
            logger.info(f"HTML内のBase64画像をリサイズしました（max_height=300px）")
        except Exception as e:
            logger.warning(f"Base64画像のリサイズに失敗（元のまま保存）: {e}")

        # ファイル名を生成（件名が長すぎる場合は切り詰める）
        # 無効な文字を除去
        safe_subject = subject[:50].replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
        timestamp = message_id[:10]  # メッセージIDの最初の10文字
        file_name = f"{timestamp}_{safe_subject}.html"

        # HTMLコンテンツをバイト列に変換
        html_bytes = html_content.encode('utf-8')

        # Driveにアップロード
        file_id = self.drive.upload_file(
            file_content=html_bytes,
            file_name=file_name,
            mime_type='text/html',
            folder_id=self.email_folder_id
        )

        if file_id:
            logger.info(f"メールHTMLをDriveに保存: {file_name}")
        else:
            logger.error(f"メールHTMLの保存に失敗: {file_name}")

        return file_id

    async def process_single_message(
        self,
        message_id: str
    ) -> Dict[str, Any]:
        """
        1件のメールを処理

        Args:
            message_id: メッセージID

        Returns:
            処理結果の辞書
        """
        result = {
            'message_id': message_id,
            'success': False,
            'attachment_file_ids': [],
            'document_ids': [],
            'error': None
        }

        try:
            # メールの詳細を取得
            message = self.gmail.get_message(message_id, format='full')
            if not message:
                logger.error(f"メールの取得に失敗: {message_id}")
                result['error'] = "メールの取得に失敗"
                return result

            # ヘッダー情報を抽出
            headers = self.gmail.parse_message_headers(message)
            subject = headers.get('Subject', '（件名なし）')
            from_header = headers.get('From', '不明')
            to_email = headers.get('To', '')
            date_str = headers.get('Date', '')

            # From ヘッダーをパース（"名前 <email@example.com>" → (名前, email) に分離）
            from email.utils import parseaddr
            sender_name, sender_email = parseaddr(from_header)
            if not sender_name:
                sender_name = sender_email  # 名前がない場合はメールアドレスを使用

            logger.info(f"メール処理開始: {subject}")

            # 本文と添付ファイルを抽出
            parts = self.gmail.extract_message_parts(message)
            text_plain = parts.get('text_plain', '')
            text_html = parts.get('text_html', '')
            attachments = parts.get('attachments', [])

            # HTMLメール内のCID参照画像をBASE64形式に変換
            if text_html and attachments:
                text_html = self.gmail.convert_html_with_inline_images(
                    message_id, text_html, attachments
                )

            # 受信日時をISO形式に変換
            sent_at = None
            if date_str:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(date_str)
                    sent_at = dt.isoformat()
                except Exception as e:
                    logger.warning(f"日付のパースに失敗: {date_str}, {e}")

            # 添付ファイルの処理
            attachment_info_list = []
            if self.config['import_settings']['save_attachments'] and attachments:
                allowed_extensions = self.config['import_settings']['attachment_extensions']

                for att in attachments:
                    filename = att.get('filename', '')
                    attachment_id = att.get('attachmentId')
                    ext = Path(filename).suffix.lower()

                    # 拡張子フィルタ
                    if allowed_extensions and ext not in allowed_extensions:
                        logger.debug(f"スキップ（拡張子フィルタ）: {filename}")
                        continue

                    if not attachment_id:
                        logger.warning(f"添付ファイルIDが空: {filename}")
                        continue

                    # 添付ファイルのデータを取得
                    att_data = self.gmail.get_attachment(message_id, attachment_id)
                    if not att_data:
                        logger.error(f"添付ファイルの取得に失敗: {filename}")
                        continue

                    # Driveに保存
                    file_id = self.save_attachment_to_drive(att_data, filename, message_id)
                    if file_id:
                        result['attachment_file_ids'].append(file_id)
                        attachment_info_list.append({
                            'filename': filename,
                            'drive_file_id': file_id,
                            'size': att.get('size', 0),
                            'mime_type': att.get('mimeType', '')
                        })

            # メタデータ準備
            metadata = {
                'message_id': message_id,
                'thread_id': message.get('threadId', ''),
                'subject': subject,
                'from': from_header,
                'from_name': sender_name,
                'from_email': sender_email,
                'to': to_email,
                'date': date_str,
                'labels': message.get('labelIds', []),
                'attachments': attachment_info_list,
                'has_attachments': len(attachment_info_list) > 0
            }

            # 本文テキストを結合（プレーンテキスト優先）
            email_body = text_plain if text_plain else text_html

            # コンテンツハッシュ（重複検出用）
            content_for_hash = f"{subject}|{sender_email}|{date_str}|{email_body}"
            content_hash = hashlib.sha256(content_for_hash.encode('utf-8')).hexdigest()

            # メール本文（HTML）をDriveに保存
            email_html_file_id = None
            email_png_file_id = None  # 画像処理用PNG（一時ファイル）
            if text_html:
                # HTML保存（表示用・永続）
                email_html_file_id = self.save_email_html_to_drive(text_html, message_id, subject)
                if email_html_file_id:
                    result['attachment_file_ids'].append(email_html_file_id)

                    # PNG生成&保存（AI処理用・一時）
                    email_png_file_id = await self.generate_and_upload_png_from_html(text_html, message_id, subject)
                    if email_png_file_id:
                        result['attachment_file_ids'].append(email_png_file_id)
                        logger.info(f"HTMLメール処理: HTML={email_html_file_id}, PNG={email_png_file_id}")

            # Supabaseに保存するデータ
            # 1. メール本文（HTML→PNG）のレコードを作成
            if email_html_file_id and email_png_file_id:
                # HTMLからテキストを抽出（取り込み時点で抽出）
                from bs4 import BeautifulSoup
                try:
                    soup = BeautifulSoup(text_html, 'html.parser')
                    # scriptとstyleタグを除去
                    for script in soup(["script", "style"]):
                        script.decompose()
                    # テキスト抽出
                    extracted_text = soup.get_text(separator='\n', strip=True)
                    logger.info(f"HTMLからテキスト抽出: {len(extracted_text)}文字")
                except Exception as e:
                    logger.warning(f"HTML解析エラー（フォールバック使用）: {e}")
                    extracted_text = email_body  # フォールバック

                # ファイル名の共通部分
                safe_subject = subject[:50].replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
                timestamp = message_id[:10]

                email_doc_data = {
                    'file_url': f"https://drive.google.com/file/d/{email_html_file_id}/view",
                    'file_id': email_html_file_id,
                    'screenshot_url': f"https://drive.google.com/file/d/{email_png_file_id}/view",  # PNG（OCR用・一時）
                    'file_name': f"{timestamp}_{safe_subject}.html",  # HTML拡張子
                    'doc_type': self.config['import_settings']['doc_type'],
                    'workspace': self.config['import_settings']['workspace'],
                    'person': self.config['import_settings']['person'],
                    'organization': self.config['import_settings']['organization'],
                    'attachment_text': extracted_text,  # HTMLから抽出したテキスト（取り込み時点）
                    'summary': '',  # process_queued_documents.py で生成
                    'tags': ['gmail', 'email_html'],
                    'document_date': sent_at,
                    'metadata': metadata,  # シンプルなメタデータのみ
                    'content_hash': content_hash,
                    'processing_status': 'pending',
                    'processing_stage': 'gmail_html',
                    # 表示用フィールド
                    'display_subject': subject,
                    'display_sent_at': sent_at,
                    'display_sender': sender_name,
                    'display_sender_email': sender_email,
                    'display_post_text': email_body,  # 全文
                    # Phase 3: owner_id 必須
                    'owner_id': self.owner_id
                }

                try:
                    doc_result = await self.db.insert_document('Rawdata_FILE_AND_MAIL', email_doc_data)
                    if doc_result:
                        doc_id = doc_result.get('doc_id')
                        result['document_ids'].append(doc_id)
                        logger.info(f"Supabase保存完了（メール本文HTML）: {subject}")
                except Exception as db_error:
                    logger.error(f"Supabase保存エラー（メール本文）: {db_error}")
                    result['error'] = str(db_error)

            # 2. 添付ファイルのレコードを作成（ある場合のみ）
            if attachment_info_list:
                # 添付ファイルがある場合：各添付ファイルごとにレコードを作成
                for att_info in attachment_info_list:
                    doc_data = {
                        'file_url': f"https://drive.google.com/file/d/{att_info['drive_file_id']}/view",
                        'file_id': att_info['drive_file_id'],
                        'file_name': att_info['filename'],
                        'doc_type': self.config['import_settings']['doc_type'],
                        'workspace': self.config['import_settings']['workspace'],
                        'person': self.config['import_settings']['person'],
                        'organization': self.config['import_settings']['organization'],
                        'attachment_text': '',  # process_queued_documents.py で抽出
                        'summary': '',  # process_queued_documents.py で生成
                        'tags': ['gmail', 'attachment'],
                        'document_date': sent_at,
                        'metadata': metadata,
                        'content_hash': content_hash,
                        'processing_status': 'pending',
                        'processing_stage': 'gmail_attachment_downloaded',
                        # 表示用フィールド
                        'display_subject': subject,
                        'display_sent_at': sent_at,
                        'display_sender': sender_name,
                        'display_sender_email': sender_email,
                        'display_post_text': email_body,  # 全文
                        # Phase 3: owner_id 必須
                        'owner_id': self.owner_id
                    }

                    try:
                        doc_result = await self.db.insert_document('Rawdata_FILE_AND_MAIL', doc_data)
                        if doc_result:
                            doc_id = doc_result.get('doc_id')
                            result['document_ids'].append(doc_id)
                            logger.info(f"Supabase保存完了（添付ファイル）: {att_info['filename']}")
                    except Exception as db_error:
                        logger.error(f"Supabase保存エラー: {db_error}")
                        result['error'] = str(db_error)

            # メールのラベルを変更（DMラベルを削除し、Processedラベルに移動）
            processed_label = self.config['gmail'].get('processed_label', 'Processed')
            remove_source_label = self.config['gmail'].get('remove_source_label_after_import', True)

            # Processedラベルを取得または作成
            processed_label_id = self._get_or_create_label(processed_label)

            if processed_label_id:
                labels_to_add = [processed_label_id]
                labels_to_remove = []

                # DMラベルを削除する設定の場合
                if remove_source_label:
                    dm_label_id = self._get_label_id('DM')
                    if dm_label_id:
                        labels_to_remove.append(dm_label_id)

                self.gmail.modify_labels(message_id, add_labels=labels_to_add, remove_labels=labels_to_remove)
                logger.info(f"ラベル変更完了: {message_id} -> {processed_label}")

            result['success'] = True
            logger.info(f"メール処理完了: {subject} ({len(result['attachment_file_ids'])} attachments)")

        except Exception as e:
            logger.error(f"メール処理エラー: {e}", exc_info=True)
            result['error'] = str(e)

        return result

    async def run(self):
        """パイプラインを実行"""
        try:
            # Gmail設定を取得
            gmail_config = self.config['gmail']
            query = gmail_config.get('query', 'label:DM')
            max_results = gmail_config.get('max_results', 100)

            logger.info(f"メール一覧を取得中...")
            logger.info(f"  - Query: {query}")
            logger.info(f"  - Max results: {max_results}")

            # メール一覧を取得
            messages = self.gmail.list_messages(
                query=query,
                max_results=max_results
            )

            if not messages:
                logger.info("メールが見つかりませんでした")
                return

            logger.info(f"メール一覧取得完了: {len(messages)}件")

            # メッセージIDを抽出
            message_ids = [m['id'] for m in messages]

            # 既存のメッセージIDをチェック
            existing_ids = await self.check_existing_messages(message_ids)

            # 新規メッセージを抽出
            new_message_ids = [mid for mid in message_ids if mid not in existing_ids]

            logger.info(f"現在のメール: {len(messages)}件")
            logger.info(f"既存のメール: {len(existing_ids)}件")
            logger.info(f"新規メール: {len(new_message_ids)}件")

            if not new_message_ids:
                logger.info("新規メールはありません")
                return

            # 新規メッセージを処理
            results = []
            for i, message_id in enumerate(new_message_ids, 1):
                logger.info(f"[{i}/{len(new_message_ids)}] 処理中...")
                result = await self.process_single_message(message_id)
                results.append(result)

            # サマリー
            success_count = sum(1 for r in results if r['success'])
            total_attachments = sum(len(r['attachment_file_ids']) for r in results)
            total_docs = sum(len(r['document_ids']) for r in results)

            logger.info("=" * 60)
            logger.info("処理完了")
            logger.info(f"  成功: {success_count}/{len(results)}")
            logger.info(f"  失敗: {len(results) - success_count}/{len(results)}")
            logger.info(f"  処理した添付ファイル: {total_attachments}件")
            logger.info(f"  登録したドキュメント: {total_docs}件（pending状態）")
            logger.info("=" * 60)
            logger.info("")
            logger.info("次のステップ:")
            logger.info(f"  python process_queued_documents.py --workspace={self.config['import_settings']['workspace']}")
            logger.info("=" * 60)

            # 結果を表示
            print("\n" + "=" * 80)
            print("Gmail取り込み結果")
            print("=" * 80)

            for result in results:
                print(f"\nMessage ID: {result['message_id']}")
                print(f"  Success: {result['success']}")
                print(f"  Attachments: {len(result['attachment_file_ids'])}")
                for file_id in result['attachment_file_ids']:
                    print(f"    - https://drive.google.com/file/d/{file_id}/view")
                print(f"  Documents: {len(result['document_ids'])} (pending)")
                if result['error']:
                    print(f"  Error: {result['error']}")

            print("\n" + "=" * 80)
            print("次のステップ:")
            print(f"  python process_queued_documents.py --workspace={self.config['import_settings']['workspace']}")
            print("=" * 80)

        except Exception as e:
            logger.error(f"パイプライン実行エラー: {e}", exc_info=True)
            raise


async def main():
    """メインエントリーポイント"""
    import argparse

    parser = argparse.ArgumentParser(description='Gmail取り込みパイプライン')
    parser.add_argument('--mail-type', type=str, default='DM', help='メールタイプ（DM, JOBなど）デフォルト: DM')
    parser.add_argument('--email', type=str, help='アクセス対象のメールアドレス（省略時は環境変数から取得）')
    parser.add_argument('--config', type=str, help='設定ファイルのパス')
    parser.add_argument('--email-folder-id', type=str, help='メール本文HTML保存先のDriveフォルダID（省略時は環境変数から取得）')
    parser.add_argument('--attachment-folder-id', type=str, help='添付ファイル保存先のDriveフォルダID（省略時は環境変数から取得）')
    args = parser.parse_args()

    # パイプラインの初期化
    config_file = Path(args.config) if args.config else None
    pipeline = GmailIngestionPipeline(
        mail_type=args.mail_type,
        user_email=args.email,
        config_file=config_file,
        email_folder_id=args.email_folder_id,
        attachment_folder_id=args.attachment_folder_id
    )

    # 実行
    await pipeline.run()


if __name__ == "__main__":
    asyncio.run(main())
