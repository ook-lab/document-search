"""後方互換: F52 実装へ委譲（付番 F-40 は廃止。数値順=実行順のため F52 を使用）。"""

from dms.pipeline.stage_f.f08_table_rebuilder import F08TableRebuilder

F52TableRebuilder = F08TableRebuilder

__all__ = ["F52TableRebuilder"]
