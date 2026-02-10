#!/usr/bin/env python3
"""
統合デバッグパイプライン実行スクリプト（サブステージ対応版）

全パイプライン（A→B→D→E→F→G）を実行し、各ステージの結果をローカルに保存する。
特定ステージまたはサブステージだけの再実行も可能。

使用例:
  # 全行程を実行（キャッシュがあればスキップ）
  python run_debug_pipeline.py [UUID] --pdf path/to/file.pdf

  # Stage Bだけを再実行（Stage Aのキャッシュを使用）
  python run_debug_pipeline.py [UUID] --stage B --force

  # Stage DからGまで実行
  python run_debug_pipeline.py [UUID] --start D --end G --force

  # サブステージ F3（日付正規化）だけを再実行
  python run_debug_pipeline.py [UUID] --stage F3 --force

  # サブステージ D8からD10まで実行
  python run_debug_pipeline.py [UUID] --start D8 --end D10 --force

  # タグ付きで保存（比較用）
  python run_debug_pipeline.py [UUID] --stage E --tag "v2_test"
"""

import os
import sys
import json
import argparse
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Set

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# shared.pipeline.__init__.py の壊れたインポート（旧pipeline.py）を回避
# 個別サブパッケージ（stage_a, stage_b, ...）は正常にインポート可能
import types
_pipeline_pkg = types.ModuleType('shared.pipeline')
_pipeline_pkg.__path__ = [str(PROJECT_ROOT / 'shared' / 'pipeline')]
_pipeline_pkg.__package__ = 'shared.pipeline'
sys.modules.setdefault('shared', types.ModuleType('shared'))
sys.modules['shared'].__path__ = [str(PROJECT_ROOT / 'shared')]
sys.modules['shared.pipeline'] = _pipeline_pkg

from loguru import logger

# ステージ コントローラーのインポート
from shared.pipeline.stage_a import A3EntryPoint
from shared.pipeline.stage_b import B1Controller

# サブステージ クラスのインポート（Stage D）
from shared.pipeline.stage_d.d3_vector_line_extractor import D3VectorLineExtractor
from shared.pipeline.stage_d.d5_raster_line_detector import D5RasterLineDetector
from shared.pipeline.stage_d.d8_grid_analyzer import D8GridAnalyzer
from shared.pipeline.stage_d.d9_cell_identifier import D9CellIdentifier
from shared.pipeline.stage_d.d10_image_slicer import D10ImageSlicer

# サブステージ クラスのインポート（Stage E）
from shared.pipeline.stage_e.e1_ocr_scouter import E1OcrScouter
from shared.pipeline.stage_e.e5_text_block_visualizer import E5TextBlockVisualizer
from shared.pipeline.stage_e.e20_context_extractor import E20ContextExtractor
from shared.pipeline.stage_e.e30_table_structure_extractor import E30TableStructureExtractor

# サブステージ クラスのインポート（Stage F）
from shared.pipeline.stage_f.f1_data_fusion_merger import F1DataFusionMerger
from shared.pipeline.stage_f.f3_smart_date_normalizer import F3SmartDateNormalizer
from shared.pipeline.stage_f.f5_logical_table_joiner import F5LogicalTableJoiner

# サブステージ クラスのインポート（Stage G）
from shared.pipeline.stage_g.g1_table_reproducer import G1TableReproducer
from shared.pipeline.stage_g.g3_block_arranger import G3BlockArranger
from shared.pipeline.stage_g.g5_noise_eliminator import G5NoiseEliminator


