"""
pipeline-lab HTTP API とパイプライン実行本体を同居させた単一 Flask アプリ。

前提（このサービスの実行モデル）::
    - アップロードは PDF 1 ファイルのみ（複数ファイルのバッチ投入は前提にしない）。
    - **パイプラインに渡すのは常に 1 本の PDF だけ**である。多ページの元ファイルでも、
      ジョブのたびにその 1 本分を書き出してから A/B/D/E に渡す。マルチページ PDF を
      丸ごと 1 ジョブで解析する経路は **存在しない**（「単ページモード」という対比ではなく、
      **lab における実行形はこの 1 種類しかない**）。
    - UI の ``page_index`` は「元ファイルのどの見開きを題材にするか」の選択であり、
      E の ``blocks[].page`` のようなパイプライン内部のページ概念とは別物。
    - DB キュー・リース・別プロセスワーカーは使わない。
      POST を処理している同一プロセス内で同期的にステージを呼ぶ。

エンドポイント::
    POST /api/run/<session_id>/<page_index> … 上記の 1 ジョブ（題材にする元ファイル上のインデックスを指定）。
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz  # PyMuPDF
from flask import Blueprint, current_app, jsonify, render_template, request, send_from_directory
from loguru import logger as loguru_logger
from werkzeug.utils import secure_filename

from dms.pipeline.stage_f.f46_f47_chain_logs import F46_LOG_NAME, F47_LOG_NAME
from dms.pipeline.stage_g.g11_controller import G11Controller as _G11Controller

lab_bp = Blueprint('pipeline_lab', __name__, template_folder='../templates')

_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.tif', '.tiff'}

# 料金表（USD / 100万トークン）。出力料金は思考トークンを含む。
_GEMINI_PRICING: Dict[str, Dict[str, float]] = {
    'gemini-3.1-flash-lite': {'input': 0.25, 'output': 1.50},
    'gemini-2.5-flash-lite': {'input': 0.10, 'output': 0.40},
}


def _calc_ai_cost(raw_entries: List[Dict]) -> Dict:
    """log_ai_usage エントリを集計してモデル別コスト内訳を返す。"""
    by_model: Dict[str, Dict] = {}
    for e in raw_entries:
        m = e['model']
        if m not in by_model:
            by_model[m] = {'prompt_tokens': 0, 'completion_tokens': 0, 'thinking_tokens': 0, 'calls': 0}
        by_model[m]['prompt_tokens'] += e['prompt_tokens']
        by_model[m]['completion_tokens'] += e['completion_tokens']
        by_model[m]['thinking_tokens'] += e['thinking_tokens']
        by_model[m]['calls'] += 1

    breakdown = []
    total = 0.0
    for model, tok in by_model.items():
        p = _GEMINI_PRICING.get(model) or {'input': 0.0, 'output': 0.0}
        in_cost = tok['prompt_tokens'] / 1_000_000 * p['input']
        out_cost = (tok['completion_tokens'] + tok['thinking_tokens']) / 1_000_000 * p['output']
        cost = in_cost + out_cost
        total += cost
        breakdown.append({
            'model': model,
            'calls': tok['calls'],
            'prompt_tokens': tok['prompt_tokens'],
            'completion_tokens': tok['completion_tokens'],
            'thinking_tokens': tok['thinking_tokens'],
            'input_cost_usd': round(in_cost, 6),
            'output_cost_usd': round(out_cost, 6),
            'total_cost_usd': round(cost, 6),
        })
    breakdown.sort(key=lambda x: x['total_cost_usd'], reverse=True)
    return {'breakdown': breakdown, 'total_cost_usd': round(total, 6)}


def _is_image(name: str) -> bool:
    return Path(name.lower()).suffix in _IMAGE_EXTS


def _image_to_pdf(img_path: Path, pdf_path: Path) -> None:
    """画像を単一ページ PDF に変換（PyMuPDF）。"""
    img_doc = fitz.open(str(img_path))
    pdf_bytes = img_doc.convert_to_pdf()
    img_doc.close()
    pdf_path.write_bytes(pdf_bytes)


def _sessions_root() -> Path:
    return Path(current_app.config['UPLOAD_FOLDER']) / 'pipeline_lab'


def _safe_session_dir(session_id: str) -> Optional[Path]:
    root = _sessions_root()
    target = (root / session_id).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    if not target.is_dir():
        return None
    return target


def _pdf_page_count(pdf_path: Path) -> int:
    doc = fitz.open(str(pdf_path))
    try:
        return len(doc)
    finally:
        doc.close()


def _render_pdf_previews(pdf_path: Path, session_dir: Path, session_slug: str) -> List[Dict[str, Any]]:
    """全ページ PNG（embedder と同じ Matrix 2 様式）。pages[].image_url でサムネ・メイン共通。"""
    preview_dir = session_dir / 'preview'
    preview_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    pages: List[Dict[str, Any]] = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            name = f'page_{i}.png'
            out_path = preview_dir / name
            pix.save(str(out_path))
            url = f'/pipeline-lab/files/{session_slug}/preview/{name}'
            pages.append({
                'page_index': i,
                'image_url': url,
            })
        return pages
    finally:
        doc.close()


def _wrap_single_d_result(d_result: Dict[str, Any]) -> Dict[str, Any]:
    """E1 の入力形に合わせ、D 結果を長さ 1 の配列として包む（lab は常に 1 本の D 結果だけ）。"""
    from dms.pipeline.pipeline_manager import PipelineManager

    return PipelineManager._merge_d_results([d_result])


def _lab_pipeline_input_pdf(pdf_path: Path, work_dir: Path, source_page_index: int) -> Path:
    """
    lab の 1 ジョブは **常に 1 本の PDF** だけを A/B/D/E に渡す（それ以外の実行形はない）。

    ``pdf_path`` が多ページなら ``source_page_index`` の 1 ページ分だけを複写した 1 本を返す。
    1 ページしか無いなら ``pdf_path`` をそのまま返す（embedder のサムネと同じ「題材の切り出し」）。
    """
    n = _pdf_page_count(pdf_path)
    if n <= 1:
        return pdf_path
    out = work_dir / f'run_slice_p{source_page_index}.pdf'
    from dms.pipeline.pipeline_manager import PipelineManager

    PipelineManager._split_pdf_single_page(pdf_path, source_page_index, out)
    return out


def _json_safe(obj: Any) -> Any:
    """Path 等を含むオブジェクトを JSON 可能な形にする。"""
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))


def _stage_d_table_previews(session_root: Path, session_slug: str, d_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Stage D が切り出した表画像ごとに、ラボ静的配信用の URL を付与する。"""
    out: List[Dict[str, Any]] = []
    try:
        root = session_root.resolve()
    except OSError:
        return out
    for t in (d_result.get('tables') or []):
        ip = t.get('image_path') or ''
        if not ip:
            continue
        try:
            abs_p = Path(str(ip)).resolve()
            rel = abs_p.relative_to(root)
            rel_str = rel.as_posix()
            out.append({
                'table_id': t.get('table_id') or '',
                'origin_uid': t.get('origin_uid') or '',
                'image_url': f'/pipeline-lab/files/{session_slug}/{rel_str}',
            })
        except (ValueError, OSError):
            continue
    return out


def _bbox_top_norm(bbox: Any) -> Optional[float]:
    """正規化 bbox（0〜1 想定）の上端 y。ピクセル座標っぽい場合は None。"""
    if not bbox or not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return None
    try:
        vals = [float(x) for x in bbox[:4]]
    except (TypeError, ValueError):
        return None
    if any(v > 2.5 or v < -0.5 for v in vals):
        return None
    return vals[1]


def _bbox_top_for_sort_y(bbox: Any, page_height_pt: Optional[float]) -> Optional[float]:
    """表 bbox の上端を visual_stream 用 0〜1 に。正規化済みならそのまま、pt なら page 高で割る。"""
    y_norm = _bbox_top_norm(bbox)
    if y_norm is not None:
        return y_norm
    if not bbox or not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return None
    if page_height_pt is None or page_height_pt <= 0:
        return None
    try:
        y0 = float(bbox[1])
    except (TypeError, ValueError, IndexError):
        return None
    return max(0.0, min(1.0, y0 / page_height_pt))


