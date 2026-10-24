// The Simulation tab: each scenario of the last A/B bench replayed as a small 3D world, with our Vigie (world B, "Ours")
// or without it (world A, "Theirs": each agent checks only its own rules). The film comes from vigie/simulation.py.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const sec = document.getElementById("simulation");
const F = await fetch("/dashboard/dashboard-sim.json").then(r => (r.ok ? r.json() : null), () => null);
if (!F) {
  sec.innerHTML = `<p class="empty">No replay yet: run <code>make metrics</code>, then reload.</p>`;
  throw new Error("no /dashboard/dashboard-sim.json");
}

// The film is timed at 3 ticks/s; 1× plays it 4 times slower, flights included (Marius found 3 ticks/s far too fast).
const TPS = 3, SPEEDS = [0.25, 0.5, 1, 2, 4], SLOW = 0.25;
// Where everything stands: the Agents tab's "Who feeds whom" graph turned so that its columns (the services, the quant,
// the traders and payment, compliance, risk, the executor) climb the floor from front to back, leaning LEAN to the right,
// each column's rows in the graph's order, and the Vigie last, after the executor (Marius, 24-10)
const COLUMNS = [["core-ledger", "kyc-docs", "market-feed", "news-wire", "payment-gateway"], ["quant"], ["trader-1", "trader-2", "trader-3", "payment"], ["compliance"], ["risk"], ["executor"], ["vigie"]];
const LEAN = THREE.MathUtils.degToRad(7), sin = Math.sin(LEAN), cos = Math.cos(LEAN);
const tilt = (c, r) => [c * sin + r * cos, r * sin - c * cos], untilt = (x, z) => [x * sin - z * cos, x * cos + z * sin];
const POS = {};
COLUMNS.forEach((ids, k) => ids.forEach((id, i) => (POS[id] = tilt((k - 3) * 9, (i - (ids.length - 1) / 2) * 6))));
POS.vigie = tilt(3.5 * 9, 0);  // half a step further, so that the name at the foot of its tall tower clears the executor
const SKIN = { quant: 0xa78bfa, trader: 0x60a5fa, payment: 0x34d399, compliance: 0xfb923c, risk: 0xf472b6, executor: 0x2dd4bf, vigie: 0x94a3b8 };
const LIGHT = { VERT: 0x34d399, ORANGE: 0xfbbf24, ROUGE: 0xf87171, QUARANTAINE: 0xf87171 };
const LED = { normal: 0x34d399, compromised: 0xef4444, down: 0x1e293b, stale: 0xfbbf24, slow: 0xfbbf24 };
const DOT = { bad: 0xef4444, approve: 0x16a34a, refuse: 0xef4444, wire: 0xeab308, fraud: 0xef4444 };
// a clean link is drawn in the page's ink, faint, on the pale floor; a hovered node's links take its colour
const PLAIN = 0x0e1726, SKY = 0x2b6bff;
const WHY = { ours: "With our Vigie: one view of every agent and service, and a gate before each execution.", theirs: "Without it: each agent checks only its own rules, nothing watches the whole bank." };
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
// Camera presets, to get back to a simple view after orbiting: where the camera looks from, how far, and what it looks at.
const VIEWS = { "3/4": { dir: [0.5, 20.5, 24.5], dist: 42 }, Top: { dir: [0, 1, 0.02], dist: 49 }, Front: { dir: [0, 0.25, 1], dist: 45 }, Agents: { dir: [0, 1.3, 1], dist: 31, on: "agents" } };

// What stops the replay when its setting is on: the kinds of event simulation.py writes
const STOPS = { stopBad: ["bad", "warn"], stopGood: ["good"] };
// The Settings box's choices, kept by this browser as the recon keeps its own
const set = { display: "3D", view: "3/4", speed: 1, world: "ours", stopBad: false, stopGood: false, focus: true, reads: true, bubbles: true, helper: true, details: true, ...JSON.parse(localStorage.getItem("vigie.sim") || "{}") };
const keep = () => localStorage.setItem("vigie.sim", JSON.stringify(set));
const OFF_ON = [[false, "Off"], [true, "On"]];
const choice = (key, options) => `<span class="choice" role="group" data-set="${key}">${options.map(([v, label, title]) =>
  `<button type="button" data-v="${esc(JSON.stringify(v))}"${title ? ` title="${esc(title)}"` : ""}>${esc(label)}</button>`).join("")}</span>`;
// a row's help is the helper's box (dashboard.js), not the browser's tooltip: name|what it is|what a click does
const row = (name, html, title) => `<span class="opt"${title ? ` data-help="${esc(name)}|${esc(title)}|Picks one; this browser keeps it"` : ""}><span>${name}</span>${html}</span>`;

// The recon dashboard's Settings box (recon-v3-mr/web, helper.tsx and map.tsx Fold): its gear and its name in the top
// left corner, over the menu; it opens under the pointer and folds when the pointer leaves, unless pinned.
sec.innerHTML = `<div class="settings"><div class="fold">
    <span class="fold-head"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915"/><circle cx="12" cy="12" r="3"/></svg>Settings</span>
    <button type="button" class="fold-pin" aria-label="Keep Settings open"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/></svg></button>
    <div class="fold-body"><div class="fold-in">
      <span class="opt" data-help="Play|Plays or pauses the replay; the slider moves it to any tick|P plays or pauses, Space fits everything on screen, 2 or 3 shows 2D or 3D"><span class="choice"><button type="button" id="sim-play"></button></span><input type="range" id="sim-t" min="0" step="1" aria-label="Tick"><span id="sim-tick"></span></span>
      ${row("Display", choice("display", [["3D", "3D"], ["2D", "2D"]]), "The 3D scene, or the same replay as a flat map seen from above")}
      ${row("View", choice("view", Object.keys(VIEWS).map(k => [k, k])), "Puts the camera back on a preset view (3D only)")}
      ${row("Speed", choice("speed", SPEEDS.map(s => [s, s + "×"])), "1× is a tick every 1.3 s")}
      ${row("Scenario", `<select id="sim-sc" aria-label="Scenario">${Object.keys(F.scenarios).map(n => `<option value="${esc(n)}">${esc(n.replaceAll("_", " "))}</option>`).join("")}</select>`, "The scenario of the bench replayed: its film comes from make metrics")}
      ${row("Vigie", choice("world", Object.entries(WHY).map(([w, why]) => [w, w === "ours" ? "Ours" : "Theirs", why])), `Ours: ${WHY.ours} Theirs: ${WHY.theirs}`)}
      ${row("Stop on a problem", choice("stopBad", OFF_ON), "Pauses when a service is compromised, an agent acts on compromised data, money leaves to a rerouted IBAN, the bank light turns orange or red, a CVE is published, a trader stops or the Vigie's heartbeat is lost")}
      ${row("Stop on an action", choice("stopGood", OFF_ON), "Pauses when the gate or the executor refuses, the Vigie raises a level, isolates a service or turns the bank red, compliance blocks, or a forged signature is rejected")}
      ${row("Focus on a stop", choice("focus", OFF_ON), "When the replay stops, the camera flies to whoever caused it")}
      ${row("Background reads", choice("reads", OFF_ON), "The dots of the agents' routine reads of the services; off, only their messages fly")}
      ${row("Bubbles", choice("bubbles", OFF_ON), "What each agent says, over its head")}
      ${row("Helper", choice("helper", OFF_ON), "A box that follows the pointer: what is under it, or what happens at this tick")}
      ${row("Details", choice("details", OFF_ON), "Whether the helper's box adds what an agent reads, its messages of this tick and its last event")}
      <span class="opt" data-help="Layout|Hold Option over a drop or a service to carry it: it follows the pointer until Option is let go, a role's pad with its agents|Saves where everything stands and the camera's angle as the defaults, for everyone"><span>Layout</span><span class="choice"><button type="button" id="sim-save">Save as default</button></span></span>
    </div></div></div></div>
  <div class="sim-stage"><h2 class="sim-side">A Vigie</h2><h2 class="sim-side ours">Our Vigie</h2><ol class="sim-refused"></ol><ol class="sim-refused ours"></ol></div>`;
const ui = { sc: sec.querySelector("#sim-sc"), play: sec.querySelector("#sim-play"), t: sec.querySelector("#sim-t"), tick: sec.querySelector("#sim-tick"),
  refused: sec.querySelectorAll(".sim-refused"), fold: sec.querySelector(".fold"), pin: sec.querySelector(".fold-pin") };
const stage = sec.querySelector(".sim-stage");

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
stage.prepend(renderer.domElement);
const labels = new CSS2DRenderer();
labels.domElement.className = "sim-labels";
stage.append(labels.domElement);

const scene = new THREE.Scene();
scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.6;
const camera = new THREE.PerspectiveCamera(36, 2, 0.1, 200);
const controls = new OrbitControls(camera, renderer.domElement);
Object.assign(controls, { enableDamping: true, enablePan: false, minDistance: 10, maxDistance: 80, maxPolarAngle: 1.4 });
scene.add(new THREE.HemisphereLight(0xffffff, 0xc9d6ee, 1.5));
const sun = new THREE.DirectionalLight(0xffffff, 2.4);
sun.position.set(8, 18, 12);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.normalBias = 0.03;
Object.assign(sun.shadow.camera, { left: -22, right: 22, top: 22, bottom: -22, near: 1, far: 60 });
sun.shadow.camera.updateProjectionMatrix();
scene.add(sun);
const world = new THREE.Group();
scene.add(world);
// no floor to see (Marius, 24-10), only the shadows the blobs cast on it
const floor = new THREE.Mesh(new THREE.PlaneGeometry(90, 90), new THREE.ShadowMaterial({ opacity: 0.3 }));
floor.rotation.x = -Math.PI / 2;
floor.receiveShadow = true;
world.add(floor);

