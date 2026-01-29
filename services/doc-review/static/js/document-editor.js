/**
 * Document Editor - ドキュメントレビュー画面用JavaScript
 * Streamlit版 document_review_app.py と同等の機能を実装
 */

// =============================================================================
// 状態
// =============================================================================

const DocState = {
    documents: [],
    currentDoc: null,
    currentMetadata: null,
    isReviewed: false,
};

// =============================================================================
// フィールド名マッピング (Streamlit版と同等)
// =============================================================================

const FIELD_NAME_MAP = {
    // 新しい構造化フィールド
    "monthly_schedule_list": "📅 月間予定",
    "learning_content_list": "📚 学習予定",
    "weekly_timetable_matrix": "📅 週間時間割",
    // 汎用フィールド
    "articles": "📝 記事・お知らせ",
    "text_blocks": "📝 文章セクション",
    "special_events": "🎉 特別イベント",
    "requirements": "📦 持ち物・準備",
    "important_points": "⚠️ 重要事項",
    // その他の既存フィールド
    "daily_schedule": "📅 日別時間割",
    "weekly_schedule": "📅 週間予定",
    "periods": "📅 時限別科目",
    "class_schedules": "📅 クラス別時間割",
    "structured_tables": "📊 構造化テーブル",
    "monthly_schedule_blocks": "📅 月間予定表",
    "learning_content_blocks": "📚 教科別学習予定",
    "extracted_tables": "📊 抽出テーブル",
    "calendar_events": "📆 カレンダー予定",
    "tasks": "✅ タスク一覧",
    "basic_info": "📋 基本情報",
    "other_text": "📝 その他テキスト",
    "warnings": "⚠️ 警告",
    "schema_validation": "🔍 スキーマ検証",
};

// =============================================================================
// 構造化フィールド検出 (Streamlit版と同等)
// =============================================================================

function detectStructuredFields(metadata) {
    const structuredFields = [];

    for (const [key, value] of Object.entries(metadata)) {
        // articles, text_blocks はフォーム編集タブで表示
        if (key === "articles" || key === "text_blocks") continue;
        // _raw_text_blocks は表示しない（JSONタブでのみ確認可能）
        if (key === "_raw_text_blocks") continue;

        // 構造化データの判定
        const isStructuredKey = (
            key.endsWith("_list") ||
            key.endsWith("_blocks") ||
            key.endsWith("_matrix") ||
            key.endsWith("_tables") ||
            key === "structured_tables" ||
            key === "weekly_schedule" ||
            key === "extracted_tables" ||
            key === "calendar_events" ||
            key === "tasks" ||
            key === "special_events"
        );

        if (isStructuredKey && Array.isArray(value) && value.length > 0) {
            // 最初の要素が辞書であることを確認
            if (typeof value[0] === 'object' && value[0] !== null) {
                structuredFields.push({
                    key: key,
                    label: formatFieldName(key),
                    data: value
                });
            }
        }
    }

    return structuredFields;
}

// =============================================================================
// フィールド名フォーマット (Streamlit版と同等)
// =============================================================================

