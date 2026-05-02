/**
 * GmsReceiptPrint — client-side receipt print helper.
 *
 * Three public entry points:
 *
 *   GmsReceiptPrint.printFromUrl(opts)
 *     Classic iframe/popup print — opens a renderable receipt HTML page and
 *     calls window.print() inside it.  Used for HTML receipt templates.
 *
 *   GmsReceiptPrint.printThermal(opts)
 *     Thermal (ESC/POS) print — fetches receipt preview text from backend,
 *     shows a confirmation modal, then POSTs to the printer dispatch endpoint.
 *     Includes a duplicate-click guard (in-flight lock).
 *
 *   GmsReceiptPrint.previewOnly(opts)
 *     Like printThermal but shows the preview without sending to printer.
 *     Useful for inspect / debug flows.
 *
 * Options for printThermal / previewOnly:
 *   previewUrl   {string}  GET endpoint returning { ok, receipt_text, cpl, paper_size }
 *   printUrl     {string}  POST endpoint for dispatch (only used by printThermal)
 *   printPayload {object}  Extra fields merged into print POST body (e.g. { sale_id })
 *   button       {Element} Button to disable during operation
 *   busyText     {string}  Button label while busy (default 'Printing…')
 *   onSuccess    {fn}      Called with { msg, target } on success
 *   onError      {fn}      Called with error message string on failure
 *   modalTitle   {string}  Modal heading (default 'Receipt Preview')
 *   copies       {number}  Copies to print (default 1)
 */
