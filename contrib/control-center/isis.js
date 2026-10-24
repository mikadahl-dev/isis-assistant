/* Reiter ISIS – Uni-Daten aus isis-api (über das Dashboard-Backend: /api/isis/<pfad>): Übersicht, Kurse & Dateien, Wissen (Vault), Chat.
   Alles lesend; der Chat beantwortet Fragen aus Notizen, Folientext und ISIS-Terminen. Wird vor app.js geladen. */
"use strict";

const iapi = async (path) => {
  const r = await fetch(`/api/isis/${path}`, { cache: "no-store" });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
};
const IS_MS = 60000;
const IS_SUB = [["over", "Übersicht"], ["files", "Kurse & Dateien"], ["wiki", "Wissen"], ["ask", "Chat"]];
const IS_TYPES = { begriff: "Begriff", formel: "Formel", verfahren: "Verfahren", beispiel: "Beispiel", termin: "Termin" };
const IS_BELEG = { ja: ["ok", "belegt"], teilweise: ["warn", "teilweise belegt"], unsicher: ["crit", "prüfen"] };
const IS = { sub: "over", status: null, courses: [], events: [], err: null, at: 0, busy: false,
             fcourse: null, files: {}, wcourse: null, vault: {}, note: null, q: "", hits: null, type: "", graph: false };
try { IS.sub = localStorage.getItem("hl-isis-sub") || "over"; } catch { /* egal */ }
if (!IS_SUB.some(([k]) => k === IS.sub)) IS.sub = "over";

const fileUrl = (key, page) => `/api/isis/file?key=${encodeURIComponent(key)}${page ? `#page=${page}` : ""}`;
const WD = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
function when(ts) {
  const d = new Date(ts * 1000);
  const hm = d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  return `${WD[d.getDay()]} ${d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" })}${hm === "00:00" ? "" : ` ${hm}`}`;
}
function until(ts) {
  const days = Math.round((new Date(ts * 1000).setHours(0, 0, 0, 0) - new Date().setHours(0, 0, 0, 0)) / 864e5);
  return days < 0 ? "vorbei" : days === 0 ? "heute" : days === 1 ? "morgen" : `in ${days} Tagen`;
}
const stars = (n) => "★".repeat(n || 1);
const isNew = (f) => Date.now() / 1000 - (f.changed_at || f.first_seen || 0) < 7 * 86400;

// ================================================================ Laden + Rahmen
function initIsis() {
  const v = $("#view-isis");
  if (!v.dataset.ready) {
    v.dataset.ready = "1";
    v.innerHTML = `<div class="is-subnav" role="tablist">${IS_SUB.map(([k, l]) =>
      `<button type="button" class="chip" data-is-sub="${k}">${l}</button>`).join("")}</div>
      <div id="is-over"></div><div id="is-files" hidden></div><div id="is-wiki" hidden></div><div id="is-ask" hidden></div>`;
    v.addEventListener("click", isisClick);
    v.addEventListener("submit", isisSubmit);
    v.addEventListener("input", (ev) => { if (ev.target.id === "is-q") isisSearch(ev.target.value); });
  }
  v.querySelectorAll("[data-is-sub]").forEach((b) => b.classList.toggle("on", b.dataset.isSub === IS.sub));
  for (const [k] of IS_SUB) $(`#is-${k}`).hidden = k !== IS.sub;
}
async function loadIsis(force = false) {
  initIsis();
  if (IS.busy || (!force && Date.now() - IS.at < IS_MS && IS.status)) return drawIsis(false);
  IS.busy = true;
  try {
    [IS.status, IS.courses, IS.events] = await Promise.all([iapi("status"), iapi("courses"), iapi("events")]);
    IS.err = null;
  } catch (e) {
    IS.err = e.message;
  } finally {
    IS.at = Date.now();
    IS.busy = false;
  }
  drawIsis(true);
}
function drawIsis(fresh) {
  if (currentTab() !== "isis") return;
  initIsis();
  if (IS.sub === "over" && (fresh || !$("#is-over").innerHTML)) safe($("#is-over"), isisOver);
  if (IS.sub === "files") isisFilesView(false); // Listen nur beim Kurswechsel neu zeichnen (Suche/Fokus bleiben)
  if (IS.sub === "wiki") isisWikiView(false);
  if (IS.sub === "ask") isisAskInit();
}
const courseById = (id) => IS.courses.find((c) => c.id === id);
const withNodes = () => IS.courses.filter((c) => c.nodes > 0);
function courseChips(attr, cur, list = IS.courses) {
  return `<div class="chips is-left">${list.map((c) => `<button type="button" class="chip ${c.id === cur ? "on" : ""}" ${attr}="${c.id}"
    title="${esc(c.name)}">${esc(c.short)}</button>`).join("")}</div>`;
}

