"use strict";
const core = DispatchCore, providers = DispatchProviders;
const params = new URLSearchParams(location.search);
const mode = params.get("mode") === "api" ? "api" : "mock";
const mock = mode === "mock" ? providers.createMockProvider() : null;
const state = {profile: core.profiles.find(p => p.operator_id === params.get("operator")) || core.profiles[0], profiles: core.profiles,
    site: null, assets: [], incidents: [], sensors: [], selected: null, epoch: 0, offset: 0, busy: false,
    notes: new Map(), detailKey: "", seq: 0, notificationReady: false, audio: null, lastSound: 0};
let provider, refreshTimer, presenceTimer, jobEpoch = 0, refreshNumber = 0, profileChange = 0;
const commands = new Map();
const $ = id => document.getElementById(id);
const labels = {forbidden_zone: "Запрещённая зона", unauthorized_access: "Событие доступа", sensor_offline: "Потеря связи с датчиком", model_anomaly: "Модельное подозрение",
    open: "Не принято", acknowledged: "Принято оператором", closed: "Закрыто", info: "Информация", warning: "Предупреждение", critical: "Критично",
    online: "Связь есть", offline: "Связь потеряна", unknown: "Состояние неизвестно", contacted: "Связался", inspection_requested: "Направил проверку", checked: "Проверил сведения"};
