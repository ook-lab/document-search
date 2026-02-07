"""
Stage H2: Text Specialist (テキスト処理専門)

【設計 2026-01-27】Stage HI分割: H1 + H2

役割: H1で軽量化されたテキストを処理し、構造化 + 統合・要約を実行
      従来のStage HI統合版と同等の機能（ただし入力量が削減済み）

============================================
入力:
  - reduced_text: H1で表テキストを削除した軽量テキスト
  - h1_result: Stage H1の処理結果（processed_tables等）
  - source_inventory: REF_ID付きセグメントリスト

出力:
  - document_date: 基準日付
  - tags: 検索用タグ
  - metadata: 構造化データ（H1の結果も含む）
  - title: ドキュメントタイトル
  - summary: 要約
  - calendar_events: カレンダーイベント
  - tasks: タスクリスト
  - audit_canonical_text: 監査用正本テキスト

特徴:
  - 入力テキスト量が削減されているため、トークン消費が減少
  - H1で抽出した表メタデータをマージ
============================================
"""
import re
import json
import json_repair
from typing import Dict, Any, Optional, List
from pathlib import Path
from string import Template
from loguru import logger
from datetime import datetime

from shared.ai.llm_client.llm_client import LLMClient
from ..constants import STAGE_F_OUTPUT_SCHEMA_VERSION


