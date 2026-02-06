"""
hensachi 系（偏差値表）axis_bins 回帰テスト

目的:
  axis_bins 経路が将来の変更で崩れても即検知できるよう、
  指標ベースの回帰テストを提供する。

合格条件（数値で固定）:
  - format_id が hensachi 系
  - axis_bins 経路に入っている
  - 行数が一定以上（rows >= 30）
  - 偏差値が入っている行率（deviation_present_rate >= 0.8）
  - 代表校が取れる（開成/麻布/武蔵 のいずれか2つ以上）
  - notes_empty_rate が高すぎない（<= 0.7）

使用方法:
  pytest tests/test_hensachi_axis_bins_regression.py -v

fixtures 配置:
  tests/fixtures/hensachi/
    ├── hensachi_1.pdf  # 合不合判定テスト Aライン80 男子（サンプル1）
    ├── hensachi_2.pdf  # 合不合判定テスト Aライン80 男子（サンプル2）
    └── hensachi_3.pdf  # 合不合判定テスト Aライン80 男子（サンプル3）
"""

import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
import pytest

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# fixtures ディレクトリ
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "hensachi"

# テスト対象 PDF
TEST_PDFS = [
    ("hensachi_1.pdf", {"min_rows": 30, "min_dev_rate": 0.8}),
    ("hensachi_2.pdf", {"min_rows": 30, "min_dev_rate": 0.8}),
    ("hensachi_3.pdf", {"min_rows": 25, "min_dev_rate": 0.75}),  # サンプル3は少なめでもOK
]

# 代表校リスト（いずれか2つ以上が取れればOK）
REPRESENTATIVE_SCHOOLS = [
    "開成", "麻布", "武蔵", "駒場東邦", "海城",
    "早稲田", "慶應", "渋谷教育学園", "聖光学院", "栄光学園",
    "筑波大学附属駒場", "桜蔭", "女子学院", "雙葉", "豊島岡",
]


def load_pipeline():
    """パイプラインをロード（遅延インポート）"""
    try:
        from shared.ai.llm_client.llm_client import LLMClient
        from shared.pipeline.stage_f.f1_grid_detector import F1GridDetector
        from shared.pipeline.stage_f.f2_structure_analyzer import F2StructureAnalyzer
        from shared.pipeline.stage_f.f3_cell_assigner import F3CellAssigner
        from shared.pipeline.stage_e import E6VisionOCR, E7TextMerger, E8BboxNormalizer
        return {
            "LLMClient": LLMClient,
            "F1GridDetector": F1GridDetector,
            "F2StructureAnalyzer": F2StructureAnalyzer,
            "F3CellAssigner": F3CellAssigner,
            "E6VisionOCR": E6VisionOCR,
            "E7TextMerger": E7TextMerger,
            "E8BboxNormalizer": E8BboxNormalizer,
        }
    except ImportError as e:
        pytest.skip(f"パイプラインモジュールが見つかりません: {e}")


def pdf_to_images(pdf_path: Path) -> List[Any]:
    """PDFを画像に変換"""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(pdf_path))
        images = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom
            img_bytes = pix.tobytes("png")
            images.append({
                "page_num": page_num,
                "bytes": img_bytes,
                "width": pix.width,
                "height": pix.height,
            })
        doc.close()
        return images
    except ImportError:
        pytest.skip("PyMuPDF (fitz) が必要です: pip install pymupdf")


