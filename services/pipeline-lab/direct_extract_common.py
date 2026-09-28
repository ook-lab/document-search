"""
AI直接抽出（Gemini 3.8 flash によるブロック構造化・検証・Markdown合成）の共通モジュール。

対話用（blueprints/lab.py: api_extract_direct）とバッチ用（pipeline_batch.py）で
プロンプト、スキーマ、画像化、検証、Markdown 合成を共通利用する。
フォールバック絶対禁止（欠損やエラーの隠蔽・推測補完は不可）。
"""
from __future__ import annotations

import html as _html
import json
from typing import Any, Dict, List, Literal, Optional, Tuple
from pydantic import BaseModel, Field, ValidationError
import fitz
import yaml as _yaml


# ---------------------------------------------------------------------------
# Pydantic スキーマ定義
# ---------------------------------------------------------------------------

class ContainerInfo(BaseModel):
    id: str = Field(description="コンテナ識別子（例: 'page_main', 'box_notice', 'col_left', 'col_right'）")
    type: Literal["page_body", "callout_box", "column_left", "column_right", "header", "footer"] = Field(
        description="コンテナ種別。枠線で囲まれた領域は 'callout_box'、段組みは 'column_*'"
    )
    title: Optional[str] = Field(None, description="枠線の上や内部に書かれた枠タイトル（例: '4月の予定', '持ち物'）")
    bbox: Optional[List[int]] = Field(None, description="枠線・段組み全体の [ymin, xmin, ymax, xmax] (0〜1000 整数)")


class TableRelationInfo(BaseModel):
    role: Literal[
        "table_body",              # 表本体そのもの
        "pre_table_description",   # 表の直前にある導入・説明文（例: '以下の通り集金を...'）
        "table_heading",           # 表のタイトル・セクション見出し
        "table_footnote",          # 表の直後にある注記・脚注（例: '※日程は変更の可能性があります'）
        "none"                     # 表とは無関係な一般的な地の文
    ] = Field(description="表との文脈的関係")
    target_table_id: Optional[str] = Field(None, description="関連する表ID（例: 'T1'）。none の場合は null")


class TableData(BaseModel):
    table_id: str = Field(description="表ID（例: 'T1', 'T2'）")
    caption: str = Field(description="表のタイトル（画像上のタイトルまたは端的な名称）")
    description: str = Field(description="何の表かを端的に説明した要約（20〜40文字）")
    row_label_column_count: int = Field(
        description="左端から何列が行見出し（行ラベル・インデックス列）かを表す列数（0以上の整数。行見出しが無い場合は0、通常の表で第1列が日付や項目の場合は1）"
    )
    header_axes: List[str] = Field(
        description="見出しの各段が何を表すかの名前の配列（例: ['クラス', '時限']）。header_rows の段数と同数で空文字禁止"
    )
    header_rows: List[List[str]] = Field(
        description="見出し行の配列（上の段から順に1段以上）。横結合や縦結合された見出しセルはその範囲の全列・全段に値を完全展開する"
    )
    data_rows: List[List[str]] = Field(
        description="データ行（セル内改行は解体し1データ1行、空セルは空文字、結合セルは完全展開）"
    )


class PageBlock(BaseModel):
    order: int = Field(description="ページ内での自然な読み順（1から始まる連番）")
    block_type: Literal[
        "heading",             # 大見出し・中見出し・小見出し
        "paragraph",           # 一般の地の文・段落
        "bullet_list",         # 箇条書き
        "table",               # 表本体
        "figure_caption",      # 写真・イラスト・図のキャプション（印刷された文字）
        "figure_description",  # 写真・イラスト・図の内容の説明（キャプションとは別）
        "footnote"             # ページ下部の脚注・注釈
    ] = Field(description="ブロックの種別。『枠』はブロックではなく container で表す")
    bbox: List[int] = Field(
        description="ブロックの正規化座標 [ymin, xmin, ymax, xmax] (0〜1000 の整数。ymin < ymax, xmin < xmax)"
    )
    container: ContainerInfo = Field(description="このブロックが所属する枠や段の情報")
    table_relation: TableRelationInfo = Field(description="表との文脈的関係")
    text_content: Optional[str] = Field(
        default=None, description="Markdownテキスト（見出しの#や箇条書きの-を含む）。table の場合は null"
    )
    table_data: Optional[TableData] = Field(
        default=None, description="表の詳細構造データ。table 以外の場合は null"
    )


