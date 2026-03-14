"""
Pipeline Constants (Ver 9.0)

I/O契約と定数を一元管理
"""

# スキーマバージョン
STAGE_F_OUTPUT_SCHEMA_VERSION = "stage_f_output.v9.0"

# 座標グリッドサイズ（DEPRECATED: Ver 10.7で廃止。E8は生ピクセル座標を維持する。archive互換のため値は残す）
QUANTIZE_GRID_SIZE = 1000

# チャンク処理
CHUNK_SIZE_PAGES = 1

# ============================================
# Stage F: I/O契約（固定）
# ============================================
#
# F1出力: grid + has_table + quality + quality_detail + source + source_detail
# F2出力: logical_structure
# F3出力: structured_table（セル割当の確定）
#
# ============================================

# F2: 構造解析 - Gemini 2.5 Flash
F2_MODEL = "gemini-2.5-flash"
F2_MAX_TOKENS = 65536
F2_TEMPERATURE = 0.0

# F1: グリッド品質閾値（これ以下はフォールバック）
# Aルート(vector) < 0.5 → Bルート(OpenCV) < 0.5 → Cルート(Form Parser)
F1_QUALITY_THRESHOLD = 0.5

# F1: Form Parser設定（環境変数から取得）
# - GCP_PROJECT_ID: GCPプロジェクトID
# - GCP_LOCATION: Document AIリージョン（デフォルト: us）
# - DOCUMENT_AI_FORM_PARSER_PROCESSOR_ID: プロセッサID

# ============================================
# Stage G: I/O契約（固定）
# ============================================
#
# G3: Scrub（唯一の書き換えゾーン）
# G4: Assemble（read-only組み立て）
# G5: Audit（検算・品質・確定 = 唯一の正本出口）
# G6: Packager（用途別出力整形、AI禁止）
#
# 絶対ルール:
# 1. 値を書き換えるのは G3 だけ
# 2. G4/G5/G6 は read-only
# 3. G5の出力 scrubbed_data が唯一の正本
# 4. G6は用途別フォーマットだけ
#
# ============================================

# E7: 文字結合
E7_MODEL = "gemini-2.5-flash-lite"
