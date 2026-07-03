from pathlib import Path
from typing import List
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request, AuthorizedSession
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from loguru import logger

SCOPES = ['https://www.googleapis.com/auth/drive']
_DIR = Path(__file__).resolve().parent
TOKEN_DIR = _DIR / 'tokens'
CREDENTIALS_FILE = _DIR / 'oauth_credentials.json'


class DriveAccount:
    def __init__(self, token_path: Path):
        self.token_path = token_path
        self.email = token_path.stem
        self.credentials = None
        self.service = self._build()

    def _build(self):
        creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self.token_path.write_text(creds.to_json())
            else:
                raise ValueError('トークンが無効です。再認証してください。')
        self.credentials = creds
        service = build('drive', 'v3', credentials=creds)
        # AuthorizedSession はスレッドセーフ（並列エクスポート用）
        self.session = AuthorizedSession(creds)
        try:
            self.email = service.about().get(fields='user').execute()['user']['emailAddress']
        except Exception:
            pass
        return service


class OAuth2DriveConnector:
    def __init__(self):
        TOKEN_DIR.mkdir(exist_ok=True)
        self.accounts: List[DriveAccount] = self._load()

    def _load(self) -> List[DriveAccount]:
        result = []
        for p in sorted(TOKEN_DIR.glob('*.json')):
            try:
                acc = DriveAccount(p)
                result.append(acc)
                logger.info(f'Drive アカウント読み込み: {acc.email}')
            except Exception as e:
                logger.warning(f'トークン無効 {p.name}: {e}')
        return result

    def add_account(self) -> str:
        if not CREDENTIALS_FILE.exists():
            raise FileNotFoundError(
                f'oauth_credentials.json が見つかりません ({CREDENTIALS_FILE})。'
                'Google Cloud Console から「デスクトップアプリ」用のOAuth2クライアントIDを'
                'ダウンロードして oauth_credentials.json という名前で同ディレクトリに置いてください。'
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
        idx = len(list(TOKEN_DIR.glob('*.json')))
        token_path = TOKEN_DIR / f'account_{idx}.json'
        token_path.write_text(creds.to_json())
        acc = DriveAccount(token_path)
        self.accounts.append(acc)
        return acc.email

    def remove_account(self, email: str) -> bool:
        for acc in self.accounts:
            if acc.email == email:
                acc.token_path.unlink(missing_ok=True)
                self.accounts = [a for a in self.accounts if a.email != email]
                return True
        return False

    def list_accounts(self) -> List[dict]:
        return [{'email': a.email} for a in self.accounts]
