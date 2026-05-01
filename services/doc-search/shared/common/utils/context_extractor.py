"""
Context Extractor
クエリから関連するユーザーコンテキストを抽出する
"""
from typing import Dict, List, Any, Optional
import re


class ContextExtractor:
    """
    クエリからユーザーコンテキスト（user_context.yaml）の
    関連情報のみを抽出するクラス
    """

    def __init__(self, user_context: Dict[str, Any]):
        """
        Args:
            user_context: user_context.yamlから読み込んだ辞書
        """
        self.user_context = user_context
        self.children = user_context.get('children', [])

    def extract_relevant_context(
        self,
        query: str,
        include_schedules: bool = False
    ) -> Dict[str, Any]:
        """
        クエリから関連するコンテキストを抽出

        Args:
            query: ユーザーの質問
            include_schedules: スケジュール情報を含めるか（回答生成時はTrue、検索時はFalse）

        Returns:
            抽出されたコンテキスト（子供の情報など）
        """
        if not self.children:
            return {"children": [], "relevance": "none"}

        # クエリから子供の名前を検出
        matched_children = self._find_children_by_name(query)

        # 名前が見つからない場合、学年やクラスから検出
        if not matched_children:
            matched_children = self._find_children_by_grade_or_class(query)

        # それでも見つからない場合
        if not matched_children:
            # クエリが学校や塾に関する一般的な質問の場合、全員を含める（後方互換性）
            if self._is_general_school_query(query):
                matched_children = self.children
                relevance = "general"
            else:
                # 関連性が低い場合は空
                return {"children": [], "relevance": "low"}
        else:
            relevance = "high"

        # 抽出されたコンテキストを構築
        extracted_children = []
        for child in matched_children:
            child_info = {
                "name": child.get("name"),
                "grade": child.get("grade"),
                "birth_date": child.get("birth_date"),
            }

            # 学校情報（基本）
            school = child.get("school", {})
            if school:
                child_info["school"] = {
                    "name": school.get("name"),
                    "class": school.get("class")
                }
                # スケジュールを含める場合のみ追加
                if include_schedules and school.get("schedule"):
                    child_info["school"]["schedule"] = school.get("schedule")

            # 塾情報（基本）
            cram_school = child.get("cram_school", {})
            if cram_school:
                child_info["cram_school"] = {
                    "name": cram_school.get("name")
                }
                # スケジュールを含める場合のみ追加
                if include_schedules and cram_school.get("schedule"):
                    child_info["cram_school"]["schedule"] = cram_school.get("schedule")

            extracted_children.append(child_info)

        return {
            "children": extracted_children,
            "relevance": relevance
        }

    def _find_children_by_name(self, query: str) -> List[Dict[str, Any]]:
        """
        クエリから子供の名前を検出して該当する子供を返す
        別名（aliases）もチェックする

        Args:
            query: ユーザーの質問

        Returns:
            該当する子供のリスト
        """
        matched = []
        for child in self.children:
            # 正式名でチェック
            name = child.get("name", "")
            if name and name in query:
                matched.append(child)
                continue

            # 別名（aliases）でもチェック
            aliases = child.get("aliases", [])
            if aliases:
                for alias in aliases:
                    if alias and alias in query:
                        matched.append(child)
                        break  # 一度マッチしたら次の子供へ

        return matched

    def _find_children_by_grade_or_class(self, query: str) -> List[Dict[str, Any]]:
        """
        クエリから学年やクラスを検出して該当する子供を返す

        Args:
            query: ユーザーの質問

        Returns:
            該当する子供のリスト
        """
        matched = []

        # 学年の検出（例: 「5年」「3年生」）
        grade_match = re.search(r'(\d+)年', query)
        if grade_match:
            grade = int(grade_match.group(1))
            for child in self.children:
                if child.get("grade") == grade:
                    matched.append(child)
            if matched:
                return matched

        # クラスの検出（例: 「1組」「2組」）
        class_match = re.search(r'(\d+)組', query)
        if class_match:
            class_num = class_match.group(1) + "組"
            for child in self.children:
                school = child.get("school", {})
                class_name = school.get("class", "")
                if class_num in class_name:
                    matched.append(child)

        return matched

    def _is_general_school_query(self, query: str) -> bool:
        """
        学校や塾に関する一般的な質問かどうかを判定

        Args:
            query: ユーザーの質問

        Returns:
            一般的な質問ならTrue
        """
        # 一般的な学校関連キーワード
        school_keywords = [
            "学校", "授業", "時間割", "科目", "宿題",
            "塾", "クラス", "先生", "教室", "学年通信"
        ]

        # 時間に関するキーワード（「今日の」「明日の」など）
        time_keywords = ["今日", "明日", "今週", "来週", "月曜", "火曜", "水曜", "木曜", "金曜"]

        # いずれかのキーワードが含まれているか
        for keyword in school_keywords + time_keywords:
            if keyword in query:
                return True

        return False

    def build_search_context_string(self, extracted_context: Dict[str, Any]) -> str:
        """
        検索用の軽量なコンテキスト文字列を構築（名前、学年、クラスのみ）

        Args:
            extracted_context: extract_relevant_contextで抽出されたコンテキスト

        Returns:
            検索用のコンテキスト文字列
        """
        if not extracted_context.get("children"):
            return ""

        parts = []
        for child in extracted_context["children"]:
            name = child.get("name", "")
            grade = child.get("grade", "")
            school = child.get("school", {})
            class_name = school.get("class", "") if school else ""

            if name:
                child_info = f"{name}"
                if grade:
                    child_info += f"（{grade}年生）"
                if class_name:
                    child_info += f" {class_name}"
                parts.append(child_info)

        if parts:
            return "関連: " + ", ".join(parts)
        return ""

    def build_answer_context_string(self, extracted_context: Dict[str, Any]) -> str:
        """
        回答生成用の詳細なコンテキスト文字列を構築（スケジュール含む）

        Args:
            extracted_context: extract_relevant_contextで抽出されたコンテキスト

        Returns:
            回答生成用のコンテキスト文字列
        """
        if not extracted_context.get("children"):
            return ""

        lines = ["【ユーザーの前提情報】"]

        for child in extracted_context["children"]:
            name = child.get("name", "不明")
            birth_date = child.get("birth_date", "不明")
            grade = child.get("grade", "不明")

            lines.append(f"\n■ {name} さん（{grade}年生、生年月日: {birth_date}）")

            # 学校情報
            school = child.get("school", {})
            if school:
                school_name = school.get("name", "不明")
                class_name = school.get("class", "不明")
                lines.append(f"  - 学校: {school_name} {class_name}")

                # 学校の時間割
                schedule = school.get("schedule", {})
                if schedule:
                    lines.append("  - 学校の授業:")
                    for day, periods in schedule.items():
                        subjects = [p.get("subject", "") for p in periods if isinstance(p, dict)]
                        if subjects:
                            lines.append(f"    {day}: {', '.join(subjects)}")

            # 塾情報
            cram_school = child.get("cram_school", {})
            if cram_school:
                cram_name = cram_school.get("name", "不明")
                lines.append(f"  - 塾: {cram_name}")

                # 塾のスケジュール
                schedule = cram_school.get("schedule", {})
                if schedule:
                    lines.append("  - 塾の授業:")
                    for day, sessions in schedule.items():
                        if sessions:
                            session_info = []
                            for s in sessions:
                                if isinstance(s, dict):
                                    time = s.get("time", "")
                                    subject = s.get("subject", "")
                                    session_info.append(f"{subject}({time})")
                            if session_info:
                                lines.append(f"    {day}: {', '.join(session_info)}")

        return "\n".join(lines)
