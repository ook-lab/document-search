"""
Google OAuth2 ユーザー同意フロー トークン生成スクリプト

個人GCPプロジェクト配下でGmailおよびGoogle Drive APIにアクセスするための
リフレッシュトークンを含む `google_oauth_token.json` を生成する手動実行ヘルパースクリプト。

事前準備:
1. GCP Console (https://console.cloud.google.com/) で OAuth 2.0 クライアント ID
   (アプリケーションの種類: デスクトップ アプリ / Desktop App) を作成します。
2. ダウンロードした JSON ファイルを `oauth_credentials.json` という名前で
   リポジトリルート (または本スクリプトと同じディレクトリ) に配置します。
3. 本スクリプトを実行すると、ブラウザが開き Google アカウント同意画面が表示されます。
4. `ookubo.y@gmail.com` でログインして権限を許可してください。
5. 認証完了後、リフレッシュトークンを含む `google_oauth_token.json` が出力されます。

使用例:
  python scripts/generate_google_oauth_token.py
  python scripts/generate_google_oauth_token.py --client-secrets path/to/oauth_credentials.json --output google_oauth_token.json
"""

import os
import sys
import json
import argparse
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

# Gmail および Google Drive の統合スコープ
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/drive",
]


def main():
    parser = argparse.ArgumentParser(description="Google OAuth2 リフレッシュトークン生成ヘルパー")
    parser.add_argument(
        "--client-secrets",
        "-c",
        default="oauth_credentials.json",
        help="OAuth 2.0 クライアント秘密鍵 JSON ファイルのパス (デフォルト: oauth_credentials.json)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="google_oauth_token.json",
        help="出力するトークン JSON ファイルのパス (デフォルト: google_oauth_token.json)",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=0,
        help="ローカルリダイレクト用ポート番号 (デフォルト: 0 = ランダムな利用可能ポート)",
    )
    args = parser.parse_args()

    client_secrets_path = Path(args.client_secrets).resolve()
    output_path = Path(args.output).resolve()

    if not client_secrets_path.exists():
        print(f"エラー: OAuth2 クライアントファイルが見つかりません: {client_secrets_path}", file=sys.stderr)
        print("\n手順:", file=sys.stderr)
        print("1. GCP Console (https://console.cloud.google.com/) にアクセス", file=sys.stderr)
        print("2. [APIとサービス] -> [認証情報] から [認証情報を作成] -> [OAuth クライアント ID] を選択", file=sys.stderr)
        print("3. アプリケーションの種類として「デスクトップ アプリ」を選択して作成", file=sys.stderr)
        print("4. ダウンロードした JSON ファイルを 'oauth_credentials.json' として配置してください。", file=sys.stderr)
        sys.exit(1)

    print(f"OAuth2 クライアント設定を読み込んでいます: {client_secrets_path}")
    print("ブラウザを起動してユーザー同意フローを開始します...")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets_path),
        scopes=DEFAULT_SCOPES,
    )

    creds = flow.run_local_server(
        port=args.port,
        prompt="consent",
        access_type="offline",
    )

    if not creds.refresh_token:
        print("エラー: リフレッシュトークンが取得できませんでした。すでに承認済みの場合、GCPのアカウント設定でアプリへのアクセス許可を取り消してから再試行してください。", file=sys.stderr)
        sys.exit(1)

    token_data = json.loads(creds.to_json())

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2, ensure_ascii=False)

    print(f"\n成功: トークン情報を保存しました: {output_path}")
    print("\n保存された構造の確認:")
    print(f"  client_id: {token_data.get('client_id')}")
    print(f"  refresh_token: {'***' if token_data.get('refresh_token') else 'なし (ERROR)'}")
    print(f"  token_uri: {token_data.get('token_uri')}")
    print(f"  scopes: {token_data.get('scopes')}")

    print("\n使用方法:")
    print("  環境変数に設定する場合:")
    print(f"    export GOOGLE_OAUTH_TOKEN_JSON='$(cat {output_path.name})'")
    print("  またはローカルファイルとして配置:")
    print(f"    {output_path.name} をコネクタ実行環境から参照可能な場所に保存してください。")


if __name__ == "__main__":
    main()
