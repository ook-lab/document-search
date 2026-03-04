"""
Google カレンダー一括登録アプリ

- Google OAuth2 でカレンダー一覧を取得
- テキストエリアに箇条書きで予定を入力
- Gemini 2.5 Flash-lite でパース
- 選択したカレンダーに一括登録

時間パターン:
  パターン1: 開始〜終了 両方指定 (例: 10:00-12:00)
  パターン2: 開始のみ (デフォルト +1時間)
  パターン3: 時間なし → 終日イベント
"""

import os
import json
import sys
import threading
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, session
)
from flask_cors import CORS
from dotenv import load_dotenv

import google.oauth2.credentials
import google_auth_oauthlib.flow
import googleapiclient.discovery
import google.generativeai as genai
from supabase import create_client

load_dotenv(Path(__file__).parent.parent.parent / '.env')

# ─────────────────────────────────────────
# 設定
# ─────────────────────────────────────────
SCOPES = ['https://www.googleapis.com/auth/calendar']
CREDENTIALS_FILE = Path(__file__).parent / 'auth' / 'credentials.json'
TOKEN_FILE = Path(__file__).parent / 'auth' / 'token.json'

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_AI_API_KEY')
GEMINI_MODEL = 'gemini-2.5-flash-lite'

# Supabase
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY')

def _get_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# Secret Manager の設定（Cloud Run 時に使用）
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID')
SECRET_CREDENTIALS = 'calendar-register-credentials'
SECRET_TOKEN      = 'calendar-register-token'

# ローカル実行時: http を許可
os.environ.setdefault('OAUTHLIB_INSECURE_TRANSPORT', '1')

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'calendar-register-dev-key')
CORS(app)


# ─────────────────────────────────────────
# Secret Manager ヘルパー
# ─────────────────────────────────────────

def _sm_read(secret_name: str) -> str | None:
    """Secret Manager からシークレットを読む。失敗したら None"""
    if not GCP_PROJECT_ID:
        return None
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        name = f'projects/{GCP_PROJECT_ID}/secrets/{secret_name}/versions/latest'
        resp = client.access_secret_version(request={'name': name})
        return resp.payload.data.decode('utf-8')
    except Exception:
        return None


def _sm_write(secret_name: str, value: str):
    """Secret Manager にシークレットを書く（バージョンを追加）"""
    if not GCP_PROJECT_ID:
        return
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        parent = f'projects/{GCP_PROJECT_ID}/secrets/{secret_name}'
        client.add_secret_version(
            request={'parent': parent, 'payload': {'data': value.encode('utf-8')}}
        )
    except Exception as e:
        app.logger.warning(f'Secret Manager 書き込み失敗 ({secret_name}): {e}')


def _read_credentials_json() -> dict | None:
    """credentials.json を読む（ローカルファイル優先、次に Secret Manager）"""
    if CREDENTIALS_FILE.exists():
        with open(CREDENTIALS_FILE) as f:
            return json.load(f)
    raw = _sm_read(SECRET_CREDENTIALS)
    return json.loads(raw) if raw else None


# ─────────────────────────────────────────
# OAuth2 ヘルパー
# ─────────────────────────────────────────

def _load_credentials():
    """token を読み込む（ローカルファイル優先、次に Secret Manager）"""
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


def _save_credentials(creds):
    """credentials を保存（ローカルファイル + Secret Manager + google_oauth_tokens）"""
    data = json.dumps({
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': list(creds.scopes or []),
    }, indent=2)
    # ローカルファイルに保存
    TOKEN_FILE.parent.mkdir(exist_ok=True)
    with open(TOKEN_FILE, 'w') as f:
        f.write(data)
    # Secret Manager にも保存（Cloud Run 向け）
    _sm_write(SECRET_TOKEN, data)
    # google_oauth_tokens テーブルに保存（Edge Function が参照するため必須）
    user_id = os.environ.get('CALENDAR_SYNC_USER_ID')
    if user_id and creds.refresh_token:
        db = _get_supabase()
        if db:
            try:
                db.table('google_oauth_tokens').upsert(
                    {'user_id': user_id, 'refresh_token': creds.refresh_token},
                    on_conflict='user_id'
                ).execute()
            except Exception as e:
                app.logger.warning(f'google_oauth_tokens 保存失敗: {e}')


def _get_valid_credentials():
    """有効な credentials を返す。期限切れなら更新。なければ None"""
    creds = _load_credentials()
    if creds is None:
        return None
    if creds.expired and creds.refresh_token:
        import google.auth.transport.requests
        creds.refresh(google.auth.transport.requests.Request())
        _save_credentials(creds)
    return creds if creds.valid else None


def _build_calendar_service(creds):
    return googleapiclient.discovery.build('calendar', 'v3', credentials=creds)


