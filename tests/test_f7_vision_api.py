"""
F7 Vision API Extractor テスト
"""
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from shared.pipeline.vision_api_extractor import VisionAPIExtractor


def test_extract(image_path: str):
    """Vision API OCR テスト"""
    print(f"\n{'='*60}")
    print(f"F7 Vision API Test: {image_path}")
    print('='*60)

    extractor = VisionAPIExtractor()

    # TEXT_DETECTION 版
    print("\n[1] TEXT_DETECTION (word単位)")
    result = extractor.extract(Path(image_path))

    print(f"  Provider: {result['ocr_provider']}")
    print(f"  Page size: {result['page_size']}")
    print(f"  Tokens: {len(result['tokens'])}")
    print(f"  Low conf: {len(result['tokens_low_conf'])}")
    print(f"  Stats: {result['stats']}")

    # 最初の10トークンを表示
    print("\n  First 10 tokens:")
    for i, token in enumerate(result['tokens'][:10]):
        print(f"    [{i}] '{token['text']}' bbox={token['bbox']} conf={token['conf']}")

    # DOCUMENT_TEXT_DETECTION 版
    print("\n[2] DOCUMENT_TEXT_DETECTION (confidence付き)")
    result2 = extractor.extract_with_document_detection(Path(image_path))

    print(f"  Provider: {result2['ocr_provider']}")
    print(f"  Tokens: {len(result2['tokens'])}")
    print(f"  Low conf: {len(result2['tokens_low_conf'])}")
    print(f"  Stats: {result2['stats']}")

    # 最初の10トークンを表示
    print("\n  First 10 tokens:")
    for i, token in enumerate(result2['tokens'][:10]):
        print(f"    [{i}] '{token['text']}' bbox={token['bbox']} conf={token['conf']}")

    # low_conf があれば表示
    if result2['tokens_low_conf']:
        print("\n  Low confidence tokens:")
        for token in result2['tokens_low_conf'][:5]:
            print(f"    '{token['text']}' conf={token['conf']}")

    print("\n" + "="*60)
    print("TEST COMPLETE")
    print("="*60)

    return result2


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_f7_vision_api.py <image_path>")
        print("Example: python test_f7_vision_api.py temp/page_0.png")
        sys.exit(1)

    test_extract(sys.argv[1])
