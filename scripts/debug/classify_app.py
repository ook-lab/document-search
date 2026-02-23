"""
PDF分類検証アプリ

10〜20ファイルをまとめてアップロードし、
分類結果と根拠を一覧表示する。

起動:
    python scripts/debug/classify_app.py
    → http://localhost:5050
"""

import re
import sys
import io
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Supabase 保存（インポート失敗しても分類は動く）
try:
    from shared.common.database.client import DatabaseClient
    _db = DatabaseClient(use_service_role=True)
    _SUPABASE_ENABLED = True
except Exception as _e:
    print(f"[classify_app] Supabase 接続スキップ: {_e}")
    _db = None
    _SUPABASE_ENABLED = False

import yaml
from flask import Flask, request, jsonify, render_template_string

try:
    import pdfplumber
except ImportError:
    print("ERROR: pip install pdfplumber")
    sys.exit(1)

# ── 設定読み込み ──────────────────────────────────────────────
_RULES_FILE = Path(__file__).parent.parent.parent / 'shared/pipeline/stage_a/type_rules.yaml'

def _load_rules() -> dict:
    with open(_RULES_FILE, encoding='utf-8') as f:
        return yaml.safe_load(f)

RULES = _load_rules()
CREATOR_PATTERNS  = RULES.get('creator_patterns', {})
PRODUCER_PATTERNS = RULES.get('producer_patterns', {})
WING_PATTERN = re.compile(r'wing', re.IGNORECASE)
SUBSET_RE    = re.compile(r'^[A-Z]{6}\+')

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200MB


# ── 分類ロジック ──────────────────────────────────────────────

def _match(text: str, patterns: list) -> str | None:
    for p in patterns:
        if re.search(p, text, re.IGNORECASE):
            return p
    return None


_TITLE_EXT_MAP = {
    r'\.xlsx?$':  'EXCEL',
    r'\.docx?$':  'WORD',
    r'\.pptx?$':  'WORD',
    r'\.ai$':     'ILLUSTRATOR',
    r'\.indd$':   'INDESIGN',
    r'\.numbers$':'EXCEL',
    r'\.pages$':  'WORD',
    r'\.ods$':    'EXCEL',
    r'\.odt$':    'WORD',
}


def _classify_by_title(title: str) -> tuple[str, str] | None:
    """Titleフィールドの拡張子から元ファイル種別を判定。判定できなければNone。"""
    for pattern, verdict in _TITLE_EXT_MAP.items():
        if re.search(pattern, title, re.IGNORECASE):
            return verdict, f'Title拡張子一致: "{re.search(pattern, title, re.IGNORECASE).group()}" (Title={title!r})'
    return None


def _classify_by_meta(creator: str, producer: str, title: str = '') -> tuple[str, str]:
    """Creator → Title拡張子 → Producer の順で分類。(verdict, reason)"""
    # Step 1: Creator照合
    c = creator.lower()
    for app_name, patterns in CREATOR_PATTERNS.items():
        hit = _match(c, patterns)
        if hit:
            return app_name, f'Creator一致: "{hit}"'

    # Step 2: Titleの拡張子（CreatorなしでもTitleに元ファイル名が残る）
    if title:
        result = _classify_by_title(title)
        if result:
            return result

    # Step 3: Producer照合
    p = producer.lower()
    for app_name, patterns in PRODUCER_PATTERNS.items():
        hit = _match(p, patterns)
        if hit:
            return app_name, f'Producer一致: "{hit}"（Creatorなし）'

    if not creator and not producer and not title:
        return 'UNKNOWN', 'Creator/Producer/Titleともに空'
    return 'UNKNOWN', f'パターン不一致 (Creator={creator!r}, Producer={producer!r}, Title={title!r})'


def _strip_subset(fontname: str) -> str:
    return SUBSET_RE.sub('', fontname)


def _analyze_page(page) -> dict:
    """1ページの特徴を収集"""
    chars  = page.chars  or []
    images = page.images or []

    char_count  = len(chars)
    image_count = len(images)

    # ベクター数（線・曲線・矩形）
    lines  = page.lines  or []
    curves = page.curves or []
    rects  = page.rects  or []
    vector_count = len(lines) + len(curves) + len(rects)

    # フォント名収集
    fonts: set[str] = set()
    for c in chars:
        fn = _strip_subset(c.get('fontname', ''))
        if fn:
            fonts.add(fn)

    # WINGフォント
    wing_fonts = [f for f in fonts if WING_PATTERN.search(f)]

    # 画像カラースペース・フィルター（colorspaceはリストで返る場合があるので文字列化）
    colorspaces = list({str(img.get('colorspace', '')) for img in images if img.get('colorspace')})
    filters     = list({str(img.get('filter', ''))     for img in images if img.get('filter')})

    # テキスト選択可能か
    has_selectable_text = char_count > 0

    # x0座標の分布（テキスト流れの均一性）
    x0_values = [c['x0'] for c in chars if 'x0' in c]
    x0_std = 0.0
    if len(x0_values) > 1:
        mean = sum(x0_values) / len(x0_values)
        x0_std = (sum((x - mean) ** 2 for x in x0_values) / len(x0_values)) ** 0.5

    return {
        'chars':               char_count,
        'images':              image_count,
        'vectors':             vector_count,
        'fonts':               sorted(fonts),
        'wing_fonts':          wing_fonts,
        'colorspaces':         colorspaces,
        'filters':             filters,
        'has_selectable_text': has_selectable_text,
        'x0_std':              round(x0_std, 1),
    }


