// Formatting, sparklines, colour scales and the API client. No data is ever produced here.

export const $ = (s, r = document) => r.querySelector(s);
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export async function api(path) {
  const r = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (_) { /* not JSON */ }
    throw new Error(`${r.status} ${detail}`);
  }
  return r.json();
}

export function connectStream(handlers, onState) {
  let es, retry = 1000;
  const open = () => {
    es = new EventSource('/api/stream');
    es.addEventListener('hello', () => { retry = 1000; onState && onState(true); });
    for (const [k, fn] of Object.entries(handlers)) {
      es.addEventListener(k, (ev) => { try { fn(JSON.parse(ev.data)); } catch (e) { console.error(k, e); } });
    }
    es.onerror = () => {
      onState && onState(false);
      es.close();
      setTimeout(open, retry);
      retry = Math.min(retry * 2, 30000);   // exponential backoff on reconnect
    };
  };
  open();
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// ------------------------------------------------------------------ numbers
export function fmtNum(v, unit = '', dp) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const a = Math.abs(v);
  if (dp === undefined) {
    if (unit === 'count' || unit === 'bp') dp = 0;
    else if (unit === '%' || unit === '% GDP' || unit === 'pp') dp = 2;
    else if (a === 0) dp = 2;
    else if (a >= 10000) dp = 0;
    else if (a >= 1000) dp = 1;
    else if (a >= 100) dp = 2;
    else if (a >= 1) dp = 2;
    else dp = 4;
  }
  return v.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

export function fmtChg(v, kind) {
  if (v === null || v === undefined) return '<span class="dim">—</span>';
  const cls = v > 0 ? 'up' : v < 0 ? 'down' : 'dim';
  const s = kind === 'bp' ? `${v > 0 ? '+' : ''}${v.toFixed(0)}bp` : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
  return `<span class="${cls}">${s}</span>`;
}

export function fmtAge(sec) {
  if (sec === null || sec === undefined) return '—';
  if (sec < 90) return `${sec}s`;
  if (sec < 5400) return `${Math.round(sec / 60)}m`;
  if (sec < 172800) return `${Math.round(sec / 3600)}h`;
  return `${Math.round(sec / 86400)}d`;
}

export function ageOf(iso) {
  if (!iso) return null;
  const t = Date.parse(iso.length === 10 ? iso + 'T00:00:00Z' : iso);
  return Number.isNaN(t) ? null : Math.max(0, Math.round((Date.now() - t) / 1000));
}

export function shortDate(iso) {
  if (!iso) return '—';
  if (iso.length > 10 && iso.includes('T')) return iso.slice(5, 16).replace('T', ' ');
  return iso.slice(0, 10);
}

export function statusBadge(status, ageS) {
  if (!status) return '';
  const age = status === 'STALE' && ageS != null ? ` ${fmtAge(ageS)}` : '';
  return `<span class="badge b-${status}" title="${status === 'STALE' ? 'Feed failing: last good value shown; age since last successful fetch' : status}">${status}${age}</span>`;
}

export function flagsHtml(flags = []) {
  const show = flags.filter((f) => /^(DERIVED|INVERTED|UNASSESSED|MANUAL|DEMO|REVISED|ESTIMATE|OLD|EURO_AREA|LEVEL_SHIFT_CONFIRMED|EOD)$/.test(f));
  return show.map((f) => `<span class="flag" title="${esc(flagHelp[f] || f)}">${f.replace('_CONFIRMED', '')}</span>`).join('');
}
const flagHelp = {
  DERIVED: 'Calculated from stored observations (see basis)', INVERTED: '1/x of the published quote',
  UNASSESSED: 'JODI assessment code 3: not assessed for comparability', MANUAL: 'Entered by hand in config',
  DEMO: 'SYNTHETIC demo data', REVISED: 'Provider revised this value after first publication',
  ESTIMATE: 'Model estimate (EIA STEO), not a reported figure', OLD: 'Latest available value is old for its cadence',
  EURO_AREA: 'Euro-area aggregate applied to member', LEVEL_SHIFT: 'Large move accepted after a confirming print', EOD: 'End-of-day print',
};

// ---------------------------------------------------------------- sparkline
export function spark(points, w = 100, h = 16, opts = {}) {
  const vals = (points || []).map((p) => (Array.isArray(p) ? p[1] : p)).filter((v) => v !== null && v !== undefined);
  if (vals.length < 2) return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"></svg>`;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi === lo) { hi += 1; lo -= 1; }
  const step = w / (vals.length - 1);
  const d = vals.map((v, i) => `${i ? 'L' : 'M'}${(i * step).toFixed(1)},${(h - 1 - ((v - lo) / (hi - lo)) * (h - 2)).toFixed(1)}`).join('');
  const col = opts.color || (vals[vals.length - 1] >= vals[0] ? 'var(--up)' : 'var(--down)');
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="${d}" fill="none" stroke="${col}" stroke-width="1" vector-effect="non-scaling-stroke"/></svg>`;
}

export function bigChart(points, unit, opts = {}) {
  const W = 340, H = 150, P = { l: 4, r: 52, t: 8, b: 16 };
  const pts = (points || []).filter((p) => p[1] !== null);
  if (pts.length < 2) return '<div class="note">Not enough history stored yet.</div>';
  const vals = pts.map((p) => p[1]);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi === lo) { hi += 1; lo -= 1; }
  const x = (i) => P.l + (i / (pts.length - 1)) * (W - P.l - P.r);
  const y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join('');
  const grid = [lo, (lo + hi) / 2, hi].map((v) => `<line x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}" stroke="#222"/><text x="${W - P.r + 3}" y="${y(v) + 3}" fill="#8a8a8a" font-size="9">${fmtNum(v, unit)}</text>`).join('');
  const zero = lo < 0 && hi > 0 ? `<line x1="${P.l}" x2="${W - P.r}" y1="${y(0)}" y2="${y(0)}" stroke="#555" stroke-dasharray="2 2"/>` : '';
  const col = opts.color || 'var(--amber)';
  return `<svg viewBox="0 0 ${W} ${H}" font-family="inherit">${grid}${zero}<path d="${d}" fill="none" stroke="${col}" stroke-width="1.2"/>
    <text x="${P.l}" y="${H - 3}" fill="#8a8a8a" font-size="9">${esc(pts[0][0].slice(0, 10))}</text>
    <text x="${W - P.r}" y="${H - 3}" fill="#8a8a8a" font-size="9" text-anchor="end">${esc(pts[pts.length - 1][0].slice(0, 10))}</text></svg>`;
}

