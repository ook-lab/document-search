"""
ネットスーパー商品分類管理UI

商品のgeneral_nameとsmall_categoryを分類ごとにレビュー・修正します。
"""

import streamlit as st
import pandas as pd
import os
from datetime import datetime, timezone
from supabase import create_client

# ページ設定
st.set_page_config(
    page_title="商品分類管理",
    page_icon="🏷️",
    layout="wide"
)

st.title("🏷️ ネットスーパー商品分類管理")

# Supabase接続（サービスロールキーを使用してRLS制限を回避）
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    st.error("環境変数 SUPABASE_URL と SUPABASE_SERVICE_ROLE_KEY を設定してください")
    st.stop()

db = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# デバッグ: UIが見ているデータベースの商品数を表示
_debug_count = db.table('Rawdata_NETSUPER_items').select('id', count='exact').execute()
st.warning(f"🔍 デバッグ: このUIが見ている全商品数 = {_debug_count.count}件 | URL: {SUPABASE_URL[:30]}...")

# セッション状態の初期化
if 'general_name_index' not in st.session_state:
    st.session_state.general_name_index = 0
if 'large_category' not in st.session_state:
    st.session_state.large_category = None
if 'medium_category' not in st.session_state:
    st.session_state.medium_category = None
if 'small_category' not in st.session_state:
    st.session_state.small_category = None

# 一般名詞リスト取得（キャッシュ）
@st.cache_data(ttl=60)
def get_general_names():
    result = db.table('Rawdata_NETSUPER_items').select(
        'general_name'
    ).not_.is_('general_name', 'null').execute()
    return sorted(list(set([r['general_name'] for r in result.data if r.get('general_name')])))

# カテゴリ階層を構築（キャッシュ）
@st.cache_data(ttl=60)
def build_category_hierarchy():
    """MASTER_Categories_productから階層パスを構築"""
    result = db.table('MASTER_Categories_product').select('id, name, parent_id').execute()

    categories = {cat['id']: cat for cat in result.data}

    def get_path(cat_id):
        """カテゴリIDから階層パスを取得"""
        path = []
        current_id = cat_id
        while current_id:
            cat = categories.get(current_id)
            if cat:
                path.insert(0, cat['name'])
                current_id = cat['parent_id']
            else:
                break
        return ' > '.join(path)

    # 各カテゴリのパスを構築
    paths = {}
    for cat_id, cat in categories.items():
        paths[cat['name']] = get_path(cat_id)

    return paths

# 大分類を取得（商品数付き）
@st.cache_data(ttl=0)
def get_large_categories():
    """大分類を取得（商品1件以上のみ、件数表示）"""
    import pandas as pd

    try:
        # PostgreSQL で直接集計（超高速）
        # RPC関数を使うか、生SQLでGROUP BYする
        # まずカテゴリーマッピングを取得（全件）
        all_categories = []
        offset = 0
        while True:
            result = db.table('MASTER_Categories_product').select('id, large_category').range(offset, offset + 999).execute()
            if not result.data:
                break
            all_categories.extend(result.data)
            offset += 1000
        cat_df = pd.DataFrame(all_categories)

        # 商品のcategory_idごとの件数を取得（全件）
        all_products = []
        offset = 0
        while True:
            result = db.table('Rawdata_NETSUPER_items').select('category_id').not_.is_('category_id', 'null').range(offset, offset + 999).execute()
            if not result.data:
                break
            all_products.extend(result.data)
            offset += 1000
        prod_df = pd.DataFrame(all_products)

        # Pandasで超高速集計
        if len(prod_df) > 0 and len(cat_df) > 0:
            # category_idごとの件数
            count_by_cat = prod_df['category_id'].value_counts().to_dict()

            # large_categoryにマッピング
            cat_map = cat_df.set_index('id')['large_category'].to_dict()

            # large_categoryごとに集計
            large_counts = {}
            for cat_id, count in count_by_cat.items():
                large_cat = cat_map.get(cat_id)
                if large_cat:
                    large_counts[large_cat] = large_counts.get(large_cat, 0) + count

            # 辞書に追加
            cat_with_counts = {f"{large} ({count}件)": large for large, count in large_counts.items() if count > 0}
        else:
            cat_with_counts = {}

    except Exception as e:
        st.error(f"大分類取得エラー: {e}")
        cat_with_counts = {}

    # 未分類を追加
    unclassified_result = db.table('Rawdata_NETSUPER_items').select('id', count='exact').is_('category_id', 'null').execute()
    unclassified_count = unclassified_result.count if unclassified_result.count else 0

    if unclassified_count > 0:
        if any("未分類" in key for key in cat_with_counts.keys()):
            cat_with_counts[f"未分類(カテゴリ未設定) ({unclassified_count}件)"] = "未分類(カテゴリ未設定)"
        else:
            cat_with_counts[f"未分類 ({unclassified_count}件)"] = "未分類"

    return cat_with_counts

