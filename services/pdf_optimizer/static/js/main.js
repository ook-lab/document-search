let pdfDoc = null;
let currentFileId = null;
let selectedPages = new Set();
let lastSelectedIndex = -1;
let pageLabels = {}; // Maps pageIndex (0-based) to label text
let previewRenderTask = null;
let currentZoom = 1.0;

document.addEventListener('DOMContentLoaded', () => {
    // Inject loading overlay
    const loadingOverlay = document.createElement('div');
    loadingOverlay.className = 'loading-overlay';
    loadingOverlay.id = 'loading-overlay';
    loadingOverlay.innerHTML = '<div class="spinner"></div><div id="loading-text">処理中...</div>';
    document.body.appendChild(loadingOverlay);

    // Event Listeners
    document.getElementById('upload-btn').addEventListener('click', () => {
        document.getElementById('file-input').click();
    });

    document.getElementById('file-input').addEventListener('change', handleFileUpload);
    document.getElementById('apply-tags-btn').addEventListener('click', applyTags);
    document.getElementById('process-download-btn').addEventListener('click', processAndDownload);
    
    // Zoom Controls
    document.getElementById('zoom-in-btn').addEventListener('click', () => {
        currentZoom += 0.2;
        applyZoom();
    });
    
    document.getElementById('zoom-out-btn').addEventListener('click', () => {
        currentZoom = Math.max(0.2, currentZoom - 0.2);
        applyZoom();
    });
    
    document.getElementById('zoom-reset-btn').addEventListener('click', () => {
        currentZoom = 1.0;
        applyZoom();
    });
});

function applyZoom() {
    const canvas = document.getElementById('preview-canvas');
    if (canvas) {
        canvas.style.transform = `scale(${currentZoom})`;
    }
}

function showLoading(text) {
    document.getElementById('loading-text').textContent = text;
    document.getElementById('loading-overlay').classList.add('active');
}

function hideLoading() {
    document.getElementById('loading-overlay').classList.remove('active');
}

async function handleFileUpload(e) {
    const file = e.target.files[0];
    if (!file) return;

    showLoading('アップロード中...');
    
    // Reset state
    pageLabels = {};
    selectedPages.clear();
    lastSelectedIndex = -1;

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await fetch('/upload', { method: 'POST', body: formData });
        const data = await res.json();
        
        if (res.ok) {
            currentFileId = data.file_id;
            document.getElementById('meta-title').value = file.name.replace('.pdf', '');
            await loadPDF(`/files/${currentFileId}`);
            
            // Analyze existing PDF labels automatically
            try {
                showLoading('既存ラベルの解析中...');
                const analyzeRes = await fetch(`/analyze/${currentFileId}`);
                if (analyzeRes.ok) {
                    const analyzeData = await analyzeRes.json();
                    
                    // Populate metadata if available
                    if (analyzeData.metadata) {
                        if (analyzeData.metadata.title) document.getElementById('meta-title').value = analyzeData.metadata.title;
                        if (analyzeData.metadata.author) document.getElementById('meta-author').value = analyzeData.metadata.author;
                        if (analyzeData.metadata.subject) document.getElementById('meta-subject').value = analyzeData.metadata.subject;
                    }
                    
                    // Populate page labels
                    if (analyzeData.pages) {
                        for (const [pageIdxStr, text] of Object.entries(analyzeData.pages)) {
                            const pageIdx = parseInt(pageIdxStr, 10);
                            pageLabels[pageIdx] = text;
                            const thumb = document.getElementById(`thumb-${pageIdx}`);
                            if (thumb) {
                                const badge = thumb.querySelector('.label-badge');
                                badge.textContent = text;
                                badge.style.display = 'block';
                            }
                        }
                    }
                }
            } catch (err) {
                console.error("Analysis failed", err);
            }
        } else {
            alert("Error: " + data.error);
        }
    } catch (err) {
        alert("Upload failed.");
        console.error(err);
    } finally {
        hideLoading();
    }
}

async function loadPDF(url) {
    showLoading('PDFを読み込み中...');
    try {
        pdfDoc = await pdfjsLib.getDocument(url).promise;
        const listContainer = document.getElementById('page-list');
        listContainer.innerHTML = '';
        
        // Lazy loaded thumbnail observer
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const index = parseInt(entry.target.dataset.index, 10);
                    renderThumbnail(index, entry.target);
                    observer.unobserve(entry.target);
                }
            });
        }, { root: listContainer, rootMargin: '100px' });
        
        for (let i = 0; i < pdfDoc.numPages; i++) {
            const item = document.createElement('div');
            item.className = 'thumb-item';
            item.id = `thumb-${i}`;
            item.dataset.index = i;
            
            item.innerHTML = `
                <div class="page-num">${i + 1}</div>
                <div class="canvas-container">
                    <canvas></canvas>
                </div>
                <div class="label-badge" style="display:none;"></div>
            `;
            
            // Selection logic
            item.addEventListener('click', (e) => handleThumbClick(i, e));
            
            listContainer.appendChild(item);
            observer.observe(item);
        }
        
        // Select first page by default
        if (pdfDoc.numPages > 0) {
            handleThumbClick(0, { shiftKey: false, ctrlKey: false, metaKey: false });
        }
    } catch (err) {
        console.error(err);
        alert("Failed to render PDF");
    } finally {
        hideLoading();
    }
}

