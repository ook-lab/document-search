"""
Gmail Cleaner Blueprint
セール期限切れメールを Gemini で検出し、Supabase + Gmail から削除するツール
"""
import os
import json
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
import google.oauth2.credentials
import google_auth_oauthlib.flow
import googleapiclient.discovery
import google.auth.transport.requests

from services.auth_service import login_required

gmail_cleaner_bp = Blueprint('gmail_cleaner', __name__)

# ─────────────────────────────────────────
# 設定
# ─────────────────────────────────────────
SCOPES             = ['https://www.googleapis.com/auth/gmail.modify']
CREDENTIALS_FILE   = Path(__file__).parent.parent / 'auth' / 'credentials.json'
SECRET_CREDENTIALS = 'calendar-register-credentials'   # calendar-register と共有
SECRET_TOKEN       = 'gmail-cleaner-token'
TOKEN_FILE         = Path(__file__).parent.parent / 'auth' / 'gmail_cleaner_token.json'

GEMINI_MODEL   = 'gemini-2.5-flash-lite'

SUPABASE_URL              = os.environ.get('SUPABASE_URL', '')
SUPABASE_SERVICE_ROLE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
GCP_PROJECT_ID            = os.environ.get('GCP_PROJECT_ID', '')
REDIRECT_URI              = os.environ.get(
    'GMAIL_CLEANER_REDIRECT_URI',
    'http://localhost:5002/gmail-cleaner/auth/callback',
)


# ─────────────────────────────────────────
# Supabase
# ─────────────────────────────────────────
def _get_db():
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


# ─────────────────────────────────────────
# Secret Manager
# ─────────────────────────────────────────
def _sm_read(name: str) -> str | None:
    if not GCP_PROJECT_ID:
        return None
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        ver = f'projects/{GCP_PROJECT_ID}/secrets/{name}/versions/latest'
        return client.access_secret_version(request={'name': ver}).payload.data.decode()
    except Exception:
        return None


def _sm_write(name: str, data: str):
    if not GCP_PROJECT_ID:
        return
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        parent = f'projects/{GCP_PROJECT_ID}/secrets/{name}'
        client.add_secret_version(
            request={'parent': parent, 'payload': {'data': data.encode()}}
        )
    except Exception:
        pass


# ─────────────────────────────────────────
# OAuth ヘルパー
# ─────────────────────────────────────────
def _read_credentials_json() -> dict | None:
    if CREDENTIALS_FILE.exists():
        with open(CREDENTIALS_FILE) as f:
            return json.load(f)
    raw = _sm_read(SECRET_CREDENTIALS)
    return json.loads(raw) if raw else None


def _load_token() -> google.oauth2.credentials.Credentials | None:
    data = None
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE) as f:
            data = json.load(f)
    if data is None:
        raw = _sm_read(SECRET_TOKEN)
        data = json.loads(raw) if raw else None
    if data is None:
        return None
    return google.oauth2.credentials.Credentials(
        token=data.get('token'),
        refresh_token=data.get('refresh_token'),
        token_uri=data.get('token_uri'),
        client_id=data.get('client_id'),
        client_secret=data.get('client_secret'),
        scopes=data.get('scopes'),
    )


def _save_token(creds: google.oauth2.credentials.Credentials):
    data = json.dumps({
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': list(creds.scopes or []),
    }, indent=2)
    TOKEN_FILE.parent.mkdir(exist_ok=True)
    with open(TOKEN_FILE, 'w') as f:
        f.write(data)
    _sm_write(SECRET_TOKEN, data)


def _get_valid_credentials() -> google.oauth2.credentials.Credentials | None:
    creds = _load_token()
    if creds is None:
        return None
    if creds.expired and creds.refresh_token:
        creds.refresh(google.auth.transport.requests.Request())
        _save_token(creds)
    return creds if creds.valid else None


def _build_gmail(creds):
    return googleapiclient.discovery.build('gmail', 'v1', credentials=creds)


# ─────────────────────────────────────────
# ページ
# ─────────────────────────────────────────
@gmail_cleaner_bp.route('/gmail-cleaner')
@login_required
def index():
    authed = _get_valid_credentials() is not None
    return render_template('gmail_cleaner/index.html', authed=authed)


