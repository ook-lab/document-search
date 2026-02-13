"""
D-10: Image Slicer（画像分割）

D-8/D-9で特定した表領域を個別の画像として切り出し、
非表領域（地文のみ）の画像を生成する。

目的:
1. 表領域を個別の画像（patch）として切り出す
2. 非表領域画像を生成（表を白塗りマスク）
3. 座標メタデータを保持してStage Eに渡す
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger
import numpy as np

try:
    import cv2
    from PIL import Image
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("[D-10] OpenCV/PIL がインストールされていません")


class D10ImageSlicer:
    """D-10: Image Slicer（画像分割）"""

    def __init__(
        self,
        full_page_threshold: float = 0.95,  # ページ面積の95%以上は全面table扱い
        min_interior_hlines: int = 3        # 全面tableを許可する最小内側水平線数
    ):
        """
        Image Slicer 初期化

        Args:
            full_page_threshold: ページ面積の何%以上を全面tableとみなすか
            min_interior_hlines: 全面tableを許可する最小内側水平線数
        """
        self.full_page_threshold = full_page_threshold
        self.min_interior_hlines = min_interior_hlines

        if not CV2_AVAILABLE:
            logger.error("[D-10] OpenCV/PIL が必要です")

    def slice(
        self,
        image_path: Path,
        grid_result: Dict[str, Any],
        cell_result: Dict[str, Any],
        output_dir: Path
    ) -> Dict[str, Any]:
        """
        画像を表領域と非表領域に分割

        Args:
            image_path: 元の画像パス（B-90生成）
            grid_result: D-8の格子解析結果
            cell_result: D-9のセル特定結果
            output_dir: 出力ディレクトリ

        Returns:
            {
                'page_index': 0,
                'tables': [
                    {
                        'table_id': 'T1',
                        'bbox': [x0, y0, x1, y1],
                        'image_path': 'path/to/table_T1.png',
                        'cell_map': [...]
                    }
                ],
                'non_table_image_path': 'path/to/background_only.png',
                'metadata': { 'original_size': [W, H] }
            }
        """
        if not CV2_AVAILABLE:
            logger.error("[D-10] OpenCV/PIL が利用できません")
            return self._empty_result()

        logger.info(f"[D-10] 画像分割開始: {image_path.name}")

        try:
            # 画像を読み込み（日本語パス対応）
            import numpy as np
            with open(image_path, 'rb') as f:
                image_bytes = np.frombuffer(f.read(), np.uint8)
                image = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR)

            if image is None:
                logger.error(f"[D-10] 画像読み込み失敗: {image_path}")
                return self._empty_result()

            height, width = image.shape[:2]
            logger.info(f"[D-10] 画像サイズ: {width}x{height}")

            # 出力ディレクトリ作成
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            # 表領域をフィルタリング（全面表の誤検出を除外）
            raw_table_regions = grid_result.get('table_regions', [])
            unified_lines = grid_result.get('unified_lines', {})
            table_regions = self._filter_table_regions(
                raw_table_regions,
                unified_lines,
                width,
                height
            )

            # 表領域を切り出し
            tables = []

            for table_region in table_regions:
                table_id = table_region['table_id']
                bbox = table_region['bbox']

                # 正規化座標をピクセル座標に変換
                x0 = int(bbox[0] * width)
                y0 = int(bbox[1] * height)
                x1 = int(bbox[2] * width)
                y1 = int(bbox[3] * height)

                # 表画像を切り出し
                table_image = image[y0:y1, x0:x1]

                # 保存
                table_image_path = output_dir / f"d10_table_{table_id}.png"
                cv2.imwrite(str(table_image_path), table_image)

                logger.info(f"[D-10] 表画像保存: {table_image_path.name}")

                tables.append({
                    'table_id': table_id,
                    'bbox': bbox,
                    'image_path': str(table_image_path),
                    'cell_map': cell_result.get('cells', [])
                })

            # 非表領域画像を生成（表を白塗り）
            non_table_image = image.copy()

            for table_region in table_regions:
                bbox = table_region['bbox']

                # 正規化座標をピクセル座標に変換
                x0 = int(bbox[0] * width)
                y0 = int(bbox[1] * height)
                x1 = int(bbox[2] * width)
                y1 = int(bbox[3] * height)

                # 白塗り
                non_table_image[y0:y1, x0:x1] = 255

            # 保存
            non_table_image_path = output_dir / "d10_background.png"
            cv2.imwrite(str(non_table_image_path), non_table_image)

            logger.info(f"[D-10] 非表画像保存: {non_table_image_path.name}")

            logger.info("[D-10] 画像分割完了")
            logger.info(f"  ├─ 表画像: {len(tables)}枚")
            logger.info(f"  └─ 非表画像: 1枚")

            return {
                'page_index': 0,
                'tables': tables,
                'non_table_image_path': str(non_table_image_path),
                'metadata': {
                    'original_size': [width, height]
                }
            }

        except Exception as e:
            logger.error(f"[D-10] 分割エラー: {e}", exc_info=True)
            return self._empty_result()

    def _filter_table_regions(
        self,
        table_regions: List[Dict[str, Any]],
        unified_lines: Dict[str, List],
        page_width: int,
        page_height: int
    ) -> List[Dict[str, Any]]:
        """
        表領域をフィルタリング（全面表の誤検出を除外）

        Args:
            table_regions: D-8の表領域リスト
            unified_lines: D-8の統合罫線情報
            page_width: ページ幅（px）
            page_height: ページ高さ（px）

        Returns:
            フィルタリング後の表領域リスト
        """
        if not table_regions:
            return []

        filtered = []
        horizontal_lines = unified_lines.get('horizontal', [])

        for region in table_regions:
            table_id = region['table_id']
            bbox = region['bbox']  # 正規化座標 [x0, y0, x1, y1]

            # bbox のサイズを計算
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]

            # 1) ページ全面 table チェック
            is_full_page = (w >= self.full_page_threshold and h >= self.full_page_threshold)

            if not is_full_page:
                # 全面tableでないならOK
                filtered.append(region)
                continue

            # 2) 全面 table の場合、内側水平線をチェック
            # ページ上端/下端から5%以上離れた水平線を「内側」とみなす
            interior_hlines = []
            for line in horizontal_lines:
                y = line.get('y0', 0)  # 正規化座標
                if 0.05 < y < 0.95:  # 上下5%を除外
                    interior_hlines.append(line)

            interior_hline_count = len(interior_hlines)

            # 3) 判定
            if interior_hline_count >= self.min_interior_hlines:
                # 内側水平線が十分あるので、真の全面tableと判断
                logger.info(f"[D-10] 全面table許可: {table_id} (w={w:.2f}, h={h:.2f}, interior_hlines={interior_hline_count})")
                filtered.append(region)
            else:
                # 内側水平線が不足 → 誤検出として reject
                logger.warning(f"[D-10] reject table {table_id} reason=full_page_bbox (w={w:.2f}, h={h:.2f}, interior_hlines={interior_hline_count})")

        logger.info(f"[D-10] 表領域フィルタ: {len(table_regions)} → {len(filtered)}")
        return filtered

    def _empty_result(self) -> Dict[str, Any]:
        """空の結果を返す"""
        return {
            'page_index': 0,
            'tables': [],
            'non_table_image_path': '',
            'metadata': {}
        }
