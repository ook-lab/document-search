interface SyncFolder {
  folder_id: number;
  virtual_name: string;
  devices: string[];
}

interface FileMeta {
  file_id: number;
  folder_id: number;
  relative_path: string;
  file_name: string;
  file_size: number;
  file_hash: string;
  last_modified_at: string;
  device_name: string;
}

interface ActivityLog {
  log_id: number;
  device_name: string;
  action_type: string;
  file_path: string;
  created_at: string;
}

// State
let folders: SyncFolder[] = [];
let files: FileMeta[] = [];
let selectedFolderId: number | null = null;
let selectedSubPath: string = ""; // "" means root of virtual folder

// DOM Elements
const tabExplorer = document.getElementById("tab-explorer") as HTMLButtonElement;
const tabActivities = document.getElementById("tab-activities") as HTMLButtonElement;
const pageExplorer = document.getElementById("page-explorer") as HTMLDivElement;
const pageActivities = document.getElementById("page-activities") as HTMLDivElement;

const folderTreeRoot = document.getElementById("folder-tree-root") as HTMLUListElement;
const breadcrumb = document.getElementById("breadcrumb") as HTMLDivElement;
const filesTbody = document.getElementById("files-tbody") as HTMLTableSectionElement;
const activitiesTbody = document.getElementById("activities-tbody") as HTMLTableSectionElement;
const refreshActivities = document.getElementById("refresh-activities") as HTMLButtonElement;

// Tab Routing
tabExplorer.addEventListener("click", () => {
  tabExplorer.classList.add("bg-white", "shadow-sm", "text-gray-800");
  tabExplorer.classList.remove("text-gray-500");
  tabActivities.classList.remove("bg-white", "shadow-sm", "text-gray-800");
  tabActivities.classList.add("text-gray-500");

  pageExplorer.classList.remove("hidden");
  pageActivities.classList.add("hidden");
});

tabActivities.addEventListener("click", () => {
  tabActivities.classList.add("bg-white", "shadow-sm", "text-gray-800");
  tabActivities.classList.remove("text-gray-500");
  tabExplorer.classList.remove("bg-white", "shadow-sm", "text-gray-800");
  tabExplorer.classList.add("text-gray-500");

  pageActivities.classList.remove("hidden");
  pageExplorer.classList.add("hidden");

  loadActivities();
});

// Load Folders & Build Tree
async function loadFolders() {
  try {
    const res = await fetch("/api/web/folders/unified");

    if (!res.ok) throw new Error("Failed to fetch folders");
    folders = await res.json();
    renderFolderTree();
  } catch (err) {
    console.error(err);
    folderTreeRoot.innerHTML = `<li class="text-sm text-red-500 p-2">フォルダ一覧の取得に失敗しました。</li>`;
  }
}

// Render Folder Tree (Unified folder model directly returned by server)
function renderFolderTree() {
  if (folders.length === 0) {
    folderTreeRoot.innerHTML = `<li class="text-sm text-gray-400 italic p-2">同期フォルダがありません</li>`;
    return;
  }

  let html = "";
  folders.forEach(f => {
    const isSelected = selectedFolderId === f.folder_id;
    const devicesTooltip = `同期デバイス: ${f.devices.join(", ")}`;

    html += `
      <li class="space-y-1">
        <div class="flex items-center justify-between p-2 rounded-md cursor-pointer transition-colors ${
          isSelected && selectedSubPath === "" ? "bg-blue-50 text-[#2563EB] font-bold" : "hover:bg-gray-50 text-gray-700"
        }" onclick="selectFolder(${f.folder_id}, '')" title="${escapeHtml(devicesTooltip)}">
          <span class="flex items-center space-x-2 text-sm">
            <svg class="w-4.5 h-4.5 text-[#E05236]" width="18" height="18" fill="currentColor" viewBox="0 0 20 20"><path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z"></path></svg>
            <span class="truncate max-w-[160px] font-semibold text-gray-800">${escapeHtml(f.virtual_name)}</span>
          </span>
          <span class="text-[9px] bg-[#FFF0ED] text-[#E05236] px-2 py-0.5 rounded-full font-bold shrink-0 border border-[#FFDAD3]">${f.devices.length}台で同期</span>
        </div>
        ${isSelected ? renderSubFoldersTree(f.folder_id) : ""}
      </li>
    `;
  });
  folderTreeRoot.innerHTML = html;
}