const ball = new THREE.SphereGeometry(1, 40, 28);
const mat = (color, o) => new THREE.MeshPhysicalMaterial({ color, roughness: 0.35, clearcoat: 0.7, clearcoatRoughness: 0.2, ...o });
const felt = color => mat(color, { roughness: 0.55, clearcoat: 0.2 });
const WHITE = mat(0xffffff, { roughness: 0.15 }), INK = new THREE.MeshStandardMaterial({ color: 0x0b0d12, roughness: 0.3 });
const STEEL = mat(0x475569, { metalness: 0.7, roughness: 0.3 }), GOLD = mat(0xfacc15, { metalness: 0.9, roughness: 0.25 }), RED = felt(0xef4444);
const v = (x, y, z) => new THREE.Vector3(x, y, z);
function mesh(geo, material, at, s) {
  const m = new THREE.Mesh(geo, material);
  if (at) m.position.set(...at);
  if (s) Array.isArray(s) ? m.scale.set(...s) : m.scale.setScalar(s);
  m.castShadow = true;
  return m;
}
const turn = (m, x = 0, y = 0, z = 0) => (m.rotation.set(x, y, z), m);
function stick(a, b, r, material) {
  const m = mesh(new THREE.CylinderGeometry(r, r, a.distanceTo(b), 10), material);
  m.position.addVectors(a, b).multiplyScalar(0.5);
  m.quaternion.setFromUnitVectors(v(0, 1, 0), b.clone().sub(a).normalize());
  return m;
}
const dome = r => new THREE.SphereGeometry(r, 32, 12, 0, Math.PI * 2, 0, Math.PI / 2);
const disc = (r, h, front) => new THREE.CylinderGeometry(r, r, h, 32, 1, false, front ? -Math.PI / 2 : 0, front ? Math.PI : Math.PI * 2);
const ring = (r, t, arc = Math.PI * 2) => new THREE.TorusGeometry(r, t, 8, 32, arc);
const star = new THREE.Shape();
for (let i = 0; i < 10; i++) {
  const r = i % 2 ? 0.45 : 1, a = Math.PI / 2 + (i * Math.PI) / 5;
  i ? star.lineTo(Math.cos(a) * r, Math.sin(a) * r) : star.moveTo(Math.cos(a) * r, Math.sin(a) * r);
}
const STAR = new THREE.ExtrudeGeometry(star, { depth: 0.3, bevelEnabled: false });

function glasses(square) {
  const k = new THREE.Group(), r = square ? 0.3 : 0.25, lens = new THREE.TorusGeometry(r, 0.035, 8, square ? 4 : 28);
  for (const s of [-1, 1]) k.add(turn(mesh(lens, INK, [s * 0.32, 1.15, 1.02]), 0, 0, square ? Math.PI / 4 : 0), stick(v(s * 0.55, 1.17, 1), v(s * 0.86, 1.2, 0.35), 0.022, INK));
  return k.add(stick(v(-0.12, 1.18, 1.03), v(0.12, 1.18, 1.03), 0.025, INK));
}

// One look per role, so the eye tells them apart: glasses for the analysts (quant, risk), something else for the rest.
function dress(role, body) {
  if (role === "quant") {
    const board = turn(mesh(new THREE.BoxGeometry(1.15, 0.06, 1.15), INK, [0, 1.86, 0]), 0, Math.PI / 4);
    body.add(glasses(false), mesh(disc(0.44, 0.24), INK, [0, 1.74, 0]), board, stick(v(0, 1.9, 0), v(0.8, 1.9, 0), 0.018, GOLD),
      stick(v(0.8, 1.9, 0), v(0.8, 1.5, 0), 0.018, GOLD), mesh(ball, GOLD, [0.8, 1.46, 0], 0.07));
  } else if (role === "risk") {
    const yellow = felt(0xfacc15);
    body.add(glasses(true), mesh(dome(0.7), yellow, [0, 1.42, 0]), mesh(disc(0.96, 0.05), yellow, [0, 1.42, 0]));
  } else if (role === "trader") {
    body.add(turn(mesh(new THREE.OctahedronGeometry(1), RED, [0, 0.44, 0.84], [0.12, 0.21, 0.03]), 0.54), mesh(ball, RED, [0, 0.66, 0.95], 0.07));
  } else if (role === "compliance") {
    body.add(mesh(new THREE.CylinderGeometry(0.62, 0.55, 0.32, 32), felt(0x1e3a8a), [0, 1.74, 0]), mesh(disc(0.82, 0.04, true), INK, [0, 1.6, 0]), mesh(STAR, GOLD, [0, 1.72, 0.58], 0.13));
  } else if (role === "payment") {
    const shade = mat(0x22c55e, { transparent: true, opacity: 0.7, side: THREE.DoubleSide, roughness: 0.2 });
    body.add(turn(mesh(ring(0.76, 0.04), INK, [0, 1.5, 0]), Math.PI / 2), mesh(new THREE.CylinderGeometry(0.74, 1.08, 0.16, 32, 1, true, -Math.PI / 2, Math.PI), shade, [0, 1.42, 0]),
      turn(mesh(disc(0.2, 0.06), GOLD, [0.45, 0.6, 0.8]), Math.PI / 2));
  } else if (role === "executor") {
    const pad = felt(0x0f172a);
    body.add(mesh(ring(1, 0.045, Math.PI), STEEL, [0, 0.95, 0]), turn(mesh(disc(0.22, 0.16), pad, [-0.98, 0.95, 0]), 0, 0, Math.PI / 2),
      turn(mesh(disc(0.22, 0.16), pad, [0.98, 0.95, 0]), 0, 0, Math.PI / 2), stick(v(-1, 0.9, 0.12), v(-0.42, 0.62, 0.86), 0.025, INK), mesh(ball, INK, [-0.4, 0.6, 0.88], 0.07));
  } else if (role === "vigie") {
    const brass = mat(0xd4a017, { metalness: 0.9, roughness: 0.3 });
    body.add(mesh(new THREE.CylinderGeometry(0.66, 0.56, 0.3, 32), WHITE, [0, 1.74, 0]), turn(mesh(ring(0.57, 0.05), felt(0x1e3a8a), [0, 1.64, 0]), Math.PI / 2),
      mesh(disc(0.8, 0.04, true), INK, [0, 1.6, 0]), mesh(STAR, GOLD, [0, 1.76, 0.62], 0.1),
      stick(v(0.62, 1, 0.5), v(1.15, 0.8, 1.25), 0.1, brass), stick(v(1.15, 0.8, 1.25), v(1.45, 0.69, 1.65), 0.075, brass));
  }
}

function tag(html, at) {
  const d = document.createElement("div");
  d.className = "sim-label";
  d.innerHTML = html;
  const o = new CSS2DObject(d);
  o.position.set(...at);
  o.center.set(0.5, 0);
  return o;
}

const nodes = {};
const MOUTH = new THREE.TorusGeometry(0.16, 0.03, 8, 20, Math.PI);
function blob(id, profile, role = id) {
  const g = new THREE.Group(), hop = new THREE.Group(), body = new THREE.Group(), skin = mat(SKIN[role] ?? 0x94a3b8, { emissive: 0xdc2626, emissiveIntensity: 0 });
  g.add(hop);
  hop.add(body);
  body.add(mesh(ball, skin, [0, 0.9, 0], [1, 0.9, 1]));
  const eyes = [-0.32, 0.32].map(x => {
    const e = new THREE.Group(), pupil = mesh(ball, INK, [0, 0, 0.12], 0.1);
    e.position.set(x, 1.15, 0.78);
    e.add(mesh(ball, WHITE, null, 0.2), pupil);
    body.add(e);
    return { e, pupil };
  });
  const mouth = mesh(MOUTH, INK);
  body.add(mouth);
  dress(role, body);
  const halo = turn(mesh(new THREE.RingGeometry(1.2, 1.42, 48), new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }), [0, 0.03, 0]), -Math.PI / 2);
  const glass = mesh(ball, new THREE.MeshPhysicalMaterial({ color: 0xbae6fd, transparent: true, opacity: 0.22, roughness: 0, clearcoat: 1, depthWrite: false }), [0, 0.95, 0]);
  halo.castShadow = glass.castShadow = false;
  glass.scale.setScalar(0.001);
  const label = tag(id === "vigie" ? "Vigie" : profile ? `${esc(id)}<small>${esc(profile)}</small>` : esc(id), [0, 0, 1.45]);
  g.add(halo, glass, label);
  return (nodes[id] = { id, g, hop, body, skin, label, base: new THREE.Color(SKIN[role] ?? 0x94a3b8), eyes, mouth, halo, glass, top: 2.9,
    phase: Math.random() * 7, blinkAt: Math.random() * 4, hopAt: -9, gulpAt: -9, shakeAt: -9, look: null, lookUntil: 0, sick: false, asleep: false, level: null });
}

const BOX = new RoundedBoxGeometry(1.7, 1.4, 1.3, 4, 0.16), STRIP = new THREE.BoxGeometry(1.2, 0.08, 0.04);
function server(id) {
  const g = new THREE.Group(), shell = mat(0x1e293b, { metalness: 0.5, roughness: 0.35, emissive: 0xef4444, emissiveIntensity: 0 });
  g.add(mesh(BOX, shell, [0, 0.72, 0]));
  const leds = [0.45, 0.72, 0.99].map(y => mesh(STRIP, new THREE.MeshStandardMaterial({ color: 0x0b0d12, emissive: LED.normal }), [0, y, 0.66]));
  const field = new THREE.Group();
  field.add(new THREE.Mesh(new THREE.IcosahedronGeometry(1.55, 1), new THREE.MeshBasicMaterial({ color: 0x22d3ee, wireframe: true, transparent: true, opacity: 0.55 })),
    mesh(ball, new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.1, depthWrite: false }), null, 1.5));
  field.position.y = 0.72;
  field.scale.setScalar(0.001);
  // under its front, which turns to the camera
  const label = tag(esc(id), [0, 0, 0.75]);
  g.add(...leds, field, label);
  return (nodes[id] = { id, g, shell, leds, field, div: label.element, html: "", mode: "normal", iso: false, top: 2.8, phase: Math.random() * 7 });
}

