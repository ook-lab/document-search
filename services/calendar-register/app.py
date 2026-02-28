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
    """credentials を保存（ローカルファイル + Secret Manager）"""
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
【プリセット辞書】（以下の定義を優先して解釈すること）
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
        return jsonify({'preset_text': ''})
    try:
        res = db.table('calendar_presets').select('preset_text').eq('calendar_id', calendar_id).execute()
        if res.data:
            return jsonify({'preset_text': res.data[0]['preset_text']})
        return jsonify({'preset_text': ''})
    except Exception as e:
        return jsonify({'preset_text': '', 'warning': str(e)})


@app.route('/api/presets/<path:calendar_id>', methods=['POST'])
def api_preset_save(calendar_id):
    """カレンダーのプリセットを保存"""
    db = _get_supabase()
    if not db:
        return jsonify({'error': 'Supabase が設定されていません'}), 500
    data = request.get_json()
    preset_text = (data or {}).get('preset_text', '')
    try:
        db.table('calendar_presets').upsert(
            {'calendar_id': calendar_id, 'preset_text': preset_text},
            on_conflict='calendar_id'
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
    results = []

    for ev in events:
        try:
            body = _build_event_body(ev)
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

def _build_event_body(ev: dict) -> dict:
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
    try:
        result = service.events().list(
            calendarId=calendar_id,
            timeMin=f'{date_from}T00:00:00+09:00',
            timeMax=f'{date_to}T23:59:59+09:00',
            singleEvents=True,
            orderBy='startTime',
            maxResults=200,
        ).execute()

        events = []
        for e in result.get('items', []):
            start = e.get('start', {})
            end   = e.get('end', {})
            events.append({
                'id':    e['id'],
                'title': e.get('summary', '（タイトルなし）'),
                'date':  (start.get('dateTime') or start.get('date', ''))[:10],
                'start_time': (start.get('dateTime') or '')[-14:-9] if 'dateTime' in start else None,
                'end_time':   (end.get('dateTime') or '')[-14:-9]   if 'dateTime' in end   else None,
                'all_day': 'date' in start,
            })
        return jsonify({'events': events})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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

    slots_text = '\n'.join(
        f'{s["date"]} {s.get("start_time","") or "終日"} {s.get("end_time","") or ""} [{s["id"]}] {s["title"]}'
        for s in slots
    )

    prompt = f"""
あなたは学習スケジュールの割り振り専門家です。

以下の【既存の枠】に対して、【割り振り指示】に従って科目・タイトルを割り当ててください。

【既存の枠】（日付 開始時刻 終了時刻 [イベントID] 現タイトル）
{slots_text}

【割り振り指示】
{subject_text}

【出力ルール】
- 各枠に対して新しいタイトルを割り当てる
- 割り振り指示に書かれていない枠は現タイトルをそのまま維持する
- JSON配列のみ返す（説明文不要）

【出力形式】
[
  {{"id": "イベントID", "new_title": "新しいタイトル"}},
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

        # slots の情報とマージしてプレビュー用データを返す
        id_to_slot = {s['id']: s for s in slots}
        results = []
        for a in assignments:
            slot = id_to_slot.get(a['id'], {})
            results.append({
                'id':        a['id'],
                'new_title': a['new_title'],
                'date':      slot.get('date', ''),
                'start_time': slot.get('start_time'),
                'end_time':   slot.get('end_time'),
                'old_title':  slot.get('title', ''),
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
            service.events().patch(
                calendarId=calendar_id,
                eventId=a['id'],
                body={'summary': a['new_title']}
            ).execute()
            results.append({'id': a['id'], 'title': a['new_title'], 'success': True})
        except Exception as e:
            results.append({'id': a['id'], 'title': a.get('new_title', ''), 'success': False, 'error': str(e)})

    return jsonify({'results': results})


# ─────────────────────────────────────────
# エントリーポイント
# ─────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('CALENDAR_REGISTER_PORT', 5003))
    print(f'[calendar-register] http://localhost:{port}')
    print(f'  credentials.json: {CREDENTIALS_FILE}')
    print(f'  token.json: {TOKEN_FILE}')
    app.run(debug=True, port=port)
