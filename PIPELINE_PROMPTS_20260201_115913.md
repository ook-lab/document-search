# パイプライン プロンプト全文ドキュメント
生成日時: 2026-02-01T11:59:13.379007
---
## サマリー
- プロンプト定義数: 9
- LLM呼び出し箇所: 12
- ステージ数: 29

---
## ステージ概要
### __init__.py
```
G_unified_pipeline: 統合ドキュメント処理パイプライン

Stage E-K を統合した、堅牢かつ高精度なドキュメント処理フロー

使用方法:
    from shared.pipeline import UnifiedDocumentPipeline

    pipeline = UnifiedDocumentPipeline()
    result = await pipeline.process_document(
        file_path=Path("document.pdf"),
        file_name="document.pdf",
        doc_type="invoice",
        workspace="personal",
        mime_type="application/pdf",
        source_id="drive_file_id"
    )
```

### stage_h_structuring.py
```
Stage H: Structuring (構造化)

非構造化テキストから、意味のあるデータ(JSON)を抽出
- 役割: 指定スキーマに基づくJSON抽出、表データの正規化
- モデル: 設定ファイルで指定（Gemini, OpenAI等のLLM）
- 重要: doc_typeを判定するのではなく、渡されたdoc_typeのスキーマを使ってデータを抽出

F_stage_c_extractor から完全移行
```

### stage_i_synthesis.py
```
Stage I: Synthesis (統合・要約)

抽出されたデータと元のテキストを統合し、人間と検索エンジンに最適な形に整形
- 役割: 全情報を統合し、要約・タグ生成・基準日付抽出
- モデル: 設定ファイルで指定（デフォルト: Gemini 2.5 Flash）

D_stage_a_classifier から完全移行
```

### config_loader.py
```
設定ローダー

models.yaml と pipeline_routes.yaml を読み込み、
doc_type や workspace に応じて適切なプロンプトとモデルを返す
```

### constants.py
```
Stage F / Stage H 共通定数

v1.1 契約で使用するスキーマバージョンおよび定数を一元管理

【設計 2026-01-31】Ver 6.1 CSV型JSON超軽量モード対応
```

### image_preprocessing.py
```
画像前処理ユーティリティ

PaddleOCRの認識精度向上のための画像前処理機能を提供
```

### ocr_config.py
```
OCRエンジン設定とキャッシング

PaddleOCRの設定、バージョン検出、結果キャッシング機能を提供
```

### ocr_report.py
```
OCR認識精度レポート生成

OCR処理の詳細な統計とレポートを生成
```

### pipeline.py
```
統合ドキュメント処理パイプライン (Stage E-K) - 設定ベース版

設計書: DESIGN_UNIFIED_PIPELINE.md v2.0 に準拠
処理順序: Stage E → F → G → H1 → H2 → J → K

Stage概要:
- Stage E: Pre-processing（テキスト抽出）
- Stage F: Visual Analysis（視覚解析、gemini-2.5-pro）
         - 物理的OCR抽出、JSON出力（カラムナ形式）
- Stage G: Logical Refinement（論理的精錬、gemini-2.0-flash-lite）
         - 重複排除、REF_ID付与、unified_text生成
- Stage H1: Table Specialist（表処理専門）
         - 定型表・構造化表を先に処理
         - カラムナ形式→辞書リスト変換
         - H2への入力量削減のため表テキストを抽出
- Stage H2: Text Specialist（テキスト処理専門、gemini-2.0-flash）
         - 軽量化されたテキストで構造化 + 要約
         - calendar_events, tasks, title, summary を生成
         - audit_canonical_text（監査用正本）を生成
- Stage J: Chunking（チャンク化）
- Stage K: Embedding（ベクトル化）

特徴:
- doc_type / workspace に応じて自動的にプロンプトとモデルを切り替え
- config/ 内の YAML と Markdown ファイルで設定管理
- Stage G で REF_ID付き目録を生成し、後続ステージが参照可能
- H1 + H2 分割によりトークン消費を削減
```

### __init__.py
```
G_unified_pipeline prompts module
```

### stage_e_preprocessing.py
```
Stage E: Pre-processing (前処理) - 物理抽出専用（AI排除版）

【設計 2026-01-26】コスト最適化のためAIを完全排除

役割: ファイルからの「物理的テキスト抽出」のみ
- PDF: pdfplumber / PyMuPDF でテキスト抽出
- Office: OfficeProcessor でテキスト抽出
- テキスト: エンコーディング検出して読み込み
- 画像/音声/動画: raw_text="" で返す（Stage F-7で処理）

処理フロー（E-3で終了）:
- E-1: ファイル検証
- E-2: MIMEタイプルーティング
- E-3: 物理テキスト抽出（AI不使用）

【重要】
- 添付なしの場合は Stage E をスキップし、Stage G からスタート
- 画像/音声/動画は raw_text="" で返し、Stage F-7 で AI 処理
```

