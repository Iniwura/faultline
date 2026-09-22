import { createClient, isSuccessful } from "genlayer-js";
import { studioDevnet } from "genlayer-js/chains";
import { TransactionHashVariant } from "genlayer-js/types";
import { refreshOwnerRecords } from "./records.js";

const CONTRACT = "0x5516Cd4ed18bAE5ADCA01908366c49bFC8612F00";
const LIVE_IDS = {
  source: "faultline-fixed-source-1789912411",
  decisionB: "faultline-fixed-decision-b-1789912411",
  decisionC: "faultline-fixed-decision-c-1789912411"
};
const PROOF_TX = "0x347b6fd885b0e4fe69a63a3bbfcaf91747ee247db8228b62c557b5bf754efe35";
const LATEST_NONFINAL = TransactionHashVariant.LATEST_NONFINAL;
const readClient = createClient({ chain: studioDevnet });
let writeClient = null;
let walletAddress = "";
let liveLoaded = false;
let liveFetchInFlight = null;
let simulationOn = false;
let selectedNode = "source";
let flash = null;
let activityLog = [
  { label: "Decision recheck", result: "VALID", detail: "Vendor X procurement decision", tx: PROOF_TX, time: "Verified live" }
];
let userSources = [];
let userDecisions = [];

let liveState = {
  source: {
    id: LIVE_IDS.source,
    title: "IANA Example Domains",
    claim: "IANA maintains example domains such as example.com and example.org for documentation purposes.",
    url: "https://www.iana.org/help/example-domains",
    state: "SOURCE_CURRENT",
    friendly: "CURRENT",
    revision: 0
  },
  decisionB: {
    id: LIVE_IDS.decisionB,
    title: "Source claim still valid",
    question: "Should this decision continue to rely on the tracked claim?",
    state: "DECISION_VALID",
    friendly: "VALID",
    revision: 1,
    dependencies: [LIVE_IDS.source]
  },
  decisionC: {
    id: LIVE_IDS.decisionC,
    title: "Downstream action may continue",
    question: "The downstream decision remains safe while B remains valid.",
    state: "DECISION_VALID",
    friendly: "VALID",
    revision: 1,
    dependencies: [LIVE_IDS.decisionB]
  },
  readAt: "cached verified state"
};

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const short = (value, head = 18, tail = 10) => {
  const text = String(value ?? "");
  return text.length > head + tail + 1 ? `${text.slice(0, head)}…${text.slice(-tail)}` : text;
};
const now = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const parseContractReturn = (value) => {
  if (typeof value !== "string") return value;
  try { return JSON.parse(value); } catch { return value; }
};
const friendlyState = (state) => {
  const value = String(state ?? "");
  if (value.includes("INVALIDATED")) return "INVALIDATED";
  if (value.includes("UNRESOLVED")) return "UNRESOLVED";
  if (value.includes("UNOBSERVED")) return "UNOBSERVED";
  if (value.includes("STALE")) return "NEEDS RECHECK";
  if (value.includes("CHANGED")) return "CHANGED";
  if (value.includes("CURRENT")) return "CURRENT";
  if (value.includes("VALID")) return "VALID";
  if (value.includes("NO_MATERIAL_CHANGE")) return "NO IMPORTANT CHANGE";
  if (value.includes("MATERIAL_CHANGE")) return "CHANGED";
  return value || "UNKNOWN";
};
const stateClass = (state) => friendlyState(state).toLowerCase().replaceAll(" ", "-");

async function read(method, args = []) {
  const result = await readClient.readContract({
    address: CONTRACT,
    functionName: method,
    args,
    jsonSafeReturn: true,
    transactionHashVariant: LATEST_NONFINAL
  });
  return parseContractReturn(result);
}

async function refreshUserRecords() {
  const records = await refreshOwnerRecords({
    read,
    owner: walletAddress
  });

  userSources = records.sources.map((item) => ({
    ...item,
    friendly: friendlyState(item.state)
  }));

  userDecisions = records.decisions.map((item) => ({
    ...item,
    friendly: friendlyState(item.state)
  }));

  return records;
}

