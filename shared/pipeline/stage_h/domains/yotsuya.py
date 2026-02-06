"""
四谷大塚 偏差値一覧表 専用ドメインハンドラ

【役割】
- ドメイン検出: キーワードマッチング
- 意味解釈: row_header=偏差値, col_header=日程
- 出力フォーマット: 学校名, 偏差値, 日程

【注意】
住所録方式の基盤ロジック（位置→ヘッダー値継承）は H1 本体が担当。
このハンドラはドメイン固有の意味解釈のみ行う。
"""

import re
from typing import Dict, Any, List, Optional
from loguru import logger


class YotsuyaDomainHandler:
    """四谷大塚 偏差値一覧表 専用パーサ（Ver 13.0: 役割分担明確化）"""

    # ドメイン検出キーワード
    DOMAIN_KEYWORDS = ["四谷大塚", "合不合判定", "80偏差値", "Aライン"]

    # ノイズパターン（データとして扱わないセル）
    NOISE_PATTERNS = [
        r"^テスト$", r"^男子$", r"^女子$", r"^合不合判定",
        r"^判定校$", r"^教科$", r"^Aライン$", r"^80偏差値",
        r"^\d{4}年", r"^偏差値$", r"^試験日$", r"^学校名$", r"^備考$",
    ]

    # 記号フラグ
    FLAGS = ["☆", "◇", "▼", "●", "□", "◎", "○", "△"]

    def __init__(self):
        pass

    def detect(self, table_title: str, unified_text: str = "") -> bool:
        """四谷大塚ドメインかどうかを判定"""
        search_text = f"{table_title} {unified_text}".lower()
        for keyword in self.DOMAIN_KEYWORDS:
            if keyword.lower() in search_text:
                return True
        return False

    def process(
        self,
        normalized_cells: List[Dict],
        table: Dict[str, Any],
        ref_id: str,
        table_title: str,
        raw_tokens: List[Dict] = None
    ) -> Optional[Dict[str, Any]]:
        """
        四谷大塚表の意味解釈

        Args:
            normalized_cells: H1が住所録方式で正規化したセル
                各セル: {text, row, col, bbox, row_header, col_header}
            table: 元のテーブルデータ
            ref_id: 参照ID
            table_title: テーブルタイトル
            raw_tokens: E8トークン（肩付き注釈用）

        Returns:
            処理済みテーブル（flat_data, grid_data含む）
        """
        if not normalized_cells:
            logger.warning(f"[Yotsuya] 正規化セルが空: {ref_id}")
            return None

        # ============================================
        # 意味解釈: 複数の入力形式に対応
        # ============================================
        seen = set()
        entities = []

        for cell in normalized_cells:
            # 形式A: H1の住所録方式（text + row_header + col_header）
            # 形式B: 構造化済み（学校名, 偏差値, 試験日 などのキー）

            # 学校名の取得
            school_name = (
                cell.get('text') or
                cell.get('学校名') or
                cell.get('school_name') or
                cell.get('name') or
                ''
            )
            school_name = str(school_name).strip()
            if not school_name:
                continue

            # ノイズ判定
            if self._is_noise(school_name):
                continue

            # 偏差値の取得
            deviation = (
                cell.get('row_header') or  # H1住所録方式
                cell.get('偏差値') or       # 構造化済み
                cell.get('deviation')
            )

            # 日程の取得
            test_date = (
                cell.get('col_header') or  # H1住所録方式
                cell.get('試験日') or       # 構造化済み
                cell.get('日程') or
                cell.get('test_date')
            )

            # 日付正規化: "2026-02-05" → "2/5", "2月5日" → "2/5"
            if test_date:
                test_date = self._normalize_date(test_date)

            # フラグ分離
            flags = []
            for flag in self.FLAGS:
                if flag in school_name:
                    flags.append(flag)
                    school_name = school_name.replace(flag, '').strip()

            # カッコ内偏差値で上書き: "開成(72)" → deviation=72
            score_match = re.search(r'\((\d{2})\)', school_name)
            if score_match:
                deviation = score_match.group(1)
                school_name = re.sub(r'\(\d{2}\)', '', school_name).strip()

            # 偏差値の妥当性チェック
            if deviation:
                try:
                    dev_int = int(deviation)
                    if not (30 <= dev_int <= 80):
                        deviation = None
                except ValueError:
                    deviation = None

            # 重複除去
            key = (school_name, deviation, test_date)
            if key in seen:
                continue
            seen.add(key)

            # 表示名: フラグを先頭に
            display_name = ''.join(flags) + school_name if flags else school_name

            entities.append({
                '学校名': display_name,
                '偏差値': deviation,
                '日程': test_date,
            })

        # 偏差値でソート（降順）
        entities.sort(
            key=lambda e: int(e['偏差値']) if e['偏差値'] else 0,
            reverse=True
        )

        logger.info(f"[Yotsuya] {len(entities)}校を構造化")

        # ============================================
        # 出力生成
        # ============================================
        flat_data = entities
        grid_data = self._build_grid(entities)

        return {
            'ref_id': ref_id,
            'table_title': table_title,
            'table_type': 'yotsuya_hensachi',
            'schema_matched': True,
            'domain': 'yotsuya_hensachi',
            'columns': ['学校名', '偏差値', '日程'],
            'rows': flat_data,
            'flat_data': flat_data,
            'flat_columns': ['学校名', '偏差値', '日程'],
            'grid_data': grid_data,
            'row_count': len(flat_data),
            'source': 'stage_h1_yotsuya_v13'
        }

    def _is_noise(self, text: str) -> bool:
        """ノイズ判定"""
        for pattern in self.NOISE_PATTERNS:
            if re.search(pattern, text):
                return True
        return False

    def _normalize_date(self, date_str: str) -> str:
        """日付を M/D 形式に正規化"""
        if not date_str:
            return date_str

        # "2026-02-05" → "2/5"
        match = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', date_str)
        if match:
            return f"{int(match.group(2))}/{int(match.group(3))}"

        # "2月5日" → "2/5"
        match = re.match(r'(\d{1,2})月(\d{1,2})', date_str)
        if match:
            return f"{int(match.group(1))}/{int(match.group(2))}"

        # "2/5" そのまま
        match = re.match(r'(\d{1,2})/(\d{1,2})', date_str)
        if match:
            return f"{int(match.group(1))}/{int(match.group(2))}"

        return date_str

    def _build_grid(self, entities: List[Dict]) -> Dict[str, Any]:
        """グリッドデータを構築"""
        # 偏差値と日程を収集
        all_deviations = set()
        all_dates = set()
        has_unknown = False

        for e in entities:
            if e['偏差値']:
                all_deviations.add(e['偏差値'])
            if e['日程']:
                all_dates.add(e['日程'])
            else:
                has_unknown = True

        # ソート
        row_headers = sorted(all_deviations, key=lambda d: int(d), reverse=True)

        def date_key(d):
            try:
                parts = d.split('/')
                return (int(parts[0]), int(parts[1]))
            except:
                return (99, 99)

        col_headers = sorted(all_dates, key=date_key)
        if has_unknown:
            col_headers.append('不明')

        # グリッド構築
        dev_to_row = {d: i for i, d in enumerate(row_headers)}
        date_to_col = {d: i for i, d in enumerate(col_headers)}

        grid = [['' for _ in col_headers] for _ in row_headers]

        for e in entities:
            dev = e['偏差値']
            date = e['日程'] or '不明'
            name = e['学校名']

            if dev in dev_to_row and date in date_to_col:
                r, c = dev_to_row[dev], date_to_col[date]
                if grid[r][c]:
                    grid[r][c] += '\n' + name
                else:
                    grid[r][c] = name

        # 行ラベル付き
        rows = [[row_headers[i]] + row for i, row in enumerate(grid)]

        return {
            'columns': ['偏差値'] + col_headers,
            'rows': rows,
            'row_headers': row_headers,
            'col_headers': col_headers,
        }
