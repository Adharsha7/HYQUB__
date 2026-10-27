/** HYQUB UI controller. textContent only, no wallet addresses, token kept in api.js memory. */
import * as api from './api.js';
import { parseEthToWei, weiToEthString } from './wei.js';

const DAILY_LIMIT = 1;

const $ = (id) => document.getElementById(id);
const SCREENS = ['auth', 'home', 'pay', 'receive', 'status'];
let signup = false, user = null, to = null, amount = '', timer = null, hidden = false, balance = '0', idemKey = null;
let currentFilter = 'all', currentHistory = [], lastReceipt = null, keypadAmount = '', currentTheme = 'dark';
let activeSheetHash = '', chartRendered = false;
let sessionInterval = null, sessionRemaining = 1800; // 30 minutes

const show = (n) => {
  SCREENS.forEach((s) => { const el = $(`screen-${s}`); if (el) el.hidden = s !== n; });
  updateNav(n);
  document.body.classList.toggle('auth-screen-active', n === 'auth');
  // Show/hide header user controls based on auth state
  const headerUser = $('header-user');
  const btnLogout = $('btn-logout');
  if (headerUser) headerUser.hidden = !user || n === 'auth';
  if (btnLogout) btnLogout.hidden = !user || n === 'auth';
};
const txt = (id, t) => { const e = $(id); if (e) e.textContent = t; };
const clear = (el) => { while (el && el.firstChild) el.removeChild(el.firstChild); };
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function toast(msg) {
  const t = $('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('on');
  setTimeout(() => t.classList.remove('on'), 2200);
}

function triggerShake(node) {
  if (!node) return;
  node.classList.remove('shake');
  void node.offsetWidth;
  node.classList.add('shake');
  setTimeout(() => node.classList.remove('shake'), 400);
}

// ---------- Network Reachability Banner ----------
function showNetworkBanner(msg) {
  const b = $('network-banner');
  if (!b) return;
  const t = $('network-banner-text');
  if (t && msg) t.textContent = msg;
  b.hidden = false;
}
function hideNetworkBanner() {
  const b = $('network-banner');
  if (b) b.hidden = true;
}

// ---------- Friendly Error Mapping ----------
function friendlyError(err) {
  if (!err) return 'Something went wrong. Please try again.';
  if (err.code === 'NETWORK' || err.status === 0) {
    return 'Cannot reach the server. Check that the backend is running.';
  }
  if (err.code === 'WRONG_PASSWORD') {
    return 'Wrong password. Nothing was sent.';
  }
  if (err.status === 401) {
    return 'Your session has expired. Please log in again.';
  }
  if (err.code === 'INSUFFICIENT_BALANCE') {
    return 'Your balance is too low for this payment.';
  }
  if (err.code === 'LIMIT_EXCEEDED') {
    return err.message || 'Payment exceeds daily or per-transaction limit.';
  }
  if (err.code === 'RATE_LIMITED' || err.status === 429) {
    return 'Too many attempts. Please wait a minute and try again.';
  }
  if (err.code === 'SELF_PAYMENT') {
    return 'You cannot send money to yourself.';
  }
  if (err.code === 'RECIPIENT_NOT_FOUND') {
    return 'Recipient was not found.';
  }
  if (err.code === 'INVALID_AMOUNT') {
    return err.message || 'Enter a valid positive amount with up to 18 decimals.';
  }
  if (err.status === 503) {
    return 'Payment service is temporarily unavailable. Please try again shortly.';
  }
  return err.message || 'Payment failed. Nothing was sent. Please try again.';
}

// Theme-derived avatar colors using existing CSS variables
const AVATAR_PALETTE = [
  '#8b9cff', '#6677f0', '#4fd69c', '#ffd166', '#ff7a7a',
  '#c4ceff', '#9bb8ff', '#7ae8c1', '#ffb347', '#e88bff'
];

function paintAvatar(node, name) {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) & 0x7fffffff;
  const color = AVATAR_PALETTE[h % AVATAR_PALETTE.length];
  node.style.background = color;
  node.textContent = (name[0] || '?').toUpperCase();
}
function avatar(name, lg) {
  const a = el('div', lg ? 'av lg' : 'av');
  paintAvatar(a, name);
  return a;
}
function avatarSmall(name) {
  const a = el('div', 'av');
  a.style.width = '24px';
  a.style.height = '24px';
  a.style.fontSize = '10px';
  paintAvatar(a, name);
  return a;
}

// ---------- Greeting ----------
function getGreeting() {
  const h = new Date().getHours();
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  return 'Good evening';
}

// ---------- Relative Time ----------
function relativeTime(ts) {
  const now = Date.now();
  const then = Number(ts) * 1000;
  const diff = Math.max(0, now - then);
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return 'Just now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days === 1) return 'Yesterday';
  if (days < 7) return `${days}d ago`;
  return new Date(then).toLocaleDateString([], { dateStyle: 'medium' });
}

