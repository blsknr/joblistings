"use strict";

const paths = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  archive: '<rect x="3" y="3" width="18" height="5" rx="1"/><path d="M5 8v12h14V8M10 12h4"/>',
  sliders: '<path d="M4 7h7m5 0h4M4 17h3m5 0h8"/><circle cx="13" cy="7" r="2.5"/><circle cx="9" cy="17" r="2.5"/>',
  link: '<path d="m10 13 4-4m-6 5-2 2a4 4 0 0 0 6 6l4-4a4 4 0 0 0 0-6M8 12a4 4 0 0 1 0-6l4-4a4 4 0 0 1 6 6l-2 2" transform="translate(1 0) scale(.9)"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 17l9 5 9-5"/>',
  send: '<path d="m21 3-6 18-4-8-8-4 18-6ZM11 13 21 3"/>',
  chat: '<path d="M20 11a8 8 0 0 1-8 8H5l-3 3v-9a9 9 0 1 1 18-2Z"/><path d="M7 10h8M7 14h5"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  external: '<path d="M14 3h7v7m0-7L11 13M10 3H4v17h17v-7"/>',
  pin: '<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  briefcase: '<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V3h8v4M3 12c6 4 12 4 18 0M10 13h4"/>',
  refresh: '<path d="M20 8a8 8 0 1 0 0 9M20 3v6h-6"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.briefcase}</svg>`;
const $ = selector => document.querySelector(selector);
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const safeLink = value => { try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) ? escapeHTML(url.href) : ""; } catch { return ""; } };
const date = value => new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "short" }).format(new Date(value));
let state = { jobs: [], statuses: [], profile: {}, options: {job_types: [], work_models: {}}, view: "active", selected: null, dirty: false };
let toastTimer, pollTimer;
let profileDirty = false;

function hydrateIcons(root = document) { root.querySelectorAll("[data-icon]").forEach(el => el.innerHTML = icon(el.dataset.icon)); }
hydrateIcons();
$("#today").textContent = new Intl.DateTimeFormat("de-DE", { weekday: "long", day: "numeric", month: "long" }).format(new Date());

async function api(path, method = "GET", body) {
  const response = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  let data;
  try { data = await response.json(); } catch { throw new Error("Die Anwendung ist gerade nicht erreichbar."); }
  if (!response.ok) throw new Error(data.error || "Das hat leider nicht geklappt.");
  return data;
}

function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").hidden = true, 4500);
}

function replaceJob(job) {
  const index = state.jobs.findIndex(item => item.id === job.id);
  if (index < 0) state.jobs.unshift(job); else state.jobs[index] = job;
  render();
}

async function load() {
  clearTimeout(pollTimer);
  try {
    const data = await api("/api/jobs");
    const previousSelected = state.jobs.find(job => job.id === state.selected);
    const firstLoad = !state.statuses.length;
    const profileChanged = JSON.stringify(state.profile) !== JSON.stringify(data.profile);
    Object.assign(state, data);
    $("#load-error").hidden = true;
    if (firstLoad) $("#status-filter").innerHTML = '<option value="">Alle Status</option>' + state.statuses.map(value => `<option>${escapeHTML(value)}</option>`).join("");
    render();
    if (state.view === "profile" && !profileDirty && (profileChanged || firstLoad)) renderProfileForm();
    const selected = state.jobs.find(job => job.id === state.selected);
    if (selected && previousSelected?.import_state === "pending" && selected.import_state !== "pending" && !state.dirty && $("#detail-dialog").open) renderDetail(selected);
  } catch (error) {
    $("#load-error").textContent = error.message + " Läuft der lokale Server noch?";
    $("#load-error").hidden = false;
  } finally {
    pollTimer = setTimeout(load, state.jobs.some(job => job.import_state === "pending") ? 2000 : 12000);
  }
}

function statusOptions(value) {
  return state.statuses.map(status => `<option${value === status ? " selected" : ""}>${escapeHTML(status)}</option>`).join("");
}

