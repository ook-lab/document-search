from flask import Flask, render_template, request, redirect, url_for, jsonify
from datetime import datetime
from collections import defaultdict
from pathlib import Path
from dotenv import load_dotenv

# .env 読み込み（プロジェクトルート）
load_dotenv(Path(__file__).parent / '../../.env')

from db_client import get_db

app = Flask(__name__)


# ──────────────────────────────────────────────
# 共通ヘルパー
# ──────────────────────────────────────────────

def month_range(year_month):
    year, month = map(int, year_month.split('-'))
    start = f"{year_month}-01"
    end = f"{year + 1}-01-01" if month == 12 else f"{year}-{month + 1:02d}-01"
    return start, end


def prev_next_month(year_month):
    year, month = map(int, year_month.split('-'))
    prev_y, prev_m = (year - 1, 12) if month == 1  else (year, month - 1)
    next_y, next_m = (year + 1, 1)  if month == 12 else (year, month + 1)
    return f"{prev_y}-{prev_m:02d}", f"{next_y}-{next_m:02d}"


def months_between(from_ym, to_ym):
    fy, fm = map(int, from_ym.split('-'))
    ty, tm = map(int, to_ym.split('-'))
    months, y, m = [], fy, fm
    while (y, m) <= (ty, tm):
        months.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return months


def _get_manual_map(db, ids):
    manual_map = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        res = db.table("Kakeibo_Manual_Edits").select("*").in_("transaction_id", chunk).execute()
        for m in res.data:
            manual_map[m['transaction_id']] = m
    return manual_map


def _match_category_rule(content, rules):
    """Kakeibo_Category_Rules の自動マッチ"""
    for rule in rules:
        pattern = rule.get('content_pattern', '')
        if pattern and pattern in content:
            return {
                'cat_major':     rule.get('category_major', ''),
                'cat_mid':       rule.get('category_mid', ''),
                'cat_small':     rule.get('category_small', ''),
                'cat_person':    rule.get('category_person', ''),
                'cat_belonging': rule.get('category_belonging', ''),
            }
    return None


def _match_card_loan_rule(content, institution, amount, rules):
    """Kakeibo_Card_Loan_Rules の自動マッチ（kakeibo app.py と同ロジック）"""
    content     = content or ""
    institution = institution or ""
    for rule in rules:
        if not rule.get('is_active'):
            continue
        cp   = rule.get('content_pattern', '')
        ip   = rule.get('institution_pattern', '')
        sign = rule.get('amount_sign', 'any')
        if cp and cp not in content:
            continue
        if ip and ip not in institution:
            continue
        if sign == '+' and amount <= 0:
            continue
        if sign == '-' and amount >= 0:
            continue
        return rule
    return None


def _match_cash_category_rule(content, institution, amount, rules):
    """Kakeibo_Cash_Category_Rules の自動マッチ"""
    content     = content or ""
    institution = institution or ""
    for rule in rules:
        if not rule.get('is_active'):
            continue
        cp   = rule.get('content_pattern', '')
        ip   = rule.get('institution_pattern', '')
        sign = rule.get('amount_sign', 'any')
        if cp and cp not in content:
            continue
        if ip and ip not in institution:
            continue
        if sign == '+' and amount <= 0:
            continue
        if sign == '-' and amount >= 0:
            continue
        return rule
    return None


def _get_rules(db):
    """全ルールを一括取得"""
    auto_rules = db.table("Kakeibo_Auto_Exclude_Rules").select("*").eq("is_active", True).execute().data
    cat_rules  = db.table("Kakeibo_Category_Rules").select("*").eq("is_active", True)\
                   .order("priority", desc=True).order("use_count", desc=True).execute().data
    card_loan_rules  = db.table("Kakeibo_Card_Loan_Rules").select("*").eq("is_active", True).execute().data
    cash_cat_rules   = db.table("Kakeibo_Cash_Category_Rules").select("*").eq("is_active", True).execute().data
    return auto_rules, cat_rules, card_loan_rules, cash_cat_rules