def _get_related_calendar_ids(service, calendar_id: str) -> list[str]:
    """
    指定カレンダーID + 同名の _arc / _pen カレンダーのIDをまとめて返す。
    例: 「学校」→ ['学校のID', '学校_arcのID', '学校_penのID']
    """
    try:
        base_name = service.calendars().get(calendarId=calendar_id).execute().get('summary', '')
    except Exception:
        return [calendar_id]
    if not base_name:
        return [calendar_id]

    targets = {f'{base_name}_arc', f'{base_name}_pen'}
    related = [calendar_id]
    page_token = None
    while True:
        result = service.calendarList().list(pageToken=page_token).execute()
        for c in result.get('items', []):
            if c.get('summary') in targets:
                related.append(c['id'])
        page_token = result.get('nextPageToken')
        if not page_token:
            break
    return related


def _get_user_email(creds) -> str | None:
    """認証済みユーザーのメールアドレスを返す（プライマリカレンダーのIDと同一）"""
    try:
        return _build_calendar_service(creds).calendars().get(calendarId='primary').execute().get('id')
    except Exception:
        return None


# ─────────────────────────────────────────
# Gemini パース
# ─────────────────────────────────────────

def parse_events_with_gemini(text: str, preset_text: str = '') -> list:
    """
    箇条書きテキストを Gemini でパースして構造化イベントリストを返す

    Returns:
        [
            {
                "title": str,
                "date": "YYYY-MM-DD",
                "start_time": "HH:MM" or null,  # null → 終日
                "end_time": "HH:MM" or null,    # null → start+1h or 終日
                "location": str or null,
                "description": str or null,
                "all_day": bool
            },
            ...
        ]
    """
    if not GEMINI_API_KEY:
        raise RuntimeError('GEMINI_API_KEY が設定されていません')

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL)

    today = datetime.now()
    preset_section = f"""
【プリセット】
{preset_text}
""" if preset_text.strip() else ''

    prompt = f"""
あなたはカレンダー予定のパースを行うAIです。
今日の日付は {today.strftime('%Y年%m月%d日')}（{today.year}年）です。

以下の箇条書きテキストを解析し、各行を1件のカレンダーイベントとして構造化してください。
{preset_section}
【時間パターンのルール】
- パターン1（開始〜終了）: start_time と end_time を両方セット
- パターン2（開始のみ）: start_time だけセット、end_time は null、all_day は false
- パターン3（時間なし）: start_time と end_time を null、all_day は true

【日付のルール】
- 「1/15」「1月15日」→ 年度を推定して YYYY-MM-DD に変換
- 「来週月曜」「明日」→ 今日の日付から計算して YYYY-MM-DD に変換
- 「毎週〇曜」→ 直近の該当日を1件だけ作成

【その他ルール】
- 場所・会場が書いてあれば location に入れる
- 備考・詳細があれば description に入れる
- タイトルは簡潔に（場所・時間は除いてよい）
- 解釈できない行は無視してよい

【入力テキスト】
{text}

【出力形式】（JSON配列のみ返す。説明文は不要）
[
  {{
    "title": "イベント名",
    "date": "YYYY-MM-DD",
    "start_time": "HH:MM",
    "end_time": "HH:MM",
    "location": null,
    "description": null,
    "all_day": false
  }}
]
"""

    response = model.generate_content(prompt)
    raw = response.text.strip()

    # ```json ... ``` を除去
    if '```json' in raw:
        raw = raw[raw.find('```json') + 7:raw.rfind('```')].strip()
    elif '```' in raw:
        raw = raw[raw.find('```') + 3:raw.rfind('```')].strip()

    return json.loads(raw)


# ─────────────────────────────────────────
# ルート
# ─────────────────────────────────────────

@app.route('/')
def index():
    creds = _get_valid_credentials()
    authed = creds is not None
    return render_template('index.html', authed=authed)


def _get_redirect_uri() -> str:
    """リダイレクト URI を返す（環境変数 OAUTH_REDIRECT_URI 優先）"""
    if os.environ.get('OAUTH_REDIRECT_URI'):
        return os.environ['OAUTH_REDIRECT_URI']
    port = int(os.environ.get('CALENDAR_REGISTER_PORT', 5003))
    return f'http://localhost:{port}/auth/callback'


@app.route('/auth/login')
def auth_login():
    """OAuth2 認証フロー開始"""
    creds_data = _read_credentials_json()
    if not creds_data:
        return jsonify({'error': 'credentials.json が見つかりません（auth/ フォルダまたは Secret Manager を確認）'}), 500

    flow = google_auth_oauthlib.flow.Flow.from_client_config(
        creds_data, scopes=SCOPES, redirect_uri=_get_redirect_uri()
    )
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    session['oauth_state'] = state
    return redirect(auth_url)


@app.route('/auth/callback')
def auth_callback():
    """OAuth2 コールバック"""
    creds_data = _read_credentials_json()
    flow = google_auth_oauthlib.flow.Flow.from_client_config(
        creds_data,
        scopes=SCOPES,
        state=session.get('oauth_state'),
        redirect_uri=_get_redirect_uri()
    )
    flow.fetch_token(authorization_response=request.url)
    _save_credentials(flow.credentials)
    return redirect(url_for('index'))


