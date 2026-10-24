"use strict";
// KRYS's dashboard: polls the API and redraws the four tabs. What the back does not send yet, or everything when no
// simulation runs, is drawn from dashboard-demo.json beside this file, tagged "sample" (or "demo data" in the header).

const ROLES = {
  quant: "Valorise chaque actif et mesure son instabilité. Ne négocie jamais.",
  trader: "Propose des ordres : achète sous la juste valeur, vend au-dessus, réagit aux nouvelles.",
  compliance: "Vérifie que chaque ordre et chaque paiement est autorisé (KYC, liste restreinte, ordres fractionnés).",
  risk: "Mesure ce que la banque peut perdre et freine : valide, réduit ou refuse.",
  payment: "Paie les factures des fournisseurs par la passerelle de paiement.",
  executor: "N'exécute que ce que le portique de Krys approuve.",
};
const LAYERS = {1: "identité", 2: "règles", 3: "comportement", 4: "cohérence"};
const LIMIT_NAMES = {
  max_position_pct: "plus grosse position", daily_loss_pct: "perte du jour", exposure_pct: "exposition",
  var_pct: "valeur en risque", daily_loss_incl_fraud_pct: "perte fraude comprise", liquidity_floor_pct: "plancher de trésorerie", daily_pct: "payé aujourd'hui",
};
// a light or a level of KRYS: the colour of its block and the word of its pastille
const STATE = {VERT: ["green", "vert"], ORANGE: ["orange", "orange"], ROUGE: ["red", "rouge"], QUARANTAINE: ["red", "en quarantaine"]};
// what Krys did to an agent, as its action code says it; the code stays the class of its colour
const ACTION = {green: "approuvé", block: "bloqué", slow: "ralenti", flag: "signalé", quarantine: "quarantaine", advise: "avis", tune: "réglage", red: "rouge"};
const actionName = a => Object.hasOwn(ACTION, a) ? ACTION[a] : a;
const SEVERITY = {critical: "critique", warning: "alerte", info: "info"};
const PITCH = "php2shell_day";  // the scenario of the pitch's three figures
const ORDER = ["quant", "trader", "compliance", "risk", "payment", "executor"];
const COLUMN = {quant: 1, trader: 2, payment: 2, compliance: 3, risk: 4, executor: 5};  // the graph's columns, services at 0

const $ = id => document.getElementById(id);
// a number as French writes it: a decimal comma and a thin space between the thousands
const num = (x, digits = 0) => x == null || Number.isNaN(+x) ? "-" : (+x).toLocaleString("fr-FR", {minimumFractionDigits: digits, maximumFractionDigits: digits});
const eur = v => v == null ? "-" : (Math.abs(v) >= 1e6 ? num(v / 1e6, 2) + " M€" : num(Math.round(v) || 0) + " €");
// Les archives gardent leurs identifiants et preuves ; seul le texte rendu adopte le nom public.
const displayName = s => String(s ?? "").replace(/\b(?:the |The |la |La )?Vigie\b/g, "Krys").replace(/Banque Horizon/g, "Argus");
const esc = s => displayName(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const pct = (x, digits = 0) => x == null ? "-" : num(x * 100, digits) + "%";
const tone = p => p >= 100 ? "red" : p >= 80 ? "orange" : "green";
const bar = (value, limit = 100) => { const p = limit ? value / limit * 100 : 0; return `<div class="bar-in s-${tone(p)}"><i style="width:${Math.min(100, Math.max(2, p))}%"></i></div>`; };
const row = (name, value, limit, text) => `<div class="row"><span class="name" title="${esc(name)}">${esc(name)}</span>${bar(value, limit)}<span class="num">${text}</span></div>`;
const table = (head, rows, foot = "") => `<div class="frame"><table><thead><tr>${head}</tr></thead><tbody>${rows.join("")}</tbody>${foot}</table></div>`;
const chip = (c, text) => `<span class="chip s-${c}">${esc(text)}</span>`;
const count = (n, name, fill) => `<div class="count ${n ? "f-" + fill : "f-grey"}"><b>${n}</b><span>${esc(name)}</span></div>`;
const big = (value, name, fill, under = "") => `<div class="count big f-${fill}"><b>${value}</b><span>${esc(name)}</span>${under ? `<small>${under}</small>` : ""}</div>`;
// A human decision in two steps: its button asks for the confirmation, a second click within five seconds sends it,
// signed (human()). The command is data, read back by the click; nothing in a row becomes code.
const decision = (command, label, ghost = false, input = "") => {
  const ready = armed?.command === command && Date.now() < armed.until;
  return `<button type="button" class="act${ghost && !ready ? " ghost" : ""}${ready ? " armed" : ""}" data-decide="${esc(command)}"${input ? ` data-input="${esc(input)}"` : ""}>${ready ? "Confirmer ?" : esc(label)}</button>`;
};
// a question to Krys about a row, in the Krys view or in its bubble
const askButton = question => `<button type="button" class="ask" data-ask="${esc(question)}">Demander à Krys</button>`;
// no pastille that only says a colour: the border already does (Marius, 23-10)
const pastille = word => ["vert", "orange", "rouge"].includes(word) ? "" : `<span class="pastille">${esc(word)}</span>`;
const block = (kind, id, [c, word], text, title = id) => `<button type="button" class="block s-${c}" data-open="${kind}:${esc(id)}">${pastille(word)}<h3>${esc(title)}</h3><p>${text}</p></button>`;
const head = (title, [c, word]) => `<div class="dlg-head s-${c}"><h3>${esc(title)}</h3>${pastille(word)}</div>`;
const order = id => ORDER.indexOf(id.split("-")[0]) * 10 + (+id.split("-")[1] || 0);

let DEMO = null, S = {}, V = {status: {}, decisions: []}, G = {nodes: []}, I = null, GR = null;
let live = false, stale = false, moved = 0, showApprovals = false, detail = null, D = null;
// Argus shows the Krys bank only: in the three-bank demo its world is C, elsewhere the only world there is. MODE is
// /health's: live or replay.
let WORLD = "", MODE = null;
// the archived measures of Krys (vigie/console/data/impact), read once for the Gouvernance view's six indicators
let IMPACT = null;
// a human decision is sent on its second click, within five seconds: the first one only asks for the confirmation
let armed = null;
// the block a former tab's anchor names: scrolled to once the view has drawn what stands above it
let pendingBlock = null;
// the incidents Krys drafts from its signed journal (the outbox of mails-chat), and a Krys command waiting for its confirmation
let OUTBOX = null, prepared = null;

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
const SAMPLE = `<span class="sample" title="le serveur ne publie pas encore cette donnée : exemple tiré de dashboard-demo.json">exemple</span>`;
const sample = (...keys) => keys.some(k => D.fake.has(k)) ? SAMPLE : "";
// Live, a part the bank does not publish is not filled from the sample: it says so, and Krys answers in the chat.
// Without a simulation, the whole page is the sample and says so in its header.
const known = (...keys) => !live || !keys.some(k => D.fake.has(k));
const unpublished = question => `<p class="empty">La banque en direct ne publie pas cette donnée. ${askButton(question)}</p>`;
// what Krys publishes on the bus, live: its last status (suspects, frozen assets, R0, propositions) and its findings
const krysStatus = () => live ? (D.S.alerts || []).filter(a => a.kind === "krys_status").at(-1) ?? null : null;
const krysFindings = () => live ? (D.S.alerts || []).filter(a => a.kind === "layer3_finding").map(a => a.finding).filter(Boolean) : [];
// in the three-bank demo, human commands go through Krys (prepared, then confirmed); elsewhere through /inject
const viaKrys = () => WORLD !== "";

async function poll() {
  const [s, v] = await Promise.all([getJson("/state" + WORLD), getJson("/vigie" + WORLD)]);
  // stale: the API is gone, or it still answers but the world's tick has not moved for 5 s
  if (s && v) { live = true; if (s.tick !== S.tick) moved = Date.now(); stale = Date.now() - moved > 5000; S = s; V = v; } else if (live) stale = true;
  render();
}
async function pollGraph() { const g = await getJson("/graph" + WORLD); if (g) G = g; }
async function pollHealth() { const h = await getJson("/health"); MODE = h?.mode ?? null; WORLD = h?.worlds?.includes("C") ? "?world=C" : ""; }
async function pollImprovements() { I = await getJson("/improvements"); }
async function pollGuardrails() { GR = await getJson("/guardrails"); }
async function readImpact() { IMPACT = await getJson("/dashboard/console/impact/data.json"); }
async function pollOutbox() { const o = await getJson("/console/mails-chat/outbox"); OUTBOX = Array.isArray(o?.items) ? o.items : null; }

// --- shared readings ----------------------------------------------------------------------------------------------

function suspects() {
  if (live && D.fake.has("metrics") && D.fake.has("isolated_services"))
    return Object.fromEntries((krysStatus()?.suspects || []).map(sid => [sid, {since_tick: null, text: "isolé par Krys"}]));
  const out = {...D.st.metrics?.suspect_services};
  for (const sid of D.st.isolated_services || []) out[sid] ??= {since_tick: "?", text: "isolé par Krys"};
  return out;
}
// an agent's state is the worst of its sanction level and its four layer lights: an orange layer does not show green
const RANK = ["VERT", "ORANGE", "ROUGE", "QUARANTAINE"];
function agentState(id) {
  const a = D.S.agents?.[id] || {}, va = known("agents") ? D.st.agents?.[id] || {} : {};
  const level = [va.level || a.light || "VERT", ...Object.values(va.lights || {})].reduce((x, y) => RANK.indexOf(y) > RANK.indexOf(x) ? y : x);
  return a.alive === false && level === "VERT" ? ["grey", "silencieux"] : STATE[level] || ["grey", level];
}
// red: KRYS distrusts it; orange: layer 1 reports it exposed (a CVE, a large impact radius) but it is still trusted
const serviceState = id => suspects()[id] ? ["red", "suspect"] : known("graph") && (D.st.graph?.suspects || []).some(s => s.service === id) ? ["orange", "exposé"] : ["green", "de confiance"];
const agentIds = () => Object.keys(D.S.agents || {}).sort((x, y) => order(x) - order(y));
const roleOf = id => D.S.agents?.[id]?.role || id.split("-")[0];
const bankLight = () => D.S.light?.light || D.st.light || "VERT";
const actionsOf = id => (D.st.actions || []).filter(x => x.agent === id && x.action !== "green");
function ledgerGroups(filter = () => true) {
  const groups = new Map();
  for (const e of (D.st.ledger_evidence || []).filter(filter).slice().reverse()) {
    const text = Object.entries(e).filter(([k]) => !["service", "reason", "proof"].includes(k))
      .map(([k, v]) => `${k}: ${typeof v === "number" ? (Math.abs(v) < 1000 ? v.toLocaleString("fr-FR", {maximumFractionDigits: 2}) : num(Math.round(v))) : JSON.stringify(v)}`).join(" · ");
    const k = e.service + e.reason + (e.asset ?? text);  // the newest evidence of each service, reason and asset
    if (groups.has(k)) groups.get(k).count++; else groups.set(k, {...e, text, count: 1});
  }
  return [...groups.values()];
}
const ledgerTable = groups => groups.length ? table(`<th>service</th><th>raison</th><th>preuve</th><th class="num">fois</th>`,
  groups.slice(0, 8).map(e => `<tr><td>${esc(e.service)}</td><td class="a-block">${esc(e.reason)}</td><td>${esc(e.text)}</td><td class="num">${e.count}</td></tr>`)) : `<p class="empty">aucune</p>`;
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
  renderBar(); renderSummary(); renderSuggestions(); renderSynthese(); renderAgents(); renderActions(); renderGuardrails(); renderMetrics(); renderGovernance(); renderImprove(); renderDetail();
  if (pendingBlock) { $(pendingBlock)?.scrollIntoView({block: "start"}); pendingBlock = null; }
}