def _get_excluded_majors(db):
    """集計から除外する大分類の一覧を取得"""
    res = db.table("Kakeibo_View_Excluded_Majors").select("cat_major").execute()
    return {row['cat_major'] for row in (res.data or [])}


def _check_auto_target(content, institution, auto_rules):
    for rule in auto_rules:
        cp = rule.get('content_pattern') or ""
        ip = rule.get('institution_pattern') or ""
        if cp in (content or "") and ip in (institution or ""):
            action = rule.get('note')
            return action if action in ['BOTH', 'CASH_ONLY'] else 'CASH_ONLY'
    return None


# ──────────────────────────────────────────────
# データ取得：明細一覧ベース（支出分析用）
# ──────────────────────────────────────────────

def get_list_transactions(db, start_date, end_date, auto_rules, cat_rules, card_loan_rules):
    """
    明細一覧に表示されるトランザクションを返す。
    kakeibo/app.py の index() と同じ除外ロジックを適用。
    """
    res = db.table("Rawdata_BANK_transactions") \
        .select("*") \
        .gte("date", start_date) \
        .lt("date", end_date) \
        .order("date", desc=True) \
        .execute()
    transactions = res.data

    ids = [t['id'] for t in transactions]
    manual_map = _get_manual_map(db, ids) if ids else {}

    result = []
    for t in transactions:
        m           = manual_map.get(t['id'], {})
        content     = t.get('content', '')
        institution = t.get('institution', '')
        amount      = t['amount']
        view_target = m.get('view_target')

        # 消し込み済み（is_excluded=True）は除外
        is_excluded = m.get('is_excluded', False)

        if not m:
            auto_action = _check_auto_target(content, institution, auto_rules)
            if auto_action == 'CASH_ONLY':
                is_excluded = True
        else:
            if view_target == 'CASH_ONLY':
                is_excluded = True

        if is_excluded:
            continue

        # ローン管理行は除外
        if view_target == 'loan':
            continue

        # カードローンルールにマッチする取引は明細一覧から除外
        matched_card_rule = _match_card_loan_rule(content, institution, amount, card_loan_rules)
        if matched_card_rule and view_target != 'list':
            continue

        # カテゴリ解決：Manual_Edits → Category_Rules 自動マッチ
        cat_major  = m.get('category_major') or ''
        cat_mid    = m.get('category_mid') or ''
        cat_small  = m.get('category_small') or ''
        cat_person = m.get('category_person') or ''

        if not cat_major:
            suggested = _match_category_rule(content, cat_rules)
            if suggested:
                cat_major  = suggested['cat_major']
                cat_mid    = suggested['cat_mid']
                cat_small  = suggested['cat_small']
                cat_person = suggested['cat_person']

        result.append({
            **t,
            'category':   cat_major or '未分類',
            'cat_mid':    cat_mid,
            'cat_small':  cat_small,
            'cat_person': cat_person or '未設定',
        })

    return result


# ──────────────────────────────────────────────
# データ取得：現金計算ベース（入出金サマリー用）
# ──────────────────────────────────────────────

