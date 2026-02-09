"""
G-3: Semantic Block Arrangement（テキストブロック整頓）

ドキュメント内の全テキストを、意味のあるブロック単位
（タイトル、段落、箇条書き、注釈）に再構成する。

目的:
1. 論理的な読み順で配列化
2. 各ブロックに type を付与（text, heading, notice）
3. UI側での適切な表示スタイル切り替え
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger


class G3BlockArranger:
    """G-3: Semantic Block Arrangement（ブロック整頓）"""

    def __init__(self):
        """Block Arranger 初期化"""
        pass

    def arrange(
        self,
        raw_text: str,
        events: List[Dict[str, Any]],
        tasks: List[Dict[str, Any]],
        notices: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        テキストを意味的なブロックに整理

        Args:
            raw_text: Stage F の統合テキスト
            events: イベントリスト
            tasks: タスクリスト
            notices: 注意事項リスト

        Returns:
            {
                'success': bool,
                'blocks': list,  # ブロックリスト
                'block_count': int
            }
        """
        logger.info("[G-3] ブロック整頓開始")

        try:
            blocks = []

            # イベントブロック
            if events:
                blocks.append({
                    'block_id': f'B{len(blocks) + 1}',
                    'type': 'events',
                    'label': '予定・スケジュール',
                    'content': events,
                    'display_order': 1
                })

            # タスクブロック
            if tasks:
                blocks.append({
                    'block_id': f'B{len(blocks) + 1}',
                    'type': 'tasks',
                    'label': 'タスク・持ち物',
                    'content': tasks,
                    'display_order': 2
                })

            # 注意事項ブロック
            if notices:
                blocks.append({
                    'block_id': f'B{len(blocks) + 1}',
                    'type': 'notices',
                    'label': '注意事項',
                    'content': notices,
                    'display_order': 3
                })

            # テキストブロック（段落分割）
            if raw_text:
                text_blocks = self._split_into_paragraphs(raw_text)
                for idx, para in enumerate(text_blocks):
                    blocks.append({
                        'block_id': f'B{len(blocks) + 1}',
                        'type': 'text',
                        'label': f'段落{idx + 1}',
                        'content': para,
                        'display_order': 10 + idx
                    })

            # display_order でソート
            blocks.sort(key=lambda b: b.get('display_order', 999))

            logger.info(f"[G-3] 整頓完了: {len(blocks)}ブロック")

            return {
                'success': True,
                'blocks': blocks,
                'block_count': len(blocks)
            }

        except Exception as e:
            logger.error(f"[G-3] 整頓エラー: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'blocks': [],
                'block_count': 0
            }

    def _split_into_paragraphs(
        self,
        text: str
    ) -> List[str]:
        """
        テキストを段落に分割

        Args:
            text: 全文テキスト

        Returns:
            段落リスト
        """
        # 改行で分割
        lines = text.split('\n')

        paragraphs = []
        current_para = []

        for line in lines:
            line = line.strip()

            if not line:
                # 空行で段落を区切る
                if current_para:
                    paragraphs.append('\n'.join(current_para))
                    current_para = []
            else:
                current_para.append(line)

        # 最後の段落
        if current_para:
            paragraphs.append('\n'.join(current_para))

        return paragraphs

    def _classify_block_type(
        self,
        text: str
    ) -> str:
        """
        テキストからブロックタイプを推定

        Args:
            text: テキスト

        Returns:
            'heading', 'text', 'notice', 'list'
        """
        # 簡易的な分類
        if len(text) < 50 and text.endswith('：'):
            return 'heading'
        elif '※' in text or '注意' in text or '重要' in text:
            return 'notice'
        elif text.startswith('・') or text.startswith('−'):
            return 'list'
        else:
            return 'text'
