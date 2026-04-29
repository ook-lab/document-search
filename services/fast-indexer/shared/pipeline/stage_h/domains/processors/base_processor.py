"""
Base Domain Processor

ドメイン固有処理の抽象基底クラス
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List


class BaseDomainProcessor(ABC):
    """
    ドメイン固有処理を実装するプロセッサーの基底クラス

    各ドメインは以下のメソッドを実装する：
    - process(): cells_enriched に対してドメイン固有の処理を実行
    """

    def __init__(self, domain_def: Dict[str, Any]):
        """
        Args:
            domain_def: ドメイン定義（JSON から読み込んだ辞書）
        """
        self.domain_def = domain_def

    @abstractmethod
    def process(self, cells_enriched: List[Dict]) -> None:
        """
        cells_enriched に対してドメイン固有の処理を実行

        Args:
            cells_enriched: G8出力の enriched セルリスト（in-place変更）

        Note:
            このメソッドは cells_enriched を in-place で変更する。
            新しいリストを返す必要はない。
        """
        pass
