document.addEventListener("DOMContentLoaded", () => {
    const folderInput = document.getElementById("folder-path-input");
    const cleanDirsCheckbox = document.getElementById("clean-dirs-checkbox");
    const scanBtn = document.getElementById("scan-btn");
    const executeBtn = document.getElementById("execute-btn");
    const statusMessage = document.getElementById("status-message");
    
    const previewSection = document.getElementById("preview-section");
    const previewTbody = document.getElementById("preview-tbody");
    const fileCountSpan = document.getElementById("file-count");
    
    const logSection = document.getElementById("log-section");
    const logOutput = document.getElementById("log-output");

    let currentScanFiles = [];

    // ステータスメッセージ表示用のユーティリティ
    function showStatus(message, type) {
        statusMessage.className = `status-message ${type}`;
        
        let iconHtml = "";
        if (type === "loading") {
            iconHtml = `
                <svg class="spinner" viewBox="0 0 50 50">
                    <circle class="path" cx="25" cy="25" r="20" fill="none" stroke-width="5"></circle>
                </svg>
            `;
        } else if (type === "success") {
            iconHtml = `<i class="fa-solid fa-circle-check"></i>`;
        } else if (type === "error") {
            iconHtml = `<i class="fa-solid fa-circle-exclamation"></i>`;
        }

        statusMessage.innerHTML = `${iconHtml}<span class="status-text">${message}</span>`;
        statusMessage.classList.remove("hidden");
    }

    function hideStatus() {
        statusMessage.classList.add("hidden");
    }

    // スキャン (Dry Run) の実行
    scanBtn.addEventListener("click", async () => {
        const rootDir = folderInput.value.trim();
        if (!rootDir) {
            showStatus("フォルダパスを入力してください。", "error");
            return;
        }

        // UI状態のリセット
        hideStatus();
        previewSection.classList.add("hidden");
        executeBtn.disabled = true;
        currentScanFiles = [];

        showStatus("フォルダをスキャンしています...", "loading");

        try {
            const response = await fetch("/api/scan", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ root_dir: rootDir })
            });

            const data = await response.json();

            if (!response.ok || !data.success) {
                showStatus(data.error || "スキャンに失敗しました。", "error");
                return;
            }

            currentScanFiles = data.files || [];
            
            if (currentScanFiles.length === 0) {
                showStatus("サブフォルダ内に移動対象のファイルは見つかりませんでした（すでに最上位に配置されているか、空です）。", "success");
                return;
            }

            // プレビューテーブルの描画
            renderPreview(currentScanFiles);
            
            showStatus(`スキャン完了: 移動対象ファイル ${currentScanFiles.length} 件。プレビューを確認して実行してください。`, "success");
            executeBtn.disabled = false;

        } catch (error) {
            console.error("Scan error:", error);
            showStatus("サーバーとの通信に失敗しました。", "error");
        }
    });

    // プレビューのレンダリング
    function renderPreview(files) {
        previewTbody.innerHTML = "";
        fileCountSpan.textContent = files.length;

        files.forEach(file => {
            const tr = document.createElement("tr");

            // 移動元カラム
            const tdSrc = document.createElement("td");
            tdSrc.className = "src-path-col";
            tdSrc.textContent = file.rel_src_path;
            tr.appendChild(tdSrc);

            // 矢印カラム
            const tdArrow = document.createElement("td");
            tdArrow.className = "arrow-col";
            tdArrow.innerHTML = `<i class="fa-solid fa-arrow-right-long"></i>`;
            tr.appendChild(tdArrow);

            // 移動先カラム
            const tdDest = document.createElement("td");
            tdDest.className = "dest-name-col";
            tdDest.textContent = file.dest_filename;

            // 連番が付与された場合はバッジを表示
            if (file.dest_filename !== file.original_filename) {
                const renamedSpan = document.createElement("span");
                renamedSpan.className = "renamed-flag";
                renamedSpan.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> 重複回避リネーム`;
                tdDest.appendChild(renamedSpan);
            }
            tr.appendChild(tdDest);

            previewTbody.appendChild(tr);
        });

        previewSection.classList.remove("hidden");
    }

    // フラット化の実行
    executeBtn.addEventListener("click", async () => {
        const rootDir = folderInput.value.trim();
        const deleteEmptyDirs = cleanDirsCheckbox.checked;

        if (!rootDir) {
            showStatus("フォルダパスを入力してください。", "error");
            return;
        }

        if (currentScanFiles.length === 0) {
            showStatus("移動対象のファイルがありません。まずはスキャンを行ってください。", "error");
            return;
        }

        // UI制御
        executeBtn.disabled = true;
        scanBtn.disabled = true;
        folderInput.disabled = true;
        cleanDirsCheckbox.disabled = true;
        
        showStatus("フラット化処理を実行しています...", "loading");
        logSection.classList.add("hidden");
        logOutput.innerHTML = "";

        try {
            const response = await fetch("/api/flatten", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    root_dir: rootDir,
                    delete_empty_dirs: deleteEmptyDirs
                })
            });

            const data = await response.json();

            if (!response.ok || !data.success) {
                showStatus(data.error || "処理の実行中にエラーが発生しました。", "error");
                executeBtn.disabled = false;
                scanBtn.disabled = false;
                folderInput.disabled = false;
                cleanDirsCheckbox.disabled = false;
                return;
            }

            // ログの出力
            renderLogs(data);

            // 結果ステータス表示
            if (data.errors && data.errors.length > 0) {
                showStatus(`処理完了（一部エラーあり）: ${data.success_count} 件の移動に成功、${data.errors.length} 件が失敗。`, "error");
            } else {
                showStatus(`処理成功: すべてのファイル (${data.success_count} 件) の移動が完了しました！`, "success");
            }

            // スキャンリストのクリアとテーブルの再非表示
            currentScanFiles = [];
            previewSection.classList.add("hidden");

        } catch (error) {
            console.error("Execute error:", error);
            showStatus("サーバーとの通信に失敗しました。", "error");
        } finally {
            scanBtn.disabled = false;
            folderInput.disabled = false;
            cleanDirsCheckbox.disabled = false;
        }
    });

    // 実行ログのレンダリング
    function renderLogs(data) {
        logOutput.innerHTML = "";
        
        // 開始メッセージ
        addLogLine(`[INFO] 処理を開始しました。対象ディレクトリ: ${data.root_dir}`, "info");

        // 移動結果
        if (data.moved_files && data.moved_files.length > 0) {
            addLogLine(`[INFO] --- ファイル移動結果 ---`, "info");
            data.moved_files.forEach(file => {
                addLogLine(`[SUCCESS] 移動元: ${file.rel_src}  =>  移動先名: ${file.dest_filename}`, "success");
            });
        }

        // エラーログ
        if (data.errors && data.errors.length > 0) {
            addLogLine(`[ERROR] --- 発生したエラー ---`, "error");
            data.errors.forEach(err => {
                addLogLine(`[FAIL] ファイル: ${err.src_path} | エラー内容: ${err.error}`, "error");
            });
        }

        // 空フォルダ削除ログ
        if (data.deleted_dirs && data.deleted_dirs.length > 0) {
            addLogLine(`[INFO] --- 空フォルダのクリーンアップ結果 ---`, "info");
            data.deleted_dirs.forEach(dir => {
                addLogLine(`[CLEANUP] 空フォルダを削除しました: ${dir}`, "info");
            });
        } else if (cleanDirsCheckbox.checked) {
            addLogLine(`[INFO] 削除対象の空フォルダはありませんでした。`, "info");
        }

        // 完了サマリー
        addLogLine(`\n[INFO] 処理が完了しました。`, "info");
        addLogLine(`[INFO] 成功件数: ${data.success_count} 件`, "success");
        addLogLine(`[INFO] 失敗件数: ${data.errors ? data.errors.length : 0} 件`, data.errors && data.errors.length > 0 ? "error" : "success");
        if (cleanDirsCheckbox.checked) {
            addLogLine(`[INFO] 削除フォルダ数: ${data.deleted_dirs ? data.deleted_dirs.length : 0} 件`, "info");
        }

        logSection.classList.remove("hidden");
        // 最下部までスクロール
        logOutput.scrollTop = logOutput.scrollHeight;
    }

    function addLogLine(text, type = "") {
        const div = document.createElement("div");
        div.className = `log-line log-${type}`;
        div.textContent = text;
        logOutput.appendChild(div);
    }
});
