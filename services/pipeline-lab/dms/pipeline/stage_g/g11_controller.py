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

    # アノテーション型 → 行頭プレフィックス（section_break は別処理）
    _LINE_PREFIX: Dict[str, str] = {
        "heading_1": "# ",
        "heading_2": "## ",
        "bullet_item": "- ",
        "blockquote": "> ",
    }

    # 分割マーカー（_split_md_to_articles で分割境界として使う）
    _SPLIT_MARKER = "\x00SPLIT\x00"

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
            tables=stage_f_result.get("tables") or [],
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
    def _apply_typography_markup(text: str, text_lines: List[Dict[str, Any]]) -> str:
        """
        block.text の各行に text_lines のフォント情報を適用して Markdown を生成する。
        元テキストの空行・段落区切り・改行順序を完全保持。
        text_lines に対応エントリがない行は素のまま出力（新テキスト追加ゼロ）。
        """
        from collections import Counter as _Counter
        sizes = [ln.get("size", 0) for ln in text_lines if ln.get("size", 0) > 0]
        body_size = _Counter(sizes).most_common(1)[0][0] if sizes else 12.0

        line_map: Dict[str, Dict[str, Any]] = {}
        for ln in text_lines:
            key = ln.get("text", "").strip()
            if key and key not in line_map:
                line_map[key] = ln

        result: List[str] = []
        for raw_line in text.split("\n"):
            stripped = raw_line.strip()
            if not stripped:
                result.append("")
                continue
            info = line_map.get(stripped)
            if info is None:
                result.append(raw_line)
                continue
            size = info.get("size") or body_size
            bold = info.get("bold", False)
            red = info.get("red", False)
            if size >= body_size * 1.3:
                result.append(f"# {stripped}")
            elif size >= body_size * 1.15:
                result.append(f"## {stripped}")
            elif red and bold:
                result.append(f"**[RED]{stripped}[/RED]**")
            elif red:
                result.append(f"[RED]{stripped}[/RED]")
            elif bold:
                result.append(f"**{stripped}**")
            else:
                result.append(raw_line)
        return "\n".join(result)

    _PDF_SENTENCE_ENDERS = frozenset('。！？」』）】…')
    # 構造行プレフィックス（これで始まる行は前後の行と結合しない）
    _STRUCTURAL_PREFIXES = ('# ', '## ', '- ', '> ', '---')

    @staticmethod
    def _merge_pdf_lines(text: str) -> str:
        """PDF物理行の折り返しを段落単位に結合する。
        - 連続行（\\n 区切り）: ENDERS で終わらない行 → 直結（改行なし結合）
        - 空行（\\n\\n）: 常に保持（改行あり結合 = 段落区切り）
        - 構造行（# ## - > ---）は前後と結合しない
        """
        ENDERS = G11Controller._PDF_SENTENCE_ENDERS
        STRUCT = G11Controller._STRUCTURAL_PREFIXES
        lines = text.split('\n')
        result: List[str] = []

        for line in lines:
            if not line:
                result.append(line)  # 空行は常に保持
            elif result and result[-1] and result[-1][-1] not in ENDERS and not line.startswith('　'):
                if any(result[-1].startswith(p) for p in STRUCT) or any(line.startswith(p) for p in STRUCT):
                    result.append(line)
                else:
                    result[-1] += line
            else:
                result.append(line)

        return '\n'.join(result)

    @staticmethod
    def _get_ai_annotations(text: str, has_typography: bool = False) -> Dict[str, Any]:
        """
        Gemini にテキスト構造のアノテーション指示を JSON で返させる。
        AI はテキストを生成・変更しない。行番号とスパン文字列のみ返す。

        戻り値: {"annotation_types": {型名: 説明, ...}, "annotations": [...]}
        annotation_types は AI がこの文書に合わせて 5〜8 種類自由に定義する。
        """
        if not text.strip():
            return {"annotation_types": {}, "annotations": []}
        try:
            import json as _json
            import google.generativeai as genai
            from dms.common.config.settings import settings
            if not settings.GOOGLE_AI_API_KEY:
                return {"annotation_types": {}, "annotations": []}

            lines = text.split("\n")
            numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(lines))

            if has_typography:
                line_types = (
                    "paragraph_break（前行と結合しない・新しい項目・段落の開始行）, "
                    "section_break（話題の切れ目・新しいセクションの開始行）, "
                    "bullet_item（並列する箇条書き項目）, "
                    "blockquote（注記・引用・お知らせ補足）"
                )
            else:
                line_types = (
                    "paragraph_break（前行と結合しない・新しい項目・段落の開始行）, "
                    "heading_1（文書タイトル等の最重要見出し）, "
                    "heading_2（セクション見出し）, "
                    "section_break（内容のテーマが切り替わる境界行）, "
                    "bullet_item（並列する箇条書き項目）, "
                    "blockquote（注記・引用・お知らせ補足）"
                )

            prompt = (
                "次のテキストの各行を読んで、行レベルの構造型を JSON のみで返してください。\n"
                "【絶対ルール】JSON 以外は一切出力しない。テキストを生成・変更しない。\n\n"
                f"行レベルの型（line キー）: {line_types}\n\n"
                "【bullet_item の使い方】\n"
                "  同じレベルで並ぶ項目リストの行には bullet_item を付ける。\n"
                "  例：「①〇〇」「②〇〇」、「・〇〇」、「項目名：値」が複数並ぶ行など。\n"
                "  散文の途中で改行されただけの継続行には付けない（それは paragraph_break）。\n\n"
                "【paragraph_break の使い方】\n"
                "  このテキストは PDF の物理的な折り返しを含む。\n"
                "  散文で前の行から文が継続する場合は付けない（折り返しは結合する）。\n"
                "  前の行と意味的に独立した新しい段落・見出し行には付ける。\n\n"
                "【blockquote の使い方】\n"
                "  本文と別扱いの注記・補足・条件付きお知らせには blockquote を付ける。\n"
                "  例：「※〜」「＊注意〜」「〔補足〕〜」「なお〜」「ただし〜」など、\n"
                "  条件・例外・注意書きとして添えられた行。\n\n"
                "【heading_1 / heading_2 の使い方】\n"
                "  短い見出し行（タイトル・セクション名）には heading_1 または heading_2 を付ける。\n"
                "  heading_1/2 が付いた行は自動的にセクション分割の境界になるので、\n"
                "  見出し行に section_break は不要（重複して付けない）。\n\n"
                "【section_break の使い方】\n"
                "  見出し行ではないが内容のテーマが切り替わる境界行に付ける。\n"
                "  見出し行には heading_1/2 を使い、section_break は使わない。\n\n"
                "出力形式（例）:\n"
                '{"annotations": [{"line": 0, "type": "heading_1"}, '
                '{"line": 3, "type": "bullet_item"}, '
                '{"line": 4, "type": "bullet_item"}, '
                '{"line": 9, "type": "heading_2"}, '
                '{"line": 14, "type": "paragraph_break"}]}\n\n'
                f"テキスト:\n{numbered}"
            )

            genai.configure(api_key=settings.GOOGLE_AI_API_KEY)
            model = genai.GenerativeModel("gemini-2.5-flash-lite")
            resp = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                ),
            )
            data = _json.loads(resp.text.strip())
            return {
                "annotation_types": data.get("annotation_types") or {},
                "annotations": data.get("annotations") or [],
            }
        except Exception as e:
            logger.warning(f"[G21] AI アノテーション取得失敗: {e}")
            return {"annotation_types": {}, "annotations": []}

    @staticmethod
    def _apply_annotations(md: str, annotations: List[Dict[str, Any]]) -> str:
        """
        AI アノテーション指示を md テキストに機械的に適用する。
        元テキストの文字は変えない。行頭プレフィックスとスパンタグのみ挿入。
        スパン型は [TYPE_NAME]...[/TYPE_NAME] に統一（型名は大文字化）。

        行結合: AI が paragraph_break を付けなかった連続行（散文の折り返し）を結合する。
        空行（\\n\\n）は常に保持する。
        """
        lines = md.split("\n")

        # 行結合を抑止するインデックス: 構造系アノテーションはすべて暗黙の paragraph_break
        no_merge: set = set()

        _LINE_TYPES = frozenset({"paragraph_break", "section_break", "heading_1", "heading_2", "bullet_item", "blockquote"})
        for ann in annotations:
            if "line" in ann:
                idx = ann.get("line")
                ann_type = ann.get("type", "")
                if not isinstance(idx, int) or idx < 0 or idx >= len(lines):
                    continue
                if ann_type not in _LINE_TYPES:
                    continue  # スパン型が誤って line キーで来た場合は無視
                no_merge.add(idx)
                if ann_type == "section_break":
                    if not lines[idx].startswith(G11Controller._SPLIT_MARKER) and \
                       not lines[idx].startswith("# ") and \
                       not lines[idx].startswith("## "):
                        lines[idx] = G11Controller._SPLIT_MARKER + lines[idx]
                    continue
                if ann_type == "paragraph_break":
                    continue
                prefix = G11Controller._LINE_PREFIX.get(ann_type)
                if prefix is None:
                    continue
                line = lines[idx]
                if line.startswith("# ") and ann_type in ("heading_1", "heading_2"):
                    continue
                if line.startswith("## ") and ann_type == "heading_2":
                    continue
                if line.startswith(prefix):
                    continue
                lines[idx] = prefix + line

        _HEAD_PREFIXES = ("# ", "## ", G11Controller._SPLIT_MARKER)

        # 散文折り返し結合: no_merge に含まれない連続行を直結する
        merged: List[str] = []
        for i, line in enumerate(lines):
            if not line:
                merged.append(line)  # 空行は保持
            elif merged and merged[-1] and i not in no_merge and not merged[-1].startswith(_HEAD_PREFIXES):
                merged[-1] += line
            else:
                merged.append(line)
        result = "\n".join(merged)

        for ann in annotations:
            if "span" in ann:
                span_text = ann.get("span", "")
                ann_type = ann.get("type", "").upper()
                if not ann_type or not span_text or span_text not in result:
                    continue
                open_tag = f"[{ann_type}]"
                close_tag = f"[/{ann_type}]"
                if f"{open_tag}{span_text}{close_tag}" in result:
                    continue
                import re as _re
                if _re.search(r'\[[A-Z][A-Z0-9_]*\][^\[]*?' + _re.escape(span_text), result):
                    continue
                result = result.replace(span_text, f"{open_tag}{span_text}{close_tag}", 1)

        return result

    @staticmethod
    def _split_md_to_articles(full_md: str) -> List[Dict[str, str]]:
        """Markdown を # / ## 見出し と section_break マーカーで article に分割する。"""
        import re as _re
        _strip_spans = lambda s: _re.sub(r'\[/?[A-Z][A-Z0-9_]*\]', '', s).strip()
        marker = G11Controller._SPLIT_MARKER
        # section_break マーカーを改行付き分割境界に統一してから分割
        normalized = full_md.replace(marker, "\n" + marker)
        parts = _re.split(r'\n(?=#{1,2} |\x00SPLIT\x00)', "\n" + normalized)
        articles = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # section_break マーカーを除去してから処理
            part = part.replace(marker, "").strip()
            if not part:
                continue
            lines = part.split("\n")
            if lines[0].startswith("## ") or lines[0].startswith("# "):
                title = ""
                body = "\n".join(lines).strip()
            else:
                title = ""
                body = part
            if body:
                articles.append({"title": title, "body": body})
        return articles

    @staticmethod
    def _merge_small_articles(
        articles: List[Dict[str, Any]],
        min_body_chars: int = 80,
        max_articles: int = 8,
    ) -> List[Dict[str, Any]]:
        """
        細かく分割しすぎた article を統合する。

        - 本文が min_body_chars 未満の article は前の article に結合する
        - それでも max_articles を超える場合は末尾から順に結合して上限に収める
        """
        if len(articles) <= 1:
            return articles

        merged: List[Dict[str, Any]] = []
        for art in articles:
            body = (art.get("body") or "").strip()
            has_heading = body.startswith("# ") or body.startswith("## ")
            if merged and not has_heading and len(body) < min_body_chars:
                prev = merged[-1]
                prev_body = (prev.get("body") or "").strip()
                prev["body"] = (prev_body + "\n" + body).strip() if prev_body else body
            else:
                merged.append(dict(art))

        while len(merged) > max_articles:
            # 末尾2つを結合
            last = merged.pop()
            prev = merged[-1]
            last_body = (last.get("body") or "").strip()
            prev_body = (prev.get("body") or "").strip()
            prev["body"] = (prev_body + "\n" + last_body).strip() if prev_body else last_body

        return merged

    @staticmethod
    def _has_table_between(
        block_a: Dict[str, Any],
        block_b: Dict[str, Any],
        tables: List[Dict[str, Any]],
    ) -> bool:
        """2つの連続するブロック間に表が存在するか判定。"""
        page_a = block_a.get('page', 0)
        page_b = block_b.get('page', 0)
        y_end_a = block_a.get('y1', block_a.get('y0', 0))
        y_start_b = block_b.get('y0', 0)
        for table in tables:
            t_page = table.get('page', 0)
            bbox = table.get('bbox')
            if not bbox:
                continue
            _, ty0, _, ty1 = bbox
            if page_a == page_b == t_page:
                if ty0 >= y_end_a and ty1 <= y_start_b + 5:
                    return True
            elif t_page == page_a and page_a != page_b:
                if ty0 >= y_end_a:
                    return True
            elif t_page == page_b and page_a != page_b:
                if ty1 <= y_start_b + 5:
                    return True
        return False

    @staticmethod
    def _group_blocks_by_tables(
        blocks: List[Dict[str, Any]],
        tables: List[Dict[str, Any]],
    ) -> List[List[Dict[str, Any]]]:
        """表で区切られるブロックを別グループに分割する。"""
        if not blocks:
            return []
        groups: List[List[Dict[str, Any]]] = [[blocks[0]]]
        for i in range(1, len(blocks)):
            if G11Controller._has_table_between(blocks[i - 1], blocks[i], tables):
                groups.append([blocks[i]])
            else:
                groups[-1].append(blocks[i])
        return groups

    @staticmethod
    def _g21_articles(
        non_table_text: str,
        non_table_text_blocks: List[Dict[str, Any]] = None,
        tables: List[Dict[str, Any]] = None,
    ) -> List[Dict[str, str]]:
        """
        非表テキストブロックに構造アノテーションを付加して article リストを返す。

        フロー:
          1. 全ブロックを表の位置でグループ分け（表で区切られたブロックは別グループ）
          2. グループ内ブロックに typography_markup を適用して \n\n 結合
          3. グループごとに _get_ai_annotations を1回呼ぶ（全文を文脈として渡す）
          4. _apply_annotations でプログラムが指示を機械的に適用
        """
        blocks = non_table_text_blocks or []
        if not blocks and not (non_table_text or "").strip():
            return []

        all_articles: List[Dict[str, str]] = []
        if blocks:
            groups = G11Controller._group_blocks_by_tables(blocks, tables or [])
            for group in groups:
                md_parts: List[str] = []
                has_any_typography = False
                for block in group:
                    text = (block.get("text") or "").strip()
                    if not text:
                        continue
                    text_lines = block.get("text_lines") or []
                    if text_lines:
                        md_parts.append(G11Controller._apply_typography_markup(text, text_lines))
                        has_any_typography = True
                    else:
                        md_parts.append(text)
                if not md_parts:
                    continue
                full_text = "\n".join(G11Controller._merge_pdf_lines(part) for part in md_parts)
                result = G11Controller._get_ai_annotations(full_text, has_typography=has_any_typography)
                annotations = result.get("annotations") or []
                full_md = G11Controller._apply_annotations(full_text, annotations)
                articles = G11Controller._split_md_to_articles(full_md)
                if not articles:
                    articles = [{"title": "", "body": full_md}]
                all_articles.extend(articles)
        else:
            nt = G11Controller._merge_pdf_lines((non_table_text or "").strip())
            if not nt:
                return []
            result = G11Controller._get_ai_annotations(nt, has_typography=False)
            annotations = result.get("annotations") or []
            full_md = G11Controller._apply_annotations(nt, annotations)
            articles = G11Controller._split_md_to_articles(full_md)
            if not articles:
                articles = [{"title": "", "body": full_md}]
            all_articles.extend(articles)

        if not all_articles:
            return []
        return G11Controller._merge_small_articles(all_articles)

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
