# F60 UI デリバリー設計メモ（`F60UIDeliveryController` — ディレクトリ名 `stage_g` は履歴由来）

更新日: 2026-05-12  
対象: `F60UIDeliveryController` が組み立てるチェーンと `ui_data` の役割分担。

## チェーン全体

```
F50 までの Stage F 出口
  ↓
F-52（`F08TableRebuilder`）… `consolidated_tables` を **cells（E40）/ data（B）/ markdown（旧E）** いずれからも同一 `ui_tables` 形へリビルド（F60 入口で実行）
  ↓
F60 内 … **Stage F（F50 まで）由来のイベント・タスク・注意・本文（`raw_text`）を `sections` 用ブロックに並べる**（E-52 は廃止）
  ↓
F-53（`F09NoiseEliminator`）… 受け取った `blocks` で ui_data 素体を組み、表チェーンへ分岐
      ├─ 表（`ui_tables` 入力）: F-54 → F-55 → F-56 → F-57 → F-58 → ui_data.tables
      └─ 地の文: LLM チェーンなし（`g21_articles` は **F50 出口の `non_table_text` の全文複写**、チャンク・要約・合成見出しなし）
  ↓
終端（`__init__.py` 内）… ui_data 最終整形・`final_metadata`（互換キー名を維持）
```

- **G-6 は廃止**（コメント上の旧参照のみ残る場合あり）。
- **G-21 / G-22（テキスト記事化・テキスト AI）は廃止**。予定・タスク・注意の正本は **F 由来の `ui_data`**。`g22_output` は空スロット互換のみ。
- 表チェーンは **F-55 の検出に従い F-56 がサブ表化**し、**F-58 が table_analyses** を返す。

## 入力（F50 までの Stage F から F60 が読むもの）

| キー | 用途 |
|------|------|
| `non_table_text` | **F50 地の文の正本**（F60 が `raw_text` に載せて sections / `g21_articles` の材料にする） |
| `consolidated_tables` | F60 表再生の入力 |
| `normalized_events` / `tasks` / `notices` | F60 のブロック化・F-53 の timeline 等 |
| `document_info` / `display_fields` | メタ・表示用フィールド |

## 出力 `ui_data` の役割（現行の意図）

| キー | 出所 | 備考 |
|------|------|------|
| `sections` | F60 → F-53 → `_dedupe_prose_sections` | 最終では **イベント等のブロックのみ**（地の文は `g21_articles` のみで二重表示を避ける） |
| `g21_articles` | F60（**F50 `non_table_text` 全文を1件**、`title` は空） | **地の文の正本**（旧 G21 LLM 置き換え廃止） |
| `tables` | F-58 後変換 | UI 用グリッド |
| `tables_review_html` | F60（`stage_f.review_tables_payload`） | 人が読む・印刷向けの単純 HTML 断片 |
| `timeline` / `actions` / `notices` | F 由来（G-22 の上書きなし） | 構造化スロット |
| `g11_structured_tables` | F-54 | デバッグ・下流参照用（キー名 `g11_*` は後方互換） |

### 地の文の二重防止（2026-05 時点）

`g21_articles` に**本文がある**場合、`F60UIDeliveryController` は `ui_data.sections` から **`type: "text"` を削除**する（`_dedupe_prose_sections`）。  
**プローズの単一正本は F50 の `non_table_text`（`g21_articles` に複写）**。`sections` はイベント等の非テキストブロック用に残す。

## `final_metadata`（レビュー・デバッグ用）

- `g11_output` / `g14_output` / `g17_output` / `g21_output` / `g22_output` など中間〜最終の参照コピー（`g22_output` は互換用の空／プレースホルダ）。
- `tables_ssot` … 再検証・差分用の表まわり正本（`structured_tables` / `detections` / `e14_reconstructed`）。

## 他サービスとの整合

- **pipeline-lab**: `visual_stream` は `g21_articles` に本文があるとき **E 非表ブロックと二重に並べない**（正本に合わせる）。
- **`ui_data` → Markdown 変換**: `g21_articles` を Markdown に出し、g21 に本文があるときは `sections` の `type:text` をスキップ。
- **doc-search**: スケジュール用キーワード探索に `g21_articles` も含める（sections から text が除かれても取りこぼさない）。

## 関連ファイル

| ファイル | 役割 |
|----------|------|
| `__init__.py` | `F60UIDeliveryController`・sections ブロック組立・チェーン組立・`ui_data` 最終マージ・`_dedupe_prose_sections`・`g21_articles`（F50 正本からのルール生成） |
| `stage_f/f11_table_structurer.py` 〜 `stage_f/f47_table_ai_processor.py` + `stage_f/f13`・`f14`・`f46` | 表構造化〜UI 表（実行順 F-54→F-55→F-56→F-57→F-58） |
| `stage_f/f08_table_rebuilder.py` | F50 後の `ui_tables` リビルド（ログタグは F-52） |
| `stage_f/f09_noise_eliminator.py` | 受け取った `blocks` で `ui_data` 素体・表チェーン分岐（`text_chain` は未使用） |
