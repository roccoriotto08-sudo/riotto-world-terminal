// Globe view: thin wrapper over globe.gl. Polygons are created ONCE; data refreshes
// only swap colour accessors or the small marker collections that changed, so a
// price tick never rebuilds the scene.
import { ShipLayer } from './ships.js';
import { esc, fmtNum, flagsHtml, scaleColor, shortDate } from './util.js';

export const TANKER_COLOR = { laden: '#ff7a00', ballast: '#00e5ff', idle: '#ffd54f', stale: '#4a4a4a' };

export class GlobeView {
  constructor(el, countries, handlers) {
    this.el = el;
    this.h = handlers;
    this.values = {};
    this.meta = null;
    this.selected = null;
    this.hover = null;
    this.highlight = new Set();
    this.sig = {};
    this.idleTimer = null;

    const G = window.Globe;
    try { this.g = new G(el, { animateIn: false }); } catch (_) { this.g = G({ animateIn: false })(el); }
    const g = this.g;
    g.backgroundColor('#000000')
      .showAtmosphere(false)
      .showGraticules(true)
      .polygonsTransitionDuration(0)
      .polygonsData(countries.features)
      .polygonSideColor(() => 'rgba(0,0,0,0)')
      .polygonStrokeColor((f) => this._stroke(f))
      .polygonAltitude((f) => this._alt(f))
      .polygonCapColor((f) => this._cap(f))
      .polygonLabel((f) => this._tip(f))
      .onPolygonHover((f) => { this.hover = f ? f.properties.iso3 : null; this._refreshPolys(); })
      .onPolygonClick((f, ev, coords) => {
        if (this._swallow()) return;          // that click was on a tanker drawn over the country
        this.h.onCountry(f.properties.iso3, coords);
      });
    const mat = g.globeMaterial();
    if (mat && mat.color) mat.color.set('#05080b');
    if (mat && 'shininess' in mat) mat.shininess = 0;
    // Perf: country side walls are invisible at this altitude but cost one draw call each
    // (~290). A non-visible material skips them entirely: measured 14.7ms -> 7.5ms per frame.
    if (mat && typeof g.polygonSideMaterial === 'function') {
      const hidden = new mat.constructor({ visible: false });
      g.polygonSideMaterial(() => hidden);
    }

    g.pointsTransitionDuration(0).pointLat('lat').pointLng('lon').pointColor('color')
      .pointAltitude('alt').pointRadius('r').pointLabel('label').pointResolution(6)
      .onPointClick((d) => this.h.onPick(d));
    g.labelsTransitionDuration(0).labelLat('lat').labelLng('lon').labelText('text').labelSize('size')
      .labelColor('color').labelDotRadius('dot').labelAltitude(0.026).labelResolution(2)  // above raised polygon caps; 3 = 2x triangles for no visible gain
      .labelLabel('label').onLabelClick((d) => this.h.onPick(d));
    g.ringLat('lat').ringLng('lon').ringAltitude(0.003)
      .ringColor((d) => (t) => `rgba(${d.rgb},${(1 - t).toFixed(3)})`)
      .ringMaxRadius('maxR').ringPropagationSpeed('speed').ringRepeatPeriod('period');
    g.arcsTransitionDuration(0).arcStartLat('sLat').arcStartLng('sLon').arcEndLat('eLat').arcEndLng('eLon')
      .arcColor('color').arcStroke(0.32).arcAltitudeAutoScale(0.25)
      .arcDashLength(0.4).arcDashGap(0.12).arcDashAnimateTime(6000).arcLabel('label')
      .onArcClick((d) => this.h.onPick(d));
    g.pathTransitionDuration(0).pathPoints('path').pathPointLat((p) => p[0]).pathPointLng((p) => p[1])
      .pathColor('color').pathDashLength('dash').pathDashGap('gap').pathLabel('label')
      .onPathClick((d) => this.h.onPick(d));

    // Tankers: animated 3D ships (ships.js, 3 instanced draw calls). They are not globe.gl
    // objects, so hover/click picking is done here in screen space (see _pickTanker).
    this.ships = new ShipLayer(g);
    this.tankers = [];
    this.tip = document.createElement('div');
    this.tip.className = 'float-tooltip-kap tanker-tip';
    this.tip.style.cssText = 'position:absolute;display:none;pointer-events:none;z-index:5';
    el.parentElement.appendChild(this.tip);
    let down = null, lastMove = 0;
    el.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; }, true);
    el.addEventListener('pointerup', (e) => {
      if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;   // a drag, not a click
      const v = this._pickTanker(e);
      // globe.gl reports its own polygon/globe click ~0.5 s later: swallow exactly that one
      if (v) { this.suppressUntil = Date.now() + 1500; this.h.onPick({ pick: { type: 'vessel', id: v.mmsi } }); }
    }, true);
    el.addEventListener('pointermove', (e) => {
      if (!this.tankers.length || Date.now() - lastMove < 40) return;
      lastMove = Date.now();
      const v = this._pickTanker(e);
      if (!v) { this.tip.style.display = 'none'; el.style.cursor = ''; return; }
      const r = el.getBoundingClientRect();
      this.tip.innerHTML = this.h.tankerTip(v);
      this.tip.style.left = `${e.clientX - r.left + 14}px`;
      this.tip.style.top = `${e.clientY - r.top + 10}px`;
      this.tip.style.display = 'block';
      el.style.cursor = 'pointer';
    });
    el.addEventListener('pointerleave', () => { this.tip.style.display = 'none'; });
    g.onGlobeClick(({ lat, lng }) => { if (!this._swallow()) this.h.onLocation(lat, lng); });

    const c = g.controls();
    c.autoRotate = true;
    c.autoRotateSpeed = 0.28;
    c.enableDamping = true;
    c.minDistance = 120;
    c.maxDistance = 900;
    // pause auto-rotate on interaction, resume after 20s idle
    c.addEventListener('start', () => this.pauseRotate());
    // Labels/markers are sized in degrees, so they balloon when zoomed in. Rescale them to the
    // camera altitude once a zoom/pan ends (never per frame: re-sizing rebuilds label geometry).
    this.k = 1;
    c.addEventListener('end', () => this._rescale());
    el.addEventListener('wheel', () => this.pauseRotate(), { passive: true });

    // 60fps budget: cap the backing-store pixel ratio (retina at 2x quadruples fragment cost)
    g.renderer().setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    this.reset(0);
    // globe.gl defaults to the window size; pin it to the container on every layout change
    const fit = () => {
      if (el.clientWidth && (g.width() !== el.clientWidth || g.height() !== el.clientHeight)) {
        g.width(el.clientWidth).height(el.clientHeight);
      }
    };
    fit();
    new ResizeObserver(fit).observe(el);
    window.addEventListener('resize', () => requestAnimationFrame(fit));
  }

  pauseRotate() {
    const c = this.g.controls();
    c.autoRotate = false;
    clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => { c.autoRotate = true; }, 20000);
  }

  _rescale() {
    const alt = this.g.pointOfView().altitude;
    const k = Math.max(0.02, Math.min(1, alt / 1.9));
    if (Math.abs(k - this.k) / this.k < 0.15) return;
    this.k = k;
    this.g.labelSize((d) => d.size * k).labelDotRadius((d) => d.dot * k)
      .pointRadius((d) => d.r * Math.max(k, 0.05)).pointAltitude((d) => d.alt * k);
  }

  reset(ms = 900) { this.g.pointOfView({ lat: 22, lng: 40, altitude: 1.9 }, ms); setTimeout(() => this._rescale(), ms + 50); }

  focus(lat, lon, altitude = 1.4) {
    if (lat == null || lon == null) return;
    this.pauseRotate();
    this.g.pointOfView({ lat, lng: lon, altitude }, 900);
    setTimeout(() => this._rescale(), 950);
  }

  // ------------------------------------------------------------ polygons
  _cap(f) {
    const cell = this.values[f.properties.iso3];
    if (!cell || !this.meta) return '#0d0d0d';
    const col = scaleColor(this.meta, cell.value);
    const old = cell.flags && cell.flags.includes('OLD');
    return col + (old ? '66' : 'e6');
  }
  _stroke(f) {
    const iso = f.properties.iso3;
    if (iso === this.selected) return '#ffffff';
    if (this.highlight.has(iso)) return '#00e5ff';
    if (iso === this.hover) return '#ffb000';
    return '#2b2b2b';
  }
  _alt(f) {
    const iso = f.properties.iso3;
    if (iso === this.selected) return 0.022;
    if (this.highlight.has(iso)) return 0.014;
    return iso === this.hover ? 0.012 : 0.006;
  }
  _tip(f) {
    const p = f.properties, cell = this.values[p.iso3], m = this.meta;
    let body = '<span class="dim">no data for this metric</span>';
    if (cell && m) {
      body = `<span class="t">${fmtNum(cell.value, m.unit)} ${esc(m.unit)}</span> ${flagsHtml(cell.flags)}<br>`
        + `<span class="dim">as of ${esc(cell.period || shortDate(cell.observation_date))} · ${esc(cell.source)}</span>`;
      if (cell.level !== undefined) body += `<br><span class="dim">level ${fmtNum(cell.level)} ${esc(cell.level_unit || '')}</span>`;
    }
    return `<b>${esc(p.name)}</b> <span class="dim">${esc(p.iso3)}</span><br>${m ? esc(m.label) + ': ' : ''}${body}`;
  }
  _refreshPolys() {
    // re-binding the accessors makes globe.gl re-evaluate materials without rebuilding geometry
    this.g.polygonCapColor((f) => this._cap(f)).polygonStrokeColor((f) => this._stroke(f)).polygonAltitude((f) => this._alt(f));
  }
  setChoropleth(meta, values) { this.meta = meta; this.values = values || {}; this._refreshPolys(); }
  setSelected(iso3) { this.selected = iso3; this._refreshPolys(); }
  setHighlight(set) { this.highlight = set; this._refreshPolys(); }

  // ---------------------------------------------------- marker collections
  // Each collection is only re-set when its signature changes.
  _set(kind, data) {
    const sig = JSON.stringify(data.map((d) => [d.id, d.color, d.text, d.size, d.alt, d.r, d.label && d.label.length]));
    if (this.sig[kind] === sig) return;
    this.sig[kind] = sig;
    ({ points: () => this.g.pointsData(data), labels: () => this.g.labelsData(data), rings: () => this.g.ringsData(data),
       arcs: () => this.g.arcsData(data), paths: () => this.g.pathsData(data) })[kind]();
  }
  _swallow() {
    const hit = Date.now() < (this.suppressUntil || 0);
    this.suppressUntil = 0;
    return hit;
  }

  // ------------------------------------------------------------ tankers
  setTankers(list, fetchedAt) {
    this.tankers = list;
    this.ships.setData(list, fetchedAt);
    this.ships.setVisible(list.length > 0);
  }
  _visible(lat, lon) {
    const pov = this.g.pointOfView();
    const toR = Math.PI / 180;
    const cosd = Math.sin(lat * toR) * Math.sin(pov.lat * toR) + Math.cos(lat * toR) * Math.cos(pov.lat * toR) * Math.cos((lon - pov.lng) * toR);
    return cosd > 1 / (1 + pov.altitude) + 0.02;     // inside the visible cap of the sphere
  }
  _pickTanker(e) {
    if (!this.tankers.length) return null;
    const r = this.el.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    let best = null, bd = 8;                         // pick radius in px
    for (const v of this.tankers) {
      const lat = v.drLat ?? v.lat, lon = v.drLon ?? v.lon;     // where the ship is drawn
      if (!this._visible(lat, lon)) continue;
      const s = this.g.getScreenCoords(lat, lon, 0.0015);
      const d = Math.hypot(s.x - x, s.y - y);
      if (d < bd) { bd = d; best = v; }
    }
    return best;
  }

  setLayers({ points = [], labels = [], rings = [], arcs = [], paths = [] }) {
    this._set('points', points); this._set('labels', labels); this._set('rings', rings);
    this._set('arcs', arcs); this._set('paths', paths);
  }
}
