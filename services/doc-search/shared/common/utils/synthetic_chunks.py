"""
合成チャンク生成ユーティリティ

構造化されたメタデータ（JSON）から検索専用の合成チャンクを生成します。
これにより、本文には詳しく書かれていないが重要な情報（スケジュール、議題等）を
検索結果の上位に押し上げることができます。
"""
from typing import Dict, Any, List, Optional
from loguru import logger


def create_schedule_synthetic_chunk(metadata: Dict[str, Any], file_name: str = "") -> Optional[str]:
    """
    monthly_schedule_blocks からスケジュール専用の合成チャンクを生成

    Args:
        metadata: ドキュメントのメタデータ（JSON）
        file_name: ファイル名（コンテキスト情報として追加）

    Returns:
        検索専用の合成テキスト、スケジュールがない場合は None
    """
    if not metadata:
        return None

    schedule_blocks = metadata.get('monthly_schedule_blocks', [])
    if not schedule_blocks:
        # フォールバック: weekly_schedule も確認
        weekly_schedule = metadata.get('weekly_schedule', [])
        if weekly_schedule:
            return create_weekly_schedule_synthetic_chunk(metadata, file_name)
        return None

    # スケジュール専用チャンクの構築
    lines = []
    lines.append(f"【{file_name} - 月間行事予定・スケジュール】\n")

    for block in schedule_blocks:
        date = block.get('date', '')
        event = block.get('event', '')
        notes = block.get('notes', '')

        if not event:
            continue

        # 検索に引っかかりやすい形式に整形
        # 日付と曜日情報を含める
        line_parts = []

        if date:
            # 日付をフォーマット（例: 2025-12-19 → 12月19日）
            try:
                if '-' in date:
                    parts = date.split('-')
                    if len(parts) == 3:
                        month = int(parts[1])
                        day = int(parts[2])
                        line_parts.append(f"{month}月{day}日")
                    else:
                        line_parts.append(date)
                else:
                    line_parts.append(date)
            except:
                line_parts.append(date)

        # イベント名
        line_parts.append(event)

        # メモ・詳細情報
        if notes:
            line_parts.append(f"({notes})")

        line = " ".join(line_parts)
        lines.append(f"- {line}")

    if len(lines) <= 1:  # ヘッダーのみの場合はスケジュールなし
        return None

    # 検索用キーワードを追加（検索精度向上のため）
    lines.append("\n【キーワード】")
    lines.append("行事 予定 スケジュール イベント 月間予定 日程 カレンダー")

    synthetic_text = "\n".join(lines)
    logger.debug(f"[合成チャンク] monthly_schedule_blocks から {len(schedule_blocks)}件のスケジュールを抽出")

    return synthetic_text


def create_weekly_schedule_synthetic_chunk(metadata: Dict[str, Any], file_name: str = "") -> Optional[str]:
    """
    weekly_schedule から週間スケジュール専用の合成チャンクを生成

    Args:
        metadata: ドキュメントのメタデータ（JSON）
        file_name: ファイル名（コンテキスト情報として追加）

    Returns:
        検索専用の合成テキスト、スケジュールがない場合は None
    """
    if not metadata:
        return None

    weekly_schedule = metadata.get('weekly_schedule', [])
    if not weekly_schedule:
        return None

    # 週間スケジュール専用チャンクの構築
    lines = []
    lines.append(f"【{file_name} - 週間スケジュール】\n")

    for day_item in weekly_schedule:
        date = day_item.get('date', '')
        day = day_item.get('day', '')
        day_of_week = day_item.get('day_of_week', '')
        events = day_item.get('events', [])
        note = day_item.get('note', '')
        class_schedules = day_item.get('class_schedules', [])

        # 日付ヘッダー
        header_parts = []
        if date:
            try:
                if '-' in date:
                    parts = date.split('-')
                    if len(parts) == 3:
                        month = int(parts[1])
                        day_num = int(parts[2])
                        header_parts.append(f"{month}月{day_num}日")
                    else:
                        header_parts.append(date)
                else:
                    header_parts.append(date)
            except:
                header_parts.append(date)

        if day:
            header_parts.append(f"{day}曜日")
        elif day_of_week:
            header_parts.append(day_of_week)

        if header_parts:
            lines.append(f"\n■ {' '.join(header_parts)}")

        # イベント
        if events:
            for event in events:
                if event:
                    lines.append(f"  - {event}")

        # クラススケジュール
        if class_schedules:
            for class_schedule in class_schedules:
                class_name = class_schedule.get('class', '')
                subjects = class_schedule.get('subjects', [])
                periods = class_schedule.get('periods', [])

                if class_name:
                    lines.append(f"  【{class_name}】")

                if subjects:
                    lines.append(f"    科目: {', '.join(subjects)}")

                if periods:
                    for period in periods:
                        subject = period.get('subject', '')
                        time = period.get('time', '')
                        if subject:
                            period_line = f"    - {subject}"
                            if time:
                                period_line += f" ({time})"
                            lines.append(period_line)

        # ノート
        if note:
            lines.append(f"  ※ {note}")

    if len(lines) <= 1:  # ヘッダーのみの場合はスケジュールなし
        return None

    # 検索用キーワードを追加
    lines.append("\n【キーワード】")
    lines.append("週間予定 スケジュール 時間割 授業 クラス 科目 行事")

    synthetic_text = "\n".join(lines)
    logger.debug(f"[合成チャンク] weekly_schedule から {len(weekly_schedule)}日分のスケジュールを抽出")

    return synthetic_text