// ================================================================ Übersicht
function isisOver() {
  if (IS.err && !IS.status) return `<div class="card"><div class="err-box">ISIS nicht erreichbar: ${esc(IS.err)}</div></div>`;
  if (!IS.status) return `<div class="card"><div class="sub">ISIS wird geladen …</div></div>`;
  const s = IS.status, run = s.last_run || {};
  const runOk = run.ok === 1;
  const head = `<div class="card"><div class="card-head"><h2>ISIS (TU Berlin)</h2>
      ${pill(!s.token ? "down" : runOk ? "up" : "warn", !s.token ? "Token fehlt" : runOk ? "Abgleich ok" : "letzter Abgleich fehlgeschlagen")}
      <span class="right">Abgleich alle 2 h · zuletzt ${esc(ago(run.finished || run.started))}</span></div>
    <div class="kpis">${kpi("Kurse", num(s.courses))}${kpi("Dateien", num(s.files), `${bytes(s.bytes)} auf der HDD`)}
      ${kpi("Offene Abgaben", num(s.open_tasks), `${num(s.events_upcoming)} Termine kommen`)}
      ${kpi("Wissensnetz", num(s.nodes), `${num(s.edges)} Verweise · ${num(s.chunks)} Häppchen`)}</div>
    ${run.message ? `<div class="sub">${esc(run.message)}</div>` : ""}
    ${!s.token ? `<div class="err-box">Kein ISIS-Token – auf dem isis-Server <code>isis login</code> ausführen.</div>` : ""}
    ${IS.err ? `<div class="err-box">${esc(IS.err)}</div>` : ""}</div>`;
  const now = Date.now() / 1000;
  const ev = IS.events.filter((e) => e.timestart >= now - 86400);
  const open = ev.filter((e) => e.actionable);
  const row = (e) => `<li class="${e.actionable ? "is-due" : ""}"><span class="is-when">${esc(when(e.timestart))}</span>
      <span class="is-until">${esc(until(e.timestart))}</span>
      <a href="${esc(e.url || "#")}" target="_blank" rel="noopener">${esc(e.name)}</a> <span class="muted">${esc(e.course)}</span>
      ${e.actionable ? `<span class="pill crit">Abgabe offen</span>` : ""}</li>`;
  const dues = open.length ? `<div class="card"><div class="card-head"><h2>Offene Abgaben</h2><span class="right">ISIS erwartet noch etwas von dir</span></div>
      <ul class="is-ev">${open.map(row).join("")}</ul></div>` : "";
  const cal = `<div class="card"><div class="card-head"><h2>Termine</h2><span class="right">aus dem ISIS-Kalender · auch als isis.ics auf der HDD</span></div>
      ${ev.length ? `<ul class="is-ev">${ev.slice(0, 40).map(row).join("")}</ul>` : `<div class="sub">Keine Termine.</div>`}</div>`;
  const cards = `<div class="card"><div class="card-head"><h2>Kurse</h2></div><div class="is-courses">${IS.courses.map((c) => `
      <div class="is-course"><a href="${esc(c.url)}" target="_blank" rel="noopener"><b>${esc(c.short)}</b></a>
        <div class="sub">${esc(c.name)}</div>
        <div class="sub">${num(c.files)} Dateien · ${num(c.links)} Links${c.nodes ? ` · <a href="#isis" data-is-wiki="${c.id}">${num(c.nodes)} Notizen</a>` : ""}</div>
        ${c.next ? `<div class="sub ${c.next.actionable ? "is-due-t" : ""}">Nächstes: ${esc(when(c.next.timestart))} – ${esc(c.next.name)}</div>` : ""}
        <button type="button" class="btn" data-is-files="${c.id}">Dateien</button></div>`).join("")}</div></div>`;
  return head + dues + cal + cards;
}

