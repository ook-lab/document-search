"""
日付抽出ユーティリティ

本文からすべての日付パターンを抽出し、正規化します。
日付は検索において最重要項目であるため、漏れなく抽出することが重要です。
"""
import re
from typing import List, Set
from datetime import datetime, timedelta
from loguru import logger


class DateExtractor:
    """日付抽出クラス - 本文からあらゆる日付パターンを抽出"""

    def __init__(self):
        # 日付パターン（優先度順）
        self.patterns = [
            # YYYY-MM-DD, YYYY/MM/DD
            (r'\b(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\b', 'ymd'),

            # MM月DD日、M月D日
            (r'\b(\d{1,2})月(\d{1,2})日\b', 'md'),

            # MM/DD, M/D
            (r'\b(\d{1,2})/(\d{1,2})\b', 'md_slash'),

            # YYYY年MM月、YYYY年M月
            (r'\b(\d{4})年(\d{1,2})月\b', 'ym'),

            # MM月、M月
            (r'\b(\d{1,2})月\b', 'm'),

            # 相対表現（後で処理）
            (r'(明日|明後日|明々後日|今日|昨日|一昨日)', 'relative'),
            (r'(来週|再来週|先週|今週)', 'relative_week'),
            (r'(来月|再来月|先月|今月)', 'relative_month'),
        ]

    def extract_all_dates(
        self,
        text: str,
        reference_date: str = None
    ) -> List[str]:
        """
        本文からすべての日付を抽出

        Args:
            text: 抽出対象のテキスト
            reference_date: 基準日（YYYY-MM-DD形式、相対日付の計算に使用）

        Returns:
            日付のリスト（YYYY-MM-DD形式、重複なし、ソート済み）
        """
        if not text:
            return []

        dates: Set[str] = set()

        # 基準日を設定（指定がない場合は今日）
        if reference_date:
            try:
                base_date = datetime.strptime(reference_date, '%Y-%m-%d')
            except:
                base_date = datetime.now()
        else:
            base_date = datetime.now()

        current_year = base_date.year
        current_month = base_date.month

        # 各パターンで抽出
        for pattern, date_type in self.patterns:
            matches = re.finditer(pattern, text)

            for match in matches:
                try:
                    if date_type == 'ymd':
                        # YYYY-MM-DD
                        year = int(match.group(1))
                        month = int(match.group(2))
                        day = int(match.group(3))
                        date_str = f"{year:04d}-{month:02d}-{day:02d}"
                        dates.add(date_str)

                    elif date_type == 'md':
                        # MM月DD日 → 今年として処理
                        month = int(match.group(1))
                        day = int(match.group(2))

                        # 月日が今日より前なら来年の可能性を考慮
                        date_str = f"{current_year:04d}-{month:02d}-{day:02d}"
                        try:
                            parsed = datetime.strptime(date_str, '%Y-%m-%d')
                            dates.add(date_str)

                            # 過去の日付の場合、来年も候補に
                            if parsed < base_date - timedelta(days=30):
                                next_year_date = f"{current_year + 1:04d}-{month:02d}-{day:02d}"
                                dates.add(next_year_date)
                        except:
                            pass

                    elif date_type == 'md_slash':
                        # MM/DD → 今年として処理
                        month = int(match.group(1))
                        day = int(match.group(2))

                        # MM/DDは月/日の可能性が高い（日本語文脈）
                        if month <= 12 and day <= 31:
                            date_str = f"{current_year:04d}-{month:02d}-{day:02d}"
                            try:
                                parsed = datetime.strptime(date_str, '%Y-%m-%d')
                                dates.add(date_str)

                                # 過去の日付の場合、来年も候補に
                                if parsed < base_date - timedelta(days=30):
                                    next_year_date = f"{current_year + 1:04d}-{month:02d}-{day:02d}"
                                    dates.add(next_year_date)
                            except:
                                pass

                    elif date_type == 'ym':
                        # YYYY年MM月 → その月の1日
                        year = int(match.group(1))
                        month = int(match.group(2))
                        date_str = f"{year:04d}-{month:02d}-01"
                        dates.add(date_str)

                    elif date_type == 'm':
                        # MM月 → 今年のMM月1日
                        month = int(match.group(1))
                        date_str = f"{current_year:04d}-{month:02d}-01"
                        dates.add(date_str)

                    elif date_type == 'relative':
                        # 相対日付
                        relative_text = match.group(1)
                        relative_dates = self._parse_relative_date(relative_text, base_date)
                        dates.update(relative_dates)

                    elif date_type == 'relative_week':
                        # 週単位の相対日付
                        relative_text = match.group(1)
                        relative_dates = self._parse_relative_week(relative_text, base_date)
                        dates.update(relative_dates)

                    elif date_type == 'relative_month':
                        # 月単位の相対日付
                        relative_text = match.group(1)
                        relative_dates = self._parse_relative_month(relative_text, base_date)
                        dates.update(relative_dates)

                except Exception as e:
                    logger.debug(f"日付パースエラー: {match.group(0)} - {e}")
                    continue

        # リストに変換してソート
        date_list = sorted(list(dates))

        logger.info(f"[日付抽出] {len(date_list)}件の日付を抽出: {date_list[:5]}...")

        return date_list

    def _parse_relative_date(self, text: str, base_date: datetime) -> List[str]:
        """相対日付表現を絶対日付に変換"""
        dates = []

        mapping = {
            '今日': 0,
            '明日': 1,
            '明後日': 2,
            '明々後日': 3,
            '昨日': -1,
            '一昨日': -2,
        }

        if text in mapping:
            delta_days = mapping[text]
            target_date = base_date + timedelta(days=delta_days)
            dates.append(target_date.strftime('%Y-%m-%d'))

        return dates

    def _parse_relative_week(self, text: str, base_date: datetime) -> List[str]:
        """週単位の相対日付を絶対日付に変換"""
        dates = []

        mapping = {
            '今週': 0,
            '来週': 7,
            '再来週': 14,
            '先週': -7,
        }

        if text in mapping:
            delta_days = mapping[text]
            # その週の月曜日を基準
            target_date = base_date + timedelta(days=delta_days)
            # 月曜日〜日曜日を追加
            weekday = target_date.weekday()
            monday = target_date - timedelta(days=weekday)

            for i in range(7):
                day = monday + timedelta(days=i)
                dates.append(day.strftime('%Y-%m-%d'))

        return dates

    def _parse_relative_month(self, text: str, base_date: datetime) -> List[str]:
        """月単位の相対日付を絶対日付に変換"""
        dates = []

        mapping = {
            '今月': 0,
            '来月': 1,
            '再来月': 2,
            '先月': -1,
        }

        if text in mapping:
            delta_months = mapping[text]
            year = base_date.year
            month = base_date.month + delta_months

            # 年をまたぐ場合の調整
            while month > 12:
                month -= 12
                year += 1
            while month < 1:
                month += 12
                year -= 1

            # その月の1日を追加
            dates.append(f"{year:04d}-{month:02d}-01")

        return dates
