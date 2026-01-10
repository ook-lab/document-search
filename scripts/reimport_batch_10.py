#!/usr/bin/env python3
"""10件ずつ再処理（pendingから取得）"""
import sys
import asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger
from shared.common.database.client import DatabaseClient
from shared.ai.llm_client.llm_client import LLMClient
from shared.pipeline.pipeline import UnifiedDocumentPipeline
from shared.common.connectors.google_drive import GoogleDriveConnector

async def main():
    logger.info("=== 10件バッチ再処理 ===")
    
    db = DatabaseClient(use_service_role=True)
    llm_client = LLMClient()
    pipeline = UnifiedDocumentPipeline(llm_client=llm_client)
    drive = GoogleDriveConnector()
    
    # pending の10件を取得
    query = db.client.table('Rawdata_FILE_AND_MAIL').select(
        'id, file_name, source_id, mime_type, workspace, doc_type'
    ).eq('processing_status', 'pending').limit(10)
    
    result = query.execute()
    docs = result.data
    
    logger.info(f"処理対象: {len(docs)}件")
    
    if not docs:
        logger.info("✅ pending のドキュメントがありません")
        return
    
    success = 0
    failed = 0
    
    for i, doc in enumerate(docs, 1):
        logger.info(f"\n{'='*80}")
        logger.info(f"[{i}/{len(docs)}] {doc['file_name']}")
        logger.info(f"{'='*80}")
        logger.info(f"  source_id: {doc['source_id']}")
        logger.info(f"  workspace: {doc.get('workspace')}, doc_type: {doc.get('doc_type')}")
        
        try:
            # ダウンロード
            temp_dir = Path("temp/reimport")
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_file = temp_dir / doc['file_name']
            
            logger.info("  📥 Drive からダウンロード中...")
            drive.download_file(doc['source_id'], str(temp_file))
            logger.info(f"  ✓ ダウンロード完了: {temp_file.stat().st_size:,} bytes")
            
            # 再処理
            logger.info("  🔄 E→K パイプライン実行中...")
            process_result = await pipeline.process_document(
                file_path=temp_file,
                file_name=doc['file_name'],
                doc_type=doc.get('doc_type', 'classroom'),
                workspace=doc.get('workspace', 'default'),
                mime_type=doc.get('mime_type', 'application/pdf'),
                source_id=doc['source_id'],
                existing_document_id=doc['id']
            )
            
            # 一時ファイル削除
            if temp_file.exists():
                temp_file.unlink()
            
            if process_result.get('success'):
                logger.info(f"  ✅ 成功")
                success += 1
            else:
                logger.error(f"  ❌ 失敗: {process_result.get('error')}")
                failed += 1
                
        except Exception as e:
            logger.error(f"  ❌ エラー: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    logger.info(f"\n{'='*80}")
    logger.info(f"✅ バッチ完了: 成功{success}件 / 失敗{failed}件")
    logger.info(f"{'='*80}")

if __name__ == "__main__":
    asyncio.run(main())