// ================================================================ Kurse & Dateien
async function isisFilesView(fresh) {
  if (!IS.courses.length) return safe($("#is-files"), isisOver);
  if (!IS.fcourse) IS.fcourse = IS.courses[0].id;
  const cid = IS.fcourse;
  if (!IS.files[cid]) {
    safe($("#is-files"), () => `<div class="card">${courseChips("data-is-fc", cid)}<div class="sub">Dateien werden geladen …</div></div>`);
    try { IS.files[cid] = await iapi(`files?course=${cid}`); } catch (e) { IS.files[cid] = { error: e.message }; }
  } else if (!fresh && $("#is-files").innerHTML) return;
  safe($("#is-files"), () => isisFiles(cid));
}
function isisFiles(cid) {
  const d = IS.files[cid] || {}, c = courseById(cid) || {};
  if (d.error) return `<div class="card">${courseChips("data-is-fc", cid)}<div class="err-box">${esc(d.error)}</div></div>`;
  const groups = {};
  for (const f of d.files || []) (groups[f.section || "Allgemein"] ||= []).push(f);
  for (const l of d.links || []) (groups[l.section || "Allgemein"] ||= []).push({ link: true, ...l });
  const item = (f) => f.link
    ? `<li><span class="is-ico">↗</span><a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.name)}</a> <span class="muted">Link</span></li>`
    : `<li><span class="is-ico">${/pdf/.test(f.mimetype || "") ? "📄" : "📎"}</span>${f.local
        ? `<a href="${fileUrl(f.key)}" target="_blank" rel="noopener">${esc(f.filename)}</a>` : esc(f.filename)}
        <span class="muted">${f.module && f.module !== f.filename ? `${esc(f.module)} · ` : ""}${bytes(f.filesize)}${f.timemodified ? ` · ${esc(new Date(f.timemodified * 1000).toLocaleDateString("de-DE"))}` : ""}</span>
        ${isNew(f) ? `<span class="pill info">${f.changed_at ? "geändert" : "neu"}</span>` : ""}
        ${f.error ? `<span class="pill crit" title="${esc(f.error)}">Fehler</span>` : !f.local ? `<span class="pill unknown">nicht geladen</span>` : ""}</li>`;
  const secs = Object.entries(groups).map(([s, l]) => `<h3>${esc(s)}</h3><ul class="is-files">${l.map(item).join("")}</ul>`).join("");
  return `<div class="card">${courseChips("data-is-fc", cid)}<div class="card-head"><h2>${esc(c.name || "")}</h2>
      <span class="right"><a href="${esc(c.url || "#")}" target="_blank" rel="noopener">in ISIS öffnen ↗</a></span></div>
    ${secs || `<div class="sub">Keine Dateien in diesem Kurs.</div>`}</div>`;
}

