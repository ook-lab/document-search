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

  # Stage DからFまで実行
  python run_debug_pipeline.py [UUID] --start D --end F --force

  # サブステージ F13（日付正規化）だけを再実行
  python run_debug_pipeline.py [UUID] --stage F13 --force

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

PIPELINE_LAB_ROOT = Path(__file__).resolve().parents[2]  # services/pipeline-lab/
sys.path.insert(0, str(PIPELINE_LAB_ROOT))

# dms.pipeline.__init__.py の壊れたインポート（旧pipeline.py）を回避
# 個別サブパッケージ（stage_a, stage_b, ...）は正常にインポート可能
import types
_pipeline_pkg = types.ModuleType('dms.pipeline')
_pipeline_pkg.__path__ = [str(PIPELINE_LAB_ROOT / 'dms' / 'pipeline')]
_pipeline_pkg.__package__ = 'dms.pipeline'
sys.modules.setdefault('dms', types.ModuleType('dms'))
sys.modules['dms'].__path__ = [str(PIPELINE_LAB_ROOT / 'dms')]
sys.modules['dms.pipeline'] = _pipeline_pkg

from dms.pipeline.substage_order import (
    DEBUG_PIPELINE_SUBSTAGES_EXECUTION,
    sorted_valid_targets,
)

from loguru import logger

# ステージ コントローラーのインポート
from dms.pipeline.stage_a import A3EntryPoint
from dms.pipeline.stage_b import B1Controller
# B90 Layer Purge 削除: 各ステージで抽出＋削除を統合

# サブステージ クラスのインポート（Stage D）
from dms.pipeline.stage_d.d3_vector_line_extractor import D3VectorLineExtractor
from dms.pipeline.stage_d.d5_raster_line_detector import D5RasterLineDetector
from dms.pipeline.stage_d.d8_grid_analyzer import D8GridAnalyzer
from dms.pipeline.stage_d.d9_cell_identifier import D9CellIdentifier
from dms.pipeline.stage_d.d10_image_slicer import D10ImageSlicer

# Stage E コントローラー（E1〜E40 全処理を内包）
from dms.pipeline.stage_e import E1Controller as E1ControllerFull

# サブステージ クラスのインポート（Stage F）
from dms.pipeline.stage_f.f11_data_fusion_merger import F11DataFusionMerger
from dms.pipeline.stage_f.f13_smart_date_normalizer import F13SmartDateNormalizer
from dms.pipeline.stage_f.f17_stage_f_finalize import F17StageFFinalize

# G11: Stage G（レビュー用 ui_data）。`F60UIDeliveryController` は G11 の別名
from dms.pipeline.stage_g import G11Controller


