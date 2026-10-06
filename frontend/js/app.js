// Riotto World Terminal front-end. Consumes ONLY the backend's normalized API (/api/*).
import { GlobeView, TANKER_COLOR } from './globe.js';
import { newsRow, YELLOW_KEYS } from './functions.js';
import { Dashboard } from './dashboard.js';
import {
  $, api, ageOf, bigChart, connectStream, debounce, esc, flagsHtml, fmtAge, fmtChg, fmtNum, legendGradient,
  SEV_COLOR, shortDate, SITE_COLOR, spark, STATE_COLOR, statusBadge,
} from './util.js';

const SHORT = { oil: 'OUTPUT', choke: 'CHOKE', infra: 'INFRA', macro: 'CB', comm: 'HUBS', geo: 'EVENTS', port: 'PORTF', tank: 'TANKERS' };
const LAYERS = [
  { k: 1, id: 'oil', name: 'OIL & GAS OUTPUT' },
  { k: 2, id: 'choke', name: 'CHOKEPOINTS + LANES' },
  { k: 3, id: 'infra', name: 'INFRASTRUCTURE' },
  { k: 4, id: 'macro', name: 'MACRO / CENTRAL BANKS' },
  { k: 5, id: 'comm', name: 'COMMODITY HUBS' },
  { k: 6, id: 'geo', name: 'GEOPOLITICAL EVENTS' },
  { k: 7, id: 'port', name: 'PORTFOLIO OVERLAY' },
  { k: 8, id: 'tank', name: 'TANKERS (LIVE AIS)' },
];
const WINDOWS = [['LIVE', 0], ['1M', 30], ['3M', 91], ['1Y', 365], ['5Y', 1826]];
const HEALTH_CODE = { DIGITRAFFIC: 'AIS-FI', AISSTREAM: 'AIS', MARKET: 'MKT', FRED: 'FRED', EIA: 'EIA', JODI: 'JODI', OPEC_CFG: 'OPEC', PORTWATCH: 'PW', BIS: 'BIS',
  WORLDBANK: 'WB', GDELT: 'GDLT', ACLED: 'ACLD', GDACS: 'GDCS', DEMO: 'DEMO' };
const COMMODITY_TAGS = ['OIL', 'GAS', 'LNG', 'COPPER', 'GOLD', 'URANIUM', 'SEMIS', 'GRAINS', 'RATES'];

const S = {
  meta: null, layers: { oil: true, choke: true, infra: false, macro: false, comm: false, geo: true, port: false, tank: true },
  metric: 'gdp_growth', asOf: null, windowDays: 0, sel: null,
  kpis: [], commodities: [], choro: null, production: [], chokepoints: [], macro: [], infra: null,
  gEvents: [], feed: [], portfolio: [], health: [], streamUp: false, newIds: new Set(),
};
let globe, ws;

// =================================================================== boot
async function boot() {
  S.meta = await api('/api/meta');
  S.health = S.meta.providers;
  $('#demo-banner').hidden = !S.meta.demo_mode;
  window.RIOTTO_PUBLIC = !!S.meta.public_mode;
  const countries = await (await fetch('data/countries.geojson')).json();
  S.countries = Object.fromEntries(countries.features.map((f) => [f.properties.iso3, f.properties]));
  globe = new GlobeView($('#globe'), countries, {
    onCountry: (iso) => select({ type: 'country', id: iso }),
    onPick: (d) => d && d.pick && select(d.pick),
    onLocation: (lat, lon) => select({ type: 'location', lat, lon }),
    tankerTip: (v) => `<b class="t">${esc(v.name || 'MMSI ' + v.mmsi)}</b> <span class="dim">${esc(v.size_class || '')} tanker${v.flag ? ' · ' + esc(v.flag) : ''}</span><br>`
      + `${v.sog != null ? v.sog.toFixed(1) + ' kn' : '—'} · ${esc(v.nav_text || '')}${v.destination ? ' → ' + esc(v.destination) : ''}<br>`
      + `<span class="dim">${v.laden ? esc(v.laden) + ' · ' : ''}reported ${fmtAge(v.age_s)} ago · ${esc(v.source)}</span>${v.stale ? ' <span class="down">STALE</span>' : ''}`
      + `${v.drMin ? `<br><span class="amber">position est. +${v.drMin} min from last report (course ${Math.round(v.cog)}° @ ${v.sog.toFixed(1)} kn)</span>` : ''}<br><span class="cyan">click for details</span>`,
  });
  ws = new Dashboard($('#grid'), $('#park'), {
    onMessage: (t) => msg(t),
    onSingleton: (type, shown) => {
      if (type === 'globe') {
        if (shown) { globe.g.resumeAnimation(); globe.ships.setVisible(S.layers.tank && !S.asOf); }
        else { globe.g.pauseAnimation(); globe.ships.setVisible(false); }   // no GPU work while off-screen
      }
    },
    onSpecial: (fn, args) => {
      if (fn === '@holding') { if (!S.layers.port) toggleLayer('port'); select({ type: 'holding', id: args.id }); }
      if (fn === '@url') window.open(args.url, '_blank', 'noopener');
    },
    onFocus: (p) => { $('#prompt').textContent = p ? `${p.fnEl.textContent || 'FN'}>` : '>'; },
  });
  window.__macroGlobe = { S, globe, ws };   // debugging handle (read-only use from the console)
  S.catalog = await api('/api/securities').catch(() => []);
  buildStatic(countries);
  renderHealth();
  await Promise.all([loadKpis(), loadChoro(), loadProduction(), loadChokepoints(), loadMacro(), loadInfra(),
    loadGlobeEvents(), loadFeed(), loadPortfolio(), loadVessels(), loadNews()]);
  renderDetail();
  ws.bindTabbar();
  ws.start();
  tick();
  setInterval(tick, 1000);
  setInterval(loadKpis, 30000);          // statuses can turn STALE without new data
  setInterval(loadHealth, 15000);
  setInterval(() => { if (!S.asOf) { loadGlobeEvents(); loadFeed(); } }, 120000);
  setInterval(loadPortfolio, 180000);
  setInterval(loadVessels, 60000);
  setInterval(() => { if (S.feedMode === 'news') renderNewsFeed(); }, 60000);   // keep relative times fresh
  connectStream({
    data: onData, event: onEvent, health: (h) => { S.health = h; renderHealth(); }, news: onNews,
  }, (up) => { S.streamUp = up; renderHealth(); });
  bindKeys();
}

// ============================================================ static UI
function buildStatic(countries) {
  $('#layers').innerHTML = LAYERS.map((l) => `<button class="layer" data-id="${l.id}" title="${esc(l.name)} (key ${l.k})"><i>${l.k}</i>${SHORT[l.id]}</button>`).join('');
  $('#layers').addEventListener('click', (e) => { const el = e.target.closest('.layer'); if (el) toggleLayer(el.dataset.id); });
  renderLayerBoxes();

  const m = S.meta.choropleth;
  $('#metric-select').innerHTML = Object.entries(m).map(([id, c]) => `<option value="${id}">MAP: ${esc(c.label)}</option>`).join('');
  $('#metric-select').addEventListener('change', (e) => setMetric(e.target.value));
  renderMetricBoxes();
  $('#legend-btn').addEventListener('click', () => {
    const b = $('#marker-legend-box'); b.hidden = !b.hidden; $('#legend-btn').classList.toggle('on', !b.hidden);
  });

  $('#marker-legend').innerHTML = [
    ['#ff3b30', 'pulse: output drop / disruption / sev≥4'],
    [STATE_COLOR.DISRUPTED, 'chokepoint DISRUPTED'], [STATE_COLOR.ELEVATED, 'chokepoint ELEVATED'], [STATE_COLOR.NORMAL, 'chokepoint NORMAL / lane'],
    [SITE_COLOR.refinery, 'refinery'], [SITE_COLOR.lng_export, 'LNG export'], [SITE_COLOR.lng_import, 'LNG import'],
    [SITE_COLOR.spr, 'strategic reserve'], [SITE_COLOR.terminal, 'export terminal'],
    [TANKER_COLOR.laden, 'tanker under way, likely laden'], [TANKER_COLOR.ballast, 'tanker under way, ballast/unknown'],
    [TANKER_COLOR.idle, 'tanker anchored / moored'], [TANKER_COLOR.stale, 'tanker, AIS report > 2h old'],
    ['#4fc3f7', 'oil pipeline (schematic)', 1], ['#b388ff', 'gas pipeline (schematic)', 1],
    [SEV_COLOR[3], 'event sev 3'], [SEV_COLOR[5], 'event sev 5'],
  ].map(([c, t, ln]) => `<div class="lg"><span class="sw${ln ? ' ln' : ''}" style="background:${c}"></span>${t}</div>`).join('');

  const regions = new Set(), subs = new Set();
  countries.features.forEach((f) => { regions.add(f.properties.region); subs.add(f.properties.subregion); });
  $('#f-region').insertAdjacentHTML('beforeend', [...regions].sort().map((r) => `<option>${esc(r)}</option>`).join('')
    + '<option disabled>──────</option>' + [...subs].sort().map((r) => `<option>${esc(r)}</option>`).join(''));
  $('#f-comm').insertAdjacentHTML('beforeend', COMMODITY_TAGS.map((c) => `<option>${c}</option>`).join(''));
  ['#f-kind', '#f-sev', '#f-region', '#f-comm', '#f-src', '#f-days'].forEach((s) => $(s).addEventListener('change', () => (S.feedMode === 'news' ? loadNews() : loadFeed())));
  $('#f-ntopic').addEventListener('change', loadNews);
  $('#feed-tabs').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) setFeedMode(b.dataset.mode); });
  $('#feed-table tbody').addEventListener('click', (e) => {
    const tr = e.target.closest('tr[data-url]');
    if (tr) window.open(tr.dataset.url, '_blank', 'noopener');
  });
  $('#feed-table tbody').addEventListener('click', (e) => {
    const tr = e.target.closest('tr[data-id]');
    if (tr && !e.target.closest('a')) select({ type: 'event', id: tr.dataset.id });
  });

  $('#windows').innerHTML = WINDOWS.map(([l, d]) => `<button data-d="${d}">${l}</button>`).join('');
  $('#windows').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) setWindow(+b.dataset.d); });
  $('#slider').addEventListener('input', onSlide);
  $('#play').addEventListener('click', togglePlay);
  renderWindowButtons();

  $('#cmd-form').addEventListener('submit', (e) => { e.preventDefault(); runCommand($('#cmd').value); });
  $('#about-btn').addEventListener('click', () => ws.open('ABOUT'));
  $('#ykeys').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) addYellowKey(b.dataset.yk); });
  $('#health').addEventListener('click', (e) => {
    const c = e.target.closest('[data-prov]');
    select(c ? { type: 'provider', id: c.dataset.prov } : { type: 'health' });
  });
  $('#tape').addEventListener('click', (e) => {
    const t = e.target.closest('.tk[data-id]');
    if (!t) return;
    const k = [...S.kpis, ...S.commodities].find((x) => x.id === t.dataset.id);
    const sec = k && (S.catalog || []).find((c) => c.entity === k.entity && c.metric === k.metric);
    if (sec) ws.open('GP', { sec }); else if (k) select({ type: 'kpi', id: k.id });
  });

  $('#kpi-strip').innerHTML = S.meta.kpis.map((k) => `<div class="kpi" data-id="${k.id}">
    <div class="row1"><span class="lbl">${esc(k.label)}</span><span class="st"></span></div>
    <div class="val">—</div><div class="chg"></div><div class="sp"></div><div class="foot"></div></div>`).join('');
  $('#kpi-strip').addEventListener('click', (e) => { const el = e.target.closest('.kpi'); if (el) select({ type: 'kpi', id: el.dataset.id }); });
}

