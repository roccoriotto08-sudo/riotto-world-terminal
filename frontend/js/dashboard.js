// Dashboard: workspace tabs + a draggable / resizable / closable panel grid (GridStack).
//
// Panel kinds
//   singleton  - one shared live component (globe, KPI monitor, inspector, wire, tables). Its DOM is
//                moved between the grid and a hidden "park" so app.js keeps one copy of the state.
//   function   - any number of terminal-function hosts (NEWS, GP, GC, ECO, DES, MENU, HELP, CN),
//                each with its own back-stack and numbered menu.
// Layouts persist per viewer in localStorage (best effort; the app works without it).
import { esc } from './util.js';
import { FUNCTIONS } from './functions.js';

const KEY = 'riotto.dashboard.v1';
const GS = window.GridStack.GridStack || window.GridStack;   // UMD exposes the class itself

export const SINGLETONS = {
  globe:       { title: 'GLOBE', el: 'w-globe', w: 14, h: 14 },
  kpi:         { title: 'MONITOR', el: 'w-kpi', w: 24, h: 3 },
  inspector:   { title: 'INSPECTOR', el: 'w-inspector', w: 8, h: 12 },
  wire:        { title: 'WIRE · EVENTS & NEWS', el: 'w-wire', w: 14, h: 7 },
  production:  { title: 'CRUDE OUTPUT', el: 'w-production', w: 8, h: 9 },
  chokepoints: { title: 'CHOKEPOINTS', el: 'w-chokepoints', w: 8, h: 7 },
  commodities: { title: 'COMMODITIES', el: 'w-commodities', w: 7, h: 8 },
  portfolio:   { title: 'PORTFOLIO', el: 'w-portfolio', w: 7, h: 8 },
  tankers:     { title: 'TANKERS · LIVE AIS', el: 'w-tankers', w: 9, h: 9 },
};

// "+ PANEL" menu
export const CATALOG = [
  { group: 'MAP & DATA', items: [['globe', 'Globe'], ['kpi', 'KPI monitor'], ['inspector', 'Inspector (selection detail)'],
    ['wire', 'Wire: events & news'], ['tankers', 'Tankers (live AIS)'], ['chokepoints', 'Chokepoints'], ['production', 'Crude output'],
    ['commodities', 'Commodities'], ['portfolio', 'Portfolio']] },
  { group: 'NEWS', items: [['fn:NEWS:ALL', 'Top news'], ['fn:NEWS:WAR', 'News · War'], ['fn:NEWS:OIL', 'News · Oil'],
    ['fn:NEWS:EQUITIES', 'News · US equities'], ['fn:NEWS:MACRO', 'News · Macro'], ['fn:NEWS:SHIPPING', 'News · Shipping']] },
  { group: 'FUNCTIONS', items: [['fn:GP:CL1', 'Chart · WTI (GP)'], ['fn:GP:SPX', 'Chart · S&P 500 (GP)'], ['fn:GP:USGG10YR', 'Chart · UST 10Y (GP)'],
    ['fn:GC', 'Yield curve (GC)'], ['fn:ECO', 'Economic calendar (ECO)'], ['fn:ABOUT', 'About & data credits'], ['fn:HELP', 'Help'],
    ['fn:EMPTY', 'Empty function panel']] },
];
// licence-restricted series are not served on a public deployment (see /api/credits)
const PUBLIC_HIDDEN = new Set(['fn:GP:SPX']);

const sec = (ticker, yk, name, entity, metric, kind) => ({ ticker, yk, name, entity, metric, kind });
const SEC = {
  CL1: sec('CL1', 'COMDTY', 'WTI crude (FRED spot close)', 'WTI', 'price', 'kpi'),
  SPX: sec('SPX', 'INDEX', 'S&P 500', 'SPX', 'level', 'kpi'),
  USGG10YR: sec('USGG10YR', 'GOVT', 'US Treasury 10Y (constant maturity)', 'UST10Y', 'yield', 'curve'),
  CO1: sec('CO1', 'COMDTY', 'Brent crude (FRED spot close)', 'BRENT', 'price', 'kpi'),
};
const fnPanel = (fn, args, x, y, w, h) => ({ type: 'fn', fn, args, x, y, w, h });
const one = (type, x, y, w, h) => ({ type, x, y, w, h });

