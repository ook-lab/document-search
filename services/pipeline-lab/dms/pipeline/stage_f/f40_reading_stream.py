"""後方互換: 実装は `f17_reading_stream`（F17）。"""
from dms.pipeline.stage_f.f17_reading_stream import build_f17_reading_stream as build_f40_reading_stream

__all__ = ["build_f40_reading_stream"]
