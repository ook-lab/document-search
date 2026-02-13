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
            logger.info("[Stage A] A-2: 作成ソフト判定（TypeAnalyzer）")
            type_result = self.type_analyzer.analyze(file_path)

            # A-3: サイズ・ページ測定（DimensionMeasurer）
            logger.info("[Stage A] A-3: サイズ・ページ測定（DimensionMeasurer）")
            dimension_result = self.dimension_measurer.measure(file_path)

            # A-4: レイアウト特性判定（LayoutProfiler）
            logger.info("[Stage A] A-4: レイアウト特性判定（LayoutProfiler）")
            layout_result = self._analyze_layout_profile(file_path)

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

                'page_count': dimension_result['page_count'],
                'dimensions': dimension_result['dimensions'],
                'dimensions_mm': dimension_result['dimensions_mm'],
                'is_multi_size': dimension_result['is_multi_size'],

                'layout_profile': layout_result['layout_profile'],
                'layout_metrics': layout_result['metrics'],
            }

            # A-5: Gatekeeper（通行許可）
            logger.info("[Stage A] A-5: Gatekeeper（通行許可）")
            a5_gatekeeper = self.gatekeeper.evaluate(file_path, result)

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

                    page_metrics.append({
                        'page': page_num,
                        'images': image_count,
                        'words': word_count,
                        'chars': char_count,
                        'x_std': x_std,
                        'lines': line_count,
                        'words_per_line': words_per_line
                    })

                # 全ページ平均
                avg_images = sum(m['images'] for m in page_metrics) / len(page_metrics) if page_metrics else 0
                avg_words = sum(m['words'] for m in page_metrics) / len(page_metrics) if page_metrics else 0
                avg_x_std = sum(m['x_std'] for m in page_metrics) / len(page_metrics) if page_metrics else 0
                avg_words_per_line = sum(m['words_per_line'] for m in page_metrics) / len(page_metrics) if page_metrics else 0

                # 判定ルール
                layout_profile = 'FLOW'  # デフォルト
                reason = []

                # ルール1: 画像が多い → FIXED
                if avg_images >= 50:
                    layout_profile = 'FIXED'
                    reason.append(f"images/page={avg_images:.0f}>=50")

                # ルール2: X座標の分散が大きい（文字が左右に散る）→ FIXED
                elif avg_x_std >= 100:
                    layout_profile = 'FIXED'
                    reason.append(f"x_std={avg_x_std:.0f}>=100")

                # ルール3: 行あたりの単語数が少ない（文章として流れていない）→ FIXED
                elif 0 < avg_words_per_line < 5:
                    layout_profile = 'FIXED'
                    reason.append(f"words/line={avg_words_per_line:.1f}<5")

                # それ以外は FLOW
                else:
                    reason.append("文章流し込み型と判定")

                logger.info(f"[A-4] layout_profile={layout_profile} ({', '.join(reason)})")

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
