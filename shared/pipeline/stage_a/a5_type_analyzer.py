"""
A-5: Document Type Analyzer（書類種類判断）

PDFのメタデータ（Creator, Producer）を解析し、以下のタイプを判定:
- GOODNOTES: Goodnotes 由来
- GOOGLE_DOCS: Google Docs 由来
- GOOGLE_SHEETS: Google Spreadsheet 由来
- WORD: Microsoft Word 由来
- INDESIGN: Adobe InDesign 由来
- EXCEL: Microsoft Excel 由来
- SCAN: スキャナ/複合機由来、またはメタデータが空
"""

from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger
import re


class A5TypeAnalyzer:
    """A-5: Document Type Analyzer（書類種類判断）"""

    # キーワードパターン（大文字小文字を無視）
    GOODNOTES_KEYWORDS = [
        r'goodnotes',
        r'good.*notes',
    ]

    GOOGLE_DOCS_KEYWORDS = [
        r'google.*docs',
        r'google docs renderer',
    ]

    GOOGLE_SHEETS_KEYWORDS = [
        r'google.*sheets',
    ]

    WORD_KEYWORDS = [
        r'microsoft.*word',
        r'word',
        r'winword',
    ]

    INDESIGN_KEYWORDS = [
        r'adobe.*indesign',
        r'indesign',
    ]

    EXCEL_KEYWORDS = [
        r'microsoft.*excel',
        r'excel',
    ]

    SCAN_KEYWORDS = [
        r'scan',
        r'scanner',
        r'ricoh',
        r'canon',
        r'xerox',
        r'epson',
        r'hp.*scanner',
        r'konica.*minolta',
    ]

    def analyze(self, file_path: Path) -> Dict[str, Any]:
        """
        PDFのメタデータを解析して書類種類を判定

        Args:
            file_path: PDFファイルパス

        Returns:
            {
                'document_type': str,  # GOODNOTES, WORD, INDESIGN, EXCEL, SCAN
                'raw_metadata': dict,  # 取得した全メタデータ
                'confidence': str,     # HIGH, MEDIUM, LOW
                'reason': str          # 判定理由
            }
        """
        logger.info(f"[A-5] 書類種類判断開始: {file_path.name}")

        # メタデータを取得
        metadata = self._extract_metadata(file_path)

        if not metadata:
            logger.warning("[A-5] メタデータが取得できませんでした → SCAN判定")
            return {
                'document_type': 'SCAN',
                'raw_metadata': {},
                'confidence': 'HIGH',
                'reason': 'メタデータなし'
            }

        # Creator と Producer を取得
        creator = metadata.get('Creator', '').strip()
        producer = metadata.get('Producer', '').strip()

        logger.info(f"[A-5] Creator: '{creator}'")
        logger.info(f"[A-5] Producer: '{producer}'")

        # 判定を実行
        doc_type, confidence, reason = self._classify_document(creator, producer)

        logger.info(f"[A-5] 判定結果: {doc_type} (信頼度: {confidence}, 理由: {reason})")

        return {
            'document_type': doc_type,
            'raw_metadata': metadata,
            'confidence': confidence,
            'reason': reason
        }

    def _extract_metadata(self, file_path: Path) -> Dict[str, Any]:
        """
        PDFメタデータを取得（pdfplumber優先、フォールバックでPyMuPDF）

        Args:
            file_path: PDFファイルパス

        Returns:
            メタデータ辞書
        """
        metadata = {}

        # pdfplumberで取得を試行
        try:
            import pdfplumber
            with pdfplumber.open(str(file_path)) as pdf:
                metadata = pdf.metadata or {}
                logger.debug(f"[A-5] pdfplumberでメタデータ取得: {len(metadata)}項目")
                return metadata
        except Exception as e:
            logger.warning(f"[A-5] pdfplumber取得失敗: {e}")

        # PyMuPDFで取得を試行
        try:
            import fitz
            doc = fitz.open(str(file_path))
            metadata = doc.metadata or {}
            doc.close()
            logger.debug(f"[A-5] PyMuPDFでメタデータ取得: {len(metadata)}項目")
            return metadata
        except Exception as e:
            logger.warning(f"[A-5] PyMuPDF取得失敗: {e}")

        return {}

    def _classify_document(
        self,
        creator: str,
        producer: str
    ) -> tuple[str, str, str]:
        """
        Creator/Producerから書類種類を判定

        Args:
            creator: Creator フィールド
            producer: Producer フィールド

        Returns:
            (document_type, confidence, reason)
        """
        # 大文字小文字を無視するために正規化
        creator_lower = creator.lower()
        producer_lower = producer.lower()
        combined = f"{creator_lower} {producer_lower}"

        # GOODNOTES判定（最優先：特化型のため）
        for pattern in self.GOODNOTES_KEYWORDS:
            if re.search(pattern, combined, re.IGNORECASE):
                return 'GOODNOTES', 'HIGH', f'キーワード一致: {pattern}'

        # GOOGLE_DOCS判定
        for pattern in self.GOOGLE_DOCS_KEYWORDS:
            if re.search(pattern, combined, re.IGNORECASE):
                return 'GOOGLE_DOCS', 'HIGH', f'キーワード一致: {pattern}'

        # GOOGLE_SHEETS判定
        for pattern in self.GOOGLE_SHEETS_KEYWORDS:
            if re.search(pattern, combined, re.IGNORECASE):
                return 'GOOGLE_SHEETS', 'HIGH', f'キーワード一致: {pattern}'

        # WORD判定
        for pattern in self.WORD_KEYWORDS:
            if re.search(pattern, combined, re.IGNORECASE):
                return 'WORD', 'HIGH', f'キーワード一致: {pattern}'

        # INDESIGN判定
        for pattern in self.INDESIGN_KEYWORDS:
            if re.search(pattern, combined, re.IGNORECASE):
                return 'INDESIGN', 'HIGH', f'キーワード一致: {pattern}'

        # EXCEL判定
        for pattern in self.EXCEL_KEYWORDS:
            if re.search(pattern, combined, re.IGNORECASE):
                return 'EXCEL', 'HIGH', f'キーワード一致: {pattern}'

        # SCAN判定
        for pattern in self.SCAN_KEYWORDS:
            if re.search(pattern, combined, re.IGNORECASE):
                return 'SCAN', 'HIGH', f'スキャナキーワード一致: {pattern}'

        # メタデータが空の場合はSCAN
        if not creator and not producer:
            return 'SCAN', 'HIGH', 'Creator/Producer が空'

        # 判定不能の場合はSCANとして扱う（安全側に倒す）
        return 'SCAN', 'LOW', f'判定不能 (Creator: {creator}, Producer: {producer})'