# ─────────────────────────────────────────
# OAuth フロー
# ─────────────────────────────────────────
@gmail_cleaner_bp.route('/gmail-cleaner/auth')
@login_required
def auth_start():
    import os as _os
    _os.environ.setdefault('OAUTHLIB_INSECURE_TRANSPORT', '1')
    cred_json = _read_credentials_json()
    if not cred_json:
        return jsonify({'error': 'credentials.json が見つかりません'}), 500
    flow = google_auth_oauthlib.flow.Flow.from_client_config(
        cred_json, scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')
    session['gmail_cleaner_state'] = state
    return redirect(auth_url)


@gmail_cleaner_bp.route('/gmail-cleaner/auth/callback')
def auth_callback():
    import os as _os
    _os.environ.setdefault('OAUTHLIB_INSECURE_TRANSPORT', '1')
    state = session.get('gmail_cleaner_state')
    cred_json = _read_credentials_json()
    flow = google_auth_oauthlib.flow.Flow.from_client_config(
        cred_json, scopes=SCOPES, state=state, redirect_uri=REDIRECT_URI
    )
    flow.fetch_token(authorization_response=request.url)
    _save_token(flow.credentials)
    return redirect(url_for('gmail_cleaner.index'))


# ─────────────────────────────────────────
# API: カテゴリー一覧
# ─────────────────────────────────────────
@gmail_cleaner_bp.route('/api/gmail-cleaner/categories')
def api_categories():
    try:
        db = _get_db()
        resp = (
            db.table('01_gmail_01_raw')
            .select('category')
            .eq('source', 'gmail')
            .not_.is_('category', 'null')
            .execute()
        )
        cats = sorted(set(r['category'] for r in (resp.data or []) if r.get('category')))
        return jsonify({'success': True, 'categories': cats})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ─────────────────────────────────────────
# API: メール一覧
# ─────────────────────────────────────────
@gmail_cleaner_bp.route('/api/gmail-cleaner/mails')
def api_mails():
    try:
        sent_from = request.args.get('sent_from', '')
        sent_to   = request.args.get('sent_to', '')
        category  = request.args.get('category', '')
        keyword   = request.args.get('keyword', '')
        limit     = int(request.args.get('limit', 200))

        db = _get_db()
        q = (
            db.table('01_gmail_01_raw')
            .select('id, message_id, sent_at, from_name, from_email, header_subject, snippet, category, ingested_at')
            .eq('source', 'gmail')
            .order('sent_at', desc=True)
            .limit(limit)
        )
        if sent_from:
            q = q.gte('sent_at', f'{sent_from}T00:00:00+00:00')
        if sent_to:
            q = q.lte('sent_at', f'{sent_to}T23:59:59+00:00')
        if category:
            q = q.eq('category', category)
        if keyword:
            q = q.ilike('header_subject', f'%{keyword}%')

        resp = q.execute()
        return jsonify({'success': True, 'mails': resp.data or [], 'count': len(resp.data or [])})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ─────────────────────────────────────────
# API: Gemini で期限切れ判定
# ─────────────────────────────────────────
@gmail_cleaner_bp.route('/api/gmail-cleaner/analyze', methods=['POST'])
def api_analyze():
    try:
        data = request.get_json()
        ids  = data.get('ids', [])
        if not ids:
            return jsonify({'success': False, 'error': 'ids が空'}), 400

        db = _get_db()
        resp = (
            db.table('01_gmail_01_raw')
            .select('id, header_subject, sent_at, snippet, body_plain, from_name')
            .in_('id', ids)
            .execute()
        )
        mails = resp.data or []
        if not mails:
            return jsonify({'success': True, 'results': []})

        import vertexai
    from google import genai
        today  = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        client = genai.Client(
            vertexai=True, 
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("VERTEX_AI_REGION", "us-central1")
        )

        all_results = []
        batch_size  = 20

        for i in range(0, len(mails), batch_size):
            batch = mails[i:i + batch_size]
            lines = []
            for m in batch:
                body = (m.get('body_plain') or m.get('snippet') or '')[:400]
                lines.append(
                    f'ID:{m["id"]}\n'
                    f'件名:{m.get("header_subject", "")}\n'
                    f'送信日:{m.get("sent_at", "")}\n'
                    f'送信者:{m.get("from_name", "")}\n'
                    f'本文冒頭:{body}'
                )

            prompt = f"""今日の日付は {today} です。
以下のメール一覧を確認し、セール・イベント・キャンペーン・期間限定特典の「期間が過ぎているもの」を判定してください。

判定基準:
- セール終了日・イベント日・キャンペーン期間が今日より前 → expired=true
- 時事的な期間情報がないもの（請求書・重要通知等） → expired=false
- 日付が読み取れない → expired=false

JSON配列のみ返してください（説明不要）:
[{{"id":"...","expired":true,"reason":"理由"}}]

{"="*40}
{chr(10).join(lines)}
"""
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            raw = response.text.strip()

            try:
                from shared.common.ai_cost_logger import log_ai_usage
                um = getattr(response, 'usage_metadata', None)
                log_ai_usage(
                    app='doc-review', stage='gmail-cleaner-analyze', model=GEMINI_MODEL,
                    prompt_token_count=getattr(um, 'prompt_token_count', 0) or 0,
                    candidates_token_count=getattr(um, 'candidates_token_count', 0) or 0,
                    thoughts_token_count=getattr(um, 'thoughts_token_count', 0) or 0,
                    total_token_count=getattr(um, 'total_token_count', 0) or 0,
                )
            except Exception:
                pass

            if '```json' in raw:
                raw = raw[raw.find('```json') + 7:raw.rfind('```')].strip()
            elif '```' in raw:
                raw = raw[raw.find('```') + 3:raw.rfind('```')].strip()

            all_results.extend(json.loads(raw))

        return jsonify({'success': True, 'results': all_results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ─────────────────────────────────────────
# API: 削除（Supabase + Gmail）
# ─────────────────────────────────────────
@gmail_cleaner_bp.route('/api/gmail-cleaner/delete', methods=['POST'])
def api_delete():
    try:
        data = request.get_json()
        ids  = data.get('ids', [])
        if not ids:
            return jsonify({'success': False, 'error': 'ids が空'}), 400

        db = _get_db()

        resp = (
            db.table('01_gmail_01_raw')
            .select('id, message_id')
            .in_('id', ids)
            .execute()
        )
        rows = resp.data or []

        creds = _get_valid_credentials()
        gmail_errors = []
        if creds:
            gmail = _build_gmail(creds)
            for row in rows:
                mid = row.get('message_id')
                if not mid:
                    continue
                try:
                    gmail.users().messages().trash(userId='me', id=mid).execute()
                except Exception as e:
                    gmail_errors.append(f'{mid}: {e}')
        else:
            gmail_errors.append('Gmail 未認証のため Supabase のみ削除')

        db.table('01_gmail_01_raw').delete().in_('id', ids).execute()

        return jsonify({
            'success': True,
            'deleted': len(ids),
            'gmail_errors': gmail_errors,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
