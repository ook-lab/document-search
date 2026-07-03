import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

window.onerror = function (message, source, lineno, colno, error) {
  alert(`JavaScript Error:\n${message}\n\nSource: ${source}\nLine: ${lineno}, Col: ${colno}\nError: ${error}`);
  return false;
};



interface SyncFolder {
  folder_id: number;
  local_path: string;
  virtual_name: string;
}

interface Config {
  nas_url: string;
  api_token: string;
  device_id: number;
  sync_folders: SyncFolder[];
  exclude_patterns: string[];
}

interface SyncStatus {
  is_syncing: boolean;
  current_file: string;
  progress: number; // 0.0 to 1.0
  message: string;
}

interface DeviceInfo {
  device_id: number;
  device_name: string;
  os_type: string;
}

interface WebFolderInfo {
  folder_id: number;
  device_id: number;
  device_name: string;
  virtual_name: string;
  local_path: string;
}

interface LocalFileInfo {
  name: string;
  virtual_name: string;
  date: string;
}

// DOM View Containers
let registrationContainerEl: HTMLElement | null = null;
let mainDashboardEl: HTMLElement | null = null;

// SPA Views
let viewDevicesEl: HTMLElement | null = null;
let viewSearchEl: HTMLElement | null = null;
let viewFoldersEl: HTMLElement | null = null;
let viewPublicLinksEl: HTMLElement | null = null;
let viewSharedByMeEl: HTMLElement | null = null;
let viewFileTransfersEl: HTMLElement | null = null;
let viewDeletedItemsEl: HTMLElement | null = null;

// SPA Tabs
let tabSearchEl: HTMLElement | null = null;
let tabFoldersEl: HTMLElement | null = null;
let tabDevicesEl: HTMLElement | null = null;
let tabPublicLinksEl: HTMLElement | null = null;
let tabSharedByMeEl: HTMLElement | null = null;
let tabFileTransfersEl: HTMLElement | null = null;
let tabDeletedItemsEl: HTMLElement | null = null;

// DOM Elements
let statusMessageEl: HTMLElement | null = null;
let progressContainerEl: HTMLElement | null = null;
let progressPercentEl: HTMLElement | null = null;
let progressBarEl: HTMLElement | null = null;
let currentFileEl: HTMLElement | null = null;

let deviceNameInputEl: HTMLInputElement | null = null;
let saveSettingsBtnEl: HTMLButtonElement | null = null;
let infoDeviceIdEl: HTMLElement | null = null;
let infoAuthStatusEl: HTMLElement | null = null;

// Tree Exclusions Modal & Elements
let exclusionsModalEl: HTMLElement | null = null;
let openExclusionsModalBtnEl: HTMLButtonElement | null = null;
let exclusionsCloseBtnEl: HTMLButtonElement | null = null;
let treeFolderSelectEl: HTMLSelectElement | null = null;
let treeContainerEl: HTMLElement | null = null;
let saveTreeExclusionsBtnEl: HTMLButtonElement | null = null;

let addNewVirtualFolderBtnEl: HTMLButtonElement | null = null;
let matrixHeaderEl: HTMLElement | null = null;
let matrixBodyEl: HTMLElement | null = null;
let forceSyncBtnEl: HTMLButtonElement | null = null;

// Modal Elements (Folder Bind)
let addFolderModalEl: HTMLElement | null = null;
let modalVirtualNameEl: HTMLElement | null = null;
let modalLocalPathEl: HTMLInputElement | null = null;
let modalBrowseBtnEl: HTMLButtonElement | null = null;
let modalCancelBtnEl: HTMLButtonElement | null = null;
let modalConfirmBtnEl: HTMLButtonElement | null = null;

// Modal Elements (New Virtual Folder)
let newVirtualModalEl: HTMLElement | null = null;
let newVirtualNameEl: HTMLInputElement | null = null;
let newLocalPathEl: HTMLInputElement | null = null;
let newLocalBrowseBtnEl: HTMLButtonElement | null = null;
let newVirtualCancelBtnEl: HTMLButtonElement | null = null;
let newVirtualConfirmBtnEl: HTMLButtonElement | null = null;

// Search & Folder Tab Inner Elements
let searchInputEl: HTMLInputElement | null = null;
let searchBtnEl: HTMLButtonElement | null = null;
let searchResultsBodyEl: HTMLElement | null = null;
let foldersListBodyEl: HTMLElement | null = null;
let transfersListBodyEl: HTMLElement | null = null;


// State Variables
let appConfig: Config | null = null;