// ---------- Session Countdown (Part D) ----------
function updateSessionDisplay() {
  const timerEl = $('session-timer');
  if (!timerEl) return;
  const m = Math.floor(sessionRemaining / 60);
  const s = sessionRemaining % 60;
  const mm = m.toString().padStart(2, '0');
  const ss = s.toString().padStart(2, '0');
  timerEl.textContent = `Session ends in ${mm}:${ss}`;
  timerEl.classList.toggle('warn', sessionRemaining < 300);
}

function startSessionTimer() {
  clearInterval(sessionInterval);
  sessionRemaining = 1800;
  const timerEl = $('session-timer');
  if (timerEl) {
    timerEl.hidden = false;
    timerEl.classList.remove('warn');
  }
  updateSessionDisplay();
  sessionInterval = setInterval(() => {
    sessionRemaining--;
    if (sessionRemaining <= 0) {
      clearInterval(sessionInterval);
      sessionInterval = null;
      if (timerEl) timerEl.hidden = true;
      user = null;
      setMode(false);
      show('auth');
      txt('auth-error', 'Your session expired. Log in again.');
      return;
    }
    updateSessionDisplay();
  }, 1000);
}

function clearSessionTimer() {
  clearInterval(sessionInterval);
  sessionInterval = null;
  const timerEl = $('session-timer');
  if (timerEl) timerEl.hidden = true;
}

// ---------- Theme Toggle (In-Memory Only) ----------
const toggleTheme = () => {
  currentTheme = currentTheme === 'dark' ? 'light' : 'dark';
  const icon = currentTheme === 'light' ? '☾' : '☼';
  if (currentTheme === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
  } else {
    document.documentElement.removeAttribute('data-theme');
  }
  txt('btn-theme', icon);
  txt('btn-theme-auth', icon);
};
if ($('btn-theme')) $('btn-theme').onclick = toggleTheme;
if ($('btn-theme-auth')) $('btn-theme-auth').onclick = toggleTheme;

// ---------- Bottom Navigation ----------
function updateNav(cur) {
  const nav = $('bottom-nav');
  if (!nav) return;
  if (!user || cur === 'auth' || cur === 'status') {
    nav.hidden = true;
    return;
  }
  nav.hidden = false;
  if ($('nav-home')) $('nav-home').classList.toggle('on', cur === 'home');
  if ($('nav-pay')) $('nav-pay').classList.toggle('on', cur === 'pay');
  if ($('nav-receive')) $('nav-receive').classList.toggle('on', cur === 'receive');
}
if ($('nav-home')) $('nav-home').onclick = () => goHome();
if ($('nav-pay')) $('nav-pay').onclick = () => openPay();
if ($('nav-receive')) $('nav-receive').onclick = () => openReceive();

// ---------- Auth ----------
function setMode(s) {
  signup = s;
  txt('auth-submit', s ? 'Create your account' : 'Log in');
  txt('auth-error', '');
  const pw = $('auth-password');
  if (pw) pw.autocomplete = s ? 'new-password' : 'current-password';
  txt('auth-switch-text', s ? 'Already have an account? ' : "Don't have an account? ");
  txt('auth-switch-btn', s ? 'Log in' : 'Sign up');
  txt('auth-title', s ? 'Create your account' : 'Welcome back');
  txt('auth-subtitle', s ? 'Set up your quantum-resistant wallet' : 'Sign in to continue to your wallet');
  const pwStr = $('pw-strength');
  if (pwStr) pwStr.hidden = !s || !pw || !pw.value;
}
const authSwitchBtn = $('auth-switch-btn');
if (authSwitchBtn) authSwitchBtn.onclick = () => setMode(!signup);

// Show/Hide password toggle
const btnTogglePw = $('btn-toggle-pw');
if (btnTogglePw) {
  btnTogglePw.onclick = () => {
    const pw = $('auth-password');
    if (!pw) return;
    const isPw = pw.type === 'password';
    pw.type = isPw ? 'text' : 'password';
    btnTogglePw.setAttribute('aria-pressed', isPw ? 'true' : 'false');
    btnTogglePw.setAttribute('aria-label', isPw ? 'Hide password' : 'Show password');
    const showIcon = btnTogglePw.querySelector('.eye-show');
    const hideIcon = btnTogglePw.querySelector('.eye-hide');
    if (showIcon && hideIcon) {
      showIcon.hidden = isPw;
      hideIcon.hidden = !isPw;
    }
  };
}

// Caps Lock Warning & Informational Password Strength
const authPwInput = $('auth-password');
const capsWarning = $('caps-warning');
const pwStrengthBox = $('pw-strength');
const pwStrengthBar = $('pw-strength-bar');
const pwStrengthLabel = $('pw-strength-label');

