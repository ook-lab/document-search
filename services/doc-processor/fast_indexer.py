import os
import re
import logging
import fitz  # PyMuPDF
from typing import List, Dict, Any
from datetime import datetime, timezone
from shared.common.database.client import DatabaseClient
from shared.common.processing.metadata_chunker import MetadataChunker
from google import genai

logger = logging.getLogger(__name__)

MARKER_START = "<<<MD_SANDWICH_START>>>"
MARKER_END = "<<<MD_SANDWICH_END>>>"

class FastIndexer:
    def __init__(self):
        self.db = DatabaseClient(use_service_role=True)
        # ユーザー指定により 2.5-flash-lite を固定使用
        self.model_name = "gemini-2.5-flash-lite"
        
        # Google GenAI Client
        api_key = os.environ.get("GOOGLE_API_KEY") or "AIzaSyDiVwSXMSzwtCI02lhIbkw6_04LleMvz2Q"
        self.ai_client = genai.Client(api_key=api_key)

    def process_document(self, pipeline_id: str):
        """
        指定された pipeline_meta ID のドキュメントを高速処理
        """
        try:
            # 1. pipeline_meta を取得
            pm = self.db.client.table('pipeline_meta').select('*').eq('id', pipeline_id).single().execute().data
            if not pm:
                raise ValueError(f"Pipeline meta not found: {pipeline_id}")
            
            drive_file_id = pm.get('drive_file_id')
            raw_table = pm.get('raw_table')
            raw_id = pm.get('raw_id')
            if not raw_table or not raw_id:
                raise ValueError("pipeline_meta missing raw_table or raw_id")

            def _unified_row():
                return (
                    self.db.client.table('09_unified_documents')
                    .select('id, body')
                    .eq('raw_id', raw_id)
                    .eq('raw_table', raw_table)
                    .limit(1)
                    .execute()
                )

            if not drive_file_id:
                # 取得を試みる
                raw_data = self.db.client.table(raw_table).select('file_url').eq('id', raw_id).single().execute().data
                if raw_data and raw_data.get('file_url'):
                    match = re.search(r'/d/([^/]+)', raw_data['file_url'])
                    if match:
                        drive_file_id = match.group(1)
            
            full_markdown = ""

            if drive_file_id:
                # 2. PDF を取得して MD 変換
                from shared.common.connectors.google_drive import GoogleDriveConnector
                drive = GoogleDriveConnector()
                temp_pdf = f"temp_{pipeline_id}.pdf"
                local_path = drive.download_file(drive_file_id, temp_pdf, "./")
                
                # 3. 構造化 Markdown 生成
                full_markdown = self._extract_and_convert_to_md(local_path)
                
                # 一時ファイル削除
                if os.path.exists(local_path):
                    os.remove(local_path)
            else:
                # テキストオンリーの処理
                logger.info(f"Text-only processing for {pipeline_id}")
                # 09_unified_documents（raw と 1:1 の統合行）または raw からテキスト取得
                ud_data = _unified_row().data or []
                if ud_data and ud_data[0].get('body'):
                    full_markdown = ud_data[0]['body']
                else:
                    # raw から汎用的に取得
                    raw_data = self.db.client.table(raw_table).select('*').eq('id', raw_id).single().execute().data
                    # 特定のソースに依存せず、内容が含まれていそうなフィールドを順に試す
                    full_markdown = raw_data.get('description') or raw_data.get('content') or raw_data.get('body') or ""

            if not full_markdown:
                raise ValueError("No content found to index.")

            ud_after = _unified_row().data or []
            if not ud_after:
                raise ValueError(
                    f"No 09_unified_documents row for raw_id={raw_id} raw_table={raw_table}; "
                    "run full pipeline through G31 first."
                )
            unified_doc_id = ud_after[0]['id']

            # 4. メタデータ簡易生成 (要約、タグ、日付)
            metadata_summary = self._generate_metadata_summary(full_markdown)

            # 5. 09_unified_documents の body を更新（統合行を raw で一意に特定）
            self.db.client.table('09_unified_documents') \
                .update({'body': full_markdown}) \
                .eq('raw_id', raw_id) \
                .eq('raw_table', raw_table) \
                .execute()

            # 6. MetadataChunker によるチャンク分割 (doc-processor 仕様)
            # Markdown をセクションごとに分解して MetadataChunker に渡す
            document_data = {
                'file_name': pm.get('source', 'Document'),
                'summary': metadata_summary.get('summary', ''),
                'tags': metadata_summary.get('tags', []),
                'document_date': metadata_summary.get('date', ''),
                'text_blocks': self._md_to_text_blocks(full_markdown)
            }
            
            chunker = MetadataChunker()
            chunk_dicts = chunker.create_metadata_chunks(document_data)
            
            # 7. ベクトル化 & 保存（10_ix_search_index.doc_id は 09_unified_documents.id と一致させる）
            self._vectorize_and_store(unified_doc_id, pm, chunk_dicts)

            # 8. ステータス完了
            self.db.client.table('pipeline_meta') \
                .update({
                    'processing_status': 'completed',
                    'completed_at': datetime.now(timezone.utc).isoformat()
                }) \
                .eq('id', pipeline_id) \
                .execute()

            return True

        except Exception as e:
            logger.error(f"Fast index error for {pipeline_id}: {e}", exc_info=True)
            return False

    def _extract_and_convert_to_md(self, pdf_path: str) -> str:
        doc = fitz.open(pdf_path)
        combined_md = []
        
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            text = page.get_text()
            sandwiches = re.findall(f"{re.escape(MARKER_START)}(.*?){re.escape(MARKER_END)}", text, re.DOTALL)
            
            clean_text = text
            for s in sandwiches:
                clean_text = clean_text.replace(f"{MARKER_START}{s}{MARKER_END}", "\n[EMBEDDED_CONTENT_STUB]\n")
            
            if clean_text.strip() and clean_text.strip() != "[EMBEDDED_CONTENT_STUB]":
                prompt = (
                    "以下のテキストはPDFから抽出した地の文です。構造を保ったまま、適切なMarkdown形式に変換してください。\n"
                    "見出し(#, ##)などを適切に使用してください。表形式(Table)があれば再現してください。\n"
                    "[EMBEDDED_CONTENT_STUB] という文字列は、すでに別途抽出済みの表やコンテンツが入る場所です。そこはそのまま残してください。\n\n"
                    f"テキスト:\n{clean_text}"
                )
                response = self.ai_client.models.generate_content(
                    model=self.model_name,
                    contents=prompt
                )
                page_md = response.text
            else:
                page_md = "\n[EMBEDDED_CONTENT_STUB]\n"
            
            for s in sandwiches:
                page_md = page_md.replace("[EMBEDDED_CONTENT_STUB]", s.strip(), 1)
            
            combined_md.append(f"### Page {page_idx + 1}\n\n" + page_md)
            
        doc.close()
        return "\n\n".join(combined_md)

    def _generate_metadata_summary(self, markdown: str) -> Dict[str, Any]:
        """Markdown から要約、タグ、日付を生成 (2.5-flash-lite)"""
        try:
            prompt = (
                "以下のMarkdownドキュメントを解析し、検索用のメタデータを抽出してください。\n"
                "JSON形式で返してください: { \"summary\": \"(100文字程度の要約)\", \"tags\": [\"タグ1\", \"タグ2\"], \"date\": \"YYYY-MM-DD\" }\n\n"
                f"Markdown:\n{markdown[:4000]}" # 冒頭4000文字
            )
            response = self.ai_client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            import json
            return json.loads(response.text)
        except Exception as e:
            logger.warning(f"Metadata generation failed: {e}")
            return {'summary': '', 'tags': [], 'date': ''}

    def _md_to_text_blocks(self, markdown: str) -> List[Dict[str, Any]]:
        """Markdown をセクション単位の text_blocks に変換"""
        blocks = []
        # ### Page N や ## 見出しで分割
        sections = re.split(r'\n(?=#{1,3}\s)', markdown)
        for section in sections:
            if not section.strip():
                continue
            lines = section.strip().split('\n')
            title = lines[0].replace('#', '').strip()
            content = '\n'.join(lines[1:]).strip()
            if content:
                blocks.append({'title': title, 'content': content})
        return blocks

    def _vectorize_and_store(self, unified_doc_id: str, pm: Dict, chunk_dicts: List[Dict[str, Any]]):
        from openai import OpenAI
        oa_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        
        # 既存削除
        self.db.client.table('10_ix_search_index').delete().eq('doc_id', unified_doc_id).execute()

        for chunk_data in chunk_dicts:
            chunk_text = chunk_data["chunk_text"]
            chunk_type = chunk_data["chunk_type"]
            weight = chunk_data.get("search_weight", 1.0)
            i = chunk_data["chunk_index"]
            
            res = oa_client.embeddings.create(
                model="text-embedding-3-small",
                input=chunk_text,
                dimensions=1536
            )
            embedding = res.data[0].embedding
            
            self.db.client.table('10_ix_search_index').insert({
                'doc_id': unified_doc_id,
                'person': pm.get('person'),
                'source': pm.get('source'),
                'category': pm.get('raw_table'),
                'chunk_index': i,
                'chunk_text': chunk_text,
                'embedding': embedding,
                'chunk_type': chunk_type, # text_block_0_part1 等の標準形式
                'search_weight': weight
            }).execute()
