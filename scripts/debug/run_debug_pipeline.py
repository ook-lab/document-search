#!/usr/bin/env python3
"""
デバッグパイプライン実行スクリプト

各ステージの結果をローカルに保存し、特定ステージだけの再実行を可能にする。

使用例:
  # 全行程を実行（キャッシュがあればスキップ）
  python run_debug_pipeline.py [UUID] --pdf path/to/file.pdf

  # E7Lだけを再実行（E6のキャッシュを使用）
  python run_debug_pipeline.py [UUID] --stage E7L --force

  # F1から最後まで再実行
  python run_debug_pipeline.py [UUID] --stage F1 --mode from --force

  # タグ付きで保存（比較用）
  python run_debug_pipeline.py [UUID] --stage E7P --tag "v2_patch"
"""

import os
import sys
import json
import argparse
import asyncio
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger

# ステージのインポート
from shared.ai.llm_client.llm_client import LLMClient
from shared.pipeline.stage_e import E6VisionOCR, E7LMergeDetector, E7PPatchApplier, E8BboxNormalizer
from shared.pipeline.stage_f import F1GridDetector, F2StructureAnalyzer, F3CellAssigner
from shared.pipeline.stage_g import G3Scrub, G4Assemble, G5Audit, G6Packager
from shared.pipeline.stage_g.g7_header_detector import G7HeaderDetector
from shared.pipeline.stage_g.g8_header_enricher import G8HeaderEnricher
from shared.pipeline.stage_h import StageH1Table, StageH2Text

# PDF→画像変換
try:
    from pdf2image import convert_from_path
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False
    logger.warning("pdf2image not installed")


