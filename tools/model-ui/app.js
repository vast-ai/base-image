/* ------------------------------------------------------------------ */
/* Config                                                              */
/* ------------------------------------------------------------------ */
const CONFIG = JSON.parse(document.getElementById('config').textContent);

document.getElementById('model-name').textContent = CONFIG.modelShort;
document.getElementById('model-path').textContent = CONFIG.modelId;
document.title = CONFIG.modelShort + ' \u2014 Model UI';

const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

/* ------------------------------------------------------------------ */
/* Utilities                                                           */
/* ------------------------------------------------------------------ */
function escapeHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function renderMarkdown(text) {
    if (!text) return '';
    const blocks = [];
    text = text.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
        blocks.push('<pre><code>' + escapeHtml(code.trimEnd()) + '</code></pre>');
        return '\x00B' + (blocks.length - 1) + '\x00';
    });
    text = escapeHtml(text);
    text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
    text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/\n/g, '<br>');
    text = text.replace(/\x00B(\d+)\x00/g, (_, i) => blocks[parseInt(i)]);
    return text;
}

function timeNow() {
    return new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

/* Thinking/reasoning model support
   Handles: <think>, <thinking>, <|think|> (case-insensitive)
   Also handles missing opening tag (some models only emit </think>) */
const _thinkOpenRe  = /<think(?:ing)?>|<\|think\|>/i;
const _thinkCloseRe = /<\/think(?:ing)?>|<\|\/think\|>/i;

function renderWithThinking(text) {
    /* 1. Opening tag present */
    const openMatch = text.match(_thinkOpenRe);
    if (openMatch) {
        const before = text.substring(0, openMatch.index);
        const after  = text.substring(openMatch.index + openMatch[0].length);
        const closeMatch = after.match(_thinkCloseRe);
        let think, response, done;
        if (closeMatch) {
            think    = after.substring(0, closeMatch.index);
            response = after.substring(closeMatch.index + closeMatch[0].length).trim();
            done = true;
        } else {
            think    = after;
            response = '';
            done = false;
        }
        let html = '';
        if (before.trim()) html += renderMarkdown(before);
        html += '<details class="think-block"' + (done ? '' : ' open') + '>'
            + '<summary>' + (done ? 'Thought process' : 'Thinking\u2026') + '</summary>'
            + '<div class="think-content">' + renderMarkdown(think) + '</div></details>';
        if (response) html += renderMarkdown(response);
        return html;
    }
    /* 2. No opening tag — model omitted it; closing tag marks end of thinking */
    const closeOnly = text.match(_thinkCloseRe);
    if (closeOnly) {
        const think    = text.substring(0, closeOnly.index);
        const response = text.substring(closeOnly.index + closeOnly[0].length).trim();
        let html = '<details class="think-block">'
            + '<summary>Thought process</summary>'
            + '<div class="think-content">' + renderMarkdown(think) + '</div></details>';
        if (response) html += renderMarkdown(response);
        return html;
    }
    return renderMarkdown(text);
}

function stripThinking(text) {
    /* Remove blocks with opening+closing tags */
    text = text.replace(/<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>\s*/gi, '');
    text = text.replace(/<\|think\|>[\s\S]*?<\|\/think\|>\s*/gi, '');
    /* Handle missing opener: everything before first closing tag is thinking */
    text = text.replace(/^[\s\S]*?<\/think(?:ing)?>\s*/i, '');
    text = text.replace(/^[\s\S]*?<\|\/think\|>\s*/i, '');
    return text.trim();
}

/* Range inputs — sync display values */
$$('input[type="range"]').forEach(input => {
    const display = input.parentElement.querySelector('.range-value');
    if (!display) return;
    const decimals = input.step.includes('.') ? input.step.split('.')[1].length : 0;
    const update = () => { display.textContent = parseFloat(input.value).toFixed(decimals); };
    input.addEventListener('input', update);
    update();
});

/* Auto-growing textareas */
function autoGrow(el) {
    if (!el) return;
    el.addEventListener('input', () => {
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 200) + 'px';
    });
}
autoGrow($('#chat-input'));

/* ------------------------------------------------------------------ */
/* Tabs                                                                */
/* ------------------------------------------------------------------ */
let ttsVoicesLoaded = false;

$$('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        $$('.tab-btn').forEach(b => b.classList.remove('active'));
        $$('.tab-panel').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        $('#tab-' + btn.dataset.tab).classList.add('active');
        if (btn.dataset.tab === 'tts' && !ttsVoicesLoaded) loadVoices();
    });
});

/* Tab filtering via allowedTabs config */
if (CONFIG.allowedTabs) {
    const allowed = CONFIG.allowedTabs;
    let defaultVisible = false;
    $$('.tab-btn').forEach(btn => {
        if (!allowed.includes(btn.dataset.tab)) {
            btn.style.display = 'none';
            const panel = $('#tab-' + btn.dataset.tab);
            if (panel) panel.style.display = 'none';
        } else if (btn.dataset.tab === CONFIG.defaultTab) {
            defaultVisible = true;
        }
    });
    if (!defaultVisible && allowed.length) {
        const first = $(`.tab-btn[data-tab="${allowed[0]}"]`);
        if (first) first.click();
    }
}

if (CONFIG.defaultTab !== 'chat') {
    const btn = $(`.tab-btn[data-tab="${CONFIG.defaultTab}"]`);
    if (btn) btn.click();
}

/* Per-tab capabilities from config */
const CAPS = CONFIG.caps || {};

