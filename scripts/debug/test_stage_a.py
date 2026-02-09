"""
Stage A テストスクリプト

使用例:
    python scripts/debug/test_stage_a.py "path/to/file.pdf"
"""

import sys
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from shared.pipeline.stage_a import A3EntryPoint


def main():
    """Stage A テスト実行"""
    if len(sys.argv) < 2:
        logger.error("使用方法: python test_stage_a.py <pdf_path>")
        sys.exit(1)

    pdf_path = sys.argv[1]

    # Stage A 実行
    entry_point = A3EntryPoint()
    result = entry_point.process(pdf_path)

    # 結果を表示
    logger.info("=" * 60)
    logger.info("Stage A 実行結果:")
    logger.info("=" * 60)

    if result.get('success'):
        logger.info(f"✓ 成功")
        logger.info(f"  書類種類: {result['document_type']}")
        logger.info(f"  信頼度: {result['confidence']}")
        logger.info(f"  判定理由: {result['reason']}")
        logger.info(f"  ページ数: {result['page_count']}")
        logger.info(f"  サイズ: {result['dimensions']['width']:.2f} x {result['dimensions']['height']:.2f} pt")
        logger.info(f"          ({result['dimensions_mm']['width']:.2f} x {result['dimensions_mm']['height']:.2f} mm)")
        logger.info(f"  マルチサイズ: {'はい' if result['is_multi_size'] else 'いいえ'}")
    else:
        logger.error(f"✗ 失敗: {result.get('error')}")

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