### stage_f7_to_f10.py
```
F7〜F10: 新パイプライン（検出OCR版）
【Ver 7.0】LLMによるOCRを完全排除

設計原則:
1. OCRは検出器でやる（Vision API）
2. LLMは解釈だけ（F9.5の選択問題のみ）
3. 座標はプログラムで検証して落とす

ライン構成:
  F7   : Vision API → tokens確定 (word固定)
  F7.5 : Surya block へ IoU優先マッピング + 異常検知
  F8   : page_type判定 → 表なら物理グリッド確定
  F9   : Programでタグ付け（正規表現・近傍ルール）
  F9.5 : LLM救済（少数・選択問題のみ）
  F10  : 異常排除 + 正本化 + anomaly_report
```

### stage_f_visual.py
```
Stage F: Dual-Vision Analysis (独立読解 11段階)

【設計 2026-01-31】Ver 6.5 AI/PROGRAM完全分離モード

核心: 「FはEの答えを知らない状態で、ゼロから視覚情報を暴き出す」

============================================
F-1〜F-5: 構造の下地作り（AIなし、Surya + スクリプト）
  - F-1: Image Normalization (正規化)
  - F-2: Surya Block Detection (領域検出)
  - F-3: Coordinate Quantization (座標量子化) ← トークン削減の肝
  - F-4: Logical Reading Order (読む順序の確定)
  - F-5: Block Classification (構造ラベル付与)

F-6〜F-10: 独立・二重読解（AIの本番）
  - F-6: Blind Prompting (プロンプト注入) ← Stage E結果を遮断
  - F-7: Text Extraction (AI) ← 文字+座標のみ出力、ID判断禁止
  - F-7.5: Coordinate Mapping (PROGRAM) ← 座標距離でID紐付け
  - F-8: Dual Read - Path B (視覚の鬼 / 2.5 Flash)
  - F-9: Physical Sorting + Address Tagging (PROGRAM)
  - F-9.5: AI Rescue for Ambiguous Data (2.5 Flash-Lite)
  - F-10: Stage E Scrubbing (正本化)

【Ver 6.5 変更点】
  - F-7 (AI): 画像内の全文字を座標付きで出力（ID判断は一切させない）
  - F-7.5 (PROGRAM): Surya座標との最短距離でID紐付け（閾値超えはnull）
  - F-8 後行: 構造解析でヘッダー座標を確定
  - F-9 (PROGRAM): Surya bbox + F-8ヘッダーで物理仕分け + 住所タグ付け
  - F-9.5: 低信頼度データのみ AI レスキュー
  - F-10: Stage E の正確なテキストで洗い替え
============================================
```

### stage_g1_table_refiner.py
```
Stage G1: Table Refiner（表専用整理）- Ver 6.4 事実陳列専用

【設計 2026-01-31】Ver 6.4: JSON生成でのAI使用を完全禁止

============================================
【Ver 6.4】絶対禁止事項（時限爆弾の除去）:
  ┌─────────────────────────────────────────┐
  │ ✗ 名寄せ: 表記揺れの勝手な清書         │
  │ ✗ 穴埋め: 読み取れない値の推測補完     │
  │ ✗ 重複判断: AIが「同じ」と判断した行の統合 │
  │                                         │
  │ これらは「善意の改ざん」であり          │
  │ 100%正確な抽出を破壊する時限爆弾である  │
  └─────────────────────────────────────────┘

【Ver 6.4】役割:
  - F-10 洗い替え済みデータを「そのまま」JSON配列に格納
  - AIの「作文」ではなく「事実の陳列」
  - 文字の書き換え・推測による補完・項目の結合は一切禁止

【Ver 6.4】許可されるAI使用:
  - anomaly_report にある異常セルのみのピンポイント画像再読
  - これは「読み直し」であり「推測」ではない

入力:
  - tables: 表データリスト（F-10 洗い替え済み）
  - anomaly_report: 異常セルリスト（外科手術対象）
  - file_path: 画像ファイルパス（画像再読用）

出力:
  - tables: 整形済み表データ（H1 へ直送）
  - token_usage: トークン使用量
  - audit_log: 監査ログ
============================================
```