const first = Object.values(F.scenarios)[0];
// the layout saved from the Settings box (Layout › Save as default) wins over the one above
const SAVED = await fetch("/dashboard/sim-layout.json").then(r => (r.ok ? r.json() : {}), () => ({}));
for (const id in SAVED) if (POS[id]) POS[id] = SAVED[id];
for (const s of first.services) if (POS[s]) world.add(server(s).g);
for (const a of first.agents) if (POS[a.id]) world.add(blob(a.id, a.profile, a.role).g);
// agents of one role stand on one pad, as the other tabs make them one block (Marius, 23-10), with no name of its own
// (Marius, 24-10); the pad is a rounded box around its agents, turned with the graph, fitted again whenever one of them
// moves (Marius, 24-10)
const pads = [];
const pill = (hx, hz, r) => new THREE.Shape().absarc(hx, -hz, r, -Math.PI / 2, 0).absarc(hx, hz, r, 0, Math.PI / 2)
  .absarc(-hx, hz, r, Math.PI / 2, Math.PI).absarc(-hx, -hz, r, Math.PI, Math.PI * 1.5);
function fitPad(p) {
  const cs = p.ids.map(id => untilt(...POS[id])), xs = cs.map(c => c[0]), zs = cs.map(c => c[1]);
  p.hx = (Math.max(...xs) - Math.min(...xs)) / 2;
  p.hz = (Math.max(...zs) - Math.min(...zs)) / 2;
  [p.x, p.z] = tilt(Math.min(...xs) + p.hx, Math.min(...zs) + p.hz);
  const rim = pill(p.hx, p.hz, p.r);
  rim.holes.push(pill(p.hx, p.hz, p.r - 0.14));
  p.zone.children.forEach((m, i) => (m.geometry.dispose(), (m.geometry = new THREE.ShapeGeometry(i ? rim : pill(p.hx, p.hz, p.r), 24))));
  p.zone.position.set(p.x, 0, p.z);
}
for (const [role, members] of Object.entries(Object.groupBy(first.agents.filter(a => POS[a.id]), a => a.role))) {
  if (members.length < 2) continue;
  const c = SKIN[role] ?? 0x94a3b8, zone = new THREE.Group();
  for (const opacity of [0.16, 0.9]) {
    const m = turn(new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial({ color: c, transparent: true, opacity, depthWrite: false, toneMapped: false })), -Math.PI / 2);
    m.position.y = 0.015;
    m.renderOrder = 1;
    zone.add(m);
  }
  zone.userData.pick = "pad:" + role;
  zone.rotation.y = Math.PI / 2 - LEAN;
  const p = { id: "pad:" + role, role, r: 2.4, c: "#" + new THREE.Color(c).getHexString(), n: members.length, ids: members.map(a => a.id), zone };
  fitPad(p);
  pads.push(p);
  world.add(zone);
}
const tower = new THREE.Group(), lamp = mesh(ball, new THREE.MeshStandardMaterial({ color: 0x0b0d12, emissive: LIGHT.VERT, emissiveIntensity: 2 }), [0, 6.15, -1.15], 0.26);
const beam = new THREE.PointLight(LIGHT.VERT, 6, 7);
beam.position.copy(lamp.position);
tower.add(mesh(new THREE.CylinderGeometry(0.55, 0.95, 3.2, 20), STEEL, [0, 1.6, 0]), mesh(disc(1.45, 0.2), STEEL, [0, 3.28, 0]),
  turn(mesh(ring(1.4, 0.04, Math.PI * 2), STEEL, [0, 3.85, 0]), Math.PI / 2), stick(v(0, 3.38, -1.15), v(0, 6, -1.15), 0.05, STEEL), lamp, beam);
for (let i = 0; i < 8; i++) {
  const a = (i * Math.PI) / 4, x = Math.cos(a) * 1.4, z = Math.sin(a) * 1.4;
  tower.add(stick(v(x, 3.38, z), v(x, 3.85, z), 0.03, STEEL));
}
tower.add(blob("vigie").g);
nodes.vigie.g.position.y = 3.38;
nodes.vigie.g.scale.setScalar(1.25);
world.add(tower);
for (const n of Object.values(nodes)) if (n.g.parent === world) n.g.position.set(POS[n.id][0], 0, POS[n.id][1]);
tower.position.set(POS.vigie[0], 0, POS.vigie[1]);

const FLOOR = 0.05;
const at = id => v(POS[id][0], id === "vigie" ? 4.4 : nodes[id].leds ? 0.8 : 1, POS[id][1]);
// the agents a link's end stands for: a pad's, or the one node
const members = id => pads.find(p => p.id === id)?.ids ?? [id];
// a pad's end of a link is the point of its rim nearest the link's other end
function end(id, other) {
  const p = pads.find(q => q.id === id);
  // a link runs on the floor from under the centre of a thing's feet, the Vigie's from the foot of its tower (Marius, 24-10)
  if (!p) return at(id).setY(FLOOR);
  const o = at(other), [ox, oz] = untilt(o.x - p.x, o.z - p.z), x = THREE.MathUtils.clamp(ox, -p.hx, p.hx), z = THREE.MathUtils.clamp(oz, -p.hz, p.hz), d = Math.hypot(ox - x, oz - z) || 1;
  const [dx, dz] = tilt(x + ((ox - x) / d) * p.r, z + ((oz - z) / d) * p.r);
  return v(p.x + dx, FLOOR, p.z + dz);
}

// What stands under a point of the window (clientX, clientY): the id of an agent, a service or "vigie", "pad:<role>"
// for a role's pad, or null. A ray from the camera, the first visible thing it meets that belongs to one of them.
for (const n of Object.values(nodes)) n.g.userData.pick = n.id;
tower.userData.pick = "vigie";
const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
function pick(x, y) {
  if (set.display === "2D") return pick2d(x, y);
  const r = renderer.domElement.getBoundingClientRect();
  ndc.set(((x - r.left) / r.width) * 2 - 1, 1 - ((y - r.top) / r.height) * 2);
  ray.setFromCamera(ndc, camera);
  for (const { object } of ray.intersectObject(world, true)) {
    let id = null, o = object;
    for (; o?.visible; o = o.parent) id ??= o.userData.pick;
    if (!o && id) return id;
  }
  return null;
}