function renderLayerBoxes() {
  document.querySelectorAll('#layers .layer').forEach((el) => el.classList.toggle('on', !!S.layers[el.dataset.id]));
}
function renderMetricBoxes() {
  $('#metric-select').value = S.metric;
}
function toggleLayer(id) {
  S.layers[id] = !S.layers[id];
  renderLayerBoxes();
  if (id === 'port') loadPortfolio();
  if (id === 'tank') globe.setTankers(S.layers.tank && !S.asOf ? (S.vessels || []) : [], S.vesselsAt);
  composeGlobe();
  renderLeftExtra();
}
function setMetric(id) {
  S.metric = id;
  renderMetricBoxes();
  loadChoro();
}

// ============================================================ loaders
async function loadKpis() {
  try {
    const d = await api('/api/kpis');
    S.kpis = d.kpis; S.commodities = d.commodities;
    renderKpis(); renderTape(); composeGlobe(); renderLeftExtra();
    if (S.sel && S.sel.type === 'kpi') refreshKpiDetailHeader();
  } catch (e) { msg(`KPI load failed: ${e.message}`); }
}
async function loadHealth() { try { S.health = await api('/api/health'); renderHealth(); } catch (_) { /* shown via stream state */ } }
async function loadChoro() {
  try {
    S.choro = await api(`/api/choropleth?metric=${S.metric}${S.asOf ? `&as_of=${S.asOf}` : ''}`);
    globe.setChoropleth(S.choro, S.choro.values);
    renderLegend();
  } catch (e) { msg(`choropleth: ${e.message}`); }
}
async function loadProduction() { try { S.production = await api(`/api/production${q()}`); composeGlobe(); renderLeftExtra(); } catch (e) { msg(`production: ${e.message}`); } }
async function loadChokepoints() { try { S.chokepoints = await api(`/api/chokepoints${q()}`); composeGlobe(); renderLeftExtra(); } catch (e) { msg(`chokepoints: ${e.message}`); } }
async function loadMacro() { try { S.macro = await api(`/api/macro${q()}`); composeGlobe(); } catch (e) { msg(`macro: ${e.message}`); } }
async function loadInfra() { try { S.infra = await api('/api/infrastructure'); composeGlobe(); } catch (e) { msg(`infra: ${e.message}`); } }
async function loadGlobeEvents() {
  try {
    const days = S.asOf ? 7 : 3;
    S.gEvents = await api(`/api/events?days=${days}&min_severity=2&limit=700${S.asOf ? `&as_of=${S.asOf}` : ''}`);
    composeGlobe();
    if (!S.sel) renderDetail();
  } catch (e) { msg(`events: ${e.message}`); }
}
async function loadFeed() {
  const p = new URLSearchParams({ days: $('#f-days').value, min_severity: $('#f-sev').value, limit: 400 });
  if ($('#f-kind').value) p.set('kind', $('#f-kind').value);
  if ($('#f-region').value) p.set('region', $('#f-region').value);
  if ($('#f-comm').value) p.set('commodity', $('#f-comm').value);
  if ($('#f-src').value) p.set('source', $('#f-src').value);
  if (S.asOf) p.set('as_of', S.asOf);
  try { S.feed = await api(`/api/events?${p}`); renderFeed(); } catch (e) { msg(`feed: ${e.message}`); }
}
async function loadPortfolio() {
  try {
    S.portfolio = await api('/api/portfolio');
    const geos = new Set();
    if (S.layers.port) S.portfolio.forEach((h) => (h.geographies || []).forEach((g) => geos.add(g)));
    globe.setHighlight(geos);
    renderLeftExtra();
    if (S.sel && S.sel.type === 'holding') renderDetail();
  } catch (e) { msg(`portfolio: ${e.message}`); }
}
const q = () => (S.asOf ? `?as_of=${S.asOf}` : '');
async function loadNews() {
  const p = new URLSearchParams({ hours: +$('#f-days').value * 24, limit: 300 });
  if ($('#f-ntopic').value) p.set('topic', $('#f-ntopic').value);
  try {
    S.news = await api(`/api/news?${p}`);
    if (!S.homeNews || !$('#f-ntopic').value) S.homeNews = S.news.slice(0, 40);
    if (S.feedMode === 'news') renderNewsFeed();
    if (!S.sel) renderDetail();
  } catch (e) { msg(`news: ${e.message}`); }
}
function setFeedMode(mode) {
  S.feedMode = mode;
  document.querySelectorAll('#feed-tabs button').forEach((b) => b.classList.toggle('on', b.dataset.mode === mode));
  const news = mode === 'news';
  $('#f-ntopic').hidden = !news;
  ['#f-kind', '#f-sev', '#f-region', '#f-comm', '#f-src'].forEach((s) => { $(s).hidden = news; });
  if (news) { $('#news-badge').textContent = ''; S.unread = 0; loadNews(); } else renderFeed();
}
function renderNewsFeed(newIds = new Set()) {
  $('#feed-table thead').innerHTML = '<tr><th>TIME UTC</th><th>HEADLINE · SOURCE · TAGS</th></tr>';
  $('#feed-table tbody').innerHTML = (S.news || []).map((n, i) => newsRow(n, i, { isNew: newIds.has(n.id), showNum: false })).join('')
    || '<tr><td colspan="2" class="dim">No headlines yet (feeds poll every 2 min).</td></tr>';
  $('#feed-count').textContent = `${(S.news || []).length} headlines · newest first · click to open the article`;
}
function onNews(items) {
  window.dispatchEvent(new CustomEvent('riotto:news', { detail: items }));   // live NEWS panels
  const topic = $('#f-ntopic').value;
  const add = items.filter((n) => !topic || n.topics.includes(topic));
  S.news = [...add, ...(S.news || []).filter((n) => !add.find((a) => a.id === n.id))].slice(0, 300);
  S.homeNews = [...items, ...(S.homeNews || [])].slice(0, 40);
  if (S.feedMode === 'news') renderNewsFeed(new Set(add.map((n) => n.id)));
  else { S.unread = (S.unread || 0) + items.length; $('#news-badge').textContent = S.unread; }
  const hot = items.find((n) => n.hot);
  if (hot) msg(`NEWS  ${hot.source}: ${hot.title}`, true);
  if (!S.sel) renderDetail();
}
async function loadVessels() {
  try {
    const d = await api('/api/vessels');
    S.vessels = d.vessels; S.vesselInfo = d; S.vesselsAt = Date.now();
    globe.setTankers(S.layers.tank && !S.asOf ? S.vessels : [], S.vesselsAt);
    renderLeftExtra();
  } catch (e) { msg(`tankers: ${e.message}`); }
}

// ============================================================ SSE
const refetch = {
  kpis: debounce(loadKpis, 800), choro: debounce(loadChoro, 1500), prod: debounce(loadProduction, 1500),
  choke: debounce(loadChokepoints, 1500), macro: debounce(loadMacro, 1500), port: debounce(loadPortfolio, 3000),
  gev: debounce(loadGlobeEvents, 3000),
};
const KPI_METRICS = new Set(['price', 'level', 'yield', 'spread', 'fx']);
const CHORO_BASE = { crude_prod_yoy: 'crude_prod', fx_1m: 'fx_per_usd' };
function onData(d) {
  const ms = new Set(d.metrics);
  if ([...ms].some((m) => KPI_METRICS.has(m))) refetch.kpis();
  if (S.asOf) return;   // replay mode: the map is pinned to a date
  if (ms.has(CHORO_BASE[S.metric] || S.metric)) refetch.choro();
  if (ms.has('crude_prod') || ms.has('opec_quota')) refetch.prod();
  if (ms.has('transits_total')) refetch.choke();
  if (ms.has('policy_rate')) refetch.macro();
}
function onEvent(e) {
  if (S.asOf) return;
  S.newIds.add(e.id);
  refetch.gev();
  refetch.port();
  loadFeedDebounced();
  if (e.kind === 'ALERT') msg(`ALERT  ${e.title}`, true);
}
const loadFeedDebounced = debounce(loadFeed, 1500);

// ============================================================ top bar
function tick() {
  const now = new Date();
  $('#clock').textContent = now.toISOString().slice(0, 19).replace('T', ' ') + ' UTC';
  if (now.getSeconds() % 15 === 0 || !$('#sessions').innerHTML) renderSessions(now);
}
function localParts(now, tz) {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: tz, weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
    .formatToParts(now).map((x) => [x.type, x.value]));
  return { wd: p.weekday, mins: (+p.hour % 24) * 60 + +p.minute, hm: `${p.hour}:${p.minute}` };
}
function renderSessions(now) {
  const mk = [
    ['TKY', 'Asia/Tokyo', [[540, 690], [750, 930]], [690, 750]],
    ['LDN', 'Europe/London', [[480, 990]]],
    ['NY', 'America/New_York', [[570, 960]]],
  ];
  $('#sessions').innerHTML = mk.map(([n, tz, sess, lunch]) => {
    const { wd, mins, hm } = localParts(now, tz);
    const wk = !['Sat', 'Sun'].includes(wd);
    const open = wk && sess.some(([a, b]) => mins >= a && mins < b);
    const isLunch = wk && lunch && mins >= lunch[0] && mins < lunch[1];
    const st = open ? 'OPEN' : isLunch ? 'LUNCH' : 'CLSD';
    return `<span class="sess ${open ? 'open' : isLunch ? 'lunch' : ''}" title="${n} local ${hm} · regular session only, exchange holidays not modelled">${n} ${st}</span>`;
  }).join('');
}
function renderHealth() {
  const cells = S.health.map((h) => {
    let st = h.enabled ? h.status : 'DISABLED';
    if (h.enabled && h.last_success && ageOf(h.last_success) > h.stale_after_s && st !== 'POLLING') st = 'STALE';
    const tip = [`${h.provider} — ${h.free ? 'FREE' : 'PAID'}`, h.description, `status: ${st}${h.reason ? ' (' + h.reason + ')' : ''}`,
      `last success: ${h.last_success || 'never'}${h.last_success ? ' (' + fmtAge(ageOf(h.last_success)) + ' ago)' : ''}`,
      `next poll: ${h.next_poll || '—'}`, `interval: ${h.interval_s}s · stale after ${h.stale_after_s}s`,
      h.last_error ? `last error: ${h.last_error}` : ''].filter(Boolean).join('\n');
    return `<span class="hcell h-${st}" data-prov="${esc(h.provider)}" title="${esc(tip)}\n(click for detail)">${HEALTH_CODE[h.provider] || h.provider}</span>`;
  });
  cells.push(`<span class="hcell ${S.streamUp ? 'h-OK' : 'h-FAILING'}" title="Server push stream (SSE)">SSE</span>`);
  $('#health').innerHTML = cells.join('');
  if (S.sel && S.sel.type === 'health') renderDetail();
}
let msgTimer;
function msg(t, sticky) {
  $('#cmd-msg').textContent = t;
  clearTimeout(msgTimer);
  msgTimer = setTimeout(() => { $('#cmd-msg').textContent = ''; }, sticky ? 30000 : 8000);
}

