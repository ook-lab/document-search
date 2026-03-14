"""
トクバイチラシスクレイピングモジュール

トクバイのウェブサイトからチラシ情報を取得する。
"""
import re
import time
from typing import List, Dict, Any, Optional
from loguru import logger
import requests
from bs4 import BeautifulSoup
from io import BytesIO
from PIL import Image


class TokubaiScraper:
    """トクバイスクレイピングクラス"""

    def __init__(self, store_url: str):
        """
        Args:
            store_url: 店舗のトクバイURL
                例: https://tokubai.co.jp/フーディアム/7978
        """
        self.store_url = store_url
        self.base_url = "https://tokubai.co.jp"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

    def fetch_store_page(self) -> Optional[str]:
        """
        店舗ページのHTMLを取得

        Returns:
            HTMLコンテンツ、失敗時はNone
        """
        try:
            logger.info(f"店舗ページを取得中: {self.store_url}")
            response = requests.get(self.store_url, headers=self.headers, timeout=30)
            response.raise_for_status()
            logger.info(f"ステータスコード: {response.status_code}")
            return response.text
        except Exception as e:
            logger.error(f"店舗ページの取得に失敗: {e}")
            return None

    def extract_flyer_links(self, html_content: str) -> List[Dict[str, str]]:
        """
        HTMLからチラシのリンク情報を抽出

        Args:
            html_content: 店舗ページのHTML

        Returns:
            チラシ情報のリスト
            [{'title': 'タイトル', 'url': '/チラシURL', 'flyer_id': 'xxx', 'period': '期間'}, ...]
        """
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            flyer_links = []

            # チラシへのリンクを抽出（aタグのhref="/店舗名/数字/leaflets/数字"のパターン）
            for link in soup.find_all('a', href=True):
                href = link['href']
                # チラシページのURLパターンをマッチ（例: /フーディアム/7978/leaflets/96703723）
                match = re.match(r'^/[^/]+/\d+/leaflets/(\d+)$', href)
                if match:
                    flyer_id = match.group(1)

                    # タイトルを取得（リンク内のテキストまたは画像のalt）
                    title = link.get_text(strip=True)
                    if not title:
                        img = link.find('img')
                        if img and img.get('alt'):
                            title = img['alt']

                    if not title:
                        title = f"チラシ_{flyer_id}"

                    # 期間情報を取得（親要素から探す）
                    period = ""
                    parent = link.find_parent()
                    if parent:
                        period_elem = parent.find(text=re.compile(r'\d{4}[./]\d{1,2}[./]\d{1,2}'))
                        if period_elem:
                            period = period_elem.strip()

                    flyer_info = {
                        'title': title,
                        'url': href,
                        'flyer_id': flyer_id,
                        'period': period
                    }

                    flyer_links.append(flyer_info)
                    logger.debug(f"チラシ発見: {title} ({flyer_id})")

            # 重複を除去（flyer_idでユニーク化）
            unique_flyers = {}
            for flyer in flyer_links:
                flyer_id = flyer['flyer_id']
                if flyer_id not in unique_flyers:
                    unique_flyers[flyer_id] = flyer

            result = list(unique_flyers.values())
            logger.info(f"チラシリンクを{len(result)}件抽出しました")
            return result

        except Exception as e:
            logger.error(f"チラシリンク抽出エラー: {e}", exc_info=True)
            return []

    def fetch_flyer_page(self, flyer_url: str) -> Optional[str]:
        """
        チラシ詳細ページのHTMLを取得

        Args:
            flyer_url: チラシの相対URL（例: /フーディアム/7978/1234567）

        Returns:
            HTMLコンテンツ、失敗時はNone
        """
        try:
            full_url = f"{self.base_url}{flyer_url}"
            logger.info(f"チラシページを取得中: {full_url}")
            response = requests.get(full_url, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.text
        except Exception as e:
            logger.error(f"チラシページの取得に失敗: {e}")
            return None

    def extract_image_urls(self, html_content: str, flyer_id: str) -> List[Dict[str, Any]]:
        """
        チラシページから画像URLを抽出

        Args:
            html_content: チラシページのHTML
            flyer_id: チラシID

        Returns:
            画像情報のリスト
            [{'url': '画像URL', 'page': ページ番号, 'flyer_id': 'xxx'}, ...]
        """
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            images = []

            # 画像を抽出（imgタグのsrcまたはdata-src属性）
            img_tags = soup.find_all('img')
            page_num = 1

            for img in img_tags:
                # src または data-src から画像URLを取得
                img_url = img.get('data-src') or img.get('src')

                if not img_url:
                    continue

                # チラシ画像のパターンにマッチするか確認
                # 例: https://cdn-ak.f.st-hatena.com/images/fotolife/...
                # または相対パスの場合は絶対パスに変換
                if img_url.startswith('//'):
                    img_url = 'https:' + img_url
                elif img_url.startswith('/'):
                    img_url = self.base_url + img_url

                # 画像URLが有効かチェック（最小限のフィルタ）
                if not img_url.startswith('http'):
                    continue

                # アイコンやロゴ、サムネイルを除外（URLのキーワードでフィルタ）
                if any(keyword in img_url.lower() for keyword in ['logo', 'icon', 'banner', 'ad', 'thumb', 'thumbnail']):
                    continue

                # URLに小さいサイズの寸法が含まれている場合は除外
                # 例: "100x100", "150x150", "200x200" など
                size_pattern = re.search(r'(\d+)x(\d+)', img_url)
                if size_pattern:
                    width = int(size_pattern.group(1))
                    height = int(size_pattern.group(2))
                    if width < 400 or height < 400:
                        logger.debug(f"URLにサムネイルサイズが含まれているため除外: {width}x{height} ({img_url})")
                        continue

                images.append({
                    'url': img_url,
                    'page': page_num,
                    'flyer_id': flyer_id
                })

                page_num += 1

            logger.info(f"チラシ画像を{len(images)}件抽出しました (flyer_id: {flyer_id})")
            return images

        except Exception as e:
            logger.error(f"画像URL抽出エラー: {e}", exc_info=True)
            return []

    def download_image(self, image_url: str, min_width: int = 400, min_height: int = 400) -> Optional[bytes]:
        """
        画像をダウンロード（サムネイルサイズは除外）

        Args:
            image_url: 画像のURL
            min_width: 最小幅（ピクセル）。これ以下はサムネイルとして除外
            min_height: 最小高さ（ピクセル）。これ以下はサムネイルとして除外

        Returns:
            画像データ（バイト列）、失敗時はNone
        """
        try:
            logger.debug(f"画像ダウンロード中: {image_url}")
            response = requests.get(image_url, headers=self.headers, timeout=30)
            response.raise_for_status()

            # Content-Typeをチェック
            content_type = response.headers.get('Content-Type', '')
            if not content_type.startswith('image/'):
                logger.warning(f"画像ではないコンテンツ: {content_type}")
                return None

            image_data = response.content

            # 画像サイズをチェック（サムネイルを除外）
            try:
                img = Image.open(BytesIO(image_data))
                width, height = img.size
                logger.debug(f"画像サイズ: {width}x{height}")

                if width < min_width or height < min_height:
                    logger.info(f"サムネイルサイズのため除外: {width}x{height} (URL: {image_url})")
                    return None

            except Exception as img_error:
                logger.warning(f"画像サイズチェックエラー: {img_error}")
                # サイズチェックに失敗した場合は、画像データをそのまま返す

            logger.debug(f"画像ダウンロード成功 ({len(image_data)} bytes, {width}x{height})")
            return image_data

        except Exception as e:
            logger.error(f"画像ダウンロードエラー: {e}")
            return None

    def get_all_flyers(self) -> List[Dict[str, Any]]:
        """
        店舗の全チラシ情報を取得（リンクのみ、画像は未ダウンロード）

        Returns:
            チラシ情報のリスト
        """
        html_content = self.fetch_store_page()
        if not html_content:
            return []

        flyer_links = self.extract_flyer_links(html_content)
        return flyer_links

    def get_flyer_images(self, flyer_info: Dict[str, str]) -> List[Dict[str, Any]]:
        """
        特定のチラシの画像情報を取得

        Args:
            flyer_info: get_all_flyers()で取得したチラシ情報

        Returns:
            画像情報のリスト
        """
        flyer_url = flyer_info['url']
        flyer_id = flyer_info['flyer_id']

        html_content = self.fetch_flyer_page(flyer_url)
        if not html_content:
            return []

        # ページ間で負荷をかけないよう少し待機
        time.sleep(1)

        images = self.extract_image_urls(html_content, flyer_id)
        return images
