"""
HTML to Screenshot Utility

PlaywrightでHTMLをスクリーンショット画像に変換（Async版）
"""
import base64
import re
import io
from pathlib import Path
from typing import Optional, Union
from playwright.async_api import async_playwright
from PIL import Image
from loguru import logger


class HTMLScreenshotGenerator:
    """HTMLをスクリーンショット画像に変換するクラス（Async対応）"""

    def __init__(self, viewport_width: int = 1200, viewport_height: int = 800):
        """
        初期化

        Args:
            viewport_width: ビューポート幅
            viewport_height: ビューポート高さ
        """
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height

    async def html_to_screenshot(
        self,
        html_content: str,
        output_path: Optional[Union[str, Path]] = None,
        full_page: bool = True
    ) -> bytes:
        """
        HTMLコンテンツをスクリーンショット画像に変換

        Args:
            html_content: HTML文字列
            output_path: 保存先パス（Noneの場合は保存しない）
            full_page: フルページスクリーンショット（Trueの場合）

        Returns:
            スクリーンショット画像のバイナリデータ（PNG形式）
        """
        try:
            async with async_playwright() as p:
                # ブラウザを起動（ヘッドレスモード）
                browser = await p.chromium.launch(headless=True)

                # ページを作成
                page = await browser.new_page(
                    viewport={'width': self.viewport_width, 'height': self.viewport_height}
                )

                # ⚠️ 巨大画像対策：Base64埋め込み画像を実際にリサイズ
                html_content = self._resize_embedded_images(html_content)

                # HTMLコンテンツを設定
                await page.set_content(html_content, wait_until='networkidle')

                # スクリーンショット撮影
                screenshot_bytes = await page.screenshot(
                    full_page=full_page,
                    type='png'
                )

                # ファイルに保存（オプション）
                if output_path:
                    output_path = Path(output_path)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(screenshot_bytes)
                    logger.info(f"スクリーンショット保存: {output_path}")

                await browser.close()
                return screenshot_bytes

        except Exception as e:
            logger.error(f"スクリーンショット生成エラー: {e}")
            raise

    def _resize_embedded_images(self, html_content: str, max_height: int = 800) -> str:
        """
        HTMLに埋め込まれたBase64画像を実際にリサイズ

        Args:
            html_content: HTML文字列
            max_height: 画像の最大高さ（px）

        Returns:
            リサイズ後の画像を含むHTML
        """
        def resize_base64_image(match):
            """Base64画像をリサイズして置き換え"""
            try:
                # data:image/png;base64,xxxxx の形式
                full_data_uri = match.group(0)
                mime_type = match.group(1)  # image/png など
                base64_data = match.group(2)

                # Base64デコード
                img_bytes = base64.b64decode(base64_data)
                img = Image.open(io.BytesIO(img_bytes))

                # リサイズが必要かチェック
                if img.height > max_height:
                    # アスペクト比を保ってリサイズ
                    ratio = max_height / img.height
                    new_width = int(img.width * ratio)
                    img_resized = img.resize((new_width, max_height), Image.Resampling.LANCZOS)

                    # Base64に再エンコード
                    buffer = io.BytesIO()
                    img_format = img.format or 'PNG'
                    img_resized.save(buffer, format=img_format)
                    resized_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

                    logger.debug(f"画像リサイズ: {img.width}x{img.height} → {new_width}x{max_height}")

                    return f'data:{mime_type};base64,{resized_base64}'
                else:
                    # リサイズ不要
                    return full_data_uri

            except Exception as e:
                logger.warning(f"Base64画像リサイズ失敗: {e}")
                return match.group(0)  # 元のまま返す

        # data:image/xxx;base64,xxxxx パターンを検索してリサイズ
        pattern = r'data:(image/[^;]+);base64,([A-Za-z0-9+/=]+)'
        resized_html = re.sub(pattern, resize_base64_image, html_content)

        return resized_html
