"""
Stage H Domains: ドメイン固有の表パースロジック

各ドメインハンドラは統一インターフェースを提供:
- detect(table_title, unified_text) -> bool: ドメイン判定
- process(table, ref_id, table_title) -> Optional[Dict]: 表処理
"""

from .yotsuya import YotsuyaDomainHandler

# ドメインハンドラのレジストリ（優先順）
DOMAIN_HANDLERS = [
    YotsuyaDomainHandler,
    # 将来: SapixDomainHandler, NichinokenDomainHandler, etc.
]

__all__ = [
    'YotsuyaDomainHandler',
    'DOMAIN_HANDLERS',
]
