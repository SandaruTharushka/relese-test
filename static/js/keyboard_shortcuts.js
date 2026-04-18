(function initPosKeyboardShortcuts() {
  const cfg = window.posShortcutsConfig || {};

  function isTypingTarget(target) {
    if (!target) return false;
    if (target.isContentEditable) return true;
    const tag = target.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
  }

  function getVisibleModal() {
    return document.querySelector('.modal-overlay.show');
  }

  function openHelpPanel() {
    if (typeof window.openModal === 'function') {
      window.openModal('shortcutHelpModal');
    }
  }

  function selectPayment(method, buttonId) {
    const payOpen = document.getElementById('payModal')?.classList.contains('show');
    if (!payOpen) {
      if (typeof window.openPaymentOrImeiModal === 'function') window.openPaymentOrImeiModal();
    }
    if (typeof window.selectPayMethod === 'function') window.selectPayMethod(method);
    if (cfg.flashElement) cfg.flashElement(document.getElementById(buttonId));
  }

  function saveCurrentContext() {
    const visibleModal = getVisibleModal();
    if (visibleModal) {
      const saveBtn = visibleModal.querySelector('#saveMissingBtn, #btnSaveOnlineProduct, #confirmPayBtn, .btn-primary, .btn-success');
      if (saveBtn && !saveBtn.disabled) {
        saveBtn.click();
        return;
      }
    }
    const activeForm = document.activeElement?.closest('form');
    if (activeForm) {
      activeForm.requestSubmit?.();
      return;
    }
    window.toast?.('Nothing to save in current context', 'warning');
  }

  function tryCompleteSaleFromEnter(activeEl) {
    if (activeEl?.id === 'searchInput') {
      return window.addFirstFilteredProductToCart?.();
    }

    const payModalOpen = document.getElementById('payModal')?.classList.contains('show');
    if (payModalOpen) {
      window.confirmPayment?.();
      return true;
    }

    return false;
  }

  function changeSelectedQty(delta) {
    const cartLength = Number(window.getCartLength?.() || 0);
    if (!cartLength) return;
    const idx = Number(window.getSelectedCartIndex?.());
    if (!Number.isInteger(idx) || idx < 0 || idx >= cartLength) {
      window.selectCartItem?.(cartLength - 1);
      return;
    }
    window.updateQty?.(idx, delta);
  }

  function removeSelectedItem() {
    const cartLength = Number(window.getCartLength?.() || 0);
    if (!cartLength) return;
    const idx = Number(window.getSelectedCartIndex?.());
    if (!Number.isInteger(idx) || idx < 0 || idx >= cartLength) {
      window.toast?.('Select a cart item first', 'warning');
      return;
    }
    window.removeFromCart?.(idx, { requireConfirmation: false });
  }

  function navigateTo(path) {
    if (!path || window.location.pathname === path) return;
    window.location.href = path;
  }

  function normalizeShortcut(event) {
    const key = event.key;
    const ctrl = event.ctrlKey || event.metaKey;

    if (ctrl) {
      const lower = String(key).toLowerCase();
      return `Ctrl+${lower}`;
    }

    if (event.code === 'NumpadAdd' || key === '+' || (key === '=' && event.shiftKey)) return '+';
    if (event.code === 'NumpadSubtract' || key === '-') return '-';
    return key;
  }

  function handleKeyboardShortcut(event) {
    const activeEl = document.activeElement;
    const shortcut = normalizeShortcut(event);
    const typing = isTypingTarget(activeEl);

    const alwaysAllowed = new Set(['Escape', 'F1', 'F2', 'F3', 'Enter']);
    if (typing && !alwaysAllowed.has(shortcut)) return;

    const handlers = {
      F1: () => { window.newSale?.(); window.toast?.('New sale started', 'info'); },
      F2: () => { document.getElementById('searchInput')?.focus(); document.getElementById('searchInput')?.select?.(); },
      F3: () => { document.getElementById('wsSearchInput')?.focus(); document.getElementById('wsSearchInput')?.select?.(); },
      Enter: () => {
        if (activeEl?.id === 'barcodeInput') return;
        if (tryCompleteSaleFromEnter(activeEl)) event.preventDefault();
      },
      Escape: () => {
        if (cfg.closeVisibleModals) cfg.closeVisibleModals();
      },
      'Ctrl+c': () => selectPayment('Cash', 'payMethodCash'),
      'Ctrl+k': () => selectPayment('Card', 'payMethodCard'),
      'Ctrl+m': () => selectPayment('StoreCredit', null),
      Delete: () => removeSelectedItem(),
      '+': () => changeSelectedQty(1),
      '-': () => changeSelectedQty(-1),
      'Ctrl+d': () => { document.getElementById('discountInput')?.focus(); document.getElementById('discountInput')?.select?.(); },
      'Ctrl+q': () => window.manualEditSelectedQty?.(),
      'Ctrl+b': () => navigateTo(cfg.routes?.billing),
      'Ctrl+p': () => navigateTo(cfg.routes?.products),
      'Ctrl+u': () => navigateTo(cfg.routes?.customers),
      'Ctrl+s': () => saveCurrentContext(),
      'Ctrl+h': () => openHelpPanel(),
    };

    const fn = handlers[shortcut];
    if (!fn) return;
    event.preventDefault();
    fn();
  }

  window.handleKeyboardShortcut = handleKeyboardShortcut;
  document.addEventListener('keydown', handleKeyboardShortcut);
})();