### stage_g2_text_refiner.py
```
Stage G2: Text Refiner（テキスト専用整理）- Ver 6.4 事実陳列専用

【設計 2026-01-31】Ver 6.4: JSON生成でのAI使用を完全禁止

============================================
【Ver 6.4】絶対禁止事項（時限爆弾の除去）:
  ┌─────────────────────────────────────────┐
  │ ✗ 名寄せ: 表記揺れの勝手な清書         │
  │ ✗ 穴埋め: 読み取れない値の推測補完     │
  │ ✗ OCR浄化: 「文脈から推測して自然に」  │
  │ ✗ スライディング・ウィンドウ研磨       │
  │                                         │
  │ これらは「善意の改ざん」であり          │
  │ 100%正確な抽出を破壊する時限爆弾である  │
  └─────────────────────────────────────────┘

【Ver 6.4】役割:
  - F-10 洗い替え済みデータを「そのまま」JSON配列に格納
  - テキストの順序整理とアンカー挿入のみ（プログラム的処理）
  - AIの「作文」ではなく「事実の陳列」
  - 文字の書き換え・推測による補完・項目の結合は一切禁止

【Ver 6.4】許可されるAI使用:
  - anomaly_report にある異常テキストのみのピンポイント画像再読
  - これは「読み直し」であり「推測」ではない

入力:
  - text_blocks: 地文データリスト（F-10 洗い替え済み）
  - table_anchors: 表のアンカー情報
  - anomaly_report: 異常テキストリスト（外科手術対象）
  - file_path: 画像ファイルパス（画像再読用）

出力:
  - unified_text: 一本化された原稿
  - segments: セグメントリスト
  - token_usage: トークン使用量
  - audit_log: 監査ログ
============================================
```

### stage_g_gate.py
```
Stage G Gate: 純粋物流センター（Ver 5.9）

【設計 2026-01-31】物理的振分に専念

役割: F7/F8/E を束ねて G1/G2 に運ぶだけ
      「知能」を一切持たない。解釈しない。判断しない。

============================================
禁止事項（物理的に削除済み）:
  - 洗い替え（Scrubbing）→ G1/G2の仕事
  - 表候補の検出・分割 → F7が既に決定済み
  - 勝手な廃棄・フィルタリング

許可事項:
  1. パッキング: F7 + F8 + E を一つの袋に詰める
  2. 振分: 表 → G1、テキスト → G2 へ直送

【Ver 5.9】G1/H1への座標データ強化
  - e_content: Stage Eの正確なテキスト（OCR置換用）
  - f8_anchors: Suryaの物理座標（全軸マッピング用）
============================================
```

### stage_g_refiner.py
```
Stage G: Integration Refiner (統合精錬) - v2.0 + Ver 6.4 異常検知対応

【設計 2026-01-28】G-Gate + G1 + G2 による物理的分離
【設計 2026-01-31】Ver 6.4: F-10 anomaly_report による外科手術

役割: Stage E（物理抽出）と Stage F（独立読解）の結果を統合し、
      G1（表専用）と G2（テキスト専用）で整理してから H1/H2 へ渡す

============================================
新アーキテクチャ (Ver 6.4):

[Stage E] + [Stage F (F-10)]
         ↓
    [G-Gate] ←─── 仕分けゲート + anomaly_report 振り分け
         ↓
   ┌─────┴─────┐
   ↓           ↓
 [G1]        [G2]
 表整理      テキスト整理
 +異常再読   +異常再読
   ↓           ↓
 [H1]        [H2]

【Ver 6.4】F-10 からの入力:
  - scrubbed_data: 洗い替え成功データ（信頼度100%）
  - anomaly_report: 異常箇所リスト（外科手術対象）
    - no_bbox: 座標情報がない
    - invalid_bbox: bbox形式が不正
    - no_evidence_in_bbox: 枠内にE文字がない
    - no_physical_evidence: 物理証拠自体がない（画像PDF等）

出力:
  - g1_result: 表データ（検証済み）→ H1 へ
  - g2_result: テキストセグメント（重複排除済み）→ H2 へ
  - unified_text: 統合テキスト（後方互換）
  - source_inventory: REF_ID付きセグメント（後方互換）
  - table_inventory: TBL_ID付き表（後方互換）
  - audit_log: 監査ログ（洗い替え・異常解決の履歴）
============================================
```

