"""
テキストチャンク分割ユーティリティ

長いテキストを適切なサイズのチャンクに分割し、検索精度を向上させます。
"""
from typing import List, Dict, Any
import re
from loguru import logger


class TextChunker:
    """
    テキストをチャンクに分割するクラス

    設計方針:
    - 意味のある単位（段落、セクション）を優先的に保持
    - 各チャンクは500-1000文字程度に収める
    - オーバーラップを設けることで文脈の連続性を確保
    """

    def __init__(
        self,
        chunk_size: int = 800,
        chunk_overlap: int = 100,
        min_chunk_size: int = 100
    ):
        """
        Args:
            chunk_size: 目標チャンクサイズ（文字数）
            chunk_overlap: チャンク間のオーバーラップ（文字数）
            min_chunk_size: 最小チャンクサイズ（これより小さいチャンクは前のチャンクにマージ）
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_size = min_chunk_size

    def split_text(self, text: str, metadata: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        テキストをチャンクに分割

        Args:
            text: 分割対象のテキスト
            metadata: チャンクに付与する追加メタデータ

        Returns:
            チャンクのリスト、各チャンクは以下の形式:
            {
                "chunk_index": int,
                "chunk_text": str,
                "chunk_size": int,
                "page_numbers": List[int] (オプション),
                "section_title": str (オプション)
            }
        """
        if not text or not text.strip():
            logger.warning("空のテキストが渡されました")
            return []

        # ページ情報を抽出（--- Page N --- の形式）
        page_info = self._extract_page_info(text)

        # ステップ1: セクション分割（大きな区切りを優先）
        sections = self._split_by_sections(text)

        # ステップ2: 各セクションをチャンクサイズに収まるように分割
        chunks = []
        chunk_index = 0

        for section in sections:
            section_chunks = self._split_section(section, chunk_index, page_info)
            chunks.extend(section_chunks)
            chunk_index += len(section_chunks)

        logger.info(f"テキスト分割完了: {len(chunks)} チャンク（総文字数: {len(text)}）")

        return chunks

    def _extract_page_info(self, text: str) -> Dict[int, int]:
        """
        テキストからページ情報を抽出

        Returns:
            {文字位置: ページ番号} の辞書
        """
        page_markers = re.finditer(r'---\s*Page\s+(\d+)\s*---', text)
        page_info = {}

        for match in page_markers:
            page_num = int(match.group(1))
            position = match.start()
            page_info[position] = page_num

        return page_info

    def _get_page_number(self, position: int, page_info: Dict[int, int]) -> int:
        """
        特定の文字位置が属するページ番号を取得

        Args:
            position: テキスト内の文字位置
            page_info: _extract_page_info() の出力

        Returns:
            ページ番号（不明な場合は1）
        """
        if not page_info:
            return 1

        # positionより前にある最新のページマーカーを探す
        valid_pages = [(pos, page) for pos, page in page_info.items() if pos <= position]

        if valid_pages:
            # 最も近いページマーカーのページ番号を返す
            return max(valid_pages, key=lambda x: x[0])[1]

        return 1  # デフォルトはページ1

    def _split_by_sections(self, text: str) -> List[str]:
        """
        テキストをセクションに分割（大きな区切りを優先）

        優先順位:
        1. ページ区切り（--- Page N ---）
        2. 大見出し（### で始まる行）
        3. 段落（空行で区切られた塊）

        Returns:
            セクションのリスト
        """
        # まず、ページ単位で分割
        page_sections = re.split(r'(?=---\s*Page\s+\d+\s*---)', text)

        all_sections = []

        for page_section in page_sections:
            if not page_section.strip():
                continue

            # 各ページ内で、見出しまたは段落単位に分割
            # 改行2つ以上を区切りとする
            sub_sections = re.split(r'\n\s*\n', page_section)

            for sub_section in sub_sections:
                if sub_section.strip():
                    all_sections.append(sub_section.strip())

        return all_sections

    def _split_section(
        self,
        section: str,
        start_index: int,
        page_info: Dict[int, int]
    ) -> List[Dict[str, Any]]:
        """
        1つのセクションをチャンクサイズに収まるように分割

        Args:
            section: 分割対象のセクション
            start_index: このセクションの開始チャンク番号
            page_info: ページ情報

        Returns:
            チャンクのリスト
        """
        chunks = []

        # セクション全体がチャンクサイズ以下の場合はそのまま1チャンクにする
        if len(section) <= self.chunk_size:
            chunks.append({
                "chunk_index": start_index,
                "chunk_text": section,
                "chunk_size": len(section)
            })
            return chunks

        # セクションを文単位で分割
        sentences = self._split_sentences(section)

        current_chunk = []
        current_size = 0
        chunk_index = start_index

        for sentence in sentences:
            sentence_size = len(sentence)

            # 現在のチャンクに追加すると chunk_size を超える場合
            if current_size + sentence_size > self.chunk_size and current_chunk:
                # 現在のチャンクを確定
                chunk_text = "".join(current_chunk)
                chunks.append({
                    "chunk_index": chunk_index,
                    "chunk_text": chunk_text,
                    "chunk_size": len(chunk_text)
                })

                # 次のチャンクを開始（オーバーラップを考慮）
                chunk_index += 1

                # オーバーラップ分のテキストを次のチャンクに引き継ぐ
                overlap_text = chunk_text[-self.chunk_overlap:] if len(chunk_text) > self.chunk_overlap else chunk_text
                current_chunk = [overlap_text, sentence]
                current_size = len(overlap_text) + sentence_size
            else:
                # 現在のチャンクに追加
                current_chunk.append(sentence)
                current_size += sentence_size

        # 最後のチャンクを追加
        if current_chunk:
            chunk_text = "".join(current_chunk)

            # 最小サイズチェック: 小さすぎる場合は前のチャンクにマージ
            if len(chunk_text) < self.min_chunk_size and chunks:
                # 前のチャンクに追加
                chunks[-1]["chunk_text"] += "\n" + chunk_text
                chunks[-1]["chunk_size"] = len(chunks[-1]["chunk_text"])
            else:
                chunks.append({
                    "chunk_index": chunk_index,
                    "chunk_text": chunk_text,
                    "chunk_size": len(chunk_text)
                })

        return chunks

    def _split_sentences(self, text: str) -> List[str]:
        """
        テキストを文単位で分割

        日本語の句読点（。、！、？）および英語のピリオドで分割

        Args:
            text: 分割対象のテキスト

        Returns:
            文のリスト
        """
        # 日本語の句点・疑問符・感嘆符、英語のピリオド、改行で分割
        # ただし、分割記号自体は保持する
        pattern = r'([。！？\.?!]\s*|\n+)'

        parts = re.split(pattern, text)

        sentences = []
        current = ""

        for part in parts:
            if not part:
                continue

            current += part

            # 句読点または改行を含む場合、文として確定
            if re.match(pattern, part):
                sentences.append(current)
                current = ""

        # 残りがあれば追加
        if current.strip():
            sentences.append(current)

        return sentences