async function loadLiveState(force = false) {
  if (liveFetchInFlight && !force) return liveFetchInFlight;
  if (liveLoaded && !force) return liveState;
  liveFetchInFlight = Promise.all([
    read("get_source", [LIVE_IDS.source]),
    read("get_decision", [LIVE_IDS.decisionB]),
    read("get_decision", [LIVE_IDS.decisionC]),
    read("get_effective_decision_state", [LIVE_IDS.decisionB]),
    read("get_effective_decision_state", [LIVE_IDS.decisionC])
  ]).then(([source, decisionB, decisionC, effectiveB, effectiveC]) => {
    liveState = {
      source: { ...liveState.source, ...source, id: LIVE_IDS.source, title: "IANA Example Domains", claim: source.tracked_claim || liveState.source.claim, url: source.url || liveState.source.url, state: source.state || "SOURCE_CURRENT", friendly: friendlyState(source.state || "SOURCE_CURRENT") },
      decisionB: { ...liveState.decisionB, ...decisionB, id: LIVE_IDS.decisionB, title: "Source claim still valid", question: decisionB.question || liveState.decisionB.question, state: effectiveB || decisionB.state || "DECISION_VALID", friendly: friendlyState(effectiveB || decisionB.state || "DECISION_VALID"), dependencies: [LIVE_IDS.source] },
      decisionC: { ...liveState.decisionC, ...decisionC, id: LIVE_IDS.decisionC, title: "Downstream action may continue", question: decisionC.question || liveState.decisionC.question, state: effectiveC || decisionC.state || "DECISION_VALID", friendly: friendlyState(effectiveC || decisionC.state || "DECISION_VALID"), dependencies: [LIVE_IDS.decisionB] },
      readAt: `live read / ${now()}`
    };
    liveLoaded = true;
    draw();
    return liveState;
  }).catch((error) => {
    liveLoaded = true;
    document.querySelectorAll("[data-live-readout]").forEach((element) => { element.textContent = "live read unavailable / cached verified state"; });
    throw error;
  }).finally(() => { liveFetchInFlight = null; });
  return liveFetchInFlight;
}

function rootNav(active = "") {
  return `<header class="topbar"><a class="brand" href="/">FAULTLINE<sup>®</sup></a><nav class="nav" aria-label="Primary navigation"><a href="/#how-it-works" ${active === "how" ? 'aria-current="page"' : ""}>How it works</a><a href="/app/live" ${active === "live" ? 'aria-current="page"' : ""}>Live proof</a><a class="nav-cta" href="/app">Launch app</a></nav></header>`;
}

function appNav(active = "") {
  return `<header class="topbar app-topbar"><a class="brand" href="/">FAULTLINE<sup>®</sup></a><button class="menu-toggle" type="button" data-menu-toggle aria-expanded="false">Menu</button><nav class="app-nav-links" data-app-links aria-label="App navigation"><a href="/app" ${active === "overview" ? 'aria-current="page"' : ""}>Overview</a><a href="/app/sources" ${active === "sources" ? 'aria-current="page"' : ""}>Sources</a><a href="/app/decisions" ${active === "decisions" ? 'aria-current="page"' : ""}>Decisions</a><a href="/app/activity" ${active === "activity" ? 'aria-current="page"' : ""}>Activity</a></nav><button class="wallet-button" type="button" data-connect-wallet>${walletAddress ? `Wallet ${short(walletAddress, 5, 4)}` : "Connect wallet"}</button></header>`;
}

function footer() { return `<footer class="footer"><a class="brand" href="/">FAULTLINE<sup>®</sup></a><span>DEPLOYED ON GENLAYER STUDIO DEV</span></footer>`; }

function simpleGraph() {
  return `<div class="simple-graph" aria-label="Source to decision dependency chain"><div class="simple-node"><span>01 / SOURCE</span><strong>IANA EXAMPLE DOMAINS</strong><b class="state current">CURRENT</b></div><div class="simple-arrow">↓</div><div class="simple-node"><span>02 / DECISION</span><strong>SOURCE CLAIM STILL VALID</strong><b class="state valid">VALID</b></div><div class="simple-arrow">↓</div><div class="simple-node"><span>03 / DECISION</span><strong>DOWNSTREAM ACTION MAY CONTINUE</strong><b class="state valid">VALID</b></div></div>`;
}

function liveGraphMarkup() {
  const source = simulationOn ? { ...liveState.source, state: "SOURCE_CHANGED", friendly: "CHANGED" } : liveState.source;
  const decisionB = simulationOn ? { ...liveState.decisionB, state: "DECISION_STALE", friendly: "NEEDS RECHECK" } : liveState.decisionB;
  const decisionC = simulationOn ? { ...liveState.decisionC, state: "DECISION_STALE", friendly: "NEEDS RECHECK" } : liveState.decisionC;
  const node = (key, item) => `<button class="live-node ${key} ${selectedNode === key ? "selected" : ""}" type="button" data-node="${key}"><div class="node-top"><span>${key === "source" ? "01 / SOURCE" : key === "decisionB" ? "02 / DECISION" : "03 / DECISION"}</span><b class="state ${stateClass(item.friendly)}">${esc(item.friendly)}</b></div><strong>${esc(item.title || item.question)}</strong></button>`;
  return `<div class="live-graph-canvas"><svg viewBox="0 0 1000 520" preserveAspectRatio="none" aria-hidden="true"><path class="graph-path ${simulationOn ? "orange" : ""}" d="M 290 240 C 410 250, 430 315, 505 330"/><path class="graph-path ${simulationOn ? "orange" : ""}" d="M 675 310 C 735 270, 760 205, 785 198"/></svg>${node("source", source)}${node("decisionB", decisionB)}${node("decisionC", decisionC)}</div>`;
}