(function initReceiptPrintHelper(windowObj) {
  if (!windowObj) return;

  // ── In-flight lock ──────────────────────────────────────────────────────────
  const _inflight = new Set();

  function _lockKey(url) {
    return url || 'default';
  }

  function _isLocked(url) {
    return _inflight.has(_lockKey(url));
  }

  function _lock(url) {
    _inflight.add(_lockKey(url));
  }

  function _unlock(url) {
    _inflight.delete(_lockKey(url));
  }

  // ── Button state helpers ────────────────────────────────────────────────────

  function _setBusy(button, busy, busyText) {
    if (!button) return;
    if (busy) {
      if (!button.dataset.originalLabel) {
        button.dataset.originalLabel = button.textContent || '';
      }
      button.disabled = true;
      if (busyText) button.textContent = busyText;
      return;
    }
    button.disabled = false;
    if (button.dataset.originalLabel) {
      button.textContent = button.dataset.originalLabel;
      delete button.dataset.originalLabel;
    }
  }

  // ── Modal helpers ───────────────────────────────────────────────────────────

  function _createModal(title, receiptText, cpl) {
    const overlay = document.createElement('div');
    overlay.id = 'gmsReceiptPreviewOverlay';
    overlay.style.cssText = [
      'position:fixed;inset:0;background:rgba(0,0,0,0.55);z-index:9999',
      'display:flex;align-items:center;justify-content:center',
      'font-family:sans-serif',
    ].join(';');

    const paperWidth = cpl && cpl <= 32 ? '280px' : '400px';

    overlay.innerHTML = `
      <div style="background:#fff;border-radius:6px;max-width:90vw;width:${paperWidth};
                  max-height:85vh;display:flex;flex-direction:column;box-shadow:0 8px 32px rgba(0,0,0,0.25)">
        <div style="padding:12px 16px;border-bottom:1px solid #e5e7eb;display:flex;
                    align-items:center;justify-content:space-between">
          <strong style="font-size:14px">${_escHtml(title)}</strong>
          <button id="gmsRcptClose" style="background:none;border:none;cursor:pointer;
                  font-size:18px;line-height:1;padding:2px 6px;color:#6b7280">&times;</button>
        </div>
        <div style="overflow-y:auto;flex:1;padding:12px 16px">
          <pre id="gmsRcptPreviewText" style="font-family:'Courier New',Courier,monospace;
               font-size:11px;white-space:pre;margin:0;line-height:1.45;
               background:#f9fafb;padding:8px;border-radius:4px;
               border:1px solid #e5e7eb;overflow-x:auto">${_escHtml(receiptText)}</pre>
        </div>
        <div style="padding:10px 16px;border-top:1px solid #e5e7eb;display:flex;
                    gap:8px;justify-content:flex-end">
          <button id="gmsRcptCancel"
                  style="padding:6px 16px;border:1px solid #d1d5db;border-radius:4px;
                         background:#f9fafb;cursor:pointer;font-size:13px">Cancel</button>
          <button id="gmsRcptConfirm"
                  style="padding:6px 18px;border:none;border-radius:4px;
                         background:#2563eb;color:#fff;cursor:pointer;font-size:13px;
                         font-weight:600">Print</button>
        </div>
      </div>`;

    document.body.appendChild(overlay);
    return overlay;
  }

  function _escHtml(str) {
    return (str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function _removeModal() {
    const el = document.getElementById('gmsReceiptPreviewOverlay');
    if (el) el.remove();
  }

  // ── Core: fetch preview + show modal + optionally dispatch ─────────────────

  async function _runThermalFlow(opts, dispatchAfterConfirm) {
    const previewUrl  = String(opts.previewUrl  || '').trim();
    const printUrl    = String(opts.printUrl    || '').trim();
    const printPayload = opts.printPayload || {};
    const button      = opts.button || null;
    const busyText    = opts.busyText || 'Printing…';
    const modalTitle  = opts.modalTitle || 'Receipt Preview';
    const copies      = Math.max(1, Math.min(Number(opts.copies) || 1, 5));
    const onSuccess   = typeof opts.onSuccess === 'function' ? opts.onSuccess : null;
    const onError     = typeof opts.onError   === 'function' ? opts.onError   : null;

    if (!previewUrl) {
      const msg = 'previewUrl is required for thermal print.';
      if (onError) onError(msg);
      throw new Error(msg);
    }

    // Duplicate-click guard
    if (_isLocked(previewUrl)) {
      return;
    }
    _lock(previewUrl);
    _setBusy(button, true, busyText);

    try {
      // 1. Fetch receipt preview text from backend
      const previewResp = await fetch(previewUrl, {
        method: 'GET',
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' },
      });
      if (!previewResp.ok) {
        throw new Error(`Preview fetch failed: HTTP ${previewResp.status}`);
      }
      const previewData = await previewResp.json();
      if (!previewData.ok) {
        throw new Error(previewData.msg || previewData.error || 'Preview generation failed');
      }

      const receiptText = previewData.receipt_text || '';
      const cpl         = previewData.cpl || 48;

      // 2. Show preview modal (if dispatching after confirm, show Print button)
      _setBusy(button, false);
      _removeModal();
      const modal = _createModal(modalTitle, receiptText, cpl);

      // 3. Resolve on user action
      await new Promise((resolve, reject) => {
        const closeBtn   = document.getElementById('gmsRcptClose');
        const cancelBtn  = document.getElementById('gmsRcptCancel');
        const confirmBtn = document.getElementById('gmsRcptConfirm');

        if (!dispatchAfterConfirm) {
          // preview-only: hide print button
          if (confirmBtn) confirmBtn.style.display = 'none';
        }

        function cleanup() {
          _removeModal();
        }

        if (closeBtn)  closeBtn.addEventListener('click',  () => { cleanup(); resolve('cancelled'); });
        if (cancelBtn) cancelBtn.addEventListener('click', () => { cleanup(); resolve('cancelled'); });

        if (dispatchAfterConfirm && confirmBtn) {
          confirmBtn.addEventListener('click', async () => {
            if (!printUrl) { cleanup(); resolve('no-print-url'); return; }

            confirmBtn.disabled = true;
            confirmBtn.textContent = 'Printing…';

            try {
              // 4. POST to printer dispatch endpoint
              const printResp = await fetch(printUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                  'Content-Type': 'application/json',
                  'Accept': 'application/json',
                },
                body: JSON.stringify({ ...printPayload, copies }),
              });
              const printData = await printResp.json().catch(() => ({}));
              cleanup();

              if (!printResp.ok || !printData.ok) {
                reject(new Error(printData.msg || printData.error || `Print failed: HTTP ${printResp.status}`));
              } else {
                if (onSuccess) onSuccess(printData);
                resolve('printed');
              }
            } catch (err) {
              cleanup();
              reject(err);
            }
          });
        }

        // Close on overlay click (outside modal card)
        modal.addEventListener('click', (e) => {
          if (e.target === modal) { cleanup(); resolve('cancelled'); }
        });
      });

    } catch (err) {
      _removeModal();
      const msg = err instanceof Error ? err.message : String(err);
      if (onError) onError(msg);
      throw err;
    } finally {
      _unlock(previewUrl);
      _setBusy(button, false);
    }
  }

  // ── Legacy: iframe / popup HTML receipt ────────────────────────────────────

  function _cleanupFrame(frameId, delayMs) {
    window.setTimeout(() => {
      const node = document.getElementById(frameId);
      if (node) node.remove();
    }, delayMs);
  }

  async function printFromUrl(opts) {
    const options       = opts || {};
    const receiptUrl    = String(options.url || '').trim();
    if (!receiptUrl) throw new Error('Receipt URL is required to start printing.');

    const button        = options.button || null;
    const busyText      = options.busyText || 'Preparing…';
    const frameId       = options.frameId || 'supermartReceiptPrintFrame';
    const loadTimeoutMs = Number(options.loadTimeoutMs  || 12000);
    const cleanupDelay  = Number(options.cleanupDelayMs || 60000);
    const preferIframe  = options.preferIframe !== false;
    const fallbackPopup = options.fallbackToPopup !== false;

    if (_isLocked(receiptUrl)) return;
    _lock(receiptUrl);
    _setBusy(button, true, busyText);
    if (typeof options.onStart === 'function') options.onStart();

    const existing = document.getElementById(frameId);
    if (existing) existing.remove();

    let didQueuePrint = false;
    try {
      if (preferIframe) {
        await new Promise((resolve, reject) => {
          const iframe = document.createElement('iframe');
          iframe.id = frameId;
          iframe.title = 'Receipt print frame';
          Object.assign(iframe.style, {
            position: 'fixed', width: '1px', height: '1px',
            right: '0', bottom: '0', opacity: '0', border: '0',
            pointerEvents: 'none',
          });

          let done = false;
          const finish = (fn) => { if (done) return; done = true; clearTimeout(timer); fn(); };
          const timer = setTimeout(
            () => finish(() => reject(new Error('Timed out loading printable receipt.'))),
            loadTimeoutMs,
          );

          iframe.onerror = () => finish(() => reject(new Error('Printable receipt failed to load.')));
          iframe.onload = () => {
            try {
              const fw = iframe.contentWindow;
              if (fw) { fw.focus(); fw.print(); }
              didQueuePrint = true;
              _cleanupFrame(frameId, cleanupDelay);
              finish(resolve);
            } catch (e) {
              finish(() => reject(e instanceof Error ? e : new Error('Unable to access print frame.')));
            }
          };

          document.body.appendChild(iframe);
          iframe.src = receiptUrl;
        });
      }

      if (!didQueuePrint && fallbackPopup) {
        const popup = window.open(receiptUrl, '_blank', 'noopener,noreferrer');
        if (!popup) throw new Error('Popup blocked while opening printable receipt.');
        didQueuePrint = true;
      }

      if (!didQueuePrint) throw new Error('Printing did not start.');
      if (typeof options.onQueued === 'function') options.onQueued();

    } catch (err) {
      if (typeof options.onError === 'function') {
        options.onError(err instanceof Error ? err.message : 'Printing failed to start.');
      }
      throw err;
    } finally {
      _unlock(receiptUrl);
      _setBusy(button, false);
    }
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  /**
   * printThermal(opts)
   * Fetch preview → show modal → on confirm → dispatch to printer API.
   * Duplicate-click safe.
   */
  async function printThermal(opts) {
    return _runThermalFlow(opts, true);
  }

  /**
   * previewOnly(opts)
   * Fetch preview → show modal without Print button.
   * Used for inspect / debug flows.
   */
  async function previewOnly(opts) {
    return _runThermalFlow(opts, false);
  }

  windowObj.GmsReceiptPrint = windowObj.SuperMartReceiptPrint = {
    printFromUrl,
    printThermal,
    previewOnly,
  };

})(window);