function render() {
  const p = state.profile;
  $("#focus-name").textContent = p.name || "Meine Jobsuche";
  $("#dashboard-eyebrow").textContent = p.name || "DEINE JOBSUCHE";
  $("#focus-model").textContent = state.options.work_models[p.work_model] || "Frei anpassbar";
  $("#profile-onboarding").hidden = Boolean((p.job_types || []).length || (p.fields || []).length || (p.cities || []).length);
  $("#focus-role").textContent = p.role || "Deine Jobsuche";
  $("#focus-hours").textContent = p.hours_min != null && p.hours_max != null ? `${p.hours_min}–${p.hours_max} Std. / Woche` : p.hours || "Stundenumfang offen";
  $("#focus-region").textContent = (p.cities || []).slice(0, 2).join(" & ") || "Deine Wunschregion";
  $("#footer-profile").textContent = p.role || "Dein persönlicher Stellenüberblick";
  const active = state.jobs.filter(job => !job.archived);
  $("#nav-count").textContent = active.length;
  $("#archive-count").textContent = state.jobs.length - active.length;
  $("#stat-total").textContent = active.length;
  $("#stat-applied").textContent = active.filter(job => ["Beworben", "Gespräch", "Angebot", "Absage"].includes(job.status)).length;
  $("#stat-interview").textContent = active.filter(job => job.status === "Gespräch").length;
  const query = $("#search").value.trim().toLocaleLowerCase("de");
  const filter = $("#status-filter").value;
  const sort = $("#sort").value;
  const categories = [...new Set([...(p.categories || []), ...state.jobs.map(job => job.category).filter(Boolean)])];
  const types = [...new Set(state.jobs.map(job => job.job_type).filter(Boolean))];
  updateFilter($("#category-filter"), categories, "Alle Kategorien");
  updateFilter($("#job-type-filter"), types, "Alle Jobarten");
  const category = $("#category-filter").value;
  const jobType = $("#job-type-filter").value;
  let jobs = state.jobs.filter(job => job.archived === (state.view === "archived"));
  const total = jobs.length;
  jobs = jobs.filter(job => (!filter || job.status === filter) && (!category || job.category === category) && (!jobType || job.job_type === jobType) && (!query || [job.title, job.company, job.location, job.notes, job.industry, job.job_type, job.category].join(" ").toLocaleLowerCase("de").includes(query)));
  jobs.sort((a, b) => sort === "company" ? a.company.localeCompare(b.company, "de") : (sort === "updated" ? b.updated_at.localeCompare(a.updated_at) : b.created_at.localeCompare(a.created_at)) || b.id - a.id);
  $("#result-count").textContent = jobs.length;
  $("#list-summary").textContent = total ? `${jobs.length} von ${total} ${total === 1 ? "Stelle" : "Stellen"}` : "Noch keine Stellen gespeichert";
  $("#jobs-title").firstChild.textContent = state.view === "archived" ? "Dein Archiv " : "Deine Stellen ";
  $("#page-title").textContent = state.view === "archived" ? "Platz für neue Möglichkeiten." : "Deine nächste gute Stelle.";
  $("#page-subtitle").textContent = state.view === "archived" ? "Archivierte Stellen bleiben erhalten. Du kannst sie jederzeit zurückholen." : "Sammeln, sortieren, dranbleiben. Ein Link ist der Anfang.";
  const pending = state.jobs.filter(job => job.import_state === "pending").length;
  $("#pending-label").innerHTML = pending ? `<span class="spinner"></span> ${pending} ${pending === 1 ? "Import läuft" : "Importe laufen"}` : "";
  document.querySelectorAll(".nav-item[data-view]").forEach(button => { button.classList.toggle("active", button.dataset.view === state.view); button.setAttribute("aria-current", button.dataset.view === state.view ? "page" : "false"); });
  $(".list-head").hidden = !jobs.length;
  if (!jobs.length) {
    const filtered = total > 0;
    $("#job-list").innerHTML = `<div class="empty-state"><div class="empty-symbol">${icon(filtered ? "search" : "briefcase")}</div><h3>${filtered ? "Hier passt gerade keine Stelle." : state.view === "archived" ? "Dein Archiv ist noch leer." : "Dein nächster Schritt beginnt hier."}</h3><p>${filtered ? "Versuche einen anderen Suchbegriff oder ändere deine Filter." : state.view === "archived" ? "Stellen, die du nicht mehr verfolgen möchtest, kannst du hier aufbewahren." : "Du hast eine interessante Stelle gefunden? Füge oben den Link ein. Wir kümmern uns um die Details."}</p><button class="text-button" id="empty-action">${filtered ? "Filter zurücksetzen" : state.view === "archived" ? "Zu deinen Stellen" : "Ersten Stellenlink einfügen"}</button></div>`;
    $("#empty-action").onclick = () => { if (filtered) { $("#search").value = ""; $("#status-filter").value = ""; $("#category-filter").value = ""; $("#job-type-filter").value = ""; render(); } else if (state.view === "archived") { state.view = "active"; render(); } else $("#job-url").focus(); };
    return;
  }
  $("#job-list").innerHTML = jobs.map(job => {
    const initials = job.company.replace(/^(www\.|jobs\.)/, "").slice(0, 2).toUpperCase();
    const flags = job.flags;
    return `<article class="job-row" tabindex="0" data-id="${job.id}" aria-label="Details: ${escapeHTML(job.title || job.company)}"><div class="job-main"><div class="company-mark" data-color="${job.id % 4}" aria-hidden="true">${escapeHTML(initials)}</div><div><h3 class="job-title">${escapeHTML(job.title || (job.import_state === "pending" ? "Stellenanzeige wird gelesen …" : "Gespeicherter Stellenlink"))}</h3><div class="company-name">${escapeHTML(job.company)}</div><div class="job-taxonomy">${job.job_type ? `<span>${escapeHTML(job.job_type)}</span>` : ""}${job.category ? `<span class="category-tag">${escapeHTML(job.category)}</span>` : ""}</div>${job.import_state === "pending" ? '<div class="import-status"><span class="spinner"></span>Details werden übernommen</div>' : job.import_state === "error" ? '<div class="import-status error">Link gespeichert · Import prüfen</div>' : ""}</div></div><div class="job-conditions"><div class="job-meta">${icon("pin")} ${escapeHTML(job.location || "Standort offen")}${job.hours ? " · " + escapeHTML(job.hours) : ""}</div><div class="flags">${flags.map(flag => `<span class="flag ${flag.kind}">${escapeHTML(flag.text)}</span>`).join("")}</div></div><div class="job-status"><select class="status-select" data-id="${job.id}" data-status="${escapeHTML(job.status)}" aria-label="Status für ${escapeHTML(job.title || job.company)}">${statusOptions(job.status)}</select></div><time class="job-date" datetime="${escapeHTML(job.created_at)}">${date(job.created_at)}</time><button class="open-detail" aria-label="Stellendetails öffnen">${icon("external")}</button></article>`;
  }).join("");
  document.querySelectorAll(".job-row").forEach(row => {
    row.addEventListener("click", event => { if (!event.target.closest("select")) openDetail(Number(row.dataset.id)); });
    row.addEventListener("keydown", event => { if (event.target === row && ["Enter", " "].includes(event.key)) { event.preventDefault(); openDetail(Number(row.dataset.id)); } });
  });
  document.querySelectorAll(".status-select").forEach(select => select.addEventListener("change", async () => {
    select.disabled = true;
    try { const data = await api(`/api/jobs/${select.dataset.id}`, "PATCH", { status: select.value }); replaceJob(data.job); toast("Status gespeichert."); }
    catch (error) { toast(error.message); render(); }
  }));
}

