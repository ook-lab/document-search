"""
Stage B: 形式特化型・物理構造化

各アプリケーション特有のPDF描画構造を解析し、
リッチテキストと構造化データを抽出する。

コントローラー:
- B1Controller: Stage B Orchestrator（プロセッサ選択・実行）

ネイティブ処理（100%精度）:
- B6NativeWordProcessor: .docx専用
- B7NativeExcelProcessor: .xlsx専用
- B8NativePPTProcessor: .pptx専用

PDF処理（座標解析）:
- B3PDFWordProcessor: PDF-Word専用
- B4PDFExcelProcessor: PDF-Excel専用
- B5PDFPPTProcessor: PDF-PowerPoint専用
- B11GoogleDocsProcessor: Google Docs由来PDF
- B12GoogleSheetsProcessor: Google Sheets由来PDF
- B14GoodnotesProcessor: Goodnotes PDF専用
- B30DtpProcessor: InDesign/Scan由来PDF

特化型処理:
- B42MultiColumnReportProcessor: 多段組レポート専用

抽出＋削除統合:
- 各プロセッサ（B3, B11, B12, etc.）で抽出と同時に削除を実行
- purged_pdf_path を Stage D に渡す
"""

from .b1_controller import B1Controller
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

__all__ = [
    'B1Controller',
    'B3PDFWordProcessor',
    'B4PDFExcelProcessor',
    'B5PDFPPTProcessor',
    'B6NativeWordProcessor',
    'B7NativeExcelProcessor',
    'B8NativePPTProcessor',
    'B11GoogleDocsProcessor',
    'B12GoogleSheetsProcessor',
    'B14GoodnotesProcessor',
    'B30DtpProcessor',
    'B42MultiColumnReportProcessor',
]
