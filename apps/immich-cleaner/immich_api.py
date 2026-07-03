import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()

class ImmichAPI:
    def __init__(self, url=None, api_key=None):
        self.url = url or os.getenv("IMMICH_URL", "http://192.168.12.31:3001")
        self.api_key = api_key or os.getenv("IMMICH_API_KEY")
        
        # URLの末尾のスラッシュを除去
        if self.url.endswith("/"):
            self.url = self.url[:-1]
            
        self.headers = {
            "x-api-key": self.api_key,
            "Accept": "application/json"
        }

    def test_connection(self):
        """接続確認"""
        endpoints = ["/api/users/me", "/api/server/about"]
        for ep in endpoints:
            try:
                res = requests.get(f"{self.url}{ep}", headers=self.headers, timeout=10)
                if res.status_code == 200:
                    return True, res.json()
            except Exception as e:
                print(f"Endpoint {ep} failed: {e}")
        return False, None

    def fetch_all_asset_summaries(self, progress_callback=None):
        """ページネーションを使って全アセット（通常＆アーカイブ）の概要をフェッチ"""
        assets = []
        page = 1
        has_more = True
        take = 500  # 大容量対応のため、1リクエストあたりの件数を500に設定
        
        while has_more:
            if progress_callback:
                progress_callback(f"Fetching asset list (Batch {page})...")
                
            payload = {
                "take": take,
                "page": page,
                "withArchived": True  # アーカイブされた写真も漏らさず取得する
            }
            try:
                res = requests.post(f"{self.url}/api/search/metadata", json=payload, headers=self.headers, timeout=20)
                if res.status_code != 200:
                    print(f"Failed to fetch metadata search page {page}: {res.status_code}")
                    break
                    
                res_data = res.json()
                page_assets = []
                
                if isinstance(res_data, dict) and "assets" in res_data:
                    assets_part = res_data["assets"]
                    if isinstance(assets_part, dict):
                        page_assets = assets_part.get("items", [])
                        next_page = assets_part.get("nextPage", None)
                        has_more = next_page is not None and next_page != "" and next_page != page
                    elif isinstance(assets_part, list):
                        page_assets = assets_part
                        has_more = False
                else:
                    break
                    
                if not page_assets:
                    break
                    
                assets.extend(page_assets)
                page += 1
                
            except Exception as e:
                print(f"Error fetching metadata page {page}: {e}")
                break
                
        return assets

    def fetch_asset_details_parallel(self, asset_summaries, max_workers=30, progress_callback=None):
        """マルチスレッドでアセット詳細 (exifInfo) を並列フェッチ"""
        details = []
        total = len(asset_summaries)
        
        def fetch_detail(asset):
            asset_id = asset.get("id")
            url = f"{self.url}/api/assets/{asset_id}"
            try:
                res = requests.get(url, headers=self.headers, timeout=5)
                if res.status_code == 200:
                    return res.json()
            except Exception:
                pass
            return None

        if progress_callback:
            progress_callback(f"Starting parallel details fetch for {total} assets...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_detail, a): a for a in asset_summaries}
            completed = 0
            for future in as_completed(futures):
                completed += 1
                if completed % 100 == 0 or completed == total:
                    if progress_callback:
                        progress_callback(f"Fetched details: {completed}/{total} assets...")
                
                res_data = future.result()
                if res_data:
                    details.append(res_data)
                    
        return details

    def download_original(self, asset_id, save_path):
        """アセットのオリジナルファイルをダウンロードして保存"""
        url = f"{self.url}/api/assets/{asset_id}/original"
        try:
            res = requests.get(url, headers=self.headers, stream=True, timeout=60)
            if res.status_code == 200:
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                with open(save_path, "wb") as f:
                    for chunk in res.iter_content(chunk_size=8192):
                        f.write(chunk)
                return True
        except Exception as e:
            print(f"Error downloading asset {asset_id}: {e}")
        return False

    def delete_asset(self, asset_id):
        """アセットを削除 (ゴミ箱へ移動)"""
        url = f"{self.url}/api/assets"
        payload = {
            "ids": [asset_id]
        }
        try:
            # DELETE /api/assets はアセットをゴミ箱へ送る
            res = requests.delete(url, json=payload, headers=self.headers, timeout=10)
            if res.status_code == 204 or res.status_code == 200:
                return True
        except Exception as e:
            print(f"Error deleting asset {asset_id}: {e}")
        return False
