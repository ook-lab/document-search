"""
Stage B-42 テストスクリプト（Multi-Column Report専用）

使用例:
    python scripts/debug/test_stage_b42.py "path/to/report.pdf"
"""

import sys
import json
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from shared.pipeline.stage_b import B42MultiColumnReportProcessor


def main():
    """Stage B-42 テスト実行"""
    if len(sys.argv) < 2:
        logger.error("使用方法: python test_stage_b42.py <pdf_path>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])

    if not pdf_path.exists():
        logger.error(f"ファイルが存在しません: {pdf_path}")
        sys.exit(1)

    # B-12 実行
    processor = B42MultiColumnReportProcessor()
    result = processor.process(pdf_path)

    # 結果を表示
    logger.info("=" * 60)
    logger.info("[B-42] Multi-Column Report 実行結果:")
    logger.info("=" * 60)

    if result.get('is_structured'):
        logger.info(f"✓ 成功")
        logger.info(f"  データタイプ: {result['data_type']}")
        logger.info(f"  タグ情報: {result['tags']}")
        logger.info(f"  レコード数: {len(result['records'])}")

        # 最初の10レコードを表示
        logger.info("\n  レコードサンプル（最初の10件）:")
        for i, record in enumerate(result['records'][:10]):
            logger.info(f"    [{i+1}] {record}")

        # JSON形式で保存
        output_path = pdf_path.parent / f"{pdf_path.stem}_b42_result.json"
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"\n  結果を保存: {output_path}")

    else:
        logger.error(f"✗ 失敗: {result.get('error')}")

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