if (authPwInput) {
  const checkCaps = (e) => {
    if (capsWarning && e.getModifierState) {
      capsWarning.hidden = !e.getModifierState('CapsLock');
    }
  };
  authPwInput.addEventListener('keyup', checkCaps);
  authPwInput.addEventListener('keydown', checkCaps);
  authPwInput.addEventListener('blur', () => {
    if (capsWarning) capsWarning.hidden = true;
  });

  authPwInput.addEventListener('input', () => {
    if (!pwStrengthBox || !pwStrengthBar) return;
    if (!signup || !authPwInput.value) {
      pwStrengthBox.hidden = true;
      return;
    }
    pwStrengthBox.hidden = false;
    const v = authPwInput.value;
    let score = 0;
    if (v.length >= 8) score++;
    if (v.length >= 12) score++;
    if (/[0-9]/.test(v)) score++;
    if (/[^A-Za-z0-9]/.test(v)) score++;
    const levels = [
      { w: '25%', c: '#ff7a7a', t: 'Weak' },
      { w: '50%', c: '#ffd166', t: 'Fair' },
      { w: '75%', c: '#6677f0', t: 'Good' },
      { w: '100%', c: '#4fd69c', t: 'Strong' }
    ];
    const lvl = levels[Math.max(0, Math.min(score, 3))];
    pwStrengthBar.style.width = lvl.w;
    pwStrengthBar.style.backgroundColor = lvl.c;
    if (pwStrengthLabel) pwStrengthLabel.textContent = `Strength: ${lvl.t}`;
  });
}

// Observe error element to toggle error styling
const authErrBox = $('auth-error');
const authCardBox = $('auth-card');
if (authErrBox && authCardBox && window.MutationObserver) {
  const observer = new MutationObserver(() => {
    const hasErr = Boolean(authErrBox.textContent && authErrBox.textContent.trim());
    authCardBox.classList.toggle('has-error', hasErr);
  });
  observer.observe(authErrBox, { childList: true, characterData: true, subtree: true });
}

// Subtle desktop parallax on shield
const shieldInteractive = document.getElementById('art-interactive-shield');
if (authCardBox && shieldInteractive) {
  let pRaf = null;
  authCardBox.addEventListener('mousemove', (e) => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches ||
        window.matchMedia('(pointer: coarse)').matches) return;
    if (pRaf) cancelAnimationFrame(pRaf);
    pRaf = requestAnimationFrame(() => {
      const rect = authCardBox.getBoundingClientRect();
      const nx = (e.clientX - (rect.left + rect.width / 2)) / rect.width;
      const ny = (e.clientY - (rect.top + rect.height / 2)) / rect.height;
      shieldInteractive.style.transform = `translate(255px, 145px) translate(${nx * 10}px, ${ny * 8}px)`;
    });
  });
  authCardBox.addEventListener('mouseleave', () => {
    shieldInteractive.style.transform = 'translate(255px, 145px)';
  });
}

$('auth-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const u = $('auth-username').value.trim(), p = $('auth-password').value;
  txt('auth-error', '');
  if (signup && p.length < 8) {
    triggerShake($('auth-card'));
    return txt('auth-error', 'Use at least 8 characters for your password.');
  }
  const btn = $('auth-submit');
  btn.disabled = true;
  $('auth-hint').hidden = false;
  try {
    const d = signup ? await api.signup(u, p) : await api.login(u, p);
    hideNetworkBanner();
    user = { id: d.user_id, name: d.name };
    $('auth-password').value = '';
    startSessionTimer();
    await goHome();
  } catch (err) {
    if (err.code === 'NETWORK' || err.status === 0) showNetworkBanner();
    triggerShake($('auth-card'));
    txt('auth-error', err.status === 409 ? 'That username is taken. Try another one.' : friendlyError(err));
  } finally {
    btn.disabled = false;
    $('auth-hint').hidden = true;
  }
});

api.setUnauthorizedHandler(() => {
  clearSessionTimer();
  user = null;
  setMode(false);
  show('auth');
  txt('auth-error', 'Your session expired. Log in again.');
});
$('btn-logout').onclick = async () => {
  clearSessionTimer();
  await api.logout();
  user = null;
  setMode(false);
  show('auth');
};

// ---------- Home: Balance ----------
const when = (ts) => new Date(ts * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });

function paintBalance() {
  txt('home-balance', hidden ? '••••' : balance);
  txt('btn-eye', hidden ? 'Show' : 'Hide');
}
$('btn-eye').onclick = () => {
  hidden = !hidden;
  paintBalance();
};

function animateBalanceCountUp(targetStr) {
  if (hidden || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    paintBalance();
    return;
  }
  const target = Number(targetStr);
  if (isNaN(target) || target === 0) {
    paintBalance();
    return;
  }
  const duration = 450;
  const start = performance.now();
  function stepAnim(now) {
    const elapsed = now - start;
    const progress = Math.min(1, elapsed / duration);
    const curr = target * progress;
    txt('home-balance', curr.toFixed(4));
    if (progress < 1) {
      requestAnimationFrame(stepAnim);
    } else {
      paintBalance();
    }
  }
  requestAnimationFrame(stepAnim);
}

