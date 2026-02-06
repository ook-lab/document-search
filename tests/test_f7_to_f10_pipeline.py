"""
F7〜F10 新パイプライン テスト
"""
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from shared.pipeline.stage_f7_to_f10 import (
    F7VisionOCR,
    F75CoordinateMapper,
    F8Structuring,
    F9PhysicalTagger,
    F10Scrubbing,
    F7toF10Pipeline
)


def test_f7_only(image_path: str):
    """F7単体テスト"""
    print(f"\n{'='*60}")
    print(f"F7 Vision API Test: {image_path}")
    print('='*60)

    f7 = F7VisionOCR()
    result = f7.process(Path(image_path))

    print(f"Provider: {result['ocr_provider']}")
    print(f"Page size: {result['page_size']}")
    print(f"Tokens: {len(result['tokens'])}")
    print(f"Low conf: {len(result['tokens_low_conf'])}")
    print(f"Stats: {result['stats']}")

    print("\nFirst 10 tokens:")
    for i, t in enumerate(result['tokens'][:10]):
        print(f"  [{i}] '{t['text']}' conf={t['conf']:.3f}")

    return result


def test_f75_mapping(tokens, mock_blocks, page_size):
    """F7.5マッピングテスト"""
    print(f"\n{'='*60}")
    print("F7.5 Coordinate Mapping Test")
    print('='*60)

    f75 = F75CoordinateMapper()
    result = f75.process(tokens, mock_blocks, page_size)

    print(f"Mapped: {len(result['mapped_tokens'])}")
    print(f"Unmapped: {len(result['unmapped_tokens'])}")
    print(f"Anomalies: {result['anomalies']}")

    print("\nFirst 5 mapped:")
    for t in result['mapped_tokens'][:5]:
        print(f"  block={t['id']}: '{t['text']}' score={t['score']}")

    return result


def test_f8_structuring(mapped_tokens, blocks, page_size):
    """F8構造化テスト"""
    print(f"\n{'='*60}")
    print("F8 Structuring Test")
    print('='*60)

    f8 = F8Structuring()
    result = f8.process(mapped_tokens, blocks, page_size)

    print(f"Page type: {result['page_type']}")
    print(f"Table likelihood: {result['table_likelihood']}")
    print(f"Tables: {len(result['tables'])}")
    print(f"Text blocks: {len(result['text_blocks'])}")

    if result['tables']:
        t = result['tables'][0]
        print(f"\nTable 0:")
        print(f"  bbox: {t['table_bbox']}")
        print(f"  rows: {t['row_count']}, cols: {t['col_count']}")
        print(f"  x_headers: {[h['text'] for h in t['x_headers'][:5]]}")

    return result


def test_f9_tagging(mapped_tokens, f8_result, page_size):
    """F9タグ付けテスト"""
    print(f"\n{'='*60}")
    print("F9 Physical Tagger Test")
    print('='*60)

    f9 = F9PhysicalTagger()
    result = f9.process(mapped_tokens, f8_result, page_size)

    print(f"Tagged: {len(result['tagged_texts'])}")
    print(f"Low confidence: {len(result['low_confidence'])}")

    # タグ別カウント
    tag_counts = {}
    for t in result['tagged_texts']:
        tag = t.get('tag') or 'none'
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
    print(f"Tags: {tag_counts}")

    # タグ付きサンプル
    tagged = [t for t in result['tagged_texts'] if t.get('tag')]
    print("\nTagged samples:")
    for t in tagged[:5]:
        print(f"  [{t['tag']}] '{t['text']}'")

    return result


def test_f10_scrubbing(f9_result, f75_anomalies):
    """F10正本化テスト"""
    print(f"\n{'='*60}")
    print("F10 Scrubbing Test")
    print('='*60)

    f10 = F10Scrubbing()
    result = f10.process(f9_result, f75_anomalies)

    print(f"Final tokens: {len(result['final_tokens'])}")
    print(f"Final tables: {len(result['final_tables'])}")
    print(f"Anomalies: {len(result['anomaly_report'])}")
    print(f"Stop reason: {result['stop_reason']}")

    if result['anomaly_report']:
        print("\nAnomaly report:")
        for a in result['anomaly_report']:
            print(f"  {a['type']}: {a.get('count', a.get('ratio', ''))}")

    return result


def test_full_pipeline(image_path: str):
    """フルパイプラインテスト"""
    print(f"\n{'='*60}")
    print(f"F7-F10 Full Pipeline Test: {image_path}")
    print('='*60)

    # モックのSuryaブロック（実際はSuryaから取得）
    # 画像全体を1つのブロックとする簡易版
    mock_blocks = {
        "b0": {"bbox": [0, 0, 1200, 500]},  # 画像全体
    }

    pipeline = F7toF10Pipeline()
    result = pipeline.process(
        Path(image_path),
        mock_blocks
    )

    print(f"\n=== Pipeline Result ===")
    print(f"F7: {result['f7']}")
    print(f"F7.5: {result['f75']}")
    print(f"F8: {result['f8']}")
    print(f"F9: {result['f9']}")
    print(f"F9.5: {result['f95']}")
    print(f"F10 stop_reason: {result['f10']['stop_reason']}")
    print(f"Total elapsed: {result['elapsed']}s")

    return result


def test_step_by_step(image_path: str):
    """ステップバイステップテスト"""
    print(f"\n{'#'*60}")
    print(f"# Step-by-Step Test: {image_path}")
    print('#'*60)

    # F7
    f7_result = test_f7_only(image_path)
    tokens = f7_result['tokens']
    page_size = f7_result['page_size']

    # モックブロック（画像を4分割）
    w, h = page_size['w'], page_size['h']
    mock_blocks = {
        "b0": {"bbox": [0, 0, w//2, h//2]},
        "b1": {"bbox": [w//2, 0, w, h//2]},
        "b2": {"bbox": [0, h//2, w//2, h]},
        "b3": {"bbox": [w//2, h//2, w, h]},
    }

    # F7.5
    f75_result = test_f75_mapping(tokens, mock_blocks, page_size)
    mapped = f75_result['mapped_tokens']

    # F8
    f8_result = test_f8_structuring(mapped, mock_blocks, page_size)

    # F9
    f9_result = test_f9_tagging(mapped, f8_result, page_size)

    # F10
    f10_result = test_f10_scrubbing(f9_result, f75_result['anomalies'])

    print("\n" + "="*60)
    print("ALL STEPS COMPLETE")
    print("="*60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_f7_to_f10_pipeline.py <image_path>")
        print("Example: python test_f7_to_f10_pipeline.py .local/temp/nolley_top.png")
        sys.exit(1)

    image_path = sys.argv[1]

    # ステップバイステップテスト
    test_step_by_step(image_path)

    # フルパイプラインテスト
    test_full_pipeline(image_path)
