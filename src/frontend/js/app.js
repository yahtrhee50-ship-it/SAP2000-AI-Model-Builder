/**
 * Main application controller.
 * Manages tab switching, AI chat session, and manual-form mode.
 */
import { StructuralViewer } from './viewer.js';
import { buildModelFromForm, fetchPreview, buildFromForm, initManualForm } from './manual.js';

const API = '';

let viewer = null;
let sessionId = null;
let isStreaming = false;
let activeTab = 'ai';
let pendingModel = null;   // model dict waiting for SAP2000 build (both modes)

// ── DOM refs ───────────────────────────────────────────────────────────────
const msgInput        = document.getElementById('msg-input');
const messages        = document.getElementById('messages');
const sendBtn         = document.getElementById('send-btn');
const startBtn        = document.getElementById('start-btn');
const buildBtnAI      = document.getElementById('build-btn-ai');
const buildBtnManual  = document.getElementById('build-btn-manual');
const previewBtn      = document.getElementById('preview-btn');
const providerSel     = document.getElementById('provider-select');
const apiKeyInput     = document.getElementById('api-key');
const sapDot          = document.getElementById('sap-dot');
const sapLabel        = document.getElementById('sap-label');
const completionFill  = document.getElementById('completion-fill');
const infoBar         = document.getElementById('info-bar');
const buildOverlay    = document.getElementById('build-overlay');
const buildModalClose = document.getElementById('build-modal-close');
const buildConfirm    = document.getElementById('build-confirm');
const savePathInput   = document.getElementById('save-path');

// ── Init ───────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  viewer = new StructuralViewer('viewer-canvas');
  initManualForm();
  initTabs();
  checkSapStatus();
  setInterval(checkSapStatus, 15000);
});

// ── Tab switching ──────────────────────────────────────────────────────────
function initTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      activeTab = tab;
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`tab-${tab}`).classList.add('active');
    });
  });
}

// ── AI Chat ────────────────────────────────────────────────────────────────
startBtn?.addEventListener('click', startSession);
sendBtn?.addEventListener('click', sendMessage);
msgInput?.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

buildBtnAI?.addEventListener('click', () => {
  pendingModel = null;   // AI mode uses session_id path
  buildOverlay.classList.add('show');
});

