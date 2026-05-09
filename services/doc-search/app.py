"""
Flask Web Application
質問・回答システムのWebインターフェース
"""
import os
import sys
import re
from pathlib import Path

# doc-search 専用パッケージ docsearch のみ（パイプライン dms/ とは無関係・コードも別実装）。
_service_dir = Path(__file__).resolve().parent
if str(_service_dir) not in sys.path:
    sys.path.insert(0, str(_service_dir))

from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# カレンダー主軸（date_range / calendar_primary）の開始・終了から、RPC filter_date 用に広げる暦日数（1暦月は使わずこの値のみ）
SEARCH_CALENDAR_MARGIN_DAYS = 30

# クエリ分類に依存せず検索統合へ載せる（捏造禁止）
RAG_POLICY_SEARCH_UNIVERSAL_JA = (
    "【検索・埋め込み｜絶対遵守】後段で渡す資料に現れる語形・日付・人物・別表記を、1つも捨てるな。"
    "検索が後から全文を読む前提で、取りこぼしゼロに近づく統合文にせよ。少数ヒットで質問を狭めるな。"
    "ヒット件数に心の上限を置くな。何十・何百の手がかりでも拾いに行けるよう語を増やせ。"
    "材料に無い事実は1文字も足すな。"
)

# 回答系プロンプトへ毎回載せる（創作禁止）。先頭の【網羅】はスキャンで見落とされないようにする。
RAG_POLICY_ANSWER_UNIVERSAL_JA = (
    "【網羅｜絶対遵守】このプロンプトで渡す資料の文字は、手元に見えている範囲で先頭から末尾まで読み飛ばすな。"
    "スキップ・粗読み・先に進む省略は禁止。全文を目で追い、質問に関係しうる断片をすべて見つけ出し、拾いつくせ。見落としは許されない。"
    "件数の目安で打ち切るな。10件でも100件でも200件でも、入力に現れる限りどこまでも探し、拾い続けろ。"
    "【入力】に【１】【２】【３】があるときはブロックごとに同様に網羅すること（まとめて片付けるな）。"
    "資料に書いてないことだけを書くな（捏造・推測の禁止）。"
    "省略・切断の示唆があるときは、手元に無い文字は存在不明とし、その部分は断定するな。不確実性に書け。"
)

# クライアントの遅延初期化（Cloud Run起動高速化）
db_client = None
llm_client = None


def _to_halfwidth_digits(s: str) -> str:
    """全角数字を半角に（週・日付の正規表現がマッチするようにする）。"""
    if not s:
        return s
    return str(s).translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def _query_implies_calendar_week(query: str) -> bool:
    q = _to_halfwidth_digits((query or "").strip())
    if not q:
        return False
    if "来週" in q:
        return True
    if "明日" in q and "から" in q and ("一週間" in q or "1週間" in q):
        return True
    if re.search(r"(?:(\d{1,2})月\s*)?(\d{1,2})日(?:を含む)?の?週", q):
        return True
    return bool(re.search(r"\d{1,2}/\d{1,2}(?:を含む)?の?週", q))


def _parse_date_range_bounds(date_range: str) -> Tuple[Optional[date], Optional[date]]:
    """'YYYY-MM-DD..YYYY-MM-DD' を (start, end) に。不正時は (None, None)。"""
    if not date_range or ".." not in date_range:
        return None, None
    a, b = date_range.split("..", 1)
    a, b = a.strip(), b.strip()
    try:
        return date.fromisoformat(a), date.fromisoformat(b)
    except ValueError:
        return None, None


def _format_date_range_bounds(start_d: date, end_d: date) -> str:
    return f"{start_d.isoformat()}..{end_d.isoformat()}"


def _canonical_date_range_literal(date_range: str) -> str:
    """パースに成功したときだけ YYYY-MM-DD..YYYY-MM-DD の1形式。失敗時は空文字。"""
    a, b = _parse_date_range_bounds((date_range or "").strip())
    if a is None or b is None:
        return ""
    return _format_date_range_bounds(a, b)


def _apply_mandatory_search_query_range(refined_query: str, date_range: str) -> str:
    """
    date_range がパース可能なとき、検索用文字列の先頭に date_range と同一のリテラルを1回だけ付与する。
    付与はこの関数のみで行い、LLM の任意生成に依存しない。
    """
    lit = _canonical_date_range_literal(date_range)
    if not lit:
        return (refined_query or "").strip()
    rq = (refined_query or "").strip()
    if rq == lit or rq.startswith(lit + " "):
        return rq
    return f"{lit} {rq}".strip()


def _focal_range_string_for_scoring(refined_date_range: str, wide_lo: date, wide_hi: date) -> str:
    """
    RPC 用の広い窓とは別に、加点の主軸として使う狭いレンジ。
    正規化で日付レンジが付いていればそれを使い、無ければ広い窓の両端を使う（加点の解像度を保つ）。
    """
    t = (refined_date_range or "").strip()
    if t and ".." in t:
        a, b = _parse_date_range_bounds(t)
        if a is not None and b is not None:
            return _format_date_range_bounds(a, b)
    return _format_date_range_bounds(wide_lo, wide_hi)


def _calendar_rpc_date_bounds(refined_date_range: str) -> Tuple[Optional[date], Optional[date]]:
    """Googleカレンダー行の RPC 絞り込みは、正規化で付いた指定範囲のみ（広い窓に広げない）。"""
    t = (refined_date_range or "").strip()
    if not t or ".." not in t:
        return None, None
    a, b = _parse_date_range_bounds(t)
    if a is None or b is None:
        return None, None
    return a, b


def _suppress_calendar_facts_for_integrated_query(user_query: str, date_range_literal: str) -> bool:
    """
    統合質問文へカレンダーの機械ヒットを載せない条件（トークン圧迫対策）。
    - 主軸日付レンジが付いていない（広い検索窓のみ扱う問い）
    - 去年／今年／単年・年度表記など、年単位のスコープとして聞いている問い
    上記のとき True。RPC や検索結果へのカレンダー行合成は別経路のまま。
    """
    dr = (date_range_literal or "").strip()
    if not dr:
        return True
    q = _to_halfwidth_digits((user_query or "").strip())
    for t in ("去年", "昨年", "今年", "本年度"):
        if t in q:
            return True
    if re.search(r"(?:19|20)\d{2}\s*年", q):
        return True
    if re.search(r"(?:19|20)\d{2}\s*年度", q):
        return True
    return False


def _merge_ordered_rag_input(
    part1_question: str, part2_unified_md: str, part3_other_chunks: str, max_chars: int
) -> Tuple[str, Dict[str, Any]]:
    """
    回答系 LLM へ渡す文字列を１→２→３の順で連結し、max_chars を超えるときは末尾から切る（先頭＝質問を優先）。
    max_chars は当面「文字数」の上限とみなす。戻り値のメタは UI 表示用。
    """
    s1 = (part1_question or "").strip()
    s2 = (part2_unified_md or "").strip()
    s3 = (part3_other_chunks or "").strip()
    blk1 = f"【１｜質問】\n{s1}"
    blk2 = f"【２｜類似度順・統合MD】\n{s2 if s2 else '（該当なし）'}"
    blk3 = f"【３｜類似度順・抽出チャンク】\n{s3 if s3 else '（該当なし）'}"
    full = f"{blk1}\n\n{blk2}\n\n{blk3}"
    full_len = len(full)
    if max_chars <= 0 or full_len <= max_chars:
        out = full
        truncated = False
        dropped = 0
    else:
        out = full[:max_chars]
        truncated = True
        dropped = full_len - max_chars
    meta: Dict[str, Any] = {
        "full_length_before_cut": full_len,
        "max_context_chars": max_chars,
        "truncated": truncated,
        "sent_length": len(out),
        "cut_dropped_chars": dropped,
        "cut_position_note": f"先頭から {len(out)} 文字までがモデル入力。{full_len} 文字目以降は切り捨て。"
        if truncated
        else "切り捨てなし（全文がモデル入力）",
    }
    return out, meta


def _fill_intent_spec_ranges(intent_spec: Dict[str, Any], date_range: str, context_days: int = 14) -> None:
    """
    intent_spec に日付レンジを補完する。
    calendar が複数日（週など）のときは document_context_range を主軸の前後 SEARCH_CALENDAR_MARGIN_DAYS 暦日にする。
    calendar が単日で document が空のときは focal の前後 context_days 日。
    """
    if not isinstance(intent_spec, dict):
        return
    cal = (intent_spec.get("calendar_primary_range") or "").strip()
    if not cal and date_range:
        intent_spec["calendar_primary_range"] = date_range.strip()
        cal = intent_spec["calendar_primary_range"]
    doc_ctx = (intent_spec.get("document_context_range") or "").strip()
    if doc_ctx and ".." in doc_ctx:
        return
    start_d, end_d = _parse_date_range_bounds(cal)
    if start_d is None or end_d is None:
        return
    if start_d != end_d:
        lo = start_d - timedelta(days=SEARCH_CALENDAR_MARGIN_DAYS)
        hi = end_d + timedelta(days=SEARCH_CALENDAR_MARGIN_DAYS)
        intent_spec["document_context_range"] = _format_date_range_bounds(lo, hi)
        return
    focal = start_d
    lo = focal - timedelta(days=context_days)
    hi = focal + timedelta(days=context_days)
    intent_spec["document_context_range"] = _format_date_range_bounds(lo, hi)
    fds = intent_spec.get("focal_dates")
    if not isinstance(fds, list) or not fds:
        intent_spec["focal_dates"] = [focal.isoformat()]


def _normalize_intent_spec_dict(raw: Any, query: str, date_range: str) -> Dict[str, Any]:
    """LLM 出力を intent_spec 形に正す。欠損は補う。"""
    lit = _canonical_date_range_literal(date_range)
    if lit:
        date_range = lit
    spec: Dict[str, Any]
    if isinstance(raw, dict):
        spec = dict(raw)
    else:
        spec = {}
    spec.setdefault("version", 1)
    spec.setdefault("task", "unresolved")
    spec.setdefault("resolved_instruction_ja", "")
    spec.setdefault("focal_dates", [])
    spec.setdefault("calendar_primary_range", "")
    spec.setdefault("document_context_range", "")
    if not (spec.get("calendar_primary_range") or "").strip() and date_range:
        spec["calendar_primary_range"] = date_range.strip()
    _fill_intent_spec_ranges(spec, date_range)
    dr_clean = (date_range or "").strip()
    week_instruction_forced = False
    if _query_implies_calendar_week(query) and dr_clean and ".." in dr_clean:
        a, b = _parse_date_range_bounds(dr_clean)
        if a is not None and b is not None and (b - a).days >= 6:
            spec["calendar_primary_range"] = dr_clean
            cur = a
            fds: List[str] = []
            while cur <= b:
                fds.append(cur.isoformat())
                cur += timedelta(days=1)
            spec["focal_dates"] = fds
            _fill_intent_spec_ranges(spec, dr_clean)
            doc_rng = (spec.get("document_context_range") or "").strip()
            if not doc_rng:
                doc_rng = _format_date_range_bounds(
                    a - timedelta(days=SEARCH_CALENDAR_MARGIN_DAYS),
                    b + timedelta(days=SEARCH_CALENDAR_MARGIN_DAYS),
                )
            spec["resolved_instruction_ja"] = (
                f"(1) 質問の意図（原文）: {query}\n"
                f"(2) 暦の区間 {a.isoformat()} から {b.isoformat()} までに該当する予定・イベントを、根拠付きで列挙せよ（この区間外を主答えにしてはならない）。\n"
                f"(3) 関連文書は暦の区間 {doc_rng} と日付が重なるチャンクのみを根拠として用いよ。\n"
                f"(4) 不足は不確実性に書け。"
            )
            week_instruction_forced = True
    if not week_instruction_forced and not (spec.get("resolved_instruction_ja") or "").strip():
        spec["resolved_instruction_ja"] = (
            "ユーザーの発話を手がかりに、文書群から答えを構成せよ。\n"
            f"元の質問: {query}\n"
            + (f"日付の目安（カレンダー主軸）: {spec.get('calendar_primary_range') or date_range}\n")
            + (f"関連文書の日付窓: {spec.get('document_context_range') or '（未設定）'}\n")
        )
    fds = spec.get("focal_dates")
    if isinstance(fds, list):
        spec["focal_dates"] = [str(x).strip() for x in fds if str(x).strip()]
    else:
        spec["focal_dates"] = []
    return spec


def _build_reading_context_block(person_names: Optional[List[str]]) -> str:
    """人物ごとの読み込みコンテキスト（検索文の統合 LLM にのみ渡す）。"""
    from docsearch.user_context import load_person_reading_contexts

    selected = [p.strip() for p in (person_names or []) if isinstance(p, str) and p.strip()]
    context_rows = load_person_reading_contexts(selected)
    blocks: List[str] = []
    for row in context_rows:
        md = (row.get("ai_payload_md") or "").strip()
        if not md:
            continue
        person = row.get("person_name") or ""
        blocks.append(f"[PERSON: {person}]\n{md[:6000]}")
    rc = "\n\n".join(blocks).strip()
    return f"\n【読み込みコンテキスト】\n{rc}\n" if rc else "\n【読み込みコンテキスト】\nなし\n"


def _calendar_premise_block(target_date_range: str, calendar_rows: List[Dict[str, Any]]) -> str:
    """日付レンジと機械ヒットしたカレンダー行から【前提知識】ブロックを組み立てる。"""
    tdr = (target_date_range or "").strip()
    lines = _calendar_premise_lines(calendar_rows)
    if not tdr and not lines:
        return ""
    premise_parts: List[str] = []
    if tdr:
        premise_parts.append(f"対象日付: {tdr}")
    if lines:
        premise_parts.append("機械ヒットしたカレンダー予定:")
        premise_parts.extend(lines)
    premise = "\n".join(premise_parts).strip()
    return f"【前提知識】\n{premise}" if premise else ""