/* Compute image/video mode once: 'edit', 'generate', or 'both' */
function capsMode(tabCaps) {
    if (!tabCaps) return 'both';
    const has = v => tabCaps.includes(v);
    if (has('all')) return 'both';
    if (has('edit') && has('generate')) return 'both';
    if (has('edit')) return 'edit';
    if (has('generate')) return 'generate';
    return 'both';
}
const imgMode = capsMode(CAPS.image);
const vidMode = capsMode(CAPS.video);

/* Chat caps — hide unavailable modality checkboxes ('all' = no restriction) */
if (CAPS.chat && !CAPS.chat.includes('all')) {
    const boxes = [...$$('#chat-modalities input[type="checkbox"]')];
    let anyChecked = false;
    boxes.forEach(cb => {
        if (!CAPS.chat.includes(cb.value)) {
            cb.checked = false;
            cb.closest('label').style.display = 'none';
        } else if (cb.checked) {
            anyChecked = true;
        }
    });
    /* Ensure at least one visible checkbox is checked */
    if (!anyChecked) {
        const first = boxes.find(cb => CAPS.chat.includes(cb.value));
        if (first) first.checked = true;
    }
}

/* Image caps — adjust attachment field and button */
if (imgMode === 'edit') {
    const label = document.querySelector('#img-uploads')?.closest('.field')?.querySelector('label');
    if (label) label.innerHTML = 'Input image <span style="font-weight:400;color:var(--text-tertiary)">(required)</span>';
    $('#img-generate').disabled = true;
    $('#img-generate').title = 'Attach an image first';
} else if (imgMode === 'generate') {
    const field = document.querySelector('#img-uploads')?.closest('.field');
    if (field) field.style.display = 'none';
}

/* Video caps — adjust attachment field and button */
if (vidMode === 'edit') {
    const label = document.querySelector('#vid-uploads')?.closest('.field')?.querySelector('label');
    if (label) label.innerHTML = 'Reference image/video <span style="font-weight:400;color:var(--text-tertiary)">(required)</span>';
    $('#vid-generate').disabled = true;
    $('#vid-generate').title = 'Attach a reference first';
} else if (vidMode === 'generate') {
    const field = document.querySelector('#vid-uploads')?.closest('.field');
    if (field) field.style.display = 'none';
}

/* ------------------------------------------------------------------ */
/* Lightbox                                                            */
/* ------------------------------------------------------------------ */
let lightboxImages = [];
let lightboxIndex = 0;

function showLightboxImage() {
    if (!lightboxImages.length) return;
    $('#lightbox-img').src = lightboxImages[lightboxIndex];
    $('#lb-prev').disabled = lightboxIndex <= 0;
    $('#lb-next').disabled = lightboxIndex >= lightboxImages.length - 1;
}

function openLightbox(src) {
    const activePanel = $('.tab-panel.active');
    const imgs = activePanel ? activePanel.querySelectorAll('.gen-results img, .gen-history img, .message-content img') : [];
    lightboxImages = Array.from(imgs).map(img => img.src);
    lightboxIndex = lightboxImages.indexOf(src);
    if (lightboxIndex < 0) lightboxIndex = 0;
    showLightboxImage();
    $('#lightbox').classList.add('active');
}

$('#lb-prev').addEventListener('click', e => {
    e.stopPropagation();
    if (lightboxIndex > 0) { lightboxIndex--; showLightboxImage(); }
});
$('#lb-next').addEventListener('click', e => {
    e.stopPropagation();
    if (lightboxIndex < lightboxImages.length - 1) { lightboxIndex++; showLightboxImage(); }
});
$('#lightbox-img').addEventListener('click', e => e.stopPropagation());
$('#lightbox').addEventListener('click', e => {
    if (e.target.id === 'lightbox' ) $('#lightbox').classList.remove('active');
});
document.addEventListener('keydown', e => {
    if (!$('#lightbox').classList.contains('active')) return;
    if (e.key === 'Escape') {
        $('#lightbox').classList.remove('active');
    } else if (e.key === 'ArrowLeft' && lightboxIndex > 0) {
        lightboxIndex--; showLightboxImage();
    } else if (e.key === 'ArrowRight' && lightboxIndex < lightboxImages.length - 1) {
        lightboxIndex++; showLightboxImage();
    }
});

/* ------------------------------------------------------------------ */
/* Payload build functions                                             */
/* ------------------------------------------------------------------ */
function getCheckedModalities() {
    const checked = Array.from($$('#chat-modalities input[type="checkbox"]:checked')).map(cb => cb.value);
    return checked.length ? checked : ['text'];
}

function buildImagePayload() {
    const prompt = $('#img-prompt').value.trim();
    const negative = $('#img-negative').value.trim();
    const size = $('#img-size').value.split('x');
    const steps = parseInt($('#img-steps').value);
    const cfg = parseFloat($('#img-cfg').value);
    const seed = parseInt($('#img-seed').value);
    const count = parseInt($('#img-count').value);

    const userContent = [];
    userContent.push({ type: 'text', text: prompt });
    if (imgAttachState.dataUrl) {
        userContent.push({ type: 'image_url', image_url: { url: imgAttachState.dataUrl } });
    }

    const body = {
        model: CONFIG.modelId,
        messages: [{ role: 'user', content: userContent.length === 1 ? prompt : userContent }],
        modalities: ['image'],
        stream: false,
        max_tokens: 2048,
        width: parseInt(size[0]),
        height: parseInt(size[1]),
        num_inference_steps: steps,
        cfg_scale: cfg,
    };
    if (seed >= 0) body.seed = seed;
    if (negative) body.negative_prompt = negative;
    if (count > 1) body.n = count;
    return body;
}