class DebugPipeline:
    """デバッグ用パイプライン（サブステージ対応版）"""

    # ステージ定義（実行順序）
    STAGES = ["A", "B", "D", "E", "F", "G"]

    # 各ステージのサブステージ定義（実行順序）
    SUBSTAGES = {
        "A": ["A3"],
        "B": ["B1"],
        "D": ["D3", "D5", "D8", "D9", "D10"],
        "E": ["E1", "E5", "E20", "E30"],
        "F": ["F1", "F3", "F5"],
        "G": ["G1", "G3", "G5"],
    }

    # 全サブステージの実行順序（フラット）
    ALL_SUBSTAGES = [
        "A3",
        "B1",
        "D3", "D5", "D8", "D9", "D10",
        "E1", "E5", "E20", "E30",
        "F1", "F3", "F5",
        "G1", "G3", "G5",
    ]

    # サブステージ → 親ステージ
    SUBSTAGE_TO_STAGE = {}
    for _stage, _subs in SUBSTAGES.items():
        for _sub in _subs:
            SUBSTAGE_TO_STAGE[_sub] = _stage

    # CLI choices 用
    VALID_TARGETS = STAGES + ALL_SUBSTAGES

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

        # Stage A/B: コントローラー（モノリシック）
        self._stage_a = A3EntryPoint()
        self._stage_b = B1Controller()

        # Stage D サブステージ
        self._d3 = D3VectorLineExtractor()
        self._d5 = D5RasterLineDetector()
        self._d8 = D8GridAnalyzer()
        self._d9 = D9CellIdentifier()
        self._d10 = D10ImageSlicer()

        # Stage E サブステージ
        _gemini_key = os.environ.get('GOOGLE_AI_API_KEY')
        self._e1 = E1OcrScouter()
        self._e5 = E5TextBlockVisualizer()
        self._e20 = E20ContextExtractor(api_key=_gemini_key)
        self._e30 = E30TableStructureExtractor(api_key=_gemini_key)

        # Stage F サブステージ
        self._f1 = F1DataFusionMerger()
        self._f3 = F3SmartDateNormalizer(api_key=_gemini_key)
        self._f5 = F5LogicalTableJoiner()

        # Stage G サブステージ
        self._g1 = G1TableReproducer()
        self._g3 = G3BlockArranger()
        self._g5 = G5NoiseEliminator()

        logger.info(f"DebugPipeline initialized: uuid={uuid}, output_dir={self.output_dir}")

    # ════════════════════════════════════════
    # ファイル I/O
    # ════════════════════════════════════════

    class _NumpyEncoder(json.JSONEncoder):
        """numpy型をPythonネイティブ型に変換するJSONエンコーダー"""
        def default(self, obj):
            try:
                import numpy as np
                if isinstance(obj, np.bool_):
                    return bool(obj)
                if isinstance(obj, np.floating):
                    return float(obj)
                if isinstance(obj, np.integer):
                    return int(obj)
                if isinstance(obj, np.ndarray):
                    return obj.tolist()
            except ImportError:
                pass
            return super().default(obj)

    def _save_json(self, file_path: Path, data: Any) -> Path:
        if file_path.exists():
            bak_path = file_path.with_suffix(".json.bak")
            file_path.replace(bak_path)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, cls=self._NumpyEncoder)
        return file_path

    def _load_json(self, file_path: Path) -> Optional[Dict[str, Any]]:
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                logger.warning(f"JSONパース失敗: {file_path} - {e}")
        return None

    def _get_filename(self, stage_name: str) -> Path:
        if self.tag:
            return self.output_dir / f"{self.uuid}_stage_{stage_name.lower()}_{self.tag}.json"
        return self.output_dir / f"{self.uuid}_stage_{stage_name.lower()}.json"

    def _get_substage_filename(self, substage_id: str) -> Path:
        if self.tag:
            return self.output_dir / f"{self.uuid}_substage_{substage_id.lower()}_{self.tag}.json"
        return self.output_dir / f"{self.uuid}_substage_{substage_id.lower()}.json"

    def save_stage(self, stage_name: str, data: Any) -> Path:
        fp = self._get_filename(stage_name)
        self._save_json(fp, data)
        logger.info(f"[Stage {stage_name}] 結果を保存: {fp}")
        return fp

    def load_stage(self, stage_name: str) -> Optional[Dict[str, Any]]:
        fp = self._get_filename(stage_name)
        data = self._load_json(fp)
        if data is not None:
            logger.info(f"[Stage {stage_name}] キャッシュ読み込み: {fp}")
        return data

    def save_substage(self, substage_id: str, data: Any) -> Path:
        fp = self._get_substage_filename(substage_id)
        self._save_json(fp, data)
        logger.info(f"[{substage_id}] サブステージ結果を保存: {fp}")
        return fp

    def load_substage(self, substage_id: str) -> Optional[Dict[str, Any]]:
        fp = self._get_substage_filename(substage_id)
        data = self._load_json(fp)
        if data is not None:
            logger.info(f"[{substage_id}] サブステージキャッシュ読み込み: {fp}")
        return data

    def _get_substage_data(self, substage_id: str, ctx: dict) -> Optional[Any]:
        """サブステージ結果を取得（ctx → サブステージcache → フルステージcacheの順）"""
        # 1. コンテキストから
        if ctx.get(substage_id) is not None:
            return ctx[substage_id]
        # 2. サブステージキャッシュから
        data = self.load_substage(substage_id)
        if data is not None:
            return data
        # 3. フルステージキャッシュから抽出（Stage D のみ対応）
        stage = self.SUBSTAGE_TO_STAGE.get(substage_id)
        stage_data = self.load_stage(stage) if stage else None
        if stage_data and stage == "D":
            debug = stage_data.get('debug', {})
            extract = {
                "D3": "vector_lines", "D5": "raster_lines",
                "D8": "grid_result", "D9": "cell_result",
            }
            if substage_id in extract:
                return debug.get(extract[substage_id])
            if substage_id == "D10":
                return {
                    'tables': stage_data.get('tables', []),
                    'non_table_image_path': stage_data.get('non_table_image_path', ''),
                    'metadata': stage_data.get('metadata', {}),
                }
        return None

    # ════════════════════════════════════════
    # 実行制御
    # ════════════════════════════════════════

    def _resolve_target(self, target: str) -> List[str]:
        """ターゲットをサブステージリストに展開（"F"→["F1","F3","F5"]）"""
        if target in self.STAGES:
            return self.SUBSTAGES[target]
        if target in self.ALL_SUBSTAGES:
            return [target]
        raise ValueError(f"Unknown target: {target}")

    def _determine_active_substages(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        target: Optional[str] = None,
        mode: str = "all"
    ) -> List[str]:
        """実行対象のサブステージリストを決定"""
        if mode == "only" and target:
            return self._resolve_target(target)

        active = list(self.ALL_SUBSTAGES)

        if start:
            first = self._resolve_target(start)[0]
            idx = self.ALL_SUBSTAGES.index(first)
            active = [s for s in active if self.ALL_SUBSTAGES.index(s) >= idx]

        if end:
            last = self._resolve_target(end)[-1]
            idx = self.ALL_SUBSTAGES.index(last)
            active = [s for s in active if self.ALL_SUBSTAGES.index(s) <= idx]

        return active

    def _should_run(self, substage_id: str, active_set: Set[str], force: bool, has_cache: bool) -> bool:
        if substage_id not in active_set:
            return False
        return force or not has_cache

    # ════════════════════════════════════════
    # メイン実行
    # ════════════════════════════════════════

    def run(
        self,
        pdf_path: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        target: Optional[str] = None,
        mode: str = "all",
        force: bool = False
    ) -> Dict[str, Any]:
        start_time = time.time()
        active_list = self._determine_active_substages(start, end, target, mode)
        active_set = set(active_list)

        logger.info("=" * 60)
        logger.info(f"Debug Pipeline Start: {self.uuid}")
        logger.info(f"  active: {active_list}")
        logger.info(f"  force={force}")
        logger.info("=" * 60)

        ctx: Dict[str, Any] = {}
        results = {}
        errors = []

        try:
            self._exec_stage_a(ctx, active_set, force, pdf_path)
            self._exec_stage_b(ctx, active_set, force, pdf_path)
            self._exec_stage_d(ctx, active_set, force)
            self._exec_stage_e(ctx, active_set, force)
            self._exec_stage_f(ctx, active_set, force)
            self._exec_stage_g(ctx, active_set, force)

            for stage in self.STAGES:
                if stage in ctx and ctx[stage] is not None:
                    results[stage] = "saved"

        except FileNotFoundError as e:
            logger.error(str(e))
            errors.append(str(e))
        except Exception as e:
            logger.error(f"パイプラインエラー: {e}", exc_info=True)
            errors.append(str(e))

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
            "results": results,
            "errors": errors,
            "output_dir": str(self.output_dir)
        }

    # ════════════════════════════════════════
    # Stage A（モノリシック）
    # ════════════════════════════════════════

    def _exec_stage_a(self, ctx, active_set, force, pdf_path):
        cached = self.load_stage("A")
        if self._should_run("A3", active_set, force, cached is not None):
            logger.info("[A3] Entry Point 実行中...")
            if not pdf_path:
                raise FileNotFoundError("Stage A にはPDFファイルが必要です")
            result = self._stage_a.process(pdf_path)
            self.save_stage("A", result)
            self.save_substage("A3", result)
            ctx["A"] = result
        else:
            ctx["A"] = cached

        if not ctx.get("A") or not ctx["A"].get('success'):
            if "A3" in active_set:
                raise ValueError("Stage A 失敗または無効なデータ")

    # ════════════════════════════════════════
    # Stage B（モノリシック）
    # ════════════════════════════════════════

    def _exec_stage_b(self, ctx, active_set, force, pdf_path):
        cached = self.load_stage("B")
        if self._should_run("B1", active_set, force, cached is not None):
            logger.info("[B1] Controller 実行中...")
            if not pdf_path:
                raise FileNotFoundError("Stage B にはPDFファイルが必要です")
            result = self._stage_b.process(
                file_path=pdf_path,
                a_result=ctx.get("A")
            )
            self.save_stage("B", result)
            self.save_substage("B1", result)
            ctx["B"] = result
        else:
            ctx["B"] = cached

    # ════════════════════════════════════════
    # Stage D（サブステージ: D3→D5→D8→D9→D10）
    # ════════════════════════════════════════

    def _exec_stage_d(self, ctx, active_set, force):
        d_subs = {"D3", "D5", "D8", "D9", "D10"}
        if not (d_subs & active_set):
            ctx["D"] = self.load_stage("D")
            return

        stage_b = ctx.get("B") or self.load_stage("B")
        if not stage_b:
            logger.warning("[Stage D] Stage B データなし。スキップ。")
            ctx["D"] = {'success': False, 'error': 'No Stage B data'}
            return

        purged_pdf_path = stage_b.get('purged_pdf_path')
        purged_image_paths = stage_b.get('purged_image_paths', [])
        purged_image_path = Path(purged_image_paths[0]) if purged_image_paths else None

        if not purged_pdf_path:
            logger.warning("[Stage D] purged_pdf_path なし。スキップ。")
            ctx["D"] = {'success': False, 'error': 'No purged PDF'}
            return

        page_num = 0

        # D3: ベクトル罫線抽出
        d3 = self._get_substage_data("D3", ctx)
        if self._should_run("D3", active_set, force, d3 is not None):
            logger.info("[D3] ベクトル罫線抽出 実行中...")
            d3 = self._d3.extract(Path(purged_pdf_path), page_num)
            self.save_substage("D3", d3)
        ctx["D3"] = d3

        # D5: ラスター罫線検出
        d5 = self._get_substage_data("D5", ctx)
        if self._should_run("D5", active_set, force, d5 is not None):
            if purged_image_path and purged_image_path.exists():
                logger.info("[D5] ラスター罫線検出 実行中...")
                d5 = self._d5.detect(purged_image_path)
                self.save_substage("D5", d5)
            else:
                logger.info("[D5] スキップ: 画像なし")
                d5 = None
        ctx["D5"] = d5

        # D8: 格子解析
        d8 = self._get_substage_data("D8", ctx)
        if self._should_run("D8", active_set, force, d8 is not None):
            logger.info("[D8] 格子解析 実行中...")
            d8 = self._d8.analyze(ctx.get("D3"), ctx.get("D5"))
            self.save_substage("D8", d8)
        ctx["D8"] = d8

        # D9: セル特定
        d9 = self._get_substage_data("D9", ctx)
        if self._should_run("D9", active_set, force, d9 is not None):
            logger.info("[D9] セル特定 実行中...")
            d9 = self._d9.identify(ctx.get("D8"))
            self.save_substage("D9", d9)
        ctx["D9"] = d9

        # D10: 画像分割
        d10 = self._get_substage_data("D10", ctx)
        if self._should_run("D10", active_set, force, d10 is not None):
            if purged_image_path and purged_image_path.exists():
                logger.info("[D10] 画像分割 実行中...")
                d10 = self._d10.slice(
                    purged_image_path, ctx.get("D8"), ctx.get("D9"), self.output_dir
                )
                self.save_substage("D10", d10)
            else:
                logger.info("[D10] スキップ: 画像なし")
                d10 = {'tables': [], 'non_table_image_path': '', 'metadata': {}}
        ctx["D10"] = d10

        # Stage D 結果を合成・保存
        stage_d = {
            'success': True,
            'page_index': page_num,
            'tables': (d10 or {}).get('tables', []),
            'non_table_image_path': (d10 or {}).get('non_table_image_path', ''),
            'metadata': (d10 or {}).get('metadata', {}),
            'debug': {
                'vector_lines': d3,
                'raster_lines': d5,
                'grid_result': d8,
                'cell_result': d9,
            }
        }
        self.save_stage("D", stage_d)
        ctx["D"] = stage_d

    # ════════════════════════════════════════
    # Stage E（サブステージ: E1→E5→E20, E30）
    # ════════════════════════════════════════

    def _exec_stage_e(self, ctx, active_set, force):
        e_subs = {"E1", "E5", "E20", "E30"}
        if not (e_subs & active_set):
            ctx["E"] = self.load_stage("E")
            return

        stage_d = ctx.get("D") or self.load_stage("D")
        if not stage_d:
            logger.warning("[Stage E] Stage D データなし。スキップ。")
            ctx["E"] = {'success': False, 'error': 'No Stage D data'}
            return

        non_table_image = stage_d.get('non_table_image_path')
        tables = stage_d.get('tables', [])
        total_tokens = 0
        models_used = []

        # E1: OCR Scouter（全画像）
        e1 = self.load_substage("E1")
        if self._should_run("E1", active_set, force, e1 is not None):
            logger.info("[E1] OCR Scouter 実行中...")
            e1 = {'non_table_scout': None, 'table_scouts': []}
            if non_table_image and Path(non_table_image).exists():
                e1['non_table_scout'] = self._e1.scout(Path(non_table_image))
            for tbl in tables:
                img = Path(tbl.get('image_path', ''))
                if img.exists():
                    scout = self._e1.scout(img)
                    scout['table_id'] = tbl.get('table_id', 'Unknown')
                    e1['table_scouts'].append(scout)
            self.save_substage("E1", e1)
        ctx["E1"] = e1

        # E5: Text Block Visualizer（非表領域）
        e5 = self.load_substage("E5")
        if self._should_run("E5", active_set, force, e5 is not None):
            logger.info("[E5] Text Block Visualizer 実行中...")
            e5 = {'block_result': None, 'block_hint': ''}
            if non_table_image and Path(non_table_image).exists():
                scout = (ctx.get("E1") or {}).get('non_table_scout', {})
                extracted_text = scout.get('extracted_text') if scout else None
                block_result = self._e5.detect_blocks(Path(non_table_image), extracted_text)
                block_hint = self._e5.generate_prompt_hint(block_result.get('blocks', []))
                e5 = {'block_result': block_result, 'block_hint': block_hint}
            self.save_substage("E5", e5)
        ctx["E5"] = e5

        # E20: Context Extractor（非表領域 → Gemini Flash-lite）
        e20 = self.load_substage("E20")
        if self._should_run("E20", active_set, force, e20 is not None):
            logger.info("[E20] Context Extractor 実行中...")
            e20 = {'success': False}
            if non_table_image and Path(non_table_image).exists():
                scout = (ctx.get("E1") or {}).get('non_table_scout', {})
                if scout and not scout.get('should_skip'):
                    block_hint = (ctx.get("E5") or {}).get('block_hint', '')
                    extract = self._e20.extract(Path(non_table_image), block_hint=block_hint)
                    e20 = {
                        **extract,
                        'scout_result': scout,
                        'block_result': (ctx.get("E5") or {}).get('block_result')
                    }
                    total_tokens += extract.get('tokens_used', 0)
                    model = extract.get('model_used')
                    if model and model not in models_used:
                        models_used.append(model)
            self.save_substage("E20", e20)
        ctx["E20"] = e20

        # E30: Table Structure Extractor（表領域 → Gemini Flash）
        e30 = self.load_substage("E30")
        if self._should_run("E30", active_set, force, e30 is not None):
            logger.info("[E30] Table Structure Extractor 実行中...")
            e30 = {'table_results': []}
            scout_map = {}
            if ctx.get("E1"):
                scout_map = {s.get('table_id'): s for s in ctx["E1"].get('table_scouts', [])}
            for tbl in tables:
                tid = tbl.get('table_id', 'Unknown')
                img = Path(tbl.get('image_path', ''))
                if not img.exists():
                    continue
                scout = scout_map.get(tid, {})
                if scout.get('should_skip'):
                    continue
                extract = self._e30.extract(img, cell_map=tbl.get('cell_map', []))
                e30['table_results'].append({**extract, 'table_id': tid, 'scout_result': scout})
                total_tokens += extract.get('tokens_used', 0)
                model = extract.get('model_used')
                if model and model not in models_used:
                    models_used.append(model)
            self.save_substage("E30", e30)
        ctx["E30"] = e30

        # Stage E 結果を合成・保存
        stage_e = {
            'success': True,
            'non_table_content': ctx.get("E20") or {},
            'table_contents': (ctx.get("E30") or {}).get('table_results', []),
            'metadata': {'total_tokens': total_tokens, 'models_used': models_used}
        }
        self.save_stage("E", stage_e)
        ctx["E"] = stage_e

    # ════════════════════════════════════════
    # Stage F（サブステージ: F1→F3→F5）
    # ════════════════════════════════════════

    def _exec_stage_f(self, ctx, active_set, force):
        f_subs = {"F1", "F3", "F5"}
        if not (f_subs & active_set):
            ctx["F"] = self.load_stage("F")
            return

        # F1: Data Fusion Merger
        f1 = self.load_substage("F1")
        if self._should_run("F1", active_set, force, f1 is not None):
            logger.info("[F1] Data Fusion Merger 実行中...")
            f1 = self._f1.merge(
                stage_a_result=ctx.get("A") or self.load_stage("A"),
                stage_b_result=ctx.get("B") or self.load_stage("B"),
                stage_d_result=ctx.get("D") or self.load_stage("D"),
                stage_e_result=ctx.get("E") or self.load_stage("E")
            )
            self.save_substage("F1", f1)
        ctx["F1"] = f1

        if not f1 or not f1.get('success'):
            logger.error("[F1] 統合失敗")
            ctx["F"] = f1 or {'success': False}
            self.save_stage("F", ctx["F"])
            return

        # F3: Smart Date/Time Normalizer
        f3 = self.load_substage("F3")
        if self._should_run("F3", active_set, force, f3 is not None):
            logger.info("[F3] Smart Date/Time Normalizer 実行中...")
            events = f1.get('events', [])
            year_ctx = f1.get('document_info', {}).get('year_context')
            if events:
                f3 = self._f3.normalize_dates(events=events, year_context=year_ctx)
            else:
                f3 = {'success': True, 'normalized_events': [], 'normalization_count': 0}
            self.save_substage("F3", f3)
        ctx["F3"] = f3

        normalized_events = (
            f3.get('normalized_events', [])
            if f3 and f3.get('success')
            else f1.get('events', [])
        )

        # F5: Logical Table Joiner
        f5 = self.load_substage("F5")
        if self._should_run("F5", active_set, force, f5 is not None):
            logger.info("[F5] Logical Table Joiner 実行中...")
            tables = f1.get('tables', [])
            if tables:
                f5 = self._f5.join_tables(tables)
            else:
                f5 = {'success': True, 'joined_tables': [], 'join_count': 0}
            self.save_substage("F5", f5)
        ctx["F5"] = f5

        consolidated_tables = (
            f5.get('joined_tables', [])
            if f5 and f5.get('success')
            else f1.get('tables', [])
        )

        # Stage F 結果を合成・保存
        metadata = f1.get('metadata', {})
        if f3 and f3.get('success'):
            metadata['total_tokens'] = metadata.get('total_tokens', 0) + f3.get('tokens_used', 0)

        stage_f = {
            'success': True,
            'document_info': f1.get('document_info', {}),
            'normalized_events': normalized_events,
            'tasks': f1.get('tasks', []),
            'notices': f1.get('notices', []),
            'consolidated_tables': consolidated_tables,
            'raw_integrated_text': f1.get('raw_text', ''),
            'metadata': metadata
        }
        self.save_stage("F", stage_f)
        ctx["F"] = stage_f

    # ════════════════════════════════════════
    # Stage G（サブステージ: G1→G3→G5）
    # ════════════════════════════════════════

    def _exec_stage_g(self, ctx, active_set, force):
        g_subs = {"G1", "G3", "G5"}
        if not (g_subs & active_set):
            ctx["G"] = self.load_stage("G")
            return

        stage_f = ctx.get("F") or self.load_stage("F")
        if not stage_f:
            logger.warning("[Stage G] Stage F データなし。スキップ。")
            ctx["G"] = {'success': False, 'error': 'No Stage F data'}
            return

        # G1: Table Reproducer
        g1 = self.load_substage("G1")
        if self._should_run("G1", active_set, force, g1 is not None):
            logger.info("[G1] Table Reproducer 実行中...")
            g1 = self._g1.reproduce(stage_f.get('consolidated_tables', []))
            self.save_substage("G1", g1)
        ctx["G1"] = g1

        ui_tables = (g1 or {}).get('ui_tables', [])

        # G3: Block Arranger
        g3 = self.load_substage("G3")
        if self._should_run("G3", active_set, force, g3 is not None):
            logger.info("[G3] Block Arranger 実行中...")
            g3 = self._g3.arrange(
                raw_text=stage_f.get('raw_integrated_text', ''),
                events=stage_f.get('normalized_events', []),
                tasks=stage_f.get('tasks', []),
                notices=stage_f.get('notices', [])
            )
            self.save_substage("G3", g3)
        ctx["G3"] = g3

        blocks = (g3 or {}).get('blocks', [])

        # G5: Noise Eliminator
        g5 = self.load_substage("G5")
        if self._should_run("G5", active_set, force, g5 is not None):
            logger.info("[G5] Noise Eliminator 実行中...")
            g5 = self._g5.eliminate(
                stage_f_result=stage_f,
                ui_tables=ui_tables,
                blocks=blocks
            )
            self.save_substage("G5", g5)
        ctx["G5"] = g5

        # Stage G 結果を合成・保存
        ui_data = (g5 or {}).get('ui_data', {})
        stage_g = {
            'success': (g5 or {}).get('success', False),
            'ui_data': ui_data,
            'metadata': {
                'stage': 'G',
                'conversion_count': (g1 or {}).get('conversion_count', 0),
                'block_count': (g3 or {}).get('block_count', 0),
                'total_tokens': stage_f.get('metadata', {}).get('total_tokens', 0)
            }
        }
        self.save_stage("G", stage_g)
        ctx["G"] = stage_g

        if stage_g.get('success') and ui_data:
            ui_path = self.output_dir / f"{self.uuid}_ui_data.json"
            self._save_json(ui_path, ui_data)
            logger.info(f"[Stage G] UI用データを保存: {ui_path}")


