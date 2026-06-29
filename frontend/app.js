const uploadZone = document.getElementById('upload-zone');
const fileInput = document.getElementById('file-input');
const uploadContent = document.querySelector('.upload-content');
const previewsContainer = document.getElementById('image-previews');
const fileCount = document.getElementById('file-count');
const generateBtn = document.getElementById('generate-btn');
const btnText = document.querySelector('.btn-text');
const spinner = document.querySelector('.spinner');
const statusMessage = document.getElementById('status-message');
const quotaUsed = document.getElementById('quota-used');
const quotaLimit = document.getElementById('quota-limit');
const quotaFill = document.getElementById('quota-fill');

let currentFiles = [];

async function fetchQuota() {
    try {
        const r = await fetch('/api/quota');
        const d = await r.json();
        quotaUsed.textContent = d.used;
        quotaLimit.textContent = 10;
        const pct = Math.min(100, (d.used / 10) * 100);
        quotaFill.style.width = pct + '%';
    } catch (_) {}
}
fetchQuota();

uploadZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadZone.classList.add('dragover');
});

uploadZone.addEventListener('dragleave', () => {
    uploadZone.classList.remove('dragover');
});

uploadZone.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadZone.classList.remove('dragover');
    if (e.dataTransfer.files.length > 0) {
        handleFiles(e.dataTransfer.files);
    }
});

uploadZone.addEventListener('click', (e) => {
    if (!e.target.closest('.preview-item') && !e.target.closest('.remove-btn')) {
        fileInput.click();
    }
});

fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleFiles(e.target.files);
    }
});

function handleFiles(files) {
    const newFiles = Array.from(files).filter(f => f.type.startsWith('image/'));
    if (newFiles.length === 0) {
        showStatus('Please upload image files.', 'error');
        return;
    }

    const total = currentFiles.length + newFiles.length;
    if (total > 10) {
        showStatus('Maximum 10 photos allowed.', 'error');
        return;
    }

    currentFiles = [...currentFiles, ...newFiles];
    renderPreviews();
    updateButtonState();
    hideStatus();
}

function renderPreviews() {
    previewsContainer.innerHTML = '';
    previewsContainer.classList.remove('hidden');
    uploadContent.classList.add('hidden');

    currentFiles.forEach((file, index) => {
        const reader = new FileReader();
        const item = document.createElement('div');
        item.className = 'preview-item';

        const img = document.createElement('img');
        img.className = 'preview-thumb';

        const removeBtn = document.createElement('button');
        removeBtn.className = 'remove-btn';
        removeBtn.textContent = '×';
        removeBtn.dataset.index = index;
        removeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            currentFiles.splice(index, 1);
            renderPreviews();
            updateButtonState();
        });

        const label = document.createElement('span');
        label.className = 'preview-label';

        item.appendChild(img);
        item.appendChild(removeBtn);
        item.appendChild(label);
        previewsContainer.appendChild(item);

        reader.onload = (e) => {
            img.src = e.target.result;
            label.textContent = `${index + 1}`;
        };
        reader.readAsDataURL(file);
    });

    fileCount.textContent = `${currentFiles.length} photo${currentFiles.length > 1 ? 's' : ''} selected`;
    fileCount.hidden = false;

    if (currentFiles.length === 0) {
        resetUpload();
    }
}

function resetUpload() {
    currentFiles = [];
    fileInput.value = '';
    previewsContainer.classList.add('hidden');
    previewsContainer.innerHTML = '';
    uploadContent.classList.remove('hidden');
    fileCount.hidden = true;
    generateBtn.disabled = true;
    hideStatus();
}

function updateButtonState() {
    generateBtn.disabled = currentFiles.length === 0;
}

function showStatus(message, type) {
    statusMessage.textContent = message;
    statusMessage.className = `status-${type}`;
    statusMessage.classList.remove('hidden');
}

function hideStatus() {
    statusMessage.classList.add('hidden');
}

generateBtn.addEventListener('click', async () => {
    if (currentFiles.length === 0) return;

    generateBtn.disabled = true;
    btnText.textContent = `Analyzing ${currentFiles.length} photo${currentFiles.length > 1 ? 's' : ''}...`;
    spinner.classList.remove('hidden');
    hideStatus();

    const formData = new FormData();
    currentFiles.forEach((file) => {
        formData.append('files', file);
    });

    try {
        const response = await fetch('/api/process-inspection', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            let msg = 'Failed to process inspection';
            try {
                const err = await response.json();
                if (Array.isArray(err.detail)) {
                    msg = err.detail.map(e => e.msg).join('; ');
                } else if (typeof err.detail === 'string') {
                    msg = err.detail;
                }
            } catch (_) {}
            throw new Error(msg);
        }

        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = 'Texas_1st_Auto_Inspection_Report.pdf';
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);

        const warning = response.headers.get('X-Warning');
        if (warning) {
            showStatus(warning, 'warning');
        } else {
            showStatus('Success! Your PDF has been generated.', 'success');
        }
    } catch (error) {
        showStatus(error.message, 'error');
    } finally {
        generateBtn.disabled = false;
        btnText.textContent = 'Generate Official PDF';
        spinner.classList.add('hidden');
        fetchQuota();
    }
});