// ============================================================ KPI strip + tape
// spreads are stored in % but read in bp; yields stay in %
const isBp = (k) => k.metric === 'spread';
function kpiVal(k) {
  if (k.value === null) return '—';
  return isBp(k) ? `${fmtNum(k.value * 100, '', 0)}<span class="dim" style="font-size:10px">bp</span>`
    : `${fmtNum(k.value, k.unit, k.unit === '%' ? 3 : undefined)}${k.unit === '%' ? '<span class="dim" style="font-size:10px">%</span>' : ''}`;
}
function kpiFoot(k) {
  if (k.value === null) return `<span class="down" title="${esc(k.missing || '')}">NO DATA · ${esc((k.missing || '').split(';')[0])}</span>`;
  const date = k.source === 'MARKET' || k.source === 'DEMO' ? shortDate(k.observation_date) : (k.period_label || shortDate(k.observation_date));
  return `as of ${esc(date)} · ${esc(k.source)}${k.change_source && k.change_source !== k.source ? ' (chg ' + esc(k.change_source) + ')' : ''}`;
}
function renderKpis() {
  for (const k of S.kpis) {
    const el = document.querySelector(`.kpi[data-id="${k.id}"]`);
    if (!el) continue;
    el.querySelector('.st').innerHTML = statusBadge(k.status, k.provider_age_s) + flagsHtml(k.flags);
    el.querySelector('.val').innerHTML = kpiVal(k);
    const c = k.changes || {};
    el.querySelector('.chg').innerHTML = `<span><i>1D</i>${fmtChg(c.d1, k.change_kind)}</span><span><i>1W</i>${fmtChg(c.w1, k.change_kind)}</span><span><i>1M</i>${fmtChg(c.m1, k.change_kind)}</span>`;
    el.querySelector('.sp').innerHTML = spark(k.spark, 100, 14);
    el.querySelector('.foot').innerHTML = kpiFoot(k);
    el.querySelector('.foot').title = `source ${k.source || '—'} · fetched ${k.fetched_at || '—'} · cadence ${k.release_cadence || '—'}`;
    el.classList.toggle('sel', !!(S.sel && S.sel.type === 'kpi' && S.sel.id === k.id));
  }
}
let tapeBuilt = false;
function renderTape() {
  const items = [...S.kpis, ...S.commodities.filter((c) => !S.kpis.find((k) => k.id === c.id))];
  if (!tapeBuilt) {
    const one = items.map((k) => `<span class="tk" data-id="${k.id}"><b>${esc(k.label)}</b><span class="v"></span><span class="c"></span></span>`).join('');
    $('#tape-inner').innerHTML = one + one;   // duplicated so the scroll loops seamlessly
    tapeBuilt = true;
  }
  for (const k of items) {
    document.querySelectorAll(`.tk[data-id="${k.id}"]`).forEach((el) => {
      el.querySelector('.v').textContent = k.value === null ? 'n/a' : (isBp(k) ? `${fmtNum(k.value * 100, '', 0)}bp` : fmtNum(k.value, k.unit));
      el.querySelector('.c').innerHTML = `${fmtChg(k.changes?.d1 ?? null, k.change_kind)}${k.status === 'STALE' ? ' <span class="down">STALE</span>' : ''}`;
    });
  }
}

// ============================================================ legend + left rail
function renderLegend() {
  const c = S.choro;
  if (!c) return;
  $('#legend').innerHTML = `<div class="bar" style="background:${legendGradient(c)}"></div>
    <div class="ticks"><span>${fmtNum(c.domain[0], c.unit, 0)}</span>${c.mid !== null && c.mid !== undefined ? `<span>${c.mid}</span>` : ''}<span>${fmtNum(c.domain[1], c.unit, 0)}${esc(c.unit)}</span></div>`;
  const dates = Object.values(c.values).map((v) => (v.period || v.observation_date || '').slice(0, 10)).sort();
  const latest = dates[dates.length - 1] || '—';
  $('#choro-meta').innerHTML = `${esc(c.source)} · ${c.count} countries · latest ${esc(latest)} ${statusBadge(c.provider_status)}<br>`
    + (c.range ? `range ${fmtNum(c.range[0], c.unit)} … ${fmtNum(c.range[1], c.unit)} · faded = OLD for cadence` : 'no data yet');
  $('#hud-metric').textContent = `${c.label} (${c.unit})${S.asOf ? ' · as of ' + S.asOf : ''}`;
}

function renderLeftExtra() {
  const put = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
  if (S.production.length) {
    put('w-production', `<table><thead><tr><th>CTRY</th><th class="num">KB/D</th><th class="num">M/M</th><th class="num">Y/Y</th><th>MON</th></tr></thead><tbody>`
      + S.production.slice(0, 18).map((p) => `<tr class="click" data-pick='${esc(JSON.stringify({ type: 'country', id: p.entity }))}' title="${esc(p.source)}${p.crosscheck ? ` · vs ${p.crosscheck.source} ${fmtNum(p.crosscheck.value)} (${p.crosscheck.diff_pct}%)` : ''}${p.quota ? ` · quota ${p.quota.value}` : ''}">
        <td class="${p.drop_alert ? 'down' : p.old_period ? 'dim' : 'white'}">${p.entity}${p.crosscheck && p.crosscheck.flag ? '<span class="flag">XCHK</span>' : ''}</td>
        <td class="num ${p.old_period ? 'dim' : 'white'}">${fmtNum(p.value, '', 0)}</td><td class="num">${fmtChg(p.mom_pct)}</td><td class="num">${fmtChg(p.yoy_pct)}</td>
        <td class="${p.old_period ? 'down' : 'dim'}">${esc(p.period || '')}</td></tr>`).join('')
      + `</tbody></table><div class="note">kb/d by reference month (JODI / EIA). Red = m/m ≤ ${S.meta.production_threshold_pct}% · red month = reporter >6m behind · XCHK = EIA vs JODI gap ≥ threshold</div>`);
  }
  if (S.chokepoints.length) {
    put('w-chokepoints', `<table><thead><tr><th>ID</th><th>STATE</th><th class="num">7D VS 90D</th><th>AS OF</th><th class="num">AIS TANKERS</th></tr></thead><tbody>`
      + S.chokepoints.map((c) => `<tr class="click" data-pick='${esc(JSON.stringify({ type: 'chokepoint', id: c.id }))}'>
        <td class="white">${esc(c.id)}</td><td style="color:${STATE_COLOR[c.state] || '#888'}">${esc(c.state)}</td>
        <td class="num">${c.pct_vs_baseline === null || c.pct_vs_baseline === undefined ? '—' : fmtChg(c.pct_vs_baseline)}</td><td class="dim">${esc(shortDate(c.observation_date))}</td><td class="num dim">${c.ais_tankers ?? '—'}</td></tr>`).join('')
      + '</tbody></table><div class="note">Transits: IMF PortWatch (~1 week lag). AIS tankers: live, within 150 km.</div>');
  }
  if (S.commodities.length) {
    put('w-commodities', `<table><thead><tr><th>NAME</th><th class="num">LAST</th><th class="num">1D</th><th>AS OF</th><th></th></tr></thead><tbody>`
      + S.commodities.map((k) => `<tr class="click" data-pick='${esc(JSON.stringify({ type: 'kpi', id: k.id }))}' title="${esc(kpiFoot(k).replace(/<[^>]+>/g, ''))}">
        <td class="amber">${esc(k.label)}</td><td class="num white">${fmtNum(k.value, k.unit)}</td><td class="num">${fmtChg(k.changes?.d1 ?? null)}</td>
        <td class="dim">${esc(k.period_label || shortDate(k.observation_date))}</td><td>${statusBadge(k.status, k.provider_age_s)}</td></tr>`).join('')
      + '</tbody></table>');
  }
  if (S.portfolio.length) {
    put('w-portfolio', `<table><thead><tr><th>TICKER</th><th>SIDE</th><th class="num">PX</th><th class="num">7D HITS</th></tr></thead><tbody>`
      + S.portfolio.map((h) => `<tr class="click" data-pick='${esc(JSON.stringify({ type: 'holding', id: h.ticker }))}'>
        <td class="white">${esc(h.ticker)}</td><td class="${h.side === 'long' ? 'up' : 'down'}">${h.side.toUpperCase()}</td>
        <td class="num">${h.price ? fmtNum(h.price.value) : '<span class="dim">no px</span>'}</td>
        <td class="num"><span class="sevbox sev${Math.max(1, h.max_severity)}">${h.hit_count}</span></td></tr>`).join('')
      + '</tbody></table>');
  }
  if (S.vessels) {
    const vs = S.vessels, cur = vs.filter((v) => !v.stale);
    const by = (f) => Object.entries(cur.reduce((m, v) => { const k = f(v) || '—'; m[k] = (m[k] || 0) + 1; return m; }, {})).sort((a, b) => b[1] - a[1]);
    const feeds = S.vesselInfo?.feeds || {};
    put('w-tankers', `<div class="note">${Object.entries(feeds).map(([k, f]) => `<span class="click" ${pickAttr({ type: 'provider', id: k })} style="cursor:pointer">${k} ${f.status === 'DISABLED' ? '<span class="badge faint" title="' + esc(f.reason || '') + '">OFF · NO KEY</span>' : statusBadge(f.status, f.age_s)}</span>`).join(' ')}</div>
      <div class="kv"><span>Current / stale</span><span class="white">${cur.length} / ${vs.length - cur.length}</span></div>
      <div class="kv"><span>Under way / idle</span><span>${cur.filter((v) => v.moving).length} / ${cur.filter((v) => !v.moving).length}</span></div>
      <div class="note">${by((v) => v.size_class).map(([k, n]) => `${esc(k)} ${n}`).join(' · ')}</div>
      <div class="note">Flags: ${by((v) => v.flag).slice(0, 6).map(([k, n]) => `${esc(k)} ${n}`).join(' · ')}</div>
      ${vesselRows(cur.filter((v) => v.moving).sort((a, b) => (b.length || 0) - (a.length || 0)), 12)}
      ${feeds.AISSTREAM && feeds.AISSTREAM.status === 'DISABLED' ? '<div class="note">Only the Baltic is covered. Add AISSTREAM_API_KEY (free) for Hormuz, Red Sea, Malacca, US Gulf, North Sea, Black Sea, China.</div>' : ''}`);
  }

}
document.addEventListener('click', (e) => {
  const tr = e.target.closest('#park tr[data-pick], .pbody tr[data-pick], #detail [data-pick], .pbody [data-pick]');
  if (tr) select(JSON.parse(tr.dataset.pick));
});