// ---------- Security Card (Removed) ----------
if ($('security-text')) txt('security-text', '');
if ($('btn-toggle-security')) {
  $('btn-toggle-security').onclick = () => {
    const sc = $('card-security');
    if (sc) sc.hidden = !sc.hidden;
  };
}

// ---------- Wallet Address ----------
if ($('btn-copy-addr')) {
  $('btn-copy-addr').onclick = async () => {
    const addr = $('home-wallet-addr');
    if (!addr || !addr.dataset.full) return;
    try {
      await navigator.clipboard.writeText(addr.dataset.full);
      toast('Copied');
    } catch {
      toast('Clipboard unavailable');
    }
  };
}

// ---------- Activity Bottom Sheet ----------
function openSheet(h) {
  activeSheetHash = h.tx_hash || '';
  const sent = h.direction === 'sent';
  const name = h.counterparty ?? 'Unknown';
  txt('sheet-title', `${sent ? 'Sent' : 'Received'} ${h.amount_eth} ETH`);

  const st = $('sheet-status');
  st.className = `chip-status ${h.status === 'success' ? 'ok' : h.status === 'pending' ? 'pending' : 'bad'}`;
  st.textContent = h.status === 'success' ? 'Success' : h.status === 'pending' ? 'Pending' : 'Failed';

  txt('sheet-party-label', sent ? 'To' : 'From');
  txt('sheet-party', name);
  txt('sheet-time', when(h.created_at));
  txt('sheet-hash', activeSheetHash || 'Not available');

  // "How this payment was verified"
  // Only shows stages with real backend evidence.
  // Backend records: status ('success'|'failed'|'pending'), tx_hash (string or '').
  // No verifier counts, no intermediate approval steps — those are not exposed by the backend.
  const list = $('sheet-verify-list');
  if (list) {
    clear(list);

    const isSent = h.direction === 'sent';
    const txStatus = h.status; // 'success' | 'failed' | 'pending'
    const hasTxHash = Boolean(h.tx_hash && h.tx_hash.trim());

    // Stage 1: ML-DSA-65 signing — only applies to sent transactions.
    // The backend always signs before submission; if we have a record at all, signing ran.
    if (isSent) {
      let sigState, sigMark, sigLabel;
      if (txStatus === 'failed' && !hasTxHash) {
        // Failed before reaching the chain — signing may or may not have completed.
        sigState = 'pending';
        sigMark = '?';
        sigLabel = 'ML-DSA-65 signing — outcome unknown (payment failed before chain)';
      } else {
        sigState = 'done';
        sigMark = '✓';
        sigLabel = 'Signed with ML-DSA-65 (post-quantum)';
      }
      const sigRow = el('div', 'sheet-verify-item');
      sigRow.append(el('div', `sheet-verify-dot ${sigState}`, sigMark), el('span', '', sigLabel));
      list.append(sigRow);
    }

    // Stage 2: Chain submission — based on tx_hash presence.
    {
      let chainState, chainMark, chainLabel;
      if (hasTxHash) {
        chainState = 'done';
        chainMark = '✓';
        chainLabel = 'Submitted to local test chain';
      } else if (txStatus === 'pending') {
        chainState = 'pending';
        chainMark = '⏳';
        chainLabel = 'Awaiting chain submission';
      } else if (txStatus === 'failed') {
        chainState = 'fail';
        chainMark = '✕';
        chainLabel = 'Chain submission failed or was not reached';
      } else {
        // success but no hash — unusual; record it honestly
        chainState = 'done';
        chainMark = '✓';
        chainLabel = 'Submitted to local test chain';
      }
      const chainRow = el('div', 'sheet-verify-item');
      chainRow.append(el('div', `sheet-verify-dot ${chainState}`, chainMark), el('span', '', chainLabel));
      list.append(chainRow);
    }

    // Stage 3: Final outcome — mirrors the stored status.
    {
      let outcomeState, outcomeMark, outcomeLabel;
      if (txStatus === 'success') {
        outcomeState = 'done';
        outcomeMark = '✓';
        outcomeLabel = 'Payment recorded as successful';
      } else if (txStatus === 'pending') {
        outcomeState = 'pending';
        outcomeMark = '⏳';
        outcomeLabel = 'Outcome pending';
      } else {
        outcomeState = 'fail';
        outcomeMark = '✕';
        outcomeLabel = 'Payment recorded as failed';
      }
      const outcomeRow = el('div', 'sheet-verify-item');
      outcomeRow.append(el('div', `sheet-verify-dot ${outcomeState}`, outcomeMark), el('span', '', outcomeLabel));
      list.append(outcomeRow);
    }
  }

  $('sheet-backdrop').hidden = false;
  $('tx-sheet').hidden = false;
}
function closeSheet() {
  $('sheet-backdrop').hidden = true;
  $('tx-sheet').hidden = true;
}
$('sheet-backdrop').onclick = closeSheet;
$('btn-close-sheet').onclick = closeSheet;
$('btn-copy-tx').onclick = async () => {
  if (!activeSheetHash) return;
  try {
    await navigator.clipboard.writeText(activeSheetHash);
    toast('Copied');
  } catch {
    toast('Clipboard unavailable');
  }
};

