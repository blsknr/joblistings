"use strict";

function updateFilter(select, values, label) {
  const previous = select.value;
  const options = `<option value="">${label}</option>` + values.sort((a, b) => a.localeCompare(b, "de")).map(value => `<option value="${escapeHTML(value)}">${escapeHTML(value)}</option>`).join("");
  if (select.innerHTML !== options) {
    select.innerHTML = options;
    select.value = values.includes(previous) ? previous : "";
  }
}

function jobClassificationFields(job) {
  const types = [...new Set([...state.options.job_types, ...(state.profile.job_types || []), job.job_type].filter(Boolean))];
  const categories = [...new Set([...(state.profile.categories || []), job.category].filter(Boolean))];
  const options = (values, current, label) => `<option value="">${label}</option>` + values.map(value => `<option value="${escapeHTML(value)}"${value === current ? " selected" : ""}>${escapeHTML(value)}</option>`).join("");
  return `<div class="field"><label for="field-job_type">Jobart</label><select id="field-job_type" name="job_type">${options(types, job.job_type, "Noch nicht angegeben")}</select></div><div class="field"><label for="field-category">Deine Kategorie</label><select id="field-category" name="category">${options(categories, job.category, "Ohne Kategorie")}</select><small>Kategorien legst du im Suchprofil fest.</small></div>`;
}

function navigate(view) {
  if (state.view === "profile" && profileDirty && !confirm("Ungespeicherte Profiländerungen verwerfen?")) return;
  profileDirty = false;
  state.view = view;
  $("#dashboard-view").hidden = view === "profile";
  $("#profile-view").hidden = view !== "profile";
  if (view === "profile") renderProfileForm();
  else for (const id of ["search", "status-filter", "category-filter", "job-type-filter"]) $("#" + id).value = "";
  render();
  $(view === "profile" ? "#profile-title" : "#page-title").scrollIntoView({block: "start"});
}

