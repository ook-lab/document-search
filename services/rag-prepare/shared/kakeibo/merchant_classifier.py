from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from loguru import logger

from shared.common.database.client import DatabaseClient


@dataclass
class MerchantToClassify:
    merchant_key: str
    institution: Optional[str]
    sample_contents: list[str]


class MerchantClassifier(Protocol):
    def classify(self, item: MerchantToClassify) -> dict:
        """
        return:
        {
            "category_major": str,
            "category_minor": str|None,
            "confidence": float|None,
            "model": str|None,
            "evidence": str|None
        }
        """
        ...


class NullClassifier:
    """AIを使わない場合のダミー分類器（常に未分類返し）"""
    def classify(self, item: MerchantToClassify) -> dict:
        return {
            "category_major": "未分類",
            "category_minor": None,
            "confidence": None,
            "model": None,
            "evidence": "null-classifier",
        }


class KakeiboAICacheUpdater:
    def __init__(self, db: DatabaseClient | None = None):
        # バッチ処理なので service_role を使用
        self.db = (db or DatabaseClient(use_service_role=True)).client

    def fetch_unclassified_merchants(self, limit: int = 200) -> list[MerchantToClassify]:
        """
        未分類merchant_keyを抽出する。
        "未分類"の定義：
         - enriched view で category_major_final が NULL or '未分類' 相当
         - merchant_key が NULL でない
        """
        resp = (
            self.db.table("view_kakeibo_enriched_transactions")
            .select("merchant_key,institution,content,category_major_final")
            .neq("merchant_key", None)
            .execute()
        )

        rows = resp.data or []
        bucket: dict[str, MerchantToClassify] = {}

        for r in rows:
            mk = r.get("merchant_key")
            if not mk:
                continue

            # 既に分類済みのものはスキップ
            cat = r.get("category_major_final")
            if cat and cat != "未分類":
                continue

            if mk not in bucket:
                bucket[mk] = MerchantToClassify(
                    merchant_key=mk,
                    institution=r.get("institution"),
                    sample_contents=[],
                )

            # 摘要サンプルは最大3つまで保持
            if len(bucket[mk].sample_contents) < 3:
                c = r.get("content")
                if c:
                    bucket[mk].sample_contents.append(c)

            if len(bucket) >= limit:
                break

        return list(bucket.values())

    def upsert_cache(self, merchant_key: str, result: dict) -> None:
        payload = {
            "merchant_key": merchant_key,
            "category_major": result["category_major"],
            "category_minor": result.get("category_minor"),
            "confidence": result.get("confidence"),
            "model": result.get("model"),
            "decided_by": "ai",
            "evidence": result.get("evidence"),
        }

        # キャッシュテーブルへUpsert
        resp = (
            self.db.table("Kakeibo_AI_CategoryCache")
            .upsert(payload, on_conflict="merchant_key")
            .execute()
        )

        if getattr(resp, "error", None):
            raise RuntimeError(resp.error)

    def run(self, classifier: MerchantClassifier, limit: int = 200) -> int:
        targets = self.fetch_unclassified_merchants(limit=limit)
        if not targets:
            logger.info("No unclassified merchants found.")
            return 0

        logger.info(f"Classifying merchants: {len(targets)}")
        updated = 0

        for item in targets:
            result = classifier.classify(item)

            # "未分類"を返されたら保存しない（無駄キャッシュを避ける）
            if result.get("category_major") in (None, "", "未分類"):
                continue

            self.upsert_cache(item.merchant_key, result)
            updated += 1

        logger.info(f"AI cache upserted: {updated}")
        return updated
