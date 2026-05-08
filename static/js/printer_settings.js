/* Printer Settings page logic. */
(() => {
  const SAVED = window.__SAVED_PRINTER_SETTINGS__ || {};

  function csrf() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
  }

  function statusBadges(p) {
    const s = (p?.status || 'unknown').toLowerCase();
    const badges = [];

    // Main status badge
    const statusMap = {
      online: 'badge-online',
      offline: 'badge-offline',
      paused: 'badge-paused',
      error: 'badge-error',
      unknown: 'badge-unknown',
    };
    const cls = statusMap[s] || 'badge-unknown';
    badges.push(`<span class="badge-status ${cls}">${s.toUpperCase()}</span>`);

    // Additional status badges
    if (p.driver_installed && !p.connected) {
      badges.push(`<span class="badge-status badge-disconnected">DISCONNECTED</span>`);
    }
    if (p.driver_installed && !p.connected && s !== 'offline') {
      badges.push(`<span class="badge-status badge-driver-only">DRIVER ONLY</span>`);
    }
    if (p.status === 'error') {
      badges.push(`<span class="badge-status badge-error">ERROR</span>`);
    }

    return badges.join('');
  }

  async function refreshPrinters() {
    const list = document.getElementById('printerList');
    const warning = document.getElementById('printerWarning');
    list.innerHTML = 'Loading…';
    try {
      const res = await fetch('/api/settings/printers/list', { credentials: 'same-origin' });
      const data = await res.json();
      const printers = data.printers || [];

      // Show warning if no online printers
      const onlinePrinters = printers.filter(p => p.status === 'online');
      if (data.warning || onlinePrinters.length === 0) {
        warning.innerHTML = '⚠️ ' + (data.warning || 'No connected receipt printer detected. Please verify printer connection.');
        warning.style.display = 'block';
      } else {
        warning.style.display = 'none';
      }

      if (printers.length === 0) {
        list.innerHTML = '<div class="text-muted">No printers detected. Connect a printer or check Windows drivers.</div>';
      } else {
        list.innerHTML = printers.map(p => `
          <div class="printer-row">
            <div class="printer-name">${p.name}</div>
            <div class="printer-badges">${statusBadges(p)}</div>
            ${p.message ? `<div class="printer-message">${p.message}</div>` : ''}
            ${p.port_name ? `<div class="printer-details">Port: ${p.port_name}</div>` : ''}
          </div>
        `).join('');
      }

      const recv = document.getElementById('receiptPrinter');
      const lab = document.getElementById('labelPrinter');
      [recv, lab].forEach(sel => {
        const current = sel.value;
        sel.innerHTML = '<option value="">— Select a printer —</option>'
          + printers.map(p => `<option value="${p.name}">${p.name}</option>`).join('');
        sel.value = current || '';
      });
      // re-apply saved selection if not yet set
      if (!recv.value && SAVED.printer_receipt_name) recv.value = SAVED.printer_receipt_name;
      if (!lab.value && SAVED.printer_label_name) lab.value = SAVED.printer_label_name;
      await refreshStatus();
    } catch (e) {
      list.innerHTML = `<div style="color:#dc2626">Failed to load printers: ${e.message}</div>`;
    }
  }
  window.refreshPrinters = refreshPrinters;

  async function refreshStatus() {
    const sel = document.getElementById('receiptPrinter');
    const out = document.getElementById('receiptStatus');
    if (!sel.value) {
      out.innerHTML = '<span class="text-muted">No printer selected</span>';
      return;
    }
    out.innerHTML = 'Checking…';
    try {
      const res = await fetch('/api/settings/printers/status?name=' + encodeURIComponent(sel.value));
      const data = await res.json();
      const s = data.status || {};
      const badges = statusBadges(s);
      const msg = s.message || '';
      const port = s.port_name ? ` <span class="text-muted text-sm">(Port: ${s.port_name})</span>` : '';
      out.innerHTML = `<div style="margin-top:6px">${badges}${port}${msg ? ` <div class="text-muted text-sm" style="margin-top:4px">${msg}</div>` : ''}</div>`;
    } catch (e) {
      out.innerHTML = `<span style="color:#dc2626">Status check failed: ${e.message}</span>`;
    }
  }
  window.refreshStatus = refreshStatus;

  async function savePrinterSettings() {
    const msg = document.getElementById('saveMsg');
    msg.textContent = 'Saving…';
    msg.style.color = '';
    const payload = {
      printer_receipt_name: document.getElementById('receiptPrinter').value,
      printer_label_name: document.getElementById('labelPrinter').value,
      printer_default_paper_width: document.getElementById('paperWidth').value,
      printer_connection_type: document.getElementById('connectionType').value,
      printer_print_mode: document.getElementById('printMode').value,
      printer_auto_detect_enabled: document.getElementById('autoDetect').checked,
      printer_is_enabled: document.getElementById('printingEnabled').checked,
    };
    try {
      const res = await fetch('/api/settings/printers/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.msg || 'Save failed');
      msg.textContent = `Saved (${data.saved} settings).`;
      msg.style.color = '#16a34a';
    } catch (e) {
      msg.textContent = `Error: ${e.message}`;
      msg.style.color = '#dc2626';
    }
  }
  window.savePrinterSettings = savePrinterSettings;

  async function testPrint(kind) {
    const msg = document.getElementById('saveMsg');
    msg.textContent = `Sending ${kind} test…`;
    msg.style.color = '';
    try {
      const res = await fetch('/api/settings/printers/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
        body: JSON.stringify({ kind }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.msg || 'Test failed');
      msg.textContent = data.msg || 'Test sent.';
      msg.style.color = '#16a34a';
    } catch (e) {
      msg.textContent = `Test failed: ${e.message}`;
      msg.style.color = '#dc2626';
    }
  }
  window.testPrint = testPrint;

  async function cutPaper() {
    const msg = document.getElementById('saveMsg');
    msg.textContent = 'Sending cut command…';
    msg.style.color = '';
    try {
      const res = await fetch('/api/settings/printers/cut', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.msg || 'Cut failed');
      msg.textContent = 'Cut command sent successfully.';
      msg.style.color = '#16a34a';
    } catch (e) {
      msg.textContent = `Cut command failed: ${e.message}`;
      msg.style.color = '#dc2626';
    }
  }
  window.cutPaper = cutPaper;

  function applySaved() {
    document.getElementById('paperWidth').value = SAVED.printer_default_paper_width || '80mm';
    document.getElementById('connectionType').value = SAVED.printer_connection_type || 'windows';
    document.getElementById('printMode').value = SAVED.printer_print_mode || 'windows_raw';
    document.getElementById('autoDetect').checked = SAVED.printer_auto_detect_enabled !== false;
    document.getElementById('printingEnabled').checked = SAVED.printer_is_enabled !== false;
  }

  document.getElementById('receiptPrinter').addEventListener('change', refreshStatus);
  applySaved();
  refreshPrinters();
})();
