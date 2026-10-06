// Terminal functions rendered inside workspace panels: MENU, DES, GP, GC, ECO, HELP.
// Each renderer: async (body, args, ctx) -> { title, menu: [{label, action}], resize?, cleanup? }
// `action` is {fn, args} handed back to the workspace (numbered "n <GO>" selection).
import { api, esc, fmtAge, fmtNum, flagsHtml, shortDate, statusBadge } from './util.js';

export const YELLOW_KEYS = { F2: 'GOVT', F8: 'EQUITY', F9: 'COMDTY', F10: 'INDEX', F11: 'CRNCY' };
const secQs = (sec) => `entity=${encodeURIComponent(sec.entity)}&metric=${encodeURIComponent(sec.metric)}`;
const isSpread = (sec) => sec.metric === 'spread';

function menuHtml(items) {
  return `<div class="fmenu">${items.map((m, i) => `<div class="fmi" data-n="${i + 1}"><span class="n">${i + 1})</span> ${m.label}</div>`).join('')}</div>`;
}

// ------------------------------------------------------------------ HELP
export async function HELP(body) {
  body.innerHTML = `<div class="fpad">
    <div class="sub">COMMAND SYNTAX</div>
    <div class="note"><span class="white">&lt;TICKER&gt; &lt;YELLOW KEY&gt; &lt;FUNCTION&gt; &lt;GO&gt;</span>. Enter = GO. The yellow key is optional (it disambiguates). A bare ticker opens its numbered menu; type the number then GO.</div>
    <table class="ft"><tbody>
      <tr><td class="amber">GP</td><td>Price/level chart: studies, log, compare</td><td class="dim">CL1 COMDTY GP</td></tr>
      <tr><td class="amber">DES</td><td>Description: sources, coverage, status, bounds</td><td class="dim">USGG10YR GOVT DES</td></tr>
      <tr><td class="amber">GC</td><td>US Treasury curve vs 1W/1M/1Y</td><td class="dim">GC</td></tr>
      <tr><td class="amber">ECO</td><td>US release calendar + CB meetings</td><td class="dim">ECO</td></tr>
      <tr><td class="amber">TOP / N</td><td>Live news wire, optional topic: WAR OIL EQUITIES MARKETS MACRO SHIPPING WORLD</td><td class="dim">N OIL</td></tr>
      <tr><td class="amber">CN</td><td>Company news for a watchlist equity</td><td class="dim">XOM US EQUITY CN</td></tr>
      <tr><td class="amber">MENU</td><td>Back in the focused panel (also Esc)</td><td class="dim">MENU</td></tr>
      <tr><td class="amber">ADD x</td><td>Add a panel: GLOBE KPI INSPECTOR WIRE TANKERS CHOKEPOINTS PRODUCTION COMMODITIES PORTFOLIO</td><td class="dim">ADD TANKERS</td></tr>
      <tr><td class="amber">TAB x</td><td>Switch workspace by name or number</td><td class="dim">TAB MARKETS</td></tr>
      <tr><td class="amber">MAX</td><td>Maximize / restore the focused panel (also ⤢, double-click header, Esc)</td><td class="dim">MAX</td></tr>
    </tbody></table>
    <div class="sub">YELLOW KEYS</div>
    <div class="note"><span class="yk">F2 GOVT</span> <span class="yk">F8 EQUITY</span> <span class="yk">F9 COMDTY</span> <span class="yk">F10 INDEX</span> <span class="yk">F11 CRNCY</span>. On a Mac hold fn, or click the chips next to the command line.</div>
    <div class="sub">EXAMPLE TICKERS</div>
    <div class="note">COMDTY: CL1 CO1 NG1 TTF JKM HG1 GC1 UX1 · GOVT: USGG3M USGG2YR USGG10YR USGG30YR DEU10Y GBR10Y JPN10Y · INDEX: SPX VIX USYC2Y10 LF98OAS CPIAUCSL PAYEMS UNRATE ICSA · CRNCY: USDJPY USDEUR USDCNY DXY · EQUITY: watchlist tickers (XOM US …)</div>
    <div class="sub">GLOBE COMMANDS (unchanged)</div>
    <div class="note">CHN / GERMANY → country · HORMUZ → chokepoint · GDP CPI RATES YIELD OIL FX CA → choropleth · LAYER n · HEALTH · LIVE · R</div>
    <div class="note">Tankers: type a tanker name, MMSI or IMO (e.g. BREEZE). Not available on free data: WEI, futures curves (CT), intraday tick charts, consensus (ECO survey column).</div></div>`;
  return { title: 'HELP', menu: [] };
}

