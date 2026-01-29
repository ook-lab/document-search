"""
Stage F / Stage H 共通定数

v1.1 契約で使用するスキーマバージョンおよび定数を一元管理

【設計 2026-01-26】新Stage F: 10ステップ構成対応
"""

# Stage H 入力スキーマバージョン
STAGE_H_INPUT_SCHEMA_VERSION = "stage_h_input.v1.1"

# Stage F 出力スキーマバージョン
STAGE_F_OUTPUT_SCHEMA_VERSION = "stage_f_output.v2.0"

# block_type の許可値（v1.1）
BLOCK_TYPES_V1_1 = [
    "post_body",      # 投稿本文（最優先文脈、必ず先頭）
    "heading",        # 見出し
    "paragraph",      # 段落
    "list_item",      # 箇条書き
    "table",          # 表（Markdown形式）
    "table_text",     # 表内テキスト
    "note",           # 注記
]

# ============================================
# Stage F: 10ステップ構成の定数
# ============================================

# F-1: Image Normalization
F1_TARGET_DPI = 300  # 統一DPI

# F-2: Surya Block Detection
SURYA_MAX_DIM = 2000  # Suryaリサイズ上限

# F-3: Coordinate Quantization（座標量子化）
QUANTIZE_GRID_SIZE = 1000  # 1000×1000 グリッド

# F-6 OCR 上限（レガシー互換）
MAX_OCR_CALLS = 20
MAX_CROP_LONG_EDGE = 1000  # リサイズ閾値
PER_PAGE_MAX_UNION_ROI = 3
MIN_ROI_AREA = 2000  # 最小ROI面積
UNION_PADDING = 20  # union ROIのpadding (px)

# F-7: Dual Read - Path A
F7_MODEL_IMAGE = "gemini-2.0-flash"  # 画像用（テキストの鬼）
F7_MODEL_AV = "gemini-2.5-flash-lite"  # 音声/動画用

# F-8: Dual Read - Path B
F8_MODEL = "gemini-2.5-flash"  # 構造解析（視覚の鬼）

# F-7/F-8 共通
F7_F8_MAX_TOKENS = 65536
F7_F8_TEMPERATURE = 0.0

# チャンク処理（MAX_TOKENSエラー回避）
# gemini-2.0-flash の出力上限は 8,192 トークンのため、1ページ単位で処理
CHUNK_SIZE_PAGES = 1  # 1ページごとに分割処理

# ============================================
# Stage G / H1 / H2 モデル定義
# ============================================

# Stage G: Integration Refiner
G_MODEL = "gemini-2.5-flash-lite"

# Stage H1: Table Specialist
H1_MODEL = "gemini-2.5-flash-lite"

# Stage H2: Text Specialist
H2_MODEL = "gemini-2.5-flash"
