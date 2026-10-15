/** HYQUB UI controller. textContent only, no wallet addresses, token kept in api.js memory. */
import * as api from './api.js';
import { parseEthToWei } from './wei.js';

// Illustrative conversion rate for demo display only; test ETH has no real value.
const DEMO_RATE_USD = 2500;
const DAILY_LIMIT = 1;
const TEST_STATS = { backend: 91, frontend: 39, updated: 'October 2026' };

const $ = (id) => document.getElementById(id);
const SCREENS = ['auth', 'home', 'pay', 'receive', 'status'];
let signup = false, user = null, to = null, amount = '', timer = null, hidden = false, balance = '0', idemKey = null;
let currentFilter = 'all', currentHistory = [], lastReceipt = null, keypadAmount = '', currentTheme = 'dark';
let activeSheetHash = '', chartRendered = false;
let sessionInterval = null, sessionRemaining = 1800; // 30 minutes

const show = (n) => {
  SCREENS.forEach((s) => ($(`screen-${s}`).hidden = s !== n));
  updateNav(n);
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

function paintAvatar(node, name) {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  node.style.background = `hsl(${h} 70% 75%)`;
  node.textContent = (name[0] || '?').toUpperCase();
}
function avatar(name, lg) {
  const a = el('div', lg ? 'av lg' : 'av');
  paintAvatar(a, name);
  return a;
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
$('btn-theme').onclick = () => {
  currentTheme = currentTheme === 'dark' ? 'light' : 'dark';
  if (currentTheme === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
    txt('btn-theme', '☾');
  } else {
    document.documentElement.removeAttribute('data-theme');
    txt('btn-theme', '☼');
  }
};

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
  txt('auth-submit', s ? 'Create account' : 'Log in');
  txt('auth-error', '');
  const pw = $('auth-password');
  if (pw) pw.autocomplete = s ? 'new-password' : 'current-password';
  txt('auth-switch-text', s ? 'Already have an account? ' : "Don't have an account? ");
  txt('auth-switch-btn', s ? 'Log in' : 'Sign up');
}
const authSwitchBtn = $('auth-switch-btn');
if (authSwitchBtn) authSwitchBtn.onclick = () => setMode(!signup);

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
    user = { id: d.user_id, name: d.name };
    $('auth-password').value = '';
    startSessionTimer();
    await goHome();
  } catch (err) {
    triggerShake($('auth-card'));
    txt('auth-error', err.status === 409 ? 'That username is taken. Try another one.'
      : err.status === 401 ? 'Wrong username or password.'
      : err.status === 429 ? 'Too many attempts. Wait a minute and try again.' : err.message);
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

// ---------- Home: Balance & Demo USD ----------
const when = (ts) => new Date(ts * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });

function paintDemoUsd(bStr) {
  const elUsd = $('home-demo-usd');
  if (!elUsd) return;
  if (hidden) {
    elUsd.textContent = '≈ $•••• at a demo rate. Test ETH has no real value.';
    return;
  }
  const n = Number(bStr);
  const val = isNaN(n) ? '0.00' : (n * DEMO_RATE_USD).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  elUsd.textContent = `≈ $${val} at a demo rate. Test ETH has no real value.`;
}