@app.route('/auth/logout')
def auth_logout():
    TOKEN_FILE.unlink(missing_ok=True)
    return redirect(url_for('index'))


@app.route('/api/presets/<path:calendar_id>', methods=['GET'])
def api_preset_get(calendar_id):
    """カレンダーのプリセットを取得"""
    db = _get_supabase()
    if not db:
        return jsonify({'preset_text': '', 'tags': []})
    try:
        res = db.table('calendar_presets').select('preset_text, tags').eq('calendar_id', calendar_id).execute()
        if res.data:
            return jsonify({'preset_text': res.data[0]['preset_text'], 'tags': res.data[0].get('tags') or []})
        return jsonify({'preset_text': '', 'tags': []})
    except Exception as e:
        return jsonify({'preset_text': '', 'tags': [], 'warning': str(e)})


@app.route('/api/presets/<path:calendar_id>', methods=['POST'])
def api_preset_save(calendar_id):
    """カレンダーのプリセットを保存"""
    db = _get_supabase()
    if not db:
        return jsonify({'error': 'Supabase が設定されていません'}), 500
    data = request.get_json() or {}
    preset_text = data.get('preset_text', '')
    tags        = data.get('tags', [])
    try:
        existing = db.table('calendar_presets').select('calendar_id').eq('calendar_id', calendar_id).execute()
        if existing.data:
            db.table('calendar_presets').update(
                {'preset_text': preset_text, 'tags': tags}
            ).eq('calendar_id', calendar_id).execute()
        else:
            db.table('calendar_presets').insert(
                {'calendar_id': calendar_id, 'preset_text': preset_text, 'tags': tags}
            ).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/calendars')
def api_calendars():
    """カレンダー一覧を返す"""
    creds = _get_valid_credentials()
    if not creds:
        return jsonify({'error': 'unauthorized'}), 401

    service = _build_calendar_service(creds)
    all_items = []
    page_token = None
    while True:
        result = service.calendarList().list(pageToken=page_token).execute()
        all_items.extend(result.get('items', []))
        page_token = result.get('nextPageToken')
        if not page_token:
            break
    calendars = [
        {
            'id': c['id'],
            'name': c.get('summary', c['id']),
            'primary': c.get('primary', False),
            'color': c.get('backgroundColor', '#4285F4'),
        }
        for c in all_items
        if not c.get('summary', '').endswith('_arc') and not c.get('summary', '').endswith('_pen')
    ]
    # プライマリを先頭に
    calendars.sort(key=lambda c: (not c['primary'], c['name']))
    return jsonify(calendars)