function buildVideoPayload() {
    const prompt = $('#vid-prompt').value.trim();
    const negative = $('#vid-negative').value.trim();
    const width = parseInt($('#vid-width').value);
    const height = parseInt($('#vid-height').value);
    const frames = parseInt($('#vid-frames').value);
    const fps = parseInt($('#vid-fps').value);
    const steps = parseInt($('#vid-steps').value);
    const cfg = parseFloat($('#vid-cfg').value);
    const seed = parseInt($('#vid-seed').value);

    const userContent = [];
    userContent.push({ type: 'text', text: prompt });
    if (vidAttachState.dataUrl) {
        userContent.push({ type: 'image_url', image_url: { url: vidAttachState.dataUrl } });
    }

    const body = {
        model: CONFIG.modelId,
        messages: [{ role: 'user', content: userContent.length === 1 ? prompt : userContent }],
        modalities: ['video'],
        stream: false,
        max_tokens: 2048,
        width: width,
        height: height,
        num_frames: frames,
        fps: fps,
        num_inference_steps: steps,
        cfg_scale: cfg,
    };
    if (seed >= 0) body.seed = seed;
    if (negative) body.negative_prompt = negative;
    return body;
}

function buildTtsPayload() {
    const body = {
        model: CONFIG.modelId,
        input: $('#tts-text').value.trim(),
        voice: $('#tts-voice').value,
        speed: parseFloat($('#tts-speed').value),
        response_format: 'wav',
    };
    const lang = $('#tts-lang').value.trim();
    if (lang) body.language = lang;
    const instr = $('#tts-instructions').value.trim();
    if (instr) body.instructions = instr;
    return body;
}


/* ------------------------------------------------------------------ */
/* IndexedDB persistence                                               */
/* ------------------------------------------------------------------ */
const DB_NAME = 'modelui-history';
const DB_VERSION = 1;
const STORES = { image: 'images', video: 'videos', tts: 'tts' };
const imgHistoryData = [];
const vidHistoryData = [];
const ttsHistoryData = [];

function openDB() {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, DB_VERSION);
        req.onupgradeneeded = () => {
            const db = req.result;
            for (const name of Object.values(STORES)) {
                if (!db.objectStoreNames.contains(name)) {
                    db.createObjectStore(name, { autoIncrement: true });
                }
            }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
}

async function idbSave(storeName, entries) {
    const db = await openDB();
    const tx = db.transaction(storeName, 'readwrite');
    const store = tx.objectStore(storeName);
    store.clear();
    for (const entry of entries) store.put(entry);
    return new Promise((resolve, reject) => {
        tx.oncomplete = () => { db.close(); resolve(); };
        tx.onerror = () => { db.close(); reject(tx.error); };
    });
}

async function idbLoad(storeName) {
    const db = await openDB();
    const tx = db.transaction(storeName, 'readonly');
    const store = tx.objectStore(storeName);
    return new Promise((resolve, reject) => {
        const req = store.getAll();
        req.onsuccess = () => { db.close(); resolve(req.result || []); };
        req.onerror = () => { db.close(); reject(req.error); };
    });
}

async function idbClear(storeName) {
    const db = await openDB();
    const tx = db.transaction(storeName, 'readwrite');
    tx.objectStore(storeName).clear();
    return new Promise((resolve, reject) => {
        tx.oncomplete = () => { db.close(); resolve(); };
        tx.onerror = () => { db.close(); reject(tx.error); };
    });
}

/* ------------------------------------------------------------------ */
/* Generation history helper                                           */
/* ------------------------------------------------------------------ */
const GEN_HISTORY_MAX = 50;

function prependGenCard(historyEl, footerEl, promptText, contentHtml, grid, time) {
    const card = document.createElement('div');
    card.className = 'gen-card';
    card.innerHTML =
        '<div class="gen-meta">' +
            '<span class="gen-prompt">' + escapeHtml(promptText) + '</span>' +
            '<span class="gen-time">' + (time || timeNow()) + '</span>' +
        '</div>' +
        '<div class="gen-results' + (grid ? ' gen-grid' : '') + '">' + contentHtml + '</div>';
    historyEl.prepend(card);
    /* Cap history */
    while (historyEl.children.length > GEN_HISTORY_MAX) {
        historyEl.removeChild(historyEl.lastChild);
    }
    footerEl.style.display = '';
    /* Wire lightbox clicks for images in this card */
    card.querySelectorAll('img').forEach(img => {
        img.addEventListener('click', () => openLightbox(img.src));
    });
}

/* ------------------------------------------------------------------ */
/* "Use as edit input" helpers                                          */
/* ------------------------------------------------------------------ */
function makeUseBtn(onClick) {
    const btn = document.createElement('button');
    btn.className = 'use-input-btn';
    btn.textContent = '+';
    btn.title = 'Use as edit input';
    btn.addEventListener('click', e => { e.stopPropagation(); onClick(); });
    return btn;
}

function wrapHistoryImg(img) {
    if (imgMode === 'generate') return img;
    const wrapper = document.createElement('div');
    wrapper.className = 'history-thumb';
    wrapper.appendChild(img);
    wrapper.appendChild(makeUseBtn(() => {
        imgAttachState.dataUrl = img.src;
        imgAttachState.file = null;
        renderImgUploads();
    }));
    return wrapper;
}

function addVidUseBtn(card, b64) {
    if (vidMode === 'generate') return;
    const meta = card.querySelector('.gen-meta');
    if (!meta) return;
    meta.appendChild(makeUseBtn(() => {
        vidAttachState.dataUrl = 'data:video/mp4;base64,' + b64;
        vidAttachState.file = { type: 'video/mp4', name: 'generated.mp4' };
        renderVidUploads();
    }));
}

