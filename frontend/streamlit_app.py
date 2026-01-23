"""
Streamlit UI for Document Processing
Cloud Run APIを呼び出してドキュメント処理を実行
"""
import os
import streamlit as st
import requests
import time

# Backend API URL（環境変数から取得、デフォルトはCloud RunのURL）
BACKEND_URL = os.getenv(
    "BACKEND_URL",
    "https://doc-processor-983922127476.asia-northeast1.run.app"
)

st.set_page_config(
    page_title="ドキュメント処理システム",
    page_icon="📄",
    layout="wide"
)

st.title("📄 ドキュメント処理システム")
st.markdown("---")

# Initialize session state
if 'processing' not in st.session_state:
    st.session_state.processing = False
if 'last_result' not in st.session_state:
    st.session_state.last_result = None

# Sidebar settings
with st.sidebar:
    st.header("⚙️ 処理設定")

    workspace = st.text_input(
        "ワークスペース",
        value="all",
        help="'all' で全ワークスペース、または特定のワークスペース名を入力"
    )

    limit = st.number_input(
        "処理件数上限",
        min_value=1,
        max_value=1000,
        value=100,
        help="一度に処理する最大件数"
    )

    preserve_workspace = st.checkbox(
        "ワークスペースを保持",
        value=True,
        help="ドキュメントのworkspaceを保持するか"
    )

    st.markdown("---")

    if st.button("🔄 統計情報を更新", use_container_width=True):
        st.rerun()

# Main content
col1, col2 = st.columns([2, 1])

with col1:
    st.header("📊 処理キューの状態")

    try:
        response = requests.get(
            f"{BACKEND_URL}/api/process/stats",
            params={"workspace": workspace},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                stats = data.get('stats', {})

                # Metrics in columns
                metric_cols = st.columns(5)

                with metric_cols[0]:
                    st.metric("⏳ 待機中", stats.get('pending', 0))
                with metric_cols[1]:
                    st.metric("🔄 処理中", stats.get('processing', 0))
                with metric_cols[2]:
                    st.metric("✅ 完了", stats.get('completed', 0))
                with metric_cols[3]:
                    st.metric("❌ 失敗", stats.get('failed', 0))
                with metric_cols[4]:
                    st.metric("📝 未処理", stats.get('null', 0))

                st.markdown("---")

                # Summary
                col_summary1, col_summary2 = st.columns(2)
                with col_summary1:
                    st.metric("📦 合計", stats.get('total', 0))
                with col_summary2:
                    success_rate = stats.get('success_rate', 0)
                    st.metric("✨ 成功率", f"{success_rate:.1f}%")

                pending_count = stats.get('pending', 0)
            else:
                st.error(f"エラー: {data.get('error', '不明なエラー')}")
                pending_count = 0
        else:
            st.error(f"❌ API エラー: {response.status_code}")
            pending_count = 0

    except Exception as e:
        st.error(f"❌ 統計取得エラー: {str(e)}")
        pending_count = 0

with col2:
    st.header("🚀 処理実行")

    if pending_count == 0:
        st.info("処理待ちのドキュメントはありません")
        process_button_disabled = True
    else:
        st.success(f"{pending_count} 件のドキュメントが処理待ちです")
        process_button_disabled = False

    if st.button(
        "▶️ 処理を開始",
        type="primary",
        use_container_width=True,
        disabled=process_button_disabled or st.session_state.processing
    ):
        st.session_state.processing = True
        st.rerun()

# Processing section
if st.session_state.processing:
    st.markdown("---")
    st.header("🔄 処理中...")

    with st.spinner("Cloud Runで処理を実行中..."):
        try:
            response = requests.post(
                f"{BACKEND_URL}/api/process/start",
                json={
                    "workspace": workspace,
                    "limit": limit,
                    "preserve_workspace": preserve_workspace
                },
                timeout=3600  # 1時間のタイムアウト
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    st.session_state.last_result = data
                    st.success("✅ 処理が完了しました！")

                    # Show results
                    col_res1, col_res2, col_res3 = st.columns(3)
                    with col_res1:
                        st.metric("処理数", data.get('processed', 0))
                    with col_res2:
                        st.metric("成功", data.get('success_count', 0))
                    with col_res3:
                        st.metric("失敗", data.get('failed_count', 0))
                else:
                    st.error(f"❌ エラー: {data.get('error', '不明なエラー')}")
            else:
                st.error(f"❌ API エラー: {response.status_code}")

        except requests.exceptions.Timeout:
            st.error("❌ タイムアウトエラー: 処理に時間がかかりすぎています")
        except Exception as e:
            st.error(f"❌ エラー: {str(e)}")

        finally:
            st.session_state.processing = False

        if st.button("🔄 ページを更新"):
            st.rerun()

# Instructions
with st.sidebar:
    st.markdown("---")
    st.markdown("### ℹ️ 使い方")
    st.markdown("""
    1. **ワークスペースを選択**
       - 'all' で全ワークスペース
       - または特定のワークスペース名

    2. **処理件数を設定**
       - 一度に処理する最大件数

    3. **処理を開始**
       - ▶️ ボタンをクリック
       - Cloud RunでOCR処理を実行

    4. **結果を確認**
       - 処理完了後に結果を表示
    """)

    st.markdown("---")
    st.info(f"バックエンド: {BACKEND_URL}")

# Footer
st.markdown("---")
st.markdown("**注意:** 処理には時間がかかる場合があります。ブラウザを閉じずにお待ちください。")
