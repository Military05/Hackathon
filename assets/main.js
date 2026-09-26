import { config } from "./config.js";
import { createDemoAdapter } from "./demo-adapter.js";
import { createRestAdapter } from "./rest-adapter.js";

const adapter = config.mode === "api" ? createRestAdapter(config.api) : createDemoAdapter({ resourceIds: config.sections.map((section) => section.id) });
const state = { route: "dashboard", rows: {}, errors: {}, query: "", page: 1 };
const PAGE_SIZE = 8;
const $ = (selector) => document.querySelector(selector);
const html = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

document.title = `${config.title} · Admin`;
$("#brand-name").textContent = config.title;
$("#header-brand").textContent = config.title;
$("#brand-mark").textContent = config.title.charAt(0).toUpperCase();
$("#mode-title").textContent = config.mode === "api" ? "Подключён API" : "Демонстрация";
$("#mode-description").textContent = config.mode === "api" ? "Данные вашего проекта" : "Данные сохранены в этом браузере";
$("#mode-badge").textContent = config.mode === "api" ? "API" : "Демо";

$("#menu-button").addEventListener("click", () => {
  const open = $("#sidebar").classList.toggle("open");
  $("#menu-button").setAttribute("aria-expanded", String(open));
});
$("#close-editor").addEventListener("click", () => $("#editor").close());
$("#cancel-editor").addEventListener("click", () => $("#editor").close());
$("#editor").addEventListener("click", (event) => { if (event.target === $("#editor")) $("#editor").close(); });
$("#editor-form").addEventListener("submit", saveForm);
$("#main-content").addEventListener("click", onMainClick);
$("#main-content").addEventListener("input", (event) => {
  if (event.target.id === "search") { state.query = event.target.value; state.page = 1; renderTable(); }
});
window.addEventListener("hashchange", route);

