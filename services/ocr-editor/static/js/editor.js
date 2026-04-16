let currentPdfId = null;
let currentPreviews = [];
let currentPageNum = 0;
let ocrData = []; 

const elements = {
    pdfUpload: document.getElementById('pdfUpload'),
    uploadBtn: document.getElementById('uploadBtn'),
    savePdfBtn: document.getElementById('savePdfBtn'),
    templateSelector: document.getElementById('templateSelector'),
    editTemplateBtn: document.getElementById('editTemplateBtn'),
    pageImage: document.getElementById('pageImage'),
    svgOverlay: document.getElementById('svgOverlay'),
    textEditor: document.getElementById('textEditor'),
    loading: document.getElementById('loading'),
    notification: document.getElementById('notification'),
    pageList: document.getElementById('pageList'),
    sidebar: document.getElementById('sidebar'),
    modal: document.getElementById('templateModal'),
    tplName: document.getElementById('tplName'),
    tplPrompt: document.getElementById('tplPrompt'),
    saveTemplateBtn: document.getElementById('saveTemplateBtn'),
    closeModal: document.getElementById('closeModal')
};

// Event Listeners
elements.uploadBtn.addEventListener('click', () => elements.pdfUpload.click());
elements.pdfUpload.addEventListener('change', uploadPdf);
elements.savePdfBtn.addEventListener('click', saveSearchablePdf);
elements.editTemplateBtn.addEventListener('click', () => {
    elements.modal.classList.remove('hidden');
    elements.tplName.value = elements.templateSelector.options[elements.templateSelector.selectedIndex].text;
    // We need some initial data here, or fetch from backend
});
elements.closeModal.addEventListener('click', () => elements.modal.classList.add('hidden'));

async function uploadPdf(e) {
    const file = e.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    showLoading(true, "PDFを処理中...");
    try {
        const response = await fetch('/upload', { method: 'POST', body: formData });
        const data = await response.json();
        
        if (data.error) throw new Error(data.error);

        currentPdfId = data.pdf_id;
        currentPreviews = data.previews;
        renderPageList();
        loadPage(0);
        elements.sidebar.classList.remove('hidden');
    } catch (err) {
        showNotification("エラー: " + err.message);
    } finally {
        showLoading(false);
    }
}

function renderPageList() {
    elements.pageList.innerHTML = '';
    currentPreviews.forEach((prev, idx) => {
        const div = document.createElement('div');
        div.className = `page-item ${idx === currentPageNum ? 'active' : ''}`;
        div.style.padding = '0.75rem';
        div.style.cursor = 'pointer';
        div.style.borderBottom = '1px solid #eee';
        div.style.background = idx === currentPageNum ? '#f1f5f9' : 'transparent';
        div.innerHTML = `<span style="font-size:0.9rem">Page ${idx + 1}</span>`;
        div.onclick = () => loadPage(idx);
        elements.pageList.appendChild(div);
    });
}

async function loadPage(idx) {
    currentPageNum = idx;
    const page = currentPreviews[idx];
    elements.pageImage.src = page.url;
    
    // Reset editor
    elements.textEditor.innerHTML = '<div class="placeholder"><div style="text-align:center"><p>OCRを開始するにはボタンを押してください</p><br><button class="btn primary" id="runOcrBtn">分析実行</button></div></div>';
    document.getElementById('runOcrBtn').onclick = () => runOcr(idx);

    elements.pageImage.onload = () => {
        updateSvgSize();
    };

    renderPageList();
}

function updateSvgSize() {
    const img = elements.pageImage;
    elements.svgOverlay.style.width = img.clientWidth + 'px';
    elements.svgOverlay.style.height = img.clientHeight + 'px';
    elements.svgOverlay.setAttribute('viewBox', `0 0 1000 1000`);
    elements.svgOverlay.setAttribute('preserveAspectRatio', 'none');
}