# 中分類を取得（商品数付き）
@st.cache_data(ttl=60)
def get_medium_categories(large_category_name):
    """指定した大分類の中分類を取得（商品1件以上のみ、件数表示）"""
    import pandas as pd

    cat_with_counts = {}

    # category_id IS NULL（未分類）の場合
    if large_category_name == "未分類" or large_category_name == "未分類(カテゴリ未設定)":
        unclassified_result = db.table('Rawdata_NETSUPER_items').select('id', count='exact').is_('category_id', 'null').execute()
        unclassified_count = unclassified_result.count if unclassified_result.count else 0
        if unclassified_count > 0:
            cat_with_counts[f"未分類 ({unclassified_count}件)"] = "未分類"
        return cat_with_counts

    try:
        # 1. この大分類のカテゴリーから category_id → medium_category のマッピングを取得
        categories = db.table('MASTER_Categories_product').select('id, medium_category').eq('large_category', large_category_name).execute()
        cat_df = pd.DataFrame(categories.data)

        if len(cat_df) == 0:
            return cat_with_counts

        cat_ids = cat_df['id'].tolist()

        # 2. これらのカテゴリーIDに属する商品を取得（バッチ処理）
        all_products = []
        batch_size = 50
        for i in range(0, len(cat_ids), batch_size):
            batch_ids = cat_ids[i:i+batch_size]
            try:
                products = db.table('Rawdata_NETSUPER_items').select('category_id').in_('category_id', batch_ids).execute()
                all_products.extend(products.data)
            except Exception:
                pass

        if not all_products:
            return cat_with_counts

        # 3. Pandasで超高速集計
        prod_df = pd.DataFrame(all_products)
        count_by_cat = prod_df['category_id'].value_counts().to_dict()

        # medium_categoryにマッピング
        cat_map = cat_df.set_index('id')['medium_category'].to_dict()

        # medium_categoryごとに集計
        medium_counts = {}
        for cat_id, count in count_by_cat.items():
            medium_cat = cat_map.get(cat_id)
            if medium_cat:
                medium_counts[medium_cat] = medium_counts.get(medium_cat, 0) + count

        # 辞書に追加
        cat_with_counts = {f"{medium} ({count}件)": medium for medium, count in medium_counts.items() if count > 0}

    except Exception as e:
        st.error(f"中分類取得エラー: {e}")

    return cat_with_counts

