/* ETH price chart — live data from backend, demo fallback. */
(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const W = 640, H = 240, PAD = { t: 14, r: 12, b: 26, l: 52 };
  const RANGES = { '7D': 7, '30D': 30, '90D': 90 };

  /* --- Seeded mock (used only when backend data is unavailable) --- */
  function rng(seed) {
    return function () {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function buildMockSeries(base) {
    const r = rng(42), out = [];
    const today = new Date(); today.setHours(0, 0, 0, 0);
    let p = base * 0.92;
    for (let i = 89; i >= 0; i--) {
      const d = new Date(today); d.setDate(today.getDate() - i);
      p *= 1 + (r() - 0.48) * 0.05;
      out.push({ date: d, price: p });
    }
    return out;
  }

  /* --- Provider display labels (ONLY place provider names appear) --- */
  const PROVIDER_LABELS = {
    binance: 'Binance ETH/USDT (approx. USD)',
    kraken:  'Kraken ETH/USD',
  };

  /* --- Convert [[ms, price], ...] to [{date, price}, ...] --- */
  function normaliseSeries(raw) {
    return raw.map(([ms, price]) => ({ date: new Date(ms), price: Number(price) }));
  }

  /* --- Validate incoming series from backend --- */
  function isValidSeries(s) {
    if (!Array.isArray(s) || s.length < 7) return false;
    for (const pt of s) {
      if (!Array.isArray(pt) || pt.length < 2) return false;
      const p = Number(pt[1]);
      if (!Number.isFinite(p) || p <= 0) return false;
    }
    return true;
  }

  const usd = n => '$' + n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  const day = d => d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  const pct = n => (n >= 0 ? '+' : '') + n.toFixed(2) + '%';

  function svgEl(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function div(cls, text, parent) {
    const e = document.createElement('div');
    e.className = cls;
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }

  const CSS = `
  .pc-card{background:var(--card,var(--s1,#151a23));border:1px solid var(--line,#2a2f45);border-radius:16px;padding:16px;color:var(--text,var(--tx,#e6e9ef))}
  .pc-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
  .pc-title{font-size:13px;color:var(--muted,var(--dim,#8b93a7))}
  .pc-price{font-size:26px;font-weight:700;margin-top:2px}
  .pc-change{font-size:13px;font-weight:600}
  .up{color:#22c55e}.down{color:#ef4444}
  .pc-tabs{display:flex;gap:6px}
  .pc-tab{border:0;background:transparent;color:var(--muted,var(--dim,#8b93a7));padding:6px 12px;border-radius:999px;cursor:pointer;font:inherit;font-size:13px}
  .pc-tab.active{background:var(--accent,var(--s2,#2d3650));color:var(--text,var(--tx,#e6e9ef))}
  .pc-wrap{position:relative;margin-top:8px}
  .pc-svg{width:100%;height:auto;display:block;touch-action:pan-y}
  .pc-grid{stroke:var(--muted,var(--dim,#8b93a7));stroke-opacity:.18;stroke-dasharray:3 4}
  .pc-axis{fill:var(--muted,var(--dim,#8b93a7));font-size:11px}
  .pc-vline{stroke:var(--muted,var(--dim,#8b93a7));stroke-opacity:.6}
  .pc-dot{stroke:var(--card,var(--s1,#151a23));stroke-width:2}
  .pc-tip{display:none;position:absolute;top:4px;transform:translateX(-50%);pointer-events:none;
    background:var(--card2,var(--s2,#1d2431));border-radius:10px;padding:6px 10px;font-size:12px;white-space:nowrap;box-shadow:0 4px 14px rgba(0,0,0,.35)}
  .pc-tip-price{font-weight:700;font-size:14px}
  .pc-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:12px}
  .pc-stat-label{font-size:11px;color:var(--muted,var(--dim,#8b93a7))}
  .pc-stat-value{font-size:14px;font-weight:600;margin-top:2px}
  .pc-note{font-size:11px;color:var(--muted,var(--dim,#8b93a7));margin-top:10px}
  @media(max-width:600px){.pc-stats{grid-template-columns:repeat(2,1fr)}}`;

  /**
   * renderPriceChart(root, opts)
   *
   * opts.series  – [[ms, price], ...] from /market/eth (90 points).
   *               When omitted the seeded mock series is used.
   * opts.source  – Provider identifier (controls the footer note)
   * opts.base    – base price for mock series (default 2500)
   */
  window.renderPriceChart = function (root, opts) {
    opts = opts || {};

    // Choose data source — fall back to mock if series is absent or invalid
    const source = opts.source || 'demo';
    const isLive = isValidSeries(opts.series);
    const all = isLive ? normaliseSeries(opts.series) : buildMockSeries(opts.base || 2500);

    let range = '30D';

    if (!document.getElementById('price-chart-css')) {
      const s = document.createElement('style');
      s.id = 'price-chart-css'; s.textContent = CSS;
      document.head.appendChild(s);
    }
    root.textContent = '';
    root.classList.add('pc-card');

    const head = div('pc-head', null, root);
    const tb = div('', null, head);

    // Title: "ETH price" for live data and "ETH price (demo data)" for demo
    const titleText = (isLive && source !== 'demo') ? 'ETH price' : 'ETH price (demo data)';
    div('pc-title', titleText, tb);
    const priceEl = div('pc-price', '', tb);
    const changeEl = div('pc-change', '', tb);
    const tabs = div('pc-tabs', null, head);
    const wrap = div('pc-wrap', null, root);
    const svg = svgEl('svg', {
      viewBox: `0 0 ${W} ${H}`, class: 'pc-svg', role: 'img',
      'aria-label': 'ETH price chart'
    }, wrap);
    const tip = div('pc-tip', null, wrap);
    const stats = div('pc-stats', null, root);

    // Footer note — exact text per spec; provider names come from PROVIDER_LABELS
    let noteText;
    if (isLive && PROVIDER_LABELS[source]) {
      noteText = 'Data: ' + PROVIDER_LABELS[source] + '. Test ETH has no real value.';
    } else {
      noteText = 'Simulated prices for demo purposes. Test ETH has no real value.';
    }
    div('pc-note', noteText, root);

    Object.keys(RANGES).forEach(k => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'pc-tab' + (k === range ? ' active' : '');
      b.textContent = k;
      b.addEventListener('click', () => {
        range = k;
        tabs.querySelectorAll('.pc-tab').forEach(t => t.classList.toggle('active', t === b));
        draw();
      });
      tabs.appendChild(b);
    });

    function stat(label, value, cls) {
      const s = div('pc-stat', null, stats);
      div('pc-stat-label', label, s);
      div('pc-stat-value ' + (cls || ''), value, s);
    }

    function draw() {
      const data = all.slice(-RANGES[range]);
      const n = data.length;
      svg.textContent = ''; stats.textContent = '';
      const prices = data.map(d => d.price);
      let min = Math.min(...prices), max = Math.max(...prices);
      const lo = min, hi = max, padY = (max - min) * 0.1 || 1;
      min -= padY; max += padY;
      const x = i => PAD.l + (i / (n - 1)) * (W - PAD.l - PAD.r);
      const y = p => PAD.t + (1 - (p - min) / (max - min)) * (H - PAD.t - PAD.b);
      const up = prices[n - 1] >= prices[0];
      const color = up ? '#22c55e' : '#ef4444';
      const defs = svgEl('defs', {}, svg);
      const g = svgEl('linearGradient', { id: 'pc-grad', x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
      svgEl('stop', { offset: '0%', 'stop-color': color, 'stop-opacity': 0.28 }, g);
      svgEl('stop', { offset: '100%', 'stop-color': color, 'stop-opacity': 0 }, g);
      for (let k = 0; k <= 4; k++) {
        const v = min + (max - min) * k / 4, yy = y(v);
        svgEl('line', { x1: PAD.l, x2: W - PAD.r, y1: yy, y2: yy, class: 'pc-grid' }, svg);
        svgEl('text', { x: PAD.l - 8, y: yy + 4, 'text-anchor': 'end', class: 'pc-axis' }, svg)
          .textContent = usd(v);
      }
      [0, Math.floor((n - 1) / 2), n - 1].forEach((i, idx) => {
        svgEl('text', {
          x: x(i), y: H - 6, class: 'pc-axis',
          'text-anchor': idx === 0 ? 'start' : idx === 2 ? 'end' : 'middle'
        }, svg).textContent = day(data[i].date);
      });
      const pts = data.map((d, i) => x(i).toFixed(1) + ',' + y(d.price).toFixed(1));
      svgEl('path', {
        d: 'M' + pts.join(' L') + ` L${x(n - 1)},${H - PAD.b} L${x(0)},${H - PAD.b} Z`,
        fill: 'url(#pc-grad)'
      }, svg);
      svgEl('path', {
        d: 'M' + pts.join(' L'), fill: 'none',
        stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round'
      }, svg);
      let best = 1, worst = 1;
      const chg = i => (prices[i] / prices[i - 1] - 1) * 100;
      for (let i = 1; i < n; i++) {
        if (chg(i) > chg(best)) best = i;
        if (chg(i) < chg(worst)) worst = i;
      }
      svgEl('circle', { cx: x(best), cy: y(prices[best]), r: 4, fill: '#22c55e' }, svg);
      svgEl('circle', { cx: x(worst), cy: y(prices[worst]), r: 4, fill: '#ef4444' }, svg);
      priceEl.textContent = usd(prices[n - 1]);
      const total = (prices[n - 1] / prices[0] - 1) * 100;
      changeEl.textContent = pct(total) + ' over ' + range;
      changeEl.className = 'pc-change ' + (total >= 0 ? 'up' : 'down');
      stat('High', usd(hi));
      stat('Low', usd(lo));
      stat('Best day', day(data[best].date) + ' ' + pct(chg(best)), 'up');
      stat('Worst day', day(data[worst].date) + ' ' + pct(chg(worst)), 'down');
      const line = svgEl('line', { y1: PAD.t, y2: H - PAD.b, class: 'pc-vline', visibility: 'hidden' }, svg);
      const dot = svgEl('circle', { r: 4, fill: color, class: 'pc-dot', visibility: 'hidden' }, svg);
      const hit = svgEl('rect', { x: PAD.l, y: 0, width: W - PAD.l - PAD.r, height: H, fill: 'transparent' }, svg);
      function move(ev) {
        const rect = svg.getBoundingClientRect();
        const cx = ev.touches ? ev.touches[0].clientX : ev.clientX;
        const vx = (cx - rect.left) / rect.width * W;
        let i = Math.round((vx - PAD.l) / (W - PAD.l - PAD.r) * (n - 1));
        i = Math.max(0, Math.min(n - 1, i));
        line.setAttribute('x1', x(i)); line.setAttribute('x2', x(i));
        dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(prices[i]));
        line.setAttribute('visibility', 'visible'); dot.setAttribute('visibility', 'visible');
        tip.textContent = '';
        div('', day(data[i].date), tip);
        div('pc-tip-price', usd(prices[i]), tip);
        if (i > 0) div(chg(i) >= 0 ? 'up' : 'down', pct(chg(i)) + ' vs prev day', tip);
        tip.style.left = Math.min(Math.max(x(i) / W * 100, 14), 86) + '%';
        tip.style.display = 'block';
      }
      function hide() {
        line.setAttribute('visibility', 'hidden');
        dot.setAttribute('visibility', 'hidden');
        tip.style.display = 'none';
      }
      hit.addEventListener('mousemove', move);
      hit.addEventListener('touchstart', move, { passive: true });
      hit.addEventListener('touchmove', move, { passive: true });
      hit.addEventListener('mouseleave', hide);
      hit.addEventListener('touchend', hide);
    }
    draw();
  };

  /**
   * updatePriceChart(root, liveData)
   *
   * Call after receiving fresh data from /market/eth.
   * liveData: { source, points }  where points may be null (demo sentinel).
   * Falls back to the existing mock series when source is demo or points is null/invalid.
   */
  window.updatePriceChart = function (root, liveData) {
    if (!root || !liveData) return;
    if (liveData.source === 'demo' || !liveData.points || !isValidSeries(liveData.points)) return;
    window.renderPriceChart(root, {
      series: liveData.points,
      source: liveData.source,
    });
  };
})();
