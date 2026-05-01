"""
メタデータ別ベクトル化戦略

タイトル、サマリー、日付などのメタデータを別々にチャンク化し、
検索時に重み付けを行うことで精度を向上させます。

旧方式: タイトルと本文を混ぜて小チャンク化 → タイトル情報が希釈
新方式: メタデータ種別ごとに独立したチャンクを生成 → 検索精度向上
"""
import re
from typing import List, Dict, Any, Optional
from loguru import logger


class MetadataChunker:
    """
    メタデータを種類別にチャンク化するクラス

    チャンク種別と重み付け:
    - title: タイトル専用チャンク（重み2.0）- 最高優先度
    - persons: 担当者・関係者チャンク（重み1.8）- 高優先度 ★新規
    - organizations: 組織名チャンク（重み1.7）- 高優先度 ★新規
    - summary: サマリー専用チャンク（重み1.5）
    - date: 日付専用チャンク（重み1.3）
    - tags: タグ専用チャンク（重み1.2）
    - people: AI抽出人物チャンク（重み1.2）★新規
    - content_small: 本文小チャンク（重み1.0）- 150文字
    - content_large: 本文大チャンク（重み1.0）- 全文
    - synthetic: 合成チャンク（重み1.0）- 構造化データ
    """

    # チャンク長の上限（300文字 - Stage Hガイドラインに準拠）
    MAX_CHUNK_LENGTH = 300

    # チャンク種別と検索重み
    CHUNK_WEIGHTS = {
        'title': 2.0,                    # タイトルマッチは最優先
        'display_subject': 2.0,          # 表示件名（最重要）
        'table': 2.0,                    # 構造化表（最重要）★新規追加
        'doc_type': 1.8,                 # 授業名・ドキュメント種別（重要）
        'display_post_text': 1.8,        # 表示投稿本文（重要）
        'persons': 1.8,                  # 担当者・関係者（重要）
        'text_block': 1.8,               # テキストブロック（重要）★新規追加
        'schedule': 1.7,                 # 週間予定（重要）★新規追加
        'organizations': 1.7,            # 組織名（重要）
        'summary': 1.5,                  # サマリーは高優先
        'display_type': 1.5,             # 表示種別（お知らせ/課題/資料）
        'task': 1.5,                     # タスク（重要）
        'display_sender': 1.3,           # 表示送信者名
        'display_sent_at': 1.3,          # 表示送信日時
        'date': 1.3,                     # 日付検索
        'calendar_event': 1.3,           # カレンダーイベント
        'tags': 1.2,                     # タグ検索
        'people': 1.2,                   # AI抽出人物（やや重要）
        'other': 1.0,                    # その他のテキスト（標準）★新規追加
        'classroom_sender_email': 1.0,   # Classroom送信者メール
        'content_small': 1.0,            # 本文検索（標準）
        'content_large': 1.0,            # 回答生成用
        'synthetic': 1.0,                # 構造化データ
    }

    def __init__(self):
        """初期化"""
        self.chunk_counter = 0

    def create_metadata_chunks(
        self,
        document_data: Dict[str, Any],
        existing_content_chunks: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """
        ドキュメントデータからメタデータチャンクを生成

        Args:
            document_data: ドキュメント情報
                - file_name: ファイル名（タイトル）
                - summary: AI生成サマリー
                - document_date: 文書日付
                - tags: タグリスト
                - full_text: 全文（オプション）
            existing_content_chunks: 既存の本文チャンク（オプション）
                指定された場合、本文チャンク生成をスキップ

        Returns:
            メタデータチャンクのリスト
            [{
                'chunk_type': 'title',
                'chunk_text': '...',
                'search_weight': 2.0,
                'chunk_index': 0
            }, ...]
        """
        chunks = []
        self.chunk_counter = 0

        # 1. タイトルチャンク（最高優先度）
        title = document_data.get('file_name') or document_data.get('title')
        if title:
            # 拡張子を除去してクリーンなタイトルを作成
            clean_title = self._clean_title(title)
            if clean_title:
                chunks.extend(self._create_chunk(
                    chunk_type='title',
                    text=clean_title,
                    metadata={'original_filename': title}
                ))
                logger.debug(f"[MetadataChunker] タイトルチャンク作成: {clean_title[:50]}...")

        # 2. サマリーチャンク
        summary = document_data.get('summary')
        if summary and len(summary.strip()) > 10:
            chunks.extend(self._create_chunk(
                chunk_type='summary',
                text=summary.strip()
            ))
            logger.debug(f"[MetadataChunker] サマリーチャンク作成: {len(summary)}文字")

        # 3. 日付チャンク
        date_text = self._format_date_chunk(document_data)
        if date_text:
            chunks.extend(self._create_chunk(
                chunk_type='date',
                text=date_text
            ))
            logger.debug(f"[MetadataChunker] 日付チャンク作成: {date_text}")

        # 4. タグチャンク
        tags = document_data.get('tags', [])
        if tags:
            tag_text = self._format_tags_chunk(tags)
            if tag_text:
                chunks.extend(self._create_chunk(
                    chunk_type='tags',
                    text=tag_text
                ))
                logger.debug(f"[MetadataChunker] タグチャンク作成: {len(tags)}個のタグ")

        # 5. doc_type（授業名・ドキュメント種別）チャンク（高優先度）
        doc_type = document_data.get('doc_type')
        if doc_type and doc_type.strip():
            chunks.extend(self._create_chunk(
                chunk_type='doc_type',
                text=f"授業名: {doc_type.strip()}"
            ))
            logger.debug(f"[MetadataChunker] doc_typeチャンク作成: {doc_type}")

        # 6. 表示用専用チャンク（Google Classroom等の投稿情報）
        display_subject = document_data.get('display_subject')
        if display_subject and display_subject.strip():
            chunks.extend(self._create_chunk(
                chunk_type='display_subject',
                text=f"件名: {display_subject.strip()}"
            ))
            logger.debug(f"[MetadataChunker] 表示件名チャンク作成: {len(display_subject)}文字")

        display_post_text = document_data.get('display_post_text')
        if display_post_text and display_post_text.strip():
            chunks.extend(self._create_chunk(
                chunk_type='display_post_text',
                text=f"投稿本文: {display_post_text.strip()}"
            ))
            logger.debug(f"[MetadataChunker] 表示投稿本文チャンク作成: {len(display_post_text)}文字")

        display_type = document_data.get('display_type')
        if display_type:
            chunks.extend(self._create_chunk(
                chunk_type='display_type',
                text=f"種別: {display_type}"
            ))
            logger.debug(f"[MetadataChunker] 表示種別チャンク作成: {display_type}")

        display_sender = document_data.get('display_sender')
        if display_sender:
            chunks.extend(self._create_chunk(
                chunk_type='display_sender',
                text=f"送信者: {display_sender}"
            ))
            logger.debug(f"[MetadataChunker] 表示送信者チャンク作成: {display_sender}")

        display_sent_at = document_data.get('display_sent_at')
        if display_sent_at:
            chunks.extend(self._create_chunk(
                chunk_type='display_sent_at',
                text=f"送信日時: {display_sent_at}"
            ))
            logger.debug(f"[MetadataChunker] 表示送信日時チャンク作成: {display_sent_at}")

        classroom_sender_email = document_data.get('classroom_sender_email')
        if classroom_sender_email:
            chunks.extend(self._create_chunk(
                chunk_type='classroom_sender_email',
                text=f"送信者メール: {classroom_sender_email}"
            ))
            logger.debug(f"[MetadataChunker] Classroom送信者メールチャンク作成")

        # 7. persons（担当者・関係者）チャンク（高重要）
        persons = document_data.get('persons', [])
        if persons:
            persons_text = self._format_persons_chunk(persons)
            if persons_text:
                chunks.extend(self._create_chunk(
                    chunk_type='persons',
                    text=persons_text
                ))
                logger.debug(f"[MetadataChunker] personsチャンク作成: {len(persons)}名")

        # 8. organizations（組織名）チャンク（高重要）
        organizations = document_data.get('organizations', [])
        if organizations:
            orgs_text = self._format_organizations_chunk(organizations)
            if orgs_text:
                chunks.extend(self._create_chunk(
                    chunk_type='organizations',
                    text=orgs_text
                ))
                logger.debug(f"[MetadataChunker] organizationsチャンク作成: {len(organizations)}組織")

        # 9. people（AI抽出人物）チャンク
        people = document_data.get('people', [])
        if people:
            people_text = self._format_people_chunk(people)
            if people_text:
                chunks.extend(self._create_chunk(
                    chunk_type='people',
                    text=people_text
                ))
                logger.debug(f"[MetadataChunker] peopleチャンク作成: {len(people)}名")

        # 10. text_blocks（テキストブロック）チャンク
        text_blocks = document_data.get('text_blocks', [])
        if text_blocks:
            for i, block in enumerate(text_blocks):
                block_text = self._format_text_block_chunk(block, i)
                if block_text:
                    chunks.extend(self._create_chunk(
                        chunk_type=f'text_block_{i}',
                        text=block_text,
                        metadata={
                            'original_structure': block,
                            'structure_type': 'text_block'
                        }
                    ))
                    logger.debug(f"[MetadataChunker] text_blockチャンク作成: block_{i}")

        # 11. structured_tables（構造化表）チャンク
        structured_tables = document_data.get('structured_tables', [])
        if structured_tables:
            for i, table in enumerate(structured_tables):
                table_text = self._format_table_chunk(table, i)
                if table_text:
                    table_meta = table.get('metadata', {})
                    table_semantics = table_meta.get('table_semantics') or {}
                    chunks.extend(self._create_chunk(
                        chunk_type=f'table_{i}',
                        text=table_text,
                        metadata={
                            'original_structure': table,
                            'structure_type': 'table',
                            'table_semantics': table_semantics,
                        }
                    ))
                    logger.debug(f"[MetadataChunker] tableチャンク作成: table_{i} semantics={table_semantics.get('type')}/{table_semantics.get('target')}")

        # 12. weekly_schedule（週間予定）チャンク
        weekly_schedule = document_data.get('weekly_schedule', [])
        if weekly_schedule:
            for schedule in weekly_schedule:
                schedule_text = self._format_schedule_chunk(schedule)
                if schedule_text:
                    date_str = schedule.get('date', f'schedule_{len(chunks)}')
                    chunks.extend(self._create_chunk(
                        chunk_type=f'schedule_{date_str}',
                        text=schedule_text,
                        metadata={
                            'original_structure': schedule,
                            'structure_type': 'schedule'
                        }
                    ))
                    logger.debug(f"[MetadataChunker] scheduleチャンク作成: {date_str}")

        # 13. other_text（その他のテキスト）チャンク
        other_text = document_data.get('other_text', [])
        if other_text:
            for i, other_item in enumerate(other_text):
                if isinstance(other_item, dict):
                    item_type = other_item.get('type', 'misc')
                    content = other_item.get('content', '')
                    if content and content.strip():
                        chunks.extend(self._create_chunk(
                            chunk_type=f'other_{item_type}_{i}',
                            text=content.strip(),
                            metadata={
                                'original_structure': other_item,
                                'structure_type': 'other_text',
                                'other_type': item_type
                            }
                        ))
                        logger.debug(f"[MetadataChunker] other_textチャンク作成: {item_type}")

        # 14. calendar_events（カレンダーイベント）チャンク
        calendar_events = document_data.get('calendar_events', [])
        if calendar_events:
            for i, event in enumerate(calendar_events):
                if isinstance(event, dict):
                    event_date = event.get('event_date', '')
                    event_time = event.get('event_time', '')
                    event_name = event.get('event_name', '')
                    location = event.get('location', '')
                    description = event.get('description', '')
                    participants = event.get('participants', [])

                    # イベントの説明文を生成
                    event_text_parts = []
                    if event_date:
                        event_text_parts.append(f"日付: {event_date}")
                    if event_time:
                        event_text_parts.append(f"時刻: {event_time}")
                    if event_name:
                        event_text_parts.append(f"イベント名: {event_name}")
                    if location:
                        event_text_parts.append(f"場所: {location}")
                    if description:
                        event_text_parts.append(f"詳細: {description}")
                    if participants:
                        event_text_parts.append(f"参加者: {', '.join(participants)}")

                    event_text = '\n'.join(event_text_parts)

                    if event_text.strip():
                        chunks.extend(self._create_chunk(
                            chunk_type=f'calendar_event_{i}',
                            text=event_text.strip(),
                            metadata={
                                'original_structure': event,
                                'structure_type': 'calendar_event',
                                'event_date': event_date,
                                'event_name': event_name
                            }
                        ))
                        logger.debug(f"[MetadataChunker] calendar_eventチャンク作成: {event_name}")

        # 15. tasks（タスク）チャンク
        tasks = document_data.get('tasks', [])
        if tasks:
            for i, task in enumerate(tasks):
                if isinstance(task, dict):
                    task_name = task.get('task_name', '')
                    deadline = task.get('deadline', '')
                    priority = task.get('priority', '')
                    category = task.get('category', '')
                    description = task.get('description', '')
                    checklist = task.get('checklist', [])
                    assignee = task.get('assignee', '')

                    # タスクの説明文を生成
                    task_text_parts = []
                    if task_name:
                        task_text_parts.append(f"タスク名: {task_name}")
                    if deadline:
                        task_text_parts.append(f"期限: {deadline}")
                    if priority:
                        task_text_parts.append(f"優先度: {priority}")
                    if category:
                        task_text_parts.append(f"カテゴリ: {category}")
                    if description:
                        task_text_parts.append(f"詳細: {description}")
                    if checklist:
                        task_text_parts.append(f"チェックリスト:\n" + '\n'.join([f"  - {item}" for item in checklist]))
                    if assignee:
                        task_text_parts.append(f"担当者: {assignee}")

                    task_text = '\n'.join(task_text_parts)

                    if task_text.strip():
                        chunks.extend(self._create_chunk(
                            chunk_type=f'task_{i}',
                            text=task_text.strip(),
                            metadata={
                                'original_structure': task,
                                'structure_type': 'task',
                                'task_name': task_name,
                                'deadline': deadline,
                                'priority': priority
                            }
                        ))
                        logger.debug(f"[MetadataChunker] taskチャンク作成: {task_name}")

        # 16. notices（お知らせ）チャンク
        notices = document_data.get('notices', [])
        if notices:
            for i, notice in enumerate(notices):
                if isinstance(notice, dict):
                    category = notice.get('category', '')
                    content = notice.get('content', '')

                    if not content or not content.strip():
                        continue

                    notice_text_parts = []
                    if category:
                        notice_text_parts.append(f"カテゴリ: {category}")
                    notice_text_parts.append(content.strip())

                    notice_text = '\n'.join(notice_text_parts)
                    chunks.extend(self._create_chunk(
                        chunk_type=f'notice_{i}',
                        text=notice_text,
                        metadata={
                            'original_structure': notice,
                            'structure_type': 'notice',
                            'category': category
                        }
                    ))
                    logger.debug(f"[MetadataChunker] noticeチャンク作成: {category}")

        logger.info(f"[MetadataChunker] メタデータチャンク生成完了: {len(chunks)}個")
        return chunks

    def _split_text_by_sentences(self, text: str) -> List[str]:
        """
        長すぎるテキストを文の境界で分割（300文字制限）

        全体との関係を保つため、分割は文の境界で行い、
        メタデータに元の構造情報を記録する。

        Args:
            text: 分割するテキスト

        Returns:
            分割されたテキストのリスト（300文字以下の場合は1要素のリスト）
        """
        if len(text) <= self.MAX_CHUNK_LENGTH:
            return [text]

        # 文の境界で分割（。！？改行）
        sentences = re.split(r'([。！？\n])', text)

        parts = []
        current = ""

        i = 0
        while i < len(sentences):
            sentence = sentences[i]
            delimiter = sentences[i + 1] if i + 1 < len(sentences) else ""

            # 次の文を追加しても制限内に収まるか確認
            if len(current) + len(sentence) + len(delimiter) <= self.MAX_CHUNK_LENGTH:
                current += sentence + delimiter
                i += 2 if delimiter else 1
            else:
                # 現在のバッファを保存
                if current:
                    parts.append(current.strip())
                    current = ""

                # 単一の文が長すぎる場合は強制分割
                if len(sentence + delimiter) > self.MAX_CHUNK_LENGTH:
                    # 文を強制的に分割
                    for j in range(0, len(sentence), self.MAX_CHUNK_LENGTH):
                        chunk_part = sentence[j:j + self.MAX_CHUNK_LENGTH]
                        parts.append(chunk_part.strip())
                    if delimiter:
                        parts[-1] += delimiter
                    i += 2 if delimiter else 1
                else:
                    current = sentence + delimiter
                    i += 2 if delimiter else 1

        # 残りを追加
        if current.strip():
            parts.append(current.strip())

        return parts if parts else [text]

    def _create_chunk(
        self,
        chunk_type: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        チャンクオブジェクトを作成（長すぎる場合は自動分割）

        300文字を超える場合、文の境界で自動分割し、
        全体との関係を保つためメタデータに分割情報を記録。

        Args:
            chunk_type: チャンク種別
            text: チャンクテキスト
            metadata: 追加メタデータ

        Returns:
            チャンクオブジェクトのリスト（通常は1要素、分割時は複数要素）
        """
        # テキストを分割（300文字以下なら1要素のリスト）
        text_parts = self._split_text_by_sentences(text)

        if len(text_parts) > 1:
            logger.warning(
                f"[MetadataChunker] チャンク長超過により分割: {chunk_type} "
                f"({len(text)}文字 → {len(text_parts)}個に分割)"
            )

        # プレフィックスベースの重み付け（text_block_0 → text_block, table_1 → table）
        weight = self.CHUNK_WEIGHTS.get(chunk_type, None)
        if weight is None:
            # プレフィックスを抽出して検索
            for prefix in ['text_block', 'table', 'schedule', 'calendar_event', 'task', 'other']:
                if chunk_type.startswith(prefix):
                    weight = self.CHUNK_WEIGHTS.get(prefix, 1.0)
                    break
            if weight is None:
                weight = 1.0

        chunks = []
        for i, part in enumerate(text_parts):
            # 分割された場合はサフィックスを付与（全体との関係を明示）
            if len(text_parts) > 1:
                type_with_suffix = f"{chunk_type}_part{i}"
            else:
                type_with_suffix = chunk_type

            chunk = {
                'chunk_type': type_with_suffix,
                'chunk_text': part,
                'chunk_size': len(part),
                'search_weight': weight,
                'chunk_index': self.chunk_counter
            }

            if metadata:
                chunk['metadata'] = metadata.copy()
                # 分割情報を追加（全体との関係を保持）
                if len(text_parts) > 1:
                    chunk['metadata']['split_info'] = {
                        'original_chunk_type': chunk_type,
                        'original_length': len(text),
                        'part_index': i,
                        'total_parts': len(text_parts)
                    }

            chunks.append(chunk)
            self.chunk_counter += 1

        return chunks

    def _clean_title(self, title: str) -> str:
        """
        ファイル名からクリーンなタイトルを抽出

        - 拡張子を除去
        - 日付プレフィックスを整形
        - アンダースコアをスペースに変換
        """
        # 拡張子を除去
        clean = re.sub(r'\.(pdf|docx?|xlsx?|pptx?|txt|html?)$', '', title, flags=re.IGNORECASE)

        # アンダースコアをスペースに変換（ただしスネークケースを考慮）
        # 例: "2024_12_01_会議資料" → "2024年12月01日 会議資料"

        # 日付パターンを検出して整形
        date_pattern = r'^(\d{4})[_\-]?(\d{2})[_\-]?(\d{2})[_\s]*'
        match = re.match(date_pattern, clean)
        if match:
            year, month, day = match.groups()
            date_str = f"{year}年{month}月{day}日"
            rest = clean[match.end():].strip('_- ')
            clean = f"{date_str} {rest}" if rest else date_str

        # 残りのアンダースコアをスペースに
        clean = clean.replace('_', ' ')

        return clean.strip()

    def _format_date_chunk(self, document_data: Dict[str, Any]) -> Optional[str]:
        """
        日付関連情報をチャンク用テキストに整形

        document_date, event_dates, relevant_dateを統合
        """
        parts = []

        # メイン文書日付
        doc_date = document_data.get('document_date')
        if doc_date:
            parts.append(f"文書日付: {doc_date}")

        # イベント日付（複数可）
        event_dates = document_data.get('event_dates', [])
        if event_dates:
            if isinstance(event_dates, list):
                for event in event_dates:
                    if isinstance(event, dict):
                        label = event.get('label', 'イベント')
                        date = event.get('date', '')
                        if date:
                            parts.append(f"{label}: {date}")
                    elif isinstance(event, str):
                        parts.append(f"イベント日: {event}")

        # 関連日付
        relevant_date = document_data.get('relevant_date')
        if relevant_date and relevant_date != doc_date:
            parts.append(f"関連日付: {relevant_date}")

        return '\n'.join(parts) if parts else None

    def _format_tags_chunk(self, tags: List[str]) -> Optional[str]:
        """
        タグリストをチャンク用テキストに整形
        """
        if not tags:
            return None

        # 重複除去と整形
        unique_tags = list(dict.fromkeys(tags))  # 順序を保持して重複除去

        # タグをカンマ区切りとハッシュタグ形式の両方で表現
        tag_text = ', '.join(unique_tags)
        hashtags = ' '.join([f"#{tag}" for tag in unique_tags])

        return f"タグ: {tag_text}\n{hashtags}"

    def _format_persons_chunk(self, persons: List[str]) -> Optional[str]:
        """
        担当者・関係者リストをチャンク用テキストに整形

        配列には複数の表記（漢字、ひらがな、アルファベット）が含まれる想定
        例: ['山田太郎', 'やまだたろう', 'Yamada Taro', '山田']
        """
        if not persons:
            return None

        # 重複除去
        unique_persons = list(dict.fromkeys(persons))

        # スペース区切りで結合（ベクトル化時に全表記が考慮される）
        persons_text = ' '.join(unique_persons)

        return f"担当者: {persons_text}"

    def _format_organizations_chunk(self, organizations: List[str]) -> Optional[str]:
        """
        組織名リストをチャンク用テキストに整形

        配列には複数の表記（正式名称、略称、ひらがな、英語）が含まれる想定
        例: ['東京大学', 'とうきょうだいがく', 'Tokyo University', '東大', 'UTokyo']
        """
        if not organizations:
            return None

        # 重複除去
        unique_orgs = list(dict.fromkeys(organizations))

        # スペース区切りで結合
        orgs_text = ' '.join(unique_orgs)

        return f"組織: {orgs_text}"

    def _format_people_chunk(self, people: List[str]) -> Optional[str]:
        """
        AI抽出人物リストをチャンク用テキストに整形

        Args:
            people: AIが文書から抽出した人物名のリスト
        """
        if not people:
            return None

        # 重複除去
        unique_people = list(dict.fromkeys(people))

        # カンマ区切りとスペース区切りの両方で表現
        people_text = ', '.join(unique_people)
        people_spaced = ' '.join(unique_people)

        return f"関係者: {people_text}\n{people_spaced}"

    def _format_text_block_chunk(self, block: Dict[str, Any], index: int) -> Optional[str]:
        """
        テキストブロックをチャンク用テキストに展開

        Args:
            block: テキストブロック {'title': str, 'content': str}
            index: ブロックのインデックス

        Returns:
            展開されたテキスト
        """
        if not block or not isinstance(block, dict):
            return None

        title = block.get('title', '')
        content = block.get('content', '')

        if not content:
            return None

        # タイトルと本文を結合
        parts = []
        if title:
            parts.append(f"【{title}】")
        parts.append(content)

        return '\n'.join(parts)

    def _format_table_chunk(self, table: Dict[str, Any], index: int) -> Optional[str]:
        """
        構造化表をテキストに展開。

        G17 の col_map がある場合（2D rows + metadata）:
          col_map キー N → row[N + row_label_col + 1] の位置にある値を
          G17 が解析したカラム名と対応付けてテキスト化する。

        col_map がない場合（headers + rows_as_dicts）:
          既存ロジックで処理。
        """
        if not table or not isinstance(table, dict):
            return None

        # semantic_title（G17が生成した人間語タイトル）を優先、なければ table_title にフォールバック
        semantic_title = table.get('semantic_title', '')
        table_title = semantic_title if semantic_title else table.get('table_title', f'表{index + 1}')
        headers = table.get('headers', [])
        rows = table.get('rows', [])
        metadata = table.get('metadata', {})

        if not rows:
            return None

        lines = [f"【{table_title}】", ""]

        col_map = metadata.get('col_map', {})
        row_label_col = int(metadata.get('row_label_col') or 0)
        data_start_row = int(metadata.get('data_start_row') or 0)

        # G17 の col_map あり: 座標ベースでカラム名を付ける
        if col_map and rows and isinstance(rows[0], list):
            slot_map = {}  # {row内の位置: カラム名}
            for key, info in col_map.items():
                pos = int(key) + row_label_col + 1
                label = None
                if isinstance(info, dict):
                    for v in info.values():
                        if isinstance(v, str) and v.strip():
                            label = v.strip()
                            break
                elif isinstance(info, str) and info.strip():
                    label = info.strip()
                slot_map[pos] = label or f"列{key}"

            # フィルダウン:
            # - null: 全列フィルダウン（結合セルの跡）
            # - "": ラベル列（slot_map に含まれない列）ではフィルダウン
            #       Stage B が結合セルを "" で出力するため
            #       データ列（slot_map に含まれる列）の "" は空セルとして保持
            data_col_positions = set(slot_map.keys())
            last_values: dict = {}
            filled_rows = []
            for row in rows[data_start_row:]:
                if not row:
                    filled_rows.append(row)
                    continue
                filled = list(row)
                for col_idx, val in enumerate(filled):
                    is_data_col = col_idx in data_col_positions
                    if val is None or (val == '' and not is_data_col):
                        filled[col_idx] = last_values.get(col_idx)
                    else:
                        last_values[col_idx] = val
                filled_rows.append(filled)

            for row_idx, row in enumerate(filled_rows, 1):
                if not row:
                    continue
                row_label = str(row[row_label_col] or '').replace('\n', ' ').strip() if row_label_col < len(row) else ''
                parts = []
                for pos, col_label in sorted(slot_map.items()):
                    val = row[pos] if pos < len(row) else None
                    if val is None or str(val).strip() == '':
                        continue
                    val_str = str(val).replace('\n', ' ').strip()
                    parts.append(f"{col_label}: {val_str}")
                if row_label or parts:
                    row_text = (f"{row_label}: " if row_label else "") + ", ".join(parts)
                    if row_text.strip():
                        lines.append(f"{row_idx}. {row_text}")

        else:
            # col_map なし: headers + rows_as_dicts（または列名なし2D）
            for row_idx, row in enumerate(rows[data_start_row:], 1):
                if isinstance(row, dict):
                    row_parts = []
                    for header in headers:
                        value = row.get(header, '')
                        if isinstance(value, list):
                            value = '\n'.join(str(v) for v in value)
                        elif value is not None:
                            value = str(value)
                        else:
                            value = ''
                        if value:
                            row_parts.append(f"{header}: {value}")
                    if row_parts:
                        lines.append(f"{row_idx}. " + ", ".join(row_parts))
                elif isinstance(row, list):
                    row_parts = []
                    for i, value in enumerate(row):
                        if isinstance(value, list):
                            value_str = '\n'.join(str(v) for v in value)
                        else:
                            value_str = str(value) if value is not None else ''
                        if i < len(headers):
                            header = headers[i]
                            if isinstance(header, list):
                                header = ', '.join(str(h) for h in header)
                            row_parts.append(f"{header}: {value_str}")
                        else:
                            if value_str:
                                row_parts.append(value_str)
                    if row_parts:
                        lines.append(f"{row_idx}. " + ", ".join(row_parts))

        return '\n'.join(lines) if len(lines) > 2 else None

    def _format_schedule_chunk(self, schedule: Dict[str, Any]) -> Optional[str]:
        """
        週間予定をテキストに展開

        Args:
            schedule: {
                'date': str,
                'day_of_week': str,
                'events': List[str],
                'class_schedules': List[Dict],
                'note': str
            }

        Returns:
            予定全体をテキスト化したもの
        """
        if not schedule or not isinstance(schedule, dict):
            return None

        date = schedule.get('date', '')
        day_of_week = schedule.get('day_of_week', '')
        events = schedule.get('events', [])
        class_schedules = schedule.get('class_schedules', [])
        note = schedule.get('note', '')

        if not date:
            return None

        lines = []

        # ヘッダー
        if day_of_week:
            lines.append(f"{date}（{day_of_week}）")
        else:
            lines.append(date)
        lines.append("")

        # クラス別時間割
        if class_schedules:
            for class_schedule in class_schedules:
                class_name = class_schedule.get('class', '')
                periods = class_schedule.get('periods', [])

                if class_name:
                    lines.append(f"【{class_name}】")

                for period in periods:
                    period_num = period.get('period', '')
                    subject = period.get('subject', '')
                    if period_num and subject:
                        lines.append(f"{period_num}時間目: {subject}")

                lines.append("")

        # イベント
        if events:
            lines.append("【行事】")
            for event in events:
                lines.append(f"- {event}")
            lines.append("")

        # 連絡事項
        if note:
            lines.append("【連絡事項】")
            lines.append(note)

        return '\n'.join(lines).strip() if lines else None
