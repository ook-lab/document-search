# hensachi テスト用 fixtures

このディレクトリには、hensachi 系（偏差値表）の回帰テスト用 PDF を配置します。

## 必要なファイル

- `hensachi_1.pdf` - 合不合判定テスト Aライン80 偏差値一覧 男子（サンプル1）
- `hensachi_2.pdf` - 合不合判定テスト Aライン80 偏差値一覧 男子（サンプル2）
- `hensachi_3.pdf` - 合不合判定テスト Aライン80 偏差値一覧 男子（サンプル3）

## 期待される内容

各 PDF は以下の条件を満たす必要があります：

1. **フォーマット**: 四谷大塚の合不合判定テスト偏差値表
2. **構造**: 学校名が左側、偏差値が中央〜右側に配置
3. **行数**: 30行以上（学校が30校以上）
4. **代表校**: 開成、麻布、武蔵などの代表校が含まれる

## テスト実行

```bash
# 全テスト実行
pytest tests/test_hensachi_axis_bins_regression.py -v

# ユニットテストのみ（PDF不要）
pytest tests/test_hensachi_axis_bins_regression.py::TestAxisBinsUnit -v

# 回帰テストのみ（PDF必要）
pytest tests/test_hensachi_axis_bins_regression.py::TestHensachiAxisBinsRegression -v
```

## 注意

- テスト用 PDF はリポジトリにコミットしないでください（.gitignore に追加済み）
- 実際のテスト実行時は、ローカルに PDF を配置してください
