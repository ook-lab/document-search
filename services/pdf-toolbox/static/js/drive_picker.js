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
            .drive-picker-icon { width: 26px; text-align: center; }
            .drive-picker-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
            .drive-picker-meta { font-size: 0.75rem; color: #6b7280; }
            .drive-picker-empty { color: #6b7280; text-align: center; padding: 42px 12px; }
        `;
        document.head.appendChild(style);
    }

    async function listFolder(folderId) {
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
        const state = { folderId: 'root', stack: [] };
        const backdrop = document.createElement('div');
        backdrop.className = 'drive-picker-backdrop';
        backdrop.innerHTML = `
            <div class="drive-picker-modal">
                <div class="drive-picker-header">
                    <span>Google DriveからPDFを選択</span>
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

        async function render(folderId) {
            body.innerHTML = '<div class="drive-picker-empty">読み込み中...</div>';
            upButton.disabled = state.stack.length === 0;
            try {
                const items = await listFolder(folderId);
                if (items.length === 0) {
                    body.innerHTML = '<div class="drive-picker-empty">PDFまたはフォルダがありません。</div>';
                    return;
                }
                body.innerHTML = '';
                for (const item of items) {
                    const isFolder = item.mimeType === FOLDER_MIME;
                    const row = document.createElement('button');
                    row.type = 'button';
                    row.className = 'drive-picker-row';
                    row.innerHTML = `
                        <span class="drive-picker-icon">${isFolder ? '📁' : '📄'}</span>
                        <span class="drive-picker-name">${escapeHtml(item.name || '')}</span>
                        <span class="drive-picker-meta">${isFolder ? 'フォルダ' : 'PDF'}</span>
                    `;
                    row.addEventListener('click', () => {
                        if (isFolder) {
                            state.stack.push(state.folderId);
                            state.folderId = item.id;
                            render(state.folderId);
                        } else if (item.mimeType === PDF_MIME) {
                            onSelect(item);
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
            state.folderId = state.stack.pop();
            render(state.folderId);
        });
        backdrop.addEventListener('click', event => {
            if (event.target === backdrop) backdrop.remove();
        });
        render(state.folderId);
    }

    function escapeHtml(text) {
        return String(text).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[char]));
    }

    window.openDrivePicker = openDrivePicker;
})();