### stage_h1_table.py
```
Stage H1: Table Specialist (表処理専門)

【設計 2026-01-27】Stage HI分割: H1 + H2

役割: Stage G の table_inventory から定型表・構造化表を処理
      スキーマやテンプレートに基づいて表データを構造化

============================================
入力:
  - table_inventory: REF_ID付き表リスト（Stage G出力）
  - doc_type: ドキュメントタイプ
  - workspace: ワークスペース

出力:
  - processed_tables: 処理済み表データ
  - extracted_metadata: 表から抽出したメタデータ
  - table_text_fragments: H2から削除すべきテキスト断片

特徴:
  - 軽量モデル使用（Flash-Lite）または LLMなしのルールベース処理
  - カラムナ形式を辞書リストに復元
  - H2への入力量削減のため、処理済み表のテキストを返す
============================================
```

### stage_h2_text.py
```
Stage H2: Text Specialist (テキスト処理専門)

【設計 2026-01-27】Stage HI分割: H1 + H2

役割: H1で軽量化されたテキストを処理し、構造化 + 統合・要約を実行
      従来のStage HI統合版と同等の機能（ただし入力量が削減済み）

============================================
入力:
  - reduced_text: H1で表テキストを削除した軽量テキスト
  - h1_result: Stage H1の処理結果（processed_tables等）
  - source_inventory: REF_ID付きセグメントリスト

出力:
  - document_date: 基準日付
  - tags: 検索用タグ
  - metadata: 構造化データ（H1の結果も含む）
  - title: ドキュメントタイトル
  - summary: 要約
  - calendar_events: カレンダーイベント
  - tasks: タスクリスト
  - audit_canonical_text: 監査用正本テキスト

特徴:
  - 従来のStage HIと同じ処理（互換性維持）
  - 入力テキスト量が削減されているため、トークン消費が減少
  - H1で抽出した表メタデータをマージ
============================================
```

### stage_h_kakeibo.py
```
Stage H: Kakeibo Structuring (家計簿構造化)

家計簿レシート専用のStage H処理
- 税額按分計算
- 商品分類
- マスタデータとの紐付け
```

### stage_hi_combined.py
```
Stage H+I: Combined Structuring & Synthesis (構造化 + 統合・要約)

【設計 2026-01-24】Stage F → G → H+I の情報進化パイプライン
============================================
役割: Stage G の整理済み出力を受け取り、1回のLLM呼び出しで
      構造化（旧Stage H）と統合・要約（旧Stage I）を同時に実行

入力:
  - unified_text: Stage G で整理されたMarkdown全文
  - source_inventory: REF_ID付きセグメントリスト
  - table_inventory: REF_ID付き表リスト
  - stage_f_structure: Stage F の構造化情報（フォールバック用）

出力:
  - document_date: 基準日付
  - tags: 検索用タグ
  - metadata: 構造化データ（basic_info, articles, weekly_schedule, etc.）
  - title: ドキュメントタイトル
  - summary: 要約
  - calendar_events: カレンダーイベント
  - tasks: タスクリスト
  - audit_canonical_text: 監査用正本テキスト

特徴:
  - 1回のLLM呼び出しでH+Iを実行（コスト削減）
  - REF_IDによる参照追跡
  - 情報の完全維持（1文字も削除しない）
============================================
```

### stage_j_chunking.py
```
Stage J: Chunking (チャンク化)

メタデータからチャンクを生成
- 役割: 検索用チャンクの作成
- 処理: MetadataChunker でメタデータチャンク生成
```

### stage_k_embedding.py
```
Stage K: Embedding (ベクトル化)

チャンクをベクトル化して search_index に保存
- 役割: チャンクをベクトル化
- モデル: OpenAI text-embedding-3-small (1536次元)
```

### __init__.py
```
Pipeline Utilities
```

### table_parser.py
```
Table Parser Utilities

カラムナ形式（columns + rows）を辞書リスト形式に復元するユーティリティ
Stage F → Stage H1 間のデータ変換に使用
```

### vision_api_extractor.py
```
F7: Vision API OCR Extractor
【Ver 7.0】検出OCR版 - LLMによるOCRを完全排除

設計原則:
- OCRは「検出器」で行う（Vision API）
- LLMは使わない（推測・生成を構造的に封じる）
- 座標はプログラムで検証して落とす
```

### embeddings.py
```
Embedding Client (DEPRECATED - OpenAI text-embedding-3-small を使用してください)
このクラスは後方互換性のために残されていますが、使用は推奨されません。
代わりに LLMClient.generate_embedding() を使用してください。
```

### exceptions.py
```
LLMクライアントのカスタム例外
```