// SPA Tab Switching Logic
function switchTab(activeTabId: string) {
  const views = [
    { el: viewDevicesEl, tab: tabDevicesEl },
    { el: viewSearchEl, tab: tabSearchEl },
    { el: viewFoldersEl, tab: tabFoldersEl },
    { el: viewPublicLinksEl, tab: tabPublicLinksEl },
    { el: viewSharedByMeEl, tab: tabSharedByMeEl },
    { el: viewFileTransfersEl, tab: tabFileTransfersEl },
    { el: viewDeletedItemsEl, tab: tabDeletedItemsEl }
  ];

  views.forEach(v => {
    if (v.el) v.el.style.display = "none";
    if (v.tab) {
      v.tab.style.backgroundColor = "transparent";
      v.tab.style.color = "#9CA3AF";
    }
  });

  const active = views.find(v => v.tab?.id === activeTabId);
  if (active) {
    if (active.el) active.el.style.display = "flex";
    if (active.tab) {
      active.tab.style.backgroundColor = "#E05236";
      active.tab.style.color = "white";
    }
  }

  // Trigger tab-specific loaders
  if (activeTabId === "tab-folders") {
    renderFoldersTab();
  } else if (activeTabId === "tab-search") {
    clearSearch();
  }
}

// Render FOLDERS view (Sync folder list with Native Reveal link)
function renderFoldersTab() {
  if (!foldersListBodyEl || !appConfig) return;

  if (appConfig.sync_folders.length === 0) {
    foldersListBodyEl.innerHTML = `
      <tr>
        <td colspan="3" style="text-align: center; color: #9CA3AF; padding: 24px; font-size: 12px;">
          現在同期しているローカルフォルダはありません。<br/>
          「DEVICES」タブから「＋」ボタンをクリックして同期を追加してください。
        </td>
      </tr>
    `;
    return;
  }

  let html = "";
  appConfig.sync_folders.forEach(f => {
    html += `
      <tr style="border-bottom: 1px solid #E5E7EB; hover:background-color: #F9FAFB;">
        <td style="padding: 10px 12px; font-size: 12px; font-weight: 600; color: #374151;">
          <div style="display: flex; align-items: center; gap: 8px;">
            <svg width="14" height="14" fill="currentColor" class="text-gray-400" viewBox="0 0 20 20"><path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z"></path></svg>
            <span>${escapeHtml(f.virtual_name)}</span>
          </div>
        </td>
        <td style="padding: 10px 12px; font-size: 11px; color: #6B7280; font-family: monospace;">${escapeHtml(f.local_path)}</td>
        <td style="padding: 10px 12px; text-align: center;">
          <button onclick="window.revealFolder('${escapeHtml(f.local_path.replace(/\\/g, "\\\\"))}')"
            style="background-color: white; border: 1px solid #C4CBD0; color: #374151; font-size: 11px; font-weight: bold; padding: 4px 10px; border-radius: 4px; cursor: pointer;">
            📂 開く
          </button>
        </td>
      </tr>
    `;
  });
  foldersListBodyEl.innerHTML = html;
}

// Global reveal wrapper
(window as any).revealFolder = async (path: string) => {
  try {
    await invoke("open_in_explorer", { path });
  } catch (e) {
    alert(`エクスプローラー起動失敗: ${e}`);
  }
};

function clearSearch() {
  if (searchInputEl) searchInputEl.value = "";
  if (searchResultsBodyEl) {
    searchResultsBodyEl.innerHTML = `
      <tr>
        <td colspan="3" style="text-align: center; color: #9CA3AF; padding: 32px; font-size: 12px;">ファイル検索キーを入力して検索してください。</td>
      </tr>
    `;
  }
}

async function executeSearch() {
  if (!searchInputEl || !searchResultsBodyEl) return;
  const query = searchInputEl.value.trim().toLowerCase();

  if (!query) {
    clearSearch();
    return;
  }

  searchResultsBodyEl.innerHTML = `
    <tr>
      <td colspan="3" style="text-align: center; color: #9CA3AF; padding: 32px; font-size: 12px;">
        同期中の実ファイルをスキャンしています...
      </td>
    </tr>
  `;

  try {
    const files = await invoke<LocalFileInfo[]>("get_all_synced_files");
    const results = files.filter(f => f.name.toLowerCase().includes(query));

    if (results.length === 0) {
      searchResultsBodyEl.innerHTML = `
        <tr>
          <td colspan="3" style="text-align: center; color: #E05236; padding: 32px; font-size: 12px; font-weight: bold;">
            「${escapeHtml(query)}」に一致するファイルが同期対象フォルダ内に見つかりません。
          </td>
        </tr>
      `;
      return;
    }

    let html = "";
    results.forEach(f => {
      html += `
        <tr style="border-bottom: 1px solid #E5E7EB; hover:background-color: #F9FAFB;">
          <td style="padding: 10px 12px; font-size: 12px; color: #374151;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <svg width="14" height="14" fill="none" stroke="currentColor" class="text-gray-400" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
              <span>${escapeHtml(f.name)}</span>
            </div>
          </td>
          <td style="padding: 10px 12px; font-size: 11px; color: #6B7280;">${escapeHtml(f.virtual_name)}</td>
          <td style="padding: 10px 12px; font-size: 11px; color: #9CA3AF; font-family: monospace;">${escapeHtml(f.date)}</td>
        </tr>
      `;
    });
    searchResultsBodyEl.innerHTML = html;
  } catch (err) {
    console.error(err);
    searchResultsBodyEl.innerHTML = `
      <tr>
        <td colspan="3" style="text-align: center; color: #E05236; padding: 32px; font-size: 12px;">
          ファイル一覧のスキャンに失敗しました: ${escapeHtml(String(err))}
        </td>
      </tr>
    `;
  }
}