function protocolDetails(item, id, kind = "") {
  return `<details class="protocol-details"><summary>View protocol details</summary><div class="technical-grid"><span>Canonical state</span><b>${esc(item.state || "—")}</b><span>Revision</span><b>${esc(item.revision ?? "—")}</b><span>Raw ID</span><b>${esc(id || item.id || "—")}</b>${kind === "source" && item.fingerprint ? `<span>Fingerprint</span><b>${esc(item.fingerprint)}</b>` : ""}${kind === "source" && item.last_content_hash ? `<span>Last content hash</span><b>${esc(item.last_content_hash)}</b>` : ""}</div></details>`;
}

function home() {
  return `${rootNav()}<main class="page"><section class="hero"><div class="hero-inner"><div class="eyebrow">Structural monitoring system / GenLayer</div><h1>When a fact <span class="accent">changes,</span><br />every decision<br />downstream<br /><span>should know.</span></h1><p class="hero-copy">Faultline tracks the facts your decisions depend on. When one changes, affected decisions become stale automatically.</p><div class="action-row"><a class="button primary" href="/app">Launch app <span aria-hidden="true">↗</span></a><a class="button dark-button" href="/app/live">See live proof</a></div><div class="hero-graph">${simpleGraph()}</div></div></section><section class="how-section reveal" id="how-it-works"><div class="section-grid"><div class="section-number">01 / How it works</div><div><h2 class="statement">A fact moves.<br /><span class="muted">The chain knows.</span></h2><p class="section-intro">Five simple steps keep decisions honest as the world changes.</p><div class="how-list"><div><span>01</span><h3>Track a fact</h3><p>Add a public source and describe the claim you want to monitor.</p></div><div><span>02</span><h3>Link a decision</h3><p>Create a decision and specify which facts or decisions it depends on.</p></div><div><span>03</span><h3>Check for change</h3><p>GenLayer checks whether the source changed materially.</p></div><div><span>04</span><h3>Faultline propagates</h3><p>If something upstream changes, every affected decision becomes stale.</p></div><div><span>05</span><h3>Recheck</h3><p>Re-evaluate only the decisions affected by the change.</p></div></div></div></div></section><section class="proof-teaser dark reveal"><div class="section-grid"><div class="section-number">02 / Live proof</div><div><h2 class="statement">See the<br />chain in<br /><span class="accent">motion.</span></h2><p class="section-intro">A real three-node example currently stored on GenLayer Studio Dev.</p><div class="proof-strip"><span>IANA SOURCE / CURRENT</span><span>DECISION B / VALID</span><span>DECISION C / VALID</span></div><div class="action-row"><a class="button dark-button" href="/app/live">See live proof <span aria-hidden="true">↗</span></a></div></div></div></section></main>${footer()}`;
}

function graphOverview() {
  return `<div class="overview-graph"><div><span>01 / SOURCE</span><strong>${esc(liveState.source.title)}</strong><b class="state ${stateClass(liveState.source.friendly)}">${esc(liveState.source.friendly)}</b></div><i>↓</i><div><span>02 / DECISION</span><strong>${esc(liveState.decisionB.title)}</strong><b class="state ${stateClass(liveState.decisionB.friendly)}">${esc(liveState.decisionB.friendly)}</b></div><i>↓</i><div><span>03 / DECISION</span><strong>${esc(liveState.decisionC.title)}</strong><b class="state ${stateClass(liveState.decisionC.friendly)}">${esc(liveState.decisionC.friendly)}</b></div></div>`;
}

function overview() {
  return `${appNav("overview")}<main class="app-page"><section class="app-intro overview-intro"><div class="eyebrow">Faultline</div><h1>Dependency workspace</h1><p>Track the facts your decisions rely on, then recheck only what changed.</p></section><section class="app-content"><div class="question-block"><div><span class="section-number">What do you want to do?</span></div><div class="choice-grid"><a class="choice" href="/app/sources?new=1"><span>01</span><strong>Track a new source</strong><small>Add a public fact to monitor.</small></a><a class="choice" href="/app/decisions?new=1"><span>02</span><strong>Create a decision</strong><small>Connect a choice to its dependencies.</small></a></div></div><div class="app-section-head"><span class="section-number">01 / Your graph</span><a href="/app/sources">View sources ↗</a></div>${graphOverview()}<div class="app-section-head recent-head"><span class="section-number">02 / Recent activity</span><a href="/app/activity">View all activity ↗</a></div>${activityMarkup(3)}</section></main>${footer()}`;
}