class DirectExtractPageResult(BaseModel):
    page_index: int = Field(description="ページ番号（0始まり）")
    blocks: List[PageBlock] = Field(description="読み順に並んだ全ブロックの配列")


class DirectExtractValidationError(Exception):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


# ---------------------------------------------------------------------------
# 厳格な業務ルール検証（フォールバック絶対禁止）
# ---------------------------------------------------------------------------

def validate_direct_extract_result(data: DirectExtractPageResult) -> None:
    """スキーマ違反、order の欠番・重複、bbox の矛盾、表の列数不一致、block_type 不整合を厳格に検査。フォールバック絶対禁止。"""
    blocks = data.blocks
    if not blocks:
        raise DirectExtractValidationError("ブロック配列 (blocks) が空です。")

    orders = [b.order for b in blocks]
    expected_orders = list(range(1, len(blocks) + 1))
    if orders != expected_orders:
        raise DirectExtractValidationError(
            f"order の連番不正: 期待値={expected_orders[:10]}... 実際={orders[:10]}... (欠番または重複があります)"
        )

    for b in blocks:
        bb = b.bbox
        if not isinstance(bb, (list, tuple)) or len(bb) != 4:
            raise DirectExtractValidationError(f"ブロック #{b.order} の bbox 要素数不正 (4要素必要): {bb}")
        for coord in bb:
            if not isinstance(coord, int) or coord < 0 or coord > 1000:
                raise DirectExtractValidationError(
                    f"ブロック #{b.order} の bbox 座標が 0〜1000 の整数ではありません: {bb}"
                )
        ymin, xmin, ymax, xmax = bb
        if ymin >= ymax:
            raise DirectExtractValidationError(f"ブロック #{b.order} の bbox 座標矛盾 (ymin >= ymax): {bb}")
        if xmin >= xmax:
            raise DirectExtractValidationError(f"ブロック #{b.order} の bbox 座標矛盾 (xmin >= xmax): {bb}")

        if b.container and b.container.bbox is not None:
            cbb = b.container.bbox
            if not isinstance(cbb, (list, tuple)) or len(cbb) != 4:
                raise DirectExtractValidationError(f"ブロック #{b.order} の container.bbox 要素数不正: {cbb}")
            for coord in cbb:
                if not isinstance(coord, int) or coord < 0 or coord > 1000:
                    raise DirectExtractValidationError(f"ブロック #{b.order} の container.bbox 範囲不正: {cbb}")
            if cbb[0] >= cbb[2] or cbb[1] >= cbb[3]:
                raise DirectExtractValidationError(f"ブロック #{b.order} の container.bbox 座標矛盾: {cbb}")

        if b.block_type == "table":
            if not b.table_data:
                raise DirectExtractValidationError(
                    f"ブロック #{b.order} は block_type='table' ですが table_data がありません。"
                )
            if b.text_content:
                raise DirectExtractValidationError(
                    f"ブロック #{b.order} は block_type='table' ですが text_content が設定されています (null であるべきです)。"
                )
            td = b.table_data
            if not td.header_rows or not isinstance(td.header_rows, list) or len(td.header_rows) == 0:
                raise DirectExtractValidationError(
                    f"表ブロック #{b.order} (table_id='{td.table_id}') の header_rows が空です。"
                )
            num_header_rows = len(td.header_rows)
            if not td.header_axes or not isinstance(td.header_axes, list) or len(td.header_axes) != num_header_rows:
                raise DirectExtractValidationError(
                    f"表ブロック #{b.order} (table_id='{td.table_id}') の header_axes の数 ({len(td.header_axes) if isinstance(td.header_axes, list) else '非配列'}) が段数 ({num_header_rows}) と不一致です。"
                )
            for axis_idx, axis in enumerate(td.header_axes):
                if axis is None or not str(axis).strip():
                    raise DirectExtractValidationError(
                        f"表ブロック #{b.order} (table_id='{td.table_id}') の header_axes[{axis_idx}] が空文字です（空文字禁止）。"
                    )

            first_header_row = td.header_rows[0]
            if not isinstance(first_header_row, list) or len(first_header_row) == 0:
                raise DirectExtractValidationError(
                    f"表ブロック #{b.order} (table_id='{td.table_id}') の header_rows[0] が空または配列ではありません。"
                )
            header_col_count = len(first_header_row)
            for h_idx, h_row in enumerate(td.header_rows):
                if not isinstance(h_row, list) or len(h_row) != header_col_count:
                    raise DirectExtractValidationError(
                        f"表ブロック #{b.order} (table_id='{td.table_id}') の header_rows[{h_idx}] の列数 ({len(h_row) if isinstance(h_row, list) else '非配列'}) が段 #0 の列数 ({header_col_count}) と不一致です。"
                    )

            if type(td.row_label_column_count) is not int or td.row_label_column_count < 0 or td.row_label_column_count >= header_col_count:
                raise DirectExtractValidationError(
                    f"表ブロック #{b.order} (table_id='{td.table_id}') の row_label_column_count ({td.row_label_column_count}) は 0 以上かつ列数 ({header_col_count}) 未満でなければなりません。"
                )

            for row_idx, row in enumerate(td.data_rows):
                if not isinstance(row, list) or len(row) != header_col_count:
                    raise DirectExtractValidationError(
                        f"表ブロック #{b.order} (table_id='{td.table_id}') の行 #{row_idx + 1} の列数 ({len(row) if isinstance(row, list) else '非配列'}) が header_rows の列数 ({header_col_count}) と不一致です。"
                    )
            if not td.description or not str(td.description).strip():
                raise DirectExtractValidationError(
                    f"表ブロック #{b.order} (table_id='{td.table_id}') の description が空です（スキーマ上必須です）。"
                )
        else:
            if not b.text_content or not str(b.text_content).strip():
                raise DirectExtractValidationError(
                    f"ブロック #{b.order} は block_type='{b.block_type}' ですが text_content が空です。"
                )
            if b.table_data is not None:
                raise DirectExtractValidationError(
                    f"ブロック #{b.order} は block_type='{b.block_type}' ですが table_data が設定されています (null であるべきです)。"
                )