/* ------------------------------------------------------------------ */
/* Shared: add message to a chat container                             */
/* ------------------------------------------------------------------ */
function addMessage(container, role, content) {
    const div = document.createElement('div');
    div.className = 'message ' + role;
    div.innerHTML =
        '<div class="message-role">' + (role === 'user' ? 'You' : 'Assistant') + '</div>' +
        '<div class="message-content">' + renderMarkdown(content) + '</div>';
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
}

/* ------------------------------------------------------------------ */
/* Chat                                                                */
/* ------------------------------------------------------------------ */
const chatState = { messages: [], files: [], streaming: false, controller: null };

/* File attachments */
$('#chat-attach').addEventListener('click', () => $('#chat-file').click());
$('#chat-file').addEventListener('change', e => {
    for (const file of e.target.files) {
        const reader = new FileReader();
        reader.onload = () => {
            chatState.files.push({ name: file.name, type: file.type, dataUrl: reader.result });
            renderChatUploads();
        };
        reader.readAsDataURL(file);
    }
    e.target.value = '';
});

function renderChatUploads() {
    const container = $('#chat-uploads');
    container.innerHTML = '';
    chatState.files.forEach((f, i) => {
        const div = document.createElement('div');
        div.className = 'upload-thumb';
        if (f.type.startsWith('image/')) {
            const img = document.createElement('img');
            img.src = f.dataUrl;
            img.alt = '';
            div.appendChild(img);
        } else {
            div.innerHTML = '<div class="audio-badge">&#9835;</div>';
        }
        const btn = document.createElement('button');
        btn.className = 'remove-btn';
        btn.textContent = '\u00d7';
        btn.onclick = () => { chatState.files.splice(i, 1); renderChatUploads(); };
        div.appendChild(btn);
        container.appendChild(div);
    });
}

function renderChatMedia(msg) {
    const content = msg.content;
    if (Array.isArray(content)) {
        let text = '';
        let mediaHtml = '';
        for (const part of content) {
            if (part.type === 'text') {
                text += part.text;
            } else if (part.type === 'image_url' && part.image_url?.url) {
                mediaHtml += '<img src="' + escapeHtml(part.image_url.url)
                    + '" style="height:120px;width:auto;max-width:100%;object-fit:cover;border-radius:var(--radius-sm);margin:0.25rem 0;cursor:pointer" alt="">';
            } else if (part.type === 'video_url' && part.video_url?.url) {
                mediaHtml += '<video controls src="' + escapeHtml(part.video_url.url)
                    + '" style="max-width:100%;border-radius:var(--radius);margin:0.25rem 0"></video>';
            }
        }
        if (mediaHtml) return { text, mediaHtml };
    }
    if (typeof content === 'string' && content.length > 1000
        && /^[A-Za-z0-9+/=]+$/.test(content.slice(0, 200))) {
        return {
            text: '',
            mediaHtml: '<img src="data:image/png;base64,' + escapeHtml(content)
                + '" style="height:120px;width:auto;max-width:100%;object-fit:cover;border-radius:var(--radius-sm);margin:0.25rem 0;cursor:pointer" alt="">'
        };
    }
    return null;
}

function pushChatMediaToHistory(msg) {
    const content = msg.content;
    if (!content) return;
    const time = timeNow();
    const prompt = '(from chat)';
    const items = Array.isArray(content) ? content : [];

    /* Handle raw base64 string content as image */
    if (typeof content === 'string' && content.length > 1000
        && /^[A-Za-z0-9+/=]+$/.test(content.slice(0, 200))) {
        const b64 = content;
        imgHistoryData.unshift({ b64, prompt, time });
        while (imgHistoryData.length > 200) imgHistoryData.pop();
        idbSave(STORES.image, imgHistoryData).catch(() => {});
        const historyEl = $('#img-history');
        const img = document.createElement('img');
        img.src = 'data:image/png;base64,' + b64;
        img.alt = '';
        img.addEventListener('click', () => openLightbox(img.src));
        historyEl.prepend(wrapHistoryImg(img));
        $('#img-history-footer').style.display = '';
        return;
    }

    for (const part of items) {
        if (part.type === 'image_url' && part.image_url?.url) {
            const url = part.image_url.url;
            const m = url.match(/^data:[^;]+;base64,(.+)$/);
            if (m) {
                const b64 = m[1];
                imgHistoryData.unshift({ b64, prompt, time });
                while (imgHistoryData.length > 200) imgHistoryData.pop();
                idbSave(STORES.image, imgHistoryData).catch(() => {});
                const historyEl = $('#img-history');
                const img = document.createElement('img');
                img.src = url;
                img.alt = '';
                img.addEventListener('click', () => openLightbox(img.src));
                historyEl.prepend(wrapHistoryImg(img));
                $('#img-history-footer').style.display = '';
            }
        } else if (part.type === 'video_url' && part.video_url?.url) {
            const url = part.video_url.url;
            const m = url.match(/^data:[^;]+;base64,(.+)$/);
            if (m) {
                try {
                    const binary = atob(m[1]);
                    const bytes = new Uint8Array(binary.length);
                    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
                    const blob = new Blob([bytes], { type: 'video/mp4' });
                    const blobUrl = URL.createObjectURL(blob);
                    const videoHtml = '<video controls src="' + blobUrl + '"></video>';
                    prependGenCard($('#vid-history'), $('#vid-history-footer'), prompt, videoHtml, false, time);
                    addVidUseBtn($('#vid-history').firstChild, m[1]);
                    vidHistoryData.push({ prompt, time, video: m[1] });
                    while (vidHistoryData.length > 200) vidHistoryData.shift();
                    idbSave(STORES.video, vidHistoryData).catch(() => {});
                } catch (e) { console.warn('[model-ui] Failed to process video from chat:', e); }
            }
        }
    }
}

