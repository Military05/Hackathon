"use strict";
const $ = id => document.getElementById(id);
const titles = {forbidden_zone:"Въезд в запрещённую зону",unauthorized_access:"Подтверждённый проход без допуска",sensor_offline:"Нет сигнала датчика",model_anomaly:"Необычное движение",route_deviation:"Отклонение от маршрута",collision:"Пересечение транспорта"};
const labels = {open:"Не принято",acknowledged:"Принято",closed:"Завершено",active:"Условие активно",restored:"Условие восстановлено",unknown:"Нужно проверить",online:"На связи",offline:"Нет связи",rejected_model_signal:"Подозрение отклонено"};
const sectorNames = {logistics:"Логистика и склады",production:"Производство",coordination:"КПП"};
const S = {site:null,map:null,assets:[],sensors:[],incidents:[],profiles:[],summary:{},selected:null,detail:null,detailId:null,operator:"dispatcher-1",cursor:0,role:"dispatcher",tab:"objects",tabFilters:{objects:{state:"all",type:"all",query:"",sort:"priority",page:0,scroll:0},people:{state:"all",type:"all",query:"",sort:"priority",page:0,scroll:0}},job:null,session:crypto.randomUUID(),buildingLayers:new Map(),assetLayers:new Map(),sensorLayers:new Map(),asOf:Date.now(),serverOffset:0,busy:false,context:0,detailRequest:0,commandBusy:false,switching:false,presenceChain:Promise.resolve(),connectionError:false,agentAvailable:true,analysisSubmitting:false};
Object.assign(S,{mapRenderer:null,diagnostics:null,noticeRows:[],objectSelection:null,demoBusy:false,demo:null});
const scenarioNames={normal:"Штатная работа завода",logistics:"Доставка комплектующих",shift:"Начало смены",service:"Обход служебного транспорта","forbidden-zone":"Въезд в закрытую зону","unauthorized-access":"Проход без допуска","sensor-offline":"Потеря сигнала датчика",simultaneous:"Несколько происшествий","unusual-movement":"Необычное движение"};
const notificationNames={new_incident:"Новое происшествие",transfer_requested:"Предложена передача",transfer_accepted:"Передача принята",transfer_cancelled:"Передача отменена",transfer_expired:"Срок передачи истёк"};
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
    const r=await fetch(`/api${path}`,{method,credentials:"same-origin",signal:controller.signal,headers:{...(operator?{"X-Demo-Operator":operator}:{}),...(typeof ProductAuth!=="undefined"?ProductAuth.headers():{}),...(body?{"Content-Type":"application/json"}:{})},...(body?{body:JSON.stringify(body)}:{})});
    let v;
    try{v=await r.json();}catch{throw Error("Сервер вернул некорректный ответ");}
    if(!r.ok){
      const messages={revision_conflict:"Карточка изменилась у другого оператора. Посмотрите обновлённые данные и повторите действие осознанно.",operator_available:"Отсутствие ответственного ещё не подтверждено. Подождите установленный срок и проверьте сводку.",startup_grace:"Система восстанавливает рабочие места после запуска. Повторите позже.",operator_conflict:"Действие доступно диспетчеру ответственного сектора или получателю передачи.",unavailable:"Локальная модель недоступна. Мониторинг и ручная обработка продолжаются."};
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
  return Number(!workable(a))-Number(!workable(b))||(priority[a.severity]??3)-(priority[b.severity]??3)||Number(Boolean(a.assigned_operator_id))-Number(Boolean(b.assigned_operator_id))||Date.parse(a.detected_at)-Date.parse(b.detected_at)||a.incident_id.localeCompare(b.incident_id);
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
  if(S.role==="admin"){S.mapRenderer?.fitAll();return;}
  const sector=S.profiles.find(p=>(p.operator_id||p.id)===S.operator)?.sector_id;
  if(S.mapRenderer){S.mapRenderer.focusSector(sector,{animate});return;}
  const rectangles=(S.site.site_areas||[]).filter(a=>a.responsible_sector_id===sector&&a.rectangle).map(a=>a.rectangle);
  if(!rectangles.length){S.map.fitBounds([[0,0],[100,100]]);return;}
  const minX=Math.min(...rectangles.map(r=>r.x)),minY=Math.min(...rectangles.map(r=>r.y)),maxX=Math.max(...rectangles.map(r=>r.x+r.width)),maxY=Math.max(...rectangles.map(r=>r.y+r.height));
  S.map.fitBounds([xy(minX,maxY),xy(maxX,minY)],{padding:[18,18]});
}
function initMap(){
  S.mapRenderer=EnterpriseMap.create({elementId:"map",site:S.site,operator:S.role==="admin"?"admin":S.operator,role:S.role,onAsset:showAsset,onSensor:showSensor,onBuilding:showBuilding});
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
  objectInfo(`${entityName({asset_id:id})} · ${vehicleNames[vehicleType]||"Транспорт"} · Цель: ${named(S.site.buildings,a.destination)||"не задана"} · Последний сигнал: ${clock(a.last_seen)} (${age(a.last_seen)}), позиция ${coordinates}. Допуск: ${allowed.join(", ")||"нет"}. Запрещено: ${forbidden.join(", ")||"нет"}. ${assetStale(a)?"Текущее место не подтверждено.":""}`,"asset",id,(S.site.sensors||[]).filter(s=>s.asset_id===id&&ownSensor(s)).map(s=>s.id||s.sensor_id));
}
function showBuilding(id){const b=S.site.buildings.find(x=>x.id===id);if(!b)return;objectInfo(`${b.name}. Рабочих происшествий: ${relatedIncidents("building",id).length}.`,"building",id,(S.site.sensors||[]).filter(s=>s.building_id===id&&ownSensor(s)).map(s=>s.id||s.sensor_id));}
function showSensor(id,inspect=false){const s=S.sensors.find(x=>x.sensor_id===id);objectInfo(`${named(S.site.sensors,id)} · ${labels[s?.status]||"Сигнал ещё не получен"} · ${age(s?.last_received_at)}. Проверка связи не подтверждает физическую исправность датчика.`,"sensor",id);if(inspect)S.diagnostics?.inspectSensor?.(id);}
function drawAssets(){
  S.mapRenderer?.update({assets:S.assets,sensors:S.sensors,incidents:S.incidents,serverOffset:S.serverOffset});
}
function mySector(){return S.profiles.find(p=>(p.operator_id||p.id)===S.operator)?.sector_id;}
function recipientName(i){const profile=S.profiles.find(p=>p.sector_id===i.responsible_sector_id)||S.site?.operator_profiles?.find(p=>p.sector_id===i.responsible_sector_id);const id=i.pending_transfer?.status==="pending"?i.pending_transfer.to_operator_id:i.assigned_operator_id||profile?.operator_id||profile?.id;return operatorName(id);}
function belongsToPeople(i){return Boolean(i.employee_id||i.type==="unauthorized_access");}
function ownSensor(sensor){
  if(S.role==="admin")return true;
  const meta=S.site.sensors?.find(row=>(row.id||row.sensor_id)===(sensor.sensor_id||sensor.id))||sensor;
  const asset=S.site.assets?.find(row=>row.id===meta.asset_id),building=S.site.buildings?.find(row=>row.id===(meta.building_id||asset?.destination));
  const area=S.site.site_areas?.find(row=>row.id===(meta.site_area_id||building?.site_area_id));return area?.responsible_sector_id===mySector();
}
function hideSelected(){S.selected=null;S.detail=null;S.detailId=null;S.job=null;S.detailRequest++;$("selected-panel").hidden=true;$("details").textContent="";$("selected-id").textContent="";S.mapRenderer?.clearHighlight();renderIncidents();}
function saveTabFilters(){S.tabFilters[S.tab]={state:$("filter-state").value,type:$("filter-type").value,query:$("search-incidents").value,sort:$("incident-sort").value,page:S.tabFilters[S.tab].page||0,scroll:$("incidents").scrollTop};}
function switchIncidentTab(tab){
  if(tab!==S.tab){saveTabFilters();S.tab=tab;hideSelected();}
  const filters=S.tabFilters[tab];$("filter-state").value=filters.state;$("filter-type").value=filters.type;$("search-incidents").value=filters.query;$("incident-sort").value=filters.sort;
  $("filter-type").querySelectorAll("option").forEach(option=>{option.hidden=tab==="people"&&!(["all","unauthorized_access","sensor_offline"].includes(option.value));});
  for(const name of ["objects","people"]){const button=$(`${name}-tab`);button.setAttribute("aria-selected",String(name===tab));button.classList.toggle("active",name===tab);}
  $("incident-workspace").setAttribute("aria-labelledby",`${tab}-tab`);
  $("checkpoint-panel").hidden=tab!=="people"||!(S.role==="admin"||S.operator==="dispatcher-3");
  if(typeof OperationsView!=="undefined"){OperationsView.configure({role:S.role,operator:S.operator,tab});OperationsView.update(api);}
  renderIncidents();renderNotifications();$("incidents").scrollTop=filters.scroll||0;
}
function configureWorkspace(){
  const admin=S.role==="admin";document.body.classList.toggle("admin-workspace",admin);document.querySelector(".operator").hidden=admin;document.querySelector(".activity-bar").hidden=admin;$("activity-export").hidden=admin;$("my-sector").hidden=admin;
  const people=admin||S.operator==="dispatcher-3"||S.incidents.some(belongsToPeople);$("people-tab").hidden=!people;if(!people&&S.tab==="people")S.tab="objects";
  if(typeof OperationsView!=="undefined")OperationsView.configure({role:S.role,operator:S.operator,tab:S.tab});switchIncidentTab(S.tab);
}