@app.route('/api/parse', methods=['POST'])
def api_parse():
    """Gemini でテキストをパース（プレビュー用）"""
    data = request.get_json()
    text = (data or {}).get('text', '').strip()
    preset_text = (data or {}).get('preset_text', '')
    if not text:
        return jsonify({'error': 'テキストが空です'}), 400

    try:
        events = parse_events_with_gemini(text, preset_text)
        return jsonify({'events': events})
    except json.JSONDecodeError as e:
        return jsonify({'error': f'Gemini の出力をパースできませんでした: {e}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/register', methods=['POST'])
def api_register():
    """
    パース済みイベントを Google カレンダーに一括登録

    Request body:
        {
            "calendar_id": "...",
            "events": [{ title, date, start_time, end_time, location, description, all_day }, ...]
        }

    Response:
        {
            "results": [
                { "title": ..., "success": true, "event_id": ..., "html_link": ... },
                { "title": ..., "success": false, "error": ... },
                ...
            ]
        }
    """
    creds = _get_valid_credentials()
    if not creds:
        return jsonify({'error': 'unauthorized'}), 401

    data = request.get_json()
    calendar_id = (data or {}).get('calendar_id')
    events = (data or {}).get('events', [])

    if not calendar_id:
        return jsonify({'error': 'calendar_id が必要です'}), 400
    if not events:
        return jsonify({'error': 'events が空です'}), 400

    service = _build_calendar_service(creds)
    user_email = _get_user_email(creds)
    results = []

    for ev in events:
        try:
            body = _build_event_body(ev, user_email=user_email)
            created = service.events().insert(
                calendarId=calendar_id,
                body=body
            ).execute()
            results.append({
                'title': ev.get('title'),
                'success': True,
                'event_id': created.get('id'),
                'html_link': created.get('htmlLink'),
                'summary': _format_event_summary(ev),
            })
        except Exception as e:
            results.append({
                'title': ev.get('title'),
                'success': False,
                'error': str(e),
            })

    return jsonify({'results': results})


# ─────────────────────────────────────────
# イベント body 生成ヘルパー
# ─────────────────────────────────────────

def _build_event_body(ev: dict, user_email: str | None = None) -> dict:
    """
    パース済みイベント dict → Google Calendar API の event body
    """
    title = ev.get('title', '（無題）')
    date = ev.get('date')           # YYYY-MM-DD
    start_time = ev.get('start_time')   # HH:MM or null
    end_time = ev.get('end_time')       # HH:MM or null
    all_day = ev.get('all_day', False)
    location = ev.get('location')
    description = ev.get('description')
    attendance = ev.get('attendance')   # 'accepted' / 'declined' / 'tentative'

    body = {'summary': title}
    if location:
        body['location'] = location
    if description:
        body['description'] = description

    if all_day or not start_time:
        # パターン3: 終日
        body['start'] = {'date': date}
        body['end'] = {'date': date}
    elif end_time:
        # パターン1: 開始〜終了
        body['start'] = {'dateTime': f'{date}T{start_time}:00', 'timeZone': 'Asia/Tokyo'}
        body['end']   = {'dateTime': f'{date}T{end_time}:00',   'timeZone': 'Asia/Tokyo'}
    else:
        # パターン2: 開始のみ → +1時間
        dt_start = datetime.fromisoformat(f'{date}T{start_time}:00')
        dt_end = dt_start + timedelta(hours=1)
        body['start'] = {'dateTime': dt_start.isoformat(), 'timeZone': 'Asia/Tokyo'}
        body['end']   = {'dateTime': dt_end.isoformat(),   'timeZone': 'Asia/Tokyo'}

    if attendance and user_email:
        body['attendees'] = [{'email': user_email, 'responseStatus': attendance, 'self': True}]

    return body


def _format_event_summary(ev: dict) -> str:
    """ログ/UI 表示用のサマリ文字列"""
    date = ev.get('date', '')
    start = ev.get('start_time', '')
    end = ev.get('end_time', '')
    if ev.get('all_day') or not start:
        time_str = '終日'
    elif end:
        time_str = f'{start}〜{end}'
    else:
        time_str = f'{start}〜（+1h）'
    return f'{date} {time_str} {ev.get("title", "")}'.strip()


# ─────────────────────────────────────────
# 割り振り機能
# ─────────────────────────────────────────

@app.route('/api/events', methods=['GET'])
def api_events():
    """期間指定でイベント一覧を取得"""
    creds = _get_valid_credentials()
    if not creds:
        return jsonify({'error': 'unauthorized'}), 401

    calendar_id = request.args.get('calendar_id')
    date_from   = request.args.get('from')   # YYYY-MM-DD
    date_to     = request.args.get('to')     # YYYY-MM-DD
    if not all([calendar_id, date_from, date_to]):
        return jsonify({'error': 'calendar_id, from, to が必要です'}), 400

    service = _build_calendar_service(creds)
    user_email = _get_user_email(creds)
    try:
        # メイン + _arc + _pen の3カレンダーをまとめて取得
        related_ids = _get_related_calendar_ids(service, calendar_id)
        all_items = []
        for cal_id in related_ids:
            try:
                result = service.events().list(
                    calendarId=cal_id,
                    timeMin=f'{date_from}T00:00:00+09:00',
                    timeMax=f'{date_to}T23:59:59+09:00',
                    singleEvents=True,
                    orderBy='startTime',
                    maxResults=200,
                ).execute()
                all_items.extend(result.get('items', []))
            except Exception:
                pass

        # 開始時刻でソート
        all_items.sort(key=lambda e: e.get('start', {}).get('dateTime') or e.get('start', {}).get('date', ''))

        events = []
        for e in all_items:
            start = e.get('start', {})
            end   = e.get('end', {})
            attendees = e.get('attendees', [])
            my_status = next(
                (a.get('responseStatus') for a in attendees if a.get('self') or a.get('email') == user_email),
                None
            )
            events.append({
                'id':          e['id'],
                'calendar_id': e.get('organizer', {}).get('email') or calendar_id,
                'title':       e.get('summary', '（タイトルなし）'),
                'date':        (start.get('dateTime') or start.get('date', ''))[:10],
                'start_time':  (start.get('dateTime') or '')[-14:-9] if 'dateTime' in start else None,
                'end_time':    (end.get('dateTime') or '')[-14:-9]   if 'dateTime' in end   else None,
                'all_day':     'date' in start,
                'location':    e.get('location'),
                'description': e.get('description'),
                'my_status':   my_status,
            })
        return jsonify({'events': events})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/attendance/<path:calendar_id>/<event_id>', methods=['PATCH'])
def api_update_attendance(calendar_id, event_id):
    """イベントの出欠ステータスを更新（attendees.responseStatus）"""
    creds = _get_valid_credentials()
    if not creds:
        return jsonify({'error': 'unauthorized'}), 401

    data = request.get_json() or {}
    status = data.get('status')
    if status not in ('accepted', 'declined', 'tentative'):
        return jsonify({'error': 'status は accepted / declined / tentative のいずれか'}), 400

    user_email = _get_user_email(creds)
    if not user_email:
        return jsonify({'error': 'メールアドレスの取得に失敗しました'}), 500

    service = _build_calendar_service(creds)
    try:
        event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        # 自分以外の attendees は保持し、自分のエントリを上書き
        attendees = [a for a in event.get('attendees', [])
                     if not a.get('self') and a.get('email') != user_email]
        attendees.append({'email': user_email, 'responseStatus': status, 'self': True})
        service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body={'attendees': attendees},
            sendUpdates='none',
        ).execute()
        return jsonify({'success': True, 'status': status})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/backfill-person', methods=['POST'])