# 小分類を取得（商品数付き）
@st.cache_data(ttl=60)
def get_small_categories_by_medium(large_category_name, medium_category_name):
    """指定した大分類・中分類の小分類を取得（商品1件以上のみ、件数表示）"""
    cat_with_counts = {}

    # category_id IS NULL（未分類）の場合
    if medium_category_name == "未分類":
        unclassified_result = db.table('Rawdata_NETSUPER_items').select('id', count='exact').is_('category_id', 'null').execute()
        unclassified_count = unclassified_result.count if unclassified_result.count else 0
        if unclassified_count > 0:
            cat_with_counts[f"未分類 ({unclassified_count}件)"] = "未分類"
        return cat_with_counts

    # この大分類・中分類に属するDISTINCT small_categoryを取得
    categories = db.table('MASTER_Categories_product').select('small_category, id').eq('large_category', large_category_name).eq('medium_category', medium_category_name).execute()

    for cat in categories.data:
        small_name = cat.get('small_category')
        cat_id = cat.get('id')

        if not small_name:
            continue

        # 商品数をカウント
        count_result = db.table('Rawdata_NETSUPER_items').select('id', count='exact').eq('category_id', cat_id).execute()
        count = count_result.count if count_result.count else 0

        # 商品が1件以上ある場合のみ追加
        if count > 0:
            cat_with_counts[f"{small_name} ({count}件)"] = small_name

    return cat_with_counts

# タブで表示方法を切り替え
tabs = st.tabs(["一般名詞で分類", "小カテゴリで分類", "統計情報"])

# =============================================================================
# タブ1: 一般名詞で分類
# =============================================================================
with tabs[0]:
    st.header("一般名詞（general_name）ごとに商品を確認・修正")

    general_names = get_general_names()

    if not general_names:
        st.info("一般名詞が設定されている商品がありません。")
    else:
        # 検索ボックス
        search_term = st.text_input(
            "🔍 一般名詞を検索",
            placeholder="例: 牛乳、卵、パン...",
            key="general_name_search"
        )

        # 検索フィルタリング
        if search_term:
            filtered_names = [name for name in general_names if search_term.lower() in name.lower()]
        else:
            filtered_names = general_names

        if not filtered_names:
            st.warning(f"「{search_term}」に一致する一般名詞が見つかりません。")
        else:
            # 一般名詞を選択
            selected_general_name = st.selectbox(
                f"一般名詞を選択（{len(filtered_names)}件）",
                filtered_names,
                index=min(st.session_state.general_name_index, len(filtered_names)-1),
                key="general_name_select",
                on_change=lambda: setattr(st.session_state, 'general_name_index', filtered_names.index(st.session_state.general_name_select) if st.session_state.general_name_select in filtered_names else 0)
            )

            if selected_general_name:
                # 選択した一般名詞の商品を取得
                products = db.table('Rawdata_NETSUPER_items').select(
                    'id, product_name, general_name, small_category, organization, current_price_tax_included'
                ).eq('general_name', selected_general_name).limit(100).execute()

                st.subheader(f"一般名詞: {selected_general_name} ({len(products.data)}件)")

                if products.data:
                    # データフレームに変換
                    df_data = []
                    for p in products.data:
                        df_data.append({
                            "ID": p['id'],
                            "商品名": p['product_name'],
                            "一般名詞": p.get('general_name', ''),
                            "小カテゴリ": p.get('small_category', ''),
                            "店舗": p.get('organization', ''),
                            "価格": p.get('current_price_tax_included', 0)
                        })

                    df = pd.DataFrame(df_data)

                    # データエディタで編集
                    edited_df = st.data_editor(
                        df,
                        column_config={
                            "ID": st.column_config.TextColumn("ID", disabled=True, width="small"),
                            "商品名": st.column_config.TextColumn("商品名", disabled=True, width="large"),
                            "一般名詞": st.column_config.TextColumn("一般名詞", width="medium"),
                            "小カテゴリ": st.column_config.TextColumn("小カテゴリ", width="medium"),
                            "店舗": st.column_config.TextColumn("店舗", disabled=True, width="medium"),
                            "価格": st.column_config.NumberColumn("価格", disabled=True, width="small")
                        },
                        hide_index=True,
                        key=f"editor_general_{selected_general_name}"
                    )

                    # 保存ボタン
                    if st.button("💾 変更を保存", type="primary", key="save_general"):
                        try:
                            # 変更を反映
                            current_time = datetime.now(timezone.utc).isoformat()
                            success_count = 0
                            has_verified_column = True

                            for idx, row in edited_df.iterrows():
                                product_id = row["ID"]
                                update_data = {
                                    "general_name": row["一般名詞"],
                                    "small_category": row["小カテゴリ"]
                                }

                                # manually_verified カラムが存在する場合のみ追加
                                if has_verified_column:
                                    update_data["manually_verified"] = True
                                    update_data["last_verified_at"] = current_time

                                try:
                                    db.table('Rawdata_NETSUPER_items').update(update_data).eq('id', product_id).execute()
                                    success_count += 1
                                except Exception as e:
                                    # manually_verified カラムが存在しない場合、フラグなしで再試行
                                    if "manually_verified" in str(e) and has_verified_column:
                                        has_verified_column = False
                                        update_data = {
                                            "general_name": row["一般名詞"],
                                            "small_category": row["小カテゴリ"]
                                        }
                                        db.table('Rawdata_NETSUPER_items').update(update_data).eq('id', product_id).execute()
                                        success_count += 1
                                    else:
                                        raise

                            if has_verified_column:
                                st.success(f"✅ {success_count}件の商品を更新しました（検証済みとしてマーク）")
                            else:
                                st.success(f"✅ {success_count}件の商品を更新しました")
                                st.info("💡 ヒント: マイグレーション実行後、検証済みフラグが自動的に付くようになります")
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ 保存中にエラーが発生しました: {str(e)}")
                            st.exception(e)