# ---------------------------------------------------------------------------
# 直接抽出用プロンプト定義
# ---------------------------------------------------------------------------

_DIRECT_EXTRACT_PROMPT = """あなたは極めて精密なOCRおよび文書構造化システムです。
提供された画像（ページ画像）からすべてのブロックを印刷物の自然な読み順（Reading Order）の1本の配列（blocks）として抽出し、JSONスキーマに従って出力してください。
「順番は崩さず、テキストブロックはテキストブロック、表は表としてちゃんと読み取る」ことを絶対条件とします。文字の無い写真・イラスト・図についても、内容を把握して説明ブロックとして抽出してください。

【最重要ルール】
1. **読み順の維持（Reading Order）**:
   - ページ全体を上から下、左から右（段組みや囲み枠の文脈を考慮）へ、人間が読む本来の順序でブロックを並べてください。
   - 決して「文章」と「表」でページを2つに分割してはいけません。表の直前の説明文、表、表の直後の注記、次のセクションの見出しの順序をそのまま保持してください。
   - `order` は 1 から始まる連番（1, 2, 3, ...）で重複や欠番を絶対に作らないでください。

2. **ブロック種別（block_type）**:
   - `heading`: 見出し（大・中・小見出し）
   - `paragraph`: 一般の地の文・段落
   - `bullet_list`: 箇条書きリスト
   - `table`: 表本体
   - `figure_caption`: 図・写真・イラストのキャプション（印刷された説明文・題名）
   - `figure_description`: 写真・イラスト・図・手書きの図の内容の説明（印刷されたキャプションとは別物）
   - `footnote`: ページ下部の注釈・脚注
   - ※枠線（囲み枠・コラム）自体はブロックにせず、`container` 属性で表現してください。

3. **正規化座標（bbox）**:
   - 0〜1000の整数で [ymin, xmin, ymax, xmax] を指定してください。
   - 必ず ymin < ymax かつ xmin < xmax を満たす必要があります。

4. **コンテナ情報（container）**:
   - `id`: 固有のコンテナ識別子（例: 'page_main', 'box_notice', 'col_left', 'col_right'）
   - `type`: 'page_body', 'callout_box'（囲み枠）, 'column_left'（左段）, 'column_right'（右段）, 'header', 'footer'
   - `title`: 枠線の内外に書かれた枠タイトル（例: '4月の予定', 'PTAからのお知らせ'）
   - `bbox`: 枠線全体の [ymin, xmin, ymax, xmax]（0〜1000整数、なければ null）

5. **表との文脈的関係（table_relation）**:
   - `role`:
     - 'table_body': 表本体そのもの
     - 'pre_table_description': 表の直前にある導入文・説明（例: '以下の通り集金を...'）
     - 'table_heading': 表の見出し・タイトル
     - 'table_footnote': 表の直後にある注記・脚注（例: '※日程は変更の可能性があります'）
     - 'none': 表とは無関係な一般テキスト
   - `target_table_id`: 対象の表ID（例: 'T1'）。none の場合は null。

6. **写真・イラスト・図の説明（figure_description）**:
   - ページ内の写真・イラスト・図・手書きの図などの領域ごとに、その内容を説明する `figure_description` ブロックを読み順の位置に出してください（既存の印刷されたキャプションである `figure_caption` とは別物です）。
   - 説明には、見て分かる場面・行事・人数や様子・写っている物・写真の中で読める文字（看板・掲示・黒板など）を書き、見えないことを推測で書かないでください。
   - ページ全体が文字のない写真の場合でも、決して blocks を空にせず、必ずこの `figure_description` ブロックを出力してください。
   - `text_content` に説明文を入れ、`table_data` は null にしてください。

7. **テキストブロック（text_content）**:
   - `block_type` が `table` 以外の場合（`figure_description` を含む）、Markdownテキスト（見出しの#や箇条書きの-を含む、または写真・図の説明文）を `text_content` に記述し、`table_data` は null にしてください。
   - 漢字のふりがな（ルビ）は完全に除去してください。

8. **表ブロック（table_data）**:
   - `block_type` が `table` の場合、詳細構造を `table_data` に記述し、`text_content` は null にしてください。
   - `row_label_column_count`: 左端から何列が行見出し（行ラベル・インデックス列、例: 時間割の第1列「日付」や項目の列）かを表す列数（0以上の整数）。行見出しの列がない場合は 0、第1列が行見出しの場合は 1 を指定してください。全列数未満でなければなりません。
   - `header_axes`: 見出しの各段が何を表すかの名前の配列（例: ['クラス', '時限']、1段見出しなら ['項目'] など）。`header_rows` の段数と同数の要素を持ち、空文字は絶対に含めないでください。これはデータ列の各軸の名前です（行見出し列のための名前ではありません）。
   - `header_rows`: 見出し行の配列（上の段から順に1段以上）。
     - 時間割のような多段見出し（例: 1段目「6A」「6B」が横結合、2段目「朝」「1」〜「6」）の場合、上の段から順に各段を行配列として出力してください。
     - 横結合された見出しセルは、その結合範囲のすべての列に値を展開（コピー）してください（例: 「6A」が6A朝〜6A6の全列に及ぶなら、1段目の該当列すべてに「6A」を入れる）。
     - 縦結合された見出しセルも、その結合範囲のすべての段に値を展開（コピー）してください。
     - 第1列ヘッダーを 'header' と書く旧ルールは廃止されました。画像上の実際の見出し（例: '日付'）をそのまま書いてください。画像上で第1列に見出しが無い場合は空文字 "" としてください（推測で作らない）。
     - 各段の列数はすべての段で同一であり、`data_rows` の全行の列数とも完全一致させてください。
   - `data_rows`: 1データ1行。セル内改行は解体して複数行に分け、空セルは空文字 "" を入れて `header_rows` と全行で列数を完全一致させてください。
   - 結合セルは完全展開（結合範囲の全行・全列に値をコピー）してください。
   - ふりがな（ルビ）は除去してください。
"""