async function runOcr(pageNum) {
    showLoading(true, "AIが文字と位置を抽出しています...");
    try {
        const response = await fetch('/ocr', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                pdf_id: currentPdfId,
                page_num: pageNum,
                template_id: elements.templateSelector.value
            })
        });
        const data = await response.json();
        
        if (data.error) throw new Error(data.error);

        ocrData = data;
        renderEditor();
        renderOverlay();
        elements.savePdfBtn.disabled = false;
    } catch (err) {
        showNotification("OCRエラー: " + err.message);
    } finally {
        showLoading(false);
    }
}

function renderEditor() {
    elements.textEditor.innerHTML = '';
    ocrData.forEach((item, idx) => {
        const div = document.createElement('div');
        div.className = 'text-item';
        div.id = `text-item-${idx}`;
        div.innerHTML = `
            <textarea rows="2">${item.text}</textarea>
            <div class="meta">座標: [${item.box_2d.map(Math.round).join(', ')}]</div>
        `;
        
        const textarea = div.querySelector('textarea');
        textarea.oninput = (e) => {
            ocrData[idx].text = e.target.value;
        };
        
        div.onmouseenter = () => highlightBound(idx, true);
        div.onmouseleave = () => highlightBound(idx, false);
        
        elements.textEditor.appendChild(div);
    });
}

function renderOverlay() {
    elements.svgOverlay.innerHTML = '';
    ocrData.forEach((item, idx) => {
        const [ymin, xmin, ymax, xmax] = item.box_2d;
        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute('x', xmin);
        rect.setAttribute('y', ymin);
        rect.setAttribute('width', xmax - xmin);
        rect.setAttribute('height', ymax - ymin);
        rect.setAttribute('class', 'rect-bound');
        rect.id = `bound-${idx}`;
        rect.style.pointerEvents = 'all';
        
        rect.onmouseenter = () => {
            highlightBound(idx, true);
            const editorItem = document.getElementById(`text-item-${idx}`);
            editorItem.scrollIntoView({ behavior: 'smooth', block: 'center' });
        };
        rect.onmouseleave = () => highlightBound(idx, false);
        
        elements.svgOverlay.appendChild(rect);
    });
}

function highlightBound(idx, active) {
    const bound = document.getElementById(`bound-${idx}`);
    const editorItem = document.getElementById(`text-item-${idx}`);
    if (bound) bound.classList.toggle('active', active);
    if (editorItem) editorItem.style.background = active ? '#eff6ff' : '#fafafa';
}

async function saveSearchablePdf() {
    showLoading(true, "検索可能PDFを出力中...");
    const corrections = ocrData.map(item => ({
        page_num: currentPageNum,
        text: item.text,
        box_2d: item.box_2d
    }));

    try {
        const response = await fetch('/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                pdf_id: currentPdfId,
                corrections: corrections
            })
        });
        const data = await response.json();
        
        if (data.download_url) {
            const link = document.createElement('a');
            link.href = data.download_url;
            link.download = 'edited_searchable.pdf';
            link.click();
            showNotification("保存しました！");
        }
    } catch (err) {
        showNotification("保存エラー: " + err.message);
    } finally {
        showLoading(false);
    }
}

elements.saveTemplateBtn.onclick = async () => {
    const id = elements.templateSelector.value;
    const name = elements.tplName.value;
    const prompt = elements.tplPrompt.value;

    const response = await fetch('/save_template', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, content: { name, prompt } })
    });
    
    if (response.ok) {
        showNotification("テンプレートを更新しました。");
        elements.modal.classList.add('hidden');
        location.reload();
    }
};

function showLoading(show, message = "") {
    elements.loading.classList.toggle('hidden', !show);
    if (message) elements.loading.querySelector('p').innerText = message;
}

function showNotification(msg) {
    elements.notification.innerText = msg;
    elements.notification.classList.remove('hidden');
    setTimeout(() => {
        elements.notification.classList.add('hidden');
    }, 3000);
}

window.onresize = updateSvgSize;