function renderProfileForm() {
  const p = state.profile;
  const listInput = (key, label, placeholder, hint = "Mehrere Einträge mit Komma trennen.") => `<div class="field"><label for="profile-${key}">${label}</label><input id="profile-${key}" name="${key}" value="${escapeHTML((p[key] || []).join(", "))}" placeholder="${escapeHTML(placeholder)}" maxlength="4000"><small>${hint}</small></div>`;
  const numeric = (key, label) => `<div class="field"><label for="profile-${key}">${label}</label><input id="profile-${key}" name="${key}" type="number" min="0" max="168" step="0.5" value="${p[key] ?? ""}" placeholder="Offen"></div>`;
  const customTypes = (p.job_types || []).filter(type => !state.options.job_types.includes(type));
  $("#profile-editor").innerHTML = `<form id="profile-form"><div class="profile-intro"><div class="profile-avatar">${icon("sliders")}</div><div><span class="eyebrow purple">DEIN SUCHPROFIL</span><div class="field"><label class="sr-only" for="profile-name">Profilname</label><input id="profile-name" name="name" value="${escapeHTML(p.name || "Meine Jobsuche")}" placeholder="Zum Beispiel: Mein nächster Karriereschritt" maxlength="100" required></div><p>Deine Einstellungen bleiben auf diesem Rechner.</p></div></div>
  <div class="profile-grid">
    <section class="profile-card wide"><div class="profile-card-heading"><span class="profile-section-icon">${icon("briefcase")}</span><div><h2>Was möchtest du machen?</h2><p>Wähle passende Jobarten und beschreibe deine Fachrichtungen.</p></div></div><fieldset class="job-type-choices"><legend class="sr-only">Gewünschte Jobarten</legend>${state.options.job_types.map(type => `<label class="choice-chip"><input type="checkbox" name="job_types" aria-label="${escapeHTML(type)}" value="${escapeHTML(type)}"${(p.job_types || []).includes(type) ? " checked" : ""}><span>${escapeHTML(type)}</span></label>`).join("")}</fieldset><div class="profile-field-pair">${listInput("fields", "Fachrichtungen / Tätigkeitsbereiche", "z. B. Marketing, Pflege, Softwareentwicklung", "Beschreibt deinen Fokus; keine automatische Qualifikationsbewertung.")}<div class="field"><label for="profile-custom-types">Weitere Jobarten</label><input id="profile-custom-types" name="custom_types" value="${escapeHTML(customTypes.join(", "))}" placeholder="Eigene Bezeichnungen, mit Komma getrennt" maxlength="4000"></div></div></section>
    <section class="profile-card"><div class="profile-card-heading"><span class="profile-section-icon">${icon("layers")}</span><div><h2>Branchen</h2><p>Was interessiert dich, was kommt nicht infrage?</p></div></div>${listInput("industries", "Bevorzugte Branchen", "z. B. Gesundheit, Energie, Bildung")}${listInput("excluded_industries", "Ausgeschlossene Branchen", "z. B. Automobil, Tabak")}</section>
    <section class="profile-card"><div class="profile-card-heading"><span class="profile-section-icon">${icon("pin")}</span><div><h2>Orte & Regionen</h2><p>Deine bevorzugten Standorte und Ausschlüsse.</p></div></div>${listInput("cities", "Gewünschte Orte / Regionen", "z. B. Hamburg, Hannover")}${listInput("excluded_cities", "Ausgeschlossene Orte", "Orte, die für dich nicht infrage kommen")}</section>
    <section class="profile-card"><div class="profile-card-heading"><span class="profile-section-icon">${icon("clock")}</span><div><h2>Zeitlicher Rahmen</h2><p>Lass Felder frei, wenn du flexibel bist.</p></div></div><div class="profile-field-pair hours-pair">${numeric("hours_min", "Mindestens Std. / Woche")}${numeric("hours_max", "Wunsch-Maximum")}</div>${numeric("hours_emergency_max", "Maximum im Ausnahmefall")}<p class="field-explanation">Die Ausnahmegrenze ist optional und liegt mindestens beim Wunsch-Maximum.</p></section>
    <section class="profile-card"><div class="profile-card-heading"><span class="profile-section-icon">${icon("grid")}</span><div><h2>Arbeitsmodell</h2><p>Wie viel Präsenz passt in deinen Alltag?</p></div></div><div class="field"><label for="profile-work_model">Remote / Präsenz</label><select id="profile-work_model" name="work_model">${Object.entries(state.options.work_models).map(([key, label]) => `<option value="${key}"${p.work_model === key ? " selected" : ""}>${escapeHTML(label)}</option>`).join("")}</select></div><div class="field"><label for="profile-remote">Präsenzwünsche & Ausnahmen</label><textarea id="profile-remote" name="remote" maxlength="2000" placeholder="z. B. ein Präsenztag pro Monat wäre möglich">${escapeHTML(p.remote)}</textarea></div></section>
    <section class="profile-card wide"><div class="profile-card-heading"><span class="profile-section-icon">${icon("archive")}</span><div><h2>Deine Kategorien</h2><p>Ordne Stellen so, wie es für deine Suche sinnvoll ist.</p></div></div>${listInput("categories", "Eigene Kategorien", "z. B. Favoriten, Plan B, Später prüfen", "Du kannst jeder Stelle eine Kategorie zuweisen und danach filtern. Bestehende Zuordnungen bleiben erhalten, wenn du eine Kategorie hier entfernst.")}</section>
  </div><div id="profile-error" class="error-banner" role="alert" hidden></div><div class="profile-savebar"><span id="profile-feedback" role="status">Änderungen gelten auch für bereits gespeicherte Stellen.</span><div><button type="button" class="secondary-button" id="profile-discard">Zurücksetzen</button><button class="primary-button" id="profile-save" type="submit">Suchprofil speichern</button></div></div></form>`;
  $("#profile-form").oninput = () => { profileDirty = true; $("#profile-feedback").textContent = "Du hast ungespeicherte Änderungen."; };
  $("#profile-form").onchange = $("#profile-form").oninput;
  $("#profile-discard").onclick = () => { if (!profileDirty || confirm("Deine ungespeicherten Änderungen zurücksetzen?")) { profileDirty = false; renderProfileForm(); } };
  $("#profile-form").onsubmit = saveProfile;
}

async function saveProfile(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  const lists = ["fields", "industries", "excluded_industries", "cities", "excluded_cities", "categories"];
  const split = value => String(value || "").split(/[,;\n]/).map(item => item.trim()).filter(Boolean);
  const values = Object.fromEntries(lists.map(key => [key, split(form.get(key))]));
  values.job_types = [...form.getAll("job_types"), ...split(form.get("custom_types"))];
  for (const key of ["hours_min", "hours_max", "hours_emergency_max"]) values[key] = form.get(key) === "" ? null : Number(form.get(key));
  for (const key of ["name", "work_model", "remote"]) values[key] = form.get(key).trim();
  $("#profile-save").disabled = true;
  $("#profile-error").hidden = true;
  try {
    const data = await api("/api/profile", "PATCH", values);
    state.profile = data.profile;
    state.jobs = data.jobs;
    profileDirty = false;
    render();
    renderProfileForm();
    $("#profile-feedback").textContent = "Gespeichert. Deine Stellen sind jetzt nach diesem Profil eingeordnet.";
    toast("Suchprofil gespeichert.");
  } catch (error) {
    $("#profile-error").textContent = error.message;
    $("#profile-error").hidden = false;
    $("#profile-save").disabled = false;
    $("#profile-error").scrollIntoView({block: "center"});
  }
}