### llm_client.py
```
LLMクライアント（v3.0: マルチプロバイダ対応）
Gemini / Anthropic / OpenAI を統一インターフェースで利用
```

---
## プロンプト全文
**以下がLLMに渡される実際の指示文です。ハルシネーションの原因追跡に使用してください。**

### 1. inline_f_prompt_1 (stage_h_structuring.py)
- 種別: inline-f-string
- 説明: インライン定義（動的パラメータ含む）

**プロンプト全文:**
```
以下のJSONにエラーがあります。修正してください。

エラー: {error_message}

元のJSON:
```
{failed_content}
```

修正されたJSONを ```json ブロックで出力してください。
```

### 2. inline_f_prompt_1 (stage_f7_to_f10.py)
- 種別: inline-f-string
- 説明: インライン定義（動的パラメータ含む）

**プロンプト全文:**
```
以下のテキストに最も適切なタグを1つ選んでください。

テキスト: "{text}"

選択肢:
1. address（住所）
2. phone（電話番号）
3. date（日付）
4. postal_code（郵便番号）
5. other（その他）
6. unclear（判別不能）

回答は番号のみ（例: 1）で答えてください。
```

### 3. _build_f7_prompt (stage_f_visual.py)
- 種別: function
- 説明: 【Ver 6.6】無限ループ防止版 - 推測禁止・終端条件明示

        AIの仕事: 画像に実在する文字だけを座標付きで出力
        禁止事項: 推測、補完、表の復元、同一文字の反復生成

**プロンプト全文:**
```
あなたは「抽出器」です。推測・補完・並べ替え・表の復元は禁止です。
画像に存在する文字だけを、見える範囲から抽出してください。
画像に存在しない行・列・項目・繰り返しを生成してはいけません。

## 出力形式（JSONのみ、説明文禁止）
{
  "texts": [
    {"text": "<string>", "bbox": [x0, y0, x1, y1]}
  ],
  "stop_reason": "<string>"
}

## 基本設計
1) これは“レイアウト理解タスク”ではない、“テキスト抽出”タスクだ。
2) 「意味的一貫性」ではなく、単純な文字の「列挙」を要求しています。
3) 「一覧ではない。」全体を考えることは禁止。常に部分の抽出だけを考えろ。
4) 全体を考えないのだから、「抜けがある」かどうかを想像するのは禁止。
5) 「位置はバラバラ」なので、相対的な空白位置にデータがあるはずという考えは禁止

## 最重要ルール
6) 画像に確実に見える文字列だけを出力する。不確実なら出力しない。
7) 読み込んだテキストを、絶対に視覚的に再生成しない。
8) 縦方向か、横方向か、一定のルートをたどって読み続ける。縦横ランダムに読まない。
9) 縦に読む場合は左端から、左右の座標を固定して上端から下端まで読み、右へ少しずらして、また左右の座標を固定して上端から下端まで読むことを繰り返す。
10) 横に読む場合は上端から、上下の座標を固定して左端から右端まで読み、下へ少しずらして、また上下の座標を固定して左端から右端まで読むことを繰り返す。
11) y座標を一定刻みで増やして「表の続きを生成」する行為は禁止。画像の根拠がないのにbboxを規則的に増やして出力してはいけない。
12) 全ての座標にテキストがあることを前提にしない。
13) 多くの場所は空欄である。空欄であったら文字生成をスキップさせる。
14) bboxは必ず「その文字が存在する場所」を囲む。推測で座標を作らない。
15) bboxは必ず重ならない。
16) 文字列の正規化（学校名の推測、漢字変換、表記ゆれ統合）禁止。見たままを出力。
17) 赤/黄色のIDラベルは無視（読み取り対象外）。

## 座標ガード（最重要）
A) bbox は必ず「2つの座標ペア」だけを持つこと：
   bbox = [x0, y0, x1, y1]
   それ以外の形式（点、3点、ポリゴン、配列のネスト、bboxが無い等）は出力禁止。

B) 座標値の上限：
   x0, y0, x1, y1 のいずれも 1001 以上を禁止する。
   1001以上の値を出しそうになったら、そこで直ちに stop_reason="COORD_GUARD" で終了せよ。

C) 完全同一 bbox の反復禁止（絶対）：
   同一ページ内で、完全に同じ bbox [x0,y0,x1,y1] を2回以上出力してはいけない。
   既出 bbox を出しそうになった時点で、直ちに stop_reason="REPEAT_GUARD" で終了せよ。

