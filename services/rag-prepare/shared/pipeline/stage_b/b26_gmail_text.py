"""
B26 Gmail テキストメールプロセッサ

body_plain を段落分割して整形テキストを返す。
Stage A/D/E はスキップ。
"""
import re
import logging

logger = logging.getLogger(__name__)


class B26GmailTextProcessor:
    """Gmail テキストメール専用プロセッサ（B26）"""

    def process(self, raw_doc: dict) -> dict:
        """
        body_plain を段落分割して整形テキストに変換する。

        Args:
            raw_doc: 01_gmail_01_raw のレコード

        Returns:
            {
                'email_type': 'text',
                'assembled_text': str,
                'processor_name': 'B26_GMAIL_TEXT',
            }
        """
        body_plain = raw_doc.get('body_plain') or ''
        logger.info(f"[B26] テキストメール処理開始: {len(body_plain)}文字")

        assembled_text = self._format_plain_text(body_plain)

        logger.info(f"[B26] 完了: {len(assembled_text)}文字")
        return {
            'email_type': 'text',
            'assembled_text': assembled_text,
            'processor_name': 'B26_GMAIL_TEXT',
        }

    def _format_plain_text(self, text: str) -> str:
        """
        プレーンテキストを整形する。

        - 連続する空白行を 1 行に圧縮
        - 引用行（> で始まる行）をまとめて 1 ブロックに
        - 末尾空白を除去
        """
        if not text or not text.strip():
            return ''

        lines = text.splitlines()
        result_parts = []
        current_para = []
        in_quote = False

        for line in lines:
            stripped = line.rstrip()

            # 引用行
            if stripped.startswith('>'):
                if current_para:
                    result_parts.append('\n'.join(current_para))
                    current_para = []
                if not in_quote:
                    result_parts.append('')  # 引用前に空行
                    in_quote = True
                result_parts.append(stripped)
                continue

            in_quote = False

            # 空行: 段落区切り
            if not stripped:
                if current_para:
                    result_parts.append('\n'.join(current_para))
                    current_para = []
                result_parts.append('')
                continue

            current_para.append(stripped)

        if current_para:
            result_parts.append('\n'.join(current_para))

        # 連続する空行を 1 行に圧縮
        final_lines = []
        prev_blank = False
        for part in result_parts:
            if part == '':
                if not prev_blank:
                    final_lines.append('')
                prev_blank = True
            else:
                final_lines.append(part)
                prev_blank = False

        return '\n'.join(final_lines).strip()