async function sendChat() {
    if (chatState.streaming) {
        if (chatState.controller) chatState.controller.abort();
        return;
    }
    const input = $('#chat-input');
    const text = input.value.trim();
    const files = [...chatState.files];

    if (!text && !files.length) return;

    const container = $('#chat-messages');
    const modalities = getCheckedModalities();
    const hasNonText = modalities.some(m => m !== 'text');

    if (CAPS.chat && !CAPS.chat.includes('all')) {
        const caps = CAPS.chat.filter(c => !c.startsWith('require_'));
        const bad = modalities.filter(m => !caps.includes(m));
        if (bad.length) {
            addMessage(container, 'assistant', 'Unsupported modalities: ' + bad.join(', '));
            return;
        }
    }
    if (CAPS.chat) {
        if (CAPS.chat.includes('require_attach') && !files.length) {
            addMessage(container, 'assistant', 'This model requires a file attachment. Please attach a file before sending.');
            return;
        }
    }

    /* Build user content — multimodal if files attached, plain text otherwise */
    let userContent;
    if (files.length) {
        const parts = [];
        for (const f of files) {
            if (f.type.startsWith('audio/')) {
                const ext = f.name.split('.').pop();
                parts.push({ type: 'input_audio', input_audio: { data: f.dataUrl, format: ext } });
            } else {
                parts.push({ type: 'image_url', image_url: { url: f.dataUrl } });
            }
        }
        if (text) parts.push({ type: 'text', text: text });
        userContent = parts;
    } else {
        userContent = text;
    }

    chatState.messages.push({ role: 'user', content: userContent });

    let displayText = files.map(f => '[' + f.name + ']').join(' ');
    if (text) displayText += (displayText ? ' ' : '') + text;
    addMessage(container, 'user', displayText);

    const system = $('#chat-system').value.trim();
    const temp = parseFloat($('#chat-temp').value);
    const maxTokens = parseInt($('#chat-max-tokens').value);
    const apiMessages = [];
    if (system) apiMessages.push({ role: 'system', content: system });
    apiMessages.push(...chatState.messages);

    const body = { model: CONFIG.modelId, messages: apiMessages, temperature: temp, max_tokens: maxTokens, stream: !hasNonText };

    input.value = '';
    input.style.height = 'auto';
    chatState.files = [];
    renderChatUploads();

    const assistantDiv = addMessage(container, 'assistant', '');
    const contentEl = assistantDiv.querySelector('.message-content');
    contentEl.classList.add('cursor-blink');

    chatState.streaming = true;
    const sendBtn = $('#chat-send');
    sendBtn.textContent = 'Stop';

    /* Force non-streaming for non-text output modalities */
    if (hasNonText) {
        body.modalities = modalities;
        if (modalities.includes('audio')) body.audio = { voice: 'default', format: 'wav' };
    }

    const isStream = body.stream !== false;
    let fullResponse = '';
    let thinkResponse = '';
    let completed = false;
    chatState.controller = new AbortController();

    try {
        const response = await fetch('/api/chat/completions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            signal: chatState.controller.signal,
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({ error: response.statusText }));
            throw new Error(err.error?.message || err.error || JSON.stringify(err));
        }

        if (isStream) {
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let finished = false;
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();
                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    const data = line.slice(6).trim();
                    if (data === '[DONE]') { finished = true; break; }
                    try {
                        const delta = JSON.parse(data).choices?.[0]?.delta || {};
                        const cd = delta.content || '';
                        const rd = delta.reasoning_content || '';
                        if (cd) fullResponse += cd;
                        if (rd) thinkResponse += rd;
                        if (cd || rd) {
                            let display = fullResponse;
                            if (thinkResponse) display = '<think>' + thinkResponse + (fullResponse ? '</think>' : '') + fullResponse;
                            contentEl.innerHTML = renderWithThinking(display);
                            container.scrollTop = container.scrollHeight;
                        }
                    } catch (e) { console.warn('[model-ui] Failed to parse SSE chunk:', e); }
                }
                if (finished) break;
            }
        } else {
            const result = await response.json();
            const msg = result.choices?.[0]?.message || {};
            const mediaResult = renderChatMedia(msg);
            if (mediaResult) {
                let html = '';
                if (mediaResult.text) html += renderWithThinking(mediaResult.text);
                html += mediaResult.mediaHtml;
                contentEl.innerHTML = html;
                contentEl.querySelectorAll('img').forEach(img =>
                    img.addEventListener('click', () => openLightbox(img.src)));
                fullResponse = mediaResult.text;
                pushChatMediaToHistory(msg);
            } else {
                fullResponse = msg.content || '';
                thinkResponse = msg.reasoning_content || '';
                let display = fullResponse;
                if (thinkResponse) display = '<think>' + thinkResponse + '</think>' + fullResponse;
                contentEl.innerHTML = renderWithThinking(display);

                /* Handle audio response */
                const audioObj = msg.audio;
                if (audioObj && audioObj.data) {
                    let raw = audioObj.data;
                    if (raw.includes(',')) raw = raw.split(',')[1];
                    const bytes = atob(raw);
                    const arr = new Uint8Array(bytes.length);
                    for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
                    const blob = new Blob([arr], { type: 'audio/wav' });
                    $('#chat-audio').src = URL.createObjectURL(blob);
                }
            }
        }
        completed = true;
    } catch (e) {
        if (e.name !== 'AbortError') {
            fullResponse += '\n\nError: ' + e.message;
            let display = fullResponse;
            if (thinkResponse) display = '<think>' + thinkResponse + '</think>' + fullResponse;
            contentEl.innerHTML = renderWithThinking(display);
        }
    }

    contentEl.classList.remove('cursor-blink');
    if (completed) chatState.messages.push({ role: 'assistant', content: stripThinking(fullResponse) });
    chatState.streaming = false;
    chatState.controller = null;
    sendBtn.textContent = 'Send';
}

