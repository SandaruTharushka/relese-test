function showResetAlert(type, msg) {
  const el = document.getElementById('resetAlert');
  el.className = 'alert alert-' + (type === 'error' ? 'error' : 'success');
  el.textContent = msg;
  el.style.display = 'flex';
}

function goResetStep(step) {
  document.getElementById('step-verify').classList.toggle('show', step === 'verify');
  document.getElementById('step-password').classList.toggle('show', step === 'password');
}

async function verifyIdentity() {
  const email = (document.getElementById('reset_email').value || '').trim().toLowerCase();
  const username = (document.getElementById('reset_username').value || '').trim();

  if (!email || !username) {
    showResetAlert('error', 'Please fill in all required fields');
    return;
  }

  const btn = document.getElementById('verifyIdentityBtn');
  btn.disabled = true;
  btn.textContent = '⏳ Checking...';

  try {
    const res = await fetch('/forgot-password', {
      method: 'POST',
      headers: withCsrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ email, username }),
    });
    const data = await res.json();
    if (!data.ok) {
      showResetAlert('error', data.msg || 'Unable to verify account');
      return;
    }
    showResetAlert('success', 'Identity verified. Enter your new password.');
    goResetStep('password');
  } catch (_) {
    showResetAlert('error', 'Unable to reach the server. Please try again.');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Continue';
  }
}

async function savePassword() {
  const password = document.getElementById('new_password').value || '';
  const confirmPassword = document.getElementById('confirm_password').value || '';

  if (!password || !confirmPassword) {
    showResetAlert('error', 'Please fill in all required fields');
    return;
  }

  const btn = document.getElementById('savePasswordBtn');
  btn.disabled = true;
  btn.textContent = '⏳ Saving...';

  try {
    const res = await fetch('/reset-password', {
      method: 'POST',
      headers: withCsrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ password, confirm_password: confirmPassword }),
    });
    const data = await res.json();
    if (!data.ok) {
      showResetAlert('error', data.msg || 'Unable to change password');
      return;
    }
    showResetAlert('success', data.msg || 'Password changed successfully');
    setTimeout(() => { window.location.href = '/login'; }, 1200);
  } catch (_) {
    showResetAlert('error', 'Unable to reach the server. Please try again.');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save New Password';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('verifyIdentityBtn').addEventListener('click', verifyIdentity);
  document.getElementById('savePasswordBtn').addEventListener('click', savePassword);
  document.getElementById('reset_username').addEventListener('keydown', (e) => { if (e.key === 'Enter') verifyIdentity(); });
  document.getElementById('confirm_password').addEventListener('keydown', (e) => { if (e.key === 'Enter') savePassword(); });
});
