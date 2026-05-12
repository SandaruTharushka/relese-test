'use strict';

// ── Helpers ───────────────────────────────────────────────────────────────────

function logLine(msg) {
  const el = document.getElementById('log');
  const ts = new Date().toLocaleTimeString();
  el.textContent += `[${ts}] ${msg}\n`;
  el.scrollTop = el.scrollHeight;
}

function clearLog() {
  document.getElementById('log').textContent = '';
}

function showMsg(id, text, isOk) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.className = 'test-result ' + (isOk ? 'ok' : 'err');
}

async function apiFetch(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
    ...opts,
  });
  return res.json();
}

// ── Bridge status ─────────────────────────────────────────────────────────────

async function refreshStatus() {
  try {
    const data = await apiFetch('/api/scanner/status');
    const pill = document.getElementById('bridge-pill');
    const details = document.getElementById('status-details');

    if (data.bridge_running) {
      pill.className = 'status-pill pill-green';
      pill.textContent = '● Bridge Running';
    } else {
      pill.className = 'status-pill pill-red';
      pill.textContent = '● Bridge Stopped';
    }

    details.innerHTML = [
      `<b>Sales scanner:</b> ${data.sales_configured ? '✓ Configured' : '✗ Not configured'}`,
      `<b>Workshop scanner:</b> ${data.workshop_configured ? '✓ Configured' : '✗ Not configured'}`,
      `<b>API URL:</b> ${data.api_url || '—'}`,
      `<b>Debounce:</b> ${data.debounce_ms ?? '—'} ms`,
    ].join('<br>');

    logLine(`Status refreshed — bridge ${data.bridge_running ? 'running' : 'stopped'}`);
  } catch (e) {
    logLine('Status check failed: ' + e.message);
  }
}

// ── Device detection ──────────────────────────────────────────────────────────

async function detectDevices() {
  logLine('Detecting HID keyboard devices…');
  try {
    const data = await apiFetch('/api/scanner/devices');
    const devices = data.devices || [];
    logLine(`Found ${devices.length} device(s).`);

    const workshopSel = document.getElementById('workshop-device');
    const salesSel = document.getElementById('sales-device');

    // Preserve current selection
    const prevWorkshop = workshopSel.value;
    const prevSales = salesSel.value;

    [workshopSel, salesSel].forEach(sel => {
      while (sel.options.length > 1) sel.remove(1);
    });

    devices.forEach(d => {
      [workshopSel, salesSel].forEach(sel => {
        const opt = new Option(d.name, d.id);
        sel.add(opt);
      });
    });

    workshopSel.value = prevWorkshop;
    salesSel.value = prevSales;

    if (data.warning) logLine('Warning: ' + data.warning);
  } catch (e) {
    logLine('Device detection failed: ' + e.message);
  }
}

// ── Load config ───────────────────────────────────────────────────────────────

async function loadConfig() {
  try {
    const data = await apiFetch('/api/scanner/config');
    const cfg = data.config || {};

    document.getElementById('api-url').value = cfg.api_url || 'http://127.0.0.1:5000';
    document.getElementById('debounce-ms').value = cfg.debounce_ms ?? 300;

    // Pre-select saved devices if they appear in the lists after detectDevices
    if (cfg.workshop_scanner_device_id) {
      const sel = document.getElementById('workshop-device');
      let found = false;
      for (const opt of sel.options) { if (opt.value === cfg.workshop_scanner_device_id) { found = true; break; } }
      if (!found) {
        sel.add(new Option(cfg.workshop_scanner_device_id, cfg.workshop_scanner_device_id));
      }
      sel.value = cfg.workshop_scanner_device_id;
    }
    if (cfg.sales_scanner_device_id) {
      const sel = document.getElementById('sales-device');
      let found = false;
      for (const opt of sel.options) { if (opt.value === cfg.sales_scanner_device_id) { found = true; break; } }
      if (!found) {
        sel.add(new Option(cfg.sales_scanner_device_id, cfg.sales_scanner_device_id));
      }
      sel.value = cfg.sales_scanner_device_id;
    }
  } catch (e) {
    logLine('Failed to load config: ' + e.message);
  }
}

// ── Save config ───────────────────────────────────────────────────────────────

async function saveConfig() {
  const payload = {
    workshop_scanner_device_id: document.getElementById('workshop-device').value || null,
    sales_scanner_device_id: document.getElementById('sales-device').value || null,
    api_url: document.getElementById('api-url').value.trim() || 'http://127.0.0.1:5000',
    debounce_ms: parseInt(document.getElementById('debounce-ms').value, 10) || 300,
  };

  try {
    const data = await apiFetch('/api/scanner/config', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    if (data.ok) {
      showMsg('save-msg', '✓ Configuration saved. Restart the app to apply changes.', true);
      logLine('Config saved successfully.');
    } else {
      showMsg('save-msg', '✗ ' + (data.error || 'Save failed'), false);
    }
  } catch (e) {
    showMsg('save-msg', '✗ Network error: ' + e.message, false);
  }
}

// ── Test scan ─────────────────────────────────────────────────────────────────

async function testScan() {
  const barcode = document.getElementById('test-barcode').value.trim() || 'TEST-0000';
  logLine(`Sending test scan: ${barcode}`);
  try {
    const data = await apiFetch('/api/scanner/test-scan', {
      method: 'POST',
      body: JSON.stringify({ barcode }),
    });
    if (data.ok) {
      showMsg('test-result', `✓ Test scan OK — barcode: ${data.barcode}. ${data.note}`, true);
      logLine(`Test scan accepted: ${data.barcode}`);
    } else {
      showMsg('test-result', '✗ ' + (data.error || 'Test failed'), false);
    }
  } catch (e) {
    showMsg('test-result', '✗ Network error: ' + e.message, false);
    logLine('Test scan error: ' + e.message);
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
  await Promise.all([refreshStatus(), detectDevices()]);
  await loadConfig();
  logLine('Scanner Settings page loaded.');
});
