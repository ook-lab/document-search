from flask import Flask, render_template, request, redirect, url_for
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
    """Kakeibo_Category_Rules の自動マッチ（kakeibo app.py と同ロジック）"""
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


def _get_rules(db):
    """自動除外ルール・カテゴリルールを一括取得"""
    auto_rules = db.table("Kakeibo_Auto_Exclude_Rules").select("*").eq("is_active", True).execute().data
    cat_rules  = db.table("Kakeibo_Category_Rules").select("*").eq("is_active", True)\
                   .order("priority", desc=True).order("use_count", desc=True).execute().data
    return auto_rules, cat_rules


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

def get_list_transactions(db, start_date, end_date, auto_rules, cat_rules):
    """
    明細一覧に表示されるトランザクションを返す。
    カテゴリは Manual_Edits → Category_Rules 自動マッチの優先順で解決。
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
        m = manual_map.get(t['id'], {})

        # 除外判定（明細一覧と同じロジック）
        is_excluded = m.get('is_excluded', False)
        view_target = m.get('view_target')

        if not m:
            # 手動設定なし → 自動ルールチェック
            auto_action = _check_auto_target(t.get('content', ''), t.get('institution', ''), auto_rules)
            if auto_action == 'CASH_ONLY':
                is_excluded = True
        else:
            if view_target == 'CASH_ONLY':
                is_excluded = True

        # ローン管理行も除外
        if view_target == 'loan':
            continue

        # カテゴリ解決：Manual_Edits → Category_Rules 自動マッチ
        cat_major  = m.get('category_major') or ''
        cat_mid    = m.get('category_mid') or ''
        cat_small  = m.get('category_small') or ''
        cat_person = m.get('category_person') or ''

        if not cat_major:
            suggested = _match_category_rule(t.get('content', ''), cat_rules)
            if suggested:
                cat_major  = suggested['cat_major']
                cat_mid    = suggested['cat_mid']
                cat_small  = suggested['cat_small']
                cat_person = suggested['cat_person']

        if is_excluded:
            continue

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

def get_cash_transactions(db, start_date, end_date, auto_rules, cat_rules):
    """現金計算対象のトランザクションを返す（kakeibo cash_calc と同ロジック）"""
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
        m = manual_map.get(t['id'], {})

        is_cash_target = False
        auto_action = _check_auto_target(t.get('content', ''), t.get('institution', ''), auto_rules)
        if auto_action in ['CASH_ONLY', 'BOTH']:
            is_cash_target = True

        if m:
            vt = m.get('view_target')
            if vt in ['BANK_OUTFLOW', 'BOTH', 'CASH_ONLY']:
                is_cash_target = True
            elif vt in ['INTERNAL_TRANSFER', 'LIST_ONLY']:
                is_cash_target = False

        if not is_cash_target:
            continue

        vt = m.get('view_target')
        if vt == 'BANK_OUTFLOW':
            category = '銀行出金（消込済）'
        else:
            cat_major = m.get('category_major') or ''
            if not cat_major:
                suggested = _match_category_rule(t.get('content', ''), cat_rules)
                cat_major = suggested['cat_major'] if suggested else ''
            category = cat_major or '未分類'

        result.append({**t, 'category': category})

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
    auto_rules, cat_rules = _get_rules(db)

    start, end = month_range(year_month)
    txs = get_list_transactions(db, start, end, auto_rules, cat_rules)

    expenses = [t for t in txs if t['amount'] < 0]

    # 階層集計: {大分類: {中分類: {小分類: amount}}}
    hierarchy = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    major_total = defaultdict(int)
    for t in expenses:
        maj = t['category']
        mid = t['cat_mid'] or '（未設定）'
        sml = t['cat_small'] or ''
        hierarchy[maj][mid][sml] += t['amount']
        major_total[maj] += t['amount']

    # 大分類を金額順（支出多い順）にソート
    cat_sorted = sorted(major_total.items(), key=lambda x: x[1])
    # テンプレートに渡す構造: [(大分類, 合計, [(中分類, 合計, [(小分類, 合計)])])]
    hierarchy_list = []
    for maj, maj_amt in cat_sorted:
        mid_list = []
        for mid, sml_dict in sorted(hierarchy[maj].items(), key=lambda x: sum(x[1].values())):
            sml_list = [(s, a) for s, a in sorted(sml_dict.items(), key=lambda x: x[1]) if s]
            mid_total = sum(sml_dict.values())
            mid_list.append((mid, mid_total, sml_list))
        hierarchy_list.append((maj, maj_amt, mid_list))

    # 人物別集計: {人物: {大分類: {中分類: amount}}}
    person_cat = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    person_total = defaultdict(int)
    for t in expenses:
        p   = t['cat_person']
        maj = t['category']
        mid = t['cat_mid'] or '（未設定）'
        person_cat[p][maj][mid] += t['amount']
        person_total[p] += t['amount']
    person_sorted = sorted(person_total.items(), key=lambda x: x[1])

    # テンプレート用: [(人物, 合計, [(大分類, 合計, [(中分類, 合計)])])]
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
    auto_rules, cat_rules = _get_rules(db)

    start, end = month_range(year_month)
    txs = get_cash_transactions(db, start, end, auto_rules, cat_rules)

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
    auto_rules, cat_rules = _get_rules(db)

    month_list = months_between(from_ym, to_ym)
    ty, tm = map(int, to_ym.split('-'))
    start = f"{from_ym}-01"
    end   = f"{ty + 1}-01-01" if tm == 12 else f"{ty}-{tm + 1:02d}-01"

    txs = get_list_transactions(db, start, end, auto_rules, cat_rules)

    data        = defaultdict(lambda: defaultdict(int))
    income_by_m = defaultdict(int)
    all_cats    = set()

    for t in txs:
        ym = t['date'][:7]
        if t['amount'] < 0:
            data[ym][t['category']] += t['amount']
            all_cats.add(t['category'])
        else:
            income_by_m[ym] += t['amount']

    cats = sorted(all_cats)
    colors = ['#FF6384','#36A2EB','#FFCE56','#4BC0C0','#9966FF',
              '#FF9F40','#C9CBCF','#EA526F','#7BC8A4','#F67019']

    chart_datasets = [
        {
            'label': cat,
            'data': [abs(data[m].get(cat, 0)) for m in month_list],
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
        income_by_month=income_by_m,
        chart_labels=month_list,
        chart_datasets=chart_datasets,
    )


@app.route('/trend')
def trend():
    """カテゴリ推移（支出ベース）"""
    today = datetime.now()
    from_ym = request.args.get('from', f"{today.year}-01")
    to_ym   = request.args.get('to',   today.strftime('%Y-%m'))
    selected_cats = request.args.getlist('cats')

    db = get_db()
    auto_rules, cat_rules = _get_rules(db)

    ty, tm = map(int, to_ym.split('-'))
    start = f"{from_ym}-01"
    end   = f"{ty + 1}-01-01" if tm == 12 else f"{ty}-{tm + 1:02d}-01"

    txs = get_list_transactions(db, start, end, auto_rules, cat_rules)
    month_list = months_between(from_ym, to_ym)

    all_cats = sorted({t['category'] for t in txs if t['amount'] < 0})
    data = defaultdict(lambda: defaultdict(int))
    for t in txs:
        if t['amount'] < 0:
            data[t['date'][:7]][t['category']] += t['amount']

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

    return render_template('trend.html',
        from_ym=from_ym,
        to_ym=to_ym,
        all_cats=all_cats,
        selected_cats=show_cats,
        month_list=month_list,
        chart_labels=month_list,
        chart_datasets=chart_datasets,
    )


@app.route('/person')
def person():
    """人物別集計（支出ベース）"""
    year_month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    db = get_db()
    auto_rules, cat_rules = _get_rules(db)

    start, end = month_range(year_month)
    txs = get_list_transactions(db, start, end, auto_rules, cat_rules)

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



if __name__ == '__main__':
    app.run(debug=True, port=5005)