// ------------------------------------------------------------------ MENU
export async function MENU(body, { sec }) {
  const h = await api(`/api/history?${secQs(sec)}&days=30`);
  const items = [
    { label: 'GP  — chart', action: { fn: 'GP', args: { sec } } },
    { label: 'DES — description & data provenance', action: { fn: 'DES', args: { sec } } },
  ];
  if (sec.kind === 'curve') items.push({ label: 'GC  — Treasury curve', action: { fn: 'GC', args: {} } });
  if (sec.kind === 'eco') items.push({ label: 'ECO — release calendar', action: { fn: 'ECO', args: {} } });
  if (sec.kind === 'equity') items.push({ label: 'Portfolio exposure (right rail)', action: { fn: '@holding', args: { id: sec.entity.slice(3) } } });
  if (sec.kind === 'equity') items.push({ label: 'CN  — company news', action: { fn: 'CN', args: { sec } } });
  const last = h.value != null ? `${valFmt(sec, h.value)} <span class="dim">${esc(h.unit || '')}</span>` : '<span class="down">NO DATA</span>';
  body.innerHTML = `<div class="fpad"><div class="sechead"><span class="white big">${esc(sec.ticker)}</span> <span class="yk">${esc(sec.yk)}</span> <span class="dim">${esc(sec.name)}</span></div>
    <div class="kv"><span>Last</span><span class="white">${last} ${statusBadge(h.status, h.provider_age_s)}</span></div>
    <div class="kv"><span>As of</span><span>${esc(h.period_label || shortDate(h.observation_date))} · ${esc(h.source || '—')}</span></div>
    ${menuHtml(items)}${h.value == null ? '<div class="note down">No stored data for this security: check the data-health matrix (missing key or market provider).</div>' : ''}</div>`;
  return { title: `${sec.ticker} ${sec.yk}`, menu: items };
}

function valFmt(sec, v) {
  if (v == null) return '—';
  return isSpread(sec) ? `${fmtNum(v * 100, '', 0)}bp` : fmtNum(v, sec.metric === 'yield' || sec.metric === 'yield_10y' ? '%' : '', sec.metric.includes('yield') ? 3 : undefined);
}

// ------------------------------------------------------------------ DES
export async function DES(body, { sec }) {
  const d = await api(`/api/des?${secQs(sec)}`);
  const src = d.sources.map((s) => `<tr><td class="white">${esc(s.source)}</td><td class="num white">${valFmt(sec, s.value)}</td>
    <td>${esc(s.period_label || shortDate(s.observation_date))}</td><td class="num">${s.count}</td><td class="dim">${esc((s.first || '').slice(0, 10))}</td>
    <td>${esc(s.release_cadence)}</td><td>${statusBadge(s.status, s.provider_age_s)}</td><td>${flagsHtml(s.flags)}</td></tr>`).join('');
  const b = d.bounds ? Object.entries(d.bounds).map(([k, v]) => `${k}=${v}`).join(' · ') : 'none';
  body.innerHTML = `<div class="fpad"><div class="sechead"><span class="white big">${esc(sec.ticker)}</span> <span class="yk">${esc(sec.yk)}</span> <span class="dim">${esc(sec.name)}</span></div>
    <div class="kv"><span>Stored as</span><span class="white">${esc(sec.entity)} · ${esc(sec.metric)}</span></div>
    ${sec.note ? `<div class="kv"><span>Quote</span><span>${esc(sec.note)}</span></div>` : ''}
    <div class="sub">SOURCES</div>
    ${src ? `<table class="ft"><thead><tr><th>SRC</th><th class="num">LAST</th><th>AS OF</th><th class="num">OBS</th><th>FIRST</th><th>CADENCE</th><th>STATUS</th><th>FLAGS</th></tr></thead><tbody>${src}</tbody></table>` : '<div class="note down">No data stored.</div>'}
    <div class="sub">DATA QUALITY</div>
    <div class="kv"><span>Sanity bounds</span><span>${esc(b)}</span></div>
    <div class="kv"><span>Quarantined prints</span><span class="${d.quarantined ? 'down' : ''}">${d.quarantined}</span></div>
    <div class="note">Charts use one source's history only; sources are never spliced together.</div>
    ${menuHtml([{ label: 'GP — chart' }])}</div>`;
  return { title: `${sec.ticker} DES`, menu: [{ label: 'GP', action: { fn: 'GP', args: { sec } } }] };
}

// ------------------------------------------------------------------ GP
const WINDOWS = [['1M', 30], ['3M', 91], ['6M', 182], ['YTD', -1], ['1Y', 365], ['2Y', 730], ['5Y', 1826], ['MAX', 3990]];
const CMP_COLORS = ['#00e5ff', '#b388ff', '#2ecc40'];

