"""
手動検証済みデータを取得してAIの分類精度を向上させる

AIが新規商品を分類する際、過去に人間が検証した良い例を
Few-shot learningの参考として提供します。
"""

from typing import List, Dict, Optional
from supabase import Client


class VerifiedExamplesProvider:
    """検証済み商品例を提供するクラス"""

    def __init__(self, db_client: Client):
        """
        Args:
            db_client: Supabaseクライアント
        """
        self.db = db_client

    def get_verified_examples(
        self,
        limit: int = 20,
        general_name: Optional[str] = None,
        small_category: Optional[str] = None,
        diverse: bool = True
    ) -> List[Dict]:
        """
        手動検証済みの商品例を取得

        Args:
            limit: 取得する例の数（デフォルト: 20）
            general_name: 特定の一般名詞に絞る（オプション）
            small_category: 特定の小カテゴリに絞る（オプション）
            diverse: 多様な例を取得するか（True: 異なる分類から均等に取得）

        Returns:
            検証済み商品のリスト
        """
        query = self.db.table('Rawdata_NETSUPER_items').select(
            'product_name, general_name, small_category, keywords'
        ).eq('manually_verified', True)

        # フィルター条件
        if general_name:
            query = query.eq('general_name', general_name)
        if small_category:
            query = query.eq('small_category', small_category)

        # 最新の検証済みデータを優先
        query = query.order('last_verified_at', desc=True)

        # 取得
        result = query.limit(limit * 2 if diverse else limit).execute()

        if not result.data:
            return []

        # 多様性を確保する場合、各分類から均等に取得
        if diverse and not general_name and not small_category:
            return self._diversify_examples(result.data, limit)

        return result.data[:limit]

    def _diversify_examples(self, examples: List[Dict], limit: int) -> List[Dict]:
        """
        異なる分類から均等に例を取得

        Args:
            examples: 検証済み商品のリスト
            limit: 取得する例の数

        Returns:
            多様な分類を含む商品リスト
        """
        # 一般名詞ごとにグループ化
        groups = {}
        for example in examples:
            general_name = example.get('general_name', '未分類')
            if general_name not in groups:
                groups[general_name] = []
            groups[general_name].append(example)

        # 各グループから順番に取得
        diversified = []
        group_lists = list(groups.values())
        index = 0

        while len(diversified) < limit and index < max(len(g) for g in group_lists):
            for group in group_lists:
                if index < len(group) and len(diversified) < limit:
                    diversified.append(group[index])
            index += 1

        return diversified[:limit]

    def format_examples_for_prompt(
        self,
        examples: List[Dict],
        format_type: str = "numbered"
    ) -> str:
        """
        AI プロンプト用に例をフォーマット

        Args:
            examples: 検証済み商品のリスト
            format_type: フォーマットタイプ（"numbered", "json", "markdown"）

        Returns:
            フォーマットされた文字列
        """
        if not examples:
            return "（検証済みデータなし）"

        if format_type == "numbered":
            lines = ["以下は人間が検証した正しい分類例です：\n"]
            for i, ex in enumerate(examples, 1):
                lines.append(
                    f"{i}. 商品名: {ex['product_name']}\n"
                    f"   一般名詞: {ex.get('general_name', '未設定')}\n"
                    f"   小カテゴリ: {ex.get('small_category', '未設定')}\n"
                )
            return "\n".join(lines)

        elif format_type == "json":
            import json
            return json.dumps([{
                "product_name": ex['product_name'],
                "general_name": ex.get('general_name'),
                "small_category": ex.get('small_category')
            } for ex in examples], ensure_ascii=False, indent=2)

        elif format_type == "markdown":
            lines = ["| 商品名 | 一般名詞 | 小カテゴリ |", "| --- | --- | --- |"]
            for ex in examples:
                lines.append(
                    f"| {ex['product_name']} | "
                    f"{ex.get('general_name', '未設定')} | "
                    f"{ex.get('small_category', '未設定')} |"
                )
            return "\n".join(lines)

        return str(examples)

    def get_category_specific_examples(
        self,
        product_name: str,
        limit: int = 10
    ) -> List[Dict]:
        """
        商品名から推測される分類に関連する例を取得

        Args:
            product_name: 新規商品の商品名
            limit: 取得する例の数

        Returns:
            関連する検証済み商品のリスト
        """
        # シンプルなキーワードマッチング
        # 商品名から主要なキーワードを抽出
        keywords = self._extract_keywords(product_name)

        all_examples = []
        for keyword in keywords[:3]:  # 上位3つのキーワードで検索
            result = self.db.table('Rawdata_NETSUPER_items').select(
                'product_name, general_name, small_category'
            ).eq('manually_verified', True).ilike('product_name', f'%{keyword}%').limit(5).execute()

            all_examples.extend(result.data)

        # 重複を除去
        seen = set()
        unique_examples = []
        for ex in all_examples:
            key = (ex['product_name'], ex.get('general_name'))
            if key not in seen:
                seen.add(key)
                unique_examples.append(ex)

        return unique_examples[:limit]

    def _extract_keywords(self, product_name: str) -> List[str]:
        """
        商品名から主要なキーワードを抽出

        Args:
            product_name: 商品名

        Returns:
            キーワードのリスト
        """
        # スペースで分割
        words = product_name.split()

        # 数字や記号を除去して意味のある単語のみ抽出
        keywords = []
        for word in words:
            # 基本的な日本語/英語の単語のみ抽出
            if len(word) >= 2 and not word.isdigit():
                keywords.append(word)

        return keywords


# 使用例
if __name__ == "__main__":
    import os
    from supabase import create_client

    # Supabase接続
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    db = create_client(SUPABASE_URL, SUPABASE_KEY)

    # プロバイダー作成
    provider = VerifiedExamplesProvider(db)

    # 検証済み例を取得
    examples = provider.get_verified_examples(limit=10, diverse=True)
    print(f"取得した検証済み例: {len(examples)}件")

    # プロンプト用にフォーマット
    formatted = provider.format_examples_for_prompt(examples, format_type="numbered")
    print("\n" + formatted)

    # 特定商品に関連する例を取得
    related = provider.get_category_specific_examples("明治おいしい牛乳 900ml")
    print(f"\n関連する検証済み例: {len(related)}件")