// Default workspaces (24 columns x 24 rows fill the screen)
export const DEFAULT_TABS = [
  { name: 'MAIN', items: [one('kpi', 0, 0, 24, 3), one('globe', 0, 3, 15, 14), one('inspector', 15, 3, 9, 14),
    one('wire', 0, 17, 15, 7), fnPanel('NEWS', { topic: 'ALL' }, 15, 17, 9, 7)] },
  { name: 'MARKETS', items: [one('kpi', 0, 0, 24, 3), fnPanel('GP', { sec: SEC.USGG10YR, win: '1Y' }, 0, 3, 12, 11),
    fnPanel('GC', {}, 12, 3, 12, 11), fnPanel('ECO', {}, 0, 14, 12, 10), fnPanel('NEWS', { topic: 'EQUITIES' }, 12, 14, 12, 10)] },
  { name: 'ENERGY', items: [one('globe', 0, 0, 14, 15), one('tankers', 14, 0, 10, 8), one('chokepoints', 14, 8, 10, 7),
    one('production', 0, 15, 8, 9), fnPanel('NEWS', { topic: 'OIL' }, 8, 15, 9, 9),
    fnPanel('GP', { sec: SEC.CO1, win: '1Y', cmps: [SEC.CL1] }, 17, 15, 7, 9)] },
  { name: 'GEO', items: [one('globe', 0, 0, 16, 16), one('inspector', 16, 0, 8, 16), one('wire', 0, 16, 12, 8),
    fnPanel('NEWS', { topic: 'WAR' }, 12, 16, 12, 8)] },
];

let uid = 0;
const nid = () => `p${Date.now().toString(36)}${(uid++).toString(36)}`;

export class Dashboard {
  constructor(root, park, hooks) {
    this.root = root;
    this.park = park;
    this.hooks = hooks;   // onMessage, onSpecial(fn,args), onFocus(panel|null), onSingleton(type, shown)
    this.panels = new Map();
    this.focusId = null;
    this.maxId = null;
    this.locked = false;
    this.state = this._load();
    this.grid = GS.init({ column: 24, cellHeight: 30, margin: 3, float: false, animate: false,
      draggable: { handle: '.ph-drag' }, resizable: { handles: 'e,se,s,sw,w' }, minRow: 1 }, root);
    this.grid.on('change', () => this._saveLayout());
    this._fitRows = this._fitRows.bind(this);
    window.addEventListener('resize', this._fitRows);
    root.addEventListener('mousedown', (e) => {
      const item = e.target.closest('.grid-stack-item');
      if (item) this.focus(item.dataset.pid);
    });
  }