// ---------- Inline SVG Icons ----------
function svgSent() {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  const line = document.createElementNS(ns, 'line');
  line.setAttribute('x1', '12'); line.setAttribute('y1', '19');
  line.setAttribute('x2', '12'); line.setAttribute('y2', '5');
  const poly = document.createElementNS(ns, 'polyline');
  poly.setAttribute('points', '5 12 12 5 19 12');
  svg.append(line, poly);
  return svg;
}
function svgReceived() {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  const line = document.createElementNS(ns, 'line');
  line.setAttribute('x1', '12'); line.setAttribute('y1', '5');
  line.setAttribute('x2', '12'); line.setAttribute('y2', '19');
  const poly = document.createElementNS(ns, 'polyline');
  poly.setAttribute('points', '19 12 12 19 5 12');
  svg.append(line, poly);
  return svg;
}

// ---------- Activity List & Grouping ----------
function histRow(h) {
  const sent = h.direction === 'sent';
  const name = h.counterparty ?? 'Unknown';
  const row = el('div', 'item');

  const ico = el('div', `tx-ico ${sent ? 'sent' : 'recv'}`);
  ico.appendChild(sent ? svgSent() : svgReceived());

  const mid = el('div', 'mid');
  const nameRow = el('div', 'name');
  nameRow.append(avatarSmall(name), el('span', '', `${sent ? 'To' : 'From'} ${name}`));

  const timeEl = el('div', 'time-relative', relativeTime(h.created_at));
  timeEl.title = when(h.created_at);
  mid.append(nameRow, timeEl);

  const right = el('div', 'item-right');
  const ethStr = h.value_wei ? weiToEthString(h.value_wei) : (h.amount_eth || '0');
  const amtEl = el('div', `tx-amount ${sent ? 'sent-amount' : 'recv-amount'}`, `${sent ? '-' : '+'}${ethStr} ETH`);
  const statusLabel = h.status === 'success' ? 'Success' : h.status === 'pending' ? 'Pending' : 'Failed';
  const statusCls = h.status === 'success' ? 'ok' : h.status === 'pending' ? 'pending' : 'bad';
  right.append(amtEl, el('span', `chip-status ${statusCls}`, statusLabel));

  row.append(ico, mid, right);
  row.onclick = () => openSheet(h);
  return row;
}

function dateLabel(d) {
  return d.toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' });
}

function paintHistory() {
  const list = $('home-history');
  clear(list);

  let filtered = currentHistory;
  if (currentFilter === 'sent') {
    filtered = currentHistory.filter((h) => h.direction === 'sent');
  } else if (currentFilter === 'recv') {
    filtered = currentHistory.filter((h) => h.direction === 'received');
  } else if (currentFilter === 'fail') {
    filtered = currentHistory.filter((h) => h.status !== 'success');
  }

  $('home-empty').hidden = filtered.length > 0;
  if (filtered.length === 0) return;

  const now = new Date();
  const yest = new Date();
  yest.setDate(now.getDate() - 1);

  // Group by date string for flexible grouping
  const groups = new Map();

  filtered.forEach((h) => {
    const d = new Date(Number(h.created_at) * 1000);
    const isToday = d.getFullYear() === now.getFullYear() &&
                    d.getMonth() === now.getMonth() &&
                    d.getDate() === now.getDate();
    if (isToday) {
      if (!groups.has('Today')) groups.set('Today', []);
      groups.get('Today').push(h);
      return;
    }
    const isYest = d.getFullYear() === yest.getFullYear() &&
                   d.getMonth() === yest.getMonth() &&
                   d.getDate() === yest.getDate();
    if (isYest) {
      if (!groups.has('Yesterday')) groups.set('Yesterday', []);
      groups.get('Yesterday').push(h);
      return;
    }
    const label = dateLabel(d);
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(h);
  });

  for (const [grpName, items] of groups) {
    list.append(el('div', 'group-label', grpName));
    items.forEach((h) => list.append(histRow(h)));
  }
}

function setHistoryFilter(f) {
  currentFilter = f;
  $('tab-filter-all').classList.toggle('on', f === 'all');
  $('tab-filter-sent').classList.toggle('on', f === 'sent');
  $('tab-filter-recv').classList.toggle('on', f === 'recv');
  $('tab-filter-fail').classList.toggle('on', f === 'fail');
  paintHistory();
}
$('tab-filter-all').onclick = () => setHistoryFilter('all');
$('tab-filter-sent').onclick = () => setHistoryFilter('sent');
$('tab-filter-recv').onclick = () => setHistoryFilter('recv');
$('tab-filter-fail').onclick = () => setHistoryFilter('fail');

