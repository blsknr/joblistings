"use strict";

const $ = selector => document.querySelector(selector);
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const safeLink = value => { try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) ? escapeHTML(url.href) : ""; } catch { return ""; } };
const date = value => new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit", year: "2-digit" }).format(new Date(value));
const flagList = flags => flags.map(flag => `<span class="flag ${flag.kind}">${escapeHTML(flag.text)}</span>`).join("");
let state = { jobs: [], statuses: [], profile: {}, options: { job_types: [], work_models: {} }, view: "active", selected: null, dirty: false };
let toastTimer, pollTimer;
let profileDirty = false;

async function api(path, method = "GET", body) {
  const response = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  let data;
  try { data = await response.json(); } catch { throw new Error("Server nicht erreichbar."); }
  if (!response.ok) throw new Error(data.error || "Fehlgeschlagen.");
  return data;
}

function toast(message) {
  // A modal dialog sits in the top layer; the toast must live inside it to be visible.
  const host = $("#detail-dialog").open ? $("#detail-dialog") : document.body;
  if ($("#toast").parentElement !== host) host.append($("#toast"));
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").hidden = true, 3500);
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
    $("#load-error").textContent = error instanceof TypeError ? "Server nicht erreichbar." : error.message;
    $("#load-error").hidden = false;
  } finally {
    pollTimer = setTimeout(load, state.jobs.some(job => job.import_state === "pending") ? 2000 : 12000);
  }
}

function statusOptions(value) {
  return state.statuses.map(status => `<option${value === status ? " selected" : ""}>${escapeHTML(status)}</option>`).join("");
}

function renderProfileLine() {
  const p = state.profile;
  const hasHours = [p.hours_min, p.hours_max, p.hours_emergency_max].some(value => value != null);
  const parts = [
    [...(p.job_types || []), ...(p.fields || [])].join(", "),
    hasHours && p.hours,
    (p.cities || []).join(", "),
    p.work_model && p.work_model !== "any" && state.options.work_models[p.work_model],
  ].filter(Boolean);
  $("#profile-line").innerHTML = parts.length
    ? `<span class="label">${escapeHTML(p.name || "Suchprofil")}</span>${escapeHTML(parts.join(" · "))} <button class="link" data-view="profile">Bearbeiten</button>`
    : `Kein Suchprofil festgelegt. <button class="link" data-view="profile">Festlegen</button>`;
}

function resetFilters() {
  for (const id of ["search", "status-filter", "category-filter", "job-type-filter"]) $("#" + id).value = "";
}