function sma(vals, n) {
  const out = new Array(vals.length).fill(null);
  let s = 0;
  for (let i = 0; i < vals.length; i++) {
    s += vals[i];
    if (i >= n) s -= vals[i - n];
    if (i >= n - 1) out[i] = s / n;
  }
  return out;
}
function boll(vals, n = 20, k = 2) {
  const m = sma(vals, n), up = [], lo = [];
  for (let i = 0; i < vals.length; i++) {
    if (m[i] == null) { up.push(null); lo.push(null); continue; }
    let v = 0;
    for (let j = i - n + 1; j <= i; j++) v += (vals[j] - m[i]) ** 2;
    const sd = Math.sqrt(v / n);
    up.push(m[i] + k * sd); lo.push(m[i] - k * sd);
  }
  return [up, lo];
}
function niceTicks(lo, hi, n = 5) {
  const span = hi - lo || 1, step0 = span / n, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || mag * 10;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
  return out;
}
const tsOf = (d) => Date.parse(d + 'T00:00:00Z');

export async function GP(body, args, ctx) {
  const st = { sec: args.sec, win: args.win || '1Y', studies: new Set(args.studies || []), log: !!args.log,
    cmps: args.cmps || [], data: null, cmpData: [] };
  body.innerHTML = `<div class="gp">
    <div class="gpbar"><span class="gpwins">${WINDOWS.map(([l]) => `<button data-w="${l}">${l}</button>`).join('')}</span>
      <span class="gpst">${['MA20', 'MA50', 'MA200', 'BB'].map((s) => `<button data-s="${s}">${s}</button>`).join('')}<button data-log="1">LOG</button></span>
      <span class="gpcmp"><input placeholder="+ compare (e.g. CO1)" spellcheck="false"><span class="chips"></span></span></div>
    <div class="gphead"></div><div class="gpread"></div><div class="gpchart"></div></div>`;
  const $ = (s) => body.querySelector(s);

  const days = () => {
    const w = WINDOWS.find((x) => x[0] === st.win)[1];
    if (w > 0) return w;
    const now = new Date();
    return Math.max(7, Math.ceil((now - Date.UTC(now.getUTCFullYear(), 0, 1)) / 864e5));
  };
  const persist = () => ctx.persist({ sec: st.sec, win: st.win, studies: [...st.studies], log: st.log, cmps: st.cmps });

  async function load() {
    const d = days() + 210;   // extra lookback so MA200 / BB are defined at the window start
    st.data = await api(`/api/history?${secQs(st.sec)}&days=${d}`);
    st.cmpData = await Promise.all(st.cmps.map((c) => api(`/api/history?${secQs(c)}&days=${d}`)));
    draw();
  }

  function draw() {
    body.querySelectorAll('.gpwins button').forEach((b) => b.classList.toggle('on', b.dataset.w === st.win));
    body.querySelectorAll('.gpst button[data-s]').forEach((b) => b.classList.toggle('on', st.studies.has(b.dataset.s)));
    $('.gpst button[data-log]').classList.toggle('on', st.log);
    $('.chips').innerHTML = st.cmps.map((c, i) => `<span class="chip" style="border-color:${CMP_COLORS[i]};color:${CMP_COLORS[i]}">${esc(c.ticker)} <b data-rm="${i}">×</b></span>`).join('');
    const h = st.data;
    const scale = isSpread(st.sec) ? 100 : 1;
    const all = (h.points || []).map(([d, v]) => [d, v * scale]);
    const cut = new Date(Date.now() - days() * 864e5).toISOString().slice(0, 10);
    const startIdx = Math.max(0, all.findIndex((p) => p[0] >= cut));
    const vis = all.slice(startIdx);
    const unit = isSpread(st.sec) ? 'bp' : (h.unit || '');
    if (vis.length < 2) {
      $('.gphead').innerHTML = headHtml(h, vis, unit);
      $('.gpchart').innerHTML = `<div class="note">${h.points && h.points.length ? 'Fewer than 2 observations in this window (low-frequency series?) — try a longer window.' : 'No stored data for this security.'}</div>`;
      return;
    }
    const rebase = st.cmps.length > 0;
    const base = vis[0][1];
    const tf = (v) => (rebase ? (v / base) * 100 : v);
    const lines = [{ pts: vis.map(([d, v]) => [d, tf(v)]), color: '#ffb000', w: 1.4, name: st.sec.ticker }];
    const allVals = all.map((p) => p[1]);
    const studyLine = (arr, color, name, dash) => {
      lines.push({ pts: arr.slice(startIdx).map((v, i) => [vis[i][0], v == null ? null : tf(v)]), color, w: 1, name, dash });
    };
    if (st.studies.has('MA20')) studyLine(sma(allVals, 20), '#e8e8e8', 'MA20');
    if (st.studies.has('MA50')) studyLine(sma(allVals, 50), '#00e5ff', 'MA50');
    if (st.studies.has('MA200')) studyLine(sma(allVals, 200), '#b388ff', 'MA200');
    if (st.studies.has('BB')) { const [u, l] = boll(allVals); studyLine(u, '#8a8a8a', 'BB+', '3 2'); studyLine(l, '#8a8a8a', 'BB−', '3 2'); }
    st.cmpData.forEach((c, i) => {
      const cs = isSpread(st.cmps[i]) ? 100 : 1;
      const pts = (c.points || []).filter((p) => p[0] >= vis[0][0]);
      if (pts.length < 2) return;
      const b0 = pts[0][1] * cs;
      lines.push({ pts: pts.map(([d, v]) => [d, ((v * cs) / b0) * 100]), color: CMP_COLORS[i], w: 1.2, name: st.cmps[i].ticker, src: c.source });
    });

    const el = $('.gpchart');
    const W = Math.max(300, el.clientWidth), H = Math.max(160, el.clientHeight);
    const P = { l: 6, r: 62, t: 8, b: 20 };
    const vals = lines.flatMap((l) => l.pts.map((p) => p[1])).filter((v) => v != null);
    const canLog = st.log && vals.every((v) => v > 0);
    const f = canLog ? Math.log : (v) => v;
    let lo = Math.min(...vals.map(f)), hi = Math.max(...vals.map(f));
    if (hi === lo) { hi += 1; lo -= 1; }
    const pad = (hi - lo) * 0.05; lo -= pad; hi += pad;
    const t0 = tsOf(vis[0][0]), t1 = tsOf(vis[vis.length - 1][0]);
    const X = (d) => P.l + ((tsOf(d) - t0) / (t1 - t0 || 1)) * (W - P.l - P.r);
    const Y = (v) => P.t + (1 - (f(v) - lo) / (hi - lo)) * (H - P.t - P.b);
    const yt = canLog ? niceTicks(Math.exp(lo), Math.exp(hi)) : niceTicks(lo, hi);
    const grid = yt.map((v) => `<line x1="${P.l}" x2="${W - P.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="#1d1d1d"/><text x="${W - P.r + 4}" y="${Y(v) + 3}" fill="#8a8a8a" font-size="10">${fmtNum(v, rebase ? '' : unit)}</text>`).join('');
    const nx = Math.max(2, Math.floor(W / 110));
    const xt = Array.from({ length: nx + 1 }, (_, i) => vis[Math.round((i / nx) * (vis.length - 1))][0]);
    const xg = xt.map((d) => `<text x="${X(d)}" y="${H - 5}" fill="#8a8a8a" font-size="10" text-anchor="middle">${d.slice(2)}</text>`).join('');
    const paths = lines.map((l) => {
      let dstr = '', pen = false;
      for (const [d, v] of l.pts) {
        if (v == null || d < vis[0][0]) { pen = false; continue; }
        dstr += `${pen ? 'L' : 'M'}${X(d).toFixed(1)},${Y(v).toFixed(1)}`; pen = true;
      }
      return `<path d="${dstr}" fill="none" stroke="${l.color}" stroke-width="${l.w}" ${l.dash ? `stroke-dasharray="${l.dash}"` : ''} vector-effect="non-scaling-stroke"/>`;
    }).join('');
    const last = lines[0].pts[lines[0].pts.length - 1][1];
    const lastTag = `<rect x="${W - P.r + 1}" y="${Y(last) - 7}" width="${P.r - 2}" height="14" fill="#ffb000"/><text x="${W - P.r + 4}" y="${Y(last) + 4}" fill="#000" font-size="10" font-weight="700">${fmtNum(last, rebase ? '' : unit)}</text>`;
    el.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${grid}${xg}${paths}${lastTag}
      <line class="xh" y1="${P.t}" y2="${H - P.b}" stroke="#555" visibility="hidden"/><rect class="hit" x="${P.l}" y="0" width="${W - P.l - P.r}" height="${H}" fill="transparent"/></svg>`;
    $('.gphead').innerHTML = headHtml(h, vis, unit, rebase, canLog !== st.log ? 'LOG unavailable (non-positive values)' : '');
    $('.gpread').innerHTML = legend(lines);

    // crosshair: updates two nodes in place, never re-renders the chart
    const svg = el.querySelector('svg'), xh = svg.querySelector('.xh');
    svg.querySelector('.hit').addEventListener('mousemove', (ev) => {
      const r = svg.getBoundingClientRect();
      const t = t0 + ((ev.clientX - r.left - P.l) / (W - P.l - P.r)) * (t1 - t0);
      let i = vis.findIndex((p) => tsOf(p[0]) >= t);
      if (i < 0) i = vis.length - 1;
      if (i > 0 && Math.abs(tsOf(vis[i - 1][0]) - t) < Math.abs(tsOf(vis[i][0]) - t)) i -= 1;
      const d = vis[i][0];
      xh.setAttribute('x1', X(d)); xh.setAttribute('x2', X(d)); xh.setAttribute('visibility', 'visible');
      $('.gpread').innerHTML = `<span class="white">${d}</span> ` + lines.map((l) => {
        const p = [...l.pts].reverse().find((q) => q[0] <= d && q[1] != null);
        return `<span style="color:${l.color}">${esc(l.name)} ${p ? fmtNum(p[1], rebase ? '' : unit) : '—'}</span>`;
      }).join(' ');
    });
    svg.querySelector('.hit').addEventListener('mouseleave', () => { xh.setAttribute('visibility', 'hidden'); $('.gpread').innerHTML = legend(lines); });
  }

  function headHtml(h, vis, unit, rebase, warn) {
    if (!vis.length) return `<span class="white big">${esc(st.sec.ticker)}</span> <span class="dim">${esc(st.sec.name)}</span> ${statusBadge(h.status)}`;
    const a = vis[0][1], b = vis[vis.length - 1][1];
    const chg = isSpread(st.sec) || st.sec.metric.includes('yield') ? `${b - a >= 0 ? '+' : ''}${fmtNum((b - a) * (isSpread(st.sec) ? 1 : 100), '', 0)}bp` : `${b >= a ? '+' : ''}${fmtNum((b / a - 1) * 100, '%')}%`;
    const hi = Math.max(...vis.map((p) => p[1])), lo = Math.min(...vis.map((p) => p[1]));
    return `<span class="white big">${esc(st.sec.ticker)}</span> <span class="yk">${esc(st.sec.yk)}</span> <span class="dim">${esc(st.sec.name)}</span>
      <span class="white">${fmtNum(b, unit)} ${esc(unit)}</span> <span class="${b >= a ? 'up' : 'down'}">${chg} (${st.win})</span>
      <span class="dim">H ${fmtNum(hi, unit)} L ${fmtNum(lo, unit)} · ${vis.length} obs · as of ${esc(h.period_label || shortDate(h.observation_date))} · ${esc(h.source)}</span> ${statusBadge(h.status, h.provider_age_s)}
      ${rebase ? '<span class="cyan">REBASED =100</span>' : ''} ${warn ? `<span class="down">${warn}</span>` : ''}`;
  }
  const legend = (lines) => lines.map((l) => `<span style="color:${l.color}">■ ${esc(l.name)}${l.src ? ' (' + esc(l.src) + ')' : ''}</span>`).join(' ');

  body.querySelector('.gpwins').addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; st.win = b.dataset.w; persist(); load(); });
  body.querySelector('.gpst').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    if (b.dataset.log) st.log = !st.log;
    else st.studies.has(b.dataset.s) ? st.studies.delete(b.dataset.s) : st.studies.add(b.dataset.s);
    persist(); draw();
  });
  const cmpIn = body.querySelector('.gpcmp input');
  cmpIn.addEventListener('keydown', async (e) => {
    e.stopPropagation();
    if (e.key !== 'Enter') return;
    const q = cmpIn.value.trim();
    if (!q) return;
    try {
      const r = await api(`/api/command?q=${encodeURIComponent(q)}`);
      if (st.cmps.length >= 3) st.cmps.shift();
      st.cmps.push(r.security); cmpIn.value = ''; persist(); load();
    } catch (err) { cmpIn.value = ''; cmpIn.placeholder = `no match: ${q}`; }
  });
  body.querySelector('.chips').addEventListener('click', (e) => {
    const rm = e.target.closest('[data-rm]'); if (!rm) return;
    st.cmps.splice(+rm.dataset.rm, 1); st.cmpData.splice(+rm.dataset.rm, 1); persist(); draw();
  });
  await load();
  return { title: `${st.sec.ticker} ${st.sec.yk} GP`, menu: [], resize: draw };
}

// ------------------------------------------------------------------ GC
const CURVE_STYLE = { CURRENT: ['#ffb000', 2, ''], '1W AGO': ['#00e5ff', 1.2, '4 3'], '1M AGO': ['#8a8a8a', 1.2, ''], '1Y AGO': ['#b388ff', 1.2, '2 2'] };
export async function GC(body) {
  const d = await api('/api/curve');
  if (!d.curves.length) {
    body.innerHTML = '<div class="fpad note down">No Treasury curve data (needs FRED_API_KEY).</div>';
    return { title: 'GC', menu: [] };
  }
  const cur = d.curves[0];
  const menu = cur.points.map((p) => ({ label: p.tenor, action: { fn: 'GP', args: { sec: { ticker: `UST${p.tenor}`, yk: 'GOVT', name: `US Treasury ${p.tenor}`, entity: p.entity, metric: 'yield', kind: 'curve' } } } }));
  body.innerHTML = `<div class="gc"><div class="gphead"><span class="white big">GC</span> <span class="dim">US Treasury constant-maturity curve · ${esc(d.source)} · as of</span> <span class="white">${esc(d.anchor)}</span> ${statusBadge(d.status, d.provider_age_s)}</div>
    <div class="gpread">${d.curves.map((c) => `<span style="color:${CURVE_STYLE[c.label][0]}">■ ${c.label} (${c.target})</span>`).join(' ')}</div>
    <div class="gcchart"></div>
    <div class="gctables"><table class="ft"><thead><tr><th>#</th><th>TENOR</th><th class="num">YIELD</th><th class="num">Δ1W</th><th class="num">Δ1M</th><th class="num">Δ1Y</th><th>DATE</th></tr></thead><tbody>
    ${cur.points.map((p, i) => {
      const dv = (k) => { const o = d.curves[k].points[i].value; return p.value != null && o != null ? (p.value - o) * 100 : null; };
      const c = (v) => (v == null ? '<span class="dim">—</span>' : `<span class="${v > 0 ? 'up' : v < 0 ? 'down' : 'dim'}">${v > 0 ? '+' : ''}${v.toFixed(0)}</span>`);
      return `<tr class="click fmi" data-n="${i + 1}"><td class="dim">${i + 1})</td><td class="amber">${p.tenor}</td><td class="num white">${p.value == null ? '—' : p.value.toFixed(3)}</td><td class="num">${c(dv(1))}</td><td class="num">${c(dv(2))}</td><td class="num">${c(dv(3))}</td><td class="dim">${esc(p.date || '—')}</td></tr>`;
    }).join('')}</tbody></table>
    <table class="ft"><thead><tr><th>SPREAD (bp)</th><th class="num">NOW</th><th class="num">1W</th><th class="num">1M</th><th class="num">1Y</th></tr></thead><tbody>
    ${d.spreads.map((s) => `<tr><td class="amber">${s.name}</td>${['CURRENT', '1W AGO', '1M AGO', '1Y AGO'].map((k) => `<td class="num ${k === 'CURRENT' ? 'white' : 'dim'}">${s[k] == null ? '—' : s[k].toFixed(0)}</td>`).join('')}</tr>`).join('')}
    </tbody></table></div>
    <div class="note">Δ in bp. Type a row number + GO to chart that tenor. Yields are FRED H.15 daily closes (end-of-day, ~1 business-day lag).</div></div>`;
  const drawCurve = () => {
    const el = body.querySelector('.gcchart');
    const W = Math.max(300, el.clientWidth), H = Math.max(140, el.clientHeight);
    const P = { l: 40, r: 12, t: 10, b: 20 };
    const vals = d.curves.flatMap((c) => c.points.map((p) => p.value)).filter((v) => v != null);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.08 || 0.2; lo -= pad; hi += pad;
    const n = cur.points.length;
    const X = (i) => P.l + (i / (n - 1)) * (W - P.l - P.r);
    const Y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
    const grid = niceTicks(lo, hi).map((v) => `<line x1="${P.l}" x2="${W - P.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="#1d1d1d"/><text x="${P.l - 4}" y="${Y(v) + 3}" fill="#8a8a8a" font-size="10" text-anchor="end">${v.toFixed(2)}</text>`).join('');
    const xl = cur.points.map((p, i) => `<text x="${X(i)}" y="${H - 5}" fill="#8a8a8a" font-size="10" text-anchor="middle">${p.tenor}</text>`).join('');
    const paths = [...d.curves].reverse().map((c) => {
      const [col, w, dash] = CURVE_STYLE[c.label];
      let s = '', pen = false;
      c.points.forEach((p, i) => { if (p.value == null) { pen = false; return; } s += `${pen ? 'L' : 'M'}${X(i).toFixed(1)},${Y(p.value).toFixed(1)}`; pen = true; });
      return `<path d="${s}" fill="none" stroke="${col}" stroke-width="${w}" ${dash ? `stroke-dasharray="${dash}"` : ''}/>`;
    }).join('');
    const dots = cur.points.map((p, i) => (p.value == null ? '' : `<circle cx="${X(i)}" cy="${Y(p.value)}" r="2.5" fill="#ffb000"/><text x="${X(i)}" y="${Y(p.value) - 6}" fill="#e8e8e8" font-size="9" text-anchor="middle">${p.value.toFixed(2)}</text>`)).join('');
    el.innerHTML = `<svg width="${W}" height="${H}">${grid}${xl}${paths}${dots}</svg>`;
  };
  drawCurve();
  return { title: 'GC  US TREASURY CURVE', menu, resize: drawCurve };
}