function renderBar() {
  const [c, word] = STATE[bankLight()] || ["grey", bankLight()], lamp = $("light");
  // no green pastille anywhere (Marius): a green bank shows a plain "normal", the lamp lights only for orange and red
  lamp.className = c === "green" ? "box lamp calm" : `box lamp s-${c}`; lamp.textContent = c === "green" ? "normal" : word;
  const conn = $("conn"), mode = $("mode");
  // live or a recording, as /health says it; without a running simulation, the sample data says so
  mode.textContent = !live ? "Données d'exemple" : MODE === "replay" ? "Rejeu" : "Direct";
  mode.className = !live ? "mode warn" : MODE === "replay" ? "mode" : "mode live";
  mode.title = !live ? "Aucune simulation en cours : la console montre des données d'exemple" : "";
  // a finished day is not a breakdown (Edouard, 24-10): say which one it is
  conn.textContent = live && stale ? (S.tick >= 179 ? "journée terminée" : `en pause · tick ${S.tick}`) : "";
  conn.className = live && stale ? "warn" : "";
  document.body.classList.toggle("stale", stale);
}

function renderAgents() {
  const ids = agentIds(), sus = suspects();
  const red = ids.filter(id => agentState(id)[0] === "red").length, nSus = Object.keys(sus).length;
  let html = `<p class="lead"><b>${red} agent${red === 1 ? "" : "s"} sur ${ids.length}</b> en rouge · <b>${nSus}</b> service${nSus === 1 ? "" : "s"} suspect${nSus === 1 ? "" : "s"} · cliquez un bloc pour ses contrôles</p>`;
  // agents of one role are one block, their count in its pastille and the worst of their colours on its border (Marius, 23-10)
  const roles = Map.groupBy(ids, roleOf), worst = ["green", "grey", "orange", "red"];
  html += `<div class="grid three">${[...roles].map(([role, members]) => members.length === 1
    ? block("agent", members[0], agentState(members[0]), esc(ROLES[role] || ""))
    : block("role", role, [members.map(id => agentState(id)[0]).reduce((x, y) => worst.indexOf(y) > worst.indexOf(x) ? y : x), String(members.length)], esc(ROLES[role] || ""))).join("")}</div>`;
  html += `<h2>Qui alimente qui<span class="hint">un nœud passe au rouge dès qu'une donnée d'un service suspect l'atteint</span>${known("contamination_graph") ? sample("contamination_graph") : ""}</h2>${graph()}`;
  put("agents", html);
}

/** The graph screen: KRYS's contamination graph (layer 3), with the hidden dependencies layer 1 found. */
function graph() {
  if (!known("contamination_graph")) return unpublished("Quels services ont contaminé quels agents ?");
  const g = D.st.contamination_graph || {nodes: [], edges: []};
  if (!g.nodes?.length) return `<p class="empty">aucun message pour l'instant</p>`;
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
    `<path d="${path(at[e.source], at[e.target])}" class="edge${e.contaminated ? " bad" : ""}"><title>${esc(e.source)} → ${esc(e.target)} : ${e.count} ${e.kind === "message" ? "messages" : "lectures"}${e.contaminated ? ", contaminé" : ""}</title></path>`);
  for (const e of (D.st.graph?.edges || []).filter(e => e.hidden && at[e.source] && at[e.target]))
    edges.push(`<path d="${path(at[e.target], at[e.source])}" class="edge hidden-dep"><title>dépendance cachée : ${esc(e.source)} appelle ${esc(e.target)} (${e.calls} appels), jamais déclarée</title></path>`);
  const nodes = g.nodes.map(n => { const p = at[n.id]; return `<g class="node s-${color(n)}" data-open="${n.kind === "service" ? "service" : "agent"}:${esc(n.id)}" tabindex="0" role="button" transform="translate(${p.x.toFixed(1)},${p.y.toFixed(1)})"><rect width="${NW}" height="${NH}" rx="10"/><text x="${NW / 2}" y="${NH / 2 + 4.5}" text-anchor="middle">${esc(n.id)}</text></g>`; });
  return `<div class="frame graph"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Qui alimente qui">${edges.join("")}${nodes.join("")}</svg>
    <div class="legend"><span><i style="border-color:var(--red)"></i>a porté une donnée d'un service suspect</span><span><i style="border-color:rgb(14 23 38 / .3)"></i>saine</span><span><i style="border-color:var(--orange);border-top-style:dashed"></i>dépendance cachée (jamais déclarée)${sample("graph")}</span></div></div>`;
}

function agentDetail(id) {
  const st = D.st, ind = st.metrics || {}, a = D.S.agents?.[id] || {}, va = st.agents?.[id] || {}, role = a.role || id.split("-")[0];
  const susp = va.suspicion || 0, sus = suspects(), deps = D.G.nodes.find(n => n.id === id)?.depends_on || [];
  let html = head(id, agentState(id)) + `<p>${esc(ROLES[role] || role)}</p>`;
  // live, Krys's detailed checks on an agent are not published: she answers about it in the chat
  if (!known("agents", "metrics")) return html + `<div class="chips">${chip(a.alive === false ? "red" : "green", a.alive === false ? "silencieux" : "en vie")}${a.paused ? chip("orange", "en pause") : ""}</div>` + unpublished(`Que sait Krys de ${id} ?`);
  html += `<h4>Les quatre couches de <span class="name">Krys</span> sur lui</h4><div class="chips">${[1, 2, 3, 4].map(n => { const l = va.lights?.[n] || "VERT", [c, w] = STATE[l] || ["grey", l]; return chip(c, `${n} ${LAYERS[n]}: ${w}`); }).join("")}</div>`;
  html += `<div class="chips">${chip(a.alive === false ? "red" : "green", a.alive === false ? "silencieux" : "en vie")}${a.paused ? chip("orange", "en pause") : ""}${va.quarantined ? chip("red", "en quarantaine : clé révoquée, outils coupés") : ""}${a.vigie_lost ? chip("orange", "a perdu Krys") : ""}`
    + deps.map(d => chip(sus[d] ? "red" : "grey", `lit ${d}${sus[d] ? " (suspect)" : ""}`)).join("") + chip(susp >= 50 ? "red" : susp > 0 ? "orange" : "green", `soupçon ${num(susp)}/100`) + `</div>`;
  let rows = "";
  for (const [name, v] of Object.entries(ind.limits?.[id] || {}))
    rows += row(LIMIT_NAMES[name] || name, v.pct, 100, `${num(v.pct)}%${v.ticks_to_breach != null ? ` · ${Math.round(v.ticks_to_breach)} ticks` : ""}`);
  if (id === "quant") for (const [asset, z] of Object.entries(ind.price_drift_z || {})) rows += row(`saut de ${asset}`, z, ind.thresholds?.price_drift_z || 4, `${num(z, 1)}σ`);
  if (id === "risk" && ind.reconciliation?.gap_pct != null) rows += row("trésorerie / registre", ind.reconciliation.gap_pct, ind.thresholds?.reconciliation_gap_pct || 1, `${num(ind.reconciliation.gap_pct, 1)}%`);
  const b = st.behavior?.[id];
  if (b) rows += row("ordres, 20 derniers", b.cumulative_eur, 2 * b.limit_eur, eur(b.cumulative_eur)) + row("ordres près de la limite", b.near_count, b.near_threshold, `${b.near_count}/${b.near_threshold}`);
  for (const [k, c] of Object.entries(st.cusum?.[id] || {})) rows += row(`CUSUM des ordres (${k})`, c.cusum, c.threshold, `${num(c.cusum, 1)} / ${num(c.threshold, 1)}`);
  html += `<h4>Contrôles : part de la limite vue par l'agent · ticks avant dépassement</h4>${rows ? `<div>${rows}</div>` : `<p class="empty">aucune limite propre</p>`}`;
  const m = st.layer1?.agents?.[id];
  if (m) html += `<h4>Son historique (couche 1)${sample("layer1")}</h4><div class="chips">${chip("grey", `${m.decisions} décisions, ${pct(m.decision_rate, 1)} des ticks`)}${m.refusal_rate != null ? chip(m.refusal_rate > 0.2 ? "orange" : "grey", `${pct(m.refusal_rate)} refusées`) : ""}`
    + `${m.data_age_ticks ? chip(m.data_age_ticks.max > 20 ? "orange" : "grey", `données jusqu'à ${m.data_age_ticks.max} ticks d'âge`) : ""}${m.heartbeat ? chip(m.heartbeat.silent_ticks ? "orange" : "grey", `régularité du battement ${pct(m.heartbeat.regularity)}`) : ""}`
    + `${m.repetitions?.max > 3 ? chip("orange", `répété ${m.repetitions.max}× : ${m.repetitions.action}`) : ""}${m.llm ? chip("grey", `confiance LLM ${m.llm.confidence}, diversité ${m.llm.justification_diversity}`) : ""}</div>`;
  const p = st.profiles?.[id];
  if (p) html += `<p class="hint" style="margin:0">Référence saine : ${num(p.frequency, 2)} ordres par tick, au plus ${eur(p.max_cumulative_20)} sur 20 ordres, ${p.max_near_20} près de la limite ; négocie ${esc((p.assets || []).join(", "))}.</p>`;
  const orders = (st.orders?.[id] || []).slice(-6).reverse();
  if (orders.length) html += `<h4>Ses derniers ordres</h4>` + table(`<th>tick</th><th>actif</th><th class="num">montant</th>`, orders.map(o => `<tr><td>${o.tick}</td><td>${esc(o.asset)}</td><td class="num">${eur(o.notional_eur)}</td></tr>`));
  const acts = actionsOf(id).slice(-6).reverse();
  html += `<h4>Ce que <span class="name">Krys</span> lui a fait</h4>` + (acts.length ? table(`<th>tick</th><th>action</th><th>pourquoi</th><th>couche</th>`,
    acts.map(x => `<tr><td>${x.tick}</td><td class="a-${esc(x.action)}"><b>${esc(actionName(x.action))}</b></td><td>${esc(x.text || x.reason)}</td><td>${esc(x.layer)}</td></tr>`)) : `<p class="empty">rien : toutes ses actions ont été approuvées</p>`);
  if (va.quarantined) html += `<div>${decision(`human unblock ${id}`, "Lever la quarantaine")}</div>`;
  return html;
}

