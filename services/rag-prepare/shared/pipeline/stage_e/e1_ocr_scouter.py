"""
E-1: OCR Scouter（文字数測定）

軽量OCRエンジンを使用して画像内の文字数を測定し、
後続処理のルーティング判定に使用する。

目的:
1. 文字密度を測定（高密度 vs 低密度）
2. 処理スキップ判定（ノイズのみの画像を除外）
3. APIコスト最適化のための事前スカウティング
"""

from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger
import numpy as np

try:
    import pytesseract
    from PIL import Image
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    logger.warning("[E-1] pytesseract/PIL がインストールされていません")


class E1OcrScouter:
    """E-1: OCR Scouter（文字数測定）"""

    def __init__(
        self,
        low_density_threshold: int = 100,   # 低密度判定の閾値（文字数）
        high_density_threshold: int = 500,  # 高密度判定の閾値（文字数）
        min_char_threshold: int = 1         # 最小文字数（0文字のみスキップ）
    ):
        """
        OCR Scouter 初期化

        Args:
            low_density_threshold: 低密度判定の閾値
            high_density_threshold: 高密度判定の閾値
            min_char_threshold: 最小文字数（これ以下は処理スキップ）
        """
        self.low_density_threshold = low_density_threshold
        self.high_density_threshold = high_density_threshold
        self.min_char_threshold = min_char_threshold

        if not TESSERACT_AVAILABLE:
            logger.error("[E-1] pytesseract/PIL が必要です")

    def scout(
        self,
        image_path: Path,
        lang: str = 'jpn+eng',
        include_words: bool = False
    ) -> Dict[str, Any]:
        """
        画像内の文字数を測定

        Args:
            image_path: 画像ファイルパス
            lang: OCR言語設定
            include_words: True の場合、単語ごとの座標も返す

        Returns:
            {
                'char_count': int,           # 推定文字数
                'density_level': str,        # 'none', 'low', 'medium', 'high'
                'should_skip': bool,         # 処理スキップすべきか
                'extracted_text': str,       # 抽出されたテキスト
                'confidence': float,         # OCR信頼度（0.0-1.0）
                'words': [dict]              # 単語ごとの座標（include_words=True時）
            }
        """
        if not TESSERACT_AVAILABLE:
            logger.error("[E-1] pytesseract/PIL が利用できません")
            return self._empty_result()

        logger.info(f"[E-1] 文字数測定開始: {image_path.name}")

        try:
            # 画像を読み込み
            image = Image.open(str(image_path))

            # include_words=True の場合は image_to_data を使用
            if include_words:
                data = pytesseract.image_to_data(image, lang=lang, output_type=pytesseract.Output.DICT)

                # 単語データを抽出（空文字を除外）
                words = []
                for i in range(len(data['text'])):
                    text = data['text'][i].strip()
                    if text:  # 空文字を除外
                        words.append({
                            'text': text,
                            'bbox': [
                                data['left'][i],
                                data['top'][i],
                                data['left'][i] + data['width'][i],
                                data['top'][i] + data['height'][i]
                            ],
                            'conf': data['conf'][i]
                        })

                # 全テキストを結合
                text = ' '.join([w['text'] for w in words])

            else:
                # 従来の image_to_string を使用（高速）
                text = pytesseract.image_to_string(image, lang=lang)
                words = []

            # 文字数をカウント（空白・改行を除く）
            char_count = len(text.replace(' ', '').replace('\n', '').replace('\t', ''))

            # 密度レベルを判定
            density_level = self._classify_density(char_count)

            # スキップ判定
            should_skip = char_count < self.min_char_threshold

            # 判定根拠をログ出力
            logger.info(f"[E-1] 文字数測定詳細:")
            logger.info(f"  ├─ 文字数: {char_count} 文字")
            logger.info(f"  ├─ 閾値設定: 最小={self.min_char_threshold}, 低密度={self.low_density_threshold}, 高密度={self.high_density_threshold}")

            # 密度判定の根拠
            if char_count < self.min_char_threshold:
                logger.info(f"  ├─ 密度判定: 'none' (文字数 {char_count} < 最小閾値 {self.min_char_threshold})")
            elif char_count < self.low_density_threshold:
                logger.info(f"  ├─ 密度判定: 'low' (文字数 {char_count} < 低密度閾値 {self.low_density_threshold})")
            elif char_count < self.high_density_threshold:
                logger.info(f"  ├─ 密度判定: 'medium' (文字数 {char_count} < 高密度閾値 {self.high_density_threshold})")
            else:
                logger.info(f"  ├─ 密度判定: 'high' (文字数 {char_count} >= 高密度閾値 {self.high_density_threshold})")

            # should_skip の判定根拠
            if should_skip:
                logger.info(f"  └─ スキップ判定: YES (文字数 {char_count} < 最小閾値 {self.min_char_threshold} → 処理スキップ)")
            else:
                logger.info(f"  └─ スキップ判定: NO (文字数 {char_count} >= 最小閾値 {self.min_char_threshold} → 処理継続)")

            # 信頼度を簡易的に計算（文字数ベース）
            confidence = min(1.0, char_count / self.high_density_threshold)

            logger.info(f"[E-1] 測定完了:")
            logger.info(f"  ├─ 文字数: {char_count}")
            logger.info(f"  ├─ 密度: {density_level}")
            if include_words:
                logger.info(f"  ├─ 単語数: {len(words)}")
            logger.info(f"  └─ スキップ: {should_skip}")
            logger.info(f"[E-1] 抽出テキスト全文=『{text}』")

            result = {
                'char_count': char_count,
                'density_level': density_level,
                'should_skip': should_skip,
                'extracted_text': text,
                'confidence': confidence,
                # エイリアスキー（controller から density/skip でアクセス可能）
                'density': density_level,
                'skip': should_skip,
            }

            if include_words:
                result['words'] = words

            return result

        except Exception as e:
            logger.error(f"[E-1] 測定エラー: {e}", exc_info=True)
            return self._empty_result()

    def _classify_density(self, char_count: int) -> str:
        """
        文字数から密度レベルを分類

        Args:
            char_count: 文字数

        Returns:
            'none', 'low', 'medium', 'high'
        """
        if char_count < self.min_char_threshold:
            return 'none'
        elif char_count < self.low_density_threshold:
            return 'low'
        elif char_count < self.high_density_threshold:
            return 'medium'
        else:
            return 'high'

    def _empty_result(self) -> Dict[str, Any]:
        """空の結果を返す"""
        return {
            'char_count': 0,
            'density_level': 'none',
            'should_skip': True,
            'extracted_text': '',
            'confidence': 0.0,
            # エイリアスキー
            'density': 'none',
            'skip': True,
        }

    def scout_all_pages(
        self,
        purged_images_dir: Path,
        page_count: int,
        lang: str = 'jpn+eng'
    ) -> Dict[str, Any]:
        """
        全ページの文字数を測定（白紙ページも記録）

        Args:
            purged_images_dir: B3 等の purged_images ディレクトリ
            page_count: 総ページ数
            lang: OCR言語設定

        Returns:
            {
                'pages_total': int,
                'pages_processed': int,
                'per_page': [
                    {
                        'page': int,
                        'words_count': int,
                        'chars_count': int,
                        'status': str,  # 'ok', 'blank_skip', 'error'
                        'skip_reason': str
                    }
                ],
                'total_words': int,
                'total_chars': int
            }
        """
        if not TESSERACT_AVAILABLE:
            logger.error("[E-1] pytesseract/PIL が利用できません")
            return {
                'pages_total': page_count,
                'pages_processed': 0,
                'per_page': [],
                'total_words': 0,
                'total_chars': 0
            }

        logger.info(f"[E-1] 全ページスカウト開始: {page_count}ページ")

        per_page = []
        total_words = 0
        total_chars = 0

        for page_idx in range(page_count):
            page_img_path = purged_images_dir / f"purged_page_{page_idx}.png"

            if not page_img_path.exists():
                logger.warning(f"[E-1] page={page_idx} 画像なし")
                per_page.append({
                    'page': page_idx,
                    'words_count': 0,
                    'chars_count': 0,
                    'status': 'error',
                    'skip_reason': 'image_not_found'
                })
                continue

            try:
                # 画像を読み込み
                image = Image.open(str(page_img_path))

                # 白紙判定（画素の平均と分散）
                img_array = np.array(image.convert('L'))  # グレースケール化
                mean_val = np.mean(img_array)
                std_val = np.std(img_array)

                # 閾値: 平均が240以上 かつ 標準偏差が10以下 → 白紙
                is_blank = (mean_val > 240) and (std_val < 10)

                if is_blank:
                    logger.info(f"[E-1] page={page_idx} status=blank_skip words=0 chars=0 reason=blank_page")
                    per_page.append({
                        'page': page_idx,
                        'words_count': 0,
                        'chars_count': 0,
                        'status': 'blank_skip',
                        'skip_reason': 'blank_page'
                    })
                    continue

                # OCR で単語・文字数を測定
                data = pytesseract.image_to_data(image, lang=lang, output_type=pytesseract.Output.DICT)

                # 空文字を除外してカウント
                words = [w for w in data.get('text', []) if w.strip()]
                words_count = len(words)

                # 文字数（空白・改行を除く）
                chars_count = sum(len(w.replace(' ', '').replace('\n', '')) for w in words)

                # 抽出テキスト（ログ確認用）
                extracted_text = ' '.join(words)
                logger.info(f"[E-1] page={page_idx} status=ok words={words_count} chars={chars_count}")
                logger.info(f"[E-1] page={page_idx} text全文=『{extracted_text}』")

                per_page.append({
                    'page': page_idx,
                    'words_count': words_count,
                    'chars_count': chars_count,
                    'extracted_text': extracted_text,
                    'status': 'ok',
                    'skip_reason': ''
                })

                total_words += words_count
                total_chars += chars_count

            except Exception as e:
                logger.error(f"[E-1] page={page_idx} エラー: {e}", exc_info=True)
                per_page.append({
                    'page': page_idx,
                    'words_count': 0,
                    'chars_count': 0,
                    'status': 'error',
                    'skip_reason': str(e)
                })

        # 全ページ集計の詳細をログ出力
        ok_pages = sum(1 for p in per_page if p.get('status') == 'ok')
        blank_pages = sum(1 for p in per_page if p.get('status') == 'blank_skip')
        error_pages = sum(1 for p in per_page if p.get('status') == 'error')

        logger.info("=" * 80)
        logger.info("[E-1] 全ページスカウト集計:")
        logger.info("=" * 80)
        logger.info(f"  ├─ 総ページ数: {page_count}")
        logger.info(f"  ├─ 処理済み: {len(per_page)}")
        logger.info(f"  ├─ 正常: {ok_pages} ページ")
        logger.info(f"  ├─ 白紙スキップ: {blank_pages} ページ")
        logger.info(f"  ├─ エラー: {error_pages} ページ")
        logger.info(f"  ├─ 総単語数: {total_words}")
        logger.info(f"  ├─ 総文字数: {total_chars}")
        logger.info(f"  └─ 平均文字数/ページ: {total_chars // ok_pages if ok_pages > 0 else 0}")
        logger.info("=" * 80)

        logger.info(f"[E-1] 全ページスカウト完了: pages_total={page_count} pages_processed={len(per_page)} total_chars={total_chars}")

        return {
            'pages_total': page_count,
            'pages_processed': len(per_page),
            'per_page': per_page,
            'total_words': total_words,
            'total_chars': total_chars
        }