// ------------------------------------------------------------------ ECO
function ecoFmt(row, v) {
  if (v == null) return '<span class="dim">—</span>';
  if (row.transform === 'yoy' || row.transform === 'mom') return `${v.toFixed(row.transform === 'mom' ? 1 : 1)}%`;
  if (row.transform === 'chg') return `${v > 0 ? '+' : ''}${fmtNum(v, '', 0)}k`;
  if (row.metric === 'policy_rate' || row.unit === '%') return `${v.toFixed(row.metric === 'policy_rate' ? 2 : 1)}%`;
  return fmtNum(v, '', Math.abs(v) >= 100 ? 0 : 1);
}
const ECO_CLS = { SCHEDULED: 'cyan', RELEASED: 'white', 'AWAITING FRED': 'orange', PAST: 'dim' };
export async function ECO(body) {
  const d = await api('/api/eco');
  const rows = d.rows.filter((r) => r.status !== 'PAST');
  const menu = rows.map((r) => ({ label: r.indicator, action: { fn: 'GP', args: { sec: { ticker: r.series || `${r.entity} RATE`, yk: 'INDEX', name: `${r.indicator}${r.release ? ' — ' + r.release : ''}`, entity: r.entity, metric: r.metric, kind: 'eco' } } } }));
  body.innerHTML = `<div class="fpad"><div class="gphead"><span class="white big">ECO</span> <span class="dim">US economic calendar + central-bank meetings · today ${esc(d.today)}</span></div>
    <table class="ft eco"><thead><tr><th>#</th><th>DATE</th><th>EVENT</th><th>INDICATOR</th><th title="Released rows: period of LATEST. Scheduled rows: period of PRIOR (latest value FRED holds).">REF PER.</th><th class="num">PRIOR</th><th class="num">LATEST</th><th>STATUS</th></tr></thead><tbody>
    ${rows.map((r, i) => `<tr class="click fmi${r.date === d.today ? ' today' : ''}" data-n="${i + 1}"><td class="dim">${i + 1})</td><td class="white">${esc(r.date.slice(5))}</td>
      <td class="amber">${esc(r.release)}${r.calendar_verified === false ? ' <span class="flag" title="config calendar, not verified">CFG</span>' : ''}</td><td>${esc(r.indicator)}</td>
      <td class="dim">${esc((r.status === 'RELEASED' ? r.period : r.prior_period) || '')}</td>
      <td class="num">${ecoFmt(r, r.prior)}</td><td class="num white">${ecoFmt(r, r.latest)}</td>
      <td class="${ECO_CLS[r.status] || ''}" title="${esc(r.fred_updated ? 'FRED series updated ' + r.fred_updated : '')}">${esc(r.status)}</td></tr>`).join('')}
    </tbody></table>
    <div class="note">${esc(d.note)} RELEASED = FRED updated on/after the release date. AWAITING FRED = agency published, FRED not refreshed yet (PRIOR shows the last value FRED holds). Values are latest vintages (revisions included).</div></div>`;
  return { title: 'ECO  ECONOMIC CALENDAR', menu };
}

