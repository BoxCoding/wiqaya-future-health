const $ = (s, root = document) => root.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const pct = (x, d = 1) => (x == null ? "—" : `${(x * 100).toFixed(d)}%`);
const fmt = (x) => (x == null ? "<10" : Number(x).toLocaleString("en-US"));
const say = (msg) => { $("#status").textContent = msg; };

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${r.status}: ${(await r.json().catch(() => ({}))).detail ?? r.statusText}`);
  return r.json();
}

const PATHS = {
  home: '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  plan: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  person: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/>',
  shield: '<path d="M12 3l8 3v6c0 5-3.4 8.3-8 9.5C7.4 20.3 4 17 4 12V6z"/><path d="M9 12l2 2 4-4"/>',
  chart: '<path d="M3 21h18M6 17v-6M11 17V5M16 17v-9M21 17v-3"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  alert: '<path d="M12 3.5l9.5 16.5h-19z"/><path d="M12 10v4.5M12 17.5v.01"/>',
  up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  msg: '<path d="M4 5h16v11H9l-5 4z"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  route: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.5 6H15a3 3 0 0 1 0 6H9a3 3 0 0 0 0 6h6.5"/>',
  tool: '<path d="M14.5 6.5a4 4 0 0 0-5.3 5.3L4 17l3 3 5.2-5.2a4 4 0 0 0 5.3-5.3l-2.5 2.5-2.5-.5-.5-2.5z"/>',
  pen: '<path d="M4 20l4-1 11-11-3-3L5 16z"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
};
const icon = (name) => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${PATHS[name]}</svg>`;

const VIEWS = {
  overview: ["Command centre", "home", "Command centre", ""],
  plan: ["Weekly plan", "plan", "Weekly outreach plan", "Whole households, ranked by the early findings an invitation is expected to cause, filling this week's real clinic slots. Population prioritisation: the clinician decides."],
  copilot: ["Ask Wiqaya", "spark", "Ask Wiqaya: multi-agent copilot", "Ask in plain language. A supervisor routes the question to specialist agents: the model proposes, deterministic tools execute, and a safety agent checks every draft before you see it."],
  person: ["Person", "person", "Person and household", "Care gaps from rules, risk from the model with its uncertainty, the data behind each flag, and the exact messages the household will receive."],
  safeguards: ["Safeguards", "shield", "Population safeguards", "Reach and calibration across segments of the local population, with a release gate and small-cell suppression."],
  outcomes: ["Outcomes", "chart", "Outcomes and evaluation", "The same clinic capacity under two policies, scored against the synthetic ground truth."],
};

const SHORT = { "Well-Child Visit + developmental assessment": "WCV + developmental", "Genetic counselling, then enhanced surveillance": "Genetic counselling" };
const short = (p) => { const s = SHORT[p] ?? p.replace("Adult screening: ", ""); return s[0].toUpperCase() + s.slice(1); };
const age = (a) => (a < 2 ? `${Math.floor(a * 12)} mo` : `${Math.floor(a)} y`);
const kind = (p) => (p.startsWith("Genetic") ? "gen" : p.startsWith("Well-Child") ? "child" : "adult");
const chip = (p) => `<span class="chip ${kind(p)}"><i></i>${esc(short(p))}</span>`;
const avatar = (m, lg = "") => `<span class="av ${m.age < 18 ? "child" : ""} ${lg}" aria-hidden="true">${esc(m.name[0])}</span>`;
const LANG = { ar: "Arabic", en: "English" };
const CHIP_LEGEND = `<div class="legend"><span><span class="sw" style="background:var(--s3)"></span>Child pathway</span><span><span class="sw" style="background:var(--s1)"></span>Adult screening</span><span><span class="sw" style="background:var(--s7)"></span>Genomic</span></div>`;

let overview, outcomes, regions, currentPerson, dimension = "nationality";