// ================================================================ Wissen (Vault)
async function isisWikiView(fresh) {
  const list = withNodes();
  if (!list.length) return safe($("#is-wiki"), () => `<div class="card"><div class="sub">${IS.status ? "Noch keine Notizen – das Wissensnetz wird mit <code>isis wissen build</code> gebaut." : "ISIS wird geladen …"}</div></div>`);
  if (!IS.wcourse || !list.some((c) => c.id === IS.wcourse)) IS.wcourse = list[0].id;
  const cid = IS.wcourse;
  if (!IS.vault[cid]) {
    try { IS.vault[cid] = await iapi(`vault?course=${cid}`); } catch (e) { IS.vault[cid] = { error: e.message }; }
  } else if (!fresh && $("#is-wiki").innerHTML) return;
  safe($("#is-wiki"), isisWiki);
}
function isisWiki() {
  const cid = IS.wcourse, v = IS.vault[cid] || {};
  if (v.error) return `<div class="card">${courseChips("data-is-wc", cid, withNodes())}<div class="err-box">${esc(v.error)}</div></div>`;
  const types = [...new Set((v.nodes || []).map((n) => n.type))];
  const shown = (IS.hits ? IS.hits.notes.filter((h) => h.courseid === cid).map((h) => (v.nodes || []).find((n) => n.id === h.id)).filter(Boolean) : v.nodes || [])
    .filter((n) => !IS.type || n.type === IS.type);
  const li = (n) => `<li><a href="#isis" data-is-note="${n.id}" class="${IS.note && IS.note.id === n.id ? "on" : ""}">${esc(n.title)}</a>
      <span class="is-star" title="Wichtigkeit">${stars(n.importance)}</span>${n.status === "unsicher" ? `<span class="is-warn" title="jeff hält das für nicht belegt">⚠</span>` : ""}</li>`;
  const pages = IS.hits && IS.hits.pages.length ? `<h3>Im Folientext</h3><ul class="is-hits">${IS.hits.pages.map((p) =>
    `<li><a href="${fileUrl(p.file_key, p.page)}" target="_blank" rel="noopener">${esc(p.file)} S. ${p.page}</a> <span class="muted">${esc(p.course)}</span>
      <div class="sub">${esc(p.text.slice(0, 180))} …</div></li>`).join("")}</ul>` : "";
  const side = `<div class="is-side">
      <input type="search" id="is-q" placeholder="Suchen in Notizen und Folien …" value="${esc(IS.q)}" aria-label="Suche">
      <div class="chips is-left is-types"><button type="button" class="chip ${IS.type ? "" : "on"}" data-is-type="">alle</button>${types.map((t) =>
        `<button type="button" class="chip ${IS.type === t ? "on" : ""}" data-is-type="${t}">${esc(IS_TYPES[t] || t)}</button>`).join("")}</div>
      <ul class="is-notes">${shown.map(li).join("") || `<li class="sub">Nichts gefunden.</li>`}</ul>${pages}</div>`;
  const main = IS.graph ? `<div class="is-graph" id="is-graph">${isisGraph(v)}</div>` : isisNote();
  return `<div class="card">${courseChips("data-is-wc", cid, withNodes())}
    <div class="card-head"><h2>Wissensnetz</h2><span class="right">${num((v.nodes || []).length)} Notizen · ${num((v.edges || []).length)} Verbindungen</span>
      <button type="button" class="btn" data-is-graph="1">${IS.graph ? "Notiz" : "Graph"}</button></div>
    <div class="is-wiki">${side}<div class="is-main">${main}</div></div>
    <div class="sub">Vom 9B aus den Folien gezogen, Zitate per Code geprüft, jeff markiert Zweifelhaftes (⚠). Dieselben Notizen liegen als Obsidian-Vault auf der HDD (<code>ISIS/wissen</code>).</div></div>`;
}
function isisNote() {
  const n = IS.note;
  if (!n) return `<div class="is-empty sub">Notiz links auswählen – oder oben auf „Graph“.</div>`;
  if (n.loading) return `<div class="sub">Lädt …</div>`;
  if (n.error) return `<div class="err-box">${esc(n.error)}</div>`;
  const [st, label] = IS_BELEG[n.status] || IS_BELEG.ja;
  const chips = (l) => l.map((x) => `<a href="#isis" class="chip" data-is-note="${x.id}">${esc(x.title)}</a>`).join("");
  const src = n.sources.map((s) => `<blockquote>${esc(s.quote)}<footer><a href="${fileUrl(s.file_key, s.page)}" target="_blank" rel="noopener">${esc(s.file)}${s.page ? `, S. ${s.page}` : ""}</a>
      ${s.grounded && !/^supported/.test(s.grounded) ? `<span class="muted">· jeff: ${esc(s.grounded)}</span>` : ""}</footer></blockquote>`).join("");
  return `<article class="is-note">
    <h2>${esc(n.title)}</h2>
    <div class="is-meta">${pill("unknown", IS_TYPES[n.type] || n.type)} <span class="is-star">${stars(n.importance)}</span> ${pill(st, label)}</div>
    ${n.status === "unsicher" ? `<div class="err-box">jeff hält das für nicht durch die Folie belegt – Zitat unten selbst prüfen.</div>` : ""}
    <p>${esc(n.content)}</p>
    <h3>Belege</h3>${src}
    ${n.links.length ? `<h3>Verwandt</h3><div class="chips is-left">${chips(n.links)}</div>` : ""}
    ${n.backlinks.length ? `<h3>Verweist hierher</h3><div class="chips is-left">${chips(n.backlinks)}</div>` : ""}
    ${n.also.length ? `<h3>Auch in</h3><div class="chips is-left">${n.also.map((x) => `<a href="#isis" class="chip" data-is-note="${x.id}">${esc(x.course)}: ${esc(x.title)}</a>`).join("")}</div>` : ""}
  </article>`;
}
async function isisOpenNote(id) {
  IS.note = { id, loading: true };
  IS.graph = false;
  safe($("#is-wiki"), isisWiki);
  try { IS.note = await iapi(`node?id=${id}`); } catch (e) { IS.note = { id, error: e.message }; }
  if (IS.note.courseid && IS.note.courseid !== IS.wcourse) { IS.wcourse = IS.note.courseid; return isisWikiView(true); }
  safe($("#is-wiki"), isisWiki);
  if (matchMedia("(max-width: 760px)").matches) $(".is-main").scrollIntoView({ behavior: "smooth", block: "start" });
}
let isTimer = 0;
function isisSearch(q) {
  IS.q = q;
  clearTimeout(isTimer);
  isTimer = setTimeout(async () => {
    if (q.trim().length < 3) IS.hits = null;
    else { try { IS.hits = await iapi(`search?q=${encodeURIComponent(q.trim())}`); } catch (e) { IS.hits = { notes: [], pages: [], error: e.message }; } }
    if (IS.q !== q) return;
    safe($("#is-wiki"), isisWiki);
    const inp = $("#is-q"); inp.focus(); inp.setSelectionRange(q.length, q.length);
  }, 350);
}