def api_backfill_person():
    """
    calendar_sync_state の person 設定値で 02_gcal_01_raw の person を一括上書きする。
    カレンダーIDをキーに照合し、person が未設定なら calendar_name を使う。
    """
    db = _get_supabase()
    user_id = os.environ.get('CALENDAR_SYNC_USER_ID')
    if not db or not user_id:
        return jsonify({'error': 'Supabase または CALENDAR_SYNC_USER_ID が未設定'}), 500

    states = db.table('calendar_sync_state') \
               .select('calendar_id, calendar_name, person') \
               .eq('user_id', user_id) \
               .execute()

    if not states.data:
        return jsonify({'updated_calendars': 0})

    updated_calendars = 0
    for state in states.data:
        cal_id = state['calendar_id']
        person = (state.get('person') or '').strip() or state.get('calendar_name') or cal_id
        db.table('02_gcal_01_raw').update({'person': person}).eq('calendar_id', cal_id).execute()
        updated_calendars += 1

    return jsonify({'ok': True, 'updated_calendars': updated_calendars})


@app.route('/api/backfill-attendees', methods=['POST'])
def api_backfill_attendees():
    """
    02_gcal_01_raw で attendees が NULL のイベントに自分を参加者として追加する。
    Google カレンダー側も PATCH して attendees を書き込む。
    """
    creds = _get_valid_credentials()
    if not creds:
        return jsonify({'error': 'unauthorized'}), 401

    user_email = _get_user_email(creds)
    if not user_email:
        return jsonify({'error': 'メールアドレスの取得に失敗しました'}), 500

    db = _get_supabase()
    if not db:
        return jsonify({'error': 'Supabase が設定されていません'}), 500

    # attendees が NULL のレコードを取得
    rows = db.table('02_gcal_01_raw') \
             .select('id, event_id, calendar_id') \
             .is_('attendees', 'null') \
             .execute()

    if not rows.data:
        return jsonify({'updated': 0, 'errors': []})

    service = _build_calendar_service(creds)
    attendees_value = [{'email': user_email, 'responseStatus': 'accepted', 'self': True}]

    updated = 0
    errors = []

    for row in rows.data:
        event_id   = row['event_id']
        calendar_id = row['calendar_id']
        db_id      = row['id']

        # Google カレンダーを PATCH
        try:
            service.events().patch(
                calendarId=calendar_id,
                eventId=event_id,
                body={'attendees': attendees_value},
                sendUpdates='none',
            ).execute()
        except Exception as e:
            errors.append({'event_id': event_id, 'error': str(e)})
            continue

        # DB を更新
        try:
            import json as _json
            db.table('02_gcal_01_raw') \
              .update({'attendees': attendees_value}) \
              .eq('id', db_id) \
              .execute()
            updated += 1
        except Exception as e:
            errors.append({'event_id': event_id, 'error': f'DB更新失敗: {e}'})

    return jsonify({'updated': updated, 'errors': errors, 'total': len(rows.data)})


