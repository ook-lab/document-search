"""
AI Cost Tracker Dashboard（port 5005）

AIトークン使用量とコストを集計・可視化するFlaskダッシュボード。
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

_db = None


def get_db():
    global _db
    if _db is None:
        from shared.common.database.client import DatabaseClient
        _db = DatabaseClient(use_service_role=True)
    return _db


# ===========================================================================
# ページ
# ===========================================================================

@app.route('/')
def index():
    return render_template('index.html')


# ===========================================================================
# API: サマリー集計
# ===========================================================================

@app.route('/api/summary')
def api_summary():
    """期間別集計（app/stage/model/日別）"""
    try:
        date_from = request.args.get('from', (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d'))
        date_to   = request.args.get('to',   datetime.now().strftime('%Y-%m-%d'))

        db = get_db()

        # ai_usage_logs を取得
        resp = (
            db.client.table('ai_usage_logs')
            .select('*')
            .gte('created_at', f'{date_from}T00:00:00')
            .lte('created_at', f'{date_to}T23:59:59')
            .order('created_at')
            .execute()
        )
        logs = resp.data or []

        # 単価マスタ取得
        pricing_map = _get_pricing_map(db)

        # 集計
        summary = _aggregate(logs, pricing_map)

        return jsonify({'success': True, 'summary': summary, 'count': len(logs)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ===========================================================================
# API: 詳細レコード
# ===========================================================================

@app.route('/api/detail')
def api_detail():
    """フィルタ付き詳細レコード一覧"""
    try:
        date_from = request.args.get('from', (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d'))
        date_to   = request.args.get('to',   datetime.now().strftime('%Y-%m-%d'))
        app_filter   = request.args.get('app', '')
        stage_filter = request.args.get('stage', '')
        limit = int(request.args.get('limit', 200))

        db = get_db()
        q = (
            db.client.table('ai_usage_logs')
            .select('*')
            .gte('created_at', f'{date_from}T00:00:00')
            .lte('created_at', f'{date_to}T23:59:59')
            .order('created_at', desc=True)
            .limit(limit)
        )
        if app_filter:
            q = q.eq('app', app_filter)
        if stage_filter:
            q = q.eq('stage', stage_filter)

        resp = q.execute()
        logs = resp.data or []

        pricing_map = _get_pricing_map(db)
        for log in logs:
            log['cost_usd'] = _calc_cost(log, pricing_map)

        return jsonify({'success': True, 'logs': logs, 'count': len(logs)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ===========================================================================
# API: 単価マスタ
# ===========================================================================

@app.route('/api/pricing', methods=['GET'])
def api_pricing_get():
    """単価マスタ一覧取得"""
    try:
        db = get_db()
        resp = db.client.table('ai_model_pricing').select('*').order('model').execute()
        return jsonify({'success': True, 'pricing': resp.data or []})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/pricing', methods=['POST'])
def api_pricing_post():
    """単価の追加・更新"""
    try:
        data = request.get_json()
        required = ['model', 'input_price_per_1m', 'output_price_per_1m']
        for f in required:
            if f not in data:
                return jsonify({'success': False, 'error': f'{f} is required'}), 400

        db = get_db()
        db.client.table('ai_model_pricing').upsert({
            'model': data['model'],
            'source_type': data.get('source_type', 'all'),
            'prompt_tier': data.get('prompt_tier', 'all'),
            'input_price_per_1m': float(data['input_price_per_1m']),
            'output_price_per_1m': float(data['output_price_per_1m']),
            'thinking_price_per_1m': float(data.get('thinking_price_per_1m', 0)),
            'notes': data.get('notes', ''),
        }, on_conflict='model,source_type,prompt_tier').execute()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ===========================================================================
# API: セッション別内訳（ドキュメント単位）
# ===========================================================================

@app.route('/api/sessions')
def api_sessions():
    """session_id（ドキュメント/リクエスト）別の内訳を返す"""
    try:
        date_from = request.args.get('from', (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d'))
        date_to   = request.args.get('to',   datetime.now().strftime('%Y-%m-%d'))
        app_filter = request.args.get('app', '')
        limit = int(request.args.get('limit', 100))

        db = get_db()
        q = (
            db.client.table('ai_usage_logs')
            .select('session_id, app, stage, model, prompt_token_count, candidates_token_count, thoughts_token_count, total_token_count, created_at')
            .gte('created_at', f'{date_from}T00:00:00')
            .lte('created_at', f'{date_to}T23:59:59')
            .not_.is_('session_id', 'null')
            .order('created_at', desc=True)
        )
        if app_filter:
            q = q.eq('app', app_filter)

        resp = q.execute()
        logs = resp.data or []

        pricing_map = _get_pricing_map(db)

        # session_id ごとに集計
        sessions: dict = {}
        for log in logs:
            sid = log['session_id']
            app_name = log.get('app', 'unknown')
            stage = log.get('stage', 'unknown')

            if sid not in sessions:
                sessions[sid] = {
                    'session_id': sid,
                    'app': app_name,
                    'first_seen': log.get('created_at', ''),
                    'stages': {},
                    'total_cost_usd': 0.0,
                    'total_tokens': 0,
                    'title': None,
                    'file_name': None,
                }

            cost = _calc_cost(log, pricing_map)
            sessions[sid]['total_cost_usd'] += cost
            sessions[sid]['total_tokens']   += log.get('total_token_count', 0) or 0

            sk = stage
            if sk not in sessions[sid]['stages']:
                sessions[sid]['stages'][sk] = {
                    'stage': sk,
                    'models': set(),
                    'count': 0,
                    'prompt_tokens': 0,
                    'candidates_tokens': 0,
                    'thoughts_tokens': 0,
                    'total_tokens': 0,
                    'cost_usd': 0.0,
                }
            s = sessions[sid]['stages'][sk]
            s['models'].add(log.get('model', '') or '')
            s['count']             += 1
            s['prompt_tokens']     += log.get('prompt_token_count', 0) or 0
            s['candidates_tokens'] += log.get('candidates_token_count', 0) or 0
            s['thoughts_tokens']   += log.get('thoughts_token_count', 0) or 0
            s['total_tokens']      += log.get('total_token_count', 0) or 0
            s['cost_usd']          += cost

        # doc-processor のセッションは pipeline_meta → raw テーブルからタイトルを取得
        doc_processor_ids = [
            sid for sid, s in sessions.items()
            if s['app'] == 'doc-processor'
        ]
        if doc_processor_ids:
            try:
                # pipeline_meta から raw_id, raw_table を取得
                meta_resp = (
                    db.client.table('pipeline_meta')
                    .select('id, raw_id, raw_table')
                    .in_('id', doc_processor_ids[:200])
                    .execute()
                )
                metas = meta_resp.data or []

                # raw_table ごとにグループ化して一括クエリ
                by_raw_table: dict = {}
                meta_map: dict = {}  # pipeline_meta.id → {raw_id, raw_table}
                for row in metas:
                    rt = row.get('raw_table', '')
                    ri = row.get('raw_id', '')
                    meta_map[row['id']] = {'raw_id': ri, 'raw_table': rt}
                    by_raw_table.setdefault(rt, []).append(ri)

                # 各 raw テーブルからタイトル列を取得
                raw_title: dict = {}  # raw_id → title文字列
                for rt, raw_ids in by_raw_table.items():
                    if not rt or not raw_ids:
                        continue
                    try:
                        # テーブルごとに適切な列を取得
                        if rt == '01_gmail_01_raw':
                            cols = 'id, header_subject'
                        else:
                            cols = 'id, file_name'
                        raw_resp = (
                            db.client.table(rt)
                            .select(cols)
                            .in_('id', raw_ids[:200])
                            .execute()
                        )
                        for rrow in (raw_resp.data or []):
                            rid = rrow.get('id')
                            title = (
                                rrow.get('header_subject')
                                or rrow.get('file_name')
                                or ''
                            )
                            raw_title[rid] = title
                    except Exception:
                        pass

                # session_id → title にマップ
                for sid, info in meta_map.items():
                    if sid in sessions:
                        title = raw_title.get(info['raw_id'], '')
                        sessions[sid]['title']     = title
                        sessions[sid]['file_name'] = title
            except Exception:
                pass  # タイトル取得失敗は非クリティカル

        # 結果整形
        result = []
        for s in sessions.values():
            s['total_cost_usd'] = round(s['total_cost_usd'], 6)
            for st in s['stages'].values():
                st['cost_usd'] = round(st['cost_usd'], 6)
                st['models'] = ', '.join(sorted(m for m in st['models'] if m))
            s['stages'] = sorted(s['stages'].values(), key=lambda x: x['stage'])
            result.append(s)

        # 最新順
        result.sort(key=lambda x: x['first_seen'], reverse=True)
        result = result[:limit]

        return jsonify({'success': True, 'sessions': result, 'count': len(result)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ===========================================================================
# API: モデル変更シミュレーション
# ===========================================================================

@app.route('/api/simulate')
def api_simulate():
    """既存ログを別モデル単価で再計算してコスト差を返す"""
    try:
        model_from = request.args.get('model_from', '')
        model_to   = request.args.get('model_to', '')
        date_from  = request.args.get('from', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
        date_to    = request.args.get('to',   datetime.now().strftime('%Y-%m-%d'))

        if not model_from or not model_to:
            return jsonify({'success': False, 'error': 'model_from and model_to are required'}), 400

        db = get_db()
        resp = (
            db.client.table('ai_usage_logs')
            .select('*')
            .eq('model', model_from)
            .gte('created_at', f'{date_from}T00:00:00')
            .lte('created_at', f'{date_to}T23:59:59')
            .execute()
        )
        logs = resp.data or []

        pricing_map = _get_pricing_map(db)

        cost_original = sum(_calc_cost(log, pricing_map) for log in logs)

        # model_to の単価で再計算
        cost_simulated = 0.0
        for log in logs:
            pt = log.get('prompt_token_count', 0) or 0
            tier = 'large' if pt > 200_000 else 'standard'
            p_to = (
                pricing_map.get((model_to, 'all', tier))
                or pricing_map.get((model_to, 'all', 'all'))
                or {}
            )
            cost_simulated += (
                pt / 1_000_000 * float(p_to.get('input_price_per_1m', 0))
                + (log.get('candidates_token_count', 0) or 0) / 1_000_000 * float(p_to.get('output_price_per_1m', 0))
                + (log.get('thoughts_token_count', 0) or 0) / 1_000_000 * float(p_to.get('thinking_price_per_1m', 0))
            )

        total_tokens = sum((log.get('total_token_count', 0) or 0) for log in logs)

        return jsonify({
            'success': True,
            'model_from': model_from,
            'model_to': model_to,
            'record_count': len(logs),
            'total_tokens': total_tokens,
            'cost_original_usd': round(cost_original, 6),
            'cost_simulated_usd': round(cost_simulated, 6),
            'cost_diff_usd': round(cost_simulated - cost_original, 6),
            'cost_ratio': round(cost_simulated / cost_original, 4) if cost_original > 0 else None,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ===========================================================================
# 内部ヘルパー
# ===========================================================================

def _get_pricing_map(db) -> dict:
    """(model, source_type, prompt_tier) → 単価行 の辞書を返す"""
    resp = db.client.table('ai_model_pricing').select('*').order('effective_from', desc=True).execute()
    pricing_map = {}
    for row in (resp.data or []):
        key = (row['model'], row.get('source_type', 'all'), row.get('prompt_tier', 'all'))
        if key not in pricing_map:
            pricing_map[key] = row
    return pricing_map


def _calc_cost(log: dict, pricing_map: dict) -> float:
    """1レコードのコスト（USD）を計算。prompt_tierはトークン数から自動判定"""
    model = log.get('model', '')
    prompt_tokens = log.get('prompt_token_count', 0) or 0
    prompt_tier = 'large' if prompt_tokens > 200_000 else 'standard'

    # 優先順位: text_image_video → all（ログにsource_typeがないため text_image_video をデフォルトとする）
    p = (
        pricing_map.get((model, 'text_image_video', prompt_tier))
        or pricing_map.get((model, 'text_image_video', 'all'))
        or pricing_map.get((model, 'all', prompt_tier))
        or pricing_map.get((model, 'all', 'all'))
        or {}
    )
    if not p:
        return 0.0
    return (
        prompt_tokens / 1_000_000 * float(p.get('input_price_per_1m', 0))
        + (log.get('candidates_token_count', 0) or 0) / 1_000_000 * float(p.get('output_price_per_1m', 0))
        + (log.get('thoughts_token_count', 0) or 0) / 1_000_000 * float(p.get('thinking_price_per_1m', 0))
    )


def _aggregate(logs: list, pricing_map: dict) -> dict:
    """ログリストを複数軸で集計して返す"""
    total_cost = 0.0
    total_tokens = 0
    by_app   = {}
    by_stage = {}
    by_model = {}
    by_day   = {}

    for log in logs:
        cost = _calc_cost(log, pricing_map)
        tokens = log.get('total_token_count', 0) or 0
        total_cost   += cost
        total_tokens += tokens

        # by_app
        a = log.get('app', 'unknown')
        by_app.setdefault(a, {'cost': 0.0, 'tokens': 0, 'count': 0})
        by_app[a]['cost']   += cost
        by_app[a]['tokens'] += tokens
        by_app[a]['count']  += 1

        # by_stage
        s = log.get('stage', 'unknown')
        key = f"{a}/{s}"
        by_stage.setdefault(key, {'app': a, 'stage': s, 'cost': 0.0, 'tokens': 0, 'count': 0})
        by_stage[key]['cost']   += cost
        by_stage[key]['tokens'] += tokens
        by_stage[key]['count']  += 1

        # by_model
        m = log.get('model', 'unknown')
        by_model.setdefault(m, {'cost': 0.0, 'tokens': 0, 'count': 0,
                                'prompt_tokens': 0, 'candidates_tokens': 0, 'thoughts_tokens': 0})
        by_model[m]['cost']              += cost
        by_model[m]['tokens']            += tokens
        by_model[m]['count']             += 1
        by_model[m]['prompt_tokens']     += log.get('prompt_token_count', 0) or 0
        by_model[m]['candidates_tokens'] += log.get('candidates_token_count', 0) or 0
        by_model[m]['thoughts_tokens']   += log.get('thoughts_token_count', 0) or 0

        # by_day
        day = (log.get('created_at', '') or '')[:10]
        by_day.setdefault(day, {'cost': 0.0, 'tokens': 0, 'count': 0})
        by_day[day]['cost']   += cost
        by_day[day]['tokens'] += tokens
        by_day[day]['count']  += 1

    # コストをroundして返す
    for d in by_app.values():
        d['cost'] = round(d['cost'], 6)
    for d in by_stage.values():
        d['cost'] = round(d['cost'], 6)
    for d in by_model.values():
        d['cost'] = round(d['cost'], 6)
    for d in by_day.values():
        d['cost'] = round(d['cost'], 6)

    return {
        'total_cost_usd':  round(total_cost, 6),
        'total_tokens':    total_tokens,
        'total_records':   len(logs),
        'by_app':          [{'app': k, **v} for k, v in sorted(by_app.items())],
        'by_stage':        sorted(by_stage.values(), key=lambda x: -x['cost']),
        'by_model':        sorted(by_model.items(), key=lambda x: -x[1]['cost']),
        'by_day':          [{'day': k, **v} for k, v in sorted(by_day.items())],
    }


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5005, debug=True)