const fields = [
  ["title", "Jobtitel", "Titel der Stelle", true], ["company", "Unternehmen", "Unternehmen"], ["location", "Standort", "Noch nicht angegeben"],
  ["hours", "Wochenstunden", "Noch nicht angegeben"], ["remote", "Remote / Präsenz", "Noch nicht angegeben"],
  ["salary", "Vergütung", "Noch nicht angegeben"], ["industry", "Branche", "Noch nicht eingeordnet"], ["deadline", "Bewerbungsfrist", "Noch nicht angegeben"],
];

function openDetail(id) {
  state.selected = id;
  state.dirty = false;
  renderDetail(state.jobs.find(job => job.id === id));
  $("#detail-dialog").showModal();
}

function renderDetail(job) {
  const fieldHTML = fields.map(([name, label, placeholder, full]) => `<div class="field${full ? " full" : ""}"><label for="field-${name}">${label}</label><input id="field-${name}" name="${name}" value="${escapeHTML(job[name])}" placeholder="${placeholder}" maxlength="10000">${job.sources[name] ? `<small>${escapeHTML(job.sources[name])}</small>` : ""}</div>`).join("");
  $("#detail-content").innerHTML = `<div class="detail-header"><div class="company-name">${escapeHTML(job.company)}</div><h2>${escapeHTML(job.title || "Gespeicherter Stellenlink")}</h2><div class="flags">${job.flags.map(flag => `<span class="flag ${flag.kind}">${escapeHTML(flag.text)}</span>`).join("")}</div></div><div class="detail-links"><a href="${safeLink(job.url)}" target="_blank" rel="noopener noreferrer">${icon("external")}Originalanzeige</a>${safeLink(job.apply_url) ? `<a href="${safeLink(job.apply_url)}" target="_blank" rel="noopener noreferrer">${icon("briefcase")}Zum Bewerbungsportal</a>` : ""}</div><div class="notice${job.import_state === "error" ? " error" : ""}" role="status">${job.import_state === "pending" ? '<span class="spinner"></span> ' : ""}${escapeHTML(job.import_message || "Dein Link ist gespeichert.")}${job.imported_at ? `<br><small>Zuletzt eingelesen: ${date(job.imported_at)}</small>` : ""}</div><form id="detail-form" class="detail-form"><div class="field-grid"><div class="field full"><label for="field-status">Bewerbungsstatus</label><select name="status" id="field-status">${statusOptions(job.status)}</select></div>${fieldHTML}${jobClassificationFields(job)}<div class="field full"><label for="field-next_step">Nächster Schritt</label><input id="field-next_step" name="next_step" value="${escapeHTML(job.next_step)}" placeholder="Zum Beispiel: Wochenstunden und Präsenztage klären" maxlength="10000"></div><div class="field full"><label for="field-notes">Deine Notizen</label><textarea id="field-notes" name="notes" placeholder="Was ist dir an der Stelle wichtig?" maxlength="10000">${escapeHTML(job.notes)}</textarea></div></div><div class="detail-actions"><button class="primary-button" type="submit">Änderungen speichern</button><button class="secondary-button" type="button" id="retry-import"${job.import_state === "pending" ? " disabled" : ""}>${icon("refresh")}Neu einlesen</button><button class="text-button" type="button" id="archive-job">${job.archived ? "Aus Archiv holen" : "Archivieren"}</button></div></form><details class="source-details"><summary>Gespeicherte Stellenanzeige</summary><div class="original-text">${escapeHTML(job.description || "Noch kein Anzeigentext verfügbar.")}</div></details><details class="source-details"><summary>Anzeigentext selbst einfügen</summary><p class="subtle">Falls die Webseite nicht auslesbar ist: Kopiere die Anzeige hier hinein. Vorhandene manuelle Änderungen bleiben erhalten.</p><form id="paste-form" class="paste-area"><label class="sr-only" for="paste-text">Vollständiger Anzeigentext</label><textarea id="paste-text" placeholder="Jobtitel und vollständigen Anzeigentext einfügen …" minlength="80" maxlength="100000" required></textarea><button class="secondary-button" type="submit"${job.import_state === "pending" ? " disabled" : ""}>Angaben aus Text übernehmen</button></form></details>`;
  $("#detail-form").addEventListener("input", () => state.dirty = true);
  $("#paste-text").addEventListener("input", () => state.dirty = true);
  $("#detail-form").addEventListener("submit", async event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.target));
    const changed = Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== job[key]));
    const button = event.submitter;
    button.disabled = true;
    try { const data = await api(`/api/jobs/${job.id}`, "PATCH", changed); replaceJob(data.job); state.dirty = false; renderDetail(data.job); toast("Änderungen gespeichert."); }
    catch (error) { toast(error.message); button.disabled = false; }
  });
  $("#retry-import").onclick = async event => {
    if (state.dirty && !confirm("Ungespeicherte Änderungen verwerfen und die Anzeige neu einlesen?")) return;
    event.currentTarget.disabled = true;
    try { const data = await api(`/api/jobs/${job.id}/retry`, "POST", {}); state.dirty = false; replaceJob(data.job); renderDetail(data.job); load(); }
    catch (error) { toast(error.message); event.target.disabled = false; }
  };
  $("#archive-job").onclick = async () => {
    if (state.dirty && !confirm("Ungespeicherte Änderungen verwerfen?")) return;
    try { const data = await api(`/api/jobs/${job.id}`, "PATCH", { archived: !job.archived }); replaceJob(data.job); state.dirty = false; $("#detail-dialog").close(); toast(data.job.archived ? "Stelle archiviert." : "Stelle zurückgeholt."); }
    catch (error) { toast(error.message); }
  };
  $("#paste-form").onsubmit = async event => {
    event.preventDefault();
    if (state.dirty && [...new FormData($("#detail-form"))].some(([key, value]) => value !== job[key]) && !confirm("Ungespeicherte Änderungen im Formular verwerfen und den Anzeigentext übernehmen?")) return;
    const button = event.submitter;
    button.disabled = true;
    try { const data = await api(`/api/jobs/${job.id}/text`, "POST", { text: $("#paste-text").value }); state.dirty = false; replaceJob(data.job); renderDetail(data.job); toast("Angaben übernommen."); }
    catch (error) { toast(error.message); button.disabled = false; }
  };
}