def _query_type_guidance_ja(query_type_info: Dict[str, Any]) -> str:
    """検索統合 LLM 用の網羅方針。分類タイプには依存しない（ログ用の query_type_info は呼び出し側で保持）。"""
    _ = query_type_info
    return RAG_POLICY_SEARCH_UNIVERSAL_JA


def _calendar_facts_plain(date_range_literal: str, calendar_rows: List[Dict[str, Any]]) -> str:
    """統合 LLM に渡す、主軸日付とカレンダー機械ヒットの平文化。"""
    tdr = (date_range_literal or "").strip()
    lines = _calendar_premise_lines(calendar_rows)
    parts: List[str] = []
    if tdr:
        parts.append(f"主軸の日付レンジ（参照・本文先頭にそのまま書かない）: {tdr}")
    if lines:
        parts.append("機械ヒットしたカレンダー予定:")
        parts.extend(lines)
    return "\n".join(parts).strip()


def _assemble_search_query_mechanical(
    *,
    original_query: str,
    reading_context_block: str,
    date_range_literal: str,
    llm_enriched_query: str,
    calendar_rows: List[Dict[str, Any]],
    query_type_info: Dict[str, Any],
    intent_spec: Dict[str, Any],
) -> str:
    """
    統合 LLM が失敗したときのみ使う機械連結（ログが無ければ通常経路では呼ばない想定）。
    """
    parts: List[str] = []
    oq = (original_query or "").strip()
    if oq:
        parts.append(f"【元の質問】\n{oq}")
    rc = (reading_context_block or "").strip()
    if rc:
        parts.append(rc)
    eq = _strip_existing_calendar_premise_block((llm_enriched_query or "").strip())
    if eq:
        parts.append(f"【検索用に補った質問】\n{eq}")
    prem = _calendar_premise_block(date_range_literal, calendar_rows)
    if prem:
        parts.append(prem)
    qh = _query_type_guidance_ja(query_type_info)
    if qh:
        parts.append(f"【網羅方針（全質問共通）】\n{qh}")
    if isinstance(intent_spec, dict):
        ri = (intent_spec.get("resolved_instruction_ja") or "").strip()
        if ri:
            parts.append(f"【下流向け手順（Step0）】\n{ri}")
        cal = (intent_spec.get("calendar_primary_range") or "").strip()
        doc = (intent_spec.get("document_context_range") or "").strip()
        extras: List[str] = []
        if cal:
            extras.append(f"カレンダー参照レンジ: {cal}")
        if doc and doc != cal:
            extras.append(f"関連文書参照レンジ: {doc}")
        if extras:
            parts.append("\n".join(extras))
    body = "\n\n".join(p for p in parts if p).strip()
    return _apply_mandatory_search_query_range(body, date_range_literal)


def _assemble_search_query_with_llm(
    llm_client,
    *,
    original_query: str,
    llm_enriched_query: str,
    date_range_literal: str,
    calendar_rows: List[Dict[str, Any]],
    query_type_info: Dict[str, Any],
    intent_spec: Dict[str, Any],
    person_names: Optional[List[str]],
    log_context: Optional[dict] = None,
) -> str:
    """
    検索・埋め込み用の長文を LLM で1本に統合する。
    読み込みコンテキストはここで初めて読み込む。
    """
    rc_block = _build_reading_context_block(person_names)
    cal_facts = _calendar_facts_plain(date_range_literal, calendar_rows)
    qh = _query_type_guidance_ja(query_type_info)
    ri = ""
    cal_rng = ""
    doc_rng = ""
    if isinstance(intent_spec, dict):
        ri = (intent_spec.get("resolved_instruction_ja") or "").strip()
        cal_rng = (intent_spec.get("calendar_primary_range") or "").strip()
        doc_rng = (intent_spec.get("document_context_range") or "").strip()
    rng_lines: List[str] = []
    if cal_rng:
        rng_lines.append(f"カレンダー主軸のレンジ（参照）: {cal_rng}")
    if doc_rng:
        rng_lines.append(f"関連文書を広げるレンジ（参照）: {doc_rng}")
    rng_block = "\n".join(rng_lines).strip()

    eq = _strip_existing_calendar_premise_block((llm_enriched_query or "").strip())

    prompt = f"""あなたは、検索エンジンとベクトル検索に渡す**統合質問文**を1本だけ書く担当です。
以下の材料に書いてある内容以外は創作しない。検索がヒットしやすい自然な日本語にまとめる。

【厳守】
- 出力は**プレーンテキスト1本分のみ**。見出し・コードフェンス・JSON・前置きや後書きは付けない。
- **YYYY-MM-DD..YYYY-MM-DD** の形式の暦の区間を**出力の先頭に書かない**（システムが別途先頭に1回だけ付ける）。
- 材料に無い予定・提出物・人物関係は書き足さない。

■ ユーザーそのものの発話
{(original_query or '').strip()}

■ 直前の正規化で補った検索向けの文（草稿）
{eq or '（なし）'}

■ 主軸の日付と機械ヒットしたカレンダー（事実として統合に含める）
{cal_facts or '（なし）'}

■ 網羅方針（全質問共通）
{qh}

■ 下流への手順・拘束（検索要約にも溶け込ませる）
{ri or '（なし）'}

■ 参照用レンジ
{rng_block or '（なし）'}

■ 人物・読み込みコンテキスト
{(rc_block or '').strip() or '（なし）'}

統合した検索用テキスト（出力のみ）:"""

    ctx = dict(log_context) if log_context else {}
    ctx.setdefault("app", "doc-search")
    ctx.setdefault("stage", "search-query-assemble")

    response = llm_client.call_model(
        tier="ui_response",
        prompt=prompt,
        model_name="gemini-2.5-flash-lite",
        log_context=ctx,
    )
    text = ""
    if response.get("success"):
        raw_out = (response.get("content") or "").strip()
        if raw_out.startswith("```"):
            lines = raw_out.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            while lines and lines[-1].strip() == "```":
                lines.pop()
            raw_out = "\n".join(lines).strip()
        text = raw_out
    if not text:
        print("[ERROR] 検索文統合 LLM が空を返したため機械連結に切り替え", flush=True)
        return _assemble_search_query_mechanical(
            original_query=original_query,
            reading_context_block=rc_block,
            date_range_literal=date_range_literal,
            llm_enriched_query=llm_enriched_query,
            calendar_rows=calendar_rows,
            query_type_info=query_type_info,
            intent_spec=intent_spec if isinstance(intent_spec, dict) else {},
        )
    return _apply_mandatory_search_query_range(text, date_range_literal)


def _query_with_intent_for_prompt(user_query: str, intent_spec: Optional[Dict[str, Any]]) -> str:
    """回答系モデルに渡す質問欄（元の発話＋正規化意図）。"""
    uq = (user_query or "").strip()
    if not isinstance(intent_spec, dict) or not intent_spec:
        return uq
    ri = (intent_spec.get("resolved_instruction_ja") or "").strip()
    if not ri:
        return uq
    return (
        f"{uq}\n\n"
        f"【正規化された意図・手順（Step0 で固定。ここを最優先で解釈せよ）】\n{ri}"
    )


def _llm_question_with_calendar_premise(query_for_llm: str, refined_query: str) -> str:
    """統合済み検索文（カレンダー・読み込みコンテキスト等を含む）を回答プロンプトに載せる。"""
    q = (query_for_llm or "").strip()
    r = (refined_query or "").strip()
    if not r:
        return q
    return (
        f"{q}\n\n"
        "【検索に使った統合文（機械ヒットの予定・読み込みコンテキスト等を含む。根拠として Evidence に引用してよい）】\n"
        f"{r}"
    )


def _calendar_row_date_str(row: Dict[str, Any]) -> str:
    """カレンダー行の代表日（YYYY-MM-DD）を返す。取れない場合は空文字。"""
    raw = row.get("start_at") or row.get("post_at") or row.get("due_date")
    if raw is None:
        return ""
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    s = str(raw).strip()
    return s[:10] if len(s) >= 10 else ""


def _calendar_premise_lines(rows: List[Dict[str, Any]], max_lines: int = 30) -> List[str]:
    """
    機械ヒットしたカレンダー予定を、質問前提へ注入するための箇条書きへ整形。
    """
    out: List[str] = []
    for row in rows[:max_lines]:
        d = _calendar_row_date_str(row)
        title = str(row.get("title") or "（無題予定）").strip()
        location = str(row.get("location") or "").strip()
        if location:
            out.append(f"- {d} {title} @ {location}")
        else:
            out.append(f"- {d} {title}")
    return out