function sourceItems() {
  return [
    liveState.source,
    ...userSources.filter((item) => item.id !== liveState.source.id)
  ];
}

function decisionItems() {
  const liveIds = new Set([liveState.decisionB.id, liveState.decisionC.id]);
  return [
    liveState.decisionB,
    liveState.decisionC,
    ...userDecisions.filter((item) => !liveIds.has(item.id))
  ];
}
function dependentCount(id) { return decisionItems().filter((item) => (item.dependencies || []).includes(id)).length; }
function emptyState(title, copy, href, label) { return `<div class="empty-state"><h3>${title}</h3><p>${copy}</p><a class="button ink-button" href="${href}">${label}</a></div>`; }

function sourceForm() {
  const sourceId = `faultline-ui-source-${Date.now()}`;
  return `<form class="guided-form" data-operation="register_source"><div class="form-title"><span class="section-number">Track a source</span><h2>Track a source</h2><p>Add one public fact to the graph.</p></div><div class="form-fields"><div class="field"><label for="source-id">Source ID</label><input id="source-id" name="source_id" required value="${sourceId}" /></div><div class="field"><label for="source-url">Public URL</label><input id="source-url" name="url" type="url" required placeholder="https://example.com/source" /></div><div class="field"><label for="tracked-claim">What fact should Faultline track?</label><textarea id="tracked-claim" name="tracked_claim" rows="3" required placeholder="IANA maintains example domains for documentation purposes."></textarea><small>Write the claim as a clear sentence.</small></div></div><div class="form-actions"><button class="button ink-button" type="submit">Track source ↗</button><button class="text-button" type="button" data-close-form>Cancel</button></div></form>`;
}

function sourceRow(item) {
  const state = item.friendly || friendlyState(item.state);
  return `<a class="data-row" href="/app/source/${encodeURIComponent(item.id)}"><div><span class="row-kicker">Source</span><strong>${esc(item.title || item.claim || item.tracked_claim || item.id)}</strong><p>${esc(item.claim || item.tracked_claim || "Public fact being monitored")}</p></div><span class="state ${stateClass(state)}">${esc(state)}</span><span class="row-meta">Revision ${esc(item.revision ?? 0)}<br />${dependentCount(item.id)} dependent ${dependentCount(item.id) === 1 ? "decision" : "decisions"}</span><span class="row-arrow">↗</span></a>`;
}

function sourcesPage() {
  const params = new URLSearchParams(window.location.search);
  const showForm = params.get("new") === "1";
  const sourceList = sourceItems();
  const success = flash?.type === "source" ? `<div class="success-band"><span>Source added</span><strong>${esc(flash.message)}</strong><div><a class="button primary" href="/app/decisions?new=1&dependency=${encodeURIComponent(flash.id)}">Create a decision using this source</a><a class="text-button" href="/app/source/${encodeURIComponent(flash.id)}">View source</a></div></div>` : "";
  return `${appNav("sources")}<main class="app-page"><section class="app-intro compact"><div class="eyebrow">Sources / monitored facts</div><div class="page-title-row"><div><h1>Sources</h1><p>Sources are real-world facts your decisions rely on.</p></div><a class="button primary" href="/app/sources?new=1">+ Track source</a></div></section><section class="app-content">${success}${showForm ? sourceForm() : ""}${sourceList.length ? `<div class="data-list-head"><span>Source / claim</span><span>State</span><span>Revision / dependents</span></div><div class="data-list">${sourceList.map(sourceRow).join("")}</div>` : emptyState("No sources yet", "Start by tracking a public fact your decisions rely on.", "/app/sources?new=1", "Track your first source")}</section></main>${footer()}`;
}

function findSource(id) { return sourceItems().find((item) => item.id === id); }
function findDecision(id) { return decisionItems().find((item) => item.id === id); }

