"""
Stage B テストスクリプト

使用例:
    python scripts/debug/test_stage_b.py "path/to/file.docx"
    python scripts/debug/test_stage_b.py "path/to/file.xlsx"
    python scripts/debug/test_stage_b.py "path/to/file.pptx"
"""

import sys
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from shared.pipeline.stage_b import (
    B6NativeWordProcessor,
    B7NativeExcelProcessor,
    B8NativePPTProcessor
)


def main():
    """Stage B テスト実行"""
    if len(sys.argv) < 2:
        logger.error("使用方法: python test_stage_b.py <file_path>")
        sys.exit(1)

    file_path = Path(sys.argv[1])

    if not file_path.exists():
        logger.error(f"ファイルが存在しません: {file_path}")
        sys.exit(1)

    # ファイル拡張子に応じてプロセッサを選択
    suffix = file_path.suffix.lower()

    if suffix == '.docx':
        processor = B6NativeWordProcessor()
        stage_name = "B-6 (Native Word)"
    elif suffix == '.xlsx':
        processor = B7NativeExcelProcessor()
        stage_name = "B-7 (Native Excel)"
    elif suffix == '.pptx':
        processor = B8NativePPTProcessor()
        stage_name = "B-8 (Native PowerPoint)"
    else:
        logger.error(f"未対応のファイル形式: {suffix}")
        sys.exit(1)

    # Stage B 実行
    logger.info(f"[{stage_name}] 処理開始: {file_path.name}")
    result = processor.process(file_path)

    # 結果を表示
    logger.info("=" * 60)
    logger.info(f"[{stage_name}] 実行結果:")
    logger.info("=" * 60)

    if result.get('is_structured'):
        logger.info(f"✓ 成功")
        logger.info(f"  タグ情報: {result['tags']}")

        # Word
        if 'paragraphs' in result:
            logger.info(f"  段落数: {len(result['paragraphs'])}")

        # Excel
        if 'sheets' in result:
            logger.info(f"  シート数: {len(result['sheets'])}")
            for sheet in result['sheets']:
                logger.info(f"    - {sheet['name']}: {sheet['max_row']}行 x {sheet['max_col']}列")

        # PowerPoint
        if 'slides' in result:
            logger.info(f"  スライド数: {len(result['slides'])}")

        # 表
        if result.get('structured_tables'):
            logger.info(f"  表の数: {len(result['structured_tables'])}")

        # テキストプレビュー
        text = result.get('text_with_tags', '')
        if text:
            preview = text[:500] + ('...' if len(text) > 500 else '')
            logger.info(f"\n  テキストプレビュー:\n{preview}")

    else:
        logger.error(f"✗ 失敗: {result.get('error')}")

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