  // ------------------------------------------------------------ persistence
  _load() {
    let s = null;
    try { s = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (_) { s = null; }
    if (!s || !Array.isArray(s.tabs) || !s.tabs.length) {
      s = { active: 0, locked: false, tabs: DEFAULT_TABS.map((t) => ({ id: nid(), name: t.name, items: JSON.parse(JSON.stringify(t.items)) })) };
    }
    return s;
  }
  _persist() {
    try { localStorage.setItem(KEY, JSON.stringify(this.state)); } catch (_) { /* storage unavailable */ }
  }
  _saveLayout() {
    if (this._switching) return;
    const tab = this.state.tabs[this.state.active];
    if (!tab) return;
    const pos = Object.fromEntries((this.grid.save(false) || []).map((n) => [n.id, n]));
    tab.items = [...this.panels.values()].map((p) => {
      const n = pos[p.id] || {};
      const base = { type: p.type, x: n.x ?? p.x, y: n.y ?? p.y, w: n.w ?? p.w, h: n.h ?? p.h };
      if (p.type === 'fn') {
        const top = p.hist[p.hist.length - 1];
        Object.assign(base, top ? { fn: top.fn, args: top.args } : { fn: 'EMPTY', args: {} });
      }
      return base;
    });
    this._persist();
  }

  // ------------------------------------------------------------ tabs
  start() {
    this.setLocked(!!this.state.locked);
    this._renderTabs();
    this._buildTab(this.state.active);
    this._fitRows();
  }
  _renderTabs() {
    const el = document.getElementById('tabs');
    el.innerHTML = this.state.tabs.map((t, i) => `<div class="tab${i === this.state.active ? ' on' : ''}" data-i="${i}" title="Click: open · double-click: rename">
      <span class="tn">${esc(t.name)}</span>${this.state.tabs.length > 1 ? `<span class="tx" data-del="${i}" title="Delete workspace">×</span>` : ''}</div>`).join('');
  }
  bindTabbar() {
    const tabs = document.getElementById('tabs');
    tabs.addEventListener('click', (e) => {
      const del = e.target.closest('[data-del]');
      if (del) { e.stopPropagation(); this._armDelete(del, +del.dataset.del); return; }
      const t = e.target.closest('.tab');
      if (t && !t.querySelector('.tn[contenteditable="true"]')) this.switchTab(+t.dataset.i);
    });
    tabs.addEventListener('dblclick', (e) => { const t = e.target.closest('.tab'); if (t) this.renameTab(+t.dataset.i); });
    document.getElementById('tab-add').addEventListener('click', () => this.addTab());
    document.getElementById('lock-btn').addEventListener('click', () => this.setLocked(!this.locked));
    document.getElementById('reset-btn').addEventListener('click', () => this.resetTab());
    const menu = document.getElementById('panel-menu');
    const show = ([k]) => !(window.RIOTTO_PUBLIC && PUBLIC_HIDDEN.has(k));
    menu.innerHTML = CATALOG.map((g) => `<div class="mg">${g.group}</div>` + g.items.filter(show).map(([k, l]) => `<div class="mi" data-k="${k}">${esc(l)}</div>`).join('')).join('');
    document.getElementById('panel-add').addEventListener('click', (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; this._markMenu(); });
    menu.addEventListener('click', (e) => { const mi = e.target.closest('.mi'); if (mi) { this.addFromCatalog(mi.dataset.k); menu.hidden = true; } });
    document.addEventListener('click', (e) => { if (!e.target.closest('#add-wrap')) menu.hidden = true; });
  }
  _markMenu() {
    const on = new Set([...this.panels.values()].map((p) => p.type));
    document.querySelectorAll('#panel-menu .mi').forEach((m) => {
      const k = m.dataset.k;
      m.classList.toggle('placed', SINGLETONS[k] && on.has(k));
    });
  }
  switchTab(i) {
    if (i === this.state.active || !this.state.tabs[i]) return;
    this._saveLayout();
    this.state.active = i;
    this._persist();
    this._renderTabs();
    this._buildTab(i);
  }
  addTab(name) {
    this._saveLayout();
    this.state.tabs.push({ id: nid(), name: (name || `WORKSPACE ${this.state.tabs.length + 1}`).toUpperCase().slice(0, 18), items: [] });
    this.state.active = this.state.tabs.length - 1;
    this._persist();
    this._renderTabs();
    this._buildTab(this.state.active);
    this.hooks.onMessage('New workspace: name it, then + PANEL (top right) or type a function, e.g. TOP <GO>');
    if (!name) this.renameTab(this.state.active);
  }
  // inline rename: the tab label becomes editable; Enter saves, Esc cancels
  renameTab(i) {
    const tabEl = document.querySelector(`#tabs .tab[data-i="${i}"] .tn`);
    if (!tabEl) return;
    const before = this.state.tabs[i].name;
    tabEl.contentEditable = 'true';
    tabEl.focus();
    document.getSelection().selectAllChildren(tabEl);
    const done = (save) => {
      tabEl.removeEventListener('keydown', onKey);
      tabEl.removeEventListener('blur', onBlur);
      const v = tabEl.textContent.trim().toUpperCase().slice(0, 18);
      this.state.tabs[i].name = save && v ? v : before;
      this._persist();
      this._renderTabs();
    };
    const onKey = (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); done(true); }
      if (e.key === 'Escape') { e.preventDefault(); done(false); }
    };
    const onBlur = () => done(true);
    tabEl.addEventListener('keydown', onKey);
    tabEl.addEventListener('blur', onBlur);
  }
  // two-step delete: first click arms (shows DELETE?), second click within 3 s deletes
  _armDelete(el, i) {
    if (el.classList.contains('armed')) { this.deleteTab(i); return; }
    el.classList.add('armed');
    el.textContent = 'DELETE?';
    setTimeout(() => { if (el.isConnected) { el.classList.remove('armed'); el.textContent = '×'; } }, 3000);
  }
  deleteTab(i) {
    if (this.state.tabs.length < 2) return;
    const wasActive = i === this.state.active;
    this.state.tabs.splice(i, 1);
    if (this.state.active >= this.state.tabs.length || wasActive) this.state.active = Math.max(0, Math.min(i, this.state.tabs.length - 1));
    else if (i < this.state.active) this.state.active -= 1;
    this._persist();
    this._renderTabs();
    if (wasActive) this._buildTab(this.state.active, true);
  }
  resetTab() {
    const tab = this.state.tabs[this.state.active];
    const def = DEFAULT_TABS.find((d) => d.name === tab.name);
    const b = document.getElementById('reset-btn');
    if (!b.classList.contains('armed')) {           // two-step, like tab delete
      b.classList.add('armed');
      b.textContent = def ? 'RESET LAYOUT?' : 'CLEAR PANELS?';
      setTimeout(() => { b.classList.remove('armed'); b.textContent = 'RESET'; }, 3000);
      return;
    }
    b.classList.remove('armed'); b.textContent = 'RESET';
    tab.items = def ? JSON.parse(JSON.stringify(def.items)) : [];
    this._persist();
    this._buildTab(this.state.active, true);
  }
  setLocked(on) {
    this.locked = on;
    this.state.locked = on;
    this.grid.setStatic(on);
    document.body.classList.toggle('locked', on);
    const b = document.getElementById('lock-btn');
    if (b) { b.textContent = on ? 'LOCKED' : 'UNLOCKED'; b.classList.toggle('on', on); }
    this._persist();
  }

  _buildTab(i, skipSave) {
    this._switching = true;
    this.unmaximize();
    for (const p of [...this.panels.values()]) this._destroy(p);
    this.grid.removeAll(true);
    this.panels.clear();
    this.focusId = null;
    const tab = this.state.tabs[i];
    this.grid.batchUpdate();
    for (const it of tab.items) this._create(it);
    this.grid.batchUpdate(false);
    this._switching = false;
    const shown = new Set([...this.panels.values()].map((p) => p.type));
    for (const t of Object.keys(SINGLETONS)) this.hooks.onSingleton(t, shown.has(t));
    const firstFn = [...this.panels.values()].find((p) => p.type === 'fn');
    this.focus(firstFn ? firstFn.id : null);
    if (!skipSave) this._saveLayout();
    this._fitRows();
  }

  // ------------------------------------------------------------ panels
  _create(it) {
    const id = nid();
    const isFn = it.type === 'fn';
    const meta = SINGLETONS[it.type];
    if (!isFn && !meta) return null;
    const item = document.createElement('div');
    item.className = 'grid-stack-item';
    item.dataset.pid = id;
    item.innerHTML = `<div class="grid-stack-item-content panel ${isFn ? 'fnp' : 'sgp'}">
      <div class="phd ph-drag"><span class="pt">${esc(isFn ? '…' : meta.title)}</span><span class="pfn"></span>
        <span class="pctl">${isFn ? '<button class="pb" title="Back (MENU / Esc)">◀</button>' : ''}<button class="pm" title="Maximize (double-click header) · Esc restores">⤢</button><button class="pc" title="Close panel">×</button></span></div>
      <div class="pbody"></div></div>`;
    const p = { id, type: it.type, el: item, body: item.querySelector('.pbody'), titleEl: item.querySelector('.pt'),
      fnEl: item.querySelector('.pfn'), hist: [], menu: [], cleanup: null, resize: null, seq: 0,
      x: it.x, y: it.y, w: it.w || (meta ? meta.w : 9), h: it.h || (meta ? meta.h : 9) };
    this.panels.set(id, p);
    item.querySelector('.pc').addEventListener('click', (e) => { e.stopPropagation(); this.remove(id); });
    item.querySelector('.pm').addEventListener('click', (e) => { e.stopPropagation(); this.toggleMax(id); });
    item.querySelector('.phd').addEventListener('dblclick', (e) => { if (!e.target.closest('button')) this.toggleMax(id); });
    if (isFn) {
      item.querySelector('.pb').addEventListener('click', (e) => { e.stopPropagation(); this.back(id); });
      p.body.addEventListener('click', (e) => {
        const row = e.target.closest('.fmi[data-n]');
        if (!row) return;
        if (row.dataset.url) { window.open(row.dataset.url, '_blank', 'noopener'); return; }
        if (!e.target.closest('a,button,input')) this.select(+row.dataset.n, id);
      });
    } else {
      // first move any other placement of this singleton back to the park (one live copy)
      for (const other of this.panels.values()) if (other !== p && other.type === it.type) this.remove(other.id, true);
      p.body.appendChild(document.getElementById(meta.el));
    }
    let rt;
    new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(() => p.resize && p.resize(), 120); }).observe(p.body);
    const opts = { id, w: p.w, h: p.h, minW: 3, minH: 2 };
    if (it.x != null) { opts.x = it.x; opts.y = it.y; } else opts.autoPosition = true;
    this.grid.makeWidget(item, opts);
    if (isFn) {
      if (it.fn && it.fn !== 'EMPTY') this.run(p, it.fn, it.args || {}, true);
      else this._empty(p);
    }
    return p;
  }
  _empty(p) {
    p.titleEl.textContent = 'FUNCTION';
    p.fnEl.textContent = '';
    p.body.innerHTML = `<div class="empty"><div>Empty panel</div><div class="dim">Click here, then type a function: <b>TOP</b> · <b>N OIL</b> · <b>CL1 COMDTY GP</b> · <b>GC</b> · <b>ECO</b> · <b>HELP</b></div></div>`;
  }
  _destroy(p) {
    if (p.cleanup) { try { p.cleanup(); } catch (_) { /* ignore */ } }
    if (p.type !== 'fn') this.park.appendChild(document.getElementById(SINGLETONS[p.type].el));
  }
  remove(id, quiet) {
    const p = this.panels.get(id);
    if (!p) return;
    if (this.maxId === id) this.unmaximize();
    this._destroy(p);
    this.grid.removeWidget(p.el, true);
    this.panels.delete(id);
    if (this.focusId === id) this.focus(null);
    if (p.type !== 'fn' && !quiet) this.hooks.onSingleton(p.type, false);
    this._saveLayout();
  }
  addFromCatalog(k) {
    if (SINGLETONS[k]) {
      const existing = [...this.panels.values()].find((p) => p.type === k);
      if (existing) { this.focus(existing.id); this.flash(existing); return; }
      const p = this._create({ type: k });
      this.hooks.onSingleton(k, true);
      this._saveLayout();
      return p;
    }
    const [, fn, arg] = k.split(':');
    const args = fn === 'NEWS' ? { topic: arg } : fn === 'GP' ? { sec: SEC[arg], win: '1Y' } : {};
    const p = this._create({ type: 'fn', fn, args, w: fn === 'NEWS' ? 9 : 10, h: 9 });
    this.focus(p.id);
    this._saveLayout();
    p.el.scrollIntoView({ block: 'nearest' });
    return p;
  }
  addSingleton(type) { return this.addFromCatalog(type); }
  flash(p) { p.el.classList.remove('flash'); void p.el.offsetWidth; p.el.classList.add('flash'); }

  focus(id) {
    this.focusId = id && this.panels.has(id) ? id : null;
    for (const p of this.panels.values()) p.el.classList.toggle('focus', p.id === this.focusId);
    const p = this.focusId && this.panels.get(this.focusId);
    this.hooks.onFocus(p && p.type === 'fn' ? p : null);
  }

  toggleMax(id) { if (this.maxId === id) this.unmaximize(); else this.maximize(id); }
  maximize(id) {
    this.unmaximize();
    const p = this.panels.get(id);
    if (!p) return;
    this.maxId = id;
    p.el.classList.add('maxed');
    document.body.classList.add('has-max');
    this.grid.movable(p.el, false);
    this.grid.resizable(p.el, false);
    this.focus(id);
  }
  unmaximize() {
    const p = this.maxId && this.panels.get(this.maxId);
    this.maxId = null;
    document.body.classList.remove('has-max');
    if (!p) return false;
    p.el.classList.remove('maxed');
    if (!this.locked) { this.grid.movable(p.el, true); this.grid.resizable(p.el, true); }
    return true;
  }

  // Fill the viewport: 24 rows of cells by default.
  _fitRows() {
    // measure the scroll container, not the grid (GridStack sizes the grid element itself)
    const h = this.root.parentElement.clientHeight || window.innerHeight - 100;
    this.grid.cellHeight(Math.max(18, Math.floor((h - 4) / 24)));
  }

  // ------------------------------------------------------------ terminal functions
  // Commands open in the focused function panel; otherwise a new panel is added.
  async open(fn, args = {}) {
    if (fn.startsWith('@')) return this.hooks.onSpecial(fn, args);
    let p = this.focusId && this.panels.get(this.focusId);
    if (!p || p.type !== 'fn') {
      p = this._create({ type: 'fn', w: fn === 'NEWS' ? 9 : 10, h: 9 });
      this.focus(p.id);
      p.el.scrollIntoView({ block: 'nearest' });
    }
    return this.run(p, fn, args, true);
  }
  async run(p, fn, args, push) {
    const render = FUNCTIONS[fn];
    if (!render) { this.hooks.onMessage(`${fn}: unknown function`); return; }
    const seq = ++p.seq;
    if (p.cleanup) { try { p.cleanup(); } catch (_) { /* ignore */ } p.cleanup = null; }
    p.titleEl.textContent = '…';
    p.fnEl.textContent = fn;
    p.body.innerHTML = '<div class="fpad note">loading…</div>';
    const entry = { fn, args: JSON.parse(JSON.stringify(args || {})) };
    if (push) { p.hist.push(entry); if (p.hist.length > 30) p.hist.shift(); }
    const ctx = { persist: (a) => { entry.args = JSON.parse(JSON.stringify(a)); this._saveLayout(); } };
    try {
      const out = await render(p.body, args, ctx);
      if (seq !== p.seq) { if (out && out.cleanup) out.cleanup(); return; }
      p.titleEl.textContent = out.title || fn;
      p.menu = out.menu || [];
      p.resize = out.resize || null;
      p.cleanup = out.cleanup || null;
    } catch (e) {
      if (seq !== p.seq) return;
      p.titleEl.textContent = `${fn} · ERROR`;
      p.body.innerHTML = `<div class="fpad down">${esc(e.message)}</div>`;
      p.menu = []; p.resize = null;
    }
    this._saveLayout();
  }
  hasMenu() { const p = this.focusId && this.panels.get(this.focusId); return !!(p && p.type === 'fn' && p.menu.length); }
  select(n, id = this.focusId) {
    const p = this.panels.get(id);
    if (!p) return;
    const item = p.menu[n - 1];
    if (!item) { this.hooks.onMessage(`${n}: no such menu item`); return; }
    this.focus(id);
    if (item.action.fn.startsWith('@')) return this.hooks.onSpecial(item.action.fn, item.action.args);
    this.run(p, item.action.fn, item.action.args, true);
  }
  back(id = this.focusId) {
    const p = id && this.panels.get(id);
    if (!p || p.type !== 'fn' || p.hist.length < 2) return false;
    p.hist.pop();
    const prev = p.hist[p.hist.length - 1];
    this.run(p, prev.fn, prev.args, false);
    return true;
  }
}
