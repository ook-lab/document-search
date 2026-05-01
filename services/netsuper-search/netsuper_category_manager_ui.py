"""
ネットスーパーカテゴリー管理UI

Streamlitを使用して、カテゴリーごとの実行スケジュールを管理します。
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
import sys

_repo = Path(__file__).resolve().parents[2]
_netsuper = Path(__file__).resolve().parent
sys.path.insert(0, str(_repo))
sys.path.insert(0, str(_netsuper))

from common.category_manager_db import CategoryManagerDB

# ページ設定
st.set_page_config(
    page_title="ネットスーパーカテゴリー管理",
    page_icon="🛒",
    layout="wide"
)

st.title("🛒 ネットスーパーカテゴリー管理")

# CategoryManagerの初期化（Supabaseベース）
manager = CategoryManagerDB()

# タブで店舗を切り替え
tabs = st.tabs(["楽天西友", "東急ストア", "ダイエー", "設定"])

# 各店舗の共通処理
def show_store_categories(store_name: str, store_display_name: str):
    """店舗のカテゴリー管理画面を表示"""
    st.header(f"{store_display_name} カテゴリー管理")

    categories = manager.get_all_categories(store_name)

    if not categories:
        st.info(f"{store_display_name} のカテゴリーが登録されていません。")
        st.markdown("初期化するには、スクレイピングスクリプトを一度実行してください。")
        return

    # 統計情報
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("総カテゴリー数", len(categories))
    with col2:
        enabled_count = sum(1 for cat in categories if cat.get("enabled", True))
        st.metric("有効", enabled_count)
    with col3:
        disabled_count = len(categories) - enabled_count
        st.metric("無効", disabled_count)
    with col4:
        now = datetime.now()
        runnable_count = sum(
            1 for cat in categories
            if cat.get("enabled", True) and manager.should_run_category(store_name, cat["category_name"], now)
        )
        st.metric("実行可能", runnable_count)

    st.divider()

    # カテゴリー一覧を表形式で表示・編集
    st.subheader("カテゴリー一覧")

    # データフレームに変換
    df_data = []
    for cat in categories:
        df_data.append({
            "名前": cat["category_name"],
            "有効": cat.get("enabled", True),
            "開始日": cat.get("start_date", ""),
            "インターバル（日）": cat.get("interval_days", 7),
            "前回実行日": cat.get("last_run", "未実行"),
            "備考": cat.get("notes", "")
        })

    df = pd.DataFrame(df_data)

    # データエディタで編集
    edited_df = st.data_editor(
        df,
        column_config={
            "名前": st.column_config.TextColumn("カテゴリー名", disabled=True, width="medium"),
            "有効": st.column_config.CheckboxColumn("有効", width="small"),
            "開始日": st.column_config.TextColumn(
                "開始日",
                help="YYYY-MM-DD形式で入力（例: 2025-12-24）",
                width="medium"
            ),
            "インターバル（日）": st.column_config.NumberColumn(
                "インターバル（日）",
                min_value=1,
                max_value=365,
                step=1,
                width="small"
            ),
            "前回実行日": st.column_config.TextColumn("前回実行日", disabled=True, width="medium"),
            "備考": st.column_config.TextColumn("備考", width="large")
        },
        hide_index=True,
        width="stretch",
        key=f"editor_{store_name}"
    )

    # ボタン行
    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        if st.button("💾 変更を保存", type="primary", key=f"save_{store_name}"):
            # 変更内容を反映
            for idx, row in edited_df.iterrows():
                category_name = row["名前"]
                manager.update_category(
                    store_name,
                    category_name,
                    {
                        "enabled": row["有効"],
                        "start_date": row["開始日"],
                        "interval_days": int(row["インターバル（日）"]),
                        "notes": row["備考"]
                    }
                )
            st.success("✅ 変更を保存しました")
            st.rerun()

    with col2:
        if st.button("🔄 最終実行日をリセット", key=f"reset_{store_name}"):
            for cat in categories:
                manager.update_category(store_name, cat["category_name"], {"last_run": None})
            st.success("✅ すべてのカテゴリーの最終実行日をリセットしました")
            st.rerun()

    with col3:
        if st.button("✅ すべて有効化", key=f"enable_all_{store_name}"):
            for cat in categories:
                manager.update_category(store_name, cat["category_name"], {"enabled": True})
            st.success("✅ すべてのカテゴリーを有効化しました")
            st.rerun()

    st.divider()

    # 実行日・インターバルの説明
    with st.expander("ℹ️ 開始日・インターバルの仕組み"):
        st.markdown("""
        ### 実行の仕組み

        1. **開始日**
           - 手動で自由に設定可能（YYYY-MM-DD形式）
           - GitHub Actionsが毎日午前2時に実行
           - 「現在日付 >= 開始日」のカテゴリーが処理される

        2. **実行後の自動更新**
           - 実行後、開始日が自動更新される
           - 計算式: `実行日 + インターバル日数 + 1日`
           - 例: 12/24実行、インターバル7日 → 次回開始日は 1/1

        3. **手動実行（リポジトリルートで、PYTHONPATH を通す）**
           ```bash
           export PYTHONPATH="$PWD:$PWD/services/netsuper-search"
           # 楽天西友 - 特定カテゴリーを手動実行
           MANUAL_CATEGORIES="野菜,果物" python -m rakuten_seiyu.process_with_schedule --manual

           # 東急ストア
           MANUAL_CATEGORIES="野菜,果物" python -m tokyu_store.process_with_schedule --manual

           # ダイエー
           MANUAL_CATEGORIES="野菜・果物" python -m daiei.process_with_schedule --manual

           # スケジュール通り（例: 楽天西友）
           python -m rakuten_seiyu.process_with_schedule
           ```

        ### 例

        - **通常運用**: 開始日 = 2025-12-28、インターバル = 7日
          - 12/28 午前2時のGitHub Actions実行時に処理
          - 開始日が自動的に 2026-01-05 に更新

        - **翌日午前2時に実行したい場合**:
          - 開始日を過去の日付（例: 2025-12-23）に設定
          - 翌日午前2時のGitHub Actions実行時に処理される
        """)

# 各タブに店舗を表示
with tabs[0]:
    show_store_categories("rakuten_seiyu", "楽天西友ネットスーパー")

with tabs[1]:
    show_store_categories("tokyu_store", "東急ストア")

with tabs[2]:
    show_store_categories("daiei", "ダイエーネットスーパー")

# 設定タブ
with tabs[3]:
    st.header("⚙️ 全般設定")

    st.subheader("データベース情報")
    st.text(f"テーブル名: {manager.table_name}")
    st.caption("Supabaseテーブルでスケジュール管理（Streamlit Cloud対応）")

    # 各店舗のカテゴリー数を表示
    col1, col2, col3 = st.columns(3)
    with col1:
        rakuten_cats = manager.get_all_categories("rakuten_seiyu")
        st.metric("楽天西友", f"{len(rakuten_cats)}カテゴリー")
    with col2:
        tokyu_cats = manager.get_all_categories("tokyu_store")
        st.metric("東急ストア", f"{len(tokyu_cats)}カテゴリー")
    with col3:
        daiei_cats = manager.get_all_categories("daiei")
        st.metric("ダイエー", f"{len(daiei_cats)}カテゴリー")

    st.divider()

    st.subheader("一括設定")

    col1, col2, col3 = st.columns(3)

    with col1:
        default_interval = st.number_input(
            "デフォルトインターバル（日）",
            min_value=1,
            max_value=365,
            value=7,
            key="default_interval"
        )

    with col2:
        # 日付入力
        default_date = st.date_input(
            "デフォルト開始日",
            value=datetime.now() + timedelta(days=1),
            key="default_date"
        )

    with col3:
        target_store = st.selectbox(
            "対象店舗",
            ["rakuten_seiyu", "tokyu_store", "daiei"],
            format_func=lambda x: {
                "rakuten_seiyu": "楽天西友",
                "tokyu_store": "東急ストア",
                "daiei": "ダイエー"
            }[x],
            key="target_store"
        )

    if st.button("🔄 選択した店舗のすべてのカテゴリーに一括適用"):
        # 日付を文字列に変換
        start_date_str = default_date.strftime("%Y-%m-%d")

        categories = manager.get_all_categories(target_store)
        for cat in categories:
            manager.update_category(
                target_store,
                cat["category_name"],
                {
                    "start_date": start_date_str,
                    "interval_days": default_interval
                }
            )
        st.success(f"✅ {target_store} のすべてのカテゴリーに設定を適用しました")
        st.rerun()

    st.divider()

    st.subheader("データベースの内容")

    # 全店舗のスケジュールデータを取得して表示
    all_stores = ["rakuten_seiyu", "tokyu_store", "daiei"]
    all_schedules = []

    for store in all_stores:
        categories = manager.get_all_categories(store)
        for cat in categories:
            all_schedules.append({
                "店舗": store,
                "カテゴリー": cat.get("category_name"),
                "有効": cat.get("enabled", True),
                "開始日": cat.get("start_date"),
                "インターバル": cat.get("interval_days", 7),
                "前回実行": cat.get("last_run", "未実行")
            })

    if all_schedules:
        import pandas as pd
        df = pd.DataFrame(all_schedules)
        st.dataframe(df, hide_index=True, use_container_width=True)
    else:
        st.info("スケジュールデータがありません。スクレイピングスクリプトを実行してカテゴリーを初期化してください。")