async function renderThumbnail(index, element) {
    try {
        const page = await pdfDoc.getPage(index + 1);
        const viewport = page.getViewport({ scale: 0.3 }); // adjust scale as needed
        const canvas = element.querySelector('canvas');
        const ctx = canvas.getContext('2d');
        
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        
        await page.render({ canvasContext: ctx, viewport: viewport }).promise;
    } catch (err) {
        console.error("Error rendering thumbnail", index, err);
    }
}

async function renderPreview(index) {
    try {
        const page = await pdfDoc.getPage(index + 1);
        const container = document.getElementById('preview-container');
        container.innerHTML = ''; // Clear previous
        
        const canvas = document.createElement('canvas');
        canvas.id = 'preview-canvas';
        container.appendChild(canvas);
        
        const ctx = canvas.getContext('2d');
        
        const padding = 40;
        const containerWidth = container.clientWidth - padding;
        const containerHeight = container.clientHeight - padding;
        
        const unscaledViewport = page.getViewport({ scale: 1.0 });
        const scale = Math.min(containerWidth / unscaledViewport.width, containerHeight / unscaledViewport.height);
        
        const viewport = page.getViewport({ scale: scale > 0 ? scale : 1.0 });
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        
        if (previewRenderTask) {
            previewRenderTask.cancel();
        }
        
        previewRenderTask = page.render({ canvasContext: ctx, viewport: viewport });
        await previewRenderTask.promise;
        
        // Reset zoom visual
        currentZoom = 1.0;
        applyZoom();
    } catch (err) {
        if (err.name !== 'RenderingCancelledException') {
            console.error("Error rendering preview", err);
        }
    }
}

function handleThumbClick(index, event) {
    if (event.shiftKey && lastSelectedIndex !== -1) {
        // Range selection
        let start = Math.min(index, lastSelectedIndex);
        let end = Math.max(index, lastSelectedIndex);
        
        if (!event.ctrlKey && !event.metaKey) {
            selectedPages.clear();
        }
        
        for (let i = start; i <= end; i++) {
            selectedPages.add(i);
        }
    } else if (event.ctrlKey || event.metaKey) {
        // Toggle selection
        if (selectedPages.has(index)) {
            selectedPages.delete(index);
        } else {
            selectedPages.add(index);
        }
    } else {
        // Single selection
        selectedPages.clear();
        selectedPages.add(index);
    }
    
    lastSelectedIndex = index;
    updateSelectionUI();
    if (selectedPages.size > 0 && selectedPages.has(index)) {
        renderPreview(index);
    } else if (selectedPages.size > 0) {
        // Render the last selected if current was deselected
        const arr = Array.from(selectedPages);
        renderPreview(arr[arr.length - 1]);
    } else {
         document.getElementById('preview-container').innerHTML = '<div class="empty-state">サムネイルを選択してプレビュー</div>';
    }
}

function updateSelectionUI() {
    document.querySelectorAll('.thumb-item').forEach((el, index) => {
        if (selectedPages.has(index)) {
            el.classList.add('selected');
        } else {
            el.classList.remove('selected');
        }
    });
}

function applyTags() {
    const baseTitle = document.getElementById('base-title').value.trim();
    let startNumStr = document.getElementById('start-num').value;
    let startNum = startNumStr !== '' ? parseInt(startNumStr, 10) : 1;
    
    if (!baseTitle) {
        alert("タイトル名を入力してください。");
        return;
    }
    
    if (selectedPages.size === 0) {
        alert("適用するページを左のリストから選択してください。");
        return;
    }
    
    // Sort so pages are tagged sequentially
    const sortedSelected = Array.from(selectedPages).sort((a, b) => a - b);
    
    sortedSelected.forEach((pageIndex, i) => {
        const num = startNum + i;
        const labelText = `${baseTitle}_${num}`;
        pageLabels[pageIndex] = labelText;
        
        // Update UI
        const thumb = document.getElementById(`thumb-${pageIndex}`);
        if (thumb) {
            const badge = thumb.querySelector('.label-badge');
            badge.textContent = labelText;
            badge.style.display = 'block';
        }
    });
}

async function processAndDownload() {
    if (!currentFileId) {
        alert("PDFをアップロードしてください。");
        return;
    }
    
    showLoading('PDFを保存・出力しています...');
    
    const payload = {
        metadata: {
            title: document.getElementById('meta-title').value,
            author: document.getElementById('meta-author').value,
            subject: document.getElementById('meta-subject').value
        },
        pages: pageLabels // Format: { "0": "Label_1", "3": "Label_4" }
    };
    
    try {
        const res = await fetch(`/process/${currentFileId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });
        
        const data = await res.json();
        
        if (res.ok && data.download_url) {
            // Navigate to download URL to trigger browser download
            window.location.href = data.download_url;
        } else {
            alert("エラー: " + data.error);
        }
    } catch (err) {
        console.error(err);
        alert("通信エラーが発生しました。");
    } finally {
        hideLoading();
    }
}