class DebugPipeline:
    """デバッグ用パイプライン（ステージ結果をローカル保存）"""

    # ステージ定義（実行順序）
    STAGES = ["E6", "E7L", "E7P", "E8", "F1", "F2", "F3", "G3", "G4", "G5", "G6", "G7", "G8", "H1", "H2"]

    def __init__(
        self,
        uuid: str,
        base_dir: str = "debug_output",
        tag: Optional[str] = None
    ):
        self.uuid = uuid
        self.tag = tag
        self.output_dir = Path(base_dir) / uuid
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # LLMクライアント
        self.llm_client = LLMClient()

        # ステージインスタンス
        self._e6_ocr = E6VisionOCR()
        self._e7l = E7LMergeDetector(self.llm_client)
        self._e7p = E7PPatchApplier()
        self._e8_normalizer = E8BboxNormalizer()
        self._f1_detector = F1GridDetector()
        self._f2_analyzer = F2StructureAnalyzer(self.llm_client)
        self._f3_assigner = F3CellAssigner()
        self._g3_scrub = G3Scrub()
        self._g4_assemble = G4Assemble()
        self._g5_audit = G5Audit()
        self._g6_packager = G6Packager()
        self._g7_header = G7HeaderDetector(self.llm_client)
        self._g8_enricher = G8HeaderEnricher()
        self._h1_table = StageH1Table(self.llm_client)
        self._h2_text = StageH2Text(self.llm_client)

        logger.info(f"DebugPipeline initialized: uuid={uuid}, output_dir={self.output_dir}")

    def _get_filename(self, stage_name: str) -> Path:
        """ステージ結果のファイル名を生成"""
        if self.tag:
            return self.output_dir / f"{self.uuid}_{stage_name}_{self.tag}.json"
        return self.output_dir / f"{self.uuid}_{stage_name}.json"

    class _NumpyEncoder(json.JSONEncoder):
        """numpy型をPythonネイティブ型に変換するJSONエンコーダー"""
        def default(self, obj):
            import numpy as np
            if isinstance(obj, np.bool_):
                return bool(obj)
            if isinstance(obj, np.floating):
                return float(obj)
            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            return super().default(obj)

    def save_stage(self, stage_name: str, data: Any) -> Path:
        """ステージ結果を保存"""
        file_path = self._get_filename(stage_name)

        # 既存ファイルがあれば .bak に退避
        if file_path.exists():
            bak_path = file_path.with_suffix(".json.bak")
            file_path.replace(bak_path)
            logger.debug(f"Backup created: {bak_path}")

        # 保存（numpy型安全網付き）
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, cls=self._NumpyEncoder)

        logger.info(f"[{stage_name}] 結果を保存: {file_path}")
        return file_path

    def load_stage(self, stage_name: str) -> Optional[Dict[str, Any]]:
        """ステージ結果を読み込み"""
        file_path = self._get_filename(stage_name)
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info(f"[{stage_name}] キャッシュ読み込み: {file_path}")
                return data
            except json.JSONDecodeError as e:
                logger.warning(f"[{stage_name}] キャッシュ破損（JSONパース失敗）: {file_path} - {e}")
                return None
        return None

    def should_run(
        self,
        current_stage: str,
        target_stage: Optional[str],
        mode: str,
        force: bool,
        has_cache: bool
    ) -> bool:
        """実行すべきか判定"""
        if mode == "only":
            # 指定ステージのみ
            if current_stage != target_stage:
                return False
            return force or not has_cache

        if mode == "from":
            # 指定ステージ以降
            if target_stage and target_stage in self.STAGES:
                if self.STAGES.index(current_stage) < self.STAGES.index(target_stage):
                    return False
            return force or not has_cache

        # mode == "all"
        return force or not has_cache

    def run(
        self,
        pdf_path: Optional[str] = None,
        image_path: Optional[str] = None,
        target_stage: Optional[str] = None,
        mode: str = "all",
        force: bool = False
    ) -> Dict[str, Any]:
        """
        パイプライン実行

        Args:
            pdf_path: PDFファイルパス
            image_path: 画像ファイルパス（PDFがない場合）
            target_stage: 対象ステージ
            mode: "all" | "only" | "from"
            force: キャッシュを無視して強制実行
        """
        start_time = time.time()
        logger.info("=" * 60)
        logger.info(f"Debug Pipeline Start: {self.uuid}")
        logger.info(f"  mode={mode}, target={target_stage}, force={force}")
        logger.info("=" * 60)

        results = {}
        errors = []

        # 画像準備
        pil_img = None
        img_path_str = None
        page_size = None

        if pdf_path and Path(pdf_path).exists():
            if PDF2IMAGE_AVAILABLE:
                images = convert_from_path(pdf_path, dpi=150)
                if images:
                    pil_img = images[0]
                    # 一時ファイルとして保存
                    temp_img_path = self.output_dir / f"{self.uuid}_page0.png"
                    pil_img.save(temp_img_path, format='PNG')
                    img_path_str = str(temp_img_path)
                    page_size = {'w': pil_img.width, 'h': pil_img.height}
                    logger.info(f"PDF→画像変換: {page_size}")
        elif image_path and Path(image_path).exists():
            from PIL import Image
            pil_img = Image.open(image_path)
            img_path_str = image_path
            page_size = {'w': pil_img.width, 'h': pil_img.height}

        if not pil_img:
            logger.error("入力画像がありません")
            return {"error": "No input image"}

        try:
            # ================================================
            # E6: Vision OCR
            # ================================================
            stage = "E6"
            e6_data = self.load_stage(stage)
            if self.should_run(stage, target_stage, mode, force, e6_data is not None):
                logger.info(f"[{stage}] 実行中...")
                e6_result = self._e6_ocr.extract(Path(img_path_str), page_size['w'], page_size['h'])
                e6_data = {
                    "vision_tokens": e6_result.get("vision_tokens", []),
                    "success": e6_result.get("success", False),
                    "stats": e6_result.get("stats", {})
                }
                self.save_stage(stage, e6_data)
            results[stage] = e6_data

            if not e6_data:
                raise FileNotFoundError(f"[{stage}] キャッシュがありません")

            # ================================================
            # E7L: LLM差分抽出（接着候補の検出のみ）
            # ================================================
            stage = "E7L"
            e7l_data = self.load_stage(stage)
            if self.should_run(stage, target_stage, mode, force, e7l_data is not None):
                logger.info(f"[{stage}] 実行中...")
                vision_tokens = e6_data.get("vision_tokens", [])
                merge_instructions = self._e7l.detect(vision_tokens, image_path=img_path_str)
                e7l_data = {
                    "merge_instructions": merge_instructions,
                    "input_count": len(vision_tokens),
                    "merge_count": len(merge_instructions)
                }
                self.save_stage(stage, e7l_data)
            results[stage] = e7l_data

            if not e7l_data:
                raise FileNotFoundError(f"[{stage}] キャッシュがありません")

            # ================================================
            # E7P: Pythonパッチ適用（物理結合の実行）
            # ================================================
            stage = "E7P"
            e7p_data = self.load_stage(stage)
            if self.should_run(stage, target_stage, mode, force, e7p_data is not None):
                logger.info(f"[{stage}] 実行中...")
                vision_tokens = e6_data.get("vision_tokens", [])
                merge_instructions = e7l_data.get("merge_instructions", [])
                merged_tokens = self._e7p.apply(vision_tokens, merge_instructions)
                e7p_data = {
                    "merged_tokens": merged_tokens,
                    "input_count": len(vision_tokens),
                    "output_count": len(merged_tokens)
                }
                self.save_stage(stage, e7p_data)
            results[stage] = e7p_data

            if not e7p_data:
                raise FileNotFoundError(f"[{stage}] キャッシュがありません")

            # ================================================
            # E8: BBox Normalizer
            # ================================================
            stage = "E8"
            e8_data = self.load_stage(stage)
            if self.should_run(stage, target_stage, mode, force, e8_data is not None):
                logger.info(f"[{stage}] 実行中...")
                merged_tokens = e7p_data.get("merged_tokens", [])
                normalized_tokens = self._e8_normalizer.normalize(merged_tokens, page_size)
                e8_data = {
                    "normalized_tokens": normalized_tokens,
                    "page_size": page_size
                }
                self.save_stage(stage, e8_data)
            results[stage] = e8_data

            if not e8_data:
                raise FileNotFoundError(f"[{stage}] キャッシュがありません")

            # ================================================
            # F1: Grid Detector
            # ================================================
            stage = "F1"
            f1_data = self.load_stage(stage)
            if self.should_run(stage, target_stage, mode, force, f1_data is not None):
                logger.info(f"[{stage}] 実行中...")
                f1_result = self._f1_detector.detect(
                    pdf_path=Path(pdf_path) if pdf_path else None,
                    page_image=pil_img,
                    page_num=0,
                    page_size=page_size
                )
                f1_data = f1_result
                self.save_stage(stage, f1_data)
            results[stage] = f1_data

            if not f1_data:
                raise FileNotFoundError(f"[{stage}] キャッシュがありません")

            # ================================================
            # F2: Structure Analyzer
            # ================================================
            stage = "F2"
            f2_data = self.load_stage(stage)
            if self.should_run(stage, target_stage, mode, force, f2_data is not None):
                logger.info(f"[{stage}] 実行中...")
                line_candidates = f1_data.get("line_candidates", {"horizontal": [], "vertical": []})
                table_bbox_candidate = f1_data.get("table_bbox_candidate")
                panel_candidates = f1_data.get("panel_candidates", [])
                separator_candidates_all = f1_data.get("separator_candidates_all", [])
                separator_candidates_ranked = f1_data.get("separator_candidates_ranked", [])
                tokens = e8_data.get("normalized_tokens", [])

                # tokensをchunk_blocks形式に変換
                chunk_blocks = [
                    {
                        "block_id": f"b{i}",
                        "text": t.get("text", ""),
                        "bbox": t.get("bbox", [0, 0, 0, 0]),
                        "coords": {"bbox": t.get("bbox", [0, 0, 0, 0])}
                    }
                    for i, t in enumerate(tokens)
                ]

                f2_result = self._f2_analyzer.analyze(
                    line_candidates=line_candidates,
                    tokens=chunk_blocks,
                    page_image=pil_img,
                    page_size=page_size,
                    table_bbox_candidate=table_bbox_candidate,
                    panel_candidates=panel_candidates,
                    separator_candidates_all=separator_candidates_all,
                    separator_candidates_ranked=separator_candidates_ranked,
                    doc_type="debug"
                )
                f2_data = f2_result
                self.save_stage(stage, f2_data)
            results[stage] = f2_data

            if not f2_data:
                raise FileNotFoundError(f"[{stage}] キャッシュがありません")

            # ================================================
            # F3: Cell Assigner（マルチパネル3点座標対応）
            # ================================================
            stage = "F3"
            f3_data = self.load_stage(stage)
            if self.should_run(stage, target_stage, mode, force, f3_data is not None):
                logger.info(f"[{stage}] 実行中...")
                grid = f2_data.get("grid", {})
                tokens = e8_data.get("normalized_tokens", [])
                structure = f2_data
                f2_panels = f2_data.get("panels", [])

                # tokensをchunk_blocks形式に変換
                chunk_blocks = [
                    {
                        "block_id": f"b{i}",
                        "text": t.get("text", ""),
                        "bbox": t.get("bbox", [0, 0, 0, 0]),
                        "coords": {"bbox": t.get("bbox", [0, 0, 0, 0])}
                    }
                    for i, t in enumerate(tokens)
                ]

                f3_result, low_confidence = self._f3_assigner.assign(
                    grid=grid,
                    tokens=chunk_blocks,
                    structure=structure,
                    panels=f2_panels
                )
                f3_data = {
                    "structured_table": f3_result,
                    "low_confidence": low_confidence
                }
                self.save_stage(stage, f3_data)
            results[stage] = f3_data

            if not f3_data:
                raise FileNotFoundError(f"[{stage}] キャッシュがありません")

            # ================================================
            # G3: Scrub
            # ================================================
            stage = "G3"
            g3_data = self.load_stage(stage)
            if self.should_run(stage, target_stage, mode, force, g3_data is not None):
                logger.info(f"[{stage}] 実行中...")
                g3_result = self._g3_scrub.scrub(
                    structured_table=f3_data.get("structured_table", {}),
                    logical_structure=f2_data,
                    e_physical_chars=[],
                    f1_quality=f1_data.get("quality", 1.0)
                )
                g3_data = g3_result
                self.save_stage(stage, g3_data)
            results[stage] = g3_data

            if not g3_data:
                raise FileNotFoundError(f"[{stage}] キャッシュがありません")

            # ================================================
            # G4: Assemble
            # ================================================
            stage = "G4"
            g4_data = self.load_stage(stage)
            if self.should_run(stage, target_stage, mode, force, g4_data is not None):
                logger.info(f"[{stage}] 実行中...")
                g4_result = self._g4_assemble.assemble(
                    scrubbed_core=g3_data,
                    logical_structure=f2_data,
                    metadata={"uuid": self.uuid}
                )
                g4_data = g4_result
                self.save_stage(stage, g4_data)
            results[stage] = g4_data

            if not g4_data:
                raise FileNotFoundError(f"[{stage}] キャッシュがありません")

            # ================================================
            # G5: Audit
            # ================================================
            stage = "G5"
            g5_data = self.load_stage(stage)
            if self.should_run(stage, target_stage, mode, force, g5_data is not None):
                logger.info(f"[{stage}] 実行中...")
                g5_result = self._g5_audit.audit(
                    assembled_payload=g4_data,
                    post_body={},
                    metadata={"uuid": self.uuid}
                )
                g5_data = g5_result
                self.save_stage(stage, g5_data)
            results[stage] = g5_data

            if not g5_data:
                raise FileNotFoundError(f"[{stage}] キャッシュがありません")

            # ================================================
            # G6: Packager
            # ================================================
            stage = "G6"
            g6_data = self.load_stage(stage)
            if self.should_run(stage, target_stage, mode, force, g6_data is not None):
                logger.info(f"[{stage}] 実行中...")
                g6_result = self._g6_packager.package(
                    scrubbed_data=g5_data,
                    targets=["db", "search", "ui"]
                )
                g6_data = g6_result
                self.save_stage(stage, g6_data)
            results[stage] = g6_data

            if not g6_data:
                raise FileNotFoundError(f"[{stage}] キャッシュがありません")

            # ================================================
            # G7: Header Detector（構造ベースのヘッダー検出）
            # ================================================
            stage = "G7"
            g7_data = self.load_stage(stage)
            if self.should_run(stage, target_stage, mode, force, g7_data is not None):
                logger.info(f"[{stage}] 実行中...")

                # G4の tables（cells_flat 付き）を入力にする
                g4_tables = g4_data.get("tables", [])
                if not g4_tables:
                    # G4にtablesがない場合、path_a_result経由で構築
                    path_a = g4_data.get("path_a_result", g5_data.get("path_a_result", {}))
                    tagged_texts = path_a.get("tagged_texts", [])
                    cell_tokens = [t for t in tagged_texts if t.get("type") == "cell"]
                    if cell_tokens:
                        g4_tables = [{"ref_id": "T0", "cells_flat": cell_tokens}]

                g7_result = self._g7_header.process(g4_tables)

                # 結果ログ
                logger.info("=" * 60)
                logger.info(f"[G7 RESULT] === ヘッダー検出結果 ===")
                for tbl in g7_result:
                    ref_id = tbl.get("ref_id", "?")
                    hmap = tbl.get("header_map", {})
                    panels = hmap.get("panels", {})
                    logger.info(f"[G7 RESULT] {ref_id}:")
                    for pk, pv in panels.items():
                        ch_rows = pv.get("col_header_rows", [])
                        rh_cols = pv.get("row_header_cols", [])
                        logger.info(f"[G7 RESULT]   {pk}: col_header_rows={ch_rows}, row_header_cols={rh_cols}")

                        # ヘッダー行/列の実際のテキストをログ出力
                        cells_flat = tbl.get("cells_flat", [])
                        pid_str = pk.replace("P", "")
                        for r in ch_rows:
                            row_texts = [
                                c.get("text", "")
                                for c in cells_flat
                                if str(c.get("panel_id", 0) or 0) == pid_str and c.get("row") == r
                            ]
                            logger.info(f"[G7 RESULT]     col_header R{r} texts: {row_texts}")
                        # ローカル列番号→グローバル列番号の逆引きマップ構築
                        panel_cells = [
                            c for c in cells_flat
                            if str(c.get("panel_id", 0) or 0) == pid_str
                        ]
                        unique_cols = sorted(set(c.get("col", 0) for c in panel_cells))
                        local_to_global = {lc: gc for lc, gc in enumerate(unique_cols)}
                        for col_local in rh_cols:
                            col_global = local_to_global.get(col_local, col_local)
                            col_texts = [
                                c.get("text", "")
                                for c in panel_cells
                                if c.get("col") == col_global
                            ]
                            logger.info(f"[G7 RESULT]     row_header C{col_local}(global={col_global}) texts: {col_texts}")
                logger.info("=" * 60)

                g7_data = {"tables": g7_result}
                self.save_stage(stage, g7_data)
            results[stage] = g7_data

            if not g7_data:
                raise FileNotFoundError(f"[{stage}] キャッシュがありません")

            # ================================================
            # G8: Header Enricher（ヘッダー紐付け強化）
            # ================================================
            stage = "G8"
            g8_data = self.load_stage(stage)
            if self.should_run(stage, target_stage, mode, force, g8_data is not None):
                logger.info(f"[{stage}] 実行中...")

                # G7の tables（cells_flat + header_map 付き）を入力にする
                g7_tables = g7_data.get("tables", [])
                g8_result = self._g8_enricher.process(g7_tables)

                # 結果ログ
                logger.info("=" * 60)
                logger.info(f"[G8 RESULT] === ヘッダー紐付け結果 ===")
                for tbl in g8_result:
                    ref_id = tbl.get("ref_id", "?")
                    enriched = tbl.get("cells_enriched", [])
                    data_cells = [c for c in enriched if not c.get("is_header")]
                    logger.info(f"[G8 RESULT] {ref_id}: {len(enriched)}セル (データ: {len(data_cells)})")
                    for c in data_cells:
                        logger.info(f"[G8 RESULT]   {c.get('enriched_text', '')[:100]}")
                logger.info("=" * 60)

                g8_data = {"tables": g8_result}
                self.save_stage(stage, g8_data)
            results[stage] = g8_data

            if not g8_data:
                raise FileNotFoundError(f"[{stage}] キャッシュがありません")

            # ================================================
            # H1: Table Specialist（表処理専門）
            # ================================================
            stage = "H1"
            h1_data = self.load_stage(stage)
            if self.should_run(stage, target_stage, mode, force, h1_data is not None):
                logger.info(f"[{stage}] 実行中...")

                # G6のdb出力からtablesとcellsを取得
                db_payload = g6_data.get("payload_for_db", {})
                tables_raw = db_payload.get("tables", [])
                cells_raw = db_payload.get("cells", [])

                # G5のfull_text_orderedを取得
                full_text_ordered = g5_data.get("path_a_result", {}).get("full_text_ordered", "")

                # G4で付与されたcontext_for情報を活用
                tagged_texts = g5_data.get("path_a_result", {}).get("tagged_texts", [])
                table_contexts = [
                    t for t in tagged_texts
                    if t.get("context_for") and t.get("type") == "untagged"
                ]
                if table_contexts:
                    logger.info(f"[{stage}] 表コンテキスト検出: {len(table_contexts)}件")
                    for ctx in table_contexts:
                        logger.info(f"[{stage}]   '{ctx.get('text')}' -> {ctx.get('context_for')}")

                # table_inventoryを構築（tablesにcellsを紐付け）
                table_inventory = []
                for tbl in tables_raw:
                    anchor_id = tbl.get("anchor_id", "")
                    # このテーブルに属するセルを抽出（row/col情報があれば使用）
                    tbl_cells = [c for c in cells_raw if c.get("text")]  # 空でないセル

                    # コンテキスト（タイトル）を取得
                    tbl_context = [ctx.get("text", "") for ctx in table_contexts
                                   if ctx.get("context_for") == anchor_id]
                    table_title = " ".join(tbl_context) if tbl_context else ""

                    # G7のheader_mapを探す
                    g7_tables = g7_data.get("tables", [])
                    header_map = {}
                    for g7t in g7_tables:
                        if g7t.get("ref_id") == anchor_id:
                            header_map = g7t.get("header_map", {})
                            break
                    # ref_idで見つからなければインデックスで取得
                    if not header_map and len(g7_tables) > len(table_inventory):
                        idx = len(table_inventory)
                        if idx < len(g7_tables):
                            header_map = g7_tables[idx].get("header_map", {})

                    table_inventory.append({
                        "ref_id": anchor_id,
                        "table_title": table_title,
                        "table_type": "generic",
                        "row_count": tbl.get("row_count", 0),
                        "col_count": tbl.get("col_count", 0),
                        "x_headers": tbl.get("x_headers", []),
                        "y_headers": tbl.get("y_headers", []),
                        "cells": tbl_cells,
                        "is_heavy": tbl.get("is_heavy", False),
                        "header_map": header_map,
                    })

                # G8の cells_enriched を table_inventory に追加
                g8_tables = g8_data.get("tables", [])
                for i, inv in enumerate(table_inventory):
                    ref_id = inv.get("ref_id")
                    matched = False
                    for g8t in g8_tables:
                        if g8t.get("ref_id") == ref_id:
                            inv["cells_enriched"] = g8t.get("cells_enriched", [])
                            inv["cells_flat"] = g8t.get("cells_flat", [])
                            matched = True
                            break
                    if not matched and i < len(g8_tables):
                        inv["cells_enriched"] = g8_tables[i].get("cells_enriched", [])
                        inv["cells_flat"] = g8_tables[i].get("cells_flat", [])

                logger.info(f"[{stage}] table_inventory構築: {len(table_inventory)}表, cells={len(cells_raw)}")
                for inv in table_inventory:
                    enriched_count = len(inv.get('cells_enriched', []))
                    logger.info(f"[{stage}]   {inv.get('ref_id')}: cells_enriched={enriched_count}, header_map={inv.get('header_map', {})}")

                # E8のトークン座標を取得（肩付き注釈の精密判定用）
                raw_tokens = e8_data.get("normalized_tokens", [])
                logger.info(f"[{stage}] E8トークン数: {len(raw_tokens)}")

                h1_result = self._h1_table.process(
                    table_inventory=table_inventory,
                    doc_type="default",
                    workspace="debug",
                    unified_text=full_text_ordered,
                    raw_tokens=raw_tokens  # E8トークン座標を渡す
                )
                # コンテキスト情報を追加
                h1_result["table_contexts"] = table_contexts
                h1_data = h1_result
                self.save_stage(stage, h1_data)
            results[stage] = h1_data

            if not h1_data:
                raise FileNotFoundError(f"[{stage}] キャッシュがありません")

            # ================================================
            # H2: Text Specialist（テキスト処理専門）
            # ================================================
            stage = "H2"
            h2_data = self.load_stage(stage)
            if self.should_run(stage, target_stage, mode, force, h2_data is not None):
                logger.info(f"[{stage}] 実行中...")

                # G4でソートされたfull_text_orderedを使用（読み順保証）
                full_text_ordered = g5_data.get("path_a_result", {}).get("full_text_ordered", "")

                # H1で軽量化されたテキスト（表テキスト削除済み）
                reduced_text = h1_data.get("reduced_text", full_text_ordered)

                # 表コンテキストをプロンプトに組み込む
                table_contexts = h1_data.get("table_contexts", [])
                context_hint = ""
                if table_contexts:
                    context_texts = [ctx.get("text", "") for ctx in table_contexts]
                    context_hint = f"\n\n【表タイトル/見出し】\n" + "\n".join(context_texts)

                # H2用プロンプトテンプレート（$変数はTemplate.substituteで置換される）
                h2_prompt_template = """以下のドキュメントを解析し、JSON形式で出力してください。

【ファイル名】$file_name
【ドキュメントタイプ】$doc_type
【現在日付】$current_date

【ドキュメント本文】
$combined_text

【出力形式】
以下のJSON形式で出力してください：
```json
{
  "title": "ドキュメントのタイトル",
  "document_date": "YYYY-MM-DD形式の日付（不明な場合はnull）",
  "summary": "ドキュメントの要約（100-200文字）",
  "tags": ["タグ1", "タグ2"],
  "calendar_events": [
    {"event_date": "YYYY-MM-DD", "event_name": "イベント名"}
  ],
  "tasks": [
    {"task_name": "タスク名", "deadline": "YYYY-MM-DD"}
  ],
  "metadata": {}
}
```"""

                h2_result = self._h2_text.process(
                    file_name=self.uuid,
                    doc_type="default",
                    workspace="debug",
                    reduced_text=reduced_text + context_hint,
                    prompt=h2_prompt_template,
                    model="gemini-2.0-flash",
                    h1_result=h1_data,
                    stage_f_structure=f2_data,
                    stage_g_result=g5_data
                )
                h2_data = h2_result
                self.save_stage(stage, h2_data)
            results[stage] = h2_data

        except FileNotFoundError as e:
            logger.error(str(e))
            errors.append(str(e))
        except Exception as e:
            logger.error(f"パイプラインエラー: {e}", exc_info=True)
            errors.append(str(e))

        # サマリー出力
        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info(f"Debug Pipeline Completed: {self.uuid}")
        logger.info(f"  elapsed: {elapsed:.2f}s")
        logger.info(f"  stages: {list(results.keys())}")
        if errors:
            logger.error(f"  errors: {errors}")
        logger.info("=" * 60)

        return {
            "uuid": self.uuid,
            "elapsed": elapsed,
            "results": {k: "saved" for k in results.keys()},
            "errors": errors,
            "output_dir": str(self.output_dir)
        }


