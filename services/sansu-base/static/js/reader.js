// sansu-base OCR Reader Frontend Logic

document.addEventListener('DOMContentLoaded', () => {
    const state = {
        session_id: crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).substring(2) + Date.now().toString(36),
        currentStep: 1,
        problemFile: null,
        explanationFile: null,
        problemMarkdown: '',
        matplotlibCode: '',
        problemDiagramUrl: '',
        problemTextScanned: false,
        problemDiagramScanned: false,
        explanationMarkdown: '',
        strategySummary: '',
        tags: [],
        driveFiles: [],
        activeDriveTarget: null,
        accumulatedCost: 0.0
    };

    const PRICING = {
        'gemini-3.5-flash':      { input: 1.50,  output: 9.00 },
        'gemini-3.1-flash-lite': { input: 0.075, output: 0.30 }
    };

    const loadingOverlay = document.getElementById('loading-overlay');
    const loadingText    = document.getElementById('loading-text');
    const toast          = document.getElementById('toast');

    const stepIndicators = {
        1: document.getElementById('step-indicator-1'),
        2: document.getElementById('step-indicator-2'),
        3: document.getElementById('step-indicator-3')
    };
    const stepLines  = { 1: document.getElementById('step-line-1'), 2: document.getElementById('step-line-2') };
    const wizardPanels = {
        1: document.getElementById('wizard-panel-1'),
        2: document.getElementById('wizard-panel-2'),
        3: document.getElementById('wizard-panel-3')
    };

    // --- Toast ---
    function showToast(message, type = 'success') {
        toast.textContent = message;
        toast.className = 'toast';
        if (type === 'success') toast.classList.add('success');
        if (type === 'error')   toast.classList.add('error');
        toast.style.display = 'block';
        setTimeout(() => { toast.style.display = 'none'; }, 4000);
    }

    // --- Wizard Navigation ---
    function navigateToStep(stepNum) {
        Object.keys(wizardPanels).forEach(k => {
            wizardPanels[k].classList.toggle('active', parseInt(k) === stepNum);
        });
        Object.keys(stepIndicators).forEach(k => {
            const num = parseInt(k);
            stepIndicators[k].classList.toggle('active',    num === stepNum);
            stepIndicators[k].classList.toggle('completed', num < stepNum);
        });
        Object.keys(stepLines).forEach(k => {
            stepLines[k].classList.toggle('completed', parseInt(k) < stepNum);
        });
        state.currentStep = stepNum;
        if (stepNum === 3) renderFinalPreview();
    }

    // --- Drive Modal ---
    const driveModal          = document.getElementById('drive-modal-overlay');
    const driveFilesContainer = document.getElementById('drive-files-container');
    const driveLoading        = document.getElementById('drive-loading');

    document.getElementById('btn-close-drive-modal').addEventListener('click', () => {
        driveModal.style.display = 'none';
    });

    function openDriveModal(target) {
        state.activeDriveTarget = target;
        driveModal.style.display = 'flex';
        driveLoading.style.display = 'flex';
        driveFilesContainer.style.display = 'none';

        fetch('/api/drive/files')
            .then(res => { if (!res.ok) throw new Error(); return res.json(); })
            .then(files => { state.driveFiles = files; renderDriveFiles(); })
            .catch(() => {
                showToast('Googleドライブとの通信に失敗しました。', 'error');
                driveModal.style.display = 'none';
            });
    }

    function renderDriveFiles() {
        driveLoading.style.display = 'none';
        driveFilesContainer.style.display = 'grid';
        driveFilesContainer.innerHTML = '';

        if (!state.driveFiles.length) {
            driveFilesContainer.innerHTML = '<div style="grid-column:span 4;text-align:center;color:var(--text-muted);">ファイルが見つかりません</div>';
            return;
        }
        state.driveFiles.forEach(file => {
            const card = document.createElement('div');
            card.className = 'drive-file-card';
            const iconClass = file.mimeType === 'application/pdf' ? 'fa-file-pdf' : 'fa-file-image';
            card.innerHTML = `<i class="fa-solid ${iconClass}"></i><div class="drive-file-name" title="${file.name}">${file.name}</div><div class="drive-file-date">${new Date(file.modifiedTime).toLocaleDateString()}</div>`;
            card.addEventListener('click', () => selectDriveFile(file));
            driveFilesContainer.appendChild(card);
        });
    }

    function selectDriveFile(file) {
        const fileData = { drive: true, id: file.id, name: file.name };
        if (state.activeDriveTarget === 'problem') {
            state.problemFile = fileData;
            setFileInfo('problem-file-info', `<i class="fa-brands fa-google-drive"></i> ${file.name}`, 'problem');
        } else {
            state.explanationFile = fileData;
            setFileInfo('explanation-file-info', `<i class="fa-brands fa-google-drive"></i> ${file.name}`, 'explanation');
        }
        driveModal.style.display = 'none';
        showToast(`Googleドライブから選択: ${file.name}`);
    }

    // --- File Info Chip ---
    function setFileInfo(infoId, html, targetKey) {
        const info = document.getElementById(infoId);
        info.style.display = 'flex';
        info.innerHTML = `<span>${html}</span> <i class="fa-solid fa-xmark" style="cursor:pointer;" id="btn-clear-${targetKey}-file"></i>`;
        document.getElementById(`btn-clear-${targetKey}-file`).addEventListener('click', (e) => {
            e.stopPropagation();
            if (targetKey === 'problem') state.problemFile = null;
            else state.explanationFile = null;
            info.style.display = 'none';
        });
    }

    // --- Drag and Drop ---
    function setupDragAndDrop(zoneId, inputId, targetKey, infoId) {
        const zone  = document.getElementById(zoneId);
        const input = document.getElementById(inputId);
        zone.addEventListener('dragover',  (e) => { e.preventDefault(); zone.classList.add('dragover'); });
        zone.addEventListener('dragleave', ()  => zone.classList.remove('dragover'));
        zone.addEventListener('drop', (e) => {
            e.preventDefault();
            zone.classList.remove('dragover');
            if (e.dataTransfer.files.length > 0) selectLocalFile(e.dataTransfer.files[0], targetKey, infoId);
        });
        input.addEventListener('change', () => {
            if (input.files.length > 0) selectLocalFile(input.files[0], targetKey, infoId);
        });
    }

    function selectLocalFile(file, targetKey, infoId) {
        if (targetKey === 'problem') state.problemFile = file;
        else state.explanationFile = file;
        setFileInfo(infoId, `<i class="fa-solid fa-file"></i> ${file.name} (${(file.size / 1024 / 1024).toFixed(2)} MB)`, targetKey);
    }

    setupDragAndDrop('problem-upload-zone',     'problem-file-input',     'problem',     'problem-file-info');
    setupDragAndDrop('explanation-upload-zone', 'explanation-file-input', 'explanation', 'explanation-file-info');

    document.getElementById('btn-problem-drive').addEventListener('click',     () => openDriveModal('problem'));
    document.getElementById('btn-explanation-drive').addEventListener('click', () => openDriveModal('explanation'));

    // --- Cost Display ---
    function updateCostDisplay(tokenUsage, model, displayElementId) {
        const pricing = PRICING[model];
        if (!pricing) return;
        const totalUsd = (tokenUsage.input_tokens  || 0) * (pricing.input  / 1_000_000)
                       + (tokenUsage.output_tokens || 0) * (pricing.output / 1_000_000);
        state.accumulatedCost += totalUsd;
        const el = document.getElementById(displayElementId);
        if (el) el.innerHTML = `<span>入力: ${tokenUsage.input_tokens || 0} t | 出力: ${tokenUsage.output_tokens || 0} t</span><strong>約 ${(totalUsd * 160).toFixed(2)} 円</strong>`;
    }

    // --- FormData builder ---
    function buildOcrFormData(file, model, hint, part) {
        const fd = new FormData();
        fd.append('model',      model);
        fd.append('hint',       hint);
        fd.append('session_id', state.session_id);
        fd.append('part',       part);
        if (file.drive) fd.append('file_id', file.id);
        else            fd.append('file',    file);
        return fd;
    }

    // =========================================================
    // STEP 1 — Split OCR
    // =========================================================
    const problemActionsNext    = document.getElementById('problem-actions-next');
    const problemTextPreview    = document.getElementById('problem-text-preview');
    const problemDiagramPreview = document.getElementById('problem-diagram-preview');

    function maybeShowNextButton() {
        if (state.problemTextScanned || state.problemDiagramScanned) {
            problemActionsNext.style.display = 'block';
        }
    }

    function renderProblemTextPreview() {
        const md = state.problemDiagramUrl
            ? state.problemMarkdown.replace('problem_diagram.png', state.problemDiagramUrl)
            : state.problemMarkdown;
        problemTextPreview.innerHTML = `<div class="markdown-body">${md}</div>`;
        if (window.MathJax && window.MathJax.typesetPromise) {
            window.MathJax.typesetPromise([problemTextPreview]);
        }
    }

    // 「文字のみを正確にスキャン」
    document.getElementById('btn-ocr-text').addEventListener('click', () => {
        if (!state.problemFile) {
            showToast('問題の画像またはPDFを選択してください。', 'error');
            return;
        }
        const model = document.getElementById('problem-model').value;
        const hint  = document.getElementById('problem-hint').value;
        const fd    = buildOcrFormData(state.problemFile, model, hint, 'text');

        loadingText.textContent = '問題文を正確にスキャン中...';
        loadingOverlay.style.display = 'flex';

        fetch('/api/ocr/read-problem', { method: 'POST', body: fd })
            .then(res => { if (!res.ok) throw new Error(); return res.json(); })
            .then(data => {
                loadingOverlay.style.display = 'none';
                if (data.error && !data.problem_markdown) {
                    showToast(`エラー: ${data.error}`, 'error');
                    return;
                }
                state.problemMarkdown = data.problem_markdown || '';
                state.problemTextScanned = true;
                document.getElementById('problem-text-editor').value = state.problemMarkdown;
                renderProblemTextPreview();
                updateCostDisplay(data.token_usage, model, 'problem-cost-display');
                document.getElementById('btn-edit-text-block').style.display = 'inline-flex';
                maybeShowNextButton();
                showToast('問題文のスキャンが完了しました！');
            })
            .catch(() => {
                loadingOverlay.style.display = 'none';
                showToast('OCRサーバーへの接続エラーが発生しました。', 'error');
            });
    });

    // 「図形のみを正確に再現」
    document.getElementById('btn-ocr-diagram').addEventListener('click', () => {
        if (!state.problemFile) {
            showToast('問題の画像またはPDFを選択してください。', 'error');
            return;
        }
        const model = document.getElementById('problem-model').value;
        const hint  = document.getElementById('problem-hint').value;
        const fd    = buildOcrFormData(state.problemFile, model, hint, 'diagram');

        loadingText.textContent = '図形を解析してコードを生成中...';
        loadingOverlay.style.display = 'flex';

        fetch('/api/ocr/read-problem', { method: 'POST', body: fd })
            .then(res => { if (!res.ok) throw new Error(); return res.json(); })
            .then(data => {
                loadingOverlay.style.display = 'none';
                if (data.error && !data.image_url && !data.matplotlib_code) {
                    showToast(`エラー: ${data.error}`, 'error');
                    return;
                }
                state.matplotlibCode    = data.matplotlib_code || '';
                state.problemDiagramUrl = data.image_url || '';
                state.problemDiagramScanned = true;
                document.getElementById('problem-diagram-code-editor').value = state.matplotlibCode;

                if (state.problemDiagramUrl) {
                    problemDiagramPreview.innerHTML = `<img src="${state.problemDiagramUrl}" style="max-width:100%;border-radius:4px;" alt="図形プレビュー">`;
                } else if (data.error) {
                    problemDiagramPreview.innerHTML = `<div style="color:#ef4444;font-size:0.85rem;padding:0.5rem;">${data.error}</div>`;
                }

                if (state.problemTextScanned) renderProblemTextPreview();
                updateCostDisplay(data.token_usage, model, 'problem-cost-display');
                document.getElementById('btn-edit-diagram-block').style.display = 'inline-flex';
                maybeShowNextButton();
                showToast('図形の再現が完了しました！');
            })
            .catch(() => {
                loadingOverlay.style.display = 'none';
                showToast('OCRサーバーへの接続エラーが発生しました。', 'error');
            });
    });

    // テキストブロック 編集 / プレビュー反映
    const problemTextEditorPane = document.getElementById('problem-text-editor-pane');
    const btnEditTextBlock      = document.getElementById('btn-edit-text-block');

    btnEditTextBlock.addEventListener('click', () => {
        const visible = problemTextEditorPane.style.display !== 'none';
        problemTextEditorPane.style.display = visible ? 'none' : 'flex';
        btnEditTextBlock.innerHTML = visible
            ? '<i class="fa-solid fa-pen"></i> 修正'
            : '<i class="fa-solid fa-eye"></i> プレビュー';
    });

    document.getElementById('btn-sync-text-preview').addEventListener('click', () => {
        state.problemMarkdown = document.getElementById('problem-text-editor').value;
        renderProblemTextPreview();
        showToast('テキストをプレビューに反映しました。');
    });

    // 図形ブロック 編集 / 再描画
    const problemDiagramEditorPane = document.getElementById('problem-diagram-editor-pane');
    const btnEditDiagramBlock      = document.getElementById('btn-edit-diagram-block');

    btnEditDiagramBlock.addEventListener('click', () => {
        const visible = problemDiagramEditorPane.style.display !== 'none';
        problemDiagramEditorPane.style.display = visible ? 'none' : 'flex';
        btnEditDiagramBlock.innerHTML = visible
            ? '<i class="fa-solid fa-code"></i> コード修正'
            : '<i class="fa-solid fa-eye"></i> プレビュー';
    });

    document.getElementById('btn-sync-diagram-preview').addEventListener('click', () => {
        const code = document.getElementById('problem-diagram-code-editor').value.trim();
        if (!code) { showToast('コードが空です。', 'error'); return; }

        loadingText.textContent = '図形を再描画中...';
        loadingOverlay.style.display = 'flex';

        fetch('/api/diagram/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code, session_id: state.session_id, suffix: 'problem' })
        })
        .then(res => res.json())
        .then(data => {
            loadingOverlay.style.display = 'none';
            if (data.success && data.image_url) {
                state.matplotlibCode    = code;
                state.problemDiagramUrl = data.image_url;
                problemDiagramPreview.innerHTML = `<img src="${data.image_url}" style="max-width:100%;border-radius:4px;" alt="図形プレビュー">`;
                if (state.problemTextScanned) renderProblemTextPreview();
                showToast('図形を再描画しました！');
            } else {
                showToast(`描画エラー: ${data.error}`, 'error');
            }
        })
        .catch(() => {
            loadingOverlay.style.display = 'none';
            showToast('図形描画中にエラーが発生しました。', 'error');
        });
    });

    document.getElementById('btn-step1-next').addEventListener('click', () => navigateToStep(2));

    // =========================================================
    // STEP 2 — Explanation OCR
    // =========================================================
    const explanationPreviewBody    = document.getElementById('explanation-preview-body');
    const explanationEditorPane     = document.getElementById('explanation-editor-pane');
    const explanationMarkdownEditor = document.getElementById('explanation-markdown-editor');
    const btnEditExplanation        = document.getElementById('btn-edit-explanation');
    const btnSyncExplanationPreview = document.getElementById('btn-sync-explanation-preview');
    const explanationActionsNext    = document.getElementById('explanation-actions-next');

    document.getElementById('btn-run-explanation-ocr').addEventListener('click', () => {
        if (!state.explanationFile) {
            showToast('解説の画像またはPDFを選択してください。', 'error');
            return;
        }
        const model = document.getElementById('explanation-model').value;
        const hint  = document.getElementById('explanation-hint').value;
        const fd    = buildOcrFormData(state.explanationFile, model, hint, 'all');
        fd.append('problem_text', state.problemMarkdown);

        loadingText.textContent = '解説をスキャン＆詳細化中...';
        loadingOverlay.style.display = 'flex';

        fetch('/api/ocr/read-explanation', { method: 'POST', body: fd })
            .then(res => { if (!res.ok) throw new Error(); return res.json(); })
            .then(data => {
                loadingOverlay.style.display = 'none';
                if (data.error && !data.explanation_markdown) {
                    showToast(`エラー: ${data.error}`, 'error');
                    return;
                }
                state.explanationMarkdown = data.explanation_markdown || '';
                explanationMarkdownEditor.value = state.explanationMarkdown;
                state.strategySummary = data.strategy_summary || '';
                state.tags = data.tags || [];

                document.getElementById('save-strategy').value = state.strategySummary;

                renderExplanationPreview();
                updateCostDisplay(data.token_usage, model, 'explanation-cost-display');
                btnEditExplanation.style.display = 'inline-flex';
                explanationActionsNext.style.display = 'block';
                showToast(data.error ? '読み取り完了（一部警告あり）' : '解答解説の読み取りに成功しました！',
                          data.error ? 'warning' : 'success');
            })
            .catch(() => {
                loadingOverlay.style.display = 'none';
                showToast('OCRサーバーへの接続エラーが発生しました。', 'error');
            });
    });

    function renderExplanationPreview() {
        explanationPreviewBody.innerHTML = `
            <div class="strategy-box">
                <strong>解法の核心（コア戦略）:</strong> ${state.strategySummary || '抽出中...'}
            </div>
            <div class="markdown-body">${state.explanationMarkdown}</div>
            <div style="margin-top:1rem;display:flex;gap:0.5rem;flex-wrap:wrap;">
                ${state.tags.map(t => `<span class="badge badge-category">#${t}</span>`).join('')}
            </div>
        `;
        if (window.MathJax && window.MathJax.typesetPromise) {
            window.MathJax.typesetPromise([explanationPreviewBody]);
        }
    }

    btnEditExplanation.addEventListener('click', () => {
        const visible = explanationEditorPane.style.display !== 'none';
        explanationEditorPane.style.display = visible ? 'none' : 'flex';
        btnEditExplanation.innerHTML = visible
            ? '<i class="fa-solid fa-pen"></i> 編集'
            : '<i class="fa-solid fa-eye"></i> プレビューを表示';
    });

    btnSyncExplanationPreview.addEventListener('click', () => {
        state.explanationMarkdown = explanationMarkdownEditor.value;
        renderExplanationPreview();
        showToast('エディタの変更をプレビューに反映しました。');
    });

    document.getElementById('btn-step2-back').addEventListener('click', () => navigateToStep(1));
    document.getElementById('btn-step2-next').addEventListener('click', () => navigateToStep(3));

    // =========================================================
    // STEP 3 — Metadata & Save
    // =========================================================
    const finalPreviewContainer = document.getElementById('final-preview-container');

    function getMergedProblemMarkdown() {
        if (!state.problemMarkdown) return '';
        return state.problemDiagramUrl
            ? state.problemMarkdown.replace('problem_diagram.png', state.problemDiagramUrl)
            : state.problemMarkdown;
    }

    function renderFinalPreview() {
        const sourceBook = document.getElementById('save-source-book').value || '(教材名未入力)';
        const chapter    = document.getElementById('save-chapter').value    || '';
        const unit       = document.getElementById('save-unit').value       || '未設定';
        const pageNum    = document.getElementById('save-page-number').value   || '';
        const problemNum = document.getElementById('save-problem-number').value || '';
        const strategy   = document.getElementById('save-strategy').value   || state.strategySummary;

        const chapterStr    = chapter    ? ` ${chapter}`      : '';
        const problemNumStr = problemNum ? ` (問:${problemNum})` : '';

        finalPreviewContainer.innerHTML = `
            <div class="preview-doc-header" style="border-bottom:2px solid rgba(99,102,241,0.1);padding-bottom:1rem;margin-bottom:1.5rem;">
                <div style="font-size:1.4rem;font-weight:700;color:#0f172a;margin-bottom:0.5rem;">
                    ${sourceBook}${chapterStr}${problemNumStr}
                </div>
                <div class="preview-meta-badges" style="display:flex;gap:0.5rem;flex-wrap:wrap;">
                    <span class="badge badge-school" style="background:#f1f5f9;color:#475569;">ID: 自動発番</span>
                    <span class="badge badge-category" style="background:#e0e7ff;color:#4f46e5;">単元: ${unit}</span>
                    ${pageNum ? `<span class="badge badge-subcat" style="background:#fce7f3;color:#db2777;">ページ数: ${pageNum}</span>` : ''}
                </div>
                ${strategy ? `<div class="strategy-box" style="margin-top:1rem;background:rgba(99,102,241,0.04);border-left:4px solid var(--accent-primary);padding:0.75rem 1rem;border-radius:4px;font-size:0.88rem;color:#312e81;"><strong>解法の核心:</strong> ${strategy}</div>` : ''}
            </div>
            <h4 class="preview-section-header" style="font-size:1.1rem;border-bottom:1px dashed #e2e8f0;padding-bottom:0.25rem;margin-bottom:0.75rem;"><i class="fa-solid fa-circle-question"></i> 問題</h4>
            <div class="markdown-body" style="margin-bottom:2rem;">${getMergedProblemMarkdown() || '(問題文なし)'}</div>
            <h4 class="preview-section-header" style="font-size:1.1rem;border-bottom:1px dashed #e2e8f0;padding-bottom:0.25rem;margin-bottom:0.75rem;"><i class="fa-solid fa-lightbulb"></i> 解説</h4>
            <div class="markdown-body">${state.explanationMarkdown || '(解説なし)'}</div>
        `;
        if (window.MathJax && window.MathJax.typesetPromise) {
            window.MathJax.typesetPromise([finalPreviewContainer]);
        }
    }

    ['save-source-book', 'save-chapter', 'save-unit', 'save-page-number', 'save-problem-number', 'save-strategy'].forEach(id => {
        document.getElementById(id).addEventListener('input', renderFinalPreview);
    });

    document.getElementById('btn-step3-back').addEventListener('click', () => navigateToStep(2));

    document.getElementById('btn-save-problem').addEventListener('click', () => {
        const sourceBook = document.getElementById('save-source-book').value.trim();
        const chapter    = document.getElementById('save-chapter').value.trim();
        const unit       = document.getElementById('save-unit').value.trim();
        const pageNum    = document.getElementById('save-page-number').value.trim();
        const strategy   = document.getElementById('save-strategy').value.trim();

        if (!sourceBook) {
            showToast('教材名は必須入力です。', 'error');
            return;
        }

        const requestData = {
            session_id:           state.session_id,
            source_book:          sourceBook,
            chapter,
            unit,
            problem_markdown:     getMergedProblemMarkdown(),
            explanation_markdown: state.explanationMarkdown,
            strategy_summary:     pageNum ? `${pageNum} | ${strategy}` : strategy,
            tags:                 state.tags
        };

        loadingText.textContent = 'データベースおよびGoogle Driveへ保存中...';
        loadingOverlay.style.display = 'flex';

        fetch('/api/problems/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestData)
        })
        .then(res => { if (!res.ok) throw new Error(); return res.json(); })
        .then(data => {
            loadingOverlay.style.display = 'none';
            if (data.status === 'success') {
                showToast(`登録完了！ID: ${data.display_id}`);
                setTimeout(() => { window.location.href = '/'; }, 2000);
            } else {
                showToast(`保存に失敗: ${data.error}`, 'error');
            }
        })
        .catch(() => {
            loadingOverlay.style.display = 'none';
            showToast('保存中にエラーが発生しました。', 'error');
        });
    });
});