function renderIncidents(){
  const type=$("filter-type").value,state=$("filter-state").value,query=$("search-incidents").value.trim().toLocaleLowerCase("ru-RU"),sort=$("incident-sort").value;
  const rows=S.incidents.filter(i=>{
    if(!workable(i)||belongsToPeople(i)!==(S.tab==="people"))return false;
    if(type!=="all"&&i.type!==type)return false;
    if(state!=="all"&&(state==="unclaimed"?Boolean(i.assigned_operator_id):state==="mine"?i.assigned_operator_id!==S.operator:state==="pending"?i.pending_transfer?.status!=="pending":i.condition_state!==state&&i.status!==state))return false;
    return !query||[titles[i.type],entityName(i),placeName(i),recipientName(i),i.incident_id,i.asset_id,i.other_asset_id,i.sensor_id,i.building_id,i.zone_id].join(" ").toLocaleLowerCase("ru-RU").includes(query);
  }).sort(sort==="newest"?(a,b)=>Date.parse(b.detected_at)-Date.parse(a.detected_at):sort==="oldest"?(a,b)=>Date.parse(a.detected_at)-Date.parse(b.detected_at):compareIncidents);
  const filters=S.tabFilters[S.tab],pages=Math.max(1,Math.ceil(rows.length/8));filters.page=Math.min(filters.page||0,pages-1);const page=rows.slice(filters.page*8,filters.page*8+8);
  setMarkup("incidents",page.length?page.map(i=>`<button class="incident ${esc(i.severity)} ${i.incident_id===S.selected?"selected":""}" data-id="${esc(i.incident_id)}"><strong>${esc(titles[i.type]||i.type)}</strong><p>${esc(entityName(i))} · ${esc(placeName(i))}</p><small>${esc(labels[i.status]||i.status)} · ${S.role==="admin"?"Направлено: ":""}${esc(recipientName(i))}</small><span class="pill">${esc(labels[i.condition_state]||i.condition_state||"active")}</span><time datetime="${esc(i.detected_at)}">${esc(clock(i.detected_at))} · ${esc(age(i.detected_at))}</time></button>`).join(""):"<p class='muted'>Нет активных происшествий по выбранным условиям.</p>");
  $("incident-count").textContent=rows.length?`${rows.length} активных`:"";$("incident-prev").disabled=filters.page===0;$("incident-next").disabled=filters.page+1>=pages;$("incident-page").textContent=rows.length?`${filters.page+1} / ${pages}`:"";document.querySelector(".incident-pagination").hidden=pages===1;
  $("incidents").querySelectorAll("[data-id]").forEach(b=>b.onclick=()=>selectIncident(b.dataset.id).catch(showError));
}
async function clearIncidents(){
  const button=$("clear-incidents");if(button.disabled)return;button.disabled=true;
  try{await api("/incidents/clear","POST",{});S.context++;S.incidents=[];S.noticeRows=[];$("notifications").textContent="";hideSelected();for(const filters of Object.values(S.tabFilters))filters.page=0;await refresh();}catch(error){showError(error);}finally{button.disabled=false;}
}

