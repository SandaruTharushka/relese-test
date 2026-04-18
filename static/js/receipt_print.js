(function initReceiptPrintHelper(windowObj) {
  if (!windowObj) return;

  function setButtonBusy(button, busy, busyText) {
    if (!button) return;
    if (busy) {
      if (!button.dataset.originalLabel) button.dataset.originalLabel = button.textContent || '';
      button.disabled = true;
      if (busyText) button.textContent = busyText;
      return;
    }
    button.disabled = false;
    if (button.dataset.originalLabel) {
      button.textContent = button.dataset.originalLabel;
    }
  }

  function cleanupFrame(frameId, delayMs) {
    window.setTimeout(() => {
      const node = document.getElementById(frameId);
      if (node) node.remove();
    }, delayMs);
  }

  async function printFromUrl(opts) {
    const options = opts || {};
    const receiptUrl = String(options.url || '').trim();
    if (!receiptUrl) {
      throw new Error('Receipt URL is required to start printing.');
    }

    const button = options.button || null;
    const busyText = options.busyText || 'Preparing…';
    const frameId = options.frameId || 'supermartReceiptPrintFrame';
    const loadTimeoutMs = Number(options.loadTimeoutMs || 12000);
    const cleanupDelayMs = Number(options.cleanupDelayMs || 60000);
    const preferIframe = options.preferIframe !== false;
    const fallbackToPopup = options.fallbackToPopup !== false;

    setButtonBusy(button, true, busyText);
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
          iframe.style.position = 'fixed';
          iframe.style.width = '1px';
          iframe.style.height = '1px';
          iframe.style.right = '0';
          iframe.style.bottom = '0';
          iframe.style.opacity = '0';
          iframe.style.border = '0';
          iframe.style.pointerEvents = 'none';

          let done = false;
          const finish = (fn) => {
            if (done) return;
            done = true;
            window.clearTimeout(timer);
            fn();
          };

          const timer = window.setTimeout(() => {
            finish(() => reject(new Error('Timed out while loading printable receipt.')));
          }, loadTimeoutMs);

          iframe.onerror = () => finish(() => reject(new Error('Printable receipt failed to load.')));
          iframe.onload = () => {
            try {
              const frameWindow = iframe.contentWindow;
              if (frameWindow) {
                frameWindow.focus();
                frameWindow.print();
              }
              didQueuePrint = true;
              cleanupFrame(frameId, cleanupDelayMs);
              finish(resolve);
            } catch (err) {
              finish(() => reject(err instanceof Error ? err : new Error('Unable to access print frame.')));
            }
          };

          document.body.appendChild(iframe);
          iframe.src = receiptUrl;
        });
      }

      if (!didQueuePrint && fallbackToPopup) {
        const popup = window.open(receiptUrl, '_blank', 'noopener,noreferrer');
        if (!popup) {
          throw new Error('Popup blocked while opening printable receipt.');
        }
        didQueuePrint = true;
      }

      if (!didQueuePrint) {
        throw new Error('Printing did not start.');
      }

      if (typeof options.onQueued === 'function') options.onQueued();
    } catch (error) {
      if (typeof options.onError === 'function') {
        options.onError(error instanceof Error ? error.message : 'Printing failed to start.');
      }
      throw error;
    } finally {
      setButtonBusy(button, false);
    }
  }

  windowObj.SuperMartReceiptPrint = {
    printFromUrl,
  };
})(window);