@app.route('/api/redistribute-events', methods=['POST'])
def api_redistribute_events():
    """
    02_gcal_01_raw のイベントを responseStatus に応じて別カレンダーに移動する。
      accepted  → 現カレンダー（移動なし）
      declined  → {カレンダー名}_arc
      tentative → {カレンダー名}_pen
    カレンダーが存在しなければ自動作成する。
    """
    creds = _get_valid_credentials()
    if not creds:
        return jsonify({'error': 'unauthorized'}), 401

    db = _get_supabase()
    if not db:
        return jsonify({'error': 'Supabase が設定されていません'}), 500

    service = _build_calendar_service(creds)

    # カレンダー一覧を全件取得してキャッシュ（名前 → ID）
    all_cals_by_name = {}
    page_token = None
    while True:
        result = service.calendarList().list(pageToken=page_token).execute()
        for c in result.get('items', []):
            all_cals_by_name[c.get('summary', '')] = c['id']
        page_token = result.get('nextPageToken')
        if not page_token:
            break

    cal_name_cache = {}   # calendar_id → カレンダー名
    dest_cache = {}       # (calendar_id, suffix) → 移動先カレンダーID

    def get_cal_name(cal_id: str) -> str:
        if cal_id not in cal_name_cache:
            try:
                cal = service.calendars().get(calendarId=cal_id).execute()
                cal_name_cache[cal_id] = cal.get('summary', cal_id)
            except Exception:
                cal_name_cache[cal_id] = cal_id
        return cal_name_cache[cal_id]

    def get_or_create_dest(cal_id: str, suffix: str) -> str | None:
        key = (cal_id, suffix)
        if key in dest_cache:
            return dest_cache[key]
        target_name = f'{get_cal_name(cal_id)}_{suffix}'
        if target_name in all_cals_by_name:
            dest_cache[key] = all_cals_by_name[target_name]
            return dest_cache[key]
        # 存在しなければ作成
        try:
            new_cal = service.calendars().insert(body={'summary': target_name}).execute()
            all_cals_by_name[target_name] = new_cal['id']
            dest_cache[key] = new_cal['id']
            return new_cal['id']
        except Exception as e:
            app.logger.warning(f'カレンダー作成失敗 {target_name}: {e}')
            return None

    # attendees が設定済みのレコードを取得
    rows = db.table('02_gcal_01_raw') \
             .select('id, event_id, calendar_id, attendees') \
             .not_.is_('attendees', 'null') \
             .execute()

    moved = 0
    skipped = 0
    errors = []

    for row in rows.data:
        attendees = row.get('attendees') or []
        # self: true のエントリから responseStatus を取得
        my_status = next(
            (a.get('responseStatus') for a in attendees if a.get('self')),
            attendees[0].get('responseStatus') if attendees else None
        )

        if my_status == 'accepted' or not my_status:
            skipped += 1
            continue

        suffix = 'arc' if my_status == 'declined' else 'pen'  # tentative → pen
        cal_id   = row['calendar_id']
        event_id = row['event_id']
        db_id    = row['id']

        dest_cal_id = get_or_create_dest(cal_id, suffix)
        if not dest_cal_id:
            errors.append({'event_id': event_id, 'error': f'移動先カレンダーの取得/作成に失敗'})
            continue

        # Google カレンダーでイベントを移動
        try:
            service.events().move(
                calendarId=cal_id,
                eventId=event_id,
                destination=dest_cal_id,
            ).execute()
        except Exception as e:
            errors.append({'event_id': event_id, 'error': str(e)})
            continue

        # DB の calendar_id を更新
        try:
            db.table('02_gcal_01_raw').update({'calendar_id': dest_cal_id}).eq('id', db_id).execute()
            moved += 1
        except Exception as e:
            errors.append({'event_id': event_id, 'error': f'DB更新失敗: {e}'})

    return jsonify({'moved': moved, 'skipped': skipped, 'errors': errors, 'total': len(rows.data)})