// Render Matrix UI (SugarSync device.jpg design)
async function renderMatrix() {
  if (!matrixHeaderEl || !matrixBodyEl || !appConfig) return;

  if (appConfig.device_id === 0) {
    matrixBodyEl.innerHTML = `
      <tr>
        <td colspan="100" class="text-center text-gray-400 py-8">
          デバイスを登録して接続するとマトリクスが表示されます。
        </td>
      </tr>
    `;
    return;
  }

  try {
    const devices = await invoke<DeviceInfo[]>("get_nas_devices");
    const allFolders = await invoke<WebFolderInfo[]>("get_nas_folders");

    // 1. Render Header (Devices)
    let headerHtml = `<th class="py-3 px-3 matrix-th text-xs w-1/3">Folders in SugarSync</th>`;
    devices.forEach(d => {
      const isSelf = d.device_id === appConfig?.device_id;
      headerHtml += `
        <th class="py-3 px-3 matrix-th text-xs text-center ${isSelf ? 'self-column-highlight' : ''}">
          <div class="font-bold text-gray-700">${escapeHtml(d.device_name)}</div>
          ${isSelf ? '<div class="text-[9px] text-gray-500 font-semibold mt-0.5">This computer</div>' : `<div class="text-[9px] text-gray-400 font-normal mt-0.5">${escapeHtml(d.os_type)}</div>`}
        </th>
      `;
    });
    matrixHeaderEl.innerHTML = headerHtml;

    // 2. Extract Unique Virtual Sync Folders (desktop, my document, download are default)
    const defaultFolders = ["Desktop", "Documents", "Downloads"];
    const uniqueVirtualNames = Array.from(new Set([
      ...defaultFolders,
      ...allFolders.map(f => f.virtual_name)
    ]));

    // 3. Render Matrix Grid Rows
    let bodyHtml = "";
    uniqueVirtualNames.forEach(vName => {
      // Determine folder icon type (shared or private)
      const isSharedFolder = vName.toLowerCase().includes("shared") || vName.toLowerCase().includes("co");
      const folderIcon = isSharedFolder 
        ? `<svg class="w-4 h-4 text-gray-500 shrink-0" width="16" height="16" fill="currentColor" viewBox="0 0 20 20"><path d="M9 2a1 1 0 000 2h2a1 1 0 100-2H9z"></path><path fill-rule="evenodd" d="M4 5a2 2 0 012-2 3 3 0 003 3h2a3 3 0 003-3 2 2 0 012 2v11a2 2 0 01-2 2H6a2 2 0 01-2-2V5zm3 4a1 1 0 000 2h.01a1 1 0 100-2H7zm3 0a1 1 0 000 2h3a1 1 0 100-2h-3zm-3 4a1 1 0 000 2h.01a1 1 0 100-2H7zm3 0a1 1 0 000 2h3a1 1 0 100-2h-3z" clip-rule="evenodd"></path></svg>`
        : `<svg class="w-4 h-4 text-gray-400 shrink-0" width="16" height="16" fill="currentColor" viewBox="0 0 20 20"><path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z"></path></svg>`;

      bodyHtml += `<tr class="border-b border-[#BDCCD6] hover:bg-gray-50/50 transition-colors">`;
      bodyHtml += `<td class="py-3 px-4 font-semibold text-gray-700 text-xs flex items-center gap-2 select-none">
        ${folderIcon}
        <div class="truncate py-0.5">${escapeHtml(vName)}</div>
      </td>`;

      devices.forEach(d => {
        const isSelf = d.device_id === appConfig?.device_id;
        const mapping = allFolders.find(f => f.virtual_name === vName && f.device_id === d.device_id);

        bodyHtml += `<td class="py-2 px-3 text-center matrix-td ${isSelf ? 'self-column-highlight' : ''}">`;
        
        if (mapping) {
          if (isSelf) {
            // Synced (Self) -> Bordered dropdown button [ 📁 ▽ ]
            bodyHtml += `
              <div class="flex items-center justify-center">
                <div class="flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold rounded sync-btn-self transition-all select-none">
                  <svg class="w-3.5 h-3.5 text-gray-500" width="14" height="14" fill="currentColor" viewBox="0 0 20 20"><path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z"></path></svg>
                  <span class="text-[8px] text-gray-400 font-bold">▼</span>
                </div>
              </div>
            `;
          } else {
            // Synced (Other Computer) -> Light green flat folder container [ 📁 ]
            bodyHtml += `
              <div class="flex items-center justify-center">
                <div class="bg-[#F0F8EC] border border-[#D0EBC4] text-green-700 p-1.5 rounded shadow-sm select-none" title="${escapeHtml(d.device_name)}で同期中">
                  <svg class="w-3.5 h-3.5" width="14" height="14" fill="currentColor" viewBox="0 0 20 20"><path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z"></path></svg>
                </div>
              </div>
            `;
          }
        } else if (isSelf) {
          // Unsynced (Self) -> Green "＋" add button
          bodyHtml += `
            <button onclick="window.openAddFolderModal('${escapeHtml(vName)}')"
              class="add-btn-green transition-all" title="このPCで同期を追加">
              ＋
            </button>
          `;
        } else {
          // Unsynced (Other Computer) -> Blank Cell
          bodyHtml += ``;
        }
        
        bodyHtml += `</td>`;
      });

      bodyHtml += `</tr>`;
    });

    matrixBodyEl.innerHTML = bodyHtml;

  } catch (e) {
    console.error("Failed to render matrix:", e);
    matrixBodyEl.innerHTML = `
      <tr>
        <td colspan="100" class="text-center text-red-500 py-6">
          マトリクスの読み込みに失敗しました: <br/>${escapeHtml(String(e))}
        </td>
      </tr>
    `;
  }
}

