"""
B27 Gmail HTML メールプロセッサ

BeautifulSoup で HTML を DOM 順に走査し、テキストと画像のスロットリストを構築する。
トラッキングピクセルをフィルタリングし、リンク画像を PNG として保存する。
"""
import logging
import re
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 1メールあたりの最大画像枚数（DM大量画像対策）
MAX_IMAGES_PER_EMAIL = 20

# トラッキングURLに含まれるキーワード
TRACKING_URL_KEYWORDS = ('track', 'pixel', 'beacon', '1x1', 'spacer', 'open.gif')

# 最小ファイルサイズ（bytes）
MIN_FILE_SIZE_BYTES = 1024

# 最小画像サイズ（px）
MIN_IMAGE_SIZE_PX = 10


class B27GmailHTMLProcessor:
    """Gmail HTML メール専用プロセッサ（B27）"""

    def process(self, raw_doc: dict, temp_dir: Optional[Path] = None) -> dict:
        """
        HTML メールを DOM 順に走査してスロットリストを構築する。

        Args:
            raw_doc: 01_gmail_01_raw のレコード
            temp_dir: 画像保存先ディレクトリ（None の場合は tempfile で作成）

        Returns:
            {
                'email_type': 'html',
                'slots': [
                    {'type': 'text',  'text': '...'},
                    {'type': 'image', 'path': '/tmp/.../img_0.png', 'slot_idx': 0},
                ],
                'image_paths': ['/tmp/.../img_0.png', ...],
                'plain_text': str,
                'processor_name': 'B27_GMAIL_HTML',
            }
        """
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("[B27] beautifulsoup4 がインストールされていません")
            return self._fallback_result(raw_doc)

        body_html = raw_doc.get('body_html') or ''
        if not body_html.strip():
            logger.info("[B27] body_html が空: B26 フォールバック")
            return self._fallback_result(raw_doc)

        logger.info(f"[B27] HTML メール処理開始: {len(body_html)}文字")

        if temp_dir is None:
            temp_dir = Path(tempfile.mkdtemp(prefix='b27_'))
        else:
            temp_dir = Path(temp_dir)
            temp_dir.mkdir(parents=True, exist_ok=True)

        soup = BeautifulSoup(body_html, 'html.parser')

        # <style> / <script> を除去
        for tag in soup.find_all(['style', 'script', 'head']):
            tag.decompose()

        slots = []
        image_paths = []
        slot_idx = 0
        image_count = 0

        # DOM 順に走査
        for element in soup.body.descendants if soup.body else soup.descendants:
            from bs4 import NavigableString, Tag
            if isinstance(element, NavigableString):
                text = element.strip()
                if text:
                    # 親が img/script/style なら無視
                    parent_name = element.parent.name if element.parent else ''
                    if parent_name in ('img', 'script', 'style'):
                        continue
                    slots.append({'type': 'text', 'text': text})

            elif isinstance(element, Tag) and element.name == 'img':
                if image_count >= MAX_IMAGES_PER_EMAIL:
                    logger.info(f"[B27] 最大画像数 {MAX_IMAGES_PER_EMAIL} 枚に達した: 残り画像はスキップ")
                    continue

                src = element.get('src', '')
                if not src:
                    continue

                if self._is_tracking_pixel(element, src):
                    logger.debug(f"[B27] トラッキングピクセルをスキップ: {src[:80]}")
                    continue

                img_path = self._download_image(src, temp_dir, slot_idx)
                if img_path is None:
                    continue

                slots.append({
                    'type': 'image',
                    'path': str(img_path),
                    'slot_idx': slot_idx,
                })
                image_paths.append(str(img_path))
                image_count += 1
                slot_idx += 1

        # plain_text: 画像なし時のフォールバック用テキスト
        plain_text = self._extract_plain_text(soup)

        logger.info(f"[B27] 完了: {len(slots)} スロット, {len(image_paths)} 画像")

        return {
            'email_type': 'html',
            'slots': slots,
            'image_paths': image_paths,
            'plain_text': plain_text,
            'processor_name': 'B27_GMAIL_HTML',
        }

    def _is_tracking_pixel(self, element, src: str) -> bool:
        """トラッキングピクセル判定"""
        # base64 埋め込み
        if src.startswith('data:'):
            return True

        # 1x1 サイズ属性
        width = element.get('width', '')
        height = element.get('height', '')
        try:
            if int(width) <= 1 or int(height) <= 1:
                return True
        except (ValueError, TypeError):
            pass

        # URL キーワード
        src_lower = src.lower()
        for kw in TRACKING_URL_KEYWORDS:
            if kw in src_lower:
                return True

        return False

    def _download_image(self, src: str, temp_dir: Path, slot_idx: int) -> Optional[Path]:
        """
        画像をダウンロードして PNG として保存する。
        失敗または小さすぎる場合は None を返す。
        """
        try:
            import requests
        except ImportError:
            logger.error("[B27] requests がインストールされていません")
            return None

        try:
            from PIL import Image
        except ImportError:
            logger.error("[B27] Pillow がインストールされていません")
            return None

        try:
            headers = {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                )
            }
            resp = requests.get(src, timeout=10, headers=headers, stream=True)
            if resp.status_code != 200:
                logger.debug(f"[B27] 画像DL失敗 HTTP {resp.status_code}: {src[:80]}")
                return None

            raw_bytes = resp.content
            if len(raw_bytes) < MIN_FILE_SIZE_BYTES:
                logger.debug(f"[B27] 画像が小さすぎる ({len(raw_bytes)} bytes < {MIN_FILE_SIZE_BYTES}): スキップ")
                return None

        except Exception as e:
            logger.debug(f"[B27] 画像DL例外: {e} - {src[:80]}")
            return None

        # Pillow でサイズ確認
        try:
            import io
            img = Image.open(io.BytesIO(raw_bytes))
            w, h = img.size
            if w <= MIN_IMAGE_SIZE_PX or h <= MIN_IMAGE_SIZE_PX:
                logger.debug(f"[B27] 画像が小さすぎる ({w}x{h}px <= {MIN_IMAGE_SIZE_PX}px): スキップ")
                return None

            # PNG として保存
            out_path = temp_dir / f'img_{slot_idx}.png'
            img.save(str(out_path), 'PNG')
            logger.info(f"[B27] 画像保存: {out_path.name} ({w}x{h}px)")
            return out_path

        except Exception as e:
            logger.debug(f"[B27] 画像処理例外: {e}")
            return None

    def _extract_plain_text(self, soup) -> str:
        """BeautifulSoup から平文テキストを抽出（フォールバック用）"""
        lines = []
        for text in soup.stripped_strings:
            lines.append(text)
        return '\n'.join(lines)

    def _fallback_result(self, raw_doc: dict) -> dict:
        """body_plain または空文字列を返すフォールバック"""
        plain_text = raw_doc.get('body_plain') or ''
        return {
            'email_type': 'html',
            'slots': [{'type': 'text', 'text': plain_text}] if plain_text else [],
            'image_paths': [],
            'plain_text': plain_text,
            'processor_name': 'B27_GMAIL_HTML',
        }
