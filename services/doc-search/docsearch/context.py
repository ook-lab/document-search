"""ユーザーコンテキスト抽出（doc-search 専用）。"""
from __future__ import annotations

import re
from typing import Any, Dict, List


class ContextExtractor:
    def __init__(self, user_context: Dict[str, Any]) -> None:
        self.user_context = user_context
        self.children = user_context.get("children", [])

    def extract_relevant_context(self, query: str, include_schedules: bool = False) -> Dict[str, Any]:
        if not self.children:
            return {"children": [], "relevance": "none"}
        matched_children = self._find_children_by_name(query)
        if not matched_children:
            matched_children = self._find_children_by_grade_or_class(query)
        if not matched_children:
            if self._is_general_school_query(query):
                matched_children = self.children
                relevance = "general"
            else:
                return {"children": [], "relevance": "low"}
        else:
            relevance = "high"

        extracted_children = []
        for child in matched_children:
            child_info: Dict[str, Any] = {
                "name": child.get("name"),
                "grade": child.get("grade"),
                "birth_date": child.get("birth_date"),
            }
            school = child.get("school", {})
            if school:
                child_info["school"] = {"name": school.get("name"), "class": school.get("class")}
                if include_schedules and school.get("schedule"):
                    child_info["school"]["schedule"] = school.get("schedule")
            cram_school = child.get("cram_school", {})
            if cram_school:
                child_info["cram_school"] = {"name": cram_school.get("name")}
                if include_schedules and cram_school.get("schedule"):
                    child_info["cram_school"]["schedule"] = cram_school.get("schedule")
            extracted_children.append(child_info)
        return {"children": extracted_children, "relevance": relevance}

    def _find_children_by_name(self, query: str) -> List[Dict[str, Any]]:
        matched: List[Dict[str, Any]] = []
        for child in self.children:
            name = child.get("name", "")
            if name and name in query:
                matched.append(child)
                continue
            for alias in child.get("aliases", []) or []:
                if alias and alias in query:
                    matched.append(child)
                    break
        return matched

    def _find_children_by_grade_or_class(self, query: str) -> List[Dict[str, Any]]:
        matched: List[Dict[str, Any]] = []
        grade_match = re.search(r"(\d+)年", query)
        if grade_match:
            grade = int(grade_match.group(1))
            for child in self.children:
                if child.get("grade") == grade:
                    matched.append(child)
            if matched:
                return matched
        class_match = re.search(r"(\d+)組", query)
        if class_match:
            class_num = class_match.group(1) + "組"
            for child in self.children:
                school = child.get("school", {})
                class_name = school.get("class", "")
                if class_num in class_name:
                    matched.append(child)
        return matched

    def _is_general_school_query(self, query: str) -> bool:
        school_keywords = ["学校", "授業", "時間割", "科目", "宿題", "塾", "クラス", "先生", "教室", "学年通信"]
        time_keywords = ["今日", "明日", "今週", "来週", "月曜", "火曜", "水曜", "木曜", "金曜"]
        for keyword in school_keywords + time_keywords:
            if keyword in query:
                return True
        return False

    def build_search_context_string(self, extracted_context: Dict[str, Any]) -> str:
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
        return "関連: " + ", ".join(parts) if parts else ""

    def build_answer_context_string(self, extracted_context: Dict[str, Any]) -> str:
        if not extracted_context.get("children"):
            return ""
        lines = ["【ユーザーの前提情報】"]
        for child in extracted_context["children"]:
            name = child.get("name", "不明")
            birth_date = child.get("birth_date", "不明")
            grade = child.get("grade", "不明")
            lines.append(f"\n■ {name} さん（{grade}年生、生年月日: {birth_date}）")
            school = child.get("school", {})
            if school:
                lines.append(f"  - 学校: {school.get('name', '不明')} {school.get('class', '不明')}")
                schedule = school.get("schedule", {})
                if schedule:
                    lines.append("  - 学校の授業:")
                    for day, periods in schedule.items():
                        subjects = [p.get("subject", "") for p in periods if isinstance(p, dict)]
                        if subjects:
                            lines.append(f"    {day}: {', '.join(subjects)}")
            cram_school = child.get("cram_school", {})
            if cram_school:
                lines.append(f"  - 塾: {cram_school.get('name', '不明')}")
                schedule = cram_school.get("schedule", {})
                if schedule:
                    lines.append("  - 塾の授業:")
                    for day, sessions in schedule.items():
                        if sessions:
                            session_info = []
                            for s in sessions:
                                if isinstance(s, dict):
                                    session_info.append(f"{s.get('subject', '')}({s.get('time', '')})")
                            if session_info:
                                lines.append(f"    {day}: {', '.join(session_info)}")
        return "\n".join(lines)