# =============================================================================
# タブ2: 小カテゴリで分類（3段階連動プルダウン + 未分類対応）
# =============================================================================
with tabs[1]:
    st.header("小カテゴリ（small_category）ごとに商品を確認・修正")

    # 大分類を取得（{表示名: 実名} の辞書）
    large_categories_dict = get_large_categories()
    large_display_names = list(large_categories_dict.keys())

    col1, col2, col3 = st.columns(3)

    with col1:
        # 大分類プルダウン（未分類は get_large_categories() に含まれる）
        selected_large_display = st.selectbox(
            "🏢 大分類",
            ["選択してください"] + large_display_names,
            key="large_cat_select"
        )

    # 表示名から実名を取得
    if selected_large_display in large_categories_dict:
        selected_large = large_categories_dict[selected_large_display]
    else:
        selected_large = selected_large_display  # "選択してください" or "未分類"

    # 中分類を取得
    medium_categories_dict = {}
    if selected_large and selected_large not in ["選択してください"]:
        medium_categories_dict = get_medium_categories(selected_large)
    medium_display_names = list(medium_categories_dict.keys())

    with col2:
        # 中分類プルダウン
        if selected_large == "選択してください":
            st.selectbox("📂 中分類", ["大分類を選択してください"], disabled=True)
            selected_medium_display = None
            selected_medium = None
        elif medium_display_names:
            selected_medium_display = st.selectbox(
                "📂 中分類",
                ["選択してください"] + medium_display_names,
                key="medium_cat_select"
            )
            # 表示名から実名を取得
            if selected_medium_display in medium_categories_dict:
                selected_medium = medium_categories_dict[selected_medium_display]
            else:
                selected_medium = selected_medium_display
        else:
            st.selectbox("📂 中分類", ["該当なし"], disabled=True)
            selected_medium_display = None
            selected_medium = None

    # 小分類を取得
    small_categories_dict = {}
    if selected_medium and selected_medium not in ["選択してください"]:
        small_categories_dict = get_small_categories_by_medium(selected_large, selected_medium)

    small_display_names = list(small_categories_dict.keys())

    with col3:
        # 小分類プルダウン
        if selected_medium is None or selected_medium == "選択してください":
            st.selectbox("📄 小分類", ["中分類を選択してください"], disabled=True)
            selected_small_display = None
            selected_small = None
        elif small_display_names:
            selected_small_display = st.selectbox(
                "📄 小分類",
                ["選択してください"] + small_display_names,
                key="small_cat_select"
            )
            # 表示名から実名を取得
            if selected_small_display in small_categories_dict:
                selected_small = small_categories_dict[selected_small_display]
            else:
                selected_small = selected_small_display
        else:
            st.selectbox("📄 小分類", ["該当なし"], disabled=True)
            selected_small_display = None
            selected_small = None

    # 商品取得関数（キャッシュ付き）
    @st.cache_data(ttl=300)
    def fetch_products_by_category(large, medium, small):
        """カテゴリに応じた商品を取得（5分間キャッシュ）"""
        # 小分類まで選択されている場合
        if small and small != "選択してください":
            # 未分類（category_id IS NULL）の場合
            if small == "未分類":
                result = db.table('Rawdata_NETSUPER_items').select(
                    'id, product_name, general_name, small_category, category_id, organization, current_price_tax_included'
                ).is_('category_id', 'null').limit(1000).execute()
                return result.data

            cat_result = db.table('MASTER_Categories_product').select('id').eq(
                'large_category', large
            ).eq('medium_category', medium).eq('small_category', small).execute()

            if cat_result.data:
                small_id = cat_result.data[0]['id']
                result = db.table('Rawdata_NETSUPER_items').select(
                    'id, product_name, general_name, small_category, category_id, organization, current_price_tax_included'
                ).eq('category_id', small_id).limit(100).execute()
                return result.data
            return []

        # 大+中分類選択、小分類は未選択
        elif medium and medium not in ["選択してください", None]:
            cat_result = db.table('MASTER_Categories_product').select('id').eq(
                'large_category', large
            ).eq('medium_category', medium).execute()

            all_cat_ids = [cat['id'] for cat in cat_result.data]

            if all_cat_ids:
                # PostgRESTの.in()制限を回避: 50件ずつバッチ処理
                all_products = []
                batch_size = 50
                for i in range(0, len(all_cat_ids), batch_size):
                    batch_ids = all_cat_ids[i:i+batch_size]
                    try:
                        result = db.table('Rawdata_NETSUPER_items').select(
                            'id, product_name, general_name, small_category, category_id, organization, current_price_tax_included'
                        ).in_('category_id', batch_ids).limit(1000).execute()
                        all_products.extend(result.data)
                        if len(all_products) >= 1000:
                            break
                    except Exception as e:
                        # エラーが発生してもスキップして続行
                        pass
                return all_products[:1000]
            return []

        # 大分類のみ選択、中分類は未選択
        elif large and large not in ["選択してください"]:
            cat_result = db.table('MASTER_Categories_product').select('id').eq(
                'large_category', large
            ).execute()

            all_cat_ids = [cat['id'] for cat in cat_result.data]

            if all_cat_ids:
                # PostgRESTの.in()制限を回避: 50件ずつバッチ処理
                all_products = []
                batch_size = 50
                for i in range(0, len(all_cat_ids), batch_size):
                    batch_ids = all_cat_ids[i:i+batch_size]
                    try:
                        result = db.table('Rawdata_NETSUPER_items').select(
                            'id, product_name, general_name, small_category, category_id, organization, current_price_tax_included'
                        ).in_('category_id', batch_ids).limit(1000).execute()
                        all_products.extend(result.data)
                        if len(all_products) >= 1000:
                            break
                    except Exception as e:
                        # エラーが発生してもスキップして続行
                        pass
                return all_products[:1000]
            return []

        return []

    # 商品を取得
    products_data = fetch_products_by_category(selected_large, selected_medium, selected_small)

    # 表示パスを設定
    display_path = ""
    if selected_small and selected_small != "選択してください":
        display_path = f"📂 {selected_large} > {selected_medium} > {selected_small}"
    elif selected_medium and selected_medium not in ["選択してください", None]:
        display_path = f"📂 {selected_large} > {selected_medium} （配下全て）"
    elif selected_large and selected_large not in ["選択してください"]:
        display_path = f"📂 {selected_large} （配下全て）"

    if products_data:
        st.subheader(f"{display_path} ({len(products_data)}件)")

        # category_idからカテゴリ情報を取得するためのキャッシュ
        category_cache = {}

        def get_category_info(category_id):
            """category_idから大中小分類を取得"""
            if not category_id:
                return "未分類", "未分類", "未分類"

            if category_id in category_cache:
                return category_cache[category_id]

            # MASTER_Categories_productから取得
            result = db.table('MASTER_Categories_product').select(
                'large_category, medium_category, small_category'
            ).eq('id', category_id).execute()

            if result.data:
                cat = result.data[0]
                large = cat.get('large_category') or "未分類"
                medium = cat.get('medium_category') or "未分類"
                small = cat.get('small_category') or "未分類"
                category_cache[category_id] = (large, medium, small)
                return large, medium, small

            return "未分類", "未分類", "未分類"

        # データフレームに変換（ID列を削除、大中分類を追加）
        df_data = []
        for p in products_data:
            large, medium, small = get_category_info(p.get('category_id'))

            df_data.append({
                "選択": False,  # チェックボックス
                "_id": p['id'],  # 内部用（非表示）
                "商品名": p['product_name'],
                "一般名詞": p.get('general_name', ''),
                "大分類": large,
                "中分類": medium,
                "小分類": small,
                "店舗": p.get('organization', ''),
                "価格": p.get('current_price_tax_included', 0)
            })

        df = pd.DataFrame(df_data)

        # セッションステートで選択状態を管理
        selection_key = f"selection_{selected_large}_{selected_medium}_{selected_small}"
        if selection_key not in st.session_state:
            st.session_state[selection_key] = {}

        # 全選択・全解除ボタン
        col_select1, col_select2, col_select3 = st.columns([1, 1, 8])

        with col_select1:
            if st.button("☑️ 全選択", key="select_all"):
                for idx in range(len(df)):
                    st.session_state[selection_key][idx] = True
                st.rerun()

        with col_select2:
            if st.button("☐ 全解除", key="deselect_all"):
                st.session_state[selection_key] = {}
                st.rerun()

        # セッションステートの選択状態をデータフレームに反映
        for idx in st.session_state[selection_key]:
            if idx < len(df):
                df.at[idx, "選択"] = st.session_state[selection_key][idx]

        # 一括設定UI
        st.markdown("---")
        st.subheader("📦 選択した商品に一括適用")

        col_bulk1, col_bulk2, col_bulk3, col_bulk4 = st.columns([2, 2, 2, 1])

        with col_bulk1:
            bulk_large = st.text_input("🏢 大分類", key="bulk_large", placeholder="例: 食品類")
        with col_bulk2:
            bulk_medium = st.text_input("📂 中分類", key="bulk_medium", placeholder="例: 調味料")
        with col_bulk3:
            bulk_small = st.text_input("📄 小分類", key="bulk_small", placeholder="例: 味噌")

        st.markdown("---")

        # データエディタで編集
        edited_df = st.data_editor(
            df,
            column_config={
                "選択": st.column_config.CheckboxColumn("選択", default=False, width="small"),
                "_id": None,  # 非表示
                "商品名": st.column_config.TextColumn("商品名", disabled=True, width="large"),
                "一般名詞": st.column_config.TextColumn("一般名詞", width="medium"),
                "大分類": st.column_config.TextColumn("大分類", width="small"),
                "中分類": st.column_config.TextColumn("中分類", width="small"),
                "小分類": st.column_config.TextColumn("小分類", width="medium"),
                "店舗": st.column_config.TextColumn("店舗", disabled=True, width="small"),
                "価格": st.column_config.NumberColumn("価格", disabled=True, width="small")
            },
            hide_index=True,
            key=f"editor_category_{selected_large}_{selected_medium}_{selected_small}"
        )

        # 編集後の選択状態をセッションステートに保存
        for idx, row in edited_df.iterrows():
            if row["選択"]:
                st.session_state[selection_key][idx] = True
            elif idx in st.session_state[selection_key]:
                del st.session_state[selection_key][idx]

        # カテゴリー作成/取得ヘルパー関数
        def get_or_create_category(large_name, medium_name, small_name):
            """カテゴリーを取得、なければ作成（大中小の組み合わせで1つのID）"""
            # トリム
            large_name = str(large_name).strip() if large_name else ""
            medium_name = str(medium_name).strip() if medium_name else ""
            small_name = str(small_name).strip() if small_name else ""

            # 未分類チェック
            if not large_name or not medium_name or not small_name or small_name == "未分類":
                return None

            # 検索・登録用の一つなぎの名前
            category_full_name = f"{large_name}>{medium_name}>{small_name}"

            try:
                # 既存カテゴリーを検索（nameで検索）
                result = db.table('MASTER_Categories_product').select('id').eq('name', category_full_name).execute()

                if result.data:
                    return result.data[0]['id']

                # 新規作成
                new_cat = {
                    'name': category_full_name,
                    'large_category': large_name,
                    'medium_category': medium_name,
                    'small_category': small_name,
                    'parent_id': None
                }
                result = db.table('MASTER_Categories_product').insert(new_cat).execute()

                if not result.data:
                    raise Exception(f"カテゴリ '{category_full_name}' の作成に失敗しました")

                return result.data[0]['id']
            except Exception as e:
                raise Exception(f"カテゴリ '{category_full_name}' の取得/作成中にエラーが発生しました: {str(e)}")

        # 一括適用ボタン
        col_btn1, col_btn2 = st.columns([1, 3])

        with col_btn1:
            if st.button("📦 選択した商品に一括適用", type="primary", key="bulk_apply"):
                # 選択された商品を取得
                selected_rows = edited_df[edited_df["選択"] == True]

                if len(selected_rows) == 0:
                    st.warning("⚠️ 商品が選択されていません")
                elif not bulk_large or not bulk_medium or not bulk_small:
                    st.warning("⚠️ 大分類・中分類・小分類をすべて入力してください")
                else:
                    try:
                        # カテゴリを取得/作成
                        category_id = get_or_create_category(bulk_large, bulk_medium, bulk_small)

                        if not category_id:
                            st.error("❌ カテゴリの作成に失敗しました")
                        else:
                            # 選択された商品を一括更新
                            current_time = datetime.now(timezone.utc).isoformat()
                            success_count = 0

                            for idx, row in selected_rows.iterrows():
                                product_id = row["_id"]

                                update_data = {
                                    "small_category": bulk_small,
                                    "category_id": category_id
                                }

                                try:
                                    db.table('Rawdata_NETSUPER_items').update(update_data).eq('id', product_id).execute()
                                    success_count += 1
                                except Exception as e:
                                    st.error(f"❌ 商品ID {product_id} の更新に失敗: {str(e)}")

                            # キャッシュをクリア
                            st.cache_data.clear()

                            st.success(f"✅ {success_count}件の商品を一括更新しました")
                            st.rerun()

                    except Exception as e:
                        st.error(f"❌ 一括適用中にエラーが発生しました: {str(e)}")
                        st.exception(e)

        with col_btn2:
            st.caption(f"選択中: {len(edited_df[edited_df['選択'] == True])}件")

        # 個別編集保存ボタン
        if st.button("💾 変更を保存", type="primary", key="save_category"):
            try:
                # 変更を反映
                current_time = datetime.now(timezone.utc).isoformat()
                success_count = 0
                has_verified_column = True

                for idx, row in edited_df.iterrows():
                    product_id = row["_id"]

                    # カテゴリー取得/作成
                    large_name = row["大分類"]
                    medium_name = row["中分類"]
                    small_name = row["小分類"]

                    # 大中小の組み合わせで1つのIDを取得/作成
                    category_id = get_or_create_category(large_name, medium_name, small_name)

                    update_data = {
                        "general_name": row["一般名詞"],
                        "small_category": small_name if small_name != "未分類" else None,
                        "category_id": category_id
                    }

                    # manually_verified カラムが存在する場合のみ追加
                    if has_verified_column:
                        update_data["manually_verified"] = True
                        update_data["last_verified_at"] = current_time

                    try:
                        db.table('Rawdata_NETSUPER_items').update(update_data).eq('id', product_id).execute()
                        success_count += 1
                    except Exception as e:
                        # manually_verified カラムが存在しない場合、フラグなしで再試行
                        if "manually_verified" in str(e) and has_verified_column:
                            has_verified_column = False
                            update_data = {
                                "general_name": row["一般名詞"],
                                "small_category": small_name if small_name != "未分類" else None,
                                "category_id": category_id
                            }
                            db.table('Rawdata_NETSUPER_items').update(update_data).eq('id', product_id).execute()
                            success_count += 1
                        else:
                            raise

                # キャッシュをクリア（新しいカテゴリーが追加された場合）
                st.cache_data.clear()

                if has_verified_column:
                    st.success(f"✅ {success_count}件の商品を更新しました（検証済みとしてマーク）")
                else:
                    st.success(f"✅ {success_count}件の商品を更新しました")
                    st.info("💡 ヒント: マイグレーション実行後、検証済みフラグが自動的に付くようになります")
                st.rerun()
            except Exception as e:
                st.error(f"❌ 保存中にエラーが発生しました: {str(e)}")
                st.exception(e)

