"""
四谷大塚 偏差値一覧表 専用ドメインハンドラ

【対象】
- 合不合判定テスト 80偏差値一覧
- 学校別偏差値ランキング表

【特徴】
- 固定ヘッダー（偏差値, 2/3, 2/4～）
- セル内に学校名+個別偏差値+フラグ（☆◇▼等）
- 行先頭が偏差値（72, 71, 70, ...）

【Ver 10.9】強制カラムアライメント
- ノイズ列（テスト、男子、合不合判定等）を除去
- 物理列 → 論理列（偏差値, 2/3, 2/4～）へ再マッピング
- ヘッダー行が汚れていてもデータ内容から列の役割を推定
"""

import re
from typing import Dict, Any, List, Optional, Tuple
from loguru import logger


class YotsuyaDomainHandler:
    """四谷大塚 偏差値一覧表 専用パーサ（Ver 11.1: 肩付き注釈精密判定）"""

    # ドメイン検出キーワード
    DOMAIN_KEYWORDS = ["四谷大塚", "合不合判定", "80偏差値", "Aライン"]

    # 論理ヘッダー（強制的にこの構造に再編）
    LOGICAL_HEADERS = ["偏差値", "2/3", "2/4～"]

    # ヘッダー検出パターン（物理ヘッダーから論理ヘッダーへのマッピング）
    HEADER_PATTERNS = [
        (r"偏差値", "偏差値"),
        (r"2\s*/\s*3(?!\s*～|~)", "2/3"),  # 2/3のみ（2/3～は除外）
        (r"2\s*/\s*4\s*[～~]|2/4以降|2\s*/\s*4", "2/4～"),  # 2/4～ または 2/4
    ]

    # ノイズ列パターン（これらを含む列は除外）
    NOISE_PATTERNS = [
        r"^テスト$",
        r"^男子$",
        r"^女子$",
        r"^合不合判定",
        r"^判定校$",
        r"^教科$",
        r"^Aライン$",
        r"^80偏差値",
        r"^\d{4}年",  # 年度表記
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

    # 肩付き注釈の判定閾値（Ver 11.1）
    SHOULDER_Y_MAX_DISTANCE = 10  # 注釈と学校名の垂直距離の最大値（px）- 厳格化
    SHOULDER_X_MIN_OVERLAP = 1    # X座標の最小重なり（px）- 緩和して確実にマッチ

    def process(
        self,
        table: Dict[str, Any],
        ref_id: str,
        table_title: str,
        raw_tokens: List[Dict] = None
    ) -> Optional[Dict[str, Any]]:
        """
        四谷大塚 偏差値一覧表の専用パース処理（Ver 12.0: 住所録方式）

        【設計思想】
        - 「ヘッダー（箱）を先に用意してデータを詰める」のではない
        - 「データ（学校名）に住所（偏差値・日程）を振る」だけ
        - 同じ住所に何校いても問題ない（フラットなリスト出力）
        """
        cells = table.get('cells', [])
        raw_tokens = raw_tokens or []

        if not cells:
            logger.warning(f"[Yotsuya] cells が空: {ref_id}")
            return None

        # ============================================
        # Step 1: 住所ラベルの辞書を構築
        # ============================================
        # 偏差値ラベル: row番号 → 偏差値（2桁数字）
        # 日程ラベル: col番号 → 日程（M/D形式）
        row_deviation_labels = {}  # {row: "70"}
        col_date_labels = {}       # {col: "2/3"}
        data_cells = []            # 学校名などの実体データ

        for cell in cells:
            text = cell.get('text', '').strip()
            if not text:
                continue

            r = cell.get('row')
            c = cell.get('col')
            bbox = cell.get('bbox', [0, 0, 0, 0])
            x = bbox[0] if bbox else 0

            # 偏差値ラベルの特定（2桁数字、通常は最左列 col=0 付近）
            if re.match(r'^\d{2}$', text) and c is not None and c <= 1:
                try:
                    deviation = int(text)
                    if 30 <= deviation <= 80:
                        row_deviation_labels[r] = text
                        continue
                except ValueError:
                    pass

            # 日程ラベルの特定（M/D形式、通常は最上行 row=0 付近）
            if re.match(r'^\d{1,2}/\d{1,2}', text) and r is not None and r <= 2:
                col_date_labels[c] = text
                continue

            # ノイズ判定
            is_noise = False
            for pattern in self.NOISE_PATTERNS:
                if re.search(pattern, text):
                    is_noise = True
                    break
            if is_noise:
                continue

            # 学校名候補（1文字より長いテキスト）
            if len(text) > 1:
                data_cells.append({
                    'text': text,
                    'row': r,
                    'col': c,
                    'bbox': bbox,
                    'x': x
                })

        logger.info(f"[Yotsuya] 住所ラベル: 偏差値={len(row_deviation_labels)}行, 日程={len(col_date_labels)}列")
        logger.debug(f"[Yotsuya] 偏差値ラベル: {row_deviation_labels}")
        logger.debug(f"[Yotsuya] 日程ラベル: {col_date_labels}")

        # ============================================
        # Step 2: E8トークンから肩付き日付注釈インデックスを構築
        # ============================================
        date_annotations = self._build_date_annotation_index(raw_tokens)
        logger.info(f"[Yotsuya] 肩付き日付注釈: {len(date_annotations)}個")

        # ============================================
        # Step 3: 各データセルに住所を振る（住所録方式）
        # ============================================
        entities = []

        for cell in data_cells:
            text = cell['text']
            r = cell['row']
            c = cell['col']
            bbox = cell['bbox']

            # --- 住所A: 偏差値（行ラベルから取得） ---
            base_deviation = row_deviation_labels.get(r)

            # --- 住所B: 日程（列ラベルから取得） ---
            base_date = col_date_labels.get(c)

            # --- 住所の上書きA: カッコ内偏差値 ---
            # "開成(72)" → 偏差値を72に上書き
            name = text
            score_match = re.search(r'\((\d{2})\)', text)
            if score_match:
                base_deviation = score_match.group(1)
                name = re.sub(r'\(\d{2}\)', '', text).strip()

            # --- 住所の上書きB: 肩付き日程 ---
            # 学校名の真上に日付があれば、それを日程として採用
            shoulder_date = self._find_shoulder_annotation(bbox, date_annotations)
            if shoulder_date:
                base_date = shoulder_date

            # フラグの分離（☆◇▼など）
            flags = []
            for flag in self.FLAGS:
                if flag in name:
                    flags.append(flag)
                    name = name.replace(flag, '').strip()

            # 学校名の分離（連結されている場合）
            school_names = self._split_school_names(name, bbox, raw_tokens)

            for school_name, school_bbox in school_names:
                if not school_name:
                    continue

                # 分離後の学校にも肩付き日程をチェック
                final_date = base_date
                if school_bbox:
                    shoulder = self._find_shoulder_annotation(school_bbox, date_annotations)
                    if shoulder:
                        final_date = shoulder

                # 表示用学校名: フラグを先頭に付加（☆開成）
                display_name = ''.join(flags) + school_name if flags else school_name

                entities.append({
                    '学校名': display_name,
                    '偏差値': base_deviation,
                    '日程': final_date,
                    # 内部メタデータ（デバッグ用、UIには非表示）
                    '_meta': {
                        'raw_name': school_name,
                        'flags': flags.copy(),
                        'bbox': school_bbox or bbox,
                        'original': text
                    }
                })

        # 偏差値でソート（降順）、同偏差値内は元の順序維持
        entities.sort(key=lambda e: int(e['偏差値']) if e['偏差値'] else 0, reverse=True)

        logger.info(f"[Yotsuya] 住所録方式で {len(entities)} 校を構造化")

        # ============================================
        # 出力1: flat_data（フラット表：エンティティリスト）
        # ============================================
        flat_data = [
            {
                '学校名': e['学校名'],
                '偏差値': e['偏差値'],
                '日程': e['日程']
            }
            for e in entities
        ]

        # ============================================
        # 出力2: grid_data（グリッド表：データに基づく論理構造）
        # ============================================
        grid_data = self._build_grid_data(entities)

        return {
            'ref_id': ref_id,
            'table_title': table_title,
            'table_type': 'yotsuya_hensachi',
            'schema_matched': True,
            'domain': 'yotsuya_hensachi',
            # フラット表用
            'columns': ['学校名', '偏差値', '日程'],
            'rows': flat_data,
            'flat_data': flat_data,
            'flat_columns': ['学校名', '偏差値', '日程'],
            # グリッド表用（元の表構造）
            'grid_data': grid_data,
            # 内部用
            '_entities': entities,
            'row_count': len(flat_data),
            'source': 'stage_h1_yotsuya_v12'
        }

    def _build_grid_data(self, entities: List[Dict]) -> Dict[str, Any]:
        """
        データに基づいた論理グリッドを構築

        紙の制約（書ききれない等）に縛られず、
        実際のデータに合わせた完全なグリッドを生成。

        - 行: 実際に存在する偏差値（74, 72, 71, 70, ...）
        - 列: 実際に存在する日程（2/1, 2/4, 2/5, 2/6, ...）

        Args:
            entities: 処理済みエンティティ（学校名、偏差値、日程を含む）

        Returns:
            {
                'columns': ['偏差値', '2/1', '2/4', '2/5', ...],
                'rows': [['74', '筑駒', '', '', ...], ...],
                'row_headers': ['74', '72', '71', ...],
                'col_headers': ['2/1', '2/4', '2/5', ...],
            }
        """
        # 全エンティティから実際の偏差値・日程を収集
        all_deviations = set()
        all_dates = set()

        for e in entities:
            dev = e.get('偏差値')
            date = e.get('日程')
            if dev:
                all_deviations.add(dev)
            if date:
                all_dates.add(date)

        # 行ヘッダー: 偏差値降順
        row_headers = sorted(all_deviations, key=lambda d: int(d), reverse=True)

        # 列ヘッダー: 日付順（M/D形式）
        def date_sort_key(d):
            try:
                parts = d.split('/')
                return (int(parts[0]), int(parts[1]))
            except:
                return (99, 99)

        col_headers = sorted(all_dates, key=date_sort_key)

        # マッピング
        dev_to_row = {d: i for i, d in enumerate(row_headers)}
        date_to_col = {d: i for i, d in enumerate(col_headers)}

        # グリッド初期化
        num_rows = len(row_headers)
        num_cols = len(col_headers)
        grid = [['' for _ in range(num_cols)] for _ in range(num_rows)]

        # エンティティをグリッドに配置（実際の偏差値・日程で）
        for e in entities:
            name = e.get('学校名', '')
            dev = e.get('偏差値')
            date = e.get('日程')

            if dev in dev_to_row and date in date_to_col:
                grid_row = dev_to_row[dev]
                grid_col = date_to_col[date]
                # 同じセルに複数校がある場合は改行で連結
                if grid[grid_row][grid_col]:
                    grid[grid_row][grid_col] += '\n' + name
                else:
                    grid[grid_row][grid_col] = name

        # 出力形式: 行ラベルを含む2D配列
        rows_with_labels = []
        for i, row_data in enumerate(grid):
            rows_with_labels.append([row_headers[i]] + row_data)

        return {
            'columns': ['偏差値'] + col_headers,
            'rows': rows_with_labels,
            'row_headers': row_headers,
            'col_headers': col_headers,
            'grid_only': grid,
        }

    def _split_school_names(
        self,
        name: str,
        bbox: List[float],
        raw_tokens: List[Dict]
    ) -> List[Tuple[str, Optional[List[float]]]]:
        """
        学校名を分離（マージされた名前の分離対応）

        Args:
            name: 学校名（マージされている可能性あり）
            bbox: セル全体のbbox
            raw_tokens: E8トークン（個別bbox検索用）

        Returns:
            [(学校名, bbox), ...] - 単一の場合は1要素のリスト
        """
        # まずE8トークンから個別の学校名を探す
        school_bboxes = self._find_all_school_bboxes_in_merged_name(name, raw_tokens)

        if len(school_bboxes) >= 2:
            # マージされた名前：個別に分離
            logger.debug(f"[Yotsuya] 学校名分離: '{name}' -> {[s[0] for s in school_bboxes]}")
            return school_bboxes

        # 単一の学校名：トークンからbboxを探す
        token_bbox = self._find_school_token_bbox(name, raw_tokens)
        return [(name, token_bbox or bbox)]

    def _group_cells_by_row(self, cells: List[Dict]) -> Dict[int, List[Dict]]:
        """セルをY座標でグループ化"""
        rows_by_y = {}
        for cell in cells:
            text = cell.get('text', '').strip()
            bbox = cell.get('bbox', [0, 0, 0, 0])
            y_key = int(bbox[1] / 10) * 10 if bbox else 0

            if y_key not in rows_by_y:
                rows_by_y[y_key] = []

            rows_by_y[y_key].append({
                'text': text,
                'x': bbox[0] if bbox else 0,
                'bbox': bbox
            })

        return rows_by_y

    def _detect_column_roles(
        self,
        rows_by_y: Dict[int, List[Dict]],
        sorted_y_keys: List[int]
    ) -> Dict[str, List[Tuple[float, float]]]:
        """
        列の役割を検出（X座標範囲 → 論理ヘッダー）

        Returns:
            {
                '偏差値': [(x_min, x_max), ...],  # 偏差値列のX範囲
                '2/3': [(x_min, x_max), ...],     # 2/3列のX範囲
                '2/4～': [(x_min, x_max), ...],   # 2/4～列のX範囲
                'noise': [(x_min, x_max), ...],   # ノイズ列のX範囲
            }
        """
        roles = {
            '偏差値': [],
            '2/3': [],
            '2/4～': [],
            'noise': [],
            'data': []  # 未分類のデータ列
        }

        # 全セルからX座標の分布を取得
        all_x_positions = []
        for y_key in sorted_y_keys:
            for cell in rows_by_y[y_key]:
                x = cell.get('x', 0)
                text = cell.get('text', '').strip()
                bbox = cell.get('bbox', [0, 0, 0, 0])
                x_max = bbox[2] if len(bbox) > 2 else x + 50
                all_x_positions.append({
                    'x': x,
                    'x_max': x_max,
                    'text': text
                })

        # X座標でクラスタリング（列の境界を検出）
        column_clusters = self._cluster_by_x(all_x_positions)
        logger.debug(f"[Yotsuya] 列クラスタ数: {len(column_clusters)}")

        # 各クラスタの役割を判定
        for cluster in column_clusters:
            x_min = cluster['x_min']
            x_max = cluster['x_max']
            texts = cluster['texts']

            role = self._classify_column(texts)
            if role:
                roles[role].append((x_min, x_max))
            else:
                # 未分類 → データ列として扱う
                roles['data'].append((x_min, x_max))

        # データ列を2/3と2/4～に振り分け
        self._assign_data_columns(roles)

        return roles

    def _cluster_by_x(self, positions: List[Dict]) -> List[Dict]:
        """X座標でクラスタリング（左端の位置でグループ化）"""
        if not positions:
            return []

        # X座標（左端）でソート
        sorted_pos = sorted(positions, key=lambda p: p['x'])

        # まず左端座標でグループ化（近いものをまとめる）
        MERGE_THRESHOLD = 40  # この距離以内なら同じ列とみなす

        clusters = []
        current_cluster = {
            'x_min': sorted_pos[0]['x'],
            'x_max': sorted_pos[0]['x_max'],
            'x_center': sorted_pos[0]['x'],  # クラスタの中心（左端の平均）
            'texts': [sorted_pos[0]['text']],
            'count': 1
        }

        for pos in sorted_pos[1:]:
            # 左端同士の距離で判定
            dist = abs(pos['x'] - current_cluster['x_center'])

            if dist < MERGE_THRESHOLD:
                # 同じクラスタに追加
                current_cluster['x_max'] = max(current_cluster['x_max'], pos['x_max'])
                current_cluster['texts'].append(pos['text'])
                # 中心を更新（移動平均）
                current_cluster['count'] += 1
                current_cluster['x_center'] = (
                    (current_cluster['x_center'] * (current_cluster['count'] - 1) + pos['x'])
                    / current_cluster['count']
                )
            else:
                # 新しいクラスタを開始
                clusters.append(current_cluster)
                current_cluster = {
                    'x_min': pos['x'],
                    'x_max': pos['x_max'],
                    'x_center': pos['x'],
                    'texts': [pos['text']],
                    'count': 1
                }

        clusters.append(current_cluster)

        logger.debug(f"[Yotsuya] 列クラスタ: {[(c['x_min'], c['x_max'], len(c['texts'])) for c in clusters]}")

        return clusters

    def _classify_column(self, texts: List[str]) -> Optional[str]:
        """列内のテキストから役割を分類"""
        # 空でないテキストのみ
        non_empty = [t for t in texts if t.strip()]
        if not non_empty:
            return 'noise'

        # ノイズ判定
        noise_count = 0
        for text in non_empty:
            for pattern in self.NOISE_PATTERNS:
                if re.search(pattern, text):
                    noise_count += 1
                    break

        if noise_count > len(non_empty) * 0.5:
            return 'noise'

        # 偏差値列判定（2桁数字が多い）
        deviation_count = sum(1 for t in non_empty if re.match(r'^\d{2}$', t.strip()))
        if deviation_count > len(non_empty) * 0.3:
            return '偏差値'

        # ヘッダーパターンで判定
        for text in non_empty:
            for pattern, role in self.HEADER_PATTERNS:
                if re.search(pattern, text):
                    if role != '偏差値':  # 偏差値は上で処理済み
                        return role

        # 学校名が含まれていればデータ列
        school_pattern = r'[学院校塾]|\d{1,2}$'
        school_count = sum(1 for t in non_empty if re.search(school_pattern, t))
        if school_count > 0:
            return None  # Noneはdata列として扱う

        return 'noise'

    def _assign_data_columns(self, roles: Dict[str, List[Tuple[float, float]]]):
        """未分類のデータ列を2/3と2/4～に振り分け"""
        data_columns = roles.get('data', [])
        if not data_columns:
            return

        # X座標でソート
        sorted_data = sorted(data_columns, key=lambda r: r[0])

        # 偏差値列のX範囲を取得
        deviation_ranges = roles.get('偏差値', [])
        if deviation_ranges:
            # 最も左の偏差値列の右端を基準にする
            left_deviation_max = min(r[1] for r in deviation_ranges)
            # 最も右の偏差値列の左端を基準にする
            right_deviation_min = max(r[0] for r in deviation_ranges)
        else:
            left_deviation_max = 0
            right_deviation_min = float('inf')

        # データ列を左右に振り分け
        for x_min, x_max in sorted_data:
            center_x = (x_min + x_max) / 2

            # 左側の偏差値列より右、右側の偏差値列より左にあるデータ列
            if center_x > left_deviation_max:
                # 中間点を計算（左偏差値と右偏差値の間）
                if right_deviation_min < float('inf'):
                    midpoint = (left_deviation_max + right_deviation_min) / 2
                    if center_x < midpoint:
                        roles['2/3'].append((x_min, x_max))
                    else:
                        roles['2/4～'].append((x_min, x_max))
                else:
                    # 右偏差値列がない場合は2/3に振り分け
                    roles['2/3'].append((x_min, x_max))

        # dataリストをクリア
        roles['data'] = []

    def _extract_deviation_from_row(
        self,
        row_cells: List[Dict],
        column_roles: Dict
    ) -> Optional[int]:
        """行から偏差値を抽出"""
        deviation_ranges = column_roles.get('偏差値', [])

        for cell in row_cells:
            x = cell.get('x', 0)
            text = cell.get('text', '').strip()

            # 偏差値列の範囲内か確認
            in_deviation_col = any(
                x_min <= x <= x_max + 20
                for x_min, x_max in deviation_ranges
            )

            if in_deviation_col and re.match(r'^\d{2}$', text):
                return int(text)

        # 偏差値列が検出されていない場合、最初の2桁数字を使う
        if not deviation_ranges:
            for cell in row_cells:
                text = cell.get('text', '').strip()
                if re.match(r'^\d{2}$', text):
                    val = int(text)
                    if 30 <= val <= 80:  # 妥当な偏差値範囲
                        return val

        return None

    def _build_date_annotation_index(self, raw_tokens: List[Dict]) -> List[Dict]:
        """
        E8トークンから日付注釈のインデックスを構築

        Returns:
            [{'date': '2/5', 'bbox': [x0, y0, x1, y1]}, ...]
        """
        annotations = []
        for token in raw_tokens:
            text = token.get('text', '').strip()
            bbox = token.get('bbox', [])

            # M/D形式の日付のみ
            if re.match(r'^\d{1,2}/\d{1,2}$', text) and len(bbox) >= 4:
                annotations.append({
                    'date': text,
                    'bbox': bbox,
                    'x_min': bbox[0],
                    'x_max': bbox[2],
                    'y_min': bbox[1],
                    'y_max': bbox[3]
                })

        return annotations

    def _extract_date_from_deviation_column(
        self,
        row_cells: List[Dict],
        column_roles: Dict
    ) -> Optional[str]:
        """偏差値列にある日付を抽出（行全体に適用する日付）"""
        deviation_ranges = column_roles.get('偏差値', [])

        for cell in row_cells:
            x = cell.get('x', 0)
            text = cell.get('text', '').strip()

            # 偏差値列の範囲内か確認
            in_deviation_col = any(
                x_min <= x <= x_max + 20
                for x_min, x_max in deviation_ranges
            )

            if in_deviation_col and re.match(r'^\d{1,2}/\d{1,2}$', text):
                return text

        return None

    def _find_shoulder_annotation(
        self,
        school_bbox: List[float],
        date_annotations: List[Dict]
    ) -> Optional[str]:
        """
        学校名の真上にある肩付き日付注釈を検索

        Args:
            school_bbox: 学校名のbbox [x0, y0, x1, y1]
            date_annotations: 日付注釈リスト

        Returns:
            マッチした日付文字列、またはNone
        """
        if not school_bbox or len(school_bbox) < 4:
            return None

        school_x_min, school_y_min, school_x_max, school_y_max = school_bbox

        for ann in date_annotations:
            ann_x_min = ann['x_min']
            ann_x_max = ann['x_max']
            ann_y_max = ann['y_max']  # 注釈の下端

            # 垂直距離チェック: 注釈の下端が学校名の上端より上で、かつ近い
            y_distance = school_y_min - ann_y_max
            if y_distance < 0 or y_distance > self.SHOULDER_Y_MAX_DISTANCE:
                continue

            # X座標の重なりチェック
            overlap_start = max(school_x_min, ann_x_min)
            overlap_end = min(school_x_max, ann_x_max)
            x_overlap = overlap_end - overlap_start

            if x_overlap >= self.SHOULDER_X_MIN_OVERLAP:
                logger.debug(
                    f"[Yotsuya] 肩付き注釈マッチ: {ann['date']} -> "
                    f"Y距離={y_distance:.0f}px, X重なり={x_overlap:.0f}px"
                )
                return ann['date']

        return None

    def _find_school_token_bbox(
        self,
        school_name: str,
        raw_tokens: List[Dict]
    ) -> Optional[List[float]]:
        """学校名に対応するE8トークンのbboxを検索"""
        # 完全一致を優先
        for token in raw_tokens:
            if token.get('text', '').strip() == school_name:
                return token.get('bbox')

        # 部分一致（学校名がトークン内に含まれる）
        for token in raw_tokens:
            if school_name in token.get('text', ''):
                return token.get('bbox')

        # 逆部分一致（トークンが学校名内に含まれる）- マージされた名前対応
        for token in raw_tokens:
            token_text = token.get('text', '').strip()
            if len(token_text) >= 2 and token_text in school_name:
                return token.get('bbox')

        return None

    def _find_all_school_bboxes_in_merged_name(
        self,
        merged_name: str,
        raw_tokens: List[Dict]
    ) -> List[Tuple[str, List[float]]]:
        """
        マージされた学校名から個別の学校とbboxを抽出

        Args:
            merged_name: マージされた学校名（例: "芝2 広尾小石川ISG4"）
            raw_tokens: E8トークンリスト

        Returns:
            [(学校名, bbox), ...]
        """
        results = []

        # 学校名らしいトークンを探す（学・院・高・中・ISG等を含む）
        school_patterns = [
            r'[学院校塾]',
            r'ISG\d',
            r'[早慶開麻芝駒桜栄渋谷広尾聖光]\d?$'
        ]

        for token in raw_tokens:
            token_text = token.get('text', '').strip()
            bbox = token.get('bbox')

            if not bbox or len(bbox) < 4:
                continue

            # トークンがマージ名に含まれているか
            if token_text in merged_name:
                # 学校名らしいかチェック
                is_school = False
                for pattern in school_patterns:
                    if re.search(pattern, token_text):
                        is_school = True
                        break

                if is_school:
                    results.append((token_text, bbox))

        return results

    def _extract_schools_with_annotations(
        self,
        row_cells: List[Dict],
        column_roles: Dict,
        current_deviation: int,
        inherited_date: Optional[str],
        date_annotations: List[Dict],
        raw_tokens: List[Dict]
    ) -> List[Dict]:
        """学校データを抽出（肩付き注釈の精密判定付き）"""
        schools = []

        for cell in row_cells:
            x = cell.get('x', 0)
            text = cell.get('text', '').strip()

            if not text:
                continue

            # ノイズ判定
            is_noise = False
            for pattern in self.NOISE_PATTERNS:
                if re.search(pattern, text):
                    is_noise = True
                    break

            if is_noise:
                continue

            # 単独の数字（偏差値列）はスキップ
            if re.match(r'^\d{1,2}$', text):
                continue

            # 日付のみのセルはスキップ
            if re.match(r'^\d{1,2}/\d{1,2}$', text):
                continue

            # 列の役割を特定
            column_name = self._get_column_role(x, column_roles)

            # ノイズ列はスキップ
            if column_name == 'noise' or column_name == '偏差値':
                continue

            # セル内データをパース
            parsed = self._parse_cell(text)
            if parsed:
                parsed['column'] = column_name

                # 肩付き注釈の精密判定
                school_name = parsed.get('name', '')

                # まずマージ名かどうかをチェック（複数の学校トークンが含まれるか）
                school_bboxes = self._find_all_school_bboxes_in_merged_name(
                    school_name, raw_tokens
                )

                if len(school_bboxes) >= 2:
                    # マージされた学校名：個別に分離して出力
                    logger.info(
                        f"[Yotsuya] マージ名検出、分離出力: {school_name} -> "
                        f"{[s[0] for s in school_bboxes]}"
                    )

                    for sub_name, sub_bbox in school_bboxes:
                        shoulder_date = self._find_shoulder_annotation(
                            sub_bbox, date_annotations
                        )

                        # 個別エントリを作成
                        sub_entry = {
                            'raw_text': sub_name,
                            'name': sub_name,
                            'individual_score': None,  # 分離後は個別スコア不明
                            'test_date': shoulder_date or inherited_date,
                            'flags': parsed.get('flags', []).copy(),
                            'column': column_name,
                            '_split_from': school_name,  # 分離元を記録
                        }

                        if shoulder_date:
                            logger.debug(
                                f"[Yotsuya] 分離: {sub_name} <- 肩付き {shoulder_date}"
                            )
                        else:
                            logger.debug(
                                f"[Yotsuya] 分離: {sub_name} <- 継承日付 {inherited_date}"
                            )

                        schools.append(sub_entry)

                    # マージ名自体は出力しない（continueで次のセルへ）
                    continue

                elif len(school_bboxes) == 1:
                    # 単一の学校名
                    school_bbox = school_bboxes[0][1]
                    shoulder_date = self._find_shoulder_annotation(
                        school_bbox, date_annotations
                    )
                    if shoulder_date:
                        parsed['test_date'] = shoulder_date
                        logger.debug(
                            f"[Yotsuya] 肩付き適用: {school_name} <- {shoulder_date}"
                        )
                    elif parsed.get('test_date') is None and inherited_date:
                        parsed['test_date'] = inherited_date

                else:
                    # トークンが見つからない場合は継承日付を適用
                    if parsed.get('test_date') is None and inherited_date:
                        parsed['test_date'] = inherited_date

                schools.append(parsed)

        return schools

    def _extract_date_from_row(self, row_cells: List[Dict]) -> Optional[str]:
        """行内の独立した日付セルを検出（後方互換用）"""
        for cell in row_cells:
            text = cell.get('text', '').strip()
            # M/D形式の日付のみ
            if re.match(r'^\d{1,2}/\d{1,2}$', text):
                return text
        return None

    def _extract_schools_from_row(
        self,
        row_cells: List[Dict],
        column_roles: Dict,
        current_deviation: int,
        inherited_date: Optional[str] = None
    ) -> List[Dict]:
        """行から学校データを抽出（日付継承対応）"""
        schools = []

        for cell in row_cells:
            x = cell.get('x', 0)
            text = cell.get('text', '').strip()

            if not text:
                continue

            # ノイズ判定
            is_noise = False
            for pattern in self.NOISE_PATTERNS:
                if re.search(pattern, text):
                    is_noise = True
                    break

            if is_noise:
                continue

            # 単独の数字（偏差値列）はスキップ
            if re.match(r'^\d{1,2}$', text):
                continue

            # 日付のみのセルはスキップ（既に_extract_date_from_rowで処理済み）
            if re.match(r'^\d{1,2}/\d{1,2}$', text):
                continue

            # 列の役割を特定
            column_name = self._get_column_role(x, column_roles)

            # ノイズ列はスキップ
            if column_name == 'noise' or column_name == '偏差値':
                continue

            # セル内データをパース
            parsed = self._parse_cell(text)
            if parsed:
                parsed['column'] = column_name
                # 日付継承：セル内に日付がなければ継承日付を適用
                if parsed.get('test_date') is None and inherited_date:
                    parsed['test_date'] = inherited_date
                schools.append(parsed)

        return schools

    def _get_column_role(self, x: float, column_roles: Dict) -> str:
        """X座標から列の役割を取得"""
        TOLERANCE = 30

        for role in ['2/3', '2/4～']:
            for x_min, x_max in column_roles.get(role, []):
                if x_min - TOLERANCE <= x <= x_max + TOLERANCE:
                    return role

        # 偏差値列チェック
        for x_min, x_max in column_roles.get('偏差値', []):
            if x_min - TOLERANCE <= x <= x_max + TOLERANCE:
                return '偏差値'

        # ノイズ列チェック
        for x_min, x_max in column_roles.get('noise', []):
            if x_min - TOLERANCE <= x <= x_max + TOLERANCE:
                return 'noise'

        # 未分類 → 位置で推定
        all_ranges = []
        for role in ['2/3', '2/4～']:
            for r in column_roles.get(role, []):
                all_ranges.append((r[0], r[1], role))

        if all_ranges:
            # 最も近い範囲を探す
            min_dist = float('inf')
            closest_role = '2/3'
            for x_min, x_max, role in all_ranges:
                center = (x_min + x_max) / 2
                dist = abs(x - center)
                if dist < min_dist:
                    min_dist = dist
                    closest_role = role
            return closest_role

        return '2/3'  # デフォルト

    def _parse_cell(self, cell_text: str) -> Optional[Dict[str, Any]]:
        """
        セル内データのパース

        パターンA: "筑波大駒場 74" → name + individual_score
        パターンB: "2/5 早稲田2" → test_date + name
        パターンC: "☆開成" → flags + name
        """
        if not cell_text:
            return None

        result = {
            'raw_text': cell_text,
            'name': cell_text,
            'individual_score': None,
            'test_date': None,
            'flags': []
        }

        working_text = cell_text

        # パターンC: 記号フラグの抽出
        for flag in self.FLAGS:
            if flag in working_text:
                result['flags'].append(flag)
                working_text = working_text.replace(flag, '').strip()

        # パターンB: 日付プレフィックスの抽出 "2/5 早稲田2"
        date_match = re.match(r'^(\d{1,2}/\d{1,2})\s+(.+)$', working_text)
        if date_match:
            result['test_date'] = date_match.group(1)
            working_text = date_match.group(2)

        # パターンA: 末尾の数値（偏差値）の抽出 "筑波大駒場 74"
        score_match = re.match(r'^(.+?)\s+(\d{2})$', working_text)
        if score_match:
            result['name'] = score_match.group(1).strip()
            result['individual_score'] = int(score_match.group(2))
        else:
            # 括弧付きの数値も対応 "開成(72)"
            score_match2 = re.match(r'^(.+?)\s*\((\d{2})\)$', working_text)
            if score_match2:
                result['name'] = score_match2.group(1).strip()
                result['individual_score'] = int(score_match2.group(2))
            else:
                result['name'] = working_text

        # 名前が空または数字のみならスキップ
        if not result['name'] or re.match(r'^\d+$', result['name']):
            return None

        return result
