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
        四谷大塚 偏差値一覧表の専用パース処理（Ver 11.1: 肩付き注釈精密判定）
        """
        cells = table.get('cells', [])
        raw_tokens = raw_tokens or []

        if not cells:
            logger.warning(f"[Yotsuya] cells が空: {ref_id}")
            return None

        # Step 1: セルをY座標（行）でグループ化
        rows_by_y = self._group_cells_by_row(cells)
        sorted_y_keys = sorted(rows_by_y.keys())
        if not sorted_y_keys:
            return None

        # Step 2: 列の役割を推定（強制アライメント）
        column_roles = self._detect_column_roles(rows_by_y, sorted_y_keys)
        logger.info(f"[Yotsuya] 列役割検出: {column_roles}")

        # Step 3: E8トークンから日付注釈インデックスを構築
        date_annotations = self._build_date_annotation_index(raw_tokens)
        logger.info(f"[Yotsuya] 日付注釈検出: {len(date_annotations)}個")

        # Step 4: データ行の処理
        structured_rows = []
        current_deviation = None
        current_test_date = None  # 行レベルの日付継承用（偏差値列に記載された日付）

        for y_key in sorted_y_keys:
            row_cells = sorted(rows_by_y[y_key], key=lambda c: c.get('x', 0))
            if not row_cells:
                continue

            # 偏差値列を探す
            deviation_value = self._extract_deviation_from_row(row_cells, column_roles)

            if deviation_value is not None:
                current_deviation = deviation_value

            if current_deviation is None:
                continue

            # 偏差値列にある日付のみ行全体に継承（肩付き注釈は個別処理）
            deviation_col_date = self._extract_date_from_deviation_column(
                row_cells, column_roles
            )
            if deviation_col_date:
                current_test_date = deviation_col_date
                logger.debug(f"[Yotsuya] 偏差値列日付: {current_test_date}")

            # 学校データを抽出（肩付き注釈の精密判定付き）
            schools = self._extract_schools_with_annotations(
                row_cells, column_roles, current_deviation,
                current_test_date, date_annotations, raw_tokens
            )

            if schools:
                # 同じ偏差値の既存行があればマージ
                existing_row = next(
                    (r for r in structured_rows if r['deviation'] == current_deviation),
                    None
                )
                if existing_row:
                    existing_row['schools'].extend(schools)
                else:
                    structured_rows.append({
                        'deviation': current_deviation,
                        'schools': schools
                    })

        # 偏差値でソート（降順）
        structured_rows.sort(key=lambda r: r['deviation'], reverse=True)

        logger.info(f"[Yotsuya] 構造化完了: {len(structured_rows)}行")

        return {
            'ref_id': ref_id,
            'table_title': table_title,
            'table_type': 'yotsuya_hensachi',
            'schema_matched': True,
            'domain': 'yotsuya_hensachi',
            'columns': self.LOGICAL_HEADERS,
            'rows': structured_rows,
            'row_count': len(structured_rows),
            'source': 'stage_h1_yotsuya'
        }

    def _group_cells_by_row(self, cells: List[Dict]) -> Dict[int, List[Dict]]:
        """
        セルを行でグループ化（Ver 11.2: F8/G6のrowインデックスを優先）
        """
        rows_by_key = {}
        for cell in cells:
            text = cell.get('text', '').strip()
            bbox = cell.get('bbox', [0, 0, 0, 0])

            # F8/G6のrowインデックスを優先
            row_key = cell.get('row')
            if row_key is None:
                # フォールバック：座標から計算（非推奨パス）
                row_key = int(bbox[1] / 10) * 10 if bbox else 0

            col_idx = cell.get('col')  # 列インデックスも保持

            if row_key not in rows_by_key:
                rows_by_key[row_key] = []

            rows_by_key[row_key].append({
                'text': text,
                'x': bbox[0] if bbox else 0,
                'col': col_idx,
                'bbox': bbox
            })

        return rows_by_key

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