// What the helper's box (dashboard.js) says under the pointer: the agent, the service or the pad there, at the tick on
// screen; over nothing, the tick itself: what happened at it, the trading result and the money lost to fraud so far
const WORD = { VERT: "green", ORANGE: "orange", ROUGE: "red", QUARANTAINE: "quarantine" };
const RING_SAYS = {
  ROUGE: "Red ring: it acts on compromised data, or the Vigie put it on red.",
  ORANGE: "Orange ring: it reads a compromised service, or got a message built on one.",
  VERT: "Green ring: no problem has reached it.",
};
const an = word => (/^[aeiou]/.test(word) ? "an " : "a ") + word;
const MODE_SAYS = { normal: "Works normally.", compromised: "Compromised: what it sends is false.", stale: "Stale: its data comes late.", slow: "Slow: it answers late.", down: "Down: it answers nothing." };
window.simHelp = (x, y) => {
  const id = pick(x, y), n = nodes[id], ours = worldName === "ours";
  const lastOf = who => W.events.findLast(([t, , text]) => t <= tick && actor(text) === who);
  const said = (W.msgAt[tick] ?? []).filter(m => m[1] === id || m[2] === id)
    .map(([, a, b, kind, bad]) => `${a === id ? `Sends ${an(kind)} to ${b}` : `Gets ${an(kind)} from ${a}`}${bad ? ", built on compromised data" : ""}, at this tick.`);
  const then = e => (e ? [`Last at tick ${e[0]}: ${e[2]}.`] : []);
  if (id?.startsWith("pad:")) {
    const p = pads.find(p => "pad:" + p.role === id);
    return { name: `${p.role} pad`, what: `The ${p.n} ${p.role}s of the bank: one role, one block in the other tabs.` };
  }
  if (id === "vigie")
    return { name: "Vigie", what: `Watches every agent and service, and its gate checks each execution. Bank light: ${WORD[light] ?? light}.${n.asleep ? " Its heartbeat is lost." : ""}`, details: then(lastOf("vigie")) };
  if (n?.leds) {
    const by = sc.reads.filter(r => r[1] === id), seen = by.filter(r => !r[3]).map(r => r[0]), hid = by.filter(r => r[3]).map(r => r[0]);
    return { name: id, what: `A service of the bank. ${MODE_SAYS[n.mode] ?? n.mode}${n.iso ? " The Vigie isolated it: whatever it fed is refused." : ""}`,
      details: [...(seen.length ? [`Read by ${seen.join(", ")}.`] : []), ...(hid.length ? [`Called without being declared by ${hid.join(", ")}.`] : []), ...said, ...then(lastOf(id))] };
  }
  if (n) {
    const a = sc.agents.find(a => a.id === id), reads = sc.reads.filter(r => r[0] === id);
    return { name: id, what: `${ROLES[a.role] ?? a.role}${a.profile ? ` Trades on ${a.profile}.` : ""} ${RING_SAYS[n.alert] ?? ""}${n.asleep ? " It has stopped." : ""}${ours && n.level && n.level !== "VERT" ? ` The Vigie has it on ${WORD[n.level]}.` : ""}`,
      details: [...(reads.length ? [`Reads ${reads.map(([, s, , hidden]) => s + (hidden ? " (never declared)" : "")).join(", ")}.`] : []), ...said, ...then(lastOf(id))] };
  }
  const now = (W.evAt[tick] ?? []).map(e => e[2] + "."), before = W.events.findLast(e => e[0] < tick);
  return { name: `Tick ${tick} of ${sc.ticks - 1}`,
    what: `${ui.sc.value.replaceAll("_", " ")}, ${ours ? "with our Vigie" : "without the Vigie"}${playing ? "" : ", paused"}. Bank light: ${WORD[light] ?? light}.`,
    click: (set.display === "2D" ? "" : "Drag to turn the scene, scroll to zoom. ") + "P plays or pauses, Space fits everything on screen, 2 or 3 shows 2D or 3D.",
    details: [...(now.length ? now : [`Nothing new at this tick.${before ? ` Last at tick ${before[0]}: ${before[2]}.` : ""}`]),
      `Trading result so far: ${eur(W.pnl[tick])}.`, ...(W.fraud[tick] ? [`Lost to fraud so far: ${eur(W.fraud[tick])}.`] : [])] };
};
const links = new Map();
function link(a, b, hidden) {
  const key = [a, b].sort().join("|");
  if (links.has(key) || ![a, b].every(id => nodes[id] || pads.some(p => p.id === id))) return links.get(key);
  const material = new THREE.MeshBasicMaterial({ color: PLAIN, transparent: true, opacity: 0.16, depthWrite: false });
  const redMat = new THREE.MeshBasicMaterial({ color: 0xef4444, transparent: true, opacity: 0.85, depthWrite: false });
  const l = { a, b, g: new THREE.Group(), curve: new THREE.QuadraticBezierCurve3(), material, redMat, fill: 0, redFrom: null, hidden: !!hidden, up: false, vigie: a === "vigie" || b === "vigie", svc: nodes[a]?.leds ? a : nodes[b]?.leds ? b : null };
  shape(l);
  world.add(l.g);
  links.set(key, l);
  return l;
}
// a link's line between its two ends, an arc when it bridges another (relief), and the tube or the dashes along it,
// drawn again while a drop is carried
function shape(l) {
  const { curve, g } = l;
  curve.v0.copy(end(l.a, l.b));
  curve.v2.copy(end(l.b, l.a));
  curve.v1.lerpVectors(curve.v0, curve.v2, 0.5).y += l.up ? 1 + curve.v0.distanceTo(curve.v2) * 0.08 : 0;
  curve.needsUpdate = true;
  for (const o of g.children) o.geometry.dispose();
  g.clear();
  const path = (material, r) => l.hidden
    ? Array.from({ length: 24 }, (_, i) => stick(curve.getPoint(i / 24), curve.getPoint((i + 0.55) / 24), r, material))
    : [new THREE.Mesh(new THREE.TubeGeometry(curve, 48, r, 6), material)];
  l.red = path(l.redMat, l.hidden ? 0.04 : 0.05);
  l.red.forEach(m => (m.renderOrder = 1));  // over the link's own colour, not blended under it
  g.add(...path(l.material, l.hidden ? 0.03 : 0.035), ...l.red);
  g.traverse(o => (o.castShadow = false));
  redden(l);
}
// links run straight; where two cross on the floor, one of them arches over the other, never both (Marius, 24-10):
// the link that crosses the most straight ones arches first, the shorter on a tie, until no two straight links cross
function relief() {
  const ls = [...links.values()].filter(l => !l.vigie || worldName === "ours"), seg = new Map(ls.map(l => [l, [end(l.a, l.b), end(l.b, l.a)]]));
  const side = (a, b, c) => Math.sign((b.x - a.x) * (c.z - a.z) - (b.z - a.z) * (c.x - a.x));
  const cross = ([p, q], [r, s]) => side(p, q, r) * side(p, q, s) < 0 && side(r, s, p) * side(r, s, q) < 0;
  let open = ls.flatMap((a, i) => ls.slice(i + 1).filter(b => cross(seg.get(a), seg.get(b))).map(b => [a, b]));
  const up = new Set(), len = l => seg.get(l)[0].distanceTo(seg.get(l)[1]);
  while (open.length) {
    const n = new Map();
    for (const pair of open) for (const l of pair) n.set(l, (n.get(l) ?? 0) + 1);
    const [top] = [...n].sort(([a, x], [b, y]) => y - x || len(a) - len(b))[0];
    up.add(top);
    open = open.filter(pair => !pair.includes(top));
  }
  for (const l of links.values()) if (l.up !== up.has(l)) (l.up = up.has(l)), shape(l);
}
// the problem travelling along a link: its red reaches l.fill of the way from the end it comes from (Marius, 24-10); a
// tube's triangles run along it 36 to a step, so a draw range shows its first part
function redden(l) {
  const back = l.redFrom === l.b, k = Math.round(l.fill * 48) * 36;
  if (l.hidden) l.red.forEach((m, i) => (m.visible = (back ? 23 - i : i) < l.fill * 24));
  else l.red[0].geometry.setDrawRange(back ? 48 * 36 - k : 0, k);
}

const GEO = { dot: new THREE.SphereGeometry(0.11, 14, 10), box: new THREE.BoxGeometry(0.3, 0.2, 0.05), coin: new THREE.CylinderGeometry(0.2, 0.2, 0.06, 22) };
const glow = {};
const shine = c => (glow[c] ??= new THREE.MeshStandardMaterial({ color: c, emissive: c, emissiveIntensity: 0.9, roughness: 0.4 }));
// the replay waits on Play (or P) when the tab opens, and a new scenario keeps the play or pause it had (Marius, 24-10)
let flying = [], fx = [], sc, W, worldName = set.world, tick = 0, acc = 0, playing = false, light = "VERT", time = 0;

// a message to or from an agent of a pad rides the pad's link when the pad stands for it
const team = id => pads.find(p => p.ids.includes(id))?.id;
const wire = (a, b) => [[a, b], [team(a), b], [a, team(b)]].map(k => links.get(k.sort().join("|"))).find(Boolean);
// one dot at a time on a link (Marius, 24-10): a message like the one already on it (a price to the three traders)
// rides with it, another waits until it has arrived, and one that would wait more than a tick is dropped; a dot crosses
// its link within a tick, so a link keeps up with a message a tick
function send(from, to, kind, bad, delay) {
  const l = wire(from, to);
  if (!l || (l.vigie && worldName !== "ours")) return;
  const back = from !== l.a && team(from) !== l.a, q = flying.includes(l.last) && l.last;
  if (q && q.kind === kind && q.back === back && q.bad === !!bad) return;
  const wait = Math.max(delay, q ? q.dur - q.at : 0), refused = kind === "refuse";
  if (wait > delay + 1 / TPS && !refused) return;
  const shape = kind === "wire" || kind === "fraud" ? "coin" : kind === "order" || kind === "payment" ? "box" : "dot";
  // in the page's ink, whatever it carries, but the order the gate is about to refuse, in red and slower (Marius, 24-10)
  const m = mesh(GEO[shape], shine(refused ? DOT.refuse : PLAIN));
  m.visible = false;
  world.add(m);
  flying.push((l.last = { m, l, back, from, to, kind, bad: !!bad, at: -wait, dur: refused ? 0.6 : Math.min(0.3, 0.12 + l.curve.getLength() / 100) }));
  if (refused) l.curve.getPoint(back ? 1 : 0, m.position), watch(l.last);
}

// the camera closes in on a refused order's dot and follows it to the gate, then goes back to where it was once the
// refusal has shown (Marius, 24-10); grabbing the camera calls it off
function watch(p) {
  zoom = { p, until: time + 1.5, back: zoom?.back ?? { target: controls.target.clone(), pos: camera.position.clone() } };
}

function burst(id, color, size = 2) {
  const m = mesh(ball, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.55, depthWrite: false }), at(id).toArray(), 0.2);
  m.castShadow = false;
  world.add(m);
  fx.push({ m, t: 0, dur: 0.8, grow: size });
}

function depart(p) {
  const s = nodes[p.from];
  if (!s?.eyes) return;
  if (!["read", "hidden", "price"].includes(p.kind)) s.hopAt = time;
  s.look = p.to;
  s.lookUntil = time + 1.2;
}

function arrive(p) {
  const d = nodes[p.to];
  if (d?.eyes) {
    d.gulpAt = time;
    if (time > d.lookUntil) (d.look = p.from), (d.lookUntil = time + 0.8);
  }
  if (p.kind === "refuse") burst(p.to, DOT.refuse, 2.6), (d.shakeAt = time);
  if (p.kind === "approve") burst(p.to, DOT.approve, 1.4);
  if (p.kind === "fraud") burst(p.to, DOT.fraud, 3.2);
  if (p.kind === "wire") burst(p.to, DOT.wire, 1.4);
}

function actor(text) {
  if (/^(the Vigie|the gate|bank light|a message signed)/.test(text)) return "vigie";
  if (/^the executor/.test(text)) return "executor";
  if (/rerouted IBAN/.test(text)) return "payment-gateway";
  if (/^real market news/.test(text)) return "market-feed";
  return Object.keys(nodes).find(id => text.startsWith(id + " "));
}

// a refusal says nothing over the scene: it is in its side's list (Marius, 24-10)
const REFUSED = /refuses|rejected|blocks/;
function say([, kind, text]) {
  const id = actor(text), n = nodes[id];
  if (!set.bubbles || !n || (id === "vigie" && worldName !== "ours") || REFUSED.test(text)) return;
  n.bubble?.removeFromParent();
  const d = document.createElement("div");
  d.className = "sim-bubble k-" + kind;
  d.textContent = (text.startsWith(id + " ") ? text.slice(id.length + 1) : text.replace(/^the (Vigie|executor) /, "")).split(":")[0];
  n.bubble = new CSS2DObject(d);
  n.bubble.position.set(0, n.top, 0);
  n.g.add(n.bubble);
  n.bubbleUntil = time + 2.6;
}

