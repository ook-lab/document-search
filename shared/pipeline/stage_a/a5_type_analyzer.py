"""
A-5: Document Type Analyzer（書類種類判断）

PDFのメタデータ（Creator, Producer）を解析し、以下のタイプを判定:
- GOODNOTES: Goodnotes 由来
- GOOGLE_DOCS: Google Docs 由来
- GOOGLE_SHEETS: Google Spreadsheet 由来
- WORD: Microsoft Word 由来
- INDESIGN: Adobe InDesign 由来
- EXCEL: Microsoft Excel 由来
- REPORT: WINJr等の帳票出力システム由来
- DTP: Illustrator等の DTP ツール由来
- SCAN: スキャナ/複合機由来、またはメタデータが空

ページ別解析（page_type_map / type_groups）:
- 全ページのフォントを解析してページ単位の種別を判定
- 表紙(DTP)＋本文(REPORT)のような混在 PDF を MIXED として検出
- type_groups を B1 Controller に渡し、B プロセッサが masked_pages で分岐する
"""

from pathlib import Path
from typing import Dict, Any, Optional, List
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

    # ページ別フォント分類パターン（優先順位順）
    # フォント名は subset prefix（例: BCDEEE+）を除いた後の名前で照合
    PAGE_FONT_REPORT = [
        r'wing',              # DFMincho-UB-WING-RKSJ-H 等（WINJr 帳票システム）
    ]
    PAGE_FONT_DTP = [
        r'kozmin',            # KozMinPro / KozMinPr6N（Illustrator/InDesign）
        r'kozgo',             # KozGoPro
        r'minionpro',         # MinionPro（Illustrator）
        r'myriad',            # MyriadPro（Illustrator）
        r'midashi',           # 見出し系DTPフォント
    ]
    PAGE_FONT_WORD = [
        r'ms-pgothic',        # MS Pゴシック
        r'ms-gothic',         # MS ゴシック
        r'ms-pmincho',        # MS P明朝
        r'ms-mincho',         # MS 明朝
        r'meiryo',            # メイリオ
        r'yu\s*gothic',       # 游ゴシック
        r'yu\s*mincho',       # 游明朝
        r'calibri',           # Calibri（Word 標準欧文フォント）
        r'times\s*new\s*roman',
        r'arial',
        r'cambria',
    ]

    # ページをカバー（表紙/裏表紙）とみなす文字数閾値
    COVER_CHAR_THRESHOLD = 150

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
        logger.info(f"[A-2 TypeAnalyzer] 書類種類判断開始: {file_path.name}")

        # メタデータを取得
        metadata = self._extract_metadata(file_path)

        if not metadata:
            logger.warning("[A-2 TypeAnalyzer] メタデータが取得できませんでした → SCAN判定")
            return {
                'document_type': 'SCAN',
                'raw_metadata': {},
                'confidence': 'HIGH',
                'reason': 'メタデータなし'
            }

        # 完全なメタデータをログ出力
        logger.info("[A-2 TypeAnalyzer] 取得したメタデータ（全項目）:")
        import json
        for key, value in metadata.items():
            logger.info(f"  ├─ {key}: {json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value}")

        # Creator と Producer を取得
        creator = metadata.get('Creator', '').strip()
        producer = metadata.get('Producer', '').strip()

        logger.info("[A-2 TypeAnalyzer] 判定用キーフィールド:")
        logger.info(f"  ├─ Creator: '{creator}'")
        logger.info(f"  └─ Producer: '{producer}'")

        # メタデータ判定を実行
        meta_type, meta_confidence, meta_reason = self._classify_document(creator, producer)

        # ページ別フォント解析（常時実行）
        page_type_map = self._analyze_pages(file_path)
        type_groups = self._group_pages_by_type(page_type_map)

        # ページ解析でより正確な判定が得られれば上書き
        doc_type, confidence, reason = self._determine_final_type(
            meta_type, meta_confidence, meta_reason, type_groups
        )

        logger.info(f"[A-2 TypeAnalyzer] 最終判定結果: {doc_type} (信頼度: {confidence})")
        logger.info(f"[A-2 TypeAnalyzer] 判定理由: {reason}")
        logger.info(f"[A-2 TypeAnalyzer] ページ種別: { {t: len(p) for t, p in type_groups.items()} }")

        return {
            'document_type': doc_type,
            'raw_metadata': metadata,
            'confidence': confidence,
            'reason': reason,
            'page_type_map': page_type_map,   # {page_idx: type_str}
            'type_groups': type_groups,        # {type_str: [page_idx, ...]}
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
            logger.info(f"[A-2 TypeAnalyzer] pdfplumberでメタデータ取得を試行...")
            with pdfplumber.open(str(file_path)) as pdf:
                metadata = pdf.metadata or {}
                logger.info(f"[A-2 TypeAnalyzer] pdfplumberでメタデータ取得成功: {len(metadata)}項目")
                return metadata
        except Exception as e:
            logger.warning(f"[A-2 TypeAnalyzer] pdfplumber取得失敗: {e}")

        # PyMuPDFで取得を試行
        try:
            import fitz
            logger.info(f"[A-2 TypeAnalyzer] PyMuPDFでメタデータ取得を試行...")
            doc = fitz.open(str(file_path))
            metadata = doc.metadata or {}
            doc.close()
            logger.info(f"[A-2 TypeAnalyzer] PyMuPDFでメタデータ取得成功: {len(metadata)}項目")
            return metadata
        except Exception as e:
            logger.warning(f"[A-2 TypeAnalyzer] PyMuPDF取得失敗: {e}", exc_info=True)

        logger.error("[A-2 TypeAnalyzer] すべてのメタデータ取得方法が失敗しました")
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

        logger.info("[A-2 TypeAnalyzer] パターンマッチング開始:")
        logger.info(f"  ├─ 検索対象: '{combined}'")

        # GOODNOTES判定（最優先：特化型のため）
        logger.debug("  ├─ GOODNOTES パターンチェック...")
        for pattern in self.GOODNOTES_KEYWORDS:
            if re.search(pattern, combined, re.IGNORECASE):
                logger.info(f"  ✓ GOODNOTES 一致: パターン '{pattern}'")
                return 'GOODNOTES', 'HIGH', f'キーワード一致: {pattern}'

        # GOOGLE_DOCS判定
        logger.debug("  ├─ GOOGLE_DOCS パターンチェック...")
        for pattern in self.GOOGLE_DOCS_KEYWORDS:
            if re.search(pattern, combined, re.IGNORECASE):
                logger.info(f"  ✓ GOOGLE_DOCS 一致: パターン '{pattern}'")
                return 'GOOGLE_DOCS', 'HIGH', f'キーワード一致: {pattern}'

        # GOOGLE_SHEETS判定
        logger.debug("  ├─ GOOGLE_SHEETS パターンチェック...")
        for pattern in self.GOOGLE_SHEETS_KEYWORDS:
            if re.search(pattern, combined, re.IGNORECASE):
                logger.info(f"  ✓ GOOGLE_SHEETS 一致: パターン '{pattern}'")
                return 'GOOGLE_SHEETS', 'HIGH', f'キーワード一致: {pattern}'

        # WORD判定
        logger.debug("  ├─ WORD パターンチェック...")
        for pattern in self.WORD_KEYWORDS:
            if re.search(pattern, combined, re.IGNORECASE):
                logger.info(f"  ✓ WORD 一致: パターン '{pattern}'")
                return 'WORD', 'HIGH', f'キーワード一致: {pattern}'

        # INDESIGN判定
        logger.debug("  ├─ INDESIGN パターンチェック...")
        for pattern in self.INDESIGN_KEYWORDS:
            if re.search(pattern, combined, re.IGNORECASE):
                logger.info(f"  ✓ INDESIGN 一致: パターン '{pattern}'")
                return 'INDESIGN', 'HIGH', f'キーワード一致: {pattern}'

        # EXCEL判定
        logger.debug("  ├─ EXCEL パターンチェック...")
        for pattern in self.EXCEL_KEYWORDS:
            if re.search(pattern, combined, re.IGNORECASE):
                logger.info(f"  ✓ EXCEL 一致: パターン '{pattern}'")
                return 'EXCEL', 'HIGH', f'キーワード一致: {pattern}'

        # SCAN判定
        logger.debug("  ├─ SCAN パターンチェック...")
        for pattern in self.SCAN_KEYWORDS:
            if re.search(pattern, combined, re.IGNORECASE):
                logger.info(f"  ✓ SCAN 一致: パターン '{pattern}'")
                return 'SCAN', 'HIGH', f'スキャナキーワード一致: {pattern}'

        # メタデータが空の場合はSCAN
        if not creator and not producer:
            logger.warning("  ✗ すべてのパターン不一致: Creator/Producer が空 → SCAN（HIGH）")
            return 'SCAN', 'HIGH', 'Creator/Producer が空'

        # 判定不能の場合はSCANとして扱う（安全側に倒す）
        logger.warning(f"  ✗ すべてのパターン不一致: Creator='{creator}', Producer='{producer}' → SCAN（LOW）")
        return 'SCAN', 'LOW', f'判定不能 (Creator: {creator}, Producer: {producer})'

    # ------------------------------------------------------------------
    # ページ別解析
    # ------------------------------------------------------------------

    def _analyze_pages(self, file_path: Path) -> Dict[int, str]:
        """
        全ページのフォントを解析してページ別タイプを返す

        Returns:
            {page_idx(0始まり): type_str}
            type_str: REPORT / WORD / DTP / COVER / UNKNOWN
        """
        result: Dict[int, str] = {}
        try:
            import pdfplumber
            with pdfplumber.open(str(file_path)) as pdf:
                for i, page in enumerate(pdf.pages):
                    result[i] = self._classify_page(page)
        except Exception as e:
            logger.warning(f"[A-2 TypeAnalyzer] ページ別解析失敗: {e}")
        return result

    def _classify_page(self, page) -> str:
        """1ページのフォントから種別を判定"""
        chars = page.chars or []
        char_count = len(chars)

        # 文字数が極端に少ない → カバーページ（COVER）
        if char_count < self.COVER_CHAR_THRESHOLD:
            return 'COVER'

        # フォント名収集（subset prefix を除去）
        fonts: set[str] = set()
        for c in chars:
            fn = c.get('fontname', '')
            if '+' in fn:
                fn = fn.split('+', 1)[1]
            fonts.add(fn.lower())

        # 優先順位順にチェック
        for font in fonts:
            for pattern in self.PAGE_FONT_REPORT:
                if re.search(pattern, font, re.IGNORECASE):
                    return 'REPORT'

        for font in fonts:
            for pattern in self.PAGE_FONT_DTP:
                if re.search(pattern, font, re.IGNORECASE):
                    return 'DTP'

        for font in fonts:
            for pattern in self.PAGE_FONT_WORD:
                if re.search(pattern, font, re.IGNORECASE):
                    return 'WORD'

        return 'UNKNOWN'

    def _group_pages_by_type(self, page_type_map: Dict[int, str]) -> Dict[str, List[int]]:
        """page_type_map を type → [page_idx, ...] に変換"""
        groups: Dict[str, List[int]] = {}
        for idx, ptype in page_type_map.items():
            groups.setdefault(ptype, []).append(idx)
        # ページ番号順にソート
        for pages in groups.values():
            pages.sort()
        return groups

    def _determine_final_type(
        self,
        meta_type: str,
        meta_confidence: str,
        meta_reason: str,
        type_groups: Dict[str, List[int]]
    ) -> tuple[str, str, str]:
        """
        メタデータ判定 + ページ別解析を統合して最終種別を決定

        ページ解析で明確な種別が分かれば HIGH として上書きする。
        """
        # COVER / UNKNOWN のみは「コンテンツなし」としてメタデータ判定を使う
        content_types = {t: p for t, p in type_groups.items()
                         if t not in ('COVER', 'UNKNOWN')}

        if not content_types:
            # コンテンツページが検出されなかった → メタデータ判定をそのまま使用
            return meta_type, meta_confidence, meta_reason

        if len(content_types) == 1:
            # コンテンツ種別が1種類 → そのタイプを HIGH で確定
            ptype = list(content_types.keys())[0]
            page_list = list(content_types.values())[0]
            reason = f'ページフォント解析: {ptype} ({len(page_list)}ページ)'
            return ptype, 'HIGH', reason

        # 複数種別 → MIXED（B1 が type_groups を見て複数プロセッサを走らせる）
        summary = ', '.join(f'{t}:{len(p)}p' for t, p in sorted(content_types.items()))
        reason = f'ページ混在: {summary}'
        logger.info(f"[A-2 TypeAnalyzer] 混在文書検出: {reason}")
        return 'MIXED', 'HIGH', reason