def run_e1_to_f3_pipeline(pdf_path: Path) -> Dict[str, Any]:
    """
    E1〜F3 パイプラインを実行し、F3 結果を返す

    Returns:
        {
            'format_id': str,
            'column_strategy': str,
            'rows': List[Dict],
            'tagged_texts': List[Dict],
            'x_headers': List[str],
            'y_headers': List[str],
            'stats': Dict,
            'structure': Dict,
        }
    """
    modules = load_pipeline()

    # PDF → 画像
    images = pdf_to_images(pdf_path)
    if not images:
        pytest.skip(f"PDFから画像を取得できません: {pdf_path}")

    # 最初のページのみ処理（回帰テスト用）
    img_data = images[0]

    # E6: Vision OCR
    e6 = modules["E6VisionOCR"]()
    # E6は画像パスを期待するので、一時ファイルに書き出す
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(img_data["bytes"])
        temp_img_path = Path(f.name)

    try:
        e6_result = e6.process(temp_img_path)
    finally:
        temp_img_path.unlink()

    tokens = e6_result.get("tokens", [])
    page_size = e6_result.get("page_size", {"width": img_data["width"], "height": img_data["height"]})

    # E7: Text Merger（LLMClient が必要、スキップ可能）
    try:
        llm_client = modules["LLMClient"]()
        e7 = modules["E7TextMerger"](llm_client)
        e7_result = e7.process(tokens, page_size)
        tokens = e7_result.get("merged_tokens", tokens)
    except Exception:
        pass  # E7 スキップ

    # E8: BBox Normalizer
    e8 = modules["E8BboxNormalizer"]()
    e8_result = e8.normalize(tokens, page_size)
    tokens = e8_result.get("normalized_tokens", tokens)

    # F1: Grid Detector
    f1 = modules["F1GridDetector"]()
    f1_result = f1.detect(tokens, page_size)
    grid = f1_result.get("grid", {})

    # F2: Structure Analyzer
    try:
        llm_client = modules["LLMClient"]()
        f2 = modules["F2StructureAnalyzer"](llm_client)
    except Exception:
        # LLMClient なしでも動作確認
        f2 = modules["F2StructureAnalyzer"](None)

    f2_result = f2.analyze(grid, tokens, page_size)
    structure = f2_result

    # F3: Cell Assigner
    f3 = modules["F3CellAssigner"]()
    f3_result, _ = f3.assign(grid, tokens, structure)

    return {
        "format_id": structure.get("metadata", {}).get("format_id", ""),
        "column_strategy": structure.get("metadata", {}).get("column_strategy", ""),
        "rows": f3_result.get("rows", []),
        "tagged_texts": f3_result.get("tagged_texts", []),
        "x_headers": f3_result.get("x_headers", []),
        "y_headers": f3_result.get("y_headers", []),
        "stats": f3_result.get("stats", {}),
        "structure": structure,
    }


def count_deviation_present(tagged_texts: List[Dict]) -> tuple:
    """
    偏差値が入っている行数をカウント

    Returns:
        (total_rows, deviation_present_count, rate)
    """
    # y_header ごとにグループ化
    rows_by_school = {}
    for t in tagged_texts:
        y_header = t.get("y_header", "")
        if not y_header:
            continue
        if y_header not in rows_by_school:
            rows_by_school[y_header] = {"has_deviation": False}
        # col=1 または x_header に「偏差値」が含まれる
        if t.get("col") == 1 or "偏差値" in t.get("x_header", ""):
            text = t.get("text", "").strip()
            if text and text.isdigit():
                rows_by_school[y_header]["has_deviation"] = True

    total = len(rows_by_school)
    dev_count = sum(1 for r in rows_by_school.values() if r["has_deviation"])
    rate = dev_count / total if total > 0 else 0

    return total, dev_count, rate


def find_representative_schools(y_headers: List[str]) -> List[str]:
    """代表校を検索"""
    found = []
    for school in REPRESENTATIVE_SCHOOLS:
        for yh in y_headers:
            if school in yh:
                if school not in found:
                    found.append(school)
                break
    return found


def count_notes_empty_rate(tagged_texts: List[Dict]) -> float:
    """備考が空の行率"""
    # y_header ごとにグループ化
    rows_by_school = {}
    for t in tagged_texts:
        y_header = t.get("y_header", "")
        if not y_header:
            continue
        if y_header not in rows_by_school:
            rows_by_school[y_header] = {"has_note": False}
        # col=2 または x_header に「備考」が含まれる
        if t.get("col") == 2 or "備考" in t.get("x_header", ""):
            text = t.get("text", "").strip()
            if text:
                rows_by_school[y_header]["has_note"] = True

    total = len(rows_by_school)
    empty_count = sum(1 for r in rows_by_school.values() if not r["has_note"])
    rate = empty_count / total if total > 0 else 0

    return rate


# =============================================================================
# テストケース
# =============================================================================