class ParentChildChunker:
    """
    Parent-Child Indexing用のチャンク分割クラス

    設計方針:
    - 親チャンク（1000-2000文字）: 回答用の十分なコンテキスト
    - 子チャンク（200-400文字）: 検索用の細かい粒度
    - 検索は子チャンクで実行し、ヒットしたら親チャンクを返す
    """

    def __init__(
        self,
        parent_chunk_size: int = 1500,
        parent_chunk_overlap: int = 200,
        child_chunk_size: int = 300,
        child_chunk_overlap: int = 50
    ):
        """
        Args:
            parent_chunk_size: 親チャンクの目標サイズ（1000-2000文字推奨）
            parent_chunk_overlap: 親チャンク間のオーバーラップ
            child_chunk_size: 子チャンクの目標サイズ（200-400文字推奨）
            child_chunk_overlap: 子チャンク間のオーバーラップ
        """
        self.parent_chunker = TextChunker(
            chunk_size=parent_chunk_size,
            chunk_overlap=parent_chunk_overlap
        )
        self.child_chunk_size = child_chunk_size
        self.child_chunk_overlap = child_chunk_overlap

    def split_text(self, text: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        テキストを親子チャンクに分割

        Args:
            text: 分割対象のテキスト

        Returns:
            {
                "parent_chunks": List[Dict],  # 親チャンクのリスト
                "child_chunks": List[Dict]    # 子チャンクのリスト
            }
        """
        # ステップ1: 親チャンクを作成（1000-2000文字）
        parent_chunks = self.parent_chunker.split_text(text)

        # ステップ2: 各親チャンクを子チャンクに分割（200-400文字）
        all_child_chunks = []
        child_global_index = 0

        for parent_idx, parent_chunk in enumerate(parent_chunks):
            parent_text = parent_chunk["chunk_text"]

            # 親チャンクに識別用のメタデータを追加
            parent_chunk["is_parent"] = True
            parent_chunk["chunk_level"] = "parent"
            parent_chunk["parent_local_index"] = parent_idx

            # 親チャンクのテキストを子チャンクに分割
            child_chunks = self._split_into_children(
                parent_text=parent_text,
                parent_index=parent_idx,
                start_child_index=child_global_index
            )

            all_child_chunks.extend(child_chunks)
            child_global_index += len(child_chunks)

        logger.info(
            f"Parent-Child分割完了: "
            f"{len(parent_chunks)}親チャンク、{len(all_child_chunks)}子チャンク"
        )

        return {
            "parent_chunks": parent_chunks,
            "child_chunks": all_child_chunks
        }

    def _split_into_children(
        self,
        parent_text: str,
        parent_index: int,
        start_child_index: int
    ) -> List[Dict[str, Any]]:
        """
        親チャンクを子チャンクに分割

        Args:
            parent_text: 親チャンクのテキスト
            parent_index: 親チャンクのインデックス
            start_child_index: 子チャンクの開始インデックス

        Returns:
            子チャンクのリスト
        """
        # 子チャンク用のTextChunkerを作成
        child_chunker = TextChunker(
            chunk_size=self.child_chunk_size,
            chunk_overlap=self.child_chunk_overlap,
            min_chunk_size=100
        )

        # 子チャンクに分割
        child_chunks = child_chunker.split_text(parent_text)

        # 各子チャンクに親情報を追加
        for i, child_chunk in enumerate(child_chunks):
            child_chunk["chunk_index"] = start_child_index + i
            child_chunk["is_parent"] = False
            child_chunk["chunk_level"] = "child"
            child_chunk["parent_local_index"] = parent_index
            child_chunk["child_local_index"] = i

        return child_chunks
