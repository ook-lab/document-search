"""
B-1: Stage B Controller（Orchestrator）

Stage Aの判定結果に基づいて、適切なStage Bプロセッサを選択・実行する。

振り分けロジック:
1. ファイル拡張子の確認（PDF vs Native）
2. Stage Aの document_type に基づいてプロセッサを選択
3. 特化型プロセッサ（B-40番台）への対応
"""

from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger

from .b3_pdf_word import B3PDFWordProcessor
from .b4_pdf_excel import B4PDFExcelProcessor
from .b5_pdf_ppt import B5PDFPPTProcessor
from .b6_native_word import B6NativeWordProcessor
from .b7_native_excel import B7NativeExcelProcessor
from .b8_native_ppt import B8NativePPTProcessor
from .b10_dtp import B10DtpProcessor
from .b14_goodnotes_processor import B14GoodnotesProcessor
from .b42_multicolumn_report import B42MultiColumnReportProcessor
from .b90_layer_purge import B90LayerPurgeProcessor


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
        self.b10_dtp = B10DtpProcessor()
        self.b14_goodnotes = B14GoodnotesProcessor()
        self.b42_multicolumn = B42MultiColumnReportProcessor()
        self.b90_layer_purge = B90LayerPurgeProcessor()

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

        # 強制指定がある場合
        if force_processor:
            logger.info(f"  └─ 強制指定: {force_processor}")
            return self._execute_processor(force_processor, file_path)

        # Stage A の結果から document_type を取得
        document_type = None
        if a_result:
            document_type = a_result.get('document_type')
            logger.info(f"  └─ Stage A判定: {document_type}")

        # プロセッサを選択
        processor_name = self._select_processor(file_ext, document_type)

        logger.info(f"[B-1] 選択されたプロセッサ: {processor_name}")
        logger.info("=" * 60)

        # プロセッサを実行
        return self._execute_processor(processor_name, file_path)

    def _select_processor(self, file_ext: str, document_type: Optional[str]) -> str:
        """
        ファイル拡張子とdocument_typeからプロセッサを選択

        Args:
            file_ext: ファイル拡張子
            document_type: Stage Aの判定結果

        Returns:
            プロセッサ名
        """
        # ========================================
        # 特化型プロセッサ（B-10番台、B-40番台）の判定
        # ========================================
        if document_type == 'GOODNOTES':
            return 'B14_GOODNOTES'

        if document_type == 'REPORT':
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
        # PDF処理（B-3, B-4, B-5, B-10）
        # ========================================
        elif file_ext == '.pdf':
            if document_type == 'WORD':
                return 'B3_PDF_WORD'
            elif document_type == 'EXCEL':
                return 'B4_PDF_EXCEL'
            elif document_type == 'POWERPOINT' or document_type == 'PPT':
                return 'B5_PDF_PPT'
            elif document_type == 'INDESIGN':
                return 'B10_DTP'
            elif document_type == 'SCAN':
                # スキャンPDFは汎用DTP処理
                return 'B10_DTP'
            else:
                # 判定不能の場合は汎用DTP処理
                logger.warning(f"[B-1] 未知の document_type: {document_type}, B10_DTPで処理")
                return 'B10_DTP'

        # ========================================
        # 未対応の拡張子
        # ========================================
        else:
            logger.error(f"[B-1] 未対応の拡張子: {file_ext}")
            return 'UNKNOWN'

    def _execute_processor(self, processor_name: str, file_path: Path) -> Dict[str, Any]:
        """
        プロセッサを実行

        Args:
            processor_name: プロセッサ名
            file_path: ファイルパス

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
            'B10_DTP': self.b10_dtp,
            'B14_GOODNOTES': self.b14_goodnotes,
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
            result = processor.process(file_path)
            result['processor_name'] = processor_name

            # B-90: Layer Purge (PDFファイルかつ構造化成功の場合のみ)
            file_ext = file_path.suffix.lower()
            if file_ext == '.pdf' and result.get('is_structured'):
                logger.info("[B-1] B-90 Layer Purge を実行")
                purge_result = self.b90_layer_purge.purge(file_path, result)

                if purge_result.get('success'):
                    result['purged_pdf_path'] = purge_result['purged_pdf_path']
                    result['purged_image_paths'] = purge_result['purged_image_paths']
                    result['mask_stats'] = purge_result['mask_stats']
                    logger.info(f"[B-1] B-90 完了: {len(purge_result['purged_image_paths'])}枚の画像を生成")
                else:
                    logger.warning(f"[B-1] B-90 失敗: {purge_result.get('error')}")

            return result
        except Exception as e:
            logger.error(f"[B-1] プロセッサ実行エラー: {e}", exc_info=True)
            return {
                'is_structured': False,
                'error': str(e),
                'processor_name': processor_name
            }