// ---------- Home Screen Lifecycle ----------
async function goHome() {
  // Set greeting
  txt('home-greeting', `${getGreeting()}, ${user.name}`);

  // Set header user pill
  txt('header-username', user.name);
  paintAvatar($('header-av'), user.name);

  // Keep hidden elements for backward compat
  txt('home-name', user.name);
  paintAvatar($('home-av'), user.name);

  show('home');

  const chartEl = $('price-chart');
  if (chartEl && !chartRendered && typeof window.renderPriceChart === 'function') {
    window.renderPriceChart(chartEl, { base: 2500 });
    chartRendered = true;
    cleanChartDisclaimer();
    const obs = new MutationObserver(() => cleanChartDisclaimer());
    obs.observe(chartEl, { childList: true, subtree: true });
  }

  // Fetch live market data once per Home visit (background, non-blocking)
  if (chartEl && typeof window.updatePriceChart === 'function') {
    api.getMarket().then((data) => {
      window.updatePriceChart(chartEl, data);
      cleanChartDisclaimer();
    }).catch(() => { /* silently fall back to mock chart */ });
  }

  await refreshHome();
}

function cleanChartDisclaimer() {
  const note = document.querySelector('.pc-note');
  if (note && note.textContent.includes('Test ETH has no real value.')) {
    note.textContent = note.textContent.replace(/\.?\s*Test ETH has no real value\.?/gi, '').trim();
    if (!note.textContent) note.style.display = 'none';
  }
}

async function refreshHome() {
  $('home-skel').hidden = false;
  $('home-empty').hidden = true;

  // Show balance skeleton
  const balSkel = $('home-bal-skel');
  const balCard = $('home-bal-card');
  if (balSkel) balSkel.hidden = false;
  if (balCard) balCard.style.opacity = '0.4';

  try {
    const [me, hist] = await Promise.all([api.getMe(), api.getHistory()]);
    hideNetworkBanner();
    const [w, f = ''] = me.balance_eth.split('.');
    const frac = f.slice(0, 6).replace(/0+$/, '');
    balance = frac ? `${w}.${frac}` : w;
    animateBalanceCountUp(balance);

    // Show wallet address if available
    if (me.wallet_address) {
      const addrEl = $('home-wallet-addr');
      const addrRow = $('home-wallet-addr-row');
      if (addrEl && addrRow) {
        const addr = me.wallet_address;
        addrEl.textContent = `${addr.slice(0, 6)}...${addr.slice(-4)}`;
        addrEl.dataset.full = addr;
        addrRow.hidden = false;
      }
    }

    currentHistory = hist;
    paintHistory();

    const sentItems = hist.filter((h) => h.direction === 'sent');

    // Daily Limit Meter (last 24h successful sent items)
    const nowSec = Date.now() / 1000;
    const sent24h = hist
      .filter((h) => h.direction === 'sent' && h.status === 'success' && (nowSec - Number(h.created_at)) <= 86400)
      .reduce((acc, h) => acc + (Number(h.amount_eth) || 0), 0);

    const sent24hStr = sent24h.toFixed(3);
    txt('limit-text', `${sent24hStr} / ${DAILY_LIMIT} ETH sent in 24h`);
    const pct = Math.min(100, Math.max(0, (sent24h / DAILY_LIMIT) * 100));
    const fill = $('limit-fill');
    if (fill) fill.style.width = `${pct}%`;

    // Contacts
    const names = [...new Set(sentItems.map((h) => h.counterparty).filter(Boolean))].slice(0, 6);
    const box = $('contacts');
    clear(box);
    $('contacts-wrap').hidden = names.length === 0;
    names.forEach((n) => {
      const b = el('button', 'contact');
      b.append(avatar(n), el('span', '', n));
      b.onclick = () => quickPick(n);
      box.append(b);
    });
  } catch (err) {
    if (err.code === 'NETWORK' || err.status === 0) showNetworkBanner();
    if (err.status !== 401) toast('Could not load your account. Try Refresh.');
  } finally {
    $('home-skel').hidden = true;
    if (balSkel) balSkel.hidden = true;
    if (balCard) balCard.style.opacity = '1';
  }
}
if ($('btn-refresh')) {
  $('btn-refresh').onclick = async () => {
    await refreshHome();
    toast('Updated');
  };
}

// ---------- Receive Screen ----------
function openReceive() {
  if (!user) return;
  paintAvatar($('recv-av'), user.name);
  txt('recv-name', user.name);
  show('receive');
}
if ($('btn-go-receive')) $('btn-go-receive').onclick = openReceive;
$('btn-copy-username').onclick = async () => {
  if (!user) return;
  try {
    await navigator.clipboard.writeText(user.name);
    toast('Copied');
  } catch {
    toast('Clipboard unavailable');
  }
};

// ---------- Pay (Send) Wizard & Keypad ----------
function step(n) {
  [1, 2, 3].forEach((i) => {
    $(`pay-step-${i}`).hidden = i !== n;
    $(`st${i}`).classList.toggle('on', i <= n);
  });
  txt('pay-error', '');
}

