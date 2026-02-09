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

    def __init__(self):
        """Image Slicer 初期化"""
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
            # 画像を読み込み
            image = cv2.imread(str(image_path))
            if image is None:
                logger.error(f"[D-10] 画像読み込み失敗: {image_path}")
                return self._empty_result()

            height, width = image.shape[:2]
            logger.info(f"[D-10] 画像サイズ: {width}x{height}")

            # 出力ディレクトリ作成
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            # 表領域を切り出し
            table_regions = grid_result.get('table_regions', [])
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
                table_image_path = output_dir / f"table_{table_id}.png"
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
            non_table_image_path = output_dir / "background_only.png"
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

    def _empty_result(self) -> Dict[str, Any]:
        """空の結果を返す"""
        return {
            'page_index': 0,
            'tables': [],
            'non_table_image_path': '',
            'metadata': {}
        }