async function selectIncident(id){S.selected=id;S.detail=null;S.detailId=null;S.job=null;S.detailRequest++;$("selected-panel").hidden=false;$("selected-panel").open=true;S.mapRenderer?.clearHighlight();$("selected-id").textContent=id;$("details").textContent="Загрузка карточки…";renderIncidents();await renderDetails();}
function showSelectedOnMap(){if(!S.detail||S.detail.incident_id!==S.selected)return;const found=S.mapRenderer?.showIncident(S.detail);if(found===false){showError(Error("Для этого случая нет подтверждённых координат. Проверьте источник данных; точка на карте не выдумывается."));}else $("error").hidden=true;}
async function command(action,extra={}){
  const shown=S.detail;
  if(S.role==="admin")throw Error("Администратор наблюдает происшествия; обработка доступна назначенному диспетчеру.");
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
function recipientOptions(i){
  const profiles=S.profiles.filter(p=>(p.operator_id||p.id)!==S.operator);
  const select=$("recipient"),previous=select.value;
  const ids=profiles.map(p=>p.operator_id||p.id).join("|");
  if(select.dataset.ids!==ids){select.innerHTML=profiles.map(p=>`<option value="${esc(p.operator_id||p.id)}">${esc(p.name||p.operator_id||p.id)}</option>`).join("");select.dataset.ids=ids;}
  for(const option of select.options){const p=profiles.find(p=>(p.operator_id||p.id)===option.value);option.disabled=!(p?.can_receive_transfer??p?.operator_ready);option.textContent=`${p?.name||option.value}${p?.operator_ready?" · на смене":p?.can_receive_transfer?" · ожидает входа":" · недоступен"}`;}
  const available=profiles.filter(p=>p.can_receive_transfer??p.operator_ready).map(p=>p.operator_id||p.id);
  select.value=available.includes(previous)?previous:available.includes(S.operator)?S.operator:available[0]||"";
  select.disabled=S.commandBusy||!available.length;
}
function updateActions(i){
  if(S.detailId!==i.incident_id)return;
  if(S.role==="admin"){
    $("recipient-wrap").hidden=true;$("response-controls").hidden=true;$("analysis").hidden=true;
    setMarkup("detail-actions",'<button id="show-on-map">Показать на карте</button>');$("show-on-map").onclick=showSelectedOnMap;return;
  }
  const own=i.assigned_operator_id===S.operator,working=workable(i),pending=i.pending_transfer?.status==="pending";
  $("recipient-wrap").hidden=!(own&&!pending&&working);
  recipientOptions(i);
  $("recipient").onchange=()=>updateActions(S.detail);
  const readyRecipient=Boolean($("recipient").value);
  const disabled=S.commandBusy?" disabled":"";
  const button=(id,text,primary=false,extraDisabled=false)=>`<button id="${id}"${primary?' class="primary"':""}${disabled||extraDisabled?" disabled":""}>${text}</button>`;
  setMarkup("detail-actions",`${button("show-on-map","Показать на карте")}${!i.assigned_operator_id&&working&&i.can_claim!==false?button("claim","Принять ответственность",true):""}${own&&working?button("contact","Записать: связался")+button("inspect","Записать: запросил проверку")+button("save-note","Сохранить заметку"):""}${own&&i.condition_active===false&&i.condition_state!=="unknown"&&!pending&&working?button("close","Завершить обработку"):""}${own&&i.type==="model_anomaly"&&working?button("dismiss","Отклонить модельное подозрение"):""}${own&&!pending&&working?button("transfer","Предложить передачу",false,!readyRecipient):""}${pending?`<span class="pill">Передача → ${esc(operatorName(i.pending_transfer.to_operator_id))}</span><small class="transfer-clock">${esc(transferClock(i))}</small>${i.pending_transfer.to_operator_id===S.operator?button("accept","Принять передачу",true):""}${own?button("cancel","Отменить передачу"):""}`:""}${button("analyse",S.agentAvailable?"Проанализировать ИИ":"ИИ ещё не подключён",false,!S.agentAvailable||S.analysisSubmitting)}`);

  const bind=(id,fn)=>{if($(id))$(id).onclick=async()=>{try{await fn();}catch(e){showError(e);}};};
  bind("claim",()=>command("claim"));
  bind("close",()=>command("close",{reason:reason()||"Условие восстановлено, проверено оператором"}));
  bind("contact",()=>command("record_response",{response_code:"contacted",reason:reason()||"Оператор сообщил о выполненном контакте"}));
  bind("inspect",()=>command("record_response",{response_code:"inspection_requested",reason:reason()||"Оператор сообщил о запросе проверки"}));
  bind("dismiss",()=>command("dismiss_model",{reason:reason()||"Проверено оператором: штатная операция"}));
  bind("transfer",()=>command("request_transfer",{to_operator_id:$("recipient").value,reason:reason()||"Запрошена помощь"}));
  bind("accept",()=>command("accept_transfer",{transfer_id:S.detail.pending_transfer.transfer_id}));
  bind("cancel",()=>command("cancel_transfer",{transfer_id:S.detail.pending_transfer.transfer_id,reason:reason()||"Передача отменена отправителем"}));
  bind("save-note",saveNote);
  bind("analyse",startAnalysis);
  bind("show-on-map",showSelectedOnMap);
}
function applyDetails(i){
  if(i.incident_id!==S.selected)return;if(S.detail?.incident_id===i.incident_id&&S.detail.dispatch_revision>i.dispatch_revision)return;
  if(!workable(i)){hideSelected();return;}
  if(S.detailId!==i.incident_id){
    $("details").innerHTML='<div id="detail-facts" class="facts"></div><div id="response-controls" class="response"><label for="reason">Заметка / реакция диспетчера</label><input id="reason" placeholder="Причина / выполненное действие" maxlength="500"></div><div id="recipient-wrap" class="recipient-wrap"><label for="recipient">Получатель передачи</label><select id="recipient"></select></div><div id="detail-actions" class="actions"></div><div id="analysis" class="analysis"></div>';S.detailId=i.incident_id;
  }
  S.detail=i;$("selected-id").textContent=i.incident_id;
  setMarkup("detail-facts",`<div><label>Причина</label>${esc(titles[i.type]||i.type)}</div><div><label>Место и объект</label>${esc(placeName(i))} · ${esc(entityName(i))}</div><div><label>Условие / обработка</label>${esc(labels[i.condition_state]||i.condition_state)} / ${esc(labels[i.status]||i.status)}</div><div><label>${S.role==="admin"?"Направлено диспетчеру":"Ответственный / получатель"}</label>${esc(recipientName(i))}</div><div><label>Обнаружено</label>${esc(clock(i.detected_at))} · ${esc(age(i.detected_at))}</div>`);
  S.mapRenderer?.highlight(i,{recenter:false});
  updateActions(i);
  if(S.role!=="admin"&&!S.job)setMarkup("analysis",S.agentAvailable?"":"<p class='muted'>ИИ пока недоступен.</p>");
}

async function renderDetails(){
  if(!S.selected||!$("selected-panel").open)return;
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
async function startAnalysis(){
  const id=S.selected,context=S.context,operator=S.operator;
  if(S.role==="admin"||!id||S.analysisSubmitting||!S.agentAvailable)return;
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
  const errorText={model_reply_invalid:"Модель не сформировала корректный ответ. Запросите анализ повторно.",model_reply_truncated:"Ответ модели получен не полностью. Запросите анализ повторно.",invalid_evidence:"Утверждения модели не подтверждены прочитанными данными. Результат не опубликован.",unavailable:"ИИ недоступен. Мониторинг и ручная обработка продолжают работать.",execution_timeout:"Анализ не завершился за отведённое время. Запросите его повторно."}[j.error?.code]||"Не удалось завершить анализ. Подробности сохранены на сервере.";
    const freshnessBlock=p?.version===2?`<div class="analysis-freshness ${stale?"analysis-freshness-stale":"analysis-freshness-current"}" role="status"><span class="muted">Актуальность результата</span><br><strong>${stale?"Анализ устарел — данные изменились":"Анализ завершён · данные после проверки не изменились"}</strong><p>${esc(freshness)}</p></div>`:"";
    const body=p?.version===2?`${freshnessBlock}<h3>${esc(p.title)}</h3><p>${esc(p.description)}</p><p><strong>Что установила система</strong><br>${esc(p.established)}</p>${list("Дополнительно установлено",p.observations)}<p><strong>Состояние на момент анализа</strong><br>${esc(p.state?.text||"Состояние не указано.")}</p><p class="muted">Данные на: ${esc(capturedTime)} (местное время)</p><p><strong>Почему нужно обратить внимание</strong><br>${esc(p.attention)}</p><p><strong>Что ещё неизвестно</strong><br>${esc(p.unknown)}</p>${p.history_summary?`<p><strong>Похожие случаи</strong><br>${esc(p.history_summary)}</p>`:""}${list("Записи диспетчера",p.dispatcher_notes)}${list("Что сделать диспетчеру",p.recommendations)}`:j.result?"<p>Для этого результата понятное описание недоступно. Запросите новый анализ; исходные данные сохранены на сервере.</p>":"";
  setMarkup("analysis",`<strong>Анализ: ${esc({pending:"Ожидается",queued:"Ожидает очереди",running:"Выполняется",completed:"Готов",failed:"Ошибка"}[j.status]||"Статус не определён")} ${esc(staleText)}</strong>${j.error?`<p>${esc(errorText)}</p>`:""}${body}`);
}
function renderSensors(){if(S.diagnostics){S.diagnostics.update(S.sensors.filter(ownSensor));return;}setMarkup("sensors",S.sensors.filter(ownSensor).map(s=>`<div class="sensor-row"><div><strong>${esc(s.sensor_id)}</strong><small> · ${esc(s.type)} ${esc(s.asset_id||s.building_id||"")}</small><br><small>${esc(age(s.last_received_at))}</small></div><span class="${esc(s.status)}">${esc(labels[s.status]||s.status)}</span></div>`).join(""));}
function renderNotifications(){
  const rows=S.noticeRows.filter(n=>S.incidents.some(i=>i.incident_id===n.incident_id&&workable(i)&&belongsToPeople(i)===(S.tab==="people"))).slice(-3).reverse();
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
  $("sim-status").textContent=demo.error?`Ошибка источника: ${demo.error}`:!available?"Источник движения пока не подключён":demo.running?`${demoScenarioName(demo.scenario)}${demo.demonstration_complete?" · эпизод завершён":""}`:"Сценарий остановлен · новые сигналы не поступают";
  $("start").disabled=!available||S.demoBusy;$("stop").disabled=!available||!demo.running||S.demoBusy;select.disabled=!available||S.demoBusy;
  $("start").title=available?"Запустить выбранный сценарий для всех рабочих мест":"Источник движения не подключён. Карта и ручная обработка доступны.";
}
async function runDemo(action){
  if(S.demoBusy)return;S.demoBusy=true;if(S.demo)renderDemo(S.demo);
  try{await api(`/demo/${action}`,"POST",action==="start"?{scenario:$("scenario").value}:{});$("error").hidden=true;}
  catch(e){showError(e);}finally{S.demoBusy=false;if(S.demo)renderDemo(S.demo);await refresh();}
}
async function notifications(initial=false){
  if(S.role==="admin")return;
  const context=S.context,operator=S.operator;
  let cursor=initial?0:S.cursor;
  while(true){
    const v=await api(`/dispatch-notifications?after_seq=${cursor}&limit=100`,"GET",undefined,operator);
    if(context!==S.context)return;
    const rows=v.notifications||v.items||[];
    const next=v.next_seq??(rows.at(-1)?.seq||cursor);
    if(!initial&&rows.length){S.noticeRows.push(...rows);S.noticeRows=S.noticeRows.slice(-20);renderNotifications();}
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
    const [assets,sensors,incidents,summary,health,demo,profiles]=await Promise.all([api("/assets","GET",undefined,operator),api("/sensors","GET",undefined,operator),api(`/incidents?scope=${S.role==="admin"?"all":"workstation"}&limit=200`,"GET",undefined,operator),api("/dispatch-summary","GET",undefined,operator),api("/health","GET",undefined,operator),api("/demo/status","GET",undefined,operator),api("/operator-profiles","GET",undefined,operator)]);
    if(context!==S.context)return;
    S.assets=array(assets,"assets");S.sensors=array(sensors,"sensors");S.incidents=array(incidents,"incidents");if(S.selected&&!S.incidents.some(i=>i.incident_id===S.selected&&workable(i)))hideSelected();S.summary=summary;S.profiles=array(profiles,"operator_profiles");$("people-tab").hidden=!(S.role==="admin"||S.operator==="dispatcher-3"||S.incidents.some(belongsToPeople));
    const agentStatus=typeof health.agent==="object"?health.agent.status:health.agent;
    S.agentAvailable=agentStatus==="ready";
    $("agent-status").textContent=S.agentAvailable?"ИИ: готов к анализу":"ИИ: нет связи с локальной моделью";
    if(summary.as_of){S.asOf=Date.parse(summary.as_of);S.serverOffset=S.asOf-Date.now();}
    $("connection").textContent="● Сервер на связи";$("connection").style.color="#5bceae";
    const mlStatus=typeof health.ml==="object"?health.ml.status:health.ml;
    $("ml-status").textContent=mlStatus==="unavailable"||mlStatus==="not_connected"?"Модель движения пока не подключена":`Модель движения: ${mlStatus==="ready"?"готова":mlStatus}`;
    renderDemo(demo);
    drawAssets();renderIncidents();renderSensors();renderNotifications();await notifications();
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
async function presence(){if(S.role==="admin"||S.switching||(S.auth?.enabled&&!S.auth.user))return;try{await sendPresence(S.operator,S.session,$("ready").checked?"ready":"away");}catch(e){showError(e);}}
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
    configureWorkspace();S.mapRenderer?.setOperator?.(S.operator,S.role);focusSector();
    if(typeof window!=="undefined"){const url=new URL(window.location.href);url.searchParams.set("operator",operator);window.history.replaceState(null,"",url);}
  }catch(e){$("operator").value=S.operator;showError(e);}
  finally{S.switching=false;$("operator").disabled=Boolean(S.auth?.enabled);}
  if(S.operator!==previous)await refresh();
}
async function boot(){
  try{
    if(typeof ProductAuth!=="undefined"){S.auth=await ProductAuth.init({beforeLogout:()=>S.role==="admin"?Promise.resolve():sendPresence(S.operator,S.session,"away"),onError:showError});if(S.auth.enabled&&!S.auth.user)return;}
    if(typeof window!=="undefined"){S.role=S.auth?.user?.role||"dispatcher";S.operator=S.auth?.enabled?S.auth.user.operator_id:initialOperator(window.location.search);$("operator").value=S.operator;$("operator").disabled=Boolean(S.auth?.enabled);}
    S.site=await api("/site");S.profiles=array(await api("/operator-profiles"),"operator_profiles");initMap();configureWorkspace();
    if(typeof OperationsView!=="undefined")OperationsView.bind(api,showError);
    if(typeof SensorDiagnostics!=="undefined")S.diagnostics=SensorDiagnostics.create({elementId:"sensors",getSensors:()=>S.sensors.filter(ownSensor),requestCheck:async()=>array(await api("/sensors"),"sensors").filter(ownSensor),positionStaleSeconds:S.site.dispatch_config?.position_stale_seconds||5,serverTime:()=>Date.now()+S.serverOffset,onSelectSensor:id=>{showSensor(id);if(!S.mapRenderer.showIncident({sensor_id:id}))showError(Error("У датчика нет подтверждённой позиции."));}});
    await presence();await notifications(true);await refresh();focusSector();
    setInterval(presence,3000);const loop=async()=>{await refresh();setTimeout(loop,1000);};setTimeout(loop,1000);
  }catch(e){showError(e);}
}
function bindControls(){
  $("operator").onchange=()=>switchOperator($("operator").value).catch(showError);$("ready").onchange=presence;
  $("fit").onclick=()=>S.mapRenderer?S.mapRenderer.fitAll():S.map.fitBounds([[0,0],[100,100]]);
  $("my-sector").onclick=()=>focusSector(true);$("show-routes").onchange=()=>S.mapRenderer?.setRoutesVisible?.($("show-routes").checked);
  for(const id of ["filter-type","filter-state","incident-sort"]){$(id).onchange=()=>{S.tabFilters[S.tab].page=0;saveTabFilters();renderIncidents();};}
  $("search-incidents").oninput=()=>{S.tabFilters[S.tab].page=0;saveTabFilters();renderIncidents();};
  $("objects-tab").onclick=()=>switchIncidentTab("objects");$("people-tab").onclick=()=>switchIncidentTab("people");
  $("incident-prev").onclick=()=>{S.tabFilters[S.tab].page--;renderIncidents();};$("incident-next").onclick=()=>{S.tabFilters[S.tab].page++;renderIncidents();};
  $("clear-incidents").onclick=clearIncidents;$("close-selected").onclick=event=>{event.preventDefault();hideSelected();};
  $("start").onclick=()=>runDemo("start");$("stop").onclick=()=>runDemo("stop");
}
if(typeof window!=="undefined"){bindControls();boot();}
