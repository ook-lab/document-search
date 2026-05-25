"""pipeline-lab と同じ経路で 1 ページ実行し visual_stream の順を厳密検証する。"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import pdfplumber

_lab_dir = Path(__file__).resolve().parents[2]  # services/pipeline-lab/
sys.path.insert(0, str(_lab_dir))


def _stream_index(vs: list, needle: str, *, kind: Optional[str] = None) -> Optional[int]:
    for i, s in enumerate(vs):
        if kind and s.get("kind") != kind:
            continue
        if needle in (s.get("text") or ""):
            return i
    return None


def _first_table_index(vs: list) -> Optional[int]:
    for i, s in enumerate(vs):
        if s.get("kind") in ("g_table", "g_table_group"):
            return i
    return None


def _table_index(vs: list, table_index: int) -> Optional[int]:
    for i, s in enumerate(vs):
        if s.get("kind") == "g_table" and s.get("table_index") == table_index:
            return i
        if s.get("kind") == "g_table_group":
            idxs = s.get("table_indices") or []
            if table_index in idxs:
                return i
    return None


def _assert_before(
    failures: List[str],
    vs: list,
    label: str,
    a: Optional[int],
    b: Optional[int],
) -> None:
    if a is None or b is None:
        return
    if a > b:
        failures.append(f"{label}: index {a} は {b} より前である必要がある")
    elif a == b:
        return


def _pdf_anchor_order(pdf_path: Path, page: int) -> List[Tuple[str, float, float]]:
    """pdfplumber の単語を (text, top, x0) で読み順に並べた参照。"""
    rows: List[Tuple[str, float, float]] = []
    with pdfplumber.open(pdf_path) as doc:
        p = doc.pages[page]
        for w in p.extract_words() or []:
            t = (w.get("text") or "").strip()
            if t:
                rows.append((t, float(w["top"]), float(w["x0"])))
    rows.sort(key=lambda r: (round(r[1], 1), r[2]))
    return rows


def _anchor_rank(anchors: List[Tuple[str, float, float]], needle: str) -> Optional[int]:
    hits = [i for i, (_, top, x0) in enumerate(anchors) if needle in _]
    return hits[0] if hits else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path, help="入力 PDF（1 ページ目を page_index=0 で検証）")
    ap.add_argument("--page", type=int, default=0)
    args = ap.parse_args()
    pdf: Path = args.pdf
    if not pdf.is_file():
        print("PDF not found:", pdf, file=sys.stderr)
        return 2

    import blueprints.lab as L

    wd = Path(tempfile.mkdtemp(prefix="lab_ord_"))
    res = L._run_pdf_pipeline_stages(pdf, wd, "ordtest", args.page)
    if not res.get("success"):
        print("pipeline failed:", res.get("stage"), res.get("error"), file=sys.stderr)
        return 1

    vs = (res.get("reading") or {}).get("visual_stream") or []
    print("visual_stream_len", len(vs))
    for i, s in enumerate(vs):
        k = s.get("kind")
        sy = s.get("sort_y")
        if k == "non_table_paragraph":
            t = (s.get("text") or "")[:70].replace("\n", " ")
            print(f"{i:02d} prose sy={sy:.5f} | {t}")
        elif k == "g_table":
            print(f"{i:02d} g_table ti={s.get('table_index')} sy={sy:.5f}")
        elif k == "g_table_group":
            print(f"{i:02d} g_table_group idx={s.get('table_indices')} sy={sy:.5f}")
        elif k == "g21_article":
            print(f"{i:02d} g21 sy={sy:.5f}")
        else:
            print(f"{i:02d} {k} sy={sy}")

    ntb = (res.get("reading") or {}).get("non_table_text_blocks") or []
    print("non_table_text_blocks", len(ntb))
    for j, b in enumerate(ntb[:30]):
        t = (b.get("text") or "")[:55].replace("\n", " ")
        print(
            f"  blk{j:02d} y0={b.get('y0')} x0={b.get('x0')} "
            f"src={b.get('source')} | {t}"
        )

    failures: List[str] = []

    first_tbl = _first_table_index(vs)
    print("checks: first_table_index=", first_tbl)

    idx_hero = _stream_index(vs, "HERO")
    idx_yotei = _stream_index(vs, "予定")
    idx_23 = _stream_index(vs, "23日")
    idx_27 = _stream_index(vs, "27日")
    idx_28 = _stream_index(vs, "28日")
    idx_29 = _stream_index(vs, "昭和")
    idx_30 = _stream_index(vs, "30日")
    idx_1 = _stream_index(vs, "1日")
    idx_kettei = _stream_index(vs, "学級委員")
    idx_t0 = _table_index(vs, 0)
    idx_t1 = _table_index(vs, 1)
    idx_t2 = _table_index(vs, 2)

    idx_2026 = _stream_index(vs, "2026")
    idx_18 = _stream_index(vs, "18日")
    _assert_before(failures, vs, "2026→5/18見出し", idx_2026, idx_yotei)
    _assert_before(failures, vs, "5/18→18日", idx_yotei, idx_18)
    _assert_before(failures, vs, "23日→最初の表", idx_23, first_tbl)
    _assert_before(failures, vs, "HERO→予定見出し", idx_hero, idx_yotei)
    if idx_27 is not None:
        _assert_before(failures, vs, "予定→27日", idx_yotei, idx_27)
    if idx_27 is not None and idx_28 is not None:
        _assert_before(failures, vs, "27日→28日", idx_27, idx_28)
    if idx_28 is not None and idx_29 is not None:
        _assert_before(failures, vs, "28日→昭和(29日)", idx_28, idx_29)
    if idx_29 is not None and idx_30 is not None:
        _assert_before(failures, vs, "昭和→30日", idx_29, idx_30)
    if idx_30 is not None and idx_1 is not None:
        _assert_before(failures, vs, "30日→1日", idx_30, idx_1)
    if idx_1 is not None:
        _assert_before(failures, vs, "1日→最初の表", idx_1, first_tbl)
    _assert_before(failures, vs, "表0(5A)→表1(5B)", idx_t0, idx_t1)
    _assert_before(failures, vs, "表1(5B)→学級委員", idx_t1, idx_kettei)
    _assert_before(failures, vs, "学級委員→表2(名簿)", idx_kettei, idx_t2)

    prose_sy = [
        float(s["sort_y"])
        for s in vs
        if s.get("kind") == "non_table_paragraph"
    ]
    if prose_sy != sorted(prose_sy):
        failures.append("地の文 sort_y が単調増加でない")

    # F1 ブロックの (y0, x0) 順と visual_stream 地の文順が一致するか
    prose_texts = [
        (s.get("text") or "").strip()
        for s in vs
        if s.get("kind") == "non_table_paragraph"
    ]
    blk_texts = [(b.get("text") or "").strip() for b in ntb if (b.get("text") or "").strip()]
    if prose_texts != blk_texts:
        failures.append(
            "visual_stream 地の文の並びが non_table_text_blocks と一致しない "
            f"(stream={len(prose_texts)} blocks={len(blk_texts)})"
        )

    # pdfplumber で取れるアンカー同士の相対順を stream に反映しているか
    anchors = _pdf_anchor_order(pdf, args.page)

    def _stream_idx_for_anchor(needle: str) -> Optional[int]:
        if needle == "5A":
            return idx_t0
        if needle == "5B":
            return idx_t1
        return _stream_index(vs, needle)

    pdf_pairs = [
        ("HERO", "27日"),
        ("27日", "28日"),
        ("28日", "昭和"),
        ("昭和", "30日"),
        ("30日", "1日"),
        ("1日", "5A"),
        ("5B", "学級委員"),
    ]
    for a_needle, b_needle in pdf_pairs:
        ra = _anchor_rank(anchors, a_needle)
        rb = _anchor_rank(anchors, b_needle)
        if ra is None or rb is None or ra >= rb:
            continue
        if a_needle in ("5A", "5B") and b_needle in ("5A", "5B"):
            continue
        ia = _stream_idx_for_anchor(a_needle)
        ib = _stream_idx_for_anchor(b_needle)
        if ia is not None and ib is not None and ia == ib:
            continue
        _assert_before(failures, vs, f"PDF順 {a_needle}→{b_needle}", ia, ib)

    if idx_29 is not None and first_tbl is not None:
        showa_ok = idx_29 < first_tbl
        print("  昭和の地の文が最初の表より前:", showa_ok)
        if not showa_ok:
            failures.append("昭和を含む地の文が最初の表より前にない")

    idx_last_bullet = idx_23 if idx_23 is not None else idx_18
    if idx_last_bullet is not None and first_tbl is not None:
        bullets_before_table = idx_last_bullet < first_tbl
        print("  最終箇条書きが最初の表より前:", bullets_before_table)
        if not bullets_before_table:
            failures.append("予定箇条書きが最初の表より前にない")

    if idx_kettei is not None and idx_t1 is not None:
        kettei_ok = idx_kettei > idx_t1
        print("  学級委員ブロック index=", idx_kettei, "5B付近表 index=", idx_t1)
        print("  学級委員が5B表より後ろ(期待):", kettei_ok)
        if not kettei_ok:
            failures.append("学級委員が5B付近の表より後ろにない")

    if failures:
        for msg in failures:
            print("FAIL:", msg, file=sys.stderr)
        return 1

    print("OK: すべての順序チェックを通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
