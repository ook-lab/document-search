"""
Google OAuth リフレッシュトークン取得スクリプト
使い方: python get_refresh_token.py
"""
import http.server
import threading
import webbrowser
import urllib.parse
import urllib.request
import json
import os

CLIENT_ID     = input("GOOGLE_CLIENT_ID を入力: ").strip()
CLIENT_SECRET = input("GOOGLE_CLIENT_SECRET を入力: ").strip()
REDIRECT_URI  = "http://localhost:8080"
SCOPE         = "https://www.googleapis.com/auth/calendar"

auth_url = (
    "https://accounts.google.com/o/oauth2/v2/auth"
    f"?client_id={CLIENT_ID}"
    f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
    "&response_type=code"
    f"&scope={urllib.parse.quote(SCOPE)}"
    "&access_type=offline"
    "&prompt=consent"
)

code_holder = {}

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code_holder["code"] = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h1>OK! Check your terminal.</h1>")
    def log_message(self, *args): pass

server = http.server.HTTPServer(("", 8080), Handler)
t = threading.Thread(target=server.handle_request)
t.start()

print("\nブラウザでGoogleログインを完了してください...")
webbrowser.open(auth_url)
t.join()

code = code_holder.get("code")
if not code:
    print("コード取得失敗")
    exit(1)

data = urllib.parse.urlencode({
    "code": code,
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "redirect_uri": REDIRECT_URI,
    "grant_type": "authorization_code",
}).encode()

req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
with urllib.request.urlopen(req) as res:
    tokens = json.loads(res.read())

print("\n=== リフレッシュトークン ===")
print(tokens.get("refresh_token", "取得失敗（prompt=consentでも出ない場合は既存トークンを失効させてください）"))
