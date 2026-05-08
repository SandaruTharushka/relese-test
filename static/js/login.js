function showAlert(el, type, msg) {
  el.className = 'alert alert-' + (type === 'error' ? 'error' : 'success');
  el.textContent = msg;
  el.style.display = 'flex';
}

function toggleVis(id, btn) {
  const el = document.getElementById(id);
  el.type = el.type === 'password' ? 'text' : 'password';
  btn.textContent = el.type === 'password' ? '👁' : '🙈';
}

async function parseApiResponse(res) {
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return { status: res.status, ok: res.ok, data: await res.json() };
  }
  return { status: res.status, ok: res.ok, data: null };
}

function authMessageFromResponse(result) {
  const data = result?.data || {};
  if (data.msg) return data.msg;
  if (result?.status === 401) return 'Invalid username or password';
  if (result?.status === 409) return 'First-run setup is required before sign in.';
  return 'Login failed. Please try again.';
}

async function checkServerHealth() {
  try {
    const res = await fetch('/health', { method: 'GET' });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      if (data && data.status === 'starting') return 'starting';
      return 'error';
    }
    return 'ok';
  } catch (_) {
    return 'unreachable';
  }
}

async function doLogin() {
  const btn = document.getElementById('loginBtn');
  const u = document.getElementById('inp_user').value.trim();
  const p = document.getElementById('inp_pass').value;
  const al = document.getElementById('loginAlert');

  if (!u || !p) {
    showAlert(al, 'error', 'Please fill in all required fields');
    return;
  }

  btn.disabled = true;
  btn.textContent = '⏳ Signing in…';

  // Pre-flight: confirm the backend is reachable before sending credentials.
  const health = await checkServerHealth();
  if (health === 'starting') {
    showAlert(al, 'error', 'The server is still starting up. Please wait a moment and try again.');
    btn.disabled = false;
    btn.textContent = '🔐 Sign In';
    return;
  }
  if (health !== 'ok') {
    showAlert(al, 'error',
      'Cannot reach the backend server. ' +
      'If you just opened the application, wait a few seconds and try again. ' +
      'If the problem persists, restart the application and check the log file in ' +
      '%LOCALAPPDATA%\\Garage Management System\\logs\\desktop-runtime.log'
    );
    btn.disabled = false;
    btn.textContent = '🔐 Sign In';
    return;
  }

  let result = null;
  try {
    const res = await fetch('/login', {
      method: 'POST',
      headers: withCsrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ username: u, password: p }),
    });
    result = await parseApiResponse(res);
  } catch (_) {
    showAlert(al, 'error',
      'Lost connection to the backend server. ' +
      'Please restart the application. ' +
      'Check logs at: %LOCALAPPDATA%\\Garage Management System\\logs\\desktop-runtime.log'
    );
    btn.disabled = false;
    btn.textContent = '🔐 Sign In';
    return;
  }

  const data = result?.data || {};
  if (data.csrf_expired) {
    window.location.reload();
    return;
  }

  if (data.ok) {
    window.location.href = data.role === 'Cashier' ? '/billing' : '/dashboard';
    return;
  }

  showAlert(al, 'error', authMessageFromResponse(result));
  btn.disabled = false;
  btn.textContent = '🔐 Sign In';
}

// ── Virtual Keyboard & Mode Selection ────────────────────────────────────────

const MODE_KEY = 'supermart_input_mode';
let vkActiveInput = null;
let vkShiftOn = false;

const VK_ROWS = [
  ['1','2','3','4','5','6','7','8','9','0'],
  ['q','w','e','r','t','y','u','i','o','p'],
  ['a','s','d','f','g','h','j','k','l'],
  ['SHIFT','z','x','c','v','b','n','m','BACKSPACE'],
  ['@','.','_','-','SPACE','TAB','ENTER','HIDE'],
];

const VK_LABELS  = { SHIFT:'⇧', BACKSPACE:'⌫', SPACE:'Space', TAB:'Tab', ENTER:'↵', HIDE:'✕' };
const VK_SHIFT_N = { '1':'!','2':'@','3':'#','4':'$','5':'%','6':'^','7':'&','8':'*','9':'(','0':')' };

function vkBuild() {
  const kb = document.getElementById('vKeyboard');
  if (!kb || kb.children.length) return;

  VK_ROWS.forEach(row => {
    const rowEl = document.createElement('div');
    rowEl.className = 'vk-row';

    row.forEach(key => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.setAttribute('data-vkey', key);
      btn.textContent = VK_LABELS[key] !== undefined ? VK_LABELS[key] : key;

      let extraClass = '';
      if (key === 'SPACE')                              extraClass = 'xwide';
      else if (['SHIFT','BACKSPACE','ENTER','HIDE','TAB'].includes(key)) extraClass = 'wide action';
      if (key === 'SHIFT')  btn.id = 'vkShiftBtn';
      if (key === 'ENTER')  extraClass += ' enter-key';

      btn.className = 'vk-key' + (extraClass ? ' ' + extraClass.trim() : '');

      const onPress = (e) => { e.preventDefault(); vkKey(key); };
      btn.addEventListener('mousedown', onPress);
      btn.addEventListener('touchstart', onPress, { passive: false });
      rowEl.appendChild(btn);
    });

    kb.appendChild(rowEl);
  });
}