## 停止条件（いずれかで終了）
- 画像からこれ以上確実な文字が見つからない → stop_reason="COMPLETE"
- textsが2000件 → "LIMIT_REACHED"
- 反復ガード（同一text100回 or 同一bbox2回）→ "REPEAT_GUARD"
- 座標上限超過（1001以上）→ "COORD_GUARD"

このタスクは「網羅」より「真実性」を優先する。見えないものは欠損として良い。
```

### 4. _build_f8_prompt (stage_f_visual.py)
- 種別: function
- 説明: 【Ver 6.6】F-8: ヘッダー候補抽出（例削除・推測禁止・null許可版）

        禁止: 推測、補完、一般知識による埋め合わせ、固有名詞生成
        許可: 不確実なら空配列、根拠付きヘッダーのみ

**プロンプト全文:**
```
あなたは「ヘッダー候補抽出器」です。推測・補完・一般知識による埋め合わせは禁止です。
画像内に実在する文字列だけを根拠として、表の見出し候補を抽出してください。
画像に存在しない固有名詞・学校名・地名などを生成してはいけません。

## タスク
1) x_headers: 表の列見出し候補（上段に並ぶ見出し）
2) y_headers: 表の行見出し候補（左側に並ぶ見出し）
3) table_structure: 表の構造情報（行数・列数・セル結合）
※「候補」であり、確証がない場合は空配列でよい。

## 絶対ルール
- 推測で補完しない。見えていないものは出さない。
- 画像に出現しない文字列を生成しない。
- 既知の典型（学校名一覧、都道府県、科目など）で埋めない。
- 迷ったらnull/[]を返す（欠損は正しい）。
- 重複候補を増やさない（同一文字列は1回まで）。

## 根拠ルール
x_headers/y_headersの各要素には「根拠bbox」を必ず付ける。
根拠が提示できない候補は出力禁止。

## 出力形式（JSONのみ、説明文禁止）
{{
  "x_headers": [
    {{"text": "<string>", "bbox": [x0,y0,x1,y1], "confidence": 0.0-1.0}}
  ],
  "y_headers": [
    {{"text": "<string>", "bbox": [x0,y0,x1,y1], "confidence": 0.0-1.0}}
  ],
  "table_structure": {{
    "header_rows": <int>,
    "total_rows": <int or null>,
    "total_cols": <int or null>,
    "table_type": "list_type" | "matrix_type" | null
  }},
  "notes": ["不確実なため空配列にした", "見出しが判別不能"]
}}

## 停止/NULL方針
- 表の見出しが明確に存在しない、または判定不能ならx_headers=[], y_headers=[]とする。
- その場合notesに理由を書く（短文でよい）。
- 文字の書き起こしは別工程が担当。構造のみに集中する。
```

### 5. rescue_prompt (stage_f_visual.py)
- 種別: f-string
- 説明: 動的パラメータ含む

**プロンプト全文:**
```
# 住所特定レスキュー（Ver 6.6）

x_headers / y_headers が空の場合、推測で新規ヘッダーを生成してはいけない。
空は「不明」を意味する。対応する header_id は null のままにする。

以下のテキストについて、画像上の位置関係からX軸・Y軸ヘッダーを特定してください。

## 利用可能なヘッダー
- X軸ヘッダー: {json.dumps(x_headers, ensure_ascii=False)}
- Y軸ヘッダー: {json.dumps(y_headers, ensure_ascii=False)}

## 判定対象
{json.dumps([{'id': i['id'], 'text': i['text']} for i in low_confidence_items], ensure_ascii=False, indent=2)}

## 出力形式
```json
{{"rescued": [{{"id": "22", "x_header": "2/1", "y_header": "74"}}]}}
```
特定できない場合は null。説明不要。
```

### 6. inline_f_prompt_1 (stage_g1_table_refiner.py)
- 種別: inline-f-string
- 説明: インライン定義（動的パラメータ含む）

**プロンプト全文:**
```
この画像に写っている文字だけを、正確に読み取ってください。

【絶対ルール】
1. 画像に写っている文字のみを出力
2. 推測や補完は一切しない
3. 読めない場合は「読取不能」と出力

【出力形式】
読み取った文字のみ（説明不要）
```

### 7. inline_f_prompt_1 (stage_g2_text_refiner.py)
- 種別: inline-f-string
- 説明: インライン定義（動的パラメータ含む）

**プロンプト全文:**
```
この画像に写っているテキストを、正確に読み取ってください。

【絶対ルール】
1. 画像に写っている文字のみを出力
2. 推測や補完は一切しない
3. 改行は空白に置き換える
4. 読めない場合は「読取不能」と出力

