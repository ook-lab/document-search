// sansu-base OCR Reader Frontend Logic

document.addEventListener('DOMContentLoaded', () => {
    // 状態管理
    const state = {
        session_id: crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).substring(2) + Date.now().toString(36),
        currentStep: 1,
        problemFile: null,      // Local File object or {drive: true, id, name}
        explanationFile: null,  // Local File object or {drive: true, id, name}
        problemMarkdown: '',
        explanationMarkdown: '',
        strategySummary: '',
        tags: [],
        driveFiles: [],
        activeDriveTarget: null, // 'problem' or 'explanation'
        accumulatedCost: 0.0
    };

    // 料金テーブル (100万トークンあたりドル。1ドル=160円換算)
    const PRICING = {
        'gemini-3.5-flash': {
            input: 1.50,
            output: 9.00
        },
        'gemini-3.1-flash-lite': {
            input: 0.075,
            output: 0.30
        }
    };

    // UI要素のキャッシュ
    const loadingOverlay = document.getElementById('loading-overlay');
    const loadingText = document.getElementById('loading-text');
    const toast = document.getElementById('toast');

    // ウィザードパネルとインジケータ
    const stepIndicators = {
        1: document.getElementById('step-indicator-1'),
        2: document.getElementById('step-indicator-2'),
        3: document.getElementById('step-indicator-3')
    };
    const stepLines = {
        1: document.getElementById('step-line-1'),
        2: document.getElementById('step-line-2')
    };
    const wizardPanels = {
        1: document.getElementById('wizard-panel-1'),
        2: document.getElementById('wizard-panel-2'),
        3: document.getElementById('wizard-panel-3')
    };

    // --- Toast Helper ---
    function showToast(message, type = 'success') {
        toast.textContent = message;
        toast.className = 'toast';
        if (type === 'success') toast.classList.add('success');
        if (type === 'error') toast.classList.add('error');
        toast.style.display = 'block';
        setTimeout(() => {
            toast.style.display = 'none';
        }, 4000);
    }

    // --- Wizard Navigation Helper ---
    function navigateToStep(stepNum) {
        // パネル切り替え
        Object.keys(wizardPanels).forEach(k => {
            if (parseInt(k) === stepNum) {
                wizardPanels[k].classList.add('active');
            } else {
                wizardPanels[k].classList.remove('active');
            }
        });

        // インジケータ更新
        Object.keys(stepIndicators).forEach(k => {
            const num = parseInt(k);
            if (num === stepNum) {
                stepIndicators[k].classList.add('active');
                stepIndicators[k].classList.remove('completed');
            } else if (num < stepNum) {
                stepIndicators[k].classList.remove('active');
                stepIndicators[k].classList.add('completed');
            } else {
                stepIndicators[k].classList.remove('active', 'completed');
            }
        });

        // 進行線の色更新
        Object.keys(stepLines).forEach(k => {
            const num = parseInt(k);
            if (num < stepNum) {
                stepLines[k].classList.add('completed');
            } else {
                stepLines[k].classList.remove('completed');
            }
        });

        state.currentStep = stepNum;

        // ステップ3に入った場合は最終プレビューをレンダリング
        if (stepNum === 3) {
            renderFinalPreview();
        }
    }

    // --- Google Drive Selector Modal ---
    const driveModal = document.getElementById('drive-modal-overlay');
    const driveFilesContainer = document.getElementById('drive-files-container');
    const driveLoading = document.getElementById('drive-loading');
    const btnCloseDriveModal = document.getElementById('btn-close-drive-modal');

    function openDriveModal(target) {
        state.activeDriveTarget = target;
        driveModal.style.display = 'flex';
        driveLoading.style.display = 'flex';
        driveFilesContainer.style.display = 'none';

        // APIからDriveファイル一覧を取得
        fetch('/api/drive/files')
            .then(res => {
                if (!res.ok) throw new Error('Failed to fetch Drive files');
                return res.json();
            })
            .then(files => {
                state.driveFiles = files;
                renderDriveFiles();
            })
            .catch(err => {
                console.error(err);
                showToast('Googleドライブとの通信に失敗しました。', 'error');
                driveModal.style.display = 'none';
            });
    }

    function renderDriveFiles() {
        driveLoading.style.display = 'none';
        driveFilesContainer.style.display = 'grid';
        driveFilesContainer.innerHTML = '';

        if (!state.driveFiles || state.driveFiles.length === 0) {
            driveFilesContainer.innerHTML = '<div style="grid-column: span 4; text-align: center; color: var(--text-muted);">ファイルが見つかりません</div>';
            return;
        }

        state.driveFiles.forEach(file => {
            const card = document.createElement('div');
            card.className = 'drive-file-card';
            
            const isPdf = file.mimeType === 'application/pdf';
            const iconClass = isPdf ? 'fa-file-pdf' : 'fa-file-image';
            
            card.innerHTML = `
                <i class="fa-solid ${iconClass}"></i>
                <div class="drive-file-name" title="${file.name}">${file.name}</div>
                <div class="drive-file-date">${new Date(file.modifiedTime).toLocaleDateString()}</div>
            `;
            
            card.addEventListener('click', () => {
                selectDriveFile(file);
            });
            
            driveFilesContainer.appendChild(card);
        });
    }

    function selectDriveFile(file) {
        const fileData = {
            drive: true,
            id: file.id,
            name: file.name
        };

        if (state.activeDriveTarget === 'problem') {
            state.problemFile = fileData;
            const info = document.getElementById('problem-file-info');
            info.style.display = 'flex';
            info.innerHTML = `<span><i class="fa-brands fa-google-drive"></i> ${file.name}</span> <i class="fa-solid fa-xmark" style="cursor:pointer;" id="btn-clear-problem-file"></i>`;
            document.getElementById('btn-clear-problem-file').addEventListener('click', (e) => {
                e.stopPropagation();
                state.problemFile = null;
                info.style.display = 'none';
            });
        } else {
            state.explanationFile = fileData;
            const info = document.getElementById('explanation-file-info');
            info.style.display = 'flex';
            info.innerHTML = `<span><i class="fa-brands fa-google-drive"></i> ${file.name}</span> <i class="fa-solid fa-xmark" style="cursor:pointer;" id="btn-clear-explanation-file"></i>`;
            document.getElementById('btn-clear-explanation-file').addEventListener('click', (e) => {
                e.stopPropagation();
                state.explanationFile = null;
                info.style.display = 'none';
            });
        }

        driveModal.style.display = 'none';
        showToast(`Googleドライブから選択しました: ${file.name}`);
    }

    btnCloseDriveModal.addEventListener('click', () => {
        driveModal.style.display = 'none';
    });

    // --- Drag and Drop Logic ---
    function setupDragAndDrop(zoneId, inputId, targetKey, infoId) {
        const zone = document.getElementById(zoneId);
        const input = document.getElementById(inputId);
        const info = document.getElementById(infoId);

        zone.addEventListener('dragover', (e) => {
            e.preventDefault();
            zone.classList.add('dragover');
        });

        zone.addEventListener('dragleave', () => {
            zone.classList.remove('dragover');
        });

        zone.addEventListener('drop', (e) => {
            e.preventDefault();
            zone.classList.remove('dragover');
            if (e.dataTransfer.files.length > 0) {
                const file = e.dataTransfer.files[0];
                selectLocalFile(file, targetKey, info);
            }
        });

        input.addEventListener('change', () => {
            if (input.files.length > 0) {
                const file = input.files[0];
                selectLocalFile(file, targetKey, info);
            }
        });
    }

    function selectLocalFile(file, targetKey, infoElement) {
        if (targetKey === 'problem') {
            state.problemFile = file;
        } else {
            state.explanationFile = file;
        }

        infoElement.style.display = 'flex';
        infoElement.innerHTML = `<span><i class="fa-solid fa-file"></i> ${file.name} (${(file.size / 1024 / 1024).toFixed(2)} MB)</span> <i class="fa-solid fa-xmark" style="cursor:pointer;" id="btn-clear-${targetKey}-file"></i>`;
        
        document.getElementById(`btn-clear-${targetKey}-file`).addEventListener('click', (e) => {
            e.stopPropagation();
            if (targetKey === 'problem') state.problemFile = null;
            else state.explanationFile = null;
            infoElement.style.display = 'none';
        });
    }

    setupDragAndDrop('problem-upload-zone', 'problem-file-input', 'problem', 'problem-file-info');
    setupDragAndDrop('explanation-upload-zone', 'explanation-file-input', 'explanation', 'explanation-file-info');

    document.getElementById('btn-problem-drive').addEventListener('click', () => openDriveModal('problem'));
    document.getElementById('btn-explanation-drive').addEventListener('click', () => openDriveModal('explanation'));


    // --- Pricing Calculation & Output ---
    function updateCostDisplay(tokenUsage, model, displayElementId) {
        const pricing = PRICING[model];
        if (!pricing) return;

        const inCost = (tokenUsage.input_tokens || 0) * (pricing.input / 1000000);
        const outCost = (tokenUsage.output_tokens || 0) * (pricing.output / 1000000);
        const totalUsd = inCost + outCost;
        const totalJpy = totalUsd * 160;

        state.accumulatedCost += totalUsd;

        const display = document.getElementById(displayElementId);
        display.innerHTML = `
            <span>入力: ${tokenUsage.input_tokens || 0} t | 出力: ${tokenUsage.output_tokens || 0} t</span>
            <strong>約 ${totalJpy.toFixed(2)} 円 ($${totalUsd.toFixed(5)})</strong>
        `;
    }

    // --- STEP 1 OCR Action ---
    const btnRunProblemOcr = document.getElementById('btn-run-problem-ocr');
    const problemPreviewBody = document.getElementById('problem-preview-body');
    const problemEditorPane = document.getElementById('problem-editor-pane');
    const problemMarkdownEditor = document.getElementById('problem-markdown-editor');
    const btnEditProblem = document.getElementById('btn-edit-problem');
    const btnSyncProblemPreview = document.getElementById('btn-sync-problem-preview');
    const problemActionsNext = document.getElementById('problem-actions-next');

    btnRunProblemOcr.addEventListener('click', () => {
        if (!state.problemFile) {
            showToast('問題の画像またはPDFファイルを選択してください。', 'error');
            return;
        }

        const model = document.getElementById('problem-model').value;
        const hint = document.getElementById('problem-hint').value;

        const formData = new FormData();
        formData.append('model', model);
        formData.append('hint', hint);
        formData.append('session_id', state.session_id);

        if (state.problemFile.drive) {
            formData.append('file_id', state.problemFile.id);
        } else {
            formData.append('file', state.problemFile);
        }

        loadingText.textContent = "問題画像をスキャン＆図形コードを実行中...";
        loadingOverlay.style.display = 'flex';

        fetch('/api/ocr/read-problem', {
            method: 'POST',
            body: formData
        })
        .then(res => {
            if (!res.ok) throw new Error('OCR API failed');
            return res.json();
        })
        .then(data => {
            loadingOverlay.style.display = 'none';
            if (data.error && !data.problem_markdown) {
                showToast(`エラーが発生しました: ${data.error}`, 'error');
                return;
            }

            state.problemMarkdown = data.problem_markdown;
            problemMarkdownEditor.value = data.problem_markdown;

            renderProblemPreview();
            updateCostDisplay(data.token_usage, model, 'problem-cost-display');

            btnEditProblem.style.display = 'inline-flex';
            problemActionsNext.style.display = 'block';

            if (data.error) {
                showToast(`読み取り完了（一部警告あり）: ${data.error}`, 'warning');
            } else {
                showToast('問題のOCR読み取りに成功しました！');
            }
        })
        .catch(err => {
            console.error(err);
            loadingOverlay.style.display = 'none';
            showToast('OCRサーバーへの接続エラーが発生しました。', 'error');
        });
    });

    function renderProblemPreview() {
        problemPreviewBody.innerHTML = `
            <div class="markdown-body">
                ${state.problemMarkdown}
            </div>
        `;
        // MathJaxで再描画
        if (window.MathJax && window.MathJax.typesetPromise) {
            window.MathJax.typesetPromise([problemPreviewBody]);
        }
    }

    btnEditProblem.addEventListener('click', () => {
        if (problemEditorPane.style.display === 'none') {
            problemEditorPane.style.display = 'flex';
            btnEditProblem.innerHTML = '<i class="fa-solid fa-eye"></i> プレビューを表示';
        } else {
            problemEditorPane.style.display = 'none';
            btnEditProblem.innerHTML = '<i class="fa-solid fa-pen"></i> 編集';
        }
    });

    btnSyncProblemPreview.addEventListener('click', () => {
        state.problemMarkdown = problemMarkdownEditor.value;
        renderProblemPreview();
        showToast('エディタの変更をプレビューに反映しました。');
    });

    document.getElementById('btn-step1-next').addEventListener('click', () => {
        navigateToStep(2);
    });

    // --- STEP 2 OCR Action ---
    const btnRunExplanationOcr = document.getElementById('btn-run-explanation-ocr');
    const explanationPreviewBody = document.getElementById('explanation-preview-body');
    const explanationEditorPane = document.getElementById('explanation-editor-pane');
    const explanationMarkdownEditor = document.getElementById('explanation-markdown-editor');
    const btnEditExplanation = document.getElementById('btn-edit-explanation');
    const btnSyncExplanationPreview = document.getElementById('btn-sync-explanation-preview');
    const explanationActionsNext = document.getElementById('explanation-actions-next');

    btnRunExplanationOcr.addEventListener('click', () => {
        if (!state.explanationFile) {
            showToast('解説の画像またはPDFファイルを選択してください。', 'error');
            return;
        }

        const model = document.getElementById('explanation-model').value;
        const hint = document.getElementById('explanation-hint').value;

        const formData = new FormData();
        formData.append('model', model);
        formData.append('hint', hint);
        formData.append('problem_text', state.problemMarkdown);
        formData.append('session_id', state.session_id);

        if (state.explanationFile.drive) {
            formData.append('file_id', state.explanationFile.id);
        } else {
            formData.append('file', state.explanationFile);
        }

        loadingText.textContent = "解説画像をスキャン＆詳細化中...";
        loadingOverlay.style.display = 'flex';

        fetch('/api/ocr/read-explanation', {
            method: 'POST',
            body: formData
        })
        .then(res => {
            if (!res.ok) throw new Error('OCR API failed');
            return res.json();
        })
        .then(data => {
            loadingOverlay.style.display = 'none';
            if (data.error && !data.explanation_markdown) {
                showToast(`エラーが発生しました: ${data.error}`, 'error');
                return;
            }

            state.explanationMarkdown = data.explanation_markdown;
            explanationMarkdownEditor.value = data.explanation_markdown;
            state.strategySummary = data.strategy_summary;
            state.tags = data.tags || [];

            // メタデータ入力欄に自動設定
            document.getElementById('save-strategy').value = data.strategy_summary;

            renderExplanationPreview();
            updateCostDisplay(data.token_usage, model, 'explanation-cost-display');

            btnEditExplanation.style.display = 'inline-flex';
            explanationActionsNext.style.display = 'block';

            if (data.error) {
                showToast(`読み取り完了（一部警告あり）: ${data.error}`, 'warning');
            } else {
                showToast('解答解説の詳細化と図形生成に成功しました！');
            }
        })
        .catch(err => {
            console.error(err);
            loadingOverlay.style.display = 'none';
            showToast('OCRサーバーへの接続エラーが発生しました。', 'error');
        });
    });

    function renderExplanationPreview() {
        explanationPreviewBody.innerHTML = `
            <div class="strategy-box">
                <strong>解法の核心（コア戦略）:</strong> ${state.strategySummary || '抽出中...'}
            </div>
            <div class="markdown-body">
                ${state.explanationMarkdown}
            </div>
            <div style="margin-top: 1rem; display: flex; gap: 0.5rem; flex-wrap: wrap;">
                ${state.tags.map(t => `<span class="badge badge-category">#${t}</span>`).join('')}
            </div>
        `;
        if (window.MathJax && window.MathJax.typesetPromise) {
            window.MathJax.typesetPromise([explanationPreviewBody]);
        }
    }

    btnEditExplanation.addEventListener('click', () => {
        if (explanationEditorPane.style.display === 'none') {
            explanationEditorPane.style.display = 'flex';
            btnEditExplanation.innerHTML = '<i class="fa-solid fa-eye"></i> プレビューを表示';
        } else {
            explanationEditorPane.style.display = 'none';
            btnEditExplanation.innerHTML = '<i class="fa-solid fa-pen"></i> 編集';
        }
    });

    btnSyncExplanationPreview.addEventListener('click', () => {
        state.explanationMarkdown = explanationMarkdownEditor.value;
        renderExplanationPreview();
        showToast('エディタの変更をプレビューに反映しました。');
    });

    document.getElementById('btn-step2-back').addEventListener('click', () => {
        navigateToStep(1);
    });

    document.getElementById('btn-step2-next').addEventListener('click', () => {
        navigateToStep(3);
    });

    // --- STEP 3 SAVE Action ---
    const finalPreviewContainer = document.getElementById('final-preview-container');
    const btnSaveProblem = document.getElementById('btn-save-problem');

    function renderFinalPreview() {
        const sourceBook = document.getElementById('save-source-book').value || '(教材名未入力)';
        const chapter = document.getElementById('save-chapter').value || '';
        const unit = document.getElementById('save-unit').value || '未設定';
        const pageNum = document.getElementById('save-page-number').value || '';
        const problemNum = document.getElementById('save-problem-number').value || '';
        const strategy = document.getElementById('save-strategy').value || state.strategySummary;

        const chapterStr = chapter ? ` ${chapter}` : '';
        const pageNumStr = pageNum ? ` [${pageNum}]` : '';
        const problemNumStr = problemNum ? ` (問:${problemNum})` : '';

        finalPreviewContainer.innerHTML = `
            <div class="preview-doc-header" style="border-bottom: 2px solid rgba(99, 102, 241, 0.1); padding-bottom: 1rem; margin-bottom: 1.5rem;">
                <div style="font-size: 1.4rem; font-weight: 700; color: #0f172a; margin-bottom: 0.5rem;">
                    ${sourceBook}${chapterStr}${problemNumStr}
                </div>
                <div class="preview-meta-badges" style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
                    <span class="badge badge-school" style="background:#f1f5f9; color:#475569;">ID: 自動発番</span>
                    <span class="badge badge-category" style="background:#e0e7ff; color:#4f46e5;">単元: ${unit}</span>
                    ${pageNum ? `<span class="badge badge-subcat" style="background:#fce7f3; color:#db2777;">ページ数: ${pageNum}</span>` : ''}
                </div>
                ${strategy ? `
                <div class="strategy-box" style="margin-top: 1rem; background:rgba(99, 102, 241, 0.04); border-left:4px solid var(--accent-primary); padding:0.75rem 1rem; border-radius:4px; font-size:0.88rem; color:#312e81;">
                    <strong>解法の核心:</strong> ${strategy}
                </div>` : ''}
            </div>

            <h4 class="preview-section-header" style="font-size: 1.1rem; border-bottom: 1px dashed #e2e8f0; padding-bottom: 0.25rem; margin-bottom: 0.75rem;"><i class="fa-solid fa-circle-question"></i> 問題</h4>
            <div class="markdown-body" id="final-prob-preview" style="margin-bottom: 2rem;">
                ${state.problemMarkdown || '(問題文なし)'}
            </div>

            <h4 class="preview-section-header" style="font-size: 1.1rem; border-bottom: 1px dashed #e2e8f0; padding-bottom: 0.25rem; margin-bottom: 0.75rem;"><i class="fa-solid fa-lightbulb"></i> 解説</h4>
            <div class="markdown-body" id="final-exp-preview">
                ${state.explanationMarkdown || '(解説なし)'}
            </div>
        `;

        if (window.MathJax && window.MathJax.typesetPromise) {
            window.MathJax.typesetPromise([finalPreviewContainer]);
        }
    }

    // リアルタイムプレビュー同期
    ['save-source-book', 'save-chapter', 'save-unit', 'save-page-number', 'save-problem-number', 'save-strategy'].forEach(id => {
        document.getElementById(id).addEventListener('input', renderFinalPreview);
    });

    document.getElementById('btn-step3-back').addEventListener('click', () => {
        navigateToStep(2);
    });

    btnSaveProblem.addEventListener('click', () => {
        const sourceBook = document.getElementById('save-source-book').value.strip ? document.getElementById('save-source-book').value.strip() : document.getElementById('save-source-book').value.trim();
        const chapter = document.getElementById('save-chapter').value.trim();
        const unit = document.getElementById('save-unit').value.trim();
        const pageNum = document.getElementById('save-page-number').value.trim();
        const problemNum = document.getElementById('save-problem-number').value.trim();
        const strategy = document.getElementById('save-strategy').value.trim();

        if (!sourceBook) {
            showToast('教材名は必須入力です。', 'error');
            return;
        }

        const requestData = {
            session_id: state.session_id,
            source_book: sourceBook,
            chapter: chapter,
            unit: unit,
            problem_markdown: state.problemMarkdown,
            explanation_markdown: state.explanationMarkdown,
            strategy_summary: pageNum ? `${pageNum} | ${strategy}` : strategy, // ページ数がある場合は核心と連結して保存
            tags: state.tags
        };

        loadingText.textContent = "データベースおよびGoogle Driveへ保存中...";
        loadingOverlay.style.display = 'flex';

        fetch('/api/problems/import', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestData)
        })
        .then(res => {
            if (!res.ok) throw new Error('Save API failed');
            return res.json();
        })
        .then(data => {
            loadingOverlay.style.display = 'none';
            if (data.status === 'success') {
                showToast(`問題を正常に登録しました！ID: ${data.display_id}`);
                // データベース画面にリダイレクトするか、初期化する
                setTimeout(() => {
                    window.location.href = '/';
                }, 2000);
            } else {
                showToast(`保存に失敗しました: ${data.error}`, 'error');
            }
        })
        .catch(err => {
            console.error(err);
            loadingOverlay.style.display = 'none';
            showToast('保存中にエラーが発生しました。', 'error');
        });
    });
});