// Generate subfolder tree nodes from server-provided file list
function renderSubFoldersTree(folderId: number): string {
  const subDirs = new Set<string>();
  files.forEach(file => {
    if (file.relative_path.includes("/")) {
      const parts = file.relative_path.split("/");
      let currentPath = "";
      for (let i = 0; i < parts.length - 1; i++) {
        currentPath = currentPath ? `${currentPath}/${parts[i]}` : parts[i];
        subDirs.add(currentPath);
      }
    }
  });

  if (subDirs.size === 0) return "";

  const sortedDirs = Array.from(subDirs).sort();
  let html = `<ul class="pl-4 border-l border-gray-200 ml-4 space-y-1 mt-1">`;
  sortedDirs.forEach(dir => {
    const isSelected = selectedSubPath === dir;
    const displayName = dir.split("/").pop() || dir;
    const depth = dir.split("/").length - 1;
    const padding = depth * 8;
    
    html += `
      <li style="padding-left: ${padding}px">
        <div class="flex items-center space-x-1.5 p-1.5 text-xs rounded-md cursor-pointer transition-colors ${
          isSelected ? "bg-blue-50 text-[#2563EB] font-bold" : "hover:bg-gray-50 text-gray-500"
        }" onclick="selectFolder(${folderId}, '${escapeJsString(dir)}')">
          <span>📁</span>
          <span class="truncate">${escapeHtml(displayName)}</span>
        </div>
      </li>
    `;
  });
  html += `</ul>`;
  return html;
}

// Selection handler called from HTML onClick
(window as any).selectFolder = async (folderId: number, subPath: string) => {
  selectedFolderId = folderId;
  selectedSubPath = subPath;

  // Update Breadcrumb
  const folder = folders.find(f => f.folder_id === folderId);
  if (folder) {
    breadcrumb.textContent = `${folder.virtual_name} ${subPath ? " / " + subPath : ""}`;
  }

  // Load files (which are already merged and de-duplicated by Go server SQL window functions)
  await loadFiles(folderId);
  
  // Re-render
  renderFolderTree();
  renderFilesTable();
};

async function loadFiles(folderId: number) {
  try {
    const res = await fetch(`/api/web/files?folder_id=${folderId}`);
    if (!res.ok) throw new Error("Failed to load files");
    files = await res.json();
  } catch (err) {
    console.error("Failed to load files:", err);
    files = [];
  }
}

function renderFilesTable() {
  if (!selectedFolderId) return;

  // Filter files in this subPath level
  const filteredFiles = files.filter(file => {
    if (selectedSubPath === "") {
      return !file.relative_path.includes("/");
    } else {
      const prefix = `${selectedSubPath}/`;
      if (file.relative_path.startsWith(prefix)) {
        const remaining = file.relative_path.substring(prefix.length);
        return !remaining.includes("/");
      }
      return false;
    }
  });

  if (filteredFiles.length === 0) {
    filesTbody.innerHTML = `
      <tr>
        <td colspan="5" class="py-12 text-center text-gray-400 italic">
          このフォルダは空です。
        </td>
      </tr>
    `;
    return;
  }

  filesTbody.innerHTML = filteredFiles.map((file, idx) => {
    const isEven = idx % 2 === 1;
    const bgClass = isEven ? "bg-[#F8FAFC]" : "bg-white";
    const formattedSize = formatBytes(file.file_size);
    const dateStr = new Date(file.last_modified_at).toLocaleString();

    return `
      <tr class="${bgClass} hover:bg-blue-50/50 transition-colors">
        <td class="py-3 px-6 font-medium text-gray-900 flex items-center space-x-2">
          <span class="text-lg">📄</span>
          <span class="truncate max-w-sm" title="${escapeHtml(file.file_name)}">${escapeHtml(file.file_name)}</span>
        </td>
        <td class="py-3 px-4 text-gray-500">${formattedSize}</td>
        <td class="py-3 px-4 text-gray-400 text-xs">${dateStr}</td>
        <td class="py-3 px-4 text-gray-500 text-xs font-semibold">${escapeHtml(file.device_name)}</td>
        <td class="py-3 px-6 text-right space-x-3 shrink-0">
          <a href="/api/sync/download?folder_id=${file.folder_id}&relative_path=${encodeURIComponent(file.relative_path)}" 
             class="text-[#2563EB] hover:text-blue-800 font-semibold" download="${escapeHtml(file.file_name)}">ダウンロード</a>
          <button onclick="deleteFile(${file.folder_id}, '${escapeJsString(file.relative_path)}')"
                  class="text-[#DC2626] hover:text-red-800 font-semibold">削除</button>
        </td>
      </tr>
    `;
  }).join("");
}