def _stage_b_tables_for_sort(stage_b_result: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stage B structured_tables のうち bbox を持つもの（D 表が無いときの並び用）。"""
    raw = list((stage_b_result or {}).get('structured_tables') or [])
    out: List[Dict[str, Any]] = []
    for t in raw:
        if isinstance(t, dict) and t.get('bbox'):
            out.append(t)
    return out


def _bbox_y_metrics_norm(bbox: Any) -> Optional[tuple[float, float, float]]:
    """正規化 bbox の (上端 y, 下端 y, 縦中心)。無効時は None。"""
    if not bbox or not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return None
    try:
        vals = [float(x) for x in bbox[:4]]
    except (TypeError, ValueError):
        return None
    if any(v > 2.5 or v < -0.5 for v in vals):
        return None
    y0, y1 = vals[1], vals[3]
    if y1 < y0:
        y0, y1 = y1, y0
    return (y0, y1, (y0 + y1) * 0.5)


def _first_row_shape(rows: List[Any]) -> Optional[tuple]:
    if not rows:
        return None
    r0 = rows[0]
    if isinstance(r0, dict):
        return ('dict', tuple(sorted(r0.keys())))
    if isinstance(r0, list):
        return ('list', len(r0))
    return None


def _merge_same_shape_indices(g_tables: List[Dict[str, Any]], indices: List[int]) -> bool:
    """同一 Stage D 帯の複数 ui_data.tables の折りたたみを 1 つに共有してよいか（行数・先頭行の形が一致）。"""
    if len(indices) < 2:
        return False
    row_lens: List[int] = []
    shapes: List[Optional[tuple]] = []
    for i in indices:
        if not isinstance(i, int) or not (0 <= i < len(g_tables)):
            return False
        rows = g_tables[i].get('rows') or []
        n = len(rows)
        if n < 1:
            return False
        row_lens.append(n)
        shapes.append(_first_row_shape(rows))
    if len(set(row_lens)) != 1:
        return False
    if any(s is None for s in shapes):
        return False
    return len(set(shapes)) == 1


def _infer_page_height_pt(stage_b_result: Optional[Dict[str, Any]]) -> Optional[float]:
    """Stage B logical_blocks の bbox 下端の最大からページ高（pt）。取れなければ None。"""
    if not stage_b_result:
        return None
    blocks = stage_b_result.get('logical_blocks') or []
    m = 0.0
    for b in blocks:
        bb = b.get('bbox')
        if isinstance(bb, (list, tuple)) and len(bb) >= 4:
            try:
                m = max(m, float(bb[3]))
            except (TypeError, ValueError):
                pass
    return m if m > 10.0 else None


def _page_height_pt_for_visual_stream(
    stage_b_result: Optional[Dict[str, Any]],
    d_result: Optional[Dict[str, Any]],
) -> Optional[float]:
    """D-3 ``page.height``（pt）を優先。無ければ B bbox から。どちらも無ければ None。"""
    try:
        vr = ((d_result or {}).get('debug') or {}).get('vector_lines') or {}
        ps = vr.get('page_size')
        if isinstance(ps, (list, tuple)) and len(ps) >= 2:
            h = float(ps[1])
            if h > 10.0:
                return h
    except (TypeError, ValueError, IndexError):
        pass
    return _infer_page_height_pt(stage_b_result)


def _normalize_f1_block_sort_y(y0: float, page_height_pt: float) -> Optional[float]:
    """F1 ブロック y0 を 0〜1 の並び用キーに正規化。座標が無効なら None（推測値は使わない）。"""
    try:
        y = float(y0)
    except (TypeError, ValueError):
        return None
    if -0.5 <= y <= 1.6:
        return max(0.0, min(1.0, y))
    if page_height_pt is None or page_height_pt <= 0:
        return None
    return max(0.0, min(1.0, y / page_height_pt))


def _sort_y_value(entry: Dict[str, Any]) -> Optional[float]:
    sy = entry.get('sort_y')
    if sy is None:
        return None
    try:
        return float(sy)
    except (TypeError, ValueError):
        return None


def _max_stream_sort_y(stream: List[Dict[str, Any]], default: float) -> float:
    ys = [_sort_y_value(s) for s in stream]
    ok = [y for y in ys if y is not None]
    return max(ok) if ok else default


def _table_entries_have_sort_y(table_entries: List[Dict[str, Any]]) -> bool:
    if not table_entries:
        return True
    for te in table_entries:
        if te.get('sort_y') is None:
            return False
        try:
            float(te['sort_y'])
        except (TypeError, ValueError, KeyError):
            return False
    return True


_BASE_TID_RE = re.compile(r"^(?P<base>.+?)_(?:[SF]\d+|S\d+)$")


def _base_table_id(table_id: str) -> str:
    m = _BASE_TID_RE.match(str(table_id or ""))
    return m.group("base") if m else str(table_id or "")


def _bbox_sort_y_from_b_tables(
    table_id: str,
    b_tables: List[Dict[str, Any]],
    b_order: List[int],
    page_height_pt: Optional[float],
) -> Optional[float]:
    tid = str(table_id or "")
    base = _base_table_id(tid)
    for bi in b_order:
        if bi < 0 or bi >= len(b_tables):
            continue
        bt = b_tables[bi]
        btid = str(bt.get("table_id") or "")
        if btid in (tid, base):
            return _bbox_top_for_sort_y(bt.get("bbox"), page_height_pt)
    return None


def _visual_stream_sort_key(entry: Dict[str, Any]) -> tuple:
    sy = _sort_y_value(entry)
    tie = int(entry.get("tie", 0))
    return (1 if sy is None else 0, sy if sy is not None else 1.0, tie)


def _interleave_prose_and_tables(
    prose_rows: List[Dict[str, Any]],
    table_entries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """地の文と表を sort_y で混在（欠損 y は末尾寄せ）。"""
    pieces: List[Dict[str, Any]] = []
    for r in prose_rows:
        sy = r.get("sort_y")
        pieces.append(
            {
                "k": "p",
                "missing": sy is None,
                "y": float(sy) if sy is not None else 1.0,
                "s": float(r.get("x0", 0) or 0) * 1e-9,
                "o": int(r.get("order", 0)),
                "text": r["text"],
            }
        )
    for j, te in enumerate(table_entries):
        sy = te.get("sort_y")
        pieces.append(
            {
                "k": "t",
                "missing": sy is None,
                "y": float(sy) if sy is not None else 1.0,
                "s": 0.0,
                "o": 10_000 + j,
                "entry": dict(te),
            }
        )
    pieces.sort(key=lambda z: (z["missing"], z["y"], z["s"], z["o"]))
    return pieces


def _collect_table_visual_entries(
    d_tables: List[Dict[str, Any]],
    n_d: int,
    g_tables: List[Dict[str, Any]],
    *,
    b_tables: Optional[List[Dict[str, Any]]] = None,
    page_height_pt: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    表ストリーム要素（kind / sort_y / table_index(s)）を D 正規化 bbox 順で列挙。

    D 表 bbox が無い場合は Stage B structured_tables の bbox を使う（pt→正規化）。
    それも無いときだけ sort_y を付けない。tie は未設定。
    """
    by_d: Dict[int, List[int]] = {}
    if n_d <= 0:
        idxs = list(range(len(g_tables)))
        out: List[Dict[str, Any]] = []
        b_sorted: List[tuple[float, int]] = []
        for bi, bt in enumerate(b_tables or []):
            sy = _bbox_top_for_sort_y(bt.get('bbox'), page_height_pt)
            if sy is not None:
                b_sorted.append((sy, bi))
        b_sorted.sort(key=lambda x: (x[0], x[1]))
        b_order = [bi for _, bi in b_sorted]

        y_slot_count: Dict[str, int] = {}

        def _sort_y_for_g_index(ti: int) -> Optional[float]:
            if not (0 <= ti < len(g_tables)):
                return None
            gt = g_tables[ti]
            meta = gt.get("metadata") if isinstance(gt.get("metadata"), dict) else {}
            if meta.get("bbox"):
                sy = _bbox_top_for_sort_y(meta.get("bbox"), page_height_pt)
                if sy is not None:
                    return sy
            tid = str(gt.get("table_id") or "")
            return _bbox_sort_y_from_b_tables(tid, b_tables or [], b_order, page_height_pt)

        def _slot_sort_y(sy: float) -> float:
            key = f"{sy:.8f}"
            y_slot_count[key] = y_slot_count.get(key, 0) + 1
            return sy + 1e-6 * y_slot_count[key]

        if _merge_same_shape_indices(g_tables, idxs):
            sy = _sort_y_for_g_index(idxs[0]) if idxs else None
            row: Dict[str, Any] = {
                'kind': 'g_table_group',
                'table_indices': idxs,
            }
            if sy is not None:
                row['sort_y'] = _slot_sort_y(sy)
            else:
                row['sort_y'] = None
                row['position_contract'] = 'missing_d_table_bbox'
            out.append(row)
        else:
            for ti in idxs:
                sy = _sort_y_for_g_index(ti)
                row = {
                    'kind': 'g_table',
                    'table_index': ti,
                }
                if sy is not None:
                    row['sort_y'] = _slot_sort_y(sy)
                else:
                    row['sort_y'] = None
                    row['position_contract'] = 'missing_d_table_bbox'
                out.append(row)
        return out

    n_g = len(g_tables)
    for ti in range(n_g):
        if n_d == 1:
            di = 0
        else:
            di = min(ti * n_d // max(n_g, 1), n_d - 1)
        by_d.setdefault(di, []).append(ti)

    out = []
    d_indices = list(range(n_d))
    d_indices.sort(
        key=lambda i: (
            _bbox_top_norm(d_tables[i].get('bbox')) is None,
            _bbox_top_norm(d_tables[i].get('bbox')) if _bbox_top_norm(d_tables[i].get('bbox')) is not None else 1.0,
            i,
        )
    )
    for di in d_indices:
        dt = d_tables[di]
        metrics = _bbox_y_metrics_norm(dt.get('bbox'))
        g_idx_list = by_d.get(di, [])
        if not g_idx_list:
            continue
        if metrics:
            y_top, y_bot, _ = metrics
        else:
            y_top = y_bot = None
        if _merge_same_shape_indices(g_tables, g_idx_list):
            row: Dict[str, Any] = {
                'kind': 'g_table_group',
                'table_indices': list(g_idx_list),
            }
            if y_top is not None:
                row['sort_y'] = y_top + 0.00003
            else:
                row['sort_y'] = None
                row['position_contract'] = 'missing_d_table_bbox'
            out.append(row)
        else:
            if y_top is None or y_bot is None:
                for ti in g_idx_list:
                    out.append({
                        'kind': 'g_table',
                        'sort_y': None,
                        'position_contract': 'missing_d_table_bbox',
                        'table_index': ti,
                    })
                continue
            span = max(y_bot - y_top, 1e-4)
            n_sub = len(g_idx_list)
            for local_k, ti in enumerate(g_idx_list):
                if n_sub <= 1:
                    y_slot = y_top + 1e-6 * (local_k + 1)
                else:
                    y_slot = y_top + span * (local_k / (n_sub - 1)) * 0.999 + 1e-6 * (local_k + 1)
                out.append({
                    'kind': 'g_table',
                    'sort_y': y_slot,
                    'table_index': ti,
                })
    return out


def _visual_stream_from_f17_reading_stream(
    reading_stream: List[Dict[str, Any]],
    ui_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """F17 ``reading_stream``（table_ref）を Visual Editor 用 ``g_table`` 行に展開する。"""
    g_tables = list((ui_data or {}).get('tables') or [])
    tid_to_index: Dict[str, int] = {}
    for i, gt in enumerate(g_tables):
        if not isinstance(gt, dict):
            continue
        tid = str(gt.get('table_id') or '').strip()
        if tid and tid not in tid_to_index:
            tid_to_index[tid] = i

    stream: List[Dict[str, Any]] = []
    seq = 0
    for item in reading_stream:
        if not isinstance(item, dict):
            continue
        kind = item.get('kind')
        if kind == 'non_table_paragraph':
            row = dict(item)
            row['tie'] = seq
            stream.append(row)
            seq += 1
        elif kind == 'table_ref':
            tid = str(item.get('table_id') or '').strip()
            if not tid:
                continue
            indices = [i for i, gt in enumerate(g_tables) if str(gt.get('table_id') or '') == tid]
            if not indices:
                indices = sorted(
                    i
                    for i, gt in enumerate(g_tables)
                    if str(gt.get('table_id') or '').startswith(f'{tid}_')
                )
            if not indices:
                continue
            base: Dict[str, Any] = {'tie': seq}
            if item.get('sort_y') is not None:
                base['sort_y'] = item['sort_y']
            elif item.get('position_contract'):
                base['position_contract'] = item['position_contract']
            if len(indices) == 1:
                stream.append({'kind': 'g_table', 'table_index': indices[0], **base})
            else:
                stream.append(
                    {
                        'kind': 'g_table_group',
                        'table_indices': indices,
                        **base,
                    }
                )
            seq += 1
    return stream


def _build_visual_stream(
    d_result: Dict[str, Any],
    stage_d_tables: List[Dict[str, Any]],
    ui_data: Dict[str, Any],
    stage_e_result: Dict[str, Any],
    stage_f_result: Optional[Dict[str, Any]] = None,
    stage_b_result: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Visual Editor 用: 非表テキストと ``ui_data.tables`` を縦方向に並べる。

    ``stage_f_result.reading_stream``（F17 読み順正本）があるときはそれを ``g_table`` に展開する。
    無いときは ``non_table_text_blocks`` または E ブロック / sections で従来どおり構築する。
    """
    f17_stream = list((stage_f_result or {}).get('reading_stream') or [])
    if f17_stream and (ui_data or {}).get('tables'):
        positioned = sum(1 for e in f17_stream if e.get('sort_y') is not None)
        if positioned >= max(1, len(f17_stream) // 2):
            return _visual_stream_from_f17_reading_stream(f17_stream, ui_data)
        loguru_logger.warning(
            f'[pipeline-lab] reading_stream sort_y 不足 ({positioned}/{len(f17_stream)}) '
            '→ F1+B bbox 合成 visual_stream を使用'
        )

    stream: List[Dict[str, Any]] = []
    seq = 0

    d_tables = list((d_result or {}).get('tables') or [])
    n_d = len(d_tables)
    g_tables = list((ui_data or {}).get('tables') or [])

    ntc = (stage_e_result or {}).get('non_table_content') or {}
    blocks = ntc.get('blocks') if isinstance(ntc, dict) else None
    if not isinstance(blocks, list):
        blocks = []

    articles = list((ui_data or {}).get('g21_articles') or [])
    has_g21_body = any(str(a.get('body') or '').strip() for a in articles if isinstance(a, dict))

    nt_blocks = list((stage_f_result or {}).get('non_table_text_blocks') or [])
    use_f1_interleave = bool(has_g21_body and nt_blocks)

    min_d_y: Optional[float] = None
    if n_d > 0:
        _dys = [_bbox_top_norm(x.get('bbox')) for x in d_tables]
        _ok = [y for y in _dys if y is not None]
        if _ok:
            min_d_y = min(_ok)

    ph = _page_height_pt_for_visual_stream(stage_b_result, d_result)
    table_entries = _collect_table_visual_entries(
        d_tables,
        n_d,
        g_tables,
        b_tables=_stage_b_tables_for_sort(stage_b_result),
        page_height_pt=ph,
    )

    if use_f1_interleave:
        prose_rows: List[Dict[str, Any]] = []
        for i, ob in enumerate(nt_blocks):
            t = (ob.get('text') or '').strip()
            if not t:
                continue
            sy = _normalize_f1_block_sort_y(
                float(ob.get('y0', 0) or 0.0),
                ph if ph is not None else 0.0,
            )
            prose_rows.append({
                'text': t,
                'sort_y': sy,
                'x0': float(ob.get('x0', 0) or 0.0),
                'order': i,
            })

        pieces = _interleave_prose_and_tables(prose_rows, table_entries)
        for z in pieces:
            if z['k'] == 'p':
                row = {
                    'kind': 'non_table_paragraph',
                    'tie': seq,
                    'text': z['text'],
                    'source': 'f1_block',
                }
                if not z['missing']:
                    row['sort_y'] = z['y']
                else:
                    row['position_contract'] = 'missing_f1_y0'
                stream.append(row)
                seq += 1
            else:
                ent = dict(z['entry'])
                if not z['missing']:
                    ent['sort_y'] = z['y']
                ent['tie'] = seq
                stream.append(ent)
                seq += 1
    else:
        if not has_g21_body:
            for blk in blocks:
                if not isinstance(blk, dict):
                    continue
                text = (blk.get('text') or '').strip()
                if not text:
                    continue
                y = _bbox_top_norm(blk.get('bbox'))
                if y is None:
                    y = 0.08 + seq * 0.0004
                stream.append({
                    'kind': 'non_table_paragraph',
                    'sort_y': y,
                    'tie': seq,
                    'text': text,
                })
                seq += 1

        had_e_block_paragraphs = any(s.get('kind') == 'non_table_paragraph' for s in stream)
        if not had_e_block_paragraphs and not has_g21_body:
            for sec in (ui_data or {}).get('sections') or []:
                if not isinstance(sec, dict) or sec.get('type') != 'text':
                    continue
                body = sec.get('content')
                if not isinstance(body, str) or not body.strip():
                    continue
                try:
                    ord_v = float(sec.get('display_order', 100))
                except (TypeError, ValueError):
                    ord_v = 100.0
                stream.append({
                    'kind': 'non_table_paragraph',
                    'sort_y': 0.06 + min(ord_v, 500.0) * 0.0001,
                    'tie': seq,
                    'text': body.strip(),
                    'source': 'g3_section',
                })
                seq += 1

        if has_g21_body:
            y_g21 = max(0.02, (min_d_y - 0.015) if min_d_y is not None else 0.08)
            for ai, _ in enumerate(articles):
                if not isinstance(articles[ai], dict):
                    continue
                if not str((articles[ai].get('body') or '')).strip():
                    continue
                art = articles[ai]
                title = str(art.get('title') or '').strip()
                body = str(art.get('body') or '').strip()
                stream.append({
                    'kind': 'g21_article',
                    'sort_y': y_g21 + ai * 0.00005,
                    'tie': seq,
                    'article_index': ai,
                    'title': title,
                    'body': body,
                })
                seq += 1

        for te in table_entries:
            row = dict(te)
            row['tie'] = seq
            stream.append(row)
            seq += 1

        base_last = _max_stream_sort_y(stream, 0.35)
        if not has_g21_body:
            articles_tail = list((ui_data or {}).get('g21_articles') or [])
            for ai, art in enumerate(articles_tail):
                if not isinstance(art, dict):
                    continue
                title = str(art.get('title') or '').strip()
                body = str(art.get('body') or '').strip()
                if not title and not body:
                    continue
                stream.append({
                    'kind': 'g21_article',
                    'sort_y': base_last + 0.04 + ai * 0.001,
                    'tie': seq,
                    'article_index': ai,
                    'title': title,
                    'body': body,
                })
                seq += 1

    tail_y = _max_stream_sort_y(stream, 0.5) + 0.05
    if (ui_data or {}).get('timeline'):
        stream.append({'kind': 'timeline_tail', 'sort_y': tail_y, 'tie': seq})
        seq += 1
        tail_y += 0.0002
    if (ui_data or {}).get('actions'):
        stream.append({'kind': 'actions_tail', 'sort_y': tail_y, 'tie': seq})
        seq += 1
        tail_y += 0.0002
    if (ui_data or {}).get('notices'):
        stream.append({'kind': 'notices_tail', 'sort_y': tail_y, 'tie': seq})
        seq += 1
        tail_y += 0.0002

    stream.sort(key=_visual_stream_sort_key)
    return stream


_PIPELINE_LOG_MAX_RESPONSE_CHARS = 400_000


def _read_text_tail(path: Path, max_chars: int) -> tuple[str, bool]:
    if not path.is_file():
        return '', False
    try:
        txt = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return '', False
    if len(txt) <= max_chars:
        return txt, False
    omitted = len(txt) - max_chars
    return f'… 先頭 {omitted} 文字を省略 …\n' + txt[-max_chars:], True


def _attach_table_chain_stage_logs(result: Dict[str, Any], work_dir: Path, page_num: int) -> None:
    """F-57 / F-58 専用ログファイルを API 応答に載せ、page_results にもコピーする。"""
    p56 = work_dir / F46_LOG_NAME
    p57 = work_dir / F47_LOG_NAME
    t56, tr56 = _read_text_tail(p56, _PIPELINE_LOG_MAX_RESPONSE_CHARS)
    t57, tr57 = _read_text_tail(p57, _PIPELINE_LOG_MAX_RESPONSE_CHARS)
    result['f46_log'] = t56
    result['f46_log_truncated'] = tr56
    result['f47_log'] = t57
    result['f47_log_truncated'] = tr57
    out_dir = work_dir.parent / 'page_results'
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        for fname in (F46_LOG_NAME, F47_LOG_NAME):
            src = work_dir / fname
            if src.is_file():
                (out_dir / f'{page_num}_{fname}').write_bytes(src.read_bytes())
    except OSError:
        pass


def _run_pdf_pipeline_stages(pdf_path: Path, work_dir: Path, session_id: str, page_num: int, g26_model_name: Optional[str] = None) -> Dict[str, Any]:
    from dms.pipeline.stage_a import A3EntryPoint
    from dms.pipeline.stage_b import B1Controller
    from dms.pipeline.stage_d import D1Controller
    from dms.pipeline.stage_e import E1Controller
    from dms.pipeline.stage_f import F1Controller
    from dms.pipeline.stage_g import G11Controller

    work_dir.mkdir(parents=True, exist_ok=True)

    slice_pdf = _lab_pipeline_input_pdf(pdf_path, work_dir, page_num)

    stage_a = A3EntryPoint()
    stage_b = B1Controller()
    stage_d = D1Controller()
    stage_e = E1Controller()
    stage_f = F1Controller()

    stem = pdf_path.stem
    rawdata_record = {
        'display_subject': stem,
        'display_post_text': '',
        'display_sender': '',
        'display_sent_at': None,
        'doc_type': 'pipeline-lab',
        'person': None,
        'organizations': None,
        'title': stem,
        'file_name': pdf_path.name,
    }

    stage_a_result = stage_a.process(str(slice_pdf))
    if not stage_a_result or not stage_a_result.get('success'):
        return {
            'success': False,
            'error': (stage_a_result or {}).get('error') or 'Stage A 失敗',
            'stage': 'A',
        }

    if slice_pdf != pdf_path:
        stage_a_result['process_pdf_source_page_index'] = page_num
        stage_a_result['process_pdf_job_scope'] = 'pipeline_lab_one_pdf_per_job'

    stage_b_result = stage_b.process(
        file_path=str(slice_pdf),
        a_result=stage_a_result,
        log_dir=str(work_dir / 'stage_b_logs'),
    )
    if not stage_b_result or not stage_b_result.get('success'):
        return {
            'success': False,
            'error': (stage_b_result or {}).get('error') or 'Stage B 失敗',
            'stage': 'B',
        }

    stage_b_result['source_pdf_path'] = str(slice_pdf)

    purged = stage_b_result.get('purged_pdf_path')
    if not purged:
        return {'success': False, 'error': 'purged_pdf_path がありません', 'stage': 'B'}

    page_output_dir = work_dir / f'page_{page_num}'
    page_output_dir.mkdir(parents=True, exist_ok=True)
    # purged は lab の実行モデル上「1 本の入力 PDF」に対応する 1 ファイル。D1 の page_num は既定のまま（lab が PDF 内ページを持たない）。
    d_result = stage_d.process(
        pdf_path=Path(purged),
        purged_image_path=None,
        output_dir=page_output_dir,
    )
    if not d_result or not d_result.get('success'):
        return {
            'success': False,
            'error': (d_result or {}).get('error') or 'Stage D 失敗',
            'stage': 'D',
        }

    tbls = d_result.get('tables') or []
    d_summary = {
        'success': True,
        'tables_count': len(tbls),
        'non_table_image': bool(d_result.get('non_table_image_path')),
    }
    session_root = work_dir.parent
    stage_d_tables = _stage_d_table_previews(session_root, session_id, d_result)

    stage_d_merged = _wrap_single_d_result(d_result)

    stage_e_result = stage_e.process(
        purged_pdf_path=purged,
        stage_d_result=stage_d_merged,
        output_dir=str(work_dir),
        stage_b_result=stage_b_result,
        session_id=session_id,
    )
    if not stage_e_result or not stage_e_result.get('success'):
        return {
            'success': False,
            'error': (stage_e_result or {}).get('error') or 'Stage E 失敗',
            'stage': 'E',
        }

    blocks = (stage_e_result.get('non_table_content') or {}).get('blocks') or []
    texts: List[str] = []
    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        t = (blk.get('text') or '').strip()
        if t:
            texts.append(t)
    body_join = '\n\n'.join(texts)

    stage_f_result = stage_f.process(
        stage_a_result=stage_a_result,
        stage_b_result=stage_b_result,
        stage_d_result=stage_d_merged,
        stage_e_result=stage_e_result,
        rawdata_record=rawdata_record,
        session_id=session_id,
    )
    if not stage_f_result or not stage_f_result.get('success'):
        return {
            'success': False,
            'error': (stage_f_result or {}).get('error') or 'Stage F 失敗',
            'stage': 'F',
        }

    # Raw MD / 文字数は F1 統合の non_table_text（B+E）を正とする。E の blocks のみではヘッダ等が欠ける。
    non_table_plain = (stage_f_result.get('non_table_text') or '').strip()
    loguru_logger.info(
        f"[pipeline-lab] non_table_plain_len={len(non_table_plain)} "
        f"stage_e_blocks_plain_len={len(body_join)}"
    )

    g11 = G11Controller(document_id=session_id, g26_model_name=g26_model_name)
    g11_result = g11.process(stage_f_result=stage_f_result, log_dir=work_dir)
    if not g11_result or not g11_result.get('success'):
        return {
            'success': False,
            'error': (g11_result or {}).get('error') or 'Stage G（G11 UI）失敗',
            'stage': 'G',
        }

    ui_data = g11_result.get('ui_data') or {}
    final_meta = g11_result.get('final_metadata') or {}
    g22 = final_meta.get('g22_output') or {}
    tables_flat = ui_data.get('tables') or []

    try:
        visual_stream = _build_visual_stream(
            d_result,
            stage_d_tables,
            ui_data,
            stage_e_result,
            stage_f_result,
            stage_b_result,
        )
    except Exception as e:
        loguru_logger.warning(f'[pipeline-lab] visual_stream 生成に失敗（空で続行）: {e}')
        visual_stream = []

    return {
        'success': True,
        'session_id': session_id,
        'page_index': page_num,
        'stage_a': {
            'origin_app': stage_a_result.get('origin_app'),
            'layout_profile': stage_a_result.get('layout_profile'),
            'confidence': stage_a_result.get('confidence'),
            'document_type': stage_a_result.get('document_type'),
            'reason': stage_a_result.get('reason'),
            'page_type_map': stage_a_result.get('page_type_map'),
            'page_confidence_map': stage_a_result.get('page_confidence_map'),
            'meta_match_detail': stage_a_result.get('meta_match_detail'),
            'page_font_detail': stage_a_result.get('page_font_detail'),
        },
        'stage_d': d_summary,
        'stage_d_detail': _json_safe(d_result),
        'stage_d_tables': stage_d_tables,
        'reading': {
            'non_table_chars': len(non_table_plain),
            # キー名は後方互換のため維持。値は Stage F 出口の non_table_text（F1 B+E 統合本文）のみ。
            'stage_e_non_table_plain': non_table_plain,
            'stage_e_blocks_plain': body_join,
            'stage_d_table_count': len(tbls),
            'g_ui_table_count': len(tables_flat),
            'visual_stream': visual_stream,
            'non_table_text_blocks': stage_f_result.get('non_table_text_blocks') or [],
            'f1_text_merge': (stage_f_result.get('metadata') or {}).get('f1_text_merge'),
            'f_non_table_text_chars': len((stage_f_result.get('non_table_text') or '')),
            'tables_md_embed_chars': len(str((ui_data.get('tables_md_embed') or ''))),
        },
        'ui_data_summary': {
            'sections_count': len(ui_data.get('sections') or []),
            'g21_articles_count': len(ui_data.get('g21_articles') or []),
            'tables_count': len(tables_flat),
            'table_ids': [str(t.get('table_id') or '') for t in tables_flat],
            'g36_rebuild': [
                f"{st.get('table_id')}:{(st.get('metadata') or {}).get('vertical_merge_judge')}"
                for st in (ui_data.get('g11_structured_tables') or [])
                if (st.get('metadata') or {}).get('lr_rebuilt')
            ],
            'tables_md_embed_chars': len(str((ui_data.get('tables_md_embed') or ''))),
            'timeline_count': len(ui_data.get('timeline') or []),
            'actions_count': len(ui_data.get('actions') or []),
            'notices_count': len(ui_data.get('notices') or []),
        },
        'g22_summary': {
            'people': g22.get('people') or [],
            'calendar_events_count': len(g22.get('calendar_events') or []),
            'tasks_count': len(g22.get('tasks') or []),
            'notices_count': len(g22.get('notices') or []),
        },
        'ui_data_json': ui_data,
        'final_metadata_json': final_meta,
        'block_starts': _detect_block_starts(non_table_plain),
    }


def _detect_block_starts(non_table_plain: str) -> List[str]:
    """Gemini 2.5 Flash-lite で非表テキストの内容ブロック境界（各ブロック先頭行）を検出する。"""
    if not non_table_plain.strip():
        return []
    try:
        import google.generativeai as genai
        from dms.common.config.settings import settings
        if not settings.GOOGLE_AI_API_KEY:
            return []
        genai.configure(api_key=settings.GOOGLE_AI_API_KEY)
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        prompt = (
            "以下のテキストを、内容のかたまり（トピック）ごとに分割してください。\n"
            "各かたまりの先頭行（最初の1行）のみを1行ずつ出力してください。\n"
            "かたまりが1つだけの場合は「なし」とだけ出力してください。\n\n"
            f"テキスト:\n{non_table_plain}"
        )
        resp = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(max_output_tokens=300, temperature=0.0),
            request_options={"timeout": 60},
        )
        text = resp.text.strip()
        if not text or text == "なし":
            return []
        return [line.strip() for line in text.split("\n") if line.strip() and line.strip() != "なし"]
    except Exception as e:
        loguru_logger.warning(f"[pipeline-lab] block detection failed: {e}")
        return []


def _run_pdf_pipeline(pdf_path: Path, work_dir: Path, session_id: str, page_num: int, g26_model_name: Optional[str] = None) -> Dict[str, Any]:
    """loguru（dms.pipeline 名前空間）をバッファに取り込みつつステージ実行。絶対に例外を外に漏らさない。"""
    from dms.common.ai_cost_logger import start_cost_accumulation, stop_cost_accumulation
    start_cost_accumulation()
    buf = StringIO()
    fmt = (
        '{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} | {message}\n'
    )

    def _pipeline_modules_only(record: Any) -> bool:
        n = str(record['name'])
        return n.startswith('dms.pipeline')

    # handler_id が None のままでも finally が NameError にならないよう先に宣言
    handler_id: Optional[int] = None
    try:
        handler_id = loguru_logger.add(
            buf,
            format=fmt,
            level='DEBUG',
            filter=_pipeline_modules_only,
            colorize=False,
        )
    except Exception as _add_err:
        # add() 失敗は極めてまれだが、失敗してもパイプラインは動かす（ログは空になる）
        print(f'[pipeline-lab] loguru.add 失敗: {_add_err}', flush=True)

    result: Dict[str, Any] = {'success': False, 'error': 'internal', 'stage': 'exception'}
    try:
        try:
            result = _run_pdf_pipeline_stages(pdf_path, work_dir, session_id, page_num, g26_model_name=g26_model_name)
        except BaseException as e:
            # BaseException まで受ける: KeyboardInterrupt / SystemExit がパイプライン内から来ても
            # 500 + 空ログにならないよう result に格納してから finally へ
            result = {'success': False, 'error': str(e), 'stage': 'exception'}
            print(f'[pipeline-lab] pipeline exception ({type(e).__name__}): {e}', flush=True)
    finally:
        if handler_id is not None:
            try:
                loguru_logger.remove(handler_id)
            except Exception:
                pass

    full_log = buf.getvalue()
    # サーバーコンソールへ診断出力（空ログ問題の原因特定用）
    print(
        f'[pipeline-lab] pipeline完了 success={result.get("success")} '
        f'log_chars={len(full_log)} error={result.get("error", "")!r:.120}',
        flush=True,
    )

    if not isinstance(result, dict):
        result = {'success': False, 'error': str(result), 'stage': 'unknown'}

    result['pipeline_log'] = full_log
    if len(full_log) > _PIPELINE_LOG_MAX_RESPONSE_CHARS:
        result['pipeline_log_truncated'] = True
        result['pipeline_log'] = (
            f'… 先頭 {len(full_log) - _PIPELINE_LOG_MAX_RESPONSE_CHARS} 文字を省略 …\n'
            + full_log[-_PIPELINE_LOG_MAX_RESPONSE_CHARS:]
        )
    else:
        result['pipeline_log_truncated'] = False

    # セッションに全文ログを保存（省略なし）
    try:
        log_path = work_dir.parent / 'page_results' / f'{page_num}_pipeline.log'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(full_log, encoding='utf-8')
    except OSError:
        pass

    _attach_table_chain_stage_logs(result, work_dir, page_num)

    result['ai_cost'] = _calc_ai_cost(stop_cost_accumulation())
    return result


@lab_bp.route('/')
def index():
    return render_template('pipeline_lab.html')


@lab_bp.route('/api/health', methods=['GET'])
def api_health():
    """ブラウザ到達確認用。fetch が失敗する原因切り分け（未起動・別ポート等）に使う。"""
    return jsonify({'ok': True})


@lab_bp.route('/files/<session_id>/<path:rel_path>')
def session_file(session_id: str, rel_path: str):
    base = _safe_session_dir(session_id)
    if not base:
        return jsonify({'error': 'not found'}), 404
    directory = (base / Path(rel_path).parent).resolve()
    fname = Path(rel_path).name
    root = base.resolve()
    if not str(directory).startswith(str(root)):
        return jsonify({'error': 'invalid path'}), 400
    return send_from_directory(directory, fname, conditional=True)


@lab_bp.route('/api/upload', methods=['POST'])
def api_upload():
    """PDF または画像（PNG/JPG/GIF/WebP/TIFF）1 ファイルを受け付ける。"""
    uploaded = []
    for key in ('pdf_file', 'file'):
        if key not in request.files:
            continue
        uploaded.extend([fh for fh in request.files.getlist(key) if fh and fh.filename])
    if len(uploaded) > 1:
        return jsonify({'success': False, 'error': 'ファイルは 1 つだけ指定してください'}), 400
    if len(uploaded) != 1:
        return jsonify({'success': False, 'error': 'file が必要です'}), 400
    f = uploaded[0]
    fname_lower = f.filename.lower()
    is_img = _is_image(f.filename)
    if not fname_lower.endswith('.pdf') and not is_img:
        return jsonify({'success': False, 'error': 'PDF または画像ファイル（PNG/JPG/GIF/WebP/TIFF）のみ対応しています'}), 400

    session_id = uuid.uuid4().hex[:12]
    session_dir = _sessions_root() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    safe_name = secure_filename(f.filename) or ('upload.png' if is_img else 'upload.pdf')
    pdf_path = session_dir / 'input.pdf'

    try:
        if is_img:
            img_path = session_dir / safe_name
            f.save(str(img_path))
            _image_to_pdf(img_path, pdf_path)
            (session_dir / 'is_image.txt').write_text('1', encoding='utf-8')
        else:
            f.save(str(pdf_path))

        n = _pdf_page_count(pdf_path)
        if n < 1:
            shutil.rmtree(session_dir, ignore_errors=True)
            return jsonify({'success': False, 'error': 'ページがありません'}), 400
        pages = _render_pdf_previews(pdf_path, session_dir, session_id)
    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        return jsonify({'success': False, 'error': f'読込エラー: {e}'}), 400

    return jsonify({
        'success': True,
        'session_id': session_id,
        'file_id': session_id,
        'safe_filename': safe_name,
        'filename': safe_name,
        'page_count': n,
        'pages': pages,
        'is_image': is_img,
    })


@lab_bp.route('/api/run/<session_id>/<int:page_index>', methods=['POST'])
def api_run(session_id: str, page_index: int):
    """ページ単位の実行指示。キュー投入ではなく、このリクエスト内でパイプラインを完走させる。"""
    base = _safe_session_dir(session_id)
    if not base:
        return jsonify({'success': False, 'error': 'セッション不明'}), 404
    pdf_path = base / 'input.pdf'
    if not pdf_path.is_file():
        return jsonify({'success': False, 'error': 'input.pdf がありません'}), 400

    try:
        total_pages = _pdf_page_count(pdf_path)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    if page_index < 0 or page_index >= total_pages:
        return jsonify({
            'success': False,
            'error': f'page_index は 0〜{total_pages - 1} の範囲で指定してください',
        }), 400

    body = request.get_json(silent=True) or {}
    g26_model_name = (body.get('g26_model') or '').strip() or None

    work_dir = base / 'interactive_run'
    try:
        result = _run_pdf_pipeline(pdf_path, work_dir, session_id, page_index, g26_model_name=g26_model_name)
    except Exception as e:
        # _run_pdf_pipeline 内でステージ例外は握りつぶして dict 返却する想定。ここは防護壁のみ。
        return jsonify({
            'success': False,
            'error': str(e),
            'stage': 'exception',
            'pipeline_log': '',
            'f46_log': '',
            'f47_log': '',
        }), 500

    out_json = base / 'last_result.json'
    page_results_dir = base / 'page_results'
    try:
        payload_txt = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        out_json.write_text(payload_txt, encoding='utf-8')
        page_results_dir.mkdir(parents=True, exist_ok=True)
        (page_results_dir / f'{page_index}.json').write_text(payload_txt, encoding='utf-8')
    except (OSError, TypeError):
        pass

    resp = dict(result)
    if resp.get('success'):
        # ui_data / final_metadata は POST でも返す（省略するとクライアントが Raw MD・表描画に必要な
        # ui_data_json を持たず、二重 fetch 失敗時に画面が空になる）。
        resp['has_full_ui_data'] = bool(resp.get('ui_data_json'))
        resp['has_full_final_metadata'] = bool(resp.get('final_metadata_json'))
        resp['saved_result_path'] = str(out_json.relative_to(base)) if out_json.exists() else None

    return jsonify(resp)


@lab_bp.route('/api/result/<session_id>/<int:page_index>', methods=['GET'])
def api_page_full_result(session_id: str, page_index: int):
    """当該ページを実行したときに保存したフル JSON。"""
    base = _safe_session_dir(session_id)
    if not base:
        return jsonify({'error': 'not found'}), 404
    p = base / 'page_results' / f'{page_index}.json'
    if not p.is_file():
        return jsonify({'error': 'このページの実行結果がありません'}), 404
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return jsonify({'error': '結果 JSON が壊れています'}), 500
    return jsonify(data)


@lab_bp.route('/api/result/<session_id>', methods=['GET'])
def api_full_result(session_id: str):
    """同一セッションで最後に実行した結果のコピー（参照用）。キューの「最新ジョブ」ではない。"""
    base = _safe_session_dir(session_id)
    if not base:
        return jsonify({'error': 'not found'}), 404
    p = base / 'last_result.json'
    if not p.is_file():
        return jsonify({'error': '実行結果がありません'}), 404
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return jsonify({'error': '結果 JSON が壊れています'}), 500
    return jsonify(data)


# ---------------------------------------------------------------------------
# AI直接抽出（Gemini に画像を渡して MD を得る）
# ---------------------------------------------------------------------------

_DIRECT_EXTRACT_PROMPT = """
あなたは高度なOCRおよびデータ抽出システムです。
提供された画像からすべての情報を抽出し、**以下の厳密な出力形式**で返してください。
「一列のズレ、一行の結合も許さない」という極めて厳格な姿勢で臨んでください。

【思考プロセス（重要）】
正確な抽出のために、以下の手順を厳守してください。

1. **垂直境界（列のコンテナ）の定義**:
   まずヘッダー行を精査し、各列の水平方向の開始位置と終了位置を確定させてください。これを「列のコンテナ」と呼びます。
2. **座標ベースの列割り当て**:
   すべてのテキストブロックについて、その水平方向の中心座標を計算し、それがどの「列のコンテナ」に属するかを物理的に判定してください。
   **重要：データがない列を飛ばして左に詰めることは「データ改ざん」とみなし、絶対に禁止します。**
3. **垂直スキャンによる検算**:
   各列のヘッダーから下方向へ垂直に視線を走らせ、その「コンテナ」の中にデータが正しく縦一列に並んでいるかを確認してください。
4. **行の解体（結合セルの排除）**:
   画像上で上下に並んでいるデータは、一つのセルにまとめず、必ず**独立した複数の行**として書き出してください。結合セルによって省略されている情報は、すべての行に繰り返し入力してください。

【抽出ルール】
1. **1データ1行の徹底**: セル内で改行して複数のクラスや時間を詰め込まないでください。行を分けて出力してください。
2. **空セルの厳格維持**: データが存在しない列は、必ず `| |` （半角スペース一つ）を入れて列のカウントを維持してください。
3. **結合セルの完全展開**: 縦または横に結合されたセル（学年、校舎など）は、その範囲に含まれる**すべての行・列にその値をコピー**して出力してください。
4. **言語**: 日本語のまま抽出してください。
5. **AI の説明不要**: 「抽出しました」等の説明文は一切省いてください。
6. **第1列ヘッダーは必ず `header` と記述**: 表の第1列のヘッダーセルは、画像上の表ラベルや列名に関わらず、必ず `header` と書いてください。`【小学校】` などのシート名を第1列ヘッダーにしてはいけません。
7. **表の外にある見出し・シート名は非表テキストに記述**: 表本体の外側に書かれたシート名・セクション見出しは表のヘッダーセルにしないで、`## 非表（F 地の文）` セクションに記述してください。
8. **縦結合された行カテゴリラベルは第1列に展開**: 縦方向に結合されているカテゴリ・分類セル（行グループの親ラベル）は、その列のすべての対象行に繰り返しコピーしてください。列の位置をずらして隣の列に書いてはいけません。
9. **各表の直後に説明を1行**: `## T1` の次の行に `> （この表が何のための表かを20〜40文字で説明）` を必ず書いてください。表全体を見渡した上で「何の表か」を端的に記述してください。
10. **非表テキストの構造化と段落分割**: 表の外にあるタイトル、見出し、箇条書き、地の文などは、論理的なブロック（段落・セクション）ごとに改行で適切に分割してください。また、見出しには `#` や `##`、箇条書きには `-`、注記等には `>` などのマークダウン構造化記号を適切に付与してください。単に平文テキストをベタ書きするのではなく、構造化されたマークダウンとして記述してください。

【出力形式（必須・厳守）】
以下の2セクション構成で出力してください。セクション名は一字一句変えないでください。

---

## 非表（F 地の文）

（表以外のタイトル・見出し・注釈・フッター・地の文を、論理的なブロックや段落ごとに適切に分割し、見出しには「#」や「##」、箇条書きには「-」、注記等には「>」などのマークダウン構造化記号を付与して記述。表の外にあるすべてのテキストを漏らさず含める。）

## 表（ui_data.tables）

（表ごとに `## T1`, `## T2`, ... と見出しを付け、その直後に `> 説明` を1行書いてからマークダウン表を記述。表が複数ある場合は順番に並める。表がない場合はこのセクションごと省略。）

---"""


def _cell_to_yaml_item(cell: str) -> str:
    """YAML cells リストアイテム（4スペースインデント）。純整数はクォートして文字列扱い。"""
    import re as _r
    prefix = '    - '
    if not cell:
        return prefix + "''"
    if _r.match(r'^\d+$', cell.strip()):
        return f"{prefix}'{cell.strip()}'"
    return f"{prefix}{cell}"


def _infer_table_semantics(headers: List[str], rows: List[List[str]]) -> Dict[str, Any]:
    """表内容から table_semantics を推定する。"""
    financial_kw = {'収入', '支出', '決算', '予算', '繰越', '合計', '収支', '会費'}
    all_text = ' '.join(str(h) for h in headers) + ' ' + ' '.join(
        str(c) for row in rows for c in row
    )
    if any(kw in all_text for kw in financial_kw):
        return {
            'type': 'financial_report',
            'type_ja': '財務諸表',
            'target': None,
            'scope': None,
            'date_range': None,
            'confidence': 0.9,
        }
    return {
        'type': 'unknown',
        'type_ja': None,
        'target': None,
        'scope': None,
        'date_range': None,
        'confidence': 0.5,
    }


def _generate_tables_yaml(tables_data: List[Dict[str, Any]]) -> str:
    """tables リストから YAML テキストを生成する。"""
    lines = ['tables:']
    for tbl in tables_data:
        tbl_id = tbl['table_id']
        rows = tbl['data_rows']
        headers = tbl['headers']
        sem = _infer_table_semantics(headers, rows)
        type_ja_str = sem['type_ja'] if sem['type_ja'] else 'null'

        description = str(tbl.get('description') or '')
        desc_yaml = f"'{description}'" if description else "''"
        lines.append(f'- table_id: {tbl_id}')
        lines.append(f'  description: {desc_yaml}')
        lines.append('  table_semantics:')
        lines.append(f"    type: {sem['type']}")
        lines.append(f'    type_ja: {type_ja_str}')
        lines.append('    target: null')
        lines.append('    scope: null')
        lines.append('    date_range: null')
        lines.append(f"    confidence: {sem['confidence']}")
        lines.append('  header_row_indices:')
        lines.append('  - 0')
        lines.append('  month_blocks: []')
        lines.append('  data_rows:')
        for idx, row in enumerate(rows):
            lines.append(f'  - sheet_row: {idx + 1}')
            lines.append('    cells:')
            for cell in row:
                lines.append(_cell_to_yaml_item(cell))
    return '\n'.join(lines)


def _direct_extract_build_structured_md(ai_text: str) -> str:
    """AI が返した生テキストをパイプライン互換の構造化 MD に変換する。"""
    import re as _re
    import html as _html

    # AI が markdown コードブロックで包んだ場合は中身だけ取り出す
    matches = _re.findall(r'```(?:markdown|md)?\s*\n?(.*?)```', ai_text, _re.DOTALL)
    if matches:
        ai_text = '\n\n'.join(m.strip() for m in matches)
    ai_text = ai_text.strip()

    # AI の出力が既に ## 非表 / ## 表 のセクション構造を持っているか確認
    has_prose_section = bool(_re.search(r'^## 非表', ai_text, _re.MULTILINE))
    has_table_section = bool(_re.search(r'^## 表', ai_text, _re.MULTILINE))

    if has_prose_section or has_table_section:
        structured = ai_text
    else:
        # フォーマット違反: テキストと表を手動で分離して再構成
        prose_lines = []
        table_blocks = []
        current_table: list[str] = []
        in_table = False
        for line in ai_text.split('\n'):
            is_table_line = line.strip().startswith('|') and line.strip().endswith('|')
            if is_table_line:
                if not in_table:
                    in_table = True
                    current_table = []
                current_table.append(line)
            else:
                if in_table:
                    table_blocks.append('\n'.join(current_table))
                    current_table = []
                    in_table = False
                prose_lines.append(line)
        if current_table:
            table_blocks.append('\n'.join(current_table))

        parts = []
        prose = '\n'.join(prose_lines).strip()
        if prose:
            parts.append('## 非表（F 地の文）\n\n' + prose)
        if table_blocks:
            table_section_lines = ['## 表（ui_data.tables）', '']
            for i, tb in enumerate(table_blocks):
                table_section_lines.append(f'## T{i + 1}')
                table_section_lines.append('')
                table_section_lines.append(tb)
                table_section_lines.append('')
            parts.append('\n'.join(table_section_lines))
        structured = '\n\n'.join(parts) if parts else ai_text

    # ## T\d+ → ## B_T\d+ にリネーム（表ヘッダー行のみ対象）
    structured = _re.sub(r'^(## )T(\d+)\s*$', r'\1B_T\2', structured, flags=_re.MULTILINE)

    # ## 表（ui_data.tables）配下の表から YAML + HTML（colspan対応）を生成
    tables_data: list[dict] = []
    html_lines: list[str] = []

    def _parse_cells(line: str) -> List[str]:
        return [c.strip() for c in line.strip().strip('|').split('|')]

    table_section_match = _re.search(
        r'^## 表（ui_data\.tables）\s*\n(.*?)(?=^## (?!B_T\d)|$)',
        structured, _re.MULTILINE | _re.DOTALL
    )
    if table_section_match:
        table_section_text = table_section_match.group(1)
        table_blocks_in_section = _re.split(r'^## (B_T\d+)\s*$', table_section_text, flags=_re.MULTILINE)
        # [pre, id1, block1, id2, block2, ...]
        i = 1
        while i + 1 < len(table_blocks_in_section):
            tbl_id = table_blocks_in_section[i].strip()
            tbl_md = table_blocks_in_section[i + 1].strip()
            tbl_lines = [ln for ln in tbl_md.split('\n') if ln.strip().startswith('|')]
            if len(tbl_lines) >= 2:
                headers = _parse_cells(tbl_lines[0])
                data_rows = [_parse_cells(ln) for ln in tbl_lines[2:]]  # skip separator

                # ## T1 直後の > blockquote を表の説明として抽出
                desc_m = _re.search(r'^> (.+)$', tbl_md, _re.MULTILINE)
                description = desc_m.group(1).strip() if desc_m else ''

                tables_data.append({
                    'table_id': tbl_id,
                    'headers': headers,
                    'data_rows': data_rows,
                    'description': description,
                })

                # HTML 生成（末尾2列が同一非空値なら colspan=2）
                th_html = ''.join(f'<th>{_html.escape(h)}</th>' for h in headers)
                rows_html_parts = []
                for r in data_rows:
                    if len(r) >= 4 and r[-1] and r[-2] == r[-1]:
                        cells_html = (
                            ''.join(f'<td>{_html.escape(c)}</td>' for c in r[:-2])
                            + f'<td colspan="2">{_html.escape(r[-2])}</td>'
                        )
                    else:
                        cells_html = ''.join(f'<td>{_html.escape(c)}</td>' for c in r)
                    rows_html_parts.append(f'<tr>{cells_html}</tr>')
                html_lines.append(f'<!-- table:{tbl_id} -->')
                html_lines.append(
                    f'<table class="md-embed-table"><thead><tr>{th_html}</tr></thead>'
                    f'<tbody>{"".join(rows_html_parts)}</tbody></table>'
                )
            i += 2

    if tables_data or html_lines:
        embed_parts: list[str] = ['## 表（埋め込み）', '', '<!-- dms:tables-md-embed v1 -->']
        if tables_data:
            yaml_str = _generate_tables_yaml(tables_data)
            embed_parts += ['### `tables`（YAML・検索・LLM 向け）', '', '```yaml', yaml_str, '```']
        if html_lines:
            embed_parts += ['', '### 表 HTML（MD に埋め込み可）', '']
            embed_parts.extend(html_lines)
        structured += '\n\n' + '\n'.join(embed_parts)

    return structured


@lab_bp.route('/api/extract_direct/<session_id>/<int:page_index>', methods=['POST'])
def api_extract_direct(session_id: str, page_index: int):
    """AI直接抽出: ページ画像を Gemini に直接送って MD を返す。パイプライン（A→G）は実行しない。"""
    base = _safe_session_dir(session_id)
    if not base:
        return jsonify({'success': False, 'error': 'セッション不明'}), 404

    img_path = base / 'preview' / f'page_{page_index}.png'
    if not img_path.is_file():
        return jsonify({'success': False, 'error': f'ページ画像が見つかりません: page_{page_index}.png'}), 404

    body = request.get_json(silent=True) or {}
    model_name = (body.get('model') or 'gemini-2.5-flash-lite').strip()

    try:
        import google.generativeai as genai
        import os as _os
        api_key = _os.environ.get('GOOGLE_AI_API_KEY')
        if not api_key:
            return jsonify({'success': False, 'error': 'GOOGLE_AI_API_KEY が未設定です'}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)

        img_bytes = img_path.read_bytes()
        import base64 as _b64
        img_part = {
            'inline_data': {
                'mime_type': 'image/png',
                'data': _b64.b64encode(img_bytes).decode('utf-8'),
            }
        }

        response = model.generate_content(
            [_DIRECT_EXTRACT_PROMPT, img_part], request_options={"timeout": 120}
        )
        structured_md = _direct_extract_build_structured_md(response.text or '')

        result_data = {
            'success': True,
            'session_id': session_id,
            'page_index': page_index,
            'mode': 'direct',
            'model': model_name,
            'reading_stream': [{'type': 'non_table', 'text': structured_md}],
            'raw_md': structured_md,
        }
        result_file = base / f'result_page_{page_index}.json'
        result_file.write_text(
            json.dumps(result_data, ensure_ascii=False, indent=2), encoding='utf-8'
        )

        return jsonify({'success': True, 'markdown': structured_md, 'model': model_name})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Google Drive 連携
# ---------------------------------------------------------------------------

_DRIVE_PDF_MIME = 'application/pdf'
_DRIVE_FOLDER_MIME = 'application/vnd.google-apps.folder'
_DRIVE_SHORTCUT_MIME = 'application/vnd.google-apps.shortcut'


def _drive_normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    mime_type = item.get('mimeType')
    shortcut = item.get('shortcutDetails') or {}
    target_id = shortcut.get('targetId')
    target_mime_type = shortcut.get('targetMimeType')
    effective_mime_type = target_mime_type if mime_type == _DRIVE_SHORTCUT_MIME else mime_type
    effective_id = target_id if mime_type == _DRIVE_SHORTCUT_MIME and target_id else item.get('id')
    return {
        'id': item.get('id'),
        'name': item.get('name'),
        'mimeType': mime_type,
        'size': item.get('size'),
        'modifiedTime': item.get('modifiedTime'),
        'driveId': item.get('driveId'),
        'parents': item.get('parents', []),
        'targetId': target_id,
        'targetMimeType': target_mime_type,
        'targetResourceKey': shortcut.get('targetResourceKey'),
        'effectiveId': effective_id,
        'effectiveMimeType': effective_mime_type,
        'isShortcut': mime_type == _DRIVE_SHORTCUT_MIME,
    }


def _drive_list_all_files(service, **list_args) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    page_token = None
    while True:
        if page_token:
            list_args['pageToken'] = page_token
        result = service.files().list(**list_args).execute()
        items.extend(result.get('files', []))
        page_token = result.get('nextPageToken')
        if not page_token:
            return items


@lab_bp.route('/api/drive/roots', methods=['POST'])
def api_drive_roots():
    """マイドライブ・共有ドライブ一覧（drive_picker.js の /roots 相当）。"""
    try:
        from dms.common.connectors.google_drive import GoogleDriveConnector
        drive = GoogleDriveConnector()
        about = drive.service.about().get(fields='user(emailAddress,displayName)').execute()
        root = drive.service.files().get(fileId='root', fields='id,name', supportsAllDrives=True).execute()
        drives_result = drive.service.drives().list(pageSize=100, fields='drives(id,name)').execute()
        shared_drives = sorted(drives_result.get('drives', []), key=lambda item: (item.get('name') or '').lower())
        return jsonify({
            'user': about.get('user', {}),
            'rootFolderId': root.get('id', 'root'),
            'rootName': root.get('name', 'マイドライブ'),
            'sharedDrives': shared_drives,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@lab_bp.route('/api/drive/list', methods=['POST'])
def api_drive_list():
    """フォルダ内ファイル一覧（drive_picker.js の /list 相当）。"""
    data = request.json or {}
    folder_id = (data.get('folder_id') or 'root').strip() or 'root'
    source = (data.get('source') or 'my_drive').strip()
    drive_id = (data.get('drive_id') or '').strip()
    try:
        from dms.common.connectors.google_drive import GoogleDriveConnector
        drive = GoogleDriveConnector()
        fields = (
            'nextPageToken,files(id,name,mimeType,size,modifiedTime,driveId,parents,'
            'shortcutDetails(targetId,targetMimeType,targetResourceKey))'
        )
        if source == 'shared_with_me':
            query = 'sharedWithMe=true and trashed=false'
            list_args: Dict[str, Any] = {
                'q': query,
                'fields': fields,
                'supportsAllDrives': True,
                'includeItemsFromAllDrives': True,
                'corpora': 'user',
                'pageSize': 1000,
            }
        else:
            query = f"'{folder_id}' in parents and trashed=false"
            list_args = {
                'q': query,
                'fields': fields,
                'supportsAllDrives': True,
                'includeItemsFromAllDrives': True,
                'pageSize': 1000,
            }
            if source == 'shared_drive' and drive_id:
                list_args['corpora'] = 'drive'
                list_args['driveId'] = drive_id
            elif source == 'all_drives':
                list_args['corpora'] = 'allDrives'
            else:
                list_args['corpora'] = 'user'

        raw_items = _drive_list_all_files(drive.service, **list_args)
        items = [_drive_normalize_item(item) for item in raw_items]
        items.sort(key=lambda item: (
            item.get('effectiveMimeType') != _DRIVE_FOLDER_MIME,
            item.get('effectiveMimeType') != _DRIVE_PDF_MIME,
            (item.get('name') or '').lower(),
        ))
        return jsonify({'items': items, 'folder_id': folder_id, 'source': source, 'drive_id': drive_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@lab_bp.route('/api/load_from_drive', methods=['POST'])
def api_load_from_drive():
    """Drive の PDF を DL してセッションを初期化する（/api/upload と同じ後処理）。"""
    try:
        from dms.common.connectors.google_drive import GoogleDriveConnector
        data = request.json or {}
        drive_file_id = (data.get('drive_file_id') or '').strip()
        if not drive_file_id:
            return jsonify({'success': False, 'error': 'drive_file_id が必要です'}), 400

        session_id = uuid.uuid4().hex[:12]
        session_dir = _sessions_root() / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        drive = GoogleDriveConnector()
        # ファイル名・mimeType 取得
        meta = drive.service.files().get(fileId=drive_file_id, fields='name,mimeType', supportsAllDrives=True).execute()
        filename = meta.get('name', 'drive.pdf')
        mime_type = meta.get('mimeType', '')
        is_img = _is_image(filename) or (mime_type.startswith('image/') if mime_type else False)
        is_pdf = filename.lower().endswith('.pdf') or mime_type == 'application/pdf'
        if not is_pdf and not is_img:
            shutil.rmtree(session_dir, ignore_errors=True)
            return jsonify({'success': False, 'error': 'PDF または画像ファイルのみ対応しています'}), 400

        pdf_path = session_dir / 'input.pdf'
        downloaded = drive.download_file(drive_file_id, filename, session_dir)
        if not downloaded:
            shutil.rmtree(session_dir, ignore_errors=True)
            return jsonify({'success': False, 'error': 'Drive からのダウンロードに失敗しました'}), 500
        dl_path = session_dir / filename
        if is_img:
            _image_to_pdf(dl_path, pdf_path)
            (session_dir / 'is_image.txt').write_text('1', encoding='utf-8')
        elif dl_path != pdf_path:
            dl_path.rename(pdf_path)

        # drive_file_id を session に記録（後で上書き時に使う）
        (session_dir / 'drive_file_id.txt').write_text(drive_file_id, encoding='utf-8')
        (session_dir / 'drive_filename.txt').write_text(filename, encoding='utf-8')

        n = _pdf_page_count(pdf_path)
        if n < 1:
            shutil.rmtree(session_dir, ignore_errors=True)
            return jsonify({'success': False, 'error': 'ページがありません'}), 400
        pages = _render_pdf_previews(pdf_path, session_dir, session_id)

        return jsonify({
            'success': True,
            'session_id': session_id,
            'file_id': session_id,
            'filename': filename,
            'safe_filename': filename,
            'page_count': n,
            'pages': pages,
            'drive_file_id': drive_file_id,
            'is_image': is_img,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# MD_SANDWICH 検出・除去
# ---------------------------------------------------------------------------

@lab_bp.route('/api/has_sandwich/<session_id>', methods=['GET'])
def api_has_sandwich(session_id: str):
    base = _safe_session_dir(session_id)
    if not base:
        return jsonify({'error': 'not found'}), 404
    pdf_path = base / 'input.pdf'
    if not pdf_path.exists():
        return jsonify({'has_sandwich': False})
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        found = any('<<<MD_SANDWICH_START>>>' in page.get_text() for page in doc)
        doc.close()
        return jsonify({'has_sandwich': found})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@lab_bp.route('/api/strip_sandwich/<session_id>', methods=['POST'])
def api_strip_sandwich(session_id: str):
    base = _safe_session_dir(session_id)
    if not base:
        return jsonify({'success': False, 'error': 'not found'}), 404
    pdf_path = base / 'input.pdf'
    if not pdf_path.exists():
        return jsonify({'success': False, 'error': 'input.pdf がありません'}), 404
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        modified = False
        _BT_ET = re.compile(rb'BT\b.*?\bET', re.DOTALL)
        _INVIS = re.compile(rb'(?<!\d)3[ \t]+Tr\b')
        for page in doc:
            if '<<<MD_SANDWICH_START>>>' not in page.get_text():
                continue
            for xref in page.get_contents():
                content = doc.xref_stream(xref)
                new_content = _BT_ET.sub(
                    lambda m: b'' if _INVIS.search(m.group(0)) else m.group(0),
                    content
                )
                if new_content != content:
                    doc.update_stream(xref, new_content)
                    modified = True
        if modified:
            tmp_path = pdf_path.with_suffix('.tmp.pdf')
            doc.save(str(tmp_path), incremental=False, encryption=fitz.PDF_ENCRYPT_NONE)
            doc.close()
            tmp_path.replace(pdf_path)
        else:
            doc.close()
        if not modified:
            return jsonify({'success': True, 'modified': False})

        # Drive ファイルも上書きする
        drive_file_id_path = base / 'drive_file_id.txt'
        drive_error = None
        drive_overwritten = False
        if drive_file_id_path.exists():
            drive_file_id_val = drive_file_id_path.read_text(encoding='utf-8').strip()
            if drive_file_id_val:
                try:
                    from dms.common.connectors.google_drive import GoogleDriveConnector
                    drive = GoogleDriveConnector()
                    ok = drive.update_file_content(drive_file_id_val, str(pdf_path))
                    if ok:
                        drive_overwritten = True
                    else:
                        drive_error = 'Drive上書きに失敗しました（権限を確認してください）'
                except Exception as e:
                    drive_error = str(e)

        return jsonify({'success': True, 'modified': modified, 'drive_overwritten': drive_overwritten, 'drive_error': drive_error})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# 結果 PDF 生成・ダウンロード・Drive 上書き
# ---------------------------------------------------------------------------

def _collect_session_md(session_dir: Path) -> str:
    """全ページの実行結果から reading_stream テキストを集約する。"""
    parts = []
    for result_file in sorted(session_dir.glob('result_page_*.json')):
        try:
            r = json.loads(result_file.read_text(encoding='utf-8'))
        except Exception:
            continue
        page_idx = r.get('page_index', '?')
        rs = r.get('reading_stream') or []
        text = '\n'.join(b.get('text', '') for b in rs if b.get('text'))
        if text:
            parts.append(f'## Page {int(page_idx) + 1}\n\n{text}')
    return '\n\n'.join(parts)


@lab_bp.route('/api/download_result_pdf/<session_id>', methods=['GET'])
def api_download_result_pdf(session_id: str):
    """pipeline 結果の MD を元の PDF に埋め込んでダウンロード。"""
    base = _safe_session_dir(session_id)
    if not base:
        return jsonify({'error': 'not found'}), 404
    pdf_path = base / 'input.pdf'
    if not pdf_path.is_file():
        return jsonify({'error': 'input.pdf がありません'}), 404

    md_text = _collect_session_md(base)
    if not md_text:
        return jsonify({'error': '実行結果がありません。先にパイプラインを実行してください'}), 400

    out_path = base / 'result_embedded.pdf'
    try:
        doc = fitz.open(str(pdf_path))
        MARKER_START = '<<<MD_SANDWICH_START>>>'
        MARKER_END = '<<<MD_SANDWICH_END>>>'
        pages_md = md_text.split('\n\n## Page ')
        # 各ページに対応する MD を埋め込む
        page_texts: dict = {}
        for chunk in pages_md:
            chunk = chunk.strip()
            if not chunk:
                continue
            if chunk.startswith('## Page '):
                chunk = chunk[len('## Page '):]
            lines = chunk.split('\n', 1)
            try:
                p_idx = int(lines[0].strip()) - 1
                body = lines[1].strip() if len(lines) > 1 else ''
            except (ValueError, IndexError):
                continue
            page_texts[p_idx] = body
        for p_idx, body in page_texts.items():
            if p_idx < len(doc):
                page = doc[p_idx]
                payload = f'{MARKER_START}\n{body}\n{MARKER_END}'
                rect = fitz.Rect(0, 0, page.rect.width, page.rect.height)
                page.insert_textbox(rect, payload, fontsize=6, fontname='japan', render_mode=3)
        doc.save(str(out_path))
        doc.close()
    except Exception as e:
        return jsonify({'error': f'PDF 生成エラー: {e}'}), 500

    drive_filename = base / 'drive_filename.txt'
    dl_name = drive_filename.read_text(encoding='utf-8').replace('.pdf', '_pipeline.pdf') if drive_filename.exists() else 'pipeline_result.pdf'
    from flask import send_file as _send_file
    return _send_file(str(out_path), as_attachment=True, download_name=dl_name)


@lab_bp.route('/api/save_to_drive/<session_id>', methods=['POST'])
def api_save_to_drive(session_id: str):
    """結果 PDF を Drive の元ファイルに上書き保存する。"""
    base = _safe_session_dir(session_id)
    if not base:
        return jsonify({'error': 'not found'}), 404

    drive_id_file = base / 'drive_file_id.txt'
    if not drive_id_file.exists():
        return jsonify({'error': 'Drive ファイル ID が記録されていません（ローカルアップロードのセッション）'}), 400
    drive_file_id = drive_id_file.read_text(encoding='utf-8').strip()

    out_path = base / 'result_embedded.pdf'
    if not out_path.is_file():
        # まだ生成されていなければ生成する
        from flask import url_for
        gen_resp = api_download_result_pdf(session_id)
        if hasattr(gen_resp, 'status_code') and gen_resp.status_code != 200:
            return gen_resp
        if not out_path.is_file():
            return jsonify({'error': 'PDF の生成に失敗しました'}), 500

    try:
        from dms.common.connectors.google_drive import GoogleDriveConnector
        drive = GoogleDriveConnector()
        success = drive.update_file_content(drive_file_id, str(out_path))
        if not success:
            return jsonify({'error': 'Drive への上書きに失敗しました'}), 500
        return jsonify({'success': True, 'drive_file_id': drive_file_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@lab_bp.route('/api/download_md/<session_id>', methods=['GET'])
def api_download_md(session_id: str):
    """MD テキストをファイルとしてダウンロード。"""
    base = _safe_session_dir(session_id)
    if not base:
        return jsonify({'error': 'not found'}), 404
    md_text = _collect_session_md(base)
    if not md_text:
        return jsonify({'error': '実行結果がありません。先にパイプラインを実行してください'}), 400
    drive_filename_file = base / 'drive_filename.txt'
    if drive_filename_file.exists():
        dl_name = drive_filename_file.read_text(encoding='utf-8').strip().rsplit('.', 1)[0] + '_pipeline.md'
    else:
        dl_name = 'pipeline_result.md'
    from flask import Response
    return Response(
        md_text.encode('utf-8'),
        mimetype='text/markdown; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{dl_name}"'},
    )


@lab_bp.route('/api/save_md_to_drive/<session_id>', methods=['POST'])
def api_save_md_to_drive(session_id: str):
    """MD テキストを Drive の元ファイルと同じフォルダに .md ファイルとして保存。"""
    base = _safe_session_dir(session_id)
    if not base:
        return jsonify({'error': 'not found'}), 404
    drive_id_file = base / 'drive_file_id.txt'
    if not drive_id_file.exists():
        return jsonify({'error': 'Drive ファイル ID が記録されていません（ローカルアップロードのセッション）'}), 400
    drive_file_id = drive_id_file.read_text(encoding='utf-8').strip()
    md_text = _collect_session_md(base)
    if not md_text:
        return jsonify({'error': '実行結果がありません。先にパイプラインを実行してください'}), 400
    drive_filename_file = base / 'drive_filename.txt'
    pdf_name = drive_filename_file.read_text(encoding='utf-8').strip() if drive_filename_file.exists() else 'result.pdf'
    md_name = pdf_name.rsplit('.', 1)[0] + '_pipeline.md'
    try:
        from dms.common.connectors.google_drive import GoogleDriveConnector
        drive = GoogleDriveConnector()
        meta = drive.service.files().get(fileId=drive_file_id, fields='parents', supportsAllDrives=True).execute()
        parent_id = (meta.get('parents') or [None])[0]
        file_id = drive.upload_file(md_text, md_name, 'text/markdown', folder_id=parent_id)
        if not file_id:
            return jsonify({'error': 'Drive へのアップロードに失敗しました'}), 500
        return jsonify({'success': True, 'file_id': file_id, 'file_name': md_name})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@lab_bp.route('/api/save_md_to_supabase/<session_id>', methods=['POST'])
def api_save_md_to_supabase(session_id: str):
    """MD を Supabase の raw テーブル pdf_md_content カラムに保存（rag-prepare 連携）。"""
    body = request.get_json(silent=True) or {}
    raw_table = (body.get('raw_table') or '').strip()
    raw_id = (body.get('raw_id') or '').strip()
    if not raw_table or not raw_id:
        return jsonify({'error': 'raw_table と raw_id が必要です'}), 400
    md_text = (body.get('md_text') or '').strip()
    if not md_text:
        base = _safe_session_dir(session_id)
        if base:
            md_text = _collect_session_md(base)
    if not md_text:
        return jsonify({'error': '実行結果がありません。先にパイプラインを実行してください'}), 400
    try:
        import datetime as _dt
        from dms.common.database.client import DatabaseClient
        db = DatabaseClient(use_service_role=True)
        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        db.client.table(raw_table).update({'pdf_md_content': md_text, 'pdf_md_updated_at': now}).eq('id', raw_id).execute()
        return jsonify({'success': True, 'raw_table': raw_table, 'raw_id': raw_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@lab_bp.route('/api/update_page_md/<session_id>/<int:page_index>', methods=['POST'])
def api_update_page_md(session_id: str, page_index: int):
    """ブラウザ上で編集された MD テキストを result JSON に書き戻す。"""
    base = _safe_session_dir(session_id)
    if not base:
        return jsonify({'error': 'not found'}), 404
    body = request.get_json(silent=True) or {}
    md_text = body.get('markdown_text', '')
    result_file = base / f'result_page_{page_index}.json'
    try:
        if result_file.exists():
            r = json.loads(result_file.read_text(encoding='utf-8'))
        else:
            r = {'success': True, 'session_id': session_id, 'page_index': page_index}
        r['reading_stream'] = [{'type': 'non_table', 'text': md_text}]
        result_file.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding='utf-8')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