async function startSession() {
  const provider = providerSel.value;
  const apiKey   = apiKeyInput.value.trim();
  if (!apiKey) { alert('Please enter your API key.'); return; }

  startBtn.disabled = true;
  startBtn.textContent = 'Connecting…';
  clearMessages();

  try {
    const res = await fetch(`${API}/api/chat/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, api_key: apiKey }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Start failed');

    sessionId = data.session_id;
    appendMessage('ai', data.message);
    sendBtn.disabled = false;
    msgInput.disabled = false;
    msgInput.focus();
    startBtn.textContent = 'Restart';
    startBtn.disabled = false;
  } catch (err) {
    appendMessage('system', `Error: ${err.message}`);
    startBtn.disabled = false;
    startBtn.textContent = 'Start Interview';
  }
}

async function sendMessage() {
  if (!sessionId || isStreaming) return;
  const text = msgInput.value.trim();
  if (!text) return;

  appendMessage('user', text);
  msgInput.value = '';
  isStreaming = true;
  sendBtn.disabled = true;

  const typingEl = appendTyping();

  try {
    const res = await fetch(`${API}/api/chat/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Message failed');

    typingEl.remove();
    appendMessage('ai', data.message);

    if (data.preview) updatePreview(data.preview);

    if (data.model_ready) {
      buildBtnAI.disabled = false;
      appendMessage('system', 'Model complete. Review the 3D preview, then click "Build in SAP2000".');
    }
  } catch (err) {
    typingEl.remove();
    appendMessage('system', `Error: ${err.message}`);
  } finally {
    isStreaming = false;
    sendBtn.disabled = false;
    msgInput.focus();
  }
}

// ── Manual form ────────────────────────────────────────────────────────────
previewBtn?.addEventListener('click', async () => {
  previewBtn.disabled = true;
  previewBtn.textContent = 'Updating…';
  try {
    const model = buildModelFromForm();
    const preview = await fetchPreview(model);
    updatePreview(preview);
    pendingModel = model;
  } catch (err) {
    showInfoMessage(`Preview error: ${err.message}`);
  } finally {
    previewBtn.disabled = false;
    previewBtn.textContent = 'Update 3D Preview';
  }
});

buildBtnManual?.addEventListener('click', async () => {
  // Build a fresh model from the form, then open the modal
  try {
    const model = buildModelFromForm();
    const preview = await fetchPreview(model);
    updatePreview(preview);
    pendingModel = model;
    buildOverlay.classList.add('show');
  } catch (err) {
    showInfoMessage(`Validation error: ${err.message}`);
  }
});

// ── Build in SAP2000 (shared modal) ───────────────────────────────────────
buildModalClose?.addEventListener('click', () => buildOverlay.classList.remove('show'));

buildConfirm?.addEventListener('click', async () => {
  buildOverlay.classList.remove('show');
  buildConfirm.disabled = true;
  buildConfirm.textContent = 'Building…';

  const savePath = savePathInput.value.trim();

  try {
    let report;

    if (pendingModel) {
      // Manual mode: post model dict directly
      const data = await buildFromForm(pendingModel, savePath);
      report = data.report;
    } else {
      // AI mode: use session_id
      await fetch(`${API}/api/sap2000/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ visible: true }),
      });
      const res = await fetch(`${API}/api/sap2000/build/${sessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, save_path: savePath }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Build failed');
      report = data.report;
    }

    const msg = `SAP2000 model built!\nJoints: ${report.joints?.length ?? 0} | Frames: ${report.frames?.length ?? 0} | Areas: ${report.areas?.length ?? 0}` +
      (report.saved_to ? `\nSaved to: ${report.saved_to}` : '');

    if (activeTab === 'ai') appendMessage('system', msg);
    else showInfoMessage(msg);

  } catch (err) {
    if (activeTab === 'ai') appendMessage('system', `Build error: ${err.message}`);
    else showInfoMessage(`Build error: ${err.message}`);
  } finally {
    buildConfirm.disabled = false;
    buildConfirm.textContent = 'Build Model';
  }
});

// ── Preview update ─────────────────────────────────────────────────────────
function updatePreview(data) {
  viewer.update(data);

  const summary = data.completion || {};
  const total   = Object.keys(summary).length;
  const done    = Object.values(summary).filter(Boolean).length;
  const pct     = total ? Math.round((done / total) * 100) : 0;

  if (completionFill) completionFill.style.width = pct + '%';

  infoBar.innerHTML = '';
  Object.entries(summary).forEach(([key, ok]) => {
    const chip = document.createElement('div');
    chip.className = `info-chip ${ok ? 'complete' : 'missing'}`;
    chip.textContent = `${ok ? '✓' : '○'} ${key}`;
    infoBar.appendChild(chip);
  });

  if (data.loads) {
    const chip = document.createElement('div');
    chip.className = 'info-chip';
    chip.textContent = `DL ${data.loads.dead_load} | LL ${data.loads.live_load} kN/m²`;
    infoBar.appendChild(chip);
  }
}

// ── SAP2000 status ─────────────────────────────────────────────────────────
async function checkSapStatus() {
  try {
    const res = await fetch(`${API}/api/sap2000/status`);
    const data = await res.json();
    if (data.connected) {
      sapDot.classList.add('connected');
      sapLabel.textContent = 'SAP2000 connected';
    } else {
      sapDot.classList.remove('connected');
      sapLabel.textContent = 'SAP2000 not connected';
    }
  } catch {
    sapDot.classList.remove('connected');
    sapLabel.textContent = 'SAP2000 offline';
  }
}

// ── Info bar message (manual mode) ────────────────────────────────────────
function showInfoMessage(text) {
  infoBar.innerHTML = '';
  const chip = document.createElement('div');
  chip.className = 'info-chip';
  chip.textContent = text;
  infoBar.appendChild(chip);
}

// ── Chat UI helpers ────────────────────────────────────────────────────────
function appendMessage(role, text) {
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  div.innerHTML = formatMessage(text);
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

function appendTyping() {
  const div = document.createElement('div');
  div.className = 'msg ai';
  div.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

function clearMessages() { messages.innerHTML = ''; }

function formatMessage(text) {
  return text
    .replace(/```json([\s\S]*?)```/g, '<pre>$1</pre>')
    .replace(/```([\s\S]*?)```/g, '<pre>$1</pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>');
}