function formatFieldName(fieldName) {
    if (FIELD_NAME_MAP[fieldName]) {
        return FIELD_NAME_MAP[fieldName];
    }

    // 動的フィールド名の整形
    if (fieldName.endsWith("_list")) {
        const baseName = fieldName.slice(0, -5);
        return "📊 " + baseName.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    } else if (fieldName.endsWith("_blocks")) {
        const baseName = fieldName.slice(0, -7);
        return "📊 " + baseName.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    } else if (fieldName.endsWith("_matrix")) {
        const baseName = fieldName.slice(0, -7);
        return "📅 " + baseName.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    } else if (fieldName.endsWith("_tables")) {
        const baseName = fieldName.slice(0, -7);
        return "📊 " + baseName.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    return fieldName.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// =============================================================================
// セル値のフォーマット (ネストしたオブジェクト対応)
// =============================================================================

function formatCellValue(value, maxLength = 100) {
    if (value === null || value === undefined || value === '') {
        return '';
    }

    if (Array.isArray(value)) {
        // 配列: 各要素を再帰的に処理
        const formatted = value.map(v => formatCellValue(v, 50)).join(', ');
        return formatted.length > maxLength ? formatted.substring(0, maxLength) + '...' : formatted;
    }

    if (typeof value === 'object') {
        // オブジェクト: キー=値 形式で表示
        const entries = Object.entries(value)
            .filter(([k, v]) => v !== null && v !== undefined && v !== '')
            .map(([k, v]) => `${k}: ${formatCellValue(v, 30)}`);
        const formatted = entries.join('; ');
        return formatted.length > maxLength ? formatted.substring(0, maxLength) + '...' : formatted;
    }

    const str = String(value);
    return str.length > maxLength ? str.substring(0, maxLength) + '...' : str;
}

// =============================================================================
// 動的タブ生成
// =============================================================================

function generateDynamicTabs(metadata) {
    const tabsContainer = document.getElementById('metadata-tabs');
    const dynamicContents = document.getElementById('dynamic-tab-contents');

    if (!tabsContainer || !dynamicContents) return;

    // 既存の動的タブを削除
    const existingDynamicTabs = tabsContainer.querySelectorAll('.tab.dynamic-tab');
    existingDynamicTabs.forEach(tab => tab.remove());
    dynamicContents.innerHTML = '';

    // 構造化フィールドを検出
    const structuredFields = detectStructuredFields(metadata);

    // JSON と履歴タブの前に動的タブを挿入
    const jsonTab = tabsContainer.querySelector('[data-tab="tab-json"]');

    structuredFields.forEach((field, index) => {
        // タブボタンを作成
        const tabButton = document.createElement('button');
        tabButton.className = 'tab dynamic-tab';
        tabButton.dataset.tab = `tab-dynamic-${index}`;
        tabButton.textContent = field.label;
        tabsContainer.insertBefore(tabButton, jsonTab);

        // タブコンテンツを作成
        const tabContent = document.createElement('div');
        tabContent.id = `tab-dynamic-${index}`;
        tabContent.className = 'tab-content';
        tabContent.innerHTML = renderStructuredTable(field.key, field.data, field.label);
        dynamicContents.appendChild(tabContent);
    });

    // タブクリックイベントを再設定
    setupTabEvents();
}

// =============================================================================
// 構造化テーブルのレンダリング
// =============================================================================

/**
 * テーブル構造を検出する
 * @param {Object} item - 検査するオブジェクト
 * @returns {Object|null} - {type: 'single'|'multiple', headers, rows} または null
 */
function detectTableStructure(item) {
    if (!item || typeof item !== 'object') return null;

    // パターン1: headers/rows または header/data を持つ単一テーブル
    const rows = item.rows || item.data;
    const headers = item.headers || item.header;
    if (Array.isArray(rows) && rows.length > 0) {
        return { type: 'single', headers: headers || [], rows: rows };
    }

    // パターン2: table_data を持つ（process HI形式）
    if (item.table_data && Array.isArray(item.table_data)) {
        return { type: 'table_data', tableData: item.table_data };
    }

    return null;
}

/**
 * 配列がテーブルデータの集合かどうかを判定
 * @param {Array} data - 検査する配列
 * @returns {boolean}
 */
function isTableCollection(data) {
    if (!Array.isArray(data) || data.length === 0) return false;
    // 最初の要素がテーブル構造を持つか確認
    return detectTableStructure(data[0]) !== null;
}

function renderStructuredTable(key, data, label) {
    if (!Array.isArray(data) || data.length === 0) {
        return `<div class="empty-state"><p>${label}のデータがありません</p></div>`;
    }

    const first = data[0];

    // text_blocks の特別処理（文章ブロック）- これはテーブルではない
    if (key === 'text_blocks' && first.text !== undefined) {
        return renderTextBlocks(key, data, label);
    }

    // weekly_schedule の特別処理（時間割形式）- 特殊なマトリクス表示が必要
    if (key === 'weekly_schedule') {
        // 新形式: table_data を持つ場合（process HIステージ出力）
        if (first.table_data && Array.isArray(first.table_data)) {
            return renderMultipleTables(key, data, label);
        }
        // 旧形式: class_schedules を持つ場合
        if (first.class_schedules) {
            return renderWeeklySchedule(key, data, label);
        }
    }

    // tasks の特別処理（カード形式が適切）
    if (key === 'tasks' && first.task_name !== undefined) {
        return renderTasks(key, data, label);
    }

    // calendar_events の特別処理（日付ソート＋専用カラム）
    if (key === 'calendar_events' && first.event_name !== undefined) {
        return renderCalendarEvents(key, data, label);
    }

    // ★ 汎用テーブル検出: headers/rows, header/data, table_data などを持つ場合
    if (isTableCollection(data)) {
        return renderMultipleTables(key, data, label);
    }

    // structured_tables または *_tables パターン
    if (key === 'structured_tables' || key.includes('_tables')) {
        return renderMultipleTables(key, data, label);
    }

    // 2D配列の場合も汎用テーブルとして表示
    if (Array.isArray(first)) {
        return renderUniversalTable(key, data, label);
    }

    // オブジェクトの配列を通常のテーブルとして表示
    if (typeof first === 'object' && first !== null) {
        return renderUniversalTable(key, data, label);
    }

    return `<div class="empty-state"><p>表示できるデータ形式ではありません</p></div>`;
}

// =============================================================================
// 汎用テーブルレンダリング（どんな形式でも対応）
// =============================================================================

function renderUniversalTable(key, data, label) {
    if (!data || data.length === 0) {
        return `<div class="empty-state"><p>${label}のデータがありません</p></div>`;
    }

    const first = data[0];
    let headers = [];
    let rows = data;

    // データ形式を判定してヘッダーと行を抽出
    if (Array.isArray(first)) {
        // 2D配列形式: 最初の行をヘッダーとして使用するかインデックスベース
        // ヒューリスティック: 最初の行が全て文字列ならヘッダー扱い
        const firstRowAllStrings = first.every(cell => typeof cell === 'string');
        if (firstRowAllStrings && data.length > 1) {
            headers = first;
            rows = data.slice(1);
        } else {
            headers = first.map((_, i) => `列${i + 1}`);
            rows = data;
        }
    } else if (typeof first === 'object' && first !== null) {
        // オブジェクト配列形式: キーをヘッダーとして使用
        const allKeys = new Set();
        data.forEach(row => {
            if (typeof row === 'object' && row !== null) {
                Object.keys(row).forEach(k => allKeys.add(k));
            }
        });
        headers = Array.from(allKeys);

        // 列の並び替え（重要な列を先頭に）
        const priorityOrder = ['順位', 'rank', 'no', 'id', 'name', '名前', '氏名', 'class', 'date', '日付', 'title', 'タイトル'];
        headers.sort((a, b) => {
            const aLower = String(a).toLowerCase();
            const bLower = String(b).toLowerCase();
            const aIndex = priorityOrder.findIndex(p => aLower.includes(p.toLowerCase()));
            const bIndex = priorityOrder.findIndex(p => bLower.includes(p.toLowerCase()));
            if (aIndex !== -1 && bIndex !== -1) return aIndex - bIndex;
            if (aIndex !== -1) return -1;
            if (bIndex !== -1) return 1;
            return 0;
        });
    }

    let html = `
        <div class="table-header-info">
            <span class="table-row-count">${rows.length} 件</span>
        </div>
        <div class="table-wrapper">
            <table class="data-table rendered-table">
                <thead>
                    <tr>
                        ${headers.map(h => `<th>${escapeHtml(formatFieldName(String(h)))}</th>`).join('')}
                    </tr>
                </thead>
                <tbody>
    `;

    rows.forEach(row => {
        html += `<tr>`;
        if (Array.isArray(row)) {
            // 2D配列形式
            row.forEach(cell => {
                const formatted = formatCellValue(cell);
                const fullValue = formatCellValue(cell, 500);
                html += `<td title="${escapeHtml(fullValue)}">${escapeHtml(formatted)}</td>`;
            });
        } else if (typeof row === 'object' && row !== null) {
            // オブジェクト形式
            headers.forEach(k => {
                const value = row[k];
                const formatted = formatCellValue(value);
                const fullValue = formatCellValue(value, 500);
                html += `<td title="${escapeHtml(fullValue)}">${escapeHtml(formatted)}</td>`;
            });
        }
        html += `</tr>`;
    });

    html += `
                </tbody>
            </table>
        </div>
    `;

    // JSON編集オプション
    html += `
        <details class="json-edit-details">
            <summary>🔧 JSONを編集</summary>
            <textarea class="json-editor" data-field="${key}" rows="10">${JSON.stringify(data, null, 2)}</textarea>
        </details>
    `;

    return html;
}

// =============================================================================
// 週間時間割のレンダリング (weekly_schedule用)
// =============================================================================

function renderWeeklySchedule(key, data, label) {
    // クラス名を収集
    const classNames = new Set();
    data.forEach(day => {
        if (day.class_schedules) {
            day.class_schedules.forEach(cs => classNames.add(cs.class));
        }
    });
    const classes = Array.from(classNames);

    // 最大時限数を取得
    let maxPeriod = 0;
    data.forEach(day => {
        if (day.class_schedules) {
            day.class_schedules.forEach(cs => {
                if (cs.periods) {
                    cs.periods.forEach(p => {
                        if (p.period > maxPeriod) maxPeriod = p.period;
                    });
                }
            });
        }
    });

    let html = `<div class="weekly-schedule-container">`;

    // クラスごとにテーブルを生成
    classes.forEach(className => {
        html += `
            <div class="class-schedule-section">
                <h4 class="class-title">📚 ${escapeHtml(className)} 時間割</h4>
                <div class="table-wrapper">
                    <table class="data-table weekly-table">
                        <thead>
                            <tr>
                                <th>日付</th>
                                <th>曜日</th>
                                <th>朝</th>
        `;

        // 時限ヘッダー
        for (let i = 1; i <= maxPeriod; i++) {
            html += `<th>${i}限</th>`;
        }
        html += `<th>イベント</th></tr></thead><tbody>`;

        // 各日のデータ
        data.forEach(day => {
            const classSchedule = day.class_schedules?.find(cs => cs.class === className);
            const periods = classSchedule?.periods || [];

            // 時限別の科目をマップ化
            const periodMap = {};
            periods.forEach(p => {
                periodMap[p.period] = p.subject;
            });

            html += `<tr>`;
            html += `<td>${escapeHtml(day.date || '')}</td>`;
            html += `<td>${escapeHtml(day.day_of_week || '')}</td>`;

            // 朝の活動（period 0）
            html += `<td>${escapeHtml(periodMap[0] || '-')}</td>`;

            // 各時限
            for (let i = 1; i <= maxPeriod; i++) {
                html += `<td>${escapeHtml(periodMap[i] || '-')}</td>`;
            }

            // イベント
            const events = day.events?.join(', ') || '-';
            html += `<td class="events-cell" title="${escapeHtml(events)}">${escapeHtml(truncateText(events, 30))}</td>`;
            html += `</tr>`;
        });

        html += `</tbody></table></div></div>`;
    });

    html += `</div>`;

    // JSON編集オプション
    html += `
        <details class="json-edit-details">
            <summary>🔧 JSONを編集</summary>
            <textarea class="json-editor" data-field="${key}" rows="10">${JSON.stringify(data, null, 2)}</textarea>
        </details>
    `;

    return html;
}

// =============================================================================
// タスク一覧のレンダリング (tasks用)
// =============================================================================

function renderTasks(key, data, label) {
    let html = `<div class="tasks-container">`;

    data.forEach((task, index) => {
        const priority = task.priority || 'medium';
        const priorityClass = priority === 'high' ? 'priority-high' : (priority === 'low' ? 'priority-low' : 'priority-medium');

        html += `
            <div class="task-card ${priorityClass}">
                <div class="task-header">
                    <span class="task-name">${escapeHtml(task.task_name || 'タスク ' + (index + 1))}</span>
                    <span class="task-priority badge badge-${priority === 'high' ? 'warning' : 'info'}">${escapeHtml(priority)}</span>
                </div>
        `;

        if (task.category) {
            html += `<div class="task-category">📁 ${escapeHtml(task.category)}</div>`;
        }

        if (task.description) {
            html += `<div class="task-description">${escapeHtml(task.description)}</div>`;
        }

        if (task.deadline) {
            html += `<div class="task-deadline">📅 期限: ${escapeHtml(task.deadline)}</div>`;
        }

        if (task.checklist && task.checklist.length > 0) {
            html += `<div class="task-checklist"><strong>チェックリスト:</strong><ul>`;
            task.checklist.forEach(item => {
                html += `<li>${escapeHtml(item)}</li>`;
            });
            html += `</ul></div>`;
        }

        html += `</div>`;
    });

    html += `</div>`;

    // JSON編集オプション
    html += `
        <details class="json-edit-details">
            <summary>🔧 JSONを編集</summary>
            <textarea class="json-editor" data-field="${key}" rows="10">${JSON.stringify(data, null, 2)}</textarea>
        </details>
    `;

    return html;
}

// =============================================================================
// カレンダーイベントのレンダリング (calendar_events用)
// =============================================================================

function renderCalendarEvents(key, data, label) {
    // 日付でソート
    const sorted = [...data].sort((a, b) => {
        const dateA = a.event_date || '';
        const dateB = b.event_date || '';
        return dateA.localeCompare(dateB);
    });

    let html = `
        <div class="table-header-info">
            <span class="table-row-count">${data.length} 件のイベント</span>
        </div>
        <div class="table-wrapper">
            <table class="data-table calendar-events-table">
                <thead>
                    <tr>
                        <th>日付</th>
                        <th>時間</th>
                        <th>イベント名</th>
                        <th>場所</th>
                        <th>詳細</th>
                    </tr>
                </thead>
                <tbody>
    `;

    sorted.forEach(event => {
        html += `
            <tr>
                <td>${escapeHtml(event.event_date || '-')}</td>
                <td>${escapeHtml(event.event_time || '-')}</td>
                <td><strong>${escapeHtml(event.event_name || '-')}</strong></td>
                <td>${escapeHtml(event.location || '-')}</td>
                <td title="${escapeHtml(event.description || '')}">${escapeHtml(truncateText(event.description || '-', 50))}</td>
            </tr>
        `;
    });

    html += `</tbody></table></div>`;

    // JSON編集オプション
    html += `
        <details class="json-edit-details">
            <summary>🔧 JSONを編集</summary>
            <textarea class="json-editor" data-field="${key}" rows="10">${JSON.stringify(data, null, 2)}</textarea>
        </details>
    `;

    return html;
}

// =============================================================================
// テキストブロックのレンダリング (text_blocks用)
// =============================================================================

function renderTextBlocks(key, data, label) {
    // 空や無意味なブロックをフィルタ
    const meaningfulBlocks = data.filter(block => {
        const text = (block.text || '').trim();
        // 空、区切り線のみ、メタ情報のみのブロックを除外
        if (!text || text === '---' || text.startsWith('===')) return false;
        return true;
    });

    if (meaningfulBlocks.length === 0) {
        return `<div class="empty-state"><p>表示可能なテキストがありません</p></div>`;
    }

    let html = `<div class="text-blocks-container">`;

    meaningfulBlocks.forEach((block, index) => {
        const blockType = block.block_type || 'paragraph';
        const text = block.text || '';
        const order = block.order !== undefined ? block.order : index;

        // ブロックタイプに応じたアイコン
        const typeIcons = {
            'heading': '📌',
            'paragraph': '📄',
            'list_item': '•',
            'table_text': '📊',
            'table': '📊',
            'post_body': '📝',
        };
        const icon = typeIcons[blockType] || '📄';

        // テーブルテキストは特別処理
        if (blockType === 'table_text' || blockType === 'table') {
            html += `
                <div class="text-block text-block-table">
                    <div class="block-header">
                        <span class="block-icon">${icon}</span>
                        <span class="block-type">テーブル</span>
                    </div>
                    <div class="block-content table-content">
                        ${renderMarkdownTable(text)}
                    </div>
                </div>
            `;
        } else if (blockType === 'heading') {
            html += `
                <div class="text-block text-block-heading">
                    <div class="block-content heading-content">
                        ${escapeHtml(text.replace(/^#+\s*/, ''))}
                    </div>
                </div>
            `;
        } else if (blockType === 'list_item') {
            html += `
                <div class="text-block text-block-list">
                    <div class="block-content list-content">
                        ${escapeHtml(text.replace(/^[-•*]\s*/, ''))}
                    </div>
                </div>
            `;
        } else {
            // 通常のパラグラフ
            html += `
                <div class="text-block text-block-paragraph">
                    <div class="block-content">
                        ${escapeHtml(text).replace(/\n/g, '<br>')}
                    </div>
                </div>
            `;
        }
    });

    html += `</div>`;

    // JSON編集オプション
    html += `
        <details class="json-edit-details">
            <summary>🔧 JSONを編集</summary>
            <textarea class="json-editor" data-field="${key}" rows="10">${JSON.stringify(data, null, 2)}</textarea>
        </details>
    `;

    return html;
}

// マークダウンテーブルをHTMLに変換
function renderMarkdownTable(text) {
    const lines = text.trim().split('\n').filter(line => line.trim());
    if (lines.length < 2) {
        return `<pre class="table-raw">${escapeHtml(text)}</pre>`;
    }

    try {
        // ヘッダー行を解析
        const headerLine = lines[0];
        const headers = headerLine.split('|').map(h => h.trim()).filter(h => h !== '');

        // 区切り行をスキップ（---を含む行）
        let dataStartIndex = 1;
        if (lines[1] && lines[1].includes('---')) {
            dataStartIndex = 2;
        }

        // データ行
        const dataLines = lines.slice(dataStartIndex);

        let tableHtml = `<div class="table-wrapper"><table class="rendered-table markdown-table"><thead><tr>`;
        headers.forEach(h => {
            tableHtml += `<th>${escapeHtml(h)}</th>`;
        });
        tableHtml += `</tr></thead><tbody>`;

        dataLines.forEach(line => {
            if (line.includes('---')) return; // 区切り行スキップ
            const cells = line.split('|').map(c => c.trim()).filter((c, i, arr) => {
                // 最初と最後の空セルを除去（|で始まり|で終わる場合）
                return !(i === 0 && c === '') && !(i === arr.length - 1 && c === '');
            });
            if (cells.length === 0) return;

            tableHtml += `<tr>`;
            cells.forEach(cell => {
                tableHtml += `<td>${escapeHtml(cell)}</td>`;
            });
            tableHtml += `</tr>`;
        });

        tableHtml += `</tbody></table></div>`;
        return tableHtml;

    } catch (e) {
        // パースに失敗した場合は生テキスト表示
        return `<pre class="table-raw">${escapeHtml(text)}</pre>`;
    }
}

// =============================================================================
// table_data形式のレンダリング（process HIステージ出力用）
// =============================================================================

function renderTableDataFormat(table, index) {
    const tableData = table.table_data;
    const refIds = table.ref_ids || [];

    if (!tableData || tableData.length === 0) {
        return `<div class="empty-state"><p>テーブルデータがありません</p></div>`;
    }

    // クラス名を検出（class_で始まるキー）
    const firstRow = tableData[0];
    const classKeys = Object.keys(firstRow).filter(k => k.startsWith('class_'));
    const hasDate = 'date' in firstRow;

    // クラス別時間割形式かどうかを判定
    if (classKeys.length > 0 && hasDate) {
        return renderClassTimetable(tableData, classKeys, refIds, index);
    }

    // 汎用テーブル形式
    return renderGenericTableData(tableData, refIds, index);
}

// クラス別時間割のレンダリング
function renderClassTimetable(tableData, classKeys, refIds, index) {
    let html = `<div class="timetable-container">`;

    if (refIds.length > 0) {
        html += `<div class="table-ref-ids"><small>Ref: ${escapeHtml(refIds.join(', '))}</small></div>`;
    }

    // 時限を収集
    const periods = new Set();
    tableData.forEach(row => {
        classKeys.forEach(classKey => {
            const classData = row[classKey];
            if (classData && typeof classData === 'object') {
                Object.keys(classData).forEach(k => {
                    if (k !== 'morning' && k !== 'notes') {
                        periods.add(k);
                    }
                });
            }
        });
    });
    const sortedPeriods = Array.from(periods).sort((a, b) => parseInt(a) - parseInt(b));

    // クラスごとにテーブルを作成
    classKeys.forEach(classKey => {
        const className = classKey.replace('class_', '').replace('_', ' ');

        html += `
            <div class="class-timetable-section">
                <h4 class="class-title">📚 ${escapeHtml(className)} 時間割</h4>
                <div class="table-wrapper">
                    <table class="data-table timetable-table">
                        <thead>
                            <tr>
                                <th>日付</th>
                                <th>朝</th>
        `;

        // 時限ヘッダー
        sortedPeriods.forEach(p => {
            html += `<th>${p}限</th>`;
        });

        html += `</tr></thead><tbody>`;

        // 各日のデータ
        tableData.forEach(row => {
            const date = row.date || '';
            const classData = row[classKey] || {};

            html += `<tr>`;
            html += `<td class="date-cell">${escapeHtml(date)}</td>`;
            html += `<td>${escapeHtml(classData.morning || '-')}</td>`;

            sortedPeriods.forEach(p => {
                const subject = classData[p] || '-';
                html += `<td>${escapeHtml(subject)}</td>`;
            });

            html += `</tr>`;
        });

        html += `</tbody></table></div></div>`;
    });

    html += `</div>`;
    return html;
}

// 汎用table_dataレンダリング
function renderGenericTableData(tableData, refIds, index) {
    if (!tableData || tableData.length === 0) {
        return `<div class="empty-state"><p>データがありません</p></div>`;
    }

    // 全キーを収集
    const allKeys = new Set();
    tableData.forEach(row => {
        if (typeof row === 'object' && row !== null) {
            Object.keys(row).forEach(k => allKeys.add(k));
        }
    });
    const keys = Array.from(allKeys);

    let html = `<div class="generic-table-section">`;

    if (refIds.length > 0) {
        html += `<div class="table-ref-ids"><small>Ref: ${escapeHtml(refIds.join(', '))}</small></div>`;
    }

    html += `
        <div class="table-info">${tableData.length} 行</div>
        <div class="table-wrapper">
            <table class="data-table rendered-table">
                <thead>
                    <tr>
                        ${keys.map(k => `<th>${escapeHtml(formatFieldName(k))}</th>`).join('')}
                    </tr>
                </thead>
                <tbody>
    `;

    tableData.forEach(row => {
        html += `<tr>`;
        keys.forEach(k => {
            const value = row[k];
            const formatted = formatCellValue(value);
            html += `<td title="${escapeHtml(formatCellValue(value, 500))}">${escapeHtml(formatted)}</td>`;
        });
        html += `</tr>`;
    });

    html += `</tbody></table></div></div>`;
    return html;
}

// =============================================================================
// 複数テーブルのレンダリング (structured_tables用)
// =============================================================================

function renderMultipleTables(key, tables, label) {
    let html = `<div class="structured-tables-container">`;

    tables.forEach((table, index) => {
        // 新形式: table_data を持つ場合（process HIステージ出力）
        if (table.table_data && Array.isArray(table.table_data)) {
            html += renderTableDataFormat(table, index);
            return;
        }

        const tableTitle = table.table_title || table.table_name || table.description || `表 ${index + 1}`;
        const tableType = table.table_type || '';

        // キー名の柔軟な対応: rows/data どちらも受け付ける
        const rows = table.rows || table.data || [];

        // ヘッダーの柔軟な取得: columns/headers/header どちらも受け付ける（カラムナ形式優先）
        // 行がオブジェクト配列の場合はキーから自動生成
        let headers = table.columns || table.headers || table.header || [];
        if ((!headers || headers.length === 0) && rows.length > 0) {
            if (Array.isArray(rows[0])) {
                // 2D配列の場合: ヘッダーがなければインデックスベースで生成
                headers = rows[0].map((_, i) => `列${i + 1}`);
            } else if (typeof rows[0] === 'object' && rows[0] !== null) {
                // オブジェクト配列の場合: キーから自動生成
                headers = Object.keys(rows[0]);
            }
        }

        // data_summary がある場合は警告表示（テキスト要約ではなく構造化データが必要）
        const hasDataSummary = table.data_summary && (!rows || rows.length === 0);

        html += `
            <div class="structured-table-section">
                <h4 class="table-title">${escapeHtml(tableTitle)}</h4>
                ${tableType ? `<span class="table-type-badge">${escapeHtml(tableType)}</span>` : ''}
                <div class="table-info">${rows.length} 行</div>
        `;

        if (hasDataSummary) {
            // data_summary のみでrows がない場合: テキストとして表示
            html += `
                <div class="data-summary-notice" style="background:#fff3cd;border:1px solid #ffc107;padding:10px;border-radius:4px;margin:10px 0;">
                    <strong>⚠️ 構造化データなし（要約のみ）</strong>
                    <pre style="white-space:pre-wrap;margin:8px 0 0 0;font-size:13px;">${escapeHtml(table.data_summary)}</pre>
                </div>
            `;
        } else if (rows.length > 0) {
            html += `<div class="table-wrapper">`;
            html += `<table class="data-table rendered-table">`;
            html += `<thead><tr>`;

            // ヘッダー
            if (Array.isArray(headers) && headers.length > 0) {
                headers.forEach(h => {
                    html += `<th>${escapeHtml(formatFieldName(String(h)))}</th>`;
                });
            }

            html += `</tr></thead><tbody>`;

            // 行データ（配列とオブジェクト両方に対応）
            rows.forEach(row => {
                html += `<tr>`;
                if (Array.isArray(row)) {
                    // 2D配列形式: 各セルを順番に出力
                    row.forEach(cell => {
                        html += `<td title="${escapeHtml(formatCellValue(cell, 500))}">${escapeHtml(formatCellValue(cell))}</td>`;
                    });
                } else if (typeof row === 'object' && row !== null) {
                    // オブジェクト形式: ヘッダーのキーに対応する値を出力
                    headers.forEach(k => {
                        const value = row[k];
                        html += `<td title="${escapeHtml(formatCellValue(value, 500))}">${escapeHtml(formatCellValue(value))}</td>`;
                    });
                }
                html += `</tr>`;
            });

            html += `</tbody></table></div>`;
        } else {
            html += `<p class="no-data">データがありません</p>`;
        }

        html += `</div>`;
    });

    html += `</div>`;

    // JSON編集オプション
    html += `
        <details class="json-edit-details">
            <summary>🔧 JSONを編集</summary>
            <textarea class="json-editor" data-field="${key}" rows="10">${JSON.stringify(tables, null, 2)}</textarea>
        </details>
    `;

    return html;
}

// =============================================================================
// オブジェクト配列のテーブルレンダリング（後方互換性のためのエイリアス）
// =============================================================================

function renderObjectArrayTable(key, data, label) {
    // 汎用テーブルレンダラーにリダイレクト
    return renderUniversalTable(key, data, label);
}

// =============================================================================
// ドキュメント一覧
// =============================================================================

async function loadDocuments() {
    const container = document.getElementById('document-list-container');
    App.showLoading('document-list-container');

    try {
        // フィルタ値を取得
        const workspace = document.getElementById('workspace-filter')?.value || '';
        const fileType = document.getElementById('filetype-filter')?.value || '';
        const status = document.getElementById('status-filter')?.value || 'pending';
        const search = document.getElementById('search-input')?.value || '';

        const params = new URLSearchParams({
            workspace,
            file_type: fileType,
            review_status: status,
            search,
            processing_status: 'completed',  // completedのみ表示
            limit: 100,
        });

        const data = await App.api(`/api/documents?${params}`);
        DocState.documents = data.documents;

        renderDocumentList();
        updateDocumentCount(data.count);

        // URLにdoc_idがあれば選択
        const docIdFromUrl = App.getUrlParam('doc_id');
        if (docIdFromUrl) {
            selectDocument(docIdFromUrl);
        }

    } catch (error) {
        console.error('Failed to load documents:', error);
        container.innerHTML = `<div class="error-message">読み込みに失敗しました: ${error.message}</div>`;
    }
}

function renderDocumentList() {
    const container = document.getElementById('document-list-container');

    if (DocState.documents.length === 0) {
        container.innerHTML = '<div class="empty-state" style="padding:30px;"><p>レビュー対象がありません</p></div>';
        return;
    }

    // リスト形式で表示（ID不要）
    const html = DocState.documents.map(doc => {
        const isSelected = DocState.currentDoc?.id === doc.id;
        const isReviewed = doc.review_status === 'reviewed';
        const title = doc.title || doc.file_name || '(タイトルなし)';
        const date = (doc.updated_at || doc.created_at || '').substring(0, 10);
        const docType = doc.doc_type || '';

        return `
            <div class="doc-list-item ${isSelected ? 'selected' : ''} ${isReviewed ? 'reviewed' : ''}"
                 data-id="${doc.id}"
                 onclick="selectDocument('${doc.id}')">
                <div class="doc-name" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
                <div class="doc-meta">
                    <span>${date}</span>
                    ${docType ? `<span>${docType}</span>` : ''}
                    <span class="doc-status ${isReviewed ? 'reviewed' : 'pending'}">
                        ${isReviewed ? '✓済' : '未'}
                    </span>
                </div>
            </div>
        `;
    }).join('');

    container.innerHTML = html;
}

function updateDocumentCount(count) {
    const el = document.getElementById('document-count');
    const toggleCount = document.getElementById('toggle-count');
    if (el) {
        el.textContent = `${count}件`;
    }
    if (toggleCount) {
        toggleCount.textContent = count;
    }
}

// =============================================================================
// ドキュメント選択・詳細
// =============================================================================

async function selectDocument(docId) {
    // URLを更新
    App.setUrlParam('doc_id', docId);

    // リストアイテムのハイライトを更新
    document.querySelectorAll('.doc-list-item').forEach(item => {
        item.classList.remove('selected');
        if (item.dataset.id === docId) {
            item.classList.add('selected');
        }
    });

    // サイドパネルを閉じる（モバイルでは自動的に）
    const sidePanel = document.getElementById('side-panel');
    if (sidePanel && window.innerWidth < 1200) {
        sidePanel.classList.add('collapsed');
    }

    // 詳細コンテナを表示
    document.getElementById('document-detail-container').style.display = 'block';

    try {
        const doc = await App.api(`/api/documents/${docId}`);
        DocState.currentDoc = doc;
        DocState.currentMetadata = doc.metadata || {};
        DocState.isReviewed = doc.review_status === 'reviewed';

        renderDocumentDetail(doc);
        loadPreview(doc);
        loadHistory(docId);

    } catch (error) {
        console.error('Failed to load document:', error);
        App.toast('ドキュメントの読み込みに失敗しました', 'error');
    }
}

function renderDocumentDetail(doc) {
    // タイトル
    document.getElementById('doc-title').textContent = doc.title || doc.file_name || '';

    const metadata = doc.metadata || {};

    // 動的タブを生成
    generateDynamicTabs(metadata);

    // フォーム編集（基本フィールドのみ）
    renderFormEditor(metadata);

    // JSON編集
    document.getElementById('json-editor').value = JSON.stringify(metadata, null, 2);

    // レビュー状態によるボタン表示切り替え
    const reviewBtn = document.getElementById('review-btn');
    const unreviewBtn = document.getElementById('unreview-btn');

    if (DocState.isReviewed) {
        reviewBtn.style.display = 'none';
        unreviewBtn.style.display = 'inline-block';
    } else {
        reviewBtn.style.display = 'inline-block';
        unreviewBtn.style.display = 'none';
    }
}

// =============================================================================
// フォーム編集（基本フィールドのみ - 構造化データは動的タブで表示）
// =============================================================================

function renderFormEditor(metadata) {
    const container = document.getElementById('form-editor-container');

    if (!metadata || Object.keys(metadata).length === 0) {
        container.innerHTML = '<p>メタデータがありません</p>';
        return;
    }

    // 構造化フィールドのキーを取得（これらはフォームから除外）
    const structuredFields = detectStructuredFields(metadata);
    const structuredKeys = new Set(structuredFields.map(f => f.key));

    // articles を優先、なければ text_blocks を表示（後方互換性）
    let articlesHtml = '';
    if (metadata.articles && Array.isArray(metadata.articles) && metadata.articles.length > 0) {
        articlesHtml = renderArticlesForm(metadata.articles);
    } else if (metadata.text_blocks && Array.isArray(metadata.text_blocks)) {
        articlesHtml = renderTextBlocksForm(metadata.text_blocks, metadata);
    }

    // 基本フィールド（構造化データとarticles/text_blocks/_raw_text_blocks以外）
    const basicFields = Object.entries(metadata)
        .filter(([key]) => !structuredKeys.has(key) && key !== 'articles' && key !== 'text_blocks' && key !== '_raw_text_blocks')
        .map(([key, value]) => {
            const isArray = Array.isArray(value);
            const isObject = typeof value === 'object' && value !== null && !isArray;

            // 小さい配列やオブジェクトはインラインテーブルで表示
            if (isArray && value.length > 0 && value.length <= 10 && typeof value[0] === 'object') {
                return renderInlineTable(key, value);
            }

            if (isArray) {
                // 単純な配列はリストで表示
                return `
                    <div class="form-group">
                        <label>${formatFieldName(key)}</label>
                        <div class="array-list">
                            ${value.map(item => `<span class="array-item">${escapeHtml(formatCellValue(item))}</span>`).join('')}
                        </div>
                    </div>
                `;
            }

            if (isObject) {
                // オブジェクトはキー・値テーブルで表示
                return renderKeyValueTable(key, value);
            }

            // スカラー値
            return `
                <div class="form-group">
                    <label>${formatFieldName(key)}</label>
                    <input type="text"
                           data-field="${key}"
                           value="${escapeHtml(String(value || ''))}">
                </div>
            `;
        });

    // articles/text_blocks を上部に、その他のフィールドを下部に配置
    const content = articlesHtml + basicFields.join('');
    container.innerHTML = content || '<p>編集可能なフィールドがありません</p>';
}

// =============================================================================
// 記事（articles）のフォーム表示（編集可能）
// =============================================================================

function renderArticlesForm(articles) {
    if (!articles || articles.length === 0) {
        return '';
    }

    let html = `<div class="articles-form-section">
        <h4 class="section-label">📝 記事・お知らせ</h4>`;

    articles.forEach((article, index) => {
        const title = article.title || `記事 ${index + 1}`;
        const body = article.body || '';
        const rows = Math.min(Math.max(body.split('\n').length + 2, 8), 20);

        html += `
            <div class="article-item">
                <div class="article-header">
                    <input type="text"
                           class="article-title-input"
                           data-article-index="${index}"
                           data-field="title"
                           value="${escapeHtml(title)}"
                           placeholder="見出し">
                </div>
                <textarea class="article-body-editor"
                          data-article-index="${index}"
                          data-field="body"
                          rows="${rows}"
                          placeholder="本文">${escapeHtml(body)}</textarea>
            </div>
        `;
    });

    html += `</div>`;
    return html;
}

// =============================================================================
// テキストブロックのフォーム表示（編集可能）- 後方互換性のため維持
// =============================================================================

function renderTextBlocksForm(textBlocks, metadata) {
    // 構造化済みデータの有無をチェック
    const hasCalendarEvents = metadata.calendar_events && metadata.calendar_events.length > 0;
    const hasWeeklySchedule = metadata.weekly_schedule && metadata.weekly_schedule.length > 0;
    const hasTasks = metadata.tasks && metadata.tasks.length > 0;
    const hasStructuredTables = metadata.structured_tables && metadata.structured_tables.length > 0;

    // Vision OCR 補完情報以降のブロックを特定（重複データなので除外）
    let visionOcrStartIndex = -1;
    for (let i = 0; i < textBlocks.length; i++) {
        const text = (textBlocks[i].text || '').trim();
        if (text.includes('Vision OCR') || text.includes('補完情報')) {
            visionOcrStartIndex = i;
            break;
        }
    }

    // テキスト系ブロックのみフィルタ（構造化済み・重複データは除外）
    const textOnlyBlocks = textBlocks.filter((block, index) => {
        const text = (block.text || '').trim();
        const blockType = block.block_type || '';

        // Vision OCR 補完情報以降は全て除外（重複）
        if (visionOcrStartIndex !== -1 && index >= visionOcrStartIndex) return false;

        // 空は除外
        if (!text) return false;

        // 区切り線やメタ情報は除外
        if (text === '---') return false;
        if (text.startsWith('=== [SOURCE:')) return false;
        if (text.startsWith('# Page ')) return false;

        // マークダウン記法の見出し（## で始まる）は除外（構造化済み）
        if (text.startsWith('## ') || text.startsWith('# ')) return false;

        // 発行情報（年号、発行日など）は除外（structured_tables に既にある）
        if (/^\d{4}年$/.test(text)) return false;
        if (/^\d+\/\d+\s*発行/.test(text)) return false;
        if (/^\*\*\d+\*\*$/.test(text)) return false;  // **34** のような号数

        // テーブル系は専用タブで表示するので除外
        if (blockType === 'table_text' || blockType === 'table') return false;

        // calendar_events がある場合、日程リストを除外
        if (hasCalendarEvents) {
            if (blockType === 'list_item' && /\d+日|\/\d+/.test(text)) return false;
            if (blockType === 'heading' && text.includes('予定')) return false;
            // 「・」で始まる日程リストも除外
            if (text.startsWith('・') && /\d+日/.test(text)) return false;
        }

        // weekly_schedule がある場合、時間割関連を除外
        if (hasWeeklySchedule) {
            if (blockType === 'heading' && /時間割|時限/.test(text)) return false;
        }

        // tasks がある場合、タスク関連を除外
        if (hasTasks) {
            if (blockType === 'heading' && /タスク|TODO|持ち物/.test(text)) return false;
        }

        // structured_tables がある場合
        if (hasStructuredTables) {
            // 学校名・学年の単独行は除外
            if (/^洗足学園/.test(text) || /^\d+年生$/.test(text)) return false;
            // タイトル（HEROなど）の単独行は除外
            if (/^\*\*[A-Z]+\*\*$/.test(text) || /^[A-Z]{2,}$/.test(text)) return false;
        }

        return true;
    });

    if (textOnlyBlocks.length === 0) {
        return '';
    }

    // 見出しごとにセクションをグループ化
    const sections = [];
    let currentSection = null;

    textOnlyBlocks.forEach((block) => {
        const blockType = block.block_type || 'paragraph';
        const text = (block.text || '').trim();

        if (blockType === 'heading') {
            // 新しいセクションを開始
            currentSection = {
                title: text.replace(/^#+\s*/, ''),  // マークダウンの#を除去
                blocks: [],
                blockIndices: []
            };
            sections.push(currentSection);
        } else {
            // 現在のセクションに追加（セクションがなければ作成）
            if (!currentSection) {
                currentSection = {
                    title: null,
                    blocks: [],
                    blockIndices: []
                };
                sections.push(currentSection);
            }
            currentSection.blocks.push(text);
            currentSection.blockIndices.push(textBlocks.indexOf(block));
        }
    });

    // セクションをレンダリング
    let html = `<div class="text-blocks-form-section">`;

    sections.forEach((section, sectionIndex) => {
        // 空のセクションはスキップ
        if (!section.title && section.blocks.length === 0) return;

        // セクションタイトル（見出しがある場合）
        const sectionTitle = section.title || `セクション ${sectionIndex + 1}`;

        // ブロックのテキストを結合
        const combinedText = section.blocks.join('\n\n');

        if (!combinedText && !section.title) return;

        const rows = Math.min(Math.max((combinedText || '').split('\n').length + 2, 8), 20);

        html += `
            <div class="text-section-item">
                <div class="section-header">
                    <span class="section-title-label">📝 ${escapeHtml(sectionTitle)}</span>
                </div>
                ${combinedText ? `
                    <textarea class="section-text-editor"
                              data-section-index="${sectionIndex}"
                              data-block-indices="${section.blockIndices.join(',')}"
                              rows="${rows}">${escapeHtml(combinedText)}</textarea>
                ` : ''}
            </div>
        `;
    });

    html += `</div>`;
    return html;
}

// =============================================================================
// インラインテーブル（小さい配列用）
// =============================================================================

function renderInlineTable(key, data) {
    if (!data || data.length === 0) return '';

    const allKeys = new Set();
    data.forEach(row => {
        if (typeof row === 'object' && row !== null) {
            Object.keys(row).forEach(k => allKeys.add(k));
        }
    });

    const keys = Array.from(allKeys);

    return `
        <div class="form-group inline-table-group">
            <label>${formatFieldName(key)}</label>
            <div class="table-wrapper compact">
                <table class="data-table rendered-table compact">
                    <thead>
                        <tr>
                            ${keys.map(k => `<th>${escapeHtml(formatFieldName(k))}</th>`).join('')}
                        </tr>
                    </thead>
                    <tbody>
                        ${data.map(row => `
                            <tr>
                                ${keys.map(k => `<td>${escapeHtml(formatCellValue(row[k]))}</td>`).join('')}
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        </div>
    `;
}

// =============================================================================
// キー・値テーブル（オブジェクト用）
// =============================================================================

function renderKeyValueTable(key, obj) {
    if (!obj || typeof obj !== 'object') return '';

    const entries = Object.entries(obj).filter(([k, v]) => v !== null && v !== undefined);

    return `
        <div class="form-group kv-table-group">
            <label>${formatFieldName(key)}</label>
            <div class="table-wrapper compact">
                <table class="data-table rendered-table kv-table">
                    <tbody>
                        ${entries.map(([k, v]) => `
                            <tr>
                                <th>${escapeHtml(formatFieldName(k))}</th>
                                <td>${escapeHtml(formatCellValue(v))}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        </div>
    `;
}

// =============================================================================
// タブイベント設定
// =============================================================================

function setupTabEvents() {
    document.querySelectorAll('.tabs .tab').forEach(tab => {
        tab.addEventListener('click', () => {
            // 全タブの active を解除
            document.querySelectorAll('.tabs .tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

            // クリックされたタブを active に
            tab.classList.add('active');
            const targetId = tab.dataset.tab;
            const targetContent = document.getElementById(targetId);
            if (targetContent) {
                targetContent.classList.add('active');
            }
        });
    });
}

// =============================================================================
// ユーティリティ
// =============================================================================

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function truncateText(text, maxLength = 100) {
    if (!text) return '';
    const str = String(text);
    return str.length > maxLength ? str.substring(0, maxLength) + '...' : str;
}

// =============================================================================
// プレビュー
// =============================================================================

async function loadPreview(doc) {
    const container = document.getElementById('preview-container');
    const fileId = doc.drive_file_id || doc.source_id;
    const fileName = doc.file_name || 'document';

    if (!fileId) {
        container.innerHTML = '<div class="empty-state"><p>ファイルIDがありません</p></div>';
        return;
    }

    // ファイル拡張子を判定
    const ext = fileName.split('.').pop()?.toLowerCase() || '';

    if (ext === 'pdf') {
        // PDF.jsで表示
        container.innerHTML = `
            <div style="text-align:center;padding:20px;">
                <div class="spinner"></div>
                <p>PDFを読み込み中...</p>
            </div>
        `;

        try {
            // fetchでPDFをArrayBufferとして取得
            const response = await fetch(`/api/documents/${doc.id}/preview`, {
                credentials: 'same-origin'
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const arrayBuffer = await response.arrayBuffer();

            // PDF.jsで読み込み
            const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;

            // キャンバスコンテナを作成
            container.innerHTML = `
                <div id="pdf-pages" style="overflow:auto;height:100%;background:#666;padding:10px;"></div>
            `;
            const pagesContainer = document.getElementById('pdf-pages');

            // 全ページをレンダリング
            for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
                const page = await pdf.getPage(pageNum);
                const scale = 1.5;
                const viewport = page.getViewport({ scale });

                const canvas = document.createElement('canvas');
                canvas.style.display = 'block';
                canvas.style.margin = '0 auto 10px auto';
                canvas.style.boxShadow = '0 2px 8px rgba(0,0,0,0.3)';
                canvas.width = viewport.width;
                canvas.height = viewport.height;

                const context = canvas.getContext('2d');
                await page.render({ canvasContext: context, viewport }).promise;

                pagesContainer.appendChild(canvas);
            }

        } catch (err) {
            console.error('PDF load error:', err);
            container.innerHTML = `
                <div class="empty-state">
                    <p>PDFの読み込みに失敗しました: ${err.message}</p>
                    <a href="/api/documents/${doc.id}/preview" class="btn btn-primary" target="_blank">
                        📥 ダウンロード
                    </a>
                </div>
            `;
        }

    } else if (['txt', 'md', 'csv', 'json', 'log'].includes(ext)) {
        // テキストファイル
        fetch(`/api/documents/${doc.id}/preview`)
            .then(res => res.text())
            .then(text => {
                container.innerHTML = `
                    <pre style="padding:20px;overflow:auto;height:100%;margin:0;background:#fff;font-size:13px;">
${escapeHtml(text)}
                    </pre>
                `;
            })
            .catch(err => {
                container.innerHTML = `<div class="error-message">読み込みエラー: ${err.message}</div>`;
            });
    } else {
        // その他
        container.innerHTML = `
            <div class="empty-state">
                <p>このファイルタイプ(.${ext})のプレビューには対応していません</p>
                <a href="/api/documents/${doc.id}/preview" class="btn btn-primary" download="${fileName}">
                    📥 ダウンロード
                </a>
            </div>
        `;
    }
}

// =============================================================================
// 修正履歴
// =============================================================================

async function loadHistory(docId) {
    const container = document.getElementById('history-container');

    try {
        const data = await App.api(`/api/documents/${docId}/history`);

        if (!data.history || data.history.length === 0) {
            container.innerHTML = '<p>修正履歴がありません</p>';
            return;
        }

        container.innerHTML = data.history.map((h, i) => `
            <div style="margin-bottom:15px;padding:10px;background:#f8f9fa;border-radius:6px;">
                <strong>修正 #${i + 1}</strong>
                <div style="font-size:13px;color:#666;">
                    ${h.corrected_at || ''} ${h.corrector_email ? `by ${h.corrector_email}` : ''}
                </div>
                ${h.notes ? `<div style="margin-top:5px;">${escapeHtml(h.notes)}</div>` : ''}
            </div>
        `).join('');

    } catch (error) {
        console.error('Failed to load history:', error);
        container.innerHTML = '<p>履歴の読み込みに失敗しました</p>';
    }
}

// =============================================================================
// アクション
// =============================================================================

async function saveDocument() {
    if (!DocState.currentDoc) {
        App.toast('ドキュメントが選択されていません', 'error');
        return;
    }

    // 現在のメタデータをベースにする
    let metadata = JSON.parse(JSON.stringify(DocState.currentMetadata || {}));

    // フォームからの編集をマージ
    collectFormEdits(metadata);

    // JSONエディタタブがアクティブな場合はJSONエディタの値を使用
    const jsonTab = document.querySelector('[data-tab="tab-json"]');
    if (jsonTab && jsonTab.classList.contains('active')) {
        const jsonEditor = document.getElementById('json-editor');
        try {
            metadata = JSON.parse(jsonEditor.value);
        } catch (e) {
            document.getElementById('json-error').textContent = 'JSONの形式が正しくありません';
            document.getElementById('json-error').style.display = 'block';
            App.toast('JSONの形式が正しくありません', 'error');
            return;
        }
    }

    document.getElementById('json-error').style.display = 'none';

    try {
        await App.api(`/api/documents/${DocState.currentDoc.id}`, {
            method: 'PUT',
            body: JSON.stringify({
                metadata,
                doc_type: DocState.currentDoc.doc_type,
                notes: 'Flask UIからの手動修正',
            }),
        });

        App.toast('保存しました', 'success');
        DocState.currentMetadata = metadata;

        // JSONエディタも更新
        document.getElementById('json-editor').value = JSON.stringify(metadata, null, 2);

        loadHistory(DocState.currentDoc.id);

    } catch (error) {
        App.toast(`保存に失敗しました: ${error.message}`, 'error');
    }
}

// フォームの編集内容を収集してメタデータにマージ
function collectFormEdits(metadata) {
    // 基本フィールド（input）- articles用は除外
    document.querySelectorAll('#form-editor-container input[data-field]:not([data-article-index])').forEach(input => {
        const field = input.dataset.field;
        metadata[field] = input.value;
    });

    // articles の編集内容を収集
    const articleTitles = document.querySelectorAll('#form-editor-container .article-title-input');
    const articleBodies = document.querySelectorAll('#form-editor-container .article-body-editor');
    if (articleTitles.length > 0 || articleBodies.length > 0) {
        if (!metadata.articles) {
            metadata.articles = [];
        }
        articleTitles.forEach(input => {
            const idx = parseInt(input.dataset.articleIndex, 10);
            if (!isNaN(idx)) {
                if (!metadata.articles[idx]) metadata.articles[idx] = {};
                metadata.articles[idx].title = input.value || null;
            }
        });
        articleBodies.forEach(textarea => {
            const idx = parseInt(textarea.dataset.articleIndex, 10);
            if (!isNaN(idx)) {
                if (!metadata.articles[idx]) metadata.articles[idx] = {};
                metadata.articles[idx].body = textarea.value;
            }
        });
    }

    // テキストセクションの編集（見出しごとにグループ化されたもの）
    document.querySelectorAll('#form-editor-container .section-text-editor').forEach(textarea => {
        const blockIndicesStr = textarea.dataset.blockIndices;
        if (!blockIndicesStr || !metadata.text_blocks) return;

        const blockIndices = blockIndicesStr.split(',').map(i => parseInt(i, 10)).filter(i => !isNaN(i));
        const newText = textarea.value;

        // 編集されたテキストを段落ごとに分割
        const paragraphs = newText.split(/\n\n+/).map(p => p.trim()).filter(p => p);

        // 元のブロックに分配（段落数が変わった場合は最初のブロックに全て入れる）
        if (blockIndices.length === 1 || paragraphs.length !== blockIndices.length) {
            // 単一ブロックまたは段落数不一致：最初のブロックに全テキスト
            if (blockIndices[0] !== undefined && metadata.text_blocks[blockIndices[0]]) {
                metadata.text_blocks[blockIndices[0]].text = newText;
            }
        } else {
            // 段落数が一致：各ブロックに対応する段落を設定
            blockIndices.forEach((idx, i) => {
                if (metadata.text_blocks[idx]) {
                    metadata.text_blocks[idx].text = paragraphs[i] || '';
                }
            });
        }
    });

    // 旧形式のテキストブロック編集（互換性のため）
    document.querySelectorAll('#form-editor-container .block-text-editor').forEach(textarea => {
        const blockIndex = parseInt(textarea.dataset.blockIndex, 10);
        if (!isNaN(blockIndex) && metadata.text_blocks && metadata.text_blocks[blockIndex]) {
            metadata.text_blocks[blockIndex].text = textarea.value;
        }
    });
}

async function markReviewed() {
    if (!DocState.currentDoc) return;

    try {
        await App.api(`/api/documents/${DocState.currentDoc.id}/review`, { method: 'POST' });
        App.toast('レビュー完了としてマークしました', 'success');
        DocState.isReviewed = true;
        renderDocumentDetail(DocState.currentDoc);
        loadDocuments();  // リストを更新
    } catch (error) {
        App.toast(`エラー: ${error.message}`, 'error');
    }
}

async function markUnreviewed() {
    if (!DocState.currentDoc) return;

    try {
        await App.api(`/api/documents/${DocState.currentDoc.id}/unreview`, { method: 'POST' });
        App.toast('未完了に戻しました', 'success');
        DocState.isReviewed = false;
        renderDocumentDetail(DocState.currentDoc);
        loadDocuments();
    } catch (error) {
        App.toast(`エラー: ${error.message}`, 'error');
    }
}

async function rollbackDocument() {
    if (!DocState.currentDoc) return;

    App.showDeleteModal('前のバージョンに戻しますか？', async () => {
        try {
            await App.api(`/api/documents/${DocState.currentDoc.id}/rollback`, { method: 'POST' });
            App.toast('ロールバックしました', 'success');
            selectDocument(DocState.currentDoc.id);  // 再読み込み
        } catch (error) {
            App.toast(`エラー: ${error.message}`, 'error');
        }
    });
}

async function deleteDocument() {
    if (!DocState.currentDoc) return;

    App.showDeleteModal('このドキュメントを削除しますか？Google Driveからも削除されます。', async () => {
        try {
            await App.api(`/api/documents/${DocState.currentDoc.id}`, { method: 'DELETE' });
            App.toast('削除しました', 'success');
            DocState.currentDoc = null;
            document.getElementById('document-detail-container').style.display = 'none';
            App.setUrlParam('doc_id', null);
            loadDocuments();
        } catch (error) {
            App.toast(`エラー: ${error.message}`, 'error');
        }
    });
}

// =============================================================================
// 一括操作
// =============================================================================

async function bulkApprove() {
    const ids = Array.from(App.state.selectedIds);
    if (ids.length === 0) return;

    try {
        const result = await App.api('/api/documents/bulk-approve', {
            method: 'POST',
            body: JSON.stringify({ doc_ids: ids }),
        });

        App.toast(`${result.success_count}件を承認しました`, 'success');
        App.clearSelection();
        loadDocuments();

    } catch (error) {
        App.toast(`エラー: ${error.message}`, 'error');
    }
}

async function bulkDelete() {
    const ids = Array.from(App.state.selectedIds);
    if (ids.length === 0) return;

    App.showDeleteModal(`${ids.length}件のドキュメントを削除しますか？`, async () => {
        try {
            const result = await App.api('/api/documents/bulk-delete', {
                method: 'POST',
                body: JSON.stringify({ doc_ids: ids }),
            });

            App.toast(`${result.success_count}件を削除しました`, 'success');
            App.clearSelection();
            loadDocuments();

        } catch (error) {
            App.toast(`エラー: ${error.message}`, 'error');
        }
    });
}

// =============================================================================
// 統計
// =============================================================================

async function loadStats() {
    try {
        const data = await App.api('/api/stats');

        document.getElementById('stats-container').innerHTML = `
            <div class="stats-grid">
                <div class="stat-item">
                    <div class="stat-value">${data.unreviewed}</div>
                    <div class="stat-label">未レビュー</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value">${data.reviewed}</div>
                    <div class="stat-label">完了</div>
                </div>
            </div>
            <div class="progress-bar">
                <div class="progress-fill" style="width:${data.progress_percent}%"></div>
            </div>
            <div style="text-align:center;margin-top:8px;font-size:13px;color:#666;">
                進捗率: ${data.progress_percent}%
            </div>
        `;
    } catch (error) {
        console.error('Failed to load stats:', error);
    }
}

// =============================================================================
// ワークスペース
// =============================================================================

async function loadWorkspaces() {
    try {
        const data = await App.api('/api/workspaces');
        const select = document.getElementById('workspace-filter');

        if (select && data.workspaces) {
            data.workspaces.forEach(ws => {
                const option = document.createElement('option');
                option.value = ws;
                option.textContent = ws;
                select.appendChild(option);
            });
        }
    } catch (error) {
        console.error('Failed to load workspaces:', error);
    }
}

// =============================================================================
// 初期化
// =============================================================================

document.addEventListener('DOMContentLoaded', async () => {
    // 認証されていない場合は待機
    if (!App.state.isAuthenticated) {
        // checkSessionが完了するまで少し待つ
        await new Promise(resolve => setTimeout(resolve, 500));
        if (!App.state.isAuthenticated) return;
    }

    // パネルトグル設定
    setupPanelToggle();

    // タブイベントを設定
    setupTabEvents();

    // データ読み込み
    await loadWorkspaces();
    await loadStats();
    await loadDocuments();

    // イベントリスナー
    document.getElementById('filter-btn')?.addEventListener('click', loadDocuments);
    document.getElementById('refresh-btn')?.addEventListener('click', () => {
        loadStats();
        loadDocuments();
    });
    document.getElementById('search-input')?.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') loadDocuments();
    });

    document.getElementById('save-btn')?.addEventListener('click', saveDocument);
    document.getElementById('review-btn')?.addEventListener('click', markReviewed);
    document.getElementById('unreview-btn')?.addEventListener('click', markUnreviewed);
    document.getElementById('delete-btn')?.addEventListener('click', deleteDocument);
});

// パネルトグル設定
function setupPanelToggle() {
    const toggle = document.getElementById('panel-toggle');
    const sidePanel = document.getElementById('side-panel');

    if (toggle && sidePanel) {
        toggle.addEventListener('click', () => {
            sidePanel.classList.toggle('collapsed');
        });
    }
}

// グローバルに公開
window.selectDocument = selectDocument;
