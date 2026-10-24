"use strict";
// Moved from dashboard.js when the Simulation got its own page: the console and the Simulation share it.
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
const OPENS = {agent: "Ouvre ses contrôles et ce que Krys lui a fait", role: "Ouvre les contrôles de chacun de ses agents", service: "Ouvre ses preuves et qui le lit", metric: "Ouvre ses chiffres"};
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
  return svg ? {name: "Lien", what: svg.textContent} : null;
}
(() => {
  if (!matchMedia("(hover: hover)").matches) return;
  const SIZE = 22, GAP = 12, EDGE = 8, el = document.createElement("div");
  // the box escapes what it says itself: the console's esc stays in dashboard.js, the Simulation's in its module
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
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
    if (help) body.innerHTML = blockOf(ICON.what, help.name, help.what) + (help.click ? blockOf(ICON.click, "Clic", help.click) : "") + (details ? blockOf(ICON.details, "Détails", details, true) : "");
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