function sourceDetail(id) {
  const item = findSource(id);

  if (!item) {
    return `${appNav("sources")}<main class="app-page"><section class="app-intro compact"><div class="eyebrow">Source detail</div><div class="back-link"><a href="/app/sources">← Sources</a></div><h1>Source not found</h1><p>${walletAddress ? "This source could not be read from the connected wallet's contract records." : "Connect the wallet that created this source to load its contract record."}</p>${walletAddress ? "" : '<button class="button primary" type="button" data-connect-wallet>Connect wallet</button>'}</section></main>${footer()}`;
  }

  const dependents = decisionItems().filter((decision) => (decision.dependencies || []).includes(id));
  return `${appNav("sources")}<main class="app-page"><section class="app-intro compact"><div class="eyebrow">Source detail</div><div class="back-link"><a href="/app/sources">← Sources</a></div><h1>${esc(item.title || item.claim || "Source")}</h1><p>${esc(item.claim || item.tracked_claim || "Public fact being monitored")}</p></section><section class="app-content detail-content"><div class="detail-grid"><div><span class="detail-label">Status</span><strong class="big-state ${stateClass(item.friendly || friendlyState(item.state))}">${esc(item.friendly || friendlyState(item.state))}</strong></div><div><span class="detail-label">Revision</span><strong class="detail-value">${esc(item.revision ?? 0)}</strong></div><div class="wide-detail"><span class="detail-label">Public URL</span><a class="detail-url" href="${esc(item.url)}" target="_blank" rel="noreferrer">${esc(item.url || "No public URL")}</a></div></div><div class="detail-action-row"><button class="button ink-button" type="button" data-detail-action="check_source" data-detail-id="${esc(item.id)}">Check for change ↗</button><span class="form-note">GenLayer compares the current source against the tracked claim.</span></div><div class="detail-section"><div class="app-section-head"><span class="section-number">01 / Dependent decisions</span></div>${dependents.length ? `<div class="chain-list">${dependents.map((decision) => `<a href="/app/decision/${encodeURIComponent(decision.id)}"><span>Decision</span><strong>${esc(decision.title || decision.question)}</strong><b class="state ${stateClass(decision.friendly || friendlyState(decision.state))}">${esc(decision.friendly || friendlyState(decision.state))}</b></a>`).join("")}</div>` : emptyState("No dependent decisions yet", "Create a decision and connect it to this source.", `/app/decisions?new=1&dependency=${encodeURIComponent(item.id)}`, "Create a decision")}</div>${protocolDetails(item, item.id, "source")}</section></main>${footer()}`;
}

function dependencyOptions(selected = []) {
  return [...sourceItems().map((item) => ({ ...item, kind: "Source", label: item.title || item.claim })), ...decisionItems().map((item) => ({ ...item, kind: "Decision", label: item.title || item.question }))].map((item) => `<label class="dependency-option"><input type="checkbox" name="dependencies" value="${esc(item.id)}" ${selected.includes(item.id) ? "checked" : ""} /><span><small>${item.kind}</small><strong>${esc(item.label)}</strong><em class="state ${stateClass(item.friendly || friendlyState(item.state))}">${esc(item.friendly || friendlyState(item.state))}</em></span></label>`).join("");
}

function decisionForm() {
  const decisionId = `faultline-ui-decision-${Date.now()}`;
  const initial = new URLSearchParams(window.location.search).get("dependency");
  return `<form class="guided-form" data-operation="create_decision"><div class="form-title"><span class="section-number">Create a decision</span><h2>Create a decision</h2><p>Connect a choice to the facts it depends on.</p></div><div class="form-fields"><div class="field"><label for="decision-id">Decision ID</label><input id="decision-id" name="decision_id" required value="${decisionId}" /></div><div class="field"><label for="decision-question">What decision are you making?</label><textarea id="decision-question" name="question" rows="3" required placeholder="Should this decision continue to rely on the tracked claim?"></textarea></div><fieldset class="dependency-field"><legend>What does it depend on?</legend><div class="dependency-list">${dependencyOptions(initial ? [initial] : [])}</div><small>Select one or more existing sources or decisions.</small></fieldset></div><div class="form-actions"><button class="button ink-button" type="submit">Create decision ↗</button><button class="text-button" type="button" data-close-form>Cancel</button></div></form>`;
}

function decisionRow(item) {
  const state = item.friendly || friendlyState(item.state);
  const dependencyNames = (item.dependencies || []).map((id) => findSource(id)?.title || findDecision(id)?.title || short(id, 14, 5));
  return `<a class="data-row decision-row" href="/app/decision/${encodeURIComponent(item.id)}"><div><span class="row-kicker">Decision</span><strong>${esc(item.title || item.question || item.id)}</strong><p>${esc(item.question || "Decision with a dependency chain")}</p></div><span class="state ${stateClass(state)}">${esc(state)}</span><span class="row-meta">Depends on<br />${esc(dependencyNames.join(", ") || "No dependencies")}</span><span class="row-arrow">↗</span></a>`;
}

