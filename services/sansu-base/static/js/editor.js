// sansu-base Block Editor JS - Re-architected for Nested Structures
document.addEventListener('DOMContentLoaded', () => {
    // ================================================================
    // State Management
    // ================================================================
    const state = {
        session_id: generateUUID(),
        edit_id: null, // If editing an existing record
        display_id: '',
        source_book: '',
        chapter: '',
        unit: '',
        strategy: '',
        problem_number: '',
        problem: {
            blocks: [] // Unified flat list: { id, type, content, title, compiled_image_url, is_sub_start, sub_number, elev, azim }
        },
        explanation: {
            blocks: [] // Unified flat list
        },
        current_tab: 'problem', // 'problem' or 'explanation'
        active_block_id: null
    };

    let previewTimer = null;

    // ================================================================
    // UI Elements Cache
    // ================================================================
    // View panels and switcher buttons
    const btnViewElements = document.getElementById('btn-view-elements');
    const btnViewLayout = document.getElementById('btn-view-layout');
    const viewElements = document.getElementById('view-elements');
    const viewLayout = document.getElementById('view-layout');

    // Layout sub-tabs & title (Tab 2)
    const layoutTabProblem = document.getElementById('layout-tab-problem');
    const layoutTabExplanation = document.getElementById('layout-tab-explanation');
    const layoutPreviewTitle = document.getElementById('layout-preview-title');
    const modalPreviewSplitContent = document.getElementById('modal-preview-split-content');
    let activeLayoutTab = 'problem'; // 'problem' or 'explanation'

    // Layout-specific metadata inputs
    const modalSourceBookInput = document.getElementById('modal-source-book');
    const modalChapterInput = document.getElementById('modal-chapter');
    const modalUnitInput = document.getElementById('modal-unit');
    const modalStrategyInput = document.getElementById('modal-page-number');
    const modalProblemNumberInput = document.getElementById('modal-problem-number');
    const modalSourceBooksDatalist = document.getElementById('modal-source-books-list');

    const tabProblemBtn = document.getElementById('tab-problem-blocks');
    const tabExplanationBtn = document.getElementById('tab-explanation-blocks');
    const panelProblem = document.getElementById('panel-problem-blocks');
    const panelExplanation = document.getElementById('panel-explanation-blocks');

    const btnSaveDb = document.getElementById('btn-save-db');

    const btnSaveDraft = document.getElementById('btn-save-draft');
    const btnLoadDraft = document.getElementById('btn-load-draft');
    const btnExportDraft = document.getElementById('btn-export-draft');
    const btnImportDraft = document.getElementById('btn-import-draft');
    const btnExportAiMd = document.getElementById('btn-export-ai-md');
    const fileDraftImport = document.getElementById('file-draft-import');
    const draftModalOverlay = document.getElementById('draft-modal-overlay');
    const btnCloseDraftModal = document.getElementById('btn-close-draft-modal');
    const draftListContainer = document.getElementById('draft-list-container');
    const aiMdModalOverlay = document.getElementById('ai-md-modal-overlay');
    const btnCloseAiMdModal = document.getElementById('btn-close-ai-md-modal');
    const aiMdTextarea = document.getElementById('ai-md-textarea');
    const btnCopyAiMd = document.getElementById('btn-copy-ai-md');

    const loadingOverlay = document.getElementById('loading-overlay');
    const loadingText = document.getElementById('loading-text');
    const toast = document.getElementById('toast');

    // Header actions
    const btnOpenDb = document.getElementById('btn-open-db');
    const btnNewProblem = document.getElementById('btn-new-problem');
    const btnPrintPdf = document.getElementById('btn-print-pdf');
    const btnGenVariantModal = document.getElementById('btn-gen-variant-modal');

    // DB Drawer
    const dbDrawerOverlay = document.getElementById('db-drawer-overlay');
    const dbDrawer = document.getElementById('db-drawer');
    const btnCloseDrawer = document.getElementById('btn-close-drawer');
    const drawerSearchInput = document.getElementById('drawer-search-input');
    const drawerFilterUnit = document.getElementById('drawer-filter-unit');
    const drawerProblemList = document.getElementById('drawer-problem-list');

    // AI Variant modal
    const variantModalOverlay = document.getElementById('variant-modal-overlay');
    const btnCloseVariantModal = document.getElementById('btn-close-variant-modal');
    const btnCloseVariantModalBottom = document.getElementById('btn-close-variant-modal-bottom');
    const variantModelSelect = document.getElementById('variant-model-select');
    const btnRunVariantGen = document.getElementById('btn-run-variant-gen');
    const variantTokenUsage = document.getElementById('variant-token-usage');
    const variantTokenModel = document.getElementById('variant-token-model');
    const variantTokenInput = document.getElementById('variant-token-input');
    const variantTokenOutput = document.getElementById('variant-token-output');
    const variantPreviewBox = document.getElementById('variant-preview-box');
    const variantEmptyState = document.getElementById('variant-empty-state');
    const btnLoadVariantToEditor = document.getElementById('btn-load-variant-to-editor');

    // Print
    const printContainer = document.getElementById('print-container');
    const printMeta = document.getElementById('print-meta');
    const printProblemBody = document.getElementById('print-problem-body');
    const printAnswerBody = document.getElementById('print-answer-body');

    // ================================================================
    // Helper Functions
    // ================================================================
    function generateUUID() {
        if (crypto && crypto.randomUUID) {
            return crypto.randomUUID();
        }
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }

    const toHalfWidth = (str) => {
        if (!str) return '';
        return str.replace(/[！-～]/g, (s) => String.fromCharCode(s.charCodeAt(0) - 0xFEE0))
                  .replace(/　/g, ' ');
    };

    function showToast(message, type = 'success') {
        toast.textContent = message;
        toast.className = `toast ${type}`;
        toast.style.display = 'block';
        setTimeout(() => {
            toast.style.display = 'none';
        }, 3000);
    }

    function showLoading(text = '処理中...') {
        loadingText.textContent = text;
        loadingOverlay.style.display = 'flex';
    }

    function hideLoading() {
        loadingOverlay.style.display = 'none';
    }

    function renderMath() {
        if (window.MathJax && window.MathJax.typesetPromise) {
            window.MathJax.typesetPromise().catch((err) => console.log('MathJax error:', err));
        }
    }

    function escapeHtml(str) {
        if (!str) return '';
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function isHtmlContent(text) {
        if (!text) return false;
        const trimmed = text.trim();
        return trimmed.toLowerCase().startsWith('<!doctype html') || 
               trimmed.toLowerCase().startsWith('<html') ||
               (trimmed.startsWith('<') && trimmed.endsWith('>') && trimmed.includes('</'));
    }

    function extractViewInitAngles(code) {
        if (!code) return { elev: 30, azim: -60, has3d: false };
        const has3d = code.includes("projection='3d'") || code.includes('projection="3d"') || code.includes("view_init") || code.includes("Axes3D");
        
        let elev = 30;
        let azim = -60;
        let found = false;

        // 1. Scan for variable assignments like "elev = 20" or "azim = -55"
        const elevMatch = code.match(/(?:^|\n|;)\s*elev\s*=\s*(-?\d+\.?\d*)/);
        const azimMatch = code.match(/(?:^|\n|;)\s*azim\s*=\s*(-?\d+\.?\d*)/);

        if (elevMatch) {
            elev = parseFloat(elevMatch[1]);
            found = true;
        }
        if (azimMatch) {
            azim = parseFloat(azimMatch[1]);
            found = true;
        }
        
        // 2. Scan for direct arguments inside view_init(...)
        const match = code.match(/view_init\s*\(\s*([^)]+)\)/);
        if (match) {
            found = true;
            const argsStr = match[1];
            const args = argsStr.split(',').map(s => s.trim());
            
            if (args.length === 1) {
                const arg = args[0];
                if (arg.includes('elev=')) {
                    const val = parseFloat(arg.split('=')[1]);
                    if (!isNaN(val)) elev = val;
                } else if (arg.includes('azim=')) {
                    const val = parseFloat(arg.split('=')[1]);
                    if (!isNaN(val)) azim = val;
                } else {
                    const val = parseFloat(arg);
                    if (!isNaN(val)) elev = val;
                }
            } else if (args.length >= 2) {
                let hasKeys = false;
                args.forEach(arg => {
                    if (arg.includes('elev=')) {
                        const val = parseFloat(arg.split('=')[1]);
                        if (!isNaN(val)) { elev = val; hasKeys = true; }
                    } else if (arg.includes('azim=')) {
                        const val = parseFloat(arg.split('=')[1]);
                        if (!isNaN(val)) { azim = val; hasKeys = true; }
                    }
                });
                if (!hasKeys) {
                    const val0 = parseFloat(args[0]);
                    const val1 = parseFloat(args[1]);
                    if (!isNaN(val0)) elev = val0;
                    if (!isNaN(val1)) azim = val1;
                }
            }
        }
        return { elev, azim, has3d, found };
    }

    function renderHtmlInIframe(container, htmlContent, isInteractive = false) {
        container.innerHTML = '';
        const iframe = document.createElement('iframe');
        iframe.style.width = '100%';
        iframe.style.height = '400px'; // Default starting height
        iframe.style.minHeight = '150px'; // Guarantee minimum visibility
        iframe.style.border = 'none';
        iframe.style.background = '#ffffff';
        iframe.style.display = 'block';
        iframe.style.overflow = 'hidden';
        iframe.setAttribute('scrolling', 'no');
        
        // If it is in the interactive preview (right pane), we want clicks to pass through to the parent
        // so that the user can click the block to edit it.
        if (isInteractive) {
            iframe.style.pointerEvents = 'none';
        }
        
        const adjustHeight = () => {
            try {
                const doc = iframe.contentDocument || iframe.contentWindow.document;
                if (doc) {
                    const body = doc.body;
                    const html = doc.documentElement;
                    let height = 0;
                    if (body && html) {
                        height = Math.max(
                            body.scrollHeight,
                            body.offsetHeight,
                            html.clientHeight,
                            html.scrollHeight,
                            html.offsetHeight
                        );
                    } else if (html) {
                        height = html.scrollHeight;
                    }
                    
                    // If height is retrieved as valid (greater than 50px), apply it
                    if (height > 50) {
                        iframe.style.height = height + 'px';
                    }
                }
            } catch (e) {
                // Ignore cross-origin issues
            }
        };

        // 1. Set onload before append and srcdoc assignment
        iframe.onload = () => {
            adjustHeight();
            try {
                const doc = iframe.contentDocument || iframe.contentWindow.document;
                if (window.ResizeObserver && doc.body) {
                    const observer = new ResizeObserver(() => {
                        adjustHeight();
                    });
                    observer.observe(doc.body);
                }
            } catch (e) {}
        };

        // 2. Append to DOM
        container.appendChild(iframe);

        // 3. Set srcdoc to start loading
        iframe.srcdoc = htmlContent;

        // Fallbacks for script layout calculations inside the iframe
        setTimeout(adjustHeight, 300);
        setTimeout(adjustHeight, 800);
        setTimeout(adjustHeight, 1500);
        setTimeout(adjustHeight, 3000);
    }

    // Convert compiled markdown text to preview-friendly HTML
    function renderMarkdownToHtml(text) {
        if (!text) return '';
        
        // 1. Extract code blocks BEFORE paragraph splitting (they may contain blank lines)
        const codeBlocks = [];
        let processed = text.replace(/```(json|python|html)?\n([\s\S]*?)\n```/g, (match, lang, code) => {
            const idx = codeBlocks.length;
            codeBlocks.push({ lang: lang || '', code: code });
            return `\n\n__CODE_BLOCK_${idx}__\n\n`;
        });

        // 2. Extract image references before escaping
        const images = [];
        processed = processed.replace(/!\[(.*?)\]\((.*?)\)/g, (match, alt, src) => {
            const idx = images.length;
            images.push({ alt, src });
            return `__IMG_${idx}__`;
        });

        // 3. Escape HTML
        let html = processed
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/&lt;br&gt;/g, '<br>');

        // 4. Paragraph split (safe now — code blocks are placeholders)
        html = html.split(/\n\n+/).map(p => {
                const t = p.trim();
                if (t.startsWith('__CODE_BLOCK_') || t.startsWith('&lt;li') || t.startsWith('&lt;h')) {
                    return p;
                }
                return `<p style="margin-bottom:0.75rem;">${p.replace(/\n/g, '<br>')}</p>`;
            }).join('');

        // 5. Restore code blocks
        html = html.replace(/__CODE_BLOCK_(\d+)__/g, (match, idx) => {
            const cb = codeBlocks[parseInt(idx)];
            if (!cb) return match;
            const isPython = cb.lang === 'python';
            const bg = isPython ? '#1e293b' : '#f1f5f9';
            const color = isPython ? '#f8fafc' : '#334155';
            const escaped = (cb.code || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            return `<pre style="background:${bg}; color:${color}; padding:0.75rem; border-radius:6px; font-family:monospace; font-size:0.85rem; overflow-x:auto; margin:0.75rem 0; white-space:pre-wrap;"><code>${escaped}</code></pre>`;
        });

        // 6. Restore images
        html = html.replace(/__IMG_(\d+)__/g, (match, idx) => {
            const img = images[parseInt(idx)];
            if (!img) return match;
            const cacheBuster = (img.src && img.src.includes('/static/generated/')) ? `?t=${Date.now()}` : '';
            return `<div class="preview-image-container" style="text-align:center; margin:1rem 0;"><img src="${img.src || ''}${cacheBuster}" alt="${img.alt || ''}" style="width:100%; height:auto; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.05); border:1px solid #e2e8f0; display:block;"></div>`;
        });

        // 7. Headings, lists
        html = html
            .replace(/^### (.*?)$/gm, '<h4 style="margin:1rem 0 0.5rem 0; font-weight:600; font-size:1.05rem; color:#334155;">$1</h4>')
            .replace(/^## (.*?)$/gm, '<h3 style="margin:1.5rem 0 0.75rem 0; font-weight:600; color:var(--accent-primary);">$1</h3>')
            .replace(/^# (.*?)$/gm, '<h2 style="margin:2rem 0 1rem 0; font-weight:700; background:var(--accent-gradient); -webkit-background-clip:text; -webkit-text-fill-color:transparent;">$1</h2>')
            .replace(/^-\s*(.*?)$/gm, '<li style="margin-left:1.5rem; margin-bottom:0.25rem; list-style-type:disc;">$1</li>');
            
        html = html.replace(/&amp;/g, '&');
            
        return html;
    }

    // ================================================================
    // Autocomplete & DB History
    // ================================================================
    async function loadSourceBooksHistory() {
        try {
            const res = await fetch('/api/history/source-books');
            if (res.ok) {
                const books = await res.json();
                modalSourceBooksDatalist.innerHTML = '';
                books.forEach(book => {
                    const opt = document.createElement('option');
                    opt.value = book;
                    modalSourceBooksDatalist.appendChild(opt);
                });
            }
        } catch (err) {
            console.error('Failed to load source books history', err);
        }
    }

    // ================================================================
    // Block Operations
    // ================================================================
    function getTargetList(target) {
        const isProblem = target.startsWith('problem');
        const section = isProblem ? state.problem : state.explanation;
        return section ? section.blocks : null;
    }

    function countShapesInSection(section) {
        if (!section || !section.blocks) return 0;
        return section.blocks.filter(b => b.type === 'shape').length;
    }

    function createBlockObject(type) {
        const id = 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7);
        if (type === 'text') {
            return {
                id,
                type: 'text',
                content: '',
                is_sub_start: false,
                sub_number: ''
            };
        } else {
            const section = state.current_tab === 'problem' ? state.problem : state.explanation;
            const count = section ? countShapesInSection(section) : 0;
            
            return {
                id,
                type: 'shape',
                title: '',
                content: 'fig, ax = plt.subplots(figsize=(6, 5))\n\n# 円の描画例\ncircle = plt.Circle((0, 0), 2, fill=False, color="blue", linewidth=2)\nax.add_patch(circle)\n\nax.set_xlim(-3, 3)\nax.set_ylim(-3, 3)\nax.set_aspect("equal")\nax.grid(True, linestyle="--", alpha=0.6)\n\n# 必ずこのファイル名で保存してください\nplt.savefig("explanation_diagram.png", dpi=150, bbox_inches="tight")',
                compiled_image_url: '',
                compiled_image_timestamp: null,
                compile_error: '',
                is_sub_start: false,
                sub_number: ''
            };
        }
    }

    function addNestedBlock(target, type) {
        const list = getTargetList(target);
        if (!list) return;

        const newBlock = createBlockObject(type);
        if (type === 'shape') {
            if (target.startsWith('problem')) {
                newBlock.content = newBlock.content.replace("explanation_diagram.png", "problem_diagram.png");
            }
        }
        list.push(newBlock);
        renderWorkspace();
        setActiveBlock(newBlock.id);
        triggerLivePreview();
    }

    function insertNestedBlockAfter(blockId, type) {
        const result = findBlockById(blockId);
        if (!result) return;

        const newBlock = createBlockObject(type);
        if (type === 'shape') {
            if (result.sectionKey === 'problem') {
                newBlock.content = newBlock.content.replace("explanation_diagram.png", "problem_diagram.png");
            }
        }

        const section = state[result.sectionKey];
        const targetArray = section ? section.blocks : null;

        if (targetArray) {
            const idx = targetArray.findIndex(b => b.id === blockId);
            if (idx !== -1) {
                targetArray.splice(idx + 1, 0, newBlock);
                renderWorkspace();
                setActiveBlock(newBlock.id);
                triggerLivePreview();
            }
        }
    }

    function deleteNestedBlock(blockId) {
        const section = state.current_tab === 'problem' ? state.problem : state.explanation;
        
        const idx = section.blocks.findIndex(b => b.id === blockId);
        if (idx !== -1) {
            section.blocks.splice(idx, 1);
            if (state.active_block_id === blockId) {
                state.active_block_id = null;
            }
            renderWorkspace();
            if (!state.active_block_id) {
                const first = getFirstBlockOfCurrentTab();
                if (first) setActiveBlock(first.id);
                else renderActivePreview();
            }
            triggerLivePreview();
        }
    }

    function duplicateNestedBlock(blockId) {
        const section = state.current_tab === 'problem' ? state.problem : state.explanation;
        const idx = section.blocks.findIndex(b => b.id === blockId);
        if (idx !== -1) {
            const original = section.blocks[idx];
            const duplicate = JSON.parse(JSON.stringify(original));
            duplicate.id = 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7);
            section.blocks.splice(idx + 1, 0, duplicate);
            renderWorkspace();
            setActiveBlock(duplicate.id);
            triggerLivePreview();
        }
    }

    function moveNestedBlock(blockId, direction) {
        const section = state.current_tab === 'problem' ? state.problem : state.explanation;
        const idx = section.blocks.findIndex(b => b.id === blockId);
        if (idx === -1) return;
        const targetIdx = direction === 'up' ? idx - 1 : idx + 1;
        if (targetIdx < 0 || targetIdx >= section.blocks.length) return;
        const temp = section.blocks[idx];
        section.blocks[idx] = section.blocks[targetIdx];
        section.blocks[targetIdx] = temp;
        renderWorkspace();
        triggerLivePreview();
    }

    function expandJsonBlocksAt(blockId, parsedBlocks) {
        const sectionKey = state.current_tab === 'problem' ? 'problem' : 'explanation';
        const section = state[sectionKey];

        // Check if the pasted blocks contain any 'section' type (which means subquestion structure)
        const hasSection = parsedBlocks.some(b => b.type === 'section');

        if (hasSection) {
            // Overwrite the entire active section with this flat representation
            state[sectionKey] = convertFlatBlocksToNested(parsedBlocks);
            showToast('JSON配列から問題構造を展開しました！');
        } else {
            const targetIdx = section.blocks.findIndex(b => b.id === blockId);

            if (targetIdx !== -1) {
                // Remove the current empty/pasted block
                section.blocks.splice(targetIdx, 1);

                // Insert the new blocks
                parsedBlocks.forEach((b, offset) => {
                    const newId = b.id || ('block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7) + '-' + offset);
                    section.blocks.splice(targetIdx + offset, 0, {
                        id: newId,
                        type: b.type || 'text',
                        content: b.content || '',
                        title: b.title || '',
                        compiled_image_url: b.compiled_image_url || '',
                        compiled_image_timestamp: b.compiled_image_timestamp || '',
                        is_sub_start: b.is_sub_start || false,
                        sub_number: b.sub_number || ''
                    });
                });
                showToast(`${parsedBlocks.length}個のブロックを展開しました`);
            }
        }

        renderWorkspace();
        triggerLivePreview();
    }

    function updateNestedBlockContent(blockId, content) {
        const section = state.current_tab === 'problem' ? state.problem : state.explanation;
        let block = section.blocks.find(b => b.id === blockId);
        if (block) {
            block.content = content;
            triggerLivePreview();
        }
    }

    function updateBlockTitle(blockId, title) {
        for (const sectionKey of ['problem', 'explanation']) {
            const section = state[sectionKey];
            let block = section.blocks.find(b => b.id === blockId);
            if (block) {
                block.title = title;
                triggerLivePreview();
                return;
            }
        }
    }

    function findBlockById(blockId) {
        for (const sectionKey of ['problem', 'explanation']) {
            const section = state[sectionKey];
            let block = section.blocks.find(b => b.id === blockId);
            if (block) return { block, sectionKey, location: 'blocks' };
        }
        return null;
    }

    async function executeDiagramBlock(blockId) {
        const section = state.current_tab === 'problem' ? state.problem : state.explanation;
        let block = section.blocks.find(b => b.id === blockId);
        if (!block || block.type !== 'shape') return;

        showLoading('Pythonコードを実行中...');
        const cardEl = document.getElementById(blockId);
        const previewContainer = cardEl.querySelector('.diagram-preview-box');

        try {
            const res = await fetch('/api/diagram/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    code: block.content,
                    session_id: state.session_id,
                    suffix: `${state.current_tab}_${blockId}`,
                    elev: block.elev !== undefined ? block.elev : 30,
                    azim: block.azim !== undefined ? block.azim : -60
                })
            });

            const result = await res.json();
            if (res.ok && result.success) {
                block.compiled_image_url = result.image_url;
                block.compiled_image_timestamp = Date.now();
                block.compile_error = '';
                showToast('図形が描画されました！');
            } else {
                block.compiled_image_url = '';
                block.compile_error = result.error || '不明なエラーが発生しました。';
                showToast('図形描画エラー', 'error');
            }
            renderWorkspace();
            if (state.active_block_id === blockId) {
                renderActivePreview();
            }
            triggerLivePreview();
        } catch (err) {
            block.compiled_image_url = '';
            block.compile_error = '通信エラーが発生しました。';
            previewContainer.innerHTML = `<div class="diagram-error-text">${block.compile_error}</div>`;
            showToast('通信エラー', 'error');
        } finally {
            hideLoading();
        }
    }

    function updateDiagramRotation(blockId, elev, azim) {
        const result = findBlockById(blockId);
        if (result && result.block && result.block.type === 'shape') {
            if (elev !== null) result.block.elev = elev;
            if (azim !== null) result.block.azim = azim;
            executeDiagramBlock(blockId);
        }
    }


    function toggleTextBlockPreview(blockId, buttonEl) {
        const section = state.current_tab === 'problem' ? state.problem : state.explanation;
        let block = section.blocks.find(b => b.id === blockId);
        if (!block || block.type !== 'text') return;

        const previewBox = document.getElementById(`preview-box-${blockId}`);
        if (!previewBox) return;

        if (previewBox.style.display === 'none') {
            if (isHtmlContent(block.content)) {
                renderHtmlInIframe(previewBox, block.content, false);
            } else {
                const compiledMd = compileTextBlock(block.content);
                previewBox.innerHTML = renderMarkdownToHtml(compiledMd);
                if (window.MathJax && window.MathJax.typesetPromise) {
                    window.MathJax.typesetPromise([previewBox]).catch(err => console.error(err));
                }
            }
            previewBox.style.display = 'block';
            buttonEl.innerHTML = `<i class="fa-solid fa-eye-slash"></i> プレビュー非表示`;
            buttonEl.classList.add('btn-primary');
            buttonEl.classList.remove('btn-secondary');
        } else {
            previewBox.style.display = 'none';
            buttonEl.innerHTML = `<i class="fa-solid fa-eye"></i> プレビュー表示`;
            buttonEl.classList.remove('btn-primary');
            buttonEl.classList.add('btn-secondary');
        }
    }

    // ================================================================
    // Rendering Logic
    // ================================================================
    function renderBlockListIntoContainer(blocks, containerEl, targetString) {
        containerEl.innerHTML = '';
        if (blocks.length === 0) {
            containerEl.innerHTML = `
                <div style="text-align:center; padding:1.25rem; color:#94a3b8; font-size:0.8rem; border:1px dashed #e2e8f0; border-radius:6px; background:#fafafa;">
                    文または図を追加してください。
                </div>
            `;
            return;
        }

        blocks.forEach((block, idx) => {
            if (block.is_sub_start) {
                const subBadge = document.createElement('div');
                subBadge.className = 'sub-question-start-badge';
                subBadge.style.cssText = "background: #f0fdf4; border: 1px solid #bbf7d0; color: #15803d; padding: 0.35rem 0.75rem; border-radius: 6px; font-weight: 700; font-size: 0.85rem; margin-top: 1rem; margin-bottom: 0.5rem; display: flex; align-items: center; gap: 0.35rem;";
                if (targetString.startsWith('explanation')) {
                    subBadge.style.background = "#fffbeb";
                    subBadge.style.borderColor = "#fef3c7";
                    subBadge.style.color = "#b45309";
                }
                const label = targetString.startsWith('explanation') ? '小問解説' : '小問';
                subBadge.innerHTML = `<i class="fa-solid fa-hashtag"></i> ここから ${label} ${escapeHtml(block.sub_number || '')} のブロック`;
                containerEl.appendChild(subBadge);
            }

            const card = document.createElement('div');
            card.className = 'block-card';
            if (state.active_block_id === block.id) {
                card.classList.add('active');
            }
            card.id = block.id;

            let headerHTML = `
                <div class="block-header">
                    <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;">
                        <span class="block-badge-index">${idx + 1}</span>
            `;

            if (block.type === 'text') {
                headerHTML += `
                        <span class="badge" style="background:#475569; color:white; font-size:0.75rem; padding:0.15rem 0.4rem; border-radius:4px; font-weight:700;"><i class="fa-solid fa-file-lines"></i> 文</span>
                        <span style="font-size:0.7rem; color:var(--text-muted);">※ 数式(LaTeX)やJSONは自動で判別されます。</span>
                `;
            } else {
                headerHTML += `
                        <span class="badge" style="background:#8b5cf6; color:white; font-size:0.75rem; padding:0.15rem 0.4rem; border-radius:4px; font-weight:700;"><i class="fa-solid fa-shapes"></i> Python図形</span>
                        <div class="figure-title-input" style="display:flex; align-items:center; gap:0.25rem;">
                            <label style="font-size:0.75rem; font-weight:600; color:#64748b;">タイトル:</label>
                            <input type="text" class="input-figure-title" data-id="${block.id}" value="${escapeHtml(block.title || '')}" placeholder="任意" style="width:70px; padding:0.15rem 0.35rem; border:1px solid #cbd5e1; border-radius:4px; font-size:0.8rem; font-weight:600; color:#7c3aed;">
                        </div>
                `;
            }

            headerHTML += `
                    </div>
                    <div class="block-controls">
                        <button type="button" class="btn-ctrl btn-block-ctrl" data-action="insert-text" data-id="${block.id}" title="下に文を追加" style="width: 2.2rem;"><i class="fa-solid fa-plus" style="font-size:0.6rem; color:#10b981;"></i><i class="fa-solid fa-file-lines" style="font-size:0.8rem; margin-left:1px; color:#10b981;"></i></button>
                        <button type="button" class="btn-ctrl btn-block-ctrl" data-action="insert-shape" data-id="${block.id}" title="下に図を追加" style="width: 2.2rem;"><i class="fa-solid fa-plus" style="font-size:0.6rem; color:#8b5cf6;"></i><i class="fa-solid fa-shapes" style="font-size:0.8rem; margin-left:1px; color:#8b5cf6;"></i></button>
                        <button type="button" class="btn-ctrl btn-block-ctrl" data-action="up" data-id="${block.id}" title="上に移動" ${idx === 0 ? 'disabled style="opacity:0.3; cursor:not-allowed;"' : ''}><i class="fa-solid fa-arrow-up"></i></button>
                        <button type="button" class="btn-ctrl btn-block-ctrl" data-action="down" data-id="${block.id}" title="下に移動" ${idx === blocks.length - 1 ? 'disabled style="opacity:0.3; cursor:not-allowed;"' : ''}><i class="fa-solid fa-arrow-down"></i></button>
                        <button type="button" class="btn-ctrl btn-block-ctrl" data-action="duplicate" data-id="${block.id}" title="複製"><i class="fa-solid fa-copy"></i></button>
                        <button type="button" class="btn-ctrl btn-ctrl-danger btn-block-ctrl" data-action="delete" data-id="${block.id}" title="削除"><i class="fa-solid fa-trash"></i></button>
                    </div>
                </div>
            `;

            let bodyHTML = `<div class="block-body">`;
            if (block.type === 'text') {
                bodyHTML += `
                    <textarea class="block-textarea" placeholder="文を入力してください...">${escapeHtml(block.content)}</textarea>
                    <div class="text-block-action" style="display:flex; justify-content:flex-end; margin-top:0.5rem;">
                        <button type="button" class="btn btn-secondary btn-sm btn-preview-text" data-id="${block.id}" title="このブロックの表示確認">
                            <i class="fa-solid fa-eye"></i> プレビュー表示
                        </button>
                    </div>
                    <div class="text-preview-box" id="preview-box-${block.id}" style="display:none; margin-top:0.75rem; padding:0.75rem; background:#f8fafc; border:1px dashed #cbd5e1; border-radius:6px; font-size:0.9rem; line-height:1.6; color:#334155;">
                    </div>
                `;
            } else {
                bodyHTML += `
                    <textarea class="block-textarea monospaced" style="min-height:140px;" placeholder="Matplotlibコードを入力してください...">${escapeHtml(block.content)}</textarea>
                    <div class="diagram-block-action">
                        <span style="font-size:0.75rem; color:var(--text-muted);">※ 画像は '${targetString.startsWith('problem') ? 'problem_diagram.png' : 'explanation_diagram.png'}' で保存してください。</span>
                        <button type="button" class="btn btn-secondary btn-sm btn-draw-diagram" data-id="${block.id}">
                            <i class="fa-solid fa-play"></i> 図形を描画
                        </button>
                    </div>
                `;

                let previewContent = '<span style="font-size:0.8rem; color:var(--text-muted); font-style:italic;">「図形を描画」をクリックするとプレビューが表示されます。</span>';
                let rotationSliders = '';
                if (block.compiled_image_url) {
                    const cacheBuster = block.compiled_image_timestamp ? `?t=${block.compiled_image_timestamp}` : `?t=${Date.now()}`;
                    previewContent = `<img src="${block.compiled_image_url}${cacheBuster}" alt="描画プレビュー">`;
                    
                    const angles = extractViewInitAngles(block.content);
                    if (angles.has3d) {
                        const elev = block.elev !== undefined ? block.elev : angles.elev;
                        const azim = block.azim !== undefined ? block.azim : angles.azim;
                        rotationSliders = `
                            <div class="diagram-rotation-controls" style="margin-top:0.75rem; padding:0.5rem; background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; font-size:0.75rem;">
                                <div style="font-weight:600; color:#4f46e5; margin-bottom:0.4rem; display:flex; align-items:center; gap:0.25rem;">
                                    <i class="fa-solid fa-rotate"></i> 3D立体図の視点調整 (角度)
                                </div>
                                <div style="display:flex; flex-direction:column; gap:0.35rem;">
                                    <div style="display:flex; align-items:center; gap:0.5rem;">
                                        <span style="width:45px; font-weight:600; color:#64748b;">上下:</span>
                                        <input type="range" class="slider-diagram-elev" data-id="${block.id}" min="-90" max="90" value="${elev}" style="flex:1; accent-color:#4f46e5; height:5px; cursor:pointer;">
                                        <span class="val-diagram-elev" style="width:25px; text-align:right; font-weight:700; color:#1e293b;">${elev}°</span>
                                    </div>
                                    <div style="display:flex; align-items:center; gap:0.5rem;">
                                        <span style="width:45px; font-weight:600; color:#64748b;">左右:</span>
                                        <input type="range" class="slider-diagram-azim" data-id="${block.id}" min="-180" max="180" value="${azim}" style="flex:1; accent-color:#4f46e5; height:5px; cursor:pointer;">
                                        <span class="val-diagram-azim" style="width:25px; text-align:right; font-weight:700; color:#1e293b;">${azim}°</span>
                                    </div>
                                </div>
                            </div>
                        `;
                    }
                } else if (block.compile_error) {
                    previewContent = `<div class="diagram-error-text">${escapeHtml(block.compile_error)}</div>`;
                }

                bodyHTML += `
                    <div class="diagram-preview-box">
                        ${previewContent}
                    </div>
                    ${rotationSliders}
                `;
            }
            bodyHTML += `</div>`;

            card.innerHTML = headerHTML + bodyHTML;
            containerEl.appendChild(card);
        });
    }

    function renderWorkspace() {
        const isProblem = state.current_tab === 'problem';
        const section = isProblem ? state.problem : state.explanation;
        const prefix = isProblem ? 'problem' : 'explanation';

        // 1. Render Blocks List
        const listEl = document.getElementById(`${prefix}-common-list`);
        renderBlockListIntoContainer(section.blocks, listEl, `${prefix}-common`);

        // 2. Clear sub-questions container
        const subsListEl = document.getElementById(`${prefix}-subs-list`);
        if (subsListEl) {
            subsListEl.innerHTML = '';
        }

        updateTabCounters();
        attachEventListeners();
    }

    function updateTabCounters() {
        const probBlockCount = state.problem.blocks.length;
        const explBlockCount = state.explanation.blocks.length;
        
        tabProblemBtn.innerHTML = `<i class="fa-solid fa-circle-question"></i> 問題文 (${probBlockCount})`;
        tabExplanationBtn.innerHTML = `<i class="fa-solid fa-lightbulb"></i> 解説文 (${explBlockCount})`;
    }

    // ================================================================
    // Compile Blocks to Markdown (with automatic detection)
    // ================================================================
    function compileTextBlock(content) {
        if (!content || !content.trim()) return '';
        const trimmed = content.trim();

        // 1. JSON detection
        if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
            try {
                JSON.parse(trimmed);
                return `\`\`\`json\n${trimmed}\n\`\`\``;
            } catch (e) {
                // Not valid JSON, fallback
            }
        }

        // 1.5 HTML detection
        const isHtml = trimmed.toLowerCase().startsWith('<!doctype html') || 
                       trimmed.toLowerCase().startsWith('<html') ||
                       (trimmed.startsWith('<') && trimmed.endsWith('>') && trimmed.includes('</'));
        if (isHtml) {
            return `\`\`\`html\n${trimmed}\n\`\`\``;
        }

        // 2. LaTeX detection
        const hasLatexCommands = /\\[a-zA-Z]+/.test(trimmed) || /[\^]/.test(trimmed) || /\_[a-zA-Z0-9]/.test(trimmed);
        const hasMathDelimiters = trimmed.includes('$');

        if (hasLatexCommands && !hasMathDelimiters) {
            if (trimmed.includes('\n')) {
                return `$$\n${trimmed}\n$$`;
            } else {
                return `$${trimmed}$`;
            }
        }

        // 3. Plain Text / Markdown
        return trimmed
            .split('\n\n')
            .map(p => p.replace(/\n/g, '  \n'))
            .join('\n\n');
    }

    function compileSectionToMarkdown(section, isPreview = false) {
        const parts = [];

        if (section && section.blocks) {
            section.blocks.forEach(block => {
                // If this block starts a sub-question, prepend a sub-question markdown heading
                if (block.is_sub_start) {
                    const isProblem = section === state.problem;
                    const prefix = isProblem ? '小問 ' : '小問解説 ';
                    parts.push(`### ${prefix}${block.sub_number || ''}`);
                }

                if (block.type === 'text') {
                    const md = compileTextBlock(block.content);
                    if (md) parts.push(md);
                } else if (block.type === 'shape') {
                    let md = '';
                    const figTitle = block.title || '';
                    const altText = figTitle || '図';
                    if (block.compiled_image_url) {
                        let imgUrl = block.compiled_image_url;
                        if (isPreview && block.compiled_image_timestamp) {
                            imgUrl += `?t=${block.compiled_image_timestamp}`;
                        }
                        md += `![${altText}](${imgUrl})\n\n`;
                    }
                    const titleLine = figTitle ? `\n# TITLE: ${figTitle}` : '';
                    md += `\`\`\`python\n# TYPE: DIAGRAM${titleLine}\n# BLOCK_ID: ${block.id}\n${block.content}\n\`\`\``;
                    parts.push(md);
                }
            });
        }

        const compiledText = parts.join('\n\n');

        if (!isPreview) {
            const rawData = {
                version: 'sansu-base-section-v3',
                blocks: (section && section.blocks) ? section.blocks.map(b => ({
                    id: b.id,
                    type: b.type,
                    content: b.content,
                    title: b.title,
                    compiled_image_url: b.compiled_image_url,
                    elev: b.elev,
                    azim: b.azim,
                    is_sub_start: b.is_sub_start || false,
                    sub_number: b.sub_number || ''
                })) : []
            };
            return compiledText + `\n\n<!-- SANSUB_BLOCKS: ${JSON.stringify(rawData)} -->`;
        }

        return compiledText;
    }

    // ================================================================
    // Preview Management
    // ================================================================
    function updatePreviewMeta() {
        const displayEl = document.getElementById('workspace-title-display');
        if (displayEl) {
            let titleParts = [];
            if (state.source_book) titleParts.push(state.source_book);
            if (state.chapter) titleParts.push(`${state.chapter}章/回`);
            if (state.unit) titleParts.push(`単元: ${state.unit}`);
            if (state.strategy) titleParts.push(`${state.strategy}ページ`);
            if (state.problem_number) titleParts.push(`No.${state.problem_number}`);
            
            displayEl.textContent = titleParts.length > 0 ? titleParts.join(' | ') : '(未設定)';
        }
    }

    function renderReadOnlyPreview(section, container, sectionKey) {
        container.innerHTML = '';
        
        if (!section || !section.blocks || section.blocks.length === 0) {
            container.innerHTML = `<span class="empty-preview-placeholder">${sectionKey === 'problem' ? '問題文' : '解説文'}のブロックを追加すると、ここにレンダリング結果が表示されます。</span>`;
            return;
        }

        section.blocks.forEach((block, idx) => {
            if (block.is_sub_start) {
                const hdr = document.createElement('div');
                hdr.className = 'preview-sub-header';
                hdr.style.cssText = 'font-weight: 700; font-size: 1.1rem; color: #1e293b; margin: 1.5rem 0 0.5rem 0; border-bottom: 2px solid #e2e8f0; padding-bottom: 0.25rem;';
                const prefix = sectionKey === 'problem' ? '小問 ' : '小問解説 ';
                hdr.innerHTML = `<strong>${escapeHtml(prefix)}${escapeHtml(block.sub_number || '')}</strong>`;
                container.appendChild(hdr);
            }

            const wrapper = document.createElement('div');
            wrapper.className = 'preview-block-wrapper-readonly';
            wrapper.dataset.blockId = block.id;

            const content = document.createElement('div');
            content.className = 'preview-block-content-readonly';

            if (block.type === 'text') {
                if (isHtmlContent(block.content)) {
                    renderHtmlInIframe(content, block.content, false);
                } else {
                    const compiledMd = compileTextBlock(block.content);
                    const rendered = renderMarkdownToHtml(compiledMd);
                    content.innerHTML = rendered || '<span style="color:#94a3b8; font-style:italic;">（空のテキストブロック）</span>';
                }
            } else if (block.type === 'shape') {
                const figTitle = block.title || '';
                if (block.compiled_image_url) {
                    const cacheBuster = block.compiled_image_timestamp ? `?t=${block.compiled_image_timestamp}` : '';
                    const titleHtml = figTitle ? `<div class="preview-figure-title" style="font-size:0.9rem; font-weight:700; color:#7c3aed; margin-top:0.5rem; text-align:center;">${escapeHtml(figTitle)}</div>` : '';
                    
                    content.innerHTML = `
                        <div class="preview-figure-container" style="text-align:center; margin:0.75rem 0;">
                            <img src="${block.compiled_image_url}${cacheBuster}" alt="${escapeHtml(figTitle || '図')}" style="width:100%; height:auto; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.05); border:1px solid #e2e8f0; display:block;">
                            ${titleHtml}
                        </div>
                    `;
                } else {
                    const label = figTitle ? `${escapeHtml(figTitle)} — 未描画` : '図 — 未描画';
                    content.innerHTML = `<div style="text-align:center; padding:1rem; color:#94a3b8; font-style:italic;"><i class="fa-solid fa-shapes" style="margin-right:0.25rem;"></i>${label}</div>`;
                }
            }

            wrapper.appendChild(content);
            container.appendChild(wrapper);

            wrapper.addEventListener('click', () => {
                setActiveBlock(block.id);
                btnViewElements.click();
            });
        });
    }

    function renderActivePreview() {
        const activeContainer = document.getElementById('active-preview-container');
        if (!activeContainer) return;

        if (!state.active_block_id) {
            activeContainer.innerHTML = `
                <div style="text-align:center; padding:3rem 1.5rem; color:#94a3b8; font-style:italic;">
                    <i class="fa-solid fa-square-poll-horizontal" style="font-size:2.5rem; opacity:0.3; margin-bottom:1rem; display:block; color:var(--accent-primary);"></i>
                    左側のエディタでブロックをクリックまたは編集すると、ここに詳細な個別プレビューが表示されます。
                </div>
            `;
            return;
        }

        const found = findBlockById(state.active_block_id);
        if (!found) {
            state.active_block_id = null;
            renderActivePreview();
            return;
        }

        const { block, sectionKey } = found;
        const blockTypeLabel = block.type === 'text' ? '文' : 'Python図形';
        const sectionLabel = sectionKey === 'problem' ? '問題文' : '解説文';
        
        let activeSubNum = '';
        const blocks = state[sectionKey].blocks;
        const currentIdx = blocks.findIndex(b => b.id === block.id);
        if (currentIdx !== -1) {
            for (let i = currentIdx; i >= 0; i--) {
                if (blocks[i].is_sub_start) {
                    activeSubNum = blocks[i].sub_number;
                    break;
                }
            }
        }
        let contextLabel = `${sectionLabel}`;
        if (activeSubNum) {
            contextLabel += ` > 小問 ${activeSubNum}`;
        } else {
            contextLabel += ` > 大問共通`;
        }

        let previewHtml = '';

        if (block.type === 'text') {
            if (isHtmlContent(block.content)) {
                previewHtml = `<div class="active-preview-content-box" style="padding:1.5rem; background:white; border:1px solid #e2e8f0; border-radius:8px; min-height:100px;">
                    <div class="iframe-preview-wrapper" style="width:100%;"></div>
                </div>`;
            } else {
                const compiledMd = compileTextBlock(block.content);
                const rendered = renderMarkdownToHtml(compiledMd);
                previewHtml = `<div class="active-preview-content-box markdown-body" style="padding:1.5rem; background:white; border:1px solid #e2e8f0; border-radius:8px; min-height:100px; font-size:1.05rem; line-height:1.7; color:#1e293b;">
                    ${rendered || '<span style="color:#94a3b8; font-style:italic;">（空のテキストブロック）</span>'}
                </div>`;
            }
        } else if (block.type === 'shape') {
            const figTitle = block.title || '';
            const titleHtml = figTitle ? `<div class="preview-figure-title" style="font-size:1.05rem; font-weight:700; color:#7c3aed; margin-top:0.75rem; text-align:center;">${escapeHtml(figTitle)}</div>` : '';
            
            let rotationSlidersHtml = '';
            let imageOrStatus = '';

            if (block.compiled_image_url) {
                const cacheBuster = block.compiled_image_timestamp ? `?t=${block.compiled_image_timestamp}` : '';
                imageOrStatus = `<img src="${block.compiled_image_url}${cacheBuster}" alt="${escapeHtml(figTitle || '図')}" style="width:100%; max-width:550px; height:auto; border-radius:10px; box-shadow:0 8px 30px rgba(15, 23, 42, 0.08); border:1px solid #e2e8f0; display:block; margin:0 auto;">`;
                
                const angles = extractViewInitAngles(block.content);
                if (angles.has3d) {
                    const elev = block.elev !== undefined ? block.elev : angles.elev;
                    const azim = block.azim !== undefined ? block.azim : angles.azim;

                    rotationSlidersHtml = `
                        <div class="active-diagram-rotation-controls" style="margin-top:1.5rem; padding:1rem; background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px;">
                            <div style="font-weight:700; color:#4f46e5; margin-bottom:0.75rem; display:flex; align-items:center; gap:0.4rem; font-size:0.85rem;">
                                <i class="fa-solid fa-rotate"></i> 3D立体図の視点調整 (角度)
                            </div>
                            <div style="display:flex; flex-direction:column; gap:0.75rem;">
                                <div style="display:flex; align-items:center; gap:1rem;">
                                    <span style="width:75px; font-weight:700; color:#475569; font-size:0.8rem;">上下 (elev):</span>
                                    <input type="range" class="active-slider-diagram-elev" data-id="${block.id}" min="-90" max="90" value="${elev}" style="flex:1; accent-color:#4f46e5; height:6px; cursor:pointer;">
                                    <span class="active-val-diagram-elev" style="width:35px; text-align:right; font-weight:700; color:#0f172a; font-size:0.85rem;">${elev}°</span>
                                </div>
                                <div style="display:flex; align-items:center; gap:1rem;">
                                    <span style="width:75px; font-weight:700; color:#475569; font-size:0.8rem;">左右 (azim):</span>
                                    <input type="range" class="active-slider-diagram-azim" data-id="${block.id}" min="-180" max="180" value="${azim}" style="flex:1; accent-color:#4f46e5; height:6px; cursor:pointer;">
                                    <span class="active-val-diagram-azim" style="width:35px; text-align:right; font-weight:700; color:#0f172a; font-size:0.85rem;">${azim}°</span>
                                </div>
                            </div>
                        </div>
                    `;
                }
            } else if (block.compile_error) {
                imageOrStatus = `<div class="diagram-error-text" style="width:100%; background:#fef2f2; border:1px solid #fee2e2; padding:1rem; border-radius:8px; color:#ef4444; font-family:monospace; font-size:0.85rem; white-space:pre-wrap; text-align:left;">${escapeHtml(block.compile_error)}</div>`;
            } else {
                const label = figTitle ? `「${escapeHtml(figTitle)}」` : '';
                imageOrStatus = `<div style="text-align:center; padding:3rem 1.5rem; color:#94a3b8; font-style:italic; background:#fafafa; border:1px dashed #cbd5e1; border-radius:8px;">
                    <i class="fa-solid fa-shapes" style="font-size:2rem; opacity:0.3; margin-bottom:0.75rem; display:block;"></i>
                    図形 ${label} は未描画です。左のエディタで「図形を描画」をクリックしてください。
                </div>`;
            }

            previewHtml = `
                <div class="active-preview-content-box" style="padding:1rem; background:white; border:1px solid #e2e8f0; border-radius:8px; text-align:center;">
                    ${imageOrStatus}
                    ${figTitle ? titleHtml : ''}
                    ${rotationSlidersHtml}
                </div>
            `;
        }

        activeContainer.innerHTML = `
            <div class="active-preview-card" style="display:flex; flex-direction:column; gap:1rem;">
                <div class="active-preview-header" style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #e2e8f0; padding-bottom:0.75rem;">
                    <div style="display:flex; flex-direction:column; gap:0.25rem;">
                        <span style="font-size:0.75rem; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.05em;">${contextLabel}</span>
                        <div style="display:flex; align-items:center; gap:0.5rem; margin-top:0.1rem;">
                            <span class="badge" style="background:${block.type === 'text' ? '#475569' : '#8b5cf6'}; color:white; font-size:0.8rem; padding:0.2rem 0.5rem; border-radius:4px; font-weight:700;">
                                <i class="fa-solid ${block.type === 'text' ? 'fa-file-lines' : 'fa-shapes'}"></i> ${blockTypeLabel}
                            </span>
                        </div>
                    </div>
                </div>
                ${previewHtml}
            </div>
        `;

        if (block.type === 'text' && isHtmlContent(block.content)) {
            const wrapper = activeContainer.querySelector('.iframe-preview-wrapper');
            if (wrapper) {
                renderHtmlInIframe(wrapper, block.content, false);
            }
        }

        if (block.type === 'shape' && block.compiled_image_url) {
            const elevSlider = activeContainer.querySelector('.active-slider-diagram-elev');
            const azimSlider = activeContainer.querySelector('.active-slider-diagram-azim');
            const elevVal = activeContainer.querySelector('.active-val-diagram-elev');
            const azimVal = activeContainer.querySelector('.active-val-diagram-azim');

            if (elevSlider) {
                elevSlider.addEventListener('input', (e) => {
                    const val = e.target.value;
                    elevVal.textContent = `${val}°`;
                    const leftSlider = document.querySelector(`.slider-diagram-elev[data-id="${block.id}"]`);
                    if (leftSlider) {
                        leftSlider.value = val;
                        const leftLabel = leftSlider.parentNode.querySelector('.val-diagram-elev');
                        if (leftLabel) leftLabel.textContent = `${val}°`;
                    }
                });
                elevSlider.addEventListener('change', (e) => {
                    const val = parseInt(e.target.value);
                    updateDiagramRotation(block.id, val, null);
                });
            }

            if (azimSlider) {
                azimSlider.addEventListener('input', (e) => {
                    const val = e.target.value;
                    azimVal.textContent = `${val}°`;
                    const leftSlider = document.querySelector(`.slider-diagram-azim[data-id="${block.id}"]`);
                    if (leftSlider) {
                        leftSlider.value = val;
                        const leftLabel = leftSlider.parentNode.querySelector('.val-diagram-azim');
                        if (leftLabel) leftLabel.textContent = `${val}°`;
                    }
                });
                azimSlider.addEventListener('change', (e) => {
                    const val = parseInt(e.target.value);
                    updateDiagramRotation(block.id, null, val);
                });
            }
        }

        if (block.type === 'text' && !isHtmlContent(block.content) && window.MathJax && window.MathJax.typesetPromise) {
            window.MathJax.typesetPromise([activeContainer]).catch(err => console.error(err));
        }
    }

    function setActiveBlock(blockId) {
        state.active_block_id = blockId;
        
        document.querySelectorAll('.block-card').forEach(card => {
            if (card.id === blockId) {
                card.classList.add('active');
            } else {
                card.classList.remove('active');
            }
        });

        // Switch to active block preview tab automatically
        const activeTabBtn = document.getElementById('tab-active-preview');
        if (activeTabBtn && !activeTabBtn.classList.contains('active')) {
            document.querySelectorAll('.preview-tab-btn').forEach(b => b.classList.remove('active'));
            activeTabBtn.classList.add('active');
            document.getElementById('preview-active-pane').style.display = 'block';
            document.getElementById('preview-full-pane').style.display = 'none';
        }

        renderActivePreview();
    }

    function getFirstBlockOfCurrentTab() {
        const isProblem = state.current_tab === 'problem';
        const section = isProblem ? state.problem : state.explanation;
        if (section && section.blocks && section.blocks.length > 0) {
            return section.blocks[0];
        }
        return null;
    }

    // Temporary state management within the reorder modal
    // Temporary state management within the layout tab
    let tempModalState = null;

    function countSubQuestionsSoFar(blockId) {
        const section = tempModalState[activeLayoutTab];
        let count = 0;
        for (const b of section.blocks) {
            if (b.is_sub_start) {
                count++;
            }
            if (b.id === blockId) {
                break;
            }
        }
        return count;
    }

    function renderModalReorderList() {
        if (!tempModalState) return;
        
        const section = tempModalState[activeLayoutTab];
        const container = document.getElementById('reorder-list-container');
        container.innerHTML = '';

        if (!section || !section.blocks || section.blocks.length === 0) {
            container.innerHTML = `
                <div style="text-align:center; padding:2rem; color:#94a3b8; font-size:0.85rem; border:1px dashed #cbd5e1; border-radius:8px; background:#f8fafc;">
                    並べ替えるブロックがありません。
                </div>
            `;
            return;
        }

        section.blocks.forEach((block, idx) => {
            container.appendChild(createReorderItemCard(block, idx, section.blocks.length));
        });

        setupReorderDragAndDrop();
        attachModalListEventListeners();
    }

    function attachModalListEventListeners() {
        const container = document.getElementById('reorder-list-container');

        // Checkbox events
        container.querySelectorAll('.reorder-sub-start-chk').forEach(chk => {
            chk.addEventListener('change', (e) => {
                const blockId = e.target.dataset.blockId;
                const card = e.target.closest('.reorder-block-card');
                const numContainer = card.querySelector('.reorder-sub-num-container');
                const numInput = card.querySelector('.reorder-sub-num-input');
                
                const isChecked = e.target.checked;
                if (isChecked) {
                    numContainer.style.display = 'inline-flex';
                    if (numInput && !numInput.value) {
                        const count = countSubQuestionsSoFar(blockId);
                        numInput.value = `(${count + 1})`;
                    }
                } else {
                    numContainer.style.display = 'none';
                }
                
                // Update state
                const section = tempModalState[activeLayoutTab];
                const block = section.blocks.find(b => b.id === blockId);
                if (block) {
                    block.is_sub_start = isChecked;
                    if (isChecked && numInput) {
                        block.sub_number = numInput.value;
                    } else {
                        block.sub_number = '';
                    }
                }
                
                updateModalPreview();
            });
        });

        // Sub-question number inputs
        container.querySelectorAll('.reorder-sub-num-input').forEach(input => {
            input.addEventListener('input', (e) => {
                const blockId = e.target.dataset.blockId;
                const section = tempModalState[activeLayoutTab];
                const block = section.blocks.find(b => b.id === blockId);
                if (block) {
                    block.sub_number = e.target.value;
                    updateModalPreview();
                }
            });
        });

        // Up/Down/Delete actions for cards
        container.querySelectorAll('.btn-modal-block-ctrl').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const blockId = btn.dataset.blockId;
                const action = btn.dataset.action;
                const section = tempModalState[activeLayoutTab];
                const idx = section.blocks.findIndex(b => b.id === blockId);

                if (action === 'delete') {
                    if (confirm('このブロックを削除しますか？')) {
                        section.blocks.splice(idx, 1);
                        renderModalReorderList();
                        updateModalPreview();
                    }
                } else if (action === 'up' && idx > 0) {
                    const temp = section.blocks[idx];
                    section.blocks[idx] = section.blocks[idx - 1];
                    section.blocks[idx - 1] = temp;
                    renderModalReorderList();
                    updateModalPreview();
                } else if (action === 'down' && idx < section.blocks.length - 1) {
                    const temp = section.blocks[idx];
                    section.blocks[idx] = section.blocks[idx + 1];
                    section.blocks[idx + 1] = temp;
                    renderModalReorderList();
                    updateModalPreview();
                }
            });
        });
    }

    function createReorderItemCard(block, idx, blocksCount) {
        const item = document.createElement('div');
        item.className = 'reorder-item reorder-block-card';
        item.draggable = true;
        item.dataset.blockId = block.id;

        const badgeClass = block.type === 'text' ? 'reorder-badge-text' : 'reorder-badge-shape';
        const badgeLabel = block.type === 'text' ? '文' : '図';
        
        let snippet = '';
        if (block.type === 'text') {
            const cleanText = block.content ? block.content.trim().replace(/\n/g, ' ') : '';
            snippet = cleanText.length > 40 ? cleanText.substring(0, 40) + '...' : cleanText;
            if (!snippet) snippet = '（空のテキストブロック）';
        } else {
            snippet = block.title ? `[${block.title}]` : '（タイトルなし）';
            const cleanCode = block.content ? block.content.trim().replace(/\n/g, ' ') : '';
            snippet += ' ' + (cleanCode.length > 25 ? cleanCode.substring(0, 25) + '...' : cleanCode);
        }

        const isSubStart = !!block.is_sub_start;
        const subNumber = block.sub_number || '';

        item.innerHTML = `
            <div style="display:flex; align-items:center; justify-content:space-between; width:100%; gap:0.5rem; flex-wrap:wrap; padding: 0.25rem 0;">
                <div style="display:flex; align-items:center; gap:0.5rem; flex:1; min-width:0;">
                    <div class="reorder-handle" style="cursor:grab; padding:0.25rem; color:#94a3b8;"><i class="fa-solid fa-grip-vertical"></i></div>
                    <span class="reorder-badge ${badgeClass}" style="flex-shrink:0;">${badgeLabel}</span>
                    <span class="block-badge-index" style="background:#cbd5e1; color:#475569; border-radius:50%; width:18px; height:18px; display:inline-flex; align-items:center; justify-content:center; font-size:0.7rem; font-weight:700; flex-shrink:0;">${idx + 1}</span>
                    <div class="reorder-block-info" style="font-size:0.8rem; font-weight:500; color:#334155; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1;">${escapeHtml(snippet)}</div>
                </div>
                <div style="display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap;">
                    <label style="display:inline-flex; align-items:center; gap:0.25rem; font-size:0.78rem; font-weight:600; color:#475569; cursor:pointer;">
                        <input type="checkbox" class="reorder-sub-start-chk" data-block-id="${block.id}" ${isSubStart ? 'checked' : ''} style="cursor:pointer;">
                        <span>ここから小問にする</span>
                    </label>
                    <div class="reorder-sub-num-container" style="display:${isSubStart ? 'inline-flex' : 'none'}; align-items:center; gap:0.2rem; font-size:0.78rem; font-weight:600; color:#475569;">
                        <span>番号:</span>
                        <input type="text" class="reorder-sub-num-input" data-block-id="${block.id}" value="${escapeHtml(subNumber)}" placeholder="例: (1)" style="width:50px; padding:0.15rem 0.25rem; border:1px solid #cbd5e1; border-radius:4px; font-size:0.78rem; font-weight:600; height:24px;">
                    </div>
                    <div class="block-controls" style="display:flex; gap:0.15rem; flex-shrink:0;">
                        <button type="button" class="btn-ctrl btn-modal-block-ctrl" data-action="up" data-block-id="${block.id}" title="上に移動" ${idx === 0 ? 'disabled style="opacity:0.3; cursor:not-allowed;"' : ''}><i class="fa-solid fa-arrow-up" style="font-size:0.7rem;"></i></button>
                        <button type="button" class="btn-ctrl btn-modal-block-ctrl" data-action="down" data-block-id="${block.id}" title="下に移動" ${idx === blocksCount - 1 ? 'disabled style="opacity:0.3; cursor:not-allowed;"' : ''}><i class="fa-solid fa-arrow-down" style="font-size:0.7rem;"></i></button>
                        <button type="button" class="btn-ctrl btn-ctrl-danger btn-modal-block-ctrl" data-action="delete" data-block-id="${block.id}" title="削除"><i class="fa-solid fa-trash" style="font-size:0.7rem;"></i></button>
                    </div>
                </div>
            </div>
        `;
        return item;
    }

    let reorderDragEl = null;

    function setupReorderDragAndDrop() {
        const container = document.getElementById('reorder-list-container');
        const elements = container.querySelectorAll('.reorder-block-card');
        
        elements.forEach(el => {
            el.addEventListener('dragstart', function(e) {
                reorderDragEl = this;
                e.dataTransfer.effectAllowed = 'move';
                this.classList.add('dragging');
            });

            el.addEventListener('dragend', function() {
                this.classList.remove('dragging');
                reorderDragEl = null;
                rebuildTempModalStateFromDOM();
                renderModalReorderList();
                updateModalPreview();
            });

            el.addEventListener('dragover', function(e) {
                e.preventDefault();
                if (!reorderDragEl || this === reorderDragEl) return;
                
                const children = Array.from(container.children);
                const indexDrag = children.indexOf(reorderDragEl);
                const indexHover = children.indexOf(this);
                
                if (indexDrag < indexHover) {
                    this.after(reorderDragEl);
                } else {
                    this.before(reorderDragEl);
                }
            });
        });
    }

    function rebuildTempModalStateFromDOM() {
        if (!tempModalState) return;
        const container = document.getElementById('reorder-list-container');
        const cards = Array.from(container.querySelectorAll('.reorder-block-card'));
        
        const currentBlocks = tempModalState[activeLayoutTab].blocks;
        const reorderedBlocks = [];
        
        cards.forEach(card => {
            const blockId = card.dataset.blockId;
            const originalBlock = currentBlocks.find(b => b.id === blockId);
            if (originalBlock) {
                const chk = card.querySelector('.reorder-sub-start-chk');
                const numInput = card.querySelector('.reorder-sub-num-input');
                
                originalBlock.is_sub_start = chk ? chk.checked : false;
                originalBlock.sub_number = numInput ? numInput.value : '';
                
                reorderedBlocks.push(originalBlock);
            }
        });
        
        tempModalState[activeLayoutTab].blocks = reorderedBlocks;
    }

    function updateModalPreview() {
        const previewMetaTitle = document.getElementById('modal-preview-meta-title');
        const previewUnitBadge = document.getElementById('modal-preview-unit-badge');
        const previewIdBadge = document.getElementById('modal-preview-id-badge');
        const previewStrategyBox = document.getElementById('modal-preview-strategy-box');

        if (tempModalState.source_book || tempModalState.chapter) {
            previewMetaTitle.textContent = `${tempModalState.source_book}${tempModalState.chapter ? ` ${tempModalState.chapter}` : ''}`;
        } else {
            previewMetaTitle.textContent = '(教材名が入ります)';
        }

        if (tempModalState.unit) {
            previewUnitBadge.textContent = `単元: ${tempModalState.unit}`;
            previewUnitBadge.style.display = 'inline-block';
        } else {
            previewUnitBadge.style.display = 'none';
        }

        if (state.display_id) {
            previewIdBadge.textContent = `ID: ${state.display_id}`;
        } else {
            previewIdBadge.textContent = 'ID: AUTO';
        }

        if (tempModalState.strategy || tempModalState.problem_number) {
            let metaParts = [];
            if (tempModalState.strategy) metaParts.push(`<strong>ページ数:</strong> ${tempModalState.strategy} ページ`);
            if (tempModalState.problem_number) metaParts.push(`<strong>問題番号:</strong> ${tempModalState.problem_number}`);
            previewStrategyBox.innerHTML = metaParts.join('　');
            previewStrategyBox.style.display = 'block';
        } else {
            previewStrategyBox.style.display = 'none';
        }

        const layoutPreviewTitle = document.getElementById('layout-preview-title');
        if (layoutPreviewTitle) {
            layoutPreviewTitle.textContent = activeLayoutTab === 'problem' ? '問題文プレビュー' : '解説文プレビュー';
        }

        const splitContentEl = document.getElementById('modal-preview-split-content');
        if (splitContentEl) {
            renderReadOnlyPreview(tempModalState[activeLayoutTab], splitContentEl, activeLayoutTab);
        }

        clearTimeout(previewTimer);
        previewTimer = setTimeout(renderMath, 450);
    }

    function updateCompiledPreview() {
        // Main screen only shows focused block preview
        renderActivePreview();
    }

    let livePreviewTimer = null;
    function triggerLivePreview() {
        clearTimeout(livePreviewTimer);
        livePreviewTimer = setTimeout(updateCompiledPreview, 250);
    }

    // ================================================================
    // Event Handler Registry
    // ================================================================
    function attachEventListeners() {
        // Block textareas
        document.querySelectorAll('.block-textarea').forEach(textarea => {
            // Remove previous listeners using clone node or re-bind
            const newTextarea = textarea.cloneNode(true);
            textarea.parentNode.replaceChild(newTextarea, textarea);
            
            newTextarea.addEventListener('paste', (e) => {
                const pasteData = (e.clipboardData || window.clipboardData).getData('text');
                const trimmed = pasteData.trim();
                if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
                    try {
                        const parsed = JSON.parse(trimmed);
                        if (Array.isArray(parsed) && parsed.length > 0 && parsed[0].type) {
                            e.preventDefault();
                            const card = e.target.closest('.block-card');
                            if (card) {
                                expandJsonBlocksAt(card.id, parsed);
                            }
                        }
                    } catch (err) {
                        // Not valid block JSON, let default paste happen
                    }
                }
            });

            newTextarea.addEventListener('input', (e) => {
                const id = e.target.closest('.block-card').id;
                const value = e.target.value;
                updateNestedBlockContent(id, value);
                
                // Update local preview box if it is currently open
                const previewBox = document.getElementById(`preview-box-${id}`);
                if (previewBox && previewBox.style.display !== 'none') {
                    if (isHtmlContent(value)) {
                        clearTimeout(previewBox.iframeTimer);
                        previewBox.iframeTimer = setTimeout(() => {
                            renderHtmlInIframe(previewBox, value, false);
                        }, 300);
                    } else {
                        const compiledMd = compileTextBlock(value);
                        previewBox.innerHTML = renderMarkdownToHtml(compiledMd);
                        if (window.MathJax && window.MathJax.typesetPromise) {
                            clearTimeout(previewBox.timer);
                            previewBox.timer = setTimeout(() => {
                                window.MathJax.typesetPromise([previewBox]).catch(err => console.error(err));
                            }, 300);
                        }
                    }
                }
            });
        });

        // Subquestion numbers
        document.querySelectorAll('.subquestion-num-val').forEach(input => {
            const newInput = input.cloneNode(true);
            input.parentNode.replaceChild(newInput, input);
            
            newInput.addEventListener('input', (e) => {
                const subId = e.target.dataset.subid;
                updateSubQuestionNumber(subId, e.target.value);
            });
        });

        // Block controls (Up/Down/Duplicate/Delete/Insert)
        document.querySelectorAll('.btn-block-ctrl').forEach(btn => {
            const newBtn = btn.cloneNode(true);
            btn.parentNode.replaceChild(newBtn, btn);
            
            newBtn.addEventListener('click', (e) => {
                const action = newBtn.dataset.action;
                const id = newBtn.dataset.id;
                if (action === 'delete') {
                    if (confirm('このブロックを削除してもよろしいですか？')) deleteNestedBlock(id);
                } else if (action === 'duplicate') {
                    duplicateNestedBlock(id);
                } else if (action === 'up') {
                    moveNestedBlock(id, 'up');
                } else if (action === 'down') {
                    moveNestedBlock(id, 'down');
                } else if (action === 'insert-text') {
                    insertNestedBlockAfter(id, 'text');
                } else if (action === 'insert-shape') {
                    insertNestedBlockAfter(id, 'shape');
                }
            });
        });

        // Subquestion controls (Up/Down/Delete)
        document.querySelectorAll('.btn-sub-ctrl').forEach(btn => {
            const newBtn = btn.cloneNode(true);
            btn.parentNode.replaceChild(newBtn, btn);
            
            newBtn.addEventListener('click', (e) => {
                const action = newBtn.dataset.action;
                const subId = newBtn.dataset.subid;
                if (action === 'delete') {
                    if (confirm('この小問（およびその中のブロック）を削除しますか？')) deleteSubQuestion(subId);
                } else if (action === 'up') {
                    moveSubQuestion(subId, 'up');
                } else if (action === 'down') {
                    moveSubQuestion(subId, 'down');
                }
            });
        });

        // Python rendering
        document.querySelectorAll('.btn-draw-diagram').forEach(btn => {
            const newBtn = btn.cloneNode(true);
            btn.parentNode.replaceChild(newBtn, btn);
            
            newBtn.addEventListener('click', (e) => {
                const id = newBtn.dataset.id;
                executeDiagramBlock(id);
            });
        });

        // Text preview button
        document.querySelectorAll('.btn-preview-text').forEach(btn => {
            const newBtn = btn.cloneNode(true);
            btn.parentNode.replaceChild(newBtn, btn);
            
            newBtn.addEventListener('click', (e) => {
                const id = newBtn.dataset.id;
                toggleTextBlockPreview(id, newBtn);
            });
        });

        // Figure title inputs
        document.querySelectorAll('.input-figure-title').forEach(input => {
            const newInput = input.cloneNode(true);
            input.parentNode.replaceChild(newInput, input);
            
            newInput.addEventListener('input', (e) => {
                const id = newInput.dataset.id;
                updateBlockTitle(id, e.target.value);
            });
        });

        // Nested block adding buttons (Common or inside subquestion)
        document.querySelectorAll('.btn-add-nested-block').forEach(btn => {
            const newBtn = btn.cloneNode(true);
            btn.parentNode.replaceChild(newBtn, btn);
            
            newBtn.addEventListener('click', (e) => {
                const target = newBtn.dataset.target;
                const type = newBtn.dataset.type;
                addNestedBlock(target, type);
            });
        });

        // Sub-question adding buttons
        document.querySelectorAll('.btn-add-sub').forEach(btn => {
            const newBtn = btn.cloneNode(true);
            btn.parentNode.replaceChild(newBtn, btn);
            
            newBtn.addEventListener('click', (e) => {
                const target = newBtn.dataset.target;
                addSubQuestion(target);
            });
        });

        // 3D rotation sliders (elev)
        document.querySelectorAll('.slider-diagram-elev').forEach(slider => {
            const newSlider = slider.cloneNode(true);
            slider.parentNode.replaceChild(newSlider, slider);
            
            newSlider.addEventListener('input', (e) => {
                const val = e.target.value;
                const label = newSlider.parentNode.querySelector('.val-diagram-elev');
                if (label) label.textContent = `${val}°`;
                
                // Sync preview slider if visible
                const rightSlider = document.querySelector(`.active-slider-diagram-elev[data-id="${newSlider.dataset.id}"]`);
                if (rightSlider) {
                    rightSlider.value = val;
                    const rightLabel = rightSlider.parentNode.querySelector('.active-val-diagram-elev');
                    if (rightLabel) rightLabel.textContent = `${val}°`;
                }
            });
            newSlider.addEventListener('change', (e) => {
                const id = newSlider.dataset.id;
                const val = parseInt(e.target.value);
                updateDiagramRotation(id, val, null);
            });
        });

        // 3D rotation sliders (azim)
        document.querySelectorAll('.slider-diagram-azim').forEach(slider => {
            const newSlider = slider.cloneNode(true);
            slider.parentNode.replaceChild(newSlider, slider);
            
            newSlider.addEventListener('input', (e) => {
                const val = e.target.value;
                const label = newSlider.parentNode.querySelector('.val-diagram-azim');
                if (label) label.textContent = `${val}°`;
                
                // Sync preview slider if visible
                const rightSlider = document.querySelector(`.active-slider-diagram-azim[data-id="${newSlider.dataset.id}"]`);
                if (rightSlider) {
                    rightSlider.value = val;
                    const rightLabel = rightSlider.parentNode.querySelector('.active-val-diagram-azim');
                    if (rightLabel) rightLabel.textContent = `${val}°`;
                }
            });
            newSlider.addEventListener('change', (e) => {
                const id = newSlider.dataset.id;
                const val = parseInt(e.target.value);
                updateDiagramRotation(id, null, val);
            });
        });

        // Active block card clicks and focuses
        document.querySelectorAll('.block-card').forEach(card => {
            card.addEventListener('click', (e) => {
                if (e.target.closest('.block-controls') || e.target.closest('.btn-block-ctrl') || e.target.closest('.btn-ctrl') || e.target.closest('.input-figure-title')) {
                    return;
                }
                setActiveBlock(card.id);
            });
            
            const textarea = card.querySelector('.block-textarea');
            if (textarea) {
                textarea.addEventListener('focus', () => {
                    setActiveBlock(card.id);
                });
            }
        });
    }

    // Form inputs binding (Modal fields)
    modalSourceBookInput.addEventListener('input', (e) => {
        if (tempModalState) {
            tempModalState.source_book = e.target.value.trim();
            updateModalPreview();
        }
    });

    modalChapterInput.addEventListener('input', (e) => {
        if (tempModalState) {
            const start = e.target.selectionStart;
            const end = e.target.selectionEnd;
            const val = e.target.value;
            const normalized = toHalfWidth(val);
            if (val !== normalized) {
                e.target.value = normalized;
                if (document.activeElement === e.target) {
                    e.target.setSelectionRange(start, end);
                }
            }
            tempModalState.chapter = e.target.value.trim();
            updateModalPreview();
        }
    });

    modalUnitInput.addEventListener('input', (e) => {
        if (tempModalState) {
            tempModalState.unit = e.target.value.trim();
            updateModalPreview();
        }
    });

    modalStrategyInput.addEventListener('input', (e) => {
        if (tempModalState) {
            const start = e.target.selectionStart;
            const end = e.target.selectionEnd;
            const val = e.target.value;
            const normalized = toHalfWidth(val);
            if (val !== normalized) {
                e.target.value = normalized;
                if (document.activeElement === e.target) {
                    e.target.setSelectionRange(start, end);
                }
            }
            tempModalState.strategy = e.target.value.trim();
            updateModalPreview();
        }
    });

    modalProblemNumberInput.addEventListener('input', (e) => {
        if (tempModalState) {
            const start = e.target.selectionStart;
            const end = e.target.selectionEnd;
            const val = e.target.value;
            const normalized = toHalfWidth(val);
            if (val !== normalized) {
                e.target.value = normalized;
                if (document.activeElement === e.target) {
                    e.target.setSelectionRange(start, end);
                }
            }
            tempModalState.problem_number = e.target.value.trim();
            updateModalPreview();
        }
    });

    function switchLayoutTab(tabName) {
        activeLayoutTab = tabName;
        if (tabName === 'problem') {
            layoutTabProblem.classList.add('active');
            layoutTabProblem.style.background = 'white';
            layoutTabProblem.style.color = 'var(--accent-primary)';
            layoutTabProblem.style.boxShadow = '0 1px 3px rgba(0,0,0,0.05)';

            layoutTabExplanation.classList.remove('active');
            layoutTabExplanation.style.background = 'transparent';
            layoutTabExplanation.style.color = '#64748b';
            layoutTabExplanation.style.boxShadow = 'none';
        } else {
            layoutTabExplanation.classList.add('active');
            layoutTabExplanation.style.background = 'white';
            layoutTabExplanation.style.color = 'var(--accent-primary)';
            layoutTabExplanation.style.boxShadow = '0 1px 3px rgba(0,0,0,0.05)';

            layoutTabProblem.classList.remove('active');
            layoutTabProblem.style.background = 'transparent';
            layoutTabProblem.style.color = '#64748b';
            layoutTabProblem.style.boxShadow = 'none';
        }
        renderModalReorderList();
        updateModalPreview();
    }

    if (layoutTabProblem) {
        layoutTabProblem.addEventListener('click', () => switchLayoutTab('problem'));
    }
    if (layoutTabExplanation) {
        layoutTabExplanation.addEventListener('click', () => switchLayoutTab('explanation'));
    }

    // Tab controls
    tabProblemBtn.addEventListener('click', () => {
        state.current_tab = 'problem';
        tabProblemBtn.classList.add('active');
        tabExplanationBtn.classList.remove('active');
        panelProblem.classList.add('active');
        panelExplanation.classList.remove('active');
        renderWorkspace();
        const first = getFirstBlockOfCurrentTab();
        if (first) setActiveBlock(first.id);
        else {
            state.active_block_id = null;
            renderActivePreview();
        }
    });

    tabExplanationBtn.addEventListener('click', () => {
        state.current_tab = 'explanation';
        tabExplanationBtn.classList.add('active');
        tabProblemBtn.classList.remove('active');
        panelExplanation.classList.add('active');
        panelProblem.classList.remove('active');
        renderWorkspace();
        const first = getFirstBlockOfCurrentTab();
        if (first) setActiveBlock(first.id);
        else {
            state.active_block_id = null;
            renderActivePreview();
        }
    });

    // View Switcher Actions
    btnViewElements.addEventListener('click', () => {
        // Commit temporary layout state to main state when switching back to editor
        if (tempModalState) {
            state.source_book = tempModalState.source_book;
            state.chapter = tempModalState.chapter;
            state.unit = tempModalState.unit;
            state.strategy = tempModalState.strategy;
            state.problem_number = tempModalState.problem_number || '';
            state.problem = tempModalState.problem;
            state.explanation = tempModalState.explanation;
        }

        btnViewElements.classList.add('active');
        btnViewLayout.classList.remove('active');
        viewElements.style.display = 'grid';
        viewLayout.style.display = 'none';

        renderWorkspace();
        updatePreviewMeta();

        const first = getFirstBlockOfCurrentTab();
        if (first) {
            setActiveBlock(first.id);
        } else {
            state.active_block_id = null;
            renderActivePreview();
        }
        triggerLivePreview();
    });

    btnViewLayout.addEventListener('click', () => {
        // Switch to Layout and Reorder View
        // Deep copy the current state to the temporary copy
        tempModalState = {
            source_book: state.source_book || '',
            chapter: state.chapter || '',
            unit: state.unit || '',
            strategy: state.strategy || '',
            problem_number: state.problem_number || '',
            problem: JSON.parse(JSON.stringify(state.problem)),
            explanation: JSON.parse(JSON.stringify(state.explanation))
        };

        // Fill modal metadata form
        modalSourceBookInput.value = tempModalState.source_book;
        modalChapterInput.value = tempModalState.chapter;
        modalUnitInput.value = tempModalState.unit;
        modalStrategyInput.value = tempModalState.strategy;
        modalProblemNumberInput.value = tempModalState.problem_number;

        btnViewLayout.classList.add('active');
        btnViewElements.classList.remove('active');
        viewElements.style.display = 'none';
        viewLayout.style.display = 'grid';

        // Auto-select layout sub-tab matching current active tab on Tab 1
        switchLayoutTab(state.current_tab);
    });

    // Save to Database
    btnSaveDb.addEventListener('click', async () => {
        // Sync any temporary reorder/layout edits to the primary state
        if (tempModalState) {
            state.source_book = tempModalState.source_book;
            state.chapter = tempModalState.chapter;
            state.unit = tempModalState.unit;
            state.strategy = tempModalState.strategy;
            state.problem_number = tempModalState.problem_number || '';
            state.problem = tempModalState.problem;
            state.explanation = tempModalState.explanation;
        }

        if (!state.source_book) {
            showToast('教材名は必須です。2. メタデータ設定・並べ替え・保存 ページから入力してください。', 'error');
            btnViewLayout.click();
            setTimeout(() => {
                modalSourceBookInput.focus();
            }, 300);
            return;
        }

        const problemMarkdown = compileSectionToMarkdown(state.problem, false);
        const explanationMarkdown = compileSectionToMarkdown(state.explanation, false);

        if (!problemMarkdown.trim() && !explanationMarkdown.trim()) {
            showToast('問題文または解説文に少なくとも1つのブロックが必要です', 'error');
            return;
        }

        showLoading('データベースへ保存中...');

        const payload = {
            session_id: state.session_id,
            id: state.edit_id,
            display_id: state.display_id || 'auto',
            source_book: state.source_book,
            chapter: state.chapter,
            unit: state.unit,
            strategy_summary: state.strategy,
            problem_number: state.problem_number || '',
            problem_markdown: problemMarkdown,
            explanation_markdown: explanationMarkdown,
            grading_status: state.grading_status || {}
        };

        try {
            const res = await fetch('/api/problems/save-blocks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const result = await res.json();
            if (res.ok && result.status === 'success') {
                showToast(`問題を正常に保存しました！ ID: ${result.display_id}`);
                
                // Clear localStorage draft of this session
                const drafts = getLocalDrafts();
                const updatedDrafts = drafts.filter(d => d.session_id !== state.session_id);
                localStorage.setItem('sansu_drafts', JSON.stringify(updatedDrafts));
                
                setTimeout(() => {
                    window.location.href = '/';
                }, 2000);
            } else {
                showToast(result.error || '保存中にエラーが発生しました', 'error');
            }
        } catch (err) {
            showToast('通信エラーが発生しました', 'error');
        } finally {
            hideLoading();
        }
    });

    // ================================================================
    // Draft Operations (Local Storage)
    // ================================================================
    function getLocalDrafts() {
        const stored = localStorage.getItem('sansu_drafts');
        try {
            return stored ? JSON.parse(stored) : [];
        } catch (e) {
            return [];
        }
    }

    function saveCurrentDraft() {
        if (tempModalState) {
            state.source_book = tempModalState.source_book;
            state.chapter = tempModalState.chapter;
            state.unit = tempModalState.unit;
            state.strategy = tempModalState.strategy;
            state.problem = tempModalState.problem;
            state.explanation = tempModalState.explanation;
        }

        const defaultTitle = `${state.unit || '未設定'} ${state.source_book || '無題'}${state.chapter ? ` ${state.chapter}` : ''}`;
        const title = prompt('下書きの名称を入力してください:', defaultTitle);
        if (title === null) return;

        const drafts = getLocalDrafts();
        const existingIdx = drafts.findIndex(d => d.session_id === state.session_id);
        
        const draftObj = {
            session_id: state.session_id,
            edit_id: state.edit_id,
            title: title || defaultTitle,
            updated_at: new Date().toISOString(),
            data: {
                display_id: state.display_id,
                source_book: state.source_book,
                chapter: state.chapter,
                unit: state.unit,
                strategy: state.strategy,
                problem: state.problem,
                explanation: state.explanation
            }
        };

        if (existingIdx !== -1) {
            drafts[existingIdx] = draftObj;
        } else {
            drafts.push(draftObj);
        }

        localStorage.setItem('sansu_drafts', JSON.stringify(drafts));
        showToast('下書きをローカルに一時保存しました！');
    }

    function showDraftsModal() {
        const drafts = getLocalDrafts();
        draftListContainer.innerHTML = '';

        if (drafts.length === 0) {
            draftListContainer.innerHTML = '<div class="no-drafts-msg">一時保存された下書きはありません。</div>';
        } else {
            drafts.sort((a,b) => new Date(b.updated_at) - new Date(a.updated_at));

            drafts.forEach(draft => {
                const dateStr = new Date(draft.updated_at).toLocaleString('ja-JP');
                const item = document.createElement('div');
                item.className = 'draft-item';
                item.innerHTML = `
                    <div class="draft-item-info">
                        <span class="draft-item-title">${escapeHtml(draft.title)}</span>
                        <span class="draft-item-date"><i class="fa-regular fa-clock"></i> ${dateStr}</span>
                    </div>
                    <div class="draft-item-actions">
                        <button type="button" class="btn btn-secondary btn-sm btn-load" data-sid="${draft.session_id}"><i class="fa-solid fa-folder-open"></i> 読込</button>
                        <button type="button" class="btn btn-secondary btn-sm btn-delete-draft" style="color:#ef4444;" data-sid="${draft.session_id}"><i class="fa-solid fa-trash"></i> 削除</button>
                    </div>
                `;
                draftListContainer.appendChild(item);
            });

            draftListContainer.querySelectorAll('.btn-load').forEach(btn => {
                btn.addEventListener('click', () => {
                    loadDraftBySessionId(btn.dataset.sid);
                    draftModalOverlay.classList.remove('active');
                });
            });

            draftListContainer.querySelectorAll('.btn-delete-draft').forEach(btn => {
                btn.addEventListener('click', () => {
                    if (confirm('この下書きを削除してもよろしいですか？')) {
                        deleteDraftBySessionId(btn.dataset.sid);
                        showDraftsModal();
                    }
                });
            });
        }
        draftModalOverlay.classList.add('active');
    }

    function loadDraftBySessionId(sid) {
        const drafts = getLocalDrafts();
        const draft = drafts.find(d => d.session_id === sid);
        if (draft) {
            restoreState(draft);
            showToast('下書きを読み込みました！');
        }
    }

    function deleteDraftBySessionId(sid) {
        const drafts = getLocalDrafts();
        const filtered = drafts.filter(d => d.session_id !== sid);
        localStorage.setItem('sansu_drafts', JSON.stringify(filtered));
        showToast('下書きを削除しました');
    }

    function convertSectionToFlat(sec) {
        if (!sec) return { blocks: [] };
        
        if (Array.isArray(sec.blocks)) {
            return {
                blocks: sec.blocks.map(b => ({
                    id: b.id || 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7),
                    type: b.type || 'text',
                    content: b.content || '',
                    title: b.title || '',
                    compiled_image_url: b.compiled_image_url || '',
                    compiled_image_timestamp: b.compiled_image_timestamp || null,
                    elev: b.elev,
                    azim: b.azim,
                    is_sub_start: !!b.is_sub_start,
                    sub_number: b.sub_number || ''
                }))
            };
        }

        const flatBlocks = [];
        if (Array.isArray(sec.common_blocks)) {
            sec.common_blocks.forEach(b => {
                flatBlocks.push({
                    id: b.id || 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7),
                    type: b.type || 'text',
                    content: b.content || '',
                    title: b.title || '',
                    compiled_image_url: b.compiled_image_url || '',
                    compiled_image_timestamp: b.compiled_image_timestamp || null,
                    elev: b.elev,
                    azim: b.azim,
                    is_sub_start: false,
                    sub_number: ''
                });
            });
        }
        if (Array.isArray(sec.sub_questions)) {
            sec.sub_questions.forEach(sub => {
                const subBlocks = Array.isArray(sub.blocks) ? sub.blocks : [];
                if (subBlocks.length === 0) {
                    flatBlocks.push({
                        id: 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7),
                        type: 'text',
                        content: '',
                        is_sub_start: true,
                        sub_number: sub.number || ''
                    });
                } else {
                    subBlocks.forEach((b, idx) => {
                        flatBlocks.push({
                            id: b.id || 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7),
                            type: b.type || 'text',
                            content: b.content || '',
                            title: b.title || '',
                            compiled_image_url: b.compiled_image_url || '',
                            compiled_image_timestamp: b.compiled_image_timestamp || null,
                            elev: b.elev,
                            azim: b.azim,
                            is_sub_start: idx === 0,
                            sub_number: idx === 0 ? (sub.number || '') : ''
                        });
                    });
                }
            });
        }
        return { blocks: flatBlocks };
    }

    function restoreState(draft) {
        state.session_id = draft.session_id || generateUUID();
        state.edit_id = draft.edit_id || null;
        state.display_id = draft.data.display_id || '';
        state.source_book = draft.data.source_book || '';
        state.chapter = draft.data.chapter || '';
        state.unit = draft.data.unit || '';
        state.strategy = draft.data.strategy || '';
        state.problem_number = draft.data.problem_number || '';

        // Backward compatibility for old flat drafts
        if (draft.data.problem_blocks) {
            state.problem = {
                blocks: draft.data.problem_blocks.map(b => ({
                    ...b,
                    is_sub_start: !!b.is_sub_start,
                    sub_number: b.sub_number || ''
                }))
            };
        } else {
            state.problem = convertSectionToFlat(draft.data.problem);
        }

        if (draft.data.explanation_blocks) {
            state.explanation = {
                blocks: draft.data.explanation_blocks.map(b => ({
                    ...b,
                    is_sub_start: !!b.is_sub_start,
                    sub_number: b.sub_number || ''
                }))
            };
        } else {
            state.explanation = convertSectionToFlat(draft.data.explanation);
        }
        
        modalSourceBookInput.value = state.source_book;
        modalChapterInput.value = state.chapter;
        modalUnitInput.value = state.unit;
        modalStrategyInput.value = state.strategy;
        modalProblemNumberInput.value = state.problem_number;

        renderWorkspace();
        updatePreviewMeta();
        triggerLivePreview();
    }

    function exportDraftFile() {
        if (tempModalState) {
            state.source_book = tempModalState.source_book;
            state.chapter = tempModalState.chapter;
            state.unit = tempModalState.unit;
            state.strategy = tempModalState.strategy;
            state.problem = tempModalState.problem;
            state.explanation = tempModalState.explanation;
        }

        const draftData = {
            version: 'sansu-base-draft-v1',
            session_id: state.session_id,
            edit_id: state.edit_id,
            title: `${state.unit || '未設定'} ${state.source_book || ''} draft`,
            updated_at: new Date().toISOString(),
            data: {
                display_id: state.display_id,
                source_book: state.source_book,
                chapter: state.chapter,
                unit: state.unit,
                strategy: state.strategy,
                problem_number: state.problem_number,
                problem: state.problem,
                explanation: state.explanation
            }
        };

        const blob = new Blob([JSON.stringify(draftData, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const filename = `sansu_draft_${state.unit || 'draft'}_${new Date().toISOString().slice(0,10)}.json`;
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast('JSONファイルをダウンロードしました');
    }

    btnImportDraft.addEventListener('click', () => {
        fileDraftImport.click();
    });

    fileDraftImport.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = function(evt) {
            try {
                const draft = JSON.parse(evt.target.result);
                if (draft.version === 'sansu-base-draft-v1' && draft.data) {
                    restoreState(draft);
                    showToast('JSONファイルから下書きをインポートしました！');
                } else {
                    showToast('不正な下書きフォーマットです', 'error');
                }
            } catch (err) {
                showToast('JSONの解析に失敗しました', 'error');
            }
            fileDraftImport.value = '';
        };
        reader.readAsText(file);
    });

    btnSaveDraft.addEventListener('click', saveCurrentDraft);
    btnLoadDraft.addEventListener('click', showDraftsModal);
    btnExportDraft.addEventListener('click', exportDraftFile);
    btnCloseDraftModal.addEventListener('click', () => {
        draftModalOverlay.classList.remove('active');
    });

    draftModalOverlay.addEventListener('click', (e) => {
        if (e.target === draftModalOverlay) {
            draftModalOverlay.classList.remove('active');
        }
    });

    // AI Markdown Export Modal Bindings
    function compileAiExportMarkdown() {
        // Build a structured, AI-friendly format with full block hierarchy
        const lines = [];
        lines.push('---');
        lines.push('format: sansu-base-v2');
        lines.push(`source_book: "${state.source_book || '未設定'}"`);
        lines.push(`chapter: "${state.chapter || ''}"`);
        lines.push(`unit: "${state.unit || ''}"`);
        lines.push(`page: "${state.strategy || ''}"`);  
        lines.push(`problem_number: "${state.problem_number || ''}"`);
        lines.push(`id: "${state.display_id || 'AUTO'}"`);
        lines.push('---');
        lines.push('');
        lines.push('# 問題文');
        lines.push('');
        lines.push(compileAiSection(state.problem));
        lines.push('');
        lines.push('# 解説');
        lines.push('');
        lines.push(compileAiSection(state.explanation));
        return lines.join('\n');
    }

    function compileAiSection(section) {
        const parts = [];

        if (section && section.blocks) {
            section.blocks.forEach(block => {
                if (block.is_sub_start) {
                    const isProblem = section === state.problem;
                    const prefix = isProblem ? '小問 ' : '小問解説 ';
                    parts.push(`\n### ${prefix}${block.sub_number || ''}`);
                }
                const compiled = compileAiBlock(block);
                if (compiled) {
                    parts.push(compiled);
                }
            });
        }

        return parts.filter(p => p).join('\n\n');
    }

    function compileAiBlock(block) {
        if (block.type === 'text') {
            const content = block.content ? block.content.trim() : '';
            if (!content) return '';
            return content;
        } else if (block.type === 'shape') {
            const title = block.title || '';
            let md = '';
            if (title) {
                md += `<!-- ${title}: 以下のPythonコード(matplotlib)で描画される図形 -->\n`;
                md += `**[${title}]**\n\n`;
            } else {
                md += `<!-- 以下のPythonコード(matplotlib)で描画される図形 -->\n`;
            }
            md += `\`\`\`python\n${block.content}\n\`\`\``;
            return md;
        }
        return '';
    }

    btnExportAiMd.addEventListener('click', () => {
        if (tempModalState) {
            state.source_book = tempModalState.source_book;
            state.chapter = tempModalState.chapter;
            state.unit = tempModalState.unit;
            state.strategy = tempModalState.strategy;
            state.problem_number = tempModalState.problem_number || '';
            state.problem = tempModalState.problem;
            state.explanation = tempModalState.explanation;
        }
        const unifiedMd = compileAiExportMarkdown();
        aiMdTextarea.value = unifiedMd;
        aiMdModalOverlay.classList.add('active');
    });

    btnCloseAiMdModal.addEventListener('click', () => {
        aiMdModalOverlay.classList.remove('active');
    });

    aiMdModalOverlay.addEventListener('click', (e) => {
        if (e.target === aiMdModalOverlay) {
            aiMdModalOverlay.classList.remove('active');
        }
    });

    btnCopyAiMd.addEventListener('click', () => {
        navigator.clipboard.writeText(aiMdTextarea.value).then(() => {
            showToast('AI出力用MDをクリップボードにコピーしました！');
        }).catch(err => {
            showToast('コピーに失敗しました', 'error');
        });
    });

    // DB Drawer Bindings
    btnOpenDb.addEventListener('click', openProblemsDrawer);
    btnCloseDrawer.addEventListener('click', closeProblemsDrawer);
    dbDrawerOverlay.addEventListener('click', closeProblemsDrawer);
    drawerSearchInput.addEventListener('input', renderDrawerProblems);
    drawerFilterUnit.addEventListener('change', renderDrawerProblems);

    // New and Print Bindings
    btnNewProblem.addEventListener('click', resetWorkspaceToNew);
    btnPrintPdf.addEventListener('click', printCurrentWorkspace);

    // AI Variant Modal Bindings
    btnGenVariantModal.addEventListener('click', showVariantModal);
    btnCloseVariantModal.addEventListener('click', closeVariantModal);
    btnCloseVariantModalBottom.addEventListener('click', closeVariantModal);
    variantModalOverlay.addEventListener('click', (e) => {
        if (e.target === variantModalOverlay) {
            closeVariantModal();
        }
    });
    btnRunVariantGen.addEventListener('click', runVariantGeneration);
    btnLoadVariantToEditor.addEventListener('click', loadVariantToEditor);

    function cleanCompiledText(text) {
        let trimmed = text.trim();
        if (!trimmed) return '';
        
        if (trimmed.startsWith('```json') && trimmed.endsWith('```')) {
            return trimmed.substring(7, trimmed.length - 3).trim();
        }
        if (trimmed.startsWith('```html') && trimmed.endsWith('```')) {
            return trimmed.substring(7, trimmed.length - 3).trim();
        }
        if (trimmed.startsWith('$$') && trimmed.endsWith('$$')) {
            return trimmed.substring(2, trimmed.length - 2).trim();
        }
        if (trimmed.startsWith('$') && trimmed.endsWith('$')) {
            return trimmed.substring(1, trimmed.length - 1).trim();
        }
        return trimmed.replace(/  \n/g, '\n');
    }

    function cleanOcrText(text) {
        if (!text) return "";
        // Remove "document analysis result(s)" (case-insensitive, optionally followed by spaces, colons, hyphens, asterisks)
        text = text.replace(/document\s+analysis\s+results?[:\s\-\*]*/gi, '');
        // Remove "JSONをコピー" (optionally wrapped in brackets/buttons)
        text = text.replace(/\[?JSONをコピー(?:する)?\]?/g, '');
        // Remove "Copy JSON" (case-insensitive, optionally wrapped in brackets/buttons)
        text = text.replace(/\[?copy\s+json\]?/gi, '');
        return text.trim();
    }

    function sanitizeSection(section) {
        if (!section) return { blocks: [] };
        
        const cleanSection = {
            blocks: Array.isArray(section.blocks) ? [...section.blocks] : []
        };

        cleanSection.blocks.forEach(b => {
            if (b.is_sub_start === undefined) b.is_sub_start = false;
            if (b.sub_number === undefined) b.sub_number = '';
        });
        
        return cleanSection;
    }

    function convertFlatBlocksToNested(parsedBlocks) {
        const blocks = [];
        let pendingSubNumber = '';
        let isPendingSub = false;

        parsedBlocks.forEach(b => {
            if (b.type === 'section') {
                pendingSubNumber = b.content || '';
                isPendingSub = true;
            } else {
                blocks.push({
                    id: b.id || 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7),
                    type: b.type || 'text',
                    content: b.content || '',
                    title: b.title || '',
                    compiled_image_url: b.compiled_image_url || '',
                    compiled_image_timestamp: b.compiled_image_timestamp || null,
                    elev: b.elev,
                    azim: b.azim,
                    is_sub_start: isPendingSub,
                    sub_number: isPendingSub ? pendingSubNumber : ''
                });
                isPendingSub = false;
                pendingSubNumber = '';
            }
        });

        if (isPendingSub) {
            blocks.push({
                id: 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7),
                type: 'text',
                content: '',
                is_sub_start: true,
                sub_number: pendingSubNumber
            });
        }

        return { blocks };
    }

    function parseMarkdownToNested(markdown) {
        const section = {
            blocks: []
        };
        
        markdown = cleanOcrText(markdown);
        if (!markdown || !markdown.trim()) return section;

        // Try parsing from SANSUB_BLOCKS JSON comment first
        const sansubMatch = markdown.match(/<!--\s*SANSUB_BLOCKS:\s*([\s\S]*?)\s*-->/);
        if (sansubMatch) {
            try {
                const parsed = JSON.parse(sansubMatch[1]);
                if (parsed && parsed.version === 'sansu-base-section-v3') {
                    if (Array.isArray(parsed.blocks)) {
                        section.blocks = parsed.blocks.map(b => ({
                            id: b.id || 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7),
                            type: b.type || 'text',
                            content: b.content || '',
                            title: b.title || '',
                            compiled_image_url: b.compiled_image_url || '',
                            compiled_image_timestamp: b.compiled_image_timestamp || null,
                            elev: b.elev,
                            azim: b.azim,
                            is_sub_start: !!b.is_sub_start,
                            sub_number: b.sub_number || ''
                        }));
                    }
                    return sanitizeSection(section);
                }
                
                // Old version v2 (nested blocks) compatibility
                if (parsed && parsed.version === 'sansu-base-section-v2') {
                    const flatBlocks = [];
                    if (Array.isArray(parsed.common_blocks)) {
                        parsed.common_blocks.forEach(b => {
                            flatBlocks.push({
                                id: b.id || 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7),
                                type: b.type || 'text',
                                content: b.content || '',
                                title: b.title || '',
                                compiled_image_url: b.compiled_image_url || '',
                                compiled_image_timestamp: b.compiled_image_timestamp || null,
                                elev: b.elev,
                                azim: b.azim,
                                is_sub_start: false,
                                sub_number: ''
                            });
                        });
                    }
                    if (Array.isArray(parsed.sub_questions)) {
                        parsed.sub_questions.forEach(sub => {
                            const subBlocks = Array.isArray(sub.blocks) ? sub.blocks : [];
                            if (subBlocks.length === 0) {
                                flatBlocks.push({
                                    id: 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7),
                                    type: 'text',
                                    content: '',
                                    is_sub_start: true,
                                    sub_number: sub.number || ''
                                });
                            } else {
                                subBlocks.forEach((b, idx) => {
                                    flatBlocks.push({
                                        id: b.id || 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7),
                                        type: b.type || 'text',
                                        content: b.content || '',
                                        title: b.title || '',
                                        compiled_image_url: b.compiled_image_url || '',
                                        compiled_image_timestamp: b.compiled_image_timestamp || null,
                                        elev: b.elev,
                                        azim: b.azim,
                                        is_sub_start: idx === 0,
                                        sub_number: idx === 0 ? (sub.number || '') : ''
                                    });
                                });
                            }
                        });
                    }
                    section.blocks = flatBlocks;
                    return sanitizeSection(section);
                }
            } catch (e) {
                console.error("Failed to parse SANSUB_BLOCKS:", e);
            }
        }

        const trimmed = markdown.trim();
        if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
            try {
                const parsed = JSON.parse(trimmed);
                if (Array.isArray(parsed)) {
                    const res = convertFlatBlocksToNested(parsed);
                    return sanitizeSection(res);
                }
            } catch (e) {
                // Not valid JSON array, fallback to markdown parsing
            }
        }

        // Clean out any SANSUB_BLOCKS comment from the raw markdown before parsing paragraphs
        let cleanMarkdown = markdown.replace(/<!--\s*SANSUB_BLOCKS:\s*[\s\S]*?\s*-->/g, '').trim();

        // 1. Isolate all code blocks (python, html, json) first
        const codeBlocks = [];
        const regexCode = /```(python|html|json)?\n([\s\S]*?)\n```/g;
        
        let processed = cleanMarkdown.replace(regexCode, (match, lang, content) => {
            const placeholder = `__CODE_BLOCK_PLACEHOLDER_${codeBlocks.length}__`;
            codeBlocks.push({
                raw: match,
                lang: lang || '',
                content: content
            });
            return `\n\n${placeholder}\n\n`;
        });

        // 2. Split into paragraphs for sequential processing
        const paragraphs = processed.split(/\n\n+/).map(p => p.trim()).filter(p => p);

        let pendingSubNumber = '';
        let isPendingSub = false;

        paragraphs.forEach(p => {
            // Check if it's a sub-question boundary
            const subMatch = p.match(/^###\s+(?:小問|小問解説)\s*(.*)$/);
            if (subMatch) {
                pendingSubNumber = subMatch[1].trim();
                isPendingSub = true;
                return;
            }

            // Check if paragraph is a code block placeholder
            const placeholderMatch = p.match(/^__CODE_BLOCK_PLACEHOLDER_(\d+)__$/);
            if (placeholderMatch) {
                const idx = parseInt(placeholderMatch[1]);
                const blockInfo = codeBlocks[idx];
                
                let newBlock = null;
                if (blockInfo.lang === 'python') {
                    // Extract ID and title metadata from python comments if available
                    const idMatch = blockInfo.content.match(/# BLOCK_ID: (.*?)\n/);
                    const blockId = idMatch ? idMatch[1] : 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7);
                    
                    const titleMatch = blockInfo.content.match(/# TITLE: (.*?)\n/);
                    const title = titleMatch ? titleMatch[1] : '';

                    let cleanContent = blockInfo.content
                        .replace(/# TYPE: DIAGRAM\n?/, '')
                        .replace(/# BLOCK_ID: (.*?)\n?/, '')
                        .replace(/# TITLE: (.*?)\n?/, '');

                    newBlock = {
                        id: blockId,
                        type: 'shape',
                        title: title,
                        content: cleanContent.trim(),
                        compiled_image_url: '',
                        compiled_image_timestamp: null,
                        compile_error: '',
                        is_sub_start: isPendingSub,
                        sub_number: isPendingSub ? pendingSubNumber : ''
                    };
                } else {
                    // HTML / JSON text blocks
                    newBlock = {
                        id: 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7),
                        type: 'text',
                        content: blockInfo.content.trim(),
                        is_sub_start: isPendingSub,
                        sub_number: isPendingSub ? pendingSubNumber : ''
                    };
                }

                if (newBlock) {
                    section.blocks.push(newBlock);
                    isPendingSub = false;
                    pendingSubNumber = '';
                }
                return;
            }

            // Check if paragraph is a compiled image node
            const imgMatch = p.match(/^!\[(.*?)\]\((.*?)\)$/);
            if (imgMatch) {
                const imgUrl = imgMatch[2].split('?')[0];
                const imgTitle = imgMatch[1];
                
                // Re-associate image URL with the nearest shape block created just after or before
                const targetBlocks = section.blocks;
                for (let i = targetBlocks.length - 1; i >= 0; i--) {
                    if (targetBlocks[i].type === 'shape' && !targetBlocks[i].compiled_image_url) {
                        targetBlocks[i].compiled_image_url = imgUrl;
                        if (imgTitle && imgTitle !== '図') {
                            targetBlocks[i].title = imgTitle;
                        }
                        break;
                    }
                }
                return; // Do not add image node as separate text block
            }

            // Standard text paragraph
            const cleaned = cleanCompiledText(p);
            if (cleaned) {
                const txtBlock = {
                    id: 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7),
                    type: 'text',
                    content: cleaned,
                    is_sub_start: isPendingSub,
                    sub_number: isPendingSub ? pendingSubNumber : ''
                };
                section.blocks.push(txtBlock);
                isPendingSub = false;
                pendingSubNumber = '';
            }
        });

        if (isPendingSub) {
            section.blocks.push({
                id: 'block-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7),
                type: 'text',
                content: '',
                is_sub_start: true,
                sub_number: pendingSubNumber
            });
        }

        return sanitizeSection(section);
    }

    function resetWorkspaceToNew() {
        if (state.edit_id || state.problem.blocks.length > 1 || (state.problem.blocks[0] && state.problem.blocks[0].content.trim())) {
            if (!confirm('編集中の内容を破棄して、新しく問題を作成しますか？')) {
                return;
            }
        }
        
        state.edit_id = null;
        state.display_id = '';
        state.source_book = '';
        state.chapter = '';
        state.unit = '';
        state.strategy = '';
        state.problem_number = '';
        state.grading_status = {};
        
        state.problem.blocks = [];
        state.explanation.blocks = [];
        
        // Add single empty block to start with
        const pBlock = createBlockObject('text');
        const eBlock = createBlockObject('text');
        state.problem.blocks.push(pBlock);
        state.explanation.blocks.push(eBlock);
        
        // Reset modal inputs
        modalSourceBookInput.value = '';
        modalChapterInput.value = '';
        modalUnitInput.value = '';
        modalStrategyInput.value = '';
        
        // Re-render
        renderWorkspace();
        updatePreviewMeta();
        setActiveBlock(pBlock.id);
        triggerLivePreview();
        
        // Update URL
        history.pushState(null, '', '/');
        
        showToast('新しいワークスペースを作成しました');
    }

    function printCurrentWorkspace() {
        // Compile markdown for both sections
        const problemMd = compileSectionToMarkdown(state.problem, 'problem');
        const explanationMd = compileSectionToMarkdown(state.explanation, 'explanation');
        
        // Format meta text
        const bookStr = modalSourceBookInput.value.trim() || '未設定';
        const chapStr = modalChapterInput.value.trim();
        const unitStr = modalUnitInput.value.trim() || '未設定';
        const metaText = `${bookStr}${chapStr ? ` ${chapStr}` : ''} (${unitStr})`;
        
        // Inject to print elements
        printMeta.textContent = metaText;
        printProblemBody.innerHTML = renderMarkdownToHtml(problemMd);
        printAnswerBody.innerHTML = renderMarkdownToHtml(explanationMd);
        
        // MathJax re-render for print-container
        if (window.MathJax && window.MathJax.typesetPromise) {
            window.MathJax.typesetPromise([printProblemBody, printAnswerBody]).then(() => {
                window.print();
            }).catch((err) => {
                console.error("MathJax print typesetting error:", err);
                window.print();
            });
        } else {
            window.print();
        }
    }

    let allDrawerProblems = [];

    async function openProblemsDrawer() {
        dbDrawerOverlay.classList.add('open');
        dbDrawer.classList.add('open');
        
        // Fetch list
        drawerProblemList.innerHTML = '<div style="text-align:center; padding:2rem; color:#94a3b8;"><i class="fa-solid fa-spinner fa-spin"></i> 読み込み中...</div>';
        
        try {
            const res = await fetch('/api/problems');
            if (res.ok) {
                allDrawerProblems = await res.json();
                renderDrawerProblems();
            } else {
                drawerProblemList.innerHTML = '<div style="color:#ef4444; padding:1rem; text-align:center;">読み込みに失敗しました</div>';
            }
        } catch (err) {
            drawerProblemList.innerHTML = '<div style="color:#ef4444; padding:1rem; text-align:center;">エラーが発生しました</div>';
        }
    }

    function closeProblemsDrawer() {
        dbDrawerOverlay.classList.remove('open');
        dbDrawer.classList.remove('open');
    }

    function renderDrawerProblems() {
        const query = drawerSearchInput.value.trim().toLowerCase();
        const selectedUnit = drawerFilterUnit.value;
        
        drawerProblemList.innerHTML = '';
        
        const filtered = allDrawerProblems.filter(prob => {
            const matchesUnit = !selectedUnit || prob.unit === selectedUnit;
            
            const book = (prob.source_book || '').toLowerCase();
            const chapter = (prob.chapter || '').toLowerCase();
            const unit = (prob.unit || '').toLowerCase();
            const displayId = (prob.display_id || '').toLowerCase();
            
            const matchesQuery = !query || 
                book.includes(query) || 
                chapter.includes(query) || 
                unit.includes(query) || 
                displayId.includes(query);
                
            return matchesUnit && matchesQuery;
        });
        
        if (filtered.length === 0) {
            drawerProblemList.innerHTML = '<div style="text-align:center; color:#94a3b8; padding:2rem;">該当する問題がありません</div>';
            return;
        }
        
        filtered.forEach(prob => {
            const card = document.createElement('div');
            card.className = `drawer-problem-card ${state.edit_id === prob.id ? 'active' : ''}`;
            
            const content = document.createElement('div');
            content.className = 'drawer-card-content';
            
            const title = document.createElement('div');
            title.className = 'drawer-card-title';
            const chapText = prob.chapter ? ` ${prob.chapter}` : '';
            title.textContent = `${prob.source_book}${chapText}`;
            
            const meta = document.createElement('div');
            meta.className = 'drawer-card-meta';
            
            const idBadge = document.createElement('span');
            idBadge.className = 'drawer-card-id';
            idBadge.textContent = prob.display_id || 'ID: 空';
            
            const unitBadge = document.createElement('span');
            unitBadge.className = 'drawer-card-unit';
            unitBadge.textContent = prob.unit || '単元未設定';
            
            meta.appendChild(idBadge);
            meta.appendChild(unitBadge);
            
            content.appendChild(title);
            content.appendChild(meta);
            
            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'btn-delete-problem-card';
            deleteBtn.title = '問題を削除';
            deleteBtn.innerHTML = '<i class="fa-solid fa-trash-can"></i>';
            deleteBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (confirm(`問題「${prob.source_book}${chapText} (${prob.unit})」を本当に削除しますか？\nこの操作は元に戻せません。`)) {
                    showLoading('削除中...');
                    try {
                        const delRes = await fetch(`/api/problems/${prob.id}`, { method: 'DELETE' });
                        if (delRes.ok) {
                            showToast('問題を削除しました');
                            // Remove from local list
                            allDrawerProblems = allDrawerProblems.filter(p => p.id !== prob.id);
                            renderDrawerProblems();
                            
                            // If deleted the current editing one, clear editor
                            if (state.edit_id === prob.id) {
                                state.edit_id = null;
                                resetWorkspaceToNew();
                            }
                        } else {
                            showToast('削除に失敗しました', 'error');
                        }
                    } catch (err) {
                        showToast('通信エラーが発生しました', 'error');
                    } finally {
                        hideLoading();
                    }
                }
            });
            
            card.appendChild(content);
            card.appendChild(deleteBtn);
            
            card.addEventListener('click', () => {
                closeProblemsDrawer();
                loadProblemById(prob.id);
            });
            
            drawerProblemList.appendChild(card);
        });
    }

    async function loadProblemById(id) {
        state.edit_id = id;
        showLoading('問題を読込中...');
        try {
            const res = await fetch(`/api/problems/${id}`);
            if (res.ok) {
                const prob = await res.json();
                state.display_id = prob.display_id || '';
                state.source_book = prob.source_book || '';
                state.chapter = prob.chapter || '';
                state.unit = prob.unit || '';
                state.strategy = prob.strategy_summary || '';
                state.problem_number = prob.problem_number || '';
                state.grading_status = prob.grading_status || {};
                
                state.problem = parseMarkdownToNested(prob.problem_markdown);
                state.explanation = parseMarkdownToNested(prob.explanation_markdown);

                modalSourceBookInput.value = state.source_book;
                modalChapterInput.value = state.chapter;
                modalUnitInput.value = state.unit;
                modalStrategyInput.value = state.strategy;
                modalProblemNumberInput.value = state.problem_number;

                renderWorkspace();
                updatePreviewMeta();
                const first = getFirstBlockOfCurrentTab();
                if (first) {
                    setActiveBlock(first.id);
                } else {
                    state.active_block_id = null;
                    renderActivePreview();
                }
                triggerLivePreview();
                
                // Update URL parameters
                history.pushState(null, '', `?edit_id=${id}`);
                
                showToast('編集データをロードしました');
            } else {
                showToast('指定された問題が見つかりません', 'error');
            }
        } catch (err) {
            showToast('データの取得に失敗しました', 'error');
        } finally {
            hideLoading();
        }
    }

    let lastGeneratedVariantText = '';

    function showVariantModal() {
        // Must select unit
        const currentUnit = modalUnitInput.value.trim();
        if (!currentUnit) {
            showToast('類題を生成するには、まず「2. メタデータ設定・並べ替え・保存」タブで単元を入力してください。', 'error');
            // Automatically switch to metadata tab
            btnViewLayout.click();
            return;
        }

        variantModalOverlay.style.display = 'flex';
        // Reset state
        variantTokenUsage.style.display = 'none';
        btnRunVariantGen.disabled = false;
        btnRunVariantGen.innerHTML = '<i class="fa-solid fa-bolt"></i> AIで生成を開始';
        btnLoadVariantToEditor.style.display = 'none';
        variantPreviewBox.innerHTML = `
            <div class="empty-state" id="variant-empty-state" style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; color: #94a3b8; text-align: center; gap: 0.75rem;">
                <i class="fa-solid fa-wand-magic-sparkles" style="font-size: 3rem; color: #cbd5e1;"></i>
                <p style="margin: 0; font-size: 0.9rem;">単元「<strong>${currentUnit}</strong>」に関する新しい類題を生成します。</p>
            </div>
        `;
        lastGeneratedVariantText = '';
    }

    function closeVariantModal() {
        variantModalOverlay.style.display = 'none';
    }

    async function runVariantGeneration() {
        const currentUnit = modalUnitInput.value.trim();
        const currentBook = modalSourceBookInput.value.trim();
        const currentChapter = modalChapterInput.value.trim();
        
        if (!currentUnit) {
            showToast('単元を入力してください', 'error');
            return;
        }

        const modelName = variantModelSelect.value;
        const friendlyName = modelName === 'gemini-3.1-flash-lite' ? 'Gemini 3.1 Flash-Lite' : 'Gemini 3.5 Flash';
        
        btnRunVariantGen.disabled = true;
        btnRunVariantGen.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 生成中...';
        variantPreviewBox.innerHTML = '<div style="text-align:center; padding:4rem; color:#94a3b8;"><i class="fa-solid fa-spinner fa-spin fa-2x" style="margin-bottom:1rem; display:block;"></i> AIによる問題作成＆シミュレーション検証中...<br>(数分かかる場合があります)</div>';
        variantTokenUsage.style.display = 'none';
        btnLoadVariantToEditor.style.display = 'none';

        try {
            const body = {
                model: modelName,
                source_book: currentBook,
                chapter: currentChapter,
                unit: currentUnit
            };
            if (state.edit_id) {
                body.reference_id = state.edit_id;
            }
            
            const res = await fetch('/api/problems/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const result = await res.json();
            
            if (res.ok) {
                lastGeneratedVariantText = result.variant;
                
                // Show token usage
                if (result.usage) {
                    variantTokenModel.textContent = result.usage.model_name || friendlyName;
                    variantTokenInput.textContent = result.usage.prompt_tokens || '0';
                    variantTokenOutput.textContent = result.usage.candidates_tokens || '0';
                    variantTokenUsage.style.display = 'inline-flex';
                }
                
                // Render preview
                variantPreviewBox.innerHTML = renderMarkdownToHtml(lastGeneratedVariantText);
                btnLoadVariantToEditor.style.display = 'inline-flex';
                
                // Trigger MathJax typesetting
                if (window.MathJax && window.MathJax.typesetPromise) {
                    window.MathJax.typesetPromise([variantPreviewBox]);
                }
                
                showToast('類題の生成が完了しました！');
            } else {
                variantPreviewBox.innerHTML = `<div style="color:#ef4444; padding:2rem; text-align:center;">エラー: ${result.error || '生成に失敗しました'}</div>`;
            }
        } catch (err) {
            variantPreviewBox.innerHTML = '<div style="color:#ef4444; padding:2rem; text-align:center;">通信エラーが発生しました</div>';
        } finally {
            btnRunVariantGen.disabled = false;
            btnRunVariantGen.innerHTML = '<i class="fa-solid fa-bolt"></i> AIで生成を開始';
        }
    }

    function loadVariantToEditor() {
        if (!lastGeneratedVariantText) return;
        
        if (!confirm('生成された類題を現在のワークスペースに上書き読み込みしますか？\n（現在編集中のブロックは上書き破棄されます）')) {
            return;
        }

        let problemPart = "";
        let explanationPart = "";
        
        const splitRegex = /###\s*(?:類題：解説|解説|類題の解説|類題解答・解説|類題:解説|解答・解説|解答解説)/i;
        const parts = lastGeneratedVariantText.split(splitRegex);
        
        if (parts.length >= 2) {
            problemPart = parts[0].replace(/###\s*(?:類題：問題|問題|類題の問題|類題:問題|練習問題)/i, '').trim();
            explanationPart = parts[1].trim();
        } else {
            problemPart = lastGeneratedVariantText;
            explanationPart = "（解説はありません）";
        }

        // Extract key strategy if found
        const coreMatch = lastGeneratedVariantText.match(/(?:コア戦略|解法コア|解法の核心・企み|解法の核心|核心戦略)[:：\s]*(.+)$/m);
        if (coreMatch) {
            state.strategy = coreMatch[1].trim();
            modalStrategyInput.value = state.strategy;
        }

        // Parse markdown text into nested blocks structure
        state.problem = parseMarkdownToNested(problemPart);
        state.explanation = parseMarkdownToNested(explanationPart);

        // Re-render editor workspace
        renderWorkspace();
        updatePreviewMeta();
        
        const first = getFirstBlockOfCurrentTab();
        if (first) {
            setActiveBlock(first.id);
        } else {
            state.active_block_id = null;
            renderActivePreview();
        }
        triggerLivePreview();
        
        closeVariantModal();
        showToast('類題をエディタに読み込みました。微調整して保存してください。');
    }

    async function loadProblemFromUrlQuery() {
        const urlParams = new URLSearchParams(window.location.search);
        const editId = urlParams.get('edit_id') || urlParams.get('id');
        if (editId) {
            state.edit_id = editId;
            showLoading('問題を読込中...');
            try {
                const res = await fetch(`/api/problems/${editId}`);
                if (res.ok) {
                    const prob = await res.json();
                    state.display_id = prob.display_id || '';
                    state.source_book = prob.source_book || '';
                    state.chapter = prob.chapter || '';
                    state.unit = prob.unit || '';
                    state.strategy = prob.strategy_summary || '';
                    state.problem_number = prob.problem_number || '';
                    state.grading_status = prob.grading_status || {};
                    
                    state.problem = parseMarkdownToNested(prob.problem_markdown);
                    state.explanation = parseMarkdownToNested(prob.explanation_markdown);

                    modalSourceBookInput.value = state.source_book;
                    modalChapterInput.value = state.chapter;
                    modalUnitInput.value = state.unit;
                    modalStrategyInput.value = state.strategy;
                    modalProblemNumberInput.value = state.problem_number;

                    renderWorkspace();
                    updatePreviewMeta();
                    const first = getFirstBlockOfCurrentTab();
                    if (first) {
                        setActiveBlock(first.id);
                    } else {
                        state.active_block_id = null;
                        renderActivePreview();
                    }
                    triggerLivePreview();
                    
                    showToast('編集用データをロードしました');
                } else {
                    showToast('指定された問題が見つかりません', 'error');
                }
            } catch (err) {
                showToast('データの取得に失敗しました', 'error');
            } finally {
                hideLoading();
            }
        } else {
            // Populate problem with one default block
            const pBlock = createBlockObject('text');
            const eBlock = createBlockObject('text');
            state.problem.blocks.push(pBlock);
            state.explanation.blocks.push(eBlock);
            renderWorkspace();
            updatePreviewMeta();
            setActiveBlock(pBlock.id);
            triggerLivePreview();
        }
    }

    // ================================================================
    // Execution Initialization
    // ================================================================
    loadSourceBooksHistory();
    loadProblemFromUrlQuery();
});
