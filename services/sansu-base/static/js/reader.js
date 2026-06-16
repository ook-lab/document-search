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
        problemDiagramElev: 30,
        problemDiagramAzim: -60,
        problemTextScanned: false,
        problemDiagramScanned: false,
        explanationMarkdown: '',
        explanationMatplotlibCode: '',
        explanationDiagramUrl: '',
        strategySummary: '',
        tags: [],
        difficulty: null,
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

    // =========================================================
    // 一時保存 (localStorage) — 複数件対応
    // =========================================================
    const DRAFTS_KEY = 'sansu_drafts';
    const DRAFT_MAX  = 20;

    function loadDrafts() {
        try { return JSON.parse(localStorage.getItem(DRAFTS_KEY) || '[]'); } catch { return []; }
    }
    function saveDrafts(arr) {
        localStorage.setItem(DRAFTS_KEY, JSON.stringify(arr));
    }

    function buildDraftPayload(step) {
        const extraProbDiags = [];
        document.querySelectorAll('#problem-extra-diagrams > [data-image-url]').forEach(el => {
            const ta = el.querySelector('textarea');
            extraProbDiags.push({ code: ta ? ta.value.trim() : '', imageUrl: el.dataset.imageUrl || '' });
        });
        const extraExpDiags = [];
        document.querySelectorAll('#explanation-extra-diagrams > [data-image-url]').forEach(el => {
            const ta = el.querySelector('textarea');
            extraExpDiags.push({ code: ta ? ta.value.trim() : '', imageUrl: el.dataset.imageUrl || '' });
        });
        const meta = {
            sourceBook:    document.getElementById('save-source-book').value,
            chapter:       document.getElementById('save-chapter').value,
            unit:          document.getElementById('save-unit').value,
            pageNumber:    document.getElementById('save-page-number').value,
            problemNumber: document.getElementById('save-problem-number').value,
            strategy:      document.getElementById('save-strategy').value,
        };
        const name = [meta.sourceBook, meta.chapter, meta.problemNumber ? `問${meta.problemNumber}` : '']
            .filter(Boolean).join(' ') || `下書き ステップ${step}`;
        return {
            id: Date.now().toString(),
            savedAt: new Date().toISOString(),
            step, name, metadata: meta,
            state: {
                problemMarkdown: state.problemMarkdown,
                matplotlibCode: state.matplotlibCode,
                problemDiagramUrl: state.problemDiagramUrl,
                problemDiagramElev: state.problemDiagramElev,
                problemDiagramAzim: state.problemDiagramAzim,
                problemTextScanned: state.problemTextScanned,
                problemDiagramScanned: state.problemDiagramScanned,
                explanationMarkdown: state.explanationMarkdown,
                explanationMatplotlibCode: state.explanationMatplotlibCode,
                explanationDiagramUrl: state.explanationDiagramUrl,
                strategySummary: state.strategySummary,
                tags: state.tags,
            },
            organizerBlocks,
            explanationOrganizerBlocks,
            extraProbDiags,
            extraExpDiags,
        };
    }

    function saveDraft(step) {
        const draft = buildDraftPayload(step);
        const tryStore = (d) => {
            const arr = loadDrafts();
            arr.unshift(d);
            if (arr.length > DRAFT_MAX) arr.splice(DRAFT_MAX);
            saveDrafts(arr);
        };
        try {
            tryStore(draft);
            showToast(`一時保存しました（${draft.name}）`);
        } catch (e) {
            // 画像を除いて再試行
            draft.state.problemDiagramUrl = '';
            draft.state.explanationDiagramUrl = '';
            draft.extraProbDiags.forEach(d => { d.imageUrl = ''; });
            draft.extraExpDiags.forEach(d => { d.imageUrl = ''; });
            try {
                tryStore(draft);
                showToast(`一時保存しました（画像除外）`);
            } catch (e2) {
                showToast('一時保存失敗：ストレージ容量超過', 'error');
            }
        }
        updateDraftBanner();
    }

    function deleteDraft(id) {
        const arr = loadDrafts().filter(d => d.id !== id);
        saveDrafts(arr);
        updateDraftBanner();
        renderDraftList();
    }

    function clearDraft() {
        // 最終保存時は全件削除
        localStorage.removeItem(DRAFTS_KEY);
        updateDraftBanner();
    }

    function updateDraftBanner() {
        const arr = loadDrafts();
        const banner = document.getElementById('draft-banner');
        if (arr.length === 0) { banner.style.display = 'none'; return; }
        banner.style.display = 'block';
        document.getElementById('draft-banner-text').textContent = `一時保存 ${arr.length}件あり`;
    }

    function applyDraft(draft) {
        Object.assign(state, draft.state || {});
        organizerBlocks = draft.organizerBlocks || [];
        explanationOrganizerBlocks = draft.explanationOrganizerBlocks || [];

        const m = draft.metadata || {};
        const fieldMap = { sourceBook:'save-source-book', chapter:'save-chapter', unit:'save-unit',
                           pageNumber:'save-page-number', problemNumber:'save-problem-number', strategy:'save-strategy' };
        Object.entries(fieldMap).forEach(([k, id]) => {
            const el = document.getElementById(id);
            if (el) el.value = m[k] || '';
        });

        if (state.problemMarkdown) {
            document.getElementById('problem-text-editor').value = state.problemMarkdown;
            renderProblemTextPreview();
        }
        if (state.matplotlibCode)
            document.getElementById('problem-diagram-code-editor').value = state.matplotlibCode;
        if (state.problemDiagramUrl)
            renderDiagramWithSliders(state.problemDiagramUrl, state.matplotlibCode);
        if (state.explanationMarkdown)
            document.getElementById('explanation-markdown-editor').value = state.explanationMarkdown;
        if (state.explanationMatplotlibCode)
            document.getElementById('explanation-diagram-code-editor').value = state.explanationMatplotlibCode;
        if (state.explanationDiagramUrl)
            document.getElementById('explanation-diagram-preview').innerHTML =
                `<img src="${state.explanationDiagramUrl}" style="border-radius:4px;max-width:100%;" alt="解説図">`;

        const probContainer = document.getElementById('problem-extra-diagrams');
        probContainer.innerHTML = '';
        (draft.extraProbDiags || []).forEach(d => {
            createExtraDiagramEntry('problem-extra-diagrams', 'linear-gradient(135deg,#10b981 0%,#047857 100%)', 'problem');
            const entry = probContainer.lastElementChild;
            if (!entry) return;
            const ta = entry.querySelector('textarea');
            if (ta) ta.value = d.code;
            if (d.imageUrl) {
                entry.dataset.imageUrl = d.imageUrl;
                entry.querySelector('.extra-diag-preview').innerHTML =
                    `<img src="${d.imageUrl}" style="border-radius:4px;" alt="図プレビュー">`;
            }
        });
        const expContainer = document.getElementById('explanation-extra-diagrams');
        expContainer.innerHTML = '';
        (draft.extraExpDiags || []).forEach(d => {
            createExtraDiagramEntry('explanation-extra-diagrams', 'linear-gradient(135deg,#f59e0b 0%,#d97706 100%)', 'explanation');
            const entry = expContainer.lastElementChild;
            if (!entry) return;
            const ta = entry.querySelector('textarea');
            if (ta) ta.value = d.code;
            if (d.imageUrl) {
                entry.dataset.imageUrl = d.imageUrl;
                entry.querySelector('.extra-diag-preview').innerHTML =
                    `<img src="${d.imageUrl}" style="border-radius:4px;" alt="図プレビュー">`;
            }
        });

        if (state.tags && state.tags.length) renderTagsPreview(state.tags);
        document.getElementById('draft-modal-overlay').style.display = 'none';
        navigateToStep(draft.step || 1);
        showToast(`「${draft.name}」を復元しました`);
    }

    function renderDraftList() {
        const arr = loadDrafts();
        const list = document.getElementById('draft-list');
        if (arr.length === 0) {
            list.innerHTML = '<p style="color:#94a3b8;font-size:0.85rem;text-align:center;padding:1rem 0;">一時保存はありません</p>';
            return;
        }
        list.innerHTML = arr.map(d => {
            const dt = new Date(d.savedAt);
            const dateStr = `${dt.getMonth()+1}/${dt.getDate()} ${String(dt.getHours()).padStart(2,'0')}:${String(dt.getMinutes()).padStart(2,'0')}`;
            return `
            <div style="border:1px solid #e2e8f0;border-radius:10px;padding:0.7rem 1rem;display:flex;align-items:center;justify-content:space-between;gap:0.75rem;background:#fafafa;">
                <div style="flex:1;min-width:0;">
                    <div style="font-size:0.88rem;font-weight:700;color:#0f172a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${d.name}</div>
                    <div style="font-size:0.75rem;color:#94a3b8;margin-top:0.15rem;">${dateStr} &nbsp;·&nbsp; ステップ${d.step}</div>
                </div>
                <div style="display:flex;gap:0.4rem;flex-shrink:0;">
                    <button data-id="${d.id}" class="draft-restore-btn btn btn-sm btn-primary" style="font-size:0.78rem;padding:0.2rem 0.6rem;">復元</button>
                    <button data-id="${d.id}" class="draft-delete-btn btn btn-sm btn-secondary" style="font-size:0.78rem;padding:0.2rem 0.6rem;color:#ef4444;border-color:#fca5a5;">削除</button>
                </div>
            </div>`;
        }).join('');
        list.querySelectorAll('.draft-restore-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const draft = loadDrafts().find(d => d.id === btn.dataset.id);
                if (draft) applyDraft(draft);
            });
        });
        list.querySelectorAll('.draft-delete-btn').forEach(btn => {
            btn.addEventListener('click', () => { deleteDraft(btn.dataset.id); });
        });
    }

    document.getElementById('btn-open-drafts').addEventListener('click', () => {
        renderDraftList();
        document.getElementById('draft-modal-overlay').style.display = 'flex';
    });
    document.getElementById('btn-close-draft-modal').addEventListener('click', () => {
        document.getElementById('draft-modal-overlay').style.display = 'none';
    });
    document.getElementById('draft-modal-overlay').addEventListener('click', e => {
        if (e.target === document.getElementById('draft-modal-overlay'))
            document.getElementById('draft-modal-overlay').style.display = 'none';
    });

    document.getElementById('btn-draft-step1').addEventListener('click', () => saveDraft(1));
    document.getElementById('btn-draft-step2').addEventListener('click', () => saveDraft(2));
    document.getElementById('btn-draft-step3').addEventListener('click', () => saveDraft(3));

    updateDraftBanner();

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
        if (stepNum === 3) {
            // **[N]** 問題番号を自動抽出してメタデータへ
            const numMatch = state.problemMarkdown.match(/^\s*\*\*\[([^\]]+)\]\*\*/);
            if (numMatch) {
                const numEl = document.getElementById('save-problem-number');
                if (!numEl.value) numEl.value = numMatch[1];
            }
            renderFinalPreview();
            if (state.tags && state.tags.length) renderTagsPreview(state.tags);
        }
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

    // 3D角度検出
    function extractViewInitAngles(code) {
        if (!code) return { elev: 30, azim: -60, has3d: false };
        const has3d = code.includes("projection='3d'") || code.includes('projection="3d"') || code.includes('view_init') || code.includes('Axes3D');
        let elev = 30, azim = -60, found = false;
        const elevMatch = code.match(/(?:^|\n|;)\s*elev\s*=\s*(-?\d+\.?\d*)/);
        const azimMatch = code.match(/(?:^|\n|;)\s*azim\s*=\s*(-?\d+\.?\d*)/);
        if (elevMatch) { elev = parseFloat(elevMatch[1]); found = true; }
        if (azimMatch) { azim = parseFloat(azimMatch[1]); found = true; }
        const match = code.match(/view_init\s*\(\s*([^)]+)\)/);
        if (match) {
            found = true;
            const args = match[1].split(',').map(s => s.trim());
            if (args.length >= 2) {
                let hasKeys = false;
                args.forEach(arg => {
                    if (arg.includes('elev=')) { const v = parseFloat(arg.split('=')[1]); if (!isNaN(v)) { elev = v; hasKeys = true; } }
                    else if (arg.includes('azim=')) { const v = parseFloat(arg.split('=')[1]); if (!isNaN(v)) { azim = v; hasKeys = true; } }
                });
                if (!hasKeys) {
                    const v0 = parseFloat(args[0]), v1 = parseFloat(args[1]);
                    if (!isNaN(v0)) elev = v0;
                    if (!isNaN(v1)) azim = v1;
                }
            }
        }
        return { elev, azim, has3d, found };
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

    // 3D回転スライダーのレンダリング
    function renderDiagramWithSliders(imageUrl, code) {
        const angles = extractViewInitAngles(code);
        const elev = state.problemDiagramElev;
        const azim  = state.problemDiagramAzim;

        let slidersHtml = '';
        if (angles.has3d) {
            slidersHtml = `
                <div id="diagram-rotation-controls" style="margin-top:0.75rem; padding:0.6rem 0.8rem; background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; font-size:0.78rem;">
                    <div style="font-weight:700; color:#4f46e5; margin-bottom:0.5rem; display:flex; align-items:center; gap:0.4rem;">
                        <i class="fa-solid fa-rotate"></i> 3D立体図の視点調整
                    </div>
                    <div style="display:flex; flex-direction:column; gap:0.4rem;">
                        <div style="display:flex; align-items:center; gap:0.6rem;">
                            <span style="width:40px; font-weight:600; color:#64748b;">上下:</span>
                            <input type="range" id="slider-elev" min="-90" max="90" value="${elev}" style="flex:1; accent-color:#4f46e5; height:5px; cursor:pointer;">
                            <span id="val-elev" style="width:30px; text-align:right; font-weight:700; color:#1e293b;">${elev}°</span>
                        </div>
                        <div style="display:flex; align-items:center; gap:0.6rem;">
                            <span style="width:40px; font-weight:600; color:#64748b;">左右:</span>
                            <input type="range" id="slider-azim" min="-180" max="180" value="${azim}" style="flex:1; accent-color:#4f46e5; height:5px; cursor:pointer;">
                            <span id="val-azim" style="width:30px; text-align:right; font-weight:700; color:#1e293b;">${azim}°</span>
                        </div>
                    </div>
                </div>
            `;
        }

        problemDiagramPreview.innerHTML = `
            <img src="${imageUrl}" style="max-width:100%;border-radius:4px;" alt="図形プレビュー">
            ${slidersHtml}
        `;

        if (angles.has3d) {
            const elevSlider = document.getElementById('slider-elev');
            const azimSlider  = document.getElementById('slider-azim');
            const elevVal    = document.getElementById('val-elev');
            const azimVal    = document.getElementById('val-azim');

            let rotateTimer = null;

            function triggerRotation() {
                clearTimeout(rotateTimer);
                rotateTimer = setTimeout(() => {
                    const newElev = parseInt(elevSlider.value);
                    const newAzim  = parseInt(azimSlider.value);
                    state.problemDiagramElev = newElev;
                    state.problemDiagramAzim  = newAzim;

                    loadingText.textContent = '図形を回転中...';
                    loadingOverlay.style.display = 'flex';

                    fetch('/api/diagram/run', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            code: state.matplotlibCode,
                            session_id: state.session_id,
                            suffix: 'problem',
                            elev: newElev,
                            azim: newAzim
                        })
                    })
                    .then(r => r.json())
                    .then(data => {
                        loadingOverlay.style.display = 'none';
                        if (data.success && data.image_b64) {
                            state.problemDiagramUrl = 'data:image/png;base64,' + data.image_b64;
                            renderDiagramWithSliders(state.problemDiagramUrl, state.matplotlibCode);
                            if (state.problemTextScanned) renderProblemTextPreview();
                        } else {
                            showToast(`回転エラー: ${data.error}`, 'error');
                        }
                    })
                    .catch(() => {
                        loadingOverlay.style.display = 'none';
                        showToast('回転中にエラーが発生しました。', 'error');
                    });
                }, 400);
            }

            elevSlider.addEventListener('input', () => { elevVal.textContent = `${elevSlider.value}°`; });
            azimSlider.addEventListener('input',  () => { azimVal.textContent  = `${azimSlider.value}°`; });
            elevSlider.addEventListener('change', triggerRotation);
            azimSlider.addEventListener('change',  triggerRotation);
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
                if (data.error && !data.image_b64 && !data.matplotlib_code) {
                    showToast(`エラー: ${data.error}`, 'error');
                    return;
                }
                state.matplotlibCode    = data.matplotlib_code || '';
                state.problemDiagramUrl = data.image_b64 ? 'data:image/png;base64,' + data.image_b64 : '';
                state.problemDiagramScanned = true;
                const angles = extractViewInitAngles(state.matplotlibCode);
                state.problemDiagramElev = angles.elev;
                state.problemDiagramAzim  = angles.azim;
                document.getElementById('problem-diagram-code-editor').value = state.matplotlibCode;

                if (state.problemDiagramUrl) {
                    renderDiagramWithSliders(state.problemDiagramUrl, state.matplotlibCode);
                } else if (data.error) {
                    problemDiagramPreview.innerHTML = `<div style="color:#ef4444;font-size:0.85rem;padding:0.5rem;">${data.error}</div>`;
                }

                if (state.problemTextScanned) renderProblemTextPreview();
                updateCostDisplay(data.token_usage, model, 'problem-cost-display');
                maybeShowNextButton();
                showToast('図形の再現が完了しました！');
            })
            .catch(() => {
                loadingOverlay.style.display = 'none';
                showToast('OCRサーバーへの接続エラーが発生しました。', 'error');
            });
    });

    // テキストブロック 入力欄トグル
    const problemTextEditorPane = document.getElementById('problem-text-editor-pane');
    const btnEditTextBlock      = document.getElementById('btn-edit-text-block');

    btnEditTextBlock.addEventListener('click', () => {
        const visible = problemTextEditorPane.style.display !== 'none';
        problemTextEditorPane.style.display = visible ? 'none' : 'flex';
        btnEditTextBlock.innerHTML = visible
            ? '<i class="fa-solid fa-pen"></i> 入力欄を表示'
            : '<i class="fa-solid fa-eye-slash"></i> 入力欄を隠す';
    });

    // ブロックJSON → Markdown 変換（editor.js 形式の貼り付け対応）
    function blocksJsonToMarkdown(raw) {
        try {
            const blocks = JSON.parse(raw.trim());
            if (!Array.isArray(blocks)) return null;
            return blocks.map(b => {
                if (!b || !b.type) return '';
                if (b.type === 'section') return `**[${b.content}]**`;
                if (b.type === 'shape')   return '![図](problem_diagram.png)';
                if (b.type === 'formula') return b.content || '';
                return b.content || '';
            }).filter(s => s).join('\n\n');
        } catch { return null; }
    }

    document.getElementById('btn-sync-text-preview').addEventListener('click', () => {
        const editor = document.getElementById('problem-text-editor');
        const parsed = blocksJsonToMarkdown(editor.value);
        if (parsed !== null) {
            editor.value = parsed;          // テキストエリアもMarkdownに置換
        }
        state.problemMarkdown = editor.value;
        state.problemTextScanned = true;
        renderProblemTextPreview();
        maybeShowNextButton();
        showToast('テキストをプレビューに反映しました。');
    });

    // 図形ブロック 入力欄トグル
    const problemDiagramEditorPane = document.getElementById('problem-diagram-editor-pane');
    const btnEditDiagramBlock      = document.getElementById('btn-edit-diagram-block');

    btnEditDiagramBlock.addEventListener('click', () => {
        const visible = problemDiagramEditorPane.style.display !== 'none';
        problemDiagramEditorPane.style.display = visible ? 'none' : 'flex';
        btnEditDiagramBlock.innerHTML = visible
            ? '<i class="fa-solid fa-code"></i> 入力欄を表示'
            : '<i class="fa-solid fa-eye-slash"></i> 入力欄を隠す';
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
            if (data.success && data.image_b64) {
                state.matplotlibCode    = code;
                state.problemDiagramUrl = 'data:image/png;base64,' + data.image_b64;
                state.problemDiagramScanned = true;
                renderDiagramWithSliders(state.problemDiagramUrl, state.matplotlibCode);
                if (state.problemTextScanned) renderProblemTextPreview();
                maybeShowNextButton();
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
    // STEP 1 — Block Organizer（パーツ分割・並べ替え）
    // =========================================================
    let organizerBlocks = [];

    function stripMarkers(str) {
        return str
            .replace(/\*\*\[[^\]]*\]\*\*/g, '')  // **[...]** 完全マーカー除去
            .replace(/\]\*\*/g, '')               // ]** 残骸除去
            .replace(/\*\*\[[^\]]*$/gm, '')       // **[... 閉じられていない残骸除去
            .replace(/\n{3,}/g, '\n\n')
            .trim();
    }

    function autoSplitToBlocks() {
        const text = stripMarkers(state.problemMarkdown);
        if (!text) { showToast('まず問題文を入力して「プレビューに反映」してください。', 'error'); return; }

        const blocks = [];
        const parts = text.split(/(?=[\(（][0-9１-９一二三四五六七八九][）\)])/);
        parts.forEach((part, i) => {
            const trimmed = part.trim();
            if (!trimmed) return;
            const numMatch = trimmed.match(/^[\(（]([0-9１-９一二三四五六七八九])[）\)]/);
            const label = i === 0 ? '全体文' : (numMatch ? `(${numMatch[1]})` : `ブロック${i}`);
            blocks.push({ id: `blk-${Date.now()}-${i}`, type: 'text', label, content: trimmed });
        });

        // メイン図 + 追加図を全て収集（URL＋Matplotlibコード）
        const diagData = [];
        if (state.problemDiagramUrl) {
            diagData.push({ url: state.problemDiagramUrl, code: state.matplotlibCode || '' });
        }
        document.querySelectorAll('#problem-extra-diagrams > [data-image-url]').forEach(el => {
            if (el.dataset.imageUrl) {
                const ta = el.querySelector('textarea');
                diagData.push({ url: el.dataset.imageUrl, code: ta ? ta.value.trim() : '' });
            }
        });

        diagData.forEach(({ url, code }, di) => {
            const label = diagData.length === 1 ? '図形' : `図${di + 1}`;
            blocks.splice(Math.min(1 + di, blocks.length), 0, {
                id: `blk-diagram-${Date.now()}-${di}`,
                type: 'diagram',
                label,
                content: `![${label}](${url})`,
                matplotlib_code: code
            });
        });

        organizerBlocks = blocks;
        renderOrganizerBlocks();
        document.getElementById('organizer-confirm-row').style.display = 'block';
    }

    function renderOrganizerBlocks() {
        const list = document.getElementById('organizer-blocks-list');

        if (!organizerBlocks.length) {
            list.innerHTML = '<p style="color:#94a3b8;font-size:0.8rem;text-align:center;margin:0.25rem 0;">「自動分割」を押すと (1)(2) などでブロック分割されます。</p>';
            document.getElementById('organizer-confirm-row').style.display = 'none';
            return;
        }

        list.innerHTML = '';

        // sections: [{textIdx, textBlock, diagrams:[{block,idx}]}, ...]
        const sections = [];
        organizerBlocks.forEach((block, idx) => {
            if (block.type === 'text') {
                sections.push({ textIdx: idx, textBlock: block, diagrams: [] });
            } else if (block.type === 'diagram' && sections.length > 0) {
                sections[sections.length - 1].diagrams.push({ block, idx });
            }
        });

        // セクション単位でスワップ（テキスト＋配下の図を一括移動）
        function swapAdjacentSections(si) {
            if (si < 0 || si >= sections.length - 1) return;
            const s1Start = sections[si].textIdx;
            const s2Start = sections[si + 1].textIdx;
            const s2End   = si + 2 < sections.length ? sections[si + 2].textIdx : organizerBlocks.length;
            const s1Blocks = organizerBlocks.slice(s1Start, s2Start);
            const s2Blocks = organizerBlocks.slice(s2Start, s2End);
            organizerBlocks = [
                ...organizerBlocks.slice(0, s1Start),
                ...s2Blocks,
                ...s1Blocks,
                ...organizerBlocks.slice(s2End)
            ];
        }

        const baseBtn = 'padding:0 0.3rem;font-size:0.72rem;border:1px solid #e2e8f0;border-radius:3px;background:#f8fafc;line-height:1.4;';
        const dBase   = 'padding:0 0.25rem;font-size:0.68rem;border:1px solid #a7f3d0;border-radius:2px;background:#f0fdf4;line-height:1.4;';

        sections.forEach((section, si) => {
            const isFirst    = si === 0;
            const borderColor = isFirst ? '#c7d2fe' : '#bae6fd';
            const badgeColor  = isFirst ? '#6366f1' : '#0284c7';
            const sUpDis = si === 0;
            const sDnDis = si === sections.length - 1;

            const box = document.createElement('div');
            box.style.cssText = `border:2px solid ${borderColor};border-radius:8px;padding:0.4rem 0.5rem;margin-bottom:0.4rem;`;

            // テキストブロック行
            // バッジと重複する先頭の (N) / （N） を除去してから本文プレビューを生成
            const previewRaw = section.textBlock.content
                .replace(/^[\(（][0-9１-９一二三四五六七八九][）\)]\s*/, '')
                .replace(/\$[^$]+\$/g, '[数式]').replace(/\n/g, ' ');
            const preview = previewRaw.substring(0, 55) + (previewRaw.length > 55 ? '…' : '');
            const textRow = document.createElement('div');
            textRow.style.cssText = 'display:flex;align-items:center;gap:0.4rem;';
            textRow.innerHTML = `
                <div style="display:flex;flex-direction:column;gap:1px;flex-shrink:0;">
                    <button class="s-up" data-si="${si}" ${sUpDis ? 'disabled' : ''} style="${baseBtn}${sUpDis ? 'opacity:0.4;cursor:not-allowed;' : 'cursor:pointer;'}">▲</button>
                    <button class="s-dn" data-si="${si}" ${sDnDis ? 'disabled' : ''} style="${baseBtn}${sDnDis ? 'opacity:0.4;cursor:not-allowed;' : 'cursor:pointer;'}">▼</button>
                </div>
                <span style="font-size:0.72rem;font-weight:700;padding:0.1rem 0.35rem;border-radius:4px;flex-shrink:0;background:${badgeColor};color:white;">${section.textBlock.label}</span>
                <div style="flex:1;font-size:0.78rem;color:#475569;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">${preview}</div>
                <div style="display:flex;gap:0.2rem;flex-shrink:0;">
                    ${si > 0 ? `<button class="s-mrg" data-si="${si}" style="padding:0.15rem 0.3rem;font-size:0.72rem;border:1px solid #94a3b8;border-radius:3px;cursor:pointer;background:#f1f5f9;color:#475569;" title="上のパートに結合">↑結合</button>` : ''}
                    <button class="s-del" data-si="${si}" style="padding:0.15rem 0.3rem;font-size:0.72rem;border:1px solid #fca5a5;border-radius:3px;cursor:pointer;background:#fff5f5;color:#ef4444;">✕</button>
                </div>
            `;
            box.appendChild(textRow);

            // 図ブロック（インデント表示）
            section.diagrams.forEach(({ block: dBlock, idx: dIdx }) => {
                const dUpDis = dIdx === 0;
                const dDnDis = dIdx === organizerBlocks.length - 1;
                const diagRow = document.createElement('div');
                diagRow.style.cssText = 'margin-top:0.25rem;margin-left:1.5rem;display:flex;align-items:center;gap:0.3rem;padding:0.2rem 0.4rem;background:rgba(16,185,129,0.06);border:1px solid #a7f3d0;border-radius:5px;';
                diagRow.innerHTML = `
                    <div style="display:flex;flex-direction:column;gap:1px;flex-shrink:0;">
                        <button class="d-up" data-idx="${dIdx}" ${dUpDis ? 'disabled' : ''} style="${dBase}${dUpDis ? 'opacity:0.4;cursor:not-allowed;' : 'cursor:pointer;'}">▲</button>
                        <button class="d-dn" data-idx="${dIdx}" ${dDnDis ? 'disabled' : ''} style="${dBase}${dDnDis ? 'opacity:0.4;cursor:not-allowed;' : 'cursor:pointer;'}">▼</button>
                    </div>
                    <span style="font-size:0.68rem;font-weight:700;padding:0.1rem 0.3rem;border-radius:3px;background:#10b981;color:white;flex-shrink:0;">図</span>
                    <span style="font-size:0.76rem;color:#065f46;flex:1;">🖼 ${dBlock.label}</span>
                    <button class="d-del" data-idx="${dIdx}" style="padding:0.1rem 0.25rem;font-size:0.68rem;border:1px solid #fca5a5;border-radius:3px;cursor:pointer;background:#fff5f5;color:#ef4444;">✕</button>
                `;
                box.appendChild(diagRow);
            });

            list.appendChild(box);
        });

        // セクション ▲（前セクションと入れ替え）
        list.querySelectorAll('.s-up').forEach(btn => {
            btn.addEventListener('click', () => {
                const si = parseInt(btn.dataset.si);
                if (si > 0) { swapAdjacentSections(si - 1); renderOrganizerBlocks(); }
            });
        });
        // セクション ▼（次セクションと入れ替え）
        list.querySelectorAll('.s-dn').forEach(btn => {
            btn.addEventListener('click', () => {
                const si = parseInt(btn.dataset.si);
                if (si < sections.length - 1) { swapAdjacentSections(si); renderOrganizerBlocks(); }
            });
        });
        // セクション ↑結合（前セクションのテキストに統合）
        list.querySelectorAll('.s-mrg').forEach(btn => {
            btn.addEventListener('click', () => {
                const si = parseInt(btn.dataset.si);
                if (si <= 0) return;
                const prevTextIdx = sections[si - 1].textIdx;
                const thisTextIdx = sections[si].textIdx;
                organizerBlocks[prevTextIdx].content += '\n\n' + organizerBlocks[thisTextIdx].content;
                organizerBlocks.splice(thisTextIdx, 1);
                renderOrganizerBlocks();
            });
        });
        // セクション削除（テキスト＋配下の図を全て除去）
        list.querySelectorAll('.s-del').forEach(btn => {
            btn.addEventListener('click', () => {
                const si = parseInt(btn.dataset.si);
                const toRemove = new Set([sections[si].textIdx, ...sections[si].diagrams.map(d => d.idx)]);
                organizerBlocks = organizerBlocks.filter((_, i) => !toRemove.has(i));
                renderOrganizerBlocks();
            });
        });
        // 図 ▲/▼（フラット配列でスワップ → セクション境界をまたいで移動可）
        list.querySelectorAll('.d-up').forEach(btn => {
            btn.addEventListener('click', () => {
                const i = parseInt(btn.dataset.idx);
                if (i > 0) { [organizerBlocks[i-1], organizerBlocks[i]] = [organizerBlocks[i], organizerBlocks[i-1]]; renderOrganizerBlocks(); }
            });
        });
        list.querySelectorAll('.d-dn').forEach(btn => {
            btn.addEventListener('click', () => {
                const i = parseInt(btn.dataset.idx);
                if (i < organizerBlocks.length - 1) { [organizerBlocks[i], organizerBlocks[i+1]] = [organizerBlocks[i+1], organizerBlocks[i]]; renderOrganizerBlocks(); }
            });
        });
        list.querySelectorAll('.d-del').forEach(btn => {
            btn.addEventListener('click', () => {
                const i = parseInt(btn.dataset.idx);
                organizerBlocks.splice(i, 1);
                renderOrganizerBlocks();
            });
        });
    }

    document.getElementById('btn-auto-split').addEventListener('click', autoSplitToBlocks);

    document.getElementById('btn-confirm-blocks').addEventListener('click', () => {
        if (!organizerBlocks.length) { navigateToStep(2); return; }
        const finalMd = organizerBlocks.map(b => b.content).join('\n\n');
        state.problemMarkdown = finalMd;
        document.getElementById('problem-text-editor').value = finalMd;
        renderProblemTextPreview();
        navigateToStep(2);
    });

    // =========================================================
    // 追加図フィールド（問題・解説 共通）
    // =========================================================
    function createExtraDiagramEntry(containerId, accentColor, sessionSuffix) {
        const container = document.getElementById(containerId);
        const n = container.children.length + 2; // 1枚目は固定フィールドなので2から
        const uid = `${containerId}-entry-${Date.now()}`;

        const entry = document.createElement('div');
        entry.id = uid;
        entry.style.cssText = 'border:1px solid #e2e8f0;border-radius:6px;padding:0.6rem;background:#fafafa;display:flex;flex-direction:column;gap:0.4rem;';
        entry.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span class="extra-diag-label" style="font-size:0.8rem;font-weight:700;color:#475569;">図 ${n}</span>
                <button class="btn btn-secondary btn-sm btn-del-extra" style="font-size:0.75rem;padding:0.15rem 0.45rem;color:#ef4444;border-color:#fca5a5;">
                    <i class="fa-solid fa-trash"></i> 削除
                </button>
            </div>
            <div class="extra-diag-preview" style="min-height:50px;background:white;border-radius:4px;padding:0.35rem;overflow-x:auto;">
                <span style="color:#94a3b8;font-size:0.78rem;">コードを実行すると表示されます。</span>
            </div>
            <textarea class="extra-diag-code" placeholder="Pythonコードを貼り付けてください。" style="width:100%;min-height:120px;font-family:monospace;font-size:0.84rem;padding:0.45rem;border:1px solid #cbd5e1;border-radius:6px;box-sizing:border-box;"></textarea>
            <button class="btn btn-secondary btn-sm btn-run-extra" style="align-self:flex-end;background:${accentColor};color:white;border:none;font-size:0.8rem;">
                <i class="fa-solid fa-play"></i> コードを実行して描画
            </button>
        `;

        entry.querySelector('.btn-run-extra').addEventListener('click', () => {
            const code = entry.querySelector('.extra-diag-code').value.trim();
            if (!code) { showToast('コードが空です。', 'error'); return; }
            loadingText.textContent = '図形を描画中...';
            loadingOverlay.style.display = 'flex';
            fetch('/api/diagram/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code, session_id: state.session_id, suffix: `${sessionSuffix}_extra_${uid.slice(-6)}` })
            })
            .then(r => r.json())
            .then(data => {
                loadingOverlay.style.display = 'none';
                if (data.success && data.image_b64) {
                    const dataUri = 'data:image/png;base64,' + data.image_b64;
                    entry.dataset.imageUrl = dataUri;
                    entry.querySelector('.extra-diag-preview').innerHTML =
                        `<img src="${dataUri}" style="border-radius:4px;" alt="図プレビュー">`;
                    showToast('図を描画しました！');
                } else {
                    showToast(`描画エラー: ${data.error}`, 'error');
                }
            })
            .catch(() => { loadingOverlay.style.display = 'none'; showToast('描画中にエラーが発生しました。', 'error'); });
        });

        entry.querySelector('.btn-del-extra').addEventListener('click', () => {
            entry.remove();
            // 番号を振り直す
            Array.from(container.children).forEach((el, i) => {
                const lbl = el.querySelector('.extra-diag-label');
                if (lbl) lbl.textContent = `図 ${i + 2}`;
            });
        });

        container.appendChild(entry);
    }

    document.getElementById('btn-add-problem-diagram').addEventListener('click', () => {
        createExtraDiagramEntry('problem-extra-diagrams', 'linear-gradient(135deg,#10b981 0%,#047857 100%)', 'problem');
    });
    document.getElementById('btn-add-explanation-diagram').addEventListener('click', () => {
        createExtraDiagramEntry('explanation-extra-diagrams', 'linear-gradient(135deg,#f59e0b 0%,#d97706 100%)', 'explanation');
    });

    // =========================================================
    // STEP 2 — Explanation Block Organizer
    // =========================================================
    let explanationOrganizerBlocks = [];

    function autoSplitExplanationToBlocks() {
        const text = stripMarkers(state.explanationMarkdown);
        if (!text) { showToast('まず解説文を入力して「プレビューに反映」してください。', 'error'); return; }

        const blocks = [];
        const parts = text.split(/(?=[\(（][0-9１-９一二三四五六七八九][）\)])/);
        parts.forEach((part, i) => {
            const trimmed = part.trim();
            if (!trimmed) return;
            const numMatch = trimmed.match(/^[\(（]([0-9１-９一二三四五六七八九])[）\)]/);
            const label = i === 0 ? '全体文' : (numMatch ? `(${numMatch[1]})` : `ブロック${i}`);
            blocks.push({ id: `exp-blk-${Date.now()}-${i}`, type: 'text', label, content: trimmed });
        });

        // メイン解説図 + 追加図を全て収集（URL＋Matplotlibコード）
        const expDiagData = [];
        if (state.explanationDiagramUrl) {
            expDiagData.push({ url: state.explanationDiagramUrl, code: state.explanationMatplotlibCode || '' });
        }
        document.querySelectorAll('#explanation-extra-diagrams > [data-image-url]').forEach(el => {
            if (el.dataset.imageUrl) {
                const ta = el.querySelector('textarea');
                expDiagData.push({ url: el.dataset.imageUrl, code: ta ? ta.value.trim() : '' });
            }
        });
        expDiagData.forEach(({ url, code }, di) => {
            const label = expDiagData.length === 1 ? '解説図' : `解説図${di + 1}`;
            blocks.splice(Math.min(1 + di, blocks.length), 0, {
                id: `exp-blk-diagram-${Date.now()}-${di}`,
                type: 'diagram',
                label,
                content: `![${label}](${url})`,
                matplotlib_code: code
            });
        });

        explanationOrganizerBlocks = blocks;
        renderExplanationOrganizerBlocks();
        document.getElementById('exp-organizer-confirm-row').style.display = 'block';
    }

    function renderExplanationOrganizerBlocks() {
        const list = document.getElementById('exp-organizer-blocks-list');

        if (!explanationOrganizerBlocks.length) {
            list.innerHTML = '<p style="color:#94a3b8;font-size:0.8rem;text-align:center;margin:0.25rem 0;">「自動分割」を押すと (1)(2) などでブロック分割されます。</p>';
            document.getElementById('exp-organizer-confirm-row').style.display = 'none';
            return;
        }

        list.innerHTML = '';

        // sections: [{textIdx, textBlock, diagrams:[{block,idx}]}, ...]
        const sections = [];
        explanationOrganizerBlocks.forEach((block, idx) => {
            if (block.type === 'text') {
                sections.push({ textIdx: idx, textBlock: block, diagrams: [] });
            } else if (block.type === 'diagram' && sections.length > 0) {
                sections[sections.length - 1].diagrams.push({ block, idx });
            }
        });

        function swapAdjacentSections(si) {
            if (si < 0 || si >= sections.length - 1) return;
            const s1Start = sections[si].textIdx;
            const s2Start = sections[si + 1].textIdx;
            const s2End   = si + 2 < sections.length ? sections[si + 2].textIdx : explanationOrganizerBlocks.length;
            const s1Blocks = explanationOrganizerBlocks.slice(s1Start, s2Start);
            const s2Blocks = explanationOrganizerBlocks.slice(s2Start, s2End);
            explanationOrganizerBlocks = [
                ...explanationOrganizerBlocks.slice(0, s1Start),
                ...s2Blocks,
                ...s1Blocks,
                ...explanationOrganizerBlocks.slice(s2End)
            ];
        }

        const baseBtn = 'padding:0 0.3rem;font-size:0.72rem;border:1px solid #e2e8f0;border-radius:3px;background:#f8fafc;line-height:1.4;';
        const dBase   = 'padding:0 0.25rem;font-size:0.68rem;border:1px solid #fde68a;border-radius:2px;background:#fffbeb;line-height:1.4;';

        sections.forEach((section, si) => {
            const isFirst    = si === 0;
            const borderColor = isFirst ? '#fed7aa' : '#fde68a';
            const badgeColor  = isFirst ? '#d97706' : '#b45309';
            const sUpDis = si === 0;
            const sDnDis = si === sections.length - 1;

            const box = document.createElement('div');
            box.style.cssText = `border:2px solid ${borderColor};border-radius:8px;padding:0.4rem 0.5rem;margin-bottom:0.4rem;`;

            const previewRaw = section.textBlock.content
                .replace(/^[\(（][0-9１-９一二三四五六七八九][）\)]\s*/, '')
                .replace(/\$[^$]+\$/g, '[数式]').replace(/\n/g, ' ');
            const preview = previewRaw.substring(0, 55) + (previewRaw.length > 55 ? '…' : '');
            const textRow = document.createElement('div');
            textRow.style.cssText = 'display:flex;align-items:center;gap:0.4rem;';
            textRow.innerHTML = `
                <div style="display:flex;flex-direction:column;gap:1px;flex-shrink:0;">
                    <button class="es-up" data-si="${si}" ${sUpDis ? 'disabled' : ''} style="${baseBtn}${sUpDis ? 'opacity:0.4;cursor:not-allowed;' : 'cursor:pointer;'}">▲</button>
                    <button class="es-dn" data-si="${si}" ${sDnDis ? 'disabled' : ''} style="${baseBtn}${sDnDis ? 'opacity:0.4;cursor:not-allowed;' : 'cursor:pointer;'}">▼</button>
                </div>
                <span style="font-size:0.72rem;font-weight:700;padding:0.1rem 0.35rem;border-radius:4px;flex-shrink:0;background:${badgeColor};color:white;">${section.textBlock.label}</span>
                <div style="flex:1;font-size:0.78rem;color:#475569;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">${preview}</div>
                <div style="display:flex;gap:0.2rem;flex-shrink:0;">
                    ${si > 0 ? `<button class="es-mrg" data-si="${si}" style="padding:0.15rem 0.3rem;font-size:0.72rem;border:1px solid #94a3b8;border-radius:3px;cursor:pointer;background:#f1f5f9;color:#475569;" title="上のパートに結合">↑結合</button>` : ''}
                    <button class="es-del" data-si="${si}" style="padding:0.15rem 0.3rem;font-size:0.72rem;border:1px solid #fca5a5;border-radius:3px;cursor:pointer;background:#fff5f5;color:#ef4444;">✕</button>
                </div>
            `;
            box.appendChild(textRow);

            section.diagrams.forEach(({ block: dBlock, idx: dIdx }) => {
                const dUpDis = dIdx === 0;
                const dDnDis = dIdx === explanationOrganizerBlocks.length - 1;
                const diagRow = document.createElement('div');
                diagRow.style.cssText = 'margin-top:0.25rem;margin-left:1.5rem;display:flex;align-items:center;gap:0.3rem;padding:0.2rem 0.4rem;background:rgba(245,158,11,0.06);border:1px solid #fde68a;border-radius:5px;';
                diagRow.innerHTML = `
                    <div style="display:flex;flex-direction:column;gap:1px;flex-shrink:0;">
                        <button class="ed-up" data-idx="${dIdx}" ${dUpDis ? 'disabled' : ''} style="${dBase}${dUpDis ? 'opacity:0.4;cursor:not-allowed;' : 'cursor:pointer;'}">▲</button>
                        <button class="ed-dn" data-idx="${dIdx}" ${dDnDis ? 'disabled' : ''} style="${dBase}${dDnDis ? 'opacity:0.4;cursor:not-allowed;' : 'cursor:pointer;'}">▼</button>
                    </div>
                    <span style="font-size:0.68rem;font-weight:700;padding:0.1rem 0.3rem;border-radius:3px;background:#f59e0b;color:white;flex-shrink:0;">図</span>
                    <span style="font-size:0.76rem;color:#92400e;flex:1;">🖼 ${dBlock.label}</span>
                    <button class="ed-del" data-idx="${dIdx}" style="padding:0.1rem 0.25rem;font-size:0.68rem;border:1px solid #fca5a5;border-radius:3px;cursor:pointer;background:#fff5f5;color:#ef4444;">✕</button>
                `;
                box.appendChild(diagRow);
            });

            list.appendChild(box);
        });

        list.querySelectorAll('.es-up').forEach(btn => {
            btn.addEventListener('click', () => {
                const si = parseInt(btn.dataset.si);
                if (si > 0) { swapAdjacentSections(si - 1); renderExplanationOrganizerBlocks(); }
            });
        });
        list.querySelectorAll('.es-dn').forEach(btn => {
            btn.addEventListener('click', () => {
                const si = parseInt(btn.dataset.si);
                if (si < sections.length - 1) { swapAdjacentSections(si); renderExplanationOrganizerBlocks(); }
            });
        });
        list.querySelectorAll('.es-mrg').forEach(btn => {
            btn.addEventListener('click', () => {
                const si = parseInt(btn.dataset.si);
                if (si <= 0) return;
                const prevTextIdx = sections[si - 1].textIdx;
                const thisTextIdx = sections[si].textIdx;
                explanationOrganizerBlocks[prevTextIdx].content += '\n\n' + explanationOrganizerBlocks[thisTextIdx].content;
                explanationOrganizerBlocks.splice(thisTextIdx, 1);
                renderExplanationOrganizerBlocks();
            });
        });
        list.querySelectorAll('.es-del').forEach(btn => {
            btn.addEventListener('click', () => {
                const si = parseInt(btn.dataset.si);
                const toRemove = new Set([sections[si].textIdx, ...sections[si].diagrams.map(d => d.idx)]);
                explanationOrganizerBlocks = explanationOrganizerBlocks.filter((_, i) => !toRemove.has(i));
                renderExplanationOrganizerBlocks();
            });
        });
        list.querySelectorAll('.ed-up').forEach(btn => {
            btn.addEventListener('click', () => {
                const i = parseInt(btn.dataset.idx);
                if (i > 0) { [explanationOrganizerBlocks[i-1], explanationOrganizerBlocks[i]] = [explanationOrganizerBlocks[i], explanationOrganizerBlocks[i-1]]; renderExplanationOrganizerBlocks(); }
            });
        });
        list.querySelectorAll('.ed-dn').forEach(btn => {
            btn.addEventListener('click', () => {
                const i = parseInt(btn.dataset.idx);
                if (i < explanationOrganizerBlocks.length - 1) { [explanationOrganizerBlocks[i], explanationOrganizerBlocks[i+1]] = [explanationOrganizerBlocks[i+1], explanationOrganizerBlocks[i]]; renderExplanationOrganizerBlocks(); }
            });
        });
        list.querySelectorAll('.ed-del').forEach(btn => {
            btn.addEventListener('click', () => {
                const i = parseInt(btn.dataset.idx);
                explanationOrganizerBlocks.splice(i, 1);
                renderExplanationOrganizerBlocks();
            });
        });
    }

    document.getElementById('btn-exp-auto-split').addEventListener('click', autoSplitExplanationToBlocks);

    document.getElementById('btn-exp-confirm-blocks').addEventListener('click', () => {
        if (!explanationOrganizerBlocks.length) { navigateToStep(3); return; }
        const finalMd = explanationOrganizerBlocks.map(b => b.content).join('\n\n');
        state.explanationMarkdown = finalMd;
        document.getElementById('explanation-markdown-editor').value = finalMd;
        renderExplanationPreview();
        navigateToStep(3);
    });

    // =========================================================
    // STEP 2 — Explanation OCR
    // =========================================================
    const explanationPreviewBody      = document.getElementById('explanation-preview-body');
    const explanationEditorPane       = document.getElementById('explanation-editor-pane');
    const explanationMarkdownEditor   = document.getElementById('explanation-markdown-editor');
    const btnEditExplanation          = document.getElementById('btn-edit-explanation');
    const btnSyncExplanationPreview   = document.getElementById('btn-sync-explanation-preview');
    const explanationActionsNext      = document.getElementById('explanation-actions-next');
    const explanationDiagramPreview   = document.getElementById('explanation-diagram-preview');
    const explanationDiagramEditorPane = document.getElementById('explanation-diagram-editor-pane');
    const btnEditExplanationDiagram   = document.getElementById('btn-edit-explanation-diagram');

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

                // 解説図コード・画像
                if (data.matplotlib_code) {
                    state.explanationMatplotlibCode = data.matplotlib_code;
                    document.getElementById('explanation-diagram-code-editor').value = data.matplotlib_code;
                }
                if (data.image_b64) {
                    state.explanationDiagramUrl = 'data:image/png;base64,' + data.image_b64;
                    explanationDiagramPreview.innerHTML = `<img src="${state.explanationDiagramUrl}" style="border-radius:4px;" alt="解説図プレビュー">`;
                }

                renderExplanationPreview();
                updateCostDisplay(data.token_usage, model, 'explanation-cost-display');
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
        const md = state.explanationDiagramUrl
            ? state.explanationMarkdown.replace('explanation_diagram.png', state.explanationDiagramUrl)
            : state.explanationMarkdown;
        let html = '';
        if (state.strategySummary) {
            html += `<div class="strategy-box"><strong>解法の核心（コア戦略）:</strong> ${state.strategySummary}</div>`;
        }
        html += `<div class="markdown-body">${md}</div>`;
        if (state.tags && state.tags.length) {
            html += `<div style="margin-top:1rem;display:flex;gap:0.5rem;flex-wrap:wrap;">${state.tags.map(t => `<span class="badge badge-category">#${t}</span>`).join('')}</div>`;
        }
        explanationPreviewBody.innerHTML = html || '<span style="color:#94a3b8;">ここにプレビューが表示されます。</span>';
        if (window.MathJax && window.MathJax.typesetPromise) {
            window.MathJax.typesetPromise([explanationPreviewBody]);
        }
    }

    // 解説テキスト 入力欄トグル
    btnEditExplanation.addEventListener('click', () => {
        const visible = explanationEditorPane.style.display !== 'none';
        explanationEditorPane.style.display = visible ? 'none' : 'flex';
        btnEditExplanation.innerHTML = visible
            ? '<i class="fa-solid fa-pen"></i> 入力欄を表示'
            : '<i class="fa-solid fa-eye-slash"></i> 入力欄を隠す';
    });

    // 解説テキスト プレビューに反映
    btnSyncExplanationPreview.addEventListener('click', () => {
        const parsed = blocksJsonToMarkdown(explanationMarkdownEditor.value);
        if (parsed !== null) {
            explanationMarkdownEditor.value = parsed;
        }
        state.explanationMarkdown = explanationMarkdownEditor.value;
        renderExplanationPreview();
        explanationActionsNext.style.display = 'block';
        showToast('解説テキストをプレビューに反映しました。');
    });

    // 解説図 入力欄トグル
    btnEditExplanationDiagram.addEventListener('click', () => {
        const visible = explanationDiagramEditorPane.style.display !== 'none';
        explanationDiagramEditorPane.style.display = visible ? 'none' : 'flex';
        btnEditExplanationDiagram.innerHTML = visible
            ? '<i class="fa-solid fa-code"></i> 入力欄を表示'
            : '<i class="fa-solid fa-eye-slash"></i> 入力欄を隠す';
    });

    // 解説図 コードを実行して描画
    document.getElementById('btn-sync-explanation-diagram-preview').addEventListener('click', () => {
        const code = document.getElementById('explanation-diagram-code-editor').value.trim();
        if (!code) { showToast('コードが空です。', 'error'); return; }

        loadingText.textContent = '解説図を描画中...';
        loadingOverlay.style.display = 'flex';

        fetch('/api/diagram/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code, session_id: state.session_id, suffix: 'explanation' })
        })
        .then(res => res.json())
        .then(data => {
            loadingOverlay.style.display = 'none';
            if (data.success && data.image_b64) {
                state.explanationMatplotlibCode = code;
                state.explanationDiagramUrl = 'data:image/png;base64,' + data.image_b64;
                explanationDiagramPreview.innerHTML = `<img src="${state.explanationDiagramUrl}" style="border-radius:4px;" alt="解説図プレビュー">`;
                if (state.explanationMarkdown) renderExplanationPreview();
                explanationActionsNext.style.display = 'block';
                showToast('解説図を描画しました！');
            } else {
                showToast(`描画エラー: ${data.error}`, 'error');
            }
        })
        .catch(() => {
            loadingOverlay.style.display = 'none';
            showToast('解説図描画中にエラーが発生しました。', 'error');
        });
    });

    document.getElementById('btn-step2-back').addEventListener('click', () => navigateToStep(1));
    document.getElementById('btn-step2-next').addEventListener('click', () => navigateToStep(3));

    // =========================================================
    // STEP 3 — Metadata & Save
    // =========================================================
    const finalPreviewContainer = document.getElementById('final-preview-container');

    // Markdown → HTML 変換（数式を保護しつつ画像・太字・段落を処理）
    function mdToHtml(md) {
        if (!md) return '';
        const math = [];
        let s = md
            .replace(/\$\$[\s\S]+?\$\$/g, m => { math.push(m); return `§M${math.length - 1}§`; })
            .replace(/\$[^$\n]+?\$/g,     m => { math.push(m); return `§M${math.length - 1}§`; });
        s = s
            .replace(/!\[([^\]]*)\]\(([^)]+)\)/g,
                '<img src="$2" alt="$1" style="display:block;margin:0.5rem 0;border-radius:4px;">')
            .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\*([^*\n]+)\*/g,     '<em>$1</em>')
            .replace(/\n\n+/g, '<br><br>')
            .replace(/\n/g, '<br>');
        math.forEach((m, i) => { s = s.replaceAll(`§M${i}§`, m); });
        return s;
    }

    // Drive MD と同じ内容をクライアントで生成
    function generateMdContent() {
        const sourceBook = document.getElementById('save-source-book').value || '';
        const chapter    = document.getElementById('save-chapter').value    || '';
        const unit       = document.getElementById('save-unit').value       || '未設定';
        const pageNum    = document.getElementById('save-page-number').value || '';
        const problemNum = document.getElementById('save-problem-number').value || '';
        const strategy   = document.getElementById('save-strategy').value   || '';
        const imgRe      = /!\[[^\]]*\]\([^)]*\)/g;
        const markerRe   = /\*\*\[[^\]]*\]\*\*/g;

        function blocksToMd(blocks, flatMd, flatCode) {
            const secs = [];
            (blocks || []).forEach((block, idx) => {
                if (block.type === 'text') secs.push({ block, diagrams: [] });
                else if (block.type === 'diagram' && secs.length > 0) secs[secs.length - 1].diagrams.push(block);
            });
            if (secs.length) {
                const chunks = [];
                secs.forEach(sec => {
                    const text = (sec.block.content || '').replace(markerRe, '').replace(imgRe, '').trim();
                    if (text) chunks.push(text);
                    sec.diagrams.forEach(d => {
                        const code = (d.matplotlib_code || '').trim();
                        if (code) chunks.push(`\`\`\`python\n# ${d.label}\n${code}\n\`\`\``);
                    });
                });
                return chunks.join('\n\n');
            }
            let base = (flatMd || '').replace(markerRe, '').replace(imgRe, '').trim();
            if (flatCode) base += `\n\n\`\`\`python\n${flatCode.trim()}\n\`\`\``;
            return base;
        }

        const chapterStr = chapter ? ` ${chapter}` : '';
        const numStr     = problemNum ? ` 問${problemNum}` : '';
        const probMd     = blocksToMd(organizerBlocks, state.problemMarkdown, state.matplotlibCode);
        const expMd      = blocksToMd(explanationOrganizerBlocks, state.explanationMarkdown, state.explanationMatplotlibCode);

        return `# ${sourceBook}${chapterStr}${numStr}
- 単元: ${unit}
- ページ: ${pageNum || 'なし'}
- 解法の核心: ${strategy || 'なし'}

## 問題
${probMd}

## 解説
${expMd}`;
    }

    // MD ダウンロード
    document.getElementById('btn-download-md').addEventListener('click', () => {
        const md         = generateMdContent();
        const sourceBook = document.getElementById('save-source-book').value || '問題';
        const chapter    = document.getElementById('save-chapter').value || '';
        const now        = new Date();
        const dateStr    = `${now.getFullYear()}${String(now.getMonth()+1).padStart(2,'0')}${String(now.getDate()).padStart(2,'0')}`;
        const safeName   = `${sourceBook}${chapter ? '_' + chapter : ''}`.replace(/[\\/:*?"<>|]/g, '_');
        const filename   = `${dateStr}_${safeName}.md`;
        const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
        const url  = URL.createObjectURL(blob);
        const a    = document.createElement('a');
        a.href = url; a.download = filename; a.click();
        URL.revokeObjectURL(url);
    });

    // タグ読み込み
    function renderTagsPreview(tags) {
        const container = document.getElementById('tags-preview');
        if (!tags.length) { container.innerHTML = '<span style="font-size:0.75rem;color:var(--text-muted);">タグなし</span>'; return; }
        container.innerHTML = tags.map(t =>
            `<span style="display:inline-block;background:#e0e7ff;color:#4f46e5;padding:0.15rem 0.55rem;border-radius:9999px;font-size:0.75rem;margin:0.15rem;font-weight:600;">${t}</span>`
        ).join('');
    }

    document.getElementById('btn-load-tags').addEventListener('click', () => {
        const raw = document.getElementById('tags-paste-area').value.trim();
        if (!raw) { showToast('タグを貼り付けてください', 'error'); return; }

        const lines = raw.split('\n').map(l => l.trim()).filter(Boolean);

        // 1行目が「難易度: X.X」なら分離
        let difficulty = null;
        let tagLines   = lines;
        const diffMatch = lines[0] && lines[0].match(/^難易度[:：]\s*(\d+\.?\d*)/);
        if (diffMatch) {
            difficulty = parseFloat(diffMatch[1]);
            tagLines   = lines.slice(1);
        }

        const tags = [...new Set(
            tagLines.flatMap(l => l.split(/[,、\s　]+/)).map(t => t.trim()).filter(Boolean)
        )];
        state.tags       = tags;
        state.difficulty = difficulty;

        renderTagsPreview(tags);
        const diffEl = document.getElementById('difficulty-display');
        if (diffEl) {
            diffEl.textContent  = difficulty != null ? `難易度 ${difficulty}` : '';
            diffEl.style.display = difficulty != null ? 'inline-block' : 'none';
        }
        showToast(`${tags.length}件のタグ` + (difficulty != null ? ` / 難易度 ${difficulty}` : '') + ' を読み込みました');
    });

    function renderFinalPreview() {
        const sourceBook = document.getElementById('save-source-book').value || '(教材名未入力)';
        const chapter    = document.getElementById('save-chapter').value    || '';
        const unit       = document.getElementById('save-unit').value       || '未設定';
        const pageNum    = document.getElementById('save-page-number').value   || '';
        const problemNum = document.getElementById('save-problem-number').value || '';
        const strategy   = document.getElementById('save-strategy').value   || state.strategySummary;

        const chapterStr    = chapter    ? ` ${chapter}` : '';
        const problemNumStr = problemNum ? ` 問${problemNum}` : '';

        // organizerBlocks からセクション（テキスト＋配下の図）を構築
        function computeSections(blocks) {
            const secs = [];
            blocks.forEach((block, idx) => {
                if (block.type === 'text') secs.push({ blockIdx: idx, block, diagrams: [] });
                else if (block.type === 'diagram' && secs.length > 0) secs[secs.length - 1].diagrams.push(block);
            });
            return secs;
        }

        // パーツ構造を視覚化して HTML を生成（ラベルはクリックで編集可）
        function sectionsToHtml(sections, isExplanation, fallbackMd, fallbackDiagramUrl) {
            if (sections && sections.length) {
                return sections.map(sec => {
                    const isFirst = sec.block.label === '全体文';
                    const badgeColor  = isFirst ? '#6366f1' : '#0284c7';
                    const borderColor = isFirst ? '#c7d2fe' : '#bae6fd';
                    let text = (sec.block.content || '')
                        .replace(/^\s*\*\*\[[^\]]+\]\*\*\s*\n?/, '')
                        .replace(/^[\(（][0-9１-９一二三四五六七八九][）\)]\s*/, '')
                        .trim();
                    const diagHtml = sec.diagrams.map(d => {
                        const m = (d.content || '').match(/!\[[^\]]*\]\(([^)]+)\)/);
                        const url = m ? m[1] : null;
                        if (!url) return '';
                        return `<div style="overflow-x:auto;margin:0.5rem 0;"><img src="${url}" alt="${d.label}" style="border-radius:4px;max-width:none;"></div>`;
                    }).join('');
                    return `
                        <div style="margin-bottom:1.25rem;">
                            <span class="part-label-edit" data-block-idx="${sec.blockIdx}" data-is-exp="${isExplanation ? 1 : 0}"
                                style="display:inline-block;font-size:0.75rem;font-weight:700;padding:0.15rem 0.5rem;border-radius:4px;background:${badgeColor};color:white;margin-bottom:0.35rem;cursor:pointer;" title="クリックで編集">${sec.block.label}</span>
                            <button class="part-content-edit-btn" data-block-idx="${sec.blockIdx}" data-is-exp="${isExplanation ? 1 : 0}"
                                style="margin-left:0.4rem;margin-bottom:0.35rem;font-size:0.7rem;padding:0.1rem 0.45rem;border-radius:4px;border:1px solid ${borderColor};background:white;color:#475569;cursor:pointer;vertical-align:middle;" title="本文を編集">✎</button>
                            <div class="part-content-box" data-block-idx="${sec.blockIdx}" data-is-exp="${isExplanation ? 1 : 0}"
                                style="border:2px solid ${borderColor};border-radius:8px;padding:0.75rem 1rem;">
                                <div class="markdown-body">${mdToHtml(text)}</div>
                                ${diagHtml}
                            </div>
                        </div>`;
                }).join('');
            }
            // フォールバック: オーガナイザー未使用
            let md = (fallbackMd || '').replace(/^\s*\*\*\[[^\]]+\]\*\*\s*\n?/, '').trim();
            if (fallbackDiagramUrl) md = md.replace(/problem_diagram\.png|explanation_diagram\.png/g, fallbackDiagramUrl);
            return `<div class="markdown-body">${mdToHtml(md) || '(なし)'}</div>`;
        }

        const problemSections     = computeSections(organizerBlocks);
        const explanationSections = computeSections(explanationOrganizerBlocks);

        finalPreviewContainer.innerHTML = `
            <div style="border-bottom:2px solid rgba(99,102,241,0.1);padding-bottom:1rem;margin-bottom:1.5rem;">
                <div style="font-size:1.4rem;font-weight:700;color:#0f172a;margin-bottom:0.5rem;">${sourceBook}${chapterStr}${problemNumStr}</div>
                <div style="display:flex;gap:0.5rem;flex-wrap:wrap;">
                    <span style="background:#f1f5f9;color:#475569;padding:0.2rem 0.5rem;border-radius:4px;font-size:0.78rem;">ID: 自動発番</span>
                    <span style="background:#e0e7ff;color:#4f46e5;padding:0.2rem 0.5rem;border-radius:4px;font-size:0.78rem;">単元: ${unit}</span>
                    ${pageNum ? `<span style="background:#fce7f3;color:#db2777;padding:0.2rem 0.5rem;border-radius:4px;font-size:0.78rem;">ページ数: ${pageNum}</span>` : ''}
                </div>
                ${strategy ? `<div style="margin-top:1rem;background:rgba(99,102,241,0.04);border-left:4px solid var(--accent-primary);padding:0.75rem 1rem;border-radius:4px;font-size:0.88rem;color:#312e81;"><strong>解法の核心:</strong> ${strategy}</div>` : ''}
            </div>
            <h4 style="font-size:1.1rem;border-bottom:1px dashed #e2e8f0;padding-bottom:0.25rem;margin-bottom:0.75rem;"><i class="fa-solid fa-circle-question"></i> 問題</h4>
            <div style="margin-bottom:2rem;">${sectionsToHtml(problemSections, false, state.problemMarkdown, state.problemDiagramUrl)}</div>
            <h4 style="font-size:1.1rem;border-bottom:1px dashed #e2e8f0;padding-bottom:0.25rem;margin-bottom:0.75rem;"><i class="fa-solid fa-lightbulb"></i> 解説</h4>
            <div>${sectionsToHtml(explanationSections, true, state.explanationMarkdown, state.explanationDiagramUrl)}</div>
        `;
        if (window.MathJax && window.MathJax.typesetPromise) {
            window.MathJax.typesetPromise([finalPreviewContainer]);
        }

        // ✎ボタンで本文をインライン編集
        finalPreviewContainer.querySelectorAll('.part-content-edit-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const blockIdx = parseInt(btn.dataset.blockIdx);
                const isExp = btn.dataset.isExp === '1';
                const blocks = isExp ? explanationOrganizerBlocks : organizerBlocks;
                const block = blocks[blockIdx];
                const box = finalPreviewContainer.querySelector(`.part-content-box[data-block-idx="${blockIdx}"][data-is-exp="${btn.dataset.isExp}"]`);
                if (!box || box.querySelector('textarea')) return;

                // 現在の content から表示用テキスト（**[N]** と先頭 (N) を除去したもの）を取得
                const rawContent = (block.content || '')
                    .replace(/^\s*\*\*\[[^\]]+\]\*\*\s*\n?/, '')
                    .replace(/^[\(（][0-9１-９一二三四五六七八九][）\)]\s*/, '')
                    .trim();

                const ta = document.createElement('textarea');
                ta.value = rawContent;
                ta.style.cssText = 'width:100%;min-height:8rem;padding:0.5rem;border:none;outline:none;font-size:0.88rem;font-family:inherit;resize:vertical;background:transparent;';
                box.innerHTML = '';
                box.appendChild(ta);
                ta.focus();

                const save = () => {
                    const newText = ta.value;
                    // block.content を更新（先頭の (N) ラベルが元々あれば復元する）
                    const prefix = (block.content || '').match(/^(\s*\*\*\[[^\]]+\]\*\*\s*\n?[\(（][0-9１-９一二三四五六七八九][）\)]\s*)/);
                    block.content = prefix ? prefix[1] + newText : newText;
                    renderFinalPreview();
                };
                btn.addEventListener('click', save, { once: true });
                const applyBtn = document.createElement('button');
                applyBtn.textContent = '確定';
                applyBtn.style.cssText = 'margin-top:0.4rem;padding:0.2rem 0.75rem;border-radius:4px;border:none;background:#6366f1;color:white;font-size:0.8rem;cursor:pointer;display:block;';
                applyBtn.addEventListener('click', save);
                box.appendChild(applyBtn);
                ta.addEventListener('keydown', e => { if (e.key === 'Escape') renderFinalPreview(); });
            });
        });

        // ラベルクリックで編集
        finalPreviewContainer.querySelectorAll('.part-label-edit').forEach(span => {
            span.addEventListener('click', () => {
                const blockIdx = parseInt(span.dataset.blockIdx);
                const isExp = span.dataset.isExp === '1';
                const blocks = isExp ? explanationOrganizerBlocks : organizerBlocks;
                const currentLabel = blocks[blockIdx].label;
                const input = document.createElement('input');
                input.type = 'text';
                input.value = currentLabel;
                input.style.cssText = 'font-size:0.75rem;font-weight:700;padding:0.1rem 0.4rem;border-radius:4px;border:2px solid #6366f1;outline:none;min-width:4rem;max-width:8rem;background:white;color:#1e293b;';
                span.replaceWith(input);
                input.focus();
                input.select();
                const save = () => {
                    const newLabel = input.value.trim();
                    if (newLabel) blocks[blockIdx].label = newLabel;
                    renderFinalPreview();
                };
                input.addEventListener('blur', save);
                input.addEventListener('keydown', e => {
                    if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
                    if (e.key === 'Escape') { renderFinalPreview(); }
                });
            });
        });
    }

    ['save-source-book', 'save-chapter', 'save-unit', 'save-page-number', 'save-problem-number', 'save-strategy'].forEach(id => {
        document.getElementById(id).addEventListener('input', renderFinalPreview);
    });

    // 問題文修正ペイン
    document.getElementById('btn-edit-final-problem').addEventListener('click', () => {
        const pane = document.getElementById('final-problem-edit-pane');
        const open = pane.style.display !== 'none';
        pane.style.display = open ? 'none' : 'flex';
        if (!open) document.getElementById('final-problem-editor').value = state.problemMarkdown;
    });
    document.getElementById('btn-apply-final-problem').addEventListener('click', () => {
        state.problemMarkdown = document.getElementById('final-problem-editor').value;
        document.getElementById('problem-text-editor').value = state.problemMarkdown;
        renderFinalPreview();
        document.getElementById('final-problem-edit-pane').style.display = 'none';
        showToast('問題文を更新しました。');
    });

    // 解説文修正ペイン
    document.getElementById('btn-edit-final-explanation').addEventListener('click', () => {
        const pane = document.getElementById('final-explanation-edit-pane');
        const open = pane.style.display !== 'none';
        pane.style.display = open ? 'none' : 'flex';
        if (!open) document.getElementById('final-explanation-editor').value = state.explanationMarkdown;
    });
    document.getElementById('btn-apply-final-explanation').addEventListener('click', () => {
        state.explanationMarkdown = document.getElementById('final-explanation-editor').value;
        document.getElementById('explanation-markdown-editor').value = state.explanationMarkdown;
        renderFinalPreview();
        document.getElementById('final-explanation-edit-pane').style.display = 'none';
        showToast('解説文を更新しました。');
    });

    document.getElementById('btn-step3-back').addEventListener('click', () => navigateToStep(2));

    // オーガナイザーブロック → 構造化パーツ変換
    // 直後に続く diagram ブロックは直前の text ブロックに属するとして diagrams 配列へ格納
    function blocksToParts(blocks) {
        if (!blocks || !blocks.length) return null;
        const parts = [];
        let current = null;
        blocks.forEach(block => {
            if (block.type === 'text') {
                if (current) parts.push(current);
                current = { label: block.label, content: block.content, diagrams: [] };
            } else if (block.type === 'diagram') {
                const diag = { label: block.label, matplotlib_code: block.matplotlib_code || null };
                if (current) {
                    current.diagrams.push(diag);
                } else {
                    parts.push({ label: block.label, content: null, diagrams: [diag] });
                }
            }
        });
        if (current) parts.push(current);
        return parts.length ? parts : null;
    }

    document.getElementById('btn-save-problem').addEventListener('click', () => {
        const sourceBook = document.getElementById('save-source-book').value.trim();
        const chapter    = document.getElementById('save-chapter').value.trim();
        const unit       = document.getElementById('save-unit').value.trim();
        const pageNum    = document.getElementById('save-page-number').value.trim();
        const problemNum = document.getElementById('save-problem-number').value.trim();
        const strategy   = document.getElementById('save-strategy').value.trim();

        if (!sourceBook) {
            showToast('教材名は必須入力です。', 'error');
            return;
        }

        // 保存用 MD: data URI は埋め込まない（プレースホルダーのまま保存）
        const finalProblemMd = (state.problemMarkdown || '')
            .replace(/^\s*\*\*\[[^\]]+\]\*\*\s*\n?/, '').trim();
        const finalExplanationMd = state.explanationMarkdown || '';

        const requestData = {
            session_id:                   state.session_id,
            source_book:                  sourceBook,
            chapter,
            unit,
            problem_number:               problemNum,
            problem_markdown:             finalProblemMd,
            explanation_markdown:         finalExplanationMd,
            problem_matplotlib_code:      state.matplotlibCode || '',
            explanation_matplotlib_code:  state.explanationMatplotlibCode || '',
            strategy_summary:             pageNum ? `${pageNum} | ${strategy}` : strategy,
            tags:                         state.tags,
            difficulty:                   state.difficulty,
            problem_parts:                blocksToParts(organizerBlocks),
            explanation_parts:            blocksToParts(explanationOrganizerBlocks)
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
                clearDraft();
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