$('#chat-send').addEventListener('click', sendChat);
$('#chat-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendChat(); }
});
$('#chat-clear').addEventListener('click', () => {
    chatState.messages = [];
    chatState.files = [];
    $('#chat-messages').innerHTML = '';
    $('#chat-audio').removeAttribute('src');
    renderChatUploads();
});

/* ------------------------------------------------------------------ */
/* Image                                                               */
/* ------------------------------------------------------------------ */
const imgAttachState = { file: null, dataUrl: null };

function renderImgUploads() {
    const container = $('#img-uploads');
    container.innerHTML = '';
    if (imgAttachState.dataUrl) {
        const div = document.createElement('div');
        div.className = 'upload-thumb';
        const img = document.createElement('img');
        img.src = imgAttachState.dataUrl;
        img.alt = '';
        div.appendChild(img);
        const btn = document.createElement('button');
        btn.className = 'remove-btn';
        btn.textContent = '\u00d7';
        btn.onclick = () => { imgAttachState.file = null; imgAttachState.dataUrl = null; renderImgUploads(); };
        div.appendChild(btn);
        container.appendChild(div);
    }
    if (imgMode === 'edit') {
        $('#img-generate').textContent = 'Edit';
        $('#img-generate').disabled = !imgAttachState.dataUrl;
        $('#img-generate').title = imgAttachState.dataUrl ? '' : 'Attach an image first';
    } else if (imgMode === 'generate') {
        $('#img-generate').textContent = 'Generate';
    } else {
        $('#img-generate').textContent = imgAttachState.dataUrl ? 'Edit' : 'Generate';
    }
}

$('#img-attach').addEventListener('click', () => $('#img-file').click());
$('#img-file').addEventListener('change', e => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
        imgAttachState.file = file;
        imgAttachState.dataUrl = reader.result;
        renderImgUploads();
    };
    reader.readAsDataURL(file);
    e.target.value = '';
});

async function generateImage() {
    const prompt = $('#img-prompt').value.trim();
    if (!prompt) return;

    const isEdit = imgMode === 'edit' || (imgMode === 'both' && !!imgAttachState.dataUrl);
    const btn = $('#img-generate');
    const errEl = $('#img-error');
    errEl.innerHTML = '';
    btn.disabled = true;
    btn.textContent = isEdit ? 'Editing...' : 'Generating...';

    const body = buildImagePayload();

    try {
        const r = await fetch('/api/chat/completions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({ error: r.statusText }));
            throw new Error(err.error?.message || err.error || JSON.stringify(err));
        }
        const result = await r.json();
        const msg = result.choices?.[0]?.message || {};
        const content = msg.content;
        /* Extract base64 images from response */
        const b64List = [];
        if (Array.isArray(content)) {
            for (const part of content) {
                if (part.type === 'image_url' && part.image_url?.url) {
                    const m = part.image_url.url.match(/^data:[^;]+;base64,(.+)$/);
                    if (m) b64List.push(m[1]);
                }
            }
        } else if (typeof content === 'string' && content.length > 1000
            && /^[A-Za-z0-9+/=]+$/.test(content.slice(0, 200))) {
            b64List.push(content);
        }
        if (b64List.length) {
            const historyEl = $('#img-history');
            const time = timeNow();
            const frag = document.createDocumentFragment();
            for (const b64 of b64List) {
                const img = document.createElement('img');
                img.src = 'data:image/png;base64,' + b64;
                img.alt = '';
                img.addEventListener('click', () => openLightbox(img.src));
                frag.appendChild(wrapHistoryImg(img));
            }
            historyEl.prepend(frag);
            $('#img-history-footer').style.display = '';
            imgHistoryData.unshift(...b64List.map(b64 => ({ b64, prompt, time })));
            while (imgHistoryData.length > 200) imgHistoryData.pop();
            idbSave(STORES.image, imgHistoryData).catch(() => {});
        } else {
            errEl.innerHTML = '<div class="error-msg">No images returned</div>';
        }
    } catch (e) {
        errEl.innerHTML = '<div class="error-msg">' + escapeHtml(e.message) + '</div>';
    }
    btn.disabled = false;
    btn.textContent = isEdit ? 'Edit' : 'Generate';
}

$('#img-generate').addEventListener('click', generateImage);
$('#img-clear-history').addEventListener('click', () => {
    $('#img-history').innerHTML = '';
    $('#img-history-footer').style.display = 'none';
    imgHistoryData.length = 0;
    idbClear(STORES.image).catch(() => {});
});

/* ------------------------------------------------------------------ */
/* Video                                                               */
/* ------------------------------------------------------------------ */
const vidAttachState = { file: null, dataUrl: null };

function renderVidUploads() {
    const container = $('#vid-uploads');
    container.innerHTML = '';
    if (vidAttachState.dataUrl) {
        const div = document.createElement('div');
        div.className = 'upload-thumb';
        if (vidAttachState.file && vidAttachState.file.type.startsWith('video/')) {
            div.innerHTML = '<div class="audio-badge">&#9654;</div>';
        } else {
            const img = document.createElement('img');
            img.src = vidAttachState.dataUrl;
            img.alt = '';
            div.appendChild(img);
        }
        const btn = document.createElement('button');
        btn.className = 'remove-btn';
        btn.textContent = '\u00d7';
        btn.onclick = () => { vidAttachState.file = null; vidAttachState.dataUrl = null; renderVidUploads(); };
        div.appendChild(btn);
        container.appendChild(div);
    }
    if (vidMode === 'edit') {
        $('#vid-generate').textContent = 'Edit';
        $('#vid-generate').disabled = !vidAttachState.dataUrl;
        $('#vid-generate').title = vidAttachState.dataUrl ? '' : 'Attach a reference first';
    } else if (vidMode === 'generate') {
        $('#vid-generate').textContent = 'Generate';
    } else {
        $('#vid-generate').textContent = vidAttachState.dataUrl ? 'Edit' : 'Generate';
    }
}