// Graph: kleine Kräfte-Simulation (einmal durchrechnen, dann als SVG zeichnen). Klick auf einen Punkt öffnet die Notiz.
const isLayout = {};
function isisGraph(v) {
  const nodes = v.nodes || [], edges = v.edges || [];
  if (!nodes.length) return `<div class="sub">Keine Notizen.</div>`;
  const key = `${IS.wcourse}:${nodes.length}:${edges.length}`;
  let P = isLayout[key];
  if (!P) {
    const W = 1000, H = 700, idx = new Map(nodes.map((n, i) => [n.id, i]));
    P = nodes.map((n, i) => ({ x: W / 2 + 300 * Math.cos(i * 2.4) * Math.sqrt(i / nodes.length), y: H / 2 + 220 * Math.sin(i * 2.4) * Math.sqrt(i / nodes.length), vx: 0, vy: 0 }));
    const E = edges.map(([a, b]) => [idx.get(a), idx.get(b)]).filter(([a, b]) => a != null && b != null);
    for (let it = 0; it < 400; it++) {
      const k = 1 - it / 400;
      for (let i = 0; i < P.length; i++) for (let j = i + 1; j < P.length; j++) {
        const dx = P[i].x - P[j].x, dy = P[i].y - P[j].y, d2 = dx * dx + dy * dy + 0.01, f = 2200 / d2;
        P[i].vx += dx * f; P[i].vy += dy * f; P[j].vx -= dx * f; P[j].vy -= dy * f;
      }
      for (const [a, b] of E) {
        const dx = P[b].x - P[a].x, dy = P[b].y - P[a].y, f = 0.02;
        P[a].vx += dx * f; P[a].vy += dy * f; P[b].vx -= dx * f; P[b].vy -= dy * f;
      }
      for (const p of P) {
        p.vx += (W / 2 - p.x) * 0.004; p.vy += (H / 2 - p.y) * 0.004;
        p.x += Math.max(-20, Math.min(20, p.vx)) * k; p.y += Math.max(-20, Math.min(20, p.vy)) * k;
        p.vx *= 0.5; p.vy *= 0.5;
      }
    }
    const xs = P.map((p) => p.x), ys = P.map((p) => p.y), x0 = Math.min(...xs), y0 = Math.min(...ys);
    const s = Math.min(940 / (Math.max(...xs) - x0 || 1), 640 / (Math.max(...ys) - y0 || 1));
    P = P.map((p) => ({ x: 30 + (p.x - x0) * s, y: 30 + (p.y - y0) * s }));
    P.E = E;
    isLayout[key] = P;
  }
  const deg = nodes.map(() => 0);
  for (const [a, b] of P.E) { deg[a]++; deg[b]++; }
  const lines = P.E.map(([a, b]) => `<line x1="${P[a].x.toFixed(1)}" y1="${P[a].y.toFixed(1)}" x2="${P[b].x.toFixed(1)}" y2="${P[b].y.toFixed(1)}"/>`).join("");
  const dots = nodes.map((n, i) => `<g class="is-gn t-${esc(n.type)}${n.status === "unsicher" ? " doubt" : ""}" data-is-note="${n.id}">
      <circle cx="${P[i].x.toFixed(1)}" cy="${P[i].y.toFixed(1)}" r="${(4 + Math.min(deg[i], 8) + n.importance).toFixed(1)}"><title>${esc(n.title)}</title></circle>
      ${deg[i] >= 3 || n.importance >= 3 ? `<text x="${P[i].x.toFixed(1)}" y="${(P[i].y - 9 - Math.min(deg[i], 8)).toFixed(1)}">${esc(n.title)}</text>` : ""}</g>`).join("");
  const legend = Object.entries(IS_TYPES).map(([t, l]) => `<span><i class="is-gl t-${t}"></i>${l}</span>`).join("");
  return `<svg viewBox="0 0 1000 700" role="img" aria-label="Graph der Notizen"><g class="is-ge">${lines}</g>${dots}</svg><div class="is-legend sub">${legend}</div>`;
}