function paintBalance() {
  txt('home-balance', hidden ? '••••' : balance);
  txt('btn-eye', hidden ? 'Show' : 'Hide');
  paintDemoUsd(balance);
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
    paintDemoUsd(curr.toString());
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

// ---------- Activity Bottom Sheet ----------
function openSheet(h) {
  activeSheetHash = h.tx_hash || '';
  const sent = h.direction === 'sent';
  const name = h.counterparty ?? 'Unknown';
  txt('sheet-title', `${sent ? 'Sent' : 'Received'} ${h.amount_eth} ETH`);

  const st = $('sheet-status');
  st.className = `chip-status ${h.status === 'success' ? 'ok' : 'bad'}`;
  st.textContent = h.status === 'success' ? 'Success' : 'Failed';

  txt('sheet-party-label', sent ? 'To' : 'From');
  txt('sheet-party', name);
  txt('sheet-time', when(h.created_at));
  txt('sheet-hash', activeSheetHash || 'Not available');

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

// ---------- Activity List & Grouping ----------
function histRow(h) {
  const sent = h.direction === 'sent';
  const name = h.counterparty ?? 'Unknown';
  const row = el('div', 'item');

  const ico = el('div', `tx-ico ${sent ? 'sent' : 'recv'}`, sent ? '↑' : '↓');
  const mid = el('div', 'mid');
  mid.append(
    el('div', 'name', `${sent ? 'To' : 'From'} ${name}`),
    el('div', 'dim', when(h.created_at))
  );

  const right = el('div', 'item-right');
  right.append(
    el('div', sent ? '' : 'ok', `${sent ? '-' : '+'}${h.amount_eth} ETH`),
    el('span', `chip-status ${h.status === 'success' ? 'ok' : 'bad'}`, h.status)
  );

  row.append(ico, mid, right);
  row.onclick = () => openSheet(h);
  return row;
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

  const groups = { TODAY: [], YESTERDAY: [], EARLIER: [] };
  const now = new Date();
  const yest = new Date();
  yest.setDate(now.getDate() - 1);

  filtered.forEach((h) => {
    const d = new Date(Number(h.created_at) * 1000);
    const isToday = d.getFullYear() === now.getFullYear() &&
                    d.getMonth() === now.getMonth() &&
                    d.getDate() === now.getDate();
    if (isToday) {
      groups.TODAY.push(h);
      return;
    }
    const isYest = d.getFullYear() === yest.getFullYear() &&
                   d.getMonth() === yest.getMonth() &&
                   d.getDate() === yest.getDate();
    if (isYest) {
      groups.YESTERDAY.push(h);
      return;
    }
    groups.EARLIER.push(h);
  });

  ['TODAY', 'YESTERDAY', 'EARLIER'].forEach((grpName) => {
    const items = groups[grpName];
    if (!items || items.length === 0) return;
    list.append(el('div', 'group-label', grpName));
    items.forEach((h) => list.append(histRow(h)));
  });
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
  txt('home-name', user.name);
  paintAvatar($('home-av'), user.name);
  show('home');

  const chartEl = $('price-chart');
  if (chartEl && !chartRendered && typeof window.renderPriceChart === 'function') {
    // Render mock immediately so the chart is never blank
    window.renderPriceChart(chartEl, { base: DEMO_RATE_USD });
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
  try {
    const [me, hist] = await Promise.all([api.getMe(), api.getHistory()]);
    const [w, f = ''] = me.balance_eth.split('.');
    const frac = f.slice(0, 6).replace(/0+$/, '');
    balance = frac ? `${w}.${frac}` : w;
    animateBalanceCountUp(balance);

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
    if (err.status !== 401) toast('Could not load your account. Try Refresh.');
  } finally {
    $('home-skel').hidden = true;
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
function handleKeypadKey(k) {
  txt('pay-error', '');
  if (k === 'back') {
    keypadAmount = keypadAmount.slice(0, -1);
    updateKeypadDisplay();
    return;
  }
  if (k === '.') {
    if (keypadAmount.includes('.')) return;
    keypadAmount = keypadAmount ? `${keypadAmount}.` : '0.';
    updateKeypadDisplay();
    return;
  }
  if (keypadAmount === '0') {
    keypadAmount = k;
    updateKeypadDisplay();
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
}

document.querySelectorAll('.keypad .key').forEach((btn) => {
  btn.onclick = () => handleKeypadKey(btn.dataset.k);
});
document.querySelectorAll('.chips .chip').forEach((c) => {
  c.onclick = () => {
    keypadAmount = c.dataset.amt || '';
    updateKeypadDisplay();
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
  clear($('pay-results'));
  step(1);
  show('pay');
  $('pay-search').focus();
}
$('btn-go-pay').onclick = openPay;
$('btn-pay-back').onclick = () => {
  const s = [1, 2, 3].find((i) => !$(`pay-step-${i}`).hidden);
  s > 1 ? step(s - 1) : show('home');
};

function choose(u) {
  to = u;
  idemKey = null;
  keypadAmount = '';
  updateKeypadDisplay();
  paintAvatar($('p2-av'), u.name);
  txt('p2-name', u.name);
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
  const q = $('pay-search').value.trim(), box = $('pay-results');
  if (!q) return clear(box);
  timer = setTimeout(async () => {
    try {
      const users = await api.searchUsers(q);
      clear(box);
      if (!users.length) box.append(el('div', 'dim', 'No one found with that name.'));
      users.forEach((u) => {
        const r = el('div', 'item');
        r.append(avatar(u.name), el('div', 'name', u.name));
        r.onclick = () => choose(u);
        box.append(r);
      });
    } catch { /* ignore while typing */ }
  }, 250);
});

$('p2-next').onclick = () => {
  const a = keypadAmount.trim();
  try {
    parseEthToWei(a);
  } catch {
    triggerShake($('amount-container'));
    return txt('pay-error', 'Enter an amount like 0.01.');
  }
  amount = a;
  idemKey = crypto.randomUUID();
  txt('p3-amt', `${a} ETH`);
  txt('p3-name', to.name);
  paintAvatar($('p3-av'), to.name);
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

$('pay-submit').onclick = async () => {
  const pw = $('pay-password').value;
  if (!pw) {
    triggerShake($('pay-step-3'));
    return txt('pay-error', 'Enter your password to confirm.');
  }
  const btn = $('pay-submit');
  btn.disabled = true;
  $('status-receipt').hidden = true;
  $('status-copy').hidden = true;
  const tl = $('status-timeline');
  if (tl) tl.hidden = true;
  status('spin', 'Verifying...', 'Signing and checking your payment securely.');
  try {
    const r = await api.pay(to.id, amount, pw, idemKey);
    lastReceipt = { to: r.to, amount: r.amount_eth, tx: r.tx_hash || '' };
    txt('rec-to', r.to);
    txt('rec-amt', `${r.amount_eth} ETH`);
    txt('rec-tx', r.tx_hash || 'Confirmed on-chain');
    $('status-receipt').hidden = false;
    $('status-copy').hidden = false;
    if (tl) tl.hidden = false;
    status('good', 'Payment sent', `${r.amount_eth} ETH sent to ${r.to}.`, true, false);
  } catch (err) {
    if (tl) tl.hidden = true;
    if (err.status === 401 && err.code !== 'WRONG_PASSWORD') return;
    if (err.code === 'WRONG_PASSWORD') {
      toast('Wrong password. Nothing was sent.');
    }
    const m = {
      WRONG_PASSWORD: 'Wrong password. Nothing was sent.',
      INSUFFICIENT_BALANCE: 'Your balance is too low for this payment.',
      LIMIT_EXCEEDED: err.message,
      RATE_LIMITED: 'Too many payments. Wait a minute and try again.',
      SELF_PAYMENT: 'You cannot send money to yourself.',
      RECIPIENT_NOT_FOUND: 'That person was not found.',
      INVALID_AMOUNT: err.message
    }[err.code] || 'Payment failed. Nothing was sent. Please try again.';
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