def create_agenda_synthetic_chunk(metadata: Dict[str, Any], file_name: str = "") -> Optional[str]:
    """
    議事録の議題情報から合成チャンクを生成

    Args:
        metadata: ドキュメントのメタデータ（JSON）
        file_name: ファイル名

    Returns:
        検索専用の合成テキスト、議題がない場合は None
    """
    if not metadata:
        return None

    # tables フィールド内の meeting_minutes を探す
    tables = metadata.get('tables', [])
    agenda_groups = None

    for table in tables:
        if table.get('table_type') == 'meeting_minutes':
            agenda_groups = table.get('agenda_groups', [])
            break

    if not agenda_groups:
        return None

    # 議題専用チャンクの構築
    lines = []
    lines.append(f"【{file_name} - 会議議事録・決定事項】\n")

    for group in agenda_groups:
        topic = group.get('topic', '')
        items = group.get('items', [])

        if topic:
            lines.append(f"\n■ {topic}")

        for item in items:
            decision = item.get('decision', '')
            assignee = item.get('assignee', '')
            deadline = item.get('deadline', '')

            if not decision:
                continue

            item_parts = [f"  - {decision}"]

            if assignee:
                item_parts.append(f"（担当: {assignee}）")
            if deadline:
                item_parts.append(f"（期限: {deadline}）")

            lines.append(" ".join(item_parts))

    if len(lines) <= 1:
        return None

    # 検索用キーワードを追加
    lines.append("\n【キーワード】")
    lines.append("議事録 会議 決定事項 議題 担当 期限 アクション")

    synthetic_text = "\n".join(lines)
    logger.debug(f"[合成チャンク] 議事録から {len(agenda_groups)}件の議題グループを抽出")

    return synthetic_text


def create_table_synthetic_chunk(metadata: Dict[str, Any], file_name: str = "") -> Optional[str]:
    """
    表データから合成チャンクを生成（汎用）

    Args:
        metadata: ドキュメントのメタデータ（JSON）
        file_name: ファイル名

    Returns:
        検索専用の合成テキスト、表がない場合は None
    """
    if not metadata:
        return None

    tables = metadata.get('tables', [])
    if not tables:
        return None

    lines = []
    lines.append(f"【{file_name} - 表データ】\n")

    for idx, table in enumerate(tables, 1):
        table_type = table.get('table_type', 'table')
        headers = table.get('headers', [])
        rows = table.get('rows', [])

        lines.append(f"\n■ 表{idx} ({table_type})")

        # ヘッダー
        if isinstance(headers, list) and headers:
            lines.append(f"  項目: {', '.join(str(h) for h in headers)}")
        elif isinstance(headers, dict):
            classes = headers.get('classes', [])
            if classes:
                lines.append(f"  クラス: {', '.join(str(c) for c in classes)}")

        # 行データ
        if rows:
            lines.append(f"  データ行数: {len(rows)}件")

            # 最初の数行をサンプルとして追加（検索用）
            for row in rows[:3]:  # 最大3行
                if isinstance(row, dict):
                    if 'cells' in row:
                        cells = row['cells']
                        cell_values = []
                        for cell in cells:
                            if isinstance(cell, dict):
                                cell_values.append(str(cell.get('value', '')))
                            else:
                                cell_values.append(str(cell))
                        if cell_values:
                            lines.append(f"    {' | '.join(cell_values)}")
                    else:
                        values = [str(v) for v in row.values()]
                        if values:
                            lines.append(f"    {' | '.join(values)}")

    if len(lines) <= 1:
        return None

    # 検索用キーワードを追加
    lines.append("\n【キーワード】")
    lines.append("表 テーブル データ 一覧 リスト")

    synthetic_text = "\n".join(lines)
    logger.debug(f"[合成チャンク] {len(tables)}件の表データを抽出")

    return synthetic_text


def create_all_synthetic_chunks(metadata: Dict[str, Any], file_name: str = "") -> List[Dict[str, str]]:
    """
    メタデータから全ての合成チャンクを生成

    Args:
        metadata: ドキュメントのメタデータ（JSON）
        file_name: ファイル名

    Returns:
        合成チャンクのリスト、各要素は {"type": str, "content": str} の形式
    """
    synthetic_chunks = []

    # 1. スケジュール専用チャンク
    schedule_chunk = create_schedule_synthetic_chunk(metadata, file_name)
    if schedule_chunk:
        synthetic_chunks.append({
            "type": "monthly_schedule",
            "content": schedule_chunk
        })
        logger.info(f"[合成チャンク] 月間スケジュールチャンク生成: {len(schedule_chunk)}文字")

    # 2. 週間スケジュールチャンク（monthly_schedule_blocksがない場合）
    if not schedule_chunk:
        weekly_chunk = create_weekly_schedule_synthetic_chunk(metadata, file_name)
        if weekly_chunk:
            synthetic_chunks.append({
                "type": "weekly_schedule",
                "content": weekly_chunk
            })
            logger.info(f"[合成チャンク] 週間スケジュールチャンク生成: {len(weekly_chunk)}文字")

    # 3. 議事録専用チャンク
    agenda_chunk = create_agenda_synthetic_chunk(metadata, file_name)
    if agenda_chunk:
        synthetic_chunks.append({
            "type": "meeting_agenda",
            "content": agenda_chunk
        })
        logger.info(f"[合成チャンク] 議事録チャンク生成: {len(agenda_chunk)}文字")

    # 4. 汎用表データチャンク（他の専用チャンクがない場合のフォールバック）
    if not synthetic_chunks:
        table_chunk = create_table_synthetic_chunk(metadata, file_name)
        if table_chunk:
            synthetic_chunks.append({
                "type": "table_data",
                "content": table_chunk
            })
            logger.info(f"[合成チャンク] 表データチャンク生成: {len(table_chunk)}文字")

    return synthetic_chunks
