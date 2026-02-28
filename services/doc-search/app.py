"""
Flask Web Application
質問・回答システムのWebインターフェース
"""
import os
import sys
from pathlib import Path

# プロジェクトルートをPythonパスに追加（ローカル実行時用）
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from typing import Dict, List, Any, Optional
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# クライアントの遅延初期化（Cloud Run起動高速化）
db_client = None
llm_client = None
query_expander = None


def get_clients():
    """クライアントを初回アクセス時に初期化（遅延読み込み）"""
    global db_client, llm_client, query_expander

    if db_client is None:
        print("[INFO] クライアントを初期化中...")
        # 遅延import（起動高速化）
        from shared.common.database.client import DatabaseClient
        from shared.ai.llm_client.llm_client import LLMClient
        from shared.common.utils.query_expansion import QueryExpander

        db_client = DatabaseClient(use_service_role=True)
        llm_client = LLMClient()
        query_expander = QueryExpander(llm_client=llm_client)
        print("[INFO] クライアント初期化完了")

    return db_client, llm_client, query_expander


@app.route('/')
def index():
    """メインページ"""
    return render_template('index.html')


@app.route('/api/filters', methods=['GET'])
def get_filters():
    """
    フィルタオプション取得API（階層構造対応）
    workspace（親）→ doc_type（子）の階層データを返す
    """
    try:
        # クライアント取得（遅延初期化）
        db_client, _, _ = get_clients()

        # workspace別のdoc_type階層構造を取得
        hierarchy = db_client.get_workspace_hierarchy()

        # 階層構造をリスト形式に変換（フロントエンド用）
        workspace_list = []
        for workspace, doc_types in hierarchy.items():
            workspace_list.append({
                'name': workspace,
                'doc_types': doc_types
            })

        print(f"[DEBUG] フィルタ取得: {len(workspace_list)} workspaces（階層構造）")

        return jsonify({
            'success': True,
            'hierarchy': workspace_list
        })
    except Exception as e:
        print(f"[ERROR] フィルタ取得エラー: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/search', methods=['POST'])
def search_documents():
    """
    ベクトル検索API（クエリ拡張対応 + 複数フィルタ対応）
    ユーザーの質問から関連文書を検索
    """
    try:
        # クライアント取得（遅延初期化）
        db_client, llm_client, query_expander = get_clients()

        data = request.get_json()
        query = data.get('query', '')
        # リランク機能のため、フロントエンドの指定を尊重（最大50件まで）
        requested_limit = data.get('limit', 3)
        limit = min(requested_limit, 50)  # 50件取得→高精度な5件にリランク可能

        # ✅ 配列で受け取る（後方互換性のため単一値もサポート）
        workspaces = data.get('workspaces', [])
        doc_types = data.get('doc_types', [])

        # 後方互換性: 単一のworkspaceパラメータもサポート
        if not workspaces and data.get('workspace'):
            workspaces = [data.get('workspace')]

        enable_query_expansion = data.get('enable_query_expansion', False)  # デフォルトで無効
        threshold = float(data.get('threshold', 0.4))  # 足切りスコア閾値

        print(f"[DEBUG] 検索リクエスト: query='{query}', limit={limit}, workspaces={workspaces}, doc_types={doc_types}, threshold={threshold}")

        if not query:
            return jsonify({'success': False, 'error': 'クエリが空です'}), 400

        # ユーザーコンテキストを読み込み、関連情報を抽出（検索用：軽量）
        from shared.common.utils.context_extractor import ContextExtractor
        from shared.common.config.yaml_loader import load_user_context

        user_context = load_user_context()
        context_extractor = ContextExtractor(user_context)
        extracted_context = context_extractor.extract_relevant_context(
            query,
            include_schedules=False  # 検索時はスケジュール不要
        )
        context_string = context_extractor.build_search_context_string(extracted_context)

        # クエリ拡張を適用（有効な場合）
        expanded_query = query
        expansion_info = None

        # ユーザーコンテキストがあればクエリに追加
        if context_string:
            expanded_query = f"{query} {context_string}"
            print(f"[DEBUG] コンテキスト追加: '{query}' → '{expanded_query}'")

        if enable_query_expansion:
            expansion_result = query_expander.expand_query(expanded_query)
            if expansion_result.get('expansion_applied'):
                expanded_query = expansion_result.get('expanded_query', expanded_query)
                expansion_info = {
                    'original': query,
                    'expanded': expanded_query,
                    'keywords': expansion_result.get('keywords', [])
                }
                print(f"[DEBUG] クエリ拡張適用: '{query}' → '{expanded_query}'")
            else:
                print(f"[DEBUG] クエリ拡張スキップ: '{expanded_query}'")

        # Embeddingを生成（拡張されたクエリを使用）
        embedding = llm_client.generate_embedding(expanded_query)

        # ✅ 時系列フィルタを検出
        date_filter = _detect_date_filter(query)
        print(f"[DEBUG] 時系列フィルタ検出: {date_filter}")

        # ✅ クエリタイプを検出
        query_type_info = _detect_query_type(query)
        print(f"[DEBUG] クエリタイプ検出: {query_type_info['type']} (focus: {query_type_info['focus']})")

        # ✅ クロスリファレンスを検出
        referenced_file = _detect_cross_reference(query)
        cross_reference_results = []
        if referenced_file:
            print(f"[DEBUG] クロスリファレンス検出: {referenced_file}")
            # 参照されたファイルを検索
            try:
                cross_ref_response = db_client.client.table('Rawdata_FILE_AND_MAIL').select('*').ilike('file_name', f'%{referenced_file}%').limit(3).execute()
                if cross_ref_response.data:
                    # 検索結果の形式に変換
                    for doc in cross_ref_response.data:
                        cross_reference_results.append({
                            'id': doc.get('id'),
                            'file_name': doc.get('file_name'),
                            'doc_type': doc.get('doc_type'),
                            'workspace': doc.get('workspace'),
                            'document_date': doc.get('document_date'),
                            'metadata': doc.get('metadata', {}),
                            'summary': doc.get('summary'),
                            'similarity': 1.0,  # 最高スコア（参照されたファイル）
                            'is_cross_reference': True  # クロスリファレンスフラグ
                        })
                    print(f"[DEBUG] クロスリファレンス結果: {len(cross_reference_results)} 件")
            except Exception as e:
                print(f"[WARNING] クロスリファレンス検索エラー: {e}")

        # ベクトル検索を実行（同期ラッパーを使用）
        # 拡張されたクエリをテキスト検索にも使用
        results = db_client.search_documents_sync(
            expanded_query,
            embedding,
            limit,
            doc_types if doc_types else None,
            date_filter=date_filter,
            threshold=threshold,
        )

        # ✅ クロスリファレンス結果を先頭に追加
        if cross_reference_results:
            # 重複を避ける：クロスリファレンス結果と同じIDのものは除外
            cross_ref_ids = {doc['id'] for doc in cross_reference_results}
            results = [doc for doc in results if doc.get('id') not in cross_ref_ids]
            # クロスリファレンス結果を先頭に
            results = cross_reference_results + results
            print(f"[DEBUG] クロスリファレンス結果を先頭に追加: 合計 {len(results)} 件")

        print(f"[DEBUG] 検索結果: {len(results)} 件（doc_types={doc_types}）")

        print(f"[DEBUG] 最終検索結果: {len(results)} 件返却")

        response_data = {
            'success': True,
            'results': results,
            'count': len(results),
            'query_type': query_type_info  # クエリタイプ情報を含める
        }

        # クエリ拡張情報を含める（デバッグ用）
        if expansion_info:
            response_data['query_expansion'] = expansion_info

        return jsonify(response_data)

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/answer', methods=['POST'])
def generate_answer():
    """
    回答生成API（構成フロー対応）
    検索結果を元にAIで自然な回答を生成
    """
    try:
        # クライアント取得（遅延初期化）
        db_client, llm_client, _ = get_clients()

        data = request.get_json()
        query = data.get('query', '')
        documents = data.get('documents', [])
        flow_id = data.get('flow', 'flash-x1')  # ユーザーが選択した構成フロー
        model_override = data.get('model')       # モデル直接指定（flowより優先）
        max_context_chars = data.get('max_context_chars')  # トークン上限（文字数）

        if not query:
            return jsonify({'success': False, 'error': 'クエリが空です'}), 400

        # ✅ 構成フローを取得（model直接指定がある場合はそちらを優先）
        from shared.common.config.model_tiers import ResearchFlow
        if model_override:
            steps = [model_override]
            print(f"[INFO] モデル直接指定: {model_override}")
        else:
            flow_config = ResearchFlow.get_flow(flow_id)
            steps = flow_config.get('steps', ['gemini-2.5-flash'])

        print(f"[INFO] 構成フロー実行: {flow_id} ({len(steps)}ステップ)")

        # ✅ 構成フローの実行
        current_query = query
        current_documents = documents

        for step_idx, model_name in enumerate(steps, 1):
            print(f"[INFO] ステップ {step_idx}/{len(steps)}: {model_name}")

            # 2ステップ目以降は、前の回答を使ってクエリを改善
            if step_idx > 1 and 'answer' in locals():
                # クエリ改善プロンプト
                refinement_prompt = f"""以下のユーザーの質問と、これまでの回答を踏まえて、より的確な検索クエリを生成してください。

【元の質問】
{query}

【これまでの回答】
{answer}

【指示】
- 元の質問の意図を保ちつつ、より具体的で詳細な検索キーワードを含むクエリを生成してください
- 回答から得られた重要なキーワードを追加してください
- 改善されたクエリのみを出力してください（説明は不要）

【改善されたクエリ】"""

                refinement_response = llm_client.call_model(
                    tier="ui_response",
                    prompt=refinement_prompt,
                    model_name=model_name
                )

                if refinement_response.get('success'):
                    current_query = refinement_response.get('content', current_query).strip()
                    print(f"[INFO] クエリ改善: '{query}' → '{current_query}'")

                    # 改善されたクエリで再検索
                    embedding = llm_client.generate_embedding(current_query)
                    current_documents = db_client.search_documents_sync(
                        current_query,
                        embedding,
                        limit=5,
                        doc_types=None
                    )
                    print(f"[INFO] 再検索結果: {len(current_documents)} 件")

            # 現在のステップで回答生成
            answer, model_used = _generate_answer_with_model(
                llm_client,
                model_name,
                query,
                current_documents,
                is_final_step=(step_idx == len(steps)),
                max_context_chars=max_context_chars,
            )

            if not answer:
                return jsonify({
                    'success': False,
                    'error': f'ステップ{step_idx}で回答生成に失敗しました'
                }), 500

        # 最終回答を返す
        return jsonify({
            'success': True,
            'answer': answer,
            'model': model_used,
            'provider': 'gemini',
            'flow': flow_id,
            'steps': len(steps)
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def _generate_answer_with_model(
    llm_client,
    model_name: str,
    query: str,
    documents: List[Dict[str, Any]],
    is_final_step: bool = True,
    max_context_chars: Optional[int] = None,
) -> tuple:
    """
    指定されたモデルで回答を生成

    Args:
        llm_client: LLMクライアント
        model_name: 使用するモデル名
        query: ユーザーの質問
        documents: 検索結果
        is_final_step: 最終ステップかどうか

    Returns:
        (回答テキスト, モデル名)
    """
    try:
        # ユーザーコンテキストを読み込み、関連情報を抽出（回答生成用：詳細）
        from shared.common.utils.context_extractor import ContextExtractor
        from shared.common.config.yaml_loader import load_user_context

        user_context = load_user_context()
        context_extractor = ContextExtractor(user_context)
        extracted_context = context_extractor.extract_relevant_context(
            query,
            include_schedules=True  # 回答生成時はスケジュールも含める
        )
        user_context_prompt = context_extractor.build_answer_context_string(extracted_context)

        # ドキュメントコンテキストを構築
        context = _build_context(documents)

        import sys
        print(f"[DEBUG] 文書数: {len(documents)}件", flush=True, file=sys.stderr)
        print(f"[DEBUG] コンテキスト文字数: {len(context)}文字", flush=True, file=sys.stderr)

        # コンテキスト長を決定（優先順位: 明示指定 > モデル別デフォルト）
        is_lite = 'lite' in model_name
        default_max = 30000 if is_lite else 50000
        MAX_CONTEXT_LENGTH = int(max_context_chars) if max_context_chars else default_max
        max_output_tokens_override = 8192 if is_lite else None  # Liteは出力も制限

        if len(context) > MAX_CONTEXT_LENGTH:
            context = context[:MAX_CONTEXT_LENGTH] + "\n\n[... 以降は省略されました ...]"
            print(f"[WARNING] コンテキストを切り詰めました: {MAX_CONTEXT_LENGTH} 文字（モデル: {model_name}）")

        # プロンプトを作成（Phase 2.2.3: 構造的クエリ対応 + ユーザーコンテキスト追加）
        prompt_parts = []

        prompt_parts.append("以下の文書情報を参考に、ユーザーの質問に日本語で回答してください。")

        # ユーザーコンテキストがあれば追加
        if user_context_prompt:
            prompt_parts.append(f"\n{user_context_prompt}\n")

        prompt_parts.append(f"""
【質問】
{query}

【参考文書】
{context}

【回答の条件】
- **最重要：質問「{query}」に直接関連する情報は、具体的かつ詳細に回答してください**
  * 課題名、ファイル名、期限、提出方法、解答の場所など、実行に必要な全ての詳細情報を含めてください
  * ただし、質問に無関係な背景情報（生活態度、安全な生活などの一般的注意事項）は省略してください
  * 例：「宿題をリストにして」→宿題の詳細情報は全て記載、生活態度の一般的注意事項は省略

- **読みやすさを最優先してください**
  * **見出し**を使って科目やカテゴリーごとに明確に区切る（例：### 理科、### 英語）
  * **箇条書きとインデント**を活用して階層構造を明確にする
  * **重要な情報**（期限、提出方法など）は太字で強調する
  * 適切に空白行を入れて視覚的に見やすくする
  * **長くなっても構いません**。詳細かつ読みやすい回答を心がけてください

- **具体的な記載例：**
  * ✅ 良い例：「**理科：** 「植物が生きるしくみ 演習問題(自習課題)」を解く。解答は「【解答】植物がいきるしくみ（自習課題）.pdf」で確認し、丸付けと直しを丁寧に行うこと」
  * ❌ 悪い例：「理科：演習問題を解く」

- 参考文書の情報を基に、正確に回答してください
- **ユーザーの前提情報を考慮してください**（上記に記載された子供の情報、学校や塾のスケジュールなど）
- **重要：ファイル名も重視してください**
  * ユーザーが特定のファイル名を質問している場合（例：「学年通信（29）」）、そのファイル名と完全一致または部分一致する文書を優先的に参照してください
  * ファイル名が一致する文書があれば、必ずその内容を回答に含めてください
- **【表データ】**が含まれている場合、表形式の情報を積極的に活用してください
  * 時間割やスケジュールに関する質問には、表データから該当する科目や予定を抽出して回答してください
  * 議事録の質問には、議題グループや担当者・期限情報を参照してください
  * 複数のクラスやグループがある場合、質問に該当するものを絞り込んで回答してください
- 情報が不足している場合は、その旨を伝えてください
- **参考文書の記載方法：**
  * 回答の最後に「参考文書：」として、実際に使用した全ての文書のファイル名を列挙してください
  * 根拠確認のため、参照した文書は全て記載してください
  * ファイル名のみを記載し、文書番号は不要です

【回答】
""")

        # プロンプトを結合
        prompt = "\n".join(prompt_parts)

        print(f"[DEBUG] 最終プロンプト文字数: {len(prompt)}文字", flush=True, file=sys.stderr)
        print(f"[DEBUG] 推定トークン数: {len(prompt) // 4}トークン（概算）", flush=True, file=sys.stderr)

        # AIモデルで回答生成
        call_kwargs = {"model_name": model_name}
        if max_output_tokens_override:
            call_kwargs["max_tokens"] = max_output_tokens_override
        response = llm_client.call_model(
            tier="ui_response",
            prompt=prompt,
            **call_kwargs
        )

        if not response.get('success'):
            error_msg = response.get('error', 'Unknown error')
            print(f"[ERROR] LLM呼び出し失敗: {error_msg}", flush=True, file=sys.stderr)
            print(f"[ERROR] finish_reason: {response.get('error_details', {}).get('finish_reason_name', 'N/A')}", flush=True, file=sys.stderr)
            print(f"[ERROR] Full response: {response}", flush=True, file=sys.stderr)
            return None, None

        return response.get('content', ''), model_name

    except Exception as e:
        print(f"[ERROR] 回答生成エラー: {e}")
        return None, None


def _format_table_to_markdown(table_data: Dict[str, Any]) -> str:
    """
    表データをMarkdown形式のテーブルに変換（Phase 2.2.3 構造的クエリ対応）

    Args:
        table_data: 表データ（table_type, headers, rows などを含む）

    Returns:
        Markdown形式のテーブル文字列
    """
    try:
        table_type = table_data.get("table_type", "table")
        headers = table_data.get("headers", [])

        # ヘッダー行の構築
        if isinstance(headers, list) and headers:
            # シンプルなリスト形式のヘッダー
            header_line = "| " + " | ".join(str(h) for h in headers) + " |"
            separator_line = "|" + "|".join(["---" for _ in headers]) + "|"
            markdown_lines = [f"\n**表形式データ ({table_type})**\n", header_line, separator_line]
        elif isinstance(headers, dict):
            # 複雑なヘッダー構造（例: class_timetable の classes）
            classes = headers.get("classes", [])
            if classes:
                header_line = "| 日 | " + " | ".join(str(c) for c in classes) + " |"
                separator_line = "|" + "|".join(["---" for _ in range(len(classes) + 1)]) + "|"
                markdown_lines = [f"\n**クラス別時間割 ({table_type})**\n", header_line, separator_line]
            else:
                markdown_lines = [f"\n**表形式データ ({table_type})**\n"]
        else:
            markdown_lines = [f"\n**表形式データ ({table_type})**\n"]

        # 行データの処理
        rows = table_data.get("rows", [])
        if rows:
            for row in rows:
                # 行が辞書形式の場合
                if isinstance(row, dict):
                    # cells フィールドがある場合
                    if "cells" in row:
                        cells = row["cells"]
                        cell_values = []
                        for cell in cells:
                            if isinstance(cell, dict):
                                value = cell.get("value", "")
                                cell_values.append(str(value))
                            else:
                                cell_values.append(str(cell))
                        row_line = "| " + " | ".join(cell_values) + " |"
                        markdown_lines.append(row_line)
                    else:
                        # 通常の辞書行（キー: 値）
                        values = [str(v) for v in row.values()]
                        row_line = "| " + " | ".join(values) + " |"
                        markdown_lines.append(row_line)

        # daily_schedule や agenda_groups などの特殊構造
        if "daily_schedule" in table_data:
            markdown_lines.append("\n**日別スケジュール:**")
            for schedule in table_data["daily_schedule"]:
                day = schedule.get("day", "")
                markdown_lines.append(f"\n- **{day}曜日:**")

                if "class_schedules" in schedule:
                    for class_schedule in schedule["class_schedules"]:
                        class_name = class_schedule.get("class", "")
                        subjects = class_schedule.get("subjects", []) or class_schedule.get("periods", [])
                        markdown_lines.append(f"  - {class_name}: {', '.join(str(s) for s in subjects)}")

        if "agenda_groups" in table_data:
            markdown_lines.append("\n**議題グループ:**")
            for group in table_data["agenda_groups"]:
                topic = group.get("topic", "")
                markdown_lines.append(f"\n- **{topic}:**")
                for item in group.get("items", []):
                    decision = item.get("decision", "")
                    assignee = item.get("assignee", "")
                    deadline = item.get("deadline", "")
                    markdown_lines.append(f"  - {decision} (担当: {assignee}, 期限: {deadline})")

        return "\n".join(markdown_lines)

    except Exception as e:
        return f"\n[表データの変換エラー: {str(e)}]\n"


def _format_metadata(metadata: Dict[str, Any], indent: int = 0) -> str:
    """
    メタデータを見やすく整形（Phase 2.2.3: tables フィールド対応）

    Args:
        metadata: メタデータ辞書
        indent: インデントレベル

    Returns:
        整形された文字列
    """
    if not metadata:
        return ""

    lines = []
    prefix = "  " * indent

    for key, value in metadata.items():
        # Phase 2.2.3: tables フィールドを特別に処理
        if key == "tables" and isinstance(value, list):
            if not value:
                continue
            lines.append(f"{prefix}【表データ】")
            for idx, table in enumerate(value, 1):
                if isinstance(table, dict):
                    # 表をMarkdown形式に変換
                    markdown_table = _format_table_to_markdown(table)
                    lines.append(markdown_table)
            continue

        # 通常のメタデータ処理
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(_format_metadata(value, indent + 1))
        elif isinstance(value, list):
            if not value:
                continue
            lines.append(f"{prefix}{key}:")
            for item in value:
                if isinstance(item, dict):
                    # 辞書のリストの場合、各アイテムを整形
                    for sub_key, sub_value in item.items():
                        lines.append(f"{prefix}  - {sub_key}: {sub_value}")
                else:
                    lines.append(f"{prefix}  - {item}")
        else:
            lines.append(f"{prefix}{key}: {value}")

    return "\n".join(lines)


def _group_documents_by_file(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    同じドキュメントIDのチャンクをグルーピングし、最高スコアのチャンクを代表として返す

    Args:
        documents: 検索結果のリスト

    Returns:
        ドキュメントIDでグルーピングされた結果（最高スコア順）
    """
    from collections import defaultdict

    # ドキュメントIDでグルーピング
    grouped = defaultdict(list)
    for doc in documents:
        doc_id = doc.get('id')
        if doc_id:
            grouped[doc_id].append(doc)

    # 各ドキュメントグループから最高スコアのチャンクを選択
    result = []
    for doc_id, chunks in grouped.items():
        # 類似度が最も高いチャンクを選択
        best_chunk = max(chunks, key=lambda x: x.get('similarity', 0))

        # 同じドキュメントの全チャンクの内容を結合（重複排除）
        all_contents = []
        seen_contents = set()
        for chunk in sorted(chunks, key=lambda x: x.get('similarity', 0), reverse=True):
            content = chunk.get('content') or chunk.get('summary', '')
            if content and content not in seen_contents:
                all_contents.append(content)
                seen_contents.add(content)

        # 最高スコアのチャンクに統合された内容を設定
        if all_contents:
            best_chunk['content'] = '\n\n'.join(all_contents[:3])  # 最大3チャンクまで

        result.append(best_chunk)

    # 類似度順にソート
    result.sort(key=lambda x: x.get('similarity', 0), reverse=True)

    return result


def _detect_date_filter(query: str) -> Optional[str]:
    """
    クエリから時系列フィルタを検出

    Args:
        query: ユーザーのクエリ

    Returns:
        'recent': 最近1週間
        'this_week': 今週
        'this_month': 今月
        'today': 今日
        None: フィルタなし
    """
    import re
    from datetime import datetime, timedelta

    query_lower = query.lower()

    # 最新・最近届いた文書（受信日フィルタとして有効）
    if re.search(r'(最新|最近|さいきん|さいしん|new|latest|recent)', query):
        return 'recent'

    # 今日・今週・今月は文書の受信日でなくコンテンツの内容に関する質問なので
    # ベクトル検索＋LLMに委ねる（受信日フィルタは不適切）
    return None


def _detect_cross_reference(query: str) -> Optional[str]:
    """
    クエリからクロスリファレンス（他の文書への参照）を検出

    Args:
        query: ユーザーのクエリ

    Returns:
        参照されているファイル名、またはNone
    """
    import re

    # パターン1: "○○.pdfを参照", "○○.docxを参照" など
    match = re.search(r'([^\s]+?\.(pdf|docx?|xlsx?|pptx?|txt|png|jpe?g))\s*(を|の)?参照', query, re.IGNORECASE)
    if match:
        return match.group(1)

    # パターン2: "参照: ○○", "参照：○○"
    match = re.search(r'参照[:：]\s*([^\s]+)', query)
    if match:
        return match.group(1)

    # パターン3: "see ○○.pdf", "refer to ○○.pdf"
    match = re.search(r'(?:see|refer to|reference)\s+([^\s]+?\.(pdf|docx?|xlsx?|pptx?|txt|png|jpe?g))', query, re.IGNORECASE)
    if match:
        return match.group(1)

    # パターン4: "○○という文書", "○○というファイル"
    match = re.search(r'([^\s]+)\s*という(?:文書|ファイル|ドキュメント)', query)
    if match:
        return match.group(1)

    return None


def _detect_query_type(query: str) -> Dict[str, Any]:
    """
    クエリのタイプを検出

    Args:
        query: ユーザーのクエリ

    Returns:
        {
            'type': str,  # 'who', 'when', 'what', 'where', 'how', 'why', 'general'
            'focus': str,  # 検出されたフォーカス
            'keywords': List[str]  # 検出されたキーワード
        }
    """
    import re

    query_lower = query.lower()

    # 優先順位順に検出

    # When: 時間に関する質問
    if re.search(r'(いつ|何時|何日|何月|何年|when|期限|締切|締め切り|デッドライン|予定|スケジュール)', query):
        return {
            'type': 'when',
            'focus': 'time_date',
            'keywords': ['document_date', 'deadline', 'schedule', 'weekly_schedule']
        }

    # Who: 人に関する質問
    if re.search(r'(誰|だれ|who|先生|teacher|from|送信者|差出人)', query):
        return {
            'type': 'who',
            'focus': 'person',
            'keywords': ['sender', 'teacher', 'author', 'display_sender']
        }

    # Where: 場所に関する質問
    if re.search(r'(どこ|where|場所|教室|クラス|classroom)', query):
        return {
            'type': 'where',
            'focus': 'location',
            'keywords': ['location', 'classroom', 'place']
        }

    # How: 方法・手順に関する質問
    if re.search(r'(どうやって|どのように|how|方法|手順|やり方)', query):
        return {
            'type': 'how',
            'focus': 'method',
            'keywords': ['procedure', 'method', 'steps']
        }

    # Why: 理由に関する質問
    if re.search(r'(なぜ|why|理由|原因)', query):
        return {
            'type': 'why',
            'focus': 'reason',
            'keywords': ['reason', 'cause', 'purpose']
        }

    # What: 物事・内容に関する質問（デフォルト）
    if re.search(r'(何|なに|what|内容|詳細)', query):
        return {
            'type': 'what',
            'focus': 'content',
            'keywords': ['content', 'subject', 'topic']
        }

    # General: 一般的な質問
    return {
        'type': 'general',
        'focus': 'general',
        'keywords': []
    }


def _build_context(documents: List[Dict[str, Any]]) -> str:
    """
    検索結果からコンテキストを構築。
    chunk_content（Stage J で col_map を使って生成済みのテキスト）をそのまま使用。
    """
    if not documents:
        return "関連する文書が見つかりませんでした。"

    context_parts = []
    total_chars = 0

    for doc_idx, doc in enumerate(documents, 1):
        file_name     = doc.get('file_name', '無題')
        doc_type      = doc.get('doc_type', '不明')
        similarity    = doc.get('similarity', 0)
        document_date = doc.get('document_date', '')
        date_matched  = doc.get('is_date_matched', False)

        all_chunks = doc.get('all_chunks', [])
        if all_chunks:
            parts = [chunk.get('chunk_content', '') for chunk in all_chunks if chunk.get('chunk_content', '')]
            full_text = "\n\n".join(parts)
        else:
            full_text = doc.get('summary', '')

        date_tag = "（日付一致✓）" if date_matched else ""
        context_part = f"""【文書{doc_idx}】{date_tag}
ファイル名: {file_name}
文書タイプ: {doc_type}
日付: {document_date}
スコア: {similarity:.3f}

{full_text}
{"─" * 60}"""

        context_parts.append(context_part)
        total_chars += len(full_text)

    final_context = "\n\n".join(context_parts)
    print(f"[DEBUG] コンテキスト: {len(documents)} 件 / {total_chars} 文字")

    return final_context


@app.route('/api/extract_schedules', methods=['POST'])
def extract_schedules():
    """
    スケジュール抽出API
    指定された条件でドキュメントからスケジュール情報を抽出して返す
    """
    try:
        # クライアント取得
        db_client, _, _ = get_clients()

        data = request.get_json()
        workspace = data.get('workspace')
        doc_types = data.get('doc_types', [])
        start_date = data.get('start_date')  # YYYY-MM-DD形式
        end_date = data.get('end_date')  # YYYY-MM-DD形式
        limit = data.get('limit', 100)

        print(f"[DEBUG] スケジュール抽出リクエスト: workspace={workspace}, doc_types={doc_types}, date_range={start_date}~{end_date}")

        # データベースクエリを構築
        query = db_client.client.table('Rawdata_FILE_AND_MAIL').select('*')

        # フィルタを適用
        if workspace:
            query = query.eq('workspace', workspace)

        if doc_types:
            query = query.in_('doc_type', doc_types)

        # 日付範囲でフィルタ
        if start_date:
            query = query.gte('document_date', start_date)
        if end_date:
            query = query.lte('document_date', end_date)

        # 実行
        response = query.limit(limit).execute()
        documents = response.data if response.data else []

        print(f"[DEBUG] 検索結果: {len(documents)} 件")

        # スケジュール情報を抽出
        schedules = []
        for doc in documents:
            metadata = doc.get('metadata', {})

            # weekly_scheduleを抽出
            weekly_schedule = metadata.get('weekly_schedule', [])
            if weekly_schedule:
                for schedule_item in weekly_schedule:
                    schedules.append({
                        'document_id': doc.get('id'),
                        'file_name': doc.get('file_name'),
                        'doc_type': doc.get('doc_type'),
                        'workspace': doc.get('workspace'),
                        'document_date': doc.get('document_date'),
                        'schedule_type': 'weekly',
                        'schedule_data': schedule_item
                    })

            # text_blocksから日付・時間情報を抽出（オプション）
            text_blocks = metadata.get('text_blocks', [])
            for block in text_blocks:
                title = block.get('title', '')
                content = block.get('content', '')

                # タイトルや内容に日付・時間のキーワードが含まれる場合
                import re
                if re.search(r'(予定|スケジュール|日程|期限|締切|締め切り|\d{1,2}月\d{1,2}日|\d{4}-\d{2}-\d{2})', title + content):
                    schedules.append({
                        'document_id': doc.get('id'),
                        'file_name': doc.get('file_name'),
                        'doc_type': doc.get('doc_type'),
                        'workspace': doc.get('workspace'),
                        'document_date': doc.get('document_date'),
                        'schedule_type': 'text_block',
                        'schedule_data': {
                            'title': title,
                            'content': content
                        }
                    })

        print(f"[DEBUG] 抽出されたスケジュール: {len(schedules)} 件")

        # 日付順にソート
        schedules_sorted = sorted(
            schedules,
            key=lambda x: x.get('document_date') or '9999-12-31'
        )

        return jsonify({
            'success': True,
            'schedules': schedules_sorted,
            'count': len(schedules_sorted)
        })

    except Exception as e:
        print(f"[ERROR] スケジュール抽出エラー: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    """ヘルスチェックエンドポイント"""
    return jsonify({
        'status': 'ok',
        'message': 'Document Q&A System is running'
    })


@app.route('/api/debug/database', methods=['GET'])
def debug_database():
    """データベース接続・検索インデックス診断エンドポイント"""
    result = {}
    errors = {}

    try:
        db_client, llm_client, _ = get_clients()
    except Exception as e:
        return jsonify({'success': False, 'error': f'クライアント初期化失敗: {e}'}), 500

    # 1. Rawdata_FILE_AND_MAIL 件数
    try:
        count_response = db_client.client.table('Rawdata_FILE_AND_MAIL').select('id', count='exact').limit(1).execute()
        result['rawdata_count'] = count_response.count if hasattr(count_response, 'count') else 'unknown'
    except Exception as e:
        errors['rawdata_count'] = str(e)

    # 2. workspace/doc_type 階層
    try:
        hierarchy = db_client.get_workspace_hierarchy()
        result['workspace_count'] = len(hierarchy)
        result['workspaces'] = list(hierarchy.keys())
    except Exception as e:
        errors['hierarchy'] = str(e)

    # 3. 10_ix_search_index 件数・embedding確認
    try:
        idx_response = db_client.client.table('10_ix_search_index').select('id', count='exact').limit(1).execute()
        result['search_index_count'] = idx_response.count if hasattr(idx_response, 'count') else 'unknown'
    except Exception as e:
        errors['search_index_count'] = str(e)

    try:
        sample = db_client.client.table('10_ix_search_index').select('document_id, chunk_type, chunk_content').limit(3).execute()
        result['search_index_sample'] = [
            {'document_id': str(r.get('document_id', ''))[:8], 'chunk_type': r.get('chunk_type'), 'preview': (r.get('chunk_content') or '')[:50]}
            for r in (sample.data or [])
        ]
    except Exception as e:
        errors['search_index_sample'] = str(e)

    # embedding NULL件数確認（NULLならStage K未実行）
    try:
        # embedding IS NOT NULL なチャンク数（直接カラム選択でNULLチェック）
        not_null_resp = db_client.client.table('10_ix_search_index').select('id', count='exact').not_.is_('embedding', 'null').limit(1).execute()
        result['embedding_not_null_count'] = not_null_resp.count if hasattr(not_null_resp, 'count') else 'unknown'
    except Exception as e:
        errors['embedding_not_null_count'] = str(e)

    # 4. unified_search_v2 テスト呼び出し（ダミーembedding）
    try:
        # ゼロベクトルは余弦距離が未定義になるため微小値を使用
        test_embedding = [0.01] * 1536
        test_response = db_client.client.rpc('unified_search_v2', {
            'query_text': 'テスト',
            'query_embedding': test_embedding,
            'match_threshold': -2.0,  # 全件ヒット狙い（コサイン類似度の最小値は-1）
            'match_count': 3,
        }).execute()
        result['unified_search_v2_count'] = len(test_response.data or [])
        result['unified_search_v2_ok'] = True
    except Exception as e:
        result['unified_search_v2_ok'] = False
        errors['unified_search_v2'] = str(e)

    # 5. 環境変数確認
    supabase_url = os.getenv('SUPABASE_URL', 'NOT_SET')
    result['env'] = {
        'supabase_url': supabase_url[:30] + '...' if supabase_url != 'NOT_SET' else 'NOT_SET',
        'supabase_key_set': 'YES' if os.getenv('SUPABASE_KEY') else 'NO',
        'service_role_key_set': 'YES' if os.getenv('SUPABASE_SERVICE_ROLE_KEY') else 'NO',
        'openai_key_set': 'YES' if os.getenv('OPENAI_API_KEY') else 'NO',
    }

    return jsonify({
        'success': True,
        'result': result,
        'errors': errors
    })


@app.route('/api/debug/search-raw', methods=['GET'])
def debug_search_raw():
    """実際のembeddingで unified_search_v2 を直接テストするデバッグエンドポイント"""
    try:
        db_client, llm_client, _ = get_clients()
        query = request.args.get('q', '今週の予定は？')

        # 実際のembeddingを生成
        embedding = llm_client.generate_embedding(query)
        embedding_preview = embedding[:5]  # 最初の5次元だけ確認用

        # threshold=-1.0で直接呼び出し（全件対象）
        resp = db_client.client.rpc('unified_search_v2', {
            'query_text': query,
            'query_embedding': embedding,
            'match_threshold': -1.0,
            'match_count': 5,
        }).execute()

        results_preview = []
        for r in (resp.data or []):
            results_preview.append({
                'document_id': str(r.get('document_id', ''))[:8],
                'file_name': r.get('file_name', '')[:40],
                'combined_score': r.get('combined_score'),
                'raw_similarity': r.get('raw_similarity'),
            })

        return jsonify({
            'success': True,
            'query': query,
            'embedding_dim': len(embedding),
            'embedding_preview': embedding_preview,
            'result_count': len(resp.data or []),
            'results': results_preview,
        })
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/workspaces', methods=['GET'])
def get_workspaces():
    """ワークスペース一覧を取得（検索UI専用）

    【設計方針】
    - このエンドポイントは doc-search UI 専用
    - doc-review, doc-processor にも同名の /api/workspaces が存在するが、
      それぞれ別サービス・別ホストで動作するため衝突しない
    - 将来の統合は行わない（各サービスの独立性を維持）
    """
    try:
        from shared.common.database.client import DatabaseClient
        db = DatabaseClient(use_service_role=True)

        # ワークスペース一覧を取得
        query = db.client.table('Rawdata_FILE_AND_MAIL').select('workspace').execute()

        # ユニークなワークスペースを抽出
        workspaces = set()
        for row in query.data:
            workspace = row.get('workspace')
            if workspace:
                workspaces.add(workspace)

        # ソートしてリスト化
        workspace_list = sorted(list(workspaces))

        return jsonify({
            'success': True,
            'workspaces': workspace_list
        })

    except Exception as e:
        print(f"[ERROR] ワークスペース取得エラー: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


if __name__ == '__main__':
    # 開発環境での実行
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