function vkKey(key) {
  if (key === 'HIDE')      { vkHide(); return; }
  if (key === 'SHIFT')     { vkToggleShift(); return; }
  if (key === 'ENTER')     { vkHide(); doLogin(); return; }
  if (key === 'TAB') {
    const u = document.getElementById('inp_user');
    const p = document.getElementById('inp_pass');
    const next = (vkActiveInput === u) ? p : u;
    vkActiveInput = next;
    next.focus();
    return;
  }
  if (key === 'BACKSPACE') {
    if (!vkActiveInput) return;
    const v = vkActiveInput.value;
    const s = vkActiveInput.selectionStart;
    if (s > 0) {
      vkActiveInput.value = v.slice(0, s - 1) + v.slice(s);
      try { vkActiveInput.setSelectionRange(s - 1, s - 1); } catch (_) {}
    }
    return;
  }

  if (!vkActiveInput) return;

  let ch = key;
  if (key === 'SPACE')                    ch = ' ';
  else if (vkShiftOn && VK_SHIFT_N[key]) ch = VK_SHIFT_N[key];
  else if (vkShiftOn && key >= 'a' && key <= 'z') ch = key.toUpperCase();

  const s = vkActiveInput.selectionStart;
  const e = vkActiveInput.selectionEnd;
  vkActiveInput.value = vkActiveInput.value.slice(0, s) + ch + vkActiveInput.value.slice(e);
  try { vkActiveInput.setSelectionRange(s + 1, s + 1); } catch (_) {}

  // auto-release shift after one character
  if (vkShiftOn) { vkShiftOn = false; vkRenderShift(); }
}

function vkToggleShift() {
  vkShiftOn = !vkShiftOn;
  vkRenderShift();
}

function vkRenderShift() {
  const shiftBtn = document.getElementById('vkShiftBtn');
  if (shiftBtn) {
    shiftBtn.classList.toggle('shift-on', vkShiftOn);
  }
  const kb = document.getElementById('vKeyboard');
  if (!kb) return;
  kb.querySelectorAll('[data-vkey]').forEach(btn => {
    const k = btn.getAttribute('data-vkey');
    if (k.length === 1 && k >= 'a' && k <= 'z') {
      btn.textContent = vkShiftOn ? k.toUpperCase() : k;
    } else if (VK_SHIFT_N[k]) {
      btn.textContent = vkShiftOn ? VK_SHIFT_N[k] : k;
    }
  });
}

function vkShow(inputEl) {
  vkActiveInput = inputEl;
  const kb = document.getElementById('vKeyboard');
  if (kb) kb.style.display = 'block';
}

function vkHide() {
  const kb = document.getElementById('vKeyboard');
  if (kb) kb.style.display = 'none';
}

// ── Mode Application ──────────────────────────────────────────────────────────

function applyMode(mode) {
  localStorage.setItem(MODE_KEY, mode);

  // hide overlay
  const overlay = document.getElementById('modeOverlay');
  if (overlay) overlay.style.display = 'none';

  // update badge
  const badge = document.getElementById('modeBadge');
  if (badge) {
    badge.style.display = '';
    badge.textContent = mode === 'touch' ? '⌨️ PC Mode' : '🖐️ Touch Mode';
    badge.onclick = () => applyMode(mode === 'touch' ? 'pc' : 'touch');
  }

  const uInp = document.getElementById('inp_user');
  const pInp = document.getElementById('inp_pass');

  if (mode === 'touch') {
    [uInp, pInp].forEach(inp => {
      inp.setAttribute('readonly', '');
      inp.setAttribute('inputmode', 'none');
    });
    uInp.addEventListener('click',  () => vkShow(uInp));
    uInp.addEventListener('focus',  () => vkShow(uInp));
    pInp.addEventListener('click',  () => vkShow(pInp));
    pInp.addEventListener('focus',  () => vkShow(pInp));
    setTimeout(() => { uInp.focus(); vkShow(uInp); }, 120);
  } else {
    [uInp, pInp].forEach(inp => {
      inp.removeAttribute('readonly');
      inp.removeAttribute('inputmode');
    });
    vkHide();
    setTimeout(() => uInp.focus(), 120);
  }
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('themeBtn').addEventListener('click', toggleTheme);
  document.getElementById('togglePassBtn').addEventListener('click', function () { toggleVis('inp_pass', this); });
  document.getElementById('loginBtn').addEventListener('click', doLogin);
  document.getElementById('inp_user').addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });
  document.getElementById('inp_pass').addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });

  // Mode overlay buttons
  document.getElementById('btnTouchMode').addEventListener('click', () => applyMode('touch'));
  document.getElementById('btnPcMode').addEventListener('click',    () => applyMode('pc'));

  // Build keyboard DOM
  vkBuild();

  // Restore saved mode; default to 'pc' so the overlay never blocks startup
  const saved = localStorage.getItem(MODE_KEY);
  applyMode(saved === 'touch' ? 'touch' : 'pc');
});