function closeDetail(event) {
  if (state.dirty && !confirm("Ungespeicherte Änderungen verwerfen?")) { event?.preventDefault(); return; }
  state.dirty = false;
  state.selected = null;
  $("#detail-dialog").close();
}
$(".close-detail").onclick = closeDetail;
$("#detail-dialog").addEventListener("cancel", closeDetail);
$("#import-form").onsubmit = async event => {
  event.preventDefault();
  const button = $("#import-button");
  button.disabled = true;
  $("#import-feedback").textContent = "Link wird gespeichert …";
  try {
    const data = await api("/api/jobs", "POST", { url: $("#job-url").value.trim() });
    replaceJob(data.job);
    $("#job-url").value = "";
    $("#import-feedback").textContent = data.created ? "Gespeichert. Die Details folgen automatisch." : "Diesen Link hast du bereits gespeichert.";
    if (data.created) { state.view = "active"; $("#search").value = ""; $("#status-filter").value = ""; $("#category-filter").value = ""; $("#job-type-filter").value = ""; render(); }
    else openDetail(data.job.id);
    load();
  } catch (error) { $("#import-feedback").textContent = error.message; }
  finally { button.disabled = false; }
};
$("#search").addEventListener("input", render);
$("#status-filter").addEventListener("change", render);
$("#sort").addEventListener("change", render);
document.querySelectorAll("[data-view]").forEach(button => button.onclick = () => navigate(button.dataset.view));
$("#category-filter").addEventListener("change", render);
$("#job-type-filter").addEventListener("change", render);
window.addEventListener("beforeunload", event => { if (state.dirty || profileDirty) { event.preventDefault(); event.returnValue = ""; } });
load();