def get_cash_transactions(db, start_date, end_date, auto_rules, card_loan_rules, cash_cat_rules):
    """
    現金計算対象のトランザクションを返す。
    kakeibo/app.py の cash_calc() と同じロジックを適用。
    分類は cash_cat_major / cash_cat_mid を使用。
    """
    res = db.table("Rawdata_BANK_transactions") \
        .select("*") \
        .gte("date", start_date) \
        .lt("date", end_date) \
        .order("date", desc=True) \
        .execute()
    transactions = res.data

    ids = [t['id'] for t in transactions]
    manual_map = _get_manual_map(db, ids) if ids else {}

    result = []
    for t in transactions:
        m           = manual_map.get(t['id'], {})
        content     = t.get('content', '')
        institution = t.get('institution', '')
        amount      = t['amount']

        # 消し込み済みは現金計算対象外
        if m.get('is_excluded'):
            continue

        # カードローンルール（最優先）
        card_rule = _match_card_loan_rule(content, institution, amount, card_loan_rules)
        if card_rule:
            if card_rule.get('exclude'):
                continue  # 計算対象外
            cash_cat_major = m.get('cash_cat_major') or card_rule.get('cash_cat_major') or ''
            cash_cat_mid   = m.get('cash_cat_mid') or ''
            result.append({
                **t,
                'category':      cash_cat_major or '未分類',
                'cash_cat_major': cash_cat_major,
                'cash_cat_mid':   cash_cat_mid,
                'is_loan_tx':    True,
            })
            continue

        # 通常の表示先判定
        is_cash_target = False
        view_target    = m.get('view_target')

        auto_action = _check_auto_target(content, institution, auto_rules)
        if auto_action in ['CASH_ONLY', 'BOTH']:
            is_cash_target = True

        if m:
            if view_target in ['BANK_OUTFLOW', 'BOTH', 'CASH_ONLY']:
                is_cash_target = True
            elif view_target in ['INTERNAL_TRANSFER', 'LIST_ONLY']:
                is_cash_target = False

        if not is_cash_target:
            continue

        # 現金分類: cash_cat_major → Kakeibo_Cash_Category_Rules → 未分類
        cash_cat_major = m.get('cash_cat_major') or ''
        cash_cat_mid   = m.get('cash_cat_mid') or ''
        if not cash_cat_major:
            cr = _match_cash_category_rule(content, institution, amount, cash_cat_rules)
            if cr:
                cash_cat_major = cr.get('cash_cat_major') or ''
                cash_cat_mid   = cr.get('cash_cat_mid') or ''

        category = cash_cat_major or (
            '銀行出金（消込済）' if view_target == 'BANK_OUTFLOW' else '未分類'
        )

        result.append({
            **t,
            'category':      category,
            'cash_cat_major': cash_cat_major,
            'cash_cat_mid':   cash_cat_mid,
            'is_loan_tx':    False,
        })

    return result


# ──────────────────────────────────────────────
# ルート
# ──────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('expense_summary'))


@app.route('/expense')
def expense_summary():
    """支出サマリー：明細一覧ベース。何に・誰が・いくら使ったか"""
    year_month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    db = get_db()
    auto_rules, cat_rules, card_loan_rules, cash_cat_rules = _get_rules(db)
    excluded_majors = _get_excluded_majors(db)

    start, end = month_range(year_month)
    txs = get_list_transactions(db, start, end, auto_rules, cat_rules, card_loan_rules)
    txs = [t for t in txs if t['category'] not in excluded_majors]

    expenses = [t for t in txs if t['amount'] < 0]

    # 階層集計: {大分類: {中分類: {小分類: [transaction]}}}
    hierarchy = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    major_total = defaultdict(int)
    for t in expenses:
        maj = t['category']
        mid = t['cat_mid'] or '（未設定）'
        sml = t['cat_small'] or ''
        hierarchy[maj][mid][sml].append({'date': t['date'], 'content': t['content'], 'amount': t['amount']})
        major_total[maj] += t['amount']

    cat_sorted = sorted(major_total.items(), key=lambda x: x[1])
    hierarchy_list = []
    for maj, maj_amt in cat_sorted:
        mid_list = []
        for mid, sml_dict in sorted(
            hierarchy[maj].items(),
            key=lambda x: sum(sum(tx['amount'] for tx in txs) for txs in x[1].values())
        ):
            sml_list = []
            direct_txs = []
            for sml, txs in sml_dict.items():
                if sml:
                    sml_amt = sum(tx['amount'] for tx in txs)
                    sml_list.append((sml, sml_amt, sorted(txs, key=lambda x: x['date'])))
                else:
                    direct_txs = sorted(txs, key=lambda x: x['date'])
            sml_list = sorted(sml_list, key=lambda x: x[1])
            mid_total = sum(sum(tx['amount'] for tx in txs) for txs in sml_dict.values())
            mid_list.append((mid, mid_total, sml_list, direct_txs))
        hierarchy_list.append((maj, maj_amt, mid_list))

    person_cat   = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    person_total = defaultdict(int)
    for t in expenses:
        p   = t['cat_person']
        maj = t['category']
        mid = t['cat_mid'] or '（未設定）'
        person_cat[p][maj][mid] += t['amount']
        person_total[p] += t['amount']
    person_sorted = sorted(person_total.items(), key=lambda x: x[1])

    person_hierarchy = []
    for p, p_amt in person_sorted:
        cat_list = []
        for maj, mid_dict in sorted(person_cat[p].items(), key=lambda x: sum(x[1].values())):
            mid_list = [(mid, amt) for mid, amt in sorted(mid_dict.items(), key=lambda x: x[1])]
            cat_list.append((maj, sum(mid_dict.values()), mid_list))
        person_hierarchy.append((p, p_amt, cat_list))

    prev_m, next_m = prev_next_month(year_month)

    chart_labels = [c for c, _ in cat_sorted]
    chart_values = [abs(v) for _, v in cat_sorted]
    colors = ['#FF6384','#36A2EB','#FFCE56','#4BC0C0','#9966FF',
              '#FF9F40','#C9CBCF','#EA526F','#7BC8A4','#F67019']

    return render_template('expense.html',
        year_month=year_month,
        prev_month=prev_m,
        next_month=next_m,
        expenses=expenses,
        total_expense=sum(t['amount'] for t in expenses),
        hierarchy_list=hierarchy_list,
        person_sorted=person_sorted,
        person_hierarchy=person_hierarchy,
        chart_labels=chart_labels,
        chart_values=chart_values,
        colors=colors,
    )