function sectionFor(id) { return config.sections.find((section) => section.id === id); }
function route() {
  const id = location.hash.replace(/^#\/?/, "");
  state.route = sectionFor(id) ? id : "dashboard";
  state.query = "";
  state.page = 1;
  $("#sidebar").classList.remove("open");
  $("#menu-button").setAttribute("aria-expanded", "false");
  render();
}

function renderNavigation() {
  const links = [{ id: "dashboard", label: "Обзор", icon: "⌁" }, ...config.sections];
  $("#navigation").innerHTML = links.map(({ id, label, icon }) =>
    `<a href="#/${encodeURIComponent(id)}" class="nav-item ${state.route === id ? "active" : ""}" ${state.route === id ? 'aria-current="page"' : ""}><span class="nav-icon" aria-hidden="true">${html(icon)}</span><span>${html(label)}</span></a>`
  ).join("");
}

async function load() {
  const results = await Promise.allSettled(config.sections.map((section) => adapter.list(section.id)));
  results.forEach((result, index) => {
    const id = config.sections[index].id;
    if (result.status === "fulfilled") { state.rows[id] = result.value; delete state.errors[id]; }
    else { state.rows[id] = []; state.errors[id] = result.reason?.message || "Не удалось загрузить данные"; }
  });
  render();
}

function render() {
  renderNavigation();
  const section = sectionFor(state.route);
  $("#current-page").textContent = section?.label || "Обзор";
  $("#main-content").innerHTML = section ? renderSection(section) : renderDashboard();
  if (section && !state.errors[section.id]) renderTable();
}

function renderDashboard() {
  const total = config.sections.reduce((sum, section) => sum + (state.rows[section.id]?.length || 0), 0);
  return `<div class="page-intro"><div><p class="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО</p><h1>Обзор проекта</h1><p>${html(config.subtitle)}. Настройте разделы и подключите свои данные.</p></div><button class="button button-secondary" data-action="refresh">↻ Обновить</button></div>
    <div class="hero"><div><span class="hero-kicker">АДМИНИСТРАТИВНАЯ ПАНЕЛЬ</span><h2>Всё важное в одном месте</h2><p>Этот интерфейс работает отдельно от серверной части. Настройте разделы в <code>assets/config.js</code> и подключите API, когда он будет готов.</p></div><span class="hero-symbol" aria-hidden="true">${html(config.title.charAt(0).toUpperCase())}</span></div>
    <div class="metric-grid"><div class="metric-card"><span>Всего записей</span><strong>${total}</strong><small>Во всех разделах</small></div>${config.sections.map((section) => `<a class="metric-card metric-link" href="#/${encodeURIComponent(section.id)}"><span>${html(section.label)}</span><strong>${state.rows[section.id]?.length || 0}</strong><small>${state.errors[section.id] ? "Ошибка загрузки" : "Открыть раздел →"}</small></a>`).join("")}</div>
    <div class="panel getting-started"><div class="panel-heading"><div><p class="eyebrow">БЫСТРЫЙ СТАРТ</p><h2>Готово к настройке</h2></div></div><div class="steps"><div><b>01</b><span>Переименуйте панель и разделы в <code>assets/config.js</code></span></div><div><b>02</b><span>Укажите поля таблиц и форм в том же файле</span></div><div><b>03</b><span>Переключите <code>mode</code> на <code>api</code> или подключите свой адаптер</span></div></div>${config.mode === "demo" ? '<button class="button button-quiet" data-action="reset">Сбросить демоданные</button>' : ""}</div>`;
}

function renderSection(section) {
  const error = state.errors[section.id];
  return `<div class="page-intro"><div><p class="eyebrow">РАЗДЕЛ / ${html(section.label.toUpperCase())}</p><h1>${html(section.label)}</h1><p>${html(section.description || "Управление данными проекта")}</p></div><button class="button button-primary" data-action="create" ${error ? "disabled" : ""}>＋ Добавить</button></div>
    ${error ? `<div class="panel error-panel"><h2>Не удалось загрузить данные</h2><p>${html(error)}</p><button class="button button-secondary" data-action="refresh">Повторить</button></div>` : `<div class="panel list-panel"><div class="list-toolbar"><div><h2>Все ${html(section.label.toLowerCase())}</h2><span class="muted">Всего: ${state.rows[section.id]?.length || 0}</span></div><label class="search-label"><span class="sr-only">Поиск</span><input id="search" type="search" placeholder="Поиск по записям…" autocomplete="off" /></label></div><div id="table-region"></div></div>`}`;
}

function renderTable() {
  const section = sectionFor(state.route);
  const target = $("#table-region");
  if (!section || !target) return;
  const fields = section.fields.filter((field) => field.table);
  const query = state.query.trim().toLocaleLowerCase();
  const filtered = (state.rows[section.id] || []).filter((row) => !query || fields.some((field) => String(row[field.key] ?? "").toLocaleLowerCase().includes(query)));
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  state.page = Math.min(state.page, pages);
  const start = (state.page - 1) * PAGE_SIZE;
  const visible = filtered.slice(start, start + PAGE_SIZE);
  target.innerHTML = filtered.length ? `<div class="table-scroll"><table><thead><tr>${fields.map((field) => `<th scope="col">${html(field.label)}</th>`).join("")}<th scope="col">Действия</th></tr></thead><tbody>${visible.map((row) => `<tr>${fields.map((field) => { const value = row[field.key] === "" || row[field.key] == null ? "—" : row[field.key]; return `<td>${field.badge ? `<span class="status-badge">${html(value)}</span>` : html(value)}</td>`; }).join("")}<td class="row-actions"><button class="text-button" data-action="edit" data-id="${html(row.id)}">Изменить</button><button class="text-button danger" data-action="delete" data-id="${html(row.id)}">Удалить</button></td></tr>`).join("")}</tbody></table></div>
    <div class="pagination"><span>Показано ${start + 1}–${Math.min(start + PAGE_SIZE, filtered.length)} из ${filtered.length}</span><div><button class="button button-secondary" data-action="previous" ${state.page === 1 ? "disabled" : ""}>← Назад</button><span>${state.page} / ${pages}</span><button class="button button-secondary" data-action="next" ${state.page === pages ? "disabled" : ""}>Вперёд →</button></div></div>`
    : `<div class="empty-state"><span aria-hidden="true">◫</span><h3>${query ? "Ничего не найдено" : "Здесь пока пусто"}</h3><p>${query ? "Попробуйте другой запрос." : "Добавьте первую запись, чтобы начать работу."}</p></div>`;
}

async function onMainClick(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  const section = sectionFor(state.route);
  if (action === "refresh") { await load(); return; }
  if (action === "reset" && config.mode === "demo") {
    if (!window.confirm("Вернуть исходные демонстрационные данные?")) return;
    await adapter.reset(); await load(); toast("Демонстрационные данные восстановлены"); return;
  }
  if (!section) return;
  if (action === "create") { openEditor(section); return; }
  if (action === "previous" || action === "next") { state.page += action === "next" ? 1 : -1; renderTable(); return; }
  const row = state.rows[section.id]?.find((item) => String(item.id) === button.dataset.id);
  if (!row) return;
  if (action === "edit") { openEditor(section, row); return; }
  if (action === "delete" && window.confirm(`Удалить «${row.name || row.id}»?`)) {
    button.disabled = true;
    try { await adapter.remove(section.id, row.id); await load(); toast("Запись удалена"); }
    catch (error) { button.disabled = false; toast(error.message, true); }
  }
}

function openEditor(section, row) {
  const dialog = $("#editor");
  dialog.dataset.section = section.id;
  dialog.dataset.editId = row ? String(row.id) : "";
  $("#editor-title").textContent = row ? `Изменить: ${section.singular}` : `Добавить: ${section.singular}`;
  $("#form-fields").innerHTML = section.fields.map((field) => {
    const id = `field-${field.key}`;
    const common = `id="${html(id)}" name="${html(field.key)}" ${field.required ? "required" : ""}`;
    const value = row?.[field.key] ?? "";
    let control;
    if (field.type === "select") control = `<select ${common}><option value="">Выберите значение</option>${(field.options || []).map((option) => `<option value="${html(option)}" ${value === option ? "selected" : ""}>${html(option)}</option>`).join("")}</select>`;
    else if (field.type === "textarea") control = `<textarea ${common} rows="4">${html(value)}</textarea>`;
    else control = `<input ${common} type="${["text", "email", "number", "date"].includes(field.type) ? field.type : "text"}" value="${html(value)}" />`;
    return `<label class="field" for="${html(id)}"><span>${html(field.label)}${field.required ? " *" : ""}</span>${control}</label>`;
  }).join("");
  dialog.showModal();
  $("#form-fields input, #form-fields select, #form-fields textarea")?.focus();
}

async function saveForm(event) {
  event.preventDefault();
  const dialog = $("#editor");
  const section = sectionFor(dialog.dataset.section);
  if (!section) return;
  const data = new FormData(event.currentTarget);
  const value = Object.fromEntries(section.fields.map((field) => [field.key, field.type === "number" && data.get(field.key) !== "" ? Number(data.get(field.key)) : data.get(field.key) || ""]));
  const button = event.currentTarget.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    if (dialog.dataset.editId) await adapter.update(section.id, dialog.dataset.editId, value);
    else await adapter.create(section.id, value);
    dialog.close();
    state.page = 1;
    await load();
    toast("Изменения сохранены");
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

let toastTimeout;
function toast(message, error = false) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.toggle("toast-error", error);
  element.hidden = false;
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => { element.hidden = true; }, 4200);
}

route();
load();