function render() {
  const p = state.profile;
  const archived = state.view === "archived";
  const active = state.jobs.filter(job => !job.archived);
  $("#nav-count").textContent = active.length;
  $("#archive-count").textContent = state.jobs.length - active.length;
  $("#stat-total").textContent = active.length;
  $("#stat-applied").textContent = active.filter(job => ["Beworben", "Gespräch", "Angebot", "Absage"].includes(job.status)).length;
  $("#stat-interview").textContent = active.filter(job => job.status === "Gespräch").length;
  for (const id of ["#import-form", "#stats", "#profile-line"]) $(id).hidden = archived;
  renderProfileLine();
  document.querySelectorAll(".tab[data-view]").forEach(tab => {
    if (tab.dataset.view === state.view) tab.setAttribute("aria-current", "page"); else tab.removeAttribute("aria-current");
  });

  const query = $("#search").value.trim().toLocaleLowerCase("de");
  const filter = $("#status-filter").value;
  const sort = $("#sort").value;
  updateFilter($("#category-filter"), [...new Set([...(p.categories || []), ...state.jobs.map(job => job.category).filter(Boolean)])], "Alle Kategorien");
  updateFilter($("#job-type-filter"), [...new Set(state.jobs.map(job => job.job_type).filter(Boolean))], "Alle Jobarten");
  const category = $("#category-filter").value;
  const jobType = $("#job-type-filter").value;
  let jobs = state.jobs.filter(job => job.archived === archived);
  const total = jobs.length;
  jobs = jobs.filter(job => (!filter || job.status === filter) && (!category || job.category === category) && (!jobType || job.job_type === jobType) && (!query || [job.title, job.company, job.location, job.notes, job.industry, job.job_type, job.category].join(" ").toLocaleLowerCase("de").includes(query)));
  jobs.sort((a, b) => (sort === "company" ? a.company.localeCompare(b.company, "de") : sort === "updated" ? b.updated_at.localeCompare(a.updated_at) : b.created_at.localeCompare(a.created_at)) || b.id - a.id);

  $("#jobs-title").textContent = archived ? "Archiv" : "Stellen";
  $("#result-count").textContent = jobs.length === total ? total : `${jobs.length} / ${total}`;
  const pending = state.jobs.filter(job => job.import_state === "pending").length;
  $("#pending-label").innerHTML = pending ? `<span class="spinner"></span>${pending} ${pending === 1 ? "Import läuft" : "Importe laufen"}` : "";
  $(".list-head").hidden = !jobs.length;

  if (!jobs.length) {
    const filtered = total > 0;
    const text = filtered ? "Keine Treffer." : archived ? "Archiv ist leer." : "Noch keine Stellen gespeichert.";
    $("#job-list").innerHTML = `<div class="empty">${text}${filtered ? '<button class="link" id="empty-action">Filter zurücksetzen</button>' : ""}</div>`;
    if (filtered) $("#empty-action").onclick = () => { resetFilters(); render(); };
    return;
  }

  $("#job-list").innerHTML = jobs.map(job => {
    const pendingImport = job.import_state === "pending";
    const title = job.title || (pendingImport ? "Wird eingelesen …" : "Ohne Titel");
    const sub = [job.company, job.job_type].filter(Boolean).map(escapeHTML).join(" · ");
    const importState = pendingImport ? '<div class="row-state"><span class="spinner"></span>Wird eingelesen</div>'
      : job.import_state === "error" ? '<div class="row-state failed">Import fehlgeschlagen</div>' : "";
    const meta = [job.location, job.hours].filter(Boolean).map(escapeHTML).join(" · ");
    return `<article class="job-row" tabindex="0" data-id="${job.id}" aria-label="${escapeHTML(title)}">
      <div class="job-main"><h3 class="job-title">${escapeHTML(title)}</h3><div class="job-sub">${sub}${job.category ? `<span class="tag">${escapeHTML(job.category)}</span>` : ""}</div>${importState}</div>
      <div class="job-conditions"><div class="job-meta${meta ? "" : " missing"}">${meta || "Ort offen"}</div><div class="flags">${flagList(job.flags)}</div></div>
      <div class="job-status"><select class="status-select" data-id="${job.id}" data-status="${escapeHTML(job.status)}" aria-label="Status">${statusOptions(job.status)}</select></div>
      <time class="job-date" datetime="${escapeHTML(job.created_at)}">${date(job.created_at)}</time>
    </article>`;
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
  ["title", "Titel", true], ["company", "Unternehmen"], ["location", "Ort"],
  ["hours", "Wochenstunden"], ["remote", "Remote / Präsenz"],
  ["salary", "Vergütung"], ["industry", "Branche"], ["deadline", "Bewerbungsfrist"],
];

function openDetail(id) {
  state.selected = id;
  state.dirty = false;
  renderDetail(state.jobs.find(job => job.id === id));
  $("#detail-dialog").showModal();
}

function renderDetail(job) {
  const pendingImport = job.import_state === "pending";
  const fieldHTML = fields.map(([name, label, full]) => `<div class="field${full ? " full" : ""}"><label for="field-${name}">${label}</label><input type="text" id="field-${name}" name="${name}" value="${escapeHTML(job[name])}" placeholder="—" maxlength="10000">${job.sources[name] ? `<small>${escapeHTML(job.sources[name])}</small>` : ""}</div>`).join("");
  const applyLink = safeLink(job.apply_url);
  $("#detail-label").textContent = `Stelle #${job.id} · ${date(job.created_at)}`;
  $("#detail-content").innerHTML = `
    <div class="detail-header"><div class="job-sub">${escapeHTML(job.company)}</div><h2>${escapeHTML(job.title || "Ohne Titel")}</h2><div class="flags">${flagList(job.flags)}</div></div>
    <div class="detail-links"><a href="${safeLink(job.url)}" target="_blank" rel="noopener noreferrer">Originalanzeige ↗</a>${applyLink ? `<a href="${applyLink}" target="_blank" rel="noopener noreferrer">Bewerbungsportal ↗</a>` : ""}</div>
    <div class="notice${job.import_state === "error" ? " error-state" : ""}" role="status">${pendingImport ? '<span class="spinner"></span> ' : ""}${escapeHTML(job.import_message || "Link gespeichert.")}${job.imported_at ? `<small>Eingelesen am ${date(job.imported_at)}</small>` : ""}</div>
    <form id="detail-form">
      <div class="field-grid">
        <div class="field full"><label for="field-status">Status</label><select name="status" id="field-status">${statusOptions(job.status)}</select></div>
        ${fieldHTML}
        ${jobClassificationFields(job)}
        <div class="field full"><label for="field-next_step">Nächster Schritt</label><input type="text" id="field-next_step" name="next_step" value="${escapeHTML(job.next_step)}" placeholder="—" maxlength="10000"></div>
        <div class="field full"><label for="field-notes">Notizen</label><textarea id="field-notes" name="notes" maxlength="10000">${escapeHTML(job.notes)}</textarea></div>
      </div>
      <div class="actions">
        <button class="btn btn-primary" type="submit">Speichern</button>
        <button class="btn" type="button" id="retry-import"${pendingImport ? " disabled" : ""}>Neu einlesen</button>
        <button class="btn push" type="button" id="archive-job">${job.archived ? "Wiederherstellen" : "Archivieren"}</button>
      </div>
    </form>
    <details><summary>Anzeigentext</summary><div class="original-text">${escapeHTML(job.description || "Kein Text vorhanden.")}</div></details>
    <details><summary>Text manuell einfügen</summary><p class="hint">Falls der Import fehlschlägt. Manuell geänderte Felder bleiben erhalten.</p><form id="paste-form" class="paste"><label class="sr-only" for="paste-text">Anzeigentext</label><textarea id="paste-text" minlength="80" maxlength="100000" required></textarea><button class="btn" type="submit"${pendingImport ? " disabled" : ""}>Übernehmen</button></form></details>`;
  $("#detail-form").addEventListener("input", () => state.dirty = true);
  $("#paste-text").addEventListener("input", () => state.dirty = true);
  $("#detail-form").addEventListener("submit", async event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.target));
    const changed = Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== job[key]));
    const button = event.submitter;
    button.disabled = true;
    try { const data = await api(`/api/jobs/${job.id}`, "PATCH", changed); replaceJob(data.job); state.dirty = false; renderDetail(data.job); toast("Gespeichert."); }
    catch (error) { toast(error.message); button.disabled = false; }
  });
  $("#retry-import").onclick = async event => {
    if (state.dirty && !confirm("Ungespeicherte Änderungen verwerfen und neu einlesen?")) return;
    const button = event.currentTarget;
    button.disabled = true;
    try { const data = await api(`/api/jobs/${job.id}/retry`, "POST", {}); state.dirty = false; replaceJob(data.job); renderDetail(data.job); load(); }
    catch (error) { toast(error.message); button.disabled = false; }
  };
  $("#archive-job").onclick = async () => {
    if (state.dirty && !confirm("Ungespeicherte Änderungen verwerfen?")) return;
    try { const data = await api(`/api/jobs/${job.id}`, "PATCH", { archived: !job.archived }); replaceJob(data.job); state.dirty = false; $("#detail-dialog").close(); toast(data.job.archived ? "Archiviert." : "Wiederhergestellt."); }
    catch (error) { toast(error.message); }
  };
  $("#paste-form").onsubmit = async event => {
    event.preventDefault();
    if (state.dirty && [...new FormData($("#detail-form"))].some(([key, value]) => value !== job[key]) && !confirm("Ungespeicherte Änderungen verwerfen und Text übernehmen?")) return;
    const button = event.submitter;
    button.disabled = true;
    try { const data = await api(`/api/jobs/${job.id}/text`, "POST", { text: $("#paste-text").value }); state.dirty = false; replaceJob(data.job); renderDetail(data.job); toast("Übernommen."); }
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
  $("#import-feedback").textContent = "Speichern …";
  try {
    const data = await api("/api/jobs", "POST", { url: $("#job-url").value.trim() });
    replaceJob(data.job);
    $("#job-url").value = "";
    $("#import-feedback").textContent = data.created ? "Hinzugefügt. Details werden eingelesen." : "Bereits gespeichert.";
    if (data.created) { state.view = "active"; resetFilters(); render(); }
    else openDetail(data.job.id);
    load();
  } catch (error) { $("#import-feedback").textContent = error.message; }
  finally { button.disabled = false; }
};
for (const id of ["#status-filter", "#sort", "#category-filter", "#job-type-filter"]) $(id).addEventListener("change", render);
$("#search").addEventListener("input", render);
document.addEventListener("click", event => { const target = event.target.closest("[data-view]"); if (target) navigate(target.dataset.view); });
window.addEventListener("beforeunload", event => { if (state.dirty || profileDirty) { event.preventDefault(); event.returnValue = ""; } });
load();