def get_direct_extract_prompt(page_index: Optional[int] = None) -> str:
    """指定ページ番号を明記した直接抽出プロンプト文字列を返す。"""
    if page_index is not None:
        prefix = (
            f"【対象ページ】\n"
            f"このページの page_index は {page_index} です。"
            f"出力 JSON のルートにある page_index フィールドには必ず整数 {page_index} を正確に設定してください。\n\n"
        )
        return prefix + _DIRECT_EXTRACT_PROMPT
    return _DIRECT_EXTRACT_PROMPT


# ---------------------------------------------------------------------------
# 画像化ヘルパー（fitz.Matrix(3, 3) による高解像度 PNG 生成）
# ---------------------------------------------------------------------------

def render_page_to_png_bytes(doc: fitz.Document, page_index: int) -> bytes:
    """指定ページの PNG 画像バイト列を高解像度（Matrix 3x3）でレンダリングする。"""
    page = doc[page_index]
    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))
    return pix.tobytes("png")


# ---------------------------------------------------------------------------
# Markdown / YAML 合成ロジック
# ---------------------------------------------------------------------------

def _cell_to_yaml_item(cell: str) -> str:
    """YAML cells リストアイテム（4スペースインデント）。安全にエスケープ処理を行う。"""
    dumped = _yaml.safe_dump([cell], allow_unicode=True, width=float('inf')).strip()
    return '    ' + dumped