def main():
    parser = argparse.ArgumentParser(
        description="デバッグパイプライン - ステージ結果をローカル保存",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # 全行程を実行
  python run_debug_pipeline.py abc123 --pdf input.pdf

  # E7Lだけを再実行
  python run_debug_pipeline.py abc123 --stage E7L --force

  # E7Pだけを再実行（E7Lのキャッシュを使用）
  python run_debug_pipeline.py abc123 --stage E7P --force

  # F1から最後まで再実行
  python run_debug_pipeline.py abc123 --stage F1 --mode from --force

  # タグ付きで保存（比較用）
  python run_debug_pipeline.py abc123 --stage E7P --tag "v2_test" --force
        """
    )
    parser.add_argument("uuid", help="対象のUUID（任意の識別子）")
    parser.add_argument("--pdf", help="PDFファイルパス")
    parser.add_argument("--image", help="画像ファイルパス")
    parser.add_argument(
        "--stage",
        choices=DebugPipeline.STAGES,
        help="対象ステージ"
    )
    parser.add_argument(
        "--mode",
        choices=["all", "only", "from"],
        default="all",
        help="実行モード: all=全部, only=指定のみ, from=指定以降"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="キャッシュを無視して強制実行"
    )
    parser.add_argument(
        "--tag",
        help="結果ファイルにタグを付ける（比較用）"
    )
    parser.add_argument(
        "--output-dir",
        default="debug_output",
        help="出力ディレクトリ（デフォルト: debug_output）"
    )

    args = parser.parse_args()

    # 入力チェック
    if not args.pdf and not args.image:
        # キャッシュがあれば実行可能
        cache_dir = Path(args.output_dir) / args.uuid
        if not cache_dir.exists():
            parser.error("--pdf または --image が必要です（初回実行時）")

    # 実行
    pipeline = DebugPipeline(
        uuid=args.uuid,
        base_dir=args.output_dir,
        tag=args.tag
    )

    result = pipeline.run(
        pdf_path=args.pdf,
        image_path=args.image,
        target_stage=args.stage,
        mode=args.mode,
        force=args.force
    )

    # 結果出力
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