$('#vid-attach').addEventListener('click', () => $('#vid-file').click());
$('#vid-file').addEventListener('change', e => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
        vidAttachState.file = file;
        vidAttachState.dataUrl = reader.result;
        renderVidUploads();
    };
    reader.readAsDataURL(file);
    e.target.value = '';
});

async function generateVideo() {
    const prompt = $('#vid-prompt').value.trim();
    if (!prompt) return;

    const isVidEdit = vidMode === 'edit' || (vidMode === 'both' && !!vidAttachState.dataUrl);
    const btn = $('#vid-generate');
    const errEl = $('#vid-error');
    errEl.innerHTML = '';
    btn.disabled = true;
    btn.textContent = isVidEdit ? 'Editing...' : 'Generating...';

    const body = buildVideoPayload();

    try {
        const r = await fetch('/api/chat/completions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({ error: r.statusText }));
            throw new Error(err.error?.message || err.error || JSON.stringify(err));
        }
        const result = await r.json();
        const msg = result.choices?.[0]?.message || {};
        const content = msg.content;
        let videoHtml = '';
        let videoB64 = null;
        const items = Array.isArray(content) ? content : [];
        for (const part of items) {
            if (part.type === 'video_url' && part.video_url?.url) {
                const m = part.video_url.url.match(/^data:[^;]+;base64,(.+)$/);
                if (m) {
                    videoB64 = m[1];
                    const binary = atob(m[1]);
                    const bytes = new Uint8Array(binary.length);
                    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
                    const blob = new Blob([bytes], { type: 'video/mp4' });
                    const url = URL.createObjectURL(blob);
                    videoHtml += '<video controls src="' + url + '"></video>';
                }
            }
        }
        if (videoHtml) {
            const time = timeNow();
            prependGenCard(
                $('#vid-history'), $('#vid-history-footer'),
                prompt, videoHtml, false, time
            );
            if (videoB64) addVidUseBtn($('#vid-history').firstChild, videoB64);
            vidHistoryData.push({ prompt, time, video: videoB64 });
            while (vidHistoryData.length > 200) vidHistoryData.shift();
            idbSave(STORES.video, vidHistoryData).catch(() => {});
        } else {
            errEl.innerHTML = '<div class="error-msg">No video returned</div>';
        }
    } catch (e) {
        errEl.innerHTML = '<div class="error-msg">' + escapeHtml(e.message) + '</div>';
    }
    btn.disabled = false;
    btn.textContent = isVidEdit ? 'Edit' : 'Generate';
}

$('#vid-generate').addEventListener('click', generateVideo);
$('#vid-clear-history').addEventListener('click', () => {
    $('#vid-history').innerHTML = '';
    $('#vid-history-footer').style.display = 'none';
    vidHistoryData.length = 0;
    idbClear(STORES.video).catch(() => {});
});

/* ------------------------------------------------------------------ */
/* TTS                                                                 */
/* ------------------------------------------------------------------ */

async function loadVoices() {
    try {
        const r = await fetch('/api/audio/voices');
        if (r.ok) {
            const data = await r.json();
            const voices = Array.isArray(data) ? data : (data.voices || []);
            const sel = $('#tts-voice');
            if (voices.length) {
                sel.innerHTML = '';
                for (const v of voices) {
                    const name = typeof v === 'string' ? v : (v.name || v.id || String(v));
                    sel.innerHTML += '<option value="' + escapeHtml(name) + '">' + escapeHtml(name) + '</option>';
                }
            }
        }
    } catch (e) { console.warn('[model-ui] Failed to load voices:', e); }
    ttsVoicesLoaded = true;
}

async function synthesize() {
    const text = $('#tts-text').value.trim();
    if (!text) return;

    const btn = $('#tts-synth');
    const errEl = $('#tts-error');
    errEl.innerHTML = '';
    btn.disabled = true;
    btn.textContent = 'Synthesizing...';

    const body = buildTtsPayload();

    try {
        const r = await fetch('/api/audio/speech', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({ error: r.statusText }));
            throw new Error(err.error?.message || err.error || JSON.stringify(err));
        }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        const time = timeNow();
        prependGenCard(
            $('#tts-history'), $('#tts-history-footer'),
            text,
            '<audio controls autoplay src="' + url + '"></audio>',
            false, time
        );
        /* Persist as data URI */
        const fr = new FileReader();
        fr.onload = () => {
            ttsHistoryData.push({ prompt: text, time, audio: fr.result });
            while (ttsHistoryData.length > 200) ttsHistoryData.shift();
            idbSave(STORES.tts, ttsHistoryData).catch(() => {});
        };
        fr.readAsDataURL(blob);
    } catch (e) {
        errEl.innerHTML = '<div class="error-msg">' + escapeHtml(e.message) + '</div>';
    }
    btn.disabled = false;
    btn.textContent = 'Synthesize';
}

$('#tts-synth').addEventListener('click', synthesize);
$('#tts-clear-history').addEventListener('click', () => {
    $('#tts-history').innerHTML = '';
    $('#tts-history-footer').style.display = 'none';
    ttsHistoryData.length = 0;
    idbClear(STORES.tts).catch(() => {});
});