function updateKeypadDisplay() {
  $('pay-amount').value = keypadAmount || '0';
}

function validateAmountLive(val) {
  const node = $('pay-amount-val');
  if (!node) return true;
  const trimmed = (val ?? '').trim();
  if (!trimmed || trimmed === '0') {
    node.className = 'inline-val dim';
    node.textContent = 'Up to 0.5 ETH per payment and 1 ETH per day';
    return false;
  }
  try {
    const wei = parseEthToWei(trimmed);
    const maxWei = parseEthToWei('0.5');
    if (BigInt(wei) > BigInt(maxWei)) {
      node.className = 'inline-val err';
      node.textContent = 'Exceeds 0.5 ETH per-payment limit';
      return false;
    }
    node.className = 'inline-val ok';
    node.textContent = 'Valid amount (up to 0.5 ETH)';
    return true;
  } catch {
    node.className = 'inline-val err';
    node.textContent = 'Enter a valid amount like 0.05';
    return false;
  }
}

function setAmount(amtStr) {
  txt('pay-error', '');
  keypadAmount = amtStr;
  updateKeypadDisplay();
  validateAmountLive(keypadAmount);
}

function handleKeypadKey(k) {
  txt('pay-error', '');
  if (k === 'back') {
    keypadAmount = keypadAmount.slice(0, -1);
    updateKeypadDisplay();
    validateAmountLive(keypadAmount);
    return;
  }
  if (k === '.') {
    if (keypadAmount.includes('.')) return;
    keypadAmount = keypadAmount ? `${keypadAmount}.` : '0.';
    updateKeypadDisplay();
    validateAmountLive(keypadAmount);
    return;
  }
  if (keypadAmount === '0') {
    keypadAmount = k;
    updateKeypadDisplay();
    validateAmountLive(keypadAmount);
    return;
  }
  if (keypadAmount.includes('.')) {
    const parts = keypadAmount.split('.');
    if (parts[1] && parts[1].length >= 18) return;
  } else {
    if (keypadAmount.length >= 3) return;
  }
  keypadAmount += k;
  updateKeypadDisplay();
  validateAmountLive(keypadAmount);
}

document.querySelectorAll('.keypad .key').forEach((btn) => {
  btn.onclick = () => handleKeypadKey(btn.dataset.k);
});
document.querySelectorAll('.chips .chip').forEach((c) => {
  c.onclick = () => {
    setAmount(c.dataset.amt || '');
  };
});

function openPay() {
  to = null;
  amount = '';
  idemKey = null;
  keypadAmount = '';
  $('pay-search').value = '';
  $('pay-password').value = '';
  updateKeypadDisplay();
  validateAmountLive('');
  const valEl = $('pay-recipient-val');
  if (valEl) {
    valEl.className = 'inline-val dim';
    valEl.textContent = '';
  }
  clear($('pay-results'));
  step(1);
  show('pay');
  $('pay-search').focus();
}
$('btn-go-pay').onclick = openPay;

// Empty state "Send your first payment" button
if ($('btn-empty-send')) $('btn-empty-send').onclick = openPay;

$('btn-pay-back').onclick = () => {
  const s = [1, 2, 3].find((i) => !$(`pay-step-${i}`).hidden);
  s > 1 ? step(s - 1) : show('home');
};

function choose(u) {
  to = u;
  idemKey = null;
  keypadAmount = '';
  updateKeypadDisplay();
  validateAmountLive('');
  paintAvatar($('p2-av'), u.name);
  txt('p2-name', u.name);
  const valEl = $('pay-recipient-val');
  if (valEl) {
    valEl.className = 'inline-val ok';
    valEl.textContent = `Selected: ${u.name}`;
  }
  step(2);
}
async function quickPick(name) {
  openPay();
  try {
    const m = (await api.searchUsers(name)).find((u) => u.name === name);
    if (m) choose(m);
  } catch { /* user can search */ }
}

$('pay-search').addEventListener('input', () => {
  clearTimeout(timer);
  const q = $('pay-search').value.trim(), box = $('pay-results'), valEl = $('pay-recipient-val');
  if (!q) {
    if (valEl) {
      valEl.className = 'inline-val dim';
      valEl.textContent = '';
    }
    return clear(box);
  }
  if (valEl) {
    valEl.className = 'inline-val dim';
    valEl.textContent = 'Searching...';
  }
  timer = setTimeout(async () => {
    try {
      const users = await api.searchUsers(q);
      hideNetworkBanner();
      clear(box);
      if (!users.length) {
        if (valEl) {
          valEl.className = 'inline-val err';
          valEl.textContent = 'No one found with that name';
        }
        box.append(el('div', 'dim', 'No one found with that name.'));
      } else {
        if (valEl) {
          valEl.className = 'inline-val ok';
          valEl.textContent = `Found ${users.length} match${users.length > 1 ? 'es' : ''}`;
        }
        users.forEach((u) => {
          const r = el('div', 'item');
          r.append(avatar(u.name), el('div', 'name', u.name));
          r.onclick = () => choose(u);
          box.append(r);
        });
      }
    } catch (err) {
      if (err.code === 'NETWORK' || err.status === 0) showNetworkBanner();
      if (valEl) {
        valEl.className = 'inline-val err';
        valEl.textContent = 'Search failed';
      }
    }
  }, 250);
});