// ================================================================ Chat (Fragen an Notizen, Folien und Termine)
const IS_CHAT_KEY = "hl-isis-chat-v1";
const IS_EXAMPLES = ["Wann ist meine nächste Abgabe?", "Erklär mir den wichtigsten Begriff der letzten Vorlesung", "Welche Formeln brauche ich für das Übungsblatt?", "Was steht diese Woche an?"];
let isMsgs = [], isBusy = null;
try { isMsgs = JSON.parse(sessionStorage.getItem(IS_CHAT_KEY) || "[]"); } catch { isMsgs = []; }
const isSave = () => { try { sessionStorage.setItem(IS_CHAT_KEY, JSON.stringify(isMsgs.slice(-30))); } catch { /* egal */ } };
function isCite(html, refs) {
  return html.replace(/\[(\d{1,2})\]/g, (m, n) => {
    const r = (refs || []).find((x) => x.n === Number(n));
    return r ? `<a href="#isis" class="is-cite" data-is-ref="${n}" title="${esc(r.label)}">${n}</a>` : m;
  });
}
function isRefLink(r) {
  if (r.kind === "note") return `<a href="#isis" data-is-note="${r.id}">${esc(r.label)}</a>`;
  if (r.kind === "page") return `<a href="${fileUrl(r.file_key, r.page)}" target="_blank" rel="noopener">${esc(r.label)}</a>`;
  return `<a href="#isis" data-is-sub="over">${esc(r.label)}</a>`;
}
function isMsgHtml(m, i) {
  if (m.role === "user") return `<div class="cmsg user"><div class="bubble">${esc(m.content).replace(/\n/g, "<br>")}</div></div>`;
  const live = isBusy && i === isMsgs.length - 1;
  const used = new Set([...(m.content || "").matchAll(/\[(\d{1,2})\]/g)].map((x) => Number(x[1])));
  const refs = (m.refs || []).filter((r) => used.has(r.n));
  return `<div class="cmsg bot" data-i="${i}"><div class="bubble">
    ${m.content ? `<div class="md">${isCite(md(m.content), m.refs)}</div>` : live ? `<div class="ctool run">Sucht in Notizen und Folien, dann antwortet qwen3.5:9b …</div>` : ""}
    ${refs.length ? `<ol class="is-refs">${refs.map((r) => `<li value="${r.n}">${isRefLink(r)}</li>`).join("")}</ol>` : ""}
    ${m.error ? `<div class="err-box">${esc(m.error)}</div>` : ""}${m.stats ? `<div class="sub">${esc(m.stats)}</div>` : ""}</div></div>`;
}
function isRender() {
  const log = $("#is-log");
  if (!log) return;
  const near = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  log.innerHTML = isMsgs.length ? isMsgs.map(isMsgHtml).join("")
    : `<div class="chat-empty"><p>Frag mich etwas zu deinen Kursen. Ich antworte nur aus deinen Notizen, dem Folientext und den ISIS-Terminen und nenne die Quellen.</p>
       <div class="chips">${IS_EXAMPLES.map((q) => `<button type="button" class="chip" data-is-q="${esc(q)}">${esc(q)}</button>`).join("")}</div></div>`;
  if (near) log.scrollTop = log.scrollHeight;
  $("#is-send").textContent = isBusy ? "Stopp" : "Senden";
  $("#is-send").classList.toggle("stop", !!isBusy);
}
function isisAskInit() {
  const box = $("#is-ask");
  if (box.dataset.ready) return;
  box.dataset.ready = "1";
  box.innerHTML = `<div class="card chat">
    <div class="card-head"><h2>Uni-Chat</h2><span class="right">lokales Modell · antwortet aus Notizen, Folien und Terminen</span>
      <button type="button" class="btn" id="is-new">Neuer Chat</button></div>
    <div class="chat-log" id="is-log" aria-live="polite"></div>
    <form class="chat-form" id="is-form">
      <textarea id="is-in" rows="2" maxlength="2000" placeholder="Frage zu deinen Kursen … (Enter senden, Shift+Enter neue Zeile)" aria-label="Frage"></textarea>
      <button type="submit" class="btn primary" id="is-send">Senden</button>
    </form>
    <div class="sub">Die Antworten können falsch sein – über die Nummern kommst du direkt zur Folie.</div></div>`;
  $("#is-in").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); if (!isBusy) isAsk($("#is-in").value); }
  });
  $("#is-new").addEventListener("click", () => { if (isBusy) isBusy.abort(); isMsgs = []; isSave(); isRender(); $("#is-in").focus(); });
  isRender();
  $("#is-log").scrollTop = $("#is-log").scrollHeight;
}
async function isAsk(q) {
  q = (q || "").trim();
  if (!q || isBusy) return;
  $("#is-in").value = "";
  isMsgs.push({ role: "user", content: q });
  const bot = { role: "assistant", content: "", refs: [] };
  isMsgs.push(bot);
  isBusy = new AbortController();
  isRender();
  $("#is-log").scrollTop = $("#is-log").scrollHeight;
  const history = isMsgs.slice(0, -1).filter((m) => m.content).map((m) => ({ role: m.role, content: m.content })).slice(-6);
  const t0 = Date.now();
  let raf = 0, st = null;
  const redraw = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; isRender(); }); };
  try {
    const r = await fetch("/api/isis/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ messages: history }), signal: isBusy.signal });
    if (!r.ok) { let e = `HTTP ${r.status}`; try { e = (await r.json()).error || e; } catch { /* egal */ } throw new Error(e); }
    const rd = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await rd.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl); buf = buf.slice(nl + 1);
        if (!line.trim()) continue;
        const ev = JSON.parse(line);
        if (ev.delta) bot.content += ev.delta;
        else if (ev.sources) bot.refs = ev.sources;
        else if (ev.stats) st = ev.stats;
        else if (ev.error) bot.error = ev.error;
        redraw();
      }
    }
    if (!bot.content && !bot.error) bot.error = "Keine Antwort erhalten.";
  } catch (e) {
    bot.error = e.name === "AbortError" ? "Abgebrochen." : `Fehler: ${e.message}`;
  } finally {
    bot.stats = `${num((Date.now() - t0) / 1000, 0)} s${st && st.tps ? ` · ${num(st.tps, 1)} Tokens/s` : ""} · ${bot.refs.length} Fundstellen gelesen`;
    isBusy = null;
    isSave();
    isRender();
  }
}