// Render Folder sub-directories tree in Exclusions Pane
async function loadFolderTree(localPath: string) {
  if (!treeContainerEl || !appConfig) return;

  treeContainerEl.innerHTML = `
    <div class="text-center text-xs text-gray-400 py-6 animate-pulse">
      サブフォルダをスキャン中...
    </div>
  `;

  try {
    const subdirs = await invoke<string[]>("get_local_subdirs", { localPath });

    if (subdirs.length === 0) {
      treeContainerEl.innerHTML = `
        <div class="text-center text-xs text-gray-400 py-6">
          このフォルダの中に同期除外可能なサブフォルダがありません。
        </div>
      `;
      if (saveTreeExclusionsBtnEl) saveTreeExclusionsBtnEl.disabled = true;
      return;
    }

    let html = "";
    subdirs.forEach(path => {
      // Calculate depth by counting slashes to render indents
      const depth = path.split("/").length - 1;
      const indent = depth * 16; // 16px per depth level
      const folderName = path.split("/").pop() || path;

      // Checkbox is checked (meaning it's active for sync) if it is NOT in exclude_patterns
      const isExcluded = appConfig?.exclude_patterns.includes(path);
      const isChecked = !isExcluded;

      html += `
        <div class="flex items-center gap-2 py-1" style="margin-left: ${indent}px;">
          <input type="checkbox" id="chk-dir-${escapeHtml(path)}" data-dir="${escapeHtml(path)}" 
            ${isChecked ? 'checked' : ''}
            class="w-3.5 h-3.5 text-[#2563EB] border-gray-300 rounded focus:ring-blue-500" />
          <svg class="w-3.5 h-3.5 text-yellow-500 shrink-0" width="14" height="14" fill="currentColor" viewBox="0 0 20 20">
            <path fill-rule="evenodd" d="M2 6a2 2 0 012-2h4l2 2h4a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" clip-rule="evenodd"></path>
          </svg>
          <span class="text-xs text-gray-700 select-none">${escapeHtml(folderName)}</span>
        </div>
      `;
    });

    treeContainerEl.innerHTML = html;
    if (saveTreeExclusionsBtnEl) saveTreeExclusionsBtnEl.disabled = false;

  } catch (err) {
    console.error(err);
    treeContainerEl.innerHTML = `
      <div class="text-center text-xs text-red-500 py-6">
        フォルダのスキャンに失敗しました: ${escapeHtml(String(err))}
      </div>
    `;
    if (saveTreeExclusionsBtnEl) saveTreeExclusionsBtnEl.disabled = true;
  }
}

// Update Sync Status UI
interface TransferTask {
  fileName: string;
  relative_path: string;
  status: string;
  progress: number;
}

let activeTransfers: TransferTask[] = [];

function updateSyncUI(status: SyncStatus) {
  if (statusMessageEl) statusMessageEl.textContent = status.message;

  if (progressContainerEl && progressPercentEl && progressBarEl && currentFileEl) {
    if (status.is_syncing) {
      progressContainerEl.style.display = "block";
      const pct = Math.round(status.progress * 100);
      progressPercentEl.textContent = `${pct}%`;
      progressBarEl.style.width = `${pct}%`;
      currentFileEl.textContent = status.current_file ? `処理中: ${status.current_file}` : "同期の準備中...";
    } else {
      if (status.progress === 1.0) {
        progressPercentEl.textContent = "100%";
        progressBarEl.style.width = "100%";
        currentFileEl.textContent = "同期完了";

        // Set all items in the current batch to completed
        activeTransfers.forEach(t => {
          t.status = "完了";
          t.progress = 100;
        });
        renderTransfersTable();

        setTimeout(() => {
          if (progressContainerEl) progressContainerEl.style.display = "none";
        }, 3000);
      } else {
        progressContainerEl.style.display = "none";
      }
    }
  }
}

