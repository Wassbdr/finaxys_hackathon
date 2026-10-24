"use strict";
// KRYS's dashboard: polls the API and redraws the four tabs. What the back does not send yet, or everything when no
// simulation runs, is drawn from dashboard-demo.json beside this file, tagged "sample" (or "demo data" in the header).

const ROLES = {
  quant: "Prices each asset and measures how unstable it is. Never trades.",
  trader: "Proposes orders: buys under fair value, sells above, reacts to news.",
  compliance: "Checks every order and payment is allowed (KYC, restricted list, split orders).",
  risk: "Measures what the bank can lose and brakes: validates, cuts or refuses.",
  payment: "Pays supplier invoices through the payment gateway.",
  executor: "Executes only what KRYS's gate approves.",
};
const LAYERS = {1: "identity", 2: "rules", 3: "behavior", 4: "coherence"};
const LIMIT_NAMES = {
  max_position_pct: "largest position", daily_loss_pct: "loss today", exposure_pct: "exposure",
  var_pct: "value at risk", daily_loss_incl_fraud_pct: "loss incl. fraud", liquidity_floor_pct: "cash floor", daily_pct: "paid today",
};
// a light or a level of KRYS: the colour of its block and the word of its pastille
const STATE = {VERT: ["green", "green"], ORANGE: ["orange", "orange"], ROUGE: ["red", "red"], QUARANTAINE: ["red", "quarantined"]};
const PITCH = "php2shell_day";  // the scenario of the pitch's three figures
const ORDER = ["quant", "trader", "compliance", "risk", "payment", "executor"];
const COLUMN = {quant: 1, trader: 2, payment: 2, compliance: 3, risk: 4, executor: 5};  // the graph's columns, services at 0