export const FUNCTIONS = { HELP, MENU, DES, GP, GC, ECO };
export { fmtAge };

// ------------------------------------------------------------------ NEWS (TOP / N / CN)
export const NEWS_TOPICS = ['ALL', 'WAR', 'OIL', 'EQUITIES', 'MARKETS', 'MACRO', 'SHIPPING', 'WORLD'];
const relTime = (iso) => {
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  return s < 3600 ? `${Math.max(1, Math.round(s / 60))}m` : s < 86400 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`;
};
export function newsRow(n, i, { isNew = false, showNum = true } = {}) {
  const chips = n.topics.filter((t) => t !== 'WORLD' && t !== 'MARKETS').map((t) => `<span class="ntopic t-${t}">${t}</span>`).join('')
    + n.tickers.map((t) => `<span class="ntick">${esc(t)}</span>`).join('');
  return `<tr class="click fmi nrow${isNew ? ' new' : ''}" data-n="${i + 1}" data-url="${esc(n.url)}" title="${esc(n.title)}\n${esc(n.source)} · ${esc(n.published)}\nclick to open the article">
    ${showNum ? `<td class="nnum">${i + 1})</td>` : ''}<td class="ntime" title="${esc(n.published)}">${n.published.slice(11, 16)}<br><span>${relTime(n.published)}</span></td>
    <td class="ntitle"><div class="${n.hot ? 'down' : 'white'}">${esc(n.title)}</div><div class="nmeta"><span class="nsrc">${esc(n.source)}</span>${chips}</div></td></tr>`;
}
export async function NEWS(body, args = {}, ctx) {
  const st = { topic: args.topic || 'ALL', ticker: args.ticker || null, q: args.q || '' };
  let rows = [];
  body.innerHTML = `<div class="news"><div class="gpbar"><span class="ntabs">${NEWS_TOPICS.map((t) => `<button data-t="${t}">${t}</button>`).join('')}</span>
    <input class="nq" placeholder="search headlines" spellcheck="false" value="${esc(st.q)}"><span class="ncount dim"></span></div>
    <div class="fbody2"><table class="ft"><tbody></tbody></table></div>
    <div class="note">Headlines, source and time from the publishers' RSS feeds; click (or n &lt;GO&gt;) opens the article. Tags are keyword-based. Live: new headlines flash.</div></div>`;
  const $ = (s) => body.querySelector(s);
  const qs = () => new URLSearchParams({ hours: 72, limit: 300, ...(st.topic !== 'ALL' ? { topic: st.topic } : {}),
    ...(st.ticker ? { ticker: st.ticker } : {}), ...(st.q ? { q: st.q } : {}) });
  const render = (newIds = new Set()) => {
    body.querySelectorAll('.ntabs button').forEach((b) => b.classList.toggle('on', b.dataset.t === st.topic && !st.ticker));
    $('tbody').innerHTML = rows.map((n, i) => newsRow(n, i, { isNew: newIds.has(n.id) })).join('')
      || '<tr><td class="dim">No headlines match (feeds poll every 2 min).</td></tr>';
    $('.ncount').textContent = `${rows.length} headlines · 72h${st.ticker ? ' · ' + st.ticker : ''}`;
    menu.length = 0;
    rows.forEach((n) => menu.push({ label: n.title, action: { fn: '@url', args: { url: n.url } } }));
  };
  const menu = [];
  const load = async () => { rows = await api(`/api/news?${qs()}`); render(); };
  body.querySelector('.ntabs').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    st.topic = b.dataset.t; st.ticker = null; ctx.persist({ ...st }); load();
  });
  let qt;
  $('.nq').addEventListener('keydown', (e) => e.stopPropagation());
  $('.nq').addEventListener('input', (e) => { clearTimeout(qt); qt = setTimeout(() => { st.q = e.target.value.trim(); ctx.persist({ ...st }); load(); }, 300); });
  const matches = (n) => (st.topic === 'ALL' || n.topics.includes(st.topic)) && (!st.ticker || n.tickers.includes(st.ticker))
    && (!st.q || n.title.toLowerCase().includes(st.q.toLowerCase()));
  const onLive = (ev) => {
    const add = ev.detail.filter(matches).filter((n) => !rows.find((r) => r.id === n.id));
    if (!add.length) return;
    rows = [...add, ...rows].slice(0, 300);
    render(new Set(add.map((n) => n.id)));
  };
  window.addEventListener('riotto:news', onLive);
  await load();
  return { title: st.ticker ? `${st.ticker} CN  COMPANY NEWS` : `TOP  NEWS${st.topic !== 'ALL' ? ' · ' + st.topic : ''}`, menu,
    cleanup: () => window.removeEventListener('riotto:news', onLive) };
}
FUNCTIONS.NEWS = NEWS;
FUNCTIONS.CN = (body, args, ctx) => NEWS(body, { ticker: args.sec ? args.sec.entity.replace(/^EQ:/, '') : args.ticker }, ctx);

