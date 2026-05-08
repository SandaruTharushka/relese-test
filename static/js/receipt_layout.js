/* Receipt Layout settings + live preview. */
(() => {
  const INITIAL = window.__INITIAL_LAYOUT__ || {};
  const FIELDS = {
    text: ['rcpt_layout_template_style', 'rcpt_layout_paper_width', 'rcpt_layout_font_size',
           'rcpt_layout_font_family', 'rcpt_layout_header_alignment', 'rcpt_layout_footer_alignment',
           'rcpt_layout_item_row_style', 'rcpt_layout_divider_style'],
    bool: ['rcpt_layout_show_logo', 'rcpt_layout_show_company_name', 'rcpt_layout_show_address',
           'rcpt_layout_show_phone', 'rcpt_layout_show_email', 'rcpt_layout_show_customer',
           'rcpt_layout_show_vehicle', 'rcpt_layout_show_cashier', 'rcpt_layout_show_barcode',
           'rcpt_layout_show_footer', 'rcpt_layout_show_tax_info', 'rcpt_layout_show_payment_summary',
           'rcpt_layout_auto_cut'],
  };

  const KEY_TO_INPUT = {
    rcpt_layout_template_style: 'lay_template_style',
    rcpt_layout_paper_width: 'lay_paper_width',
    rcpt_layout_font_size: 'lay_font_size',
    rcpt_layout_font_family: 'lay_font_family',
    rcpt_layout_header_alignment: 'lay_header_alignment',
    rcpt_layout_footer_alignment: 'lay_footer_alignment',
    rcpt_layout_item_row_style: 'lay_item_row_style',
    rcpt_layout_divider_style: 'lay_divider_style',
    rcpt_layout_show_logo: 'lay_show_logo',
    rcpt_layout_show_company_name: 'lay_show_company_name',
    rcpt_layout_show_address: 'lay_show_address',
    rcpt_layout_show_phone: 'lay_show_phone',
    rcpt_layout_show_email: 'lay_show_email',
    rcpt_layout_show_customer: 'lay_show_customer',
    rcpt_layout_show_vehicle: 'lay_show_vehicle',
    rcpt_layout_show_cashier: 'lay_show_cashier',
    rcpt_layout_show_barcode: 'lay_show_barcode',
    rcpt_layout_show_footer: 'lay_show_footer',
    rcpt_layout_show_tax_info: 'lay_show_tax_info',
    rcpt_layout_show_payment_summary: 'lay_show_payment_summary',
    rcpt_layout_auto_cut: 'lay_auto_cut',
  };

  function csrf() { return document.querySelector('meta[name="csrf-token"]')?.content || ''; }

  function applyLayout(layout) {
    for (const key of FIELDS.text) {
      const el = document.getElementById(KEY_TO_INPUT[key]);
      if (el != null) el.value = layout[key] ?? '';
    }
    for (const key of FIELDS.bool) {
      const el = document.getElementById(KEY_TO_INPUT[key]);
      if (el != null) el.checked = !!layout[key];
    }
  }

  function readLayout() {
    const out = {};
    for (const key of FIELDS.text) {
      const el = document.getElementById(KEY_TO_INPUT[key]);
      if (el) out[key] = el.value;
    }
    for (const key of FIELDS.bool) {
      const el = document.getElementById(KEY_TO_INPUT[key]);
      if (el) out[key] = el.checked;
    }
    return out;
  }

  let _previewTimer = null;

  async function refreshPreview() {
    clearTimeout(_previewTimer);
    _previewTimer = setTimeout(async () => {
      const type = document.getElementById('previewType').value;
      const layout = readLayout();
      try {
        const res = await fetch('/api/settings/receipt-layout/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
          body: JSON.stringify({ type, layout }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.msg || 'Preview failed');
        const frame = document.getElementById('previewFrame');
        const doc = frame.contentDocument || frame.contentWindow.document;
        doc.open();
        doc.write(data.html);
        doc.close();
      } catch (e) {
        const frame = document.getElementById('previewFrame');
        const doc = frame.contentDocument || frame.contentWindow.document;
        doc.open();
        doc.write(`<pre style="color:#dc2626">Preview error: ${e.message}</pre>`);
        doc.close();
      }
    }, 200);
  }
  window.refreshPreview = refreshPreview;

  async function saveLayout() {
    const msg = document.getElementById('layoutMsg');
    msg.textContent = 'Saving…';
    try {
      const res = await fetch('/api/settings/receipt-layout/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
        body: JSON.stringify(readLayout()),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.msg || 'Save failed');
      msg.textContent = `Saved (${data.saved} fields). All receipt types now use the new layout.`;
      msg.style.color = '#16a34a';
    } catch (e) {
      msg.textContent = `Error: ${e.message}`;
      msg.style.color = '#dc2626';
    }
  }
  window.saveLayout = saveLayout;

  async function resetLayout() {
    if (!confirm('Reset receipt layout to defaults?')) return;
    const msg = document.getElementById('layoutMsg');
    msg.textContent = 'Resetting…';
    try {
      const res = await fetch('/api/settings/receipt-layout/reset', {
        method: 'POST',
        headers: { 'X-CSRFToken': csrf() },
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.msg || 'Reset failed');
      applyLayout(data.layout);
      msg.textContent = 'Reset to defaults.';
      msg.style.color = '#16a34a';
      refreshPreview();
    } catch (e) {
      msg.textContent = `Error: ${e.message}`;
      msg.style.color = '#dc2626';
    }
  }
  window.resetLayout = resetLayout;

  // wire change events for live preview
  document.querySelectorAll('input,select').forEach(el => {
    el.addEventListener('change', refreshPreview);
    el.addEventListener('input', refreshPreview);
  });
  document.getElementById('previewType').addEventListener('change', refreshPreview);

  applyLayout(INITIAL);
  refreshPreview();
})();