def _flatten_vector_hit_chunks(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    検索で返った各文書について、細かく区切った単位（チャンク）を1行ずつ並べた一覧を作る。
    各行にはそのチャンクの類似度が付く。並びは類似度が高い順、同じ値なら文書の番号と文書内の順でそろえる。
    """
    flat: List[Dict[str, Any]] = []
    for doc in results:
        doc_id = str(doc.get("id") or "")
        try:
            sim_doc = float(doc.get("similarity")) if doc.get("similarity") is not None else None
        except (TypeError, ValueError):
            sim_doc = None
        chunks = doc.get("index_chunks_all")
        if isinstance(chunks, list) and chunks:
            for ch in chunks:
                cid_raw = ch.get("id")
                cid_s = str(cid_raw) if cid_raw is not None else ""
                cvs = ch.get("chunk_vector_similarity")
                try:
                    row_sim = float(cvs) if cvs is not None else None
                except (TypeError, ValueError):
                    row_sim = None
                flat.append(
                    {
                        "doc_id": doc_id,
                        "chunk_id": cid_s or None,
                        "similarity": row_sim,
                        "chunk_index": ch.get("chunk_index"),
                        "chunk_text": (ch.get("chunk_text") or "").strip(),
                    }
                )
        else:
            cc = (doc.get("chunk_content") or "").strip()
            if not cc and doc.get("title"):
                cc = str(doc.get("title") or "").strip()
            if cc or doc.get("source") == "Googleカレンダー":
                cid_raw = doc.get("chunk_id")
                flat.append(
                    {
                        "doc_id": doc_id,
                        "chunk_id": str(cid_raw) if cid_raw is not None else None,
                        "similarity": sim_doc,
                        "chunk_index": doc.get("chunk_index"),
                        "chunk_text": cc,
                    }
                )

    def _idx_key(r: Dict[str, Any]) -> int:
        v = r.get("chunk_index")
        if v is None:
            return 1_000_000_000
        try:
            return int(v)
        except (TypeError, ValueError):
            return 1_000_000_000

    def _sim_key(r: Dict[str, Any]) -> float:
        s = r.get("similarity")
        if s is None:
            return -1.0
        try:
            return float(s)
        except (TypeError, ValueError):
            return -1.0

    flat.sort(
        key=lambda r: (
            -_sim_key(r),
            str(r.get("doc_id") or ""),
            _idx_key(r),
        )
    )
    return flat


def _strip_existing_calendar_premise_block(text: str) -> str:
    """caller から渡った refined_query に古い【前提知識】ブロックが残っているとき、二重付与を防ぐために削る。"""
    s = (text or "").strip()
    marker = "【前提知識】"
    if not s or marker not in s:
        return s
    return s.split(marker, 1)[0].rstrip()


def _inject_calendar_premise_into_query(
    refined_query: str,
    target_date_range: str,
    calendar_rows: List[Dict[str, Any]],
) -> str:
    """
    正規化質問に「対象日付」と「機械ヒットしたカレンダー予定」を前提知識として付与する。
    検索窓（広い filter_date_*）はここに書かない。
    """
    base = _strip_existing_calendar_premise_block(refined_query)
    block = _calendar_premise_block(target_date_range, calendar_rows)
    if not block:
        return base
    if base:
        return f"{base}\n\n{block}"
    return block


def _calendar_row_to_result_doc(row: Dict[str, Any]) -> Dict[str, Any]:
    """09_unified_documents のカレンダー行を検索結果形式へ変換。"""
    raw_date = row.get("start_at") or row.get("post_at")
    document_date = raw_date[:10] if isinstance(raw_date, str) and len(raw_date) >= 10 else None
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "source": row.get("source"),
        "person": row.get("person"),
        "category": row.get("category"),
        "snippet": row.get("snippet"),
        "post_at": row.get("post_at"),
        "start_at": row.get("start_at"),
        "end_at": row.get("end_at"),
        "due_date": row.get("due_date"),
        "location": row.get("location"),
        "file_url": row.get("file_url"),
        "ui_data": row.get("ui_data"),
        "meta": row.get("meta"),
        "document_date": document_date,
        "ix_search_dates": row.get("ix_search_dates") or [],
        "chunk_content": row.get("snippet") or row.get("title") or "",
        "chunk_id": None,
        "chunk_index": None,
        "chunk_type": "calendar_row",
        "document_body": "",
        "similarity": 1.0,
        "rpc_hybrid_score": 1.0,
        "max_chunk_vector_similarity": None,
        "similarity_basis": "calendar_machine",
        "final_score": 1.0,
        "is_date_matched": True,
    }


def _parse_row_date_field(val: Any) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    s = str(val).strip()
    if len(s) >= 10:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    return None


def _calendar_event_overlaps_range(row: Dict[str, Any], lo: date, hi: date) -> bool:
    """イベントが閉区間 [lo, hi] と暦日で重なるか。ix_search_dates は列が太いことがあり使わない。"""
    sa = row.get("start_at")
    ea = row.get("end_at")
    s_d = _parse_row_date_field(sa)
    e_d = _parse_row_date_field(ea)
    end_missing = ea is None or (isinstance(ea, str) and not ea.strip())
    if s_d is not None and e_d is not None:
        if s_d <= hi and e_d >= lo:
            return True
    if s_d is not None and (end_missing or e_d is None):
        if lo <= s_d <= hi:
            return True
    pa = _parse_row_date_field(row.get("post_at"))
    if pa is not None and lo <= pa <= hi:
        return True
    du = _parse_row_date_field(row.get("due_date"))
    if du is not None and lo <= du <= hi:
        return True
    d0 = _calendar_row_date_str(row)
    if d0:
        try:
            d = date.fromisoformat(d0)
            return lo <= d <= hi
        except ValueError:
            pass
    return False


def _machine_hit_calendar_rows(
    db_client,
    date_range: str,
    persons: Optional[List[str]] = None,
    sources: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """
    カレンダーのみ機械ヒット（非ベクトル）。
    source='Googleカレンダー' かつ対象日付レンジに重なる行を取得する。
    """
    s, e = _parse_date_range_bounds((date_range or "").strip())
    if s is None or e is None:
        return []
    # ソース指定があり、Googleカレンダーが含まれないなら空で返す。
    if isinstance(sources, list) and sources and ("Googleカレンダー" not in sources):
        return []

    q = (
        db_client.client.table("09_unified_documents")
        .select(
            "id, title, source, person, category, snippet, post_at, start_at, end_at, due_date, "
            "location, file_url, ui_data, meta, ix_search_dates"
        )
        .eq("source", "Googleカレンダー")
    )
    if persons:
        q = q.in_("person", persons)
    if categories:
        q = q.in_("category", categories)

    start = s.isoformat()
    end = e.isoformat()
    # 片側だけの lte / gte は「終了日以前の全イベント」等になり誤爆するため禁止。
    # 区間重なり or 単一時刻が閉区間内 or post_at / due_date が閉区間内。
    q = q.or_(
        ",".join(
            [
                f"and(start_at.not.is.null,end_at.not.is.null,start_at.lte.{end},end_at.gte.{start})",
                f"and(start_at.not.is.null,start_at.gte.{start},start_at.lte.{end})",
                f"and(post_at.not.is.null,post_at.gte.{start},post_at.lte.{end})",
                f"and(due_date.not.is.null,due_date.gte.{start},due_date.lte.{end})",
            ]
        )
    )
    resp = q.limit(limit).execute()
    rows = resp.data or []
    rows = [r for r in rows if _calendar_event_overlaps_range(r, s, e)]
    rows.sort(key=lambda r: (_calendar_row_date_str(r), str(r.get("title") or "")))
    return rows


def get_clients():
    """クライアントを初回アクセス時に初期化（遅延読み込み）"""
    global db_client, llm_client

    if db_client is None:
        print("[INFO] クライアントを初期化中...")
        # 遅延import（起動高速化）
        from docsearch.db import DocSearchDB
        from docsearch.llm import DocSearchLLM

        db_client = DocSearchDB(use_service_role=True)
        llm_client = DocSearchLLM()
        print("[INFO] クライアント初期化完了")

    return db_client, llm_client


@app.route('/')
def index():
    """メインページ"""
    return render_template('index.html')


@app.route('/api/filters', methods=['GET'])
def get_filters():
    """
    フィルタオプション取得API（階層構造対応）
    workspace（親）→ doc_type（子）の階層データを返す
    """
    try:
        # クライアント取得（遅延初期化）
        db_client, _ = get_clients()

        # workspace別のdoc_type階層構造を取得
        hierarchy = db_client.get_workspace_hierarchy()

        # 3階層構造をリスト形式に変換（フロントエンド用）
        workspace_list = []
        for person, sources in hierarchy.items():
            workspace_list.append({
                'name': person,
                'sources': [
                    {'name': src, 'categories': cats}
                    for src, cats in sources.items()
                ]
            })

        print(f"[DEBUG] フィルタ取得: {len(workspace_list)} workspaces（階層構造）")

        return jsonify({
            'success': True,
            'hierarchy': workspace_list
        })
    except Exception as e:
        print(f"[ERROR] フィルタ取得エラー: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/search', methods=['POST'])
def search_documents():
    """
    ベクトル検索API（複数フィルタ対応）。
    前処理: 日付・意図の抽出 → カレンダー機械ヒット → 質問タイプ → 検索文の一括統合。
    """
    try:
        # クライアント取得（遅延初期化）
        db_client, llm_client = get_clients()

        data = request.get_json()
        query = data.get('query', '')
        # リランク機能のため、フロントエンドの指定を尊重（最大50件まで）
        requested_limit = data.get('limit', 3)
        limit = min(requested_limit, 50)  # 50件取得→高精度な5件にリランク可能

        persons    = data.get('persons', [])
        sources    = data.get('sources', [])
        categories = data.get('categories', [])

        threshold = float(data.get('threshold', 0.4))  # 足切りスコア閾値

        print(f"[DEBUG] 検索リクエスト: query='{query}', limit={limit}, persons={persons}, sources={sources}, categories={categories}, threshold={threshold}")

        if not str(query).strip():
            return jsonify({'success': False, 'error': 'クエリが空です'}), 400

        # 検索前処理: (1) 日付・意図の抽出 (2) カレンダー機械ヒット (3) 質問タイプ (4) LLM で検索文を統合
        today = datetime.now().strftime('%Y-%m-%d')
        selected_persons = persons if isinstance(persons, list) else []

        refined = _refine_query(
            llm_client,
            query,
            today,
            person_names=selected_persons,
            log_context={'app': 'doc-search', 'stage': 'search-refine'},
        )
        llm_enriched_query = refined.get("query", query)
        llm_enriched_query = llm_enriched_query if isinstance(llm_enriched_query, str) else query
        date_range = refined.get("date_range", "")
        intent_spec = refined.get("intent_spec")
        if not isinstance(intent_spec, dict):
            intent_spec = {}
        lit0 = _canonical_date_range_literal(date_range)
        if lit0:
            date_range = lit0
        intent_spec = _normalize_intent_spec_dict(intent_spec, query, date_range)

        calendar_rows = _machine_hit_calendar_rows(
            db_client=db_client,
            date_range=date_range,
            persons=persons if isinstance(persons, list) else None,
            sources=sources if isinstance(sources, list) else None,
            categories=categories if isinstance(categories, list) else None,
        )

        cal_rows_for_unified_query = (
            [] if _suppress_calendar_facts_for_integrated_query(query, date_range) else calendar_rows
        )
        if len(cal_rows_for_unified_query) < len(calendar_rows or []):
            print(
                "[INFO] 統合質問へのカレンダー機械結果を省略（広い窓／年単位スコープなど）",
                flush=True,
            )

        query_type_info = _detect_query_type(query)
        enum_recall = True
        print(
            f"[DEBUG] クエリタイプ検出: {query_type_info['type']} (focus: {query_type_info['focus']}) "
            f"enumeration_recall={enum_recall}（全質問で広域候補）",
            flush=True,
        )

        refined_query = _assemble_search_query_with_llm(
            llm_client,
            original_query=query,
            llm_enriched_query=llm_enriched_query,
            date_range_literal=date_range,
            calendar_rows=cal_rows_for_unified_query,
            query_type_info=query_type_info,
            intent_spec=intent_spec,
            person_names=selected_persons,
            log_context={'app': 'doc-search', 'stage': 'search-query-assemble'},
        )
        print(f"[INFO] 検索正規化: '{query}' -> '{refined_query}' / date_range={date_range}", flush=True)

        embedding = llm_client.generate_embedding(refined_query.strip())

        # Step3: 暦の窓を決め、RPC 内でその期間に日付が重なる文書のチャンクだけをベクトル評価する（v13）
        lo, hi = _resolve_retrieval_date_window(query, date_range, today)
        window_s = _format_date_range_bounds(lo, hi)
        focal_s = _focal_range_string_for_scoring(date_range, lo, hi)
        cal_lo, cal_hi = _calendar_rpc_date_bounds(date_range)
        vector_sources = sources if sources else None
        if isinstance(vector_sources, list):
            vector_sources = [s for s in vector_sources if s != "Googleカレンダー"]
            vector_sources = vector_sources if vector_sources else ["__NO_VECTOR_SOURCE__"]

        results = db_client.search_documents_sync(
            refined_query,
            embedding,
            limit,
            sources=vector_sources,
            persons=persons if persons else None,
            category=categories if categories else None,
            threshold=threshold,
            date_range=focal_s or None,
            filter_date_start=lo,
            filter_date_end=hi,
            calendar_filter_date_start=cal_lo,
            calendar_filter_date_end=cal_hi,
            enumeration_recall=enum_recall,
        )
        results = results[: int(limit)]
        results = _filter_calendar_results_by_attendance_intent(results, refined_query)
        # Step4: 日付加点は主軸（狭い）レンジに対する傾斜。広い窓は RPC の絞り込み専用。
        results = _apply_date_match_bonus(results, focal_s, refined_query)

        # カレンダーは機械ヒットの結果だけを使う（非ベクトル）。
        machine_calendar_docs = [_calendar_row_to_result_doc(r) for r in calendar_rows]
        existing_ids = {str(d.get("id")) for d in results}
        machine_calendar_docs = [d for d in machine_calendar_docs if str(d.get("id")) not in existing_ids]
        results = machine_calendar_docs + [d for d in results if d.get("source") != "Googleカレンダー"]

        print(f"[DEBUG] 検索結果: {len(results)} 件（sources={sources}）")

        print(f"[DEBUG] 最終検索結果: {len(results)} 件返却")

        vector_hit_chunks = _flatten_vector_hit_chunks(results)

        response_data = {
            'success': True,
            'results': results,
            'count': len(results),
            'query_type': query_type_info,  # クエリタイプ情報を含める
            'refined_query': refined_query,
            'date_range': date_range,
            'intent_spec': intent_spec,
            'vector_hit_chunks': vector_hit_chunks,
        }

        return jsonify(response_data)

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/answer', methods=['POST'])
def generate_answer():
    """
    回答生成API

    single-25-lite: 2.5 Flash-Lite単独（1回呼び出し）
    cascade-25lite-31lite-preview: 2.5 Flash-LiteでEvidence整理 → 3.1 Flash-Lite Previewで回答生成（2回呼び出し）
    single-31-lite-preview: 3.1 Flash-Lite Preview単独（1回呼び出し）

    共通前処理:
      Step0: クエリ改善（Flash-lite固定）
      RAG:   改善クエリでベクトル+全文検索+rerank
    """
    try:
        import uuid as _uuid
        db_client, llm_client = get_clients()

        data = request.get_json()
        query = data.get('query', '')
        documents = data.get('documents') or []
        flow_id = data.get('flow', 'single-25-lite')
        max_context_chars = int(data.get('max_context_chars') or 30000)
        persons    = data.get('persons', [])
        sources    = data.get('sources', [])
        categories = data.get('categories', [])
        client_refined_query = (data.get('refined_query') or '').strip()
        client_date_range = (data.get('date_range') or '').strip()
        client_intent_spec = data.get('intent_spec')
        if not isinstance(client_intent_spec, dict):
            client_intent_spec = {}

        if not query:
            return jsonify({'success': False, 'error': 'クエリが空です'}), 400

        # リクエストID生成（このリクエスト内の全AI呼び出しを紐づける）
        request_id = str(_uuid.uuid4())

        from docsearch.models import ResearchFlow
        flow_config = ResearchFlow.get_flow(flow_id)
        steps = flow_config.get('steps')
        rounds = flow_config.get('rounds', 1)

        today = datetime.now().strftime('%Y-%m-%d')

        # Step0（日付・意図）※クライアントから草稿 refined があれば Step0 出力の代わりに使う → 統合 LLM で質問文を完成
        selected_persons = persons if isinstance(persons, list) else []
        if client_refined_query:
            llm_enriched_query = client_refined_query
            date_range = _normalize_week_range_by_rule(
                query=query,
                today=today,
                date_range=client_date_range,
            )
            intent_spec = client_intent_spec if isinstance(client_intent_spec, dict) else {}
            print(f"[INFO] クエリ改善(クライアント草稿): Step0 スキップ date_range={date_range}", flush=True)
        else:
            refined = _refine_query(
                llm_client,
                query,
                today,
                person_names=selected_persons,
                log_context={'app': 'doc-search', 'stage': 'search-refine', 'session_id': request_id},
            )
            llm_enriched_query = refined.get("query", query)
            llm_enriched_query = llm_enriched_query if isinstance(llm_enriched_query, str) else query
            date_range = refined.get("date_range", "")
            intent_spec = refined.get("intent_spec")
            if not isinstance(intent_spec, dict):
                intent_spec = {}

        lit_ans = _canonical_date_range_literal(date_range)
        if lit_ans:
            date_range = lit_ans
        intent_spec = _normalize_intent_spec_dict(intent_spec, query, date_range)
        calendar_rows_answer = _machine_hit_calendar_rows(
            db_client=db_client,
            date_range=date_range,
            persons=persons if isinstance(persons, list) else None,
            sources=sources if isinstance(sources, list) else None,
            categories=categories if isinstance(categories, list) else None,
        )

        cal_rows_for_unified_answer = (
            [] if _suppress_calendar_facts_for_integrated_query(query, date_range) else calendar_rows_answer
        )
        if len(cal_rows_for_unified_answer) < len(calendar_rows_answer or []):
            print(
                "[INFO] 回答用・統合質問へのカレンダー機械結果を省略（広い窓／年単位スコープなど）",
                flush=True,
            )

        query_type_info = _detect_query_type(query)
        enum_recall = True
        refined_query = _assemble_search_query_with_llm(
            llm_client,
            original_query=query,
            llm_enriched_query=llm_enriched_query,
            date_range_literal=date_range,
            calendar_rows=cal_rows_for_unified_answer,
            query_type_info=query_type_info,
            intent_spec=intent_spec,
            person_names=selected_persons,
            log_context={'app': 'doc-search', 'stage': 'answer-query-assemble', 'session_id': request_id},
        )
        print(f"[INFO] クエリ統合: '{query}' → '{refined_query}' / date_range={date_range}", flush=True)

        query_for_llm = _query_with_intent_for_prompt(query, intent_spec)
        answer_llm_query = _llm_question_with_calendar_premise(query_for_llm, refined_query)
        print(f"[INFO] フィルタ: persons={persons}, sources={sources}, categories={categories}", flush=True)

        # Step2-4: 検索結果が渡されていればそれを優先、なければサーバで実行
        lo: Optional[date] = None
        hi: Optional[date] = None
        focal_s = ""
        if isinstance(documents, list) and documents:
            search_results = list(documents)
            machine_calendar_docs = [_calendar_row_to_result_doc(r) for r in calendar_rows_answer]
            existing_ids = {str(d.get("id")) for d in search_results}
            machine_calendar_docs = [d for d in machine_calendar_docs if str(d.get("id")) not in existing_ids]
            search_results = machine_calendar_docs + [
                d for d in search_results if d.get("source") != "Googleカレンダー"
            ]
            lo, hi = _resolve_retrieval_date_window(query, date_range, today)
            focal_s = _focal_range_string_for_scoring(date_range, lo, hi)
            search_results = _filter_calendar_results_by_attendance_intent(search_results, refined_query)
            search_results = _apply_date_match_bonus(search_results, focal_s, refined_query)
            print(f"[INFO] RAG検索(クライアント提供hits): {len(search_results)}件", flush=True)
        else:
            embedding = llm_client.generate_embedding(refined_query.strip())
            search_limit = max(10, max_context_chars // 2000)
            lo, hi = _resolve_retrieval_date_window(query, date_range, today)
            focal_s = _focal_range_string_for_scoring(date_range, lo, hi)
            cal_lo, cal_hi = _calendar_rpc_date_bounds(date_range)
            vector_sources = sources if sources else None
            if isinstance(vector_sources, list):
                vector_sources = [s for s in vector_sources if s != "Googleカレンダー"]
                vector_sources = vector_sources if vector_sources else ["__NO_VECTOR_SOURCE__"]
            search_results = db_client.search_documents_sync(
                refined_query,
                embedding,
                search_limit,
                sources=vector_sources,
                persons=persons if persons else None,
                category=categories if categories else None,
                threshold=float(data.get("threshold", 0.4)),
                date_range=focal_s or None,
                filter_date_start=lo,
                filter_date_end=hi,
                calendar_filter_date_start=cal_lo,
                calendar_filter_date_end=cal_hi,
                enumeration_recall=enum_recall,
            )
            search_results = search_results[: int(search_limit)]
            search_results = _filter_calendar_results_by_attendance_intent(search_results, refined_query)
            search_results = _apply_date_match_bonus(search_results, focal_s, refined_query)
            machine_calendar_docs = [_calendar_row_to_result_doc(r) for r in calendar_rows_answer]
            existing_ids = {str(d.get("id")) for d in search_results}
            machine_calendar_docs = [d for d in machine_calendar_docs if str(d.get("id")) not in existing_ids]
            search_results = machine_calendar_docs + [
                d for d in search_results if d.get("source") != "Googleカレンダー"
            ]
            print(f"[INFO] RAG検索: {len(search_results)}件", flush=True)

        search_results = _filter_documents_verified_in_09_unified(db_client, search_results)

        # （２）（３）を構築し、（１）（２）（３）をこの順で結合してから入力上限で切断
        part2_unified, part3_chunks = _build_context_sections(
            search_results,
            focal_date_range=date_range if date_range and ".." in date_range else None,
        )
        ordered_rag_blob, rag_input_meta = _merge_ordered_rag_input(
            answer_llm_query,
            part2_unified,
            part3_chunks,
            max_context_chars,
        )
        print(
            f"[INFO] 回答入力(1→2→3): 総{len(ordered_rag_blob)}字 / 上限{max_context_chars} / フロー: {flow_id}",
            flush=True,
        )

        llm_prompt_trace: List[Dict[str, Any]] = []

        # フロー別実行
        if rounds == 1:
            # 1段: 回答生成+Evidence同時
            print(f"[INFO] 1段実行 ({steps[0]})", flush=True)
            answer, p1 = _answer_1step(
                llm_client, steps[0], ordered_rag_blob,
                log_context={'app': 'doc-search', 'stage': 'search-step1', 'session_id': request_id},
            )
            llm_prompt_trace.append(
                {"stage": "回答+Evidence（1段）", "model": steps[0], "prompt": p1, "prompt_chars": len(p1)}
            )

        elif rounds == 2:
            # 2段: Evidence整理 → 回答生成
            step1_limit = int(max_context_chars * 0.33)
            print(f"[INFO] 2段Step1 ({steps[0]}): →{step1_limit}字上限", flush=True)
            evidence_list, p1 = _evidence_1step(
                llm_client, steps[0], ordered_rag_blob, step1_limit,
                log_context={'app': 'doc-search', 'stage': 'search-step1', 'session_id': request_id},
            )
            llm_prompt_trace.append(
                {"stage": "Evidence抽出（2段・Step1）", "model": steps[0], "prompt": p1, "prompt_chars": len(p1)}
            )
            print(f"[INFO] 2段Step2 ({steps[1]}): 内容依存", flush=True)
            answer, p2 = _answer_from_evidence(
                llm_client, steps[1], answer_llm_query, evidence_list,
                log_context={'app': 'doc-search', 'stage': 'search-step2', 'session_id': request_id},
            )
            llm_prompt_trace.append(
                {"stage": "回答生成（2段・Step2）", "model": steps[1], "prompt": p2, "prompt_chars": len(p2)}
            )

        else:
            # 3段: Evidence抽出 → 論点整理 → 最終回答
            step1_limit = int(max_context_chars * 0.4)
            print(f"[INFO] 3段Step1 ({steps[0]}): →{step1_limit}字上限", flush=True)
            step1_output, p1 = _compress_step1(
                llm_client, steps[0], ordered_rag_blob, step1_limit,
                log_context={'app': 'doc-search', 'stage': 'search-step1', 'session_id': request_id},
            )
            llm_prompt_trace.append(
                {"stage": "Evidenceノート（3段・Step1）", "model": steps[0], "prompt": p1, "prompt_chars": len(p1)}
            )

            step2_limit = int(step1_limit * 0.33)
            print(f"[INFO] 3段Step2 ({steps[1]}): →{step2_limit}字上限", flush=True)
            step2_output, p2 = _compress_step2(
                llm_client, steps[1], answer_llm_query, step1_output, step2_limit,
                log_context={'app': 'doc-search', 'stage': 'search-step2', 'session_id': request_id},
            )
            llm_prompt_trace.append(
                {"stage": "論点整理（3段・Step2）", "model": steps[1], "prompt": p2, "prompt_chars": len(p2)}
            )

            print(f"[INFO] 3段Step3 ({steps[2]}): 内容依存", flush=True)
            answer, p3 = _compress_step3(
                llm_client, steps[2], answer_llm_query, step2_output,
                log_context={'app': 'doc-search', 'stage': 'search-step3', 'session_id': request_id},
            )
            llm_prompt_trace.append(
                {"stage": "最終回答（3段・Step3）", "model": steps[2], "prompt": p3, "prompt_chars": len(p3)}
            )

        if not answer:
            return jsonify({'success': False, 'error': '回答生成に失敗しました'}), 500

        return jsonify({
            'success': True,
            'answer': answer,
            'model': steps[-1],
            'provider': 'gemini',
            'flow': flow_id,
            'steps': rounds,
            'refined_query': refined_query,
            'date_range': date_range,
            'intent_spec': intent_spec,
            'ordered_rag_blob': ordered_rag_blob,
            'rag_input_meta': rag_input_meta,
            'llm_prompt_trace': llm_prompt_trace,
        })

    except Exception as e:
        import traceback
        print(f"[ERROR] generate_answer: {e}\n{traceback.format_exc()}", flush=True)
        return jsonify({'success': False, 'error': str(e)}), 500


def _answer_1step(
    llm_client, model_name: str, ordered_rag_blob: str, log_context: dict = None
) -> Tuple[str, str]:
    """
    1段: 回答生成+Evidence抽出を同時実行

    抽象化・根拠なし断定は禁止。各根拠にSourceを付けて出力する。
    戻り値: (モデル応答本文, call_model に渡したプロンプト全文)
    """
    prompt_parts = [f"""あなたはRAG回答エンジンです。

【網羅の前提】下記【ルール】先頭の【網羅｜絶対遵守】と同義（ここでも明示する）。
手元の【入力】に見える【１】【２】【３】の文字は、質問に関係しうる限りすべて拾いつくす。読み飛ばし・粗読み・代表的な1件だけ見て確定する、は禁止。
【入力】が上限で途中までしか無い場合は、それより後は手元に無いものとして扱い、捏造・断定はせず不確実性に書く。手前に見える文字はすべて対象とする。

以下は【１→２→３の順】です。【入力】のみを材料に質問へ回答してください。

【入力】
{ordered_rag_blob}

【ルール】
- {RAG_POLICY_ANSWER_UNIVERSAL_JA}
- Evidenceが存在する内容のみ回答する（新しい主張の創作禁止）
- 【１／質問】の【検索に使った統合文】等に載る機械ヒット予定がある場合のみ、根拠として Evidence にそのまま引用してよい（Source は Googleカレンダー / タイトル）。載っていない場合は無理に使わない
- 根拠なし断定禁止
- Evidenceは原文から1〜2文抜粋し、Sourceを必ず付ける（関連する抜粋は件数を惜しまず列挙する。同一文の繰り返しだけ避ける）
- 不明・不足情報は「不確実性」欄に明示する
- 重要情報（期限・場所・提出方法）は太字で強調する
- 【１】に載っているカレンダー・機械抽出の情報は優先して反映し、それ以外の資料と矛盾する場合は「⚠️ 注記：」で明示する

【出力形式】
回答:
<自然文回答>

Evidence:
- 「原文抜粋」 (タイトル/Source)
- 「原文抜粋」 (タイトル/Source)

不確実性:
<不足情報や条件。なければ「なし」>
"""]

    full_prompt = "\n".join(prompt_parts)
    response = llm_client.call_model(
        tier="ui_response",
        prompt=full_prompt,
        model_name=model_name,
        log_context=log_context,
    )
    if response.get('success'):
        return response.get('content', '').strip(), full_prompt
    return '', full_prompt


def _evidence_1step(
    llm_client, model_name: str, ordered_rag_blob: str, output_limit: int, log_context: dict = None
) -> Tuple[str, str]:
    """
    2段Step1: Evidence整理+Topicタグ付け（抽象化禁止）

    原文から使える情報を抜き出し、Topicラベルを付けて並べる。
    要約・言い換え禁止。Source必須。
    戻り値: (抽出テキスト, call_model に渡したプロンプト全文)
    """
    prompt = f"""あなたはRAGのEvidence抽出器です。

【網羅の前提】下記【ルール】先頭の【網羅｜絶対遵守】と同義。【入力】の【１】【２】【３】に見える文字から、質問に関係しうる断片を漏らさず抽出する。

以下は【１→２→３の順】です。末尾がシステム上限で切れている場合があります。切れた先は手元に無いものとして扱う。

【入力】
{ordered_rag_blob}

【ルール】
- {RAG_POLICY_ANSWER_UNIVERSAL_JA}
- 要約・抽象化・言い換え禁止
- Evidenceは原文から1〜2文の抜粋のみ（質問に関係しうるものは漏らさず複数行でよい）
- Topicタグを付ける（日程/範囲/持ち物/注意事項/例外 など）
- 出典（タイトルまたは、そのチャンクを示す番号）を必ず付ける
- 新しい主張の創作禁止
- 出力上限: {output_limit}字

【出力形式】
Topic: <ラベル>
Evidence: 「<原文抜粋>」
Source: <タイトル または チャンクの番号>
Confidence: <0〜1>

【抽出結果】
"""
    response = llm_client.call_model(
        tier="ui_response",
        prompt=prompt,
        model_name=model_name,
        log_context=log_context,
    )
    if response.get('success'):
        return response.get('content', '').strip(), prompt
    return ordered_rag_blob[:output_limit], prompt


def _answer_from_evidence(
    llm_client, model_name: str, query: str, evidence_list: str, log_context: dict = None
) -> Tuple[str, str]:
    """
    2段Step2: EvidenceリストからユーザーへのRAG回答を生成

    Evidenceなき記述禁止。ここで初めて抽象化OK。
    戻り値: (回答本文, call_model に渡したプロンプト全文)
    """
    prompt_parts = [f"""以下のEvidenceリストを基に、ユーザーの質問に回答してください。

【網羅の前提】下記【ルール】先頭の【網羅｜絶対遵守】が、この Evidence リストに載る抜粋・行にもそのまま適用される。リストに書いてある根拠は拾いつくしてから回答に反映する。

【質問】
{query}

【Evidenceリスト】
{evidence_list}

【ルール】
- {RAG_POLICY_ANSWER_UNIVERSAL_JA}
- Evidenceがある内容のみ回答する（創作禁止）
- 【検索に使った統合文】内の機械ヒットの予定は根拠として回答に含めてよい
- 不確実・不足情報は「不確実性」欄に明示する
- 見出し・箇条書きを活用して読みやすく整形する
- 重要情報（期限・場所・提出方法）は太字で強調する
- 【質問】に含まれるカレンダー・機械抽出の情報は優先して反映し、文書と矛盾する場合は「⚠️ 注記：」で明示する

【出力形式】
回答:
<自然文回答>

根拠:
- <Source>
- <Source>

不確実性:
<なければ「なし」>
"""]

    full_prompt = "\n".join(prompt_parts)
    response = llm_client.call_model(
        tier="ui_response",
        prompt=full_prompt,
        model_name=model_name,
        log_context=log_context,
    )
    if response.get('success'):
        return response.get('content', '').strip(), full_prompt
    return '', full_prompt


def _regenerate_step0_dates_after_failure(
    llm_client,
    original_query: str,
    today: str,
    failed_step0: Dict[str, Any],
    selected_persons: List[str],
    log_context: Optional[dict] = None,
) -> Optional[Dict[str, Any]]:
    """
    Step0 の日付まわりだけが機械検証に通らないとき、元の発話と失敗した JSON を渡して再生成する。
    この関数内のモデル呼び出しは **2 回以上しません**（日付のやり直しはこの 1 回で打ち止め）。
    成功時は query / date_range / intent_spec を返す。失敗時は None。
    """
    import json as _json

    try:
        failed_json = _json.dumps(failed_step0, ensure_ascii=False, indent=2)
    except TypeError:
        failed_json = str(failed_step0)

    persons_line = ", ".join(selected_persons) if selected_persons else "未指定"
    prompt = f"""あなたは Step0 の訂正器です。直前の Step0 が返した JSON のうち、暦の区間の形式が壊れているか、中身と矛盾しています。
元のユーザーの発話と、その失敗 JSON を根拠に、**暦だけ**をやり直してください。

【厳守】
- 出力は JSON オブジェクト 1 個のみ（前後に説明を付けない）。
- query は、失敗 JSON の query を**原則そのまま**返す。暦の修正のためだけに最小限触る場合のみ差し替え可。
- date_range は **空文字 ""** か **"YYYY-MM-DD..YYYY-MM-DD"** のどちらかだけ。それ以外の区切り・口語・片側欠けは禁止。
- intent_spec はオブジェクトで返す。version / task / resolved_instruction_ja は失敗 JSON から流用してよいが、
  focal_dates・calendar_primary_range・document_context_range は **date_range と矛盾しない**ように必ず整合させる。
- 今日の日付 {today} を基準に相対日を絶対化する（Step0 と同じ暦ルール）。

今日の日付: {today}
対象の人: {persons_line}

元の発話:
{original_query}

失敗した Step0 の JSON:
{failed_json}

訂正後の JSON 出力:"""

    ctx = dict(log_context) if log_context else {}
    ctx.setdefault("app", "doc-search")
    ctx.setdefault("stage", "step0-date-retry")

    response = llm_client.call_model(
        tier="ui_response",
        prompt=prompt,
        model_name="gemini-2.5-flash-lite",
        log_context=ctx,
    )
    if not response.get("success"):
        print("[WARN] Step0 date retry: LLM call failed", flush=True)
        return None
    content = (response.get("content") or "").strip()
    content = content.replace("```json", "").replace("```", "").strip()
    try:
        obj = _json.loads(content)
    except Exception as e:
        print(f"[WARN] Step0 date retry: JSON parse failed: {e}", flush=True)
        return None

    q_out = obj.get("query", failed_step0.get("query", original_query))
    q_out = q_out if isinstance(q_out, str) else str(original_query)
    dr_out = obj.get("date_range", "")
    dr_out = dr_out.strip() if isinstance(dr_out, str) else ""
    dr_out = _normalize_week_range_by_rule(
        query=original_query,
        today=today,
        date_range=dr_out,
    )
    lit = _canonical_date_range_literal(dr_out)
    if dr_out and not lit:
        print("[WARN] Step0 date retry: returned date_range still not canonical", flush=True)
        return None
    if dr_out:
        dr_out = lit

    raw_spec = obj.get("intent_spec")
    if isinstance(raw_spec, str):
        try:
            raw_spec = _json.loads(raw_spec)
        except Exception:
            raw_spec = {}
    if not isinstance(raw_spec, dict):
        raw_spec = {}

    intent_spec = _normalize_intent_spec_dict(raw_spec, original_query, dr_out)
    print("[INFO] Step0 date retry: repaired date_range and intent_spec", flush=True)
    return {"query": q_out.strip(), "date_range": dr_out, "intent_spec": intent_spec}


def _refine_query(
    llm_client,
    query: str,
    today: str,
    person_names: Optional[List[str]] = None,
    log_context: dict = None,
) -> Dict[str, Any]:
    """
    Step0: クエリ改善（Flash-lite固定）

    相対日を絶対日付に直し、下流のモデルが迷わないよう intent_spec（手順・拘束の構造体）を付ける。
    読み込みコンテキスト（人物ごとの MD）は Step0 には渡さない（検索文統合 LLM で初めて使用する）。
    query の長さに上限は設けない。検索文の先頭への日付リテラル付与は行わない（後段の統合で行う）。

    Returns:
        query, date_range（互換）, intent_spec（version / task / resolved_instruction_ja / focal_dates /
        calendar_primary_range / document_context_range）
    """
    import json as _json

    selected = [p.strip() for p in (person_names or []) if isinstance(p, str) and p.strip()]

    prompt = f"""あなたは検索・回答パイプラインの Step0 正規化器です。
ユーザーの発話を、AIが迷わず解釈できるようにし、曖昧な情報をブレのない表現に置き換え、発話から推測しうる背景だけを足してください。
組織・個人に固有の長いコンテキストはこの段では渡されない（後段の統合で付く）。

出力は JSON オブジェクト 1 個のみ（前後に説明文を付けない）。

【必須キー】
- query: 検索のための自然語草稿。**短く要約してはならない。** 元の発話の情報を落とさず、趣旨・人物・種別を含め、検索エンジンが文脈を拾いやすい**情報豊かな一文〜数文**にする。date_range が空でないときは query に YYYY-MM-DD 形式の暦や「を含む週」「5/9から一週間」「明日から一週間」等の暦口語を含めない（暦の区間は date_range のキーだけ）。後段で検索用文字列の先頭に同一の暦区間リテラルが機械的に1回付く）
- date_range: 質問の主軸となる暦日レンジ "YYYY-MM-DD..YYYY-MM-DD"。日付が無ければ ""
- intent_spec: 下流モデル向けの固定スキーマ（必ずオブジェクト）
  - version: 1（整数）
  - task: 英語の短いスラッグ（例: schedule_day_with_related_context, general_question）
  - resolved_instruction_ja: 下流が従うべき手順を、番号付きで複数文で書く。次を含めること:
      (1) 何を解決するか（ユーザーの意図の言い換え）
      (2) カレンダー・予定系なら「calendar_primary_range の各日についてイベントを列挙・抽出せよ」等の具体動詞
      (3) 書類・提出・連絡・参加など予定周辺なら「document_context_range と日付が重なるチャンクを抽出せよ」等
      (4) 最終回答の形（列挙・比較・要約など）
  - focal_dates: 主眼の日付の配列（各要素 YYYY-MM-DD）
  - calendar_primary_range: カレンダー検索の主レンジ "YYYY-MM-DD..YYYY-MM-DD"（無ければ ""）
  - document_context_range: 関連文書用。空のときサーバが補う。calendar_primary が複数日（週など）のときは **calendar の開始日から {SEARCH_CALENDAR_MARGIN_DAYS} 暦日前、終了日から {SEARCH_CALENDAR_MARGIN_DAYS} 暦日後**（1暦月は使わない）。単一日のみのときは focal の前後約14日

【日付ルール】（はい／いいえで検算できることのみ書く）
- すべての暦は **YYYY-MM-DD**（JSON の date_range / calendar_primary_range / focal_dates）
- 相対日は今日 {today} を基準に絶対化する
- 「明日から一週間」「明日から1週間」は **開始=明日、終了=明日+7暦日**（例: 今日が 2026-05-08 なら **2026-05-09..2026-05-16**）。「5/9から一週間」のような口語だけを resolved_instruction に残してはならない（サーバが上書きする）
- 「来週」「M月D日の週」「M/Dの週」「D日の週」はいずれも **週＝その日が属する週の日曜からちょうど7日後の日曜まで**（終端は開始+7日）。例: 2026年で「5/11の週」は **2026-05-10..2026-05-17**（5/10が日曜、5/17が次の日曜）
- RPC の広い検索窓（filter_date）は **date_range の開始日から {SEARCH_CALENDAR_MARGIN_DAYS} 暦日前、終了日から {SEARCH_CALENDAR_MARGIN_DAYS} 暦日後**。暦1か月は使わない
- date_range が空でないとき、query に暦の区間を書いてはならない（暦は date_range のみ）

今日の日付: {today}
対象の人: {", ".join(selected) if selected else "未指定"}
元の質問: {query}

参考（構造の例。内容は質問に合わせて変えよ。今日が {today} のとき）:
{{"query":"本日に関係する予定・提出物・連絡・参加依頼・持ち物・場所変更など、学校・保育・習い事の文脈で起こりうる事項を漏れなく検索したい。人物・種別は元の発話に合わせて明示する。","date_range":"{today}..{today}","intent_spec":{{"version":1,"task":"schedule_day_with_related_context","resolved_instruction_ja":"(1) ユーザーは本日の予定を把握したい。(2) カレンダー由来の情報から calendar_primary_range に含まれる日の予定・イベントをすべて抽出する。(3) 提出物・連絡・参加・宿題など予定に関連しうる文書は、document_context_range と日付が重なるものを抽出する。(4) (2)(3)を統合し時系列で列挙して答え、不足は不確実性に書く。","focal_dates":["{today}"],"calendar_primary_range":"{today}..{today}","document_context_range":""}}}}
出力:"""
    response = llm_client.call_model(
        tier="ui_response",
        prompt=prompt,
        model_name="gemini-2.5-flash-lite",
        log_context=log_context,
    )
    if response.get('success'):
        content = response.get('content', '').strip()
        # JSONコードブロックを除去
        content = content.replace('```json', '').replace('```', '').strip()
        try:
            result = _json.loads(content)
            q = result.get("query", query)
            q = q if isinstance(q, str) else query
            dr = result.get("date_range", "")
            dr = dr.strip() if isinstance(dr, str) else ""
            dr = _normalize_week_range_by_rule(
                query=query,
                today=today,
                date_range=dr,
            )
            raw_spec = result.get("intent_spec")
            if isinstance(raw_spec, str):
                try:
                    raw_spec = _json.loads(raw_spec)
                except Exception:
                    raw_spec = {}
            cal = ""
            if isinstance(raw_spec, dict):
                cal = (raw_spec.get("calendar_primary_range") or "").strip()
            if not dr and cal and ".." in cal:
                dr = _normalize_week_range_by_rule(
                    query=query,
                    today=today,
                    date_range=cal,
                )
            lit = _canonical_date_range_literal(dr)
            if lit:
                dr = lit
            lit_dr = _canonical_date_range_literal(dr)
            cal_chk = ""
            if isinstance(raw_spec, dict):
                cal_chk = (raw_spec.get("calendar_primary_range") or "").strip()
            bad_main = bool(dr.strip()) and not lit_dr
            bad_cal_only = (not dr.strip()) and bool(cal_chk) and not _canonical_date_range_literal(cal_chk)
            if bad_main or bad_cal_only:
                # 日付の LLM によるやり直しは 2 回以上はしない（このブロックの 1 回が再実行の上限）
                snap = {
                    "query": q,
                    "date_range": dr,
                    "intent_spec": dict(raw_spec) if isinstance(raw_spec, dict) else {},
                }
                fixed = _regenerate_step0_dates_after_failure(
                    llm_client,
                    query,
                    today,
                    snap,
                    selected,
                    log_context=log_context,
                )
                if fixed:
                    q = fixed["query"]
                    dr = fixed["date_range"]
                    intent_spec = fixed["intent_spec"]
                else:
                    print(
                        "[WARN] Step0: date retry failed; clearing date_range and calendar fields in intent",
                        flush=True,
                    )
                    dr = ""
                    if isinstance(raw_spec, dict):
                        raw_spec = dict(raw_spec)
                        raw_spec["calendar_primary_range"] = ""
                        raw_spec["document_context_range"] = ""
                        raw_spec["focal_dates"] = []
                    intent_spec = _normalize_intent_spec_dict(raw_spec, query, dr)
            else:
                intent_spec = _normalize_intent_spec_dict(raw_spec, query, dr)
            return {"query": q, "date_range": dr, "intent_spec": intent_spec}
        except Exception:
            pass
    sp = _normalize_intent_spec_dict({}, query, "")
    return {"query": query, "date_range": "", "intent_spec": sp}


def _compress_step1(
    llm_client, model_name: str, ordered_rag_blob: str, output_limit: int, log_context: dict = None
) -> Tuple[str, str]:
    """
    Step1: Evidenceノート生成（抽象要約禁止）

    各文書から質問に関連する情報を抜粋・構造化する。
    重複をまとめ、必ずSourceを付ける。
    戻り値: (抽出テキスト, call_model に渡したプロンプト全文)
    """
    prompt = f"""【網羅の前提】下記【ルール】先頭の【網羅｜絶対遵守】と同義。【入力】の【１】【２】【３】を読み飛ばさず、質問（１）に関係しうる情報を拾いつくす。

以下は【１→２→３の順】の質問および参照資料です。末尾がシステム上限で切れている場合があります。

【入力】
{ordered_rag_blob}

【ルール】
- {RAG_POLICY_ANSWER_UNIVERSAL_JA}
- 抽象要約禁止。原文の短い抜粋（1〜2文）をEvidenceとして必ず残す
- 同一内容の重複だけまとめ、異なる日付・項目・条件は別エントリに分ける（件数を勝手に上限しない）
- SourceはタイトルまたはDocIDを使う
- 出力形式（1エントリごと）:
  Claim: （短い主張）
  Evidence: （原文抜粋1〜2文）
  Source: （タイトル/ファイル名）
  Tag: （dates/scope/items/rules/exceptions/numbers から該当するもの）
  Confidence: （0〜1）
- 出力上限: {output_limit}字

【抽出結果】
"""
    response = llm_client.call_model(
        tier="ui_response",
        prompt=prompt,
        model_name=model_name,
        log_context=log_context,
    )
    if response.get('success'):
        return response.get('content', '').strip(), prompt
    return ordered_rag_blob[:output_limit], prompt


def _compress_step2(
    llm_client, model_name: str, query: str, step1_output: str, output_limit: int, log_context: dict = None
) -> Tuple[str, str]:
    """
    Step2: 論点別証拠束への再編（抽象化最小）

    Step1のEvidenceノートを論点(Topic)ごとに再編成する。
    新しい主張の創作禁止。根拠は必ず残す。
    戻り値: (再編成テキスト, call_model に渡したプロンプト全文)
    """
    prompt = f"""【網羅の前提】下記【ルール】先頭の【網羅｜絶対遵守】が Evidence ノートの各行にも適用される。ノートに載っている抜粋・論点を落とさず再編成する。

以下のEvidenceノートを、論点(Topic)ごとに再編成してください。

【質問】
{query}

【Evidenceノート】
{step1_output}

【ルール】
- {RAG_POLICY_ANSWER_UNIVERSAL_JA}
- タグ単位で束ねる（日付/範囲/持ち物/注意事項/例外など）
- 各TopicにKey takeaway + Evidence（抜粋）+ Sourcesを残す（Evidenceの取りこぼしをしない）
- 新しい主張の創作禁止
- 出力形式:
  Topic: （論点名）
    Key takeaway: （1行の結論）
    Evidence: （抜粋2〜5個）
    Sources: （ファイル名列挙）
- 出力上限: {output_limit}字

【論点別整理】
"""
    response = llm_client.call_model(
        tier="ui_response",
        prompt=prompt,
        model_name=model_name,
        log_context=log_context,
    )
    if response.get('success'):
        return response.get('content', '').strip(), prompt
    return step1_output[:output_limit], prompt


def _compress_step3(
    llm_client, model_name: str, query: str, step2_output: str, log_context: dict = None
) -> Tuple[str, str]:
    """
    Step3: 最終回答生成（ここで初めて抽象化OK）

    Topic別証拠束を基にユーザー向けの自然文にまとめる。
    Evidenceがある内容のみ記載。曖昧な点は明示。
    戻り値: (回答本文, call_model に渡したプロンプト全文)
    """
    prompt_parts = [f"""【網羅の前提】下記【ルール】先頭の【網羅｜絶対遵守】が、この証拠束に含まれる Evidence・記述にも適用される。取りこぼしなく反映してから回答する。

以下のTopic別証拠束を基に、ユーザーの質問に回答してください。

【質問】
{query}

【Topic別証拠束】
{step2_output}

【ルール】
- {RAG_POLICY_ANSWER_UNIVERSAL_JA}
- Evidenceがある内容のみ記載（創作禁止）
- 曖昧・不確かな点は「〜の可能性があります」と明示
- 見出し・箇条書きを活用して読みやすく整形する
- 重要情報（期限・提出方法・場所など）は太字で強調する
- 【質問】に含まれるカレンダー・機械抽出の情報は優先して反映し、文書と矛盾する場合は「⚠️ 注記：」で明示する
- 回答末尾に「参考文書：」として使用したファイル名を列挙する
- 長さは内容に応じて調整する（冗長な同義繰り返しは避け、Evidenceにあった事項の省略はしない）

【回答】
"""]

    prompt = "\n".join(prompt_parts)
    response = llm_client.call_model(
        tier="ui_response",
        prompt=prompt,
        model_name=model_name,
        log_context=log_context,
    )
    if response.get('success'):
        return response.get('content', '').strip(), prompt
    return '', prompt


def _format_table_to_markdown(table_data: Dict[str, Any]) -> str:
    """
    表データをMarkdown形式のテーブルに変換（Phase 2.2.3 構造的クエリ対応）

    Args:
        table_data: 表データ（table_type, headers, rows などを含む）

    Returns:
        Markdown形式のテーブル文字列
    """
    try:
        table_type = table_data.get("table_type", "table")
        headers = table_data.get("headers", [])

        # ヘッダー行の構築
        if isinstance(headers, list) and headers:
            # シンプルなリスト形式のヘッダー
            header_line = "| " + " | ".join(str(h) for h in headers) + " |"
            separator_line = "|" + "|".join(["---" for _ in headers]) + "|"
            markdown_lines = [f"\n**表形式データ ({table_type})**\n", header_line, separator_line]
        elif isinstance(headers, dict):
            # 複雑なヘッダー構造（例: class_timetable の classes）
            classes = headers.get("classes", [])
            if classes:
                header_line = "| 日 | " + " | ".join(str(c) for c in classes) + " |"
                separator_line = "|" + "|".join(["---" for _ in range(len(classes) + 1)]) + "|"
                markdown_lines = [f"\n**クラス別時間割 ({table_type})**\n", header_line, separator_line]
            else:
                markdown_lines = [f"\n**表形式データ ({table_type})**\n"]
        else:
            markdown_lines = [f"\n**表形式データ ({table_type})**\n"]

        # 行データの処理
        rows = table_data.get("rows", [])
        if rows:
            for row in rows:
                # 行が辞書形式の場合
                if isinstance(row, dict):
                    # cells フィールドがある場合
                    if "cells" in row:
                        cells = row["cells"]
                        cell_values = []
                        for cell in cells:
                            if isinstance(cell, dict):
                                value = cell.get("value", "")
                                cell_values.append(str(value))
                            else:
                                cell_values.append(str(cell))
                        row_line = "| " + " | ".join(cell_values) + " |"
                        markdown_lines.append(row_line)
                    else:
                        # 通常の辞書行（キー: 値）
                        values = [str(v) for v in row.values()]
                        row_line = "| " + " | ".join(values) + " |"
                        markdown_lines.append(row_line)

        # daily_schedule や agenda_groups などの特殊構造
        if "daily_schedule" in table_data:
            markdown_lines.append("\n**日別スケジュール:**")
            for schedule in table_data["daily_schedule"]:
                day = schedule.get("day", "")
                markdown_lines.append(f"\n- **{day}曜日:**")

                if "class_schedules" in schedule:
                    for class_schedule in schedule["class_schedules"]:
                        class_name = class_schedule.get("class", "")
                        subjects = class_schedule.get("subjects", []) or class_schedule.get("periods", [])
                        markdown_lines.append(f"  - {class_name}: {', '.join(str(s) for s in subjects)}")

        if "agenda_groups" in table_data:
            markdown_lines.append("\n**議題グループ:**")
            for group in table_data["agenda_groups"]:
                topic = group.get("topic", "")
                markdown_lines.append(f"\n- **{topic}:**")
                for item in group.get("items", []):
                    decision = item.get("decision", "")
                    assignee = item.get("assignee", "")
                    deadline = item.get("deadline", "")
                    markdown_lines.append(f"  - {decision} (担当: {assignee}, 期限: {deadline})")

        return "\n".join(markdown_lines)

    except Exception as e:
        return f"\n[表データの変換エラー: {str(e)}]\n"


def _format_metadata(metadata: Dict[str, Any], indent: int = 0) -> str:
    """
    メタデータを見やすく整形（Phase 2.2.3: tables フィールド対応）

    Args:
        metadata: メタデータ辞書
        indent: インデントレベル

    Returns:
        整形された文字列
    """
    if not metadata:
        return ""

    lines = []
    prefix = "  " * indent

    for key, value in metadata.items():
        # Phase 2.2.3: tables フィールドを特別に処理
        if key == "tables" and isinstance(value, list):
            if not value:
                continue
            lines.append(f"{prefix}【表データ】")
            for idx, table in enumerate(value, 1):
                if isinstance(table, dict):
                    # 表をMarkdown形式に変換
                    markdown_table = _format_table_to_markdown(table)
                    lines.append(markdown_table)
            continue

        # 通常のメタデータ処理
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(_format_metadata(value, indent + 1))
        elif isinstance(value, list):
            if not value:
                continue
            lines.append(f"{prefix}{key}:")
            for item in value:
                if isinstance(item, dict):
                    # 辞書のリストの場合、各アイテムを整形
                    for sub_key, sub_value in item.items():
                        lines.append(f"{prefix}  - {sub_key}: {sub_value}")
                else:
                    lines.append(f"{prefix}  - {item}")
        else:
            lines.append(f"{prefix}{key}: {value}")

    return "\n".join(lines)


def _group_documents_by_file(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    同じ文書を表す番号のチャンクをまとめ、類似度がいちばん高いチャンクを1行の土台にして返す。

    Args:
        documents: 検索結果のリスト

    Returns:
        文書ごとに1件（その中で類似度が最大のチャンクを基準に、ほかのチャンクの本文を併合）
    """
    from collections import defaultdict

    # ドキュメントIDでグルーピング
    grouped = defaultdict(list)
    for doc in documents:
        doc_id = doc.get('id')
        if doc_id:
            grouped[doc_id].append(doc)

    # 各ドキュメントグループから最高スコアのチャンクを選択
    result = []
    for doc_id, chunks in grouped.items():
        # 類似度が最も高いチャンクを選択
        best_chunk = max(chunks, key=lambda x: x.get('similarity', 0))

        # 同じドキュメントの全チャンクの内容を結合（重複排除）
        all_contents = []
        seen_contents = set()
        for chunk in sorted(chunks, key=lambda x: x.get('similarity', 0), reverse=True):
            content = chunk.get('content') or chunk.get('summary', '')
            if content and content not in seen_contents:
                all_contents.append(content)
                seen_contents.add(content)

        # 最高スコアのチャンクに統合された内容を設定
        if all_contents:
            best_chunk['content'] = '\n\n'.join(all_contents[:3])  # 最大3チャンクまで

        result.append(best_chunk)

    # 類似度順にソート
    result.sort(key=lambda x: x.get('similarity', 0), reverse=True)

    return result


def _detect_date_filter(query: str) -> Optional[str]:
    """
    クエリから時系列フィルタを検出

    Args:
        query: ユーザーのクエリ

    Returns:
        'recent': 最近1週間
        'this_week': 今週
        'this_month': 今月
        'today': 今日
        None: フィルタなし
    """
    import re
    from datetime import datetime, timedelta

    query_lower = query.lower()

    # 最新・最近届いた文書（受信日フィルタとして有効）
    if re.search(r'(最新|最近|さいきん|さいしん|new|latest|recent)', query):
        return 'recent'

    # 今日・今週・今月は文書の受信日でなくコンテンツの内容に関する質問なので
    # ベクトル検索＋LLMに委ねる（受信日フィルタは不適切）
    return None


def _parse_date_range(date_range: str) -> Tuple[Optional[date], Optional[date]]:
    if not date_range or ".." not in date_range:
        return None, None
    try:
        start_s, end_s = date_range.split("..", 1)
        return datetime.strptime(start_s.strip(), "%Y-%m-%d").date(), datetime.strptime(end_s.strip(), "%Y-%m-%d").date()
    except Exception:
        return None, None


def _to_sunday_start(d: date) -> date:
    # Python weekday: Mon=0 ... Sun=6
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _normalize_week_range_by_rule(query: str, today: str, date_range: str) -> str:
    """
    暦の固定化（返すのは常に YYYY-MM-DD..YYYY-MM-DD の2端）:
    - 来週: 次の日曜〜その7日後の日曜
    - 明日から一週間: 開始=明日、終了=明日+7暦日（例: 今日が 2026-05-08 なら 2026-05-09..2026-05-16）
    - 「M/Dの週」「M月D日の週」: その日を含む週の日曜から+7日後の日曜（例: 2026年「5/11の週」→ 2026-05-10..2026-05-17）
    """
    q = _to_halfwidth_digits((query or "").strip())
    if not q:
        return date_range
    try:
        base_today = datetime.strptime(_to_halfwidth_digits(str(today).strip()[:10]), "%Y-%m-%d").date()
    except Exception:
        base_today = datetime.now().date()

    # 来週
    if "来週" in q:
        this_sunday = _to_sunday_start(base_today)
        start = this_sunday + timedelta(days=7)
        end = start + timedelta(days=7)
        return f"{start.isoformat()}..{end.isoformat()}"

    # 明日から一週間（終了=明日+7暦日。例: 今日 2026-05-08 → 2026-05-09..2026-05-16）
    if "明日" in q and "から" in q and ("一週間" in q or "1週間" in q):
        start = base_today + timedelta(days=1)
        end = start + timedelta(days=7)
        return f"{start.isoformat()}..{end.isoformat()}"

    # 「M/Dの週」「M/Dを含む週」（西暦年は today の年。パース不能なら date_range をそのまま返す）
    m_slash = re.search(r"(\d{1,2})/(\d{1,2})(?:を含む)?の?週", q)
    if m_slash:
        month = int(m_slash.group(1))
        day = int(m_slash.group(2))
        year = base_today.year
        try:
            d = date(year, month, day)
        except Exception:
            return date_range
        start = _to_sunday_start(d)
        end = start + timedelta(days=7)
        return f"{start.isoformat()}..{end.isoformat()}"

    # 「X月Y日の週」または「Y日の週」
    m = re.search(r"(?:(\d{1,2})月\s*)?(\d{1,2})日(?:を含む)?の?週", q)
    if m:
        month = int(m.group(1)) if m.group(1) else base_today.month
        day = int(m.group(2))
        year = base_today.year
        try:
            d = date(year, month, day)
        except Exception:
            return date_range
        start = _to_sunday_start(d)
        end = start + timedelta(days=7)
        return f"{start.isoformat()}..{end.isoformat()}"

    return date_range


def _ix_search_dates_parsed(doc: Dict[str, Any]) -> List[date]:
    """
    09_unified_documents.ix_search_dates（検索用にフラット化した日付集約）だけを使う。
    document_date や post_at 単体は使わない（集約と二重解釈になるため）。
    """
    out: List[date] = []
    raw = doc.get("ix_search_dates") or []
    if not isinstance(raw, list):
        return out
    for raw_d in raw:
        try:
            out.append(datetime.strptime(str(raw_d)[:10], "%Y-%m-%d").date())
        except Exception:
            continue
    return out


def _resolve_retrieval_date_window(user_query: str, refined_date_range: str, today: str) -> Tuple[date, date]:
    """
    検索に使う暦の窓（閉区間）を決める。
    - 去年・昨年: 前年の1/1〜12/31
    - date_range が YYYY-MM-DD..YYYY-MM-DD でパースできたとき: 開始日から SEARCH_CALENDAR_MARGIN_DAYS 暦日前、終了日から同数暦日後（1暦月は使わない）
    - それ以外: 今日を起点に前後約1年
    """
    q = (user_query or "").strip()
    if re.search(r"(去年|昨年)", q):
        try:
            y = int(str(today)[:4]) - 1
        except Exception:
            y = datetime.now().year - 1
        return date(y, 1, 1), date(y, 12, 31)

    s, e = _parse_date_range_bounds(refined_date_range or "")
    if s is not None and e is not None:
        return s - timedelta(days=SEARCH_CALENDAR_MARGIN_DAYS), e + timedelta(days=SEARCH_CALENDAR_MARGIN_DAYS)

    try:
        t0 = date.fromisoformat(str(today).strip()[:10])
    except Exception:
        t0 = datetime.now().date()
    return t0 - timedelta(days=365), t0 + timedelta(days=365)


def _apply_date_match_bonus(results: List[Dict[str, Any]], date_range: str, query: str = "") -> List[Dict[str, Any]]:
    """
    「構成」の（2）：主軸日付との近さで加点し final_score を similarity + bonus とする。
    （参照する日付は検索用の ix_search_dates のみ）

    date_range は主軸の狭いレンジを渡すこと。
    """
    start_d, end_d = _parse_date_range(date_range)
    if not results:
        return results
    q = query or ""
    month_m = re.search(r"(\d{1,2})月", q)
    specified_month = int(month_m.group(1)) if month_m else None

    ranked: List[Dict[str, Any]] = []
    for doc in results:
        sim_raw = doc.get("similarity")
        if sim_raw is not None:
            try:
                base = float(sim_raw)
            except (TypeError, ValueError):
                base = 0.0
        else:
            base = 0.0
        bonus = 0.0
        if start_d and end_d:
            for dd in _ix_search_dates_parsed(doc):
                if start_d <= dd <= end_d:
                    bonus = max(bonus, 0.30)
                else:
                    dist = min(abs((dd - start_d).days), abs((dd - end_d).days))
                    if dist <= 7:
                        bonus = max(bonus, 0.16)
                    elif dist <= 14:
                        bonus = max(bonus, 0.08)
                    elif specified_month and dd.month == specified_month:
                        bonus = max(bonus, 0.06)
        d = dict(doc)
        d["date_bonus"] = bonus
        d["final_score"] = round(base + bonus, 6)
        d["is_date_matched"] = bool(bonus > 0)
        ranked.append(d)

    ranked.sort(key=lambda x: x.get("final_score", 0.0), reverse=True)
    return ranked


def _detect_query_type(query: str) -> Dict[str, Any]:
    """
    クエリのタイプを検出

    Args:
        query: ユーザーのクエリ

    Returns:
        {
            'type': str,  # 'list', 'who', 'when', 'what', 'where', 'how', 'why', 'general'
            'focus': str,  # 検出されたフォーカス
            'keywords': List[str]  # 検出されたキーワード
        }
    """
    import re

    q = (query or "").strip()

    # 優先順位: 明示列挙 → 人物（単一答え想定）→ 予定・週・持ち物等（複数答え想定の list）
    # → 単一点の日時・締切（when）→ その他

    # List: 明示的に列挙・網羅を求める
    if re.search(
        r'(一覧|リスト|列挙|全件|ぜんけん|全部|すべて|全て|まとめて|箇条書き|順に|並べて|'
        r'網羅|漏れなく|抜けなく|list|enumerate|bullet|all\s+of|everything)',
        q,
        re.IGNORECASE,
    ):
        return {
            'type': 'list',
            'focus': 'enumeration',
            'keywords': ['list', 'enumeration', 'all_items', 'schedule', 'content'],
        }

    # Who: 単一または人物集合の「誰」だが、予定一覧より先に人物意図を拾う
    if re.search(r'(誰|だれ|who\b|先生|teacher|from\b|送信者|差出人)', q, re.IGNORECASE):
        return {
            'type': 'who',
            'focus': 'person',
            'keywords': ['sender', 'teacher', 'author', 'display_sender'],
        }

    # List: 予定・週・行事＋持ち物など「答えが複数になりやすい」スケジュール／準備系
    # （「予定|スケジュール」を when に寄せない）
    if re.search(
        r'('
        r'予定|スケジュール|行程|タイムテーブル|カレンダー|'
        r'来週|先週|今週|翌週|先々週|再来週|'
        r'の週\b|週の(?:予定|スケジュール|授業)?|'
        r'一週間|１週間|1週間|この週間|'
        r'持ち物|準備(?:物|するもの)?|必要なもの|忘れ(?:ず|ないで)|チェックリスト|'
        r'(?:遠足|修学旅行|宿泊学習|運動会|文化祭|発表会).{0,18}(?:持ち物|準備|何を|何が|リスト|教えて)|'
        r'(?:明日|明後日|あす|あさって).{0,12}(?:予定|スケジュール|何が|何かある|用事)'
        r')',
        q,
        re.IGNORECASE,
    ):
        return {
            'type': 'list',
            'focus': 'schedule_or_multi',
            'keywords': ['schedule', 'enumeration', 'events', 'items', 'weekly'],
        }

    # When: 単一点の日時・締切（「予定」単体は上の list に回す）
    if re.search(
        r'(いつ|何時|何日|何月|何年|when\b|期限|締切|締め切り|デッドライン|いつまで|何日まで|due\b)',
        q,
        re.IGNORECASE,
    ):
        return {
            'type': 'when',
            'focus': 'time_point',
            'keywords': ['document_date', 'deadline', 'due_datetime'],
        }

    # Where: 場所に関する質問
    if re.search(r'(どこ|where|場所|教室|クラス|classroom)', q, re.IGNORECASE):
        return {
            'type': 'where',
            'focus': 'location',
            'keywords': ['location', 'classroom', 'place'],
        }

    # How: 方法・手順に関する質問
    if re.search(r'(どうやって|どのように|how|方法|手順|やり方)', q, re.IGNORECASE):
        return {
            'type': 'how',
            'focus': 'method',
            'keywords': ['procedure', 'method', 'steps'],
        }

    # Why: 理由に関する質問
    if re.search(r'(なぜ|why|理由|原因)', q, re.IGNORECASE):
        return {
            'type': 'why',
            'focus': 'reason',
            'keywords': ['reason', 'cause', 'purpose'],
        }

    # What: 物事・内容に関する質問（デフォルト）
    if re.search(r'(何|なに|what|内容|詳細)', q, re.IGNORECASE):
        return {
            'type': 'what',
            'focus': 'content',
            'keywords': ['content', 'subject', 'topic']
        }

    # General: 一般的な質問
    return {
        'type': 'general',
        'focus': 'general',
        'keywords': []
    }


def _llm_chunk_heading(ch: Optional[Dict[str, Any]]) -> str:
    """回答用コンテキストに付けるチャンク見出し（識別子風の記法は使わない）。"""
    if not ch:
        return ""
    idx = ch.get("chunk_index")
    ctype = (ch.get("chunk_type") or "").strip()
    parts: List[str] = []
    if idx is not None:
        parts.append(f"文書内の位置 {idx}")
    if ctype:
        parts.append(f"種別 {ctype}")
    return (f"【{'・'.join(parts)}】\n") if parts else ""


def _chunk_stable_key(doc_id: str, ch: Optional[Dict[str, Any]], fallback_suffix: str) -> str:
    if ch:
        cid = ch.get("id")
        if cid is not None:
            return f"{doc_id}:{cid}"
        idx = ch.get("chunk_index")
        if idx is not None:
            return f"{doc_id}:idx:{idx}"
    return f"{doc_id}:{fallback_suffix}"


def _chunk_row_similarity(ch: Optional[Dict[str, Any]], doc_fallback: float) -> float:
    """チャンク行の類似度。chunk_vector_similarity が無ければ文書側のベクトル類似度にフォールバック。"""
    if ch:
        v = ch.get("chunk_vector_similarity")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return doc_fallback


def _indexed_chunks_ordered_for_context(doc: Dict[str, Any]) -> List[Tuple[Optional[Dict[str, Any]], str]]:
    """
    インデックス上のチャンクを順序付けで返す（index_chunks_all のみ）。
    【３】の列挙と、chunk_content の単一スニペット行に使う。【２】は document_body のみ（ここは使わない）。
    chunk が無くベストだけあるときは1件のみ。
    """
    chunks_raw = doc.get("index_chunks_all") or []
    rows: List[Dict[str, Any]] = []
    if isinstance(chunks_raw, list):
        for row in chunks_raw:
            if isinstance(row, dict):
                rows.append(row)
    if rows:
        rows.sort(
            key=lambda x: (
                x.get("chunk_index") if x.get("chunk_index") is not None else 10**9,
                str(x.get("id") or ""),
            ),
        )
        out: List[Tuple[Optional[Dict[str, Any]], str]] = []
        for ch in rows:
            txt = (ch.get("chunk_text") or "").strip()
            if txt:
                out.append((ch, txt))
        return out

    snippet = (doc.get("chunk_content") or "").strip()
    return [(None, snippet)] if snippet else []


def _filter_documents_verified_in_09_unified(db_client: Any, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    【２】【３】に使うベクトル系ヒットは、09_unified_documents に実在する id のみ残す。
    RPC／クライアント渡しのずれで幽霊 doc_id が混ざっても載せない。
    Googleカレンダー行は据え置き（【２】【３】では本来就除外）。
    """
    if not documents:
        return documents

    unique_ids: List[str] = []
    seen: set[str] = set()
    for d in documents:
        if d.get("source") == "Googleカレンダー":
            continue
        did = str(d.get("id") or "").strip()
        if did and did not in seen:
            seen.add(did)
            unique_ids.append(did)

    if not unique_ids:
        return documents

    existing: set[str] = set()
    batch_size = 120
    for i in range(0, len(unique_ids), batch_size):
        batch = unique_ids[i : i + batch_size]
        try:
            resp = db_client.client.table("09_unified_documents").select("id").in_("id", batch).execute()
            for row in resp.data or []:
                rid = str(row.get("id") or "").strip()
                if rid:
                    existing.add(rid)
        except Exception as e:
            print(f"[WARN] 09 実在チェック batch 失敗: {e}", flush=True)

    out: List[Dict[str, Any]] = []
    dropped = 0
    for d in documents:
        if d.get("source") == "Googleカレンダー":
            out.append(d)
            continue
        sid = str(d.get("id") or "").strip()
        if not sid:
            dropped += 1
            continue
        if sid in existing:
            out.append(d)
        else:
            dropped += 1
            print(f"[INFO] 09 に無い doc_id を【２】【３】入力から除外: {sid}", flush=True)

    if dropped:
        print(f"[INFO] 09 実在チェック: ベクトル系 {dropped} 件除外", flush=True)
    return out


def _vector_similarity_for_top3_ranking(doc: Dict[str, Any]) -> float:
    """（2）の並び順。doc['similarity'] のみ（DB では当該文書のチャンク類似度の最大）。final_score は混ぜない。"""
    s = doc.get("similarity")
    if s is None:
        return float("-inf")
    try:
        return float(s)
    except (TypeError, ValueError):
        return float("-inf")


def _build_context_sections(
    documents: List[Dict[str, Any]], focal_date_range: Optional[str] = None
) -> Tuple[str, str]:
    """
    （2）（3）のみ返すタプル。（1）は呼び出し側で質問へ付与済みである前提。

    （2）全チャンクを類似度の良い順に見て doc_id ごとに文書類似度を付与するが、同一文書では低い値で上書きしない
        （＝その doc のチャンク類似度の最大）。その文書類似度で並べた上位ちょうど3 UUID のみを対象に、
        document_body（統合MD）が非空のものを同順で連結する。4位以下で穴埋めしない。
    （3）全チャンクをチャンク類似度の高い順に並べ、【２】に選ばれた上位3 UUID の doc_id と一致しないチャンクだけを送る。
        ブロックに載せる類似度はチャンク単位。
    Googleカレンダー行は含めない。
    呼び出し側で _filter_documents_verified_in_09_unified を通し、09 に無い id を先に落とすこと。
    focal_date_range: 呼び出し互換のみ（現在は未参照）。
    """
    _ = focal_date_range

    empty_pair = ("", "")
    if not documents:
        return empty_pair

    sep = "─" * 60

    text_docs = [d for d in documents if d.get("source") != "Googleカレンダー"]

    # (chunk_sim, doc_id, doc_dict, chunk_row|None, chunk_text)
    chunk_events: List[Tuple[float, str, Dict[str, Any], Optional[Dict[str, Any]], str]] = []
    doc_by_id: Dict[str, Dict[str, Any]] = {}

    for doc in text_docs:
        did = str(doc.get("id") or "").strip()
        if not did:
            continue
        doc_by_id[did] = doc
        doc_fallback = _vector_similarity_for_top3_ranking(doc)
        for ch, txt in _indexed_chunks_ordered_for_context(doc):
            t = (txt or "").strip()
            if not t:
                continue
            ch_sim = _chunk_row_similarity(ch, doc_fallback)
            chunk_events.append((ch_sim, did, doc, ch, t))

    doc_best: Dict[str, float] = {}
    for sim, did, *_rest in chunk_events:
        doc_best[did] = max(doc_best.get(did, float("-inf")), sim)

    for doc in text_docs:
        did = str(doc.get("id") or "").strip()
        if not did:
            continue
        if did not in doc_best:
            doc_best[did] = _vector_similarity_for_top3_ranking(doc)

    ordered_docs = sorted(
        text_docs,
        key=lambda d: (-doc_best.get(str(d.get("id") or "").strip(), float("-inf")), str(d.get("id") or "")),
    )
    top3_ids_ordered = [
        str(d.get("id") or "").strip()
        for d in ordered_docs[:3]
        if str(d.get("id") or "").strip()
    ]
    top3_id_set = set(top3_ids_ordered)

    integrated_md_bodies: List[str] = []
    for did in top3_ids_ordered:
        doc = doc_by_id.get(did)
        if not doc:
            continue
        body_text = (doc.get("document_body") or "").strip()
        if body_text:
            integrated_md_bodies.append(body_text)

    seg2 = "\n\n---\n\n".join(integrated_md_bodies).strip()

    chunk_events.sort(
        key=lambda x: (
            -x[0],
            x[1],
            (x[3] or {}).get("chunk_index") if x[3] is not None else 10**9,
            str((x[3] or {}).get("id") or "") if x[3] else "",
        ),
    )

    extras_idx = 0
    emitted_keys: set[str] = set()
    blocks3: List[str] = []

    for ch_sim, did, doc_local, ch, txt_local in chunk_events:
        if did in top3_id_set:
            continue
        key = _chunk_stable_key(did, ch, "snippet")
        if key in emitted_keys:
            continue
        emitted_keys.add(key)
        extras_idx += 1
        title_loc = doc_local.get("title", "無題")
        src_loc = doc_local.get("source", "不明")
        dd = doc_local.get("document_date", "")
        dm = doc_local.get("is_date_matched", False)
        tag = "（日付一致✓）" if dm else ""
        try:
            s_disp = f"{float(ch_sim):.3f}"
        except (TypeError, ValueError):
            s_disp = str(ch_sim)
        blk = (
            f"""【それ以外の抽出チャンク{extras_idx}】{tag}
タイトル: {title_loc}
ソース: {src_loc}
日付: {dd}
チャンク類似度: {s_disp}

{_llm_chunk_heading(ch)}{txt_local}
{sep}"""
        )
        blocks3.append(blk)

    if not integrated_md_bodies and not blocks3:
        return "", ""

    seg3 = "\n\n".join(blocks3).strip()
    total_chars_estimate = sum(len(x) for x in integrated_md_bodies) + sum(len(x) for x in blocks3)
    print(
        f"[DEBUG] （2）（3）: 【２】上位3doc UUID={top3_ids_ordered} body件数={len(integrated_md_bodies)} "
        f"【３】チャンク数={extras_idx} 文字数≈{total_chars_estimate}",
        flush=True,
    )

    return seg2, seg3


def _calendar_attendance_intent(query: str) -> Optional[set[str]]:
    """予定検索時に _arc/_pen を通常予定から分離するための出欠意図を返す。"""
    q = (query or "").lower()

    declined_terms = (
        "キャンセル", "取消", "取り消", "中止", "欠席", "不参加",
        "行かない", "断った", "declined", "_arc",
    )
    tentative_terms = (
        "未定", "保留", "仮", "ペンディング", "pending",
        "参加不参加未定", "tentative", "_pen",
    )
    all_terms = ("出欠", "参加状況", "全予定", "すべての予定", "全部の予定")

    wants_declined = any(t in q for t in declined_terms)
    wants_tentative = any(t in q for t in tentative_terms)
    wants_all = any(t in q for t in all_terms)

    if wants_all:
        return {"accepted", "declined", "tentative"}
    if wants_declined and wants_tentative:
        return {"declined", "tentative"}
    if wants_declined:
        return {"declined"}
    if wants_tentative:
        return {"tentative"}

    # 通常の「明日の予定」などでは _arc/_pen を出さない。
    return {"accepted"}


def _calendar_attendance_status(doc: Dict[str, Any]) -> str:
    meta = doc.get("meta") or {}
    status = meta.get("attendance_status")
    if status in {"accepted", "declined", "tentative"}:
        return status

    calendar_name = (meta.get("calendar_name") or "").strip()
    if calendar_name.endswith("_arc"):
        return "declined"
    if calendar_name.endswith("_pen"):
        return "tentative"

    attendees = meta.get("attendees")
    if isinstance(attendees, list):
        self_attendee = next((a for a in attendees if isinstance(a, dict) and a.get("self")), None)
        if self_attendee and self_attendee.get("responseStatus") in {"accepted", "declined", "tentative"}:
            return self_attendee["responseStatus"]

    return "accepted"


def _filter_calendar_results_by_attendance_intent(
    documents: List[Dict[str, Any]],
    query: str,
) -> List[Dict[str, Any]]:
    allowed_statuses = _calendar_attendance_intent(query)
    if not allowed_statuses:
        return documents

    filtered = []
    removed = 0
    for doc in documents:
        if doc.get("source") != "Googleカレンダー":
            filtered.append(doc)
            continue
        if _calendar_attendance_status(doc) in allowed_statuses:
            filtered.append(doc)
        else:
            removed += 1

    if removed:
        print(
            f"[DEBUG] カレンダー出欠フィルタ: allowed={sorted(allowed_statuses)} removed={removed}",
            flush=True,
        )
    return filtered


@app.route('/api/extract_schedules', methods=['POST'])
def extract_schedules():
    """
    スケジュール抽出API
    指定された条件でドキュメントからスケジュール情報を抽出して返す
    """
    try:
        # クライアント取得
        db_client, _ = get_clients()

        data = request.get_json()
        person     = data.get('person')
        sources    = data.get('sources', [])
        start_date = data.get('start_date')  # YYYY-MM-DD形式
        end_date   = data.get('end_date')    # YYYY-MM-DD形式
        limit      = data.get('limit', 100)

        print(f"[DEBUG] スケジュール抽出リクエスト: person={person}, sources={sources}, date_range={start_date}~{end_date}")

        # データベースクエリを構築
        query = db_client.client.table('09_unified_documents').select(
            'id, title, source, person, category, post_at, start_at, end_at, due_date, ui_data, meta'
        )

        if person:
            query = query.eq('person', person)

        if sources:
            query = query.in_('source', sources)

        # 日付範囲でフィルタ（post_at または start_at）
        if start_date:
            query = query.or_(f'post_at.gte.{start_date},start_at.gte.{start_date}')
        if end_date:
            query = query.or_(f'post_at.lte.{end_date},start_at.lte.{end_date}')

        response = query.limit(limit).execute()
        documents = response.data if response.data else []

        print(f"[DEBUG] 検索結果: {len(documents)} 件")

        import re
        schedules = []
        for doc in documents:
            doc_id    = doc.get('id')
            title     = doc.get('title') or ''
            source    = doc.get('source') or ''
            person_v  = doc.get('person') or ''
            post_at   = doc.get('post_at') or ''
            start_at  = doc.get('start_at') or ''
            ui_data   = doc.get('ui_data') or {}

            # Google Calendar イベントはそのままスケジュールとして扱う
            if source == 'Googleカレンダー':
                schedules.append({
                    'doc_id':       doc_id,
                    'title':        title,
                    'source':       source,
                    'person':       person_v,
                    'document_date': (start_at or post_at)[:10] if (start_at or post_at) else None,
                    'schedule_type': 'calendar_event',
                    'schedule_data': {
                        'start_at': doc.get('start_at'),
                        'end_at':   doc.get('end_at'),
                        'location': doc.get('location'),
                    }
                })
                continue

            # ui_data.sections からキーワードマッチでスケジュール抽出
            sections = ui_data.get('sections', [])
            for section in sections:
                sec_title = section.get('title', '') or ''
                sec_body  = section.get('body', '')  or ''
                combined  = sec_title + ' ' + sec_body
                if re.search(
                    r'(予定|スケジュール|日程|期限|締切|締め切り|\d{1,2}月\d{1,2}日|\d{4}-\d{2}-\d{2})',
                    combined
                ):
                    schedules.append({
                        'doc_id':       doc_id,
                        'title':        title,
                        'source':       source,
                        'person':       person_v,
                        'document_date': (post_at or start_at)[:10] if (post_at or start_at) else None,
                        'schedule_type': 'section',
                        'schedule_data': {
                            'title':   sec_title,
                            'content': sec_body,
                        }
                    })

        print(f"[DEBUG] 抽出されたスケジュール: {len(schedules)} 件")

        # 日付順にソート
        schedules_sorted = sorted(
            schedules,
            key=lambda x: x.get('document_date') or '9999-12-31'
        )

        return jsonify({
            'success': True,
            'schedules': schedules_sorted,
            'count': len(schedules_sorted)
        })

    except Exception as e:
        print(f"[ERROR] スケジュール抽出エラー: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    """ヘルスチェックエンドポイント"""
    return jsonify({
        'status': 'ok',
        'message': 'Document Q&A System is running'
    })


@app.route('/api/debug/database', methods=['GET'])
def debug_database():
    """データベース接続・検索インデックス診断エンドポイント"""
    result = {}
    errors = {}

    try:
        db_client, llm_client = get_clients()
    except Exception as e:
        return jsonify({'success': False, 'error': f'クライアント初期化失敗: {e}'}), 500

    # 1. 09_unified_documents 件数
    try:
        count_response = db_client.client.table('09_unified_documents').select('id', count='exact').limit(1).execute()
        result['unified_docs_count'] = count_response.count if hasattr(count_response, 'count') else 'unknown'
    except Exception as e:
        errors['unified_docs_count'] = str(e)

    # 2. workspace/doc_type 階層
    try:
        hierarchy = db_client.get_workspace_hierarchy()
        result['workspace_count'] = len(hierarchy)
        result['workspaces'] = list(hierarchy.keys())
    except Exception as e:
        errors['hierarchy'] = str(e)

    # 3. 10_ix_search_index 件数・embedding確認
    try:
        idx_response = db_client.client.table('10_ix_search_index').select('id', count='exact').limit(1).execute()
        result['search_index_count'] = idx_response.count if hasattr(idx_response, 'count') else 'unknown'
    except Exception as e:
        errors['search_index_count'] = str(e)

    try:
        sample = db_client.client.table('10_ix_search_index').select('doc_id, chunk_type, chunk_text').limit(3).execute()
        result['search_index_sample'] = [
            {'doc_id': str(r.get('doc_id', ''))[:8], 'chunk_type': r.get('chunk_type'), 'preview': (r.get('chunk_text') or '')[:50]}
            for r in (sample.data or [])
        ]
    except Exception as e:
        errors['search_index_sample'] = str(e)

    # embedding NULL件数確認（NULLならStage K未実行）
    try:
        # embedding IS NOT NULL なチャンク数（直接カラム選択でNULLチェック）
        not_null_resp = db_client.client.table('10_ix_search_index').select('id', count='exact').not_.is_('embedding', 'null').limit(1).execute()
        result['embedding_not_null_count'] = not_null_resp.count if hasattr(not_null_resp, 'count') else 'unknown'
    except Exception as e:
        errors['embedding_not_null_count'] = str(e)

    # 4. unified_search_v2 テスト呼び出し（ダミーembedding）
    try:
        # ゼロベクトルは余弦距離が未定義になるため微小値を使用
        test_embedding = [0.01] * 1536
        test_response = db_client.client.rpc('unified_search_v2', {
            'query_text': 'テスト',
            'query_embedding': test_embedding,
            'match_threshold': -2.0,  # 全件ヒット狙い（コサイン類似度の最小値は-1）
            'match_count': 3,
            'vector_weight': 0.7,
            'fulltext_weight': 0.3,
            'filter_sources': None,
            'filter_chunk_types': None,
            'filter_persons': None,
            'filter_category': None,
            'filter_date_start': None,
            'filter_date_end': None,
            'calendar_filter_date_start': None,
            'calendar_filter_date_end': None,
        }).execute()
        result['unified_search_v2_count'] = len(test_response.data or [])
        result['unified_search_v2_ok'] = True
    except Exception as e:
        result['unified_search_v2_ok'] = False
        errors['unified_search_v2'] = str(e)

    # 5. 環境変数確認
    supabase_url = os.getenv('SUPABASE_URL', 'NOT_SET')
    result['env'] = {
        'supabase_url': supabase_url[:30] + '...' if supabase_url != 'NOT_SET' else 'NOT_SET',
        'supabase_key_set': 'YES' if os.getenv('SUPABASE_KEY') else 'NO',
        'service_role_key_set': 'YES' if os.getenv('SUPABASE_SERVICE_ROLE_KEY') else 'NO',
        'openai_key_set': 'YES' if os.getenv('OPENAI_API_KEY') else 'NO',
    }

    return jsonify({
        'success': True,
        'result': result,
        'errors': errors
    })


@app.route('/api/debug/search-raw', methods=['GET'])
def debug_search_raw():
    """実際のembeddingで unified_search_v2 を直接テストするデバッグエンドポイント"""
    try:
        db_client, llm_client = get_clients()
        query = request.args.get('q', '今週の予定は？')

        # 実際のembeddingを生成
        embedding = llm_client.generate_embedding(query)
        embedding_preview = embedding[:5]  # 最初の5次元だけ確認用

        # threshold=-1.0で直接呼び出し（全件対象）
        resp = db_client.client.rpc('unified_search_v2', {
            'query_text': query,
            'query_embedding': embedding,
            'match_threshold': -1.0,
            'match_count': 5,
            'vector_weight': 0.7,
            'fulltext_weight': 0.3,
            'filter_sources': None,
            'filter_chunk_types': None,
            'filter_persons': None,
            'filter_category': None,
            'filter_date_start': None,
            'filter_date_end': None,
            'calendar_filter_date_start': None,
            'calendar_filter_date_end': None,
        }).execute()

        results_preview = []
        for r in (resp.data or []):
            results_preview.append({
                'doc_id': str(r.get('doc_id', ''))[:8],
                'title': (r.get('title') or '')[:40],
                'combined_score': r.get('combined_score'),
                'raw_similarity': r.get('raw_similarity'),
            })

        return jsonify({
            'success': True,
            'query': query,
            'embedding_dim': len(embedding),
            'embedding_preview': embedding_preview,
            'result_count': len(resp.data or []),
            'results': results_preview,
        })
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/workspaces', methods=['GET'])
def get_workspaces():
    """ワークスペース一覧を取得（検索UI専用）

    【設計方針】
    - このエンドポイントは doc-search UI 専用
    - document-hub（doc-processor）にも同名の /api/workspaces が存在するが、
      doc-search は別ホストのため衝突しない
    """
    try:
        from docsearch.db import DocSearchDB
        db = DocSearchDB(use_service_role=True)

        # person 一覧を取得（09_unified_documents ベース）
        query = db.client.table('09_unified_documents').select('person').execute()

        persons = set()
        for row in query.data:
            p = row.get('person')
            if p:
                persons.add(p)

        workspace_list = sorted(list(persons))

        return jsonify({
            'success': True,
            'workspaces': workspace_list
        })

    except Exception as e:
        print(f"[ERROR] ワークスペース取得エラー: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


if __name__ == '__main__':
    # 開発環境での実行
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