/* ------------------------------------------------------------------ */
/* STT (Speech-to-Text)                                                */
/* ------------------------------------------------------------------ */
const sttAttachState = { file: null, dataUrl: null };

function renderSttUploads() {
    const container = $('#stt-uploads');
    container.innerHTML = '';
    if (sttAttachState.file) {
        const div = document.createElement('div');
        div.className = 'upload-thumb';
        div.innerHTML = '<div class="audio-badge">&#9835;</div>';
        const btn = document.createElement('button');
        btn.className = 'remove-btn';
        btn.textContent = '\u00d7';
        btn.onclick = () => { sttAttachState.file = null; sttAttachState.dataUrl = null; renderSttUploads(); };
        div.appendChild(btn);
        container.appendChild(div);
        const name = document.createElement('span');
        name.style.cssText = 'font-size:0.75rem;color:var(--text-secondary);margin-left:0.25rem';
        name.textContent = sttAttachState.file.name;
        container.appendChild(name);
    }
    $('#stt-transcribe').disabled = !sttAttachState.file;
}

$('#stt-attach').addEventListener('click', () => $('#stt-file').click());
$('#stt-file').addEventListener('change', e => {
    const file = e.target.files[0];
    if (!file) return;
    sttAttachState.file = file;
    const reader = new FileReader();
    reader.onload = () => { sttAttachState.dataUrl = reader.result; };
    reader.readAsDataURL(file);
    renderSttUploads();
    e.target.value = '';
});

async function transcribe() {
    if (!sttAttachState.file) return;

    const btn = $('#stt-transcribe');
    const errEl = $('#stt-error');
    errEl.innerHTML = '';
    btn.disabled = true;
    btn.textContent = 'Transcribing...';

    const fd = new FormData();
    fd.append('file', sttAttachState.file, sttAttachState.file.name);
    fd.append('model', CONFIG.modelId);
    const lang = $('#stt-lang').value.trim();
    if (lang) fd.append('language', lang);
    const prompt = $('#stt-prompt').value.trim();
    if (prompt) fd.append('prompt', prompt);
    const temp = parseFloat($('#stt-temp').value);
    if (temp > 0) fd.append('temperature', String(temp));

    try {
        const r = await fetch('/api/audio/transcriptions', {
            method: 'POST',
            body: fd,
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({ error: r.statusText }));
            throw new Error(err.error?.message || err.error || JSON.stringify(err));
        }
        const data = await r.json();
        const text = data.text || JSON.stringify(data);
        const time = timeNow();
        const fileName = sttAttachState.file.name;
        const html = '<div style="font-size:0.875rem;line-height:1.6;white-space:pre-wrap">' + escapeHtml(text) + '</div>';
        prependGenCard($('#stt-history'), $('#stt-history-footer'), fileName, html, false, time);
    } catch (e) {
        errEl.innerHTML = '<div class="error-msg">' + escapeHtml(e.message) + '</div>';
    }
    btn.disabled = !sttAttachState.file;
    btn.textContent = 'Transcribe';
}

$('#stt-transcribe').addEventListener('click', transcribe);
$('#stt-clear-history').addEventListener('click', () => {
    $('#stt-history').innerHTML = '';
    $('#stt-history-footer').style.display = 'none';
});

/* ------------------------------------------------------------------ */
/* Restore history from IndexedDB                                      */
/* ------------------------------------------------------------------ */
(async function restoreHistory() {
    /* Images — array of { b64, prompt?, time? } objects */
    const imgEntries = await idbLoad(STORES.image).catch(() => []);
    imgHistoryData.push(...imgEntries);
    if (imgEntries.length) {
        const historyEl = $('#img-history');
        const displayEntries = imgEntries.slice(0, GEN_HISTORY_MAX);
        for (const entry of displayEntries) {
            const b64 = entry.b64;
            if (!b64) continue;
            const img = document.createElement('img');
            img.src = 'data:image/png;base64,' + b64;
            img.alt = '';
            img.addEventListener('click', () => openLightbox(img.src));
            historyEl.appendChild(wrapHistoryImg(img));
        }
        $('#img-history-footer').style.display = '';
    }

    /* Videos */
    const vidEntries = await idbLoad(STORES.video).catch(() => []);
    vidHistoryData.push(...vidEntries);
    for (const entry of vidEntries) {
        let html = '';
        if (entry.video) {
            try {
                const binary = atob(entry.video);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
                const blob = new Blob([bytes], { type: 'video/mp4' });
                html = '<video controls src="' + URL.createObjectURL(blob) + '"></video>';
            } catch {
                html = '<div class="error-msg">Video expired \u2014 regenerate</div>';
            }
        } else {
            html = '<div class="error-msg">Video expired \u2014 regenerate</div>';
        }
        prependGenCard($('#vid-history'), $('#vid-history-footer'), entry.prompt, html, false, entry.time);
        if (entry.video) addVidUseBtn($('#vid-history').firstChild, entry.video);
    }

    /* TTS */
    const ttsEntries = await idbLoad(STORES.tts).catch(() => []);
    ttsHistoryData.push(...ttsEntries);
    for (const entry of ttsEntries) {
        let html = '';
        if (entry.audio) {
            html = '<audio controls src="' + entry.audio + '"></audio>';
        } else {
            html = '<div class="error-msg">Audio expired \u2014 regenerate</div>';
        }
        prependGenCard($('#tts-history'), $('#tts-history-footer'), entry.prompt, html, false, entry.time);
    }
})().catch(e => console.warn('[model-ui] Failed to restore history:', e));