// ------------------------------------------------------------------ ABOUT (credits & disclaimer)
export async function ABOUT(body) {
  const c = await api('/api/credits');
  body.innerHTML = `<div class="fpad about">
    <div class="sechead"><span class="white big">${esc(c.project)}</span><span class="dim">by ${esc(c.author)}</span></div>
    <div class="note white">${esc(c.summary)}</div>
    <div class="sub">DISCLAIMER</div><div class="note">${esc(c.disclaimer)}</div>
    ${c.public_mode && c.hidden_series.length ? `<div class="note">Not shown on this public deployment for licence reasons: ${esc(c.hidden_series.join(', '))}.</div>` : ''}
    <div class="sub">DATA SOURCES</div>
    <table class="ft"><tbody>${c.sources.map((s) => `<tr><td class="amber wrap" style="width:34%">${esc(s.name)}</td>
      <td class="wrap">${esc(s.use)}${s.note ? `<div class="dim">${esc(s.note)}</div>` : ''}<div>${/^https?:/.test(s.terms) ? `<a href="${esc(s.terms.split(' ')[0])}" target="_blank" rel="noopener noreferrer">${esc(s.terms)}</a>` : `<span class="dim">${esc(s.terms)}</span>`}</div></td></tr>`).join('')}</tbody></table>
    <div class="sub">SOFTWARE</div><div class="note">${c.software.map((x) => `${esc(x.name)} (${esc(x.license)})`).join(' · ')}</div></div>`;
  return { title: 'ABOUT  CREDITS & DISCLAIMER', menu: [] };
}
FUNCTIONS.ABOUT = ABOUT;