const $ = id => document.getElementById(id);
const eur = v => v == null ? "-" : (Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(2) + " M€" : (Math.round(v) || 0).toLocaleString("en") + " €");
// Les archives gardent leurs identifiants et preuves ; seul le texte rendu adopte le nom public.
const displayName = s => String(s ?? "").replace(/\b(?:the |The |la |La )?Vigie\b/g, "KRYS").replace(/Banque Horizon/g, "ARGUS");
const esc = s => displayName(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const pct = (x, digits = 0) => x == null ? "-" : (x * 100).toFixed(digits) + "%";
const tone = p => p >= 100 ? "red" : p >= 80 ? "orange" : "green";
const bar = (value, limit = 100) => { const p = limit ? value / limit * 100 : 0; return `<div class="bar-in s-${tone(p)}"><i style="width:${Math.min(100, Math.max(2, p))}%"></i></div>`; };
const row = (name, value, limit, text) => `<div class="row"><span class="name" title="${esc(name)}">${esc(name)}</span>${bar(value, limit)}<span class="num">${text}</span></div>`;
const table = (head, rows, foot = "") => `<div class="frame"><table><thead><tr>${head}</tr></thead><tbody>${rows.join("")}</tbody>${foot}</table></div>`;
const chip = (c, text) => `<span class="chip s-${c}">${esc(text)}</span>`;
const count = (n, name, fill) => `<div class="count ${n ? "f-" + fill : "f-grey"}"><b>${n}</b><span>${esc(name)}</span></div>`;
const big = (value, name, fill, under = "") => `<div class="count big f-${fill}"><b>${value}</b><span>${esc(name)}</span>${under ? `<small>${under}</small>` : ""}</div>`;
// no pastille that only says a colour: the border already does (Marius, 23-10)
const pastille = word => ["green", "orange", "red"].includes(word) ? "" : `<span class="pastille">${esc(word)}</span>`;
const block = (kind, id, [c, word], text, title = id) => `<button type="button" class="block s-${c}" data-open="${kind}:${esc(id)}">${pastille(word)}<h3>${esc(title)}</h3><p>${text}</p></button>`;
const head = (title, [c, word]) => `<div class="dlg-head s-${c}"><h3>${esc(title)}</h3>${pastille(word)}</div>`;
const order = id => ORDER.indexOf(id.split("-")[0]) * 10 + (+id.split("-")[1] || 0);

let DEMO = null, S = {}, V = {status: {}, decisions: []}, G = {nodes: []}, I = null, GR = null;
let live = false, stale = false, moved = 0, showApprovals = false, detail = null, D = null;

// --- data -------------------------------------------------------------------------------------------------------

async function getJson(url) {
  try { const r = await fetch(url, {cache: "no-store"}); return r.ok ? await r.json() : null; } catch (e) { return null; }
}

/** What the page draws: the live data, each status field the back does not publish yet taken from the demo file. */
function compose() {
  const base = live ? {state: S, vigie: V, graph: G, improvements: I} : DEMO;
  const st = {...base.vigie.status}, fake = new Set(live ? [] : ["all"]);
  for (const [k, v] of Object.entries(DEMO.vigie.status)) if (st[k] === undefined) { st[k] = v; fake.add(k); }
  let imp = base.improvements;
  if (!imp) { imp = DEMO.improvements; fake.add("improvements"); }
  else if (!imp.metrics) { imp = {...imp, metrics: DEMO.improvements.metrics}; fake.add("eval"); }
  D = {S: base.state, st, decisions: base.vigie.decisions || [], G: base.graph?.nodes?.length ? base.graph : DEMO.graph, I: imp, fake};
}
const sample = (...keys) => keys.some(k => D.fake.has(k)) ? `<span class="sample" title="the back does not publish this yet: sample data from dashboard-demo.json">sample</span>` : "";

async function poll() {
  const [s, v] = await Promise.all([getJson("/state"), getJson("/vigie")]);
  // stale: the API is gone, or it still answers but the world's tick has not moved for 5 s
  if (s && v) { live = true; if (s.tick !== S.tick) moved = Date.now(); stale = Date.now() - moved > 5000; S = s; V = v; } else if (live) stale = true;
  render();
}
async function pollGraph() { const g = await getJson("/graph"); if (g) G = g; }
async function pollImprovements() { I = await getJson("/improvements"); }
async function pollGuardrails() { GR = await getJson("/guardrails"); }

// --- shared readings ----------------------------------------------------------------------------------------------

function suspects() {
  const out = {...D.st.metrics?.suspect_services};
  for (const sid of D.st.isolated_services || []) out[sid] ??= {since_tick: "?", text: "isolated by KRYS"};
  return out;
}
// an agent's state is the worst of its sanction level and its four layer lights: an orange layer does not show green
const RANK = ["VERT", "ORANGE", "ROUGE", "QUARANTAINE"];
function agentState(id) {
  const a = D.S.agents?.[id] || {}, va = D.st.agents?.[id] || {};
  const level = [va.level || a.light || "VERT", ...Object.values(va.lights || {})].reduce((x, y) => RANK.indexOf(y) > RANK.indexOf(x) ? y : x);
  return a.alive === false && level === "VERT" ? ["grey", "silent"] : STATE[level] || ["grey", level];
}
// red: KRYS distrusts it; orange: layer 1 reports it exposed (a CVE, a large impact radius) but it is still trusted
const serviceState = id => suspects()[id] ? ["red", "suspect"] : (D.st.graph?.suspects || []).some(s => s.service === id) ? ["orange", "exposed"] : ["green", "trusted"];
const agentIds = () => Object.keys(D.S.agents || {}).sort((x, y) => order(x) - order(y));
const roleOf = id => D.S.agents?.[id]?.role || id.split("-")[0];
const bankLight = () => D.S.light?.light || D.st.light || "VERT";
const actionsOf = id => (D.st.actions || []).filter(x => x.agent === id && x.action !== "green");
function ledgerGroups(filter = () => true) {
  const groups = new Map();
  for (const e of (D.st.ledger_evidence || []).filter(filter).slice().reverse()) {
    const text = Object.entries(e).filter(([k]) => !["service", "reason", "proof"].includes(k))
      .map(([k, v]) => `${k}: ${typeof v === "number" ? (Math.abs(v) < 1000 ? +v.toFixed(2) : Math.round(v).toLocaleString("en")) : JSON.stringify(v)}`).join(" · ");
    const k = e.service + e.reason + (e.asset ?? text);  // the newest evidence of each service, reason and asset
    if (groups.has(k)) groups.get(k).count++; else groups.set(k, {...e, text, count: 1});
  }
  return [...groups.values()];
}
const ledgerTable = groups => groups.length ? table(`<th>service</th><th>reason</th><th>evidence</th><th class="num">times</th>`,
  groups.slice(0, 8).map(e => `<tr><td>${esc(e.service)}</td><td class="a-block">${esc(e.reason)}</td><td>${esc(e.text)}</td><td class="num">${e.count}</td></tr>`)) : `<p class="empty">none</p>`;
// R0 of the contagion: agents that reacted to each poisoned message, on average (the propagation records of layer 3)
function r0() {
  const recs = Object.values(D.st.propagation || {});
  return recs.length ? recs.reduce((n, r) => n + (r.agents?.length || 0), 0) / recs.length : null;
}
const findings = () => [...new Map([...(D.st.findings || []), ...(D.st.herding?.findings || [])].map(f => [`${f.tick} ${f.kind} ${f.target}`, f])).values()].sort((a, b) => a.tick - b.tick);

// --- drawing --------------------------------------------------------------------------------------------------------

const drawn = {};
function put(id, html) { if (drawn[id] !== html) { drawn[id] = html; $(id).innerHTML = html; } }

function render() {
  if (!DEMO) return;
  compose();
  renderBar(); renderSummary(); renderAgents(); renderActions(); renderGuardrails(); renderMetrics(); renderImprove(); renderDetail();
}

function renderBar() {
  const [c, word] = STATE[bankLight()] || ["grey", bankLight()], lamp = $("light");
  // no green pastille anywhere (Marius): a green bank shows a plain "normal", the lamp lights only for orange and red
  lamp.className = c === "green" ? "box lamp calm" : `box lamp s-${c}`; lamp.textContent = c === "green" ? "normal" : word;
  const conn = $("conn");
  conn.textContent = !live ? "demo data, no simulation running" : stale ? `simulation stopped at tick ${S.tick}` : "";
  conn.className = !live || stale ? "warn" : "";
  document.body.classList.toggle("stale", stale);
}

function renderAgents() {
  const ids = agentIds(), sus = suspects();
  const red = ids.filter(id => agentState(id)[0] === "red").length, nSus = Object.keys(sus).length;
  let html = `<p class="lead"><b>${red} agent${red === 1 ? "" : "s"} of ${ids.length}</b> red · <b>${nSus}</b> suspect service${nSus === 1 ? "" : "s"} · click a block for its checks</p>`;
  // agents of one role are one block, their count in its pastille and the worst of their colours on its border (Marius, 23-10)
  const roles = Map.groupBy(ids, roleOf), worst = ["green", "grey", "orange", "red"];
  html += `<div class="grid three">${[...roles].map(([role, members]) => members.length === 1
    ? block("agent", members[0], agentState(members[0]), esc(ROLES[role] || ""))
    : block("role", role, [members.map(id => agentState(id)[0]).reduce((x, y) => worst.indexOf(y) > worst.indexOf(x) ? y : x), String(members.length)], esc(ROLES[role] || ""))).join("")}</div>`;
  html += `<h2>Who feeds whom<span class="hint">a node turns red once data from a suspect service reached it</span>${sample("contamination_graph")}</h2>${graph()}`;
  put("agents", html);
}

/** The graph screen: KRYS's contamination graph (layer 3), with the hidden dependencies layer 1 found. */
function graph() {
  const g = D.st.contamination_graph || {nodes: [], edges: []};
  if (!g.nodes?.length) return `<p class="empty">no message yet</p>`;
  const W = 1000, NW = 136, NH = 34, GAP = 54, cols = {}, at = {};
  for (const n of g.nodes) (cols[n.kind === "service" ? 0 : COLUMN[n.role] ?? 2] ??= []).push(n);
  const H = Math.max(...Object.values(cols).map(c => c.length)) * GAP + 16;
  for (const [col, nodes] of Object.entries(cols)) {
    nodes.sort((a, b) => order(a.id) - order(b.id) || a.id.localeCompare(b.id));
    nodes.forEach((n, i) => at[n.id] = {x: 8 + col * (W - NW - 16) / 5, y: H / 2 + (i - (nodes.length - 1) / 2) * GAP - NH / 2});
  }
  const color = n => n.contaminated ? "red" : n.kind === "service" ? serviceState(n.id)[0] : agentState(n.id)[0];
  const path = (a, b) => { const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2, dx = Math.max(40, Math.abs(x2 - x1) / 2); return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`; };
  const edges = g.edges.filter(e => at[e.source] && at[e.target]).sort((a, b) => a.contaminated - b.contaminated).map(e =>
    `<path d="${path(at[e.source], at[e.target])}" class="edge${e.contaminated ? " bad" : ""}"><title>${esc(e.source)} → ${esc(e.target)}: ${e.count} ${e.kind === "message" ? "messages" : "reads"}${e.contaminated ? ", contaminated" : ""}</title></path>`);
  for (const e of (D.st.graph?.edges || []).filter(e => e.hidden && at[e.source] && at[e.target]))
    edges.push(`<path d="${path(at[e.target], at[e.source])}" class="edge hidden-dep"><title>hidden dependency: ${esc(e.source)} calls ${esc(e.target)} (${e.calls} calls), never declared</title></path>`);
  const nodes = g.nodes.map(n => { const p = at[n.id]; return `<g class="node s-${color(n)}" data-open="${n.kind === "service" ? "service" : "agent"}:${esc(n.id)}" tabindex="0" role="button" transform="translate(${p.x.toFixed(1)},${p.y.toFixed(1)})"><rect width="${NW}" height="${NH}" rx="10"/><text x="${NW / 2}" y="${NH / 2 + 4.5}" text-anchor="middle">${esc(n.id)}</text></g>`; });
  return `<div class="frame graph"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Who feeds whom">${edges.join("")}${nodes.join("")}</svg>
    <div class="legend"><span><i style="border-color:var(--red)"></i>carried data from a suspect service</span><span><i style="border-color:rgb(14 23 38 / .3)"></i>clean</span><span><i style="border-color:var(--orange);border-top-style:dashed"></i>hidden dependency (never declared)${sample("graph")}</span></div></div>`;
}

function agentDetail(id) {
  const st = D.st, ind = st.metrics || {}, a = D.S.agents?.[id] || {}, va = st.agents?.[id] || {}, role = a.role || id.split("-")[0];
  const susp = va.suspicion || 0, sus = suspects(), deps = D.G.nodes.find(n => n.id === id)?.depends_on || [];
  let html = head(id, agentState(id)) + `<p>${esc(ROLES[role] || role)}</p>`;
  html += `<h4>KRYS's four layers on it</h4><div class="chips">${[1, 2, 3, 4].map(n => { const l = va.lights?.[n] || "VERT", [c, w] = STATE[l] || ["grey", l]; return chip(c, `${n} ${LAYERS[n]}: ${w}`); }).join("")}</div>`;
  html += `<div class="chips">${chip(a.alive === false ? "red" : "green", a.alive === false ? "silent" : "alive")}${a.paused ? chip("orange", "paused") : ""}${va.quarantined ? chip("red", "quarantined: key revoked, tools cut") : ""}${a.vigie_lost ? chip("orange", "lost KRYS") : ""}`
    + deps.map(d => chip(sus[d] ? "red" : "grey", `reads ${d}${sus[d] ? " (suspect)" : ""}`)).join("") + chip(susp >= 50 ? "red" : susp > 0 ? "orange" : "green", `suspicion ${susp.toFixed(0)}/100`) + `</div>`;
  let rows = "";
  for (const [name, v] of Object.entries(ind.limits?.[id] || {}))
    rows += row(LIMIT_NAMES[name] || name, v.pct, 100, `${v.pct.toFixed(0)}%${v.ticks_to_breach != null ? ` · ${Math.round(v.ticks_to_breach)} ticks` : ""}`);
  if (id === "quant") for (const [asset, z] of Object.entries(ind.price_drift_z || {})) rows += row(`${asset} jump`, z, ind.thresholds?.price_drift_z || 4, `${z.toFixed(1)}σ`);
  if (id === "risk" && ind.reconciliation?.gap_pct != null) rows += row("cash vs book", ind.reconciliation.gap_pct, ind.thresholds?.reconciliation_gap_pct || 1, `${ind.reconciliation.gap_pct.toFixed(1)}%`);
  const b = st.behavior?.[id];
  if (b) rows += row("orders, last 20", b.cumulative_eur, 2 * b.limit_eur, eur(b.cumulative_eur)) + row("near-limit orders", b.near_count, b.near_threshold, `${b.near_count}/${b.near_threshold}`);
  for (const [k, c] of Object.entries(st.cusum?.[id] || {})) rows += row(`CUSUM of order ${k}`, c.cusum, c.threshold, `${c.cusum.toFixed(1)} / ${c.threshold.toFixed(1)}`);
  html += `<h4>Checks: share of the limit as the agent sees it · ticks before breach</h4>${rows ? `<div>${rows}</div>` : `<p class="empty">no limit of its own</p>`}`;
  const m = st.layer1?.agents?.[id];
  if (m) html += `<h4>Its history (layer 1)${sample("layer1")}</h4><div class="chips">${chip("grey", `${m.decisions} decisions, ${pct(m.decision_rate, 1)} of ticks`)}${m.refusal_rate != null ? chip(m.refusal_rate > 0.2 ? "orange" : "grey", `${pct(m.refusal_rate)} refused`) : ""}`
    + `${m.data_age_ticks ? chip(m.data_age_ticks.max > 20 ? "orange" : "grey", `data up to ${m.data_age_ticks.max} ticks old`) : ""}${m.heartbeat ? chip(m.heartbeat.silent_ticks ? "orange" : "grey", `heartbeat regularity ${pct(m.heartbeat.regularity)}`) : ""}`
    + `${m.repetitions?.max > 3 ? chip("orange", `repeated ${m.repetitions.max}×: ${m.repetitions.action}`) : ""}${m.llm ? chip("grey", `LLM confidence ${m.llm.confidence}, diversity ${m.llm.justification_diversity}`) : ""}</div>`;
  const p = st.profiles?.[id];
  if (p) html += `<p class="hint" style="margin:0">Clean reference: ${p.frequency?.toFixed(2)} orders a tick, at most ${eur(p.max_cumulative_20)} over 20 orders, ${p.max_near_20} near the limit; trades ${esc((p.assets || []).join(", "))}.</p>`;
  const orders = (st.orders?.[id] || []).slice(-6).reverse();
  if (orders.length) html += `<h4>Its last orders</h4>` + table(`<th>tick</th><th>asset</th><th class="num">amount</th>`, orders.map(o => `<tr><td>${o.tick}</td><td>${esc(o.asset)}</td><td class="num">${eur(o.notional_eur)}</td></tr>`));
  const acts = actionsOf(id).slice(-6).reverse();
  html += `<h4>What KRYS did to it</h4>` + (acts.length ? table(`<th>tick</th><th>action</th><th>why</th><th>layer</th>`,
    acts.map(x => `<tr><td>${x.tick}</td><td class="a-${esc(x.action)}"><b>${esc(x.action)}</b></td><td>${esc(x.text || x.reason)}</td><td>${esc(x.layer)}</td></tr>`)) : `<p class="empty">nothing: every action approved</p>`);
  if (va.quarantined) html += `<div><button class="act" onclick="human('human unblock ${esc(id)}')">Lift quarantine</button></div>`;
  return html;
}

function serviceDetail(id) {
  const s = D.G.nodes.find(n => n.id === id) || {}, f = suspects()[id];
  const users = D.G.nodes.filter(n => n.kind === "agent" && (n.depends_on || []).includes(id)).map(n => n.id);
  let html = head(id, serviceState(id)) + `<p>PHP ${esc(s.php_version)} · ${s.patchable ? "can be patched" : "cannot be patched"}${s.owner_team ? ` · run by ${esc(s.owner_team)}` : ""}</p>`;
  if (s.cve?.length) html += `<div class="chips">${s.cve.map(c => chip("orange", `${c.id} ${c.name}: ${String(c.status).replaceAll("_", " ")}`)).join("")}</div>`;
  if (f) html += `<p><b>Suspect since tick ${esc(f.since_tick)}</b>: ${esc(f.text)}</p><div><button class="act" onclick="human('human unblock ${esc(id)}')">Trust it again</button></div>`;
  html += `<h4>Agents that declare reading it</h4><div class="chips">${users.map(u => chip(agentState(u)[0], u)).join("") || `<span class="empty">none</span>`}</div>`;
  const radius = D.st.graph?.nodes?.find(n => n.id === id)?.radius;
  if (radius) html += `<h4>Impact radius (layer 1)${sample("graph")}</h4><p>${radius.agents} agents of ${agentIds().length} depend on it (${pct(radius.share)}): ${esc(radius.direct.join(", "))} directly${radius.transitive.length ? `, ${esc(radius.transitive.join(", "))} through them` : ""}. ${eur(radius.eur)} of executions rest on it.</p>`;
  const reached = (D.st.contamination_graph?.edges || []).filter(e => e.source === id && e.contaminated).map(e => e.target);
  if (reached.length) html += `<h4>Where its suspect data went</h4><div class="chips">${reached.map(t => chip("red", t)).join("")}</div>`;
  html += `<h4>Evidence (shadow ledger, independent price anchor)</h4>` + ledgerTable(ledgerGroups(e => e.service === id));
  return html;
}

function renderActions() {
  const st = D.st, actions = st.actions || [], decisions = D.decisions;
  const n = k => actions.filter(a => a.action === k).length, advice = actions.filter(a => a.layer === "auditor").length;
  let html = `<div class="counts">${count(decisions.filter(d => d.approved).length, "approved at the gate", "green")}${count(decisions.filter(d => !d.approved).length, "refused at the gate", "red")}`
    + `${count(n("slow"), "agents slowed", "orange")}${count(n("quarantine"), "quarantines", "red")}${count(n("flag"), "metric flags", "orange")}${count(st.contagion_blocked || 0, "contaminated chains cut", "red")}`
    + `${st.auditor_llm ? count(advice, "LLM auditor advice", "orange") : count("off", "LLM auditor", "grey")}${count(`${st.counters?.raw_alerts ?? 0} → ${st.counters?.incidents ?? 0} → ${st.counters?.notifications ?? 0}`, "raw alerts → incidents → human notices", "blue")}</div>`;
  const waits = [];
  if (bankLight() === "ROUGE") waits.push(`<tr><td>Bank on red: every agent paused</td><td class="num"><button class="act" onclick="human('human resume')">Resume</button></td></tr>`);
  for (const a of st.quarantined || []) waits.push(`<tr><td>${esc(a)} quarantined: key revoked, tools cut</td><td class="num"><button class="act" onclick="human('human unblock ${esc(a)}')">Lift quarantine</button></td></tr>`);
  for (const [s, f] of Object.entries(suspects())) waits.push(`<tr><td>${esc(s)} suspect since tick ${esc(f.since_tick)}: every action it fed is refused. ${esc(f.text)}</td><td class="num"><button class="act ghost" onclick="human('human unblock ${esc(s)}')">Trust it again</button></td></tr>`);
  html += `<h2>Waiting for a human<span class="hint">only a signed human loosens the brake</span></h2>` + (waits.length ? table(`<th>what</th><th class="num">decision</th>`, waits) : `<p class="empty">nothing</p>`);
  const incidents = (st.incidents || []).filter(i => i.level !== "VERT").sort((x, y) => y.last_tick - x.last_tick);
  html += `<h2>Open incidents<span class="hint">repeats grouped</span></h2>` + (incidents.length ? table(`<th>incident</th><th>root</th><th>level</th><th>reasons</th><th>ticks</th><th class="num">raw alerts</th>`,
    incidents.map(i => `<tr><td>${esc(i.id)}</td><td>${esc(i.root)}</td><td class="${i.level === "ORANGE" ? "a-slow" : "a-block"}"><b>${esc((STATE[i.level] || [, i.level])[1])}</b></td><td>${esc(i.reasons.join(", "))}</td><td>${i.first_tick} → ${i.last_tick}</td><td class="num">${i.raw_count}</td></tr>`)) : `<p class="empty">none</p>`);
  html += `<h2>Why a service was isolated<span class="hint">shadow ledger rebuilt from signed executions, independent price anchor</span></h2>` + ledgerTable(ledgerGroups());
  const claims = (st.claim_evidence || []).slice().reverse();
  html += `<h2>What an agent claimed, and what KRYS found${sample("claim_evidence")}</h2>` + (claims.length ? table(`<th>agent</th><th>client</th><th>claimed</th><th>found</th>`,
    claims.slice(0, 8).map(c => `<tr><td>${esc(c.agent)}</td><td>${esc(c.client_id)}</td><td>${esc(c.assertion)}</td><td class="a-block">${esc(c.truth)}</td></tr>`)) : `<p class="empty">no false claim caught</p>`);
  const refused = decisions.filter(d => !d.approved).slice().reverse().slice(0, 15);
  html += `<h2>Refused at the gate, newest first</h2>` + (refused.length ? table(`<th>tick</th><th>request</th><th>reason</th><th>decided by</th>`,
    refused.map(d => `<tr><td>${d.tick}</td><td>${esc(d.request_id)}</td><td class="a-block">${esc(d.reason)}</td><td>${esc(d.decided_by)}</td></tr>`)) : `<p class="empty">nothing refused yet</p>`);
  html += `<h2>Every KRYS decision on an agent, newest first<label class="hint"><input type="checkbox" ${showApprovals ? "checked" : ""} onchange="showApprovals=this.checked;render()"> show approvals</label></h2>`;
  const rows = actions.filter(a => showApprovals || a.action !== "green").slice().reverse().slice(0, 40);
  html += rows.length ? table(`<th>tick</th><th>agent / target</th><th>action</th><th>why</th><th>layer</th><th class="num">suspicion</th>`,
    rows.map(a => `<tr><td>${a.tick}</td><td>${esc(a.agent)}</td><td class="a-${esc(a.action)}"><b>${esc(a.action)}</b></td><td>${esc(a.text || a.reason)}</td><td>${esc(a.layer)}</td><td class="num">${a.suspicion ?? ""}</td></tr>`)) : `<p class="empty">no action yet</p>`;
  put("actions", html);
}

/** The blocks of the Metrics tab, one a question: its state, its line, and what its click opens. */
const METRICS = {
  price: () => {
    const ind = D.st.metrics || {}, th = ind.thresholds?.price_drift_z || 4, z = Object.entries(ind.price_drift_z || {}).sort((a, b) => b[1] - a[1]);
    const anchors = ledgerGroups(e => e.reason === "ANCHOR_DIVERGENCE"), top = z[0]?.[1] ?? 0, feed = suspects()["market-feed"];
    // once market-feed is suspect, a calm last tick does not make its prices clean again
    return {title: "Price feed", state: feed ? ["red", "suspect"] : top >= th || anchors.length ? ["red", "manipulated?"] : top >= .8 * th ? ["orange", "watch"] : ["green", "clean"],
      text: (feed ? `market-feed suspect since tick ${esc(feed.since_tick)}: what it fed is refused. ` : "") + (z.length ? `Largest one-tick jump: ${esc(z[0][0])} ${top.toFixed(1)}σ, alert at ${th}σ. ${anchors.length} divergence${anchors.length === 1 ? "" : "s"} from the independent anchor.` : "Needs 20 ticks of prices."),
      detail: () => `<h4>One-tick move per asset, in standard deviations</h4><div>${z.map(([a, v]) => row(a, v, th, `${v.toFixed(1)}σ`)).join("") || `<p class="empty">needs 20 ticks of prices</p>`}</div><h4>Quant's prices against the independent anchor</h4>${ledgerTable(anchors)}`};
  },
  ledger: () => {
    const ind = D.st.metrics || {}, r = ind.reconciliation || {}, th = ind.thresholds?.reconciliation_gap_pct || 1, ev = ledgerGroups(e => e.reason !== "ANCHOR_DIVERGENCE");
    return {title: "Ledger", state: (r.gap_pct ?? 0) >= th || ev.length ? ["red", "lies?"] : r.gap_pct == null ? ["grey", "waiting"] : ["green", "consistent"],
      text: r.gap_pct != null ? `Risk's cash and the book of record ${r.gap_pct.toFixed(1)}% apart, alert at ${th}%. ${ev.length} shadow-ledger mismatch${ev.length === 1 ? "" : "es"}.` : "Waiting for Risk's heartbeat.",
      detail: () => `<h4>Risk's cash against the book of record</h4>${r.gap_pct != null ? row("gap", r.gap_pct, th, `${r.gap_pct.toFixed(1)}%`) + row("Risk sees", 0, 1, eur(r.risk_cash_eur)) + row("book of record", 0, 1, eur(r.record_cash_eur)) : `<p class="empty">waiting for Risk's heartbeat</p>`}<h4>Balances against the shadow ledger rebuilt from signed executions</h4>${ledgerTable(ev)}`};
  },
  limits: () => {
    const ind = D.st.metrics || {}, h = ind.thresholds?.breach_horizon_ticks || 10, all = [];
    for (const [agent, limits] of Object.entries(ind.limits || {})) for (const [name, v] of Object.entries(limits)) all.push([agent, name, v]);
    all.sort((a, b) => (b[2].pct >= 100) - (a[2].pct >= 100) || (a[2].ticks_to_breach ?? 1e9) - (b[2].ticks_to_breach ?? 1e9) || b[2].pct - a[2].pct);
    const next = all[0], over = all.filter(x => x[2].pct >= 100).length, soon = all.filter(x => x[2].ticks_to_breach != null && x[2].ticks_to_breach <= h).length;
    const beh = Object.entries(D.st.behavior || {}).filter(([, b]) => b.alert).map(([a]) => a);
    return {title: "Limits", state: over || beh.length ? ["red", "breached"] : soon ? ["orange", "closing in"] : ["green", "in bounds"],
      text: next ? `${over ? `${over} over the limit as the agents see it, worst first` : "Nearest"}: ${esc(next[0])} ${esc(LIMIT_NAMES[next[1]] || next[1])} at ${next[2].pct.toFixed(0)}%${next[2].ticks_to_breach != null ? `, breaks in ${Math.round(next[2].ticks_to_breach)} ticks` : ""}. Warns under ${h} ticks.` : "No limit read yet.",
      detail: () => `<h4>Every limit, share used and ticks before breach (extrapolated from the last 10 ticks)</h4>` + (all.length ? table(`<th>agent</th><th>limit</th><th class="num">used</th><th class="num">ticks to breach</th>`,
        all.map(([a, n, v]) => `<tr><td>${esc(a)}</td><td>${esc(LIMIT_NAMES[n] || n)}</td><td class="num ${v.pct >= 100 ? "a-red" : v.pct >= 80 ? "a-slow" : ""}">${v.pct.toFixed(0)}%</td><td class="num">${v.ticks_to_breach != null ? Math.round(v.ticks_to_breach) : "-"}</td></tr>`)) : `<p class="empty">none</p>`)
        + `<h4>Orders over the last 20, against each trader's clean maximum</h4>` + (Object.keys(D.st.behavior || {}).length ? table(`<th>trader</th><th class="num">cumulated</th><th class="num">clean maximum</th><th class="num">near-limit orders</th><th>alert</th>`,
        Object.entries(D.st.behavior).map(([a, b]) => `<tr><td>${esc(a)}</td><td class="num">${eur(b.cumulative_eur)}</td><td class="num">${eur(b.reference_max_eur)}</td><td class="num">${b.near_count}/${b.near_threshold}</td><td class="${b.alert ? "a-red" : "a-green"}">${b.alert ? "yes" : "no"}</td></tr>`)) : `<p class="empty">no order yet</p>`)};
  },
  behavior: () => {
    const rows = [];
    for (const [a, obs] of Object.entries(D.st.cusum || {})) for (const [k, c] of Object.entries(obs)) rows.push([a, k, c]);
    const over = rows.filter(([, , c]) => c.cusum >= c.threshold), worst = rows.sort((x, y) => y[2].cusum / y[2].threshold - x[2].cusum / x[2].threshold)[0];
    return {title: "Behavior", state: over.length ? ["red", "drifting"] : worst && worst[2].cusum >= .7 * worst[2].threshold ? ["orange", "watch"] : ["green", "usual"],
      text: worst ? `Each trader's order count and size against its clean profile (CUSUM). Closest: ${esc(worst[0])} order ${esc(worst[1])} at ${worst[2].cusum.toFixed(1)} of ${worst[2].threshold.toFixed(1)}.` : "No order yet.",
      detail: () => `<h4>CUSUM per trader, against its calibrated threshold</h4>` + (rows.length ? table(`<th>trader</th><th>series</th><th class="num">value</th><th class="num">robust z</th><th class="num">CUSUM</th><th class="num">threshold</th>`,
        rows.map(([a, k, c]) => `<tr><td>${esc(a)}</td><td>order ${esc(k)}</td><td class="num">${typeof c.value === "number" && c.value > 1000 ? eur(c.value) : c.value}</td><td class="num">${c.z.toFixed(1)}</td><td class="num ${c.cusum >= c.threshold ? "a-red" : ""}">${c.cusum.toFixed(1)}</td><td class="num">${c.threshold.toFixed(1)}</td></tr>`)) : `<p class="empty">no order yet</p>`)};
  },
  herding: () => {
    const h = D.st.herding || {}, ind = D.st.metrics || {}, th = ind.thresholds?.herding_share ?? 1;
    const same = Object.entries(ind.herding || {}).filter(([, x]) => x.share >= th), crit = (h.findings || []).some(f => f.severity === "critical");
    return {title: "Herding", state: crit || same.length ? ["red", "herd"] : h.mimetism_index >= 2 ? ["orange", "watch"] : ["green", "independent"],
      text: `Mimetism index ${(h.mimetism_index ?? 0).toFixed(1)}× the desk's usual level; ${(h.effective_agents ?? h.traders ?? 3).toFixed(1)} of ${h.traders ?? 3} traders act independently.${sample("herding")}`,
      detail: () => {
        const tr = Object.keys(h.correlation || {}).concat(Object.values(h.correlation || {}).flatMap(Object.keys)).filter((v, i, a) => a.indexOf(v) === i).sort();
        const corr = (a, b) => a === b ? 1 : h.correlation?.[a]?.[b] ?? h.correlation?.[b]?.[a];
        const loop = h.reflexive_loop?.last;
        return `<p>Over the last ${h.window_ticks ?? "-"} ticks, ${pct(h.same_side_share)} of the other traders took the same side of the same asset within 5 ticks of an order (usual: ${pct(h.baseline_share)}). Worst so far: index ${h.worst?.mimetism_index ?? "-"} at tick ${h.worst?.mimetism_tick ?? "-"}, ${h.worst?.effective_agents ?? "-"} independent traders at tick ${h.worst?.effective_tick ?? "-"}.</p>`
          + `<h4>Correlation of the traders' decisions</h4>` + (tr.length ? table(`<th></th>${tr.map(t => `<th class="num">${esc(t)}</th>`).join("")}`, tr.map(a => `<tr><th>${esc(a)}</th>${tr.map(b => { const c = corr(a, b); return `<td class="num ${c >= .6 && a !== b ? "a-red" : ""}">${c == null ? "-" : c.toFixed(2)}</td>`; }).join("")}</tr>`)) : `<p class="empty">no order yet</p>`)
          + `<h4>Reflexive loop: a sale, the Quant's price drop, a new sale</h4><p>${h.reflexive_loop?.links ?? 0} links, ${h.reflexive_loop?.max_depth ?? 0} deep at most.${loop ? ` Last at tick ${loop.tick} on ${esc(loop.asset)}: price ${loop.drop_pct}%, then ${esc(String(loop.sell).split(":")[0])} sold again.` : ""}</p>`
          + `<h4>Same side within 3 ticks, per asset (the live check)</h4>` + (Object.entries(ind.herding || {}).map(([a, x]) => row(`${a} ${x.side}`, x.share, th, `${x.traders.length}/3`)).join("") || `<p class="empty">no order yet</p>`);
      }};
  },
  contagion: () => {
    const st = D.st, iso = st.isolated_services || [], recs = Object.values(st.propagation || {}), R = r0();
    return {title: "Contamination", state: iso.length ? ["red", "contained"] : recs.length ? ["orange", "spreading"] : ["green", "none"],
      text: `${iso.length} service${iso.length === 1 ? "" : "s"} isolated, ${st.contagion_blocked || 0} contaminated chains cut at the gate${R != null ? `, R0 ${R.toFixed(1)} agents per poisoned message` : ""}.`,
      detail: () => `<p>Every message whose provenance goes through a suspect service is marked, and the mark follows depends_on. The gate refuses only marked operations, with the chain as proof.</p><div class="chips">${iso.map(s => chip("red", `${s} isolated`)).join("")}</div>`
        + `<h4>Poisoned messages and the agents that acted on them, newest first</h4>` + (recs.length ? table(`<th>service</th><th class="num">tick</th><th>agents reached</th><th class="num">reactions</th>`,
        recs.slice().reverse().slice(0, 15).map(r => `<tr><td>${esc(r.service)}</td><td class="num">${r.source_tick}</td><td>${esc((r.agents || []).join(", "))}</td><td class="num">${r.reaction_count}</td></tr>`)) : `<p class="empty">none</p>`)};
  },
  auditor: () => {
    const st = D.st, acts = (st.actions || []).filter(a => a.layer === "auditor");
    return {title: "LLM auditor", state: !st.auditor_llm ? ["grey", "off"] : acts.length ? ["orange", `${acts.length} advice`] : ["green", "quiet"],
      text: st.auditor_llm ? "Reads the agents' reasoning; can only raise an agent's suspicion, never approve." : "Off: runs only with MISTRAL_API_KEY. It can only raise suspicion, never approve.",
      detail: () => `<p>A second opinion on the LLM agents' justifications. Its advice raises suspicion and never approves anything; the gate stays deterministic.</p>` + (acts.length ? table(`<th>tick</th><th>agent</th><th>advice</th><th class="num">suspicion</th>`,
        acts.slice().reverse().map(a => `<tr><td>${a.tick}</td><td>${esc(a.agent)}</td><td>${esc(a.reason)}</td><td class="num">${a.suspicion}</td></tr>`)) : `<p class="empty">no advice${st.auditor_llm ? " yet" : ": it is off"}</p>`)};
  },
};

function renderMetrics() {
  const st = D.st, h = st.herding || {}, ceo = st.layer1?.ceo || {}, R = r0();
  let html = `<h2>For the CEO${sample("herding", "layer1")}</h2><div class="counts">`
    + big(h.effective_agents != null ? `${h.effective_agents.toFixed(1)} of ${h.traders}` : "-", "traders acting independently", h.effective_agents != null && h.effective_agents < h.traders / 2 ? "orange" : "green", "effective number, from the eigenvalues of their decisions")
    + big(R != null ? R.toFixed(1) : "-", "R0 of the contagion", R >= 1 ? "red" : "green", "agents that acted on each poisoned message")
    + big(eur(ceo.unpatchable_eur), "executed on code that cannot be patched", ceo.unpatchable_share > .5 ? "red" : "orange", ceo.unpatchable_share != null ? `${pct(ceo.unpatchable_share)} of executions, through ${esc((ceo.unpatchable_services || []).join(", "))}` : "")
    + big(pct(ceo.reconstitution_rate), "decisions traced back to a source", ceo.reconstitution_rate >= .95 ? "green" : "orange", "executions whose depends_on chain is complete") + `</div>`;
  html += `<h2>Live indicators, right after the limit checks</h2><div class="grid">${Object.entries(METRICS).map(([k, f]) => { const m = f(); return block("metric", k, m.state, m.text, m.title); }).join("")}</div>`;
  const fs = findings().slice().reverse().slice(0, 12);
  html += `<h2>Findings, newest first<span class="hint">what the layers report to the decision layer</span>${sample("findings")}</h2>` + (fs.length ? table(`<th>tick</th><th>layer</th><th>kind</th><th>target</th><th>severity</th><th class="num">score</th><th>what</th>`,
    fs.map(f => `<tr><td>${f.tick}</td><td>${f.layer}</td><td>${esc(f.kind.replaceAll("_", " "))}</td><td>${esc(f.target)}</td><td class="${f.severity === "critical" ? "a-red" : f.severity === "warning" ? "a-slow" : ""}">${esc(f.severity)}</td><td class="num">${(+f.score).toFixed(2)}</td><td>${esc(f.detail?.text || Object.entries(f.detail || {}).filter(([, v]) => typeof v !== "object").map(([k, v]) => `${k.replaceAll("_", " ")} ${v}`).join(" · "))}</td></tr>`)) : `<p class="empty">none yet</p>`);
  const alerts = (st.metrics?.alerts || []).slice().reverse().slice(0, 12);
  html += `<h2>Latest metric alerts</h2>` + (alerts.length ? table(`<th>tick</th><th>rule</th><th>target</th><th class="num">value</th><th class="num">threshold</th><th>what it means</th>`,
    alerts.map(a => `<tr><td>${a.tick}</td><td>${esc(a.rule)}</td><td>${esc(a.target)}</td><td class="num">${a.value}</td><td class="num">${a.threshold ?? ""}</td><td>${esc(a.text)}</td></tr>`)) : `<p class="empty">none</p>`);
  html += evaluation();
  put("metrics", html);
}

/** The replays of every scenario without (A) and with (B) KRYS, and the bench's metrics. */
function evaluation() {
  const I = D.I, m = I.metrics || {}, when = new Date(I.generated_at).toLocaleString("en-GB");
  let html = `<h2>Without and with KRYS<span class="hint">replays of ${esc(when)}</span>${sample("improvements")}</h2>`;
  const p = I.scenarios.find(s => s.scenario === PITCH);
  // three separate figures, never one inside another: the fraud blocked is larger than the net result saved
  if (p) html += `<div class="counts">${big(eur(p.avoided_fraud_eur), "fraud blocked", "green")}${big(eur(p.avoided_loss_eur), "net result saved", "green")}${big(eur(-p.brake_cost_eur), "cost of the blocks", "orange", "trading result lost to refused orders")}</div>
    <p class="note">${esc(PITCH)}, same seed: ${eur(p.unprotected_net_eur)} without KRYS (of which ${eur(p.avoided_fraud_eur)} of fraud), ${eur(p.protected_net_eur)} with it.</p>`;
  // the table is two thirds of the screen wide: thousands in k€, detection as ticks before (−) or after (+) the attack
  const k = v => v != null && Math.abs(v) >= 1e3 && Math.abs(v) < 1e6 ? Math.round(v / 1e3) + " k€" : eur(v);
  html += `<div style="margin-top:12px">` + table(`<th>scenario</th><th class="num">without</th><th class="num">with</th><th class="num">fraud blocked</th><th class="num">net result saved</th><th class="num">cost of the blocks</th><th class="num">clean money frozen</th><th class="num">contaminated ops</th><th>detected, ticks vs attack</th>`,
    I.scenarios.map(g => `<tr><td>${esc(g.scenario)}</td><td class="num">${k(g.unprotected_net_eur)}</td><td class="num">${k(g.protected_net_eur)}</td><td class="num">${k(g.avoided_fraud_eur)}</td><td class="num a-green">${k(g.avoided_loss_eur)}</td><td class="num a-slow">${k(-g.brake_cost_eur)}</td><td class="num">${k(g.frozen_eur)}</td><td class="num">${g.contaminated_operations.unprotected} → ${g.contaminated_operations.protected}</td><td>${(g.detection || []).map(d => `<span style="white-space:nowrap">${esc(d.service)} ${d.detected_at == null ? `<span class="a-block">missed</span>` : (d.ticks_to_detect > 0 ? "+" : d.ticks_to_detect < 0 ? "−" : "") + Math.abs(d.ticks_to_detect)}</span>`).join("<br>") || "-"}</td></tr>`),
    `<tfoot><tr><th>total</th><th></th><th></th><th></th><th class="num">${k(I.totals.avoided_loss_eur)}</th><th class="num">${k(-I.totals.brake_cost_eur)}</th><th class="num">${k(I.totals.frozen_eur)}</th><th class="num">${I.totals.missed_operations} still passed</th><th></th></tr></tfoot>`) + `</div>`;
  if (m.scenarios) {
    const iv = x => x ? `${(+x.mean).toFixed(2)} <span class="hint">[${(+x.low).toFixed(2)}–${(+x.high).toFixed(2)}]</span>` : "-";
    html += `<h2>The bench's metrics<span class="hint">make metrics · ${m.totals?.attempts ?? "-"} attempts over ${m.totals?.attack_runs ?? "-"} attack runs, ${m.totals?.clean_runs ?? "-"} clean runs</span>${sample("improvements", "eval")}</h2>`
      + table(`<th>scenario</th><th class="num">attacks that succeed, without → with</th><th class="num">gate precision</th><th class="num">gate recall</th><th class="num">detection</th><th class="num">containment</th><th class="num">clean work kept</th><th>agents red</th><th class="num">gate p50 / p99</th>`,
        m.scenarios.map(s => `<tr><td>${esc(s.scenario)}</td><td class="num">${pct(s.asr?.without?.rate)} → <b class="${s.asr?.with?.rate ? "a-red" : "a-green"}">${pct(s.asr?.with?.rate)}</b></td><td class="num">${pct(s.gate?.precision)}</td><td class="num">${pct(s.gate?.recall)}</td><td class="num">${s.detection?.detection_ticks ?? "-"} ticks</td><td class="num">${s.detection?.containment_ticks ?? "-"} ticks</td><td class="num">${pct(s.utility?.ratio)}</td><td>${(s.impact?.red_with || []).length} of ${s.impact?.agents ?? "-"}</td><td class="num">${s.gate_latency_ms?.p50 ?? "-"} / ${s.gate_latency_ms?.p99 ?? "-"} ms</td></tr>`));
    const c = m.clean;
    const share = x => x ? `${pct(x.rate ?? x.mean)} <span class="hint">[${pct(x.low)}–${pct(x.high)}]</span>` : "-";
    if (c) html += `<div class="counts" style="margin-top:12px">${big(iv(c.false_blocks_per_run), "clean operations refused per run", "orange", `${c.runs} clean runs, 95% interval`)}${big(iv(c.false_alerts_per_run), "false alerts per run", "orange")}${big(share(c.runs_with_false_isolation), "clean runs with a service isolated wrongly", "orange")}${big(share(c.utility), "clean work kept", "green")}${m.gate_latency_ms ? big(`${m.gate_latency_ms.p50} / ${m.gate_latency_ms.p99} ms`, "gate latency p50 / p99", "blue", `${m.gate_latency_ms.n} decisions, ${m.gate_latency_ms.timeouts} timeouts`) : ""}</div>`;
  }
  html += `<h2>Each rule, graded on the replays</h2>` + table(`<th>rule</th><th>layer</th><th class="num">fired</th><th class="num">right</th><th class="num">wrong</th><th class="num">contaminated money stopped</th><th class="num">clean money frozen</th>`,
    I.rules.map(r => `<tr><td>${esc(r.rule)}</td><td>${esc(r.layer)}</td><td class="num">${r.fired}</td><td class="num">${r.true}</td><td class="num">${r.false}</td><td class="num">${eur(r.blocked_eur)}</td><td class="num">${eur(r.frozen_eur)}</td></tr>`));
  return html;
}

function renderImprove() {
  const I = D.I, th = D.st.metrics?.thresholds || I.thresholds || {};
  let html = `<h2>Thresholds in force<span class="hint">a change is signed by a human and journaled</span></h2>` + table(`<th>threshold</th><th class="num">now</th><th>set to</th>`,
    Object.entries(th).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${v}</td><td><input type="number" step="any" id="th-${esc(k)}" value="${v}"> <button class="act ghost" onclick="human('human set_threshold ${esc(k)} ' + $('th-${esc(k)}').value)">Apply</button></td></tr>`));
  html += `<h2>What the replays say${sample("improvements")}</h2><div class="card">${I.summary.map(s => `<div>${esc(s)}</div>`).join("")}</div><h2>Proposals for a human</h2>`;
  for (const p of I.proposals) {
    const kind = {threshold: "threshold", gap: "coverage gap", rule: "rule"}[p.kind] || p.kind;
    let evidence = "";
    if (p.evidence?.sweep) evidence += table(`<th class="num">value</th><th>catches</th><th class="num">right alerts</th><th class="num">false alerts</th><th class="num">ticks to detect, summed</th>`,
      p.evidence.sweep.map(r => `<tr${r.value === p.proposed ? ` style="color:var(--blue)"` : ""}><td class="num">${r.value}</td><td>${esc(r.caught.join(", ")) || "-"}</td><td class="num">${r.true_alerts}</td><td class="num">${r.false_alerts}</td><td class="num">${r.ticks_to_detect}</td></tr>`));
    if (p.replay) evidence += table(`<th>replayed scenario</th><th class="num">net result saved</th><th class="num">clean money frozen</th>`,
      Object.entries(p.replay.by_scenario).map(([n, v]) => `<tr><td>${esc(n)}</td><td class="num">${eur(v.avoided_loss_eur)}</td><td class="num">${eur(v.frozen_eur)}</td></tr>`));
    for (const m of p.evidence?.missed || []) evidence += `<div style="margin:8px 0"><b>t${m.tick} ${esc(m.kind)} ${eur(m.amount_eur)} → ${esc(m.target)}</b>, fed by ${esc((m.compromised_sources || []).join(", "))}<br>` +
      m.chain.map(c => `<span class="chip">t${c.tick} ${esc(c.from)} · ${esc(c.type)}${c.fed_by_compromised.length ? " · fed by " + esc(c.fed_by_compromised.join(", ")) : ""}</span> ${esc(c.said)}`).join("<br>") + `</div>`;
    html += `<div class="card proposal"><h3><span><span class="badge">${esc(kind)}</span>${esc(p.target)}</span>` +
      (p.kind === "threshold" ? `<span>${p.proven ? chip("green", "replay agrees") : chip("red", `replay disagrees: keep ${p.current}`)} <button class="act ${p.proven ? "" : "ghost"}" onclick="human('${esc(p.apply)}')">Apply ${p.current} → ${p.proposed}</button></span>` : "") +
      `</h3><div class="why">${esc(p.why)}</div>${evidence ? `<details><summary>evidence</summary>${evidence}</details>` : ""}</div>`;
  }
  put("improve", html);
}

/** What waits for a human: the problems only a human clears, and the replays' good news only a human applies (Marius, 23-10). */
function renderSummary() {
  const st = D.st, bad = [], good = [];
  const ask = (list, text, command, label, ghost) => list.push(`<tr><td>${text}</td><td class="num">${command ? `<button class="act${ghost ? " ghost" : ""}" onclick="${command.startsWith("#") ? `location.hash='${command}'` : `human('${esc(command)}')`}">${label}</button>` : ""}</td></tr>`);
  if (bankLight() === "ROUGE") ask(bad, "The bank is on red: every agent is paused.", "human resume", "Resume");
  const risk = D.S.agents?.risk?.status;
  if (risk?.halted) ask(bad, `Risk halted trading (${esc(risk.halt_reason)}): only a human starts it again.`, "human resume_trading", "Resume trading");
  for (const a of st.quarantined || []) ask(bad, `${esc(a)} is quarantined: its key is revoked and its tools cut.`, `human unblock ${a}`, "Lift quarantine");
  for (const [s, f] of Object.entries(suspects())) ask(bad, `${esc(s)} is suspect since tick ${esc(f.since_tick)}: every action it fed is refused.`, `human unblock ${s}`, "Trust it again", true);
  for (const [who, why] of Object.entries(D.S.agents?.compliance?.status?.blocked || {})) ask(bad, `Compliance blocked ${esc(who)} (${esc(why)}): only a human unblocks it.`, `human unblock ${who}`, "Unblock", true);
  const lost = agentIds().filter(id => D.S.agents[id].vigie_lost);
  if (lost.length) ask(bad, `${esc(lost.join(", "))} lost KRYS's heartbeat and went orange by themselves: check that KRYS runs.`);
  const props = D.I.proposals || [], th = st.metrics?.thresholds || {}, open = props.filter(p => p.kind !== "threshold");
  if (open.length) ask(bad, `The replays found ${open.length} gap${open.length === 1 ? "" : "s"} or rule${open.length === 1 ? "" : "s"} to revisit: ${esc(open.map(p => p.target).join(", "))}.`, "#improve", "Read them", true);
  for (const p of props.filter(p => p.kind === "threshold" && p.proven && th[p.target] !== p.proposed)) ask(good, `The replays agree: ${esc(p.why)}`, p.apply, `Apply ${p.current} → ${p.proposed}`);
  const n = (k, word) => `<b>${k}</b> ${word}${k === 1 ? "" : "s"}`;
  let html = `<p class="lead">${n(bad.length, "problem")} and ${n(good.length, "piece")} of good news wait for you${live && !stale ? "" : ": no simulation running, so no decision can be sent"}</p>`;
  html += `<h2>Problems only a human clears</h2>` + (bad.length ? table(`<th>what</th><th class="num">your decision</th>`, bad) : `<p class="empty">none</p>`);
  html += `<h2>Good news only a human applies</h2>` + (good.length ? table(`<th>what</th><th class="num">your decision</th>`, good) : `<p class="empty">none</p>`);
  put("summary", html);
}

/** Every guardrail in force: each agent's limits as config.yaml sets them with how much of each is used now, KRYS's, the LLM's. */
const THRESHOLDS = {price_drift_z: "Price move in one tick that raises an alert, in standard deviations", reconciliation_gap_pct: "Gap between Risk's cash and the book of record that raises an alert, %",
  herding_share: "Share of the traders on one side of one asset within 3 ticks that raises an alert", breach_horizon_ticks: "Warns when a limit will break within, ticks"};
function renderGuardrails() {
  if (!GR) return put("guardrails", `<p class="empty">The API does not serve /guardrails: restart it (python -m api.app).</p>`);
  const {trader: t, compliance: c, risk: r, payment: pay, quant: q} = GR.agents, L = D.S.limits || {}, p = D.S.params || {};
  const scale = D.S.agents?.risk?.status?.trader_scale ?? 1, blocked = Object.keys(D.S.agents?.compliance?.status?.blocked || {});
  const used = v => v == null ? "" : `<span class="${v >= 100 ? "a-red" : v >= 80 ? "a-slow" : ""}">${Math.round(v)}%</span>`;
  const each = key => Object.entries(L.traders || {}).map(([id, x]) => `${esc(id)} ${used(x[key])}`).join(" · ");
  const on = x => chip(x ? "green" : "grey", x ? "on" : "off");
  const rows = list => table(`<th>who</th><th>guardrail</th><th class="num">set to</th><th class="num">now</th>`,
    list.map(([who, what, value, now = ""], i) => `<tr><td>${i && list[i - 1][0] === who ? "" : esc(who)}</td><td>${what}</td><td class="num">${value}</td><td class="num">${now}</td></tr>`));
  let html = `<p class="lead">Each agent checks its own limits; KRYS checks the agents. Values from config.yaml, use from the live bank${sample("all")}</p>`;
  html += `<h2>The bank's agents<span class="hint">an agent on orange has its order and payment limits × ${GR.vigie.orange_amount_factor}</span></h2>` + rows([
    ["trader", "Largest order", eur(t.max_order_eur), scale < 1 ? `× ${scale}: VaR is over ${pct(r.var_warning_ratio)} of its limit` : ""],
    ["trader", "Position in one asset", eur(t.max_position_eur), each("max_position_pct")],
    ["trader", "Loss in a day, then it stops until the next day", eur(t.max_daily_loss_eur), each("daily_loss_pct")],
    ["trader", "Confidence under which it does not trade", t.min_confidence],
    ["trader", "Wait between two orders on one asset", `${t.cooldown_ticks} ticks`],
    ["compliance", "Assets no order may touch (restricted list)", esc((p.restricted_list ?? c.restricted_list).join(", ")) || "none"],
    ["compliance", "An order built on confidential news (insider information)", "refused"],
    ["compliance", "Small orders in a row (split orders)", `${c.structuring_max_orders} under ${eur(c.small_order_eur)} in ${c.structuring_window_ticks} ticks`],
    ["compliance", "Selling back what one bought (wash trade)", `within ${c.wash_window_ticks} ticks`],
    ["compliance", "Cancelled orders (spoofing)", `over ${c.spoofing_max_cancels} in ${c.spoofing_window_ticks} ticks`],
    ["compliance", "Payments: the beneficiary's KYC file is reviewed, these countries are high risk; a block is lifted only by a human", esc(c.high_risk_countries.join(", ")), blocked.length ? `${blocked.length} blocked` : ""],
    ["risk", "Gross exposure", eur(r.max_exposure_eur), used(L.bank?.exposure_pct)],
    ["risk", `VaR 99% one day; over ${pct(r.var_warning_ratio)} the traders' limits go × ${r.var_warning_scale}`, eur(r.max_var_eur), used(L.bank?.var_pct)],
    ["risk", "Loss in a day, then trading halts until a human resumes it", eur(r.max_daily_loss_eur), used(L.bank?.daily_loss_pct)],
    ["risk", "Cash floor", eur(r.min_cash_eur), L.bank?.liquidity_floor_pct != null ? `${used(L.bank.liquidity_floor_pct)} of the cash` : ""],
    ["payment", "One transfer", eur(pay.max_transfer_eur)],
    ["payment", "Transfers in a day", eur(pay.max_daily_eur), used(L.payments?.daily_pct)],
    ["payment", "No payment to an account whose IBAN changed within", `${pay.iban_change_lock_ticks} ticks`],
    ["quant", "Market data older than this is not priced", `${q.max_data_age_ticks} ticks`],
    ["quant", `A one-tick jump over this drops its confidence to ${q.low_confidence}, under the traders' ${t.min_confidence}`, `${q.jump_threshold_pct}%`],
  ]);
  html += `<h2>KRYS<span class="hint">a change of threshold is signed by a human, in Improvements</span></h2>` + rows([
    ["KRYS", "Watches every message and approves every order and payment", on(D.S.vigie?.enabled ?? GR.vigie.enabled)],
    ["gate", `An order or payment it has not approved within this wait is ${GR.gate.default_on_timeout === "reject" ? "refused" : "approved"}`, `${GR.gate.timeout_s} s`, esc(D.S.vigie?.gate ?? "")],
    ["agents", "An agent that hears no KRYS heartbeat for this long goes orange by itself", `${GR.vigie.heartbeat_timeout_ticks} ticks`, agentIds().filter(id => D.S.agents[id].vigie_lost).length ? `${agentIds().filter(id => D.S.agents[id].vigie_lost).length} lost it` : ""],
    ...Object.entries(D.st.metrics?.thresholds || {}).map(([k, v]) => ["thresholds", esc(THRESHOLDS[k] || k), v]),
    ["LLM auditor", "Reads the agents' reasoning; can raise suspicion, never approve", on(D.st.auditor_llm)],
  ]);
  html += `<h2>The LLM agents</h2>` + rows([
    ["LLM", "Agents that reason with an LLM (config llm.enabled)", on(GR.llm.enabled)],
    ["LLM", "Prompt guardrails: instruction phrases are cut from outside text and a system reminder is added", on((p.guardrails ?? GR.llm.guardrails) === "on")],
  ]);
  put("guardrails", html);
}

// --- the details popup --------------------------------------------------------------------------------------------

const dlg = $("dlg");
function renderDetail() {
  if (!detail) return;
  const html = detail.kind === "agent" ? agentDetail(detail.id) : detail.kind === "service" ? serviceDetail(detail.id)
    : detail.kind === "role" ? agentIds().filter(id => roleOf(id) === detail.id).map(agentDetail).join("")
    : (m => head(m.title, m.state) + `<p>${m.text}</p>` + m.detail())(METRICS[detail.id]());
  put("detail", html);
}
document.addEventListener("click", e => {
  const b = e.target.closest("[data-open]");
  if (!b) return;
  const k = b.dataset.open, at = k.indexOf(":");
  detail = {kind: k.slice(0, at), id: k.slice(at + 1)};
  renderDetail();
  if (!dlg.open) dlg.showModal();
});
document.addEventListener("keydown", e => { if ((e.key === "Enter" || e.key === " ") && e.target.matches?.("g[data-open]")) { e.preventDefault(); e.target.dispatchEvent(new MouseEvent("click", {bubbles: true})); } });
dlg.addEventListener("click", e => { if (e.target === dlg) dlg.close(); });
dlg.addEventListener("close", () => { detail = null; drawn.detail = ""; });

// --- human decisions ----------------------------------------------------------------------------------------------

function toast(text) {
  const t = $("toast"); t.textContent = text; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.hidden = true, 3500);
}
async function human(command) {
  if (!live || stale) return toast("No simulation running: start one to send a decision");
  const r = await fetch("/inject", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({command})}).catch(() => null);
  const body = await r?.json().catch(() => ({}));
  toast(r?.ok ? `Sent, signed by human: ${command}` : `Refused: ${body?.detail || r?.status || "API unreachable"}`);
}

// --- the page: its menu, its polling ---------------------------------------------------------------------------

const tabs = [...document.querySelectorAll(".tabs a")], line = document.querySelector(".tabs .line");
function placeLine() {
  const t = tabs.find(t => t.hasAttribute("aria-current"));
  if (t) line.style.transform = `translateX(${t.offsetLeft + 12}px) scaleX(${t.offsetWidth - 24})`;
}
function go(id) {
  for (const t of tabs) t.toggleAttribute("aria-current", t.dataset.tab === id);
  for (const s of document.querySelectorAll("main section")) s.hidden = s.id !== id;
  placeLine();
}
for (const t of tabs) t.onclick = e => { e.preventDefault(); go(t.dataset.tab); history.replaceState(null, "", "#" + t.dataset.tab); };
// ?embed=simulation names the tab without an anchor: an anchor in a frame scrolls the page around it to the frame
const fromHash = () => {
  const want = location.hash.slice(1) || new URLSearchParams(location.search).get("embed");
  go(tabs.some(t => t.dataset.tab === want) ? want : "agents");
};
fromHash();
addEventListener("hashchange", fromHash);
line.style.transition = "none"; placeLine(); line.getBoundingClientRect(); line.style.transition = "";
new ResizeObserver(placeLine).observe(document.querySelector(".tabs"));

// --- the helper ---------------------------------------------------------------------------------------------------
// The recon dashboard's helper (recon-v3-mr/web components/helper.tsx): a mark that follows the pointer and opens, from
// its corner, into a box that says what is under it, what a click does and, with Details on, more. data-help="its
// name|what it is|what a click does|its details"; a block or a node says itself; the Simulation scene answers through
// simHelp (simulation.js). On by default here (Marius asked for it, 23-10); the Simulation tab's Settings turn it off.
const kept = JSON.parse(localStorage.getItem("vigie.sim") || "{}");
// off by default since 24-10 (Edouard): the box following the pointer hid the text; Settings can still turn it on
const helper = {on: kept.helper ?? false, details: kept.details ?? true};
const ICON = {
  what: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>`,
  click: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4.1 12 6"/><path d="m5.1 8-2.9-.8"/><path d="m6 12-1.9 2"/><path d="M7.2 2.2 8 5.1"/><path d="M9.037 9.69a.498.498 0 0 1 .653-.653l11 4.5a.5.5 0 0 1-.074.949l-4.349 1.041a1 1 0 0 0-.74.739l-1.04 4.35a.5.5 0 0 1-.95.074z"/></svg>`,
  details: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/></svg>`,
};
const OPENS = {agent: "Opens its checks and what KRYS did to it", role: "Opens the checks of each of its agents", service: "Opens its evidence and who reads it", metric: "Opens its figures"};
function describe(el, x, y) {
  if (!el) return null;
  if (el.closest(".sim-stage")) return window.simHelp?.(x, y) ?? null;
  const h = el.closest("[data-help]");
  if (h) { const [name, what, click, details] = h.dataset.help.split("|"); return {name, what, click, details}; }
  const b = el.closest("[data-open]");
  if (b) return {name: b.querySelector("h3, text")?.textContent, what: b.querySelector("p")?.textContent || `${b.dataset.open.split(":")[0]}, ${[...b.classList].find(c => c.startsWith("s-"))?.slice(2) ?? ""}`,
    click: OPENS[b.dataset.open.split(":")[0]]};
  const t = el.closest("[title], [data-title]");
  if (t) return {name: t.textContent.trim().slice(0, 60) || t.tagName.toLowerCase(), what: t.title || t.dataset.title};
  const svg = el.closest("path")?.querySelector("title");
  return svg ? {name: "Link", what: svg.textContent} : null;
}
(() => {
  if (!matchMedia("(hover: hover)").matches) return;
  const SIZE = 22, GAP = 12, EDGE = 8, el = document.createElement("div");
  el.className = "helper";
  el.setAttribute("aria-hidden", "true");
  el.innerHTML = `<div class="hp-corner"><div class="hp-box"><span class="hp-mark">${ICON.what}</span><div class="hp-body"></div></div></div>`;
  document.body.append(el);
  const corner = el.firstChild, box = corner.firstChild, mark = box.firstChild, body = box.lastChild;
  let tx = 0, ty = 0, frame = 0, key = "", seen = false, side = "", lifted = 0, bw = SIZE, bh = SIZE, quiet = null;
  // the browser's own tooltip would say a title a second time: it is kept aside while the pointer is on its element
  const hush = to => {
    if (to === quiet) return;
    if (quiet?.hasAttribute("data-title")) { quiet.setAttribute("title", quiet.dataset.title); quiet.removeAttribute("data-title"); }
    quiet = to;
    if (to?.hasAttribute("title")) { to.dataset.title = to.title; to.removeAttribute("title"); }
  };
  // the box opens at the pointer's lower right, or towards the left or the top where it would leave the window
  const place = () => {
    const right = tx + GAP + bw <= innerWidth - EDGE, down = ty + GAP + bh <= innerHeight - EDGE, s = `${right} ${down}`;
    const lift = down ? 0 : Math.max(0, EDGE - (ty - GAP - bh));
    if (s !== side || lift !== lifted) corner.style.transform = `translate(${right ? GAP : -GAP}px, ${(down ? GAP : -GAP) + lift}px)`;
    lifted = lift;
    if (s === side) return;
    side = s;
    for (const e of [box, mark, body]) Object.assign(e.style, {left: right ? "0" : "auto", right: right ? "auto" : "0", top: down ? "0" : "auto", bottom: down ? "auto" : "0"});
  };
  // the details one bullet a sentence, as the recon's
  const bullets = text => Array.isArray(text) ? text : text.split(/(?<=\.)\s+/);
  const blockOf = (icon, name, text, list) => `<div class="hp-block"><span class="hp-pill">${icon}${esc(name)}</span>${!text ? ""
    : list && bullets(text).length > 1 ? `<ul>${bullets(text).map(s => `<li>${esc(s)}</li>`).join("")}</ul>` : `<p>${esc(text)}</p>`}</div>`;
  const say = under => {
    const help = helper.on ? describe(under, tx, ty) : null, details = helper.details && help?.details?.length ? help.details : null;
    const k = help ? JSON.stringify([help.name, help.what, help.click, details]) : "";
    hush(under?.closest("[title], [data-title]") ?? null);
    if (k === key) return;
    key = k;
    el.classList.toggle("open", !!help);
    if (help) body.innerHTML = blockOf(ICON.what, help.name, help.what) + (help.click ? blockOf(ICON.click, "Click", help.click) : "") + (details ? blockOf(ICON.details, "Details", details, true) : "");
    [bw, bh] = help ? [body.offsetWidth, body.offsetHeight] : [SIZE, SIZE];
    Object.assign(box.style, {width: bw + "px", height: bh + "px"});
    place();
  };
  const tick = () => {
    frame = 0;
    el.style.opacity = helper.on && seen ? "1" : "0";
    say(document.elementFromPoint(tx, ty));
    el.style.transform = `translate3d(${tx}px, ${ty}px, 0)`;
    place();
  };
  const kick = () => { if (!frame) frame = requestAnimationFrame(tick); };
  addEventListener("pointermove", e => { if (e.pointerType === "touch") return; tx = e.clientX; ty = e.clientY; seen = true; kick(); }, {passive: true});
  document.documentElement.addEventListener("pointerleave", () => { seen = false; key = ""; hush(null); el.style.opacity = "0"; el.classList.remove("open"); });
  addEventListener("scroll", kick, {capture: true, passive: true});
  // the replay plays and the polls redraw under a pointer that does not move
  setInterval(() => seen && kick(), 250);
})();

(async () => {
  DEMO = await getJson("/dashboard/dashboard-demo.json");
  await Promise.all([pollGraph(), pollImprovements(), pollGuardrails()]);
  await poll();
  setInterval(poll, 1000);
  setInterval(pollGraph, 5000);
  setInterval(pollImprovements, 30000);
})();
