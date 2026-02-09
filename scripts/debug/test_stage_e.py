"""
Stage E テストスクリプト（視覚抽出・AI構造化）

使用例:
    # Stage D の結果ファイルを指定
    python scripts/debug/test_stage_e.py "path/to/stage_d_result.json" --api-key "YOUR_GEMINI_API_KEY"

    # または Stage D の出力ディレクトリを指定
    python scripts/debug/test_stage_e.py "path/to/stage_d_output_dir" --api-key "YOUR_GEMINI_API_KEY"
"""

import sys
import json
import os
import argparse
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from shared.pipeline.stage_e import E1Controller


def main():
    """Stage E テスト実行"""
    parser = argparse.ArgumentParser(description='Stage E 視覚抽出テスト')
    parser.add_argument('input_path', type=str, help='Stage D 結果ファイル or 出力ディレクトリ')
    parser.add_argument('--api-key', type=str, help='Gemini API Key')
    parser.add_argument('--output', type=str, help='出力ディレクトリ（オプション）')

    args = parser.parse_args()

    # API Key の取得（環境変数 or 引数）
    api_key = args.api_key or os.environ.get('GEMINI_API_KEY')
    if not api_key:
        logger.error("Gemini API Key が設定されていません")
        logger.error("--api-key オプションか GEMINI_API_KEY 環境変数を設定してください")
        sys.exit(1)

    # 入力パスの解析
    input_path = Path(args.input_path)

    if input_path.is_file() and input_path.suffix == '.json':
        # JSON ファイルとして読み込み
        with open(input_path, 'r', encoding='utf-8') as f:
            stage_d_result = json.load(f)
    elif input_path.is_dir():
        # ディレクトリ内の table_*.png と background_only.png を探す
        stage_d_result = {
            'tables': [],
            'non_table_image_path': ''
        }

        # 表画像を検索
        for table_img in input_path.glob('table_*.png'):
            stage_d_result['tables'].append({
                'table_id': table_img.stem.replace('table_', ''),
                'image_path': str(table_img),
                'cell_map': []  # TODO: cell_map を読み込む
            })

        # 非表画像を検索
        bg_img = input_path / 'background_only.png'
        if bg_img.exists():
            stage_d_result['non_table_image_path'] = str(bg_img)

        if not stage_d_result['tables'] and not stage_d_result['non_table_image_path']:
            logger.error("ディレクトリ内に Stage D の出力画像が見つかりません")
            sys.exit(1)
    else:
        logger.error("入力パスが不正です: JSON ファイル or ディレクトリを指定してください")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Stage E 視覚抽出テスト開始")
    logger.info("=" * 60)

    # Stage E 実行
    controller = E1Controller(gemini_api_key=api_key)
    result = controller.process(stage_d_result)

    # 結果を表示
    logger.info("\n[Stage E] 実行結果:")
    logger.info(f"  成功: {result.get('success')}")

    if result.get('success'):
        # 非表領域の結果
        non_table = result.get('non_table_content', {})
        if non_table:
            logger.info("\n  非表領域（地の文）:")
            logger.info(f"    ├─ 成功: {non_table.get('success')}")
            if non_table.get('success'):
                logger.info(f"    ├─ モデル: {non_table.get('model_used')}")
                logger.info(f"    └─ トークン: {non_table.get('tokens_used')}")

                # 抽出内容のサマリー
                content = non_table.get('extracted_content', {})
                if 'schedule' in content:
                    logger.info(f"\n    予定: {len(content.get('schedule', []))}件")
                if 'tasks' in content:
                    logger.info(f"    タスク: {len(content.get('tasks', []))}件")
                if 'notices' in content:
                    logger.info(f"    注意事項: {len(content.get('notices', []))}件")

        # 表領域の結果
        tables = result.get('table_contents', [])
        if tables:
            logger.info(f"\n  表領域: {len(tables)}個")
            for idx, table in enumerate(tables):
                table_id = table.get('table_id', f'T{idx+1}')
                logger.info(f"\n    表 {table_id}:")
                logger.info(f"      ├─ 成功: {table.get('success')}")
                if table.get('success'):
                    logger.info(f"      ├─ モデル: {table.get('model_used')}")
                    logger.info(f"      └─ トークン: {table.get('tokens_used')}")

                    # Markdown のプレビュー（最初の5行）
                    markdown = table.get('table_markdown', '')
                    if markdown:
                        lines = markdown.split('\n')[:5]
                        logger.info(f"\n      Markdown（先頭5行）:")
                        for line in lines:
                            logger.info(f"        {line}")
                        if len(markdown.split('\n')) > 5:
                            logger.info("        ...")

        # メタデータ
        metadata = result.get('metadata', {})
        logger.info(f"\n  メタデータ:")
        logger.info(f"    ├─ 総トークン: {metadata.get('total_tokens', 0)}")
        logger.info(f"    └─ 使用モデル: {', '.join(metadata.get('models_used', []))}")

        # JSON形式で保存
        output_dir = Path(args.output) if args.output else input_path.parent
        output_path = output_dir / "stage_e_result.json"

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"\n  結果を保存: {output_path}")

    else:
        logger.error(f"  エラー: {result.get('error')}")

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
