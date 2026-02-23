"""
A-3: Entry Point（処理開始）

Stage A のオーケストレーター。
A-5（書類種類判断）と A-6（サイズ測定）を実行し、
結果を統合して返す。
"""

from pathlib import Path
from typing import Dict, Any
from loguru import logger

from .a5_type_analyzer import A5TypeAnalyzer
from .a6_dimension_measurer import A6DimensionMeasurer
from .a5_gatekeeper import A5Gatekeeper


class A3EntryPoint:
    """A-3: Entry Point（処理開始）"""

    def __init__(self):
        """Stage A オーケストレーター初期化"""
        self.type_analyzer = A5TypeAnalyzer()
        self.dimension_measurer = A6DimensionMeasurer()
        self.gatekeeper = A5Gatekeeper()

    def process(self, file_path: str | Path) -> Dict[str, Any]:
        """
        Stage A 処理を実行

        Args:
            file_path: PDFファイルパス

        Returns:
            {
                'document_type': str,  # WORD, INDESIGN, EXCEL, SCAN
                'page_count': int,
                'dimensions': {
                    'width': float,
                    'height': float,
                    'unit': 'pt'
                },
                'dimensions_mm': {
                    'width': float,
                    'height': float,
                    'unit': 'mm'
                },
                'is_multi_size': bool,
                'raw_metadata': dict,
                'confidence': str,
                'reason': str
            }
        """
        file_path = Path(file_path)

        logger.info("=" * 60)
        logger.info("[Stage A] 入口処理開始（書類の判断）")
        logger.info(f"  ├─ ファイル: {file_path.name}")
        logger.info("=" * 60)

        # ファイル存在確認
        if not file_path.exists():
            logger.error(f"[Stage A] ファイルが存在しません: {file_path}")
            return self._error_result(f"File not found: {file_path}")

        if file_path.suffix.lower() != '.pdf':
            logger.error(f"[Stage A] PDFファイルではありません: {file_path}")
            return self._error_result(f"Not a PDF file: {file_path}")

        try:
            # A-2: 作成ソフト判定（TypeAnalyzer）
            logger.info("=" * 60)
            logger.info("[Stage A → A-2] 作成ソフト判定（TypeAnalyzer）開始")
            logger.info("=" * 60)
            type_result = self.type_analyzer.analyze(file_path)
            logger.info("=" * 60)
            logger.info("[Stage A → A-2] 作成ソフト判定完了")
            logger.info(f"  ├─ 判定: {type_result['document_type']}")
            logger.info(f"  ├─ 信頼度: {type_result['confidence']}")
            logger.info(f"  └─ 理由: {type_result['reason']}")
            logger.info("=" * 60)

            # A-3: サイズ・ページ測定（DimensionMeasurer）
            logger.info("")
            logger.info("=" * 60)
            logger.info("[Stage A → A-3] サイズ・ページ測定（DimensionMeasurer）開始")
            logger.info("=" * 60)
            dimension_result = self.dimension_measurer.measure(file_path)
            logger.info("=" * 60)
            logger.info("[Stage A → A-3] サイズ・ページ測定完了")
            logger.info(f"  ├─ ページ数: {dimension_result['page_count']}")
            logger.info(f"  ├─ サイズ: {dimension_result['dimensions']['width']:.2f} x {dimension_result['dimensions']['height']:.2f} pt")
            logger.info(f"  ├─ サイズ: {dimension_result['dimensions_mm']['width']:.2f} x {dimension_result['dimensions_mm']['height']:.2f} mm")
            logger.info(f"  └─ マルチサイズ: {'はい' if dimension_result['is_multi_size'] else 'いいえ'}")
            logger.info("=" * 60)

            # A-4: レイアウト特性判定（LayoutProfiler）
            logger.info("")
            logger.info("=" * 60)
            logger.info("[Stage A → A-4] レイアウト特性判定（LayoutProfiler）開始")
            logger.info("=" * 60)
            layout_result = self._analyze_layout_profile(file_path)
            logger.info("=" * 60)
            logger.info("[Stage A → A-4] レイアウト特性判定完了")
            logger.info(f"  ├─ レイアウト: {layout_result['layout_profile']}")
            logger.info(f"  ├─ 平均画像数: {layout_result['metrics'].get('avg_images_per_page', 0):.1f}/page")
            logger.info(f"  ├─ 平均単語数: {layout_result['metrics'].get('avg_words_per_page', 0):.1f}/page")
            logger.info(f"  ├─ X座標分散: {layout_result['metrics'].get('avg_x_std', 0):.1f}")
            logger.info(f"  └─ 行あたり単語数: {layout_result['metrics'].get('avg_words_per_line', 0):.2f}/line")
            logger.info("=" * 60)

            # 結果を統合（A番号順に格納 + 互換キー維持）
            result = {
                'success': True,

                # A-2: 作成ソフト判定（正本）
                'a2_type': {
                    'origin_app': type_result['document_type'],
                    'confidence': type_result['confidence'],
                    'reason': type_result['reason'],
                    'raw_metadata': type_result.get('raw_metadata', {})
                },

                # A-3: サイズ・ページ測定（正本）
                'a3_dimension': {
                    'page_count': dimension_result['page_count'],
                    'dimensions': dimension_result['dimensions'],
                    'dimensions_mm': dimension_result['dimensions_mm'],
                    'is_multi_size': dimension_result['is_multi_size'],
                },

                # A-4: レイアウト特性（正本）
                'a4_layout': {
                    'layout_profile': layout_result['layout_profile'],
                    'layout_metrics': layout_result['metrics'],
                },

                # 互換キー（既存利用者のため残す）
                'origin_app': type_result['document_type'],
                'document_type': type_result['document_type'],
                'confidence': type_result['confidence'],
                'reason': type_result['reason'],
                'raw_metadata': type_result.get('raw_metadata', {}),

                # ページ別型解析（B1 の MIXED マルチプロセッサ処理に使用）
                'page_type_map': type_result.get('page_type_map', {}),
                'type_groups': type_result.get('type_groups', {}),
                # ページ別信頼度（HIGH: Stage B, LOW: Stage B スキップ → Stage E OCR）
                'page_confidence_map': type_result.get('page_confidence_map', {}),

                'page_count': dimension_result['page_count'],
                'dimensions': dimension_result['dimensions'],
                'dimensions_mm': dimension_result['dimensions_mm'],
                'is_multi_size': dimension_result['is_multi_size'],

                'layout_profile': layout_result['layout_profile'],
                'layout_metrics': layout_result['metrics'],
            }

            # A-5: Gatekeeper（通行許可）
            logger.info("")
            logger.info("=" * 60)
            logger.info("[Stage A → A-5] Gatekeeper（通行許可）開始")
            logger.info("=" * 60)
            a5_gatekeeper = self.gatekeeper.evaluate(file_path, result)
            logger.info("=" * 60)
            logger.info("[Stage A → A-5] Gatekeeper判定完了")
            logger.info(f"  ├─ 判定: {a5_gatekeeper.get('decision')}")
            if a5_gatekeeper.get("decision") == "ALLOW":
                logger.info(f"  ├─ 許可プロセッサ: {a5_gatekeeper.get('allowed_processors')}")
                logger.info(f"  └─ 理由: {a5_gatekeeper.get('block_reason')}")
            else:
                logger.info(f"  ├─ ブロックコード: {a5_gatekeeper.get('block_code')}")
                logger.info(f"  └─ ブロック理由: {a5_gatekeeper.get('block_reason')}")
            logger.info("=" * 60)

            # 正本として積む（B1 はこれを強制）
            result["a5_gatekeeper"] = a5_gatekeeper
            # 移行期互換（B1 は a5_gatekeeper 優先で見る）
            result["gatekeeper"] = a5_gatekeeper

            logger.info("=" * 60)
            logger.info("[Stage A完了] 入口処理結果:")
            logger.info(f"  ├─ 作成ソフト: {result['origin_app']} (信頼度: {result['confidence']})")
            logger.info(f"  ├─ レイアウト: {result['layout_profile']}")
            logger.info(f"  ├─ 判定理由: {result['reason']}")
            logger.info(f"  ├─ 計測値: images={layout_result['metrics'].get('avg_images_per_page', 0):.0f}/page, "
                       f"words={layout_result['metrics'].get('avg_words_per_page', 0):.0f}/page")
            logger.info(f"  ├─ ページ数: {result['page_count']}")
            logger.info(f"  ├─ サイズ: {result['dimensions']['width']:.2f} x {result['dimensions']['height']:.2f} pt")
            logger.info(f"  │          ({result['dimensions_mm']['width']:.2f} x {result['dimensions_mm']['height']:.2f} mm)")
            logger.info(f"  ├─ マルチサイズ: {'はい' if result['is_multi_size'] else 'いいえ'}")
            if a5_gatekeeper.get("decision") == "ALLOW":
                logger.info(f"  └─ A-5 Gatekeeper: ALLOW allowed_processors={a5_gatekeeper.get('allowed_processors')}")
            else:
                logger.warning(
                    f"  └─ A-5 Gatekeeper: BLOCK code={a5_gatekeeper.get('block_code')} "
                    f"reason={a5_gatekeeper.get('block_reason')}"
                )
            logger.info("=" * 60)

            return result

        except Exception as e:
            logger.error(f"[Stage A エラー] 処理失敗: {e}", exc_info=True)
            return self._error_result(str(e))

    def _analyze_layout_profile(self, file_path: Path) -> Dict[str, Any]:
        """
        レイアウト特性を判定（FLOW / FIXED / HYBRID）

        判定ルール:
        - images_per_page >= 50 → FIXED
        - x_std が大きい、words_per_line が低い → FIXED
        - それ以外 → FLOW
        """
        try:
            import pdfplumber
            import statistics
        except ImportError:
            logger.warning("[A-4] pdfplumber が利用できません → layout_profile=FLOW（デフォルト）")
            return {
                'layout_profile': 'FLOW',
                'metrics': {}
            }

        # PyMuPDF でページごとの詳細情報を事前取得（フォント・画像詳細）
        fitz_pages = {}
        try:
            import fitz
            fitz_doc = fitz.open(str(file_path))
            for i, fpage in enumerate(fitz_doc):
                # フォント情報: (xref, ext, type, basefont, name, encoding, referencer)
                raw_fonts = fpage.get_fonts(full=True)
                fonts = []
                for f in raw_fonts:
                    name = f[3] or f[4] or ''   # basefont or name
                    ftype = f[2]                 # Type1 / TrueType / CIDFont等
                    encoding = f[5] or ''
                    # 埋め込み有無: xref > 0 かつ Type1/TrueType/CIDFont
                    embedded = (f[0] > 0)
                    # ToUnicode: PyMuPDF単体では直接取れないがencodingで推測
                    has_toUnicode = bool(encoding and encoding not in ('StandardEncoding', 'MacRomanEncoding', ''))
                    fonts.append({
                        'name': name,
                        'type': ftype,
                        'encoding': encoding,
                        'embedded': embedded,
                        'has_toUnicode': has_toUnicode,
                    })

                # 画像情報: (xref, smask, width, height, bpc, colorspace, ...)
                raw_images = fpage.get_images(full=True)
                images_detail = []
                for img in raw_images:
                    images_detail.append({
                        'width': img[2],
                        'height': img[3],
                        'bpc': img[4],           # bits per component
                        'colorspace': img[5],    # DeviceRGB / DeviceGray / DeviceCMYK等
                        'filter': img[8] or '',  # DCTDecode(JPEG) / FlateDecode等
                    })

                fitz_pages[i] = {
                    'fonts': fonts,
                    'images_detail': images_detail,
                }
            fitz_doc.close()
        except Exception as e:
            logger.warning(f"[A-4] PyMuPDF 詳細情報取得失敗（スキップ）: {e}")

        try:
            with pdfplumber.open(str(file_path)) as pdf:
                page_metrics = []

                for page_num, page in enumerate(pdf.pages):
                    # 画像オブジェクト数
                    images = page.images if hasattr(page, 'images') else []
                    image_count = len(images)

                    # 単語数
                    words = page.extract_words()
                    word_count = len(words)

                    # 文字数
                    chars = page.chars
                    char_count = len(chars)

                    # テキスト選択可否
                    has_selectable_text = char_count > 0

                    # X座標の分散（文字の横方向ばらつき）
                    if chars:
                        x_coords = [char['x0'] for char in chars]
                        x_std = statistics.stdev(x_coords) if len(x_coords) > 1 else 0
                    else:
                        x_std = 0

                    # Y座標でグループ化（行数推定）
                    if chars:
                        y_coords = sorted(set([round(char['top'], 1) for char in chars]))
                        line_count = len(y_coords)
                        words_per_line = word_count / line_count if line_count > 0 else 0
                    else:
                        line_count = 0
                        words_per_line = 0

                    # ベクター線数（罫線・枠・図形）
                    vector_count = (
                        len(getattr(page, 'lines', []) or []) +
                        len(getattr(page, 'rects', []) or []) +
                        len(getattr(page, 'curves', []) or [])
                    )

                    # PyMuPDF から取得した詳細情報をマージ
                    fitz_info = fitz_pages.get(page_num, {})

                    page_metrics.append({
                        'page': page_num,
                        # テキスト系
                        'images': image_count,
                        'words': word_count,
                        'chars': char_count,
                        'has_selectable_text': has_selectable_text,
                        'x_std': x_std,
                        'lines': line_count,
                        'words_per_line': words_per_line,
                        # ベクター
                        'vector_count': vector_count,
                        # フォント詳細（PyMuPDF）
                        'fonts': fitz_info.get('fonts', []),
                        # 画像詳細（PyMuPDF）
                        'images_detail': fitz_info.get('images_detail', []),
                    })

                # 全ページ平均
                avg_images = sum(m['images'] for m in page_metrics) / len(page_metrics) if page_metrics else 0
                avg_words = sum(m['words'] for m in page_metrics) / len(page_metrics) if page_metrics else 0
                avg_x_std = sum(m['x_std'] for m in page_metrics) / len(page_metrics) if page_metrics else 0
                avg_words_per_line = sum(m['words_per_line'] for m in page_metrics) / len(page_metrics) if page_metrics else 0

                # ページ単位の詳細ログ（最初の3ページまたは全ページ）
                logger.info("[A-4] ページ別計測値:")
                sample_pages = page_metrics[:3] if len(page_metrics) > 3 else page_metrics
                for m in sample_pages:
                    logger.info(
                        f"  ├─ Page {m['page']}: "
                        f"images={m['images']}, words={m['words']}, chars={m['chars']}, "
                        f"x_std={m['x_std']:.1f}, lines={m['lines']}, words/line={m['words_per_line']:.2f}"
                    )
                if len(page_metrics) > 3:
                    logger.info(f"  └─ ... ({len(page_metrics) - 3} ページ省略)")

                logger.info("[A-4] 平均計測値:")
                logger.info(f"  ├─ avg_images_per_page: {avg_images:.1f}")
                logger.info(f"  ├─ avg_words_per_page: {avg_words:.1f}")
                logger.info(f"  ├─ avg_x_std: {avg_x_std:.1f}")
                logger.info(f"  └─ avg_words_per_line: {avg_words_per_line:.2f}")

                # 判定ルール
                layout_profile = 'FLOW'  # デフォルト
                reason = []

                logger.info("[A-4] 判定ルール適用:")

                # ルール1: 画像が多い → FIXED
                if avg_images >= 50:
                    layout_profile = 'FIXED'
                    reason.append(f"images/page={avg_images:.0f}>=50")
                    logger.info(f"  ✓ ルール1適用: 画像が多い ({avg_images:.0f}>=50) → FIXED")

                # ルール2: X座標の分散が大きい（文字が左右に散る）→ FIXED
                elif avg_x_std >= 100:
                    layout_profile = 'FIXED'
                    reason.append(f"x_std={avg_x_std:.0f}>=100")
                    logger.info(f"  ✓ ルール2適用: X座標分散が大きい ({avg_x_std:.0f}>=100) → FIXED")

                # ルール3: 行あたりの単語数が少ない（文章として流れていない）→ FIXED
                elif 0 < avg_words_per_line < 5:
                    layout_profile = 'FIXED'
                    reason.append(f"words/line={avg_words_per_line:.1f}<5")
                    logger.info(f"  ✓ ルール3適用: 行あたり単語数が少ない ({avg_words_per_line:.1f}<5) → FIXED")

                # それ以外は FLOW
                else:
                    reason.append("文章流し込み型と判定")
                    logger.info(f"  ✓ デフォルト: 文章流し込み型 → FLOW")
                    logger.info(f"      (images/page={avg_images:.0f}<50, x_std={avg_x_std:.0f}<100, words/line={avg_words_per_line:.2f}>=5)")

                logger.info(f"[A-4] 最終判定: layout_profile={layout_profile} ({', '.join(reason)})")

                return {
                    'layout_profile': layout_profile,
                    'metrics': {
                        'avg_images_per_page': avg_images,
                        'avg_words_per_page': avg_words,
                        'avg_x_std': avg_x_std,
                        'avg_words_per_line': avg_words_per_line,
                        'per_page': page_metrics
                    }
                }

        except Exception as e:
            logger.error(f"[A-4] layout_profile 判定エラー: {e}", exc_info=True)
            return {
                'layout_profile': 'FLOW',  # エラー時はデフォルト
                'metrics': {}
            }

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'success': False,
            'error': error_message,
            'origin_app': 'UNKNOWN',
            'document_type': 'UNKNOWN',
            'layout_profile': 'FLOW',
            'layout_metrics': {},
            'page_count': 0,
            'dimensions': {'width': 0, 'height': 0, 'unit': 'pt'},
            'dimensions_mm': {'width': 0, 'height': 0, 'unit': 'mm'},
            'is_multi_size': False,
            'raw_metadata': {},
            'confidence': 'NONE',
            'reason': 'エラーにより判定不能'
        }