@app.route('/cash')
def cash_summary():
    """入出金サマリー：現金計算ベース。実際の現金の動き"""
    year_month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    db = get_db()
    auto_rules, cat_rules, card_loan_rules, cash_cat_rules = _get_rules(db)
    excluded_majors = _get_excluded_majors(db)

    start, end = month_range(year_month)
    txs = get_cash_transactions(db, start, end, auto_rules, card_loan_rules, cash_cat_rules)
    txs = [t for t in txs if t['category'] not in excluded_majors]

    income  = [t for t in txs if t['amount'] > 0]
    expense = [t for t in txs if t['amount'] < 0]

    cat_summary = defaultdict(int)
    for t in expense:
        cat_summary[t['category']] += t['amount']

    prev_m, next_m = prev_next_month(year_month)

    return render_template('cash.html',
        year_month=year_month,
        prev_month=prev_m,
        next_month=next_m,
        income_list=income,
        expense_list=expense,
        total_income=sum(t['amount'] for t in income),
        total_expense=sum(t['amount'] for t in expense),
        net=sum(t['amount'] for t in txs),
        cat_summary=sorted(cat_summary.items(), key=lambda x: x[1]),
    )


@app.route('/multi_month')
def multi_month():
    """複数月サマリー（支出ベース）"""
    today = datetime.now()
    default_from = f"{today.year}-01"
    default_to   = today.strftime('%Y-%m')
    from_ym = request.args.get('from', default_from)
    to_ym   = request.args.get('to',   default_to)

    db = get_db()
    auto_rules, cat_rules, card_loan_rules, cash_cat_rules = _get_rules(db)
    excluded_majors = _get_excluded_majors(db)

    month_list = months_between(from_ym, to_ym)
    ty, tm = map(int, to_ym.split('-'))
    start = f"{from_ym}-01"
    end   = f"{ty + 1}-01-01" if tm == 12 else f"{ty}-{tm + 1:02d}-01"

    txs = get_list_transactions(db, start, end, auto_rules, cat_rules, card_loan_rules)
    txs = [t for t in txs if t['category'] not in excluded_majors]

    # data[cat][ym] = amount
    data        = defaultdict(lambda: defaultdict(int))
    income_by_m = defaultdict(int)
    all_cats    = set()

    for t in txs:
        ym = t['date'][:7]
        if t['amount'] < 0:
            data[t['category']][ym] += t['amount']
            all_cats.add(t['category'])
        else:
            income_by_m[ym] += t['amount']

    # カテゴリを合計金額順（支出多い順）にソート
    cats = sorted(all_cats, key=lambda c: sum(data[c].values()))

    # カテゴリ別合計
    cat_totals = {c: sum(data[c].values()) for c in cats}

    # 月別支出合計・収支
    month_expense_total = {ym: sum(data[c].get(ym, 0) for c in cats) for ym in month_list}

    colors = ['#FF6384','#36A2EB','#FFCE56','#4BC0C0','#9966FF',
              '#FF9F40','#C9CBCF','#EA526F','#7BC8A4','#F67019']

    chart_datasets = [
        {
            'label': cat,
            'data': [abs(data[cat].get(m, 0)) for m in month_list],
            'backgroundColor': colors[i % len(colors)],
        }
        for i, cat in enumerate(cats)
    ]

    return render_template('multi_month.html',
        from_ym=from_ym,
        to_ym=to_ym,
        month_list=month_list,
        cats=cats,
        data=data,
        cat_totals=cat_totals,
        income_by_month=income_by_m,
        month_expense_total=month_expense_total,
        chart_labels=month_list,
        chart_datasets=chart_datasets,
    )


