"""
トクバイチラシ取得モジュール

トクバイのウェブサイトからチラシ画像を取得し、
Google DriveおよびSupabaseに登録する。
"""

from .flyer_ingestion import TokubaiFlyerIngestionPipeline

__all__ = ['TokubaiFlyerIngestionPipeline']
