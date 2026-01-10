"""
テキスト構造化ユーティリティ

テキストを行単位で解析し、意味のあるブロックに分類します。
挨拶、本文、署名、日付など、全ての行を構造化します。
"""
import re
from typing import List, Dict, Any
from loguru import logger


class TextStructurer:
    """テキストを構造化ブロックに分類するクラス"""

    # 挨拶パターン
    GREETING_PATTERNS = [
        r'^(お世話になっております|お疲れ様です|おはようございます|こんにちは|こんばんは)',
        r'^(いつもお世話になっております|平素より大変お世話になっております)',
        r'^(お忙しいところ|突然のご連絡|ご連絡ありがとうございます)',
        r'^(拝啓|敬具|草々|謹啓|謹白)',
    ]

    # 署名パターン
    SIGNATURE_PATTERNS = [
        r'^(よろしくお願い|何卒よろしく|引き続きよろしく)',
        r'^(\S+\s+\S+)$',  # 名前っぽい（2語）
        r'^(株式会社|合同会社|\S+部|\S+課)',
        r'^(Tel:|TEL:|電話:|Email:|E-mail:|メール:)',
        r'^(〒\d{3}-\d{4})',  # 郵便番号
    ]

    # 日付パターン
    DATE_PATTERNS = [
        r'\d{4}年\d{1,2}月\d{1,2}日',
        r'\d{4}/\d{1,2}/\d{1,2}',
        r'\d{4}-\d{1,2}-\d{1,2}',
        r'\d{1,2}月\d{1,2}日',
    ]

    # タイトル・見出しパターン
    TITLE_PATTERNS = [
        r'^【[^】]+】',  # 【タイトル】
        r'^■\s*.+',     # ■ タイトル
        r'^◆\s*.+',     # ◆ タイトル
        r'^##\s*.+',    # ## タイトル（Markdown）
        r'^#\s*.+',     # # タイトル（Markdown）
    ]

    # リスト項目パターン
    LIST_PATTERNS = [
        r'^[-・*]\s+',   # - 項目、・項目、* 項目
        r'^\d+\.\s+',    # 1. 項目
        r'^[①-⑳]\s*',   # ① 項目
    ]

    # 空行パターン
    EMPTY_LINE_PATTERN = r'^\s*$'

    @classmethod
    def structure_text(cls, text: str) -> List[Dict[str, Any]]:
        """
        テキストを構造化ブロックに分類

        Args:
            text: 入力テキスト

        Returns:
            構造化されたブロックのリスト
            [
                {"type": "greeting", "content": "お世話になっております", "line_number": 1},
                {"type": "body", "content": "本文の内容", "line_number": 2},
                ...
            ]
        """
        if not text:
            return []

        lines = text.split('\n')
        structured_blocks = []

        for line_num, line in enumerate(lines, start=1):
            block_type = cls._classify_line(line)

            structured_blocks.append({
                "type": block_type,
                "content": line,
                "line_number": line_num,
                "length": len(line)
            })

        logger.info(f"テキスト構造化完了: {len(structured_blocks)} 行を {len(set(b['type'] for b in structured_blocks))} 種類のブロックに分類")
        return structured_blocks

    @classmethod
    def _classify_line(cls, line: str) -> str:
        """
        1行を分類

        Args:
            line: 入力行

        Returns:
            ブロックタイプ（greeting, signature, date, title, list, body, empty）
        """
        # 空行チェック
        if re.match(cls.EMPTY_LINE_PATTERN, line):
            return "empty"

        # 挨拶チェック
        for pattern in cls.GREETING_PATTERNS:
            if re.search(pattern, line):
                return "greeting"

        # タイトル・見出しチェック
        for pattern in cls.TITLE_PATTERNS:
            if re.match(pattern, line):
                return "title"

        # リスト項目チェック
        for pattern in cls.LIST_PATTERNS:
            if re.match(pattern, line):
                return "list_item"

        # 日付チェック
        for pattern in cls.DATE_PATTERNS:
            if re.search(pattern, line):
                return "date"

        # 署名チェック
        for pattern in cls.SIGNATURE_PATTERNS:
            if re.search(pattern, line):
                return "signature"

        # その他は本文
        return "body"

    @classmethod
    def group_by_type(cls, structured_blocks: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        ブロックをタイプごとにグループ化

        Args:
            structured_blocks: 構造化されたブロックのリスト

        Returns:
            タイプごとにグループ化された辞書
        """
        grouped = {}
        for block in structured_blocks:
            block_type = block["type"]
            if block_type not in grouped:
                grouped[block_type] = []
            grouped[block_type].append(block)

        return grouped

    @classmethod
    def get_statistics(cls, structured_blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        構造化されたブロックの統計情報を取得

        Args:
            structured_blocks: 構造化されたブロックのリスト

        Returns:
            統計情報の辞書
        """
        total_lines = len(structured_blocks)
        type_counts = {}

        for block in structured_blocks:
            block_type = block["type"]
            type_counts[block_type] = type_counts.get(block_type, 0) + 1

        return {
            "total_lines": total_lines,
            "type_counts": type_counts,
            "unique_types": len(type_counts)
        }

    @classmethod
    def format_as_table(cls, structured_blocks: List[Dict[str, Any]]) -> str:
        """
        構造化されたブロックを表形式の文字列に変換

        Args:
            structured_blocks: 構造化されたブロックのリスト

        Returns:
            表形式の文字列
        """
        if not structured_blocks:
            return "（空のテキスト）"

        lines = []
        lines.append("行番号 | タイプ | 内容")
        lines.append("------|--------|------")

        for block in structured_blocks:
            line_num = block["line_number"]
            block_type = cls._translate_type(block["type"])
            content = block["content"][:50]  # 最初の50文字のみ
            lines.append(f"{line_num:04d} | {block_type} | {content}")

        return "\n".join(lines)

    @classmethod
    def _translate_type(cls, block_type: str) -> str:
        """
        ブロックタイプを日本語に翻訳

        Args:
            block_type: ブロックタイプ

        Returns:
            日本語のブロックタイプ名
        """
        translations = {
            "greeting": "挨拶",
            "signature": "署名",
            "date": "日付",
            "title": "タイトル",
            "list_item": "リスト項目",
            "body": "本文",
            "empty": "空行"
        }
        return translations.get(block_type, block_type)