def _classify_page(pg: dict, meta_type: str = '') -> tuple[str, str]:
    """ページ特徴から種別と根拠を返す"""
    chars   = pg['chars']
    images  = pg['images']
    vectors = pg['vectors']
    wings   = pg['wing_fonts']
    cs      = pg['colorspaces']

    # SCAN: テキスト選択不可 + 画像あり
    if not pg['has_selectable_text'] and images > 0:
        return 'SCAN', f'テキスト選択不可 + 画像{images}枚'

    # REPORT: WINGフォント
    if wings:
        return 'REPORT', f'WINGフォント検出: {wings}'

    # DTP: CMYKカラー（印刷用カラースペース = DTP の正の根拠）
    if 'DeviceCMYK' in cs:
        return 'DTP', f'CMYKカラー（DeviceCMYK）'

    # テキストあり → Creator確定種別があればそれを優先
    if chars > 0:
        _word_family = {'WORD', 'WORD_LTSC', 'WORD_2019'}
        if meta_type and meta_type not in _word_family and meta_type != 'UNKNOWN':
            return meta_type, f'テキスト選択可 ({chars}字) ← Creator確定={meta_type}'
        if not meta_type or meta_type == 'UNKNOWN':
            # Creator不明 → 根拠なし → UNKNOWN
            return 'UNKNOWN', f'テキスト選択可 ({chars}字) ← Creator不明・根拠なし'
        # Creator が WORD 系
        return 'WORD', f'テキスト選択可 ({chars}字) ← Creator確定=WORD'

    # 完全空白（テキストも画像もない）
    if not meta_type or meta_type == 'UNKNOWN':
        return 'UNKNOWN', '空白ページ（chars=0, images=0）'
    return meta_type, f'空白ページ（chars=0, images=0） ← Creator確定={meta_type}'


def _majority_verdict(page_verdicts: list[str]) -> str:
    """過半数の種別をドキュメント種別とする"""
    if not page_verdicts:
        return 'UNKNOWN'
    counts: dict[str, int] = {}
    for v in page_verdicts:
        counts[v] = counts.get(v, 0) + 1
    # 種類が複数 → MIXED
    unique = set(page_verdicts)
    if len(unique) > 1:
        return 'MIXED'
    return page_verdicts[0]


def _extract_raw_meta(file_bytes: bytes) -> dict:
    """pdfplumber と PyMuPDF の両方からメタデータを取得してマージする。"""
    import tempfile, os
    raw = {}

    # PyMuPDF で取得（tempfileを使ってパスで開く）
    tmp_path = None
    try:
        import fitz
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        doc = fitz.open(tmp_path)
        m = doc.metadata or {}
        for k, v in m.items():
            try:
                raw[str(k)] = str(v) if v is not None else ''
            except Exception:
                raw[str(k)] = repr(v)
        doc.close()
    except Exception:
        pass
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # pdfplumber で追加取得（PyMuPDF で取れなかったフィールドを補完）
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            m = pdf.metadata or {}
            for k, v in m.items():
                key = str(k)
                if key not in raw or not raw[key]:
                    try:
                        raw[key] = str(v) if v is not None else ''
                    except Exception:
                        raw[key] = repr(v)
    except Exception:
        pass

    return raw