function show(t) {
  tick = t;
  const st = {}, ours = worldName === "ours";
  for (const [ct, k, val] of W.changes) {
    if (ct > t) break;
    st[k] = val;
  }
  const iso = (st.iso ?? "").split(",");
  const live = sc.services.filter(s => st["s:" + s] === "compromised" && !iso.includes(s));
  for (const a of sc.agents) {
    const b = nodes[a.id];
    if (!b) continue;
    Object.assign(b, { sick: st["c:" + a.id] === 1, asleep: st["z:" + a.id] === 1, level: ours ? st["v:" + a.id] ?? "VERT" : null });
    // the ring shows the agent's problem in both worlds: red once it acts on compromised data (or the Vigie says red),
    // orange while it only reads a compromised service or got a message built on one (or the Vigie says orange)
    const exposed = live.length && (W.hit[a.id] <= t || sc.reads.some(([r, s]) => r === a.id && live.includes(s)));
    b.alert = b.sick || b.level === "ROUGE" || b.level === "QUARANTAINE" ? "ROUGE" : exposed || b.level === "ORANGE" ? "ORANGE" : "VERT";
  }
  for (const s of sc.services) {
    const n = nodes[s];
    if (!n) continue;
    n.mode = st["s:" + s] ?? "normal";
    n.iso = iso.includes(s);
    const html = esc(s) + (n.mode !== "normal" ? `<small class="m-${esc(n.mode)}">${esc(n.mode)}</small>` : "") + (n.iso ? `<small class="m-iso">isolated by the Vigie</small>` : "");
    if (n.html !== html) n.div.innerHTML = n.html = html;
  }
  nodes.vigie.asleep = !st.vigie;
  // what each world refused so far, newest first, on the left of its Vigie's side of the window (Marius, 24-10)
  ["theirs", "ours"].forEach((w, i) => {
    const html = sc.worlds[w].events.filter(e => e[0] <= t && REFUSED.test(e[2])).reverse().map(e => `<li><b class="chip">${e[0]}</b><span class="chip"><span>${esc(e[2]).replace(": ", ":</span> <span>")}</span></span></li>`).join("");
    if (ui.refused[i].html !== html) ui.refused[i].innerHTML = ui.refused[i].html = html;
  });
  light = st.light ?? "VERT";
  // a link turns red from its red end (a compromised service, or the agent whose ring went red first) once the other
  // end is hit too (Marius, 24-10); a pad's end is red or hit once one of its agents is
  const red = id => members(id).some(m => (nodes[m].leds ? nodes[m].mode === "compromised" : nodes[m].alert === "ROUGE"));
  const hit = id => red(id) || members(id).some(m => !nodes[m].leds && nodes[m].alert === "ORANGE");
  const first = id => Math.min(...members(id).map(m => (nodes[m].leds ? -1 : W.hit[m] ?? 1e9)));
  for (const l of links.values()) {
    const s = l.svc && nodes[l.svc], from = [l.a, l.b].filter(red).sort((x, y) => first(x) - first(y))[0];
    l.g.visible = !l.vigie || ours;
    l.material.color.setHex(s?.iso ? 0x0891b2 : l.hidden ? 0xf59e0b : PLAIN);
    l.material.opacity = s?.iso ? 0.45 : l.hidden ? 0.7 : 0.16;
    l.redFrom = from && hit(from === l.a ? l.b : l.a) ? from : null;
  }
  hud();
}

// pausing drops the dots on their way, they do not finish their path (Marius, 24-10)
function unfly() {
  for (const p of flying) world.remove(p.m);
  flying = [];
}
function seek(t) {
  unfly();
  for (const n of Object.values(nodes)) n.bubble?.removeFromParent(), (n.bubble = null);
  show(t);
}

function step(t) {
  show(t);
  const span = 1 / TPS, rows = W.msgAt[t] ?? [];
  rows.forEach(([, a, b, kind, bad], i) => send(a, b, kind, bad, (i / rows.length) * span * 0.9));
  if (set.reads) for (const [a, s, rate, hidden] of sc.reads)
    if (Math.random() < rate && !nodes[a]?.asleep) send(s, a, hidden ? "hidden" : "read", nodes[s]?.mode === "compromised", Math.random() * span);
  for (const e of W.evAt[t] ?? []) say(e);
}

function hud() {
  ui.tick.textContent = `${tick} / ${sc.ticks - 1}`;
  ui.t.value = tick;
  ui.play.textContent = playing ? "Pause" : tick >= sc.ticks - 1 ? "Replay" : "Play";
}

let goal = null, fit = 1, zoom = null;
function view(k) {
  const V = VIEWS[k], blobs = Object.values(nodes).filter(n => n.eyes && n.g.parent === world);
  const target = V.on ? blobs.reduce((c, n) => c.add(n.g.position), v(0, 0, 0)).divideScalar(blobs.length).setY(1) : v(-1.5, 0.8, -1);
  goal = { target, pos: v(...V.dir).setLength(V.dist * fit).add(target) };
  zoom = null;
}
// the angle saved with the layout (Layout › Save as default) becomes the default 3/4 view, the distance fitted on load
// (Marius, 24-10)
if (SAVED.camera) VIEWS["3/4"].dir = v(0, 0, 0).setFromSphericalCoords(1, SAVED.camera[1], SAVED.camera[0]).toArray();
view(VIEWS[set.view] ? set.view : "3/4");
camera.position.copy(goal.pos);
controls.target.copy(goal.target);
goal = null;

// The first event of tick t that a Stop setting pauses on
const stopAt = t => (W.evAt[t] ?? []).find(([, k]) => Object.entries(STOPS).some(([s, kinds]) => set[s] && kinds.includes(k)));
function halt(e) {
  playing = false;
  unfly();
  acc = 0;
  zoom = null;
  const id = actor(e[2]), n = nodes[id];
  if (set.focus && n && (id !== "vigie" || worldName === "ours")) {
    const target = n.g.getWorldPosition(v(0, 0, 0)).add(v(0, 1, 0));
    goal = { target, pos: camera.position.clone().sub(controls.target).setLength(16 * fit).add(target) };
    set.view = null;
    paint();
  }
  hud();
}

function paint() {
  for (const b of sec.querySelectorAll(".choice [data-v]")) b.setAttribute("aria-pressed", JSON.stringify(set[b.parentElement.dataset.set]) === b.dataset.v);
}
const apply = { display: flatOn, helper: on => (helper.on = on), details: on => (helper.details = on), view, world: w => ((worldName = w), (W = sc.worlds[w]), relief(), seek(tick)) };
for (const b of sec.querySelectorAll(".choice [data-v]"))
  b.onclick = () => {
    const k = b.parentElement.dataset.set, val = JSON.parse(b.dataset.v);
    set[k] = val;
    keep();
    apply[k]?.(val);
    paint();
  };
const pin = on => {
  ui.fold.classList.toggle("pinned", on);
  ui.pin.setAttribute("aria-pressed", on);
  ui.pin.title = on ? "Unpin: it closes once the pointer leaves" : "Pin: it stays open";
  localStorage.setItem("vigie.pin.sim", on ? "1" : "");
};
ui.pin.onclick = () => pin(!ui.fold.classList.contains("pinned"));
pin(localStorage.getItem("vigie.pin.sim") === "1");
paint();

function load(n) {
  sc = F.scenarios[n];
  for (const w of Object.values(sc.worlds)) {
    w.msgAt ??= Object.groupBy(w.msgs, m => m[0]);
    w.evAt ??= Object.groupBy(w.events, e => e[0]);
    if (!w.hit) {
      w.hit = {};
      for (const [t, , to, , bad] of w.msgs) if (bad) w.hit[to] = Math.min(w.hit[to] ?? t, t);
    }
  }
  for (const l of links.values()) world.remove(l.g);
  links.clear();
  const edges = new Map(), key = (a, b) => [a, b].sort().join("|");
  const edge = (a, b, hidden) => edges.has(key(a, b)) || edges.set(key(a, b), [a, b, !!hidden]);
  for (const [a, s, , hidden] of sc.reads) edge(s, a, hidden);
  for (const w of Object.values(sc.worlds)) for (const [, a, b] of w.msgs) edge(a, b);
  // what is linked to every agent of a pad, the same way, is linked to the pad once: fewer links (Marius, 24-10)
  for (const p of pads)
    for (const x of Object.keys(nodes)) {
      const ks = p.ids.map(m => key(x, m));
      if (p.ids.includes(x) || !ks.every(k => edges.has(k)) || new Set(ks.map(k => edges.get(k)[2])).size > 1) continue;
      edges.set(key(x, p.id), [x, p.id, edges.get(ks[0])[2]]);
      ks.forEach(k => edges.delete(k));
    }
  for (const [a, b, hidden] of edges.values()) link(a, b, hidden);
  relief();
  W = sc.worlds[worldName];
  ui.t.max = sc.ticks - 1;
  seek(0);
}