【出力形式】
読み取ったテキストのみ（説明不要）
```

### 8. inline_f_prompt_1 (stage_h2_text.py)
- 種別: inline-f-string
- 説明: インライン定義（動的パラメータ含む）

**プロンプト全文:**
```
以下のJSONにエラーがあります。修正してください。

エラー: {error_message}

元のJSON:
```
{failed_content[:3000]}
```

修正されたJSONを ```json ブロックで出力してください。
```

### 9. inline_f_prompt_1 (stage_hi_combined.py)
- 種別: inline-f-string
- 説明: インライン定義（動的パラメータ含む）

**プロンプト全文:**
```
以下のJSONにエラーがあります。修正してください。

エラー: {error_message}

元のJSON:
```
{failed_content[:3000]}
```

修正されたJSONを ```json ブロックで出力してください。
```

---
## LLM呼び出し箇所
**どこでLLMが呼ばれ、どのプロンプトが使われているかの一覧**

### 1. stage_f_visual.py:1627
- 使用プロンプト: `self`
- 使用モデル: `F7_MODEL_IMAGE`

**コンテキスト:**
```python
        logger.info("[F-7] Path A - 構造マッピング開始（座標排除版）")

        prompt = self._build_f7_prompt()

        try:
            response = self.llm_client.generate_with_vision(
                prompt=prompt,
                image_path=str(file_path),
                model=F7_MODEL_IMAGE,
                max_tokens=F7_F8_MAX_TOKENS,
                temperature=F7_F8_TEMPERATURE,
                response_format="json"
            )

            # 【Ver 6.4】生成物ログ出力（MAX_TOKENS途切れ対応）
```

### 2. stage_f_visual.py:2029
- 使用プロンプト: `self`
- 使用モデル: `F8_MODEL`

**コンテキスト:**
```python
        logger.info("[F-8] Path B - Visual Analysis 開始（座標排除版）")

        prompt = self._build_f8_prompt()

        try:
            response = self.llm_client.generate_with_vision(
                prompt=prompt,
                image_path=str(file_path),
                model=F8_MODEL,
                max_tokens=F7_F8_MAX_TOKENS,
                temperature=F7_F8_TEMPERATURE,
                response_format="json"
            )

            # 【Ver 6.4】生成物ログ出力（MAX_TOKENS途切れ対応）
```

### 3. stage_f_visual.py:2490
- 使用プロンプト: `self`
- 使用モデル: `F7_MODEL_IMAGE`

**コンテキスト:**
```python

        try:
            # Ver 6.4 プロンプト: AIは文字を読むだけ
            prompt = self._build_f7_prompt()

            response = self.llm_client.generate_with_vision(
                prompt=prompt,
                image_path=str(temp_path),
                model=F7_MODEL_IMAGE,
                max_tokens=F7_F8_MAX_TOKENS,
                temperature=F7_F8_TEMPERATURE,
                response_format="json"
            )

            # 【Ver 6.4】生成物ログ出力（MAX_TOKENS途切れ対応）
```

### 4. stage_f_visual.py:2789
- 使用プロンプト: `self`
- 使用モデル: `F7_MODEL_IMAGE`

**コンテキスト:**
```python
                prompt = self._build_f7_smart_prompt(
                    chunk_idx, chunk_start_page, patch_info, patch_type, len(patches),
                    patch_index=patch_idx, is_continuation=is_continuation
                )

                response = self.llm_client.generate_with_vision(
                    prompt=prompt,
                    image_path=str(temp_path),
                    model=F7_MODEL_IMAGE,
                    max_tokens=F7_F8_MAX_TOKENS,
                    temperature=F7_F8_TEMPERATURE,
                    response_format="json"
                )

                # 【Ver 6.4】生成物ログ出力（MAX_TOKENS途切れ対応）
```

### 5. stage_f_visual.py:3159
- 使用プロンプト: `self`
- 使用モデル: `F7_MODEL_IMAGE`

**コンテキスト:**
```python
            temp_image_path = Path(f.name)

        prompt = self._build_f7_chunk_prompt(chunk_idx, chunk_start_page, 1)

        try:
            response = self.llm_client.generate_with_vision(
                prompt=prompt,
                image_path=str(temp_image_path),
                model=F7_MODEL_IMAGE,
                max_tokens=F7_F8_MAX_TOKENS,
                temperature=F7_F8_TEMPERATURE,
                response_format="json"
            )

            # 【Ver 6.4】生成物ログ出力（MAX_TOKENS途切れ対応）