function serviceDetail(id) {
  const s = D.G.nodes.find(n => n.id === id) || {}, f = suspects()[id];
  const users = D.G.nodes.filter(n => n.kind === "agent" && (n.depends_on || []).includes(id)).map(n => n.id);
  let html = head(id, serviceState(id)) + `<p>PHP ${esc(s.php_version)} · ${s.patchable ? "corrigeable" : "non corrigeable"}${s.owner_team ? ` · exploité par ${esc(s.owner_team)}` : ""}</p>`;
  if (s.cve?.length) html += `<div class="chips">${s.cve.map(c => chip("orange", `${c.id} ${c.name}: ${String(c.status).replaceAll("_", " ")}`)).join("")}</div>`;
  if (f) html += `<p><b>Suspect depuis le tick ${esc(f.since_tick)}</b> : ${esc(f.text)}</p><div>${decision(`human unblock ${id}`, "Lui refaire confiance")}</div>`;
  html += `<h4>Agents qui déclarent le lire</h4><div class="chips">${users.map(u => chip(agentState(u)[0], u)).join("") || `<span class="empty">aucun</span>`}</div>`;
  const radius = known("graph") ? D.st.graph?.nodes?.find(n => n.id === id)?.radius : null;
  if (radius) html += `<h4>Rayon d'impact (couche 1)${sample("graph")}</h4><p>${radius.agents} agents sur ${agentIds().length} en dépendent (${pct(radius.share)}) : ${esc(radius.direct.join(", "))} directement${radius.transitive.length ? `, ${esc(radius.transitive.join(", "))} par leur intermédiaire` : ""}. ${eur(radius.eur)} d'exécutions reposent sur lui.</p>`;
  const reached = (D.st.contamination_graph?.edges || []).filter(e => e.source === id && e.contaminated).map(e => e.target);
  if (reached.length) html += `<h4>Où sont allées ses données suspectes</h4><div class="chips">${reached.map(t => chip("red", t)).join("")}</div>`;
  html += `<h4>Preuves (registre fantôme, ancre de prix indépendante)</h4>` + (known("ledger_evidence") ? ledgerTable(ledgerGroups(e => e.service === id)) : unpublished(`Pourquoi ${id} est-il suspect ?`));
  return html;
}