ui.sc.onchange = () => ((set.sc = ui.sc.value), keep(), load(ui.sc.value));
ui.play.onclick = () => {
  if (tick >= sc.ticks - 1) seek(0), (playing = true);
  else playing = !playing;
  if (!playing) unfly();
  hud();
};
ui.t.oninput = () => seek(+ui.t.value);
// 2 or 3 shows the 2D map or the 3D scene, by the key's place so an AZERTY keyboard needs no Shift (Marius, 24-10)
addEventListener("keydown", e => {
  if (sec.hidden || !/^(KeyP|Space|Digit[23]|Numpad[23])$/.test(e.code) || e.target.closest("input, select, button, textarea")) return;
  e.preventDefault();
  if (e.code === "KeyP") ui.play.click();
  else if (e.code === "Space") frameAll();
  else sec.querySelector(`.choice[data-set=display] [data-v='"${e.code.at(-1)}D"']`).click();
});
// Space fits every agent and service on the screen, the camera keeping its angle, the 2D map refitted to them; the tab
// opens fitted the same way, both views at once, straight away (Marius, 24-10)
function frameAll(start) {
  zoom = null;
  const ids = Object.keys(POS).filter(id => nodes[id] && (id !== "vigie" || worldName === "ours"));
  const xs = ids.map(id => POS[id][0]), zs = ids.map(id => POS[id][1]);
  Object.assign(MAP, { x0: Math.min(...xs) - 3, x1: Math.max(...xs.map(x => x + 3), ...pads.map(p => p.x + p.hx + p.r + 3.5)), z0: Math.min(...zs) - 4, z1: Math.max(...zs) + 4.5 });
  fit2d(stage.clientWidth, stage.clientHeight);
  if (set.display === "2D" && !start) return;
  const pts = ids.flatMap(id => [[-1.5, 0, 0], [1.5, 0, 0], [0, 0, -1.5], [0, 0, 1.5], [0, id === "vigie" ? 4.5 : 2.5, 0]]
    .map(([x, y, z]) => world.localToWorld(v(POS[id][0] + x, y, POS[id][1] + z))));
  const c = new THREE.Box3().setFromPoints(pts).getCenter(v(0, 0, 0)), dir = camera.position.clone().sub(controls.target).normalize(), cam = camera.clone();
  const tan = Math.tan(THREE.MathUtils.degToRad(cam.fov / 2)), right = v(0, 0, 0), up = v(0, 0, 0);
  let d = 40;
  // the distance at which the points span 45% of the screen's width, so that with their names everything takes at most
  // half of it, and 65% of its height, the top and bottom agents getting more room (Marius, 24-10), and the aim that centres them in the right half: seen at an angle, the near agents look
  // bigger than the far ones, so the middle of the box is not the middle of the picture; found in a few steps
  for (let i = 0; i < 6; i++) {
    cam.position.copy(dir).multiplyScalar(d).add(c);
    cam.lookAt(c);
    cam.updateMatrixWorld();
    const q = pts.map(p => p.clone().project(cam)), xs = q.map(p => p.x), ys = q.map(p => p.y);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    right.setFromMatrixColumn(cam.matrixWorld, 0);
    up.setFromMatrixColumn(cam.matrixWorld, 1);
    c.addScaledVector(right, ((x0 + x1) / 2 - 0.5) * d * tan * cam.aspect).addScaledVector(up, ((y0 + y1) / 2) * d * tan);
    d *= Math.max((x1 - x0) / 2 / 0.45, (y1 - y0) / 2 / 0.65);
  }
  goal = { target: c, pos: dir.multiplyScalar(d).add(c) };
  if (start) return camera.position.copy(goal.pos), controls.target.copy(goal.target), (goal = null);
  set.view = null;
  paint();
}
controls.addEventListener("start", () => ((goal = null), (zoom = null), (set.view = null), paint()));
if (set.sc in F.scenarios) ui.sc.value = set.sc;
load(ui.sc.value);

let framed = false;
new ResizeObserver(() => {
  const w = stage.clientWidth, h = stage.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h);
  labels.setSize(w, h);
  fit2d(w, h);
  // everything stands in the right half of the window (Marius, 24-10): the picture is moved a quarter of its width to
  // the right, the camera still turning about the middle of the scene
  camera.setViewOffset(w, h, -w / 4, 0, w, h);
  const f = Math.max(1, 1.75 / camera.aspect);
  camera.position.sub(controls.target).multiplyScalar(f / fit).add(controls.target);
  fit = f;
  if (!framed) (framed = true), frameAll(true);
}).observe(stage);

const tmp = new THREE.Vector3(), look = new THREE.Vector3(), want = new THREE.Color(), GREY = new THREE.Color(0x64748b);
function animateBlob(b, dt, k) {
  const breathe = Math.sin(time * 2.4 + b.phase) * (b.asleep ? 0.015 : 0.035);
  const h = (time - b.hopAt) / 0.42, up = h >= 0 && h < 1 ? Math.sin(h * Math.PI) : 0;
  const gulp = Math.max(0, 1 - (time - b.gulpAt) / 0.25) * 0.1, s = time - b.shakeAt;
  b.hop.position.y = up * 0.5;
  b.body.scale.set(1 - breathe * 0.5 + gulp, 1 + breathe - gulp + up * 0.08, 1 - breathe * 0.5 + gulp);
  b.body.getWorldPosition(tmp);
  const a = Math.atan2(camera.position.x - tmp.x, camera.position.z - tmp.z) - world.rotation.y;
  b.body.rotation.y = a + (s < 0.5 ? Math.sin(s * 40) * 0.25 * (1 - s / 0.5) : 0);
  // its name lies on the ground just past its ring on the camera's side, so it reads under it from any angle (Marius,
  // 24-10); the Vigie's, at the foot of its tower
  const r = b.id === "vigie" ? 0.9 : 1.45;
  b.label.position.set(Math.sin(a) * r, b.id === "vigie" ? -b.g.position.y / b.g.scale.y : 0, Math.cos(a) * r);

  if (time > b.blinkAt + 0.15) b.blinkAt = time + 1.5 + Math.random() * 4;
  const shut = b.asleep || time >= b.blinkAt;
  if (b.look && time < b.lookUntil && nodes[b.look]) world.localToWorld(look.copy(at(b.look)));
  // the Vigie, idle, sweeps the floor 45° either side of straight ahead (Marius, 24-10)
  else if (b.id === "vigie") (w => b.body.localToWorld(look.set(Math.sin(w) * 10, -2, Math.cos(w) * 10)))(Math.sin(time * 0.5) * Math.PI / 4);
  else look.copy(camera.position);
  b.body.worldToLocal(look);
  for (const { e, pupil } of b.eyes) {
    e.scale.y = shut ? 0.12 : 1;
    tmp.copy(look).sub(e.position).normalize();
    tmp.z = Math.max(tmp.z, 0.6);
    pupil.position.copy(tmp.normalize()).multiplyScalar(0.12);
  }
  b.mouth.rotation.z = b.sick ? 0 : Math.PI;
  b.mouth.position.set(0, b.sick ? 0.7 : 0.84, 0.97);

  want.copy(b.base);
  if (b.asleep) want.lerp(GREY, 0.7);
  b.skin.color.lerp(want, k);
  // a red glow, not a red tint: mixed into a teal or a blue skin, red turns it grey and the blob looks asleep
  b.skin.emissiveIntensity += ((b.sick && !b.asleep ? 0.3 + Math.sin(time * 4 + b.phase) * 0.12 : 0) - b.skin.emissiveIntensity) * k;
  b.halo.material.color.setHex(RING[b.alert] ?? RING.VERT);
  b.halo.material.opacity += ((b.alert ? 0.9 : 0) - b.halo.material.opacity) * k;
  b.halo.scale.setScalar(b.alert === "ORANGE" || b.alert === "ROUGE" ? 1.1 + Math.sin(time * 5 + b.phase) * 0.08 : 1);
  b.glass.scale.setScalar(THREE.MathUtils.lerp(b.glass.scale.x, b.level === "QUARANTAINE" ? 1.5 : 0.001, k));
  if (b.sick && !b.asleep && b.g.parent === world && Math.random() < dt * 2.5) {
    const m = mesh(GEO.dot, shine(DOT.bad), [b.g.position.x + (Math.random() - 0.5) * 1.2, 1.6, b.g.position.z + (Math.random() - 0.5) * 1.2], 0.6);
    world.add(m);
    fx.push({ m, t: 0, dur: 1.2, rise: 1.2 });
  }
}

function animateServer(n, k) {
  const bad = n.mode === "compromised", slow = n.mode === "slow" || n.mode === "stale";
  n.leds.forEach((m, i) => {
    m.material.emissive.setHex(LED[n.mode] ?? LED.normal);
    m.material.emissiveIntensity = n.mode === "down" ? 0 : Math.sin(time * (bad ? 14 : slow ? 1.5 : 5) + i * 2.1 + n.phase) > -0.2 ? 1.6 : 0.25;
  });
  n.shell.emissiveIntensity = bad ? 0.35 + Math.sin(time * 6) * 0.25 : 0;
  n.g.position.x = POS[n.id][0] + (bad ? (Math.random() - 0.5) * 0.06 : 0);
  // its lit front turns to the camera, as the blobs do (Marius, 24-10)
  n.g.getWorldPosition(tmp);
  n.g.rotation.y = Math.atan2(camera.position.x - tmp.x, camera.position.z - tmp.z) - world.rotation.y;
  n.field.scale.setScalar(THREE.MathUtils.lerp(n.field.scale.x, n.iso ? 1 : 0.001, k));
  n.field.rotation.y += 0.01;
}

// a true orange for the ring: the bank light's amber reads yellow on the dark floor
const RING = { VERT: LIGHT.VERT, ORANGE: 0xf97316, ROUGE: 0xef4444 };

for (const n of Object.values(nodes)) if (n.halo) n.halo.renderOrder = 2;