```

### 6. stage_f_visual.py:3249
- 使用プロンプト: `self`
- 使用モデル: `F8_MODEL`

**コンテキスト:**
```python
        temp_image_path = self._save_chunk_as_temp_image(chunk_pages, chunk_idx)

        prompt = self._build_f8_chunk_prompt(chunk_idx, chunk_start_page, len(chunk_pages))

        try:
            response = self.llm_client.generate_with_vision(
                prompt=prompt,
                image_path=str(temp_image_path),
                model=F8_MODEL,
                max_tokens=F7_F8_MAX_TOKENS,
                temperature=F7_F8_TEMPERATURE,
                response_format="json"
            )

            # 【Ver 6.4】生成物ログ出力（MAX_TOKENS途切れ対応）
```

### 7. stage_f_visual.py:3750
- 使用プロンプト: `rescue_prompt`
- 使用モデル: `F95_MODEL`

**コンテキスト:**
```python
                import tempfile
                with tempfile.NamedTemporaryFile(suffix='_f95_rescue.png', delete=False) as f:
                    image.save(f, format='PNG')
                    temp_path = Path(f.name)

                response = self.llm_client.generate_with_vision(
                    prompt=rescue_prompt,
                    image_path=str(temp_path),
                    model=F95_MODEL,  # Ver 6.2: gemini-2.5-flash-lite
                    max_tokens=2000,
                    temperature=0.1,
                    response_format="json"
                )

                try:
```

### 8. stage_f_visual.py:3765
- 使用プロンプト: `rescue_prompt`
- 使用モデル: `F95_MODEL`

**コンテキスト:**
```python
                    temp_path.unlink()
                except:
                    pass
            else:
                # 画像がない場合はテキストのみで判断
                response = self.llm_client.generate(
                    prompt=rescue_prompt,
                    model=F95_MODEL,  # Ver 6.2: gemini-2.5-flash-lite
                    max_tokens=2000,
                    temperature=0.1,
                    response_format="json"
                )

            # 【Ver 6.4】生成物ログ出力（MAX_TOKENS途切れ対応）
            logger.info(f"[F-9.5] ===== 生成物ログ開始 =====")
```

### 9. stage_g1_table_refiner.py:415
- 使用プロンプト: `prompt`
- 使用モデル: `G1_REREAD_MODEL`

**コンテキスト:**
```python
3. 読めない場合は「読取不能」と出力

【出力形式】
読み取った文字のみ（説明不要）"""

            response = self.llm.generate_with_vision(
                prompt=prompt,
                image_path=tmp_path,
                model=G1_REREAD_MODEL,
                max_tokens=500,
                temperature=0.0
            )

            # 一時ファイル削除
            import os
```

### 10. stage_g2_text_refiner.py:425
- 使用プロンプト: `prompt`
- 使用モデル: `G2_REREAD_MODEL`

**コンテキスト:**
```python
4. 読めない場合は「読取不能」と出力

【出力形式】
読み取ったテキストのみ（説明不要）"""

            response = self.llm.generate_with_vision(
                prompt=prompt,
                image_path=tmp_path,
                model=G2_REREAD_MODEL,
                max_tokens=1000,
                temperature=0.0
            )

            # 一時ファイル削除
            try:
```

### 11. stage_k_embedding.py:91
- 使用プロンプト: `unknown`
- 使用モデル: `unknown`

**コンテキスト:**
```python
            try:
                # null文字を除去
                chunk_text = chunk['chunk_text'].replace('\u0000', '') if chunk['chunk_text'] else ''

                # Embedding生成
                embedding = self.llm_client.generate_embedding(chunk_text)

                # search_indexに保存
                chunk_data = {
                    'document_id': document_id,
                    'owner_id': owner_id,  # Phase 3: 親ドキュメントから継承
                    'chunk_content': chunk_text,
                    'chunk_size': len(chunk_text),
                    'chunk_type': chunk['chunk_type'],
                    'embedding': embedding,
```

### 12. llm_client.py:473
- 使用プロンプト: `unknown`
- 使用モデル: `unknown`

**コンテキスト:**
```python
            dimensions=config.get("dimensions", 1536)  # デフォルト1536次元
        )

        return response.data[0].embedding

    def generate_with_vision(
        self,
        prompt: str,
        image_path: str,
        model: str = "gemini-2.0-flash-exp",
        temperature: float = 0.0,
        max_tokens: int = 65536,
        response_format: Optional[str] = None
    ) -> str:
        """
```

---
## 終わり
このドキュメントでプロンプト全文が確認できない場合は、collect_pipeline.py のバグです。
