"""
E-21: Context Extractor（地の文用 - Gemini 2.5 Flash-lite）

地の文画像（非表領域）から、Stage Bで抽出できなかった
テキストを忠実に抽出する。

目的:
1. Stage Bで抽出できなかったテキストの補完（OCR補完）
2. 画像のテキストを忠実に抽出（分類・構造化なし）
3. 高速・低コストでの処理（Flash-lite使用）

注意:
- イベント、タスク、注意事項への分類は行わない
- それらの分類は Stage G-21 で実施する
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger
import base64
import json
import re

from .coordinate_matcher import CoordinateMatcher

try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    logger.warning("[E-21] google-generativeai がインストールされていません")


class E21ContextExtractor:
    """E-21: Context Extractor（地の文用）"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-2.5-flash-lite"  # Gemini 2.5 Flash-lite
    ):
        """
        Context Extractor 初期化

        Args:
            api_key: Google AI API Key
            model_name: 使用するモデル名（必ず gemini-2.5-flash-lite を使用）
        """
        self.model_name = model_name
        self.api_key = api_key
        self.matcher = CoordinateMatcher()

        if not GENAI_AVAILABLE:
            logger.error("[E-21] google-generativeai が必要です")
            return

        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(model_name)
            logger.info(f"[E-21] モデル初期化: {model_name}")
        else:
            logger.warning("[E-21] API key が設定されていません")
            self.model = None

    def extract(
        self,
        image_path: Path,
        page: int = 0,
        words: Optional[List[Dict[str, Any]]] = None,
        blocks: Optional[List[Dict[str, Any]]] = None,
        block_hint: Optional[str] = None,
        custom_prompt: Optional[str] = None,
        vision_text: Optional[str] = None,
        anchor_text: Optional[str] = None,
        log_file=None,
    ) -> Dict[str, Any]:
        """
        画像からテキストを忠実に抽出

        Args:
            image_path: 画像ファイルパス
            page: ページ番号（0-indexed）
            words: E1 の単語リスト（現在未使用）
            blocks: E5 のブロックリスト（現在未使用）
            block_hint: E-5のブロックヒント（オプション）
            custom_prompt: カスタムプロンプト（オプション）
            vision_text: E-21 の Vision OCR テキスト（あれば注入、なければ画像のみ）
            anchor_text: PDF由来テキスト（将来の拡張口、現在は null でOK）
            log_file: ログファイルパス（オプション）

        Returns:
            {
                'success': bool,
                'extracted_content': dict,  # {'text': str} - 抽出されたテキスト
                'blocks': list,             # 座標付きブロック [{'text': str, 'bbox': [...], 'type': 'paragraph'}]
                'raw_response': str,        # Geminiの生レスポンス
                'model_used': str,          # 使用したモデル名（gemini-2.5-flash-lite）
                'tokens_used': int,         # 使用トークン数（概算）
                'route': str,               # "E22_IMAGE_ONLY" | "E21_VISION+E22"
                'vision_text_used': bool    # Vision OCR テキストをプロンプトに注入したか
            }
        """
        _sink_id = None
        if log_file:
            _sink_id = logger.add(
                str(log_file),
                format="{time:HH:mm:ss} | {level:<5} | {message}",
                filter=lambda r: "[E-21]" in r["message"],
                level="DEBUG",
                encoding="utf-8",
            )
        try:
            return self._extract_impl(
                image_path, page, words, blocks, block_hint,
                custom_prompt, vision_text, anchor_text
            )
        finally:
            if _sink_id is not None:
                logger.remove(_sink_id)

    def _extract_impl(
        self,
        image_path: Path,
        page: int = 0,
        words: Optional[List[Dict[str, Any]]] = None,
        blocks: Optional[List[Dict[str, Any]]] = None,
        block_hint: Optional[str] = None,
        custom_prompt: Optional[str] = None,
        vision_text: Optional[str] = None,
        anchor_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """extract() の実装本体"""
        if not GENAI_AVAILABLE or not self.model:
            logger.error("[E-21] Gemini API が利用できません")
            return self._error_result("Gemini API not available")

        vision_text_used = bool(vision_text and vision_text.strip())
        route = "E21_VISION+E22" if vision_text_used else "E22_IMAGE_ONLY"

        logger.info("[E-21] " + "=" * 80)
        logger.info(f"[E-21] 文脈抽出開始: {image_path.name}")
        logger.info(f"[E-21] route={route}, page={page}")
        logger.info(f"[E-21] code_path={__file__}")
        logger.info("[E-21] " + "=" * 80)

        try:
            # 画像を読み込み
            with open(image_path, 'rb') as f:
                image_data = f.read()

            logger.info(f"[E-21] 画像サイズ: {len(image_data)} bytes")

            # プロンプトを構築
            prompt = self._build_prompt(block_hint, custom_prompt, vision_text, anchor_text)

            logger.info(f"[E-21] モデル: {self.model_name}")
            logger.info(f"[E-21] プロンプト長: {len(prompt)}文字")
            logger.info(f"[E-21] prompt_head={prompt[:200].replace(chr(10), '\\\\n')}")
            logger.info("[E-21] ===== AI プロンプト全文 =====")
            logger.info(f"[E-21] {prompt}")
            logger.info("[E-21] ===== プロンプト終了 =====")

            # 入力情報のサマリー
            logger.info("[E-21] 入力情報サマリー:")
            logger.info(f"[E-21]   ├─ block_hint: {'あり' if block_hint else 'なし'} ({len(block_hint or '')}文字)")
            logger.info(f"[E-21]   ├─ vision_text: {'あり' if vision_text else 'なし'} ({len(vision_text or '')}文字)")
            logger.info(f"[E-21]   ├─ anchor_text: {'あり' if anchor_text else 'なし'} ({len(anchor_text or '')}文字)")
            logger.info(f"[E-21]   └─ words: {len(words) if words else 0}個")

            # Gemini に送信
            logger.info("[E-21] Gemini API 呼び出し開始...")
            try:
                import google.generativeai as genai_mod
                gen_config = genai_mod.GenerationConfig(max_output_tokens=8192)
            except Exception:
                gen_config = None
            response = self.model.generate_content(
                [prompt, {'mime_type': 'image/png', 'data': image_data}],
                generation_config=gen_config,
            )
            logger.info("[E-21] Gemini API 呼び出し完了")

            # レスポンスをパース
            raw_text = response.text
            logger.info(f"[E-21] レスポンス長: {len(raw_text)}文字")
            logger.info("[E-21] ===== AI レスポンス全文 =====")
            logger.info(f"[E-21] {raw_text}")
            logger.info("[E-21] ===== レスポンス終了 =====")

            # JSON部分を抽出（```json ... ``` で囲まれている場合）
            parsed_result = self._parse_response(raw_text)
            extracted_content = {'text': parsed_result.get('text', '')}
            blocks = parsed_result.get('blocks', [])

            logger.info("[E-21] パース結果:")
            import json
            logger.info(f"[E-21] {json.dumps(parsed_result, ensure_ascii=False, indent=2)}")

            # 座標付きブロック全件
            logger.info(f"[E-21] 座標付きブロック数: {len(blocks)}")
            if blocks:
                text_lens = [len(b.get('text', '')) for b in blocks]
                text_lens_sorted = sorted(text_lens)
                mid = len(text_lens_sorted) // 2
                median = text_lens_sorted[mid] if text_lens_sorted else 0
                logger.info(
                    f"[E-21] ブロックテキスト長: min={min(text_lens)} median={median} max={max(text_lens)}"
                )
                logger.info("[E-21] ブロック全件:")
                for idx, block in enumerate(blocks, 1):
                    text = block.get('text', '')
                    bbox = block.get('bbox', [])
                    logger.info(f"[E-21]   Block {idx}: bbox={bbox}, text='{text}'")

            # トークン数を概算（文字数 / 4）
            tokens_used = (len(prompt) + len(raw_text)) // 4

            logger.info("[E-21] " + "=" * 80)
            logger.info(f"[E-21] 抽出完了")
            logger.info(f"[E-21]   ├─ モデル: {self.model_name}")
            logger.info(f"[E-21]   ├─ route: {route}")
            logger.info(f"[E-21]   ├─ トークン: 約{tokens_used}")
            logger.info(f"[E-21]   ├─ 抽出テキスト長: {len(extracted_content.get('text', ''))}文字")
            logger.info(f"[E-21]   └─ 座標付きブロック: {len(blocks)}個")
            logger.info("[E-21] " + "=" * 80)

            # ページ番号を各ブロックに追加
            for block in blocks:
                block['page'] = page

            return {
                'success': True,
                'extracted_content': extracted_content,
                'blocks': blocks,  # 座標付きブロック
                'raw_response': raw_text,
                'model_used': self.model_name,
                'tokens_used': tokens_used,
                'route': route,
                'vision_text_used': vision_text_used,
                'role': 'NON_TABLE_FINAL_TEXT',
                'priority': 100,
                'is_final_non_table': True
            }

        except Exception as e:
            logger.error(f"[E-21] 抽出エラー: {e}", exc_info=True)
            return self._error_result(str(e))

    def _build_prompt(
        self,
        block_hint: Optional[str],
        custom_prompt: Optional[str],
        vision_text: Optional[str] = None,
        anchor_text: Optional[str] = None
    ) -> str:
        """
        プロンプトを構築

        Args:
            block_hint: ブロックヒント
            custom_prompt: カスタムプロンプト
            vision_text: E-21 の Vision OCR テキスト（あれば注入）
            anchor_text: PDF由来テキスト（将来の拡張口）

        Returns:
            プロンプト文字列
        """
        if custom_prompt:
            return custom_prompt

        prompt_parts = []

        # ベースプロンプト（座標付きテキスト抽出）
        prompt_parts.append("""
添付された画像から、テキストを「読みやすい意味まとまり」で抽出してください。

【最重要：ブロック分割ポリシー】
- blocks の数合わせは禁止。できるだけ大きくまとめる。
- 目安は 3〜最大でも10 ブロック。内容が少ない場合は 1〜2 ブロックでも可。
- 見出し／本文／枠付き注意／図表内テキスト（吹き出し等）は、分ける。
- 折り返し改行（同じ段落内の改行）は「結合」する。改行＝分割にしないこと。
- 原則として「1〜2行しかない本文ブロック」を量産しない（見出し・短い注意書き等の例外を除く）。
- ただし 1文字・1単語の細切れは厳禁（意味のあるまとまりとしてまとめる）。
- 迷った場合は「分割しない」ことを優先する。                        

【抽出ルール】
- 画像に実在しないテキストを生成しないこと。
- 装飾罫線・模様・枠線など本文でない要素は無視すること。
- 読み順（上→下、左→右）を守ること。
- 読めない箇所は [[判読不能]] とすること。
- 句読点や記号は可能な限り保持すること。

【座標ルール】
- 画像の左上を (0, 0) とするピクセル座標。
- bbox 形式は必ず xyxy: [x0, y0, x1, y1]
  - x1 > x0, y1 > y0 を必ず満たすこと。
  - width/height 形式（[x, y, w, h]）は禁止。

【出力形式（JSON のみ。説明文不要）】
```json
{
  "blocks": [
    {
      "text": "意味まとまりのテキスト（段落内の折り返しは結合してよい）",
      "bbox": [x0, y0, x1, y1]
    }
  ]
}
```
""")

        # Vision OCR テキストを注入（あれば）
        if vision_text and vision_text.strip():
            # 改行を空白に正規化（改行だらけのテキストがモデルの分割判断に影響するため）
            vision_text_norm = re.sub(r"\s*\n+\s*", " ", vision_text).strip()[:3000]
            prompt_parts.append("\n---\n")
            prompt_parts.append("【参考OCR（文字認識結果）】\n")
            prompt_parts.append("※以下は参考情報です。改行構造は無視してください。ブロック分割の根拠にしないこと。画像が正本です。\n")
            prompt_parts.append(vision_text_norm)
            prompt_parts.append("\n")

        # PDF由来テキストを注入（あれば）
        if anchor_text and anchor_text.strip():
            # 改行を空白に正規化
            anchor_text_norm = re.sub(r"\s*\n+\s*", " ", anchor_text).strip()[:2000]
            prompt_parts.append("\n---\n")
            prompt_parts.append("【参考テキスト（PDF抽出）】\n")
            prompt_parts.append("※以下は参考情報です。改行構造は無視してください。ブロック分割の根拠にしないこと。\n")
            prompt_parts.append(anchor_text_norm)
            prompt_parts.append("\n")

        # ブロックヒントを追加
        if block_hint:
            prompt_parts.append("\n---\n")
            prompt_parts.append(block_hint)

        return "".join(prompt_parts)

    def _parse_response(self, raw_text: str) -> Dict[str, Any]:
        """
        レスポンスをパースして座標付きブロックを抽出。
        Gemini がトークン上限で途中切断した場合も部分パースで救出する。

        Args:
            raw_text: Geminiの生レスポンス

        Returns:
            {
                'text': str,  # 全テキスト（後方互換性）
                'blocks': [{'text': str, 'bbox': [x0, y0, x1, y1]}]
            }
        """
        import json

        # コードブロックから JSON 文字列を取り出す
        if '```json' in raw_text:
            start = raw_text.find('```json') + 7
            end = raw_text.find('```', start)
            json_str = raw_text[start:end].strip() if end > start else raw_text[start:].strip()
        elif '```' in raw_text:
            start = raw_text.find('```') + 3
            end = raw_text.find('```', start)
            json_str = raw_text[start:end].strip() if end > start else raw_text[start:].strip()
        else:
            json_str = raw_text.strip()

        parsed = None

        # ① フル parse
        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as e:
            # ② 途中切断 → 最後の完全エントリまでで閉じて再試行
            logger.warning(f"[E-21] JSONパースエラー（切断検出）: {e}")
            last_complete = json_str.rfind('},')
            if last_complete > 10:
                try:
                    partial = json_str[:last_complete + 1] + ']}'
                    parsed = json.loads(partial)
                    logger.warning(
                        f"[E-21] 部分パース成功: {last_complete}/{len(json_str)}文字 "
                        f"(切断率 {100*(len(json_str)-last_complete)/len(json_str):.1f}%)"
                    )
                except json.JSONDecodeError as e2:
                    logger.warning(f"[E-21] 部分パースも失敗: {e2}")
            else:
                logger.warning(f"[E-21] 有効なエントリが見つからず。blocks=[]")

        if parsed is None:
            return {'text': '', 'blocks': []}

        blocks = parsed.get('blocks', [])

        # bbox は Gemini が出力した [x0, y0, x1, y1]（xyxy）のまま返す

        # 全テキストを生成（後方互換性）
        full_text = '\n\n'.join([block.get('text', '') for block in blocks if block.get('text', '').strip()])

        return {
            'text': full_text,
            'blocks': blocks
        }

    def _enrich_with_coordinates(
        self,
        extracted_content: Dict[str, Any],
        words: List[Dict[str, Any]],
        blocks: Optional[List[Dict[str, Any]]],
        page: int
    ) -> Dict[str, Any]:
        """
        抽出された要素に座標を付与

        注意: 現在未使用（純粋なテキスト抽出のみを行うため）
        将来的にテキスト全体への座標付与が必要になった場合に使用

        Args:
            extracted_content: Gemini が返した抽出結果
            words: E1 の単語リスト
            blocks: E5 のブロックリスト
            page: ページ番号

        Returns:
            座標が付与された extracted_content
        """
        enriched = {}

        # schedule, tasks, notices, other の各カテゴリを処理
        for key in ['schedule', 'tasks', 'notices', 'other']:
            elements = extracted_content.get(key, [])
            if not elements:
                enriched[key] = []
                continue

            # 各カテゴリに応じたテキストキーを決定
            if key == 'schedule':
                text_key = 'event'
            elif key == 'tasks':
                text_key = 'item'
            else:
                text_key = 'content'

            # 座標を付与
            enriched_elements = self.matcher.enrich_elements(
                elements, words, blocks, page, text_key
            )
            enriched[key] = enriched_elements

        # その他のキーをコピー
        for key, value in extracted_content.items():
            if key not in enriched:
                enriched[key] = value

        return enriched

    def _error_result(self, error_message: str) -> Dict[str, Any]:
        """エラー結果を返す"""
        return {
            'success': False,
            'error': error_message,
            'extracted_content': {},
            'blocks': [],  # 座標付きブロック（空）
            'raw_response': '',
            'model_used': self.model_name,
            'tokens_used': 0,
            'route': 'E22_IMAGE_ONLY',
            'vision_text_used': False,
            'role': 'NON_TABLE_FINAL_TEXT',
            'priority': 100,
            'is_final_non_table': True
        }