// The 2D view: the same replay seen from above as a flat map on a canvas, drawn in place of the 3D scene, from the
// same state (rings, zones, links, flying dots, bubbles) so the two never disagree
const flat = document.createElement("canvas"), g2 = flat.getContext("2d");
flat.className = "sim-2d";
stage.append(flat);
const MAP = { x0: -17.5, x1: 17, z0: -16, z1: 15 };  // the part of the floor the map shows
let s2 = 1, o2 = [0, 0];
const to2 = (x, z) => [o2[0] + x * s2, o2[1] + z * s2];
function fit2d(w, h) {
  const dpr = Math.min(devicePixelRatio, 2);
  Object.assign(flat, { width: w * dpr, height: h * dpr });
  g2.setTransform(dpr, 0, 0, dpr, 0, 0);
  // a little further out than the agents' own box, and no wider than half the screen, as in 3D (Marius, 24-10)
  s2 = Math.min((0.5 * w) / (MAP.x1 - MAP.x0), (0.85 * h) / (MAP.z1 - MAP.z0));
  o2 = [0.75 * w - ((MAP.x0 + MAP.x1) / 2) * s2, h / 2 - ((MAP.z0 + MAP.z1) / 2) * s2];
}
function flatOn(d) {
  renderer.domElement.hidden = labels.domElement.hidden = d === "2D";
  flat.hidden = d !== "2D";
}
flatOn(set.display);
function pick2d(x, y) {
  const r = flat.getBoundingClientRect(), px = x - r.left, py = y - r.top;
  let best = null, d = 1.5 * s2;
  for (const n of Object.values(nodes)) {
    if (n.id === "vigie" && worldName !== "ours") continue;
    const [nx, ny] = to2(...POS[n.id]), e = Math.hypot(px - nx, py - ny);
    if (e < d) (best = n.id), (d = e);
  }
  const pad = pads.find(p => (([x, z]) => Math.abs(x) < p.hx + p.r && Math.abs(z) < p.hz + p.r)(untilt((px - to2(p.x, p.z)[0]) / s2, (py - to2(p.x, p.z)[1]) / s2)));
  return best ?? (pad ? "pad:" + pad.role : null);
}
// a material's colour as the screen shows it (getHexString turns three.js's linear colour back to sRGB), and its opacity
const rgb = (c, a = 1) => "#" + c.getHexString() + Math.round(a * 255).toString(16).padStart(2, "0");
const hex = h => "#" + h.toString(16).padStart(6, "0");
const BUBBLE = { "k-bad": ["#FBE0DC", "#7f1d1d"], "k-good": ["#DCF5E3", "#14532d"], "k-warn": ["#FDEBD3", "#7c2d12"] };
function text2(t, x, y, size = 12, color = "#0E1726", weight = 500) {
  g2.font = `${weight} ${size}px Geist, "Helvetica Neue", Arial, sans-serif`;
  g2.fillStyle = color;
  g2.shadowColor = "#fff";
  g2.shadowBlur = 4;
  g2.fillText(t, x, y);
  g2.shadowBlur = 0;
}
function draw2d() {
  const ours = worldName === "ours", alpha = (a = 1) => (g2.globalAlpha = a);
  g2.clearRect(0, 0, flat.width, flat.height);
  g2.textAlign = "center";
  for (const p of pads) {
    g2.save();
    g2.translate(...to2(p.x, p.z));
    g2.rotate(LEAN - Math.PI / 2);
    g2.beginPath();
    g2.roundRect(-(p.hx + p.r) * s2, -(p.hz + p.r) * s2, 2 * (p.hx + p.r) * s2, 2 * (p.hz + p.r) * s2, p.r * s2);
    g2.fillStyle = p.c;
    alpha(0.16), g2.fill(), alpha(0.9);
    g2.strokeStyle = p.c;
    g2.lineWidth = 2;
    g2.stroke(), alpha();
    g2.restore();
  }
  for (const l of links.values()) {
    if (!l.g.visible) continue;
    g2.beginPath();
    g2.moveTo(...to2(l.curve.v0.x, l.curve.v0.z));
    g2.lineTo(...to2(l.curve.v2.x, l.curve.v2.z));
    g2.strokeStyle = rgb(l.material.color, l.material.opacity);
    g2.lineWidth = 1.5;
    g2.setLineDash(l.hidden ? [6, 5] : []);
    g2.stroke();
    if (!l.fill) continue;
    const [p, q] = l.redFrom === l.b ? [l.curve.v2, l.curve.v0] : [l.curve.v0, l.curve.v2];
    g2.beginPath();
    g2.moveTo(...to2(p.x, p.z));
    g2.lineTo(...to2(p.x + (q.x - p.x) * l.fill, p.z + (q.z - p.z) * l.fill));
    g2.strokeStyle = rgb(l.redMat.color, l.redMat.opacity);
    g2.lineWidth = 2.5;
    g2.stroke();
  }
  for (const o of held ? snaps : []) {
    if (!o) continue;
    g2.beginPath();
    g2.moveTo(...to2(...spot(held)));
    g2.lineTo(...to2(...POS[o]));
    g2.strokeStyle = hex(SKY);
    g2.setLineDash([5, 4]);
    g2.stroke();
  }
  g2.setLineDash([]);
  for (const p of flying) {
    if (!p.m.visible) continue;
    const [x, y] = to2(p.m.position.x, p.m.position.z), shape = p.m.geometry === GEO.box ? "box" : p.m.geometry === GEO.coin ? "coin" : "dot";
    g2.fillStyle = rgb(p.m.material.color);
    g2.beginPath();
    if (shape === "box") g2.rect(x - 4, y - 3, 8, 6);
    else g2.arc(x, y, shape === "coin" ? 5.5 : 3.5, 0, 7);
    g2.fill();
  }
  for (const n of Object.values(nodes)) {
    if (n.id === "vigie" && !ours) continue;
    // a fresh state per node, not just globalAlpha: WebKit stops applying a globalAlpha below 1 after text2's shadowed text
    g2.restore();
    g2.save();
    g2.globalAlpha = hovered && n.id !== hovered && !near.has(n.id) ? FADE : 1;
    const [x, y] = to2(...POS[n.id]);
    if (n.leds) {
      const w = 1.7 * s2, h = 1.3 * s2, led = n.mode === "down" ? "#94a3b8" : hex(LED[n.mode] ?? LED.normal);
      if (n.iso) {
        g2.beginPath();
        g2.arc(x, y, 1.55 * s2, 0, 7);
        g2.fillStyle = "rgb(34 211 238 / .1)";
        g2.fill();
        g2.strokeStyle = "#22d3ee";
        g2.lineWidth = 1.5;
        g2.stroke();
      }
      g2.beginPath();
      g2.roundRect(x - w / 2, y - h / 2, w, h, 0.18 * s2);
      g2.fillStyle = "#ffffff";
      g2.shadowColor = "#ef4444";
      g2.shadowBlur = n.mode === "compromised" ? 18 : 0;
      g2.fill();
      g2.shadowBlur = 0;
      g2.strokeStyle = led;
      g2.lineWidth = 2;
      g2.stroke();
      for (const k of [-0.3, 0, 0.3]) (g2.fillStyle = led), g2.fillRect(x - 0.35 * s2, y + k * s2 - 1.5, 0.7 * s2, 3);
      // the services stand almost side by side, so every other one has its name above it
      const above = COLUMNS[0].indexOf(n.id) % 2, line = k => (above ? y - h / 2 - 6 - 13 * k : y + h / 2 + 14 + 13 * k);
      text2(n.id, x, line(0), 12);
      if (n.mode !== "normal" || n.iso) text2((n.iso ? "isolated" : n.mode).toUpperCase(), x, line(1), 10.5, n.iso ? "#22d3ee" : n.mode === "compromised" ? "#f87171" : "#fbbf24", 700);
      continue;
    }
    const vigie = n.id === "vigie", ring = vigie ? (n.asleep ? 0x1e293b : LIGHT[light] ?? LIGHT.VERT) : RING[n.alert] ?? RING.VERT;
    g2.beginPath();
    g2.arc(x, y, 1.3 * s2, 0, 7);
    g2.strokeStyle = hex(ring);
    g2.lineWidth = 3;
    g2.stroke();
    g2.beginPath();
    g2.arc(x, y, 0.95 * s2, 0, 7);
    g2.fillStyle = rgb(n.skin.color);
    g2.shadowColor = "#ef4444";
    g2.shadowBlur = n.sick && !n.asleep ? 16 : 0;
    g2.fill();
    g2.shadowBlur = 0;
    if (n.level === "QUARANTAINE") {
      g2.beginPath();
      g2.arc(x, y, 1.6 * s2, 0, 7);
      g2.setLineDash([4, 4]);
      g2.strokeStyle = "#22d3ee";
      g2.lineWidth = 1.5;
      g2.stroke();
      g2.setLineDash([]);
    }
    const profile = sc.agents.find(a => a.id === n.id)?.profile;
    text2(vigie ? "Vigie" : n.id, x, y + 1.3 * s2 + 14, 12);
    if (profile) text2(profile.toUpperCase(), x, y + 1.3 * s2 + 27, 10.5, "#455064", 500);
  }
  g2.restore();
  // what each agent says, in its bubble's colours, over it
  for (const n of Object.values(nodes)) {
    if (!n.bubble) continue;
    const t = n.bubble.element.textContent, [bg, ink] = BUBBLE[n.bubble.element.classList[1]] ?? ["#ffffff", "#0E1726"];
    const [x, y] = to2(...POS[n.id]);
    g2.font = '500 12px Geist, "Helvetica Neue", Arial, sans-serif';
    const w = Math.min(240, g2.measureText(t).width + 18), top = y - 1.3 * s2 - 30;
    g2.beginPath();
    g2.roundRect(x - w / 2, top, w, 22, 10);
    g2.fillStyle = bg;
    g2.fill();
    g2.fillStyle = ink;
    g2.fillText(t, x, top + 15, w - 14);
  }
}