def analyze_pdf_bytes(file_bytes: bytes, filename: str) -> dict:
    """PDFバイト列を解析して分類結果を返す"""
    try:
        # メタデータ取得（pdfplumber → PyMuPDF フォールバック）
        raw_meta = _extract_raw_meta(file_bytes)

        def _get(*keys):
            for k in keys:
                v = raw_meta.get(k) or raw_meta.get('/' + k) or ''
                if v:
                    return str(v).strip()
            return ''

        creator  = _get('Creator', 'creator')
        producer = _get('Producer', 'producer')
        title    = _get('Title', 'title')

        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:

            # メタデータ分類
            meta_verdict, meta_reason = _classify_by_meta(creator, producer, title)

            # 権威あるメタデータ（Creator確定）
            authoritative = {
                'GOODNOTES', 'GOOGLE_DOCS', 'GOOGLE_SHEETS',
                'WORD', 'WORD_LTSC', 'WORD_2019', 'EXCEL', 'ILLUSTRATOR', 'INDESIGN',
            }

            # ページ別解析
            page_results = []
            for i, page in enumerate(pdf.pages):
                pg = _analyze_page(page)
                verdict, reason = _classify_page(pg, meta_verdict)
                page_results.append({
                    'page':    i + 1,
                    'verdict': verdict,
                    'reason':  reason,
                    **pg,
                })

            page_verdicts = [p['verdict'] for p in page_results]

            # 最終判定
            if meta_verdict in authoritative:
                # Creatorで確定でも、SCAN/REPORTページが混在していればMIXED
                # SCAN:   テキスト選択不可 → 別プロセッサ（B80）が必要
                # REPORT: WINGフォント確定 → 別プロセッサ（B42）が必要。Creator種別と共存不可
                scan_pages   = [p for p in page_results if p['verdict'] == 'SCAN']
                report_pages = [p for p in page_results if p['verdict'] == 'REPORT'] if meta_verdict != 'REPORT' else []
                if scan_pages or report_pages:
                    detail = []
                    if scan_pages:   detail.append(f'SCANページ{len(scan_pages)}枚')
                    if report_pages: detail.append(f'REPORTページ{len(report_pages)}枚')
                    doc_verdict = 'MIXED'
                    doc_reason  = f'{meta_reason} + {"・".join(detail)}混在'
                else:
                    doc_verdict = meta_verdict
                    doc_reason  = meta_reason
            elif page_results:
                doc_verdict = _majority_verdict(page_verdicts)
                if doc_verdict == 'MIXED':
                    breakdown = {}
                    for v in page_verdicts:
                        breakdown[v] = breakdown.get(v, 0) + 1
                    doc_reason = f'ページ混在: {breakdown}（メタ: {meta_reason}）'
                else:
                    # ページ根拠を代表1件取得（最初に一致したページ）
                    page_reason = next(
                        (p['reason'] for p in page_results if p['verdict'] == doc_verdict),
                        ''
                    )
                    doc_reason = f'{page_reason} ／ メタ: {meta_reason}'
            else:
                doc_verdict = meta_verdict
                doc_reason  = meta_reason

            return {
                'filename':    filename,
                'creator':     creator  or '',
                'producer':    producer or '',
                'title':       title    or '',
                'raw_meta':    raw_meta,
                'verdict':     doc_verdict,
                'reason':      doc_reason,
                'page_count':  len(page_results),
                'pages':       page_results,
                'error':       None,
            }

    except Exception as e:
        return {
            'filename':   filename,
            'creator':    '',
            'producer':   '',
            'title':      '',
            'raw_meta':   {},
            'verdict':    'ERROR',
            'reason':     str(e),
            'page_count': 0,
            'pages':      [],
            'error':      str(e),
        }


