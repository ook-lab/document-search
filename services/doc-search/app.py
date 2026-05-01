"""
Flask Web Application
質問・回答システムのWebインターフェース
"""
import os
import sys
from pathlib import Path

# 同梱 shared/ を優先しつつ、ローカルでルートスクリプトを使う場合はルートも追加
_service_dir = Path(__file__).resolve().parent
project_root = _service_dir.parent.parent
if str(_service_dir) not in sys.path:
    sys.path.insert(0, str(_service_dir))
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

        # 3階層構造をリスト形式に変換（フロントエンド用）
        workspace_list = []
        for person, sources in hierarchy.items():
            workspace_list.append({
                'name': person,
                'sources': [
                    {'name': src, 'categories': cats}
                    for src, cats in sources.items()
                ]
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

        persons    = data.get('persons', [])
        sources    = data.get('sources', [])
        categories = data.get('categories', [])

        enable_query_expansion = data.get('enable_query_expansion', False)  # デフォルトで無効
        threshold = float(data.get('threshold', 0.4))  # 足切りスコア閾値

        print(f"[DEBUG] 検索リクエスト: query='{query}', limit={limit}, persons={persons}, sources={sources}, categories={categories}, threshold={threshold}")

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
            try:
                cross_ref_response = (
                    db_client.client.table('09_unified_documents')
                    .select('id, title, source, person, category, post_at, start_at, meta, ui_data, file_url')
                    .ilike('title', f'%{referenced_file}%')
                    .limit(3)
                    .execute()
                )
                if cross_ref_response.data:
                    for doc in cross_ref_response.data:
                        raw_date = doc.get('post_at') or doc.get('start_at')
                        cross_reference_results.append({
                            'id':            doc.get('id'),
                            'title':         doc.get('title'),
                            'source':        doc.get('source'),
                            'person':        doc.get('person'),
                            'category':      doc.get('category'),
                            'document_date': raw_date[:10] if isinstance(raw_date, str) else None,
                            'meta':          doc.get('meta', {}),
                            'ui_data':       doc.get('ui_data'),
                            'file_url':      doc.get('file_url'),
                            'similarity':    1.0,
                            'is_cross_reference': True,
                        })
                    print(f"[DEBUG] クロスリファレンス結果: {len(cross_reference_results)} 件")
            except Exception as e:
                print(f"[WARNING] クロスリファレンス検索エラー: {e}")

        # ベクトル検索を実行
        results = db_client.search_documents_sync(
            expanded_query,
            embedding,
            limit,
            sources=sources if sources else None,
            persons=persons if persons else None,
            category=categories if categories else None,
            date_filter=date_filter,
            threshold=threshold,
        )

        # GOOGLE_CALENDAR を先頭に引き上げ（同スコア帯では最優先）
        cal_results   = [d for d in results if d.get('source') == 'Googleカレンダー']
        other_results = [d for d in results if d.get('source') != 'Googleカレンダー']
        results = cal_results + other_results

        # ✅ クロスリファレンス結果を先頭に追加
        if cross_reference_results:
            # 重複を避ける：クロスリファレンス結果と同じIDのものは除外
            cross_ref_ids = {doc['id'] for doc in cross_reference_results}
            results = [doc for doc in results if doc.get('id') not in cross_ref_ids]
            # クロスリファレンス結果を先頭に
            results = cross_reference_results + results
            print(f"[DEBUG] クロスリファレンス結果を先頭に追加: 合計 {len(results)} 件")

        print(f"[DEBUG] 検索結果: {len(results)} 件（sources={sources}）")

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
    回答生成API

    compress-1step: 回答生成+Evidence同時（1回呼び出し）
    compress-2step: Evidence整理 → 回答生成（2回呼び出し）
    compress-3step: Evidence抽出 → 論点整理 → 最終回答（3回呼び出し）

    共通前処理:
      Step0: クエリ改善（Flash-lite固定）
      RAG:   改善クエリでベクトル+全文検索+rerank
    """
    try:
        import uuid as _uuid
        db_client, llm_client, _ = get_clients()

        data = request.get_json()
        query = data.get('query', '')
        flow_id = data.get('flow', 'compress-1step')
        max_context_chars = int(data.get('max_context_chars') or 30000)
        persons    = data.get('persons', [])
        sources    = data.get('sources', [])
        categories = data.get('categories', [])

        if not query:
            return jsonify({'success': False, 'error': 'クエリが空です'}), 400

        # リクエストID生成（このリクエスト内の全AI呼び出しを紐づける）
        request_id = str(_uuid.uuid4())

        from shared.common.config.model_tiers import ResearchFlow
        flow_config = ResearchFlow.get_flow(flow_id)
        steps = flow_config.get('steps')
        rounds = flow_config.get('rounds', 1)

        today = datetime.now().strftime('%Y-%m-%d')

        # Step0: クエリ改善（Flash-lite固定）
        refined = _refine_query(
            llm_client, query, today,
            log_context={'app': 'doc-search', 'stage': 'search-refine', 'session_id': request_id},
        )
        refined_query = refined["query"]
        date_range = refined["date_range"]
        print(f"[INFO] クエリ改善: '{query}' → '{refined_query}' / date_range={date_range}", flush=True)
        print(f"[INFO] フィルタ: persons={persons}, sources={sources}, categories={categories}", flush=True)

        # RAG検索
        embedding = llm_client.generate_embedding(refined_query)
        search_limit = max(10, max_context_chars // 2000)
        search_results = db_client.search_documents_sync(
            refined_query, embedding, limit=search_limit,
            sources=sources if sources else None,
            persons=persons if persons else None,
            category=categories if categories else None,
            date_range=date_range,
        )
        print(f"[INFO] RAG検索: {len(search_results)}件", flush=True)

        # コンテキスト構築・切り詰め
        context = _build_context(search_results)
        if len(context) > max_context_chars:
            context = context[:max_context_chars]
        print(f"[INFO] コンテキスト: {len(context)}字 / フロー: {flow_id}", flush=True)

        # フロー別実行
        if rounds == 1:
            # 1段: 回答生成+Evidence同時
            print(f"[INFO] 1段実行 ({steps[0]})", flush=True)
            answer = _answer_1step(
                llm_client, steps[0], query, context,
                log_context={'app': 'doc-search', 'stage': 'search-step1', 'session_id': request_id},
            )

        elif rounds == 2:
            # 2段: Evidence整理 → 回答生成
            step1_limit = int(max_context_chars * 0.33)
            print(f"[INFO] 2段Step1 ({steps[0]}): →{step1_limit}字上限", flush=True)
            evidence_list = _evidence_1step(
                llm_client, steps[0], context, step1_limit,
                log_context={'app': 'doc-search', 'stage': 'search-step1', 'session_id': request_id},
            )
            print(f"[INFO] 2段Step2 ({steps[1]}): 内容依存", flush=True)
            answer = _answer_from_evidence(
                llm_client, steps[1], query, evidence_list,
                log_context={'app': 'doc-search', 'stage': 'search-step2', 'session_id': request_id},
            )

        else:
            # 3段: Evidence抽出 → 論点整理 → 最終回答
            step1_limit = int(max_context_chars * 0.4)
            print(f"[INFO] 3段Step1 ({steps[0]}): →{step1_limit}字上限", flush=True)
            step1_output = _compress_step1(
                llm_client, steps[0], query, context, step1_limit,
                log_context={'app': 'doc-search', 'stage': 'search-step1', 'session_id': request_id},
            )

            step2_limit = int(step1_limit * 0.33)
            print(f"[INFO] 3段Step2 ({steps[1]}): →{step2_limit}字上限", flush=True)
            step2_output = _compress_step2(
                llm_client, steps[1], query, step1_output, step2_limit,
                log_context={'app': 'doc-search', 'stage': 'search-step2', 'session_id': request_id},
            )

            print(f"[INFO] 3段Step3 ({steps[2]}): 内容依存", flush=True)
            answer = _compress_step3(
                llm_client, steps[2], query, step2_output,
                log_context={'app': 'doc-search', 'stage': 'search-step3', 'session_id': request_id},
            )

        if not answer:
            return jsonify({'success': False, 'error': '回答生成に失敗しました'}), 500

        return jsonify({
            'success': True,
            'answer': answer,
            'model': steps[-1],
            'provider': 'gemini',
            'flow': flow_id,
            'steps': rounds,
            'refined_query': refined_query,
            'date_range': date_range,
        })

    except Exception as e:
        import traceback
        print(f"[ERROR] generate_answer: {e}\n{traceback.format_exc()}", flush=True)
        return jsonify({'success': False, 'error': str(e)}), 500


def _answer_1step(llm_client, model_name: str, query: str, context: str, log_context: dict = None) -> str:
    """
    1段: 回答生成+Evidence抽出を同時実行

    抽象化・根拠なし断定は禁止。各根拠にSourceを付けて出力する。
    """
    try:
        from shared.common.utils.context_extractor import ContextExtractor
        from shared.common.config.yaml_loader import load_user_context
        user_context = load_user_context()
        ctx_extractor = ContextExtractor(user_context)
        extracted = ctx_extractor.extract_relevant_context(query, include_schedules=True)
        user_ctx = ctx_extractor.build_answer_context_string(extracted)
    except Exception:
        user_ctx = ""

    prompt_parts = []
    if user_ctx:
        prompt_parts.append(user_ctx)

    prompt_parts.append(f"""あなたはRAG回答エンジンです。
以下の文書チャンクを使い、ユーザーの質問に回答してください。

【質問】
{query}

【文書】
{context}

【ルール】
- Evidenceが存在する内容のみ回答する（新しい主張の創作禁止）
- 根拠なし断定禁止
- Evidenceは原文から1〜2文抜粋し、Sourceを必ず付ける
- 不明・不足情報は「不確実性」欄に明示する
- 重要情報（期限・場所・提出方法）は太字で強調する
- カレンダー確定情報がある場合は最優先で反映し、矛盾は「⚠️ 注記：」で明示する

【出力形式】
回答:
<自然文回答>

Evidence:
- 「原文抜粋」 (タイトル/Source)
- 「原文抜粋」 (タイトル/Source)

不確実性:
<不足情報や条件。なければ「なし」>
""")

    response = llm_client.call_model(
        tier="ui_response",
        prompt="\n".join(prompt_parts),
        model_name=model_name,
        log_context=log_context,
    )
    if response.get('success'):
        return response.get('content', '').strip()
    return ''


def _evidence_1step(llm_client, model_name: str, context: str, output_limit: int, log_context: dict = None) -> str:
    """
    2段Step1: Evidence整理+Topicタグ付け（抽象化禁止）

    原文から使える情報を抜き出し、Topicラベルを付けて並べる。
    要約・言い換え禁止。Source必須。
    """
    prompt = f"""あなたはRAGのEvidence抽出器です。
以下の文書チャンクから、回答に使える情報を抽出してください。

【文書】
{context}

【ルール】
- 要約・抽象化・言い換え禁止
- Evidenceは原文から1〜2文の抜粋のみ
- Topicタグを付ける（日程/範囲/持ち物/注意事項/例外 など）
- Source（タイトルまたはchunk_id）を必ず付ける
- 新しい主張の創作禁止
- 出力上限: {output_limit}字

【出力形式】
Topic: <ラベル>
Evidence: 「<原文抜粋>」
Source: <タイトル/chunk_id>
Confidence: <0〜1>

【抽出結果】
"""
    response = llm_client.call_model(
        tier="ui_response",
        prompt=prompt,
        model_name=model_name,
        log_context=log_context,
    )
    if response.get('success'):
        return response.get('content', '').strip()
    return context[:output_limit]


def _answer_from_evidence(llm_client, model_name: str, query: str, evidence_list: str, log_context: dict = None) -> str:
    """
    2段Step2: EvidenceリストからユーザーへのRAG回答を生成

    Evidenceなき記述禁止。ここで初めて抽象化OK。
    """
    try:
        from shared.common.utils.context_extractor import ContextExtractor
        from shared.common.config.yaml_loader import load_user_context
        user_context = load_user_context()
        ctx_extractor = ContextExtractor(user_context)
        extracted = ctx_extractor.extract_relevant_context(query, include_schedules=True)
        user_ctx = ctx_extractor.build_answer_context_string(extracted)
    except Exception:
        user_ctx = ""

    prompt_parts = []
    if user_ctx:
        prompt_parts.append(user_ctx)

    prompt_parts.append(f"""以下のEvidenceリストを基に、ユーザーの質問に回答してください。

【質問】
{query}

【Evidenceリスト】
{evidence_list}

【ルール】
- Evidenceがある内容のみ回答する（創作禁止）
- 不確実・不足情報は「不確実性」欄に明示する
- 見出し・箇条書きを活用して読みやすく整形する
- 重要情報（期限・場所・提出方法）は太字で強調する
- カレンダー確定情報がある場合は最優先で反映し、矛盾は「⚠️ 注記：」で明示する

【出力形式】
回答:
<自然文回答>

根拠:
- <Source>
- <Source>

不確実性:
<なければ「なし」>
""")

    response = llm_client.call_model(
        tier="ui_response",
        prompt="\n".join(prompt_parts),
        model_name=model_name,
        log_context=log_context,
    )
    if response.get('success'):
        return response.get('content', '').strip()
    return ''


def _refine_query(llm_client, query: str, today: str, log_context: dict = None) -> Dict[str, Any]:
    """
    Step0: クエリ改善（Flash-lite固定）

    相対日を絶対日付レンジに変換し、検索に適した形に整形する。
    出力文字数は入力と同程度（最大1.5倍まで）。

    Returns:
        {
            "query": "整形後の質問（YYYY-MM-DD形式の日付を含む）",
            "date_range": "YYYY-MM-DD..YYYY-MM-DD"  # 日付なければ空文字
        }
    """
    import json as _json
    max_len = int(len(query) * 1.5)
    prompt = f"""以下の質問を、検索に適した形に整形し、JSON1行で出力してください。

【ルール】
- 相対日（来週/今週/明日/〇月〇日など）は今日の日付を基準に絶対日付レンジ（YYYY-MM-DD）に変換する
- queryフィールド: 整形後の質問（最大{max_len}字、自然語を保つ・ISO日付を混ぜない）
- date_rangeフィールド: 日付レンジ（YYYY-MM-DD..YYYY-MM-DD）、日付なければ空文字
- JSON以外の出力禁止

今日の日付: {today}
元の質問: {query}
出力例: {{"query":"来週月曜のテストの範囲は","date_range":"2026-03-09..2026-03-09"}}
出力:"""
    response = llm_client.call_model(
        tier="ui_response",
        prompt=prompt,
        model_name="gemini-2.5-flash-lite",
        log_context=log_context,
    )
    if response.get('success'):
        content = response.get('content', '').strip()
        # JSONコードブロックを除去
        content = content.replace('```json', '').replace('```', '').strip()
        try:
            result = _json.loads(content)
            return {
                "query": result.get("query", query),
                "date_range": result.get("date_range", ""),
            }
        except Exception:
            pass
    return {"query": query, "date_range": ""}


def _compress_step1(llm_client, model_name: str, query: str, context: str, output_limit: int, log_context: dict = None) -> str:
    """
    Step1: Evidenceノート生成（抽象要約禁止）

    各文書から質問に関連する情報を抜粋・構造化する。
    重複をまとめ、必ずSourceを付ける。
    """
    prompt = f"""以下の文書群から、質問に関連する情報を構造化して抽出してください。

【質問】
{query}

【文書】
{context}

【ルール】
- 抽象要約禁止。原文の短い抜粋（1〜2文）をEvidenceとして必ず残す
- 重複する情報はまとめる（Evidenceは最大2つ）
- SourceはタイトルまたはDocIDを使う
- 出力形式（1エントリごと）:
  Claim: （短い主張）
  Evidence: （原文抜粋1〜2文）
  Source: （タイトル/ファイル名）
  Tag: （dates/scope/items/rules/exceptions/numbers から該当するもの）
  Confidence: （0〜1）
- 出力上限: {output_limit}字

【抽出結果】
"""
    response = llm_client.call_model(
        tier="ui_response",
        prompt=prompt,
        model_name=model_name,
        log_context=log_context,
    )
    if response.get('success'):
        return response.get('content', '').strip()
    return context[:output_limit]


def _compress_step2(llm_client, model_name: str, query: str, step1_output: str, output_limit: int, log_context: dict = None) -> str:
    """
    Step2: 論点別証拠束への再編（抽象化最小）

    Step1のEvidenceノートを論点(Topic)ごとに再編成する。
    新しい主張の創作禁止。根拠は必ず残す。
    """
    prompt = f"""以下のEvidenceノートを、論点(Topic)ごとに再編成してください。

【質問】
{query}

【Evidenceノート】
{step1_output}

【ルール】
- タグ単位で束ねる（日付/範囲/持ち物/注意事項/例外など）
- 各TopicにKey takeaway + Evidence（抜粋）+ Sourcesを残す
- 新しい主張の創作禁止
- 出力形式:
  Topic: （論点名）
    Key takeaway: （1行の結論）
    Evidence: （抜粋2〜5個）
    Sources: （ファイル名列挙）
- 出力上限: {output_limit}字

【論点別整理】
"""
    response = llm_client.call_model(
        tier="ui_response",
        prompt=prompt,
        model_name=model_name,
        log_context=log_context,
    )
    if response.get('success'):
        return response.get('content', '').strip()
    return step1_output[:output_limit]


def _compress_step3(llm_client, model_name: str, query: str, step2_output: str, log_context: dict = None) -> str:
    """
    Step3: 最終回答生成（ここで初めて抽象化OK）

    Topic別証拠束を基にユーザー向けの自然文にまとめる。
    Evidenceがある内容のみ記載。曖昧な点は明示。
    """
    try:
        from shared.common.utils.context_extractor import ContextExtractor
        from shared.common.config.yaml_loader import load_user_context

        user_context = load_user_context()
        context_extractor = ContextExtractor(user_context)
        extracted_context = context_extractor.extract_relevant_context(
            query, include_schedules=True
        )
        user_context_prompt = context_extractor.build_answer_context_string(extracted_context)
    except Exception:
        user_context_prompt = ""

    prompt_parts = []
    if user_context_prompt:
        prompt_parts.append(user_context_prompt)

    prompt_parts.append(f"""以下のTopic別証拠束を基に、ユーザーの質問に回答してください。

【質問】
{query}

【Topic別証拠束】
{step2_output}

【ルール】
- Evidenceがある内容のみ記載（創作禁止）
- 曖昧・不確かな点は「〜の可能性があります」と明示
- 見出し・箇条書きを活用して読みやすく整形する
- 重要情報（期限・提出方法・場所など）は太字で強調する
- カレンダー確定情報がある場合は最優先で反映し、矛盾は「⚠️ 注記：」で明示する
- 回答末尾に「参考文書：」として使用したファイル名を列挙する
- 長さは内容に応じて調整する（不要な冗長は避け、必要な情報は省略しない）

【回答】
""")

    prompt = "\n".join(prompt_parts)
    response = llm_client.call_model(
        tier="ui_response",
        prompt=prompt,
        model_name=model_name,
        log_context=log_context,
    )
    if response.get('success'):
        return response.get('content', '').strip()
    return ''


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
    all_chunks の chunk_text を連結して使用。
    """
    if not documents:
        return "関連する文書が見つかりませんでした。"

    context_parts = []
    total_chars = 0

    for doc_idx, doc in enumerate(documents, 1):
        title         = doc.get('title', '無題')
        source        = doc.get('source', '不明')
        similarity    = doc.get('similarity', 0)
        document_date = doc.get('document_date', '')
        date_matched  = doc.get('is_date_matched', False)

        all_chunks = doc.get('all_chunks', [])
        if all_chunks:
            parts = [chunk.get('chunk_text', '') for chunk in all_chunks if chunk.get('chunk_text', '')]
            full_text = "\n\n".join(parts)
        else:
            full_text = doc.get('chunk_content', '')

        date_tag = "（日付一致✓）" if date_matched else ""
        is_calendar = (source == 'Googleカレンダー')
        block_header = "【カレンダー確定情報】" if is_calendar else f"【文書{doc_idx}】"
        context_part = f"""{block_header}{date_tag}
タイトル: {title}
ソース: {source}
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
        person     = data.get('person')
        sources    = data.get('sources', [])
        start_date = data.get('start_date')  # YYYY-MM-DD形式
        end_date   = data.get('end_date')    # YYYY-MM-DD形式
        limit      = data.get('limit', 100)

        print(f"[DEBUG] スケジュール抽出リクエスト: person={person}, sources={sources}, date_range={start_date}~{end_date}")

        # データベースクエリを構築
        query = db_client.client.table('09_unified_documents').select(
            'id, title, source, person, category, post_at, start_at, end_at, due_date, ui_data, meta'
        )

        if person:
            query = query.eq('person', person)

        if sources:
            query = query.in_('source', sources)

        # 日付範囲でフィルタ（post_at または start_at）
        if start_date:
            query = query.or_(f'post_at.gte.{start_date},start_at.gte.{start_date}')
        if end_date:
            query = query.or_(f'post_at.lte.{end_date},start_at.lte.{end_date}')

        response = query.limit(limit).execute()
        documents = response.data if response.data else []

        print(f"[DEBUG] 検索結果: {len(documents)} 件")

        import re
        schedules = []
        for doc in documents:
            doc_id    = doc.get('id')
            title     = doc.get('title') or ''
            source    = doc.get('source') or ''
            person_v  = doc.get('person') or ''
            post_at   = doc.get('post_at') or ''
            start_at  = doc.get('start_at') or ''
            ui_data   = doc.get('ui_data') or {}

            # Google Calendar イベントはそのままスケジュールとして扱う
            if source == 'Googleカレンダー':
                schedules.append({
                    'doc_id':       doc_id,
                    'title':        title,
                    'source':       source,
                    'person':       person_v,
                    'document_date': (start_at or post_at)[:10] if (start_at or post_at) else None,
                    'schedule_type': 'calendar_event',
                    'schedule_data': {
                        'start_at': doc.get('start_at'),
                        'end_at':   doc.get('end_at'),
                        'location': doc.get('location'),
                    }
                })
                continue

            # ui_data.sections からキーワードマッチでスケジュール抽出
            sections = ui_data.get('sections', [])
            for section in sections:
                sec_title = section.get('title', '') or ''
                sec_body  = section.get('body', '')  or ''
                combined  = sec_title + ' ' + sec_body
                if re.search(
                    r'(予定|スケジュール|日程|期限|締切|締め切り|\d{1,2}月\d{1,2}日|\d{4}-\d{2}-\d{2})',
                    combined
                ):
                    schedules.append({
                        'doc_id':       doc_id,
                        'title':        title,
                        'source':       source,
                        'person':       person_v,
                        'document_date': (post_at or start_at)[:10] if (post_at or start_at) else None,
                        'schedule_type': 'section',
                        'schedule_data': {
                            'title':   sec_title,
                            'content': sec_body,
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

    # 1. 09_unified_documents 件数
    try:
        count_response = db_client.client.table('09_unified_documents').select('id', count='exact').limit(1).execute()
        result['unified_docs_count'] = count_response.count if hasattr(count_response, 'count') else 'unknown'
    except Exception as e:
        errors['unified_docs_count'] = str(e)

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
        sample = db_client.client.table('10_ix_search_index').select('doc_id, chunk_type, chunk_text').limit(3).execute()
        result['search_index_sample'] = [
            {'doc_id': str(r.get('doc_id', ''))[:8], 'chunk_type': r.get('chunk_type'), 'preview': (r.get('chunk_text') or '')[:50]}
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
                'doc_id': str(r.get('doc_id', ''))[:8],
                'title': (r.get('title') or '')[:40],
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
    - document-hub（doc-processor）にも同名の /api/workspaces が存在するが、
      doc-search は別ホストのため衝突しない
    """
    try:
        from shared.common.database.client import DatabaseClient
        db = DatabaseClient(use_service_role=True)

        # person 一覧を取得（09_unified_documents ベース）
        query = db.client.table('09_unified_documents').select('person').execute()

        persons = set()
        for row in query.data:
            p = row.get('person')
            if p:
                persons.add(p)

        workspace_list = sorted(list(persons))

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