// Hovering a blob or a service fades the others and lights its links (Marius, 23-10). The opacities are changed
// for the frame's drawing only and put back after it, so show() and the animations keep their own.
const FADE = 0.15, IVORY = new THREE.Color(0xf8f6f0);
let hovered = null, dim = new Set(), near = new Set(), tint = "";
stage.addEventListener("pointermove", e => (carry(e), hover(held ?? pick(e.clientX, e.clientY))));
stage.addEventListener("pointerleave", () => hover(null));
function hover(id) {
  id = nodes[id] ? id : null;
  if (id === hovered) return;
  hovered = id;
  // the nodes linked to it stay lit and its plain links take its colour (Marius, 23-10), their names staying in ink
  // (Marius, 24-10); the Vigie's grey and a service's would not show, so they lend the brand's blue
  near = new Set(id ? [...links.values()].filter(l => l.g.visible).flatMap(l => (members(l.a).includes(id) ? members(l.b) : members(l.b).includes(id) ? members(l.a) : [])) : []);
  near.delete(id);
  tint = id && nodes[id].base && id !== "vigie" ? "#" + nodes[id].base.getHexString() : hex(SKY);
  // a material one node shares with anything else (INK, GOLD, the tower's STEEL) gets a copy of its own, so it fades alone;
  // the Vigie fades with its tower (Marius, 24-10)
  const root = n => (n.id === "vigie" ? tower : n.g);
  const of = new Map(Object.values(nodes).map(n => [root(n), n])), owner = o => (of.has(o) || !o.parent ? o : owner(o.parent));
  const users = new Map();
  scene.traverse(o => o.material && users.set(o.material, (users.get(o.material) ?? new Set()).add(owner(o))));
  dim = new Set();
  for (const n of Object.values(nodes)) {
    const fade = !!id && n.id !== id && !near.has(n.id), copy = new Map();
    root(n).traverse(o => {
      if (o.isCSS2DObject) o.element.style.opacity = fade ? FADE : "";
      if (!o.material) return;
      if (users.get(o.material).size > 1) o.material = copy.get(o.material) ?? copy.set(o.material, o.material.clone()).get(o.material);
      if (fade) dim.add(o.material);
    });
  }
}
function hoverPaint() {
  if (!hovered) return () => {};
  const was = [];
  // a faded thing stays opaque, its colours washed towards the page's ivory, so no link shows through it (Marius, 24-10);
  // only what is see-through already (a ring, the quarantine glass) fades by its opacity
  for (const m of dim) {
    was.push([m, m.opacity, m.color?.getHex(), m.emissiveIntensity]);
    if (m.transparent) m.opacity *= FADE;
    else m.color?.lerp(IVORY, 1 - FADE);
    if (m.emissive) m.emissiveIntensity *= FADE;
  }
  for (const l of links.values()) {
    const m = l.material, on = members(l.a).includes(hovered) || members(l.b).includes(hovered);
    was.push([m, m.opacity, m.color.getHex()], [l.redMat, l.redMat.opacity]);
    m.opacity = on ? 0.95 : m.opacity * FADE;
    if (!on) l.redMat.opacity *= FADE;
    if (on && m.color.getHex() === PLAIN) m.color.set(tint);
  }
  return () => was.forEach(([m, o, c, e]) => ((m.opacity = o), c !== undefined && m.color.setHex(c), e !== undefined && (m.emissiveIntensity = e)));
}

// Holding Option over a drop, a service or a role's pad picks it up: it follows the pointer on the floor until Option is let go,
// a pad with all its agents, an agent of a pad alone with its pad fitted around it again, its links redrawn as it goes;
// Layout › Save as default keeps where everything stands (Marius, 24-10)
const ground = new THREE.Plane(v(0, 1, 0), 0), hit = new THREE.Vector3();
let held = null, grip = [0, 0], moved = false;
// leaving or reloading the page with moves not saved as the default asks first (Marius, 24-10)
addEventListener("beforeunload", e => moved && (e.preventDefault(), (e.returnValue = "")));
// its centre snaps onto the column or the row of a thing it comes within SNAP of, the nearest such thing, and a guide
// line joins the two centres (Marius, 24-10)
const SNAP = 0.7, guideMat = new THREE.MeshBasicMaterial({ color: SKY, depthTest: false }), guides = [0, 1].map(() => new THREE.Group());
let snaps = [];
world.add(...guides);
const spot = id => (p => (p ? [p.x, p.z] : POS[id]))(pads.find(p => p.id === id));
addEventListener("keyup", e => e.key === "Alt" && held && ((held = null), guide()));
function guide() {
  guides.forEach((g, axis) => {
    for (const o of g.children) o.geometry.dispose();
    g.clear();
    const o = held && snaps[axis];
    if (o) g.add(stick(v(spot(held)[0], 0.08, spot(held)[1]), v(POS[o][0], 0.08, POS[o][1]), 0.05, guideMat)).children[0].castShadow = false;
  });
}
function floorAt(x, y) {
  if (set.display === "2D") {
    const r = flat.getBoundingClientRect();
    return [(x - r.left - o2[0]) / s2, (y - r.top - o2[1]) / s2];
  }
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(ndc.set(((x - r.left) / r.width) * 2 - 1, 1 - ((y - r.top) / r.height) * 2), camera);
  if (!ray.ray.intersectPlane(ground, hit)) return null;
  world.worldToLocal(hit);
  return [hit.x, hit.z];
}
function carry(e) {
  if (!e.altKey) return ((held = null), guide());
  const p = floorAt(e.clientX, e.clientY);
  if (!p) return;
  if (!held) {
    const id = pick(e.clientX, e.clientY);
    if (!nodes[id] && !pads.some(q => q.id === id)) return;
    held = id;
    grip = [spot(id)[0] - p[0], spot(id)[1] - p[1]];
  }
  const ids = members(held);
  const t = [p[0] + grip[0], p[1] + grip[1]], others = Object.keys(POS).filter(o => nodes[o] && !ids.includes(o));
  snaps = [0, 1].map(axis => others.filter(o => Math.abs(POS[o][axis] - t[axis]) < SNAP)
    .sort((a, b) => Math.hypot(POS[a][0] - t[0], POS[a][1] - t[1]) - Math.hypot(POS[b][0] - t[0], POS[b][1] - t[1]))[0]);
  snaps.forEach((o, axis) => o && (t[axis] = POS[o][axis]));
  const dx = t[0] - spot(held)[0], dz = t[1] - spot(held)[1];
  moved ||= dx !== 0 || dz !== 0;
  for (const id of ids) {
    POS[id] = [POS[id][0] + dx, POS[id][1] + dz];
    (id === "vigie" ? tower : nodes[id].g).position.set(POS[id][0], 0, POS[id][1]);
  }
  const fitted = pads.filter(q => q.ids.some(id => ids.includes(id)));
  fitted.forEach(fitPad);
  const ends = [...ids, ...fitted.map(q => q.id)];
  for (const l of links.values()) if (ends.includes(l.a) || ends.includes(l.b)) shape(l);
  relief();
  guide();
}
sec.querySelector("#sim-save").onclick = async e => {
  const b = e.currentTarget, layout = Object.fromEntries(Object.entries(POS).map(([id, [x, z]]) => [id, [+x.toFixed(2), +z.toFixed(2)]]));
  const angle = new THREE.Spherical().setFromVector3(camera.position.clone().sub(controls.target));
  layout.camera = [+angle.theta.toFixed(3), +angle.phi.toFixed(3)];
  const r = await fetch("/dashboard/sim-layout", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(layout) }).catch(() => null);
  b.textContent = r?.ok ? "Saved" : "Not saved";
  if (r?.ok) moved = false;
  setTimeout(() => (b.textContent = "Save as default"), 1500);
};

let last = performance.now();
renderer.setAnimationLoop(now => {
  const dt = Math.min(0.05, (now - last) / 1000), k = 1 - Math.exp(-dt * 4), film = dt * SLOW * set.speed;
  last = now;
  if (sec.hidden || document.hidden) return;
  time += dt;
  if (playing) {
    acc += film * TPS;
    while (acc >= 1) {
      acc -= 1;
      if (tick >= sc.ticks - 1) {
        playing = false;
        hud();
        break;
      }
      step(tick + 1);
      const e = stopAt(tick);
      if (e) {
        halt(e);
        break;
      }
    }
  }
  for (const p of flying) {
    p.at += film;
    if (p.at < 0) continue;
    if (!p.m.visible) (p.m.visible = true), depart(p);
    const u = Math.min(1, p.at / p.dur), e = u * u * (3 - 2 * u);
    p.l.curve.getPoint(p.back ? 1 - e : e, p.m.position).y += 0.15;  // over the floor its link lies on
    p.m.rotation.y += dt * 6;
    if (u === 1) arrive(p), world.remove(p.m), (p.done = true);
  }
  flying = flying.filter(p => !p.done);
  for (const f of fx) {
    f.t += dt;
    const u = f.t / f.dur;
    if (f.grow) f.m.scale.setScalar(0.2 + u * f.grow), (f.m.material.opacity = 0.55 * (1 - u));
    if (f.rise) (f.m.position.y += dt * f.rise), f.m.scale.setScalar(0.6 * Math.max(0, 1 - u));
    if (u >= 1) world.remove(f.m), f.grow && f.m.material.dispose(), (f.done = true);
  }
  fx = fx.filter(f => !f.done);
  for (const n of Object.values(nodes)) {
    if (n.eyes) animateBlob(n, dt, k);
    else animateServer(n, k);
    if (n.bubble && playing && time > n.bubbleUntil) n.bubble.removeFromParent(), (n.bubble = null);
  }
  for (const l of links.values()) {
    const f = l.redFrom ? Math.min(1, l.fill + dt / 1.2) : 0;
    if (f !== l.fill) (l.fill = f), redden(l);
  }
  const ts = THREE.MathUtils.lerp(tower.scale.x, worldName === "ours" ? 1 : 0.001, k);
  tower.scale.setScalar(ts);
  tower.visible = ts > 0.01;
  const beacon = nodes.vigie.asleep ? 0x1e293b : LIGHT[light] ?? LIGHT.VERT;
  lamp.material.emissive.setHex(beacon);
  lamp.material.emissiveIntensity = 1.6 + Math.sin(time * 3) * 0.8;
  beam.color.setHex(beacon);
  if (zoom && flying.includes(zoom.p)) {
    const t = zoom.p.m.getWorldPosition(v(0, 0, 0));
    goal = { target: t, pos: camera.position.clone().sub(controls.target).setLength(16 * fit).add(t) };
    zoom.until = time + 1.5;
  } else if (zoom && time > zoom.until) (goal = zoom.back), (zoom = null);
  if (goal) {
    controls.target.lerp(goal.target, k);
    camera.position.lerp(goal.pos, k);
    if (camera.position.distanceTo(goal.pos) < 0.05) goal = null;
  }
  controls.update();
  const unpaint = hoverPaint();
  if (set.display === "2D") draw2d();
  else renderer.render(scene, camera), labels.render(scene, camera);
  unpaint();
});