# ── HTML ────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>PDF分類検証</title>
<style>
  body { font-family: monospace; margin: 20px; background: #1a1a1a; color: #e0e0e0; }
  h1 { color: #88aaff; }
  form { margin-bottom: 20px; }
  input[type=file] { color: #e0e0e0; }
  button { background: #3355cc; color: white; padding: 8px 20px; border: none; cursor: pointer; border-radius: 4px; }
  button:hover { background: #4466dd; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 30px; }
  th { background: #333; color: #aad; text-align: left; padding: 6px 10px; }
  td { padding: 5px 10px; border-bottom: 1px solid #333; vertical-align: top; }
  tr:hover { background: #222; }
  .verdict { font-weight: bold; font-size: 1.1em; }
  .WORD      { color: #88ff88; }
  .SCAN      { color: #ff8888; }
  .REPORT    { color: #ffcc44; }
  .DTP       { color: #ff88ff; }
  .EXCEL     { color: #44ffcc; }
  .GOODNOTES { color: #ffaa44; }
  .GOOGLE_DOCS { color: #44aaff; }
  .GOOGLE_SHEETS { color: #44ddaa; }
  .INDESIGN  { color: #cc88ff; }
  .ILLUSTRATOR { color: #ff88cc; }
  .MIXED     { color: #ffff44; }
  .UNKNOWN   { color: #888; }
  .ERROR     { color: #ff4444; }
  details summary { cursor: pointer; color: #aaa; }
  details table { margin-top: 6px; font-size: 0.85em; }
  .meta { color: #aaa; font-size: 0.85em; }
  #status { color: #aaa; margin: 10px 0; }
</style>
</head>
<body>
<h1>PDF分類検証</h1>
<form id="form">
  <input type="file" id="files" accept=".pdf" multiple>
  <button type="button" onclick="classify()">分類実行</button>
</form>
<div id="status"></div>
<div id="result"></div>

<script>
async function classify() {
  const files = document.getElementById('files').files;
  if (!files.length) { alert('ファイルを選択してください'); return; }

  const status = document.getElementById('status');
  const result = document.getElementById('result');
  status.textContent = `処理中... (${files.length}ファイル)`;
  result.innerHTML = '';

  const fd = new FormData();
  for (const f of files) fd.append('pdfs', f);

  try {
    const res  = await fetch('/classify', { method: 'POST', body: fd });
    const data = await res.json();
    status.textContent = `完了: ${data.length}ファイル`;
    result.innerHTML = renderTable(data);
  } catch(e) {
    status.textContent = 'エラー: ' + e;
  }
}

function renderTable(data) {
  let html = '<table>';
  html += '<tr><th>#</th><th>ファイル名</th><th>分類</th><th>根拠</th><th>メタデータ（全フィールド）</th><th>ページ</th></tr>';

  data.forEach((r, i) => {
    const cls = r.verdict;
    html += `<tr>
      <td>${i+1}</td>
      <td>${esc(r.filename)}</td>
      <td class="verdict ${cls}">${cls}</td>
      <td>${esc(r.reason)}</td>
      <td class="meta">${renderMeta(r.raw_meta)}</td>
      <td>${renderPages(r.pages)}</td>
    </tr>`;
  });

  html += '</table>';
  return html;
}

function renderPages(pages) {
  if (!pages || !pages.length) return '-';
  let rows = '<details><summary>ページ詳細</summary><table>';
  rows += '<tr><th>P</th><th>種別</th><th>根拠</th><th>chars</th><th>img</th><th>vec</th><th>x0σ</th></tr>';
  pages.forEach(p => {
    rows += `<tr>
      <td>${p.page}</td>
      <td class="${p.verdict}">${p.verdict}</td>
      <td>${esc(p.reason)}</td>
      <td>${p.chars}</td>
      <td>${p.images}</td>
      <td>${p.vectors}</td>
      <td>${p.x0_std}</td>
    </tr>`;
  });
  rows += '</table></details>';
  return rows;
}

function renderMeta(raw) {
  if (!raw || !Object.keys(raw).length) return '（メタデータなし）';
  return Object.entries(raw).map(([k, v]) =>
    `<div><b>${esc(k)}</b>: ${esc(v || '（空）')}</div>`
  ).join('');
}

function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
</script>
</body>
</html>
"""


# ── Supabase 保存 ─────────────────────────────────────────────

def _save_to_supabase(result: dict) -> None:
    """分類結果を classify_results + classify_page_details に保存する。失敗しても無視。"""
    if not _SUPABASE_ENABLED or not _db:
        return
    try:
        doc_row = {
            'filename':   result['filename'],
            'creator':    result.get('creator') or None,
            'producer':   result.get('producer') or None,
            'pdf_title':  result.get('title') or None,
            'raw_meta':   result.get('raw_meta') or {},
            'verdict':    result['verdict'],
            'reason':     result.get('reason') or None,
            'page_count': result.get('page_count') or 0,
            'error_msg':  result.get('error') or None,
        }
        res = _db.client.table('classify_results').insert(doc_row).execute()
        if not res.data:
            print(f"[classify_app] classify_results 挿入失敗: {result['filename']}")
            return

        result_id = res.data[0]['id']

        pages = result.get('pages') or []
        if pages:
            page_rows = [
                {
                    'result_id':           result_id,
                    'page_num':            p['page'],
                    'verdict':             p['verdict'],
                    'reason':              p.get('reason') or None,
                    'chars':               p.get('chars'),
                    'images':              p.get('images'),
                    'vectors':             p.get('vectors'),
                    'has_selectable_text': p.get('has_selectable_text'),
                    'x0_std':              p.get('x0_std'),
                    'fonts':               p.get('fonts') or [],
                    'wing_fonts':          p.get('wing_fonts') or [],
                    'colorspaces':         p.get('colorspaces') or [],
                    'filters':             p.get('filters') or [],
                }
                for p in pages
            ]
            _db.client.table('classify_page_details').insert(page_rows).execute()

        print(f"[classify_app] Supabase 保存完了: {result['filename']} (result_id={result_id[:8]})")
    except Exception as e:
        print(f"[classify_app] Supabase 保存エラー ({result['filename']}): {e}")


# ── エンドポイント ────────────────────────────────────────────

@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/classify', methods=['POST'])
def classify():
    files = request.files.getlist('pdfs')
    if not files:
        return jsonify({'error': 'ファイルなし'}), 400

    results = []
    for f in files:
        data = f.read()
        result = analyze_pdf_bytes(data, f.filename)
        _save_to_supabase(result)
        results.append(result)

    return jsonify(results)


if __name__ == '__main__':
    print("http://localhost:5050 で起動します")
    app.run(port=5050, debug=True)