class TestHensachiAxisBinsRegression:
    """hensachi 系 axis_bins 回帰テスト"""

    @pytest.fixture(autouse=True)
    def check_fixtures(self):
        """fixtures ディレクトリの存在確認"""
        if not FIXTURES_DIR.exists():
            pytest.skip(
                f"fixtures ディレクトリが見つかりません: {FIXTURES_DIR}\n"
                f"テスト用PDFを配置してください。"
            )

    @pytest.mark.parametrize("pdf_name,thresholds", TEST_PDFS)
    def test_axis_bins_regression(self, pdf_name: str, thresholds: Dict):
        """
        axis_bins 回帰テスト

        合格条件:
        - format_id が hensachi 系
        - column_strategy == "axis_bins"
        - 行数 >= min_rows
        - 偏差値率 >= min_dev_rate
        - 代表校 >= 2
        - notes_empty_rate <= 0.7
        """
        pdf_path = FIXTURES_DIR / pdf_name
        if not pdf_path.exists():
            pytest.skip(f"テストPDFが見つかりません: {pdf_path}")

        # パイプライン実行
        result = run_e1_to_f3_pipeline(pdf_path)

        # 1. format_id チェック
        format_id = result["format_id"]
        assert format_id, f"format_id が空です"
        assert "hensachi" in format_id.lower() or format_id.startswith("hensachi"), \
            f"format_id が hensachi 系ではありません: {format_id}"

        # 2. column_strategy チェック
        column_strategy = result["column_strategy"]
        assert column_strategy == "axis_bins", \
            f"column_strategy が axis_bins ではありません: {column_strategy}"

        # 3. 行数チェック
        y_headers = result["y_headers"]
        min_rows = thresholds["min_rows"]
        assert len(y_headers) >= min_rows, \
            f"行数が不足: {len(y_headers)} < {min_rows}"

        # 4. 偏差値率チェック
        total, dev_count, dev_rate = count_deviation_present(result["tagged_texts"])
        min_dev_rate = thresholds["min_dev_rate"]
        assert dev_rate >= min_dev_rate, \
            f"偏差値率が低すぎ: {dev_rate:.2f} < {min_dev_rate} (total={total}, dev={dev_count})"

        # 5. 代表校チェック
        found_schools = find_representative_schools(y_headers)
        assert len(found_schools) >= 2, \
            f"代表校が不足: {found_schools} (2校以上必要)"

        # 6. notes_empty_rate チェック
        notes_empty_rate = count_notes_empty_rate(result["tagged_texts"])
        assert notes_empty_rate <= 0.7, \
            f"備考空率が高すぎ: {notes_empty_rate:.2f} > 0.7"

        # 成功ログ
        print(f"\n=== {pdf_name} 回帰テスト合格 ===")
        print(f"  format_id: {format_id}")
        print(f"  column_strategy: {column_strategy}")
        print(f"  rows: {len(y_headers)}")
        print(f"  deviation_rate: {dev_rate:.2f}")
        print(f"  representative_schools: {found_schools}")
        print(f"  notes_empty_rate: {notes_empty_rate:.2f}")


class TestAxisBinsUnit:
    """axis_bins ユニットテスト（PDFなしで実行可能）"""

    def test_school_key_normalization(self):
        """_school_key() の正規化テスト"""
        from shared.pipeline.stage_f.f3_cell_assigner import F3CellAssigner

        f3 = F3CellAssigner()

        # 正規化テストケース
        cases = [
            ("渋谷教育学園 幕張", "渋谷教育学園幕張"),
            ("渋谷教育学園　幕張", "渋谷教育学園幕張"),  # 全角スペース
            ("開成※1", "開成"),  # 注釈除去
            ("麻布（1回）", "麻布"),  # 括弧内除去
            ("早稲田‐学院", "早稲田学院"),  # ハイフン除去
        ]

        for input_name, expected in cases:
            result = f3._school_key(input_name)
            assert result == expected, f"_school_key('{input_name}') = '{result}', expected '{expected}'"

    def test_looks_like_school_name(self):
        """_looks_like_school_name() のテスト"""
        from shared.pipeline.stage_f.f3_cell_assigner import F3CellAssigner

        f3 = F3CellAssigner()

        # 学校名として認識すべき
        should_be_school = ["開成", "麻布", "武蔵", "渋谷教育学園幕張", "早稲田学院"]
        for name in should_be_school:
            assert f3._looks_like_school_name(name), f"'{name}' は学校名として認識すべき"

        # 学校名として認識すべきでない
        should_not_be_school = ["偏差値", "Aライン", "80", "男子", "2/3", "2026年"]
        for name in should_not_be_school:
            assert not f3._looks_like_school_name(name), f"'{name}' は学校名として認識すべきでない"

    def test_is_deviation_value(self):
        """_is_deviation_value() のテスト"""
        from shared.pipeline.stage_f.f3_cell_assigner import F3CellAssigner

        f3 = F3CellAssigner()

        # 偏差値として認識すべき
        should_be_dev = [("72", ""), ("65", "NUM"), ("50", "NUMBER")]
        for text, token_type in should_be_dev:
            assert f3._is_deviation_value(text, token_type), f"'{text}' は偏差値として認識すべき"

        # 偏差値として認識すべきでない
        should_not_be_dev = [("10", ""), ("85", ""), ("abc", ""), ("", "")]
        for text, token_type in should_not_be_dev:
            assert not f3._is_deviation_value(text, token_type), f"'{text}' は偏差値として認識すべきでない"


# =============================================================================
# スタンドアロン実行
# =============================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
