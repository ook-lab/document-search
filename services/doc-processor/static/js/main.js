/**
 * Document Review Service - Main JavaScript
 *
 * 設計方針:
 * - 選択中ID: URLクエリ(?doc_id=, ?email_id=)
 * - 一覧チェック状態: フロントJS
 * - 編集中フォーム値: フロントJS
 * - 削除確認: モーダルUI + CSRF
 */

// =============================================================================
// グローバル状態
// =============================================================================

const AppState = {
    csrfToken: null,
    isAuthenticated: false,
    userEmail: null,
    selectedIds: new Set(),  // チェックされたID
};

// =============================================================================
// API ユーティリティ
// =============================================================================

/**
 * API呼び出しラッパー
 * @param {string} endpoint - エンドポイント（/api/... の形式）
 * @param {object} options - fetch オプション
 * @returns {Promise<object>} レスポンスJSON
 */
async function apiCall(endpoint, options = {}) {
    const url = endpoint.startsWith('/') ? endpoint : `${window.API_BASE}/${endpoint}`;

    const defaultHeaders = {
        'Content-Type': 'application/json',
    };

    // CSRFトークンを追加（GET以外）
    if (options.method && options.method !== 'GET' && AppState.csrfToken) {
        defaultHeaders['X-CSRFToken'] = AppState.csrfToken;
    }

    const response = await fetch(url, {
        ...options,
        headers: {
            ...defaultHeaders,
            ...options.headers,
        },
        credentials: 'same-origin',
    });

    // 401の場合はログインモーダルを表示
    if (response.status === 401) {
        showLoginModal();
        throw new Error('Unauthorized');
    }

    const data = await response.json();

    if (!response.ok) {
        throw new Error(data.message || 'API Error');
    }

    return data;
}

// =============================================================================
// 認証
// =============================================================================

/**
 * セッション状態を確認
 */
async function checkSession() {
    try {
        const data = await apiCall('/api/auth/session');
        AppState.isAuthenticated = data.is_authenticated;
        AppState.userEmail = data.user_email;
        AppState.csrfToken = data.csrf_token;

        updateAuthUI();

        if (!AppState.isAuthenticated) {
            showLoginModal();
        }

        return data.is_authenticated;
    } catch (error) {
        console.error('Session check failed:', error);
        showLoginModal();
        return false;
    }
}

/**
 * ログイン
 */
async function login(email, password) {
    try {
        const data = await apiCall('/api/auth/login', {
            method: 'POST',
            body: JSON.stringify({ email, password }),
        });

        if (data.success) {
            AppState.isAuthenticated = true;
            AppState.userEmail = data.user_email;
            AppState.csrfToken = data.csrf_token;
            updateAuthUI();
            hideLoginModal();
            showToast('ログインしました', 'success');

            // ページをリロードして初期化
            window.location.reload();
        }
    } catch (error) {
        throw error;
    }
}

/**
 * ログアウト
 */
async function logout() {
    try {
        await apiCall('/api/auth/logout', { method: 'POST' });
        AppState.isAuthenticated = false;
        AppState.userEmail = null;
        showLoginModal();
        showToast('ログアウトしました', 'success');
    } catch (error) {
        console.error('Logout failed:', error);
    }
}

/**
 * 認証UIを更新
 */
function updateAuthUI() {
    const userEmailEl = document.getElementById('user-email');
    const logoutBtn = document.getElementById('logout-btn');

    if (userEmailEl) {
        userEmailEl.textContent = AppState.userEmail || '';
    }

    if (logoutBtn) {
        logoutBtn.style.display = AppState.isAuthenticated ? 'block' : 'none';
    }
}

// =============================================================================
// モーダル
// =============================================================================

function showLoginModal() {
    const modal = document.getElementById('login-modal');
    if (modal) {
        modal.style.display = 'flex';
    }
}

function hideLoginModal() {
    const modal = document.getElementById('login-modal');
    if (modal) {
        modal.style.display = 'none';
    }
}

let deleteCallback = null;

function showDeleteModal(message, callback) {
    const modal = document.getElementById('delete-modal');
    const messageEl = document.getElementById('delete-modal-message');

    if (modal && messageEl) {
        messageEl.textContent = message;
        deleteCallback = callback;
        modal.style.display = 'flex';
    }
}

function hideDeleteModal() {
    const modal = document.getElementById('delete-modal');
    if (modal) {
        modal.style.display = 'none';
        deleteCallback = null;
    }
}

// =============================================================================
// トースト通知
// =============================================================================

