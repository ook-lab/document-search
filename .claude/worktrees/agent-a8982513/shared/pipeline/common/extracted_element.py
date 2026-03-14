"""
共通データ型：抽出要素の標準形式

全ステージで使用する統一データ構造。
座標の完全性を保証し、読み順整頓を可能にする。
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class BBox:
    """バウンディングボックス（ページ内座標系）"""
    x0: float
    y0: float
    x1: float
    y1: float

    def to_list(self) -> List[float]:
        """[x0, y0, x1, y1] 形式で返す"""
        return [self.x0, self.y0, self.x1, self.y1]

    def to_dict(self) -> Dict[str, float]:
        """辞書形式で返す"""
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2

    @classmethod
    def from_list(cls, bbox: List[float]) -> 'BBox':
        """[x0, y0, x1, y1] から生成"""
        return cls(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3])

    @classmethod
    def from_dict(cls, bbox: Dict[str, float]) -> 'BBox':
        """辞書から生成"""
        return cls(x0=bbox['x0'], y0=bbox['y0'], x1=bbox['x1'], y1=bbox['y1'])


@dataclass
class ExtractedElement:
    """
    抽出要素の標準形式

    すべての抽出要素（テキスト、表セル、見出し等）はこの形式に従う。
    page と bbox は必須フィールド（座標の完全性保証）。
    """
    text: str                    # テキスト内容
    page: int                    # ページ番号（0-indexed）
    bbox: BBox                   # 座標（ページ内座標系）
    type: str                    # 要素タイプ（text, table_cell, heading, etc.）

    # オプショナルフィールド
    confidence: float = 1.0      # 信頼度（0.0-1.0）
    source: str = ""             # 抽出元（stage_b, stage_e, etc.）
    metadata: Dict[str, Any] = field(default_factory=dict)  # 追加情報

    def to_dict(self) -> Dict[str, Any]:
        """辞書形式で返す（JSON シリアライズ用）"""
        return {
            "text": self.text,
            "page": self.page,
            "bbox": self.bbox.to_list(),
            "type": self.type,
            "confidence": self.confidence,
            "source": self.source,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ExtractedElement':
        """辞書から生成"""
        bbox_data = data.get('bbox', [0, 0, 0, 0])
        bbox = BBox.from_list(bbox_data) if isinstance(bbox_data, list) else BBox.from_dict(bbox_data)

        return cls(
            text=data['text'],
            page=data['page'],
            bbox=bbox,
            type=data['type'],
            confidence=data.get('confidence', 1.0),
            source=data.get('source', ''),
            metadata=data.get('metadata', {})
        )

    def reading_order_key(self) -> tuple:
        """
        読み順ソート用キー

        Returns:
            (page, y, x) のタプル
        """
        return (self.page, self.bbox.y0, self.bbox.x0)

    def validate(self) -> bool:
        """
        座標の完全性チェック

        Returns:
            True: 正常, False: 座標欠損
        """
        if self.page < 0:
            return False
        if self.bbox.x0 < 0 or self.bbox.y0 < 0:
            return False
        if self.bbox.x1 <= self.bbox.x0 or self.bbox.y1 <= self.bbox.y0:
            return False
        return True


@dataclass
class TableCell(ExtractedElement):
    """
    表セル（ExtractedElement の特殊化）

    表セルは行列位置情報を追加で持つ。
    """
    row: int = 0                 # 行番号（0-indexed）
    col: int = 0                 # 列番号（0-indexed）
    is_header: bool = False      # ヘッダーセルか
    rowspan: int = 1             # 行結合数
    colspan: int = 1             # 列結合数

    def to_dict(self) -> Dict[str, Any]:
        """辞書形式で返す"""
        base = super().to_dict()
        base.update({
            "row": self.row,
            "col": self.col,
            "is_header": self.is_header,
            "rowspan": self.rowspan,
            "colspan": self.colspan
        })
        return base


@dataclass
class PageElements:
    """
    ページ単位の抽出要素コレクション

    Stage E の出力形式。ページごとに要素をグループ化。
    """
    page: int                               # ページ番号
    elements: List[ExtractedElement]        # 抽出要素リスト
    words_count: int = 0                    # 単語数
    chars_count: int = 0                    # 文字数
    status: str = "ok"                      # ok, blank_skip, error
    skip_reason: str = ""                   # スキップ理由

    def to_dict(self) -> Dict[str, Any]:
        """辞書形式で返す"""
        return {
            "page": self.page,
            "elements": [e.to_dict() for e in self.elements],
            "words_count": self.words_count,
            "chars_count": self.chars_count,
            "status": self.status,
            "skip_reason": self.skip_reason
        }

    def validate(self) -> bool:
        """すべての要素の座標をチェック"""
        return all(e.validate() for e in self.elements)
