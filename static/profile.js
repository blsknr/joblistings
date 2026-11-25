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
  const options = (values, current) => `<option value="">—</option>` + values.map(value => `<option value="${escapeHTML(value)}"${value === current ? " selected" : ""}>${escapeHTML(value)}</option>`).join("");
  return `<div class="field"><label for="field-job_type">Jobart</label><select id="field-job_type" name="job_type">${options(types, job.job_type)}</select></div><div class="field"><label for="field-category">Kategorie</label><select id="field-category" name="category">${options(categories, job.category)}</select><small>Kategorien im Suchprofil anlegen</small></div>`;
}

function navigate(view) {
  if (state.view === "profile" && profileDirty && !confirm("Ungespeicherte Profiländerungen verwerfen?")) return;
  profileDirty = false;
  state.view = view;
  $("#dashboard-view").hidden = view === "profile";
  $("#profile-view").hidden = view !== "profile";
  if (view === "profile") renderProfileForm();
  else resetFilters();
  render();
  window.scrollTo(0, 0);
}

function renderProfileForm() {
  const p = state.profile;
  const listInput = (key, label, placeholder = "") => `<div class="field"><label for="profile-${key}">${label}</label><input type="text" id="profile-${key}" name="${key}" value="${escapeHTML((p[key] || []).join(", "))}" placeholder="${escapeHTML(placeholder)}" maxlength="4000"></div>`;
  const numeric = (key, label) => `<div class="field"><label for="profile-${key}">${label}</label><input id="profile-${key}" name="${key}" type="number" min="0" max="168" step="0.5" value="${p[key] ?? ""}" placeholder="—"></div>`;
  const section = (title, body, hint = "") => `<section class="pf-section"><div class="pf-label"><h2>${title}</h2>${hint ? `<p>${hint}</p>` : ""}</div><div class="pf-fields">${body}</div></section>`;
  const customTypes = (p.job_types || []).filter(type => !state.options.job_types.includes(type));
  const checks = state.options.job_types.map(type => `<label class="check"><input type="checkbox" name="job_types" value="${escapeHTML(type)}"${(p.job_types || []).includes(type) ? " checked" : ""}><span>${escapeHTML(type)}</span></label>`).join("");
  const models = Object.entries(state.options.work_models).map(([key, label]) => `<option value="${key}"${p.work_model === key ? " selected" : ""}>${escapeHTML(label)}</option>`).join("");
  $("#profile-editor").innerHTML = `<form id="profile-form">
    ${section("Name", `<div class="field"><label class="sr-only" for="profile-name">Name</label><input type="text" id="profile-name" name="name" value="${escapeHTML(p.name || "")}" maxlength="100" required></div>`)}
    ${section("Jobarten", `<fieldset class="checks"><legend class="sr-only">Jobarten</legend>${checks}</fieldset><div class="field"><label for="profile-custom-types">Weitere</label><input type="text" id="profile-custom-types" name="custom_types" value="${escapeHTML(customTypes.join(", "))}" maxlength="4000"></div>`)}
    ${section("Fachrichtungen", listInput("fields", "Bereiche", "z. B. Marketing, Pflege, Softwareentwicklung"))}
    ${section("Branchen", `<div class="pf-pair">${listInput("industries", "Bevorzugt")}${listInput("excluded_industries", "Ausgeschlossen")}</div>`)}
    ${section("Orte", `<div class="pf-pair">${listInput("cities", "Bevorzugt")}${listInput("excluded_cities", "Ausgeschlossen")}</div>`)}
    ${section("Wochenstunden", `<div class="pf-triple">${numeric("hours_min", "Minimum")}${numeric("hours_max", "Maximum")}${numeric("hours_emergency_max", "Ausnahme bis")}</div>`, "Leer lassen, wenn egal. Die Ausnahmegrenze muss mindestens dem Maximum entsprechen.")}
    ${section("Arbeitsmodell", `<div class="field"><label for="profile-work_model">Modell</label><select id="profile-work_model" name="work_model">${models}</select></div><div class="field"><label for="profile-remote">Anmerkungen</label><textarea id="profile-remote" name="remote" maxlength="2000" rows="3">${escapeHTML(p.remote)}</textarea></div>`)}
    ${section("Kategorien", listInput("categories", "Eigene Kategorien", "z. B. Favoriten, Plan B"), "Zum Sortieren von Stellen. Entfernte Kategorien bleiben an bestehenden Stellen erhalten.")}
    <div id="profile-error" class="error" role="alert" hidden></div>
    <div class="savebar"><span id="profile-feedback" role="status"></span><div><button type="button" class="btn" id="profile-discard">Zurücksetzen</button><button class="btn btn-primary" id="profile-save" type="submit">Speichern</button></div></div>
  </form>`;
  $("#profile-form").oninput = () => { profileDirty = true; $("#profile-feedback").textContent = "Ungespeicherte Änderungen"; };
  $("#profile-form").onchange = $("#profile-form").oninput;
  $("#profile-discard").onclick = () => { if (!profileDirty || confirm("Änderungen verwerfen?")) { profileDirty = false; renderProfileForm(); } };
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
    $("#profile-feedback").textContent = "Gespeichert.";
  } catch (error) {
    $("#profile-error").textContent = error.message;
    $("#profile-error").hidden = false;
    $("#profile-save").disabled = false;
    $("#profile-error").scrollIntoView({ block: "center" });
  }
}