function renderActions() {
  const st = D.st, actions = st.actions || [], decisions = D.decisions;
  const incidents = (st.incidents || []).filter(i => i.level !== "VERT").sort((x, y) => y.last_tick - x.last_tick);
  let html = `<h2>Incidents ouverts${known("incidents") ? sample("incidents") : ""}<span class="hint">répétitions regroupées</span></h2>`;
  // live, the incidents are those Krys groups from its signed journal (the outbox of the console)
  if (!known("incidents")) html += OUTBOX?.length ? table(`<th>incident</th><th>période</th><th>impact</th><th class="num">question</th>`,
    OUTBOX.map(i => `<tr><td>${esc(i.subject)}</td><td>${esc(i.period)}</td><td>${esc(i.impact)}</td><td class="num">${askButton(i.evidence?.length ? `Pourquoi ${i.evidence[0]} ?` : "Quelles sont les alertes en cours ?")}</td></tr>`))
    : OUTBOX ? `<p class="empty">aucun incident dans le journal signé</p>` : unpublished("Quelles sont les alertes en cours ?");
  else html += (incidents.length ? table(`<th>incident</th><th>origine</th><th>niveau</th><th>raisons</th><th>ticks</th><th class="num">question</th>`,
    incidents.map(i => `<tr><td>${esc(i.id)}</td><td>${esc(i.root)}</td><td class="${i.level === "ORANGE" ? "a-slow" : "a-block"}"><b>${esc((STATE[i.level] || [, i.level])[1])}</b></td><td>${esc(i.reasons.join(", "))}</td><td>${i.first_tick} → ${i.last_tick}</td><td class="num">${askButton(i.proof?.length ? `Pourquoi ${i.proof[0]} ?` : `Pourquoi l'incident ${i.id} ?`)}</td></tr>`)) : `<p class="empty">aucun incident ouvert</p>`);
  const refused = decisions.filter(d => !d.approved).slice().reverse().slice(0, 10), gate = D.S.decisions?.["execution:refused"];
  html += `<h2>Refusés au portique<span class="hint">les plus récents d'abord</span></h2>`;
  // live, the gate's decisions may only be counted by the bank: the count, and Krys for the details
  if (live && !refused.length && gate) html += `<p class="lead"><b>${gate} opération${gate === 1 ? "" : "s"}</b> refusée${gate === 1 ? "" : "s"} au portique depuis le début de la journée. ${askButton("Qu'est-ce qui est bloqué en ce moment ?")}</p>`;
  else html += (refused.length ? table(`<th>tick</th><th>demande</th><th>raison</th><th class="num">question</th>`,
    refused.map(d => `<tr><td>${d.tick}</td><td>${esc(d.request_id)}</td><td class="a-block">${esc(d.reason)}</td><td class="num">${askButton(`Pourquoi ${d.request_id} ?`)}</td></tr>`)) : `<p class="empty">aucun refus pour l'instant</p>`);
  put("actions", html);
  if (!known("actions")) return put("actions-log", unpublished("Qu'a fait Krys depuis le début de la journée ?"));
  // the full journal of Krys's actions, folded under the view (its <details> stays open across the redraws)
  const n = k => actions.filter(a => a.action === k).length;
  let log = `<div class="counts">${count(n("slow"), "agents ralentis", "orange")}${count(n("quarantine"), "quarantaines", "red")}${count(n("flag"), "signalements", "orange")}${count(st.contagion_blocked || 0, "chaînes contaminées coupées", "red")}</div>`;
  log += `<h2>Pourquoi un service a été isolé<span class="hint">registre fantôme reconstruit à partir des exécutions signées, ancre de prix indépendante</span></h2>` + ledgerTable(ledgerGroups());
  const claims = (st.claim_evidence || []).slice().reverse();
  log += `<h2>Ce qu'un agent a affirmé, et ce que Krys a constaté${sample("claim_evidence")}</h2>` + (claims.length ? table(`<th>agent</th><th>client</th><th>affirmé</th><th>constaté</th>`,
    claims.slice(0, 8).map(c => `<tr><td>${esc(c.agent)}</td><td>${esc(c.client_id)}</td><td>${esc(c.assertion)}</td><td class="a-block">${esc(c.truth)}</td></tr>`)) : `<p class="empty">aucune fausse affirmation détectée</p>`);
  log += `<h2>Chaque décision de Krys sur un agent<label class="hint"><input type="checkbox" ${showApprovals ? "checked" : ""} onchange="showApprovals=this.checked;render()"> montrer les approbations</label></h2>`;
  const rows = actions.filter(a => showApprovals || a.action !== "green").slice().reverse().slice(0, 40);
  log += rows.length ? table(`<th>tick</th><th>agent / cible</th><th>action</th><th>pourquoi</th><th>couche</th><th class="num">soupçon</th>`,
    rows.map(a => `<tr><td>${a.tick}</td><td>${esc(a.agent)}</td><td class="a-${esc(a.action)}"><b>${esc(actionName(a.action))}</b></td><td>${esc(a.text || a.reason)}</td><td>${esc(a.layer)}</td><td class="num">${a.suspicion ?? ""}</td></tr>`)) : `<p class="empty">aucune action pour l'instant</p>`;
  put("actions-log", log);
}

/** The blocks of the Metrics tab, one a question: its state, its line, and what its click opens. */
const METRICS = {
  price: () => {
    const ind = D.st.metrics || {}, th = ind.thresholds?.price_drift_z || 4, z = Object.entries(ind.price_drift_z || {}).sort((a, b) => b[1] - a[1]);
    const anchors = ledgerGroups(e => e.reason === "ANCHOR_DIVERGENCE"), top = z[0]?.[1] ?? 0, feed = suspects()["market-feed"];
    // once market-feed is suspect, a calm last tick does not make its prices clean again
    return {title: "Flux de prix", state: feed ? ["red", "suspect"] : top >= th || anchors.length ? ["red", "manipulé ?"] : top >= .8 * th ? ["orange", "à surveiller"] : ["green", "propre"],
      text: (feed ? `market-feed suspect depuis le tick ${esc(feed.since_tick)} : ce qu'il a alimenté est refusé. ` : "") + (z.length ? `Plus grand saut en un tick : ${esc(z[0][0])} ${num(top, 1)}σ, alerte à ${num(th, 1)}σ. ${anchors.length} divergence${anchors.length === 1 ? "" : "s"} avec l'ancre indépendante.` : "Il faut 20 ticks de prix."),
      detail: () => `<h4>Mouvement en un tick par actif, en écarts-types</h4><div>${z.map(([a, v]) => row(a, v, th, `${num(v, 1)}σ`)).join("") || `<p class="empty">il faut 20 ticks de prix</p>`}</div><h4>Les prix du Quant face à l'ancre indépendante</h4>${ledgerTable(anchors)}`};
  },
  ledger: () => {
    const ind = D.st.metrics || {}, r = ind.reconciliation || {}, th = ind.thresholds?.reconciliation_gap_pct || 1, ev = ledgerGroups(e => e.reason !== "ANCHOR_DIVERGENCE");
    return {title: "Registre", state: (r.gap_pct ?? 0) >= th || ev.length ? ["red", "mensonge ?"] : r.gap_pct == null ? ["grey", "en attente"] : ["green", "cohérent"],
      text: r.gap_pct != null ? `La trésorerie vue par Risk et le registre de référence s'écartent de ${num(r.gap_pct, 1)}%, alerte à ${num(th, 1)}%. ${ev.length} écart${ev.length === 1 ? "" : "s"} avec le registre fantôme.` : "En attente du battement de Risk.",
      detail: () => `<h4>La trésorerie de Risk face au registre de référence</h4>${r.gap_pct != null ? row("écart", r.gap_pct, th, `${num(r.gap_pct, 1)}%`) + row("vue par Risk", 0, 1, eur(r.risk_cash_eur)) + row("registre de référence", 0, 1, eur(r.record_cash_eur)) : `<p class="empty">en attente du battement de Risk</p>`}<h4>Soldes face au registre fantôme reconstruit à partir des exécutions signées</h4>${ledgerTable(ev)}`};
  },
  limits: () => {
    const ind = D.st.metrics || {}, h = ind.thresholds?.breach_horizon_ticks || 10, all = [];
    for (const [agent, limits] of Object.entries(ind.limits || {})) for (const [name, v] of Object.entries(limits)) all.push([agent, name, v]);
    all.sort((a, b) => (b[2].pct >= 100) - (a[2].pct >= 100) || (a[2].ticks_to_breach ?? 1e9) - (b[2].ticks_to_breach ?? 1e9) || b[2].pct - a[2].pct);
    const next = all[0], over = all.filter(x => x[2].pct >= 100).length, soon = all.filter(x => x[2].ticks_to_breach != null && x[2].ticks_to_breach <= h).length;
    const beh = Object.entries(D.st.behavior || {}).filter(([, b]) => b.alert).map(([a]) => a);
    return {title: "Limites", state: over || beh.length ? ["red", "dépassée"] : soon ? ["orange", "proche"] : ["green", "dans les bornes"],
      text: next ? `${over ? `${over} au-delà de la limite vue par les agents, la pire d'abord` : "La plus proche"} : ${esc(next[0])}, ${esc(LIMIT_NAMES[next[1]] || next[1])} à ${num(next[2].pct)}%${next[2].ticks_to_breach != null ? `, dépassée dans ${Math.round(next[2].ticks_to_breach)} ticks` : ""}. Alerte sous ${h} ticks.` : "Aucune limite lue pour l'instant.",
      detail: () => `<h4>Chaque limite, part utilisée et ticks avant dépassement (extrapolés des 10 derniers ticks)</h4>` + (all.length ? table(`<th>agent</th><th>limite</th><th class="num">utilisée</th><th class="num">ticks avant dépassement</th>`,
        all.map(([a, n, v]) => `<tr><td>${esc(a)}</td><td>${esc(LIMIT_NAMES[n] || n)}</td><td class="num ${v.pct >= 100 ? "a-red" : v.pct >= 80 ? "a-slow" : ""}">${num(v.pct)}%</td><td class="num">${v.ticks_to_breach != null ? Math.round(v.ticks_to_breach) : "-"}</td></tr>`)) : `<p class="empty">aucune</p>`)
        + `<h4>Ordres sur les 20 derniers, face au maximum sain de chaque trader</h4>` + (Object.keys(D.st.behavior || {}).length ? table(`<th>trader</th><th class="num">cumul</th><th class="num">maximum sain</th><th class="num">ordres près de la limite</th><th>alerte</th>`,
        Object.entries(D.st.behavior).map(([a, b]) => `<tr><td>${esc(a)}</td><td class="num">${eur(b.cumulative_eur)}</td><td class="num">${eur(b.reference_max_eur)}</td><td class="num">${b.near_count}/${b.near_threshold}</td><td class="${b.alert ? "a-red" : "a-green"}">${b.alert ? "oui" : "non"}</td></tr>`)) : `<p class="empty">aucun ordre pour l'instant</p>`)};
  },
  behavior: () => {
    const rows = [];
    for (const [a, obs] of Object.entries(D.st.cusum || {})) for (const [k, c] of Object.entries(obs)) rows.push([a, k, c]);
    const over = rows.filter(([, , c]) => c.cusum >= c.threshold), worst = rows.sort((x, y) => y[2].cusum / y[2].threshold - x[2].cusum / x[2].threshold)[0];
    return {title: "Comportement", state: over.length ? ["red", "dérive"] : worst && worst[2].cusum >= .7 * worst[2].threshold ? ["orange", "à surveiller"] : ["green", "habituel"],
      text: worst ? `Nombre et taille des ordres de chaque trader face à son profil sain (CUSUM). Le plus proche : ${esc(worst[0])}, ordres (${esc(worst[1])}), à ${num(worst[2].cusum, 1)} sur ${num(worst[2].threshold, 1)}.` : "Aucun ordre pour l'instant.",
      detail: () => `<h4>CUSUM par trader, face à son seuil calibré</h4>` + (rows.length ? table(`<th>trader</th><th>série</th><th class="num">valeur</th><th class="num">z robuste</th><th class="num">CUSUM</th><th class="num">seuil</th>`,
        rows.map(([a, k, c]) => `<tr><td>${esc(a)}</td><td>ordres (${esc(k)})</td><td class="num">${typeof c.value === "number" && c.value > 1000 ? eur(c.value) : c.value}</td><td class="num">${num(c.z, 1)}</td><td class="num ${c.cusum >= c.threshold ? "a-red" : ""}">${num(c.cusum, 1)}</td><td class="num">${num(c.threshold, 1)}</td></tr>`)) : `<p class="empty">aucun ordre pour l'instant</p>`)};
  },
  herding: () => {
    const h = D.st.herding || {}, ind = D.st.metrics || {}, th = ind.thresholds?.herding_share ?? 1;
    const same = Object.entries(ind.herding || {}).filter(([, x]) => x.share >= th), crit = (h.findings || []).some(f => f.severity === "critical");
    return {title: "Grégarité", state: crit || same.length ? ["red", "troupeau"] : h.mimetism_index >= 2 ? ["orange", "à surveiller"] : ["green", "indépendants"],
      text: `Indice de mimétisme ${num(h.mimetism_index ?? 0, 1)}× le niveau habituel du desk ; ${num(h.effective_agents ?? h.traders ?? 3, 1)} traders sur ${h.traders ?? 3} agissent indépendamment.${sample("herding")}`,
      detail: () => {
        const tr = Object.keys(h.correlation || {}).concat(Object.values(h.correlation || {}).flatMap(Object.keys)).filter((v, i, a) => a.indexOf(v) === i).sort();
        const corr = (a, b) => a === b ? 1 : h.correlation?.[a]?.[b] ?? h.correlation?.[b]?.[a];
        const loop = h.reflexive_loop?.last;
        return `<p>Sur les ${h.window_ticks ?? "-"} derniers ticks, ${pct(h.same_side_share)} des autres traders ont pris le même sens sur le même actif dans les 5 ticks d'un ordre (d'habitude : ${pct(h.baseline_share)}). Le pire jusqu'ici : indice ${num(h.worst?.mimetism_index, 1)} au tick ${h.worst?.mimetism_tick ?? "-"}, ${num(h.worst?.effective_agents, 1)} traders indépendants au tick ${h.worst?.effective_tick ?? "-"}.</p>`
          + `<h4>Corrélation des décisions des traders</h4>` + (tr.length ? table(`<th></th>${tr.map(t => `<th class="num">${esc(t)}</th>`).join("")}`, tr.map(a => `<tr><th>${esc(a)}</th>${tr.map(b => { const c = corr(a, b); return `<td class="num ${c >= .6 && a !== b ? "a-red" : ""}">${c == null ? "-" : num(c, 2)}</td>`; }).join("")}</tr>`)) : `<p class="empty">aucun ordre pour l'instant</p>`)
          + `<h4>Boucle réflexive : une vente, la baisse de prix du Quant, une nouvelle vente</h4><p>${h.reflexive_loop?.links ?? 0} maillons, ${h.reflexive_loop?.max_depth ?? 0} de profondeur au plus.${loop ? ` Dernière au tick ${loop.tick} sur ${esc(loop.asset)} : prix ${num(loop.drop_pct, 1)}%, puis ${esc(String(loop.sell).split(":")[0])} a revendu.` : ""}</p>`
          + `<h4>Même sens en 3 ticks, par actif (le contrôle en direct)</h4>` + (Object.entries(ind.herding || {}).map(([a, x]) => row(`${a} ${({buy: "achat", sell: "vente"})[x.side] ?? x.side}`, x.share, th, `${x.traders.length}/3`)).join("") || `<p class="empty">aucun ordre pour l'instant</p>`);
      }};
  },
  contagion: () => {
    const st = D.st, iso = st.isolated_services || [], recs = Object.values(st.propagation || {}), R = r0();
    return {title: "Contamination", state: iso.length ? ["red", "contenue"] : recs.length ? ["orange", "se propage"] : ["green", "aucune"],
      text: `${iso.length} service${iso.length === 1 ? "" : "s"} isolé${iso.length === 1 ? "" : "s"}, ${st.contagion_blocked || 0} chaînes contaminées coupées au portique${R != null ? `, R0 ${num(R, 1)} agents par message empoisonné` : ""}.`,
      detail: () => `<p>Chaque message dont la provenance passe par un service suspect est marqué, et la marque suit depends_on. Le portique ne refuse que les opérations marquées, avec la chaîne pour preuve.</p><div class="chips">${iso.map(s => chip("red", `${s} isolé`)).join("")}</div>`
        + `<h4>Messages empoisonnés et agents qui ont agi dessus, du plus récent au plus ancien</h4>` + (recs.length ? table(`<th>service</th><th class="num">tick</th><th>agents atteints</th><th class="num">réactions</th>`,
        recs.slice().reverse().slice(0, 15).map(r => `<tr><td>${esc(r.service)}</td><td class="num">${r.source_tick}</td><td>${esc((r.agents || []).join(", "))}</td><td class="num">${r.reaction_count}</td></tr>`)) : `<p class="empty">aucun</p>`)};
  },
  auditor: () => {
    const st = D.st, acts = (st.actions || []).filter(a => a.layer === "auditor");
    return {title: "Auditeur LLM", state: !st.auditor_llm ? ["grey", "éteint"] : acts.length ? ["orange", `${acts.length} avis`] : ["green", "silencieux"],
      text: st.auditor_llm ? "Lit le raisonnement des agents ; ne peut qu'augmenter le soupçon sur un agent, jamais approuver." : "Éteint : ne tourne qu'avec MISTRAL_API_KEY. Il ne peut qu'augmenter le soupçon, jamais approuver.",
      detail: () => `<p>Un second avis sur les justifications des agents LLM. Ses avis augmentent le soupçon et n'approuvent jamais rien ; le portique reste déterministe.</p>` + (acts.length ? table(`<th>tick</th><th>agent</th><th>avis</th><th class="num">soupçon</th>`,
        acts.slice().reverse().map(a => `<tr><td>${a.tick}</td><td>${esc(a.agent)}</td><td>${esc(a.reason)}</td><td class="num">${a.suspicion}</td></tr>`)) : `<p class="empty">aucun avis${st.auditor_llm ? " pour l'instant" : " : il est éteint"}</p>`)};
  },
};

function renderMetrics() {
  const st = D.st;
  if (!known("metrics")) {
    const status = krysStatus(), found = krysFindings();
    put("metrics", status ? `<div class="counts">${count(status.suspects?.length ?? 0, "services isolés", "red")}${count(status.frozen?.length ?? 0, "actifs gelés", "orange")}${count(status.r0 ? num(status.r0, 1) : 0, "R0 de la contagion", "orange")}${count(found.length, "constats récents", "blue")}</div>`
      : unpublished("Que surveille Krys en ce moment ?"));
    put("metrics-log", found.length ? table(`<th>tick</th><th>couche</th><th>type</th><th>cible</th><th>gravité</th><th class="num">score</th>`,
      found.slice().reverse().map(f => `<tr><td>${f.tick}</td><td>${f.layer}</td><td>${esc(String(f.kind).replaceAll("_", " "))}</td><td>${esc(f.target)}</td><td>${esc(SEVERITY[f.severity] ?? f.severity)}</td><td class="num">${num(+f.score, 2)}</td></tr>`)) : `<p class="empty">aucun constat publié pour l'instant</p>`);
    return;
  }
  put("metrics", `<div class="grid">${Object.entries(METRICS).map(([k, f]) => { const m = f(); return block("metric", k, m.state, m.text, m.title); }).join("")}</div>`);
  const fs = findings().slice().reverse().slice(0, 12);
  let log = `<h2>Constats, du plus récent au plus ancien<span class="hint">ce que les couches signalent à la couche de décision</span>${sample("findings")}</h2>` + (fs.length ? table(`<th>tick</th><th>couche</th><th>type</th><th>cible</th><th>gravité</th><th class="num">score</th><th>quoi</th>`,
    fs.map(f => `<tr><td>${f.tick}</td><td>${f.layer}</td><td>${esc(f.kind.replaceAll("_", " "))}</td><td>${esc(f.target)}</td><td class="${f.severity === "critical" ? "a-red" : f.severity === "warning" ? "a-slow" : ""}">${esc(SEVERITY[f.severity] ?? f.severity)}</td><td class="num">${num(+f.score, 2)}</td><td>${esc(f.detail?.text || Object.entries(f.detail || {}).filter(([, v]) => typeof v !== "object").map(([k, v]) => `${k.replaceAll("_", " ")} ${v}`).join(" · "))}</td></tr>`)) : `<p class="empty">aucun pour l'instant</p>`);
  const alerts = (st.metrics?.alerts || []).slice().reverse().slice(0, 12);
  log += `<h2>Dernières alertes des indicateurs</h2>` + (alerts.length ? table(`<th>tick</th><th>règle</th><th>cible</th><th class="num">valeur</th><th class="num">seuil</th><th>ce que cela signifie</th>`,
    alerts.map(a => `<tr><td>${a.tick}</td><td>${esc(a.rule)}</td><td>${esc(a.target)}</td><td class="num">${a.value}</td><td class="num">${a.threshold ?? ""}</td><td>${esc(a.text)}</td></tr>`)) : `<p class="empty">aucune</p>`);
  put("metrics-log", log);
}

/** The six indicators of Krys in service, each from the archived measures (or the bench's, for the gate) with its proof.
    A figure that is not there says so; none is computed here that the measures do not hold. */
function renderGovernance() {
  const card = id => IMPACT?.cards?.find(c => c.id === id);
  const krys = (id, label = "Krys") => card(id)?.groups?.flatMap(g => g.rows).filter(r => r.label === label) ?? [];
  const proof = id => { const p = card(id)?.proofs?.[0]; return p && String(p.url).startsWith("/dashboard/console/impact/") ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">source</a>` : ""; };
  const tile = (value, name, under, id, extra = "") => `<div class="kpi"><b>${value ?? `<span class="missing">manquant</span>`}</b><span>${esc(name)}</span><small>${under}${extra} ${proof(id)}</small></div>`;
  const [fraud] = krys("fraud"), [contam] = krys("contamination"), [kept] = krys("utility"), [normal] = krys("normal"), detection = krys("detection");
  const [down] = krys("outage", "Krys sans relais"), [relay] = krys("outage", "Krys avec relais");
  const delays = detection.flatMap(r => r.delay_range || []), gate = known("improvements", "eval") ? D.I.metrics?.gate_latency_ms : null;
  put("impact", `<div class="kpis">`
    + tile(fraud ? eur(fraud.denominator - fraud.numerator) : null, "de fraude au virement bloquée", fraud ? `${eur(fraud.numerator)} volé sur ${eur(fraud.denominator)} tentés, sur les journées d'attaque mesurées.` : "", "fraud")
    + tile(contam ? `${num(contam.numerator)} sur ${num(contam.denominator)}` : null, "opérations contaminées exécutées", contam ? `${pct(contam.numerator / contam.denominator, 1)} des opérations bâties sur une donnée compromise.` : "", "contamination")
    + tile(kept ? pct(kept.numerator / kept.denominator, 1) : null, "de l'activité saine conservée sous attaque", normal ? `${pct(normal.numerator / normal.denominator)} les journées normales.` : "", "utility")
    + tile(delays.length ? `${Math.max(...delays)} ticks` : null, "au plus pour repérer une attaque", detection.length ? `Sur ${detection.length} scénarios ; la faille connue est repérée avant l'attaque.` : "", "detection")
    + tile(gate ? `${num(gate.p50, 1)} ms` : null, "pour décider au portique", gate ? `p99 ${num(gate.p99, 1)} ms, sur ${num(gate.n)} décisions du banc.` : "", "", gate ? sample("improvements", "eval") : "")
    + tile(relay ? pct(relay.numerator / relay.denominator) : null, "de l'activité saine tenue pendant une panne de Krys", relay && down ? `Grâce au relais ; ${pct(down.numerator / down.denominator)} sans lui.` : "", "outage")
    + `</div>`);
}


function renderImprove() {
  if (!known("improvements")) return put("improve", `<p class="empty">Aucun rapport de rejeux n'est publié pour cette banque : les seuils et les propositions viennent de <code>make metrics</code>. ${askButton("Quels seuils Krys applique-t-elle ?")}</p>`);
  const I = D.I, th = D.st.metrics?.thresholds || I.thresholds || {};
  let html = `<h2>Seuils en vigueur<span class="hint">tout changement est une proposition signée par un humain et journalisée</span></h2>` + table(`<th>seuil</th><th class="num">actuel</th><th>nouvelle valeur</th>`,
    Object.entries(th).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${v}</td><td><input type="number" step="any" id="th-${esc(k)}" value="${v}" aria-label="Nouvelle valeur de ${esc(k)}"> ${decision(`human set_threshold ${k}`, "Signer", true, `th-${k}`)}</td></tr>`));
  html += `<h2>Propositions pour un humain${sample("improvements")}</h2>`;
  for (const p of I.proposals) {
    const kind = {threshold: "seuil", gap: "trou de couverture", rule: "règle"}[p.kind] || p.kind;
    let evidence = "";
    if (p.evidence?.sweep) evidence += table(`<th class="num">valeur</th><th>détecte</th><th class="num">bonnes alertes</th><th class="num">fausses alertes</th><th class="num">ticks pour détecter, cumulés</th>`,
      p.evidence.sweep.map(r => `<tr${r.value === p.proposed ? ` style="color:var(--blue)"` : ""}><td class="num">${r.value}</td><td>${esc(r.caught.join(", ")) || "-"}</td><td class="num">${r.true_alerts}</td><td class="num">${r.false_alerts}</td><td class="num">${r.ticks_to_detect}</td></tr>`));
    for (const m of p.evidence?.missed || []) evidence += `<div style="margin:8px 0"><b>t${m.tick} ${esc(m.kind)} ${eur(m.amount_eur)} → ${esc(m.target)}</b>, alimenté par ${esc((m.compromised_sources || []).join(", "))}<br>` +
      m.chain.map(c => `<span class="chip">t${c.tick} ${esc(c.from)} · ${esc(c.type)}${c.fed_by_compromised.length ? " · alimenté par " + esc(c.fed_by_compromised.join(", ")) : ""}</span> ${esc(c.said)}`).join("<br>") + `</div>`;
    // the replay a proposal rests on plays in the Simulation, scenario chosen
    const scenario = Object.keys(p.replay?.by_scenario || {})[0] ?? p.evidence?.missed?.[0]?.scenario ?? (I.scenarios || []).find(g => g.scenario === p.target)?.scenario;
    const replay = scenario ? `<a class="replay" href="/simulation?scenario=${encodeURIComponent(scenario)}#simulation">Voir le rejeu</a>` : "";
    html += `<div class="card proposal"><h3><span><span class="badge">${esc(kind)}</span>${esc(p.target)}</span>` +
      `<span class="row-acts">${replay}${p.kind === "threshold" ? `${p.proven ? chip("green", "le rejeu confirme") : chip("red", `le rejeu infirme : garder ${p.current}`)} ${decision(p.apply, `Appliquer ${p.current} → ${p.proposed}`, !p.proven)}` : ""}</span>` +
      `</h3><div class="why">${esc(p.why)}</div>${evidence ? `<details><summary>preuves</summary>${evidence}</details>` : ""}</div>`;
  }
  if (!I.proposals.length) html += `<p class="empty">aucune proposition</p>`;
  put("improve", html);
}

/** What waits for a human: each problem with the decision that clears it and the question Krys answers about it. */
function renderSummary() {
  const st = D.st, rows = [];
  // a row read from the sample does not wait for anyone: it says so, and the Alertes view counts only the problems read live
  let waiting = 0;
  const real = (...keys) => live && !keys.some(k => D.fake.has(k));
  const ask = (text, command, label, question, counts = live, ghost = false) => {
    if (counts) waiting++;
    rows.push(`<tr><td>${text}${counts ? "" : SAMPLE}</td><td class="num"><span class="row-acts">${askButton(question)}${command ? decision(command, label, ghost) : ""}</span></td></tr>`);
  };
  if (bankLight() === "ROUGE") ask("La banque est au rouge : tous les agents sont en pause.", viaKrys() ? "krys resume *" : "human resume", "Reprendre", "Pourquoi la banque est-elle au rouge ?");
  const risk = D.S.agents?.risk?.status;
  if (risk?.halted) ask(`Risk a arrêté le trading (${esc(risk.halt_reason)}) : seul un humain le relance.`, "human resume_trading", "Reprendre le trading", "Pourquoi Risk a-t-il arrêté le trading ?");
  if (known("quarantined")) for (const a of st.quarantined || []) ask(`${esc(a)} est en quarantaine : sa clé est révoquée et ses outils coupés.`, `human unblock ${a}`, "Lever la quarantaine", `Pourquoi ${a} est-il en quarantaine ?`, real("quarantined"));
  const fromKrys = live && D.fake.has("isolated_services");
  // a service Krys isolated stays isolated until a human trusts it again; through Krys, no command lifts it yet: ask her
  for (const [s, f] of Object.entries(suspects())) ask(`${esc(s)} est suspect${f.since_tick != null ? ` depuis le tick ${esc(f.since_tick)}` : ""} : toute action qu'il a alimentée est refusée.`, viaKrys() ? null : `human unblock ${s}`, "Lui refaire confiance", `Pourquoi ${s} est-il suspect ?`, fromKrys || real("metrics", "isolated_services"), true);
  for (const asset of krysStatus()?.frozen || []) ask(`L'actif ${esc(asset)} est gelé : aucune opération ne le touche.`, `krys unfreeze ${asset}`, "Lever le gel", `Pourquoi ${asset} est-il gelé ?`);
  for (const [who, why] of Object.entries(D.S.agents?.compliance?.status?.blocked || {})) ask(`Compliance a bloqué ${esc(who)} (${esc(why)}) : seul un humain le débloque.`, `human unblock ${who}`, "Débloquer", `Pourquoi ${who} est-il bloqué ?`, live, true);
  const lost = agentIds().filter(id => D.S.agents[id].vigie_lost);
  if (lost.length) ask(`${esc(lost.join(", "))} ${lost.length === 1 ? "a perdu le battement de Krys et est passé" : "ont perdu le battement de Krys et sont passés"} seul${lost.length === 1 ? "" : "s"} à l'orange : vérifiez que Krys tourne.`, null, null, "Pourquoi les agents ont-ils perdu le battement de Krys ?");
  const lead = !live ? "Aucune simulation ne tourne : les lignes ci-dessous sont des exemples."
    : waiting ? `<b>${waiting} décision${waiting === 1 ? "" : "s"}</b> vous attend${waiting === 1 ? "" : "ent"}. Une décision s'envoie en deux temps, signée.` : "Rien n'attend de décision humaine.";
  put("summary", `<p class="lead">${lead}</p>` + (rows.length ? table(`<th>ce qui attend</th><th class="num">votre décision</th>`, rows) : ""));
  const count = $("waiting");
  count.hidden = !live;
  count.textContent = waiting;
  count.classList.toggle("none", !waiting);
  count.setAttribute("aria-label", `${waiting} décision${waiting === 1 ? "" : "s"} en attente`);
  D.waiting = waiting;
}

/** The Synthèse: where the bank stands, what changed since the day began, and what waits for a signature. */
function renderSynthese() {
  const st = D.st, light = bankLight(), [c, word] = STATE[light] || ["grey", light];
  const incidents = known("incidents") ? (st.incidents || []).filter(i => i.level !== "VERT") : OUTBOX ?? [], isolated = Object.keys(suspects());
  const refused = D.decisions.filter(d => !d.approved).length || (live ? D.S.decisions?.["execution:refused"] ?? 0 : 0);
  let html = `<p class="lead">${live ? `Tick <b>${S.tick}</b>${stale ? (S.tick >= 179 ? ", journée terminée" : ", en pause") : ""} · ` : ""}feu de la banque <b class="a-${c}">${esc(word)}</b>${live ? "" : " · données d'exemple, aucune simulation en cours"}</p>`;
  html += `<div class="counts">${count(live ? D.waiting : "-", "décisions en attente", "red")}${count(incidents.length, "incidents ouverts", "orange")}${count(isolated.length, "services isolés", "red")}${count(refused, "refus au portique", "red")}</div>`;
  // what changed: the last decisions Krys took on the bank, newest first
  const changes = (st.actions || []).filter(a => ["block", "quarantine", "slow"].includes(a.action)).slice(-5).reverse();
  // live, what Krys proposes to the humans: her finding, the action, who decides, and the cost of acting
  const props = krysStatus()?.propositions || [];
  if (props.length) html += `<h2>Ce que Krys propose</h2>` + props.map(p => `<div class="card proposal"><h3><span>${esc(p.decideur)}</span>${askButton(p.evidence?.length ? `Pourquoi ${p.evidence[0]} ?` : "Que propose Krys ?")}</h3>`
    + `<p>${esc(p.constat)}</p><p><b>Action :</b> ${esc(p.action)}</p>${p.cout_risque ? `<p class="why">Coût ou risque : ${esc(p.cout_risque)}</p>` : ""}</div>`).join("");
  if (!known("actions")) html += `<h2>Ce qui a changé</h2>` + (OUTBOX?.length ? `<ul class="changes">${OUTBOX.slice(0, 5).map(i => `<li><b>${esc(i.subject)}</b> · ${esc(i.period)}</li>`).join("")}</ul>` : unpublished("Qu'a fait Krys depuis le début de la journée ?"));
  else html += `<h2>Ce qui a changé${sample("actions")}</h2>` + (changes.length ? `<ul class="changes">${changes.map(a => `<li><span class="tick">tick ${a.tick}</span> Krys a ${{block: "bloqué", quarantine: "mis en quarantaine", slow: "ralenti"}[a.action]} <b>${esc(a.agent)}</b> : ${esc(a.text || a.reason)}</li>`).join("")}</ul>` : `<p class="empty">rien depuis le début de la journée</p>`);
  if (!known("improvements")) { put("synthese-body", html); return; }
  const bench = D.I.proposals || [], th = st.metrics?.thresholds || {};
  const good = bench.filter(p => p.kind === "threshold" && p.proven && th[p.target] !== p.proposed);
  html += `<h2>Bonnes nouvelles à appliquer${sample("improvements")}</h2>` + (good.length ? table(`<th>ce que les rejeux confirment</th><th class="num">votre décision</th>`,
    good.map(p => `<tr><td>${esc(p.target)} : ${esc(p.current)} → ${esc(p.proposed)}. ${esc(p.why)}</td><td class="num">${decision(p.apply, `Appliquer ${p.current} → ${p.proposed}`)}</td></tr>`)) : `<p class="empty">aucune</p>`);
  const toSign = bench.filter(p => p.kind === "threshold").length, open = bench.filter(p => p.kind !== "threshold");
  html += `<a class="callout" href="#improve"><b>${toSign} proposition${toSign === 1 ? "" : "s"} de seuils</b> attend${toSign === 1 ? "" : "ent"} une signature${open.length ? `, et ${open.length} règle${open.length === 1 ? "" : "s"} ou trou${open.length === 1 ? "" : "s"} de couverture à revoir` : ""} : les voir dans le Parc →</a>`;
  put("synthese-body", html);
}

/** The suggestions of the Krys view: three questions that always work, and those the bank's state asks now. */
function renderSuggestions() {
  const asks = ["Quelles sont les alertes en cours ?", "Qu'est-ce qui est bloqué en ce moment ?"];
  if (live && !D.fake.has("isolated_services")) for (const s of (D.st.isolated_services || []).slice(0, 1)) asks.push(`Pourquoi ${s} est-il isolé ?`);
  const refused = live ? D.decisions.filter(d => !d.approved).at(-1) : null;
  asks.push(refused ? `Pourquoi ${refused.request_id} ?` : "Pourquoi ce virement est-il refusé ?");
  asks.push("Comment Krys décide-t-elle ?");
  put("suggestions", asks.map(q => `<button type="button" data-question="${esc(q)}">${esc(q)}</button>`).join(""));
}

/** Every guardrail in force: each agent's limits as config.yaml sets them with how much of each is used now, KRYS's, the LLM's. */
const THRESHOLDS = {price_drift_z: "Mouvement de prix en un tick qui déclenche une alerte, en écarts-types", reconciliation_gap_pct: "Écart entre la trésorerie de Risk et le registre de référence qui déclenche une alerte, en %",
  herding_share: "Part des traders du même côté d'un actif en 3 ticks qui déclenche une alerte", breach_horizon_ticks: "Alerte quand une limite sera dépassée dans ce délai, en ticks"};
function renderGuardrails() {
  const root = $("guardrails"), make = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text != null) el.textContent = String(text);
    if (cls) el.className = cls;
    return el;
  };
  if (!GR) { root.replaceChildren(make("p", "Les réglages des garde-fous sont indisponibles. L'usage ne peut pas être évalué.", "gr-note")); root._guardrailsSignature = null; return; }
  const {trader: t, compliance: c, risk: r, payment: pay, quant: q} = GR.agents;
  const L = D.S.limits || {}, p = D.S.params || {};
  const numeric = value => typeof value === "number" && Number.isFinite(value);
  const money = value => numeric(value) ? (value / 1e6).toLocaleString("fr-FR", {minimumFractionDigits: 2, maximumFractionDigits: 8}) + " M€" : value ?? "Non fourni";
  const unit = (value, suffix) => value == null ? "Non fourni" : `${value} ${suffix}`;
  const ratio = value => numeric(value) ? pct(value) : value ?? "Non fourni";
  const list = value => Array.isArray(value) ? value.join(", ") || "Aucun" : value ?? "Non fourni";
  const flag = value => typeof value === "boolean" ? (value ? "Activé" : "Désactivé") : value ?? "Non signalé";
  const scale = D.S.agents?.risk?.status?.trader_scale ?? 1;
  const blocked = Object.keys(D.S.agents?.compliance?.status?.blocked || {});
  const groups = ["Trader", "Compliance", "Risk", "Payment", "Quant", "Krys", "LLM"].map(name => ({name, items: []}));
  const add = (group, title, value, why, usage, note = "") => groups.find(g => g.name === group).items.push({title, value, why, usage, note});
  const traderUsage = key => Object.entries(L.traders || {}).map(([label, values]) => ({label, value: values[key]}));
  const bankUsage = key => [{label: "Banque", value: L.bank?.[key]}];
  add("Trader", "Plus gros ordre", money(t.max_order_eur), "Plafonne la taille d'un seul ordre.", undefined,
    scale < 1 ? `Facteur de taille actuel de Risk : × ${scale} ; la VaR dépasse ${ratio(r.var_warning_ratio)} de sa limite.` : "");
  add("Trader", "Position par actif", money(t.max_position_eur), "Limite la concentration sur un actif.", traderUsage("max_position_pct"));
  add("Trader", "Arrêt sur perte du jour", money(t.max_daily_loss_eur), "Arrête ce trader jusqu'au lendemain dès que sa limite de perte est atteinte.", traderUsage("daily_loss_pct"));
  add("Trader", "Confiance minimale", t.min_confidence, "Ne négocie pas sous ce score de confiance.");
  add("Trader", "Délai entre deux ordres", unit(t.cooldown_ticks, "ticks"), "Attend entre deux ordres sur le même actif.");
  add("Compliance", "Actifs restreints", list(p.restricted_list ?? c.restricted_list), "Refuse tout ordre qui touche un actif de la liste.");
  add("Compliance", "Informations confidentielles", "Refusées", "Refuse un ordre fondé sur une information privilégiée.");
  add("Compliance", "Ordres fractionnés", `${c.structuring_max_orders} sous ${money(c.small_order_eur)} / ${c.structuring_window_ticks} ticks`, "Surveille les petits ordres répétés sur cette fenêtre.");
  add("Compliance", "Fenêtre d'aller-retour", unit(c.wash_window_ticks, "ticks"), "Surveille la revente de ce qui vient d'être acheté (wash trade).");
  add("Compliance", "Surveillance des annulations", `Plus de ${c.spoofing_max_cancels} / ${c.spoofing_window_ticks} ticks`, "Surveille les annulations répétées (spoofing possible).");
  add("Compliance", "Pays à haut risque", list(c.high_risk_countries), "Examine le dossier KYC du bénéficiaire ; seul un humain lève un blocage de paiement.", undefined,
    blocked.length ? `${blocked.length} blocage${blocked.length === 1 ? "" : "s"} de paiement signalé${blocked.length === 1 ? "" : "s"} actuellement.` : "");
  add("Risk", "Exposition brute", money(r.max_exposure_eur), "Plafonne l'exposition totale de la banque.", bankUsage("exposure_pct"));
  add("Risk", "VaR à un jour · 99 %", money(r.max_var_eur), `Au-delà de ${ratio(r.var_warning_ratio)} de cette limite, la taille des ordres des traders est multipliée par ${r.var_warning_scale}.`, bankUsage("var_pct"));
  add("Risk", "Arrêt sur perte du jour de la banque", money(r.max_daily_loss_eur), "Arrête le trading jusqu'à ce qu'un humain le relance.", bankUsage("daily_loss_pct"));
  add("Risk", "Plancher de trésorerie", money(r.min_cash_eur), "L'usage rapporte le plancher à la trésorerie disponible. À 100 %, il ne reste aucune marge.", bankUsage("liquidity_floor_pct"));
  add("Payment", "Plus gros virement", money(pay.max_transfer_eur), "Plafonne un virement avant son exécution.");
  add("Payment", "Virements du jour", money(pay.max_daily_eur), "Plafonne le montant viré dans la journée.", [{label: "Paiements", value: L.payments?.daily_pct}]);
  add("Payment", "Verrou après changement d'IBAN", unit(pay.iban_change_lock_ticks, "ticks"), "Refuse les paiements peu après un changement du compte destinataire.");
  add("Quant", "Âge maximal des données", unit(q.max_data_age_ticks, "ticks"), "Ne valorise pas de données de marché plus anciennes.");
  add("Quant", "Saut de prix en un tick", unit(q.jump_threshold_pct, "%"), `Abaisse la confiance à ${q.low_confidence}, sous le seuil des traders (${t.min_confidence}), quand ce saut est dépassé.`);
  add("Krys", "Surveillance par le portique", flag(D.S.vigie?.enabled ?? GR.vigie.enabled), "Surveille les messages et approuve ordres et paiements avant leur exécution.");
  add("Krys", "Attente d'approbation", unit(GR.gate.timeout_s, "secondes"), `Une demande sans réponse est ${GR.gate.default_on_timeout === "reject" ? "refusée" : "approuvée"} après cette attente.`, undefined,
    D.S.vigie?.gate == null ? "" : `Portique actuel : ${D.S.vigie.gate}`);
  const actors = Object.values(D.S.agents || {}), lost = actors.filter(a => a.vigie_lost).length;
  add("Krys", "Battement manquant", unit(GR.vigie.heartbeat_timeout_ticks, "ticks"), "Un agent passe seul à l'orange après ce délai sans battement de Krys.", undefined,
    actors.length ? `${lost} agent${lost === 1 ? "" : "s"} sur ${actors.length} signale${lost === 1 ? "" : "nt"} un battement perdu.` : "Aucun état de battement fourni.");
  const thresholdTitles = {price_drift_z: "Alerte de mouvement de prix", reconciliation_gap_pct: "Alerte d'écart de trésorerie", herding_share: "Traders du même côté", breach_horizon_ticks: "Horizon d'alerte des limites"};
  const thresholdUnits = {price_drift_z: "écarts-types", reconciliation_gap_pct: "%", herding_share: "part", breach_horizon_ticks: "ticks"};
  for (const [key, value] of Object.entries(D.st.metrics?.thresholds || {})) {
    add("Krys", thresholdTitles[key] || key, thresholdUnits[key] ? unit(value, thresholdUnits[key]) : value,
      THRESHOLDS[key] || "Un seuil de surveillance configuré ; le changer exige une décision humaine signée.");
  }
  add("LLM", "Agents qui raisonnent", flag(GR.llm.enabled), "Indique si les agents raisonnent avec un LLM.");
  const promptGuard = p.guardrails ?? GR.llm.guardrails;
  add("LLM", "Protection des prompts", promptGuard === "on" ? "Activée" : promptGuard === "off" ? "Désactivée" : promptGuard, "Retire les phrases d'instruction des textes externes et ajoute un rappel système.");
  add("LLM", "Auditeur du raisonnement", flag(D.st.auditor_llm), "Peut augmenter le soupçon après avoir relu le raisonnement ; il n'approuve jamais une opération.");
  // Garder les nœuds lors d'un rafraîchissement identique évite de perdre le
  // point de lecture ; aucune consommation n'est déduite d'un réglage seul.
  const sampleData = D.fake.has("all"), signature = JSON.stringify([groups, sampleData, GR.vigie.orange_amount_factor]);
  if (root._guardrailsSignature === signature) return;
  root._guardrailsSignature = signature;
  const state = value => value >= 100 ? "red" : value >= 80 ? "orange" : "normal";
  const meter = (reading, title) => {
    const line = make("div", null, "gr-reading"), status = state(reading.value);
    if (!numeric(reading.value)) {
      line.append(make("p", `${reading.label} : ${reading.value == null ? "Usage non fourni" : "Lecture inutilisable : " + reading.value}`, "gr-note"));
      return line;
    }
    const text = `${reading.value}%`, bounded = Math.min(100, Math.max(0, reading.value));
    line.append(make("p", `${reading.label} · ${text} de la limite`, "gr-usage-text"));
    const gauge = make("div", null, `gr-meter gr-${status}`);
    gauge.setAttribute("role", "meter"); gauge.setAttribute("aria-label", `${title} — ${reading.label}`);
    gauge.setAttribute("aria-valuemin", "0"); gauge.setAttribute("aria-valuemax", "100");
    gauge.setAttribute("aria-valuenow", bounded); gauge.setAttribute("aria-valuetext", `${text} de la limite`);
    const fill = make("span", null, "gr-fill"); fill.style.width = `${bounded}%`; gauge.append(fill);
    line.append(gauge, make("span", status === "red" ? "À la limite ou au-delà" : status === "orange" ? "Près de la limite" : "Dans la limite", `gr-state gr-${status}`));
    return line;
  };
  const readings = groups.flatMap(group => group.items.flatMap(item => (item.usage || []).filter(reading => numeric(reading.value)).map(reading => ({group, item, reading}))));
  const near = readings.filter(({reading}) => reading.value >= 80).sort((a, b) => b.reading.value - a.reading.value);
  const summary = make("div", null, "gr-attention"); summary.id = "gr-near";
  summary.append(make("h2", "Ce qui approche d'une limite"));
  if (near.length) {
    const items = make("ul", null, "gr-near-list");
    for (const {group, item, reading} of near) {
      const li = make("li"); li.append(make("h3", `${group.name} · ${item.title}`), meter(reading, item.title)); items.append(li);
    }
    summary.append(items);
  } else summary.append(make("p", readings.length ? "Aucune limite proche de son seuil" : "Aucune lecture d'usage disponible", "gr-clear"));
  summary.append(make("p", "Les lectures à 80 % ou plus sont en orange ; à 100 % ou plus, en rouge. Un usage manquant n'est pas un zéro.", "gr-note"));
  root.replaceChildren(summary);
  root.append(make("p", "Chaque agent contrôle ses propres limites ; Krys contrôle les agents. Les réglages viennent de /guardrails, l'usage de la vue de la banque.", "gr-intro"));
  if (sampleData) root.append(make("p", "Lectures d'exemple de la banque, pas des données en direct", "gr-source"));
  root.append(make("p", `À l'orange, les limites d'ordres et de paiements sont multipliées par ${GR.vigie.orange_amount_factor}. Les réglages ci-dessous restent inchangés.`, "gr-note"));
  const grid = make("div", null, "gr-grid");
  for (const group of groups) {
    const card = make("article", null, "gr-actor"); card.append(make("h2", group.name));
    const settings = make("dl", null, "gr-settings");
    for (const item of group.items) {
      const dt = make("dt", item.title), dd = make("dd");
      dd.append(make("p", item.value ?? "Non fourni", "gr-setting"));
      if (item.usage) {
        for (const reading of item.usage) dd.append(meter(reading, item.title));
        if (!item.usage.length) dd.append(make("p", "Usage non fourni", "gr-note"));
      }
      if (item.note) dd.append(make("p", item.note, "gr-note"));
      dd.append(make("p", "Pourquoi : " + item.why, "gr-why")); settings.append(dt, dd);
    }
    card.append(settings); grid.append(card);
  }
  root.append(grid);
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
// the second click of a decision sends it; the button keeps the focus across the redraw it causes
document.addEventListener("click", e => {
  const d = e.target.closest("[data-decide]");
  if (d) {
    const command = d.dataset.decide + (d.dataset.input ? " " + ($(d.dataset.input)?.value ?? "") : "");
    if (command.startsWith("krys ")) { krysCommand(command); return; }
    if (armed?.command === d.dataset.decide && Date.now() < armed.until) { armed = null; human(command); }
    else { armed = {command: d.dataset.decide, until: Date.now() + 5000}; setTimeout(render, 5100); }
    drawn.detail = ""; render();
    document.querySelector(`[data-decide="${CSS.escape(d.dataset.decide)}"]`)?.focus();
    return;
  }
  const q = e.target.closest("[data-ask]");
  if (q) askKrys(q.dataset.ask);
});
// A command through Krys: the first click prepares it and shows Krys's recap, the second confirms it within its validity.
// Krys refuses it when the demo was launched without human commands; the refusal is shown as it is.
async function krysCommand(command) {
  const [, action, target] = command.split(" ");
  const post = (path, body) => fetch("/demo/krys/actions/" + path, {method: "POST", credentials: "same-origin", redirect: "error",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)}).then(async r => ({ok: r.ok, body: await r.json().catch(() => ({}))}), () => ({ok: false, body: {}}));
  if (prepared?.command === command && Date.now() < prepared.until) {
    const r = await post("confirm", {action_id: prepared.id, confirmation: "confirmer"});
    prepared = null; armed = null;
    toast(r.ok ? `Publié, signé par un humain : ${r.body.message_id ?? command}` : `Refusé : ${r.body.detail ?? "Krys injoignable"}`);
  } else {
    const r = await post("prepare", {action, target});
    if (!r.ok) { prepared = null; armed = null; toast(`Refusé : ${r.body.detail ?? "Krys injoignable"}`); }
    else { prepared = {command, id: r.body.action_id, until: Date.now() + 110000}; armed = {command, until: prepared.until}; toast(r.body.recap); }
  }
  render();
}
// a question to Krys: in the Krys view if it is open, otherwise in its bubble (mails-chat.js)
function askKrys(question) {
  const field = $("krys-question");
  field.value = question;
  if (!$("krys").hidden || $("mc-chat")?.hidden === false) field.focus();
  else $("mc-open")?.click();
}
async function human(command) {
  if (!live || stale) return toast("Aucune simulation ne tourne : lancez-en une pour envoyer une décision");
  const r = await fetch("/inject", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({command})}).catch(() => null);
  const body = await r?.json().catch(() => ({}));
  toast(r?.ok ? `Envoyé, signé par un humain : ${command}` : `Refusé : ${body?.detail || r?.status || "API injoignable"}`);
}

// --- the page: its views, its polling ----------------------------------------------------------------------------

// the five views of the sidebar; a section of main that no view names (the outbox) stays hidden
const tabs = [...document.querySelectorAll("[data-tab]")];
function go(id) {
  for (const t of tabs) t.toggleAttribute("aria-current", t.dataset.tab === id);
  for (const s of document.querySelectorAll("main > section")) s.hidden = s.id !== id;
}
for (const t of tabs) t.onclick = e => { e.preventDefault(); go(t.dataset.tab); history.replaceState(null, "", location.pathname + location.search + "#" + t.dataset.tab); };
// up and down move along the views, Enter opens the one in focus
document.querySelector(".views").addEventListener("keydown", e => {
  const i = tabs.indexOf(document.activeElement);
  if (i < 0 || !["ArrowDown", "ArrowUp"].includes(e.key)) return;
  e.preventDefault();
  tabs[(i + (e.key === "ArrowDown" ? 1 : tabs.length - 1)) % tabs.length].focus();
});
// a view's anchor opens it; a former tab's opens the view that took its content, scrolled to its block (anchor() is the
// head script's); ?embed=<view> names it without an anchor, as an anchor in a frame scrolls the page around it
const fromHash = () => {
  const {view, block} = anchor(location.hash.slice(1) || new URLSearchParams(location.search).get("embed") || "");
  go(tabs.some(t => t.dataset.tab === view) ? view : "krys");
  if (!block) return;
  history.replaceState(null, "", location.pathname + location.search + "#" + view);
  pendingBlock = block;
  if (DEMO) render();
};
fromHash();
addEventListener("hashchange", fromHash);

// --- the helper: /console/static/helper.js, shared with the Simulation --------------------------------------------

(async () => {
  DEMO = await getJson("/dashboard/dashboard-demo.json");
  // without the console's own files the server is not answering: every block says so, none keeps loading
  if (!DEMO) {
    for (const el of document.querySelectorAll(".loading")) el.textContent = "Le serveur d'Argus ne répond pas : rechargez la page une fois la démo lancée.";
    return;
  }
  await Promise.all([pollHealth(), readImpact()]);
  await Promise.all([pollGraph(), pollImprovements(), pollGuardrails()]);
  await poll();
  setInterval(poll, 1000);
  setInterval(pollGraph, 5000);
  setInterval(pollHealth, 5000);
  pollOutbox(); setInterval(pollOutbox, 10000);
  setInterval(pollImprovements, 30000);
})();