@app.route('/trend')
def trend():
    """カテゴリ推移（支出ベース）。大分類/中分類/小分類を選択可能"""
    today = datetime.now()
    from_ym    = request.args.get('from', f"{today.year}-01")
    to_ym      = request.args.get('to',   today.strftime('%Y-%m'))
    cat_level  = request.args.get('cat_level', 'major')   # major / mid / small
    selected_cats = request.args.getlist('cats')

    db = get_db()
    auto_rules, cat_rules, card_loan_rules, cash_cat_rules = _get_rules(db)
    excluded_majors = _get_excluded_majors(db)

    ty, tm = map(int, to_ym.split('-'))
    start = f"{from_ym}-01"
    end   = f"{ty + 1}-01-01" if tm == 12 else f"{ty}-{tm + 1:02d}-01"

    txs = get_list_transactions(db, start, end, auto_rules, cat_rules, card_loan_rules)
    txs = [t for t in txs if t['category'] not in excluded_majors]
    month_list = months_between(from_ym, to_ym)

    def _cat_key(t):
        if cat_level == 'mid':
            return t['cat_mid'] or '（未設定）'
        elif cat_level == 'small':
            return t['cat_small'] or '（未設定）'
        return t['category']

    all_cats = sorted({_cat_key(t) for t in txs if t['amount'] < 0})
    data = defaultdict(lambda: defaultdict(int))
    for t in txs:
        if t['amount'] < 0:
            data[t['date'][:7]][_cat_key(t)] += t['amount']

    show_cats = selected_cats if selected_cats else all_cats[:6]
    colors = ['#FF6384','#36A2EB','#FFCE56','#4BC0C0','#9966FF','#FF9F40',
              '#C9CBCF','#EA526F','#7BC8A4','#F67019']

    chart_datasets = [
        {
            'label': cat,
            'data': [abs(data[m].get(cat, 0)) for m in month_list],
            'borderColor': colors[i % len(colors)],
            'backgroundColor': 'transparent',
            'tension': 0.3,
        }
        for i, cat in enumerate(show_cats)
    ]

    # 全カテゴリ・全月データを JS に渡す（セット集計・占有率計算用）
    all_data_js = {ym: {cat: abs(data[ym].get(cat, 0)) for cat in all_cats} for ym in month_list}

    # 登録済みセットカテゴリーを取得（同じ cat_level のもの）
    sets_res = db.table("Kakeibo_Trend_Category_Sets").select("*") \
                 .eq("cat_level", cat_level).order("created_at").execute()
    cat_sets = sets_res.data

    return render_template('trend.html',
        from_ym=from_ym,
        to_ym=to_ym,
        cat_level=cat_level,
        all_cats=all_cats,
        selected_cats=show_cats,
        month_list=month_list,
        all_data_js=all_data_js,
        cat_sets=cat_sets,
        chart_labels=month_list,
        chart_datasets=chart_datasets,
    )


