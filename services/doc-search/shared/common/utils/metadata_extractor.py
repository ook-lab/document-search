"""
メタデータ抽出ユーティリティ

Stage Hで抽出されたメタデータから、フィルタリング用の構造化データを抽出します。
"""
from typing import Dict, Any, Optional, List
from datetime import datetime
import re
from loguru import logger


class MetadataExtractor:
    """メタデータから構造化データを抽出"""

    @staticmethod
    def extract_filtering_metadata(
        metadata: Dict[str, Any],
        document_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        メタデータからフィルタリング用の構造化データを抽出

        Args:
            metadata: Stage Hで抽出されたメタデータ
            document_date: 文書の日付（YYYY-MM-DD形式）

        Returns:
            フィルタリング用のメタデータ辞書:
            {
                "year": int,
                "month": int,
                "amount": float,
                "event_dates": List[str],
                "grade_level": str,
                "school_name": str
            }
        """
        result = {
            "year": None,
            "month": None,
            "amount": None,
            "event_dates": None,
            "grade_level": None,
            "school_name": None
        }

        # 年・月の抽出（document_dateから優先）
        if document_date:
            result["year"], result["month"] = MetadataExtractor._parse_date(document_date)
        elif metadata.get("basic_info", {}).get("issue_date"):
            # basic_info.issue_date から抽出
            result["year"], result["month"] = MetadataExtractor._parse_date(metadata["basic_info"]["issue_date"])

        # 金額の抽出（請求書、契約書など）
        if "amount" in metadata:
            result["amount"] = MetadataExtractor._parse_amount(metadata["amount"])
        elif metadata.get("basic_info", {}).get("amount"):
            result["amount"] = MetadataExtractor._parse_amount(metadata["basic_info"]["amount"])

        # イベント日付の抽出（学校行事など）
        event_dates = MetadataExtractor._extract_event_dates(metadata)
        if event_dates:
            result["event_dates"] = event_dates

        # 学年の抽出（学校関連文書）
        if metadata.get("basic_info", {}).get("grade"):
            result["grade_level"] = metadata["basic_info"]["grade"]

        # 学校名の抽出（学校関連文書）
        if metadata.get("basic_info", {}).get("school_name"):
            result["school_name"] = metadata["basic_info"]["school_name"]

        logger.debug(f"抽出されたフィルタリングメタデータ: {result}")

        return result

    @staticmethod
    def _parse_date(date_str: str) -> tuple[Optional[int], Optional[int]]:
        """
        日付文字列から年・月を抽出

        Args:
            date_str: 日付文字列（YYYY-MM-DD形式）

        Returns:
            (年, 月) のタプル
        """
        if not date_str:
            return None, None

        try:
            # YYYY-MM-DD 形式をパース
            if isinstance(date_str, str) and '-' in date_str:
                parts = date_str.split('-')
                if len(parts) >= 2:
                    year = int(parts[0])
                    month = int(parts[1])
                    return year, month
        except Exception as e:
            logger.warning(f"日付パースエラー: {date_str}, {e}")

        return None, None

    @staticmethod
    def _parse_amount(amount_value: Any) -> Optional[float]:
        """
        金額を数値に変換

        Args:
            amount_value: 金額（文字列または数値）

        Returns:
            金額（float）
        """
        if amount_value is None:
            return None

        try:
            # 既に数値の場合
            if isinstance(amount_value, (int, float)):
                return float(amount_value)

            # 文字列の場合、カンマや通貨記号を除去
            if isinstance(amount_value, str):
                # 数字とピリオド、マイナス記号のみを抽出
                cleaned = re.sub(r'[^0-9.\-]', '', amount_value)
                if cleaned:
                    return float(cleaned)

        except Exception as e:
            logger.warning(f"金額パースエラー: {amount_value}, {e}")

        return None

    @staticmethod
    def _extract_event_dates(metadata: Dict[str, Any]) -> Optional[List[str]]:
        """
        メタデータからイベント日付を抽出

        Args:
            metadata: メタデータ辞書

        Returns:
            イベント日付のリスト（YYYY-MM-DD形式）
        """
        event_dates = []

        # weekly_schedule から日付を抽出
        weekly_schedule = metadata.get("weekly_schedule", [])
        if isinstance(weekly_schedule, list):
            for day_item in weekly_schedule:
                if isinstance(day_item, dict) and "date" in day_item:
                    date_str = day_item["date"]
                    # 日付の正規化（YYYY-MM-DD形式に統一）
                    normalized_date = MetadataExtractor._normalize_date(date_str)
                    if normalized_date:
                        event_dates.append(normalized_date)

        # monthly_schedule_blocks から日付を抽出
        monthly_schedule = metadata.get("monthly_schedule_blocks", [])
        if isinstance(monthly_schedule, list):
            for item in monthly_schedule:
                if isinstance(item, dict) and "date" in item:
                    date_str = item["date"]
                    normalized_date = MetadataExtractor._normalize_date(date_str)
                    if normalized_date:
                        event_dates.append(normalized_date)

        # 重複を除去してソート
        if event_dates:
            event_dates = sorted(list(set(event_dates)))
            return event_dates

        return None

    @staticmethod
    def _normalize_date(date_str: str) -> Optional[str]:
        """
        日付を YYYY-MM-DD 形式に正規化

        Args:
            date_str: 日付文字列（様々な形式）

        Returns:
            正規化された日付（YYYY-MM-DD）
        """
        if not date_str or not isinstance(date_str, str):
            return None

        try:
            # 既に YYYY-MM-DD 形式の場合
            if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
                return date_str

            # MM-DD 形式の場合、現在の年を補完
            if re.match(r'^\d{2}-\d{2}$', date_str):
                current_year = datetime.now().year
                return f"{current_year}-{date_str}"

            # その他の形式はパースを試みる
            # 例: "2024/12/04" → "2024-12-04"
            date_obj = datetime.strptime(date_str, "%Y/%m/%d")
            return date_obj.strftime("%Y-%m-%d")

        except Exception:
            pass

        return None
