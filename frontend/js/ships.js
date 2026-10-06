// Animated 3D tankers on the globe.
//
// One InstancedMesh per part (hull, bridge, wake) => 3 draw calls for any number of ships.
// three.js r185 is vendored to match the copy bundled inside globe.gl, so these objects
// render in globe.gl's own scene and renderer.
//
// Motion honesty:
//   * heading = AIS true heading (else course over ground)
//   * a ship UNDER WAY with a report <= 30 min old is advanced along its course at its reported
//     speed ("dead reckoning"), capped at 30 min, and flagged so the tooltip says "est. +N min".
//     Everything else sits exactly on its last reported position.
//   * roll/bob and the wake shimmer are cosmetic and do not change position.
import * as THREE from '../vendor/three.module.min.js';

const DR_MAX_S = 30 * 60;
const STATE_COLOR = { laden: 0xff7a00, ballast: 0x00e5ff, idle: 0xffd54f, stale: 0x555555 };
const CLASS_SCALE = { VLCC: 1.35, Suezmax: 1.2, Aframax: 1.05, Panamax: 0.95, MR: 0.85, Small: 0.7 };
const NM_PER_DEG = 60;

function hullGeometry() {
  // deck outline (x = beam, y = forward): square stern, parallel midbody, raked bow
  const s = new THREE.Shape();
  s.moveTo(-0.17, -0.5); s.lineTo(0.17, -0.5); s.lineTo(0.17, 0.28);
  s.quadraticCurveTo(0.15, 0.45, 0, 0.5); s.quadraticCurveTo(-0.15, 0.45, -0.17, 0.28); s.closePath();
  const g = new THREE.ExtrudeGeometry(s, { depth: 0.07, bevelEnabled: false });   // extruded along +z = up
  g.translate(0, 0, -0.02);    // sit slightly "in" the water
  return g;
}
function bridgeGeometry() {
  const g = new THREE.BoxGeometry(0.3, 0.1, 0.12);
  g.translate(0, -0.4, 0.1);   // accommodation block at the stern
  return g;
}
function wakeGeometry() {
  // flat V with its apex at the origin, opening backwards (-y); placed at the stern per ship
  const g = new THREE.BufferGeometry();
  const v = new Float32Array([0, 0, 0.01, -0.28, -1, 0.01, -0.18, -1, 0.01,
                              0, 0, 0.01, 0.18, -1, 0.01, 0.28, -1, 0.01]);
  g.setAttribute('position', new THREE.BufferAttribute(v, 3));
  g.computeVertexNormals();
  return g;
}

function stateOf(v) {
  return v.stale ? 'stale' : !v.moving ? 'idle' : v.laden === 'LADEN?' ? 'laden' : 'ballast';
}

// Great-circle destination from (lat, lon) along bearing (deg) for distance (nm).
function advance(lat, lon, bearing, nm) {
  const R = Math.PI / 180, d = (nm / NM_PER_DEG) * R, b = bearing * R, p1 = lat * R, l1 = lon * R;
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
  const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
  return [p2 / R, ((l2 / R + 540) % 360) - 180];
}

export class ShipLayer {
  constructor(globe) {
    this.globe = globe;               // globe.gl instance
    this.ships = [];
    this.cap = 0;
    this.group = new THREE.Group();
    this.group.name = 'riotto-tankers';
    globe.scene().add(this.group);
    this.hullMat = new THREE.MeshLambertMaterial({ color: 0xffffff });
    this.bridgeMat = new THREE.MeshLambertMaterial({ color: 0xe8e8e8 });
    this.wakeMat = new THREE.MeshBasicMaterial({ color: 0xbfefff, transparent: true, opacity: 0.35,
      depthWrite: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending });
    this.geo = { hull: hullGeometry(), bridge: bridgeGeometry(), wake: wakeGeometry() };
    this._m = new THREE.Matrix4(); this._q = new THREE.Quaternion(); this._s = new THREE.Vector3();
    this._p = new THREE.Vector3(); this._roll = new THREE.Matrix4(); this._c = new THREE.Color();
    this.enabled = true;
    this._loop = this._loop.bind(this);
    requestAnimationFrame(this._loop);
  }