// ------------------------------------------------------------------ colours
const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const toHex = (c) => '#' + c.map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0')).join('');
const lerp = (a, b, t) => toHex(hex(a).map((v, i) => v + (hex(b)[i] - v) * t));
function ramp(stops, t) {
  t = Math.max(0, Math.min(1, t));
  const n = stops.length - 1, i = Math.min(n - 1, Math.floor(t * n));
  return lerp(stops[i], stops[i + 1], t * n - i);
}
const SEQ = ['#1a1200', '#5c3d00', '#a86e00', '#ffb000', '#ffe1a0'];
const DIV_NEG = ['#2a2a2a', '#7a1f1a', '#ff3b30'];
const DIV_POS = ['#2a2a2a', '#1d6b2a', '#2ecc40'];

export function scaleColor(meta, v) {
  if (v === null || v === undefined) return '#0d0d0d';
  const [lo, hi] = meta.domain;
  if (meta.scale === 'diverging') {
    const mid = meta.mid ?? 0;
    if (v >= mid) return ramp(DIV_POS, (v - mid) / (hi - mid || 1));
    return ramp(DIV_NEG, (mid - v) / (mid - lo || 1));
  }
  return ramp(SEQ, (v - lo) / (hi - lo || 1));
}

export function legendGradient(meta) {
  const n = 12, stops = [];
  for (let i = 0; i <= n; i++) {
    const v = meta.domain[0] + (i / n) * (meta.domain[1] - meta.domain[0]);
    stops.push(`${scaleColor(meta, v)} ${(i / n) * 100}%`);
  }
  return `linear-gradient(90deg, ${stops.join(',')})`;
}

export const SEV_COLOR = { 1: '#555555', 2: '#a87400', 3: '#ffb000', 4: '#ff7a00', 5: '#ff3b30' };
export const STATE_COLOR = { DISRUPTED: '#ff3b30', ELEVATED: '#ffb000', NORMAL: '#00e5ff', 'NO BASELINE': '#8a8a8a', 'NO DATA': '#555555' };
export const SITE_COLOR = { refinery: '#ff7a00', lng_export: '#00e5ff', lng_import: '#4fc3f7', spr: '#b388ff', terminal: '#ffd54f', hub: '#e8e8e8' };