function renderTransfersTable() {
  if (!transfersListBodyEl) return;
  if (activeTransfers.length === 0) {
    transfersListBodyEl.innerHTML = `
      <tr>
        <td colspan="3" style="padding: 24px; text-align: center; color: #9CA3AF; font-style: italic;">
          現在アクティブなファイル転送はありません。
        </td>
      </tr>
    `;
    return;
  }

  // Draw in the natural queue order (so we see tasks pending and being consumed sequentially)
  const html = activeTransfers.map(t => {
    let statusColor = "#9CA3AF"; // default grey for Pending
    if (t.status === "完了") {
      statusColor = "#10B981"; // green
    } else if (t.status.includes("中")) {
      statusColor = "#3B82F6"; // blue (processing)
    } else if (t.status === "失敗") {
      statusColor = "#EF4444"; // red
    } else if (t.status.includes("アップロード")) {
      statusColor = "#F59E0B"; // amber (upload pending)
    } else if (t.status.includes("ダウンロード")) {
      statusColor = "#8B5CF6"; // purple (download pending)
    } else if (t.status.includes("移動")) {
      statusColor = "#EC4899"; // pink (move pending/processing)
    }

    const pbWidth = t.progress;

    return `
      <tr style="border-bottom: 1px solid #F3F4F6;">
        <td style="padding: 12px 14px; font-weight: 500; color: #1F2937; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(t.relative_path)}">
          ${escapeHtml(t.fileName)}
          <div style="font-size: 9px; color: #9CA3AF; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px;">
            ${escapeHtml(t.relative_path)}
          </div>
        </td>
        <td style="padding: 12px 14px; vertical-align: middle;">
          <span style="color: ${statusColor}; font-weight: bold;">${escapeHtml(t.status)}</span>
        </td>
        <td style="padding: 12px 14px; width: 180px; vertical-align: middle;">
          <div style="display: flex; align-items: center; gap: 8px;">
            <div style="flex: 1; background-color: #E5E7EB; height: 6px; border-radius: 3px; overflow: hidden;">
              <div style="background-color: ${statusColor}; width: ${pbWidth}%; height: 100%; transition: width 0.2s ease;"></div>
            </div>
            <span style="color: #6B7280; font-size: 10px; width: 30px; text-align: right;">${pbWidth}%</span>
          </div>
        </td>
      </tr>
    `;
  }).join("");

  transfersListBodyEl.innerHTML = html;
}



// Config Load Callback
function handleConfigLoaded(config: Config) {
  appConfig = config;

  if (deviceNameInputEl) deviceNameInputEl.value = config.device_id !== 0 ? `RegisteredDevice` : "";

  // Render Sync Folders inside select dropdown
  if (treeFolderSelectEl) {
    const currentSelected = treeFolderSelectEl.value;
    treeFolderSelectEl.innerHTML = `
      <option value="">-- 同期フォルダを選択してください --</option>
      ${config.sync_folders.map(f => `<option value="${escapeHtml(f.local_path)}">${escapeHtml(f.virtual_name)} (${escapeHtml(f.local_path)})</option>`).join("")}
    `;
    treeFolderSelectEl.value = currentSelected;
  }

  // Toggle view elements by direct display manipulation
  if (config.device_id !== 0) {
    if (registrationContainerEl) registrationContainerEl.style.display = "none";
    if (mainDashboardEl) mainDashboardEl.style.display = "flex"; // flex to layout sidebar+content properly

    document.body.classList.remove("justify-center");

    if (infoDeviceIdEl) infoDeviceIdEl.textContent = config.device_id.toString();
    if (infoAuthStatusEl) {
      infoAuthStatusEl.textContent = "接続・認証完了";
      infoAuthStatusEl.className = "font-semibold text-green-600";
    }

    // Enable grid actions
    if (addNewVirtualFolderBtnEl) addNewVirtualFolderBtnEl.disabled = false;
    if (forceSyncBtnEl) forceSyncBtnEl.disabled = false;
  } else {
    if (registrationContainerEl) registrationContainerEl.style.display = "block";
    if (mainDashboardEl) mainDashboardEl.style.display = "none";

    document.body.classList.add("justify-center");
  }

  renderMatrix();
}

// Global scope bindings for dynamically rendered HTML click events
(window as any).openAddFolderModal = (virtualName: string) => {
  if (modalVirtualNameEl && modalLocalPathEl && addFolderModalEl) {
    modalVirtualNameEl.textContent = virtualName;
    modalLocalPathEl.value = "";
    addFolderModalEl.style.display = "flex";
  }
};

