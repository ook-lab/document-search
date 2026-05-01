"""
3ネットスーパー横断商品検索アプリ

楽天西友、東急ストア、ダイエーの商品を横断検索
ベクトル検索で意味的に類似した商品を検索
安い順に表示
"""

import streamlit as st
import os
import sys
from pathlib import Path

_service_dir = Path(__file__).resolve().parent
if str(_service_dir) not in sys.path:
    sys.path.insert(0, str(_service_dir))

from supabase_service import SupabaseService
from openai import OpenAI

# ページ設定
st.set_page_config(
    page_title="ネットスーパー横断検索",
    page_icon="🛒",
    layout="wide"
)

# Supabase接続
try:
    db_client = SupabaseService(use_service_role=False)
    db = db_client.client
except Exception as e:
    st.error(f"データベース接続エラー: {e}")
    st.stop()

# OpenAI接続
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    st.error("環境変数 OPENAI_API_KEY を設定してください")
    st.stop()

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# タイトル
st.title("🛒 ネットスーパー横断検索")
st.markdown(
    "**楽天西友・東急ストア・ダイエー**の商品を一括検索！類似度の高い順に表示します。"
    " 店舗データの取り込みはサイドバー **netsuper ingestion** ページから実行できます。"
)

# 検索欄
st.subheader("🔍 商品を検索")
col1, col2 = st.columns([4, 1])
with col1:
    search_input = st.text_input("商品名", placeholder="例: 牛乳、卵、パン", label_visibility="collapsed")
with col2:
    search_button = st.button("検索", type="primary", use_container_width=True)

# ボタンクリック時のみ、その場の入力値で検索（キャッシュ一切なし）
search_query = None
if search_button and search_input:
    search_query = search_input
    st.query_params["q"] = search_input

def generate_query_embedding(query: str) -> list:
    """検索クエリをベクトル化"""
    response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=query
    )
    return response.data[0].embedding


if search_query:
    # 複数キーワード検索対応
    keywords = search_query.split()

    # ベクトル検索
    try:
        with st.spinner("検索中..."):
            # 各キーワードで個別に検索してスコアを合算
            all_results = {}  # product_id -> {product_data, total_score}

            for keyword in keywords:
                # 各キーワードをベクトル化
                query_embedding = generate_query_embedding(keyword)
                embedding_str = '[' + ','.join(map(str, query_embedding)) + ']'

                # ハイブリッド検索
                result = db.rpc('hybrid_search', {
                    'query_embedding': embedding_str,
                    'query_text': keyword,
                    'match_count': 200
                }).execute()

                # 結果を集計
                for product in result.data:
                    product_id = product['id']
                    score = float(product.get('final_score', 0))

                    if product_id in all_results:
                        # 既存の商品：スコアを加算
                        all_results[product_id]['total_score'] += score
                    else:
                        # 新規の商品：データとスコアを保存
                        product['total_score'] = score
                        all_results[product_id] = product

            # 全キーワードが商品名に含まれる場合、大幅ボーナス
            if len(keywords) > 1:
                for product in all_results.values():
                    product_name_lower = product.get('product_name', '').lower()
                    all_match = all(kw.lower() in product_name_lower for kw in keywords)
                    if all_match:
                        product['total_score'] += 0.5  # 大幅ボーナス

            # 辞書から商品リストに変換
            products = list(all_results.values())

            # 合算スコアでソート（関連度の高い順）
            products.sort(key=lambda x: float(x.get('total_score', 0)), reverse=True)

        # 上位20件を取得
        top_products = products[:20]

        # 上位20件を価格の安い順に並べ替え
        display_products = sorted(
            top_products,
            key=lambda x: float(x.get('current_price_tax_included') or 999999)
        )

        if display_products:
            st.success(f"✅ {len(display_products)}件の商品を表示中（検索結果: {len(products)}件）")

            # 商品一覧表示
            for i, product in enumerate(display_products, 1):
                # コンテナキー（商品IDとインデックスのみ、キャッシュなし）
                product_id = product.get('id', i)
                with st.container(key=f"p_{product_id}_{i}"):
                    col1, col2 = st.columns([1, 4])

                    with col1:
                        # 商品画像
                        if product.get('image_url'):
                            st.image(product['image_url'], width=150)
                        else:
                            st.image("https://via.placeholder.com/150?text=No+Image", width=150)

                    with col2:
                        # 商品リンク（metadataから取得）
                        metadata = product.get('metadata', {})
                        product_url = None
                        if isinstance(metadata, dict):
                            product_url = metadata.get('raw_data', {}).get('url')

                        # 商品名（URLがある場合はリンク化）
                        product_name = product['product_name']
                        if product_url:
                            st.markdown(f"### {i}. [{product_name}]({product_url}) 🔗", unsafe_allow_html=True)
                        else:
                            st.markdown(f"### {i}. {product_name}")

                        # 価格（税込と本体を並記）
                        price_tax_included = product.get('current_price_tax_included', 0)
                        price_base = product.get('current_price', 0)
                        if price_base and price_base != price_tax_included:
                            st.markdown(f"## ¥{price_tax_included:,.0f} <small style='font-size:0.6em; color:#666;'>（本体 ¥{price_base:,.0f}）</small>", unsafe_allow_html=True)
                        else:
                            st.markdown(f"## ¥{price_tax_included:,.0f}")

                        # 店舗名
                        organization = product.get('organization', '不明')
                        if organization == '楽天西友ネットスーパー':
                            st.markdown(f"🏪 **{organization}** 🟢")
                        elif organization == '東急ストア ネットスーパー':
                            st.markdown(f"🏪 **{organization}** 🔵")
                        elif organization == 'ダイエーネットスーパー':
                            st.markdown(f"🏪 **{organization}** 🔴")
                        else:
                            st.markdown(f"🏪 **{organization}**")

                        # 商品ページへのボタン（URLがある場合）
                        if product_url:
                            st.markdown(f"""
                            <a href="{product_url}" target="_blank" style="
                                display: inline-block;
                                padding: 0.5em 1em;
                                background-color: #FF4B4B;
                                color: white;
                                text-decoration: none;
                                border-radius: 5px;
                                font-weight: bold;
                                margin-top: 0.5em;
                            ">🛒 商品ページで購入</a>
                            """, unsafe_allow_html=True)

                        # 検索スコア（複数キーワードの場合は合算スコア）
                        score = product.get('total_score') or product.get('final_score', 0)
                        if score:
                            st.caption(f"スコア: {score:.3f}")

                    st.divider()
        else:
            st.warning(f"「{search_query}」に該当する商品が見つかりませんでした")
            st.info("💡 ヒント: 別のキーワードを試してみてください")

    except Exception as e:
        st.error(f"❌ 検索エラー: {e}")
        st.exception(e)

else:
    # 初期画面
    st.info("👆 上の検索欄に商品名を入力してください")

    # サンプル検索
    st.markdown("### 💡 試してみる")
    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("🥛 牛乳"):
            st.query_params["q"] = "牛乳"
            st.rerun()

    with col2:
        if st.button("🥚 卵"):
            st.query_params["q"] = "卵"
            st.rerun()

    with col3:
        if st.button("🍞 パン"):
            st.query_params["q"] = "パン"
            st.rerun()

# フッター
st.markdown("---")
st.markdown("**対象ストア:** 楽天西友ネットスーパー / 東急ストア ネットスーパー / ダイエーネットスーパー")
