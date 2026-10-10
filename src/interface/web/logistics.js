(function (root) {
  "use strict";
  const COLORS = ["#2563eb", "#7c3aed", "#0891b2", "#d97706", "#059669"];
  const EMPTY = {type: "FeatureCollection", features: []};
  const LABELS = {
    idle: "Ожидает рейс", stopped: "Остановлен", gate_check: "Проверка на КПП",
    queued: "В очереди к доку", loading: "Погрузка", in_transit: "В пути",
    unloading: "Разгрузка", delivered: "Доставлено", blocked: "Проезд закрыт",
    awaiting_route: "Ожидает маршрут", paused: "Пауза", gps_lost: "Нет GPS",
    factory: "Предприятие", warehouse: "Склад", supplier: "Поставщик",
    truck: "Грузовик", van: "Фургон", active: "Открыто", open: "Открыто",
    resolved: "Устранено", closed: "Завершено", pending: "Рассчитывается",
    ready: "Готово", partial: "Часть маршрутов недоступна", unavailable: "Недоступно",
    road: "По дорогам", routed: "По дорогам", live: "По дорогам", real: "По дорогам",
    fallback: "Резервный маршрут", cached: "Сохранённый маршрут", good: "По дорогам",
    gps_offline: "Потеря GPS", closure: "Перекрытие дороги", traffic: "Дорожная задержка",
    congestion: "Очередь", delay: "Задержка рейса", route_blocked: "Маршрут перекрыт"
  };
  const S = {
    host: null, initialized: false, active: false, map: null, mapReady: false,
    mapProblem: "", tileReady: false, firstFit: false, state: null, selection: null,
    follow: null, drawMode: null, point: null, timer: null, frame: null, frameAt: 0,
    pollBusy: false, controller: null, generation: 0, commandBusy: false,
    vehicleMarkers: new Map(), facilityMarkers: new Map(), colors: new Map(),
    routeKey: "", closureKey: "", lastReceived: 0, messageUntil: 0, resizeObserver: null,
    rowCaches: new Map(), requestNumber: 0, appliedRequest: 0
  };
  const $ = id => S.host?.querySelector(`#${id}`) || document.getElementById(id);
  const auth = () => root.ProductAuth || root.DispatchAuth;
  const user = () => auth()?.getSession?.()?.user;
  const canWrite = () => user()?.role === "admin" || user()?.operator_id === "dispatcher-1";
  const canResolve = () => user()?.role !== "admin" && user()?.operator_id === "dispatcher-1";
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const label = value => LABELS[value] || (value ? "Неизвестное состояние" : "—");
  const number = (value, digits = 0) => value === null || value === undefined || !Number.isFinite(Number(value)) ? "—" : Number(value).toLocaleString("ru-RU", {maximumFractionDigits: digits});
  const clamp = value => Math.max(0, Math.min(1, Number(value) || 0));
  const vehicles = () => S.state?.vehicles || [];
  const facilities = () => S.state?.facilities || [];
  const trips = () => S.state?.trips || [];
  const vehicle = id => vehicles().find(v => v.id === id);
  const facility = id => facilities().find(f => f.id === id);
  const tripFor = v => trips().find(t => t.id === v?.trip_id) || trips().find(t => t.vehicle_id === v?.id && t.status !== "delivered");
  const validPosition = p => Array.isArray(p) && p.length >= 2 && p.every(Number.isFinite) && Math.abs(p[0]) <= 180 && Math.abs(p[1]) <= 90;
  const stale = v => v.gps_age === null || v.gps_age === undefined || !Number.isFinite(Number(v.gps_age)) || Number(v.gps_age) > 5 || v.gps_available === false || /gps|offline/.test(v.status || "");
  const openIncident = i => !["resolved", "closed"].includes(i.state);
  const motionDuration = () => root.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? 0 : 350;
  const time = value => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString("ru-RU", {timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit", second: "2-digit"});
  };
  const dateTime = value => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("ru-RU", {timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"});
  };
  function colorFor(id) {
    if (!S.colors.has(id)) S.colors.set(id, COLORS[S.colors.size % COLORS.length]);
    return S.colors.get(id);
  }
  function text(id, value) { const node = $(id); if (node && node.textContent !== String(value)) node.textContent = value; }
  function message(value, failure = false, duration = 6000) {
    const node = $("lg-message");
    if (!node) return;
    node.textContent = value;
    node.classList.toggle("failure", failure);
    node.classList.toggle("lg-message--error", failure);
    node.hidden = !value;
    S.messageUntil = Date.now() + duration;
  }
  function report(error) {
    if (error.status === 401 || error.code === "session_identity_changed") {
      deactivate();
      auth()?.showLogin?.(error.code === "session_identity_changed" ? "В этой вкладке изменился аккаунт. Войдите заново." : "Сессия завершена. Войдите заново.");
    }
    message(error.message || "Сервер логистики недоступен", true, 12000);
  }
  async function request(path, method = "GET", body, signal) {
    const response = await fetch(`/api/logistics${path}`, {
      method, credentials: "same-origin", signal,
      headers: {...(auth()?.headers?.() || {}), ...(body !== undefined ? {"Content-Type": "application/json"} : {})},
      ...(body !== undefined ? {body: JSON.stringify(body)} : {})
    });
    let value;
    try { value = await response.json(); } catch (_) { value = {}; }
    if (!response.ok) {
      const detail = typeof value.detail === "string" ? value.detail : value.detail?.message;
      const error = new Error(value.message || detail || `Ошибка сервера (${response.status})`);
      error.status = response.status;
      error.code = value.code || value.detail?.code;
      throw error;
    }
    return value;
  }
  async function poll() {
    if (!S.active || S.pollBusy) return;
    S.pollBusy = true;
    const generation = S.generation, requestNumber = ++S.requestNumber;
    const controller = new AbortController();
    S.controller = controller;
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const value = await request("/state", "GET", undefined, controller.signal);
      if (S.active && generation === S.generation && requestNumber >= S.appliedRequest) {
        S.appliedRequest = requestNumber;
        receive(value);
        if (Date.now() > S.messageUntil) message("Сервер подключён · состояние обновляется раз в секунду", false, 0);
      }
    } catch (error) {
      if (S.active && generation === S.generation) {
        if (error.name === "AbortError") message("Сервер не ответил вовремя. Позиции сохранены до восстановления связи.", true, 12000);
        else report(error);
      }
    } finally {
      clearTimeout(timeout);
      if (S.controller === controller) S.controller = null;
      if (generation === S.generation) S.pollBusy = false;
    }
  }
  async function command(path, body, success) {
    if (!canWrite() || S.commandBusy) return;
    S.commandBusy = true;
    renderControls();
    message("Выполняется…", false, 30000);
    try {
      const value = await request(path, "POST", body);
      S.generation += 1;
      S.controller?.abort();
      S.pollBusy = false;
      S.appliedRequest = ++S.requestNumber;
      if (Array.isArray(value.vehicles) && Array.isArray(value.facilities)) receive(value);
      else if (Array.isArray(value.state?.vehicles)) receive(value.state);
      message(success);
      if (S.active) poll();
      return true;
    } catch (error) { report(error); return false; }
    finally { S.commandBusy = false; renderControls(); }
  }
  function badge(value, variant) {
    const kind = variant || (value === "delivered" || value === "ready" || value === "resolved" ? "success" : ["blocked", "awaiting_route", "unavailable", "gps_lost"].includes(value) ? "danger" : ["queued", "loading", "unloading", "partial"].includes(value) ? "warning" : "muted");
    return `<span class="lg-badge lg-badge--${kind}">${esc(label(value))}</span>`;
  }
  function action(name, id, title, disabled = false, className = "") {
    return `<button type="button" class="${esc(className)}" data-lg-action="${esc(name)}" data-id="${esc(id)}"${disabled ? " disabled" : ""}>${esc(title)}</button>`;
  }
  function progress(value, title) {
    const percent = Math.round(clamp(value) * 100);
    return `<div class="lg-progress"><span>${esc(title)} · ${percent}%</span><progress max="100" value="${percent}" aria-label="${esc(title)}"></progress></div>`;
  }
  function updateMarkup(node, markup) {
    if (!node || node._lgMarkup === markup) return;
    const focused = node.contains(document.activeElement) ? document.activeElement : null;
    const focusAction = focused?.dataset.lgAction, focusId = focused?.dataset.id;
    node.innerHTML = markup;
    node._lgMarkup = markup;
    if (focusAction) Array.from(node.querySelectorAll("[data-lg-action]")).find(button => button.dataset.lgAction === focusAction && button.dataset.id === focusId)?.focus({preventScroll: true});
  }
  function rows(id, values, className, markup, emptyText) {
    const container = $(id);
    if (!container) return;
    let cache = S.rowCaches.get(id);
    if (!cache) { cache = new Map(); S.rowCaches.set(id, cache); container.replaceChildren(); }
    const keys = new Set(values.map(v => String(v.id)));
    for (const [key, node] of cache) if (!keys.has(key)) { node.remove(); cache.delete(key); }
    container.querySelector(".lg-empty")?.remove();
    values.forEach((value, index) => {
      const key = String(value.id);
      let node = cache.get(key);
      if (!node) { node = document.createElement("article"); node.className = className; cache.set(key, node); }
      updateMarkup(node, markup(value, index));
      const current = container.children[index];
      if (current !== node) container.insertBefore(node, current || null);
    });
    if (!values.length) { const node = document.createElement("p"); node.className = "lg-empty muted"; node.textContent = emptyText; container.appendChild(node); }
  }
  function selectOptions(id, values, placeholder) {
    const node = $(id);
    if (!node) return;
    const key = JSON.stringify(values);
    if (node._lgOptions === key) return;
    const previous = node.value;
    node.replaceChildren();
    if (placeholder) { const option = document.createElement("option"); option.value = ""; option.textContent = placeholder; node.appendChild(option); }
    for (const value of values) { const option = document.createElement("option"); option.value = value.id; option.textContent = value.name; node.appendChild(option); }
    if (values.some(value => value.id === previous)) node.value = previous;
    node._lgOptions = key;
  }
  function renderControls() {
    const writable = canWrite(), busy = S.commandBusy, state = S.state;
    const permissions = {
      "lg-start": !state, "lg-pause": !state?.running || state.paused,
      "lg-resume": !state?.running || !state.paused, "lg-reset": !state, "lg-speed": !state, "lg-scenario": false,
      "lg-recalculate": !state, "lg-add-closure": !S.map, "lg-add-facility": !S.map,
      "lg-trip-create": !state, "lg-facility-save": false, "lg-closure-save": false
    };
    for (const [id, disabled] of Object.entries(permissions)) if ($(id)) $(id).disabled = !writable || busy || disabled;
    for (const id of ["lg-trip-vehicle", "lg-trip-origin", "lg-trip-destination", "lg-trip-cargo", "lg-trip-weight", "lg-trip-quantity"]) if ($(id)) $(id).disabled = !writable || busy;
    S.host?.querySelectorAll('[data-lg-action="resolve"]').forEach(node => { node.disabled = !canResolve() || busy; });
    S.host?.querySelectorAll('[data-lg-action="reopen"], [data-lg-action="gps"]').forEach(node => { node.disabled = !writable || busy; });
    if ($("lg-fit")) $("lg-fit").disabled = !S.map;
    if ($("lg-export")) $("lg-export").disabled = !state;
  }
  function renderStatus() {
    const state = S.state;
    if (!state) return;
    text("lg-clock", `${time(state.clock)} МСК · ${state.paused ? "пауза" : state.running ? "движение" : "остановлено"} · ×${number(state.speed)}`);
    const routing = state.routing_status;
    let routeText;
    if (routing && typeof routing === "object") {
      routeText = `Маршрутизация: ${label(routing.state)}`;
      if (routing.pending) routeText += ` · ожидают расчёта: ${number(routing.pending)}`;
      if (routing.message) routeText += ` · ${routing.message}`;
    } else routeText = `Маршрутизация: ${routing ? String(routing) : "состояние недоступно"}`;
    const mapText = S.mapProblem || (!S.map ? "Карта недоступна" : !S.tileReady ? "Подложка карты: загрузка, требуется интернет" : "Подложка карты подключена");
    text("lg-routing-status", `${routeText}. ${mapText}`);
    $("lg-routing-status")?.classList.toggle("failure", Boolean(S.mapProblem) || ["unavailable", "partial"].includes(routing?.state));
  }
  function render() {
    const state = S.state;
    if (!state) return;
    const activeIncidents = (state.incidents || []).filter(openIncident);
    const moving = vehicles().filter(v => v.status === "in_transit" && !stale(v)).length;
    const delivered = trips().filter(t => t.status === "delivered");
    const queued = facilities().reduce((sum, f) => sum + (Array.isArray(f.queue) ? f.queue.length : Number(f.queue) || 0), 0);
    updateMarkup($("lg-kpis"), [["Машин в пути", moving], ["Доставлено рейсов", delivered.length], ["В очередях", queued], ["Происшествий", activeIncidents.length], ["Доставлено, т", number(delivered.reduce((sum, t) => sum + (Number(t.cargo?.weight_tonnes) || 0), 0), 1)]].map(([name, value]) => `<div class="lg-kpi"><small>${esc(name)}</small><strong>${esc(value)}</strong></div>`).join(""));
    selectOptions("lg-scenario", (state.scenarios || []).map(s => ({id: s.id, name: s.name})));
    if ($("lg-scenario") && !$("lg-scenario").dataset.userChosen) $("lg-scenario").value = state.scenario;
    if ($("lg-speed") && document.activeElement !== $("lg-speed") && !S.commandBusy) $("lg-speed").value = String(state.speed);
    selectOptions("lg-trip-vehicle", vehicles().map(v => ({id: v.id, name: `${v.name} · ${number(v.capacity, 1)} т · ${label(v.status)}`})), "Выберите машину");
    const facilityOptions = facilities().map(f => ({id: f.id, name: f.name}));
    selectOptions("lg-trip-origin", facilityOptions, "Откуда");
    selectOptions("lg-trip-destination", facilityOptions, "Куда");
    rows("lg-vehicles", vehicles(), "lg-card lg-vehicle-card", v => {
      const trip = tripFor(v), lost = stale(v), selected = S.selection?.kind === "vehicle" && S.selection.id === v.id;
      const destination = trip ? facility(trip.destination_id)?.name || "Неизвестный объект" : "Рейс не назначен";
      return `<div class="lg-card-head"><strong>${esc(v.name)}</strong>${badge(lost ? "gps_lost" : v.status)}</div><p class="lg-card-meta">${esc(v.plate || "Без номера")} · ${esc(label(v.type))} · ${number(v.speed_kmh, 1)} км/ч</p><p class="lg-card-meta">${esc(destination)} · загрузка ${number(v.load, 1)} / ${number(v.capacity, 1)} т</p>${trip ? progress(trip.progress, "Рейс") : ""}<p class="lg-card-meta">${lost ? `Последний GPS: ${number(v.gps_age, 1)} с назад` : `GPS: ${number(v.gps_age, 1)} с назад`}</p><div class="lg-card-actions">${action("vehicle", v.id, selected ? S.follow === v.id ? "Сопровождение включено" : "Продолжить сопровождение" : "Показать на карте")}</div>`;
    }, "Машины пока не добавлены");
    rows("lg-facilities", facilities(), "lg-card lg-facility-card", f => {
      const queue = Array.isArray(f.queue) ? f.queue.length : Number(f.queue) || 0;
      return `<div class="lg-card-head"><strong>${esc(f.name)}</strong><span class="lg-badge lg-badge--muted">${esc(label(f.kind))}</span></div><p class="lg-card-meta">Запас: ${number(f.stock)} ед. · доков: ${number(f.docks)} · очередь: ${queue}</p><div class="lg-card-actions">${action("facility", f.id, "На карте")}${action("open-facility", f.id, "Открыть предприятие")}</div>`;
    }, "Предприятия пока не добавлены");
    rows("lg-incidents", activeIncidents.slice().sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 40), "lg-card lg-incident-card", i => {
      const target = vehicle(i.vehicle_id)?.name || facility(i.facility_id)?.name || "Дорожная сеть";
      return `<div class="lg-card-head"><strong>${esc(i.title || label(i.kind))}</strong><span class="lg-badge lg-badge--danger">Открыто</span></div><p class="lg-card-meta">${esc(target)} · ${dateTime(i.created_at)} МСК</p><div class="lg-card-actions">${action("incident", i.id, "Подробности")}${canResolve() ? action("resolve", i.id, "Устранить", S.commandBusy) : ""}</div>`;
    }, "Открытых происшествий нет");
    rows("lg-events", (state.events || []).slice().sort((a, b) => String(b.time).localeCompare(String(a.time))).slice(0, 60), "lg-event-row", e => `<time>${time(e.time)}</time><span>${esc(e.message || "Событие логистики")}</span>`, "События появятся после запуска");
    rows("lg-closures", (state.closures || []).filter(c => c.active), "lg-card lg-closure-card", c => `<div class="lg-card-head"><strong>${esc(c.reason || "Перекрытие дороги")}</strong><span class="lg-badge lg-badge--danger">Проезд закрыт</span></div><div class="lg-card-actions">${action("closure", c.id, "На карте")}${canWrite() ? action("reopen", c.id, "Открыть участок", S.commandBusy) : ""}</div>`, "Активных перекрытий нет");
    renderSelection(); renderStatus(); renderControls();
  }
  function detailRows(values) {
    return `<dl class="lg-details">${values.map(([key, value]) => `<div><dt>${esc(key)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>`;
  }
  function renderSelection() {
    const node = $("lg-selection"), selection = S.selection;
    if (!node) return;
    if (!selection) { updateMarkup(node, '<p class="muted">Выберите машину или предприятие на карте, чтобы увидеть подробности.</p>'); return; }
    let markup = "";
    if (selection.kind === "vehicle") {
      const v = vehicle(selection.id);
      if (!v) { clearSelection(); return; }
      const trip = tripFor(v), lost = stale(v);
      markup = `<div class="lg-card-head"><h3>${esc(v.name)}</h3>${badge(lost ? "gps_lost" : v.status)}</div>`;
      markup += detailRows([["Номер", v.plate || "—"], ["Тип", label(v.type)], ["Скорость", `${number(v.speed_kmh, 1)} км/ч`], ["Загрузка", `${number(v.load, 1)} / ${number(v.capacity, 1)} т`], ["GPS", `${lost ? "Последняя известная позиция" : "Сигнал получен"} · ${number(v.gps_age, 1)} с назад`], ["Камера", S.follow === v.id ? "Сопровождает машину" : "Ручное управление"]]);
      if (trip) {
        markup += detailRows([["Откуда", facility(trip.origin_id)?.name || "—"], ["Куда", facility(trip.destination_id)?.name || "—"], ["Груз", trip.cargo?.name || "—"], ["Количество", `${number(trip.cargo?.quantity)} ед. · ${number(trip.cargo?.weight_tonnes, 1)} т`], ["Маршрут", trip.route?.coordinates?.length >= 2 ? `${number(trip.route.distance_km, 1)} км · ${trip.route.provider || "поставщик не указан"}` : "Недоступен, движение ожидает расчёта"], ["Расчётное прибытие", trip.eta ? `${dateTime(trip.eta)} МСК (демо)` : "Не рассчитано"]]);
        markup += progress(trip.progress, "Пройдено по маршруту");
        if (["loading", "unloading"].includes(trip.status)) markup += progress(trip.loading_progress, trip.status === "loading" ? "Погрузка" : "Разгрузка");
      }
      markup += `<div class="lg-card-actions">${action("vehicle", v.id, "Центрировать и сопровождать")}${action("unfollow", v.id, "Отключить сопровождение", !S.follow)}${canWrite() ? action("gps", v.id, lost ? "Восстановить GPS" : "Отключить GPS (демо)", S.commandBusy) : ""}${action("clear", "", "Закрыть")}</div>`;
    } else if (selection.kind === "facility") {
      const f = facility(selection.id);
      if (!f) { clearSelection(); return; }
      const queue = Array.isArray(f.queue) ? f.queue.map(id => vehicle(id)?.name || id).join(", ") : number(f.queue);
      markup = `<div class="lg-card-head"><h3>${esc(f.name)}</h3>${badge(f.kind)}</div>` + detailRows([["Тип", label(f.kind)], ["Запас", `${number(f.stock)} единиц`], ["Вместимость", `${number(f.capacity)} единиц`], ["Доки", number(f.docks)], ["Очередь", queue || "Нет ожидающих машин"], ["Координаты", `${number(f.lat, 5)}, ${number(f.lon, 5)}`]]) + `<div class="lg-card-actions">${action("open-facility", f.id, "Открыть предприятие")}${action("clear", "", "Закрыть")}</div>`;
    } else if (selection.kind === "incident") {
      const incident = (S.state.incidents || []).find(i => i.id === selection.id);
      if (!incident) { clearSelection(); return; }
      markup = `<div class="lg-card-head"><h3>${esc(incident.title || label(incident.kind))}</h3>${badge(incident.state)}</div>` + detailRows([["Транспорт", vehicle(incident.vehicle_id)?.name || "—"], ["Объект", facility(incident.facility_id)?.name || "—"], ["Время", `${dateTime(incident.created_at)} МСК`]]) + `<div class="lg-card-actions">${incident.vehicle_id ? action("vehicle", incident.vehicle_id, "Показать машину") : ""}${incident.facility_id ? action("facility", incident.facility_id, "Показать объект") : ""}${canResolve() && openIncident(incident) ? action("resolve", incident.id, "Устранить", S.commandBusy) : ""}${action("clear", "", "Закрыть")}</div>`;
    } else if (selection.kind === "closure") {
      const closure = (S.state.closures || []).find(c => c.id === selection.id);
      if (!closure) { clearSelection(); return; }
      markup = `<h3>${esc(closure.reason || "Перекрытие дороги")}</h3><p>${closure.active ? "Участок закрыт для движения. Маршруты рассчитывает сервер с учётом перекрытия." : "Проезд открыт."}</p><div class="lg-card-actions">${canWrite() && closure.active ? action("reopen", closure.id, "Открыть участок", S.commandBusy) : ""}${action("clear", "", "Закрыть")}</div>`;
    }
    updateMarkup(node, markup);
  }
  function setSelection(kind, id, center = true) {
    S.selection = {kind, id}; S.follow = kind === "vehicle" ? id : null;
    if (center && S.map) {
      let position;
      if (kind === "vehicle") position = S.vehicleMarkers.get(id)?.marker.getLngLat().toArray() || vehicle(id)?.position;
      if (kind === "facility") { const f = facility(id); if (f) position = [f.lon, f.lat]; }
      if (kind === "incident") {
        const incident = (S.state?.incidents || []).find(i => i.id === id), f = facility(incident?.facility_id);
        position = vehicle(incident?.vehicle_id)?.position || (f ? [f.lon, f.lat] : null);
      }
      if (kind === "closure") position = (S.state?.closures || []).find(c => c.id === id)?.polygon?.[0];
      if (validPosition(position)) S.map.easeTo({center: position, zoom: Math.max(S.map.getZoom(), kind === "facility" ? 12 : 11), duration: motionDuration(), essential: false});
    }
    updateLayers(); updateMarkerAppearance(); renderSelection();
    root.dispatchEvent(new CustomEvent("logistics:selection", {detail: {...S.selection}}));
  }
  function clearSelection() {
    S.selection = null; S.follow = null;
    updateLayers(); updateMarkerAppearance(); renderSelection();
    root.dispatchEvent(new CustomEvent("logistics:selection", {detail: null}));
  }
  function stopFollowing() { if (S.follow) { S.follow = null; renderSelection(); } }
  function fitNetwork() {
    stopFollowing();
    if (!S.map || !root.maplibregl) return;
    const positions = facilities().map(f => [f.lon, f.lat]).concat(vehicles().map(v => v.position)).filter(validPosition);
    if (!positions.length) return;
    const bounds = new root.maplibregl.LngLatBounds(); positions.forEach(p => bounds.extend(p));
    S.map.fitBounds(bounds, {padding: {top: 75, right: 75, bottom: 75, left: 75}, maxZoom: 11, duration: motionDuration()});
    S.firstFit = true;
  }
  function openFacility(id) { const selected = facility(id); if (selected) root.dispatchEvent(new CustomEvent("logistics:facility", {detail: {...selected}})); }
  function installLayers() {
    if (!S.map || S.map.getSource("lg-routes")) return;
    S.map.addSource("lg-routes", {type: "geojson", data: EMPTY});
    S.map.addSource("lg-closures", {type: "geojson", data: EMPTY});
    S.map.addLayer({id: "lg-closure-fill", type: "fill", source: "lg-closures", paint: {"fill-color": "#dc2626", "fill-opacity": 0.2}});
    S.map.addLayer({id: "lg-closure-line", type: "line", source: "lg-closures", paint: {"line-color": "#b91c1c", "line-width": 2, "line-dasharray": [3, 2]}});
    S.map.addLayer({id: "lg-route-outline", type: "line", source: "lg-routes", layout: {"line-join": "round", "line-cap": "round"}, paint: {"line-color": "#ffffff", "line-width": 6, "line-opacity": 0.8}});
    S.map.addLayer({id: "lg-route-lines", type: "line", source: "lg-routes", layout: {"line-join": "round", "line-cap": "round"}, paint: {"line-color": ["get", "color"], "line-width": 3, "line-opacity": 0.8}});
    S.map.addLayer({id: "lg-route-selected-outline", type: "line", source: "lg-routes", filter: ["==", ["get", "selected"], true], layout: {"line-join": "round", "line-cap": "round"}, paint: {"line-color": "#111827", "line-width": 9, "line-opacity": 0.9}});
    S.map.addLayer({id: "lg-route-selected-light", type: "line", source: "lg-routes", filter: ["==", ["get", "selected"], true], layout: {"line-join": "round", "line-cap": "round"}, paint: {"line-color": "#ffffff", "line-width": 7}});
    S.map.addLayer({id: "lg-route-selected", type: "line", source: "lg-routes", filter: ["==", ["get", "selected"], true], layout: {"line-join": "round", "line-cap": "round"}, paint: {"line-color": ["get", "color"], "line-width": 4.5}});
    S.map.on("click", "lg-closure-fill", event => {
      if (S.drawMode) return;
      const id = event.features?.[0]?.properties?.id; if (id) setSelection("closure", id, false);
    });
    S.mapReady = true; S.routeKey = ""; S.closureKey = ""; updateLayers();
  }
  function createMap() {
    const container = $("lg-map");
    if (S.map || !container) return;
    if (!root.maplibregl) { S.mapProblem = "Карта недоступна: библиотека карты не загружена. Таблицы и управление остаются доступны."; renderStatus(); return; }
    try {
      S.map = new root.maplibregl.Map({
        container, style: "https://tiles.openfreemap.org/styles/liberty", center: [37.62, 55.75], zoom: 6,
        attributionControl: false, maxZoom: 17, maxTileCacheSize: 96, canvasContextAttributes: {antialias: false},
        locale: {"NavigationControl.ZoomIn": "Приблизить", "NavigationControl.ZoomOut": "Отдалить", "NavigationControl.ResetBearing": "Север сверху", "AttributionControl.ToggleAttribution": "Источники карты", "Map.Title": "Карта логистики"}
      });
      S.map.addControl(new root.maplibregl.NavigationControl(), "top-right");
      S.map.addControl(new root.maplibregl.AttributionControl({compact: true}), "bottom-right");
      S.map.on("load", () => { installLayers(); syncMarkers(); if (!S.firstFit) fitNetwork(); renderStatus(); });
      S.map.on("error", () => {
        S.mapProblem = "Подложка карты или её часть недоступна. Проверьте интернет; серверные объекты остаются на карте.";
        renderStatus();
        if (!S.mapReady && !S.map.getStyle()?.layers?.length) {
          S.map.setStyle({version: 8, sources: {}, layers: [{id: "lg-offline-background", type: "background", paint: {"background-color": "#e8edf4"}}]});
          S.map.once("style.load", installLayers);
        }
      });
      S.map.on("sourcedata", event => {
        if (event.sourceId && !event.sourceId.startsWith("lg-") && event.sourceDataType === "content" && event.isSourceLoaded) { S.tileReady = true; renderStatus(); }
      });
      const manual = event => { if (event.originalEvent) stopFollowing(); };
      S.map.on("dragstart", manual); S.map.on("zoomstart", manual); S.map.on("rotatestart", manual); S.map.on("pitchstart", manual);
      S.map.on("click", event => {
        if (!S.drawMode || !canWrite()) return;
        const mode = S.drawMode;
        setDrawMode(null);
        S.point = {lon: event.lngLat.lng, lat: event.lngLat.lat};
        const dialog = $(mode === "closure" ? "lg-closure-dialog" : "lg-facility-dialog");
        if (!dialog) return;
        let coordinates = dialog.querySelector(".lg-coordinate-preview");
        if (!coordinates) { coordinates = document.createElement("p"); coordinates.className = "lg-coordinate-preview muted"; dialog.insertBefore(coordinates, dialog.firstChild); }
        coordinates.textContent = `Выбранная точка: ${number(S.point.lat, 5)}, ${number(S.point.lon, 5)}`;
        showDialog(dialog);
      });
      S.map.getCanvas().addEventListener("wheel", stopFollowing, {passive: true});
      if (root.ResizeObserver) { S.resizeObserver = new ResizeObserver(() => { if (S.active) S.map?.resize(); }); S.resizeObserver.observe(container); }
    } catch (_) { S.map = null; S.mapProblem = "Карта недоступна: браузер не смог создать WebGL-карту. Используйте списки машин и предприятий."; renderStatus(); }
  }
  function updateLayers() {
    if (!S.mapReady || !S.state) return;
    const routeData = {type: "FeatureCollection", features: trips().filter(t => t.route?.coordinates?.length >= 2 && t.status !== "delivered").map(t => ({type: "Feature", geometry: {type: "LineString", coordinates: t.route.coordinates.filter(validPosition)}, properties: {id: t.id, vehicle_id: t.vehicle_id, color: colorFor(t.vehicle_id), selected: S.selection?.kind === "vehicle" && S.selection.id === t.vehicle_id}})).filter(f => f.geometry.coordinates.length >= 2)};
    const routeKey = JSON.stringify(routeData);
    if (routeKey !== S.routeKey) { S.map.getSource("lg-routes")?.setData(routeData); S.routeKey = routeKey; }
    const closureData = {type: "FeatureCollection", features: (S.state.closures || []).filter(c => c.active && c.polygon?.length >= 3).map(c => {
      const ring = c.polygon.filter(validPosition).map(p => p.slice());
      if (ring.length && (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1])) ring.push(ring[0].slice());
      return {type: "Feature", geometry: {type: "Polygon", coordinates: [ring]}, properties: {id: c.id, reason: c.reason}};
    }).filter(f => f.geometry.coordinates[0].length >= 4)};
    const closureKey = JSON.stringify(closureData);
    if (closureKey !== S.closureKey) { S.map.getSource("lg-closures")?.setData(closureData); S.closureKey = closureKey; }
  }
  function routeGeometry(trip) {
    const points = trip?.route?.coordinates;
    if (!Array.isArray(points) || points.length < 2 || !points.every(validPosition)) return null;
    const distances = [0];
    for (let i = 1; i < points.length; i++) {
      const dx = (points[i][0] - points[i - 1][0]) * Math.cos((points[i][1] + points[i - 1][1]) * Math.PI / 360), dy = points[i][1] - points[i - 1][1];
      distances.push(distances[i - 1] + Math.hypot(dx, dy));
    }
    return {points, distances, total: distances[distances.length - 1]};
  }
  function positionOnRoute(geometry, amount) {
    if (!geometry?.total) return null;
    const target = clamp(amount) * geometry.total;
    let low = 1, high = geometry.distances.length - 1;
    while (low < high) { const middle = Math.floor((low + high) / 2); if (geometry.distances[middle] < target) low = middle + 1; else high = middle; }
    const index = low, start = geometry.points[index - 1], end = geometry.points[index], length = geometry.distances[index] - geometry.distances[index - 1];
    const fraction = length ? (target - geometry.distances[index - 1]) / length : 1;
    return [start[0] + (end[0] - start[0]) * fraction, start[1] + (end[1] - start[1]) * fraction];
  }
  function markerNode(className, symbolClass, symbol, name, onClick) {
    const button = document.createElement("button"); button.type = "button"; button.className = className;
    const icon = document.createElement("span"); icon.className = symbolClass; icon.textContent = symbol;
    const title = document.createElement("span"); title.className = "lg-marker-label"; title.textContent = name;
    button.append(icon, title);
    button.addEventListener("click", event => { event.stopPropagation(); if (!S.drawMode) onClick(); });
    button.addEventListener("dblclick", event => { event.stopPropagation(); event.preventDefault(); if (!S.drawMode) onClick(); });
    return {button, icon, title};
  }
  function syncMarkers(previous) {
    if (!S.map || !S.state) return;
    const vehicleIds = new Set(vehicles().map(v => v.id));
    for (const [id, record] of S.vehicleMarkers) if (!vehicleIds.has(id)) { record.marker.remove(); S.vehicleMarkers.delete(id); }
    vehicles().forEach((v, index) => {
      let record = S.vehicleMarkers.get(v.id);
      if (!record && validPosition(v.position)) {
        const dom = markerNode("lg-truck-marker", "lg-truck-body", String(index + 1), v.name, () => setSelection("vehicle", v.id));
        const arrow = document.createElement("span"); arrow.className = "lg-truck-arrow"; arrow.textContent = "▲"; arrow.setAttribute("aria-hidden", "true"); dom.button.prepend(arrow);
        record = {...dom, arrow, marker: new root.maplibregl.Marker({element: dom.button, anchor: "center"}).setLngLat(v.position).addTo(S.map), displayed: v.position.slice(), displayedProgress: null, tripId: null, routeRevision: null, geometry: null};
        S.vehicleMarkers.set(v.id, record);
      }
      if (!record) return;
      const trip = tripFor(v), oldVehicle = previous?.vehicles?.find(item => item.id === v.id), oldTrip = previous?.trips?.find(item => item.id === trip?.id);
      record.from = record.displayed?.slice() || v.position.slice();
      record.to = validPosition(v.position) ? v.position.slice() : record.from.slice();
      record.start = performance.now(); record.duration = stale(v) || S.state.paused || !S.state.running || motionDuration() === 0 ? 0 : 1000;
      record.fromHeading = record.displayedHeading ?? (Number(oldVehicle?.heading) || Number(v.heading) || 0);
      record.toHeading = Number(v.heading) || 0;
      const sameRoute = trip && oldTrip?.id === trip.id && oldTrip.route_revision === trip.route_revision;
      record.fromProgress = sameRoute ? record.displayedProgress ?? clamp(oldTrip.progress) : null;
      record.toProgress = trip ? clamp(trip.progress) : null;
      record.routeInterpolate = sameRoute && v.status === "in_transit" && !stale(v) && record.fromProgress !== null && record.toProgress >= record.fromProgress;
      if (record.tripId !== trip?.id || record.routeRevision !== trip?.route_revision) { record.geometry = routeGeometry(trip); record.tripId = trip?.id; record.routeRevision = trip?.route_revision; }
      record.title.textContent = v.name;
      record.button.title = `${v.name} · ${stale(v) ? "GPS потерян, последняя известная позиция" : label(v.status)}`;
      record.button.setAttribute("aria-label", `Выбрать и сопровождать ${v.name}${stale(v) ? ", GPS потерян" : ""}`);
      record.button.style.setProperty("--lg-truck-color", colorFor(v.id));
      if (stale(v)) { record.from = record.to.slice(); record.duration = 0; }
    });
    const facilityIds = new Set(facilities().map(f => f.id));
    for (const [id, record] of S.facilityMarkers) if (!facilityIds.has(id)) { record.marker.remove(); S.facilityMarkers.delete(id); }
    facilities().forEach(f => {
      if (!validPosition([f.lon, f.lat])) return;
      let record = S.facilityMarkers.get(f.id);
      if (!record) {
        const dom = markerNode("lg-facility-marker", "lg-facility-symbol", f.kind === "factory" ? "П" : f.kind === "warehouse" ? "С" : "ПС", f.name, () => setSelection("facility", f.id));
        record = {...dom, marker: new root.maplibregl.Marker({element: dom.button, anchor: "center"}).setLngLat([f.lon, f.lat]).addTo(S.map)}; S.facilityMarkers.set(f.id, record);
      }
      record.marker.setLngLat([f.lon, f.lat]); record.title.textContent = f.name;
      record.button.title = `${f.name} · ${label(f.kind)}`; record.button.setAttribute("aria-label", `Открыть сведения: ${f.name}`);
    });
    updateMarkerAppearance(); paint(performance.now());
  }
  function updateMarkerAppearance() {
    for (const [id, record] of S.vehicleMarkers) {
      const v = vehicle(id);
      record.button.classList.toggle("is-selected", S.selection?.kind === "vehicle" && S.selection.id === id);
      record.button.classList.toggle("is-stale", !v || stale(v) || (S.lastReceived && Date.now() - S.lastReceived > 5000));
      record.button.classList.toggle("is-alert", (S.state?.incidents || []).some(i => i.vehicle_id === id && openIncident(i)));
    }
    for (const [id, record] of S.facilityMarkers) record.button.classList.toggle("is-selected", S.selection?.kind === "facility" && S.selection.id === id);
  }
  function paint(now) {
    for (const record of S.vehicleMarkers.values()) {
      if (!record.to) continue;
      const fraction = record.duration ? clamp((now - record.start) / record.duration) : 1;
      const amount = record.fromProgress !== null && record.toProgress !== null ? record.fromProgress + (record.toProgress - record.fromProgress) * fraction : null;
      const routed = record.routeInterpolate ? positionOnRoute(record.geometry, amount) : null;
      record.displayed = routed || [record.from[0] + (record.to[0] - record.from[0]) * fraction, record.from[1] + (record.to[1] - record.from[1]) * fraction];
      record.displayedProgress = amount; record.marker.setLngLat(record.displayed);
      const difference = ((record.toHeading - record.fromHeading + 540) % 360) - 180;
      record.displayedHeading = record.fromHeading + difference * fraction;
      record.arrow.style.transform = `rotate(${record.displayedHeading - (S.map?.getBearing() || 0)}deg)`;
    }
    if (S.follow && S.map) { const followed = S.vehicleMarkers.get(S.follow); if (followed?.displayed && !S.map.isMoving()) S.map.jumpTo({center: followed.displayed}); }
    if (S.lastReceived && Date.now() - S.lastReceived > 5000) updateMarkerAppearance();
  }
  function animate(now) {
    if (!S.active) { S.frame = null; return; }
    if (!document.hidden && now - S.frameAt >= 1000 / 15) { S.frameAt = now; paint(now); }
    S.frame = requestAnimationFrame(animate);
  }
  function receive(value) {
    if (!value || !Array.isArray(value.vehicles) || !Array.isArray(value.facilities)) throw new Error("Сервер вернул неполное состояние логистики");
    const previous = S.state; S.state = value; S.lastReceived = Date.now();
    render(); syncMarkers(previous); updateLayers(); if (S.map && !S.firstFit) fitNetwork();
  }
  function setDrawMode(mode) {
    if (mode && (!canWrite() || !S.map)) return;
    S.drawMode = mode; S.point = null; stopFollowing();
    if (S.map) S.map.getCanvas().style.cursor = mode ? "crosshair" : "";
    $("lg-add-closure")?.setAttribute("aria-pressed", String(mode === "closure"));
    $("lg-add-facility")?.setAttribute("aria-pressed", String(mode === "facility"));
    if (mode) message(mode === "closure" ? "Нажмите на дороге: в выбранной точке будет создано перекрытие. Esc — отмена." : "Нажмите на карте, чтобы выбрать место нового предприятия. Esc — отмена.", false, 60000);
  }
  function showDialog(dialog) {
    if (typeof dialog.showModal === "function") { if (!dialog.open) dialog.showModal(); }
    else { dialog.hidden = false; dialog.setAttribute("open", ""); }
  }
  function closeDialog(id) {
    const dialog = $(id);
    if (!dialog) return;
    if (typeof dialog.close === "function") dialog.close();
    else { dialog.hidden = true; dialog.removeAttribute("open"); }
    S.point = null;
  }
  async function saveFacility(event) {
    event?.preventDefault();
    if (!S.point) { message("Сначала выберите место предприятия на карте", true); return; }
    const name = $("lg-facility-name")?.value.trim(), kind = $("lg-facility-kind")?.value;
    if (!name) { message("Введите название предприятия", true); $("lg-facility-name")?.focus(); return; }
    if (await command("/facilities", {name, kind, ...S.point}, "Предприятие добавлено сервером")) closeDialog("lg-facility-dialog");
  }
  async function saveClosure(event) {
    event?.preventDefault();
    if (!S.point) { message("Сначала выберите место перекрытия на карте", true); return; }
    const radius_m = Number($("lg-closure-radius")?.value), reason = $("lg-closure-reason")?.value.trim();
    if (!Number.isFinite(radius_m) || radius_m <= 0) { message("Укажите положительный радиус перекрытия в метрах", true); return; }
    if (await command("/closures", {...S.point, radius_m, reason: reason || "Перекрытие дороги"}, "Перекрытие создано. Сервер пересчитывает затронутые маршруты.")) closeDialog("lg-closure-dialog");
  }
  async function createTrip(event) {
    event.preventDefault();
    const vehicle_id = $("lg-trip-vehicle")?.value, origin_id = $("lg-trip-origin")?.value, destination_id = $("lg-trip-destination")?.value;
    const name = $("lg-trip-cargo")?.value.trim() || "Комплектующие", quantity = Number($("lg-trip-quantity")?.value), weight_tonnes = Number($("lg-trip-weight")?.value);
    if (!vehicle_id || !origin_id || !destination_id) { message("Выберите машину, пункт отправления и пункт назначения", true); return; }
    if (origin_id === destination_id) { message("Пункты отправления и назначения должны различаться", true); return; }
    if (!Number.isInteger(quantity) || quantity <= 0 || !Number.isFinite(weight_tonnes) || weight_tonnes <= 0) { message("Количество — целое положительное число; масса груза должна быть больше нуля", true); return; }
    if (weight_tonnes > Number(vehicle(vehicle_id)?.capacity)) { message("Масса груза превышает грузоподъёмность выбранной машины", true); return; }
    if (await command("/trips", {vehicle_id, origin_id, destination_id, cargo: {name, quantity, weight_tonnes}}, "Рейс создан. Движение начнётся после расчёта дорожного маршрута и погрузки.")) setSelection("vehicle", vehicle_id);
  }
  async function exportCsv() {
    const button = $("lg-export"); if (button) button.disabled = true;
    try {
      if (auth()?.download) await auth().download("/logistics/export.csv", "Журнал_логистики.csv");
      else {
        const response = await fetch("/api/logistics/export.csv", {credentials: "same-origin", headers: auth()?.headers?.() || {}});
        if (!response.ok) { const error = new Error(`Экспорт недоступен (${response.status})`); error.status = response.status; throw error; }
        const url = URL.createObjectURL(await response.blob()), link = document.createElement("a");
        link.href = url; link.download = "Журнал_логистики.csv"; document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      message("Журнал CSV передан браузеру для скачивания");
    } catch (error) { report(error); }
    finally { if (button) button.disabled = !S.state; }
  }
  function onAction(event) {
    const button = event.target.closest("[data-lg-action]");
    if (!button || button.disabled || !S.host.contains(button)) return;
    const {lgAction: name, id} = button.dataset;
    if (["vehicle", "facility", "incident", "closure"].includes(name)) { setSelection(name, id); return; }
    if (name === "open-facility") { openFacility(id); return; }
    if (name === "clear") { clearSelection(); return; }
    if (name === "unfollow") { stopFollowing(); return; }
    if (name === "resolve" && canResolve()) command(`/incidents/${encodeURIComponent(id)}/resolve`, {}, "Результат устранения сохранён сервером");
    if (name === "reopen") command(`/closures/${encodeURIComponent(id)}/reopen`, {}, "Проезд открыт. Сервер обновляет маршруты.");
    if (name === "gps") command(`/vehicles/${encodeURIComponent(id)}/gps`, {available: stale(vehicle(id) || {})}, stale(vehicle(id) || {}) ? "GPS восстановлен на сервере" : "GPS отключён в демонстрационном сценарии");
  }
  function bind(id, event, callback) { $(id)?.addEventListener(event, callback); }
  function init(host) {
    if (S.initialized) return root.LogisticsView;
    S.host = typeof host === "string" ? document.querySelector(host) : host || document.getElementById("logistics-view");
    if (!S.host) return root.LogisticsView;
    S.initialized = true; S.host.addEventListener("click", onAction);
    bind("lg-start", "click", () => command("/control", {action: "start", scenario: $("lg-scenario")?.value || S.state?.scenario}, "Демонстрационный сценарий запущен"));
    bind("lg-pause", "click", () => command("/control", {action: "pause"}, "Движение поставлено на паузу"));
    bind("lg-resume", "click", () => command("/control", {action: "resume"}, "Движение продолжено"));
    bind("lg-reset", "click", () => { $("lg-scenario")?.removeAttribute("data-user-chosen"); command("/control", {action: "reset"}, "Демонстрационное состояние сброшено"); });
    bind("lg-speed", "change", () => { const speed = Number($("lg-speed").value); if (Number.isFinite(speed) && speed > 0) command("/control", {action: "speed", speed}, "Скорость демонстрационного времени изменена"); });
    bind("lg-scenario", "change", () => { $("lg-scenario").dataset.userChosen = "true"; const scenario = S.state?.scenarios?.find(s => s.id === $("lg-scenario").value); if (scenario) message(`${scenario.name}: ${scenario.description || "Выберите «Запустить», чтобы начать сценарий."}`); });
    bind("lg-fit", "click", fitNetwork);
    bind("lg-recalculate", "click", () => command("/routes/recalculate", {}, "Пересчёт маршрутов запрошен. Результат появится после ответа маршрутизатора."));
    bind("lg-add-closure", "click", () => setDrawMode(S.drawMode === "closure" ? null : "closure"));
    bind("lg-add-facility", "click", () => setDrawMode(S.drawMode === "facility" ? null : "facility"));
    bind("lg-export", "click", exportCsv); bind("lg-trip-form", "submit", createTrip);
    bind("lg-facility-save", "click", saveFacility); bind("lg-closure-save", "click", saveClosure);
    bind("lg-facility-cancel", "click", event => { event.preventDefault(); closeDialog("lg-facility-dialog"); });
    bind("lg-closure-cancel", "click", event => { event.preventDefault(); closeDialog("lg-closure-dialog"); });
    bind("lg-facility-dialog", "cancel", () => { S.point = null; }); bind("lg-closure-dialog", "cancel", () => { S.point = null; });
    document.addEventListener("keydown", event => { if (S.active && event.key === "Escape" && S.drawMode) { setDrawMode(null); message("Выбор точки отменён"); } });
    document.addEventListener("visibilitychange", () => { if (S.active && !document.hidden) { S.map?.resize(); poll(); } });
    renderControls(); return root.LogisticsView;
  }
  function activate() {
    if (!S.initialized) init();
    if (!S.initialized || S.active) return;
    S.active = true; S.generation += 1; S.pollBusy = false;
    createMap(); S.map?.resize(); renderControls(); message("Загрузка состояния логистики…", false, 0); poll();
    S.timer = setInterval(poll, 1000); S.frameAt = 0; S.frame = requestAnimationFrame(animate);
  }
  function deactivate() {
    S.active = false; S.generation += 1; clearInterval(S.timer); S.timer = null;
    if (S.frame) cancelAnimationFrame(S.frame); S.frame = null;
    S.controller?.abort(); S.controller = null; S.pollBusy = false;
    setDrawMode(null); closeDialog("lg-facility-dialog"); closeDialog("lg-closure-dialog");
  }
  root.LogisticsView = {init, activate, deactivate, fitNetwork, selectVehicle: id => setSelection("vehicle", id), selectFacility: id => setSelection("facility", id), openFacility, getSelection: () => S.selection ? {...S.selection} : null, getState: () => S.state};
})(globalThis);