@app.route('/api/assign', methods=['POST'])
def api_assign():
    """Gemini で枠リスト×科目リストの割り振い案を生成"""
    data        = request.get_json()
    slots       = (data or {}).get('slots', [])    # 既存イベントリスト
    subject_text = (data or {}).get('subject_text', '').strip()

    if not slots:
        return jsonify({'error': '枠リストが空です'}), 400
    if not subject_text:
        return jsonify({'error': '科目リストが空です'}), 400
    if not GEMINI_API_KEY:
        return jsonify({'error': 'GEMINI_API_KEY が設定されていません'}), 500

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL)

    def _slot_line(s):
        time_str = f'{s.get("start_time","") or "終日"}-{s.get("end_time","") or ""}'
        loc  = f' | 場所: {s["location"]}'       if s.get('location')    else ''
        desc = f' | 説明: {s["description"][:40]}' if s.get('description') else ''
        return f'{s["date"]} {time_str} [{s["id"]}] {s["title"]}{loc}{desc}'

    slots_text = '\n'.join(_slot_line(s) for s in slots)

    prompt = f"""
あなたはカレンダーイベントの修正専門家です。

以下の【既存のイベント】に対して、【修正指示】に従って各フィールドを修正してください。

【既存のイベント】（日付 開始-終了 [ID] 件名 | 場所 | 説明）
{slots_text}

【修正指示】
{subject_text}

【出力ルール】
- 修正対象の各イベントについて、修正後の値を全フィールド出力する
- 変更しないフィールドは元の値をそのまま出力する
- start_time / end_time は HH:MM 形式。終日イベントの場合は null
- location / description が元々ない かつ 指示にもなければ null
- JSON配列のみ返す（説明文不要）

【出力形式】
[
  {{
    "id": "イベントID",
    "summary": "件名",
    "start_time": "HH:MM",
    "end_time": "HH:MM",
    "location": "場所 or null",
    "description": "説明 or null"
  }},
  ...
]
"""

    try:
        response = model.generate_content(prompt)
        raw = response.text.strip()
        if '```json' in raw:
            raw = raw[raw.find('```json') + 7:raw.rfind('```')].strip()
        elif '```' in raw:
            raw = raw[raw.find('```') + 3:raw.rfind('```')].strip()
        assignments = json.loads(raw)

        id_to_slot = {s['id']: s for s in slots}
        results = []
        for a in assignments:
            slot = id_to_slot.get(a['id'], {})
            results.append({
                'id':              a['id'],
                'date':            slot.get('date', ''),
                'old_title':       slot.get('title', ''),
                'old_start_time':  slot.get('start_time'),
                'old_end_time':    slot.get('end_time'),
                'summary':         a.get('summary',     slot.get('title', '')),
                'start_time':      a.get('start_time',  slot.get('start_time')),
                'end_time':        a.get('end_time',    slot.get('end_time')),
                'location':        a.get('location',    slot.get('location')),
                'description':     a.get('description', slot.get('description')),
            })
        return jsonify({'assignments': results})
    except json.JSONDecodeError as e:
        return jsonify({'error': f'Gemini の出力をパースできませんでした: {e}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/update', methods=['POST'])
def api_update():
    """イベントタイトルを一括更新（patch）"""
    creds = _get_valid_credentials()
    if not creds:
        return jsonify({'error': 'unauthorized'}), 401

    data        = request.get_json()
    calendar_id = (data or {}).get('calendar_id')
    assignments = (data or {}).get('assignments', [])
    if not calendar_id or not assignments:
        return jsonify({'error': 'calendar_id と assignments が必要です'}), 400

    service = _build_calendar_service(creds)
    results = []
    for a in assignments:
        try:
            body = {}
            if a.get('summary') is not None:
                body['summary'] = a['summary']
            if 'location' in a:
                body['location'] = a.get('location') or ''
            if 'description' in a:
                body['description'] = a.get('description') or ''

            date      = a.get('date', '')
            new_start = a.get('start_time')
            new_end   = a.get('end_time')
            if new_start and date:
                dt_s = datetime.fromisoformat(f'{date}T{new_start}:00')
                body['start'] = {'dateTime': dt_s.isoformat(), 'timeZone': 'Asia/Tokyo'}
            if new_end and date:
                dt_e = datetime.fromisoformat(f'{date}T{new_end}:00')
                body['end'] = {'dateTime': dt_e.isoformat(), 'timeZone': 'Asia/Tokyo'}

            if body:
                service.events().patch(
                    calendarId=calendar_id,
                    eventId=a['id'],
                    body=body
                ).execute()
            results.append({'id': a['id'], 'summary': a.get('summary', ''), 'success': True})
        except Exception as e:
            results.append({'id': a['id'], 'success': False, 'error': str(e)})

    return jsonify({'results': results})


# ─────────────────────────────────────────
# 検索インデックス設定
# ─────────────────────────────────────────

@app.route('/api/index-settings/<path:calendar_id>', methods=['GET'])
def api_index_settings_get(calendar_id):
    """カレンダーの index_enabled を取得"""
    db = _get_supabase()
    if not db:
        return jsonify({'index_enabled': False})
    try:
        # calendar_sync_state は user_id が必要だが、ここでは calendar_id のみで判断
        # token.json から user 識別子を取得する代わりに、
        # Google カレンダー API で確認済みの calendar_id をキーに検索する
        res = db.table('calendar_sync_state') \
                .select('index_enabled, person') \
                .eq('calendar_id', calendar_id) \
                .execute()
        if res.data:
            return jsonify({
                'index_enabled': bool(res.data[0]['index_enabled']),
                'person': res.data[0].get('person') or '',
            })
        return jsonify({'index_enabled': False, 'person': ''})
    except Exception as e:
        return jsonify({'index_enabled': False, 'warning': str(e)})


def _trigger_index_sync(calendar_id: str):
    """calendar-index-sync Edge Function をバックグラウンドで呼び出す"""
    user_id = os.environ.get('CALENDAR_SYNC_USER_ID')
    if not user_id or not SUPABASE_URL or not SUPABASE_KEY:
        return
    base = SUPABASE_URL.rstrip('/')
    url  = (f'{base}/functions/v1/calendar-index-sync'
            f'?user_id={urllib.parse.quote(user_id)}'
            f'&calendar_id={urllib.parse.quote(calendar_id)}')
    try:
        req = urllib.request.Request(
            url, headers={'Authorization': f'Bearer {SUPABASE_KEY}'}, method='GET'
        )
        urllib.request.urlopen(req, timeout=120)
    except Exception as e:
        app.logger.warning(f'calendar-index-sync 呼び出し失敗: {e}')


def _register_calendar_watch(calendar_id: str):
    """google-calendar-watch Edge Function を呼び出して push 通知チャンネルを登録"""
    user_id = os.environ.get('CALENDAR_SYNC_USER_ID')
    if not user_id or not SUPABASE_URL or not SUPABASE_KEY:
        return
    base = SUPABASE_URL.rstrip('/')
    url  = (f'{base}/functions/v1/google-calendar-watch'
            f'?user_id={urllib.parse.quote(user_id)}'
            f'&calendar_id={urllib.parse.quote(calendar_id)}')
    try:
        req = urllib.request.Request(
            url, headers={'Authorization': f'Bearer {SUPABASE_KEY}'}, method='GET'
        )
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        app.logger.warning(f'google-calendar-watch 呼び出し失敗: {e}')


@app.route('/api/index-settings/<path:calendar_id>', methods=['POST'])
def api_index_settings_save(calendar_id):
    """index_enabled を更新し、ON なら初回 index-sync を自動実行、OFF ならチャンク削除"""
    db = _get_supabase()
    if not db:
        return jsonify({'error': 'Supabase が設定されていません'}), 500

    data = request.get_json() or {}
    index_enabled = bool(data.get('index_enabled', False))
    person = (data.get('person') or '').strip()

    try:
        user_id = os.environ.get('CALENDAR_SYNC_USER_ID')
        row = {'user_id': user_id, 'calendar_id': calendar_id, 'index_enabled': index_enabled}
        if person:
            row['person'] = person
        db.table('calendar_sync_state').upsert(row, on_conflict='user_id,calendar_id').execute()

        if index_enabled:
            # ON: watch 登録が完了してから index-sync を実行（calendar_name が確定した後に同期）
            def _watch_then_sync(cal_id: str):
                _register_calendar_watch(cal_id)
                _trigger_index_sync(cal_id)
            threading.Thread(target=_watch_then_sync, args=(calendar_id,), daemon=True).start()
        else:
            # OFF: このカレンダーの全イベントレコード + チャンクを削除
            raw = db.table('02_gcal_01_raw') \
                    .select('id') \
                    .eq('calendar_id', calendar_id) \
                    .execute()
            if raw.data:
                raw_ids = [str(r['id']) for r in raw.data]
                # pipeline_meta ID を取得してチャンク + meta を削除
                meta_rows = db.table('pipeline_meta') \
                    .select('id') \
                    .eq('raw_table', '02_gcal_01_raw') \
                    .in_('raw_id', raw_ids) \
                    .execute()
                for r in (meta_rows.data or []):
                    db.table('10_ix_search_index').delete().eq('document_id', r['id']).execute()
                    db.table('pipeline_meta').delete().eq('id', r['id']).execute()
                # 09_unified_documents を削除
                db.table('09_unified_documents') \
                    .delete() \
                    .eq('raw_table', '02_gcal_01_raw') \
                    .in_('raw_id', raw_ids) \
                    .execute()
                # 02_gcal_01_raw を削除
                db.table('02_gcal_01_raw').delete().in_('id', raw_ids).execute()

        return jsonify({'success': True, 'index_enabled': index_enabled, 'person': person})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────
# 検索機能
# ─────────────────────────────────────────

@app.route('/api/search', methods=['GET'])
def api_search():
    """キーワード・タグでイベントを検索"""
    creds = _get_valid_credentials()
    if not creds:
        return jsonify({'error': 'unauthorized'}), 401

    calendar_id = request.args.get('calendar_id')
    q           = request.args.get('q', '').strip()
    tags_param  = request.args.get('tags', '').strip()   # カンマ区切り
    date_from   = request.args.get('from')
    date_to     = request.args.get('to')

    tags = [t.strip() for t in tags_param.split(',') if t.strip()] if tags_param else []

    if not calendar_id:
        return jsonify({'error': 'calendar_id が必要です'}), 400
    if not q and not tags:
        return jsonify({'error': 'q またはタグが必要です'}), 400

    service = _build_calendar_service(creds)

    # Google Calendar API の q: キーワードがなければ最初のタグで代用
    q_for_api = q if q else tags[0]
    base_kwargs = dict(
        q=q_for_api,
        singleEvents=True,
        orderBy='startTime',
        maxResults=200,
    )
    if date_from:
        base_kwargs['timeMin'] = f'{date_from}T00:00:00+09:00'
    if date_to:
        base_kwargs['timeMax'] = f'{date_to}T23:59:59+09:00'

    try:
        # メイン + _arc + _pen の3カレンダーをまとめて検索
        related_ids = _get_related_calendar_ids(service, calendar_id)
        all_items = []
        for cal_id in related_ids:
            try:
                result = service.events().list(calendarId=cal_id, **base_kwargs).execute()
                all_items.extend(result.get('items', []))
            except Exception:
                pass

        all_items.sort(key=lambda e: e.get('start', {}).get('dateTime') or e.get('start', {}).get('date', ''))

        events = []
        for e in all_items:
            desc = e.get('description') or ''
            # タグ AND フィルタリング（Python側）
            if tags and not all(f'#{t}' in desc for t in tags):
                continue
            start = e.get('start', {})
            end   = e.get('end', {})
            events.append({
                'id':          e['id'],
                'title':       e.get('summary', '（タイトルなし）'),
                'date':        (start.get('dateTime') or start.get('date', ''))[:10],
                'start_time':  (start.get('dateTime') or '')[-14:-9] if 'dateTime' in start else None,
                'end_time':    (end.get('dateTime') or '')[-14:-9]   if 'dateTime' in end   else None,
                'all_day':     'date' in start,
                'location':    e.get('location'),
                'description': desc,
                'html_link':   e.get('htmlLink'),
            })
        return jsonify({'events': events, 'count': len(events)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────
# エントリーポイント
# ─────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('CALENDAR_REGISTER_PORT', 5003))
    print(f'[calendar-register] http://localhost:{port}')
    print(f'  credentials.json: {CREDENTIALS_FILE}')
    print(f'  token.json: {TOKEN_FILE}')
    app.run(debug=True, port=port)