def _infer_table_semantics(header_rows: List[List[str]], rows: List[List[str]]) -> Dict[str, Any]:
    """表内容から table_semantics を推定する。"""
    financial_kw = {'収入', '支出', '決算', '予算', '繰越', '合計', '収支', '会費'}
    all_text = ' '.join(str(c) for h_row in header_rows for c in h_row) + ' ' + ' '.join(
        str(c) for row in rows for c in row
    )
    if any(kw in all_text for kw in financial_kw):
        return {
            'type': 'financial_report',
            'type_ja': '財務諸表',
            'target': None,
            'scope': None,
            'date_range': None,
            'confidence': 0.9,
        }
    return {
        'type': 'unknown',
        'type_ja': None,
        'target': None,
        'scope': None,
        'date_range': None,
        'confidence': 0.5,
    }


def _generate_tables_yaml(tables_data: List[Dict[str, Any]]) -> str:
    """tables リストから YAML テキストを生成する。"""
    tables_list: List[Dict[str, Any]] = []
    for tbl in tables_data:
        tbl_id = tbl['table_id']
        rows = tbl['data_rows']
        header_rows = tbl['header_rows']
        header_axes = tbl['header_axes']
        if not header_rows:
            raise ValueError(f"契約違反: table {tbl_id} に header_rows がありません")
        if 'row_label_column_count' not in tbl or tbl['row_label_column_count'] is None:
            raise ValueError(f"契約違反: table {tbl_id} に row_label_column_count がありません")

        sem = _infer_table_semantics(header_rows, rows)
        description = str(tbl.get('description') or '')
        caption = str(tbl.get('caption') or '').strip()

        col_count = len(header_rows[0])
        row_label_col_count = tbl['row_label_column_count']
        columns: List[Dict[str, Any]] = []
        for c in range(col_count):
            col_dict: Dict[str, Any] = {'index': c}
            if c < row_label_col_count:
                rl_vals: List[str] = []
                for h_row in header_rows:
                    v = str(h_row[c]).strip()
                    if v and (not rl_vals or rl_vals[-1] != v):
                        rl_vals.append(v)
                col_dict['row_label'] = " / ".join(rl_vals)
            else:
                axes_dict: Dict[str, str] = {}
                for r_idx, axis_name in enumerate(header_axes):
                    val = header_rows[r_idx][c]
                    axes_dict[str(axis_name)] = str(val)
                col_dict['axes'] = axes_dict
            columns.append(col_dict)

        tbl_dict: Dict[str, Any] = {
            'table_id': tbl_id,
        }
        if caption:
            tbl_dict['caption'] = caption
        tbl_dict['description'] = description
        tbl_dict['table_semantics'] = {
            'type': sem['type'],
            'type_ja': sem.get('type_ja') or None,
            'target': None,
            'scope': None,
            'date_range': None,
            'confidence': sem['confidence'],
        }
        tbl_dict['header_row_indices'] = list(range(len(header_rows)))
        tbl_dict['header_axes'] = [str(axis) for axis in header_axes]
        tbl_dict['header_rows'] = [
            {
                'header_row': h_idx,
                'cells': [str(cell) if cell is not None else '' for cell in h_row],
            }
            for h_idx, h_row in enumerate(header_rows)
        ]
        tbl_dict['columns'] = columns
        tbl_dict['month_blocks'] = []
        tbl_dict['data_rows'] = [
            {
                'sheet_row': idx + 1,
                'cells': [str(cell) if cell is not None else '' for cell in row],
            }
            for idx, row in enumerate(rows)
        ]
        tables_list.append(tbl_dict)

    payload = {'tables': tables_list}
    return _yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=float('inf'),
    ).strip()