  _ensure(n) {
    if (n <= this.cap) return;
    this.cap = Math.max(64, Math.ceil(n * 1.3));
    for (const k of ['hull', 'bridge', 'wake']) {
      if (this[k]) { this.group.remove(this[k]); this[k].dispose(); }
    }
    this.hull = new THREE.InstancedMesh(this.geo.hull, this.hullMat, this.cap);
    this.bridge = new THREE.InstancedMesh(this.geo.bridge, this.bridgeMat, this.cap);
    this.wake = new THREE.InstancedMesh(this.geo.wake, this.wakeMat, this.cap);
    for (const m of [this.hull, this.bridge, this.wake]) {
      m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      m.frustumCulled = false;        // instances span the globe; the bounding sphere would be wrong
      m.raycast = () => {};           // invisible to globe.gl's hover raycaster (it expects its own objects);
                                      // ships are picked in screen space by GlobeView._pickTanker
      this.group.add(m);
    }
    this.hull.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(this.cap * 3), 3);
  }

  setData(list, fetchedAt = Date.now()) {
    this.fetchedAt = fetchedAt;
    this.ships = list.map((v, i) => ({ v, phase: (v.mmsi % 997) / 997 * Math.PI * 2, i }));
    this._ensure(this.ships.length);
    for (const s of this.ships) {
      const st = stateOf(s.v);
      this._c.setHex(STATE_COLOR[st]);
      this.hull.setColorAt(s.i, this._c);
    }
    this.hull.instanceColor.needsUpdate = true;
    for (const m of [this.hull, this.bridge, this.wake]) m.count = this.ships.length;
    this._place(true);
  }

  setVisible(on) { this.enabled = on; this.group.visible = on; }

  // Position each ship (dead-reckoned where allowed) and build its local frame.
  _place(force) {
    const now = Date.now();
    for (const s of this.ships) {
      const v = s.v;
      const age = (v.age_s || 0) + (now - this.fetchedAt) / 1000;
      const heading = v.heading != null ? v.heading : v.cog != null ? v.cog : 0;
      let lat = v.lat, lon = v.lon, dr = 0;
      if (v.moving && !v.stale && v.sog > 0.5 && v.cog != null && age <= DR_MAX_S) {
        dr = age;
        [lat, lon] = advance(v.lat, v.lon, v.cog, (v.sog * dr) / 3600);
      }
      v.drLat = lat; v.drLon = lon; v.drMin = dr ? Math.round(dr / 60) : 0;
      if (!force && !dr && s.frame) continue;        // static ships keep their cached frame
      const c = this.globe.getCoords(lat, lon, 0.0015);
      const up = new THREE.Vector3(c.x, c.y, c.z).normalize();
      let east = new THREE.Vector3(0, 1, 0).cross(up);
      if (east.lengthSq() < 1e-8) east = new THREE.Vector3(1, 0, 0);
      east.normalize();
      const north = up.clone().cross(east).normalize();
      const h = (heading * Math.PI) / 180;
      const fwd = north.clone().multiplyScalar(Math.cos(h)).add(east.clone().multiplyScalar(Math.sin(h))).normalize();
      const right = fwd.clone().cross(up).normalize();
      s.frame = { pos: new THREE.Vector3(c.x, c.y, c.z), fwd, basis: new THREE.Matrix4().makeBasis(right, fwd, up) };
    }
  }

  _loop(t) {
    requestAnimationFrame(this._loop);
    if (!this.enabled || !this.ships.length || !this.hull) return;
    const alt = this.globe.pointOfView().altitude;
    // keep ships readable at any zoom: size follows camera altitude (screen-ish constant)
    const base = Math.max(0.05, Math.min(5, alt * 4));
    if (!this._lastPlace || t - this._lastPlace > 2000) { this._place(false); this._lastPlace = t; }   // DR creep every 2 s
    const secs = t / 1000;
    this.wakeMat.opacity = 0.22 + 0.12 * Math.sin(secs * 3.1);                                         // shimmer
    for (const s of this.ships) {
      if (!s.frame) continue;
      const v = s.v;
      const k = base * (CLASS_SCALE[v.size_class] || 0.8);
      this._roll.makeRotationY(0.07 * Math.sin(secs * 1.4 + s.phase));                                // gentle roll
      this._m.copy(s.frame.basis).multiply(this._roll);
      this._q.setFromRotationMatrix(this._m);
      const bob = 1 + 0.0004 * Math.sin(secs * 1.9 + s.phase);
      this._p.copy(s.frame.pos).multiplyScalar(bob);
      this._s.set(k, k, k);
      this._m.compose(this._p, this._q, this._s);
      this.hull.setMatrixAt(s.i, this._m);
      this.bridge.setMatrixAt(s.i, this._m);
      const wl = v.moving && !v.stale ? Math.min(2.2, 0.4 + (v.sog || 0) / 8) : 0.0001;               // wake ∝ speed
      this._s.set(k, k * wl, k);
      this._p.addScaledVector(s.frame.fwd, -0.5 * k);                                                // apex at the stern
      this._m.compose(this._p, this._q, this._s);
      this.wake.setMatrixAt(s.i, this._m);
    }
    this.hull.instanceMatrix.needsUpdate = true;
    this.bridge.instanceMatrix.needsUpdate = true;
    this.wake.instanceMatrix.needsUpdate = true;
  }
}