// ============================================================ globe composition
function composeGlobe() {
  if (!globe) return;
  const points = [], labels = [], rings = [], arcs = [], paths = [];
  const L = S.layers;
  const chokeById = Object.fromEntries(S.chokepoints.map((c) => [c.id, c]));

  if (L.oil) {
    S.production.slice(0, 30).forEach((p) => {
      if (p.lat == null) return;
      const col = p.drop_alert ? '#ff3b30' : p.old_period ? '#666666' : (p.mom_pct ?? 0) >= 0 ? '#2ecc40' : '#ffb000';
      labels.push({ id: 'prod-' + p.entity, lat: p.lat, lon: p.lon, color: col, dot: 0.25,
        size: Math.max(0.6, Math.min(1.6, Math.sqrt(p.value) / 70)),
        text: `${p.entity} ${(p.value / 1000).toFixed(2)}mb/d ${p.mom_pct === null ? '' : (p.mom_pct > 0 ? '+' : '') + p.mom_pct.toFixed(1) + '%m'}`,
        label: `<b>${esc(p.name)}</b> crude output<br><span class="t">${fmtNum(p.value, '', 0)} kb/d</span> · ${esc(p.period)} (${esc(p.source)})<br>m/m ${p.mom_pct ?? '—'}% · y/y ${p.yoy_pct ?? '—'}%${p.old_period ? `<br><span class="down">reference month is ${p.months_old} months old</span>` : ''}${p.quota ? `<br>quota (manual cfg) ${p.quota.value} kb/d · Δ ${p.quota.vs_actual_kbd}` : ''}`,
        pick: { type: 'country', id: p.entity } });
      if (p.drop_alert) rings.push({ id: 'pr-' + p.entity, lat: p.lat, lon: p.lon, rgb: '255,59,48', maxR: 4.5, speed: 2.2, period: 900 });
    });
  }
  if (L.choke) {
    S.chokepoints.forEach((c) => {
      const col = STATE_COLOR[c.state] || '#888';
      labels.push({ id: 'cp-' + c.id, lat: c.lat, lon: c.lon, color: col, dot: 0.5, size: 0.85,
        text: `${c.id} ${c.pct_vs_baseline === null || c.pct_vs_baseline === undefined ? '' : (c.pct_vs_baseline > 0 ? '+' : '') + c.pct_vs_baseline.toFixed(0) + '%'}`,
        label: `<b>${esc(c.name)}</b> — <span style="color:${col}">${esc(c.state)}</span><br>7d avg ${c.recent_avg ?? '—'} vessels/d vs 90d ${c.baseline_avg ?? '—'}<br>tankers 7d avg ${c.tanker_recent_avg ?? '—'}/d<br><span class="dim">as of ${esc(c.observation_date || '—')} · IMF PortWatch</span>`,
        pick: { type: 'chokepoint', id: c.id } });
      if (c.state === 'DISRUPTED') rings.push({ id: 'cr-' + c.id, lat: c.lat, lon: c.lon, rgb: '255,59,48', maxR: 7, speed: 3, period: 1100 });
      if (c.state === 'ELEVATED') rings.push({ id: 'cr-' + c.id, lat: c.lat, lon: c.lon, rgb: '255,176,0', maxR: 4, speed: 1.5, period: 1800 });
    });
    (S.infra?.routes || []).forEach((r) => {
      const stops = [r.from, ...r.via.map((v) => chokeById[v] ? [chokeById[v].lat, chokeById[v].lon] : null).filter(Boolean), r.to];
      const states = r.via.map((v) => chokeById[v]?.state);
      const worst = states.includes('DISRUPTED') ? 'DISRUPTED' : states.includes('ELEVATED') ? 'ELEVATED' : 'NORMAL';
      const col = worst === 'DISRUPTED' ? 'rgba(255,59,48,0.85)' : worst === 'ELEVATED' ? 'rgba(255,176,0,0.75)' : (r.commodity === 'LNG' ? 'rgba(0,229,255,0.45)' : 'rgba(0,229,255,0.30)');
      for (let i = 0; i < stops.length - 1; i++) {
        arcs.push({ id: `${r.id}-${i}`, sLat: stops[i][0], sLon: stops[i][1], eLat: stops[i + 1][0], eLon: stops[i + 1][1], color: col,
          label: `<b>${esc(r.name)}</b> (${r.commodity})<br>via ${r.via.join(' → ') || 'open ocean'}<br>worst chokepoint state: ${worst}<br><span class="dim">schematic lane · no volumes implied · click for detail</span>`,
          pick: { type: 'route', id: r.id } });
      }
    });
  }
  if (L.infra && S.infra) {
    S.infra.sites.forEach((s) => points.push({ id: 'site-' + s.id, lat: s.lat, lon: s.lon, color: SITE_COLOR[s.type] || '#ccc', r: 0.16, alt: 0.012,
      label: `<b>${esc(s.name)}</b><br>${esc(s.type.replace('_', ' '))} · ${esc(s.iso3)}<br><span class="dim">reference location (approx.) · no capacity data</span>`,
      pick: { type: 'site', id: s.id } }));
    S.infra.pipelines.forEach((p) => paths.push({ id: 'pl-' + p.id, path: p.path,
      color: p.note && p.note.startsWith('Damaged') ? 'rgba(120,120,120,0.6)' : p.commodity === 'GAS' ? 'rgba(179,136,255,0.85)' : 'rgba(79,195,247,0.85)',
      dash: p.note && p.note.startsWith('Damaged') ? 0.05 : 1, gap: p.note && p.note.startsWith('Damaged') ? 0.04 : 0,
      label: `<b>${esc(p.name)}</b> (${p.commodity})${p.note ? '<br>' + esc(p.note) : ''}<br><span class="dim">schematic route · click for detail</span>`,
      pick: { type: 'pipeline', id: p.id } }));
  }
  if (L.macro) {
    S.macro.forEach((m) => {
      if (m.lat == null) return;
      const ch = m.last_change ? m.value - m.last_change.value : 0;
      labels.push({ id: 'cb-' + m.entity, lat: m.lat, lon: m.lon, color: ch > 0 ? '#ff7a00' : ch < 0 ? '#2ecc40' : '#ffb000', dot: 0.25, size: 0.65,
        text: `${m.entity} ${m.value.toFixed(2)}${ch > 0 ? '▲' : ch < 0 ? '▼' : ''}`,
        label: `<b>${esc(m.name)}</b> policy rate <span class="t">${m.value.toFixed(2)}%</span><br>as of ${esc(m.observation_date)} (BIS)${m.last_change ? `<br>prior level ${m.last_change.value}% (before ${esc(m.last_change.observation_date)})` : ''}<br>next meeting: ${m.next_meeting ? esc(m.next_meeting) + (m.calendar_verified ? '' : ' (cfg, unverified)') : '<span class="dim">not configured</span>'}`,
        pick: { type: 'country', id: m.entity === 'EA' ? 'DEU' : m.entity } });
    });
  }
  if (L.comm) {
    S.commodities.forEach((k) => {
      if (!k.hub || k.value === null) return;
      labels.push({ id: 'hub-' + k.id, lat: k.hub[1], lon: k.hub[0], color: k.status === 'STALE' ? '#ff3b30' : '#e8e8e8', dot: 0.35, size: 0.75,
        text: `${k.id} ${fmtNum(k.value, k.unit)}`,
        label: `<b>${esc(k.label)}</b> ${fmtNum(k.value, k.unit)} ${esc(k.unit)}<br>1D ${fmtChg(k.changes?.d1 ?? null)} · 1M ${fmtChg(k.changes?.m1 ?? null)}<br><span class="dim">${kpiFoot(k)}</span>`,
        pick: { type: 'kpi', id: k.id } });
    });
  }
  if (L.geo) {
    const now = Date.now();
    S.gEvents.forEach((e) => {
      if (e.lat == null) return;
      const alert = e.kind === 'ALERT';
      points.push({ id: e.id, lat: e.lat, lon: e.lon, color: alert ? '#ff3b30' : SEV_COLOR[e.severity], r: alert ? 0.5 : 0.1 + e.severity * 0.07,
        alt: alert ? 0.06 : 0.008 + e.severity * 0.008,
        label: `<span class="sevbox sev${e.severity}">${e.severity}</span> <b>${esc(e.category)}</b> · ${esc(e.source)}<br><span class="t">${esc(e.title)}</span><br><span class="dim">${esc(shortDate(e.ts))} UTC</span>${e.readthrough ? `<br>${rtHtml(e.readthrough)}` : ''}`,
        pick: { type: 'event', id: e.id } });
      const fresh = S.asOf || now - Date.parse(e.ts) < 48 * 3600e3;
      if ((alert || e.severity >= 4) && fresh) rings.push({ id: 'er-' + e.id, lat: e.lat, lon: e.lon, rgb: alert || e.severity === 5 ? '255,59,48' : '255,122,0', maxR: alert ? 5 : 2.5, speed: 1.6, period: 1500 });
    });
  }
  if (S.selVessel && S.selVessel.track && S.selVessel.track.length > 1) {
    paths.push({ id: 'trk-' + S.selVessel.mmsi + '-' + S.selVessel.track.length, path: S.selVessel.track.map((t) => [t.lat, t.lon]),
      color: 'rgba(255,255,255,0.9)', dash: 1, gap: 0, label: `<b>${esc(S.selVessel.name || S.selVessel.mmsi)}</b> track (${S.selVessel.track.length} pts)` });
    const last = S.selVessel;
    if (last.lat != null) rings.push({ id: 'vsel', lat: last.lat, lon: last.lon, rgb: '255,255,255', maxR: 1.2, speed: 0.8, period: 1200 });
  }
  globe.setLayers({ points, labels, rings, arcs, paths });
}

function rtHtml(rt) {
  if (!rt) return '';
  return `<span class="rt">${(rt.assets || []).map((a) => `<span class="a">${esc(a.asset)} <span class="${a.direction === 'up' ? 'up' : a.direction === 'down' ? 'down' : 'dim'}">${a.direction === 'up' ? '▲' : a.direction === 'down' ? '▼' : a.direction}</span> ${Math.round(a.p * 100)}%</span>`).join('')}</span>`;
}

