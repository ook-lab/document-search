"""
Gmail コネクタ (サービスアカウント + ドメイン全体の委任)

Google Workspaceアカウントで、サービスアカウントを使ってGmail APIにアクセス。
アプリパスワード不要の安全な方法。

設定方法: docs/GMAIL_INTEGRATION_SETUP.md を参照
"""
import os
import base64
from typing import List, Dict, Any, Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from loguru import logger
from email.mime.text import MIMEText
from datetime import datetime

# 認証情報ファイルのパス (環境変数から取得)
CREDENTIALS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# Gmail APIのスコープ
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',
]


class GmailConnector:
    """Gmail APIクライアント（サービスアカウント認証）"""

    def __init__(self, user_email: str):
        """
        Gmail APIに接続

        Args:
            user_email: アクセス対象のメールアドレス（例: ookubo.y@workspace-o.com）
        """
        self.user_email = user_email
        self.service = self._authenticate()
        logger.info(f"GmailConnector初期化完了: {user_email}")

    def _authenticate(self):
        """
        サービスアカウント認証 + ドメイン全体の委任

        Returns:
            Gmail APIサービスオブジェクト
        """
        # 優先順位1: 環境変数 GOOGLE_APPLICATION_CREDENTIALS
        if CREDENTIALS_PATH and os.path.exists(CREDENTIALS_PATH):
            try:
                creds = service_account.Credentials.from_service_account_file(
                    CREDENTIALS_PATH,
                    scopes=SCOPES,
                    subject=self.user_email  # ドメイン全体の委任: 対象ユーザーを指定
                )
                logger.info(f"環境変数から認証成功: {CREDENTIALS_PATH}")
                return build('gmail', 'v1', credentials=creds)
            except Exception as e:
                logger.warning(f"環境変数からの認証失敗、Streamlit Secretsにフォールバック: {e}")

        # 優先順位2: Streamlit Secrets (デプロイ環境用)
        try:
            import streamlit as st
            if hasattr(st, 'secrets') and 'gcp_service_account' in st.secrets:
                creds_dict = dict(st.secrets["gcp_service_account"])
                creds = service_account.Credentials.from_service_account_info(
                    creds_dict,
                    scopes=SCOPES,
                    subject=self.user_email
                )
                logger.info("Streamlit Secretsから認証成功")
                return build('gmail', 'v1', credentials=creds)
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Streamlit Secretsからの認証失敗: {e}")

        # どちらも失敗した場合
        raise FileNotFoundError(
            f"認証情報が見つかりません。以下のいずれかを設定してください:\n"
            f"1. 環境変数 GOOGLE_APPLICATION_CREDENTIALS (現在: {CREDENTIALS_PATH})\n"
            f"2. Streamlit Secrets の gcp_service_account\n"
            f"\n設定方法: docs/GMAIL_INTEGRATION_SETUP.md を参照"
        )

    def list_messages(
        self,
        query: str = '',
        max_results: int = 10,
        label_ids: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        メール一覧を取得

        Args:
            query: Gmail検索クエリ（例: 'is:unread', 'from:example@gmail.com'）
            max_results: 取得する最大件数
            label_ids: フィルタするラベルID（例: ['INBOX', 'UNREAD']）

        Returns:
            メッセージのリスト（軽量版: IDとスレッドIDのみ）
        """
        try:
            params = {
                'userId': 'me',
                'maxResults': max_results,
            }
            if query:
                params['q'] = query
            if label_ids:
                params['labelIds'] = label_ids

            results = self.service.users().messages().list(**params).execute()
            messages = results.get('messages', [])

            logger.info(f"メール一覧取得: {len(messages)}件")
            return messages

        except Exception as e:
            logger.error(f"メール一覧取得エラー: {e}")
            return []

    def get_message(self, message_id: str, format: str = 'full') -> Optional[Dict[str, Any]]:
        """
        メールの詳細を取得

        Args:
            message_id: メッセージID
            format: 取得形式（'full', 'metadata', 'minimal'）

        Returns:
            メッセージの詳細情報
        """
        try:
            message = self.service.users().messages().get(
                userId='me',
                id=message_id,
                format=format
            ).execute()

            logger.debug(f"メール取得成功: {message_id}")
            return message

        except Exception as e:
            logger.error(f"メール取得エラー ({message_id}): {e}")
            return None

    def get_attachment(self, message_id: str, attachment_id: str) -> Optional[bytes]:
        """
        添付ファイルのデータを取得

        Args:
            message_id: メッセージID
            attachment_id: 添付ファイルのID

        Returns:
            添付ファイルのバイナリデータ
        """
        try:
            attachment = self.service.users().messages().attachments().get(
                userId='me',
                messageId=message_id,
                id=attachment_id
            ).execute()

            # Base64URLデコード
            data = attachment['data']
            file_data = base64.urlsafe_b64decode(data.encode('UTF-8'))

            logger.debug(f"添付ファイル取得成功: {attachment_id}")
            return file_data

        except Exception as e:
            logger.error(f"添付ファイル取得エラー ({attachment_id}): {e}")
            return None

    def parse_message_headers(self, message: Dict[str, Any]) -> Dict[str, str]:
        """
        メールヘッダーをパース

        Args:
            message: get_message()で取得したメッセージ

        Returns:
            ヘッダー情報の辞書（Subject, From, To, Date など）
        """
        headers = {}
        if 'payload' in message and 'headers' in message['payload']:
            for header in message['payload']['headers']:
                name = header['name']
                value = header['value']
                headers[name] = value

        return headers

    def extract_message_parts(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        メールの本文と添付ファイルを抽出

        Args:
            message: get_message()で取得したメッセージ

        Returns:
            {
                'text_plain': str,  # テキスト本文
                'text_html': str,   # HTML本文
                'attachments': [    # 添付ファイル情報
                    {
                        'filename': str,
                        'mimeType': str,
                        'attachmentId': str,
                        'size': int
                    },
                    ...
                ]
            }
        """
        result = {
            'text_plain': '',
            'text_html': '',
            'attachments': []
        }

        def parse_parts(parts):
            """再帰的にパートを解析"""
            for part in parts:
                mime_type = part.get('mimeType', '')
                filename = part.get('filename', '')

                # 本文の抽出
                if mime_type == 'text/plain' and not filename:
                    if 'data' in part.get('body', {}):
                        data = part['body']['data']
                        text = base64.urlsafe_b64decode(data.encode('UTF-8')).decode('utf-8', errors='replace')
                        result['text_plain'] += text

                elif mime_type == 'text/html' and not filename:
                    if 'data' in part.get('body', {}):
                        data = part['body']['data']
                        text = base64.urlsafe_b64decode(data.encode('UTF-8')).decode('utf-8', errors='replace')
                        result['text_html'] += text

                # 添付ファイルの抽出
                elif filename or part['body'].get('attachmentId'):
                    # Content-IDヘッダーを取得（インライン画像の場合）
                    headers = {}
                    if 'headers' in part:
                        for header in part['headers']:
                            headers[header['name']] = header['value']

                    attachment_info = {
                        'filename': filename,
                        'mimeType': mime_type,
                        'attachmentId': part['body'].get('attachmentId'),
                        'size': part['body'].get('size', 0),
                        'headers': headers  # Content-IDなどのヘッダー情報
                    }
                    result['attachments'].append(attachment_info)

                # マルチパートの場合は再帰
                if 'parts' in part:
                    parse_parts(part['parts'])

        # メッセージのペイロードを解析
        if 'payload' in message:
            payload = message['payload']
            if 'parts' in payload:
                parse_parts(payload['parts'])
            else:
                # シングルパート（本文のみ）
                parse_parts([payload])

        return result

    def convert_html_with_inline_images(
        self,
        message_id: str,
        html_content: str,
        attachments: List[Dict[str, Any]]
    ) -> str:
        """
        HTMLメール内のCID参照画像をBASE64形式に変換

        Args:
            message_id: メッセージID
            html_content: HTML本文
            attachments: 添付ファイル情報のリスト

        Returns:
            処理済みHTML（CID参照がdata:image形式に置換されたもの）
        """
        import re

        if not html_content or not attachments:
            return html_content

        processed_html = html_content

        # CID参照パターンを検索: src="cid:xxxxx"
        cid_pattern = re.compile(r'src=["\']cid:([^"\']+)["\']', re.IGNORECASE)
        cid_matches = cid_pattern.findall(html_content)

        if not cid_matches:
            return html_content

        logger.info(f"CID参照画像を検出: {len(cid_matches)}件")

        # 各CIDに対して処理
        for cid in cid_matches:
            # Content-IDが一致する添付ファイルを探す
            matching_attachment = None
            for att in attachments:
                # Content-IDは通常 <xxxxx> の形式
                att_headers = att.get('headers', {})
                content_id = att_headers.get('Content-ID', '').strip('<>')

                if content_id == cid:
                    matching_attachment = att
                    break

            if matching_attachment:
                attachment_id = matching_attachment.get('attachmentId')
                mime_type = matching_attachment.get('mimeType', 'image/png')

                if attachment_id:
                    # 添付ファイルデータを取得
                    att_data = self.get_attachment(message_id, attachment_id)

                    if att_data:
                        # BASE64エンコード（既にBASE64の場合もあるので確認）
                        try:
                            # att_dataがbytesの場合
                            if isinstance(att_data, bytes):
                                b64_data = base64.b64encode(att_data).decode('ascii')
                            else:
                                b64_data = att_data

                            # data:image形式に変換
                            data_uri = f"data:{mime_type};base64,{b64_data}"

                            # HTMLを置換
                            processed_html = processed_html.replace(
                                f'src="cid:{cid}"',
                                f'src="{data_uri}"'
                            )
                            processed_html = processed_html.replace(
                                f"src='cid:{cid}'",
                                f"src='{data_uri}'"
                            )

                            logger.info(f"CID画像を埋め込み: cid:{cid} -> {mime_type}")

                        except Exception as e:
                            logger.error(f"画像の埋め込みに失敗: cid:{cid}, {e}")

        return processed_html

    def modify_labels(
        self,
        message_id: str,
        add_labels: Optional[List[str]] = None,
        remove_labels: Optional[List[str]] = None
    ) -> bool:
        """
        メールのラベルを変更（既読マーク、アーカイブなど）

        Args:
            message_id: メッセージID
            add_labels: 追加するラベルID（例: ['STARRED']）
            remove_labels: 削除するラベルID（例: ['UNREAD', 'INBOX']）

        Returns:
            成功したかどうか
        """
        try:
            body = {}
            if add_labels:
                body['addLabelIds'] = add_labels
            if remove_labels:
                body['removeLabelIds'] = remove_labels

            self.service.users().messages().modify(
                userId='me',
                id=message_id,
                body=body
            ).execute()

            logger.info(f"ラベル変更成功: {message_id}")
            return True

        except Exception as e:
            logger.error(f"ラベル変更エラー ({message_id}): {e}")
            return False

    def mark_as_read(self, message_id: str) -> bool:
        """
        メールを既読にする

        Args:
            message_id: メッセージID

        Returns:
            成功したかどうか
        """
        return self.modify_labels(message_id, remove_labels=['UNREAD'])

    def archive_message(self, message_id: str) -> bool:
        """
        メールをアーカイブ（受信トレイから削除）

        Args:
            message_id: メッセージID

        Returns:
            成功したかどうか
        """
        return self.modify_labels(message_id, remove_labels=['INBOX'])

    def list_labels(self) -> List[Dict[str, Any]]:
        """
        利用可能なラベル一覧を取得

        Returns:
            ラベル情報のリスト
        """
        try:
            results = self.service.users().labels().list(userId='me').execute()
            labels = results.get('labels', [])

            logger.info(f"ラベル一覧取得: {len(labels)}件")
            return labels

        except Exception as e:
            logger.error(f"ラベル一覧取得エラー: {e}")
            return []

    def get_label_id_by_name(self, label_name: str) -> Optional[str]:
        """
        ラベル名からラベルIDを取得

        Args:
            label_name: ラベル名（例: 'processed', 'ゴミ箱'）

        Returns:
            ラベルID、見つからない場合はNone
        """
        labels = self.list_labels()
        for label in labels:
            if label.get('name') == label_name:
                return label.get('id')
        logger.warning(f"ラベルが見つかりません: {label_name}")
        return None

    def move_to_trash_label(self, message_id: str) -> bool:
        """
        メールを'processed'ラベルから'ゴミ箱'ラベルに移動

        Args:
            message_id: メッセージID

        Returns:
            成功したかどうか
        """
        try:
            # processedラベルとゴミ箱ラベルのIDを取得
            processed_label_id = self.get_label_id_by_name('processed')
            trash_label_id = self.get_label_id_by_name('ゴミ箱')

            if not processed_label_id or not trash_label_id:
                logger.error("必要なラベルが見つかりません（'processed'または'ゴミ箱'）")
                return False

            # ラベルを変更
            return self.modify_labels(
                message_id,
                add_labels=[trash_label_id],
                remove_labels=[processed_label_id]
            )

        except Exception as e:
            logger.error(f"ゴミ箱ラベルへの移動エラー ({message_id}): {e}")
            return False

    def trash_message(self, message_id: str) -> bool:
        """
        Gmail APIを使ってメールをゴミ箱に移動（実際にGmailのゴミ箱に入る）

        Args:
            message_id: メッセージID

        Returns:
            成功したかどうか
        """
        try:
            self.service.users().messages().trash(
                userId='me',
                id=message_id
            ).execute()
            logger.info(f"メールをゴミ箱に移動しました: {message_id}")
            return True

        except Exception as e:
            logger.error(f"メールのゴミ箱移動エラー ({message_id}): {e}")
            return False