function showToast(message, type = 'info', duration = 3000) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span>${message}</span>
    `;

    container.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'slideIn 0.3s ease reverse';
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

// =============================================================================
// URL クエリ管理
// =============================================================================

function getUrlParam(name) {
    const params = new URLSearchParams(window.location.search);
    return params.get(name);
}

function setUrlParam(name, value) {
    const params = new URLSearchParams(window.location.search);
    if (value) {
        params.set(name, value);
    } else {
        params.delete(name);
    }
    const newUrl = `${window.location.pathname}?${params.toString()}`;
    window.history.pushState({}, '', newUrl);
}

// =============================================================================
// チェックボックス管理
// =============================================================================

function toggleSelectAll(checkbox, tableId) {
    const table = document.getElementById(tableId);
    if (!table) return;

    const checkboxes = table.querySelectorAll('tbody input[type="checkbox"]');
    checkboxes.forEach(cb => {
        cb.checked = checkbox.checked;
        const id = cb.dataset.id;
        if (checkbox.checked) {
            AppState.selectedIds.add(id);
        } else {
            AppState.selectedIds.delete(id);
        }
    });

    updateBulkActions();
}

function toggleSelect(checkbox) {
    const id = checkbox.dataset.id;
    if (checkbox.checked) {
        AppState.selectedIds.add(id);
    } else {
        AppState.selectedIds.delete(id);
    }
    updateBulkActions();
}

function updateBulkActions() {
    const count = AppState.selectedIds.size;
    const bulkApproveBtn = document.getElementById('bulk-approve-btn');
    const bulkDeleteBtn = document.getElementById('bulk-delete-btn');

    if (bulkApproveBtn) {
        bulkApproveBtn.disabled = count === 0;
        bulkApproveBtn.textContent = count > 0
            ? `✅ まとめて承認 (${count}件)`
            : '✅ まとめて承認';
    }

    if (bulkDeleteBtn) {
        bulkDeleteBtn.disabled = count === 0;
        bulkDeleteBtn.textContent = count > 0
            ? `🗑️ まとめて削除 (${count}件)`
            : '🗑️ まとめて削除';
    }
}

function clearSelection() {
    AppState.selectedIds.clear();

    // チェックボックスをリセット
    document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.checked = false;
    });

    updateBulkActions();
}

// =============================================================================
// タブ切り替え
// =============================================================================

function switchTab(tabId) {
    // すべてのタブを非アクティブに
    document.querySelectorAll('.tab').forEach(tab => {
        tab.classList.remove('active');
    });

    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.remove('active');
    });

    // 選択されたタブをアクティブに
    const selectedTab = document.querySelector(`.tab[data-tab="${tabId}"]`);
    const selectedContent = document.getElementById(tabId);

    if (selectedTab) selectedTab.classList.add('active');
    if (selectedContent) selectedContent.classList.add('active');
}

// =============================================================================
// ローディング表示
// =============================================================================

function showLoading(containerId) {
    const container = document.getElementById(containerId);
    if (container) {
        container.innerHTML = `
            <div class="loading">
                <div class="spinner"></div>
                <span>読み込み中...</span>
            </div>
        `;
    }
}

function showEmpty(containerId, message = 'データがありません') {
    const container = document.getElementById(containerId);
    if (container) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">📭</div>
                <p>${message}</p>
            </div>
        `;
    }
}

// =============================================================================
// 初期化
// =============================================================================

document.addEventListener('DOMContentLoaded', async () => {
    // セッション確認
    await checkSession();

    // ログインフォーム
    const loginForm = document.getElementById('login-form');
    if (loginForm) {
        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const email = document.getElementById('login-email').value;
            const password = document.getElementById('login-password').value;
            const errorEl = document.getElementById('login-error');

            try {
                await login(email, password);
            } catch (error) {
                if (errorEl) {
                    errorEl.textContent = error.message;
                    errorEl.style.display = 'block';
                }
            }
        });
    }

    // ログアウトボタン
    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', logout);
    }

    // 削除モーダル
    const deleteConfirmBtn = document.getElementById('delete-confirm-btn');
    const deleteCancelBtn = document.getElementById('delete-cancel-btn');

    if (deleteConfirmBtn) {
        deleteConfirmBtn.addEventListener('click', () => {
            if (deleteCallback) {
                deleteCallback();
            }
            hideDeleteModal();
        });
    }

    if (deleteCancelBtn) {
        deleteCancelBtn.addEventListener('click', hideDeleteModal);
    }

    // タブクリックイベント
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const tabId = tab.dataset.tab;
            if (tabId) {
                switchTab(tabId);
            }
        });
    });
});

// =============================================================================
// エクスポート（他のJSから使用）
// =============================================================================

window.App = {
    state: AppState,
    api: apiCall,
    toast: showToast,
    showDeleteModal,
    hideDeleteModal,
    getUrlParam,
    setUrlParam,
    toggleSelectAll,
    toggleSelect,
    clearSelection,
    showLoading,
    showEmpty,
    switchTab,
};
