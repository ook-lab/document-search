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

from .b3_pdf_word import B3PDFWordProcessor
from .b4_pdf_excel import B4PDFExcelProcessor
from .b5_pdf_ppt import B5PDFPPTProcessor
from .b6_native_word import B6NativeWordProcessor
from .b7_native_excel import B7NativeExcelProcessor
from .b8_native_ppt import B8NativePPTProcessor
from .b11_google_docs import B11GoogleDocsProcessor
from .b12_google_sheets import B12GoogleSheetsProcessor
from .b14_goodnotes_processor import B14GoodnotesProcessor
from .b30_dtp import B30DtpProcessor
from .b42_multicolumn_report import B42MultiColumnReportProcessor
from .b90_result_merger import B90ResultMerger

# COVER / UNKNOWN はコンテンツなし扱い（B プロセッサを割り当てない）
_NON_CONTENT_TYPES = {'COVER', 'UNKNOWN'}

# ページ種別 → デフォルトプロセッサ名のマップ（FIXED レイアウト判断は B1 が上書き）
_TYPE_TO_PROCESSOR: Dict[str, str] = {
    'REPORT': 'B42_MULTICOLUMN',
    'DTP':    'B30_DTP',
    'WORD':   'B3_PDF_WORD',
    'GOOGLE_DOCS':    'B11_GOOGLE_DOCS',
    'GOOGLE_SHEETS':  'B12_GOOGLE_SHEETS',
    'GOODNOTES':      'B14_GOODNOTES',
}


