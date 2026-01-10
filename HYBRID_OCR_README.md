# ハイブリッドOCR（Surya + PaddleOCR）ガイド

## 概要

Surya（レイアウト解析）とPaddleOCR（日本語認識）を組み合わせた最強のOCRパイプライン。

### アーキテクチャ

```
1. Surya Detection → レイアウト解析 & Bounding Box取得
2. Image Cropping → Suryaの座標で画像切り出し
3. PaddleOCR → 各領域の日本語テキスト認識（高精度）
4. Integration → 読み順に沿ってテキスト結合
5. Gemini（オプション） → 最終的な構造化・抽出
```

### メリット

| 特徴 | PaddleOCRのみ | Suryaのみ | **Surya + Paddle (併用)** |
|------|---------------|-----------|---------------------------|
| レイアウト理解 | △ 単純な横書きのみ | ◎ 非常に強い | ◎ Surya由来 |
| 文字認識精度 | ◎ 日本語に強い | ○ 実用レベル | ◎ Paddle由来 |
| 処理速度 | 速い | 普通 | 遅い（2回推論） |
| 実装難易度 | 低 | 低 | 高（座標計算必要） |

### 推奨ユースケース

1. **複雑なレイアウト**: 新聞、雑誌、段組みのある文書
2. **古文書**: 縦書き・横書き混在、かすれた文字
3. **技術文書**: 図、キャプション、本文が入り乱れている
4. **高精度が必須**: 医療データ、法務文書など

## インストール

すでに完了済み：
- ✅ Python 3.12 仮想環境
- ✅ PaddlePaddle + PaddleOCR
- ✅ Surya OCR
- ✅ 全依存パッケージ

## 使い方

### 1. テストスクリプトで動作確認

```bash
# venv環境をアクティベート（Windows）
.\venv\Scripts\activate

# テスト実行
python test_hybrid_ocr.py <画像ファイルパス>

# 例:
python test_hybrid_ocr.py data/sample_document.png
```

### 2. Stage Fパイプラインで使用

```python
from shared/pipeline.stage_f_visual import StageFVisualAnalyzer
from shared/ai.llm_client.llm_client import LLMClient
from pathlib import Path

# ハイブリッドOCRモードを有効化
llm_client = LLMClient()
analyzer = StageFVisualAnalyzer(llm_client, enable_hybrid_ocr=True)

# 画像を処理
result = analyzer.process_with_hybrid_ocr(Path("document.png"))

if result['success']:
    print(f"抽出テキスト: {result['full_text']}")
    print(f"検出領域数: {len(result['regions'])}")
```

### 3. Gemini Visionと併用

Geminiで後処理を行う場合：

```python
# 1. ハイブリッドOCRでテキスト抽出
hybrid_result = analyzer.process_with_hybrid_ocr(file_path)

# 2. Geminiで構造化
if hybrid_result['success']:
    structured_data = analyzer.llm_client.generate_with_vision(
        prompt=f"""
        以下は高精度OCRで抽出されたテキストです。
        このテキストから情報を抽出してください：

        {hybrid_result['full_text']}
        """,
        image_path=str(file_path),
        model="gemini-1.5-flash",  # Flashで十分（前処理が完璧）
        response_format="json"
    )
```

## 出力フォーマット

```python
{
    'success': True,
    'full_text': '全テキスト（読み順に並び替え済み）',
    'regions': [
        {
            'bbox': [x1, y1, x2, y2],
            'text': '領域のテキスト',
            'confidence': 0.98,
            'region_id': 0
        },
        ...
    ],
    'layout': {'total_regions': 42},
    'char_count': 1234
}
```

## ログ出力

処理中は番号付きログが出力されます：

```
[H-1] ハイブリッドOCR開始: document.png
[H-2] Surya Detection実行...
[H-2] 検出領域数: 42個
[H-3] PaddleOCR テキスト認識開始...
[H-3] 進捗: 11/42 領域処理完了
[H-3] PaddleOCR完了: 42領域を認識
[H-4] テキスト統合中...
[H-4完了] ハイブリッドOCR完了: 2345文字
```

## パフォーマンス最適化

### 処理時間の目安

- **1ページのPDF/画像**: 10-30秒（領域数による）
- **複雑なレイアウト**: 30-60秒
- **大量ページ**: バッチ処理推奨

### 高速化のヒント

1. **GPU使用**: CUDA対応GPUがあれば高速化
2. **バッチ処理**: 複数画像を一度に処理
3. **並列化**: 領域ごとのOCRを並列実行

## トラブルシューティング

### エラー: "Hybrid OCR not enabled"

→ 初期化時に `enable_hybrid_ocr=True` を指定してください

### エラー: "Required packages not installed"

→ Surya または PaddleOCR が正しくインストールされていません
```bash
pip install surya-ocr paddlepaddle paddleocr "paddlex[ocr]"
```

### モデルのダウンロードに時間がかかる

→ 初回実行時のみ、Suryaモデル（約2GB）とPaddleOCRモデル（約500MB）がダウンロードされます

## パイプライン統合

ハイブリッドOCRは **Unified Document Pipeline** に統合されています。

### 設定方法

`shared/pipeline/config/models.yaml` で制御：

```yaml
hybrid_ocr:
  default: false  # デフォルト: 無効（Gemini Visionのみ）
  flyer: true     # チラシ: 有効（複雑なレイアウト）
  classroom: false  # お知らせ: 無効（シンプルなレイアウト）
```

### プログラムでの有効化

```python
from shared/pipeline.pipeline import UnifiedDocumentPipeline

# 方法1: 設定ファイルから自動取得
pipeline = UnifiedDocumentPipeline()  # models.yamlの設定を使用

# 方法2: 明示的に有効化
pipeline = UnifiedDocumentPipeline(enable_hybrid_ocr=True)

# 方法3: 明示的に無効化
pipeline = UnifiedDocumentPipeline(enable_hybrid_ocr=False)
```

### 処理フロー（F-1～F-10）

ハイブリッドOCRが有効な場合、Stage Fで以下のフローが実行されます：

1. **F-1**: PaddleOCR（PPStructure）で表構造を高精度抽出
2. **F-2**: Suryaでレイアウト解析・Bounding Box取得
3. **F-3**: 画像切り出し
4. **F-4**: PaddleOCRで日本語テキスト認識
5. **F-5**: テキスト統合（読み順ソート）
6. **F-6**: プロンプト構築（Stage E + PaddleOCR + Surya）
7. **F-7**: Gemini Vision API呼び出し
8. **F-8**: JSONクリーニング
9. **F-9**: 全結果マージ（重複削除）
10. **F-10**: 最終検証・出力（3種類のデータ）

詳細なログが各ステップで出力されます。

## 次のステップ

1. サンプル画像でテスト実行
2. 実際のドキュメントで精度確認
3. `models.yaml` で doc_type ごとに有効/無効を調整
4. 必要に応じてGemini併用パターンの実装

## 技術詳細

- **Surya**: Transformerベースのレイアウト解析モデル（LayoutLMv3系）
- **PaddleOCR**: PaddlePaddle製OCRフレームワーク（PP-OCRv5）
- **座標計算**: NumPy配列スライシングで高速切り出し
- **読み順ソート**: Y座標優先 + X座標の2段階ソート

---

実装完了日: 2026-01-02
バージョン: 1.0