function decisionsPage() {
  const showForm = new URLSearchParams(window.location.search).get("new") === "1";
  const decisions = decisionItems();
  const success = flash?.type === "decision" ? `<div class="success-band"><span>Decision created</span><strong>This decision starts NEEDS RECHECK until it is checked.</strong><div><a class="button primary" href="/app/decision/${encodeURIComponent(flash.id)}">View decision</a><button class="text-button" type="button" data-recheck-created="${esc(flash.id)}">Recheck now</button></div></div>` : "";
  return `${appNav("decisions")}<main class="app-page"><section class="app-intro compact"><div class="eyebrow">Decisions / choices with dependencies</div><div class="page-title-row"><div><h1>Decisions</h1><p>Decisions stay useful when their dependencies stay visible.</p></div><a class="button primary" href="/app/decisions?new=1">+ Create decision</a></div></section><section class="app-content">${success}${showForm ? decisionForm() : ""}${decisions.length ? `<div class="data-list-head"><span>Question</span><span>Status</span><span>Dependencies</span></div><div class="data-list">${decisions.map(decisionRow).join("")}</div>` : emptyState("No decisions yet", "Create a decision and connect it to a source.", "/app/decisions?new=1", "Create your first decision")}</section></main>${footer()}`;
}

function decisionDetail(id) {
  const item = findDecision(id);

  if (!item) {
    return `${appNav("decisions")}<main class="app-page"><section class="app-intro compact"><div class="eyebrow">Decision detail</div><div class="back-link"><a href="/app/decisions">← Decisions</a></div><h1>Decision not found</h1><p>${walletAddress ? "This decision could not be read from the connected wallet's contract records." : "Connect the wallet that created this decision to load its contract record."}</p>${walletAddress ? "" : '<button class="button primary" type="button" data-connect-wallet>Connect wallet</button>'}</section></main>${footer()}`;
  }

  const dependencies = (item.dependencies || []).map((depId) => findSource(depId) || findDecision(depId)).filter(Boolean);
  const state = item.friendly || friendlyState(item.state);
  return `${appNav("decisions")}<main class="app-page"><section class="app-intro compact"><div class="eyebrow">Decision detail</div><div class="back-link"><a href="/app/decisions">← Decisions</a></div><h1>${esc(item.title || item.question || "Decision")}</h1><p>${esc(item.question || "Decision with a dependency chain")}</p></section><section class="app-content detail-content"><div class="detail-grid"><div><span class="detail-label">Current status</span><strong class="big-state ${stateClass(state)}">${esc(state)}</strong></div><div><span class="detail-label">Revision</span><strong class="detail-value">${esc(item.revision ?? 0)}</strong></div><div class="wide-detail"><span class="detail-label">Why this decision may be stale</span><p class="detail-explanation">${state === "VALID" ? "All tracked dependencies currently match the decision snapshot." : "One or more upstream dependencies changed. Recheck this decision to establish a fresh result."}</p></div></div><div class="detail-action-row"><button class="button ink-button" type="button" data-detail-action="recheck_decision" data-detail-id="${esc(item.id)}">Recheck decision ↗</button><span class="form-note">Only the current dependency snapshot is evaluated.</span></div><div class="detail-section"><div class="app-section-head"><span class="section-number">01 / Dependency chain</span></div><div class="chain-list">${dependencies.map((dependency) => `<a href="${dependency.id === LIVE_IDS.source || sourceItems().some((source) => source.id === dependency.id) ? `/app/source/${encodeURIComponent(dependency.id)}` : `/app/decision/${encodeURIComponent(dependency.id)}`}"><span>${dependency.id === LIVE_IDS.source || sourceItems().some((source) => source.id === dependency.id) ? "Source" : "Decision"}</span><strong>${esc(dependency.title || dependency.question)}</strong><b class="state ${stateClass(dependency.friendly || friendlyState(dependency.state))}">${esc(dependency.friendly || friendlyState(dependency.state))}</b></a>`).join("")}<div class="chain-arrow">↓</div><div class="chain-current"><span>Decision</span><strong>${esc(item.title || item.question)}</strong><b class="state ${stateClass(state)}">${esc(state)}</b></div></div></div>${protocolDetails(item, item.id)}</section></main>${footer()}`;
}

function activityMarkup(limit = 20) {
  const entries = activityLog.slice(0, limit);
  return entries.length ? `<div class="activity-list">${entries.map((entry) => `<div class="activity-row"><span class="activity-time">${esc(entry.time)}</span><div><strong>${esc(entry.label)}</strong><p>${esc(entry.detail)}</p></div><b class="state ${stateClass(entry.result)}">${esc(entry.result)}</b><details class="activity-details"><summary>Details</summary><span>Transaction ${esc(short(entry.tx, 18, 10))}</span></details></div>`).join("")}</div>` : emptyState("No activity yet", "Your source checks and decision rechecks will appear here.", "/app/sources?new=1", "Track your first source");
}