function showTab(id) {
  const [, , title, sub] = VIEWS[id];
  document.querySelectorAll('[role="tab"]').forEach((t) => t.setAttribute("aria-selected", String(t.getAttribute("aria-controls") === id)));
  $(`#tab-${id}`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  document.querySelectorAll('[role="tabpanel"]').forEach((p) => (p.hidden = p.id !== id));
  $("#page-title").textContent = title;
  $("#page-sub").textContent = sub;
  $(".top-tools .field").hidden = id !== "plan";
  $(".top").hidden = ["overview", "copilot", "person"].includes(id) && !(id === "person" && !currentPerson);
  $("#kpis").hidden = ["overview", "plan", "safeguards", "outcomes", "copilot", "person"].includes(id);
  const panel = $(`#${id}`);
  panel.classList.remove("enter"); void panel.offsetWidth; panel.classList.add("enter");
  countUp(panel);
  if (id === "safeguards") loadSafeguards();
  if (id === "outcomes") renderOutcomes();
}

async function init() {
  $("#nav").innerHTML = Object.entries(VIEWS).map(([id, [label, ic]], i) =>
    `<button role="tab" id="tab-${id}" aria-controls="${id}" aria-selected="${i === 0}" data-view="${id}">${icon(ic)}${label}</button>`).join("");
  renderCopilot();
  $("#person").innerHTML = `<div class="card empty">${icon("person")}<p>Pick anyone from the weekly plan to see why they were invited.</p><button class="btn" data-open-top>Open the top-ranked household</button></div>`;
  [overview, outcomes, regions] = await Promise.all([api("/api/overview"), api("/api/outcomes"), api("/api/safeguards?dimension=region")]);
  $("#week-pill").innerHTML = `${icon("clock")}Week of ${new Date(overview.week_start).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}`;
  renderKpis();
  $("#centre").innerHTML = overview.centres.map((c) => `<option>${esc(c)}</option>`).join("");
  $("#centre").addEventListener("change", () => loadPlan($("#centre").value));
  renderOverview();
  showTab("overview");
  await loadPlan(overview.centres[0]);
  api("/api/agent/status").then(renderModel).catch(() => renderModel({ online: false }));
}

function renderKpis() {
  const [w, c] = Object.values(outcomes.policies);
  const d = w.early_findings_caused / c.early_findings_caused - 1;
  const tiles = [
    ["Residents modelled", fmt(overview.people), `${fmt(overview.households)} households · synthetic`],
    ["With a care gap", fmt(overview.due), `${pct(overview.due / overview.people, 0)} of residents are overdue`],
    ["Invitations this week", fmt(outcomes.slots_per_centre * outcomes.centres), `${outcomes.slots_per_centre} slots × ${outcomes.centres} centres`],
    ["Early findings vs calendar rule", `${d >= 0 ? "+" : ""}${(d * 100).toFixed(1)}%`, `${w.early_findings_caused} vs ${c.early_findings_caused} per week, same slots`, "hero"],
  ];
  $("#kpis").innerHTML = tiles.map(([k, v, s, cls = ""]) => `<div class="kpi lift ${cls}"><span class="kpi-k">${k}</span><span class="kpi-v">${v}</span><span class="kpi-s">${s}</span></div>`).join("");
}

/* Numbers count up from zero when a view opens; reduced-motion viewers see the final value at once. */
function countUp(root) {
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  root.querySelectorAll("[data-count]").forEach((el) => {
    const target = Number(el.dataset.count), dp = Number(el.dataset.dp || 0), pre = el.dataset.pre || "", suf = el.dataset.suf || "";
    const show = (v) => (el.textContent = `${pre}${v.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp })}${suf}`);
    if (still) return show(target);
    const t0 = performance.now();
    const step = (t) => { const k = Math.min((t - t0) / 900, 1); show(target * (1 - (1 - k) ** 3)); if (k < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  });
}
const counter = (v, { dp = 0, pre = "", suf = "" } = {}) =>
  `<span data-count="${v}" data-dp="${dp}" data-pre="${esc(pre)}" data-suf="${esc(suf)}">${pre}${Number(v).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp })}${suf}</span>`;

/* ---------- Command centre ---------- */
// Schematic positions only (not to scale): Al Dhafra west, Abu Dhabi City on the coast, Al Ain inland east.
const REGION_XY = { "Al Dhafra": [190, 222], "Abu Dhabi City": [372, 104], "Al Ain": [520, 222] };
function regionMap() {
  const rows = regions.rows.filter((r) => !r.suppressed);
  const maxDue = Math.max(...rows.map((r) => r.due));
  return `<svg class="chart map" viewBox="0 0 640 360" role="img" aria-label="Schematic map of Abu Dhabi emirate: people due and reach by region">
    <defs><linearGradient id="land" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="var(--brand-soft)"/><stop offset="1" stop-color="var(--surface-2)"/></linearGradient></defs>
    <path class="land" d="M24 168 L70 150 L118 146 L170 140 L222 128 L270 118 L318 104 L350 86 L392 74 L438 64 L478 58 L500 82 L522 120 L560 160 L590 214 L600 262 L566 300 L500 318 L420 330 L320 338 L220 340 L130 334 L64 314 L30 270 Z"/>
    <text x="40" y="132" class="sea">Arabian Gulf</text>
    ${rows.map((r) => {
      const [x, y] = REGION_XY[r.segment] ?? [320, 220], rad = 20 + 46 * Math.sqrt(r.due / maxDue), ok = r.gate === "pass";
      return `<g class="region" tabindex="0" data-tip="${esc(r.segment)}: ${fmt(r.due)} due, ${fmt(r.contacted)} invited this week, reach ${pct(r.reach)} (${ok ? "passes" : "below"} the gate)">
        <circle cx="${x}" cy="${y}" r="${rad + 10}" class="halo ${ok ? "" : "warn"}"/><circle cx="${x}" cy="${y}" r="${rad}" class="bubble ${ok ? "" : "warn"}"/>
        <text x="${x}" y="${y - 2}" text-anchor="middle" class="lbl">${esc(r.segment)}</text>
        <text x="${x}" y="${y + 15}" text-anchor="middle" class="sub">${fmt(r.due)} due</text></g>`;
    }).join("")}
    <text x="620" y="352" text-anchor="end" class="sea">Schematic, not to scale</text></svg>`;
}

function renderOverview() {
  const [w, c] = Object.values(outcomes.policies);
  const d = (w.early_findings_caused / c.early_findings_caused - 1) * 100;
  const rows = regions.rows.filter((r) => !r.suppressed);
  const feats = [
    ["spark", "Ask in plain language", "Six agents route, plan, query and verify. Nothing reaches you unchecked.", "copilot"],
    ["plan", "Benefit-ranked invitations", "Real clinic slots go to the households an invitation actually moves.", "plan"],
    ["chart", "Bias-corrected risk", "Never-screened families are not scored down for missing data.", "outcomes"],
    ["shield", "Fairness gates", "Every segment is checked for reach; small cells stay hidden.", "safeguards"],
  ];
  $("#overview").innerHTML = `
    <section class="hero">
      <div class="hero-copy">
        <p class="hero-eyebrow">Future Health Challenge · Population Health · Abu Dhabi</p>
        <h1 class="hero-title">Prevention that reaches the right family, <span>first.</span></h1>
        <p class="hero-sub">Wiqaya finds children and adults with unmet preventive care, invites the households an invitation will actually help, and explains every decision, in Arabic and English.</p>
        <div class="hero-cta"><button class="btn glow" data-view="copilot">${icon("spark")}Ask Wiqaya</button><button class="btn ghost" data-view="plan">${icon("plan")}This week's plan</button></div>
        <div class="hero-chips"><span>${icon("clock")}Week of 26 Oct 2026</span><span id="hero-model">${icon("spark")}AI agents</span><span>${icon("lock")}Synthetic data only</span></div>
      </div>
      <div class="hero-panel">
        <p class="hero-panel-k">Live this week</p>
        <div class="hero-stats">
          <div><b>${counter(overview.due)}</b><span>residents with a care gap</span></div>
          <div><b>${counter(outcomes.slots_per_centre * outcomes.centres)}</b><span>invitations across ${outcomes.centres} centres</span></div>
          <div><b>${counter(w.completions)}</b><span>expected completed visits</span></div>
          <div class="accent"><b>${counter(d, { dp: 1, pre: "+", suf: "%" })}</b><span>early findings vs calendar rule</span></div>
        </div>
      </div>
    </section>
    <div class="cols-wide">
      <div class="card"><div class="card-head"><div><h2>Where care is due</h2><p class="card-sub">Bubble size = people with a care gap · colour = reach gate</p></div>
        <div class="legend"><span><span class="sw" style="background:var(--brand)"></span>Passes gate</span><span><span class="sw" style="background:var(--warn)"></span>Below gate</span></div></div>
        ${regionMap()}</div>
      <div class="stack">
        ${rows.map((r) => `<div class="card region-card lift"><div class="card-head" style="margin:0"><div><h2>${esc(r.segment)}</h2><p class="card-sub">${fmt(r.n)} residents</p></div>
          ${r.gate === "pass" ? `<span class="status good">${icon("check")}Pass</span>` : `<span class="status warn">${icon("alert")}Review</span>`}</div>
          <div class="region-nums"><div><b>${counter(r.due)}</b><span>due</span></div><div><b>${counter(r.contacted)}</b><span>invited</span></div><div><b>${counter(r.reach * 100, { dp: 1, suf: "%" })}</b><span>reach</span></div></div></div>`).join("")}
      </div>
    </div>
    <div class="ai-spot card">
      <div><p class="hero-eyebrow">Multi-agent AI copilot</p><h2>The model proposes. Code verifies.</h2>
        <p class="card-sub" style="max-width:52ch">A supervisor routes each question to specialist agents. Every figure comes from a verified tool result, and the safety agent blocks invented numbers, names, places and claims.</p>
        <button class="btn" style="margin-top:14px" data-ask="Are any groups being left behind this week?">${icon("spark")}Try: "Are any groups being left behind?"</button></div>
      <ol class="pipe" aria-label="Agent pipeline">${["Supervisor", "Query planner", "Verifier", "Analyst tool", "Narrator", "Safety & grounding"].map((a) => `<li><span class="lane-ic">${icon(AGENT_ICON[a])}</span><small>${a}</small></li>`).join("")}<i class="pulse-dot" aria-hidden="true"></i></ol>
    </div>
    <div class="feats">${feats.map(([ic, h, t, v]) => `<button class="feat lift" data-view="${v}"><span class="feat-ic">${icon(ic)}</span><strong>${h}</strong><span>${t}</span></button>`).join("")}</div>`;
}

/* ---------- Weekly plan ---------- */
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"], HOURS = ["08", "09", "10", "11", "12", "13", "14", "15"];
const CAPACITY = DAYS.length * (HOURS.length - 1) * 6;  // 3 slots an hour x 2 rooms, midday hour kept free (mirrors outreach.py)
let planFilter = { kind: "all", lang: "all" };

function weekHeatmap(members) {
  const n = {};
  members.forEach((m) => { const k = `${m.slot.slice(0, 3)} ${m.slot.slice(-5, -3)}`; n[k] = (n[k] ?? 0) + 1; });
  const max = Math.max(...Object.values(n), 1);
  const date = Object.fromEntries(members.map((m) => [m.slot.slice(0, 3), m.slot.slice(4, 10)]));
  return `<div class="heat" role="table" aria-label="Booked slots by day and hour">
    <div class="heat-row heat-axis" role="row"><span></span>${HOURS.map((h) => `<span role="columnheader">${h}:00</span>`).join("")}</div>
    ${DAYS.map((d) => `<div class="heat-row" role="row"><span class="heat-day" role="rowheader">${d}<small>${esc(date[d] ?? "")}</small></span>${HOURS.map((h) => {
      if (h === "12") return `<span class="cell blocked" role="cell" data-tip="${d} 12:00: midday prayer window, no invitations">${icon("clock")}</span>`;
      const v = n[`${d} ${h}`] ?? 0;
      return v ? `<span class="cell" role="cell" style="--v:${Math.round((v / max) * 100)}%" data-tip="${d} ${h}:00: ${v} invited">${v}</span>`
        : `<span class="cell open" role="cell" data-tip="${d} ${h}:00: open for recalls and walk-ins">+</span>`;
    }).join("")}</div>`).join("")}</div>`;
}

function householdKinds(h) { return [...new Set(h.members.flatMap((m) => m.pathways.map(kind)))]; }

function applyPlanFilter() {
  let shown = 0;
  document.querySelectorAll("#plan [data-kinds]").forEach((el) => {
    const ok = (planFilter.kind === "all" || el.dataset.kinds.split(" ").includes(planFilter.kind)) && (planFilter.lang === "all" || el.dataset.lang === planFilter.lang);
    el.hidden = !ok; shown += ok;
  });
  document.querySelectorAll("#plan [data-plan-kind]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.planKind === planFilter.kind)));
  document.querySelectorAll("#plan [data-plan-lang]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.planLang === planFilter.lang)));
  $("#plan-count").textContent = `Showing ${shown} household${shown === 1 ? "" : "s"}`;
}

function memberRow(m) {
  return `<li class="member">${avatar(m)}<div>
    <div class="who"><button class="link" data-person="${m.id}">${esc(m.name)}</button>
    <span class="muted small">${m.age < 18 ? "child" : esc(m.role)} · ${age(m.age)}</span>
    <span class="slot">${icon("clock")}${esc(m.slot)}</span></div>
    <div>${m.pathways.map(chip).join("")}</div></div></li>`;
}

async function loadPlan(centre) {
  const p = await api(`/api/plan/${encodeURIComponent(centre)}`);
  const members = p.households.flatMap((h) => h.members);
  const top = Math.max(...p.households.map((h) => h.score), 1e-9);
  const children = members.filter((m) => m.age < 18).length;
  const ar = p.households.filter((h) => h.language === "ar").length;
  const C = 2 * Math.PI * 44, fill = p.used / p.slots;
  const medals = ["gold", "silver", "bronze"];
  const attrs = (h) => `data-kinds="${householdKinds(h).join(" ")}" data-lang="${h.language}"`;
  $("#plan").innerHTML = `
    <section class="plan-hero">
      <div class="cap-ring" role="img" aria-label="${p.used} of ${p.slots} slots filled">
        <svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="44" class="ring-bg"/><circle cx="50" cy="50" r="44" class="ring-fg" style="stroke-dasharray:${C};stroke-dashoffset:${C * (1 - fill)}"/></svg>
        <div><b>${counter(p.used)}</b><span>of ${p.slots} slots</span></div></div>
      <div class="plan-hero-copy">
        <p class="hero-eyebrow">${icon("spark")} Ranked by expected benefit per invitation</p>
        <h2>${esc(centre)}</h2>
        <p class="plan-hero-sub">Week of 26 Oct · whole households booked back-to-back · midday prayer window kept free</p>
        <div class="plan-stats">
          <div><b>${counter(p.households.length)}</b><span>households</span></div>
          <div><b>${counter(children)}</b><span>children</span></div>
          <div><b>${counter(members.length - children)}</b><span>adults</span></div>
          <div><b>${counter(ar)}</b><span>Arabic</span></div>
          <div><b>${counter(p.households.length - ar)}</b><span>English</span></div>
        </div>
      </div>
    </section>
    <div class="plan-top">
      <div class="card"><div class="card-head"><div><h2>This week at a glance</h2><p class="card-sub">Invitations by day and hour · darker = busier</p></div>
        <span class="status good">${icon("check")}${CAPACITY - p.used} open slots for recalls and walk-ins</span></div>${weekHeatmap(members)}</div>
      <div class="card"><div class="card-head"><div><h2>Care gaps across Abu Dhabi</h2><p class="card-sub">People overdue, all centres</p></div></div>
        <div class="hbars">${overview.by_pathway.map(([name, n]) => `
          <div class="hbar" data-tip="${esc(name)}: ${fmt(n)} people"><span>${chip(name)}</span><b>${fmt(n)}</b><div class="bar"><i class="k-${kind(name)}" style="width:${(n / overview.by_pathway[0][1]) * 100}%"></i></div></div>`).join("")}</div></div>
    </div>
    <div class="plan-toolbar">
      <div class="segs" role="group" aria-label="Filter by pathway">${[["all", "All"], ["child", "Children"], ["adult", "Adult screening"], ["gen", "Genomic"]].map(([k, l]) => `<button data-plan-kind="${k}" aria-pressed="false">${l}</button>`).join("")}</div>
      <div class="segs" role="group" aria-label="Filter by language">${[["all", "All languages"], ["ar", "Arabic"], ["en", "English"]].map(([k, l]) => `<button data-plan-lang="${k}" aria-pressed="false">${l}</button>`).join("")}</div>
      <span id="plan-count" class="muted small" role="status"></span>
    </div>
    <div class="podium">${p.households.slice(0, 3).map((h, i) => `
      <article class="pod lift ${medals[i]}" ${attrs(h)}>
        <div class="pod-head"><span class="medal">${i + 1}</span><div><strong>Household H${h.household}</strong><span class="badge">${h.language.toUpperCase()}</span></div>
          <div class="pod-score"><b>${(h.score * 100).toFixed(1)}</b><span>per 100 invites</span></div></div>
        <ul class="members">${h.members.map(memberRow).join("")}</ul>
      </article>`).join("")}</div>
    <div class="card"><div class="card-head"><div><h2>All ranked households</h2><p class="card-sub">Benefit = expected early findings per 100 invitations</p></div>${CHIP_LEGEND}</div>
      <div class="hh-list">${p.households.slice(3).map((h, i) => `
        <article class="hh" ${attrs(h)}>
          <span class="rank">${i + 4}</span>
          <div><div class="hh-head"><strong>Household H${h.household}</strong><span class="badge" title="${LANG[h.language]}">${h.language.toUpperCase()}</span></div>
            <ul class="members">${h.members.map(memberRow).join("")}</ul></div>
          <div class="score" data-tip="Average across this household's invited members"><b>${(h.score * 100).toFixed(1)}</b>
            <div class="meter"><i style="width:${(h.score / top) * 100}%"></i></div><span class="muted small">per 100 invites</span></div>
        </article>`).join("")}</div></div>`;
  applyPlanFilter();
  countUp($("#plan"));
  say(`Plan loaded for ${centre}`);
}

/* ---------- Person ---------- */
function riskTrack(r) {
  const max = Math.max(0.2, Math.ceil(r.hi * 1.3 * 20) / 20);
  const X = (v) => 10 + (v / max) * 280;
  const ticks = [0, max / 2, max];
  return `<svg class="track" viewBox="0 0 300 50" role="img" aria-label="Estimate ${pct(r.p)}, 80% interval ${pct(r.lo)} to ${pct(r.hi)}">
    <line class="axis" x1="10" x2="290" y1="20" y2="20"/>
    <rect class="band" x="${X(r.lo)}" y="12" width="${Math.max(X(r.hi) - X(r.lo), 3)}" height="16" rx="4" data-tip="80% interval ${pct(r.lo)} to ${pct(r.hi)}"/>
    <circle class="dot" cx="${X(r.p)}" cy="20" r="6.5" tabindex="0" data-tip="Estimate ${pct(r.p)}"/>
    ${ticks.map((t) => `<text x="${X(t)}" y="46" text-anchor="middle">${pct(t, 0)}</text>`).join("")}</svg>`;
}

const overdue = (d) => (d < 60 ? `${d} days` : d < 730 ? `${Math.round(d / 30.4)} months` : `${Math.round(d / 365)} years`);
const OVERDUE_CAP = 1095;  // days; bars are capped at 3 years so one decades-old gap doesn't flatten the rest

async function openPerson(id) {
  const v = await api(`/api/person/${id}`);
  currentPerson = v;
  const p = v.person, r = v.risk;
  const fmax = Math.max(...(r?.factors ?? []).map((f) => f.log_odds), 1e-9);
  const benefit = v.priority != null ? v.priority * 100 : null;
  const C = 2 * Math.PI * 46;
  const stat = (k, val, sub) => `<div><span class="ps-k">${k}</span><b>${val}</b><span class="ps-s">${sub}</span></div>`;
  const risk = r ? `
      <div class="risk-head"><p class="big">${pct(r.p)}</p><p class="small muted">chance of a positive screen within 24 months</p></div>
      ${riskTrack(r)}
      <p class="small muted">Shaded band: ${r.interval} interval from 20 bootstrap refits</p>
      <h3 class="small" style="margin-top:14px">What raises it (log-odds vs the average adult)</h3>
      ${r.factors.length ? r.factors.map((f) => `<div class="fbar"><span>${esc(f.factor)}</span><div class="bar"><i style="width:${(f.log_odds / fmax) * 100}%"></i></div><b>+${f.log_odds.toFixed(2)}</b></div>`).join("") : '<p class="small muted">No factor is above average.</p>'}`
    : `<div class="rules-only">${icon("shield")}<div><strong>Rules only</strong><p class="small muted">Children are prioritised by care-gap rules in this prototype. A paediatric model is on the roadmap.</p></div></div>`;
  $("#person").innerHTML = `
    <section class="person-hero">
      <div class="p-avatar" role="img" aria-label="${benefit != null ? `Benefit ${benefit.toFixed(1)} early findings per 100 invitations` : "No care gap"}">
        <svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="46" class="ring-bg"/><circle cx="50" cy="50" r="46" class="ring-fg" style="stroke-dasharray:${C};stroke-dashoffset:${C * (1 - Math.min((benefit ?? 0) / 12, 1))}"/></svg>
        <span>${esc(p.name[0])}</span></div>
      <div class="p-copy">
        <p class="hero-eyebrow">${icon("person")} Household H${p.household} · ${esc(p.centre)}</p>
        <h2>${esc(p.name)}</h2>
        <div class="p-chips"><span>${age(p.age)}</span><span>${p.sex === "F" ? "Female" : "Male"}</span><span>${esc(p.nationality)}</span><span>${LANG[p.language]}</span>
          ${p.consent.outreach ? "" : `<span class="warn-chip">${icon("alert")}Opted out of outreach</span>`}</div>
        <div class="p-stats">
          ${stat("Care gaps", counter(v.gaps.length), v.gaps.length ? "overdue pathways" : "nothing overdue")}
          ${stat("Risk", r ? counter(r.p * 100, { dp: 1, suf: "%" }) : "Rules", r ? "24-month estimate" : "child pathway")}
          ${stat("Invitation lift", counter(v.lift * 100, { dp: 1, pre: "+", suf: " pts" }), "chance of attending")}
          ${stat("This week", v.slot ? esc(v.slot.slice(0, 10)) : "Not invited", v.slot ? esc(v.slot.slice(-5)) : "slots went elsewhere")}
        </div>
      </div>
    </section>
    <div class="family">${v.household.map((m) => `
      <button class="fam ${m.id === id ? "me" : ""} ${m.due ? "due" : ""}" data-person="${m.id}" ${m.id === id ? 'aria-current="true"' : ""}>
        ${avatar(m)}<span><strong>${esc(m.name)}</strong><small>${esc(m.role)} · ${age(m.age)}</small></span>
        ${m.due ? `<span class="status warn">${icon("clock")}Due</span>` : `<span class="status good">${icon("check")}Up to date</span>`}</button>`).join("")}</div>
    ${p.consent.outreach ? "" : `<div class="banner">${icon("alert")}Opted out of outreach. Care stays fully available; no messages are sent.</div>`}
    <div class="card brief" id="brief">
      <div class="brief-head"><div class="brief-title"><span class="brief-orb">${icon("spark")}</span>
        <div><h2>AI case brief</h2><p class="card-sub">Multi-agent review for the clinician · every figure verified by code</p></div></div>
        <button class="btn glow" data-brief="${id}">${icon("spark")}Generate brief</button></div>
      <div id="brief-body"><div class="brief-idle">${[["person", "Case reviewer", "Frames why this person was flagged"], ["msg", "Outreach checker", "Confirms messages are approved templates"], ["shield", "Safety & grounding", "Blocks invented numbers, names and claims"]]
        .map(([ic, a, d]) => `<div class="idle-step"><span class="lane-ic">${icon(ic)}</span><div><strong>${a}</strong><br><span class="muted small">${d}</span></div></div>`).join("")}</div></div>
    </div>
    <div class="person-grid">
      <div class="card"><div class="card-head"><div><h2>Care gaps</h2><p class="card-sub">How long each is overdue · bars capped at 3 years</p></div><span class="badge">RULES</span></div>
        ${v.gaps.length ? `<div class="gaps">${v.gaps.map((g) => `
          <div class="gap-row">
            <div class="gap-top">${chip(g.pathway)}<b>${overdue(g.overdue_days)} overdue</b></div>
            <div class="od-track" data-tip="${fmt(g.overdue_days)} days overdue"><i class="k-${kind(g.pathway)}" style="width:${Math.max(Math.min(g.overdue_days / OVERDUE_CAP, 1) * 100, 3)}%"></i></div>
            <p class="small">${esc(g.reason)} <span class="muted">· data: ${g.data.map(esc).join(", ")}</span></p>
            <div class="seg-group" role="group" aria-label="Clinician decision on ${esc(g.pathway)}">${["accept", "change", "decline"].map((a) => `
              <button data-decide="${a}" data-pathway="${esc(g.pathway)}" aria-pressed="${v.decisions.some((d) => d.pathway === g.pathway && d.action === a)}">${icon(a === "accept" ? "check" : a === "change" ? "pen" : "x")}${a[0].toUpperCase() + a.slice(1)}</button>`).join("")}</div>
          </div>`).join("")}</div>` : `<div class="rules-only">${icon("check")}<div><strong>Nothing overdue</strong><p class="small muted">No care gaps for this person this week.</p></div></div>`}
        <p class="small muted" style="margin-top:12px">Schedules are placeholders for the DoH-approved guidelines.</p></div>
      <div class="stack">
        <div class="card"><div class="card-head"><h2>Risk</h2><span class="badge">MODEL</span></div>${risk}</div>
        <div class="card"><div class="card-head"><h2>Invitation</h2><span class="badge">LIFT</span></div>
          <p class="big">+${(v.lift * 100).toFixed(1)}<span class="muted" style="font-size:1rem;font-weight:500"> pts</span></p>
          <p class="small muted">higher chance of attending if invited, learned from past randomised outreach</p>
          <div class="meter" role="img" aria-label="Lift ${(v.lift * 100).toFixed(1)} points"><i style="width:${Math.min(v.lift / 0.35, 1) * 100}%;background:linear-gradient(90deg,var(--s1),#9085e9)"></i></div>
          <p class="small" style="margin-top:10px">${v.slot ? `${icon("clock")} Booked <strong>${esc(v.slot)}</strong>` : "Not in this week's plan. Higher-benefit households filled the slots."}</p>
          ${benefit != null ? `<p class="small muted">Expected early findings per 100 invitations: ${benefit.toFixed(1)}</p>` : ""}</div>
      </div>
    </div>
    <div class="grid2">
      <div class="card"><div class="card-head"><div><h2>Data behind this flag</h2><p class="card-sub">Only authorised categories reach the engines</p></div></div>
        <div class="prov-tiles">${v.provenance.map((d) => `
          <div class="pt ${d.authorised ? "on" : "off"}"><span class="pt-ic">${icon(d.authorised ? "shield" : "lock")}</span>
            <strong>${esc(d.category)}</strong><span class="small">${d.authorised ? esc(d.used_for) : "Withheld by consent, not used anywhere"}</span>
            <span class="pt-tag">${d.authorised ? "Used" : "Withheld"}</span></div>`).join("")}</div></div>
      <div class="card"><div class="card-head"><div><h2>Household messages</h2><p class="card-sub">Exactly what the family sees · approved templates in Arabic or English</p></div></div>
        ${v.messages.length ? `<div class="phone"><div class="phone-notch"></div><div class="phone-top">${icon("shield")} Wiqaya · Abu Dhabi primary care</div>
          <div class="phone-body">${v.messages.map((m) => `
            <div class="sms-meta">To ${esc(m.to)} · ${esc(m.channel)} ${m.private ? `<span class="status warn">${icon("lock")}Private</span>` : `<span class="status good">${icon("check")}Family</span>`}</div>
            <div class="sms" dir="${m.language === "ar" ? "rtl" : "ltr"}" lang="${esc(m.language)}">${esc(m.text)}</div>`).join("")}</div>
          <div class="phone-reply"><span>1 Book</span><span>2 Ask</span><span>3 Later</span><span>0 Opt out</span></div></div>`
          : '<p class="muted">No messages: nobody in this household is due and reachable.</p>'}
        <p class="small muted" style="margin-top:12px">Adults' own screening joins the family message only if they opted in. Teens 14+ get a private, confidential note. Genomic results are never named.</p></div>
    </div>`;
  $("#person").dataset.id = id;
  showTab("person");
  window.scrollTo({ top: 0 });
  say(`Opened ${p.name}`);
}

/* ---------- Safeguards ---------- */
const DIMS = ["nationality", "region", "sex", "language", "age band", "region × nationality"];
function reachChart(rows, gate, overall) {
  const sorted = [...rows].sort((a, b) => a.reach - b.reach);
  const max = Math.max(...sorted.map((r) => r.reach), overall) * 1.15, L = 250, W = 470, rowH = 30, top = 34;
  const X = (v) => L + (v / max) * W, H = top + sorted.length * rowH + 30;
  const ticks = [0, max / 3, (2 * max) / 3].map((t) => Math.round(t * 1000) / 1000);
  return `<svg class="chart reach-chart" viewBox="0 0 760 ${H}" role="img" aria-label="Reach by segment against the release gate">
    ${ticks.map((t) => `<line class="grid" x1="${X(t)}" x2="${X(t)}" y1="${top - 8}" y2="${H - 26}"/><text x="${X(t)}" y="${H - 8}" text-anchor="middle">${pct(t, 1)}</text>`).join("")}
    <rect x="${L}" y="${top - 8}" width="${X(gate) - L}" height="${H - top - 18}" class="below-zone"/>
    <line x1="${X(gate)}" x2="${X(gate)}" y1="${top - 14}" y2="${H - 26}" class="gate-line"/>
    <text x="${X(gate)}" y="${top - 18}" text-anchor="middle" class="lbl warn-lbl">Gate ${pct(gate)}</text>
    <line x1="${X(overall)}" x2="${X(overall)}" y1="${top - 14}" y2="${H - 26}" class="overall-line"/>
    <text x="${X(overall) + 6}" y="${top - 18}" class="lbl">Overall ${pct(overall)}</text>
    ${sorted.map((r, i) => {
      const y = top + i * rowH + rowH / 2, low = r.reach < gate;
      return `<g class="seg-row" tabindex="0" data-tip="${esc(r.segment)}: reach ${pct(r.reach)} · ${fmt(r.contacted)} of ${fmt(r.due)} due invited">
        <text x="${L - 14}" y="${y + 4}" text-anchor="end" class="seg-name ${low ? "low" : ""}">${esc(r.segment)}</text>
        <line x1="${L}" x2="${X(r.reach)}" y1="${y}" y2="${y}" class="stem ${low ? "low" : ""}"/>
        <circle cx="${X(r.reach)}" cy="${y}" r="7" class="dot ${low ? "low" : ""}"/>
        <text x="${X(r.reach) + 12}" y="${y + 4}" class="val">${pct(r.reach)}</text></g>`;
    }).join("")}</svg>`;
}

async function loadSafeguards() {
  const s = await api(`/api/safeguards?dimension=${encodeURIComponent(dimension)}`);
  const live = s.rows.filter((r) => !r.suppressed);
  const below = live.filter((r) => r.gate === "review").sort((a, b) => a.reach - b.reach);
  const gate = s.gate * s.overall_reach, passing = live.length - below.length;
  const C = 2 * Math.PI * 44, hidden = s.rows.length - live.length;
  const max = Math.max(...live.map((r) => r.reach ?? 0), s.overall_reach) * 1.15;
  const gatePill = (r) => r.suppressed ? `<span class="status hide">${icon("lock")}Hidden</span>`
    : r.gate === "pass" ? `<span class="status good">${icon("check")}Pass</span>` : `<span class="status warn">${icon("alert")}Review</span>`;
  const why = dimension === "region × nationality" ? "Are any groups being left behind this week?" : `Are any groups being left behind by ${dimension.split(" ")[0]}?`;
  $("#safeguards").innerHTML = `
    <section class="plan-hero sg-hero ${below.length ? "has-review" : ""}">
      <div class="cap-ring" role="img" aria-label="${passing} of ${live.length} segments pass the reach gate">
        <svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="44" class="ring-bg"/><circle cx="50" cy="50" r="44" class="ring-fg" style="stroke-dasharray:${C};stroke-dashoffset:${C * (1 - passing / Math.max(live.length, 1))}"/></svg>
        <div><b>${counter(passing)}<small>/${live.length}</small></b><span>segments pass</span></div></div>
      <div class="plan-hero-copy">
        <p class="hero-eyebrow">${icon("shield")} Fairness check · by ${esc(dimension)}</p>
        <h2>${below.length ? `${below.length} segment${below.length > 1 ? "s" : ""} falling behind this week` : "Every segment passes the reach gate"}</h2>
        <p class="plan-hero-sub">A segment fails if its reach is under ${pct(s.gate, 0)} of overall reach. Context data shapes how we reach people, never whether they are eligible.</p>
        <div class="plan-stats">
          <div><b>${counter(s.overall_reach * 100, { dp: 1, suf: "%" })}</b><span>overall reach</span></div>
          <div><b>${counter(gate * 100, { dp: 1, suf: "%" })}</b><span>release gate</span></div>
          <div><b>${counter(below.length)}</b><span>need review</span></div>
          <div><b>${counter(hidden)}</b><span>cells hidden</span></div>
        </div>
      </div>
    </section>
    <div class="segs" role="group" aria-label="Break down by">${DIMS.map((d) => `<button data-dim="${esc(d)}" aria-pressed="${d === dimension}">${esc(d[0].toUpperCase() + d.slice(1))}</button>`).join("")}</div>
    <div class="plan-top">
      <div class="card"><div class="card-head"><div><h2>Reach by segment</h2><p class="card-sub">Lowest first · shaded zone is below the gate</p></div>
        <div class="legend"><span><span class="sw" style="background:var(--s1)"></span>Passes</span><span><span class="sw" style="background:var(--warn)"></span>Below gate</span></div></div>
        <div class="chart-scroll">${reachChart(live, gate, s.overall_reach)}</div></div>
      <div class="stack">
        ${below.length ? below.map((r) => `<div class="card review-card lift"><div class="card-head" style="margin:0"><div><span class="status warn">${icon("alert")}Needs outreach review</span><h2 style="margin-top:8px">${esc(r.segment)}</h2></div></div>
          <div class="region-nums"><div><b>${pct(r.reach)}</b><span>reach vs ${pct(gate)} gate</span></div><div><b>${fmt(r.due)}</b><span>due</span></div><div><b>${fmt(r.contacted)}</b><span>invited</span></div></div></div>`).join("")
          : `<div class="card review-card ok"><span class="status good">${icon("check")}No segment needs review</span><p class="small muted" style="margin-top:8px">Re-checked every week and before every model release.</p></div>`}
        <button class="btn glow" data-ask="${esc(why)}">${icon("spark")}Ask Wiqaya why</button>
        <div class="card privacy"><div class="privacy-ic">${icon("lock")}</div><div><h2>Small cells stay hidden</h2>
          <p class="small muted">Any group under ${s.min_cell} people is shown as "&lt;10", and if only one is hidden the next smallest is hidden too, so totals can't reveal it.</p></div></div>
      </div>
    </div>
    <div class="card"><div class="card-head"><div><h2>Full table · by ${esc(dimension)}</h2><p class="card-sub">Black tick = release gate. High-risk reach and calibration use the synthetic ground truth.</p></div></div>
      <div class="table-wrap"><table><thead><tr><th>Segment</th><th class="num">People</th><th class="num">Due</th><th class="num">Contacted</th><th>Reach</th><th>Gate</th><th class="num">High-risk reach</th><th class="num">Predicted</th><th class="num">Observed</th></tr></thead><tbody>
      ${s.rows.map((r) => `<tr><td><strong>${esc(r.segment)}</strong></td><td class="num">${fmt(r.n)}</td><td class="num">${fmt(r.due)}</td><td class="num">${fmt(r.contacted)}</td>
        <td>${r.suppressed ? '<span class="muted">—</span>' : `<div class="reach" data-tip="${esc(r.segment)}: reach ${pct(r.reach)}, gate ${pct(gate)}"><div class="reach-track"><i class="${r.gate === "review" ? "low" : ""}" style="width:${(r.reach / max) * 100}%"></i><span class="gate" style="left:${(gate / max) * 100}%"></span></div><b>${pct(r.reach)}</b></div>`}</td>
        <td>${gatePill(r)}</td><td class="num">${pct(r.high_risk_reach)}</td><td class="num">${pct(r.predicted_risk)}</td><td class="num">${pct(r.observed_rate)}</td></tr>`).join("")}
      </tbody></table></div></div>`;
  countUp($("#safeguards"));
}

/* ---------- Outcomes ---------- */
function niceMax(v) { const step = v > 0.12 ? 0.05 : 0.02; return Math.ceil((v * 1.15) / step) * step; }

function biasPlot(b) {
  const max = niceMax(Math.max(b.true_risk, b.naive_model, b.corrected_model));
  const X = (v) => 30 + (v / max) * 540;
  const ticks = Array.from({ length: Math.round(max / 0.02) + 1 }, (_, i) => i * 0.02);
  return `<svg class="chart" viewBox="0 0 600 120" role="img" aria-label="True risk ${pct(b.true_risk)}, naive model ${pct(b.naive_model)}, corrected model ${pct(b.corrected_model)}">
    ${ticks.map((t) => `<line class="grid" x1="${X(t)}" x2="${X(t)}" y1="34" y2="86"/><text x="${X(t)}" y="104" text-anchor="middle">${pct(t, 0)}</text>`).join("")}
    <line class="axis" x1="30" x2="570" y1="86" y2="86"/>
    <line class="truth" x1="${X(b.true_risk)}" x2="${X(b.true_risk)}" y1="26" y2="86"/>
    <text class="lbl" x="${X(b.true_risk) + 6}" y="22">True risk ${pct(b.true_risk)}</text>
    <circle cx="${X(b.naive_model)}" cy="60" r="8" fill="var(--s2)" stroke="var(--surface)" stroke-width="2" tabindex="0" data-tip="Naive model: ${pct(b.naive_model)} (trained on everyone)"/>
    <text class="lbl" x="${X(b.naive_model)}" y="44" text-anchor="middle">Naive ${pct(b.naive_model)}</text>
    <circle cx="${X(b.corrected_model)}" cy="60" r="8" fill="var(--s1)" stroke="var(--surface)" stroke-width="2" tabindex="0" data-tip="Wiqaya corrected model: ${pct(b.corrected_model)} (trained on screened people)"/>
    <text class="lbl" x="${X(b.corrected_model) - 12}" y="64" text-anchor="end">Wiqaya ${pct(b.corrected_model)}</text></svg>`;
}

function calibrationPlot(cal) {
  const max = niceMax(Math.max(...cal.flatMap((c) => [c.predicted, c.observed])));
  const L = 44, B = 250, W = 300, H = 220, X = (v) => L + (v / max) * W, Y = (v) => B - (v / max) * H;
  const ticks = [0, max / 2, max];
  return `<svg class="chart cal" viewBox="0 0 360 290" role="img" aria-label="Calibration: predicted versus observed rate by decile">
    ${ticks.map((t) => `<line class="grid" x1="${L}" x2="${L + W}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${L - 8}" y="${Y(t) + 4}" text-anchor="end">${pct(t, 0)}</text><text x="${X(t)}" y="${B + 18}" text-anchor="middle">${pct(t, 0)}</text>`).join("")}
    <line class="axis" x1="${L}" x2="${L + W}" y1="${B}" y2="${B}"/>
    <line class="diag" x1="${X(0)}" y1="${Y(0)}" x2="${X(max)}" y2="${Y(max)}"/>
    <text x="${X(max) - 4}" y="${Y(max) + 14}" text-anchor="end">perfect calibration</text>
    ${cal.map((c, i) => `<circle class="pt" cx="${X(c.predicted)}" cy="${Y(c.observed)}" r="5.5" tabindex="0" data-tip="Decile ${i + 1}: predicted ${pct(c.predicted)}, observed ${pct(c.observed)} (${c.n} people)"/>`).join("")}
    <text x="${L + W / 2}" y="${B + 36}" text-anchor="middle">Predicted risk</text>
    <text transform="translate(12 ${B - H / 2}) rotate(-90)" text-anchor="middle">Observed rate</text></svg>`;
}

const ROADMAP = [
  ["Months 0–6", "Rules pilot", "Care-gap rules and outreach at 3–5 centres; 12-month baseline frozen first"],
  ["Months 6–12", "Risk model live", "Bias-corrected model behind clinician review once safeguards pass"],
  ["Months 9–18", "Genomic pathways", "Consented genetic counselling and enhanced surveillance"],
  ["Month 18+", "Emirate-wide", "Continuous monitoring, monthly drift checks, annual model review"],
];

function renderOutcomes() {
  const [[wName, w], [cName, c]] = Object.entries(outcomes.policies);
  const delta = (k) => w[k] / c[k] - 1;
  const metrics = [
    ["early_findings_caused", "Early findings caused by the invitation", "The ranking's objective", 1],
    ["caused_by_invite", "Completions caused by the invitation", "Visits that happen only because of the invite", 0],
    ["completions", "Expected completed visits", "Includes people who would have come anyway", 0],
  ];
  const b = outcomes.bias_check;
  const naiveGap = 1 - b.naive_model / b.true_risk, fixedGap = 1 - b.corrected_model / b.true_risk;
  const cal = outcomes.calibration;
  const mae = cal.reduce((a, x) => a + Math.abs(x.predicted - x.observed), 0) / cal.length;
  const card = ([key, label, note, dp]) => {
    const d = delta(key), max = Math.max(w[key], c[key]), level = Math.abs(d) < 0.02;
    const row = (name, val, cls) => `<div class="vs-row ${cls}" data-tip="${esc(name)}: ${fmt(val)}"><span>${esc(name.split(":")[0])}</span>
      <div class="bar"><i style="width:${(val / max) * 100}%"></i></div><b>${counter(val, { dp })}</b></div>`;
    return `<div class="card vs-card lift ${level ? "" : "win"}"><div class="card-head"><div><h2>${label}</h2><p class="card-sub">${note}</p></div>
      ${level ? `<span class="delta flat">≈ level</span>` : `<span class="delta up">${icon("up")}${d > 0 ? "+" : ""}${(d * 100).toFixed(1)}%</span>`}</div>
      <div class="pair">${row(wName, w[key], "ours")}${row(cName, c[key], "theirs")}</div>
      ${level ? '<p class="small muted" style="margin-top:10px">Honest result: roughly the same as the calendar rule.</p>' : ""}</div>`;
  };
  $("#outcomes").innerHTML = `
    <section class="plan-hero out-hero">
      <div class="out-big"><b>${counter(delta("early_findings_caused") * 100, { dp: 1, pre: "+", suf: "%" })}</b><span>early findings</span></div>
      <div class="plan-hero-copy">
        <p class="hero-eyebrow">${icon("chart")} Same capacity · two policies · one week</p>
        <h2>More early findings from the same ${fmt(w.invited)} invitations</h2>
        <p class="plan-hero-sub">Both policies fill ${outcomes.slots_per_centre} slots at each of ${outcomes.centres} centres. Wiqaya ranks households by the early findings an invitation is expected to cause; the calendar rule invites whoever is most overdue. Scored against the synthetic ground truth.</p>
        <div class="plan-stats">
          <div><b>${counter(w.early_findings_caused, { dp: 1 })}</b><span>Wiqaya early findings</span></div>
          <div><b>${counter(c.early_findings_caused, { dp: 1 })}</b><span>calendar rule</span></div>
          <div><b>${counter(w.completions)}</b><span>Wiqaya completed visits</span></div>
          <div><b>${counter(c.completions)}</b><span>calendar rule</span></div>
        </div>
      </div>
    </section>
    <div class="legend out-legend"><span><span class="sw" style="background:var(--s1)"></span>${esc(wName)}</span><span><span class="sw" style="background:var(--base)"></span>${esc(cName)}</span></div>
    <div class="compare">${metrics.map(card).join("")}</div>
    <div class="grid2">
      <div class="card"><div class="card-head"><div><h2>Bias correction for the never-screened</h2><p class="card-sub">${esc(b.group)} · ${fmt(b.n)} people · true risk ${pct(b.true_risk)}</p></div></div>
        <div class="bias-meter">
          <div class="bm-row"><span><span class="sw" style="background:var(--s2)"></span>Naive model</span><div class="bm-track"><i class="naive" style="width:${naiveGap * 100}%"></i></div><b>${counter(naiveGap * 100, { dp: 0, suf: "%" })}</b></div>
          <div class="bm-row"><span><span class="sw" style="background:var(--s1)"></span>Wiqaya model</span><div class="bm-track"><i class="fixed" style="width:${Math.max(fixedGap, 0) * 100}%"></i></div><b>${counter(Math.abs(fixedGap) * 100, { dp: 0, suf: "%" })}</b></div>
          <p class="small muted">How far each model under-estimates the true risk of people who were never screened</p>
        </div>
        ${biasPlot(b)}
        <p class="small" style="margin-top:10px">A naive model learns that a missed screening means low risk. Wiqaya trains only on people who were actually screened, so under-served families are not scored down.</p></div>
      <div class="card"><div class="card-head"><div><h2>Calibration</h2><p class="card-sub">Held-out screened adults, by decile of predicted risk</p></div>
        <div class="cal-stat"><b>${counter(mae * 100, { dp: 1, suf: " pts" })}</b><span>average gap, predicted vs observed</span></div></div>
        ${calibrationPlot(cal)}
        <details><summary>Show as a table</summary><div class="table-wrap"><table><thead><tr><th>Decile</th><th class="num">Predicted</th><th class="num">Observed</th><th class="num">People</th></tr></thead><tbody>
          ${cal.map((x, i) => `<tr><td>${i + 1}</td><td class="num">${pct(x.predicted)}</td><td class="num">${pct(x.observed)}</td><td class="num">${fmt(x.n)}</td></tr>`).join("")}</tbody></table></div></details></div>
    </div>
    <div class="card road"><div class="card-head"><div><h2>Path to pilot</h2><p class="card-sub">Stepped-wedge rollout: each centre is compared with itself before go-live and with centres not yet live</p></div></div>
      <ol class="road-steps">${ROADMAP.map(([when, what, detail], i) => `<li class="lift"><span class="road-dot">${i + 1}</span><small>${when}</small><strong>${what}</strong><span>${detail}</span></li>`).join("")}</ol></div>`;
  countUp($("#outcomes"));
}


/* ---------- Ask Wiqaya: multi-agent copilot ---------- */
const AGENT_ICON = { "Supervisor": "route", "Query planner": "pen", "Verifier": "eye", "Analyst tool": "tool", "Narrator": "pen",
  "Case reviewer": "person", "Outreach checker": "msg", "Safeguards reporter": "shield", "Safety & grounding": "shield" };
const EXAMPLES = [
  ["plan", "Population", "How many children under 5 in Al Ain are overdue a developmental check?"],
  ["plan", "Population", "Which Emirati women are overdue for cervical screening but not invited, by region?"],
  ["plan", "Population", "Adults over 45 in Al Dhafra due colorectal screening"],
  ["shield", "Fairness", "Are any groups being left behind this week?"],
  ["person", "Case", "Brief me on person 120"],
];
const REQUIRED = { population: ["matches"], case: ["summary", "risk"], safeguards: ["below"] };
let modelInfo = { online: false };

function renderModel(m) {
  modelInfo = m;
  const hero = $("#hero-model");
  if (hero) hero.innerHTML = `${icon("spark")}${m.online ? `${esc(m.model)} online` : "AI agents (rule-based mode)"}`;
  const el = $("#model-pill");
  if (el) el.innerHTML = m.online ? `<span class="status good">${icon("check")}${esc(m.model)} · ${m.provider === "gemini" ? "Gemini API" : "local"} · online</span>`
    : `<span class="status warn">${icon("alert")}Model offline · rule-based agents</span>`;
}

function renderCopilot() {
  $("#copilot").innerHTML = `
    <section class="ask-hero">
      <div class="ask-hero-top"><span class="brief-orb big-orb">${icon("spark")}</span>
        <div><p class="hero-eyebrow">Multi-agent copilot</p><h2>Ask Wiqaya anything about this week</h2>
          <p class="plan-hero-sub">Six agents route, plan, query and verify. The model frames the answer; every figure comes from code.</p></div>
        <span id="model-pill" class="model-pill"></span></div>
      <form id="ask-form" class="ask-box"><label class="sr-only" for="ask-input">Question</label>
        <span class="ask-ic">${icon("spark")}</span>
        <input id="ask-input" maxlength="400" autocomplete="off" placeholder="Ask about care gaps, a person, or fairness…">
        <button class="btn glow" type="submit">Ask ${icon("up")}</button></form>
      <div class="ex-grid">${EXAMPLES.map(([ic, tag, q]) => `<button class="ex-card" data-ask="${esc(q)}"><span class="ex-tag">${icon(ic)}${tag}</span><span>${esc(q)}</span></button>`).join("")}</div>
    </section>
    <div id="thread" class="thread" aria-live="polite"></div>`;
  renderModel(modelInfo);
}

function pipeline(trace) {
  const agents = [...new Set(trace.map((s) => s.agent))];
  return `<ol class="mini-pipe">${agents.map((a) => {
    const steps = trace.filter((s) => s.agent === a);
    const cls = steps.some((s) => s.action === "Approved") ? "ok" : steps.some((s) => s.action.startsWith("Rejected") || s.action === "Fell back") ? "bad" : steps.some((s) => s.source === "llm") ? "model" : "";
    return `<li class="${cls}" data-tip="${esc(a)}: ${esc(steps.map((s) => s.action).join(" → "))}"><span>${icon(AGENT_ICON[a] ?? "spark")}</span>${esc(a)}</li>`;
  }).join("")}</ol>`;
}

function answerTurn(r) {
  const f = r.facts ?? {}, picked = f.picked ?? [], req = REQUIRED[f.kind] ?? [];
  const blocked = r.trace.filter((s) => s.action.startsWith("Rejected"));
  const total = r.trace.reduce((a, s) => a + s.ms, 0);
  const lines = f.facts ? Object.entries(f.facts).filter(([k]) => (f.used ?? []).includes(k)) : [];
  const tag = (k) => picked.includes(k) ? `<span class="pick">${icon("spark")}AI pick</span>` : req.includes(k) ? `<span class="verified">${icon("lock")}Required</span>` : `<span class="verified">${icon("check")}Verified</span>`;
  return `<article class="turn ai">
    <span class="brief-orb">${icon("spark")}</span>
    <div class="ai-bubble">
      <div class="bubble-head"><strong>Wiqaya</strong>${sourceBadge(r)}<span class="muted small">${r.trace.length} steps · ${total} ms</span></div>
      ${f.lead ? `<p class="lead-line">${esc(f.lead)}</p>` : ""}
      ${lines.length ? `<ul class="fact-lines">${lines.map(([k, t]) => `<li>${icon("check")}<span>${esc(t)}</span>${tag(k)}</li>`).join("")}</ul>` : `<p class="answer-text">${esc(r.answer)}</p>`}
      <p class="disclaimer">${icon("shield")}Population prioritisation, not a diagnosis; the clinician decides.</p>
      ${pipeline(r.trace)}
      ${blocked.length ? `<div class="blocked"><p class="small"><strong>${blocked.length} draft${blocked.length > 1 ? "s" : ""} blocked by the safety agent</strong></p>${blocked.map((s) => `<span class="status warn">${icon("alert")}${esc(s.detail)}</span>`).join("")}</div>` : ""}
      ${factsPanel(r.facts)}
      <details class="replay"><summary>Full agent trace</summary>${traceList(r.trace)}</details>
    </div></article>`;
}

function traceList(steps) {
  return `<ol class="trace">${steps.map((s) => `
    <li class="step ${s.action.startsWith("Rejected") ? "bad" : s.action === "Approved" ? "ok" : ""}">
      <span class="lane-ic">${icon(AGENT_ICON[s.agent] ?? "spark")}</span>
      <div><div class="step-head"><strong>${esc(s.agent)}</strong><span>${esc(s.action)}</span>
        <span class="badge">${s.source === "llm" ? "MODEL" : "CODE"}</span><span class="muted small">${s.ms} ms</span></div>
        <p class="small muted step-detail">${esc(s.detail)}</p></div></li>`).join("")}</ol>`;
}

function sourceBadge(r) {
  return r.source === "llm" ? `<span class="status good">${icon("check")}Written by ${esc(r.model.model ?? "model")}, verified by the safety agent</span>`
    : r.trace.some((s) => s.action === "Fell back")
      ? `<span class="status warn">${icon("shield")}Model draft rejected by the safety agent; verified answer shown</span>`
      : `<span class="status hide">${icon("shield")}Model offline; verified rule-based answer</span>`;
}

function factsPanel(f) {
  if (!f) return "";
  if (f.kind === "population") {
    const r = f.result, max = Math.max(...r.groups.map((g) => g.count ?? 0), 1);
    return `<div class="sub-panel"><h3 class="small">Data returned by the analyst tool</h3>
      <div class="consents" style="margin:10px 0 14px">${Object.entries(f.spec).map(([k, v]) => `<span class="chip">${esc(k.replace("_", " "))}: ${esc(v)}</span>`).join("") || '<span class="chip">no filters</span>'}</div>
      <p><span class="big">${fmt(r.count)}</span> <span class="muted">matches · ${fmt(r.in_plan)} in this week's plan</span></p>
      ${r.groups.length ? `<div class="hbars" style="margin-top:12px">${r.groups.map((g) => `<div class="hbar"><span>${esc(g.group)}</span><b>${fmt(g.count)}</b><div class="bar"><i style="width:${((g.count ?? 0) / max) * 100}%"></i></div></div>`).join("")}</div>` : ""}
      ${r.top.length ? `<h3 class="small" style="margin-top:16px">Highest benefit per invitation</h3><ul class="members" style="margin-top:8px">${r.top.map((p) => `<li class="member">${avatar(p)}<div><button class="link" data-person="${p.id}">${esc(p.name)}</button> <span class="muted small">${age(p.age)} · ${esc(p.centre)} · ${p.benefit_per_100} per 100</span></div></li>`).join("")}</ul>` : ""}</div>`;
  }
  if (f.kind === "case") return `<div class="sub-panel"><p><button class="btn" data-person="${f.person_id}">${icon("person")}Open person ${f.person_id}</button></p></div>`;
  return `<div class="sub-panel"><h3 class="small">Segments below the reach gate · by ${esc(f.dimension)}</h3><div class="consents" style="margin-top:10px">${f.below_gate.map((s) => `<span class="status warn">${icon("alert")}${esc(s)}</span>`).join("") || `<span class="status good">${icon("check")}All pass</span>`}</div>
    <p style="margin-top:12px"><button class="btn" data-view="safeguards">${icon("shield")}Open safeguards</button></p></div>`;
}

async function ask(question) {
  const thread = $("#thread");
  $("#ask-input").value = "";
  thread.insertAdjacentHTML("beforeend", `<article class="turn user"><div class="user-bubble">${esc(question)}</div></article>
    <article class="turn ai pending"><span class="brief-orb">${icon("spark")}</span><div class="ai-bubble">
      <div class="stages">${["Supervisor", "Query planner", "Verifier", "Analyst tool", "Narrator", "Safety & grounding"].map((a, i) => `<span class="stage" style="--d:${i * 0.35}s">${icon(AGENT_ICON[a])}${a}</span>`).join("")}</div>
      <div class="shimmer"></div><div class="shimmer short"></div></div></article>`);
  const pending = thread.lastElementChild;
  pending.scrollIntoView({ behavior: "smooth", block: "end" });
  try {
    const r = await api("/api/agent/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) });
    renderModel(r.model);
    pending.outerHTML = answerTurn(r);
    say("Answer ready");
  } catch (e) { pending.outerHTML = `<article class="turn ai"><div class="ai-bubble banner" role="alert">${esc(e.message)}</div></article>`; }
}

const BRIEF_STAGES = ["Supervisor", "Case reviewer", "Outreach checker", "Safety & grounding"];
const GUARDS = ["No numbers typed by the model", "No names or places invented", "No unsupported claims", "No diagnostic or alarmist words",
  "Required facts always present", "Household messages are approved templates"];

function briefTiles(v, picked) {
  const REQUIRED = ["summary", "risk"];
  const tag = (keys) => keys.some((k) => picked.includes(k)) ? `<span class="pick">${icon("spark")}AI pick</span>`
    : keys.some((k) => REQUIRED.includes(k)) ? `<span class="verified">${icon("lock")}Required</span>` : `<span class="verified">${icon("check")}Verified</span>`;
  const withheld = v.provenance.filter((d) => !d.authorised).map((d) => d.category);
  const r = v.risk;
  const tiles = [
    ["summary", "Care gaps", ["summary"], v.gaps.length ? `${v.gaps.map((g) => chip(g.pathway)).join("")}<ul class="tile-list">${v.gaps.map((g) => `<li>${esc(g.reason)} · <b>${overdue(g.overdue_days)} overdue</b></li>`).join("")}</ul>` : '<p class="muted">No open care gaps.</p>'],
    ["risk", "Risk (24 months)", ["risk", "factors"], r ? `<p class="tile-big">${pct(r.p)}</p>${riskTrack(r)}<div>${r.factors.map((f) => `<span class="chip">${esc(f.factor)}</span>`).join("")}</div>` : '<p class="tile-big small-big">Rules only</p><p class="muted small">Children are prioritised by care-gap rules; no model score.</p>'],
    ["lift", "Invitation lift", ["lift"], `<p class="tile-big">+${(v.lift * 100).toFixed(1)}<small> pts</small></p><div class="meter"><i style="width:${Math.min(v.lift / 0.35, 1) * 100}%;background:var(--s1)"></i></div><p class="muted small">higher chance of attending if invited</p>`],
    ["slot", "This week", ["slot"], v.slot ? `<p class="tile-big small-big">${icon("clock")} ${esc(v.slot)}</p><p class="muted small">Back-to-back with the household</p>` : '<p class="tile-big small-big">Not this week</p><p class="muted small">Higher-benefit households filled the slots</p>'],
    ["consent", "Consent", ["consent"], withheld.length ? `${withheld.map((w) => `<span class="status warn">${icon("lock")}${esc(w)} withheld</span>`).join(" ")}<p class="muted small" style="margin-top:8px">Not used anywhere</p>` : `<span class="status good">${icon("check")}All categories authorised</span>`],
  ];
  return `<div class="tiles">${tiles.map(([k, h, keys, body], i) => `<div class="tile ${keys.some((x) => picked.includes(x)) ? "picked" : ""}" style="--d:${i * 70}ms"><div class="tile-head"><span>${h}</span>${tag(keys)}</div>${body}</div>`).join("")}</div>`;
}

async function caseBrief(id) {
  const card = $("#brief"), box = $("#brief-body");
  card.classList.add("running");
  box.innerHTML = `<div class="stages" role="status" aria-label="Agents working">${BRIEF_STAGES.map((a, i) => `<span class="stage" style="--d:${i * 0.45}s">${icon(AGENT_ICON[a])}${a}</span>`).join("")}</div>
    <div class="shimmer"></div><div class="shimmer short"></div>`;
  try {
    const r = await api(`/api/agent/brief/${id}`);
    const v = currentPerson, f = r.facts, rejected = r.trace.filter((s) => s.action.startsWith("Rejected"));
    const lead = f.lead ?? f.facts.summary;
    box.innerHTML = `
      <div class="brief-grid">
        <div>
          <div class="ai-quote"><span class="quote-k">${icon("spark")}${f.lead ? `AI summary · ${esc(r.model.model)}` : "Verified summary (model draft not used)"}</span><p>${esc(lead)}</p></div>
          ${briefTiles(v, f.picked ?? [])}
          <p class="disclaimer">${icon("shield")}Population prioritisation, not a diagnosis; the clinician decides.</p>
          <div class="brief-actions"><button class="btn ghost-dark" data-copy-brief>${icon("msg")}Copy brief</button><button class="btn ghost-dark" data-brief="${id}">${icon("spark")}Regenerate</button></div>
        </div>
        <aside class="trust">
          <div class="trust-top"><svg viewBox="0 0 80 80" class="ring" aria-hidden="true"><circle cx="40" cy="40" r="32" class="ring-bg"/><circle cx="40" cy="40" r="32" class="ring-fg"/></svg>
            <div><p class="trust-big">100%</p><p class="small">of figures trace to verified data, by construction</p></div></div>
          <ul class="guards">${GUARDS.map((g) => `<li>${icon("check")}${g}</li>`).join("")}</ul>
          ${rejected.length ? `<div class="blocked"><p class="small"><strong>${rejected.length} draft${rejected.length > 1 ? "s" : ""} blocked before you saw ${rejected.length > 1 ? "them" : "it"}</strong></p>${rejected.map((s) => `<span class="status warn">${icon("alert")}${esc(s.detail)}</span>`).join("")}</div>` : ""}
          <details class="replay" open><summary>Agent trace · ${r.trace.length} steps · ${r.trace.reduce((a, s) => a + s.ms, 0)} ms</summary>${traceList(r.trace)}</details>
        </aside>
      </div>`;
    box.dataset.text = r.answer;
    say("Case brief ready");
  } catch (e) { box.innerHTML = `<p class="banner" role="alert">${esc(e.message)}</p>`; }
  finally { card.classList.remove("running"); }
}

/* ---------- Events ---------- */
document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("[data-view],[data-person],[data-dim],[data-decide],[data-open-top],[data-ask],[data-brief],[data-copy-brief],[data-plan-kind],[data-plan-lang]");
  if (!t) return;
  if (t.dataset.planKind || t.dataset.planLang) {
    planFilter = { kind: t.dataset.planKind ?? planFilter.kind, lang: t.dataset.planLang ?? planFilter.lang };
    applyPlanFilter();
  } else if (t.hasAttribute("data-copy-brief")) {
    navigator.clipboard?.writeText($("#brief-body").dataset.text ?? "").then(() => say("Brief copied"), () => say("Copy failed"));
    t.innerHTML = `${icon("check")}Copied`;
  } else if (t.dataset.ask) { showTab("copilot"); ask(t.dataset.ask); }
  else if (t.dataset.brief) caseBrief(Number(t.dataset.brief));
  else if (t.dataset.view) showTab(t.dataset.view);
  else if (t.dataset.person) openPerson(Number(t.dataset.person));
  else if (t.dataset.dim) { dimension = t.dataset.dim; loadSafeguards(); }
  else if (t.hasAttribute("data-open-top")) { const first = $("#plan [data-person]"); if (first) openPerson(Number(first.dataset.person)); }
  else if (t.dataset.decide) {
    try {
      const d = await api("/api/decisions", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ person_id: Number($("#person").dataset.id), pathway: t.dataset.pathway, action: t.dataset.decide }) });
      t.parentElement.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === t)));
      say(`Recorded ${d.action} for ${d.pathway}`);
    } catch (e) { say(e.message); alert(e.message); }
  }
});

document.addEventListener("submit", (ev) => {
  if (ev.target.id !== "ask-form") return;
  ev.preventDefault();
  const q = $("#ask-input").value.trim();
  if (q.length >= 3) ask(q);
});

const tip = $("#tip");
const showTip = (text, x, y) => { tip.textContent = text; tip.hidden = false; tip.style.left = `${Math.min(x + 14, innerWidth - 280)}px`; tip.style.top = `${y + 14}px`; };
document.addEventListener("pointermove", (e) => { const t = e.target.closest?.("[data-tip]"); t ? showTip(t.dataset.tip, e.clientX, e.clientY) : (tip.hidden = true); });
document.addEventListener("focusin", (e) => { const t = e.target.closest?.("[data-tip]"); if (t) { const r = t.getBoundingClientRect(); showTip(t.dataset.tip, r.left, r.bottom); } });
document.addEventListener("focusout", () => (tip.hidden = true));

init().catch((e) => { say(e.message); $("#main").insertAdjacentHTML("afterbegin", `<div class="card banner" role="alert">Could not load: ${esc(e.message)}</div>`); });