function node(tag, text, className) {const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n;}
function svg(tag, attrs, text) {const n = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [k,v] of Object.entries(attrs)) n.setAttribute(k,v); if (text !== undefined) n.textContent = text; return n;}
function message(text, failure = false) {$("action-message").textContent = text; $("action-message").className = failure ? "notice error" : "notice";}
function system(text, failure = false) {$("system-status").textContent = text; $("system-status").className = failure ? "error" : "";}
function when(t) {return t && Number.isFinite(Date.parse(t)) ? new Date(t).toLocaleString("ru-RU") : "нет данных";}
function now() {return Date.now() + state.offset;}
function qualityText(asset) {const q = core.quality(asset, now(), state.site?.dispatch_config?.position_stale_seconds || 5); return q.state === "unknown" ? "Свежесть позиции неизвестна" : `${q.state === "fresh" ? "Позиция актуальна" : "Последняя известная позиция; текущая неизвестна"}, ${Math.floor(q.ageSeconds)} с назад`;}
function renderProfiles() {$("operator").replaceChildren(); for (const p of state.profiles) {const opt=node("option", `${p.operator_id}: ${p.name || p.sector_id}`); opt.value=p.operator_id; $("operator").append(opt);} $("operator").value=state.profile.operator_id;$("api-link").href=`?mode=api&operator=${state.profile.operator_id}`;$("mock-link").href=`?mode=mock&operator=${state.profile.operator_id}`;}
function renderMap() {
    if (!state.site) return;
    const map=$("site-map"); map.replaceChildren();
    const b=state.site.sectors?.find(s=>s.id===state.profile.sector_id)?.map_bounds;
    map.setAttribute("viewBox", !$("all-map").checked && b ? `${b.x} ${b.y} ${b.width} ${b.height}` : "0 0 100 100");
    for (const road of state.site.roads || []) map.append(svg("polyline", {points:road.points,fill:"none",stroke:"#334155","stroke-width":3}));
    for (const [items,zone] of [[state.site.zones || [],true],[state.site.buildings || [],false]]) for (const item of items) {
        const r=item.rectangle; if (!r) continue;
        const g=svg("g",{opacity:item.responsible_sector_id && item.responsible_sector_id!==state.profile.sector_id ? 0.5 : 1});
        g.append(svg("rect",{x:r.x,y:r.y,width:r.width,height:r.height,fill:zone?"#3f1d1d":"#1e3a5f",stroke:zone?"#ef4444":"#60a5fa","stroke-dasharray":zone?"2 2":"none"}),
            svg("text",{x:r.x+r.width/2,y:r.y+r.height/2,"text-anchor":"middle","font-size":3,fill:"#e2e8f0"},item.id), svg("title",{},`${item.id}: ${item.name || "Зона"}`)); map.append(g);
    }
    for (const asset of state.assets) {
        if (!Number.isFinite(asset.x) || !Number.isFinite(asset.y)) continue;
        const fresh=core.quality(asset,now(),state.site.dispatch_config?.position_stale_seconds || 5).state==="fresh";
        const g=svg("g",{id:`asset-${asset.asset_id}`});
        g.append(svg("circle",{cx:asset.x,cy:asset.y,r:2,fill:fresh?"#22c55e":"#f59e0b",stroke:"#f8fafc","stroke-dasharray":fresh?"none":"1 1"}),
            svg("text",{x:asset.x+3,y:asset.y+1,"font-size":3,fill:"#f8fafc"},asset.asset_id),
            svg("text",{x:asset.x+3,y:asset.y+4,"font-size":2,fill:"#cbd5e1"},fresh?"актуально":"последняя позиция"),
            svg("title",{},`${asset.asset_id}: ${qualityText(asset)}; цель: ${asset.destination || "не задана"}`)); map.append(g);
    }
}
function renderSummary(data) {const c=$("dispatch-summary"); c.replaceChildren(); for (const s of data.sectors || []) {const n=state.profiles.find(p=>p.sector_id===s.sector_id)?.name || s.sector_id; const card=node("div",undefined,"sector-summary"); card.append(node("strong",n),node("span",`Условия: ${s.active_count}; не принято: ${s.unclaimed_count}; эскалации: ${s.escalated_count}`),node("small",mode==="mock"?"Связь рабочего места не моделируется":`Связь рабочего места: ${s.operator_online ? "есть" : "нет / неизвестна"}`)); c.append(card);} c.append(node("small",`Неизвестное место: ${data.unknown_count ?? "—"}`));}
function renderIncidents() {
    const c=$("incidents-list"); c.replaceChildren();
    const items=core.sortedIncidents(state.incidents.filter(i=>($("all-incidents").checked || core.isVisible(i,state.profile)) && ($("show-history").checked || core.isWorkItem(i))));
    $("incident-count").textContent=items.length;
    if (!items.length) c.append(node("p","В этом фильтре происшествий нет"));
    for (const i of items) {const card=node("button",undefined,`incident incident-${i.severity} ${i.incident_id===state.selected?"selected":""}`); card.type="button"; card.dataset.incidentId=i.incident_id;
        card.append(node("strong",`${labels[i.type] || i.type} · ${labels[i.severity] || i.severity}`),node("div",`${i.incident_id} · ${i.asset_id || i.employee_id || i.sensor_id || "объект неизвестен"}`),
            node("div",`${labels[i.status] || i.status}; ${i.assigned_operator_id || "ответственный не назначен"}`),node("small",i.disposition==="rejected_model_signal"?"Подозрение отклонено; история сохранена":i.condition_state==="unknown"?"Текущее условие неизвестно":i.condition_active?"Условие активно":"Условие восстановлено / разовое"));
        card.addEventListener("click",()=>{state.selected=i.incident_id;state.detailKey="";renderIncidents();renderDetails();}); c.append(card);
    }
}
function actionButton(text, action, disabled=false, title="") {const b=node("button",text);b.type="button";b.disabled=disabled || state.busy;b.title=title;b.dataset.action=action;return b;}
function renderDetails() {
    const c=$("incident-details"), i=state.incidents.find(i=>i.incident_id===state.selected);
    if (!i) {state.detailKey="";c.replaceChildren(node("h3","Карточка происшествия"),node("p","Выберите случай. Журнал остаётся видимым."));return;}
    const key=`${state.profile.operator_id}:${state.busy}:${JSON.stringify(i)}`,asset=state.assets.find(a=>a.asset_id===i.asset_id);
    if (key!==state.detailKey) {
        state.detailKey=key;c.replaceChildren(node("h3",`${i.incident_id}: ${labels[i.type] || i.type}`));
        for (const text of [`Объект: ${i.asset_id || i.employee_id || i.sensor_id || "неизвестен"}`,`Место: ${i.zone_id || i.building_id || i.site_area_id || "unknown"}`,`Ответственный: ${i.assigned_operator_id || "не назначен"}; ${labels[i.status] || i.status}`,`Обнаружено: ${when(i.detected_at)}`]) c.append(node("div",text));
        const condition=node("p");condition.id="condition-quality";c.append(condition);
        if (asset) {const quality=node("p");quality.id="position-quality";c.append(quality,node("div",`Транспорт: ${asset.vehicle_type || asset.type}; цель: ${asset.destination || "не задана"}`));}
        if (i.type==="unauthorized_access") c.append(node("p",`Источник: ${i.access_kind==="passage_confirmed"?"подтверждённый проход":i.access_kind || "подтверждённый проход по CONTRACTS"}. Отказ карточки не доказывает проникновение.`));
        if (i.type==="model_anomaly") {c.append(node("p",`Оценка модели: ${i.score ?? "нет данных"}; не вероятность аварии.`));if(i.disposition_reason)c.append(node("p",`Основание проверки: ${i.disposition_reason}`));}
        const plan=i.response_plan || core.responses[i.type];if(plan)c.append(node("h4","Первая реакция — без ожидания ИИ"),node("p",plan.steps),node("p",`Связаться: ${plan.contact}`));
        const evidence=node("ul");for(const id of i.evidence_event_ids || []) evidence.append(node("li",id));c.append(node("h4",mode==="mock"?"Тестовые ссылки на события":"Исходные события"),evidence);
        for(const e of (i.response_history || []).slice(-5)) c.append(node("p",`${when(e.created_at)} · ${labels[e.response_code] || e.response_code}: ${e.reason}`));
        const label=node("label","Действие / результат / причина"),note=node("textarea");note.id="response-note";note.maxLength=500;note.rows=2;note.value=state.notes.get(i.incident_id) || "";note.addEventListener("input",()=>state.notes.set(i.incident_id,note.value));label.append(note);c.append(label);
        const actions=node("div",undefined,"incident-actions"),owned=i.assigned_operator_id===state.profile.operator_id && core.isWorkItem(i),blocked=core.closeBlockedReason(i,state.profile);
        actions.append(actionButton("Принять ответственность","claim",!core.canClaim(i,state.profile)),actionButton("Связался","contacted",!owned),actionButton("Направил проверку","inspection_requested",!owned),actionButton("Проверил сведения","checked",!owned),actionButton("Закрыть","close",Boolean(blocked),blocked));
        if(i.type==="model_anomaly")actions.append(actionButton("Отклонить модельное подозрение","dismiss_model",!owned));
        actions.append(actionButton("Проанализировать","analysis",mode==="mock","В mock агент не подключён"));
        if(mode==="mock")actions.append(actionButton("Тест: восстановить условие","recover",!core.isWorkItem(i)));
        c.append(actions);if(blocked)c.append(node("small",`Закрытие: ${blocked}`));
        if(mode==="mock")c.append(node("p","Изменяются только данные этой вкладки. Общая база, передача и эскалация ещё требуют backend."));
    }
    $("condition-quality").textContent=core.conditionLabel(i,asset,now());if($("position-quality"))$("position-quality").textContent=qualityText(asset);
}
function renderSensors() {const c=$("sensors-list");c.replaceChildren();for(const s of state.sensors.filter(s=>$("all-incidents").checked || !s.responsible_sector_id || s.responsible_sector_id===state.profile.sector_id)){const card=node("div",undefined,`sensor sensor-${s.status}`);card.append(node("strong",s.sensor_id),node("div",labels[s.status] || s.status || "неизвестно"),node("small",`Последний сигнал: ${when(s.last_received_at)}`));c.append(card);}if(!c.children.length)c.append(node("p","В этом фильтре датчиков нет"));}
async function refresh() {
    clearTimeout(refreshTimer);const epoch=state.epoch,p=provider,requestNumber=++refreshNumber;
    try {const d=await p.dynamic($("all-incidents").checked?"all":"workstation");if(epoch!==state.epoch || requestNumber!==refreshNumber)return;
        Object.assign(state,{assets:d.assets,incidents:d.incidents,sensors:d.sensors});if(d.overview.as_of && Number.isFinite(Date.parse(d.overview.as_of)))state.offset=Date.parse(d.overview.as_of)-Date.now();
        renderMap();renderSummary(d.overview);renderIncidents();renderDetails();renderSensors();system(mode==="mock"?"Тестовый интерфейс · backend не подключён":`Данные сервера обновлены: ${new Date().toLocaleTimeString("ru-RU")}`);
    }catch(e){if(epoch!==state.epoch || requestNumber!==refreshNumber)return;system("Обновление недоступно. Показаны последние полученные данные.",true);message(e.message || "Ошибка данных",true);renderMap();renderDetails();}
    finally{if(epoch===state.epoch && requestNumber===refreshNumber)refreshTimer=setTimeout(refresh,1000);}
}
function sessionId(){const key=`dispatch-session:${state.profile.operator_id}`;let id=sessionStorage.getItem(key);if(!id){id=crypto.randomUUID();sessionStorage.setItem(key,id);}return id;}
async function presenceLoop(epoch){if(mode!=="api" || epoch!==state.epoch)return;try{await provider.presence(sessionId(),$("availability").value);if(epoch===state.epoch)$("presence-status").textContent="Клиент передаёт состояние рабочего места";}catch{if(epoch===state.epoch)$("presence-status").textContent="Состояние рабочего места не удалось передать";}if(epoch===state.epoch)presenceTimer=setTimeout(()=>presenceLoop(epoch),3000);}
async function notifications(epoch){
    if(mode!=="api" || epoch!==state.epoch)return;const p=provider;
    try{let count=0;for(let page=0;page<20;page++){const d=await p.notifications(state.seq);if(epoch!==state.epoch)return;
        if(!Array.isArray(d.notifications) || !Number.isInteger(d.next_seq) || d.next_seq<state.seq)throw new Error("Некорректная страница уведомлений");
        const previous=state.seq;state.seq=d.next_seq;count+=d.notifications.length;if(d.notifications.length<100)break;if(previous===state.seq)throw new Error("Курсор уведомлений не продвигается");}
        if(count && state.notificationReady){$("notifications").textContent=`Новых уведомлений: ${count}. Проверьте непринятые случаи и адресованную помощь.`;
            if(state.audio && $("sound").checked && Date.now()-state.lastSound>=5000){const o=state.audio.createOscillator(),g=state.audio.createGain();o.connect(g);g.connect(state.audio.destination);g.gain.value=0.05;o.frequency.value=660;o.start();o.stop(state.audio.currentTime+0.15);state.lastSound=Date.now();}}
        state.notificationReady=true;
    }catch(e){if(epoch===state.epoch)$("notifications").textContent=`Уведомления недоступны: ${e.message}`;}
    if(epoch===state.epoch)setTimeout(()=>notifications(epoch),1000);
}
async function analyse(i){const epoch=++jobEpoch,p=provider;try{const job=await p.analyse(i.incident_id);if(epoch!==jobEpoch)return;$("agent-result").textContent=`${i.incident_id}: ${job.status}. Мониторинг продолжается.`;
    async function poll(){if(epoch!==jobEpoch)return;try{const j=await p.job(job.job_id);if(epoch!==jobEpoch)return;
        $("agent-result").textContent=`${i.incident_id}: ${j.status}${j.result?.summary?` · ${j.result.summary}`:""}${j.stale || j.result?.stale?" · анализ устарел":""}${j.error?` · ${typeof j.error==="string"?j.error:j.error.message || j.error.code}`:""}`;
        if(["queued","running"].includes(j.status))setTimeout(poll,1000);
    }catch(e){if(epoch===jobEpoch)$("agent-result").textContent=`Анализ недоступен: ${e.message}`;}}poll();
}catch(e){if(epoch===jobEpoch)$("agent-result").textContent=`Анализ не запущен: ${e.message}`;}}
async function mutate(action){
    if(state.busy)return;const i=state.incidents.find(i=>i.incident_id===state.selected);if(!i)return;
    if(action==="analysis"){analyse(i);return;}if(action==="recover"){mock.recover(i.incident_id);state.detailKey="";await refresh();return;}
    const response=["contacted","inspection_requested","checked"].includes(action),reason=(state.notes.get(i.incident_id) || "").trim();
    if(action!=="claim" && !reason){message("Опишите действие или основание проверки в поле карточки.",true);return;}
    const command={action:response?"record_response":action,expected_revision:i.dispatch_revision,request_id:crypto.randomUUID(),...(action!=="claim"?{reason}:{}),...(response?{response_code:action}:{})};
    const key=`${i.incident_id}:${state.profile.operator_id}:${JSON.stringify({...command,request_id:""})}`;if(!commands.has(key))commands.set(key,command);
    state.busy=true;$("operator").disabled=true;renderDetails();
    try{await provider.mutate(i.incident_id,commands.get(key),state.profile.operator_id);commands.delete(key);message(mode==="mock"?"Изменены тестовые данные этой вкладки.":"Сервер подтвердил действие.");}
    catch(e){if(e.status)commands.delete(key);message(e.status===409?`${e.message} Карточка перечитывается.`:e.message,true);}
    finally{state.busy=false;$("operator").disabled=false;state.detailKey="";await refresh();}
}
async function start(){
    const epoch=++state.epoch;clearTimeout(refreshTimer);clearTimeout(presenceTimer);jobEpoch++;state.notificationReady=false;state.seq=0;state.selected=null;state.detailKey="";
    state.assets=[];state.incidents=[];state.sensors=[];renderIncidents();renderDetails();renderSensors();renderMap();
    provider=mode==="mock"?mock:providers.createApiProvider(state.profile.operator_id);system("Загрузка...");
    try{const initial=await provider.initial();if(epoch!==state.epoch)return;state.site=initial.site;state.profiles=initial.profiles;state.profile=state.profiles.find(p=>p.operator_id===state.profile.operator_id);if(!state.profile)throw new Error("Сервер не знает выбранное рабочее место");renderProfiles();await refresh();presenceLoop(epoch);notifications(epoch);}
    catch(e){if(epoch===state.epoch){system("Не удалось подключить данные",true);message(`${e.message}. В API-режиме автоматического перехода на mock нет.`,true);$("incidents-list").textContent="Список происшествий не получен. Это ошибка связи, а не отсутствие случаев.";$("sensors-list").textContent="Список датчиков не получен.";$("incident-count").textContent="—";$("dispatch-summary").textContent="Сводка недоступна";}}
}
$("operator").addEventListener("change",async event=>{const id=event.target.value,change=++profileChange;if(mode==="api"){try{await provider.presence(sessionId(),"away");}catch{message("Прежняя сессия завершится по серверному timeout.");}}if(change!==profileChange)return;state.profile=state.profiles.find(p=>p.operator_id===id) || state.profile;$("notifications").textContent="";$("agent-result").textContent=mode==="mock"?"ИИ не подключён в mock-режиме.":"Выберите случай для анализа.";start();});
$("incident-details").addEventListener("click",event=>{if(event.target.dataset.action)mutate(event.target.dataset.action);});
$("all-incidents").addEventListener("change",refresh);$("show-history").addEventListener("change",renderIncidents);$("all-map").addEventListener("change",renderMap);
$("availability").addEventListener("change",()=>{clearTimeout(presenceTimer);presenceLoop(state.epoch);});
$("sound").addEventListener("change",async event=>{if(!event.target.checked)return;try{state.audio=state.audio || new(window.AudioContext || window.webkitAudioContext)();await state.audio.resume();}catch{event.target.checked=false;message("Звук недоступен; визуальные уведомления остаются.",true);}});
$("mock-burst").addEventListener("click",()=>{mock.addBurst();refresh();});$("mock-loss").addEventListener("click",()=>{mock.losePosition();refresh();});
$("mode-label").textContent=mode==="mock"?"MOCK: тестовые данные этой вкладки; общий сервер и ИИ не подключены":"API: только данные локального сервера";
$("mock-controls").hidden=mode!=="mock";$("api-link").href=`?mode=api&operator=${state.profile.operator_id}`;$("mock-link").href=`?mode=mock&operator=${state.profile.operator_id}`;
$("availability").disabled=mode==="mock";
$("agent-result").textContent=mode==="mock"?"ИИ не подключён в mock-режиме.":"Выберите случай для анализа.";
renderProfiles();start();
