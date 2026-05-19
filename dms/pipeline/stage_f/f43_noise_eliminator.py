"""後方互換: F53 実装へ委譲（付番 F-43 は廃止。数値順=実行順のため F53 を使用）。"""

from dms.pipeline.stage_f.f09_noise_eliminator import F09NoiseEliminator

F43NoiseEliminator = F09NoiseEliminator

__all__ = ["F43NoiseEliminator"]
