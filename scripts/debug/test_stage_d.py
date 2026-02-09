"""
Stage D テストスクリプト（視覚構造解析）

使用例:
    # PDFのみ（ベクトル罫線のみ）
    python scripts/debug/test_stage_d.py "path/to/file.pdf"

    # PDF + 画像（ベクトル + ラスター罫線）
    python scripts/debug/test_stage_d.py "path/to/file.pdf" --image "path/to/purged_image.png"
"""

import sys
import json
import argparse
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from shared.pipeline.stage_d import D1Controller


def main():
    """Stage D テスト実行"""
    parser = argparse.ArgumentParser(description='Stage D 視覚構造解析テスト')
    parser.add_argument('pdf_path', type=str, help='PDFファイルパス')
    parser.add_argument('--image', type=str, help='テキスト消去済み画像パス（オプション）')
    parser.add_argument('--page', type=int, default=0, help='ページ番号（0始まり）')
    parser.add_argument('--output', type=str, help='出力ディレクトリ（オプション）')

    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        logger.error(f"ファイルが存在しません: {pdf_path}")
        sys.exit(1)

    # 画像パス
    image_path = None
    if args.image:
        image_path = Path(args.image)
        if not image_path.exists():
            logger.warning(f"画像ファイルが存在しません: {image_path}")
            image_path = None

    # 出力ディレクトリ
    output_dir = None
    if args.output:
        output_dir = Path(args.output)

    logger.info("=" * 60)
    logger.info("Stage D 視覚構造解析テスト開始")
    logger.info("=" * 60)

    # Stage D 実行
    controller = D1Controller()
    result = controller.process(
        pdf_path=pdf_path,
        purged_image_path=image_path,
        page_num=args.page,
        output_dir=output_dir
    )

    # 結果を表示
    logger.info("\n[Stage D] 実行結果:")
    logger.info(f"  成功: {result.get('success')}")

    if result.get('success'):
        logger.info(f"  ページ: {result['page_index'] + 1}")
        logger.info(f"  表領域数: {len(result['tables'])}")

        # 表領域の詳細
        for table in result['tables']:
            logger.info(f"\n  表 {table['table_id']}:")
            logger.info(f"    ├─ Bbox: {table['bbox']}")
            logger.info(f"    ├─ 画像: {table['image_path']}")
            logger.info(f"    └─ セル数: {len(table['cell_map'])}")

            # セルのサンプル表示（最初の10個）
            cell_map = table['cell_map']
            if cell_map:
                logger.info(f"\n    セルサンプル（最初の10個）:")
                for i, cell in enumerate(cell_map[:10]):
                    logger.info(f"      [{i+1}] {cell['cell_id']}: {cell['bbox']}")

        # 非表画像
        if result['non_table_image_path']:
            logger.info(f"\n  非表画像: {result['non_table_image_path']}")

        # デバッグ情報
        debug = result.get('debug', {})
        if debug:
            logger.info("\n  デバッグ情報:")

            vector = debug.get('vector_lines', {})
            if vector:
                logger.info(f"    ├─ ベクトル罫線:")
                logger.info(f"    │   ├─ 水平線: {len(vector.get('horizontal_lines', []))}本")
                logger.info(f"    │   └─ 垂直線: {len(vector.get('vertical_lines', []))}本")

            raster = debug.get('raster_lines')
            if raster:
                logger.info(f"    ├─ ラスター罫線:")
                logger.info(f"    │   ├─ 水平線: {len(raster.get('horizontal_lines', []))}本")
                logger.info(f"    │   └─ 垂直線: {len(raster.get('vertical_lines', []))}本")

            grid = debug.get('grid_result', {})
            if grid:
                logger.info(f"    ├─ 格子解析:")
                logger.info(f"    │   ├─ 交点: {len(grid.get('intersections', []))}個")
                logger.info(f"    │   └─ 表領域: {len(grid.get('table_regions', []))}個")

            cell = debug.get('cell_result', {})
            if cell:
                grid_info = cell.get('grid_info', {})
                logger.info(f"    └─ セル特定:")
                logger.info(f"        ├─ グリッド: {grid_info.get('rows', 0)}行 x {grid_info.get('cols', 0)}列")
                logger.info(f"        └─ セル数: {len(cell.get('cells', []))}個")

        # JSON形式で保存
        output_path = pdf_path.parent / f"{pdf_path.stem}_stage_d_result.json"

        # デバッグ情報は大きすぎるので除外
        save_result = {
            'success': result['success'],
            'page_index': result['page_index'],
            'tables': result['tables'],
            'non_table_image_path': result['non_table_image_path'],
            'metadata': result['metadata']
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(save_result, f, ensure_ascii=False, indent=2)
        logger.info(f"\n  結果を保存: {output_path}")

    else:
        logger.error(f"  エラー: {result.get('error')}")

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
