"""
Stage F / Stage H 共通定数

v1.1 契約で使用するスキーマバージョンおよび定数を一元管理
"""

# Stage H 入力スキーマバージョン
STAGE_H_INPUT_SCHEMA_VERSION = "stage_h_input.v1.1"

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

# F-6 OCR 上限
MAX_OCR_CALLS = 20
MAX_CROP_LONG_EDGE = 1000  # リサイズ閾値
PER_PAGE_MAX_UNION_ROI = 3
MIN_ROI_AREA = 2000  # 最小ROI面積
UNION_PADDING = 20  # union ROIのpadding (px)

# F-2 Surya リサイズ上限
SURYA_MAX_DIM = 2000