// Delete handler
(window as any).deleteFile = async (folderId: number, relativePath: string) => {
  if (!confirm(`本当にこのファイルを削除しますか？\n${relativePath}`)) return;

  try {
    const res = await fetch("/api/sync/delete", {
      method: "DELETE",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ folder_id: folderId, relative_path: relativePath }),
    });

    if (res.ok) {
      alert("削除しました。");
      await loadFiles(folderId);
      renderFilesTable();
    } else {
      const errText = await res.text();
      alert(`削除に失敗しました: ${errText}`);
    }
  } catch (err) {
    alert(`通信エラーが発生しました: ${err}`);
  }
};

// Load Activities
async function loadActivities() {
  try {
    const res = await fetch("/api/web/activities");
    if (!res.ok) throw new Error("Failed to fetch activities");
    const logs: ActivityLog[] = await res.json();

    if (logs.length === 0) {
      activitiesTbody.innerHTML = `
        <tr>
          <td colspan="4" class="py-8 text-center text-gray-400 italic">
            アクティビティ履歴はありません。
          </td>
        </tr>
      `;
      return;
    }

    activitiesTbody.innerHTML = logs.map(log => {
      const dateStr = new Date(log.created_at).toLocaleString();
      let badgeClass = "bg-gray-100 text-gray-600";
      let actionLabel = log.action_type;

      if (log.action_type === "CREATE") {
        badgeClass = "bg-green-50 text-green-700 border border-green-200";
        actionLabel = "新規作成";
      } else if (log.action_type === "UPDATE") {
        badgeClass = "bg-blue-50 text-blue-700 border border-blue-200";
        actionLabel = "更新";
      } else if (log.action_type === "DELETE") {
        badgeClass = "bg-red-50 text-red-700 border border-red-200";
        actionLabel = "削除";
      }

      return `
        <tr class="hover:bg-gray-50/50 transition-colors">
          <td class="py-3.5 px-6 text-gray-400 text-xs">${dateStr}</td>
          <td class="py-3.5 px-4 font-semibold text-gray-600">${escapeHtml(log.device_name)}</td>
          <td class="py-3.5 px-4 text-xs">
            <span class="px-2 py-0.5 rounded-full font-bold ${badgeClass}">${actionLabel}</span>
          </td>
          <td class="py-3.5 px-6 text-gray-700 font-mono text-xs break-all">${escapeHtml(log.file_path)}</td>
        </tr>
      `;
    }).join("");
  } catch (err) {
    console.error(err);
    activitiesTbody.innerHTML = `
      <tr>
        <td colspan="4" class="py-8 text-center text-red-500 italic">
          アクティビティ履歴の読み込みに失敗しました。
        </td>
      </tr>
    `;
  }
}

refreshActivities.addEventListener("click", loadActivities);

// Helper functions
function formatBytes(bytes: number, decimals = 2) {
  if (!+bytes) return "0 Bytes";
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ["Bytes", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}

// Escape functions
function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Escape JS quotes
function escapeJsString(str: string): string {
  return str.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

// Initial Load
loadFolders();