// ================================================================ Klicks
function isisClick(ev) {
  const t = ev.target.closest("[data-is-sub],[data-is-fc],[data-is-files],[data-is-wc],[data-is-wiki],[data-is-note],[data-is-type],[data-is-graph],[data-is-q],[data-is-ref]");
  if (!t) return;
  const d = t.dataset;
  ev.preventDefault();
  const go = (sub) => { IS.sub = sub; try { localStorage.setItem("hl-isis-sub", sub); } catch { /* egal */ } initIsis(); };
  if (d.isSub) { go(d.isSub); return drawIsis(false); }
  if (d.isFc || d.isFiles) { IS.fcourse = Number(d.isFc || d.isFiles); go("files"); return isisFilesView(true); }
  if (d.isWc || d.isWiki) { IS.wcourse = Number(d.isWc || d.isWiki); IS.note = null; IS.hits = null; IS.q = ""; IS.type = ""; go("wiki"); return isisWikiView(true); }
  if (d.isNote) { if (IS.sub !== "wiki") go("wiki"); return isisOpenNote(Number(d.isNote)); }
  if (d.isType !== undefined) { IS.type = d.isType; return safe($("#is-wiki"), isisWiki); }
  if (d.isGraph) { IS.graph = !IS.graph; return safe($("#is-wiki"), isisWiki); }
  if (d.isQ) return isAsk(d.isQ);
  if (d.isRef) {
    const m = isMsgs[Number(t.closest(".cmsg").dataset.i)];
    const r = m && (m.refs || []).find((x) => x.n === Number(d.isRef));
    if (!r) return;
    if (r.kind === "note") { go("wiki"); return isisOpenNote(r.id); }
    if (r.kind === "page") return window.open(fileUrl(r.file_key, r.page), "_blank", "noopener");
    go("over"); return drawIsis(false);
  }
}
function isisSubmit(ev) {
  if (ev.target.id !== "is-form") return;
  ev.preventDefault();
  isBusy ? isBusy.abort() : isAsk($("#is-in").value);
}
