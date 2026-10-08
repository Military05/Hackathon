"use strict";
const $ = id => document.getElementById(id);
const titles = {forbidden_zone:"Въезд в запрещённую зону",unauthorized_access:"Подтверждённый проход без допуска",sensor_offline:"Нет сигнала датчика",model_anomaly:"Необычное движение",route_deviation:"Отклонение от маршрута",collision:"Пересечение транспорта"};
const labels = {open:"Не принято",acknowledged:"Принято",closed:"Завершено",active:"Условие активно",restored:"Условие восстановлено",unknown:"Нужно проверить",online:"На связи",offline:"Нет связи",rejected_model_signal:"Подозрение отклонено"};
const sectorNames = {logistics:"Логистика и склады",production:"Производство",coordination:"КПП"};
const actionNames = {detected:"Обнаружено",claim:"Ответственность принята",record_response:"Реакция записана",condition_restored:"Условие восстановлено",escalation:"Эскалация",request_transfer:"Передача предложена",accept_transfer:"Передача принята",cancel_transfer:"Передача отменена",reassign_unavailable:"Переназначение отсутствующего оператора",dismiss_model:"Модельное подозрение отклонено",close:"Обработка завершена"};
const S = {site:null,map:null,assets:[],sensors:[],incidents:[],profiles:[],summary:{},selected:null,detail:null,detailId:null,operator:"dispatcher-1",cursor:0,sound:false,audio:null,job:null,session:crypto.randomUUID(),buildingLayers:new Map(),assetLayers:new Map(),sensorLayers:new Map(),asOf:Date.now(),serverOffset:0,busy:false,context:0,detailRequest:0,commandBusy:false,switching:false,presenceChain:Promise.resolve(),connectionError:false,agentAvailable:true,analysisSubmitting:false};
Object.assign(S,{mapRenderer:null,diagnostics:null,noticeRows:[],objectSelection:null,demoBusy:false,demo:null});
const scenarioNames={normal:"Штатная работа завода",logistics:"Доставка комплектующих",shift:"Начало смены",service:"Обход служебного транспорта","forbidden-zone":"Въезд в закрытую зону","unauthorized-access":"Проход без допуска","sensor-offline":"Потеря сигнала датчика",simultaneous:"Несколько происшествий","unusual-movement":"Необычное движение"};
const notificationNames={new_incident:"Новое происшествие",reminder:"Случай ожидает реакции",escalation:"Случай передан на следующий уровень",transfer_requested:"Предложена передача",transfer_accepted:"Передача принята",transfer_cancelled:"Передача отменена",transfer_expired:"Срок передачи истёк",operator_unavailable:"Ответственный отсутствует",active_review:"Пора повторно проверить случай"};
const vehicleNames={forklift:"Погрузчик",service_vehicle:"Служебный автомобиль",service:"Служебный автомобиль",truck:"Грузовик"};
const esc = x => String(x??"—").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function showError(e){
  const message=e.code==="session_identity_changed"?"Аккаунт изменён в другой вкладке. Войдите заново; для разных диспетчеров используйте отдельные профили браузера.":e.status===401?"Сессия завершена. Войдите заново.":e.message||String(e);
  if(S.auth?.enabled&&(e.status===401||e.code==="session_identity_changed")){ProductAuth.showLogin(message);S.auth.user=null;return;}
  $("error").hidden=false;$("error").textContent=message;
}
async function api(path,method="GET",body,operator=S.operator){
  const controller=new AbortController();
  const t=setTimeout(()=>controller.abort(),6000);
  try{
    const r=await fetch(`/api${path}`,{method,credentials:"same-origin",signal:controller.signal,headers:{"X-Demo-Operator":operator,...(typeof ProductAuth!=="undefined"?ProductAuth.headers():{}),...(body?{"Content-Type":"application/json"}:{})},...(body?{body:JSON.stringify(body)}:{})});
    let v;
    try{v=await r.json();}catch{throw Error("Сервер вернул некорректный ответ");}
    if(!r.ok){
      const messages={revision_conflict:"Карточка изменилась у другого оператора. Посмотрите обновлённые данные и повторите действие осознанно.",operator_available:"Отсутствие ответственного ещё не подтверждено. Подождите установленный срок и проверьте сводку.",startup_grace:"Система восстанавливает рабочие места после запуска. Повторите позже.",operator_conflict:"Действие доступно ответственному оператору или назначенному резерву.",unavailable:"Локальная модель недоступна. Мониторинг и ручная обработка продолжаются."};
      const e=Error(messages[v.code]||v.message||v.detail?.message||`HTTP ${r.status}`);
      Object.assign(e,{status:r.status,code:v.code,details:v.details});
      throw e;
    }
    return v;
  }finally{clearTimeout(t);}
}
function array(v,key){const a=Array.isArray(v)?v:v[key];if(!Array.isArray(a))throw Error(`Некорректный список ${key}`);return a;}
function xy(x,y){return [100-y,x];}
function age(t){if(!t)return "Сигнал ещё не получен";const n=Math.max(0,Math.round((Date.now()+S.serverOffset-Date.parse(t))/1000));return n<60?"Только что":n<3600?`${Math.floor(n/60)} мин. назад`:`${Math.floor(n/3600)} ч. назад`;}
function workable(i){return i.status!=="closed"&&i.disposition!=="rejected_model_signal";}
function assetStale(a){return !a?.last_seen||Date.now()+S.serverOffset-Date.parse(a.last_seen)>=(S.site.dispatch_config?.position_stale_seconds||5)*1000;}
function compareIncidents(a,b){
  const priority={critical:0,warning:1,info:2};
  return Number(!workable(a))-Number(!workable(b))||(priority[a.severity]??3)-(priority[b.severity]??3)||(b.escalation_level||0)-(a.escalation_level||0)||Number(Boolean(a.assigned_operator_id))-Number(Boolean(b.assigned_operator_id))||Date.parse(a.detected_at)-Date.parse(b.detected_at)||a.incident_id.localeCompare(b.incident_id);
}
function setMarkup(id,value){const el=$(id);if(el.innerHTML!==value)el.innerHTML=value;}
function sameSelection(id,context){return S.selected===id&&S.context===context;}
function initialOperator(search){const value=new URLSearchParams(search).get("operator");return ["dispatcher-1","dispatcher-2","dispatcher-3"].includes(value)?value:"dispatcher-1";}
function named(rows,id){const row=(rows||[]).find(x=>(x.id||x.asset_id||x.sensor_id||x.employee_id)===id);return row?.name||row?.short_name||id;}
function operatorName(id){return id?S.profiles.find(p=>(p.operator_id||p.id)===id)?.name||id:"Ответственный не назначен";}
function placeName(i){return named(S.site?.zones,i.zone_id)||named(S.site?.buildings,i.building_id)||named(S.site?.site_areas,i.site_area_id)||"Место не определено";}
function entityName(i){const first=named(S.site?.assets,i.asset_id||i.employee_id)||named(S.site?.sensors,i.sensor_id)||"Источник не определён";return i.other_asset_id?`${first} ↔ ${named(S.site?.assets,i.other_asset_id)}`:first;}
function clock(t){if(!t)return "—";const value=new Date(t);return Number.isNaN(value.getTime())?"—":value.toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"});}
function transferClock(i){if(i.pending_transfer?.status!=="pending")return "";const until=i.pending_transfer.expires_at||i.pending_transfer.deadline_at;if(!until)return "Ожидается ответ получателя";const seconds=Math.max(0,Math.ceil((Date.parse(until)-Date.now()-S.serverOffset)/1000));return Number.isFinite(seconds)?seconds?`Ответить за ${seconds} сек.`:"Срок истёк · ожидается подтверждение сервера":"Ожидается ответ получателя";}
function focusSector(animate=false){
  const sector=S.profiles.find(p=>(p.operator_id||p.id)===S.operator)?.sector_id;
  if(S.mapRenderer){S.mapRenderer.focusSector(sector,{animate});return;}
  const rectangles=(S.site.site_areas||[]).filter(a=>a.responsible_sector_id===sector&&a.rectangle).map(a=>a.rectangle);
  if(!rectangles.length){S.map.fitBounds([[0,0],[100,100]]);return;}
  const minX=Math.min(...rectangles.map(r=>r.x)),minY=Math.min(...rectangles.map(r=>r.y)),maxX=Math.max(...rectangles.map(r=>r.x+r.width)),maxY=Math.max(...rectangles.map(r=>r.y+r.height));
  S.map.fitBounds([xy(minX,maxY),xy(maxX,minY)],{padding:[18,18]});
}
function initMap(){
  S.mapRenderer=EnterpriseMap.create({elementId:"map",site:S.site,onAsset:showAsset,onSensor:showSensor,onBuilding:showBuilding});
  S.map=S.mapRenderer.map;drawAssets();
}
function relatedIncidents(kind,id){return S.incidents.filter(i=>workable(i)&&(i[`${kind}_id`]===id||(kind==="asset"&&i.other_asset_id===id)||(kind==="building"&&i.site_area_id===S.site.buildings?.find(b=>b.id===id)?.site_area_id)));}
function objectInfo(text,kind,id,sensorIds=[]){
  $("asset-info").hidden=false;S.objectSelection={kind,id};const rows=relatedIncidents(kind,id);
  $("asset-info").textContent=text;
  if(rows.length||sensorIds.length){$("asset-info").innerHTML=`<p>${esc(text)}</p><div class="object-links">${rows.map(i=>`<button data-related="${esc(i.incident_id)}">${esc(titles[i.type]||i.type)} · ${esc(clock(i.detected_at))}</button>`).join("")}${sensorIds.map(sensorId=>`<button data-sensor="${esc(sensorId)}">Проверить ${esc(named(S.site.sensors,sensorId))}</button>`).join("")}</div>`;
  $("asset-info").querySelectorAll("[data-related]").forEach(b=>b.onclick=()=>selectIncident(b.dataset.related).catch(showError));
  $("asset-info").querySelectorAll("[data-sensor]").forEach(b=>b.onclick=()=>showSensor(b.dataset.sensor,true));}
}
function showAsset(id){
  S.mapRenderer?.selectAsset?.(id);
  const a=S.assets.find(x=>x.asset_id===id);
  if(!a){$("asset-info").textContent="Объект отсутствует в текущих данных.";return;}
  const coordinates=Number.isFinite(a.x)&&Number.isFinite(a.y)?`(${a.x.toFixed(1)}, ${a.y.toFixed(1)})`:"не получена";
  const policy=(S.site.permissions||[]).find(p=>p.asset_id===id),allowed=(policy?.allowed_zone_ids||[]).map(zone=>named(S.site.zones,zone));
  const vehicleType=a.vehicle_type||(S.site.assets||[]).find(x=>x.id===id)?.vehicle_type||a.type;
  const forbidden=(S.site.zones||[]).filter(z=>z.kind==="forbidden"||(z.kind==="restricted"&&(z.restricted_vehicle_types||[]).includes(vehicleType))).map(z=>z.name||z.id);
  objectInfo(`${entityName({asset_id:id})} · ${vehicleNames[vehicleType]||"Транспорт"} · Цель: ${named(S.site.buildings,a.destination)||"не задана"} · Последний сигнал: ${clock(a.last_seen)} (${age(a.last_seen)}), позиция ${coordinates}. Допуск: ${allowed.join(", ")||"нет"}. Запрещено: ${forbidden.join(", ")||"нет"}. ${assetStale(a)?"Текущее место не подтверждено.":""}`,"asset",id,(S.site.sensors||[]).filter(s=>s.asset_id===id).map(s=>s.id||s.sensor_id));
}
function showBuilding(id){const b=S.site.buildings.find(x=>x.id===id);if(!b)return;objectInfo(`${b.name}. Рабочих происшествий: ${relatedIncidents("building",id).length}.`,"building",id,(S.site.sensors||[]).filter(s=>s.building_id===id).map(s=>s.id||s.sensor_id));}
function showSensor(id,inspect=false){const s=S.sensors.find(x=>x.sensor_id===id);objectInfo(`${named(S.site.sensors,id)} · ${labels[s?.status]||"Сигнал ещё не получен"} · ${age(s?.last_received_at)}. Проверка связи не подтверждает физическую исправность датчика.`,"sensor",id);if(inspect)S.diagnostics?.inspectSensor?.(id);}
function drawAssets(){
  S.mapRenderer?.update({assets:S.assets,sensors:S.sensors,incidents:S.incidents,serverOffset:S.serverOffset});
}
function renderSummary(){
  const rows=S.summary.sectors||[];
  setMarkup("summary",rows.map(r=>`<article><div><strong>${esc(sectorNames[r.sector_id]||r.sector_id)}</strong><small>${r.operator_ready?"Оператор готов":r.operator_online||r.client_online?"Рабочее место на связи · оператор отсутствует":"Нет связи с рабочим местом"}</small></div><div><b>${r.active_count||0}</b><small>активных · ${r.unclaimed_count||0} не принято<br>${r.escalated_count||0} эскалировано</small></div></article>`).join("")+`<div class="unknown-summary">Место не определено: <strong>${S.summary.unknown_count||0}</strong> рабочих случаев. Проверяйте источник и свежесть позиции.</div>`);
}
function renderIncidents(){
  if($("clear-incident-log"))$("clear-incident-log").hidden=S.auth?.user?.role!=="admin";
  const type=$("filter-type")?.value||"",state=$("filter-state")?.value||"",query=($("search-incidents")?.value||"").trim().toLocaleLowerCase("ru-RU");
  const rows=S.incidents.filter(i=>{
    if(!$("history-toggle").checked&&!workable(i))return false;
    if(type&&type!=="all"&&i.type!==type)return false;
    if(state&&state!=="all"&&(state==="unclaimed"?Boolean(i.assigned_operator_id)||!workable(i):state==="mine"?i.assigned_operator_id!==S.operator:state==="escalated"?!i.escalation_level:state==="pending"?i.pending_transfer?.status!=="pending":i.condition_state!==state&&i.status!==state))return false;
    return !query||[titles[i.type],entityName(i),placeName(i),operatorName(i.assigned_operator_id),i.incident_id,i.asset_id,i.other_asset_id,i.sensor_id,i.building_id,i.zone_id].join(" ").toLocaleLowerCase("ru-RU").includes(query);
  }).sort(compareIncidents);
  setMarkup("incidents",rows.length?rows.map(i=>`<button class="incident ${esc(i.severity)} ${!workable(i)?"archived":""} ${i.incident_id===S.selected?"selected":""}" data-id="${esc(i.incident_id)}"><strong>${esc(titles[i.type]||i.type)}</strong><p>${esc(entityName(i))} · ${esc(placeName(i))}</p><small>${esc(labels[i.disposition]||labels[i.status]||i.status)} · ${esc(operatorName(i.assigned_operator_id))}</small><span class="pill">${esc(labels[i.condition_state]||i.condition_state||"active")}</span>${i.escalation_level?`<span class="pill escalated">Эскалация ${i.escalation_level}</span>`:""}<time datetime="${esc(i.detected_at)}">${esc(clock(i.detected_at))} · ${esc(age(i.detected_at))}</time></button>`).join(""):"<p class='muted'>Нет случаев по выбранным условиям. Завершённые и отклонённые доступны в истории.</p>");
  $("incidents").querySelectorAll("[data-id]").forEach(b=>b.onclick=()=>selectIncident(b.dataset.id).catch(showError));
}
async function selectIncident(id){S.selected=id;S.detail=null;S.detailId=null;S.job=null;S.detailRequest++;if(typeof document.querySelector==="function")document.querySelector(".detail")?.classList.remove("is-empty");S.mapRenderer?.clearHighlight();$("selected-id").textContent=id;$("details").textContent="Загрузка карточки…";renderIncidents();await renderDetails();}
function showSelectedOnMap(){if(!S.detail||S.detail.incident_id!==S.selected)return;const found=S.mapRenderer?.showIncident(S.detail);if(found===false){showError(Error("Для этого случая нет подтверждённых координат. Проверьте источник данных; точка на карте не выдумывается."));}else $("error").hidden=true;}
async function command(action,extra={}){
  const shown=S.detail;
  if(!shown||shown.incident_id!==S.selected)throw Error("Дождитесь загрузки карточки.");
  if(S.commandBusy)return;
  const id=shown.incident_id,context=S.context,operator=S.operator;
  S.detailRequest++;S.commandBusy=true;updateActions(shown);
  try{
    // Use the revision that the operator actually saw; a fresh GET here would hide a conflict.
    const updated=await api(`/incidents/${encodeURIComponent(id)}`,"PATCH",{action,expected_revision:shown.dispatch_revision,request_id:crypto.randomUUID(),...extra},operator);
    if(sameSelection(id,context)){applyDetails(updated);$("error").hidden=true;}
    await refresh();
  }catch(e){
    if(e.code==="revision_conflict"&&sameSelection(id,context)){
      if(e.details?.incident)applyDetails(e.details.incident);else await renderDetails();
    }
    throw e;
  }finally{S.commandBusy=false;if(S.detail)updateActions(S.detail);}
}
function reason(){return ($("reason")?.value||"").trim();}
function reserveOrder(owner){return owner==="dispatcher-1"?["dispatcher-3","dispatcher-2"]:owner==="dispatcher-2"?["dispatcher-3","dispatcher-1"]:S.site.dispatch_config?.recovery_reserve_order||["dispatcher-1","dispatcher-2"];}
function recoveryAllowed(i){
  if(!workable(i)||!i.assigned_operator_id||i.assigned_operator_id===S.operator)return false;
  const owner=S.profiles.find(p=>(p.operator_id||p.id)===i.assigned_operator_id);
  if(!owner||owner.operator_ready)return false;
  return reserveOrder(i.assigned_operator_id).find(id=>S.profiles.some(p=>(p.operator_id||p.id)===id&&p.operator_ready))===S.operator;
}
function recipientOptions(i){
  const owner=i.assigned_operator_id;
  const profiles=S.profiles.filter(p=>(p.operator_id||p.id)!==(recoveryAllowed(i)?owner:S.operator));
  const select=$("recipient"),previous=select.value;
  const ids=profiles.map(p=>p.operator_id||p.id).join("|");
  if(select.dataset.ids!==ids){select.innerHTML=profiles.map(p=>`<option value="${esc(p.operator_id||p.id)}">${esc(p.name||p.operator_id||p.id)}</option>`).join("");select.dataset.ids=ids;}
  for(const option of select.options){const p=profiles.find(p=>(p.operator_id||p.id)===option.value);option.disabled=!p?.operator_ready;option.textContent=`${p?.name||option.value}${p?.operator_ready?"":" · не готов"}`;}
  const available=profiles.filter(p=>p.operator_ready).map(p=>p.operator_id||p.id);
  select.value=available.includes(previous)?previous:available.includes(S.operator)?S.operator:available[0]||"";
  select.disabled=S.commandBusy||!available.length;
}
function updateActions(i){
  if(S.detailId!==i.incident_id)return;
  const own=i.assigned_operator_id===S.operator,working=workable(i),pending=i.pending_transfer?.status==="pending",recover=recoveryAllowed(i);
  $("recipient-wrap").hidden=!((own&&!pending&&working)||recover);
  recipientOptions(i);
  const readyRecipient=Boolean($("recipient").value);
  const disabled=S.commandBusy?" disabled":"";
  const button=(id,text,primary=false,extraDisabled=false)=>`<button id="${id}"${primary?' class="primary"':""}${disabled||extraDisabled?" disabled":""}>${text}</button>`;
  setMarkup("detail-actions",`${button("show-on-map","Показать на карте")}${!i.assigned_operator_id&&working&&i.can_claim!==false?button("claim","Принять ответственность",true):""}${own&&working?button("contact","Записать: связался")+button("inspect","Записать: запросил проверку")+button("save-note","Сохранить заметку"):""}${own&&i.condition_active===false&&i.condition_state!=="unknown"&&!pending&&working?button("close","Завершить обработку"):""}${own&&i.type==="model_anomaly"&&working?button("dismiss","Отклонить модельное подозрение"):""}${own&&!pending&&working?button("transfer","Предложить передачу",false,!readyRecipient):""}${recover?button("reassign","Переназначить отсутствующего",false,!readyRecipient):""}${pending?`<span class="pill">Передача → ${esc(operatorName(i.pending_transfer.to_operator_id))}</span><small class="transfer-clock">${esc(transferClock(i))}</small>${i.pending_transfer.to_operator_id===S.operator?button("accept","Принять передачу",true):""}${own?button("cancel","Отменить передачу"):""}`:""}${button("analyse",S.agentAvailable?"Проанализировать ИИ":"ИИ ещё не подключён",false,!S.agentAvailable||S.analysisSubmitting)}`);
  $("recovery-hint").hidden=!recover;
  const bind=(id,fn)=>{if($(id))$(id).onclick=async()=>{try{await fn();}catch(e){showError(e);}};};
  bind("claim",()=>command("claim"));
  bind("close",()=>command("close",{reason:reason()||"Условие восстановлено, проверено оператором"}));
  bind("contact",()=>command("record_response",{response_code:"contacted",reason:reason()||"Оператор сообщил о выполненном контакте"}));
  bind("inspect",()=>command("record_response",{response_code:"inspection_requested",reason:reason()||"Оператор сообщил о запросе проверки"}));
  bind("dismiss",()=>command("dismiss_model",{reason:reason()||"Проверено оператором: штатная операция"}));
  bind("transfer",()=>command("request_transfer",{to_operator_id:$("recipient").value,reason:reason()||"Запрошена помощь"}));
  bind("reassign",()=>command("reassign_unavailable",{to_operator_id:$("recipient").value,reason:reason()||"Ответственный отсутствует, резерв принимает обработку"}));
  bind("accept",()=>command("accept_transfer",{transfer_id:S.detail.pending_transfer.transfer_id}));
  bind("cancel",()=>command("cancel_transfer",{transfer_id:S.detail.pending_transfer.transfer_id,reason:reason()||"Передача отменена отправителем"}));
  bind("save-note",saveNote);
  bind("analyse",startAnalysis);
  bind("show-on-map",showSelectedOnMap);
}
function applyDetails(i){
  if(i.incident_id!==S.selected)return;
  if(S.detail?.incident_id===i.incident_id&&S.detail.dispatch_revision>i.dispatch_revision)return;
  if(S.detailId!==i.incident_id){
    // Inputs live outside the refreshed facts/actions; polling never replaces them.
    $("details").innerHTML='<div id="detail-facts" class="facts"></div><div id="response-plan" class="response-plan"></div><div class="response"><label for="reason">Заметка / реакция диспетчера</label><input id="reason" placeholder="Причина / запись выполненного действия" maxlength="500"></div><div id="recipient-wrap" class="recipient-wrap"><label for="recipient">Получатель</label><select id="recipient"></select></div><p id="recovery-hint" class="muted">Резерв может переназначить случай после подтверждённого отсутствия ответственного. Срок и готовность получателя проверяет сервер.</p><div id="detail-actions" class="actions"></div><details class="detail-extra"><summary>История и исходные события</summary><div id="detail-history" class="history"></div></details><details class="detail-extra"><summary>Технические данные</summary><div id="detail-technical"></div></details><div id="analysis" class="analysis"></div>';
    S.detailId=i.incident_id;
  }
  S.detail=i;$("selected-id").textContent=i.incident_id;
  setMarkup("detail-facts",`<div><label>Причина</label>${esc(titles[i.type]||i.type)}</div><div><label>Место и объект</label>${esc(placeName(i))} · ${esc(entityName(i))}</div><div><label>Условие / обработка</label>${esc(labels[i.condition_state]||i.condition_state)} / ${esc(labels[i.disposition]||labels[i.status]||i.status)}</div><div><label>Ответственный</label>${esc(operatorName(i.assigned_operator_id))}</div><div><label>Обнаружено</label>${esc(clock(i.detected_at))} · ${esc(age(i.detected_at))}</div>`);
  setMarkup("detail-technical",`<p>Случай ${esc(i.incident_id)} · ревизия ${esc(i.dispatch_revision)}</p><p>Источник: ${esc(i.asset_id||i.employee_id||i.sensor_id)}${i.other_asset_id?` ↔ ${esc(i.other_asset_id)}`:""} · участок ${esc(i.site_area_id)} · зона ${esc(i.zone_id)} · здание ${esc(i.building_id)}</p>`);
  S.mapRenderer?.highlight(i,{recenter:false});
  const plan=i.response_plan||{},steps=Array.isArray(plan.steps)?plan.steps:[plan.steps||i.response_instruction||i.details?.response_instruction||"Принять случай, проверить свежие наблюдения и записать реакцию."];
  setMarkup("response-plan",`<strong>Связаться: ${esc(plan.contact||"Ответственная роль участка")}</strong><ol>${steps.map(step=>`<li>${esc(step)}</li>`).join("")}</ol>`);
  updateActions(i);
  const ids=i.evidence_event_ids||[];
  setMarkup("detail-history",`<strong>Исходные события:</strong> ${ids.length?ids.map(esc).join(", "):"Отсутствие сигнала подтверждается состоянием источника, без выдуманного события."}${i.disposition==="rejected_model_signal"?`<p class="disposition">Подозрение отклонено: ${esc(i.disposition_reason)} · ${esc(clock(i.reviewed_at))}</p>`:""}${(i.history||[]).map(h=>`<p>${esc(clock(h.created_at))} · ${esc(h.actor_operator_id?operatorName(h.actor_operator_id):"Система")} · ${esc(actionNames[h.action]||h.action)} · ${esc(h.reason||"")}</p>`).join("")}`);
  if(!S.job)setMarkup("analysis",S.agentAvailable?"<p class='muted'>ИИ читает факты инструментами. Мониторинг работает и при выключенной локальной модели.</p>":"<p class='muted'>ИИ пока не подключён. Принимайте случаи и записывайте реакцию вручную; мониторинг продолжает работать.</p>");
}
async function renderDetails(){
  if(!S.selected)return;
  const id=S.selected,context=S.context,request=++S.detailRequest;
  const i=await api(`/incidents/${encodeURIComponent(id)}`);
  if(!sameSelection(id,context)||request!==S.detailRequest)return;
  applyDetails(i);await renderJob();
}
async function saveNote(){
  const text=reason();
  if(!text)throw Error("Введите заметку перед сохранением.");
  await command("record_response",{response_code:"checked",reason:text});
}
async function analyseTrends(){
  if(!S.incidents.length)throw Error("В журнале нет происшествий. Анализ повторяемости недоступен: сначала нужны сохранённые случаи.");
  if(!S.agentAvailable)throw Error("ИИ пока не подключён. Анализ повторяемости недоступен.");
  if(!S.selected||!S.incidents.some(i=>i.incident_id===S.selected))await selectIncident(S.incidents[0].incident_id);
  await startAnalysis();
}
async function clearIncidentLog(){
  if(S.auth?.user?.role!=="admin")throw Error("Очищать журнал может только администратор.");
  if(S.logClearing)return;
  S.logClearing=true;
  const context=S.context;
  try{
    const preview=await api("/admin/incident-log/preview");
    if(!preview.count)throw Error("Журнал происшествий уже пуст.");
    if(typeof globalThis.confirm!=="function")throw Error("Подтверждение удаления недоступно. Журнал сохранён.");
    if(!globalThis.confirm(`Удалить все происшествия (${preview.count}), включая активные?\n\n${preview.warning}\n\nПодтвердить окончательное удаление?`))return;
    if(context!==S.context)return;
    await api("/admin/incident-log/clear","POST",{confirmation:"DELETE_INCIDENT_LOG",token:preview.token});
    if(context!==S.context)return;
    S.context++; // Discard polling/details replies captured before the deletion.
    S.selected=null;S.detail=null;S.detailId=null;S.job=null;S.incidents=[];S.noticeRows=[];S.detailRequest++;
    S.mapRenderer?.clearHighlight();
    $("details").textContent="Журнал очищен. Выберите новое происшествие, когда оно появится.";
    $("selected-id").textContent="";renderIncidents();renderNotifications();
    await refresh();
  }finally{S.logClearing=false;}
}
async function startAnalysis(){
  const id=S.selected,context=S.context,operator=S.operator;
  if(!id||S.analysisSubmitting||!S.agentAvailable)return;
  S.analysisSubmitting=true;if(S.detail)updateActions(S.detail);
  try{
    const j=await api(`/incidents/${encodeURIComponent(id)}/analysis`,"POST",{},operator);
    if(!sameSelection(id,context))return;
    if(j.incident_id&&j.incident_id!==id)throw Error("Анализ относится к другой карточке.");
    S.job=j.job_id;await renderJob();
  }finally{S.analysisSubmitting=false;if(S.detail)updateActions(S.detail);}
}
async function renderJob(){
  if(!S.job||!S.selected)return;
  const job=S.job,id=S.selected,context=S.context;
  const j=await api(`/agent-jobs/${encodeURIComponent(job)}`);
  if(!sameSelection(id,context)||job!==S.job||j.incident_id&&j.incident_id!==id)return;
  const stale=j.stale||j.result?.stale;
  const staleText=stale?(j.status==="completed"?"· УСТАРЕЛ — данные изменились":"· Данные изменились после начала запроса"):"";
  const list=(title,values)=>Array.isArray(values)&&values.length?`<strong>${title}</strong><ul class="analysis-list">${values.map(v=>`<li>${esc(v)}</li>`).join("")}</ul>`:"";
  const p=j.result?.presentation;
  const capturedTime=p?.as_of&&Number.isFinite(Date.parse(p.as_of))?new Date(p.as_of).toLocaleString("ru-RU"):"не указано";
    const freshness=stale?"После анализа данные изменились. Ниже описано прежнее состояние. Состояние объекта сейчас может отличаться — проверьте свежие показания или запросите новый анализ.":"После проверки данные не изменились. Заключение описывает состояние во время анализа, а не подтверждение текущего положения.";
  const errorText={model_reply_invalid:"Модель не сформировала корректный ответ. Запросите анализ повторно.",model_reply_truncated:"Ответ модели получен не полностью. Запросите анализ повторно.",invalid_evidence:"Утверждения модели не подтверждены прочитанными данными. Результат не опубликован.",unavailable:"ИИ недоступен. Мониторинг и ручная обработка продолжают работать.",execution_timeout:"Анализ не завершился за отведённое время. Запросите его повторно."}[j.error?.code]||"Не удалось завершить анализ. Подробности сохранены в технических данных.";
    const freshnessBlock=p?.version===2?`<div class="analysis-freshness ${stale?"analysis-freshness-stale":"analysis-freshness-current"}" role="status"><span class="muted">Актуальность результата</span><br><strong>${stale?"Анализ устарел — данные изменились":"Анализ завершён · данные после проверки не изменились"}</strong><p>${esc(freshness)}</p></div>`:"";
    const body=p?.version===2?`${freshnessBlock}<h3>${esc(p.title)}</h3><p>${esc(p.description)}</p><p><strong>Что установила система</strong><br>${esc(p.established)}</p>${list("Дополнительно установлено",p.observations)}<p><strong>Состояние на момент анализа</strong><br>${esc(p.state?.text||"Состояние не указано.")}</p><p class="muted">Данные на: ${esc(capturedTime)} (местное время)</p><p><strong>Почему нужно обратить внимание</strong><br>${esc(p.attention)}</p><p><strong>Что ещё неизвестно</strong><br>${esc(p.unknown)}</p>${p.history_summary?`<p><strong>Похожие случаи</strong><br>${esc(p.history_summary)}</p>`:""}${list("Записи диспетчера",p.dispatcher_notes)}${list("Что сделать диспетчеру",p.recommendations)}`:j.result?"<p>Для этого результата понятное описание недоступно. Запросите новый анализ; исходные данные сохранены ниже.</p>":"";
    const technical={job_id:j.job_id,status:j.status,stale:Boolean(stale),incident_snapshot:j.incident_snapshot,execution:j.execution,error:j.error,result:j.result};
  setMarkup("analysis",`<strong>Анализ: ${esc({pending:"Ожидается",queued:"Ожидает очереди",running:"Выполняется",completed:"Готов",failed:"Ошибка"}[j.status]||"Статус не определён")} ${esc(staleText)}</strong>${j.error?`<p>${esc(errorText)}</p>`:""}${body}<details class="detail-extra"><summary>Технические данные</summary><p class="muted">Исходные записи и проверенные факты. Предположения и свободный текст модели не являются подтверждёнными причинами происшествия.</p><pre>${esc(JSON.stringify(technical,null,2))}</pre></details>`);
}
function renderSensors(){if(S.diagnostics){S.diagnostics.update(S.sensors);return;}setMarkup("sensors",S.sensors.map(s=>`<div class="sensor-row"><div><strong>${esc(s.sensor_id)}</strong><small> · ${esc(s.type)} ${esc(s.asset_id||s.building_id||"")}</small><br><small>${esc(age(s.last_received_at))}</small></div><span class="${esc(s.status)}">${esc(labels[s.status]||s.status)}</span></div>`).join(""));}
function beep(kind="alarm"){if(!S.sound||!S.audio)return;const o=S.audio.createOscillator(),g=S.audio.createGain();o.frequency.value=kind==="check"?440:660;g.gain.value=kind==="check"?.025:.045;o.connect(g);g.connect(S.audio.destination);o.start();o.stop(S.audio.currentTime+(kind==="check"?.12:.22));}
function renderNotifications(){
  const rows=S.noticeRows.slice(-20).reverse();
  $("notifications").textContent=rows.map(n=>`${notificationNames[n.kind]||"Изменение обработки"} · ${n.incident_id}`).join(". ");
  if(!rows.length)return;
  $("notifications").innerHTML=rows.map(n=>`<button class="notification-item" data-notification="${esc(n.incident_id)}">${esc(notificationNames[n.kind]||"Изменение обработки")}<small>${esc(n.incident_id)} · ${esc(clock(n.created_at))} · открыть карточку</small></button>`).join("");
  $("notifications").querySelectorAll("[data-notification]").forEach(b=>b.onclick=()=>selectIncident(b.dataset.notification).catch(showError));
}
function demoScenarioName(id){return S.demo?.scenario_options?.find(s=>s.id===id)?.name||scenarioNames[id]||id;}
function renderScenarioExpectation(){const el=$("scenario-expectation");if(!el)return;const option=S.demo?.scenario_options?.find(s=>s.id===$("scenario").value);el.textContent=option?.expected_alarm?`Ожидается: ${option.expected_alarm}`:"";el.hidden=!el.textContent;}
function renderDemo(demo){
  S.demo=demo;const available=demo.available!==false&&demo.status!=="unavailable";
  const select=$("scenario"),scenarios=Array.isArray(demo.scenarios)?demo.scenarios:[];
  const signature=scenarios.map(id=>`${id}:${demoScenarioName(id)}`).join("|");
  if(available&&scenarios.length&&select.dataset.scenarios!==signature){const previous=select.dataset.scenarios?select.value:null;select.innerHTML=scenarios.map(id=>`<option value="${esc(id)}">${esc(demoScenarioName(id))}</option>`).join("");select.value=scenarios.includes(previous)?previous:scenarios.includes(demo.scenario)?demo.scenario:scenarios[0];select.dataset.scenarios=signature;}
  select.onchange=renderScenarioExpectation;renderScenarioExpectation();
  $("sim-status").textContent=demo.error?`Ошибка источника: ${demo.error}`:!available?"Источник движения пока не подключён":demo.running?`${demoScenarioName(demo.scenario)} · ${demo.events_sent??demo.event_count??0} событий`:"Сценарий остановлен · новые сигналы не поступают";
  $("start").disabled=!available||S.demoBusy;$("stop").disabled=!available||!demo.running||S.demoBusy;select.disabled=!available||S.demoBusy;
  $("start").title=available?"Запустить выбранный сценарий для всех рабочих мест":"Источник движения не подключён. Карта и ручная обработка доступны.";
}
async function runDemo(action){
  if(S.demoBusy)return;S.demoBusy=true;if(S.demo)renderDemo(S.demo);
  try{await api(`/demo/${action}`,"POST",action==="start"?{scenario:$("scenario").value}:{});$("error").hidden=true;}
  catch(e){showError(e);}finally{S.demoBusy=false;if(S.demo)renderDemo(S.demo);await refresh();}
}
async function notifications(initial=false){
  const context=S.context,operator=S.operator;
  let cursor=initial?0:S.cursor;
  while(true){
    const v=await api(`/dispatch-notifications?after_seq=${cursor}&limit=100`,"GET",undefined,operator);
    if(context!==S.context)return;
    const rows=v.notifications||v.items||[];
    const next=v.next_seq??(rows.at(-1)?.seq||cursor);
    if(!initial&&rows.length){S.noticeRows.push(...rows);S.noticeRows=S.noticeRows.slice(-20);renderNotifications();beep();}
    if(rows.length&&next<=cursor)throw Error("Сервер не продвигает курсор уведомлений.");
    cursor=next;S.cursor=cursor;
    if(!initial||rows.length<100)break;
  }
}
async function refresh(){
  if(S.busy||S.switching||(S.auth?.enabled&&!S.auth.user))return;
  const context=S.context,operator=S.operator;
  S.busy=true;
  try{
    if(S.auth?.enabled)await api("/auth/me","GET",undefined,operator);
    const [assets,sensors,incidents,summary,health,demo,profiles]=await Promise.all([api("/assets","GET",undefined,operator),api("/sensors","GET",undefined,operator),api(`/incidents?scope=${$("all").checked?"all":"workstation"}`,"GET",undefined,operator),api("/dispatch-summary","GET",undefined,operator),api("/health","GET",undefined,operator),api("/demo/status","GET",undefined,operator),api("/operator-profiles","GET",undefined,operator)]);
    if(context!==S.context)return;
    S.assets=array(assets,"assets");S.sensors=array(sensors,"sensors");S.incidents=array(incidents,"incidents");S.summary=summary;S.profiles=array(profiles,"operator_profiles");
    const agentStatus=typeof health.agent==="object"?health.agent.status:health.agent;
    S.agentAvailable=agentStatus==="ready";
    $("agent-status").textContent=S.agentAvailable?"ИИ: готов к анализу":"ИИ: нет связи с локальной моделью";
    if(summary.as_of){S.asOf=Date.parse(summary.as_of);S.serverOffset=S.asOf-Date.now();}
    $("connection").textContent="● Сервер на связи";$("connection").style.color="#5bceae";
    const mlStatus=typeof health.ml==="object"?health.ml.status:health.ml;
    $("ml-status").textContent=mlStatus==="unavailable"||mlStatus==="not_connected"?"Модель движения пока не подключена":`Модель движения: ${mlStatus==="ready"?"готова":mlStatus}`;
    renderDemo(demo);
    drawAssets();renderSummary();renderIncidents();renderSensors();await notifications();
    if(typeof OperationsView!=="undefined")await OperationsView.update(api);
    if(context!==S.context)return;
    if(S.selected)await renderDetails();
    if(S.connectionError){$("error").hidden=true;S.connectionError=false;}
  }catch(e){
    if(context!==S.context)return;
    if(S.auth?.enabled&&(e.status===401||e.code==="session_identity_changed")){showError(e);return;}
    $("connection").textContent="● Связь с сервером потеряна";$("connection").style.color="#ff7272";S.connectionError=true;showError(e);
  }finally{S.busy=false;}
}
function sendPresence(operator,session,availability){
  // Serialise leases: an earlier ready request must finish before the old session goes away.
  const request=S.presenceChain.catch(()=>{}).then(()=>api("/operator-presence","POST",{session_id:session,availability},operator));
  S.presenceChain=request.catch(()=>{});return request;
}
async function presence(){if(S.switching||(S.auth?.enabled&&!S.auth.user))return;try{await sendPresence(S.operator,S.session,$("ready").checked?"ready":"away");}catch(e){showError(e);}}
async function switchOperator(operator){
  if(S.auth?.enabled){$("operator").value=S.operator;return;}
  if(S.switching||operator===S.operator)return;
  const previous=S.operator;
  S.switching=true;$("operator").disabled=true;
  try{
    await sendPresence(previous,S.session,"away");
    S.operator=operator;S.session=crypto.randomUUID();S.context++;S.selected=null;S.detail=null;S.detailId=null;S.job=null;S.cursor=0;S.noticeRows=[];S.detailRequest++;S.mapRenderer?.clearHighlight();
    $("details").textContent="Выберите происшествие";$("selected-id").textContent="";$("notifications").textContent="";$("incidents").textContent="Загрузка рабочего места…";
    await sendPresence(operator,S.session,$("ready").checked?"ready":"away");
    await notifications(true);
    focusSector();
    if(typeof window!=="undefined"){const url=new URL(window.location.href);url.searchParams.set("operator",operator);window.history.replaceState(null,"",url);}
  }catch(e){$("operator").value=S.operator;showError(e);}
  finally{S.switching=false;$("operator").disabled=Boolean(S.auth?.enabled);}
  if(S.operator!==previous)await refresh();
}
async function boot(){
  try{
    if(typeof ProductAuth!=="undefined"){S.auth=await ProductAuth.init({beforeLogout:()=>sendPresence(S.operator,S.session,"away"),onError:showError});if(S.auth.enabled&&!S.auth.user)return;}
    if(typeof window!=="undefined"){S.operator=S.auth?.enabled?S.auth.user.operator_id:initialOperator(window.location.search);$("operator").value=S.operator;$("operator").disabled=Boolean(S.auth?.enabled);}
    S.site=await api("/site");S.profiles=array(await api("/operator-profiles"),"operator_profiles");initMap();
    if(typeof OperationsView!=="undefined")OperationsView.bind(api,showError);
    if(typeof SensorDiagnostics!=="undefined")S.diagnostics=SensorDiagnostics.create({elementId:"sensors",getSensors:()=>S.sensors,requestCheck:()=>api("/sensors"),positionStaleSeconds:S.site.dispatch_config?.position_stale_seconds||5,serverTime:()=>Date.now()+S.serverOffset,onSound:()=>beep("check"),onSelectSensor:id=>{showSensor(id);if(!S.mapRenderer.showIncident({sensor_id:id}))showError(Error("У датчика нет подтверждённой позиции."));}});
    await presence();await notifications(true);await refresh();focusSector();
    setInterval(presence,3000);const loop=async()=>{await refresh();setTimeout(loop,1000);};setTimeout(loop,1000);
  }catch(e){showError(e);}
}
function bindControls(){
  if($("analyse-trends"))$("analyse-trends").onclick=()=>analyseTrends().catch(showError);
  if($("clear-incident-log"))$("clear-incident-log").onclick=()=>clearIncidentLog().catch(showError);
  $("operator").onchange=()=>switchOperator($("operator").value).catch(showError);
  $("ready").onchange=presence;$("all").onchange=refresh;$("history-toggle").onchange=renderIncidents;
  $("fit").onclick=()=>S.mapRenderer?S.mapRenderer.fitAll():S.map.fitBounds([[0,0],[100,100]]);
  if($("my-sector"))$("my-sector").onclick=()=>focusSector(true);
  if($("show-routes"))$("show-routes").onchange=()=>S.mapRenderer?.setRoutesVisible?.($("show-routes").checked);
  for(const id of ["filter-type","filter-state"])if($(id))$(id).onchange=renderIncidents;
  if($("search-incidents"))$("search-incidents").oninput=renderIncidents;
  $("start").onclick=()=>runDemo("start");
  $("stop").onclick=()=>runDemo("stop");
  $("sound").onclick=async()=>{S.sound=!S.sound;if(S.sound){S.audio||=new AudioContext();await S.audio.resume();beep();}$("sound").textContent=S.sound?"Выключить звук":"Включить звук";};
}
if(typeof window!=="undefined"){bindControls();boot();}
