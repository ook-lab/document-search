"""
Stage A + B 統合テストスクリプト

Stage Aで書類を判定し、Stage B Controllerで適切なプロセッサを選択・実行。

使用例:
    python scripts/debug/test_stage_ab.py "path/to/file.pdf"
    python scripts/debug/test_stage_ab.py "path/to/file.docx"
"""

import sys
import json
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from shared.pipeline.stage_a import A3EntryPoint
from shared.pipeline.stage_b import B1Controller


def main():
    """Stage A + B 統合テスト実行"""
    if len(sys.argv) < 2:
        logger.error("使用方法: python test_stage_ab.py <file_path>")
        sys.exit(1)

    file_path = Path(sys.argv[1])

    if not file_path.exists():
        logger.error(f"ファイルが存在しません: {file_path}")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Stage A + B 統合テスト開始")
    logger.info("=" * 60)

    # ========================================
    # Stage A: 書類の判断
    # ========================================
    stage_a = A3EntryPoint()
    a_result = stage_a.process(file_path)

    if not a_result.get('success'):
        logger.error(f"Stage A 失敗: {a_result.get('error')}")
        sys.exit(1)

    logger.info("\n[Stage A] 判定結果:")
    logger.info(f"  書類種類: {a_result['document_type']}")
    logger.info(f"  信頼度: {a_result['confidence']}")
    logger.info(f"  理由: {a_result['reason']}")

    # ========================================
    # Stage B: 形式特化型・物理構造化
    # ========================================
    stage_b = B1Controller()
    b_result = stage_b.process(file_path, a_result)

    logger.info("\n[Stage B] 実行結果:")
    logger.info(f"  プロセッサ: {b_result.get('processor_name')}")
    logger.info(f"  構造化成功: {b_result.get('is_structured')}")

    if b_result.get('is_structured'):
        # データタイプ別に結果を表示
        data_type = b_result.get('data_type')

        if data_type == 'report_multicolumn':
            # B-42 の結果
            logger.info(f"  データタイプ: {data_type}")
            logger.info(f"  レコード数: {len(b_result.get('records', []))}")

            # サンプル表示
            records = b_result.get('records', [])
            if records:
                logger.info("\n  レコードサンプル（最初の5件）:")
                for i, record in enumerate(records[:5]):
                    logger.info(f"    [{i+1}] {record}")

        elif 'paragraphs' in b_result:
            # B-6 (Native Word) の結果
            logger.info(f"  段落数: {len(b_result.get('paragraphs', []))}")
            logger.info(f"  表の数: {len(b_result.get('structured_tables', []))}")

        elif 'sheets' in b_result:
            # B-7 (Native Excel) の結果
            sheets = b_result.get('sheets', [])
            logger.info(f"  シート数: {len(sheets)}")
            for sheet in sheets:
                logger.info(f"    - {sheet['name']}: {sheet['max_row']}行 x {sheet['max_col']}列")

        elif 'slides' in b_result:
            # B-8 (Native PowerPoint) の結果
            logger.info(f"  スライド数: {len(b_result.get('slides', []))}")

        elif 'logical_blocks' in b_result:
            # PDF系プロセッサの結果
            logger.info(f"  論理ブロック数: {len(b_result.get('logical_blocks', []))}")
            logger.info(f"  表の数: {len(b_result.get('structured_tables', []))}")

        # タグ情報
        if 'tags' in b_result:
            logger.info(f"\n  タグ情報: {b_result['tags']}")

        # B-90 Layer Purge の結果
        if 'purged_image_paths' in b_result:
            logger.info(f"\n[B-90] Layer Purge 結果:")
            logger.info(f"  消去済みPDF: {b_result.get('purged_pdf_path', 'N/A')}")
            logger.info(f"  生成画像数: {len(b_result.get('purged_image_paths', []))}")
            mask_stats = b_result.get('mask_stats', {})
            if mask_stats:
                logger.info(f"  消去率: {mask_stats.get('masked_area_percentage', 0):.2f}%")
                logger.info(f"  消去bbox数: {mask_stats.get('bbox_count', 0)}")

        # JSON形式で保存
        output_path = file_path.parent / f"{file_path.stem}_ab_result.json"
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump({
                'stage_a': a_result,
                'stage_b': b_result
            }, f, ensure_ascii=False, indent=2)
        logger.info(f"\n  結果を保存: {output_path}")

    else:
        logger.error(f"  エラー: {b_result.get('error')}")

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