class DebugPipeline:
    """デバッグ用パイプライン（サブステージ対応版）"""

    # ステージ定義（実行順序）
    STAGES = ["A", "B", "D", "E", "F", "G"]

    # 各ステージのサブステージ定義（実行順序）
    SUBSTAGES = {
        "A": ["A3"],
        "B": ["B1"],  # B90削除: B1で抽出＋削除を統合
        "D": ["D3", "D5", "D8", "D9", "D10"],
        "E": ["E1"],
        "F": ["F11", "F13", "F17"],
        "G": ["G11"],
    }

    # 全サブステージの実行順序（フラット・時系列。G 以降に E 番号を置かない）
    ALL_SUBSTAGES = list(DEBUG_PIPELINE_SUBSTAGES_EXECUTION)

    # サブステージ → 親ステージ
    SUBSTAGE_TO_STAGE = {}
    for _stage, _subs in SUBSTAGES.items():
        for _sub in _subs:
            SUBSTAGE_TO_STAGE[_sub] = _stage

    # CLI choices 用（親 A…F を先に、その後サブステージはレター→数値の自然順）
    VALID_TARGETS = sorted_valid_targets(STAGES, list(ALL_SUBSTAGES))

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

        # Stage A: コントローラー（モノリシック）
        self._stage_a = A3EntryPoint()

        # Stage B: コントローラー（抽出＋削除統合）
        self._stage_b = B1Controller()

        # Stage D サブステージ
        self._d3 = D3VectorLineExtractor()
        self._d5 = D5RasterLineDetector()
        self._d8 = D8GridAnalyzer()
        self._d9 = D9CellIdentifier()
        self._d10 = D10ImageSlicer()

        # Stage E コントローラー（E1〜E40 全処理）
        _gemini_key = os.environ.get('GOOGLE_AI_API_KEY')
        self._stage_e = E1ControllerFull(gemini_api_key=_gemini_key)

        # Stage F サブステージ
        self._f11 = F11DataFusionMerger()
        self._f13 = F13SmartDateNormalizer(api_key=_gemini_key)
        self._f17 = F17StageFFinalize()

        # Stage G: G11（レビュー用 ui_data）
        self._g11_controller = G11Controller()

        # ログ出力設定: 各ステージのログを個別ファイルに出力（起動時に一括設定）
        # フィルタはモジュール名ベース（"[X-NN]" プレフィックスの有無に関わらず全行を捕捉）
        _log_dir = self.output_dir  # ROOT直下に出力
        self._log_sinks = []
        # (_module_substring, _filename):
        #   record["name"] に _module_substring が含まれるログを _filename に書き込む
        for _mod, _filename in sorted(
            [
                ("a3_entry_point", "a3_entry_point.log"),
                ("a5_", "a5_type_analyzer.log"),  # a5_type_analyzer + a5_gatekeeper
                ("a6_dimension_measurer", "a6_dimension_measurer.log"),
                ("b1_controller", "b1_controller.log"),
                ("b3_pdf_word", "b3_pdf_word.log"),
                ("b4_pdf_excel", "b4_pdf_excel.log"),
                ("b5_pdf_ppt", "b5_pdf_ppt.log"),
                ("b11_google_docs", "b11_google_docs.log"),
                ("b12_google_sheets", "b12_google_sheets.log"),
                ("b14_goodnotes", "b14_goodnotes.log"),
                ("b30_illustrator", "b30_illustrator.log"),
                ("b42_multicolumn", "b42_multi_column.log"),
                ("b90_result_merger", "b90_result_merger.log"),
                ("stage_d.d1_controller", "d1_controller.log"),
                ("d3_vector_line_extractor", "d3_vector.log"),
                ("d5_raster_line_detector", "d5_raster.log"),
                ("d8_grid_analyzer", "d8_grid.log"),
                ("d9_cell_identifier", "d9_cell.log"),
                ("d10_image_slicer", "d10_slicer.log"),
                ("stage_e.e1_", "e1_controller.log"),  # e1_controller + e1_ocr_scouter
                ("e5_text_block_visualizer", "e5_visualizer.log"),
                ("g24_table_structurer", "g24_table_structurer.log"),
                ("e20_non_table_vision_ocr", "e20_vision_ocr.log"),
                ("e21_context_extractor", "e21_context.log"),
                ("e25_paragraph_grouper", "e25_coord_fitter.log"),
                ("e30_table_structure", "e30_table_struct.log"),
                ("e31_table_vision_ocr", "e31_table_ocr.log"),
                ("e32_cell_candidate", "e32_cell_merger.log"),
                ("e37_embedded_cell", "e37_embedded.log"),
                ("e40_image_ssot", "e40_ssot.log"),
                ("g19_ui_assembly", "g19_ui_assembly.log"),
                ("g62_table_layout", "g62_table_layout.log"),
                ("g61_layout_bridge", "g61_layout_bridge.log"),
                ("g26_semantic_estimator", "g26_semantic_estimator.log"),
                ("g36_lr_vertical", "g36_lr_vertical.log"),
                ("f11_data_fusion_merger", "f11_merger.log"),
                ("f13_smart_date_normalizer", "f13_normalizer.log"),
                ("f17_stage_f_finalize", "f17_finalize.log"),
                ("f5_logical_table_joiner", "f5_table_joiner.log"),
                ("g11_controller", "g11_ui_delivery.log"),
            ],
            key=lambda t: t[1],
        ):
            sid = logger.add(
                str(_log_dir / _filename),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r, m=_mod: m in (r["name"] or ""),
                level="DEBUG",
                encoding="utf-8",
            )
            self._log_sinks.append(sid)

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
        cb = getattr(self, '_on_stage_complete', None)
        if cb:
            try:
                cb(stage_name, str(fp))
            except Exception as e:
                logger.warning(f"[Drive] stage_complete callback エラー: {e}")
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

    def _load_f_substage(self, substage_id: str) -> Optional[Dict[str, Any]]:
        """F11/F13/F17 のサブステージキャッシュを読む（サブステージ JSON のみ）。"""
        data = self.load_substage(substage_id)
        legacy = {"F11": "F20", "F13": "F30", "F17": ("F40", "F50")}
        if data is None and substage_id in legacy:
            for old_id in (legacy[substage_id] if isinstance(legacy[substage_id], tuple) else (legacy[substage_id],)):
                data = self.load_substage(old_id)
                if data is not None:
                    break
        return data

    def _load_g_substage(self, substage_id: str) -> Optional[Dict[str, Any]]:
        """G11 のサブステージキャッシュ（F60 レガシー名を読む）。"""
        data = self.load_substage(substage_id)
        if data is None and substage_id == "G11":
            data = self.load_substage("F60")
        return data

    def _get_substage_data(self, substage_id: str, ctx: dict) -> Optional[Any]:
        """サブステージ結果を取得（実行中 ctx → サブステージ JSON のみ）。"""
        if ctx.get(substage_id) is not None:
            return ctx[substage_id]
        return self.load_substage(substage_id)

    # ════════════════════════════════════════
    # 実行制御
    # ════════════════════════════════════════

    def _resolve_target(self, target: str) -> List[str]:
        """ターゲットをサブステージリストに展開（"F"→["F11","F13","F17"]、"G"→["G11"]）"""
        for legacy, new in (("F20", "F11"), ("F30", "F13"), ("F40", "F17"), ("F50", "F17"), ("F60", "G11")):
            if target == legacy:
                target = new
                break
        if target in self.STAGES:
            return self.SUBSTAGES[target]
        if target in self.ALL_SUBSTAGES:
            return [target]
        raise ValueError(f"Unknown target: {target}")

    def _strip_sandwich_layer(self, pdf_path: str) -> str:
        MARKER_START = '<<<MD_SANDWICH_START>>>'
        try:
            import fitz
            doc = fitz.open(pdf_path)
            modified = False
            for page in doc:
                if MARKER_START not in page.get_text():
                    continue
                for block in page.get_text('dict')['blocks']:
                    if block.get('type') != 0:
                        continue
                    for line in block['lines']:
                        for span in line['spans']:
                            if abs(span.get('size', 0) - 6.0) < 0.5:
                                page.add_redact_annot(fitz.Rect(span['bbox']))
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=False)
                modified = True
            if modified:
                import tempfile, pathlib
                tmp = pathlib.Path(tempfile.mktemp(suffix='.pdf'))
                doc.save(str(tmp))
                doc.close()
                logger.info(f"[Debug] MD_SANDWICH層を除去しました: {pathlib.Path(pdf_path).name}")
                return str(tmp)
            doc.close()
        except Exception as e:
            logger.warning(f"[Debug] MD_SANDWICH除去失敗（スキップ）: {e}")
        return pdf_path

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
        force: bool = False,
        on_stage_complete=None,
    ) -> Dict[str, Any]:
        start_time = time.time()
        active_list = self._determine_active_substages(start, end, target, mode)
        active_set = set(active_list)

        # ステージ完了コールバックを一時的に保存（save_stage() から呼ばれる）
        self._on_stage_complete = on_stage_complete

        logger.info("=" * 60)
        logger.info(f"Debug Pipeline Start: {self.uuid}")
        logger.info(f"  active: {active_list}")
        logger.info(f"  force={force}")
        logger.info("=" * 60)

        ctx: Dict[str, Any] = {}
        results = {}
        errors = []

        # MD_SANDWICH層（fontsize=6のスパン）をパイプライン実行前に除去
        if pdf_path:
            pdf_path = self._strip_sandwich_layer(pdf_path)

        try:
            self._exec_stage_a(ctx, active_set, force, pdf_path)
            self._exec_stage_b(ctx, active_set, force, pdf_path)

            # Stage B が失敗した場合は後続ステージを実行しない
            # （D は B の purged 画像が必要、E は D の画像が必要）
            if 'B1' in active_set:
                _b = ctx.get("B") or {}
                if not _b.get('is_structured', False):
                    raise ValueError(f"Stage B 失敗 → 後続ステージ中断: {_b.get('error') or ''}")

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
        finally:
            self._on_stage_complete = None

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
            self.save_substage("A3", result)
            self.save_stage("A", result)
            ctx["A"] = result
        else:
            ctx["A"] = cached

        if not ctx.get("A") or not ctx["A"].get('success'):
            if "A3" in active_set:
                raise ValueError("Stage A 失敗または無効なデータ")

    # ════════════════════════════════════════
    # Stage B（抽出＋削除統合）
    # ════════════════════════════════════════

    def _exec_stage_b(self, ctx, active_set, force, pdf_path):
        b_subs = {"B1"}  # B90削除: B1で抽出＋削除を統合
        if not (b_subs & active_set):
            ctx["B"] = self.load_stage("B")
            return

        # B1: Controller（抽出＋削除統合）
        b1 = self.load_substage("B1")
        if self._should_run("B1", active_set, force, b1 is not None):
            logger.info("[B1] Controller 実行中（抽出＋削除統合）...")
            if not pdf_path:
                raise FileNotFoundError("Stage B にはPDFファイルが必要です")
            # JSONキャッシュ経由の場合 page_type_map / type_groups のキーが文字列になる
            # B1Controller が max(keys()) + 1 を使うため int に正規化する
            a_result = dict(ctx.get("A") or {})
            if 'page_type_map' in a_result:
                a_result['page_type_map'] = self._normalize_page_type_map(a_result['page_type_map'])
            if 'type_groups' in a_result:
                # type_groups の値（ページリスト）も int に正規化
                a_result['type_groups'] = {
                    t: [int(p) for p in pages]
                    for t, pages in a_result['type_groups'].items()
                }
            b1 = self._stage_b.process(
                file_path=pdf_path,
                a_result=a_result,
                log_dir=self.output_dir,
            )
            self.save_substage("B1", b1)
        ctx["B1"] = b1

        if not b1:
            ctx["B"] = b1 or {"is_structured": False}
            self.save_stage("B", ctx["B"])
            return

        # Stage B 最終結果 = B1（抽出＋削除済み）
        ctx["B"] = b1
        self.save_stage("B", ctx["B"])

    # ════════════════════════════════════════
    # Stage D（サブステージ: D3→D5→D8→D9→D10）
    # ════════════════════════════════════════

    @staticmethod
    def _normalize_page_type_map(raw: dict) -> dict:
        """JSON キャッシュで文字列化されたキーを int に戻す"""
        return {int(k): v for k, v in raw.items()}

    @staticmethod
    def _merge_d_results(d_results: list) -> dict:
        """複数ページの D 結果を E1Controller が読める単一 dict に統合"""
        if not d_results:
            return {}
        if len(d_results) == 1:
            d = dict(d_results[0])
            if d.get('non_table_image_path'):
                d['non_table_image_paths'] = [d['non_table_image_path']]
            return d
        base = dict(d_results[0])
        all_tables = []
        non_table_image_paths = []
        for dr in d_results:
            all_tables.extend(dr.get('tables', []) or [])
            img = dr.get('non_table_image_path')
            if img:
                non_table_image_paths.append(img)
        base['tables'] = all_tables
        base['non_table_image_paths'] = non_table_image_paths
        base['page_count'] = len(d_results)
        return base

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
        b_tables = stage_b.get('structured_tables', [])  # B抽出済み表（D3フィルタ用）

        if not purged_pdf_path:
            logger.warning("[Stage D] purged_pdf_path なし。スキップ。")
            ctx["D"] = {'success': False, 'error': 'No purged PDF'}
            return

        # page_type_map からコンテンツページを決定（COVER/UNKNOWN はスキップ）
        _NON_CONTENT = {'COVER', 'UNKNOWN'}
        stage_a = ctx.get("A") or self.load_stage("A")
        raw_ptm = (stage_a or {}).get('page_type_map', {})
        page_type_map = self._normalize_page_type_map(raw_ptm) if raw_ptm else {}

        if page_type_map:
            content_pages = sorted(
                idx for idx, ptype in page_type_map.items()
                if ptype not in _NON_CONTENT
            )
        else:
            content_pages = [0]  # フォールバック: ページ0のみ

        logger.info(f"[Stage D] 処理対象ページ: {[p+1 for p in content_pages]}")

        # ────────────────────────────────────────────────────────────────
        # 【統一 PNG 生成】全ページを一度だけ生成（D と E で共有）
        # ────────────────────────────────────────────────────────────────
        logger.info("[Stage D] PNG 生成開始")
        import fitz
        doc = fitz.open(purged_pdf_path)
        total_pages = len(doc)
        logger.info(f"[Stage D] ページ数: {total_pages}")
        for page_idx in range(total_pages):
            page_img_path = self.output_dir / f"d1_purged_page_{page_idx}.png"
            if not page_img_path.exists():
                page = doc.load_page(page_idx)
                pix = page.get_pixmap(dpi=150)
                pix.save(str(page_img_path))
                logger.info(f"[PNG生成] d1_purged_page_{page_idx}.png 完了")
        doc.close()
        # ────────────────────────────────────────────────────────────────

        # D1 Controller を使用してマルチページ統合処理
        from dms.pipeline.stage_d import D1Controller
        d1 = D1Controller()

        # 全Dサブステージが揃っているかで分岐（揃っている→マルチページ、部分→シングルページデバッグ）
        all_d_substages_active = {"D3", "D5", "D8", "D9", "D10"} <= active_set

        if all_d_substages_active:
            # D1 Controller でマルチページ一括処理
            logger.info(f"[Stage D] D1 Controller マルチページ実行: {len(content_pages)}ページ")
            all_d_results = []
            for page_idx, page_num in enumerate(content_pages):
                page_output_dir = self.output_dir / f"page_{page_num}"
                page_output_dir.mkdir(parents=True, exist_ok=True)
                purged_image_path = self.output_dir / f"d1_purged_page_{page_num}.png"
                logger.info(f"[Stage D] ページ {page_num+1} 実行中... 画像: {purged_image_path.name}")
                d_result = d1.process(
                    pdf_path=Path(purged_pdf_path),
                    purged_image_path=purged_image_path,
                    page_num=page_num,
                    output_dir=page_output_dir,
                    log_dir=self.output_dir,
                )
                if d_result and d_result.get('success'):
                    all_d_results.append(d_result)
                else:
                    logger.warning(f"[Stage D] ページ {page_num+1} 失敗 → スキップ")

            stage_d = self._merge_d_results(all_d_results)
            if not stage_d:
                stage_d = {'success': False, 'error': 'Stage D: 全ページ失敗'}
            self.save_stage("D", stage_d)
            ctx["D"] = stage_d
            return

        # ─── サブステージ個別実行（デバッグ用: ページ0のみ）─────────────
        page_num = content_pages[0] if content_pages else 0
        purged_image_path = self.output_dir / f"d1_purged_page_{page_num}.png"
        logger.info(f"[Stage D] サブステージモード: ページ{page_num+1}のみ処理")

        # D3: ベクトル罫線抽出
        d3 = self._get_substage_data("D3", ctx)
        if self._should_run("D3", active_set, force, d3 is not None):
            logger.info("[D3] ベクトル罫線抽出 実行中...")
            d3 = self._d3.extract(Path(purged_pdf_path), page_num, known_table_regions=b_tables)
            self.save_substage("D3", d3)
        ctx["D3"] = d3

        # D5: ラスター罫線検出
        d5 = self._get_substage_data("D5", ctx)
        if self._should_run("D5", active_set, force, d5 is not None):
            logger.info("[D5] ラスター罫線検出 実行中...")
            d5 = self._d5.detect(purged_image_path)
            self.save_substage("D5", d5)
        ctx["D5"] = d5

        # D8: 格子解析
        d8 = self._get_substage_data("D8", ctx)
        if self._should_run("D8", active_set, force, d8 is not None):
            logger.info("[D8] 格子解析 実行中...")
            d8 = self._d8.analyze(
                ctx.get("D3"),
                ctx.get("D5"),
                pdf_path=purged_pdf_path,
                page_index=page_num
            )
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
            logger.info("[D10] 画像分割 実行中...")
            d10 = self._d10.slice(
                purged_image_path, ctx.get("D8"), ctx.get("D9"), self.output_dir
            )
            self.save_substage("D10", d10)
        ctx["D10"] = d10

        # Stage D 結果を合成・保存（サブステージモード: 単一ページ）
        non_table_img = (d10 or {}).get('non_table_image_path', '')
        stage_d = {
            'success': True,
            'page_index': page_num,
            'tables': (d10 or {}).get('tables', []),
            'non_table_image_path': non_table_img,
            'non_table_image_paths': [non_table_img] if non_table_img else [],
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
    # Stage E（E1Controller.process() 統合実行）
    # ════════════════════════════════════════

    def _exec_stage_e(self, ctx, active_set, force):
        if "E1" not in active_set:
            ctx["E"] = self.load_stage("E")
            return

        stage_b = ctx.get("B") or self.load_stage("B")
        stage_d = ctx.get("D") or self.load_stage("D")

        if not stage_d:
            logger.warning("[Stage E] Stage D データなし。スキップ。")
            ctx["E"] = {'success': False, 'error': 'No Stage D data'}
            return

        cached = self.load_stage("E")
        if not self._should_run("E1", active_set, force, cached is not None):
            ctx["E"] = cached
            return

        logger.info("[Stage E] E1Controller.process() 実行中...")
        stage_e = self._stage_e.process(
            purged_pdf_path=None,
            stage_d_result=stage_d,
            stage_b_result=stage_b or None,
            output_dir=self.output_dir,
        )
        self.save_stage("E", stage_e)
        ctx["E"] = stage_e

    # ════════════════════════════════════════
    # Stage F（サブステージ: F11→F13→F17。データ平面のみ）
    # ════════════════════════════════════════

    def _exec_stage_f(self, ctx, active_set, force):
        f_subs = {"F11", "F13", "F17"}
        if not (f_subs & active_set):
            ctx["F"] = self.load_stage("F")
            return

        # F11: Data Fusion Merger
        f11 = self._load_f_substage("F11")
        if self._should_run("F11", active_set, force, f11 is not None):
            logger.info("[F11] Data Fusion Merger 実行中...")
            f11 = self._f11.merge(
                stage_a_result=ctx.get("A") or self.load_stage("A"),
                stage_b_result=ctx.get("B") or self.load_stage("B"),
                stage_d_result=ctx.get("D") or self.load_stage("D"),
                stage_e_result=ctx.get("E") or self.load_stage("E"),
            )
            self.save_substage("F11", f11)
        ctx["F11"] = f11

        if not f11 or not f11.get('success'):
            logger.error("[F11] 統合失敗")
            ctx["F"] = f11 or {'success': False}
            self.save_stage("F", ctx["F"])
            return

        merge_payload = dict(f11)

        # F13: Smart Date/Time Normalizer
        f13_cached = self._load_f_substage("F13")
        if self._should_run("F13", active_set, force, f13_cached is not None):
            logger.info("[F13] Smart Date/Time Normalizer 実行中...")
            events = merge_payload.get('events', [])
            year_ctx = merge_payload.get('document_info', {}).get('year_context')
            merge_payload = self._f13.normalize(
                events=events,
                year_context=year_ctx,
                merge_result=merge_payload,
            )
            self.save_substage("F13", {
                'success': merge_payload.get('success', True),
                'normalized_events': merge_payload.get(
                    'normalized_events', merge_payload.get('events', [])
                ),
            })
        elif f13_cached and f13_cached.get('success'):
            merge_payload['normalized_events'] = f13_cached.get(
                'normalized_events', merge_payload.get('events', [])
            )

        if not merge_payload.get('success', True):
            logger.error("[F13] 日付正規化失敗")
            ctx["F"] = merge_payload
            self.save_stage("F", ctx["F"])
            return

        # F17: Stage F データ平面出口
        f17_cached = self._load_f_substage("F17")
        if self._should_run("F17", active_set, force, f17_cached is not None):
            logger.info("[F17] Stage F データ平面出口...")
            stage_f = self._f17.join(merge_result=merge_payload)
            self.save_substage("F17", stage_f)
        else:
            stage_f = f17_cached or self._f17.join(merge_result=merge_payload)

        ctx["F17"] = stage_f
        if not stage_f or not stage_f.get('success'):
            logger.error("[F17] データ平面出口失敗")
            ctx["F"] = stage_f or {'success': False}
            self.save_stage("F", ctx["F"])
            return

        self.save_stage("F", stage_f)
        ctx["F"] = stage_f

    # ════════════════════════════════════════
    # Stage G（G11: レビュー用 ui_data）
    # ════════════════════════════════════════

    def _exec_stage_g(self, ctx, active_set, force):
        if "G11" not in active_set:
            ctx["G"] = self.load_stage("G")
            return

        stage_f = ctx.get("F") or self.load_stage("F")
        if not stage_f:
            logger.warning("[Stage G] G11 のみ指定だが Stage F のキャッシュがありません")
            ctx["G"] = {"success": False, "error": "No Stage F data for G11"}
            self.save_stage("G", ctx["G"])
            return

        g11_cached = self._load_g_substage("G11")
        if not self._should_run("G11", active_set, force, g11_cached is not None):
            ctx["G"] = self.load_stage("G") or g11_cached
            return

        logger.info("[G11] レビュー用 ui_data 組立（G11Controller）...")
        out = self._g11_controller.process(stage_f_result=stage_f, log_dir=self.output_dir)
        self.save_substage("G11", out)

        g_result: Dict[str, Any] = {
            "success": bool(out.get("success")),
            "ui_data": out.get("ui_data"),
            "final_metadata": out.get("final_metadata"),
        }
        if not out.get("success"):
            g_result["error"] = out.get("error")
        else:
            ui_data = out.get("ui_data") or {}
            final_metadata = out.get("final_metadata") or {}
            if ui_data:
                ui_path = self.output_dir / f"{self.uuid}_ui_data.json"
                self._save_json(ui_path, ui_data)
                logger.info(f"[G11] UI用データを保存: {ui_path}")
            if final_metadata:
                meta_path = self.output_dir / f"{self.uuid}_final_metadata.json"
                self._save_json(meta_path, final_metadata)
                logger.info(f"[G11] final_metadata を保存: {meta_path}")

        self.save_stage("G", g_result)
        ctx["G"] = g_result


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

  # Stage DからFまで実行
  python run_debug_pipeline.py abc123 --start D --end F --force

  # サブステージ F13（日付正規化）だけを再実行
  python run_debug_pipeline.py abc123 --stage F13 --force

  # サブステージ D8（格子解析）だけを再実行
  python run_debug_pipeline.py abc123 --stage D8 --force

  # サブステージ G11 のみ（レビュー用 ui_data。Stage F キャッシュ必須）
  python run_debug_pipeline.py abc123 --stage G11 --force

  # レガシー: F60 は G11 と同義
  python run_debug_pipeline.py abc123 --stage F60 --force

  # サブステージ D8からF13まで実行
  python run_debug_pipeline.py abc123 --start D8 --end F13 --force

  # タグ付きで保存（比較用）
  python run_debug_pipeline.py abc123 --stage E --tag "v2_test" --force

サブステージ一覧:
  Stage A: A3(EntryPoint)
  Stage B: B1(Controller: 抽出＋削除統合)
  Stage D: D3(罫線抽出) D5(ラスター検出) D8(格子解析) D9(セル特定) D10(画像分割)
  Stage E: E1(OCR) E5(ブロック認識) E20(地の文抽出) E30(表抽出)
  Stage F: F11(データ統合) F13(日付正規化) F17(データ平面出口)
  Stage G: G11(レビュー用 ui_data・表チェーン)
        """
    )
    parser.add_argument("uuid", help="対象のUUID（任意の識別子）")
    parser.add_argument("--pdf", help="PDFファイルパス")
    parser.add_argument(
        "--stage",
        choices=DebugPipeline.VALID_TARGETS,
        help="対象ステージ/サブステージ（例: F, F13, D8）"
    )
    parser.add_argument(
        "--start",
        choices=DebugPipeline.VALID_TARGETS,
        help="開始ステージ/サブステージ（例: D, D8, F13）"
    )
    parser.add_argument(
        "--end",
        choices=DebugPipeline.VALID_TARGETS,
        help="終了ステージ/サブステージ（例: F, G, G11, F17）"
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