function activityPage() {
  return `${appNav("activity")}<main class="app-page"><section class="app-intro compact"><div class="eyebrow">Activity / what changed</div><h1>Activity</h1><p>Recent checks and decisions from your dependency graph.</p></section><section class="app-content"><div class="app-section-head"><span class="section-number">Recent activity</span></div>${activityMarkup()}</section></main>${footer()}`;
}

function livePage() {
  const selected = liveState[selectedNode] || liveState.source;
  const displaySelected = simulationOn ? { ...selected, state: selected.id === LIVE_IDS.source ? "SOURCE_CHANGED" : "DECISION_STALE", friendly: selected.id === LIVE_IDS.source ? "CHANGED" : "NEEDS RECHECK" } : selected;
  return `${rootNav("live")}<main class="page"><section class="route-intro"><div class="route-intro-inner"><div class="eyebrow">Reviewer route / live example</div><div class="route-title"><div><span class="live-label">LIVE EXAMPLE</span><h1>Live<br />proof</h1></div><p class="route-note">This is a real dependency chain currently stored on GenLayer Studio Dev.</p></div><div class="route-meta"><span><b>03</b> NODES</span><span><b>02</b> EDGES</span><span><b>STUDIO DEV</b> / LIVE</span></div></div></section><section class="route-body dark-route"><div class="live-proof-intro"><span>IANA SOURCE</span><b>CURRENT</b><i>↓ depends on</i><span>DECISION B</span><b>VALID</b><i>↓ depends on</i><span>DECISION C</span><b>VALID</b></div><div class="live-graph-wrap">${liveGraphMarkup()}</div><div class="refresh-line"><span data-live-readout>${esc(liveState.readAt)}</span><button class="button dark-button" type="button" data-refresh-live>Refresh live example</button></div><div class="live-drawer"><div><span class="eyebrow">Selected node</span><h2>${esc(displaySelected.title || displaySelected.question)}</h2><p>${esc(displaySelected.claim || displaySelected.question || "")}</p></div><div>${protocolDetails(displaySelected, displaySelected.id, displaySelected.id === LIVE_IDS.source ? "source" : "")}</div></div><div class="action-row"><a class="button primary" href="/app">Open in app ↗</a><button class="button dark-button" type="button" data-simulate>${simulationOn ? "Reset simulation" : "Simulate source change"}</button><span class="simulation-note">SIMULATION ONLY / NO ONCHAIN WRITE</span></div></section></main>${footer()}`;
}

function routeInfo() {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  if (path === "/app") return { name: "overview" };
  if (path === "/app/sources") return { name: "sources" };
  if (path === "/app/decisions") return { name: "decisions" };
  if (path === "/app/activity") return { name: "activity" };
  if (path === "/app/live") return { name: "live" };
  if (path.startsWith("/app/source/")) return { name: "source-detail", id: decodeURIComponent(path.slice("/app/source/".length)) };
  if (path.startsWith("/app/decision/")) return { name: "decision-detail", id: decodeURIComponent(path.slice("/app/decision/".length)) };
  return { name: "home" };
}

function pageMarkup() {
  const route = routeInfo();
  if (route.name === "overview") return overview();
  if (route.name === "sources") return sourcesPage();
  if (route.name === "source-detail") return sourceDetail(route.id);
  if (route.name === "decisions") return decisionsPage();
  if (route.name === "decision-detail") return decisionDetail(route.id);
  if (route.name === "activity") return activityPage();
  if (route.name === "live") return livePage();
  return home();
}

function draw() {
  const route = routeInfo();
  document.body.dataset.route = route.name;
  document.getElementById("app").innerHTML = pageMarkup();
  bindInteractions(route);
  requestAnimationFrame(() => document.querySelectorAll(".reveal").forEach((element) => element.classList.add("is-visible")));
}

async function connectWallet() {
  if (!window.ethereum) throw new Error("No browser wallet detected. Install or open a wallet that supports EIP-1193.");
  const accounts = await window.ethereum.request({ method: "eth_requestAccounts" });
  if (!accounts?.[0]) throw new Error("Wallet returned no account.");
  walletAddress = accounts[0];
  writeClient = createClient({ chain: studioDevnet, account: walletAddress, provider: window.ethereum });
  await refreshUserRecords();
  draw();
}

function addActivity(label, result, detail, tx = "") {
  activityLog.unshift({ label, result, detail, tx: tx || PROOF_TX, time: now() });
}