@app.route('/api/trend_set/add', methods=['POST'])
def add_trend_set():
    data      = request.json
    set_name  = (data.get('set_name') or '').strip()
    cat_level = data.get('cat_level', 'major')
    categories = data.get('categories', [])
    if not set_name or not categories:
        return jsonify({'status': 'error', 'message': 'set_name と categories は必須'}), 400
    db = get_db()
    res = db.table("Kakeibo_Trend_Category_Sets").insert({
        'set_name':   set_name,
        'cat_level':  cat_level,
        'categories': categories,
    }).execute()
    return jsonify({'status': 'success', 'id': res.data[0]['id'] if res.data else None})


@app.route('/api/trend_set/delete', methods=['POST'])
def delete_trend_set():
    set_id = request.json.get('id')
    if not set_id:
        return jsonify({'status': 'error', 'message': 'id は必須'}), 400
    db = get_db()
    db.table("Kakeibo_Trend_Category_Sets").delete().eq("id", set_id).execute()
    return jsonify({'status': 'success'})


@app.route('/person')
def person():
    """人物別集計（支出ベース）"""
    year_month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    db = get_db()
    auto_rules, cat_rules, card_loan_rules, cash_cat_rules = _get_rules(db)
    excluded_majors = _get_excluded_majors(db)

    start, end = month_range(year_month)
    txs = get_list_transactions(db, start, end, auto_rules, cat_rules, card_loan_rules)
    txs = [t for t in txs if t['category'] not in excluded_majors]

    person_cat   = defaultdict(lambda: defaultdict(int))
    person_total = defaultdict(int)
    all_cats     = set()

    for t in txs:
        if t['amount'] < 0:
            p = t['cat_person']
            person_cat[p][t['category']] += t['amount']
            person_total[p] += t['amount']
            all_cats.add(t['category'])

    persons  = sorted(person_total.keys(), key=lambda p: person_total[p])
    all_cats = sorted(all_cats)
    colors   = ['#FF6384','#36A2EB','#FFCE56','#4BC0C0','#9966FF','#FF9F40']

    chart_datasets = [
        {
            'label': p,
            'data': [abs(person_cat[p].get(cat, 0)) for cat in all_cats],
            'backgroundColor': colors[i % len(colors)],
        }
        for i, p in enumerate(persons)
    ]

    prev_m, next_m = prev_next_month(year_month)

    return render_template('person.html',
        year_month=year_month,
        prev_month=prev_m,
        next_month=next_m,
        persons=persons,
        all_cats=all_cats,
        person_cat=person_cat,
        person_total=person_total,
        chart_labels=all_cats,
        chart_datasets=chart_datasets,
    )



@app.route('/settings')
def settings():
    """大分類除外設定"""
    db = get_db()
    # 既知の大分類を Manual_Edits と Category_Rules から収集
    manual_res = db.table("Kakeibo_Manual_Edits").select("category_major").execute()
    rule_res   = db.table("Kakeibo_Category_Rules").select("category_major").execute()
    all_cats = sorted({
        r['category_major']
        for r in (manual_res.data or []) + (rule_res.data or [])
        if r.get('category_major')
    })
    excluded = _get_excluded_majors(db)
    return render_template('settings.html', all_cats=all_cats, excluded=excluded)


@app.route('/api/settings/excluded_majors', methods=['POST'])
def update_excluded_majors():
    """除外大分類リストを一括更新"""
    data = request.json or {}
    new_excluded = set(data.get('excluded', []))
    db = get_db()
    # 既存行を全削除してから再挿入
    db.table("Kakeibo_View_Excluded_Majors").delete().gte("id", 1).execute()
    if new_excluded:
        db.table("Kakeibo_View_Excluded_Majors").insert(
            [{'cat_major': c} for c in new_excluded]
        ).execute()
    return jsonify({'status': 'success'})


if __name__ == '__main__':
    app.run(debug=True, port=5005)