def main():
    parser = argparse.ArgumentParser(
        description="統合デバッグパイプライン - サブステージ単位の実行対応",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # 全行程を実行
  python run_debug_pipeline.py abc123 --pdf input.pdf

  # Stage Bだけを再実行
  python run_debug_pipeline.py abc123 --stage B --force

  # Stage DからGまで実行
  python run_debug_pipeline.py abc123 --start D --end G --force

  # サブステージ F3（日付正規化）だけを再実行
  python run_debug_pipeline.py abc123 --stage F3 --force

  # サブステージ D8（格子解析）だけを再実行
  python run_debug_pipeline.py abc123 --stage D8 --force

  # サブステージ G1からG5まで実行
  python run_debug_pipeline.py abc123 --start G1 --end G5 --force

  # サブステージ D8からF3まで実行
  python run_debug_pipeline.py abc123 --start D8 --end F3 --force

  # タグ付きで保存（比較用）
  python run_debug_pipeline.py abc123 --stage E --tag "v2_test" --force

サブステージ一覧:
  Stage A: A3(EntryPoint)
  Stage B: B1(Controller)
  Stage D: D3(罫線抽出) D5(ラスター検出) D8(格子解析) D9(セル特定) D10(画像分割)
  Stage E: E1(OCR) E5(ブロック認識) E20(地の文抽出) E30(表抽出)
  Stage F: F1(データ統合) F3(日付正規化) F5(表結合)
  Stage G: G1(表再現) G3(ブロック整頓) G5(ノイズ除去)
        """
    )
    parser.add_argument("uuid", help="対象のUUID（任意の識別子）")
    parser.add_argument("--pdf", help="PDFファイルパス")
    parser.add_argument(
        "--stage",
        choices=DebugPipeline.VALID_TARGETS,
        help="対象ステージ/サブステージ（例: F, F3, D8）"
    )
    parser.add_argument(
        "--start",
        choices=DebugPipeline.VALID_TARGETS,
        help="開始ステージ/サブステージ（例: D, D8, F3）"
    )
    parser.add_argument(
        "--end",
        choices=DebugPipeline.VALID_TARGETS,
        help="終了ステージ/サブステージ（例: G, G5, F5）"
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
    if not args.pdf:
        cache_dir = Path(args.output_dir) / args.uuid
        if not cache_dir.exists():
            parser.error("--pdf が必要です（初回実行時）")

    # 実行モード決定
    mode = "only" if args.stage else "all"

    pipeline = DebugPipeline(
        uuid=args.uuid,
        base_dir=args.output_dir,
        tag=args.tag
    )

    result = pipeline.run(
        pdf_path=args.pdf,
        start=args.start,
        end=args.end,
        target=args.stage,
        mode=mode,
        force=args.force
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
