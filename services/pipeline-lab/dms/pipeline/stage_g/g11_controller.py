"""
G11: Stage G コントローラー（レビュー UI 用 ui_data / final_metadata）。

表チェーン: G26(理解) → G36(直す) → G41/G44/G45(切る) → G61→G62(配置) → G65
前処理: G15 → G22 → G24。番号帯: G20=理解 / G30=直す / G40=切る / G60=配置。
地の文: F17 の non_table_text を g21_articles に載せるのみ。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from dms.pipeline.stage_g.g15_table_extract import attach_bbox_to_ui_tables, extract_tables_from_stage_f
from dms.pipeline.stage_g.g19_ui_assembly import G19UIAssembly
from dms.pipeline.stage_g.g22_table_rebuilder import G22TableRebuilder
from dms.pipeline.stage_g.g24_table_structurer import G24TableStructurer
from dms.pipeline.stage_g.g36_lr_merged_vertical_grid import G36_LR_REBUILD_JUDGES
from dms.pipeline.stage_g.g36_lr_vertical_orchestrator import (
    resolve_geometry_pdf_path,
    run_g36_on_structured_tables,
)
from dms.pipeline.stage_g.g26_semantic_estimator import (
    G26SemanticEstimator,
    propagate_semantics_to_sub_tables,
)
from dms.pipeline.stage_g.g61_layout_bridge import G61LayoutBridgeProcessor
from dms.pipeline.stage_g.g45_d_line_orchestrator import run_g45_apply_split
from dms.pipeline.stage_g.g41_repeating_header_detector import G41RepeatingHeaderDetector
from dms.pipeline.stage_g.g44_table_reconstructor import G44TableReconstructor
from dms.pipeline.stage_g.g62_table_layout import G62TableLayoutProcessor
from dms.pipeline.stage_g.g65_table_analysis_joiner import join_table_analyses
from dms.pipeline.stage_g.merged_cell_grid import (
    apply_merged_cell_resolution,
    normalize_grid_cells,
    row_starts_with_circled_marker,
)
from dms.pipeline.stage_g.review_tables_payload import (
    build_tables_markdown_embed,
    build_tables_review_html,
    build_tables_ssot,
)
from dms.pipeline.stage_g.table_md_emitters import (
    _norm_header_rows,
    _sanitize_header_rows,
    resolve_ui_column_labels,
)


class G11Controller:
    """Stage G: F17 出口を入力に UI データを組み立てる。"""

    def __init__(self, document_id: Optional[str] = None, g26_model_name: Optional[str] = None) -> None:
        g62 = G62TableLayoutProcessor(document_id=document_id)
        self._g61 = G61LayoutBridgeProcessor(document_id=document_id, next_stage=g62)
        self._g44 = G44TableReconstructor(next_stage=None)
        self._g41 = G41RepeatingHeaderDetector(document_id=document_id, next_stage=None)
        self._g24 = G24TableStructurer(document_id=document_id, next_stage=None)
        self._g22 = G22TableRebuilder()
        self._g19 = G19UIAssembly(table_chain=None, text_chain=None)
        self.document_id = document_id
        self.g26_model_name = g26_model_name  # None のとき G26 デフォルトを使う

    def process(
        self,
        stage_f_result: Optional[Dict[str, Any]] = None,
        log_dir: Optional[Path] = None,
        *,
        f5_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """F17 までの Stage F 結果を入力に ui_data / final_metadata を返す。"""
        if stage_f_result is None:
            stage_f_result = f5_result
        if not stage_f_result:
            return {"success": False, "error": "stage_f_result required"}
        _tld = Path(log_dir) if log_dir is not None else None

        logger.info("=" * 60)
        logger.info("[G11] Stage G 開始")
        logger.info("=" * 60)

        # G15 + G22
        tables, bbox_by_id = extract_tables_from_stage_f(stage_f_result)
        logger.info(f"[G15] consolidated_tables={len(tables)}")
        table_result = self._g22.rebuild(tables)
        ui_tables = table_result.get("ui_tables", []) if table_result.get("success") else []
        attach_bbox_to_ui_tables(ui_tables, bbox_by_id)
        logger.info(f"[G22] ui_tables={len(ui_tables)}")

        bundle = {
            "success": True,
            "ui_tables": ui_tables,
            "raw_text": stage_f_result.get("non_table_text", ""),
            "events": stage_f_result.get("normalized_events", []),
            "tasks": stage_f_result.get("tasks", []),
            "notices": stage_f_result.get("notices", []),
            "document_info": stage_f_result.get("document_info", {}),
            "conversion_count": table_result.get("conversion_count", 0),
            "display_fields": stage_f_result.get("display_fields"),
            "stage_d_line_digest": stage_f_result.get("stage_d_line_digest"),
        }
        blocks = self._sections_blocks(bundle)
        pdf_path = (
            stage_f_result.get("document_info", {}).get("purged_pdf_path")
            or (stage_f_result.get("metadata") or {}).get("purged_pdf_path")
        )
        delivery = {**bundle, "blocks": blocks}
        if pdf_path:
            delivery["purged_pdf_path"] = pdf_path

        # G24（構造化のみ）
        ctx: Dict[str, Any] = {}
        if stage_f_result.get("stage_d_line_digest"):
            ctx["stage_d_line_digest"] = stage_f_result["stage_d_line_digest"]
        if stage_f_result.get("stage_d_cell_bundle"):
            ctx["stage_d_cell_bundle"] = stage_f_result["stage_d_cell_bundle"]
        if pdf_path:
            ctx["purged_pdf_path"] = pdf_path
        g24_out = self._g24.structure(
            ui_tables,
            year_context=(bundle.get("document_info") or {}).get("year_context"),
            table_log_dir=_tld,
            chain_context=ctx,
        )
        if not g24_out.get("success"):
            return {"success": False, "error": g24_out.get("error") or "G24 failed"}
        structured = list(g24_out.get("structured_tables") or [])
        for k in ("stage_d_line_digest", "line_semantics_ai", "d_line_split_contract"):
            if g24_out.get(k) is not None:
                ctx[k] = g24_out[k]

        year_ctx = (bundle.get("document_info") or {}).get("year_context")

        g24_bundle = {
            "success": True,
            "structured_tables": structured,
            "stage_d_line_digest": ctx.get("stage_d_line_digest"),
            "stage_d_cell_bundle": ctx.get("stage_d_cell_bundle"),
            "line_semantics_ai": ctx.get("line_semantics_ai"),
            "d_line_split_contract": ctx.get("d_line_split_contract"),
        }

        logger.info("[G26] ページ理解（D罫線 + 表の列意味・切り方）")
        g26_kwargs = {"document_id": self.document_id}
        if self.g26_model_name:
            g26_kwargs["model_name"] = self.g26_model_name
        semantic, _, _ = G26SemanticEstimator(**g26_kwargs).infer_all(
            self._structured_to_e14_reconstructed(structured),
            year_ctx,
            chain_context=g24_bundle,
            non_table_text=str(bundle.get("raw_text") or ""),
        )
        g24_bundle["semantic_inference"] = semantic
        lsa = semantic.get("line_semantics_ai")
        if lsa:
            ctx["line_semantics_ai"] = lsa
            digest = dict(ctx.get("stage_d_line_digest") or {})
            digest["line_semantics_ai"] = lsa
            ctx["stage_d_line_digest"] = digest
            g24_bundle["line_semantics_ai"] = lsa
            g24_bundle["stage_d_line_digest"] = digest

        logger.info("[G36] 結合セル再構成（geometry → AI、理解のあと・切る前）")
        if pdf_path:
            _geo_pdf = (
                (bundle.get("document_info") or {}).get("source_pdf_path")
                or resolve_geometry_pdf_path(pdf_path)
            )
            run_g36_on_structured_tables(
                structured,
                _geo_pdf,
                document_id=self.document_id,
                cell_bundle=stage_f_result.get("stage_d_cell_bundle"),
            )

        logger.info("[G41] 分割方針（G32 正本）")
        g41_out = self._g41.process(
            g24_bundle,
            year_context=year_ctx,
            table_log_dir=_tld,
        )
        if not g41_out.get("success", True):
            return {"success": False, "error": g41_out.get("error") or "G41 failed"}

        logger.info("[G44] 機械分割（切る）")
        e14_result = self._g44.process(g41_out, year_context=year_ctx, table_log_dir=_tld)
        if not e14_result.get("success", True):
            return {"success": False, "error": e14_result.get("error") or "G44 failed"}

        cell_bundle = stage_f_result.get("stage_d_cell_bundle")
        if pdf_path and cell_bundle and cell_bundle.get("available"):
            from dms.pipeline.stage_g.g36_d_cell_matrix import apply_d_cell_matrix_to_e14

            geo_pdf = (
                (bundle.get("document_info") or {}).get("source_pdf_path")
                or resolve_geometry_pdf_path(pdf_path)
            )
            logger.info("[G36] D セル行列（G44 分割後・サブ表ごと）")
            apply_d_cell_matrix_to_e14(
                e14_result.get("e14_reconstructed") or [],
                geo_pdf,
                cell_bundle,
                structured_tables=structured,
                document_id=self.document_id,
            )

        if ctx.get("line_semantics_ai"):
            logger.info("[G45] D罫線物理分割")
            ctx, structured = run_g45_apply_split(ctx, structured)
            g41_out["structured_tables"] = structured

        e14_list = e14_result.get("e14_reconstructed") or []
        semantic = propagate_semantics_to_sub_tables(semantic, e14_list)
        chain_ctx = {**g41_out, "semantic_inference": semantic}

        logger.info("[G61] G26 意味 → G62 配置")
        g61_out = self._g61.process(
            e14_list,
            year_context=year_ctx,
            table_log_dir=_tld,
            chain_context=chain_ctx,
        )
        g62_result = g61_out.get("g62_result") or g61_out.get("f47_result", {})

        shell = self._g19._eliminate_impl(delivery, table_log_dir=_tld)
        ui_data = shell.get("ui_data", {})

        e13_result = g41_out
        table_analyses = join_table_analyses(g62_result.get("table_analyses", []))
        ui_tables_out = self._convert_analyses_to_ui_format(table_analyses)
        attach_bbox_to_ui_tables(ui_tables_out, bbox_by_id)
        ui_data["tables"] = ui_tables_out
        ui_data["tables_review_html"] = build_tables_review_html(ui_data.get("tables") or [])
        ui_data["tables_md_embed"] = build_tables_markdown_embed(ui_data.get("tables") or [])
        if structured:
            ui_data["g11_structured_tables"] = structured

        g21_output = self._g21_articles(
            stage_f_result.get("non_table_text") or "",
            non_table_text_blocks=stage_f_result.get("non_table_text_blocks") or [],
        )
        if g21_output:
            ui_data["g21_articles"] = g21_output
            self._dedupe_prose_sections(ui_data)

        final_metadata = {
            "g11_output": structured,
            "g14_output": e14_result.get("e14_reconstructed", []),
            "g17_output": ui_data.get("tables"),
            "g21_output": g21_output,
            "g22_output": {
                "calendar_events": [],
                "tasks": [],
                "notices": [],
                "people": [],
                "topic_sections": [],
            },
            "tables_ssot": build_tables_ssot(e13_result, e14_result),
        }

        logger.info("[G11] Stage G 完了")
        return {
            "success": True,
            "ui_data": ui_data,
            "final_metadata": final_metadata,
            "metadata": {
                "stage": "G",
                "ui_substage": "G11",
                "conversion_count": table_result.get("conversion_count", 0),
            },
        }

    @staticmethod
    def _structured_to_e14_reconstructed(
        structured_tables: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """G32 理解（分割前）用: 1 表 = 1 サブブロック。"""
        out: List[Dict[str, Any]] = []
        for t in structured_tables:
            if not isinstance(t, dict):
                continue
            headers = list(t.get("headers") or [])
            rows = [list(r) for r in (t.get("rows") or [])]
            data: List[List[Any]] = []
            if headers:
                data.append(headers)
            data.extend(rows)
            out.append(
                {
                    "table_id": t.get("table_id", ""),
                    "sub_tables": [{"sub_table_id": "", "data": data}],
                }
            )
        return out

    @staticmethod
    def _sections_blocks(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw_text = bundle.get("raw_text", "")
        events = bundle.get("events", [])
        tasks = bundle.get("tasks", [])
        notices = bundle.get("notices", [])
        blocks: List[Dict[str, Any]] = []
        if events:
            blocks.append(
                {
                    "block_id": f"B{len(blocks) + 1}",
                    "type": "events",
                    "label": "予定・スケジュール",
                    "content": events,
                    "display_order": 1,
                }
            )
        if tasks:
            blocks.append(
                {
                    "block_id": f"B{len(blocks) + 1}",
                    "type": "tasks",
                    "label": "タスク・持ち物",
                    "content": tasks,
                    "display_order": 2,
                }
            )
        if notices:
            blocks.append(
                {
                    "block_id": f"B{len(blocks) + 1}",
                    "type": "notices",
                    "label": "注意事項",
                    "content": notices,
                    "display_order": 3,
                }
            )
        if raw_text:
            blocks.append(
                {
                    "block_id": f"B{len(blocks) + 1}",
                    "type": "text",
                    "label": "",
                    "content": raw_text,
                    "display_order": 10,
                }
            )
        blocks.sort(key=lambda b: b.get("display_order", 999))
        return blocks

    @staticmethod
    def _text_lines_to_markdown(text_lines: List[Dict[str, Any]]) -> str:
        """
        pdfplumber 書式付き行リストをMarkdownに変換する。元の文字は一切変えない。
        フォントサイズで見出し判定、bold・redで装飾マーク付与。
        """
        if not text_lines:
            return ""
        import re as _re
        from collections import Counter
        sizes = [ln["size"] for ln in text_lines if ln.get("size", 0) > 0]
        body_size = Counter(sizes).most_common(1)[0][0] if sizes else 12.0

        md_lines = []
        for ln in text_lines:
            text = ln["text"]
            size = ln.get("size") or body_size
            bold = ln.get("bold", False)
            red = ln.get("red", False)
            if size >= body_size * 1.3:
                md_lines.append(f"# {text}")
            elif size >= body_size * 1.15:
                md_lines.append(f"## {text}")
            elif red and bold:
                md_lines.append(f"**[RED]{text}[/RED]**")
            elif red:
                md_lines.append(f"[RED]{text}[/RED]")
            elif bold:
                md_lines.append(f"**{text}**")
            else:
                md_lines.append(text)
        return "\n".join(md_lines)

    @staticmethod
    def _markup_block_with_gemini(text: str, text_lines: List[Dict[str, Any]] = None) -> str:
        """
        Gemini Flash-lite でテキストにMarkdownマークアップを付加する。
        元の文字は一切変えない。pdfplumber書式情報があれば参考情報として提供する。
        """
        if not text.strip():
            return text
        try:
            import google.generativeai as genai
            from dms.common.config.settings import settings
            if not settings.GOOGLE_AI_API_KEY:
                return text
            genai.configure(api_key=settings.GOOGLE_AI_API_KEY)
            model = genai.GenerativeModel("gemini-2.5-flash-lite")

            context = ""
            if text_lines:
                from collections import Counter as _Counter
                sizes = [ln["size"] for ln in text_lines if ln.get("size", 0) > 0]
                body_size = _Counter(sizes).most_common(1)[0][0] if sizes else 12.0
                hints = []
                for ln in text_lines[:40]:
                    tags = []
                    if ln.get("bold"):
                        tags.append("太字")
                    size = ln.get("size") or body_size
                    if size >= body_size * 1.3:
                        tags.append("大見出し相当")
                    elif size >= body_size * 1.15:
                        tags.append("中見出し相当")
                    if tags:
                        hints.append(f'"{ln["text"][:30]}": {", ".join(tags)}')
                if hints:
                    context = "【書式情報（参考）】\n" + "\n".join(hints) + "\n\n"

            prompt = (
                f"{context}"
                "以下のテキストに、元の文字を一切変えずにMarkdownマークアップのみを付加してください。\n\n"
                "付加できるマークアップ:\n"
                "- 見出し: # ## ###\n"
                "- 番号なしリスト: -\n"
                "- 番号付きリスト: 1. 2.\n"
                "- 強調: **太字** *斜体*\n"
                "- 注意・引用: >\n"
                "- 区切り: ---\n"
                "- セマンティックタグ（値を囲む）:\n"
                "    [SENDER]...[/SENDER]  ← 送信者・作成者・差出人\n"
                "    [RECIPIENT]...[/RECIPIENT]  ← 受信者・対象者・宛先\n"
                "    [DATE]...[/DATE]  ← 日付・期限・締め切り\n"
                "    [CATEGORY]...[/CATEGORY]  ← カテゴリ・種別・分類\n\n"
                "絶対ルール: テキストの文字は変えない。マークアップ記号のみ追加。\n\n"
                f"テキスト:\n{text[:4000]}"
            )
            resp = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(max_output_tokens=2000, temperature=0.0),
            )
            result = resp.text.strip()
            return result if result else text
        except Exception as e:
            logger.warning(f"[G21] Geminiマークアップ失敗: {e}")
            return text

    @staticmethod
    def _split_md_to_articles(full_md: str) -> List[Dict[str, str]]:
        """Markdownを # / ## 見出しでarticleに分割する。"""
        import re as _re
        parts = _re.split(r'\n(?=#{1,2} )', "\n" + full_md)
        articles = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            lines = part.split("\n")
            if lines[0].startswith("## "):
                title = lines[0][3:].strip()
                body = "\n".join(lines[1:]).strip()
            elif lines[0].startswith("# "):
                title = lines[0][2:].strip()
                body = "\n".join(lines[1:]).strip()
            else:
                title = ""
                body = part
            if body or title:
                articles.append({"title": title, "body": body})
        return articles

    @staticmethod
    def _g21_articles(
        non_table_text: str,
        non_table_text_blocks: List[Dict[str, Any]] = None,
    ) -> List[Dict[str, str]]:
        """
        非表テキストブロックにMarkdownマークアップを付加してarticleリストを返す。
        pdfplumber書式情報（text_lines）があればそれを優先し、Geminiに渡して精度を上げる。
        """
        blocks = non_table_text_blocks or []
        if not blocks and not (non_table_text or "").strip():
            return []

        md_parts = []
        if blocks:
            for block in blocks:
                text = (block.get("text") or "").strip()
                if not text:
                    continue
                text_lines = block.get("text_lines") or []
                # pdfplumber書式情報が十分あればtypography-firstでMarkdownを生成し、
                # Geminiに書式ヒントとして渡す
                md = G11Controller._markup_block_with_gemini(text, text_lines or None)
                # text_linesがある場合はredマーカーをGeminiが見落とすため後処理で補完
                if text_lines:
                    red_texts = {ln["text"] for ln in text_lines if ln.get("red")}
                    if red_texts:
                        result_lines = []
                        for ln in md.split("\n"):
                            bare = ln.lstrip("#- *>[").strip().rstrip("*]")
                            if any(r and r in bare for r in red_texts) and "[RED]" not in ln:
                                result_lines.append(f"[RED]{ln}[/RED]")
                            else:
                                result_lines.append(ln)
                        md = "\n".join(result_lines)
                if md.strip():
                    md_parts.append(md)
        else:
            nt = (non_table_text or "").strip()
            if nt:
                md_parts.append(G11Controller._markup_block_with_gemini(nt))

        if not md_parts:
            return []

        full_md = "\n\n".join(md_parts)
        articles = G11Controller._split_md_to_articles(full_md)
        return articles if articles else [{"title": "", "body": full_md}]

    @staticmethod
    def _dedupe_prose_sections(ui_data: Dict[str, Any]) -> None:
        g21 = ui_data.get("g21_articles") or []
        if not isinstance(g21, list) or not g21:
            return
        if not any(str(a.get("body") or "").strip() for a in g21 if isinstance(a, dict)):
            return
        sections = ui_data.get("sections")
        if not isinstance(sections, list) or not sections:
            return
        filtered = [b for b in sections if not (isinstance(b, dict) and b.get("type") == "text")]
        removed = len(sections) - len(filtered)
        if removed <= 0:
            return
        ui_data["sections"] = filtered
        meta = ui_data.setdefault("metadata", {})
        if isinstance(meta, dict):
            meta["section_count"] = len(filtered)

    @staticmethod
    def _is_headerless_numbered_item_table(section_data: List[List[Any]]) -> bool:
        """先頭行が丸数字項目の2列表（使途一覧など・ヘッダー行なし）。"""
        if not section_data:
            return False
        widths = [len(r) for r in section_data if isinstance(r, (list, tuple))]
        if not widths or max(widths) != 2:
            return False
        return row_starts_with_circled_marker(section_data[0])

    def _convert_analyses_to_ui_format(self, table_analyses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ui_tables: List[Dict[str, Any]] = []
        for analysis in table_analyses:
            sections = analysis.get("sections", [])
            section_data = sections[0].get("data", []) if sections else []
            sec_meta = dict(sections[0].get("metadata", {}) if sections else analysis.get("metadata", {}))
            section_data = normalize_grid_cells(section_data)
            nrows = len(section_data)
            if sec_meta.get("lr_rebuilt") and sec_meta.get(
                "vertical_merge_judge"
            ) not in G36_LR_REBUILD_JUDGES:
                raise ValueError(
                    f"g36_ai_judge_missing: table_id={analysis.get('table_id')} "
                    f"judge={sec_meta.get('vertical_merge_judge')!r}"
                )
            hr = _sanitize_header_rows(_norm_header_rows(sec_meta.get("header_rows")), nrows)
            dsr_raw = sec_meta.get("data_start_row")
            numbered_headerless = self._is_headerless_numbered_item_table(section_data)
            if numbered_headerless and hr and section_data:
                hr = [
                    r
                    for r in hr
                    if r < nrows and not row_starts_with_circled_marker(section_data[r])
                ]
            headerless = (
                numbered_headerless
                or (sec_meta.get("lr_rebuilt") and hr == [] and dsr_raw == 0)
            )
            if nrows == 1:
                hr = []
                dsr = 0
                ncol = len(section_data[0]) if section_data and isinstance(section_data[0], (list, tuple)) else 0
                column_labels = ["項目", "内容"] if ncol == 2 else [f"列{i + 1}" for i in range(ncol)]
            elif headerless:
                hr = []
                dsr = 0
                ncol = (
                    len(section_data[0])
                    if section_data and isinstance(section_data[0], (list, tuple))
                    else 0
                )
                column_labels = (
                    ["項目", "内容"]
                    if numbered_headerless and ncol == 2
                    else resolve_ui_column_labels(
                        {"rows": section_data, "metadata": sec_meta}
                    )
                )
            else:
                if not hr and nrows:
                    hr = [0]
                dsr = dsr_raw
                if not isinstance(dsr, int) or dsr < 0 or dsr > nrows:
                    dsr = (max(hr) + 1) if hr else 1
                if hr and dsr <= 0:
                    dsr = max(hr) + 1
                if dsr >= nrows:
                    dsr = max(1, max(hr) + 1) if hr else 0
                column_labels = resolve_ui_column_labels({"rows": section_data, "metadata": sec_meta})
            section_data, merges = apply_merged_cell_resolution(
                section_data,
                data_start_row=int(dsr),
                row_label_col=sec_meta.get("row_label_col"),
            )
            sec_meta["header_rows"] = hr
            sec_meta["data_start_row"] = int(dsr)
            if merges:
                sec_meta["horizontal_merges"] = merges
            if column_labels:
                sec_meta["display_column_labels"] = list(column_labels)
            ui_tables.append(
                {
                    "table_id": analysis.get("table_id", ""),
                    "table_type": analysis.get("table_type", "structured"),
                    "description": analysis.get("description", ""),
                    "headers": column_labels,
                    "rows": section_data,
                    "sections": sections,
                    "metadata": sec_meta,
                }
            )
        return ui_tables


# 後方互換
F60UIDeliveryController = G11Controller

__all__ = ["G11Controller", "F60UIDeliveryController"]