def _synthesize_structured_markdown_from_blocks(
    blocks: List[PageBlock]
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    """
    検証済みブロック配列から、下流システム完全互換の Markdown を決定論的に合成する。
    Returns:
        (structured_md, tables_data, ui_data_summary)
    """
    prose_parts: List[str] = []
    tables_data: List[Dict[str, Any]] = []
    html_lines: List[str] = []
    current_container_id: Optional[str] = None

    for b in sorted(blocks, key=lambda x: x.order):
        c_id = b.container.id if b.container else None
        if c_id != current_container_id:
            current_container_id = c_id
            if b.container and b.container.title:
                c_title = b.container.title.strip()
                if c_title:
                    heading_line = c_title if c_title.startswith("#") else f"## {c_title}"
                    prose_parts.append(heading_line)

        if b.block_type != "table":
            if b.text_content is None or not str(b.text_content).strip():
                raise ValueError(f"契約違反: ブロック #{b.order} (block_type='{b.block_type}') の text_content が欠損または空です")
            content = str(b.text_content).strip()
            if b.block_type == "figure_description":
                if content.startswith("[写真・図の説明]"):
                    prose_parts.append(content)
                else:
                    prose_parts.append(f"[写真・図の説明] {content}")
            else:
                prose_parts.append(content)
        else:
            td = b.table_data
            if not td:
                raise ValueError(f"契約違反: ブロック #{b.order} (block_type='table') に table_data がありません")
            raw_tid = td.table_id.strip()
            if not raw_tid.upper().startswith("B_"):
                tbl_id = f"B_{raw_tid}"
            else:
                tbl_id = raw_tid

            if not td.header_rows:
                raise ValueError(f"契約違反: table {tbl_id} に header_rows がありません")
            header_rows = td.header_rows
            if td.header_axes is None:
                raise ValueError(f"契約違反: table {tbl_id} に header_axes がありません")
            header_axes = td.header_axes
            if td.data_rows is None:
                raise ValueError(f"契約違反: table {tbl_id} に data_rows がありません")
            data_rows = td.data_rows
            caption = td.caption.strip() if td.caption else ""
            description = td.description.strip() if td.description else ""
            col_count = len(header_rows[0])

            table_lines: List[str] = [f"## {tbl_id}"]
            if caption:
                table_lines.append(f"::caption:: {caption}")
            if description:
                table_lines.append(f"::summary:: {description}")

            # Markdown 表のヘッダー（1行）: 各列について全段の値を上から順に保持し ' / ' で連結（連続して同じ値の段は1回だけ）
            if td.row_label_column_count is None:
                raise ValueError(f"契約違反: table {tbl_id} に row_label_column_count がありません")
            row_label_col_count = td.row_label_column_count
            single_headers: List[str] = []
            for c in range(col_count):
                vals: List[str] = []
                for h_row in header_rows:
                    v = str(h_row[c]).strip()
                    if v and (not vals or vals[-1] != v):
                        vals.append(v)
                single_headers.append(" / ".join(vals) if vals else "")

            # Markdown 表: 見出しを1行にし、各列の見出しを ' / ' で連結した上で区切り行を置く
            table_lines.append("| " + " | ".join(h.replace("|", "\\|") for h in single_headers) + " |")
            if single_headers:
                table_lines.append("| " + " | ".join("---" for _ in single_headers) + " |")
            for r in data_rows:
                table_lines.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")

            # HTML 表: thead に複数 tr（段数分の tr）
            thead_trs: List[str] = []
            for h_row in header_rows:
                th_cells = "".join(f"<th>{_html.escape(str(h))}</th>" for h in h_row)
                thead_trs.append(f"<tr>{th_cells}</tr>")
            thead_html = "".join(thead_trs)

            rows_html_parts: List[str] = []
            for r in data_rows:
                cells_html = "".join(f"<td>{_html.escape(str(c))}</td>" for c in r)
                rows_html_parts.append(f"<tr>{cells_html}</tr>")

            tbl_html = (
                f'<!-- table:{tbl_id} -->\n'
                f'<table class="md-embed-table"><thead>{thead_html}</thead>'
                f'<tbody>{"".join(rows_html_parts)}</tbody></table>'
            )

            prose_parts.append("\n".join(table_lines))
            html_lines.append(tbl_html)

            sem = _infer_table_semantics(header_rows, data_rows)

            columns: List[Dict[str, Any]] = []
            for c in range(col_count):
                if c < row_label_col_count:
                    rl_vals: List[str] = []
                    for h_row in header_rows:
                        v = str(h_row[c]).strip()
                        if v and (not rl_vals or rl_vals[-1] != v):
                            rl_vals.append(v)
                    columns.append({
                        "index": c,
                        "row_label": " / ".join(rl_vals),
                    })
                else:
                    axes_dict: Dict[str, str] = {}
                    for r_idx, axis_name in enumerate(header_axes):
                        val = header_rows[r_idx][c]
                        axes_dict[str(axis_name)] = str(val)
                    columns.append({
                        "index": c,
                        "axes": axes_dict,
                    })

            tables_data.append({
                "table_id": tbl_id,
                "caption": caption,
                "row_label_column_count": row_label_col_count,
                "header_axes": header_axes,
                "header_rows": header_rows,
                "columns": columns,
                "headers": single_headers,
                "data_rows": data_rows,
                "description": description,
                "table_semantics": sem,
            })

    body_md = "\n\n".join(prose_parts).strip()

    embed_parts: List[str] = ["## 表（埋め込み）", "", "<!-- dms:tables-md-embed v1 -->"]
    if tables_data:
        yaml_str = _generate_tables_yaml(tables_data)
        embed_parts += ["### `tables`（YAML・検索・LLM 向け）", "", "```yaml", yaml_str, "```"]
    if html_lines:
        embed_parts += ["", "### 表 HTML（MD に埋め込み可）", ""]
        embed_parts.extend(html_lines)

    full_md = body_md + "\n\n" + "\n".join(embed_parts)

    ui_data_summary = {
        "actions_count": 0,
        "g21_articles_count": 0,
        "g36_rebuild": [],
        "notices_count": 0,
        "sections_count": 0,
        "table_ids": [t["table_id"] for t in tables_data],
        "tables_count": len(tables_data),
        "tables_md_embed_chars": len("\n".join(embed_parts)),
        "timeline_count": 0,
    }

    return full_md, tables_data, ui_data_summary