class StageH2Text:
    """Stage H2: テキスト処理専門"""

    def __init__(self, llm_client: LLMClient):
        """
        Args:
            llm_client: LLMクライアント
        """
        self.llm = llm_client

    def process(
        self,
        file_name: str,
        doc_type: str,
        workspace: str,
        reduced_text: str,
        prompt: str,
        model: str,
        h1_result: Optional[Dict[str, Any]] = None,
        stage_f_structure: Optional[Dict[str, Any]] = None,
        stage_g_result: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        テキスト処理 + 構造化 + 要約

        Args:
            file_name: ファイル名
            doc_type: ドキュメントタイプ
            workspace: ワークスペース
            reduced_text: H1で軽量化されたテキスト
            prompt: プロンプト
            model: モデル名
            h1_result: Stage H1の結果
            stage_f_structure: Stage F の構造化情報
            stage_g_result: Stage G の結果

        Returns:
            Stage H2 の出力形式
        """
        logger.info(f"[Stage H2] テキスト処理開始... (doc_type={doc_type}, model={model})")

        # 入力サイズをログ
        original_size = len(stage_g_result.get('unified_text', '')) if stage_g_result else 0
        reduced_size = len(reduced_text)
        reduction_pct = (original_size - reduced_size) * 100 // original_size if original_size > 0 else 0
        logger.info(f"[Stage H2] 入力テキスト: {reduced_size}文字 (元: {original_size}文字, -{reduction_pct}%削減)")

        # Stage G の結果から source_inventory を取得
        source_inventory = []
        table_inventory = []
        if stage_g_result:
            source_inventory = stage_g_result.get('source_inventory', [])
            # H1で処理済みの表を除外した table_inventory
            removed_ids = h1_result.get('removed_table_ids', set()) if h1_result else set()
            table_inventory = [
                t for t in stage_g_result.get('table_inventory', [])
                if t.get('ref_id') not in removed_ids
            ]
            logger.info(f"[Stage H2] source_inventory={len(source_inventory)}, 残存table_inventory={len(table_inventory)}")

        # 【Ver 6.7】テキストが空でも表があれば処理続行
        if not reduced_text or not reduced_text.strip():
            if table_inventory or (h1_result and h1_result.get('processed_tables')):
                # 表データをテキスト化して続行
                logger.info(f"[Stage H2] テキストは空ですが、表があります。表データをテキスト化して続行します。")
                reduced_text = self._tables_to_text(table_inventory, h1_result)
                logger.info(f"[Stage H2] 表→テキスト変換: {len(reduced_text)}文字")
            else:
                raise ValueError("[Stage H2] 入力テキストが空です（表もありません）")

        try:
            # プロンプト構築
            logger.info("[Stage H2] プロンプト構築中...")

            # H1の結果から情報を取得
            h1_tables = h1_result.get('processed_tables', []) if h1_result else []
            unrepairable_tables = h1_result.get('unrepairable_tables', []) if h1_result else []
            h2_hint = h1_result.get('h2_hint', '') if h1_result else ''

            if unrepairable_tables:
                logger.info(f"[Stage H2] 修復不能表: {len(unrepairable_tables)}件 → プロンプトに通知")
            if h2_hint:
                logger.info(f"[Stage H2] h2_hint受信: {h2_hint[:100]}")

            full_prompt = self._build_prompt(
                prompt_template=prompt,
                file_name=file_name,
                doc_type=doc_type,
                workspace=workspace,
                combined_text=reduced_text,
                source_inventory=source_inventory,
                table_inventory=table_inventory,
                h1_tables=h1_tables,
                unrepairable_tables=unrepairable_tables,
                h2_hint=h2_hint
            )
            logger.info(f"[Stage H2] プロンプト構築完了 ({len(full_prompt)}文字)")

            # LLM呼び出し
            logger.info(f"[Stage H2] LLM呼び出し中... (model={model})")
            response = self.llm.call_model(
                tier="default",
                prompt=full_prompt,
                model_name=model
            )
            logger.info(f"[Stage H2] LLM応答受信: success={response.get('success')}")

            if not response.get("success"):
                raise RuntimeError(f"[Stage H2] LLM呼び出し失敗: {response.get('error')}")

            # JSON抽出
            content = response.get("content", "")
            logger.info(f"[Stage H2] ===== LLMレスポンス（最初の1000文字）=====\n{content[:1000]}")
            # リトライ禁止（2026-01-28）
            result = self._extract_json_with_retry(content, model=model, max_retries=0)

            # Stage F の構造化情報をマージ
            if stage_f_structure:
                result = self._merge_stage_f_structure(result, stage_f_structure)

            # H1 の結果をマージ
            if h1_result:
                result = self._merge_h1_result(result, h1_result)

            # audit_canonical_text の生成
            audit_canonical_text = self._generate_audit_canonical_text(
                result, reduced_text, source_inventory
            )

            # 結果の整形（表関連のフィールドはすべて削除）
            # H1 が既に処理済みの純粋 Python ピボット結果を保護
            metadata = result.get('metadata', {})

            # AI が生成した表関連フィールドをすべて削除
            for key in ['structured_tables', 'tables', 'table_data', 'table_list']:
                if key in metadata:
                    logger.warning(f"[Stage H2] AI が生成した '{key}' を削除（H1の結果を保護）")
                    del metadata[key]

            # JSON本体にも表関連フィールドがあれば削除
            for key in ['structured_tables', 'tables', 'table_data', 'table_list']:
                if key in result:
                    logger.warning(f"[Stage H2] AI結果から '{key}' を削除（H1の結果を保護）")
                    del result[key]

            final_result = {
                'document_date': result.get('document_date'),
                'tags': result.get('tags', []),
                'metadata': metadata,
                'title': result.get('title', ''),
                'summary': result.get('summary', ''),
                'calendar_events': result.get('calendar_events', []),
                'tasks': result.get('tasks', []),
                'audit_canonical_text': audit_canonical_text
            }

            # ============================================
            # 【H2全出力ダンプ】
            # ============================================
            logger.info("=" * 80)
            logger.info("[H2 OUTPUT DUMP] === H2 全生成物 ===")
            logger.info(f"[H2 OUTPUT] document_date: {final_result.get('document_date')}")
            logger.info(f"[H2 OUTPUT] title: {final_result.get('title')}")
            logger.info(f"[H2 OUTPUT] tags: {final_result.get('tags')}")
            logger.info(f"[H2 OUTPUT] summary: {final_result.get('summary', '')[:200]}...")
            logger.info(f"[H2 OUTPUT] calendar_events: {len(final_result.get('calendar_events', []))}件")
            for i, ev in enumerate(final_result.get('calendar_events', [])[:5]):
                logger.info(f"[H2 OUTPUT]   event[{i}]: {ev}")
            logger.info(f"[H2 OUTPUT] tasks: {len(final_result.get('tasks', []))}件")
            for i, task in enumerate(final_result.get('tasks', [])[:5]):
                logger.info(f"[H2 OUTPUT]   task[{i}]: {task}")
            # metadata内のextracted_tables（H1から来る）
            metadata = final_result.get('metadata', {})
            logger.info(f"[H2 OUTPUT] metadata keys: {list(metadata.keys())}")
            tables = metadata.get('extracted_tables', [])
            logger.info(f"[H2 OUTPUT] metadata.extracted_tables: {len(tables)}件")
            for i, tbl in enumerate(tables[:3]):
                logger.info(f"[H2 OUTPUT]   table[{i}].ref_id: {tbl.get('ref_id')}")
                logger.info(f"[H2 OUTPUT]   table[{i}].columns: {tbl.get('columns')}")
                logger.info(f"[H2 OUTPUT]   table[{i}].flat_columns: {tbl.get('flat_columns')}")
                gd = tbl.get('grid_data', {})
                logger.info(f"[H2 OUTPUT]   table[{i}].grid_data.columns: {gd.get('columns')}")
                logger.info(f"[H2 OUTPUT]   table[{i}].grid_data.row_headers: {gd.get('row_headers')}")
            logger.info(f"[H2 OUTPUT] audit_canonical_text: {len(final_result.get('audit_canonical_text', ''))}文字")
            logger.info("=" * 80)

            logger.info(f"[Stage H2完了] title={final_result['title'][:50] if final_result['title'] else 'N/A'}...")
            return final_result

        except Exception as e:
            logger.error(f"[Stage H2 エラー] 処理失敗: {e}", exc_info=True)
            raise

    def _build_prompt(
        self,
        prompt_template: str,
        file_name: str,
        doc_type: str,
        workspace: str,
        combined_text: str,
        source_inventory: List[Dict],
        table_inventory: List[Dict],
        h1_tables: List[Dict],
        unrepairable_tables: List[Dict] = None,
        h2_hint: str = ""
    ) -> str:
        """プロンプトを構築（アンカー活用強化版）"""
        # source_inventory を簡略化
        inventory_summary = []
        for item in source_inventory[:30]:
            inventory_summary.append({
                'ref_id': item.get('ref_id'),
                'type': item.get('type'),
                'text': item.get('text', '')[:200]
            })

        # H1で処理済み表の概要（詳細データはメタデータとして既に含まれている）
        h1_tables_summary = []
        for tbl in h1_tables[:10]:
            h1_tables_summary.append({
                'ref_id': tbl.get('ref_id'),
                'table_title': tbl.get('table_title'),
                'table_type': tbl.get('table_type'),
                'row_count': tbl.get('row_count', 0)
            })

        # テンプレート変数を置換
        template = Template(prompt_template)
        prompt = template.substitute(
            file_name=file_name,
            doc_type=doc_type,
            workspace=workspace,
            combined_text=combined_text,
            current_date=datetime.now().strftime("%Y-%m-%d"),
            source_inventory_json=json.dumps(inventory_summary, ensure_ascii=False, indent=2),
            table_inventory_json=json.dumps(h1_tables_summary, ensure_ascii=False, indent=2),
            source_count=len(source_inventory),
            table_count=len(h1_tables)
        )

        # ============================================
        # ドメインコンテキスト: H1から受け取ったヒント
        # ============================================
        if h2_hint:
            prompt = prompt + "\n\n" + "=" * 50 + "\n"
            prompt += "【Domain Context from H1】\n"
            prompt += h2_hint + "\n"
            prompt += "Table data has already been structured by H1. Focus on titles, annotations, notes.\n"
            prompt += "=" * 50

        # ============================================
        # 表は H1 で pure Python により処理済み
        # H2 は表に関する指示を一切出さない
        # ============================================
        # （H2 は表の生成・参照・言及を行わない）

        return prompt

    # 【削除】_build_anchor_instructions() メソッド
    # H2 は表に関する指示を一切出さないため、このメソッドは不要になった。
    # 2026-02-08: H1 が pure Python で表を完全に処理するため削除

    def _extract_json_with_retry(
        self,
        content: str,
        model: str,
        max_retries: int = 2
    ) -> Dict[str, Any]:
        """JSON抽出（リトライ機能付き）"""
        for attempt in range(max_retries + 1):
            try:
                result = self._extract_json(content)
                logger.debug(f"[Stage H2] JSON抽出成功 (試行{attempt + 1}/{max_retries + 1})")
                return result

            except Exception as e:
                if attempt < max_retries:
                    logger.warning(f"[Stage H2] JSON抽出失敗 (試行{attempt + 1}/{max_retries + 1}): {e}")
                    content = self._retry_json_extraction(content, str(e), model)
                else:
                    logger.error(f"[Stage H2] JSON抽出失敗（最終試行）: {e}")
                    raise

        return {}

    def _extract_json(self, content: str) -> Dict[str, Any]:
        """コンテンツからJSONを抽出"""
        patterns = [
            r'```json\s*(.*?)```',
            r'```\s*(.*?)```',
            r'\{[\s\S]*?\}',
        ]

        json_str = None
        for pattern in patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                json_str = match.group(1) if match.lastindex else match.group(0)
                json_str = json_str.strip()
                if json_str.startswith('{'):
                    break
                else:
                    json_str = None

        if not json_str:
            match = re.search(r'\{[\s\S]*\}', content, re.DOTALL)
            if match:
                json_str = match.group(0).strip()
            else:
                json_str = content.strip()

        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"[Stage H2] JSON解析失敗: {e}")
            try:
                return json_repair.loads(json_str)
            except Exception as repair_error:
                logger.error(f"[Stage H2] JSON修復も失敗: {repair_error}")
                raise

    def _retry_json_extraction(
        self,
        failed_content: str,
        error_message: str,
        model: str
    ) -> str:
        """JSON抽出失敗時、LLMにJSON修正を依頼"""
        prompt = f"""以下のJSONにエラーがあります。修正してください。

エラー: {error_message}

元のJSON:
```
{failed_content[:3000]}
```

修正されたJSONを ```json ブロックで出力してください。
"""

        try:
            response = self.llm.call_model(
                tier="default",
                prompt=prompt,
                model_name=model
            )

            if response.get("success"):
                return response.get("content", "")
            else:
                return failed_content

        except Exception as e:
            logger.error(f"[Stage H2] JSON修正エラー: {e}")
            return failed_content

    def _merge_stage_f_structure(
        self,
        result: Dict[str, Any],
        stage_f_structure: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Stage F の構造化情報をマージ"""
        metadata = result.get('metadata', {})

        schema_ver = stage_f_structure.get('schema_version', '')
        is_v9 = (schema_ver == STAGE_F_OUTPUT_SCHEMA_VERSION)

        if is_v9:
            stage_f_text_blocks = stage_f_structure.get('text_blocks', [])
            if stage_f_text_blocks:
                metadata['_raw_text_blocks'] = stage_f_text_blocks

        visual_elements = stage_f_structure.get('visual_elements', {})
        if visual_elements:
            deadline_info = visual_elements.get('deadline_info')
            if deadline_info and not result.get('document_date'):
                result['document_date'] = deadline_info

        result['metadata'] = metadata
        return result

    def _merge_h1_result(
        self,
        result: Dict[str, Any],
        h1_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """H1の結果をマージ"""
        metadata = result.get('metadata', {})

        # H1で抽出したメタデータをマージ
        h1_metadata = h1_result.get('extracted_metadata', {})
        if h1_metadata:
            for key, value in h1_metadata.items():
                if key not in metadata:
                    metadata[key] = value
                    logger.debug(f"[Stage H2] H1メタデータをマージ: {key}")

        # H1で処理した表を extracted_tables に追加
        processed_tables = h1_result.get('processed_tables', [])
        if processed_tables:
            # 既存の extracted_tables と統合
            existing_tables = metadata.get('extracted_tables', [])
            metadata['extracted_tables'] = existing_tables + processed_tables
            logger.info(f"[Stage H2] H1処理済み表をマージ: {len(processed_tables)}表")

        # 処理統計
        stats = h1_result.get('statistics', {})
        if stats:
            metadata['_h1_statistics'] = stats

        result['metadata'] = metadata
        return result

    def _generate_audit_canonical_text(
        self,
        result: Dict[str, Any],
        combined_text: str,
        source_inventory: List[Dict]
    ) -> str:
        """監査用正本テキストを生成"""
        parts = []

        title = result.get('title', '')
        if title:
            parts.append(f"# {title}\n")

        metadata = result.get('metadata', {})
        basic_info = metadata.get('basic_info', {})
        if basic_info:
            parts.append("## 基本情報")
            for key, value in basic_info.items():
                if value:
                    parts.append(f"- {key}: {value}")
            parts.append("")

        summary = result.get('summary', '')
        if summary:
            parts.append("## 要約")
            parts.append(summary)
            parts.append("")

        articles = metadata.get('articles', [])
        if articles:
            parts.append("## 記事・お知らせ")
            for article in articles:
                title_a = article.get('title', '')
                body = article.get('body', '')
                if title_a:
                    parts.append(f"### {title_a}")
                if body:
                    parts.append(body)
                parts.append("")

        calendar_events = result.get('calendar_events', [])
        if calendar_events:
            parts.append("## カレンダーイベント")
            for event in calendar_events:
                date = event.get('event_date', '')
                name = event.get('event_name', '')
                parts.append(f"- {date}: {name}")
            parts.append("")

        tasks = result.get('tasks', [])
        if tasks:
            parts.append("## タスク")
            for task in tasks:
                name = task.get('task_name', '')
                deadline = task.get('deadline', '')
                parts.append(f"- {name} (期限: {deadline})")
            parts.append("")

        # 表データ（H1で処理したもの）
        extracted_tables = metadata.get('extracted_tables', [])
        if extracted_tables:
            parts.append("## 抽出された表")
            for tbl in extracted_tables[:5]:
                ref_id = tbl.get('ref_id', '')
                title = tbl.get('table_title', '表')
                row_count = tbl.get('row_count', 0)
                parts.append(f"- {ref_id}: {title} ({row_count}行)")
            if len(extracted_tables) > 5:
                parts.append(f"  ... 他 {len(extracted_tables) - 5} 表")

        if source_inventory:
            parts.append("\n## 参照元（REF_ID）")
            for item in source_inventory[:10]:
                ref_id = item.get('ref_id', '')
                text = item.get('text', '')[:100]
                parts.append(f"- {ref_id}: {text}...")
            if len(source_inventory) > 10:
                parts.append(f"  ... 他 {len(source_inventory) - 10} 件")

        return '\n'.join(parts)

    def _tables_to_text(
        self,
        table_inventory: List[Dict],
        h1_result: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        【Ver 6.7】表データをテキスト化（タイトル生成用）

        テキストが空でも表があればタイトル生成できるよう、
        表の内容を簡易Markdown形式でテキスト化する。

        Args:
            table_inventory: Stage G の表目録
            h1_result: Stage H1 の結果

        Returns:
            表の内容をテキスト化した文字列
        """
        texts = []

        # table_inventory から
        for tbl in (table_inventory or []):
            t_lines = []
            title = tbl.get('title', '') or tbl.get('anchor_id', '無題')
            t_lines.append(f"## 表: {title}")

            # ヘッダー
            x_headers = tbl.get('x_headers', []) or tbl.get('columns', [])
            y_headers = tbl.get('y_headers', [])
            if x_headers:
                t_lines.append(f"列見出し: {', '.join(map(str, x_headers))}")
            if y_headers:
                t_lines.append(f"行見出し: {', '.join(map(str, y_headers[:10]))}{'...' if len(y_headers) > 10 else ''}")

            # セルデータ (tagged_texts)
            tagged_texts = tbl.get('tagged_texts', [])
            if tagged_texts:
                t_lines.append(f"データ件数: {len(tagged_texts)}セル")
                # 最初の10件をサンプル出力
                for i, tt in enumerate(tagged_texts[:10]):
                    x = tt.get('x_header', '')
                    y = tt.get('y_header', '')
                    text = tt.get('text', '')
                    t_lines.append(f"  - [{y}][{x}]: {text}")
                if len(tagged_texts) > 10:
                    t_lines.append(f"  ... 他 {len(tagged_texts) - 10}件")

            # 行データ (rows)
            rows = tbl.get('rows', [])
            if rows and not tagged_texts:
                t_lines.append(f"行数: {len(rows)}")
                for i, row in enumerate(rows[:5]):
                    t_lines.append(f"  - {row}")
                if len(rows) > 5:
                    t_lines.append(f"  ... 他 {len(rows) - 5}行")

            texts.append('\n'.join(t_lines))

        # H1 の processed_tables から
        if h1_result:
            for tbl in h1_result.get('processed_tables', []):
                t_lines = []
                anchor_id = tbl.get('anchor_id', '無題')
                t_lines.append(f"## 表: {anchor_id}")

                tagged_texts = tbl.get('tagged_texts', [])
                if tagged_texts:
                    t_lines.append(f"データ件数: {len(tagged_texts)}セル")
                    for i, tt in enumerate(tagged_texts[:10]):
                        x = tt.get('x_header', '')
                        y = tt.get('y_header', '')
                        text = tt.get('text', '')
                        t_lines.append(f"  - [{y}][{x}]: {text}")
                    if len(tagged_texts) > 10:
                        t_lines.append(f"  ... 他 {len(tagged_texts) - 10}件")

                texts.append('\n'.join(t_lines))

        return '\n\n'.join(texts)
