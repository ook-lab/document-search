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
- SCAN: スキャナ/複合機由来（Ricoh, Canon, Xerox 等のスキャナキーワード一致）
- UNKNOWN: 判定不能（メタデータなし、またはどのパターンにも一致しない）

ページ別解析（page_type_map / type_groups）:
- 全ページのフォントを解析してページ単位の種別を判定
- 表紙(DTP)＋本文(REPORT)のような混在 PDF を MIXED として検出
- type_groups を B1 Controller に渡し、B プロセッサが masked_pages で分岐する
"""

from pathlib import Path
from typing import Dict, Any, Optional, List
from loguru import logger
import re
import yaml

# 判定ルール定義ファイル（唯一の設定場所）
_RULES_FILE = Path(__file__).parent / 'type_rules.yaml'


def _load_rules() -> dict:
    with open(_RULES_FILE, encoding='utf-8') as f:
        return yaml.safe_load(f)


class A5TypeAnalyzer:
    """A-5: Document Type Analyzer（書類種類判断）

    判定ルールは type_rules.yaml で一元管理。
    パターン追加・変更はそのファイルのみ編集すればよい。
    """

    def __init__(self):
        rules = _load_rules()
        page = rules.get('page_font_patterns', {})

        # Creator照合パターン（主要判定）: {app_name: [patterns], ...} 順序保持
        self.CREATOR_PATTERNS = rules.get('creator_patterns', {})

        # Producer照合パターン（補助判定・Creator不明時のみ使用）
        self.PRODUCER_PATTERNS = rules.get('producer_patterns', {})

        # Title拡張子 + Producer 組み合わせ判定（Step 2.5）
        self.TITLE_EXT_PATTERNS = rules.get('title_extension_patterns', [])

        # ページフォント照合パターン（固有フォントのみ）
        self.PAGE_FONT_REPORT = page.get('REPORT', [])
        self.PAGE_FONT_DTP    = page.get('DTP', [])

    def analyze(self, file_path: Path) -> Dict[str, Any]:
        """
        PDFのメタデータを解析して書類種類を判定

        Args:
            file_path: PDFファイルパス

        Returns:
            {
                'document_type': str,  # GOODNOTES, WORD, INDESIGN, EXCEL, SCAN, UNKNOWN
                'raw_metadata': dict,  # 取得した全メタデータ
                'confidence': str,     # HIGH, MEDIUM, LOW
                'reason': str          # 判定理由
            }
        """
        logger.info(f"[A-5 TypeAnalyzer] 書類種類判断開始: {file_path.name}")

        # メタデータを取得
        metadata = self._extract_metadata(file_path)

        if not metadata:
            logger.warning("[A-5 TypeAnalyzer] メタデータが取得できませんでした → UNKNOWN判定")
            return {
                'document_type': 'UNKNOWN',
                'raw_metadata': {},
                'confidence': 'LOW',
                'reason': 'メタデータなし',
                'meta_match_detail': None,
                'page_font_detail': {},
            }

        # 完全なメタデータをログ出力
        logger.info("[A-5 TypeAnalyzer] 取得したメタデータ（全項目）:")
        import json
        for key, value in metadata.items():
            logger.info(f"  ├─ {key}: {json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value}")

        # Creator と Producer を取得
        creator = metadata.get('Creator', '').strip()
        producer = metadata.get('Producer', '').strip()
        title   = metadata.get('Title', '').strip()

        logger.info("[A-5 TypeAnalyzer] 判定用キーフィールド:")
        logger.info(f"  ├─ Creator: '{creator}'")
        logger.info(f"  ├─ Producer: '{producer}'")
        logger.info(f"  └─ Title: '{title}'")

        # メタデータ判定を実行（照合詳細も取得）
        meta_type, meta_confidence, meta_reason, meta_match_detail = self._classify_document(creator, producer, title)

        # ページ別フォント解析（常時実行）
        # meta_type を渡してフォールスルー種別を最初から正しく設定する
        page_type_map, page_confidence_map, page_font_detail = self._analyze_pages(
            file_path, meta_type=meta_type, meta_confidence=meta_confidence
        )
        type_groups = self._group_pages_by_type(page_type_map)

        # ページ解析でより正確な判定が得られれば上書き
        doc_type, confidence, reason = self._determine_final_type(
            meta_type, meta_confidence, meta_reason, type_groups
        )

        low_conf_count = sum(1 for c in page_confidence_map.values() if c == 'LOW')
        logger.info(f"[A-5 TypeAnalyzer] 最終判定結果: {doc_type} (信頼度: {confidence})")
        logger.info(f"[A-5 TypeAnalyzer] 判定理由: {reason}")
        logger.info(f"[A-5 TypeAnalyzer] ページ種別: { {t: len(p) for t, p in type_groups.items()} }")
        logger.info(
            f"[A-5 TypeAnalyzer] ページ別信頼度: HIGH={len(page_confidence_map) - low_conf_count}, "
            f"LOW={low_conf_count} (LOW → Stage B スキップ, Stage E OCR)"
        )

        return {
            'document_type': doc_type,
            'raw_metadata': metadata,
            'confidence': confidence,
            'reason': reason,
            'page_type_map': page_type_map,              # {page_idx: type_str}
            'type_groups': type_groups,                  # {type_str: [page_idx, ...]}
            'page_confidence_map': page_confidence_map,  # {page_idx: 'HIGH'|'LOW'}
            'meta_match_detail': meta_match_detail,      # Creator/Producerの照合過程
            'page_font_detail': {                        # ページ別フォント詳細
                str(k): v for k, v in page_font_detail.items()
            },
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
            logger.info(f"[A-5 TypeAnalyzer] pdfplumberでメタデータ取得を試行...")
            with pdfplumber.open(str(file_path)) as pdf:
                metadata = pdf.metadata or {}
                logger.info(f"[A-5 TypeAnalyzer] pdfplumberでメタデータ取得成功: {len(metadata)}項目")
                return metadata
        except Exception as e:
            logger.warning(f"[A-5 TypeAnalyzer] pdfplumber取得失敗: {e}")

        # PyMuPDFで取得を試行
        try:
            import fitz
            logger.info(f"[A-5 TypeAnalyzer] PyMuPDFでメタデータ取得を試行...")
            doc = fitz.open(str(file_path))
            metadata = doc.metadata or {}
            doc.close()
            logger.info(f"[A-5 TypeAnalyzer] PyMuPDFでメタデータ取得成功: {len(metadata)}項目")
            return metadata
        except Exception as e:
            logger.warning(f"[A-5 TypeAnalyzer] PyMuPDF取得失敗: {e}", exc_info=True)

        logger.error("[A-5 TypeAnalyzer] すべてのメタデータ取得方法が失敗しました")
        return {}

    def _classify_document(
        self,
        creator: str,
        producer: str,
        title: str = '',
    ) -> tuple[str, str, str, dict]:
        """
        Creator / Producer / Title拡張子 を照合して書類種類を判定。

        優先順位:
          1. Creator 照合（HIGH）       ← 誰が作ったか。これが判定の主軸。
          2. Producer 照合（HIGH）      ← Creator が空の場合のみ参照。
          2.5. Title拡張子 照合（HIGH） ← Producer だけでは確定できない場合。
                                          Windows「Print to PDF」の元ファイル拡張子を利用。
          3. 全て不一致 → UNKNOWN LOW

        Creator で判定できた場合は Producer を見ない。
        """
        creator_lower = creator.lower()
        producer_lower = producer.lower()

        def _match(text: str, patterns: list) -> str | None:
            for p in patterns:
                if re.search(p, text, re.IGNORECASE):
                    return p
            return None

        # ── Step 1: Creator 照合 ──────────────────────────
        logger.info("[A-5 TypeAnalyzer] Creator照合:")
        logger.info(f"  ├─ Creator: '{creator}'")
        creator_results = []
        for app_name, patterns in self.CREATOR_PATTERNS.items():
            hit = _match(creator_lower, patterns)
            creator_results.append({'app': app_name, 'matched': hit is not None, 'matched_pattern': hit})
            if hit:
                logger.info(f"  ✓ Creator → {app_name} ('{hit}')")
                return app_name, 'HIGH', f'Creator一致: {hit}', {
                    'creator': creator, 'producer': producer,
                    'decided_by': 'creator',
                    'creator_results': creator_results,
                    'producer_results': [],
                }
        logger.info("  ✗ Creator: 不一致")

        # ── Step 2: Producer 照合（Creator 不明時のみ） ───
        logger.info("[A-5 TypeAnalyzer] Producer照合（補助）:")
        logger.info(f"  ├─ Producer: '{producer}'")
        producer_results = []
        for app_name, patterns in self.PRODUCER_PATTERNS.items():
            hit = _match(producer_lower, patterns)
            producer_results.append({'app': app_name, 'matched': hit is not None, 'matched_pattern': hit})
            if hit:
                logger.info(f"  ✓ Producer → {app_name} ('{hit}')")
                return app_name, 'HIGH', f'Producer一致: {hit}', {
                    'creator': creator, 'producer': producer,
                    'decided_by': 'producer',
                    'creator_results': creator_results,
                    'producer_results': producer_results,
                }
        logger.info("  ✗ Producer: 不一致")

        # ── Step 2.5: Title拡張子 照合（Producer単独では確定できない場合） ──
        if title:
            title_lower = title.lower()
            logger.info("[A-5 TypeAnalyzer] Title拡張子照合（補助）:")
            logger.info(f"  ├─ Title: '{title}'")
            for rule in self.TITLE_EXT_PATTERNS:
                prod_pat = rule.get('producer_pattern', '')
                if not re.search(prod_pat, producer_lower, re.IGNORECASE):
                    continue
                ext_map = rule.get('extension_map', {})
                for ext, app_name in ext_map.items():
                    if title_lower.endswith(ext):
                        logger.info(f"  ✓ Title拡張子 → {app_name} (ext='{ext}')")
                        return app_name, 'HIGH', f'Title拡張子一致: {ext} (Producer={producer})', {
                            'creator': creator, 'producer': producer,
                            'decided_by': 'title_extension',
                            'creator_results': creator_results,
                            'producer_results': producer_results,
                        }
            logger.info("  ✗ Title拡張子: 不一致")

        # ── Step 3: 判定不能 ──────────────────────────────
        match_detail = {
            'creator': creator, 'producer': producer,
            'decided_by': None,
            'creator_results': creator_results,
            'producer_results': producer_results,
        }
        if not creator and not producer:
            logger.warning("  ✗ Creator/Producer ともに空 → UNKNOWN")
            return 'UNKNOWN', 'LOW', 'Creator/Producer が空', match_detail

        logger.warning(f"  ✗ 判定不能: Creator='{creator}', Producer='{producer}' → UNKNOWN")
        return 'UNKNOWN', 'LOW', f'判定不能 (Creator={creator}, Producer={producer})', match_detail

    # ------------------------------------------------------------------
    # ページ別解析
    # ------------------------------------------------------------------

    def _analyze_pages(
        self, file_path: Path, meta_type: str, meta_confidence: str
    ) -> tuple[Dict[int, str], Dict[int, str], Dict[int, dict]]:
        """
        全ページのフォントを解析してページ別タイプと信頼度を返す

        Returns:
            page_type_map:       {page_idx(0始まり): type_str}
                                 type_str: REPORT / WORD / DTP / COVER / UNKNOWN
            page_confidence_map: {page_idx(0始まり): confidence_str}
                                 confidence_str: HIGH / LOW
                                 - HIGH: フォントパターン一致 or COVER確定
                                 - LOW:  フォントパターン不一致（UNKNOWN）→ Stage E OCR へ
            page_font_detail:    {page_idx: {
                                     'char_count': int,
                                     'fonts': [str, ...],        # 検出された全フォント名（実値）
                                     'matched_type': str,
                                     'matched_font': str|None,   # 一致したフォント名
                                     'matched_pattern': str|None # 一致したパターン
                                 }}
        """
        page_type_map: Dict[int, str] = {}
        page_confidence_map: Dict[int, str] = {}
        page_font_detail: Dict[int, dict] = {}
        try:
            import pdfplumber
            # SCAN/REPORT/DTP 以外のフォールスルー種別を決定
            # Creator確定(HIGH)かつWORD系以外 → そのmeta_typeを使う
            # それ以外 → 'WORD'（テキスト選択可なら最も可能性が高い）
            _word_family = frozenset({'WORD', 'WORD_LTSC', 'WORD_2019'})
            if meta_confidence == 'HIGH' and meta_type not in _word_family and meta_type in self._AUTHORITATIVE_META_TYPES:
                fallback_type = meta_type  # Creator確定（ILLUSTRATOR, INDESIGN 等）
            elif meta_type in _word_family:
                fallback_type = meta_type  # Creator確定=WORD/WORD_LTSC/WORD_2019
            else:
                fallback_type = 'UNKNOWN'  # Creator不明 → 根拠なし

            with pdfplumber.open(str(file_path)) as pdf:
                for i, page in enumerate(pdf.pages):
                    ptype, conf, matched_font, matched_pattern, detected_fonts = self._classify_page(page, fallback_type)
                    page_type_map[i] = ptype
                    page_confidence_map[i] = conf
                    page_font_detail[i] = {
                        'fonts': sorted(detected_fonts),
                        'matched_type': ptype,
                        'matched_font': matched_font,
                        'matched_pattern': matched_pattern,
                    }
                    # 根拠を明示ログ出力
                    if matched_font:
                        logger.debug(
                            f"[A-5 TypeAnalyzer] Page {i}: type={ptype}, confidence={conf}"
                            f" ← font='{matched_font}' matched pattern='{matched_pattern}'"
                        )
                    else:
                        logger.debug(
                            f"[A-5 TypeAnalyzer] Page {i}: type={ptype}, confidence={conf}"
                            f" ← fonts={sorted(detected_fonts)} / {matched_pattern or 'no font pattern matched'}"
                        )
        except Exception as e:
            logger.warning(f"[A-5 TypeAnalyzer] ページ別解析失敗: {e}")
        return page_type_map, page_confidence_map, page_font_detail

    def _classify_page(self, page, fallback_type: str) -> tuple[str, str, object, object, set]:
        """
        1ページのフォントから種別と信頼度を判定

        Returns:
            (type_str, confidence_str, matched_font, matched_pattern, detected_fonts)
            - HIGH: 種別を確定的に判断できた（フォントパターン一致 or 表紙確定）
            - LOW:  判断できなかった（UNKNOWN）→ Stage B スキップ、Stage E OCR
            - detected_fonts: このページで検出された全フォント名（subset prefix除去後）
        """
        chars = page.chars or []
        char_count = len(chars)
        images = page.images or []
        image_count = len(images)

        # テキスト選択不可（chars=0）+ 画像あり → スキャンページ
        # ※ chars が少ないからスキャンではない。選択可能テキストが皆無であることが根拠。
        if char_count == 0 and image_count > 0:
            return 'SCAN', 'HIGH', None, f'chars=0, images={image_count} → SCAN', set()

        # chars=0, images=0 → 完全空白ページ。WORD として渡しても何も抽出されないだけ。
        # 特別な分類名は不要。

        # フォント名収集（subset prefix を除去）
        fonts: set[str] = set()
        for c in chars:
            fn = c.get('fontname', '')
            if '+' in fn:
                fn = fn.split('+', 1)[1]
            fonts.add(fn.lower())

        # 固有フォントが明確に種別を指す場合のみフォント名を根拠にする
        # REPORT: WINJr帳票専用フォント（wing系）→ 確実
        for font in fonts:
            for pattern in self.PAGE_FONT_REPORT:
                if re.search(pattern, font, re.IGNORECASE):
                    return 'REPORT', 'HIGH', font, pattern, fonts

        # DTP: Illustrator/InDesign専用フォント（kozmin/kozgo等）→ 確実
        for font in fonts:
            for pattern in self.PAGE_FONT_DTP:
                if re.search(pattern, font, re.IGNORECASE):
                    return 'DTP', 'HIGH', font, pattern, fonts

        # SCAN/REPORT/DTP いずれにも該当しない → Creator確定種別を使う
        return fallback_type, 'HIGH', None, f'selectable text → {fallback_type} (fonts={sorted(fonts)})', fonts

    def _group_pages_by_type(self, page_type_map: Dict[int, str]) -> Dict[str, List[int]]:
        """page_type_map を type → [page_idx, ...] に変換"""
        groups: Dict[str, List[int]] = {}
        for idx, ptype in page_type_map.items():
            groups.setdefault(ptype, []).append(idx)
        # ページ番号順にソート
        for pages in groups.values():
            pages.sort()
        return groups

    # Creator/Producer メタデータで確定した権威ある種別
    # これらはページフォント解析による上書きを行わない
    # ただし SCANページ混在は例外（MIXED に格上げ）
    _AUTHORITATIVE_META_TYPES = frozenset({
        'GOODNOTES', 'GOOGLE_DOCS', 'GOOGLE_SHEETS',
        'WORD', 'WORD_LTSC', 'WORD_2019', 'EXCEL', 'ILLUSTRATOR', 'INDESIGN',
        'POWERPOINT', 'ACROBAT_PDFMAKER',
        'CANVA', 'ACROBAT', 'IOS_QUARTZ', 'STUDYAID',
    })

    def _determine_final_type(
        self,
        meta_type: str,
        meta_confidence: str,
        meta_reason: str,
        type_groups: Dict[str, List[int]]
    ) -> tuple[str, str, str]:
        """
        メタデータ判定 + ページ別解析を統合して最終種別を決定

        優先順位:
        1. Creator/Producer が権威ある種別を確定（ILLUSTRATOR, INDESIGN, WORD 等）
           → ページフォント解析は上書きしない
           （例: Creator=Adobe Illustrator なら KozMin を検出しても DTP に変えない）
           ★ ただし SCANページ（テキスト選択不可）が混在する場合は MIXED に格上げ
        2. メタデータが不確定（UNKNOWN/SCAN）→ ページフォント解析で補完
        3. ページ種別が複数 → MIXED
        """
        content_types = {t: p for t, p in type_groups.items()
                         if t != 'UNKNOWN'}

        if not content_types:
            # コンテンツページが検出されなかった → メタデータ判定をそのまま使用
            return meta_type, meta_confidence, meta_reason

        # Creator/Producer で確定した権威ある種別はページフォントで上書きしない
        # ★ ただし SCAN/REPORT ページ混在は例外 → MIXED
        #    SCAN:   テキスト選択不可 → B80が必要（未実装のため今は BLOCK になる）
        #    REPORT: WINGフォント確定 → B42が必要。Creator種別と異なるプロセッサが必須
        if meta_type in self._AUTHORITATIVE_META_TYPES and meta_confidence == 'HIGH':
            _FORCE_MIXED = frozenset({'SCAN', 'REPORT'})
            force_mixed = {t for t in content_types if t in _FORCE_MIXED and t != meta_type}
            if force_mixed:
                forced = sorted(force_mixed)
                type_counts = ', '.join(f'{t}={len(content_types[t])}p' for t in forced)
                total = sum(len(p) for p in content_types.values())
                reason = (
                    f'Creator確定({meta_type}) + {"/".join(forced)}ページ混在 → MIXED '
                    f'({type_counts} / 全{total}p)'
                )
                logger.info(f"[A-5 TypeAnalyzer] Creator確定だが{'/'.join(forced)}混在 → MIXED: {reason}")
                return 'MIXED', 'HIGH', reason
            logger.info(
                f"[A-5 TypeAnalyzer] メタデータ優先: {meta_type}"
                f" （ページフォント解析={list(content_types.keys())} による上書きなし）"
            )
            return meta_type, meta_confidence, meta_reason

        # メタデータが不確定（SCAN等）→ ページフォント解析で補完
        if len(content_types) == 1:
            # コンテンツ種別が1種類 → そのタイプを HIGH で確定
            ptype = list(content_types.keys())[0]
            page_list = list(content_types.values())[0]
            reason = f'ページフォント解析: {ptype} ({len(page_list)}ページ)'
            return ptype, 'HIGH', reason

        # 複数種別 → MIXED（B1 が type_groups を見て複数プロセッサを走らせる）
        summary = ', '.join(f'{t}:{len(p)}p' for t, p in sorted(content_types.items()))
        reason = f'ページ混在: {summary}'
        logger.info(f"[A-5 TypeAnalyzer] 混在文書検出: {reason}")
        return 'MIXED', 'HIGH', reason
