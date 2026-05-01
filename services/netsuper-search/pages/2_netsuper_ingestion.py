"""
Daiei / Rakuten Seiyu / Tokyu store ingestion (moved from data-ingestion).
Runs scripts/processing entrypoints; logs go to ingestion_run_log via ingestion_runner.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

_svc = Path(__file__).resolve().parents[1]
_repo = Path(__file__).resolve().parents[3]
for p in (str(_repo), str(_svc)):
    if p not in sys.path:
        sys.path.insert(0, p)

import ingestion_runner as runner

st.set_page_config(page_title="Netsuper ingest", page_icon="📥", layout="wide")

SOURCES = {
    "daiei": {"name": "ダイエー", "script": "scripts/processing/process_daiei.py"},
    "rakuten": {"name": "楽天西友", "script": "scripts/processing/process_rakuten_seiyu.py"},
    "tokyu": {"name": "東急ストア", "script": "scripts/processing/process_tokyu_store.py"},
}

st.title("ネットスーパー店舗データ取込")
st.caption(
    "リポジトリの scripts/processing を実行します。完了までこのページを開いたままにしてください。"
)

defaults = {"daiei": "", "rakuten": "--once", "tokyu": ""}

for key, meta in SOURCES.items():
    with st.expander(meta["name"], expanded=False):
        st.text_input(
            "追加引数（スペース区切り。空なら DB の ingestion_settings）",
            key=f"args_{key}",
            value=defaults.get(key, ""),
            placeholder="例: --no-headless や --once",
        )
        if st.button(f"{meta['name']} を実行", key=f"run_{key}", type="primary"):
            script_path = str(_repo / meta["script"])
            raw = st.session_state.get(f"args_{key}", "").strip()
            if raw:
                extra = raw.split()
            else:
                st_set = runner.get_settings(key)
                ea = st_set.get("extra_args", [])
                if isinstance(ea, str):
                    extra = ea.split()
                elif isinstance(ea, list):
                    extra = list(ea)
                else:
                    extra = []

            run_id = runner.start_run(key, script_path, extra)
            log_area = st.empty()
            lines: list[str] = []
            for chunk in runner.stream_log(run_id):
                if not chunk.startswith("data: "):
                    continue
                try:
                    payload = json.loads(chunk[6:].strip())
                except json.JSONDecodeError:
                    continue
                if payload.get("done"):
                    status = payload.get("status", "unknown")
                    if status == "success":
                        st.success(f"完了: {status}")
                    else:
                        st.error(f"終了: {status}")
                    break
                if "line" in payload:
                    lines.append(str(payload["line"]))
                    log_area.code("\n".join(lines[-300:]), language=None)

st.divider()
st.subheader("直近の実行履歴（ingestion_run_log）")
try:
    hist = runner.get_history(20)
    if hist:
        st.dataframe(hist, use_container_width=True, hide_index=True)
    else:
        st.info("履歴がありません。")
except Exception as e:
    st.warning(f"履歴の取得に失敗: {e}")
