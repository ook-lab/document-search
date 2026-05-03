(function () {
    const FOLDER_MIME = 'application/vnd.google-apps.folder';
    const PDF_MIME = 'application/pdf';

    function ensureStyle() {
        if (document.getElementById('drive-picker-style')) return;
        const style = document.createElement('style');
        style.id = 'drive-picker-style';
        style.textContent = `
            .drive-picker-backdrop {
                position: fixed; inset: 0; z-index: 9999; background: rgba(15, 23, 42, 0.45);
                display: flex; align-items: center; justify-content: center; font-family: inherit;
            }
            .drive-picker-modal {
                width: min(760px, 92vw); max-height: 82vh; background: #fff; border-radius: 14px;
                box-shadow: 0 20px 40px rgba(0,0,0,0.22); display: flex; flex-direction: column; overflow: hidden;
            }
            .drive-picker-header {
                display: flex; justify-content: space-between; align-items: center; padding: 16px 18px;
                border-bottom: 1px solid #e5e7eb; font-weight: 800; color: #111827;
            }
            .drive-picker-title { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
            .drive-picker-title-main { font-weight: 800; }
            .drive-picker-title-sub { font-size: 0.78rem; color: #6b7280; font-weight: 500; }
            .drive-picker-actions { display: flex; gap: 8px; align-items: center; }
            .drive-picker-button {
                border: 1px solid #d1d5db; background: #fff; border-radius: 8px; padding: 7px 10px;
                cursor: pointer; font-weight: 700; color: #374151;
            }
            .drive-picker-body { overflow-y: auto; padding: 10px; min-height: 320px; }
            .drive-picker-row {
                width: 100%; border: 0; background: #fff; border-radius: 8px; padding: 11px 12px;
                display: flex; gap: 10px; align-items: center; text-align: left; cursor: pointer; color: #111827;
            }
            .drive-picker-row:hover { background: #f3f4f6; }
            .drive-picker-row.unsupported { color: #9ca3af; cursor: not-allowed; }
            .drive-picker-row.unsupported:hover { background: #fff; }
            .drive-picker-icon { width: 26px; text-align: center; }
            .drive-picker-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
            .drive-picker-meta { font-size: 0.75rem; color: #6b7280; }
            .drive-picker-subtitle { font-size: 0.78rem; color: #6b7280; font-weight: 500; margin-top: 3px; }
            .drive-picker-empty { color: #6b7280; text-align: center; padding: 42px 12px; }
        `;
        document.head.appendChild(style);
    }

    async function fetchJson(url, payload) {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {}),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Drive一覧の取得に失敗しました。');
        return data;
    }

    async function listRoots() {
        return fetchJson('/drive/roots', {});
    }

    async function listFolder(state) {
        const data = await fetchJson('/drive/list', {
            folder_id: state.folderId || 'root',
            source: state.source || 'my_drive',
            drive_id: state.driveId || '',
        });
        return data.items || [];
    }

    function buildRootChoices(roots) {
        const choices = [
            {
                id: roots.rootFolderId || 'root',
                name: 'マイドライブ',
                icon: '🏠',
                source: 'my_drive',
                folderId: roots.rootFolderId || 'root',
                driveId: '',
                title: 'マイドライブ',
                description: roots.user && roots.user.emailAddress ? roots.user.emailAddress : '',
            },
            {
                id: 'shared_with_me',
                name: '共有アイテム',
                icon: '🤝',
                source: 'shared_with_me',
                folderId: 'root',
                driveId: '',
                title: '共有アイテム',
                description: '共有されたPDF/フォルダ',
            },
        ];
        for (const drive of roots.sharedDrives || []) {
            choices.push({
                id: drive.id,
                name: drive.name,
                icon: '🏢',
                source: 'shared_drive',
                folderId: drive.id,
                driveId: drive.id,
                title: drive.name,
                description: '共有ドライブ',
            });
        }
        return choices;
    }

    function renderRootChoices(body, roots, onOpenRoot) {
        const choices = buildRootChoices(roots);
        body.innerHTML = '';
        for (const choice of choices) {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'drive-picker-row';
            row.innerHTML = `
                <span class="drive-picker-icon">${choice.icon}</span>
                <span class="drive-picker-name">
                    ${escapeHtml(choice.name || '')}
                    ${choice.description ? `<div class="drive-picker-subtitle">${escapeHtml(choice.description)}</div>` : ''}
                </span>
                <span class="drive-picker-meta">開く</span>
            `;
            row.addEventListener('click', () => onOpenRoot(choice));
            body.appendChild(row);
        }
    }

    async function legacyListFolder(folderId) {
        const response = await fetch('/drive/list', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder_id: folderId || 'root' }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Drive一覧の取得に失敗しました。');
        return data.items || [];
    }

    function openDrivePicker(onSelect) {
        ensureStyle();
        const state = { folderId: 'root', source: 'roots', driveId: '', title: 'Google Drive', stack: [] };
        const backdrop = document.createElement('div');
        backdrop.className = 'drive-picker-backdrop';
        backdrop.innerHTML = `
            <div class="drive-picker-modal">
                <div class="drive-picker-header">
                    <div class="drive-picker-title">
                        <span class="drive-picker-title-main">Google DriveからPDFを選択</span>
                        <span class="drive-picker-title-sub" data-role="location">読み込み中...</span>
                    </div>
                    <div class="drive-picker-actions">
                        <button type="button" class="drive-picker-button" data-action="up">上へ</button>
                        <button type="button" class="drive-picker-button" data-action="close">閉じる</button>
                    </div>
                </div>
                <div class="drive-picker-body"><div class="drive-picker-empty">読み込み中...</div></div>
            </div>
        `;
        document.body.appendChild(backdrop);
        const body = backdrop.querySelector('.drive-picker-body');
        const upButton = backdrop.querySelector('[data-action="up"]');
        const locationLabel = backdrop.querySelector('[data-role="location"]');

        function pushState() {
            state.stack.push({
                folderId: state.folderId,
                source: state.source,
                driveId: state.driveId,
                title: state.title,
            });
        }

        function restoreState(previous) {
            state.folderId = previous.folderId;
            state.source = previous.source;
            state.driveId = previous.driveId;
            state.title = previous.title;
        }

        async function render() {
            body.innerHTML = '<div class="drive-picker-empty">読み込み中...</div>';
            upButton.disabled = state.stack.length === 0;
            locationLabel.textContent = state.title || 'Google Drive';
            try {
                if (state.source === 'roots') {
                    const roots = await listRoots();
                    renderRootChoices(body, roots, choice => {
                        pushState();
                        state.source = choice.source;
                        state.folderId = choice.folderId;
                        state.driveId = choice.driveId;
                        state.title = choice.title || choice.name;
                        render();
                    });
                    return;
                }

                const items = await listFolder(state);
                if (items.length === 0) {
                    body.innerHTML = '<div class="drive-picker-empty">この場所にはファイルがありません。</div>';
                    return;
                }
                body.innerHTML = '';
                for (const item of items) {
                    const effectiveMimeType = item.effectiveMimeType || item.mimeType;
                    const effectiveId = item.effectiveId || item.id;
                    const isFolder = effectiveMimeType === FOLDER_MIME;
                    const isPdf = effectiveMimeType === PDF_MIME;
                    const row = document.createElement('button');
                    row.type = 'button';
                    row.className = `drive-picker-row ${(!isFolder && !isPdf) ? 'unsupported' : ''}`;
                    if (!isFolder && !isPdf) row.disabled = true;
                    row.innerHTML = `
                        <span class="drive-picker-icon">${isFolder ? '📁' : (isPdf ? '📄' : '▫️')}</span>
                        <span class="drive-picker-name">
                            ${escapeHtml(item.name || '')}
                            ${item.isShortcut ? '<div class="drive-picker-subtitle">ショートカット</div>' : ''}
                        </span>
                        <span class="drive-picker-meta">${isFolder ? 'フォルダ' : (isPdf ? 'PDF' : '非対応')}</span>
                    `;
                    row.addEventListener('click', () => {
                        if (isFolder) {
                            pushState();
                            state.folderId = effectiveId;
                            state.title = item.name || state.title;
                            if (state.source === 'shared_with_me') {
                                state.source = 'all_drives';
                                state.driveId = item.driveId || '';
                            } else if (state.source === 'shared_drive') {
                                state.driveId = item.driveId || state.driveId;
                            }
                            render();
                        } else if (isPdf) {
                            onSelect({ ...item, id: effectiveId, mimeType: effectiveMimeType });
                            backdrop.remove();
                        }
                    });
                    body.appendChild(row);
                }
            } catch (error) {
                body.innerHTML = `<div class="drive-picker-empty">${escapeHtml(error.message)}</div>`;
            }
        }

        backdrop.querySelector('[data-action="close"]').addEventListener('click', () => backdrop.remove());
        upButton.addEventListener('click', () => {
            if (state.stack.length === 0) return;
            restoreState(state.stack.pop());
            render();
        });
        backdrop.addEventListener('click', event => {
            if (event.target === backdrop) backdrop.remove();
        });
        render();
    }

    function escapeHtml(text) {
        return String(text).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[char]));
    }

    window.openDrivePicker = openDrivePicker;
})();