$('p2-next').onclick = () => {
  const a = keypadAmount.trim();
  try {
    const wei = parseEthToWei(a);
    const maxWei = parseEthToWei('0.5');
    if (BigInt(wei) > BigInt(maxWei)) {
      triggerShake($('amount-container'));
      return txt('pay-error', 'Payment exceeds 0.5 ETH per-payment limit.');
    }
  } catch {
    triggerShake($('amount-container'));
    return txt('pay-error', 'Enter a valid ETH amount (e.g. 0.05).');
  }
  amount = a;
  idemKey = crypto.randomUUID();
  txt('p3-amt', `${a} ETH`);
  txt('review-amount', `${a} ETH`);
  txt('review-from-name', user.name);
  paintAvatar($('review-from-av'), user.name);
  txt('p3-name', to.name);
  paintAvatar($('p3-av'), to.name);
  txt('review-network', 'Local test chain');
  step(3);
  $('pay-password').focus();
};

// ---------- Status Screen & Submission ----------
function status(kind, title, sub, done, retry) {
  const ring = $('status-ring');
  ring.className = `ring ${kind}`;
  ring.textContent = kind === 'good' ? '✓' : kind === 'fail' ? '✕' : '';
  txt('status-title', title);
  txt('status-sub', sub);
  $('status-done').hidden = !done;
  $('status-retry').hidden = !retry;
  show('status');
}

$('status-copy').onclick = async () => {
  if (!lastReceipt) return;
  const rc = `HYQUB Payment Receipt\nTo: ${lastReceipt.to}\nAmount: ${lastReceipt.amount} ETH\nTransaction: ${lastReceipt.tx}`;
  try {
    await navigator.clipboard.writeText(rc);
    toast('Receipt copied');
  } catch {
    toast('Clipboard unavailable');
  }
};

if ($('btn-copy-rec-tx')) {
  $('btn-copy-rec-tx').onclick = async () => {
    if (!lastReceipt || !lastReceipt.tx) return;
    try {
      await navigator.clipboard.writeText(lastReceipt.tx);
      toast('Copied');
    } catch {
      toast('Clipboard unavailable');
    }
  };
}

if ($('status-send-another')) {
  $('status-send-another').onclick = () => {
    openPay();
  };
}

$('pay-submit').onclick = async () => {
  const btn = $('pay-submit');
  if (btn.disabled) return;
  const pw = $('pay-password').value;
  if (!pw) {
    triggerShake($('pay-step-3'));
    return txt('pay-error', 'Enter your password to confirm.');
  }
  btn.disabled = true;
  $('status-receipt').hidden = true;
  $('status-copy').hidden = true;
  if ($('btn-copy-rec-tx')) $('btn-copy-rec-tx').hidden = true;
  if ($('status-send-another')) $('status-send-another').hidden = true;
  const tl = $('status-timeline');
  if (tl) tl.hidden = true;

  // Single honest processing state (no fake intermediate steps)
  status('spin', 'Processing payment...', 'Submitting your payment securely to the network.');
  try {
    const r = await api.pay(to.id, amount, pw, idemKey);
    hideNetworkBanner();
    lastReceipt = { to: r.to, amount: r.amount_eth, tx: r.tx_hash || '' };
    txt('rec-to', r.to);
    txt('rec-amt', `${r.amount_eth} ETH`);
    txt('rec-tx', r.tx_hash || 'Confirmed on-chain');
    $('status-receipt').hidden = false;
    $('status-copy').hidden = false;
    if ($('btn-copy-rec-tx')) $('btn-copy-rec-tx').hidden = !r.tx_hash;
    if ($('status-send-another')) $('status-send-another').hidden = false;
    status('good', 'Payment sent', `${r.amount_eth} ETH sent to ${r.to}.`, true, false);
  } catch (err) {
    if (err.code === 'NETWORK' || err.status === 0) showNetworkBanner();
    if (tl) tl.hidden = true;
    if (err.status === 401 && err.code !== 'WRONG_PASSWORD') return;
    if (err.code === 'WRONG_PASSWORD') {
      toast('Wrong password. Nothing was sent.');
    }
    const m = friendlyError(err);
    status('fail', 'Payment not sent', m, false, true);
  } finally {
    btn.disabled = false;
    $('pay-password').value = '';
  }
};

$('status-done').onclick = async () => {
  const tl = $('status-timeline');
  if (tl) tl.hidden = true;
  show('home');
  await refreshHome();
};
$('status-retry').onclick = () => {
  const tl = $('status-timeline');
  if (tl) tl.hidden = true;
  show('pay');
  step(3);
  $('pay-password').focus();
};

setMode(false);
show('auth');
