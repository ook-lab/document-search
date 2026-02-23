"""
B-1: Stage B Controller（Orchestrator）

Stage Aの判定結果に基づいて、適切なStage Bプロセッサを選択・実行する。

振り分けロジック:
1. ファイル拡張子の確認（PDF vs Native）
2. Stage Aの document_type に基づいてプロセッサを選択
3. 特化型プロセッサ（B-40番台）への対応

MIXED 文書の処理:
- A5 TypeAnalyzer が page_type_map / type_groups を生成した場合、
  type ごとに masked_pages を設定して複数プロセッサを実行
- 複数の生結果は B90 ResultMerger でマージして単一 stage_b_result を返す
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger
import yaml

from .b3_pdf_word import B3PDFWordProcessor
from .b61_pdf_word_ltsc import B61PDFWordLTSCProcessor
from .b62_pdf_word_2019 import B62PDFWord2019Processor
from .b4_pdf_excel import B4PDFExcelProcessor
from .b5_pdf_ppt import B5PDFPPTProcessor
from .b6_native_word import B6NativeWordProcessor
from .b7_native_excel import B7NativeExcelProcessor
from .b8_native_ppt import B8NativePPTProcessor
from .b11_google_docs import B11GoogleDocsProcessor
from .b12_google_sheets import B12GoogleSheetsProcessor
from .b14_goodnotes_processor import B14GoodnotesProcessor
from .b30_illustrator import B30IllustratorProcessor
from .b31_indesign import B31InDesignProcessor
from .b42_multicolumn_report import B42MultiColumnReportProcessor
from .b16_canva import B16CanvaProcessor
from .b17_studyaid import B17StudyaidProcessor
from .b18_ios_quartz import B18IOSQuartzProcessor
from .b19_pdf_web import B19PDFWebProcessor
from .b39_acrobat import B39AcrobatProcessor
from .b80_scan_ocr import B80ScanOCRProcessor
from .b90_result_merger import B90ResultMerger

# COVER 分類は廃止。全ページ B プロセッサに渡す。
# 完全空白ページはどの B に渡しても何も抽出されないだけ。
_NON_CONTENT_TYPES = set()

_RULES_PATH = Path(__file__).parent / "routing_rules.yaml"

def _load_routing_rules() -> Dict[str, Any]:
    with open(_RULES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)

_ROUTING = _load_routing_rules()
_TYPE_TO_PROCESSOR: Dict[str, str] = _ROUTING.get("mixed_type_routes", {})


class B1Controller:
    """B-1: Stage B Controller（Orchestrator）"""

    # 本番環境で許可するプロセッサ（動作確認済みのものを順次追加する）
    PRODUCTION_ALLOWED_PROCESSORS: List[str] = [
        'B11_GOOGLE_DOCS',
    ]

    def __init__(self):
        """B-1 コントローラー初期化"""
        # プロセッサインスタンスを作成
        self.b3_pdf_word = B3PDFWordProcessor()
        self.b61_pdf_word_ltsc = B61PDFWordLTSCProcessor()
        self.b62_pdf_word_2019 = B62PDFWord2019Processor()
        self.b4_pdf_excel = B4PDFExcelProcessor()
        self.b5_pdf_ppt = B5PDFPPTProcessor()
        self.b6_native_word = B6NativeWordProcessor()
        self.b7_native_excel = B7NativeExcelProcessor()
        self.b8_native_ppt = B8NativePPTProcessor()
        self.b11_google_docs = B11GoogleDocsProcessor()
        self.b12_google_sheets = B12GoogleSheetsProcessor()
        self.b14_goodnotes = B14GoodnotesProcessor()
        self.b30_illustrator = B30IllustratorProcessor()
        self.b31_indesign = B31InDesignProcessor()
        self.b42_multicolumn = B42MultiColumnReportProcessor()
        self.b16_canva = B16CanvaProcessor()
        self.b17_studyaid = B17StudyaidProcessor()
        self.b18_ios_quartz = B18IOSQuartzProcessor()
        self.b19_pdf_web = B19PDFWebProcessor()
        self.b39_acrobat = B39AcrobatProcessor()
        self.b80_scan_ocr = B80ScanOCRProcessor()
        self.b90_merger = B90ResultMerger()

    def process(
        self,
        file_path: str | Path,
        a_result: Optional[Dict[str, Any]] = None,
        force_processor: Optional[str] = None,
        log_dir=None,
        production_mode: bool = False,
    ) -> Dict[str, Any]:
        """
        Stage Aの結果に基づいて適切なプロセッサを選択・実行

        Args:
            file_path: ファイルパス
            a_result: Stage Aの実行結果（document_typeを含む）
            force_processor: 強制的に使用するプロセッサ名（オプション）
            log_dir: ログファイルの出力先ディレクトリ（B30/B42の個別ログファイル作成に使用）
            production_mode: True の場合 PRODUCTION_ALLOWED_PROCESSORS のみ許可、他は全遮断

        Returns:
            Stage Bの実行結果
        """
        from pathlib import Path as _Path
        _log_dir = _Path(log_dir) if log_dir else None
        return self._process_impl(file_path, a_result, force_processor, _log_dir, production_mode=production_mode)

    def _process_impl(
        self,
        file_path,
        a_result,
        force_processor,
        _log_dir,
        production_mode: bool = False,
    ) -> Dict[str, Any]:
        """process() の本体（sink 管理を分離するため）"""
        file_path = Path(file_path)
        file_ext = file_path.suffix.lower()

        logger.info("=" * 60)
        logger.info("[B-1] プロセッサ選択開始")
        logger.info(f"  ├─ ファイル: {file_path.name}")
        logger.info(f"  └─ 拡張子: {file_ext}")

        # =========================================
        # A-5 Gatekeeper 強制（最後の門）
        # - a5_gatekeeper が無い / ALLOW でない → 全遮断
        # - allowlist 外のプロセッサは実行しない
        # - force_processor も allowlist 外は遮断（抜け道封鎖）
        # =========================================
        gate = None
        if a_result:
            # 正本は a5_gatekeeper。移行期の互換として gatekeeper も許容
            gate = a_result.get("a5_gatekeeper") or a_result.get("gatekeeper")

        if not isinstance(gate, dict):
            logger.error("[B-1] Gate missing: a_result['a5_gatekeeper'] が存在しません（Gate未通過は遮断）")
            return {
                "is_structured": False,
                "error": "GATE_MISSING: a_result['a5_gatekeeper'] not found",
                "processor_name": "B1_CONTROLLER"
            }

        decision = gate.get("decision")
        allowed = gate.get("allowed_processors") or []

        if decision != "ALLOW":
            logger.warning(f"[B-1] Gate BLOCK: code={gate.get('block_code')} reason={gate.get('block_reason')}")
            return {
                "is_structured": False,
                "error": f"GATE_BLOCKED: {gate.get('block_code')} {gate.get('block_reason')}",
                "processor_name": "B1_CONTROLLER"
            }

        if not isinstance(allowed, list) or not allowed:
            logger.error("[B-1] Gate ALLOW だが allowlist が空（ポリシー不整合なので遮断）")
            return {
                "is_structured": False,
                "error": "GATE_POLICY_ERROR: allowed_processors is empty",
                "processor_name": "B1_CONTROLLER"
            }

        # 本番モード: PRODUCTION_ALLOWED_PROCESSORS のみ許可（他は全遮断）
        if production_mode:
            allowed = self.PRODUCTION_ALLOWED_PROCESSORS
            logger.info(f"[B-1] PRODUCTION MODE: allowlist を {allowed} に制限")

        # 強制指定がある場合（Gate の allowlist 内に限り許可）
        if force_processor:
            logger.info(f"  └─ 強制指定: {force_processor}")
            if force_processor not in allowed:
                logger.warning(f"[B-1] Gate ALLOW だが強制指定がallowlist外: {force_processor} not in {allowed}")
                return {
                    "is_structured": False,
                    "error": f"GATE_ROUTE_BLOCKED: forced processor {force_processor} not allowed (allowed={allowed})",
                    "processor_name": force_processor
                }
            return self._execute_processor(force_processor, file_path)

        # Stage A の結果から origin_app と layout_profile を取得
        origin_app = None
        layout_profile = 'FLOW'  # デフォルト
        type_groups: Dict[str, List[int]] = {}
        page_type_map: Dict[int, str] = {}

        # LOW 信頼度ページ = フォントパターン不一致（UNKNOWN）→ Stage B スキップ、Stage E OCR
        low_conf_pages: List[int] = []

        if a_result:
            # 2軸取得（新形式）
            origin_app = a_result.get('origin_app') or a_result.get('document_type')
            layout_profile = a_result.get('layout_profile', 'FLOW')
            type_groups = a_result.get('type_groups', {}) or {}
            page_type_map = a_result.get('page_type_map', {}) or {}

            page_confidence_map = a_result.get('page_confidence_map', {}) or {}
            low_conf_pages = sorted(
                int(idx) for idx, conf in page_confidence_map.items()
                if conf == 'LOW'
            )

            logger.info(f"  ├─ Stage A判定（作成ソフト）: {origin_app}")
            logger.info(f"  ├─ Stage A判定（レイアウト）: {layout_profile}")
            logger.info(f"  ├─ type_groups: { {t: len(p) for t, p in type_groups.items()} }")
            if low_conf_pages:
                logger.info(
                    f"  └─ LOW信頼度ページ（Stage B スキップ → Stage E OCR）: "
                    f"{[p+1 for p in low_conf_pages]}"
                )
            else:
                logger.info(f"  └─ LOW信頼度ページ: なし（全ページ HIGH）")

        # ── MIXED / 複数コンテンツ型: B90 経由マルチプロセッサ処理 ──
        content_groups = {t: p for t, p in type_groups.items()
                          if t not in _NON_CONTENT_TYPES}
        if len(content_groups) > 1:
            return self._process_multi_type(
                file_path, content_groups, page_type_map, allowed, layout_profile,
                log_dir=_log_dir,
            )

        # ── 単一型（従来動作）──
        processor_name = self._select_processor(file_ext, origin_app, layout_profile)

        # ルーティング失敗（BLOCK / 未知 origin_app / 未対応拡張子）
        if processor_name is None:
            logger.error(f"[B-1] プロセッサを選択できませんでした: ext={file_ext}, origin_app={origin_app}")
            return {
                "is_structured": False,
                "error": f"ROUTE_ERROR: プロセッサを選択できませんでした (ext={file_ext}, origin_app={origin_app})",
                "processor_name": "B1_CONTROLLER",
            }

        # Gate の allowlist にないプロセッサは実行しない（最後の門）
        if processor_name not in allowed:
            logger.warning(f"[B-1] Gate ALLOW だが選択プロセッサがallowlist外: {processor_name} not in {allowed}")
            return {
                "is_structured": False,
                "error": f"GATE_ROUTE_BLOCKED: selected processor {processor_name} not allowed (allowed={allowed})",
                "processor_name": processor_name
            }

        logger.info(f"[B-1] 選択されたプロセッサ: {processor_name}")
        logger.info("=" * 60)

        # プロセッサを実行（LOW 信頼度ページは masked_pages として渡す）
        return self._execute_processor(
            processor_name, file_path,
            masked_pages=low_conf_pages if low_conf_pages else None,
            log_dir=_log_dir,
        )

    def _process_multi_type(
        self,
        file_path: Path,
        content_groups: Dict[str, List[int]],
        page_type_map: Dict[int, str],
        allowed: List[str],
        layout_profile: str,
        log_dir=None,
    ) -> Dict[str, Any]:
        """
        MIXED 文書: B1 でページ種別ごとにスプリット → 各プロセッサに渡す → B90 でマージ
        """
        total_pages = max(page_type_map.keys()) + 1 if page_type_map else 0
        logger.info(f"[B-1] MIXED 処理開始（スプリット方式）: {len(content_groups)}型 / {total_pages}ページ")

        raw_results: List[Dict[str, Any]] = []

        for ptype, page_indices in content_groups.items():
            processor_name = _TYPE_TO_PROCESSOR.get(ptype)
            if not processor_name:
                # DTP等はフォント単体では作成ソフト確定不可だが、
                # B30_ILLUSTRATOR が allowed にある（メタデータで確定済み）場合はB30で処理
                if "B30_ILLUSTRATOR" in allowed:
                    processor_name = "B30_ILLUSTRATOR"
                    logger.info(f"[B-1]   {ptype} → メタデータ確定済みB30_ILLUSTRATORで処理")
                else:
                    logger.warning(f"[B-1]   {ptype} → 対応プロセッサなし → スキップ")
                    continue
            if processor_name not in allowed:
                logger.warning(f"[B-1]   {ptype} → {processor_name} がallowlist外 → スキップ")
                continue

            # B1 でページをスプリット（マスクではなく分断して渡す）
            source_pages = sorted(page_indices)
            sub_pdf = self._split_pdf_by_pages(file_path, source_pages, ptype)
            logger.info(f"[B-1]   {ptype}: {processor_name} → {sub_pdf.name} ({len(source_pages)}ページ)")

            result = self._execute_processor(processor_name, sub_pdf, log_dir=log_dir)
            result['_source_type'] = ptype
            result['_source_pages'] = source_pages  # 元PDFでのページ番号

            # サブPDFのページ番号を元PDFのページ番号に戻す
            self._remap_pages(result, source_pages)

            raw_results.append(result)

        if not raw_results:
            return {
                'is_structured': False,
                'error': 'MIXED 処理: すべての型グループがスキップされました',
                'processor_name': 'B1_CONTROLLER',
            }

        if len(raw_results) == 1:
            r = raw_results[0]
            r.pop('_source_type', None)
            r.pop('_source_pages', None)
            return r

        # B90 でマージ（purged PDF もページ順に結合）
        logger.info(f"[B-1] B90 ResultMerger に渡す: {len(raw_results)}件")
        return self.b90_merger.merge(
            raw_results,
            original_pdf_path=str(file_path), total_pages=total_pages,
        )

    @staticmethod
    def _split_pdf_by_pages(file_path: Path, page_indices: List[int], ptype: str) -> Path:
        """指定ページのみを含むサブPDFを生成する"""
        import fitz
        src = fitz.open(str(file_path))
        sub = fitz.open()
        for idx in page_indices:
            if idx < len(src):
                sub.insert_pdf(src, from_page=idx, to_page=idx)
        src.close()
        sub_path = file_path.parent / f"b1_split_{ptype.lower()}_{file_path.stem}.pdf"
        sub.save(str(sub_path))
        sub.close()
        logger.info(f"[B-1] スプリット: {sub_path.name} ({len(page_indices)}ページ: {page_indices})")
        return sub_path

    @staticmethod
    def _remap_pages(result: Dict[str, Any], source_pages: List[int]) -> None:
        """サブPDFのページ番号（0始まり）を元PDFのページ番号に戻す"""
        for block in result.get('logical_blocks', []) or []:
            sp = block.get('page', 0)
            block['page'] = source_pages[sp] if sp < len(source_pages) else sp
        for table in result.get('structured_tables', []) or []:
            sp = table.get('page', 0)
            table['page'] = source_pages[sp] if sp < len(source_pages) else sp
        for rec in result.get('records', []) or []:
            sp = rec.get('page', 0)
            rec['page'] = source_pages[sp] if sp < len(source_pages) else sp

    def _select_processor(
        self,
        file_ext: str,
        origin_app: Optional[str],
        layout_profile: str = 'FLOW'
    ) -> str:
        """
        routing_rules.yaml の定義に従いプロセッサを選択する（2軸ルーティング）

        優先順位:
          1. priority_routes  （特化型：拡張子より優先）
          2. native_routes    （Nativeファイル：拡張子ベース）
          3. pdf_routes       （PDF：origin_app ベース）
        """
        routing = _ROUTING

        # 1. 特化型（priority_routes）
        priority = routing.get("priority_routes", {})
        if origin_app in priority:
            proc = priority[origin_app]
            logger.info(f"[B-1] priority_routes: {origin_app} → {proc}")
            return proc

        # 2. Native（native_routes）
        native = routing.get("native_routes", {})
        if file_ext in native:
            proc = native[file_ext]
            logger.info(f"[B-1] native_routes: {file_ext} → {proc}")
            return proc

        # 3. PDF（pdf_routes）
        if file_ext == '.pdf':
            pdf = routing.get("pdf_routes", {})
            proc = pdf.get(origin_app)
            if proc == 'BLOCK':
                logger.error(f"[B-1] {origin_app} が B1 に到達（Gatekeeper をすり抜けた）→ 処理停止")
                return None
            if proc:
                logger.info(f"[B-1] pdf_routes: {origin_app} → {proc}")
                return proc
            logger.error(f"[B-1] 未知の origin_app: {origin_app} → 処理停止")
            return None

        logger.error(f"[B-1] 未対応の拡張子: {file_ext}")
        return None

    def _execute_processor(
        self,
        processor_name: str,
        file_path: Path,
        masked_pages: Optional[List[int]] = None,
        log_dir=None,
    ) -> Dict[str, Any]:
        """
        プロセッサを実行

        Args:
            processor_name: プロセッサ名
            file_path: ファイルパス
            masked_pages: スキップするページ番号リスト（0始まり）
            log_dir: 個別ログファイルを保存するディレクトリ

        Returns:
            プロセッサの実行結果
        """
        processor_map = {
            'B3_PDF_WORD': self.b3_pdf_word,
            'B61_PDF_WORD_LTSC': self.b61_pdf_word_ltsc,
            'B62_PDF_WORD_2019': self.b62_pdf_word_2019,
            'B4_PDF_EXCEL': self.b4_pdf_excel,
            'B5_PDF_PPT': self.b5_pdf_ppt,
            'B6_NATIVE_WORD': self.b6_native_word,
            'B7_NATIVE_EXCEL': self.b7_native_excel,
            'B8_NATIVE_PPT': self.b8_native_ppt,
            'B11_GOOGLE_DOCS': self.b11_google_docs,
            'B12_GOOGLE_SHEETS': self.b12_google_sheets,
            'B14_GOODNOTES': self.b14_goodnotes,
            'B30_ILLUSTRATOR': self.b30_illustrator,
            'B31_INDESIGN': self.b31_indesign,
            'B42_MULTICOLUMN': self.b42_multicolumn,
            'B16_CANVA':      self.b16_canva,
            'B17_STUDYAID':   self.b17_studyaid,
            'B18_IOS_QUARTZ': self.b18_ios_quartz,
            'B19_PDF_WEB':    self.b19_pdf_web,
            'B39_ACROBAT':    self.b39_acrobat,
            'B80_SCAN_OCR':   self.b80_scan_ocr,
        }

        processor = processor_map.get(processor_name)

        if not processor:
            logger.error(f"[B-1] プロセッサが見つかりません: {processor_name}")
            return {
                'is_structured': False,
                'error': f'Processor not found: {processor_name}',
                'processor_name': processor_name
            }

        try:
            # 各プロセッサが受け付けるパラメータを動的に確認して呼び出す
            import inspect
            sig = inspect.signature(processor.process)
            kwargs = {}
            if 'masked_pages' in sig.parameters and masked_pages:
                kwargs['masked_pages'] = masked_pages
            if 'log_file' in sig.parameters and log_dir:
                kwargs['log_file'] = log_dir / f"{processor_name.lower()}.log"
            result = processor.process(file_path, **kwargs)
            result['processor_name'] = processor_name

            # 完全なログ出力
            logger.info(f"[B-1] {processor_name} 処理完了")
            logger.info(f"[B-1] is_structured: {result.get('is_structured', False)}")

            structured_tables = result.get('structured_tables', [])
            logger.info(f"[B-1] structured_tables: {len(structured_tables)}個")

            # 各表の詳細をログ出力
            for idx, table in enumerate(structured_tables):
                rows = table.get('rows', len(table.get('data', [])))
                cols = table.get('cols', len(table.get('data', [[]])[0]) if table.get('data') else 0)
                has_source = 'source' in table
                source = table.get('source', 'MISSING')
                logger.info(f"[B-1]   Table {idx}: {rows}行×{cols}列, source={source}, has_source_key={has_source}")

                # dataの存在確認
                data = table.get('data')
                if data is None:
                    logger.warning(f"[B-1]   Table {idx}: data キーがありません！")
                elif not isinstance(data, list):
                    logger.warning(f"[B-1]   Table {idx}: data が list ではありません: {type(data)}")
                elif len(data) == 0:
                    logger.warning(f"[B-1]   Table {idx}: data が空です")
                elif data == [[]]:
                    logger.warning(f"[B-1]   Table {idx}: data = [[]]（空リスト）")

            return result
        except Exception as e:
            logger.error(f"[B-1] プロセッサ実行エラー: {e}", exc_info=True)
            return {
                'is_structured': False,
                'error': str(e),
                'processor_name': processor_name
            }
