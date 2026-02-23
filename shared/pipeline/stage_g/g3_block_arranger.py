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

    def __init__(self, next_stage=None):
        """
        Block Arranger 初期化

        Args:
            next_stage: 次のステージ（G-5）のインスタンス
        """
        self.next_stage = next_stage

    def arrange(
        self,
        g1_result: Dict[str, Any],
        log_file=None,
    ) -> Dict[str, Any]:
        """
        テキストを意味的なブロックに整理

        Args:
            g1_result: G-1の処理結果（直前ステージのみ）
            log_file: ログファイルパス（オプション）

        Returns:
            {
                'success': bool,
                'blocks': list,
                'events': list,
                'tasks': list,
                'notices': list,
                'document_info': dict,
                'ui_tables': list
            }
        """
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[G-3]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._arrange_impl(g1_result)
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _arrange_impl(
        self,
        g1_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """arrange() の実装本体"""
        logger.info("[G-3] ブロック整頓開始")

        try:
            # ★G-1の結果から必要なデータを取得（直前ステージのみ）
            raw_text = g1_result.get('raw_text', '')
            logger.info(f"[G-3] raw_text 全文({len(raw_text)}文字):\n{raw_text if raw_text else '（空）'}")
            events = g1_result.get('events', [])
            tasks = g1_result.get('tasks', [])
            notices = g1_result.get('notices', [])
            document_info = g1_result.get('document_info', {})
            ui_tables = g1_result.get('ui_tables', [])

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
            for block_idx, block in enumerate(blocks):
                logger.info(f"[G-3]   ブロック{block_idx}: type={block.get('type')} content={block.get('content')}")

            result = {
                'success': True,
                'blocks': blocks,
                'block_count': len(blocks),
                # ★G-5に必要なデータを含める（G-1から受け取ったデータ）
                'events': events,
                'tasks': tasks,
                'notices': notices,
                'document_info': document_info,
                'ui_tables': ui_tables,
                'display_fields': g1_result.get('display_fields'),
            }

            # ★チェーン: 次のステージ（G-5）を呼び出す
            if self.next_stage:
                logger.info("[G-3] → 次のステージ（G-5）を呼び出します")
                return self.next_stage.eliminate(g3_result=result)

            return result

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
        テキストを空行で段落に分割し、JSON blob を除去して返す。

        トピックごとのグループ化は G-22 の AI が担当する。
        ルールベースの見出し判定・統合は行わない。
        """
        # Step1: 空行で段落分割（区切りを保つ）
        lines = text.split('\n')
        raw_paragraphs: List[str] = []
        current: List[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if current:
                    raw_paragraphs.append('\n'.join(current))
                    current = []
            else:
                current.append(stripped)
        if current:
            raw_paragraphs.append('\n'.join(current))

        # Step2: JSON blob を除去（OCR の bbox/blocks 出力がテキストに混入する場合）
        def is_json_blob(p: str) -> bool:
            t = p.strip()
            if t.startswith('{') or t.startswith('[{'):
                return True
            if '"bbox"' in t or ('"blocks"' in t and '"text"' in t):
                return True
            return False

        clean = [p for p in raw_paragraphs if not is_json_blob(p)]
        return clean

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