# =============================================================================
# タブ3: 統計情報
# =============================================================================
with tabs[2]:
    st.header("📊 分類統計情報")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("一般名詞別の商品数")

        # 一般名詞ごとの商品数を集計
        result = db.table('Rawdata_NETSUPER_items').select(
            'general_name'
        ).not_.is_('general_name', 'null').execute()

        general_name_counts = {}
        for r in result.data:
            name = r.get('general_name', '未分類')
            general_name_counts[name] = general_name_counts.get(name, 0) + 1

        # データフレームに変換して表示
        if general_name_counts:
            df_general = pd.DataFrame([
                {"一般名詞": name, "商品数": count}
                for name, count in sorted(general_name_counts.items(), key=lambda x: x[1], reverse=True)
            ])
            st.dataframe(df_general, hide_index=True, height=400)

    with col2:
        st.subheader("小カテゴリ別の商品数")

        # 小カテゴリごとの商品数を集計
        result = db.table('Rawdata_NETSUPER_items').select(
            'small_category'
        ).not_.is_('small_category', 'null').execute()

        category_counts = {}
        for r in result.data:
            cat = r.get('small_category', '未分類')
            category_counts[cat] = category_counts.get(cat, 0) + 1

        # データフレームに変換して表示
        if category_counts:
            df_category = pd.DataFrame([
                {"小カテゴリ": cat, "商品数": count}
                for cat, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True)
            ])
            st.dataframe(df_category, hide_index=True, height=400)

    # 未分類商品の数
    st.divider()

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        total = db.table('Rawdata_NETSUPER_items').select('id', count='exact').execute()
        st.metric("総商品数", total.count)

    with col2:
        no_general = db.table('Rawdata_NETSUPER_items').select('id', count='exact').is_('general_name', 'null').execute()
        st.metric("一般名詞未設定", no_general.count)

    with col3:
        no_category = db.table('Rawdata_NETSUPER_items').select('id', count='exact').is_('small_category', 'null').execute()
        st.metric("小カテゴリ未設定", no_category.count)

    with col4:
        try:
            verified = db.table('Rawdata_NETSUPER_items').select('id', count='exact').eq('manually_verified', True).execute()
            st.metric("手動検証済み", verified.count, delta="AI学習用データ")
        except Exception:
            # manually_verified カラムがまだ存在しない場合
            st.metric("手動検証済み", 0, delta="要マイグレーション", delta_color="off")