class B1Controller:
    """B-1: Stage B Controller（Orchestrator）"""

    def __init__(self):
        """B-1 コントローラー初期化"""
        # プロセッサインスタンスを作成
        self.b3_pdf_word = B3PDFWordProcessor()
        self.b4_pdf_excel = B4PDFExcelProcessor()
        self.b5_pdf_ppt = B5PDFPPTProcessor()
        self.b6_native_word = B6NativeWordProcessor()
        self.b7_native_excel = B7NativeExcelProcessor()
        self.b8_native_ppt = B8NativePPTProcessor()
        self.b11_google_docs = B11GoogleDocsProcessor()
        self.b12_google_sheets = B12GoogleSheetsProcessor()
        self.b14_goodnotes = B14GoodnotesProcessor()
        self.b30_dtp = B30DtpProcessor()
        self.b42_multicolumn = B42MultiColumnReportProcessor()
        self.b90_merger = B90ResultMerger()

    def process(
        self,
        file_path: str | Path,
        a_result: Optional[Dict[str, Any]] = None,
        force_processor: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Stage Aの結果に基づいて適切なプロセッサを選択・実行

        Args:
            file_path: ファイルパス
            a_result: Stage Aの実行結果（document_typeを含む）
            force_processor: 強制的に使用するプロセッサ名（オプション）

        Returns:
            Stage Bの実行結果
        """
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

        if a_result:
            # 2軸取得（新形式）
            origin_app = a_result.get('origin_app') or a_result.get('document_type')
            layout_profile = a_result.get('layout_profile', 'FLOW')
            type_groups = a_result.get('type_groups', {}) or {}
            page_type_map = a_result.get('page_type_map', {}) or {}
            logger.info(f"  ├─ Stage A判定（作成ソフト）: {origin_app}")
            logger.info(f"  ├─ Stage A判定（レイアウト）: {layout_profile}")
            logger.info(f"  └─ type_groups: { {t: len(p) for t, p in type_groups.items()} }")

        # ── MIXED / 複数コンテンツ型: B90 経由マルチプロセッサ処理 ──
        content_groups = {t: p for t, p in type_groups.items()
                          if t not in _NON_CONTENT_TYPES}
        if len(content_groups) > 1:
            return self._process_multi_type(
                file_path, content_groups, page_type_map, allowed, layout_profile
            )

        # ── 単一型（従来動作）──
        processor_name = self._select_processor(file_ext, origin_app, layout_profile)

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

        # プロセッサを実行
        return self._execute_processor(processor_name, file_path)

    def _process_multi_type(
        self,
        file_path: Path,
        content_groups: Dict[str, List[int]],
        page_type_map: Dict[int, str],
        allowed: List[str],
        layout_profile: str,
    ) -> Dict[str, Any]:
        """
        MIXED 文書: 型グループごとに masked_pages 付きでプロセッサを実行し、
        B90 でマージして返す。
        """
        total_pages = max(page_type_map.keys()) + 1 if page_type_map else 0
        logger.info(f"[B-1] MIXED 処理開始: {len(content_groups)}型 / {total_pages}ページ")

        raw_results: List[Dict[str, Any]] = []

        for ptype, page_indices in content_groups.items():
            processor_name = _TYPE_TO_PROCESSOR.get(ptype)
            if not processor_name:
                logger.warning(f"[B-1]   {ptype} → 対応プロセッサなし → スキップ")
                continue
            if processor_name not in allowed:
                logger.warning(f"[B-1]   {ptype} → {processor_name} がallowlist外 → スキップ")
                continue

            # このグループ以外のページをマスク
            masked = [i for i in range(total_pages) if i not in page_indices]
            logger.info(f"[B-1]   {ptype}: {processor_name} pages={page_indices} masked={masked}")

            result = self._execute_processor(processor_name, file_path, masked_pages=masked)
            result['_source_type'] = ptype
            result['_source_pages'] = page_indices
            raw_results.append(result)

        if not raw_results:
            return {
                'is_structured': False,
                'error': 'MIXED 処理: すべての型グループがスキップされました',
                'processor_name': 'B1_CONTROLLER',
            }

        if len(raw_results) == 1:
            # 1件のみ → マージ不要
            r = raw_results[0]
            r.pop('_source_type', None)
            r.pop('_source_pages', None)
            return r

        # B90 でマージ
        logger.info(f"[B-1] B90 ResultMerger に渡す: {len(raw_results)}件")
        return self.b90_merger.merge(raw_results)

    def _select_processor(
        self,
        file_ext: str,
        origin_app: Optional[str],
        layout_profile: str = 'FLOW'
    ) -> str:
        """
        ファイル拡張子、作成ソフト、レイアウト特性からプロセッサを選択（2軸ルーティング）

        Args:
            file_ext: ファイル拡張子
            origin_app: 作成ソフト（WORD, INDESIGN, GOODNOTES, ...）
            layout_profile: レイアウト特性（FLOW, FIXED, HYBRID）

        Returns:
            プロセッサ名
        """
        # ========================================
        # 特化型プロセッサ（B-14、B-40番台）の判定
        # ========================================
        if origin_app == 'GOODNOTES':
            return 'B14_GOODNOTES'

        if origin_app == 'REPORT':
            return 'B42_MULTICOLUMN'

        # ========================================
        # Native処理（B-6, B-7, B-8）
        # ========================================
        if file_ext == '.docx':
            return 'B6_NATIVE_WORD'
        elif file_ext == '.xlsx':
            return 'B7_NATIVE_EXCEL'
        elif file_ext == '.pptx':
            return 'B8_NATIVE_PPT'

        # ========================================
        # PDF処理（B-3, B-4, B-5, B-11, B-12, B-30）
        # ========================================
        elif file_ext == '.pdf':
            # ────────────────────────────────────
            # Word由来PDF: layout_profile で振り分け
            # ────────────────────────────────────
            if origin_app == 'WORD':
                if layout_profile == 'FLOW':
                    # 文章流し込み型 → Word専用プロセッサ
                    logger.info("[B-1] WORD + FLOW → B3_PDF_WORD")
                    return 'B3_PDF_WORD'
                else:  # FIXED or HYBRID
                    # 固定レイアウト型 → DTP/OCR併用
                    logger.info(f"[B-1] WORD + {layout_profile} → B30_DTP")
                    return 'B30_DTP'

            # ────────────────────────────────────
            # その他のPDF
            # ────────────────────────────────────
            elif origin_app == 'EXCEL':
                return 'B4_PDF_EXCEL'
            elif origin_app == 'POWERPOINT' or origin_app == 'PPT':
                return 'B5_PDF_PPT'
            elif origin_app == 'GOOGLE_DOCS':
                return 'B11_GOOGLE_DOCS'
            elif origin_app == 'GOOGLE_SHEETS':
                return 'B12_GOOGLE_SHEETS'
            elif origin_app == 'INDESIGN':
                return 'B30_DTP'
            elif origin_app == 'SCAN':
                # スキャンPDFは汎用DTP処理
                return 'B30_DTP'
            else:
                # 判定不能の場合は処理を停止（自動でスキャン扱いしない）
                logger.error(f"[B-1] 未知の origin_app: {origin_app} → 処理停止")
                return 'UNKNOWN'

        # ========================================
        # 未対応の拡張子
        # ========================================
        else:
            logger.error(f"[B-1] 未対応の拡張子: {file_ext}")
            return 'UNKNOWN'

    def _execute_processor(
        self,
        processor_name: str,
        file_path: Path,
        masked_pages: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """
        プロセッサを実行

        Args:
            processor_name: プロセッサ名
            file_path: ファイルパス
            masked_pages: スキップするページ番号リスト（0始まり）

        Returns:
            プロセッサの実行結果
        """
        processor_map = {
            'B3_PDF_WORD': self.b3_pdf_word,
            'B4_PDF_EXCEL': self.b4_pdf_excel,
            'B5_PDF_PPT': self.b5_pdf_ppt,
            'B6_NATIVE_WORD': self.b6_native_word,
            'B7_NATIVE_EXCEL': self.b7_native_excel,
            'B8_NATIVE_PPT': self.b8_native_ppt,
            'B11_GOOGLE_DOCS': self.b11_google_docs,
            'B12_GOOGLE_SHEETS': self.b12_google_sheets,
            'B14_GOODNOTES': self.b14_goodnotes,
            'B30_DTP': self.b30_dtp,
            'B42_MULTICOLUMN': self.b42_multicolumn,
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
            # masked_pages を受け付けるプロセッサには渡す
            import inspect
            sig = inspect.signature(processor.process)
            if 'masked_pages' in sig.parameters and masked_pages:
                result = processor.process(file_path, masked_pages=masked_pages)
            else:
                result = processor.process(file_path)
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