async function submitWrite(method, args, label, afterSuccess) {
  if (!writeClient) await connectWallet();
  const txHash = await writeClient.writeContract({ address: CONTRACT, functionName: method, args, value: BigInt(0) });
  addActivity(label, "SUBMITTED", "Waiting for Studio Dev consensus", txHash);
  draw();
  const receipt = await readClient.waitForTransactionReceipt({ hash: txHash, waitUntil: "decided", fullTransaction: true });
  if (!isSuccessful(receipt)) {
    const status = receipt?.statusName || receipt?.status || "UNKNOWN";
    const execution = receipt?.txExecutionResultName || receipt?.txExecutionResult || "UNKNOWN";
    throw new Error(`Studio Dev transaction did not finish with a return (status=${status}, execution=${execution}).`);
  }
  const result = receipt?.txExecutionResultName || receipt?.tx_execution_result || receipt?.status || "DECIDED";
  addActivity(label, friendlyState(result), "Studio Dev consensus accepted", txHash);

  if (method === "check_source" || method === "recheck_decision") {
    await loadLiveState(true);
  }

  await refreshUserRecords();

  if (afterSuccess) await afterSuccess(txHash, result);
}

function handleOperation(form) {
  const method = form.dataset.operation;
  const values = Object.fromEntries(new FormData(form).entries());
  const dependencies = [...form.querySelectorAll("input[name=dependencies]:checked")].map((input) => input.value);
  const args = method === "register_source" ? [values.source_id, values.url, values.tracked_claim] : [values.decision_id, values.question, dependencies];
  const button = form.querySelector("button[type=submit]");
  if (button) { button.disabled = true; button.textContent = "Working…"; }
  submitWrite(method, args, method === "register_source" ? "Track source" : "Create decision", () => {
    if (method === "register_source") {
      flash = {
        type: "source",
        id: values.source_id,
        message: "Your source was read back from the contract."
      };
      window.history.replaceState({}, "", `/app/sources?created=${encodeURIComponent(values.source_id)}`);
    } else {
      flash = { type: "decision", id: values.decision_id };
      window.history.replaceState({}, "", `/app/decisions?created=${encodeURIComponent(values.decision_id)}`);
    }

    draw();
  }).catch((error) => {
    addActivity(method, "ERROR", error?.message || String(error));
    draw();
  });
}

function bindInteractions(route) {
  document.querySelectorAll("[data-menu-toggle]").forEach((button) => button.addEventListener("click", () => { const nav = document.querySelector("[data-app-links]"); const open = nav?.classList.toggle("is-open"); button.setAttribute("aria-expanded", String(Boolean(open))); }));
  document.querySelectorAll("[data-connect-wallet]").forEach((button) => button.addEventListener("click", () => connectWallet().catch((error) => { addActivity("Wallet", "ERROR", error?.message || String(error)); draw(); })));
  document.querySelectorAll("[data-operation]").forEach((form) => form.addEventListener("submit", (event) => { event.preventDefault(); handleOperation(form); }));
  document.querySelectorAll("[data-close-form]").forEach((button) => button.addEventListener("click", () => { window.history.pushState({}, "", route.name === "sources" ? "/app/sources" : "/app/decisions"); draw(); }));
  document.querySelectorAll("[data-detail-action]").forEach((button) => button.addEventListener("click", () => { const method = button.dataset.detailAction; submitWrite(method, [button.dataset.detailId], method === "check_source" ? "Check source" : "Recheck decision", () => draw()).catch((error) => { addActivity(method, "ERROR", error?.message || String(error)); draw(); }); }));
  document.querySelectorAll("[data-recheck-created]").forEach((button) => button.addEventListener("click", () => { submitWrite("recheck_decision", [button.dataset.recheckCreated], "Recheck decision", () => { flash = null; window.history.pushState({}, "", `/app/decision/${encodeURIComponent(button.dataset.recheckCreated)}`); draw(); }).catch((error) => { addActivity("Recheck decision", "ERROR", error?.message || String(error)); draw(); }); }));
  document.querySelectorAll("[data-node]").forEach((button) => button.addEventListener("click", () => { selectedNode = button.dataset.node; draw(); }));
  document.querySelectorAll("[data-simulate]").forEach((button) => button.addEventListener("click", () => { simulationOn = !simulationOn; draw(); }));
  document.querySelectorAll("[data-refresh-live]").forEach((button) => button.addEventListener("click", async () => { button.disabled = true; button.textContent = "Reading…"; try { await loadLiveState(true); } catch (error) { addActivity("Live read", "ERROR", error?.message || String(error)); draw(); } }));
}

window.addEventListener("popstate", draw);
document.addEventListener("click", (event) => {
  const link = event.target.closest("a[href^='/']");
  if (!link || link.target === "_blank" || link.getAttribute("href").startsWith("/#")) return;
  const url = new URL(link.href);
  if (url.origin !== window.location.origin) return;
  event.preventDefault();
  window.history.pushState({}, "", url.pathname + url.search + url.hash);
  flash = null;
  draw();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

draw();
loadLiveState().catch(() => {});
