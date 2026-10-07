"use strict";
const $ = id => document.getElementById(id);
const titles = {forbidden_zone:"Въезд в запрещённую зону",unauthorized_access:"Подтверждённый проход без допуска",sensor_offline:"Нет сигнала датчика",model_anomaly:"Необычное движение"};
const labels = {open:"Не принято",acknowledged:"Принято",closed:"Завершено",active:"Условие активно",restored:"Условие восстановлено",unknown:"Нужно проверить",online:"На связи",offline:"Нет связи",rejected_model_signal:"Подозрение отклонено"};
const sectorNames = {logistics:"Логистика",production:"Производство",coordination:"Координация"};
const actionNames = {detected:"Обнаружено",claim:"Ответственность принята",record_response:"Реакция записана",condition_restored:"Условие восстановлено",escalation:"Эскалация",request_transfer:"Передача предложена",accept_transfer:"Передача принята",cancel_transfer:"Передача отменена",reassign_unavailable:"Переназначение отсутствующего оператора",dismiss_model:"Модельное подозрение отклонено",close:"Обработка завершена"};
const S = {site:null,map:null,assets:[],sensors:[],incidents:[],profiles:[],summary:{},selected:null,detail:null,detailId:null,operator:"dispatcher-1",cursor:0,sound:false,audio:null,job:null,session:crypto.randomUUID(),buildingLayers:new Map(),assetLayers:new Map(),sensorLayers:new Map(),asOf:Date.now(),serverOffset:0,busy:false,context:0,detailRequest:0,commandBusy:false,switching:false,presenceChain:Promise.resolve(),connectionError:false,agentAvailable:true,analysisSubmitting:false};
const esc = x => String(x??"—").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function showError(e){$("error").hidden=false;$("error").textContent=e.message||String(e);}
async function api(path,method="GET",body,operator=S.operator){
  const controller=new AbortController();
  const t=setTimeout(()=>controller.abort(),6000);
  try{
    const r=await fetch(`/api${path}`,{method,signal:controller.signal,headers:{"X-Demo-Operator":operator,...(body?{"Content-Type":"application/json"}:{})},...(body?{body:JSON.stringify(body)}:{})});
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
function age(t){if(!t)return "Сигнал ещё не получен";const n=Math.max(0,Math.round((Date.now()+S.serverOffset-Date.parse(t))/1000));return `${n} сек. назад`;}
function workable(i){return i.status!=="closed"&&i.disposition!=="rejected_model_signal";}
function assetStale(a){return !a?.last_seen||Date.now()+S.serverOffset-Date.parse(a.last_seen)>=(S.site.dispatch_config?.position_stale_seconds||5)*1000;}
function compareIncidents(a,b){
  const priority={critical:0,warning:1,info:2};
  return Number(!workable(a))-Number(!workable(b))||(priority[a.severity]??3)-(priority[b.severity]??3)||(b.escalation_level||0)-(a.escalation_level||0)||Number(Boolean(a.assigned_operator_id))-Number(Boolean(b.assigned_operator_id))||Date.parse(a.detected_at)-Date.parse(b.detected_at)||a.incident_id.localeCompare(b.incident_id);
}
function setMarkup(id,value){const el=$(id);if(el.innerHTML!==value)el.innerHTML=value;}
function sameSelection(id,context){return S.selected===id&&S.context===context;}
function initialOperator(search){const value=new URLSearchParams(search).get("operator");return ["dispatcher-1","dispatcher-2","dispatcher-3"].includes(value)?value:"dispatcher-1";}
function focusSector(){
  const sector=S.profiles.find(p=>(p.operator_id||p.id)===S.operator)?.sector_id;
  const rectangles=(S.site.site_areas||[]).filter(a=>a.responsible_sector_id===sector&&a.rectangle).map(a=>a.rectangle);
  if(!rectangles.length){S.map.fitBounds([[0,0],[100,100]]);return;}
  const minX=Math.min(...rectangles.map(r=>r.x)),minY=Math.min(...rectangles.map(r=>r.y)),maxX=Math.max(...rectangles.map(r=>r.x+r.width)),maxY=Math.max(...rectangles.map(r=>r.y+r.height));
  S.map.fitBounds([xy(minX,maxY),xy(maxX,minY)],{padding:[18,18]});
}
function initMap(){
  S.map=L.map("map",{crs:L.CRS.Simple,minZoom:1,maxZoom:5,zoomSnap:.25}).fitBounds([[0,0],[100,100]]);
  S.map.attributionControl.addAttribution("Демонстрационная схема · без GPS");
  const pane=S.map.createPane("roads");pane.style.zIndex=350;
  for(let i=0;i<=100;i+=10){L.polyline([xy(i,0),xy(i,100)],{color:"#294052",weight:1,interactive:false}).addTo(S.map);L.polyline([xy(0,i),xy(100,i)],{color:"#294052",weight:1,interactive:false}).addTo(S.map);}
  for(const road of S.site.roads||[]){const pts=road.points||road.path||road;if(Array.isArray(pts))L.polyline(pts.map(p=>Array.isArray(p)?xy(...p):xy(p.x,p.y)),{pane:"roads",color:"#435b6b",weight:17,lineCap:"square",interactive:false}).addTo(S.map);}
  for(const z of S.site.zones){const r=z.rectangle;L.rectangle([xy(r.x,r.y),xy(r.x+r.width,r.y+r.height)],{color:"#ed9b58",weight:1.5,dashArray:"5 4",fillColor:"#c77932",fillOpacity:.14}).addTo(S.map).bindTooltip(`${esc(z.id)} · ${esc(z.name||"Ограниченный доступ")}`);}
  for(const b of S.site.buildings){
    const r=b.rectangle;
    const layer=L.rectangle([xy(r.x,r.y),xy(r.x+r.width,r.y+r.height)],{color:b.id==="O1"?"#a7b7e4":"#538fac",weight:1.5,fillColor:b.id==="O1"?"#53638d":"#25455c",fillOpacity:.9}).addTo(S.map).bindTooltip(b.id==="O1"?"Центральный<br>офис":esc(b.name),{permanent:true,direction:"center",className:"map-label"});
    layer.on("click",()=>{$("asset-info").textContent=`${b.name} (${b.id}). ${S.incidents.filter(i=>i.building_id===b.id&&workable(i)).length} рабочих происшествий. Ограничения доступа задаёт конфигурация.`;});
    S.buildingLayers.set(b.id,layer);
  }
  drawAssets();focusSector();
}
function showAsset(id){
  const a=S.assets.find(x=>x.asset_id===id);
  if(!a){$("asset-info").textContent="Объект отсутствует в текущих данных.";return;}
  $("asset-info").textContent=`${a.asset_id} · ${a.vehicle_type||a.type||"Транспорт"} · Цель: ${a.destination||"не задана"} · Последняя позиция (${a.x.toFixed(1)}, ${a.y.toFixed(1)}), ${age(a.last_seen)}. ${assetStale(a)?"Текущее место не подтверждено.":""}`;
}
function drawAssets(){
  for(const a of S.assets){
    if(!Number.isFinite(a.x)||!Number.isFinite(a.y))continue;
    const alarm=S.incidents.some(i=>i.asset_id===a.asset_id&&i.condition_active&&workable(i));
    const color=assetStale(a)?"#e5b655":alarm?"#ff7272":"#4eb1ff";
    let l=S.assetLayers.get(a.asset_id);
    if(!l){l=L.circleMarker(xy(a.x,a.y),{radius:7,weight:2,fillOpacity:1}).addTo(S.map).bindTooltip(esc(a.asset_id),{permanent:true,direction:"right"});l.on("click",()=>showAsset(a.asset_id));S.assetLayers.set(a.asset_id,l);}
    l.setLatLng(xy(a.x,a.y));l.setStyle({color,fillColor:color});
  }
  for(const s of S.sensors){
    const def=(S.site.sensors||[]).find(d=>(d.id||d.sensor_id)===s.sensor_id)||s;
    if(def.type==="position"||def.asset_id)continue;
    const pos=def.position||def;
    if(!Number.isFinite(pos.x)||!Number.isFinite(pos.y))continue;
    let l=S.sensorLayers.get(s.sensor_id);
    const color=s.status==="online"?"#5bceae":s.status==="offline"?"#ff7272":"#e5b655";
    if(!l){l=L.circleMarker(xy(pos.x,pos.y),{radius:4,weight:1,fillOpacity:1}).addTo(S.map).bindTooltip(esc(s.sensor_id));l.on("click",()=>{const p=S.sensors.find(i=>i.sensor_id===s.sensor_id);if(p)$("asset-info").textContent=`${p.sensor_id} · ${labels[p.status]||p.status} · ${age(p.last_received_at)}. Heartbeat показывает связь с источником, а не свежесть позиции.`;});S.sensorLayers.set(s.sensor_id,l);}
    l.setStyle({color,fillColor:color});
  }
  for(const [id,l] of S.buildingLayers){const danger=S.incidents.some(i=>i.building_id===id&&i.condition_active&&workable(i));l.setStyle({color:danger?"#ff7272":id==="O1"?"#a7b7e4":"#538fac"});}
}
function renderSummary(){
  const rows=S.summary.sectors||[];
  setMarkup("summary",rows.map(r=>`<article><div><strong>${esc(sectorNames[r.sector_id]||r.sector_id)}</strong><small>${r.operator_ready?"Оператор готов":r.operator_online||r.client_online?"Рабочее место на связи · оператор отсутствует":"Нет связи с рабочим местом"}</small></div><div><b>${r.active_count||0}</b><small>активных · ${r.unclaimed_count||0} не принято<br>${r.escalated_count||0} эскалировано</small></div></article>`).join("")+`<div class="unknown-summary">Место не определено: <strong>${S.summary.unknown_count||0}</strong> рабочих случаев. Проверяйте источник и свежесть позиции.</div>`);
}
function renderIncidents(){
  const rows=S.incidents.filter(i=>$("history-toggle").checked||workable(i)).sort(compareIncidents);
  setMarkup("incidents",rows.length?rows.map(i=>`<button class="incident ${esc(i.severity)} ${!workable(i)?"archived":""} ${i.incident_id===S.selected?"selected":""}" data-id="${esc(i.incident_id)}"><strong>${esc(titles[i.type]||i.type)}</strong><p>${esc(i.asset_id||i.employee_id||i.sensor_id)} · ${esc(i.zone_id||i.building_id||i.site_area_id||"Место не определено")}</p><small>${esc(labels[i.disposition]||labels[i.status]||i.status)} · ${esc(i.assigned_operator_id||"Ответственный не назначен")}</small><span class="pill">${esc(labels[i.condition_state]||i.condition_state||"active")}</span>${i.escalation_level?`<span class="pill escalated">Эскалация ${i.escalation_level}</span>`:""}</button>`).join(""):"<p class='muted'>В этом рабочем месте нет рабочих происшествий. Завершённые и отклонённые доступны в истории.</p>");
  $("incidents").querySelectorAll("[data-id]").forEach(b=>b.onclick=()=>selectIncident(b.dataset.id).catch(showError));
}
async function selectIncident(id){S.selected=id;S.detail=null;S.detailId=null;S.job=null;S.detailRequest++;$("selected-id").textContent=id;$("details").textContent="Загрузка карточки…";renderIncidents();await renderDetails();}
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
  setMarkup("detail-actions",`${!i.assigned_operator_id&&working?button("claim","Принять ответственность",true):""}${own&&working?button("contact","Записать: связался")+button("inspect","Записать: запросил проверку"):""}${own&&i.condition_active===false&&i.condition_state!=="unknown"&&!pending&&working?button("close","Завершить обработку"):""}${own&&i.type==="model_anomaly"&&working?button("dismiss","Отклонить модельное подозрение"):""}${own&&!pending&&working?button("transfer","Предложить передачу",false,!readyRecipient):""}${recover?button("reassign","Переназначить отсутствующего",false,!readyRecipient):""}${pending?`<span class="pill">Передача → ${esc(i.pending_transfer.to_operator_id)}</span>${i.pending_transfer.to_operator_id===S.operator?button("accept","Принять передачу",true):""}${own?button("cancel","Отменить передачу"):""}`:""}${button("analyse",S.agentAvailable?"Проанализировать ИИ":"ИИ ещё не подключён",false,!S.agentAvailable||S.analysisSubmitting)}`);
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
  bind("analyse",startAnalysis);
}
function applyDetails(i){
  if(i.incident_id!==S.selected)return;
  if(S.detail?.incident_id===i.incident_id&&S.detail.dispatch_revision>i.dispatch_revision)return;
  if(S.detailId!==i.incident_id){
    // Inputs live outside the refreshed facts/actions; polling never replaces them.
    $("details").innerHTML='<div id="detail-facts" class="facts"></div><div id="response-plan" class="response-plan"></div><div class="response"><label for="reason">Реакция оператора</label><input id="reason" placeholder="Причина / запись выполненного действия" maxlength="500"></div><div id="recipient-wrap" class="recipient-wrap"><label for="recipient">Получатель</label><select id="recipient"></select></div><p id="recovery-hint" class="muted">Резерв может переназначить случай после подтверждённого отсутствия ответственного. Срок и готовность получателя проверяет сервер.</p><div id="detail-actions" class="actions"></div><div id="detail-history" class="history"></div><div id="analysis" class="analysis"></div>';
    S.detailId=i.incident_id;
  }
  S.detail=i;$("selected-id").textContent=i.incident_id;
  setMarkup("detail-facts",`<div><label>Причина</label>${esc(titles[i.type]||i.type)}</div><div><label>Место и объект</label>${esc(i.zone_id||i.building_id||i.site_area_id)} · ${esc(i.asset_id||i.employee_id||i.sensor_id)}</div><div><label>Условие / обработка</label>${esc(labels[i.condition_state]||i.condition_state)} / ${esc(labels[i.disposition]||labels[i.status]||i.status)}</div><div><label>Ответственный</label>${esc(i.assigned_operator_id||"Не назначен")}, ревизия ${i.dispatch_revision}</div>`);
  const plan=i.response_plan||{},steps=Array.isArray(plan.steps)?plan.steps:[plan.steps||i.response_instruction||i.details?.response_instruction||"Принять случай, проверить свежие наблюдения и записать реакцию."];
  setMarkup("response-plan",`<strong>Связаться: ${esc(plan.contact||"Ответственная роль участка")}</strong><ol>${steps.map(step=>`<li>${esc(step)}</li>`).join("")}</ol>`);
  updateActions(i);
  const ids=i.evidence_event_ids||[];
  setMarkup("detail-history",`<strong>Исходные события:</strong> ${ids.length?ids.map(esc).join(", "):"Отсутствие сигнала подтверждается состоянием источника, без выдуманного события."}${i.disposition==="rejected_model_signal"?`<p class="disposition">Подозрение отклонено: ${esc(i.disposition_reason)} · ${esc(i.reviewed_at)}</p>`:""}${(i.history||[]).map(h=>`<p>${esc(h.created_at)} · ${esc(h.actor_operator_id||"Система")} · ${esc(actionNames[h.action]||h.action)} · ${esc(h.reason||"")}</p>`).join("")}`);
  if(!S.job)setMarkup("analysis",S.agentAvailable?"<p class='muted'>ИИ читает факты инструментами. Мониторинг работает и при выключенной локальной модели.</p>":"<p class='muted'>ИИ пока не подключён. Принимайте случаи и записывайте реакцию вручную; мониторинг продолжает работать.</p>");
}
async function renderDetails(){
  if(!S.selected)return;
  const id=S.selected,context=S.context,request=++S.detailRequest;
  const i=await api(`/incidents/${encodeURIComponent(id)}`);
  if(!sameSelection(id,context)||request!==S.detailRequest)return;
  applyDetails(i);await renderJob();
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
  setMarkup("analysis",`<strong>Анализ: ${esc(j.status)} ${stale?"· УСТАРЕЛ — данные изменились":""}</strong>${j.error?`<p>${esc(j.error.message||j.error)}</p>`:""}${j.result?`<p>${esc(j.result.summary)}</p><pre>${esc(JSON.stringify({facts:j.result.facts,hypotheses:j.result.hypotheses,recommendations:j.result.recommendations,tool_trace:j.result.tool_trace},null,2))}</pre>`:""}`);
}
function renderSensors(){setMarkup("sensors",S.sensors.map(s=>`<div class="sensor-row"><div><strong>${esc(s.sensor_id)}</strong><small> · ${esc(s.type)} ${esc(s.asset_id||s.building_id||"")}</small><br><small>${esc(age(s.last_received_at))}</small></div><span class="${esc(s.status)}">${esc(labels[s.status]||s.status)}</span></div>`).join(""));}
function beep(){if(!S.sound||!S.audio)return;const o=S.audio.createOscillator(),g=S.audio.createGain();o.frequency.value=660;g.gain.value=.045;o.connect(g);g.connect(S.audio.destination);o.start();o.stop(S.audio.currentTime+.22);}
async function notifications(initial=false){
  const context=S.context,operator=S.operator;
  let cursor=initial?0:S.cursor;
  while(true){
    const v=await api(`/dispatch-notifications?after_seq=${cursor}&limit=100`,"GET",undefined,operator);
    if(context!==S.context)return;
    const rows=v.notifications||v.items||[];
    const next=v.next_seq??(rows.at(-1)?.seq||cursor);
    if(!initial&&rows.length){const n=rows.at(-1);$("notifications").textContent=`Новое уведомление: ${n.kind} · ${n.incident_id}`;beep();}
    if(rows.length&&next<=cursor)throw Error("Сервер не продвигает курсор уведомлений.");
    cursor=next;S.cursor=cursor;
    if(!initial||rows.length<100)break;
  }
}
async function refresh(){
  if(S.busy||S.switching)return;
  const context=S.context,operator=S.operator;
  S.busy=true;
  try{
    const [assets,sensors,incidents,summary,health,demo,profiles]=await Promise.all([api("/assets","GET",undefined,operator),api("/sensors","GET",undefined,operator),api(`/incidents?scope=${$("all").checked?"all":"workstation"}`,"GET",undefined,operator),api("/dispatch-summary","GET",undefined,operator),api("/health","GET",undefined,operator),api("/demo/status","GET",undefined,operator),api("/operator-profiles","GET",undefined,operator)]);
    if(context!==S.context)return;
    S.assets=array(assets,"assets");S.sensors=array(sensors,"sensors");S.incidents=array(incidents,"incidents");S.summary=summary;S.profiles=array(profiles,"operator_profiles");
    const agentStatus=typeof health.agent==="object"?health.agent.status:health.agent;
    S.agentAvailable=agentStatus!=="unavailable"&&agentStatus!=="not_connected";
    if(summary.as_of){S.asOf=Date.parse(summary.as_of);S.serverOffset=S.asOf-Date.now();}
    $("connection").textContent="● Сервер на связи";$("connection").style.color="#5bceae";
    $("ml-status").textContent=`ML: ${typeof health.ml==="object"?health.ml.status:health.ml}`;
    const simulationAvailable=demo.available!==false&&demo.status!=="unavailable";
    $("sim-status").textContent=!simulationAvailable?"Симулятор пока не подключён":demo.running?`Сценарий: ${demo.scenario} · ${demo.events_sent||0} событий`:"Сценарий остановлен";
    $("start").disabled=!simulationAvailable;$("stop").disabled=!simulationAvailable||!demo.running;$("scenario").disabled=!simulationAvailable;
    drawAssets();renderSummary();renderIncidents();renderSensors();await notifications();
    if(context!==S.context)return;
    if(S.selected)await renderDetails();
    if(S.connectionError){$("error").hidden=true;S.connectionError=false;}
  }catch(e){
    if(context!==S.context)return;
    $("connection").textContent="● Связь с сервером потеряна";$("connection").style.color="#ff7272";S.connectionError=true;showError(e);
  }finally{S.busy=false;}
}
function sendPresence(operator,session,availability){
  // Serialise leases: an earlier ready request must finish before the old session goes away.
  const request=S.presenceChain.catch(()=>{}).then(()=>api("/operator-presence","POST",{session_id:session,availability},operator));
  S.presenceChain=request.catch(()=>{});return request;
}
async function presence(){if(S.switching)return;try{await sendPresence(S.operator,S.session,$("ready").checked?"ready":"away");}catch(e){showError(e);}}
async function switchOperator(operator){
  if(S.switching||operator===S.operator)return;
  const previous=S.operator;
  S.switching=true;$("operator").disabled=true;
  try{
    await sendPresence(previous,S.session,"away");
    S.operator=operator;S.session=crypto.randomUUID();S.context++;S.selected=null;S.detail=null;S.detailId=null;S.job=null;S.cursor=0;S.detailRequest++;
    $("details").textContent="Выберите происшествие";$("selected-id").textContent="";$("notifications").textContent="";$("incidents").textContent="Загрузка рабочего места…";
    await sendPresence(operator,S.session,$("ready").checked?"ready":"away");
    await notifications(true);
    focusSector();
    if(typeof window!=="undefined"){const url=new URL(window.location.href);url.searchParams.set("operator",operator);window.history.replaceState(null,"",url);}
  }catch(e){$("operator").value=S.operator;showError(e);}
  finally{S.switching=false;$("operator").disabled=false;}
  if(S.operator!==previous)await refresh();
}
async function boot(){
  try{
    if(typeof window!=="undefined"){S.operator=initialOperator(window.location.search);$("operator").value=S.operator;}
    S.site=await api("/site");S.profiles=array(await api("/operator-profiles"),"operator_profiles");initMap();await presence();await notifications(true);await refresh();
    setInterval(presence,3000);const loop=async()=>{await refresh();setTimeout(loop,1000);};setTimeout(loop,1000);
  }catch(e){showError(e);}
}
function bindControls(){
  $("operator").onchange=()=>switchOperator($("operator").value).catch(showError);
  $("ready").onchange=presence;$("all").onchange=refresh;$("history-toggle").onchange=renderIncidents;
  $("fit").onclick=()=>S.map.fitBounds([[0,0],[100,100]]);
  $("start").onclick=async()=>{try{await api("/demo/start","POST",{scenario:$("scenario").value});await refresh();}catch(e){showError(e);}};
  $("stop").onclick=async()=>{try{await api("/demo/stop","POST",{});await refresh();}catch(e){showError(e);}};
  $("sound").onclick=async()=>{S.sound=!S.sound;if(S.sound){S.audio||=new AudioContext();await S.audio.resume();beep();}$("sound").textContent=S.sound?"Выключить звук":"Включить звук";};
}
if(typeof window!=="undefined"){bindControls();boot();}