// ============================================================ feed
function renderFeed() {
  if (S.feedMode === 'news') return;
  $('#feed-table thead').innerHTML = '<tr><th>SEV</th><th>TIME UTC</th><th>TYPE</th><th>WHERE</th><th>EVENT</th><th>READ-THROUGH (prior p)</th><th>SRC</th></tr>';
  const rows = S.feed.map((e) => {
    const where = e.country_name || e.entity || (e.lat != null ? `${e.lat.toFixed(1)},${e.lon.toFixed(1)}` : '—');
    const isNew = S.newIds.has(e.id);
    return `<tr class="click${isNew ? ' new' : ''}" data-id="${esc(e.id)}"><td><span class="sevbox sev${e.severity}">${e.severity}</span></td>
      <td class="dim">${esc(shortDate(e.ts))}</td><td class="${e.kind === 'ALERT' ? 'down' : 'amber'}">${e.kind === 'ALERT' ? 'ALERT' : esc(e.category)}</td>
      <td>${esc(where)}</td><td title="${esc(e.title)}">${esc(e.title)}</td>
      <td title="${esc(e.readthrough?.text || '')}">${rtHtml(e.readthrough) || '<span class="dim">—</span>'}</td>
      <td class="dim">${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.source)}</a>` : esc(e.source)}</td></tr>`;
  });
  $('#feed-table tbody').innerHTML = rows.join('') || '<tr><td colspan="7" class="dim">No events match. Event feeds (GDELT/GDACS/ACLED) fill on their poll schedule.</td></tr>';
  $('#feed-count').textContent = `${S.feed.length} shown · sorted severity → recency${S.asOf ? ' · as of ' + S.asOf : ''}`;
  S.newIds.clear();
}

// ============================================================ time scrubber
function renderWindowButtons() {
  document.querySelectorAll('#windows button').forEach((b) => b.classList.toggle('on', +b.dataset.d === S.windowDays));
}
function setWindow(days) {
  S.windowDays = days;
  renderWindowButtons();
  const sl = $('#slider');
  stopPlay();
  if (!days) { sl.disabled = true; setAsOf(null); return; }
  sl.disabled = false;
  sl.max = days;
  sl.value = days;
  setAsOf(null);
}
function onSlide() {
  const back = S.windowDays - +$('#slider').value;
  if (back <= 0) { setAsOf(null); return; }
  const d = new Date(Date.now() - back * 86400e3).toISOString().slice(0, 10);
  $('#asof-label').textContent = d;
  applyAsOf(d);
}
const applyAsOf = debounce((d) => setAsOf(d), 250);
function setAsOf(d) {
  S.asOf = d;
  $('#asof-label').textContent = d || 'LIVE';
  $('#replay-flag').hidden = !d;
  $('#replay-flag').textContent = d ? `REPLAY AS OF ${d}` : '';
  loadChoro(); loadProduction(); loadChokepoints(); loadMacro(); loadGlobeEvents(); loadFeed();
  globe.setTankers(S.layers.tank && !d ? (S.vessels || []) : [], S.vesselsAt);   // AIS is live-only: hidden in replay
  if (S.sel && S.sel.type === 'country') renderDetail();
}
let playTimer = null;
function togglePlay() {
  if (playTimer) { stopPlay(); return; }
  if (!S.windowDays) setWindow(365);
  const sl = $('#slider');
  sl.value = 0;
  onSlide();
  $('#play').textContent = '■';
  const step = Math.max(1, Math.round(S.windowDays / 40));
  playTimer = setInterval(() => {
    sl.value = Math.min(+sl.max, +sl.value + step);
    onSlide();
    if (+sl.value >= +sl.max) stopPlay();
  }, 700);
}
function stopPlay() { clearInterval(playTimer); playTimer = null; $('#play').textContent = '▶'; }

// ============================================================ selection / detail
function select(sel) {
  S.sel = sel;
  if (![...ws.panels.values()].some((p) => p.type === 'inspector')) ws.addSingleton('inspector');   // details need somewhere to show
  if (sel.type === 'country') {
    globe.setSelected(sel.id);
    const p = S.countries[sel.id];
    if (p) globe.focus(p.lat, p.lon, 1.5);
  } else globe.setSelected(null);
  if (sel.type === 'chokepoint') {
    const c = S.chokepoints.find((x) => x.id === sel.id);
    if (c) globe.focus(c.lat, c.lon, 1.2);
  }
  if (sel.type === 'site') {
    const x = S.infra?.sites.find((y) => y.id === sel.id);
    if (x) globe.focus(x.lat, x.lon, 0.9);
  }
  if (sel.type !== 'vessel' && S.selVessel) { S.selVessel = null; composeGlobe(); }
  renderKpis();
  renderDetail();
}
function closeDetail() { S.sel = null; globe.setSelected(null); renderKpis(); renderDetail(); }

async function renderDetail() {
  const el = $('#detail');
  const s = S.sel;
  try {
    if (!s) el.innerHTML = homeHtml();
    else if (s.type === 'country') el.innerHTML = countryHtml(await api(`/api/country/${s.id}${q()}`));
    else if (s.type === 'kpi') el.innerHTML = kpiHtml(await api(`/api/kpi/${s.id}?days=${S.kpiDays || 365}`));
    else if (s.type === 'chokepoint') {
      const c = S.chokepoints.find((x) => x.id === s.id);
      el.innerHTML = chokeHtml(c, c && !S.asOf ? await api(`/api/vessels/near?lat=${c.lat}&lon=${c.lon}&km_radius=150`) : null);
    }
    else if (s.type === 'event') el.innerHTML = eventHtml([...S.feed, ...S.gEvents].find((e) => e.id === s.id));
    else if (s.type === 'holding') el.innerHTML = holdingHtml(S.portfolio.find((h) => h.ticker === s.id), await api(`/api/news?ticker=${encodeURIComponent(s.id)}&hours=336&limit=15`).catch(() => []));
    else if (s.type === 'health') el.innerHTML = await healthHtml();
    else if (s.type === 'help') el.innerHTML = helpHtml();
    else if (s.type === 'vessel') el.innerHTML = await vesselHtml(s.id);
    else if (s.type === 'site') el.innerHTML = await siteHtml(s.id);
    else if (s.type === 'pipeline') el.innerHTML = pipelineHtml(s.id);
    else if (s.type === 'route') el.innerHTML = routeHtml(s.id);
    else if (s.type === 'location') el.innerHTML = await locationHtml(s.lat, s.lon);
    else if (s.type === 'provider') el.innerHTML = await providerHtml(s.id);
    else if (s.type === 'vsearch') el.innerHTML = vsearchHtml(s.q, s.rows);
  } catch (e) {
    el.innerHTML = `${head('ERROR')}<div class="down">${esc(e.message)}</div>`;
  }
  const x = el.querySelector('.x');
  if (x) x.addEventListener('click', closeDetail);
  el.querySelectorAll('[data-kdays]').forEach((b) => b.addEventListener('click', () => { S.kpiDays = +b.dataset.kdays; renderDetail(); }));
}
const head = (t, extra = '') => `<div class="dh"><span>${t}</span><span>${extra}<span class="x" title="close (Esc)">✕</span></span></div>`;

function homeHtml() {
  const alerts = S.gEvents.filter((e) => e.kind === 'ALERT').slice(0, 12);
  const top = S.gEvents.filter((e) => e.kind !== 'ALERT' && e.severity >= 4).slice(0, 10);
  return `<div class="dh"><span>SIGNALS</span><span class="dim" style="color:#003">${S.asOf ? 'as of ' + S.asOf : 'live'}</span></div>
    <div class="sub">RULE ALERTS (${S.asOf ? '7d to as-of' : 'last 3d'})</div>
    ${alerts.length ? alerts.map(evRow).join('') : '<div class="note">No rule alerts in window. Rules fire only on newly published data (config/rules.yaml).</div>'}
    <div class="sub">CHOKEPOINTS</div>
    ${S.chokepoints.map((c) => `<div class="mrow click" data-pick='${esc(JSON.stringify({ type: 'chokepoint', id: c.id }))}' style="cursor:pointer"><span class="l">${esc(c.name)}</span><span class="v" style="color:${STATE_COLOR[c.state]}">${esc(c.state)} ${c.pct_vs_baseline != null ? (c.pct_vs_baseline > 0 ? '+' : '') + c.pct_vs_baseline + '%' : ''}</span>${spark(c.spark, 84, 16, { color: STATE_COLOR[c.state] })}</div>`).join('')}
    <div class="note">PortWatch transits as of ${esc(S.chokepoints[0]?.observation_date || '—')} (AIS-derived, ~1 week lag)</div>
    <div class="sub">TOP EVENTS (sev ≥4)</div>
    ${top.length ? top.map(evRow).join('') : '<div class="note">None in window.</div>'}
    <div class="sub">TOP NEWS <span class="dim">(TOP &lt;GO&gt; for the full wire)</span></div>
    ${topNewsHtml()}
    <div class="note">Type HELP for commands · click a country, KPI, marker, tanker, headline or event for detail.</div>`;
}
function topNewsHtml(list) {
  const pri = (n) => (n.hot ? 4 : 0) + (n.topics.includes('WAR') ? 2 : 0) + (n.topics.includes('OIL') ? 2 : 0) + (n.topics.includes('EQUITIES') || n.topics.includes('MACRO') ? 1 : 0) + (n.tickers.length ? 2 : 0);
  const src = list || (S.homeNews || []).filter((n) => Date.now() - Date.parse(n.published) < 12 * 3600e3).sort((a, b) => pri(b) - pri(a) || b.published.localeCompare(a.published)).slice(0, 8);
  if (!src.length) return '<div class="note">No headlines yet.</div>';
  return `<table class="ft"><tbody>${src.map((n, i) => newsRow(n, i, { showNum: false })).join('')}</tbody></table>`;
}
document.addEventListener('click', (e) => {
  const tr = e.target.closest('#detail tr[data-url]');
  if (tr) window.open(tr.dataset.url, '_blank', 'noopener');
});
function evRow(e) {
  return `<div class="mrow click" data-pick='${esc(JSON.stringify({ type: 'event', id: e.id }))}' style="cursor:pointer;grid-template-columns:20px 1fr">
    <span class="sevbox sev${e.severity}">${e.severity}</span><span class="white" style="white-space:normal">${esc(e.title)}</span>
    <span class="m">${esc(shortDate(e.ts))} · ${esc(e.source)} ${rtHtml(e.readthrough)}</span></div>`;
}

function mrow(m) {
  const date = m.period_label || shortDate(m.observation_date);
  const sec = { ticker: `${m.entity} ${m.label}`.toUpperCase(), yk: 'INDEX', name: `${m.label} — ${m.entity}`, entity: m.entity, metric: m.metric };
  const win = { annual: 'MAX', quarterly: '5Y', monthly: '5Y', weekly: '2Y' }[m.release_cadence] || '1Y';
  return `<div class="mrow click" data-gp='${esc(JSON.stringify(sec))}' data-win="${win}" title="click: open a chart panel (GP)"><span class="l">${esc(m.label)}</span>
    <span class="v">${fmtNum(m.value, m.unit)} <span class="dim">${esc(m.unit)}</span></span>${spark(m.spark, 84, 16, { color: 'var(--amber)' })}
    <span class="m">as of ${esc(date)} · ${esc(m.source)}${m.via ? ' (euro area)' : ''} ${statusBadge(m.status, m.provider_age_s)}${flagsHtml(m.flags)}</span></div>`;
}
function countryHtml(d) {
  const ev = d.events.length ? d.events.map(evRow).join('') : '<div class="note">No events in the last 30 days.</div>';
  const cal = d.calendar ? `<div class="kv"><span>${esc(d.calendar.bank)} next decision</span><span class="white">${esc((d.calendar.meetings || []).find((m) => m >= (S.asOf || new Date().toISOString().slice(0, 10))) || 'not configured')}</span></div>
    <div class="note">calendar: config file${d.calendar.verified ? '' : ', <span class="down">unverified</span>'} · <a href="${esc(d.calendar.source_url)}" target="_blank" rel="noopener noreferrer">source</a></div>` : '';
  return `${head(`${esc(d.name)} <span style="color:#033">${esc(d.iso3)} · ${esc(d.region || '')}</span>`)}
    ${d.as_of ? `<div class="note cyan">Replay: values as of ${esc(d.as_of)}</div>` : ''}
    <div class="sub">LATEST KPIs</div>${d.metrics.length ? d.metrics.map(mrow).join('') : '<div class="note">No stored observations for this country yet.</div>'}
    ${d.derived.map((x) => `<div class="kv"><span>${esc(x.label)} ${flagsHtml(x.flags)}</span><span class="white">${fmtNum(x.value, 'pp')} pp</span></div><div class="note">${esc(x.basis)}</div>`).join('')}
    ${cal}
    ${d.discrepancies.length ? `<div class="sub">SOURCE DISCREPANCIES</div>${d.discrepancies.map((x) => `<div class="note">${esc(x.period)} ${esc(x.source_a)} ${fmtNum(x.value_a)} vs ${esc(x.source_b)} ${fmtNum(x.value_b)} (${x.pct}%)</div>`).join('')}` : ''}
    ${d.holdings.length ? `<div class="sub">WATCHLIST EXPOSURE</div><div class="white">${d.holdings.map((t) => `<span data-pick='${esc(JSON.stringify({ type: 'holding', id: t }))}' style="cursor:pointer;margin-right:8px" class="cyan">${esc(t)}</span>`).join('')}</div>` : ''}
    <div class="sub">EVENTS (30d, severity first)</div>${ev}`;
}
function kpiHtml(k) {
  const unit = isBp(k) ? 'bp' : k.unit;
  const pts = isBp(k) ? (k.history || []).map((p) => [p[0], p[1] * 100]) : k.history;
  const days = S.kpiDays || 365;
  return `${head(esc(k.label), statusBadge(k.status, k.provider_age_s) + ' ')}
    <div class="kv"><span>Last</span><span class="white" style="font-size:16px">${kpiVal(k)} ${isBp(k) || k.unit === '%' ? '' : `<span class="dim">${esc(unit)}</span>`}</span></div>
    <div class="kv"><span>1D / 1W / 1M</span><span>${fmtChg(k.changes?.d1 ?? null, k.change_kind)} ${fmtChg(k.changes?.w1 ?? null, k.change_kind)} ${fmtChg(k.changes?.m1 ?? null, k.change_kind)}</span></div>
    <div class="kv"><span>Observation</span><span class="white">${esc(k.period_label || k.observation_date || '—')}</span></div>
    <div class="kv"><span>Source</span><span class="white">${esc(k.source || '—')} ${flagsHtml(k.flags)}</span></div>
    <div class="kv"><span>Fetched</span><span>${esc(k.fetched_at || '—')}</span></div>
    <div class="kv"><span>Cadence</span><span>${esc(k.release_cadence || '—')}</span></div>
    ${k.change_source && k.change_source !== k.source ? `<div class="note">Changes and chart use ${esc(k.change_source)} history (live source has no history yet).</div>` : ''}
    ${gpButton(k)}
    <div class="sub">HISTORY (${esc(k.history_source || '—')}) <span style="float:right">${[30, 91, 365, 1826].map((d) => `<button data-kdays="${d}" class="${d === days ? 'on' : ''}">${d === 1826 ? '5Y' : d === 365 ? '1Y' : d === 91 ? '3M' : '1M'}</button>`).join('')}</span></div>
    <div class="bigchart">${bigChart(pts, unit)}</div>
    ${k.value === null ? '<div class="note down">No provider has delivered this series. Check the data-health matrix (configure the key or market provider).</div>' : ''}`;
}
function gpButton(k) {
  const sec = (S.catalog || []).find((c) => c.entity === k.entity && c.metric === k.metric);
  return sec ? `<button class="gpbtn" data-gp='${esc(JSON.stringify(sec))}'>${esc(sec.ticker)} ${esc(sec.yk)} GP ▸ open as a chart panel</button>` : '';
}
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-gp]');
  if (b) ws.open('GP', { sec: JSON.parse(b.dataset.gp), win: b.dataset.win || undefined });
});
function refreshKpiDetailHeader() { /* full re-render is cheap and keeps one code path */ renderDetail(); }
function vesselRows(list, max = 25) {
  if (!list || !list.length) return '<div class="note">No tankers with a recent AIS position here.</div>';
  return `<table class="ft"><thead><tr><th>VESSEL</th><th>CLASS</th><th>FLAG</th><th class="num">KN</th><th>STATE</th><th class="num">KM</th><th>AGE</th></tr></thead><tbody>`
    + list.slice(0, max).map((v) => `<tr class="click" data-pick='${esc(JSON.stringify({ type: 'vessel', id: v.mmsi }))}'>
      <td class="white">${esc(v.name || v.mmsi)}</td><td>${esc(v.size_class || '—')}</td><td class="dim">${esc(v.flag || '—')}</td>
      <td class="num">${v.sog != null ? v.sog.toFixed(1) : '—'}</td><td style="color:${v.stale ? TANKER_COLOR.stale : !v.moving ? TANKER_COLOR.idle : v.laden === 'LADEN?' ? TANKER_COLOR.laden : TANKER_COLOR.ballast}">${v.stale ? 'STALE' : v.moving ? (v.laden || 'UNDER WAY') : 'ANCH/MOOR'}</td>
      <td class="num dim">${v.dist_km != null ? Math.round(v.dist_km) : ''}</td><td class="dim">${fmtAge(v.age_s)}</td></tr>`).join('')
    + `</tbody></table>${list.length > max ? `<div class="note">+${list.length - max} more</div>` : ''}`;
}
function chokeHtml(c, near) {
  if (!c) return head('CHOKEPOINT') + '<div class="note">Not loaded.</div>';
  return `${head(esc(c.name))}
    <div class="kv"><span>State</span><span style="color:${STATE_COLOR[c.state]}">${esc(c.state)}</span></div>
    <div class="kv"><span>7d avg transits</span><span class="white">${c.recent_avg ?? '—'} /day</span></div>
    <div class="kv"><span>Prior 90d avg</span><span class="white">${c.baseline_avg ?? '—'} /day</span></div>
    <div class="kv"><span>vs baseline</span><span>${c.pct_vs_baseline != null ? fmtChg(c.pct_vs_baseline) : '—'}</span></div>
    <div class="kv"><span>7d avg tankers</span><span class="white">${c.tanker_recent_avg ?? '—'} /day</span></div>
    <div class="kv"><span>Last day</span><span class="white">${c.last_day ?? '—'} vessels (${esc(c.observation_date || '—')})</span></div>
    <div class="kv"><span>Status</span><span>${statusBadge(c.status, c.provider_age_s)}</span></div>
    <div class="sub">DAILY TRANSITS (120d)</div><div class="bigchart">${bigChart(c.spark, 'count', { color: STATE_COLOR[c.state] })}</div>
    <div class="note">Source: IMF PortWatch (AIS-derived daily counts; typically ~1 week lag). Thresholds: config/settings.yaml → chokepoints. Commodities: ${esc((c.commodities || []).join(', '))}</div>
    ${near ? `<div class="sub">LIVE AIS TANKERS WITHIN 150 KM (${near.filter((v) => !v.stale).length} current)</div>${vesselRows(near)}<div class="note">${aisCoverageNote()}</div>` : ''}`;
}
function aisCoverageNote() {
  const f = S.vesselInfo?.feeds || {};
  const off = Object.entries(f).filter(([, x]) => x.status === 'DISABLED').map(([k, x]) => `${k} (${x.reason || 'off'})`);
  return `Live AIS: terrestrial receivers only; dark (AIS-off) tankers are invisible.${off.length ? ' Disabled: ' + esc(off.join(', ')) + '.' : ''}`;
}
function eventHtml(e) {
  if (!e) return head('EVENT') + '<div class="note">Event not in the current window.</div>';
  return `${head(e.kind === 'ALERT' ? 'RULE ALERT' : esc(e.category))}
    <div><span class="sevbox sev${e.severity}">${e.severity}</span> <span class="white">${esc(e.title)}</span></div>
    <div class="kv"><span>Time (UTC)</span><span class="white">${esc(e.ts)}</span></div>
    <div class="kv"><span>Where</span><span class="white">${esc(e.country_name || e.country || e.entity || '—')}${e.lat != null ? ` (${e.lat.toFixed(2)}, ${e.lon.toFixed(2)})` : ''}</span></div>
    <div class="kv"><span>Source</span><span>${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.source)} ↗</a>` : esc(e.source)}</span></div>
    <div class="kv"><span>Fetched</span><span>${esc(e.fetched_at)}</span></div>
    ${e.commodities?.length ? `<div class="kv"><span>Tags</span><span class="cyan">${esc(e.commodities.join(' '))}</span></div>` : ''}
    <div class="sub">MARKET READ-THROUGH</div>
    ${e.readthrough ? `<div class="white">${esc(e.readthrough.text)}</div>${(e.readthrough.assets || []).map((a) => `<div class="kv"><span>${esc(a.asset)}</span><span class="${a.direction === 'up' ? 'up' : 'down'}">${a.direction === 'up' ? '▲ up' : a.direction === 'down' ? '▼ down' : esc(a.direction)} · p=${a.p.toFixed(2)}</span></div>`).join('')}<div class="note">p = ${esc(e.readthrough.basis)}: analyst-set prior in config, not a model estimate.</div>` : '<div class="note">No read-through mapped for this country/category (config/readthrough.yaml).</div>'}
    ${e.trigger ? `<div class="sub">TRIGGER DATA POINTS</div><pre class="trig">${esc(JSON.stringify(e.trigger, null, 1))}</pre>` : ''}
    ${e.lat != null ? `<button id="ev-focus">FOCUS ON GLOBE</button>` : ''}
    ${e.source === 'GDELT' ? '<div class="note">GDELT is machine-coded from news; verify via the source link before acting.</div>' : ''}`;
}
document.addEventListener('click', (ev) => {
  if (ev.target.id === 'ev-focus' && S.sel?.type === 'event') {
    const e = [...S.feed, ...S.gEvents].find((x) => x.id === S.sel.id);
    if (e) globe.focus(e.lat, e.lon, 1.2);
  }
});
function holdingHtml(h, news = []) {
  if (!h) return head('HOLDING') + '<div class="note">Not loaded.</div>';
  const newsBlock = `<div class="sub">COMPANY NEWS (14d) <span class="dim">${esc(h.ticker)} CN &lt;GO&gt; for live</span></div>${news.length ? topNewsHtml(news) : '<div class="note">No tagged headlines (matching uses company names in config/watchlist.yaml).</div>'}`;
  return `${head(`${esc(h.ticker)} <span style="color:#033">${esc(h.name || '')}</span>`)}
    <div class="kv"><span>Side</span><span class="${h.side === 'long' ? 'up' : 'down'}">${h.side.toUpperCase()}</span></div>
    <div class="kv"><span>Price</span><span class="white">${h.price ? `${fmtNum(h.price.value)} <span class="dim">${esc(shortDate(h.price.observation_date))} ${esc(h.price.source)}</span> ${statusBadge(h.price.status, h.price.provider_age_s)}` : '<span class="dim">no market provider configured</span>'}</span></div>
    <div class="kv"><span>Geographies</span><span class="cyan">${esc((h.geographies || []).join(' ') || '—')}</span></div>
    <div class="kv"><span>Commodities</span><span class="cyan">${esc((h.commodities || []).join(' ') || '—')}</span></div>
    <div class="kv"><span>Chokepoints</span><span class="cyan">${esc((h.chokepoints || []).join(' ') || '—')}</span></div>
    <div class="sub">ALERTS / EVENTS AFFECTING (7d, sev ≥3) — ${h.hit_count}</div>
    ${h.hits.length ? h.hits.map((x) => `<div class="mrow click" data-pick='${esc(JSON.stringify({ type: 'event', id: x.id }))}' style="cursor:pointer;grid-template-columns:20px 1fr"><span class="sevbox sev${x.severity}">${x.severity}</span><span class="white" style="white-space:normal">${esc(x.title)}</span><span class="m">${esc(shortDate(x.ts))} · ${esc(x.reasons.join(', '))} ${rtHtml(x.readthrough)}</span></div>`).join('') : '<div class="note">Nothing in window.</div>'}
    ${newsBlock}
    <div class="note">Matching: geography = event country ∈ holding geographies; chokepoint = event near/at a listed chokepoint; exposure = commodity overlap (rule alerts or sev ≥4 only). Edit config/watchlist.yaml.</div>`;
}
async function healthHtml() {
  const [quar, disc] = await Promise.all([api('/api/quarantine?limit=15'), api('/api/discrepancies?limit=15')]);
  return `${head('DATA HEALTH')}
    <table><thead><tr><th>FEED</th><th>$</th><th>STATUS</th><th>LAST OK</th><th>NEXT</th></tr></thead><tbody>
    ${S.health.map((h) => `<tr title="${esc((h.description || '') + (h.last_error ? '\nERROR: ' + h.last_error : '') + (h.notes ? '\n' + h.notes : ''))}"><td class="white">${esc(h.provider)}</td><td class="${h.free ? 'up' : 'amber'}">${h.free ? 'FREE' : 'PAID'}</td>
      <td><span class="hcell h-${h.enabled ? h.status : 'DISABLED'}">${h.enabled ? esc(h.status) : 'OFF'}</span></td>
      <td class="dim">${h.last_success ? fmtAge(ageOf(h.last_success)) + ' ago' : 'never'}</td><td class="dim">${h.next_poll ? esc(h.next_poll.slice(0, 10) === new Date().toISOString().slice(0, 10) ? h.next_poll.slice(11, 16) : h.next_poll.slice(5, 16).replace('T', ' ')) : '—'}</td></tr>
      ${!h.enabled && h.reason ? `<tr><td colspan="5" class="dim" style="white-space:normal">↳ ${esc(h.reason)}${h.signup_url ? ` · <a href="${esc(h.signup_url.split(' ')[0])}" target="_blank" rel="noopener noreferrer">sign up</a>` : ''}</td></tr>` : ''}
      ${h.enabled && h.last_error ? `<tr><td colspan="5" class="down" style="white-space:normal">↳ ${esc(h.last_error.slice(0, 220))}</td></tr>` : ''}`).join('')}
    </tbody></table>
    <div class="sub">QUARANTINE (sanity bounds) — latest</div>
    ${quar.length ? quar.map((x) => `<div class="note">${esc(x.source)} ${esc(x.entity)}.${esc(x.metric)} ${esc(x.observation_date)} = ${x.value}: ${esc(x.reason)}</div>`).join('') : '<div class="note">Empty.</div>'}
    <div class="sub">CROSS-SOURCE DISCREPANCIES (EIA vs JODI)</div>
    ${disc.length ? disc.map((x) => `<div class="note">${esc(x.entity)} ${esc(x.period)}: ${esc(x.source_a)} ${fmtNum(x.value_a)} vs ${esc(x.source_b)} ${fmtNum(x.value_b)} (${x.pct}%)</div>`).join('') : '<div class="note">None flagged (needs both EIA and JODI).</div>'}
    <div class="note">Full audit of every provider response: <a href="/api/audit" target="_blank">/api/audit</a> · <a href="/api/quarantine" target="_blank">/api/quarantine</a></div>`;
}
// ----------------------------------------------------------- vessels & reference objects
const pickAttr = (o) => `data-pick='${esc(JSON.stringify(o))}'`;
async function vesselHtml(mmsi) {
  const v = await api(`/api/vessel/${mmsi}?track_hours=72`);
  S.selVessel = v;
  composeGlobe();
  if (v.lat != null) globe.focus(v.lat, v.lon, 0.7);
  const color = v.stale ? TANKER_COLOR.stale : !v.moving ? TANKER_COLOR.idle : v.laden === 'LADEN?' ? TANKER_COLOR.laden : TANKER_COLOR.ballast;
  const ext = (label, url) => `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${label} ↗</a>`;
  return `${head(`${esc(v.name || 'MMSI ' + v.mmsi)} <span style="color:#033">${esc(v.size_class || '')} TANKER</span>`)}
    <div class="kv"><span>State</span><span style="color:${color}">${v.stale ? 'STALE AIS' : v.moving ? 'UNDER WAY' : 'ANCHORED / MOORED'} · ${esc(v.nav_text || '—')}</span></div>
    <div class="kv"><span>Position</span><span class="white">${v.lat?.toFixed(4)}, ${v.lon?.toFixed(4)}</span></div>
    <div class="kv"><span>Speed / course</span><span class="white">${v.sog != null ? v.sog.toFixed(1) + ' kn' : '—'} / ${v.cog != null ? Math.round(v.cog) + '°' : '—'}${v.heading != null ? ' (hdg ' + v.heading + '°)' : ''}</span></div>
    <div class="kv"><span>Last AIS report</span><span>${esc(v.pos_ts || '—')} · ${fmtAge(v.age_s)} ago · ${esc(v.source)}</span></div>
    <div class="kv"><span>Destination (self-reported)</span><span class="white">${esc(v.destination || '—')}</span></div>
    <div class="kv"><span>ETA (self-reported)</span><span>${esc(v.eta || '—')}</span></div>
    <div class="sub">VESSEL</div>
    <div class="kv"><span>MMSI · IMO</span><span>${v.mmsi} · ${v.imo || '—'}</span></div>
    <div class="kv"><span>Call sign</span><span>${esc(v.callsign || '—')}</span></div>
    <div class="kv"><span>Flag (from MMSI)</span><span class="white">${esc(v.flag || 'unknown MID')}</span></div>
    <div class="kv"><span>AIS ship type</span><span>${v.ship_type} (tanker)</span></div>
    <div class="kv"><span>Length × beam</span><span>${v.length || '—'} m × ${v.beam || '—'} m → ${esc(v.size_class || '—')}</span></div>
    <div class="kv"><span>Draught (reported)</span><span>${v.draught != null ? v.draught.toFixed(1) + ' m' : '—'} ${v.laden ? `<span class="flag" title="Heuristic: draught vs typical laden draught for the size class. Crews often leave this field stale.">${esc(v.laden)}</span>` : ''}</span></div>
    <div class="sub">CONTEXT</div>
    ${v.nearest_chokepoint ? `<div class="kv click" ${pickAttr({ type: 'chokepoint', id: v.nearest_chokepoint.id })}><span>Nearest chokepoint</span><span class="cyan">${esc(v.nearest_chokepoint.name)} · ${v.nearest_chokepoint.km} km</span></div>` : ''}
    ${v.nearest_site ? `<div class="kv click" ${pickAttr({ type: 'site', id: v.nearest_site.id })}><span>Nearest listed site</span><span class="cyan">${esc(v.nearest_site.name)} · ${v.nearest_site.km} km</span></div>` : ''}
    <div class="kv"><span>Track (72h)</span><span>${v.track.length} points (white line on globe)</span></div>
    <div class="note">Lookups: ${ext('MarineTraffic', `https://www.marinetraffic.com/en/ais/details/ships/mmsi:${v.mmsi}`)} · ${v.imo ? ext('Equasis (IMO)', 'https://www.equasis.org/') : ''}</div>
    <div class="note">${aisCoverageNote()} Size class, flag and laden/ballast are derived from AIS fields and are heuristics.</div>`;
}
async function siteHtml(id) {
  const x = S.infra?.sites.find((y) => y.id === id);
  if (!x) return head('SITE') + '<div class="note">Unknown site.</div>';
  const near = await api(`/api/vessels/near?lat=${x.lat}&lon=${x.lon}&km_radius=40`);
  const routes = (S.infra.routes || []).filter((r) => Math.hypot(r.from[0] - x.lat, r.from[1] - x.lon) < 0.6 || Math.hypot(r.to[0] - x.lat, r.to[1] - x.lon) < 0.6);
  return `${head(`${esc(x.name)} <span style="color:#033">${esc(x.type.replace('_', ' ').toUpperCase())}</span>`)}
    <div class="kv click" ${pickAttr({ type: 'country', id: x.iso3 })}><span>Country</span><span class="cyan">${esc(S.countries[x.iso3]?.name || x.iso3)} ▸</span></div>
    <div class="kv"><span>Location (approx.)</span><span>${x.lat.toFixed(2)}, ${x.lon.toFixed(2)}</span></div>
    <div class="kv"><span>Capacity</span><span class="dim">not shown: no licence-clean free source (see README)</span></div>
    ${routes.length ? `<div class="sub">SCHEMATIC LANES FROM/TO HERE</div>${routes.map((r) => `<div class="kv click" ${pickAttr({ type: 'route', id: r.id })}><span class="cyan">${esc(r.name)}</span><span>${r.commodity} ▸</span></div>`).join('')}` : ''}
    <div class="sub">TANKERS WITHIN 40 KM (live AIS)</div>${vesselRows(near)}
    <div class="note">${aisCoverageNote()}</div>`;
}
function pipelineHtml(id) {
  const x = S.infra?.pipelines.find((y) => y.id === id);
  if (!x) return head('PIPELINE') + '<div class="note">Unknown pipeline.</div>';
  const a = x.path[0], b = x.path[x.path.length - 1];
  globe.focus((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 1.4);
  return `${head(`${esc(x.name)} <span style="color:#033">${esc(x.commodity)} PIPELINE</span>`)}
    ${x.note ? `<div class="kv"><span>Status (reference)</span><span class="white">${esc(x.note)}</span></div>` : ''}
    <div class="kv"><span>From</span><span>${a[0].toFixed(2)}, ${a[1].toFixed(2)}</span></div>
    <div class="kv"><span>To</span><span>${b[0].toFixed(2)}, ${b[1].toFixed(2)}</span></div>
    <div class="kv"><span>Waypoints</span><span>${x.path.length}</span></div>
    <div class="note">Schematic geometry from config/reference/infrastructure.yaml. No live flow data: none is published free for these lines.</div>`;
}
function routeHtml(id) {
  const r = S.infra?.routes.find((y) => y.id === id);
  if (!r) return head('LANE') + '<div class="note">Unknown lane.</div>';
  const byId = Object.fromEntries(S.chokepoints.map((c) => [c.id, c]));
  return `${head(`${esc(r.name)} <span style="color:#033">${esc(r.commodity)} LANE</span>`)}
    <div class="sub">CHOKEPOINTS ON THIS LANE</div>
    ${r.via.length ? r.via.map((v) => { const c = byId[v]; return `<div class="kv click" ${pickAttr({ type: 'chokepoint', id: v })}><span class="cyan">${esc(c?.name || v)} ▸</span><span style="color:${STATE_COLOR[c?.state] || '#888'}">${esc(c?.state || '—')} ${c?.pct_vs_baseline != null ? fmtChg(c.pct_vs_baseline) : ''}${c?.ais_tankers != null ? ` · ${c.ais_tankers} AIS tankers` : ''}</span></div>`; }).join('') : '<div class="note">Open ocean, no chokepoint.</div>'}
    <div class="note">Schematic lane. Colour = worst chokepoint state on the way. No volumes implied.</div>`;
}
async function locationHtml(lat, lon) {
  const near = await api(`/api/vessels/near?lat=${lat}&lon=${lon}&km_radius=150`);
  const kmTo = (a, b) => { const R = Math.PI / 180, x = Math.sin((b[0] - a[0]) * R / 2) ** 2 + Math.cos(a[0] * R) * Math.cos(b[0] * R) * Math.sin((b[1] - a[1]) * R / 2) ** 2; return 12742 * Math.asin(Math.sqrt(x)); };
  const cps = S.chokepoints.map((c) => [kmTo([lat, lon], [c.lat, c.lon]), c]).sort((a, b) => a[0] - b[0]).slice(0, 3);
  const evs = S.gEvents.filter((e) => e.lat != null && kmTo([lat, lon], [e.lat, e.lon]) < 400).slice(0, 8);
  return `${head(`LOCATION <span style="color:#033">${lat.toFixed(2)}, ${lon.toFixed(2)}</span>`)}
    <div class="sub">NEAREST CHOKEPOINTS</div>
    ${cps.map(([d, c]) => `<div class="kv click" ${pickAttr({ type: 'chokepoint', id: c.id })}><span class="cyan">${esc(c.name)} ▸</span><span>${Math.round(d)} km · <span style="color:${STATE_COLOR[c.state]}">${esc(c.state)}</span></span></div>`).join('')}
    <div class="sub">TANKERS WITHIN 150 KM</div>${vesselRows(near)}
    <div class="sub">EVENTS WITHIN 400 KM (globe window)</div>${evs.length ? evs.map(evRow).join('') : '<div class="note">None.</div>'}`;
}
async function providerHtml(name) {
  const h = await api(`/api/provider/${name}`);
  return `${head(`${esc(h.provider)} <span style="color:#033">${h.free ? 'FREE' : 'PAID'} FEED</span>`)}
    <div class="note white">${esc(h.description)}</div>
    <div class="kv"><span>Status</span><span><span class="hcell h-${h.enabled ? h.status : 'DISABLED'}">${h.enabled ? esc(h.status) : 'OFF'}</span> ${h.reason ? esc(h.reason) : ''}</span></div>
    <div class="kv"><span>Last success</span><span>${esc(h.last_success || 'never')}${h.last_success ? ' (' + fmtAge(ageOf(h.last_success)) + ' ago)' : ''}</span></div>
    <div class="kv"><span>Next poll</span><span>${esc(h.next_poll || '—')}</span></div>
    <div class="kv"><span>Interval · stale after</span><span>${h.interval_s}s · ${h.stale_after_s}s</span></div>
    <div class="kv"><span>Consecutive failures</span><span class="${h.consecutive_failures ? 'down' : ''}">${h.consecutive_failures}</span></div>
    ${h.last_error ? `<div class="note down">Last error: ${esc(h.last_error)}</div>` : ''}
    ${h.notes ? `<div class="note">${esc(h.notes)}</div>` : ''}
    ${h.signup_url ? `<div class="note">Sign-up / docs: ${esc(h.signup_url)}</div>` : ''}
    <div class="sub">LAST RESPONSES (audit)</div>
    ${h.audit.length ? `<table class="ft"><tbody>${h.audit.map((a) => `<tr title="${esc(a.url)}"><td class="dim">${esc((a.responded_at || '').slice(11, 19))}</td><td class="${a.status && a.status < 400 ? 'up' : 'down'}">${a.status ?? 'ERR'}</td><td class="num">${a.latency_ms}ms</td><td class="dim">${esc((a.url || '').replace(/^https?:\/\//, '').slice(0, 42))}</td></tr>`).join('')}</tbody></table>` : '<div class="note">No HTTP calls logged (websocket feeds log sessions in notes).</div>'}`;
}
function vsearchHtml(q, rows) {
  return `${head(`VESSEL SEARCH <span style="color:#033">${esc(q)}</span>`)}${vesselRows(rows, 40)}`;
}

function helpHtml() {
  return `${head('HELP')}
    <div class="sub">COMMAND LINE ( / to focus )</div>
    <div class="note"><span class="white">CHN, GERMANY, BR</span> → focus country · <span class="white">WTI, BRENT, VIX, 10Y, 2S10S</span> → KPI · <span class="white">HORMUZ, SUEZ, MALACCA</span> → chokepoint · <span class="white">XOM</span> → holding · <span class="white">GDP, CPI, RATES, YIELD, OIL, FX, CA</span> → choropleth · <span class="white">LAYER 3</span> · <span class="white">HEALTH</span> · <span class="white">LIVE</span> · <span class="white">R</span> reset</div>
    <div class="sub">KEYS</div>
    <div class="note"><span class="white">1-7</span> toggle layers · <span class="white">/</span> command line · <span class="white">R</span> reset view · <span class="white">Esc</span> close panel</div>
    <div class="sub">STATUS BADGES</div>
    <div class="note">${statusBadge('LIVE')} realtime feed healthy · ${statusBadge('DELAYED')} healthy, end-of-day/periodic data · ${statusBadge('STALE', 3600)} feed failing, last good value + age · ${statusBadge('ERROR')} never received</div>
    <div class="note">Every number shows its observation date and source. Production is monthly with a 1-3 month lag; chokepoints ~1 week.</div>`;
}

// ============================================================ command line + keys
function addYellowKey(yk) {
  const i = $('#cmd');
  i.value = `${i.value.trim().replace(/\s+(GOVT|CORP|MTGE|M-MKT|MUNI|PFD|EQUITY|COMDTY|INDEX|CRNCY)$/i, '')} ${yk} `.trimStart();
  i.focus();
}
const NOT_FREE = { WEI: 'needs a market-data provider (world equity indices)',
  CT: 'futures curves need a paid market feed', FXC: 'not built yet', MON: 'not built yet', CORR: 'not built yet' };

async function runCommand(raw) {
  const t = raw.trim().toUpperCase().replace(/\s*<?GO>?$/, '');
  $('#cmd').value = '';
  if (!t) return;
  // "n <GO>" picks item n from the focused panel's numbered menu
  if (/^\d{1,2}$/.test(t) && ws.hasMenu()) return ws.select(+t);
  if (t === 'HELP' || t === '?') return ws.open('HELP');
  if (t === 'ABOUT' || t === 'CREDITS') return ws.open('ABOUT');
  if (t === 'GC' || t === 'ECO') return ws.open(t);
  const nm = t.match(/^(TOP|N|NEWS)(?:\s+(WAR|OIL|EQUITIES|MARKETS|MACRO|SHIPPING|WORLD))?$/);
  if (nm) return ws.open('NEWS', { topic: nm[2] || 'ALL' });
  if (t === 'MENU' || t === 'BACK') { if (!ws.back()) msg('nothing to go back to'); return; }
  const add = t.match(/^ADD\s+(GLOBE|KPI|MONITOR|INSPECTOR|WIRE|TANKERS|CHOKEPOINTS|PRODUCTION|COMMODITIES|PORTFOLIO)$/);
  if (add) return ws.addSingleton({ MONITOR: 'kpi' }[add[1]] || add[1].toLowerCase());
  const tab = t.match(/^(?:TAB|WS)\s+(.+)$/);
  if (tab) {
    const i = /^\d+$/.test(tab[1]) ? +tab[1] - 1 : ws.state.tabs.findIndex((x) => x.name === tab[1]);
    if (ws.state.tabs[i]) return ws.switchTab(i);
    msg(`${tab[1]}: no such workspace`); return;
  }
  if (t === 'MAX') { if (ws.focusId) ws.toggleMax(ws.focusId); return; }
  if (t === 'R' || t === 'RESET') { globe.reset(); return; }
  if (t === 'HEALTH') return select({ type: 'health' });
  if (t === 'LIVE') return setWindow(0);
  const lm = t.match(/^(?:LAYER\s*)?([1-8])$/);
  if (lm) return toggleLayer(LAYERS[+lm[1] - 1].id);
  if (NOT_FREE[t]) { msg(`${t}: ${NOT_FREE[t]}`); return; }
  try {
    const r = await api(`/api/command?q=${encodeURIComponent(t)}`);
    const fn = r.function || 'MENU';
    msg(`${r.security.ticker} ${r.yk} ${fn}`);
    if (fn === 'CN' && r.security.kind !== 'equity') { msg('CN works on EQUITY tickers, e.g. XOM US EQUITY CN'); return; }
    return fn === 'GC' || fn === 'ECO' || fn === 'HELP' ? ws.open(fn) : ws.open(fn, { sec: r.security });
  } catch (e) {
    if (e.message.startsWith('400')) { msg(e.message.replace(/^400 /, '')); return; }
  }
  // not a security: fall back to globe entities (countries, chokepoints, holdings, map metrics)
  try {
    const r = await api(`/api/search?q=${encodeURIComponent(t)}`);
    msg(`${t} → ${r.type} ${r.id}`);
    if (r.type === 'metric') return setMetric(r.id);
    if (r.type === 'holding' && !S.layers.port) toggleLayer('port');
    select({ type: r.type, id: r.id });
  } catch (e) {
    const vs = await api(`/api/vessels/search?q=${encodeURIComponent(t)}`).catch(() => []);
    if (vs.length === 1) { msg(`${t} → tanker ${vs[0].name}`); return select({ type: 'vessel', id: vs[0].mmsi }); }
    if (vs.length > 1) { msg(`${t}: ${vs.length} tankers`); return select({ type: 'vsearch', q: t, rows: vs }); }
    msg(`${t}: no match (HELP <GO> for syntax)`);
  }
}
function bindKeys() {
  document.addEventListener('keydown', (e) => {
    const typing = ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName);
    if (YELLOW_KEYS[e.key]) { e.preventDefault(); addYellowKey(YELLOW_KEYS[e.key]); return; }
    if (e.key === 'Escape') {
      if (typing) document.activeElement.blur();
      else if (ws.unmaximize()) { /* Esc first restores a maximized panel */ }
      else if (!ws.back()) closeDetail();   // then MENU (back) in the focused function panel
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === '/') { e.preventDefault(); $('#cmd').focus(); return; }
    if (e.key === 'r' || e.key === 'R') { globe.reset(); return; }
    if (/^[1-8]$/.test(e.key)) toggleLayer(LAYERS[+e.key - 1].id);
  });
}

S.feedMode = 'events';
boot().catch((e) => { document.body.insertAdjacentHTML('afterbegin', `<div id="demo-banner">RIOTTO WORLD TERMINAL BOOT FAILED: ${esc(e.message)}</div>`); console.error(e); });