// Escape HTML Helper
function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Main DOM Entrypoint
window.addEventListener("DOMContentLoaded", async () => {
  // View containers
  registrationContainerEl = document.querySelector("#registration-container");
  mainDashboardEl = document.querySelector("#main-dashboard");

  // SPA View Elements
  viewDevicesEl = document.querySelector("#view-devices");
  viewSearchEl = document.querySelector("#view-search");
  viewFoldersEl = document.querySelector("#view-folders");
  viewPublicLinksEl = document.querySelector("#view-public-links");
  viewSharedByMeEl = document.querySelector("#view-shared-by-me");
  viewFileTransfersEl = document.querySelector("#view-file-transfers");
  viewDeletedItemsEl = document.querySelector("#view-deleted-items");

  // SPA Tabs
  tabSearchEl = document.querySelector("#tab-search");
  tabFoldersEl = document.querySelector("#tab-folders");
  tabDevicesEl = document.querySelector("#tab-devices");
  tabPublicLinksEl = document.querySelector("#tab-public-links");
  tabSharedByMeEl = document.querySelector("#tab-shared-by-me");
  tabFileTransfersEl = document.querySelector("#tab-file-transfers");
  tabDeletedItemsEl = document.querySelector("#tab-deleted-items");

  // Status & Progress elements
  statusMessageEl = document.querySelector("#status-message");
  progressContainerEl = document.querySelector("#progress-container");
  progressPercentEl = document.querySelector("#progress-percent");
  progressBarEl = document.querySelector("#progress-bar");
  currentFileEl = document.querySelector("#current-file");

  // Settings
  deviceNameInputEl = document.querySelector("#device-name");
  saveSettingsBtnEl = document.querySelector("#save-settings-btn");
  infoDeviceIdEl = document.querySelector("#info-device-id");
  infoAuthStatusEl = document.querySelector("#info-auth-status");

  // Exclusions Modal Elements
  exclusionsModalEl = document.querySelector("#exclusions-modal");
  openExclusionsModalBtnEl = document.querySelector("#open-exclusions-modal-btn");
  exclusionsCloseBtnEl = document.querySelector("#exclusions-close-btn");
  treeFolderSelectEl = document.querySelector("#tree-folder-select");
  treeContainerEl = document.querySelector("#tree-container");
  saveTreeExclusionsBtnEl = document.querySelector("#save-tree-exclusions-btn");

  // Actions & Matrix
  addNewVirtualFolderBtnEl = document.querySelector("#add-new-virtual-folder-btn");
  matrixHeaderEl = document.querySelector("#matrix-header");
  matrixBodyEl = document.querySelector("#matrix-body");
  forceSyncBtnEl = document.querySelector("#force-sync-btn");

  // Modal (Bind Folder)
  addFolderModalEl = document.querySelector("#add-folder-modal");
  modalVirtualNameEl = document.querySelector("#modal-virtual-name");
  modalLocalPathEl = document.querySelector("#modal-local-path");
  modalBrowseBtnEl = document.querySelector("#modal-browse-btn"); // Browse button
  modalCancelBtnEl = document.querySelector("#modal-cancel-btn");
  modalConfirmBtnEl = document.querySelector("#modal-confirm-btn");

  // Modal (New Virtual Folder)
  newVirtualModalEl = document.querySelector("#new-virtual-modal");
  newVirtualNameEl = document.querySelector("#new-virtual-name");
  newLocalPathEl = document.querySelector("#new-local-path");
  newLocalBrowseBtnEl = document.querySelector("#new-local-browse-btn"); // Browse button
  newVirtualCancelBtnEl = document.querySelector("#new-virtual-cancel-btn");
  newVirtualConfirmBtnEl = document.querySelector("#new-virtual-confirm-btn");

  // Inner elements for tabs
  searchInputEl = document.querySelector("#search-input");
  searchBtnEl = document.querySelector("#search-btn");
  searchResultsBodyEl = document.querySelector("#search-results-body");
  foldersListBodyEl = document.querySelector("#folders-list-body");
  transfersListBodyEl = document.querySelector("#transfers-list-body");


  // 1. Tab Event Listeners
  tabSearchEl?.addEventListener("click", (e) => { e.preventDefault(); switchTab("tab-search"); });
  tabFoldersEl?.addEventListener("click", (e) => { e.preventDefault(); switchTab("tab-folders"); });
  tabDevicesEl?.addEventListener("click", (e) => { e.preventDefault(); switchTab("tab-devices"); });
  tabPublicLinksEl?.addEventListener("click", (e) => { e.preventDefault(); switchTab("tab-public-links"); });
  tabSharedByMeEl?.addEventListener("click", (e) => { e.preventDefault(); switchTab("tab-shared-by-me"); });
  tabFileTransfersEl?.addEventListener("click", (e) => { e.preventDefault(); switchTab("tab-file-transfers"); });
  tabDeletedItemsEl?.addEventListener("click", (e) => { e.preventDefault(); switchTab("tab-deleted-items"); });

  // 2. Inner Search Listeners
  searchBtnEl?.addEventListener("click", executeSearch);
  searchInputEl?.addEventListener("keyup", (e) => {
    if (e.key === "Enter") executeSearch();
  });

  // 3. Initial State Fetch
  try {
    const config = await invoke<Config>("get_config");
    handleConfigLoaded(config);

    const initialStatus = await invoke<SyncStatus>("get_status");
    updateSyncUI(initialStatus);
  } catch (e) {
    console.error("Failed to load initial configuration:", e);
  }

  // 4. Save Device Settings (Enforces static IP automatically)
  document.querySelector("#settings-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!deviceNameInputEl || !saveSettingsBtnEl) return;

    const deviceName = deviceNameInputEl.value.trim();
    if (!deviceName) return;

    saveSettingsBtnEl.disabled = true;
    saveSettingsBtnEl.textContent = "デバイス登録中...";

    try {
      const config = await invoke<Config>("save_settings", { nasUrl: "http://100.82.85.101:8080", deviceName });
      handleConfigLoaded(config);
      alert("接続およびデバイス登録に成功しました！");
    } catch (err: any) {
      alert(`登録に失敗しました: ${err}`);
      saveSettingsBtnEl.disabled = false;
      saveSettingsBtnEl.textContent = "このPCを登録する";
    }
  });

  // 5. Exclusions Modal Toggle Listeners
  openExclusionsModalBtnEl?.addEventListener("click", () => {
    if (exclusionsModalEl && treeFolderSelectEl && treeContainerEl) {
      treeFolderSelectEl.value = "";
      treeContainerEl.innerHTML = `
        <div class="text-center text-xs text-gray-400 py-6">
          同期フォルダを選択すると、サブフォルダ一覧が表示されます。
        </div>
      `;
      if (saveTreeExclusionsBtnEl) saveTreeExclusionsBtnEl.disabled = true;
      exclusionsModalEl.style.display = "flex";
    }
  });

  exclusionsCloseBtnEl?.addEventListener("click", () => {
    if (exclusionsModalEl) exclusionsModalEl.style.display = "none";
  });

  // 6. Tree Exclusions Dropdown Change Listener
  treeFolderSelectEl?.addEventListener("change", async () => {
    const localPath = treeFolderSelectEl?.value;
    if (!localPath) {
      if (treeContainerEl) {
        treeContainerEl.innerHTML = `
          <div class="text-center text-xs text-gray-400 py-4">
            同期フォルダを選択すると、サブフォルダ一覧が表示されます。
          </div>
        `;
      }
      if (saveTreeExclusionsBtnEl) saveTreeExclusionsBtnEl.disabled = true;
      return;
    }
    await loadFolderTree(localPath);
  });

  // 7. Save Exclusions from Tree checkboxes
  saveTreeExclusionsBtnEl?.addEventListener("click", async () => {
    if (!treeContainerEl || !saveTreeExclusionsBtnEl || !appConfig) return;

    const checkboxes = treeContainerEl.querySelectorAll('input[type="checkbox"]') as NodeListOf<HTMLInputElement>;
    
    const deselectedPaths: string[] = [];
    const allPathsInThisFolder: string[] = [];

    checkboxes.forEach(cb => {
      const dirPath = cb.getAttribute("data-dir") || "";
      allPathsInThisFolder.push(dirPath);
      if (!cb.checked) {
        deselectedPaths.push(dirPath); // Unchecked = Exclude
      }
    });

    // Retain exclusion settings for other folders, merge new states
    const otherFolderExclusions = appConfig.exclude_patterns.filter(p => !allPathsInThisFolder.includes(p));
    const newExclusions = [...otherFolderExclusions, ...deselectedPaths];

    saveTreeExclusionsBtnEl.disabled = true;
    saveTreeExclusionsBtnEl.textContent = "保存中...";

    try {
      const config = await invoke<Config>("update_exclude_patterns", { patterns: newExclusions });
      handleConfigLoaded(config);
      alert("除外フォルダの設定を保存しました！");
    } catch (err: any) {
      alert(`保存に失敗しました: ${err}`);
    } finally {
      saveTreeExclusionsBtnEl.disabled = false;
      saveTreeExclusionsBtnEl.textContent = "除外設定を保存";
    }
  });

  // 8. Modal (Bind Folder) Handlers
  modalBrowseBtnEl?.addEventListener("click", async () => {
    if (!modalLocalPathEl) return;
    try {
      const selected = await invoke<string | null>("select_folder");
      if (selected) {
        modalLocalPathEl.value = selected;
      }
    } catch (e) {
      console.error("Folder select dialog error:", e);
    }
  });

  modalCancelBtnEl?.addEventListener("click", () => {
    if (addFolderModalEl) addFolderModalEl.style.display = "none";
  });

  modalConfirmBtnEl?.addEventListener("click", async () => {
    if (!modalVirtualNameEl || !modalLocalPathEl || !addFolderModalEl) return;
    const virtualName = modalVirtualNameEl.textContent || "";
    const localPath = modalLocalPathEl.value.trim();

    if (!localPath) {
      alert("ローカルフォルダの絶対パスを入力してください。");
      return;
    }

    try {
      const config = await invoke<Config>("add_folder", { localPath, virtualName });
      handleConfigLoaded(config);
      addFolderModalEl.style.display = "none";
      alert(`仮想フォルダ「${virtualName}」の同期を開始しました！`);
    } catch (err: any) {
      alert(`フォルダの追加に失敗しました: ${err}`);
    }
  });

  // 9. Modal (New Virtual Folder) Handlers
  addNewVirtualFolderBtnEl?.addEventListener("click", () => {
    if (newVirtualModalEl && newVirtualNameEl && newLocalPathEl) {
      newVirtualNameEl.value = "";
      newLocalPathEl.value = "";
      newVirtualModalEl.style.display = "flex";
    }
  });

  newLocalBrowseBtnEl?.addEventListener("click", async () => {
    if (!newLocalPathEl) return;
    try {
      const selected = await invoke<string | null>("select_folder");
      if (selected) {
        newLocalPathEl.value = selected;
      }
    } catch (e) {
      console.error("Folder select dialog error:", e);
    }
  });

  newVirtualCancelBtnEl?.addEventListener("click", () => {
    if (newVirtualModalEl) newVirtualModalEl.style.display = "none";
  });

  newVirtualConfirmBtnEl?.addEventListener("click", async () => {
    if (!newVirtualNameEl || !newLocalPathEl || !newVirtualModalEl) return;
    const virtualName = newVirtualNameEl.value.trim();
    const localPath = newLocalPathEl.value.trim();

    if (!virtualName || !localPath) {
      alert("仮想名とローカルパスの両を入力してください。");
      return;
    }

    try {
      const config = await invoke<Config>("add_folder", { localPath, virtualName });
      handleConfigLoaded(config);
      newVirtualModalEl.style.display = "none";
      alert(`新しいフォルダ「${virtualName}」をマトリクスに追加し、同期を開始しました！`);
    } catch (err: any) {
      alert(`作成に失敗しました: ${err}`);
    }
  });

  // 10. Manual Sync Action
  forceSyncBtnEl?.addEventListener("click", async () => {
    if (!forceSyncBtnEl) return;
    forceSyncBtnEl.disabled = true;
    try {
      await invoke("force_sync");
    } catch (e) {
      alert(`同期に失敗しました: ${e}`);
    } finally {
      forceSyncBtnEl.disabled = false;
    }
  });

  // 11. Background Event Listeners
  await listen<SyncStatus>("sync-status", (event) => {
    updateSyncUI(event.payload);
  });

  interface QueueItem {
    file_name: string;
    relative_path: string;
    direction: string;
    status: string;
    progress: number;
  }

  interface QueueUpdate {
    relative_path: string;
    status: string;
    progress: number;
  }

  interface QueueInitPayload {
    items: QueueItem[];
  }

  await listen<QueueInitPayload>("sync-queue-init", (event) => {
    console.log("sync-queue-init received payload:", event.payload);
    if (!event || !event.payload || !Array.isArray(event.payload.items)) {
      console.warn("sync-queue-init received invalid payload:", event);
      return;
    }
    activeTransfers = event.payload.items.map(item => {
      if (!item) return null;
      const relPath = item.relative_path || "";
      return {
        fileName: item.file_name || relPath.split('/').pop() || "Unknown",
        relative_path: relPath,
        status: translateDirection(item.direction || ""),
        progress: 0
      };
    }).filter((x): x is TransferTask => x !== null);
    renderTransfersTable();
  });


  await listen<QueueUpdate>("sync-queue-update", (event) => {
    const update = event.payload;
    if (!update || !update.relative_path) return;
    const item = activeTransfers.find(t => t.relative_path === update.relative_path);
    if (item) {
      if (update.status === "completed") {
        item.status = "完了";
        item.progress = 100;
      } else if (update.status === "processing") {
        let label = item.status;
        if (!label.endsWith("中")) {
          label += "中";
        }
        item.status = label;
        item.progress = Math.round(update.progress * 100) || 10;
      } else if (update.status === "failed") {
        item.status = "失敗";
        item.progress = 0;
      }
      renderTransfersTable();
    }
  });
});

function translateDirection(dir: string): string {
  if (!dir) return "処理中";
  if (dir === "download_new") return "新規ダウンロード";
  if (dir === "download_overwrite") return "上書きダウンロード";
  if (dir === "upload_new") return "新規アップロード";
  if (dir === "upload_overwrite") return "上書きアップロード";
  if (dir === "move") return "移動";
  if (dir === "delete_local" || dir === "delete_remote") return "削除";
  return "処理中";
}


