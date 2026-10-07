(function(root){
  "use strict";
  const escape=value=>String(value??"—").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const $=id=>document.getElementById(id);
  const names={"dispatcher-1":"Логистика и склады","dispatcher-2":"Производство","dispatcher-3":"КПП"};
  let pending=false,lastKey="",offset=0,total=0,version=0,refreshCallback=null,adminBusy=false;
  function filters(){const query=new URLSearchParams({limit:"12",offset:String(offset)});for(const [id,key] of [["gate-search","q"],["gate-direction","direction"],["gate-permission","permission"],["gate-since","since"],["gate-until","until"]]){const value=$(id)?.value;if(value)query.set(key,key==="since"||key==="until"?new Date(value).toISOString():value);}return query;}
  function humanTime(value){return value?new Date(value).toLocaleTimeString("ru-RU"):"—";}
  function renderGate(data){
    total=data.total||0;
    const rows=data.items||[];
    $("gate-count").textContent=`${total} записей · ${humanTime(data.as_of)}`;
    const body=rows.length?rows.map(row=>`<tr><td><time title="${escape(row.event_time)}">${escape(humanTime(row.event_time))}</time></td><td>${escape(row.employee_name||row.employee_id)}<small>${escape(row.employee_id)}</small></td><td>${row.direction==="in"?"Вход":"Выход"}</td><td><span class="status-tag ${row.permission==="violation"?"violation":"allowed"}">${row.permission==="violation"?"Без допуска":"Допуск есть"}</span>${row.shift_id?`<small title="${escape(row.shift_id)}">Смена · ${escape(row.shift_id.slice(-8))}</small>`:""}</td></tr>`).join(""):"<tr><td colspan='4' class='muted'>Подтверждённых проходов по фильтру пока нет.</td></tr>";
    if(body!==lastKey){$("gate-rows").innerHTML=body;lastKey=body;}
    $("gate-prev").disabled=offset===0;$("gate-next").disabled=offset+12>=total;
    $("gate-page").textContent=total?`${offset+1}–${Math.min(offset+12,total)} из ${total}`:"Нет записей";
    $("gate-error").hidden=true;
    const occupancy=data.occupancy;$("gate-occupancy").textContent=occupancy?`По журналу внутри: ${occupancy.observed_inside_count??0}. ${occupancy.gate_source_online?"":"Датчик КПП не на связи. "}${occupancy.note||"Исходное присутствие неизвестно."}`:"Исходное присутствие неизвестно.";
  }
  function renderShift(data){
    const shift=data?.shift||data;
    if(!shift?.shift_id){$("shift-summary").textContent="Начало смены: выберите сценарий, чтобы связать проверку людей и движение транспорта.";return;}
    const phases={checking:"Проверка людей на КПП",gate_checks:"Проверка людей на КПП",transport:"Допуск проверен · транспорт работает",running:"Транспорт работает",completed:"Смена завершена",stopped:"Сценарий остановлен"};
    const checked=shift.gate_count??shift.checked_count??0;
    const expected=shift.expected_gate_count??shift.expected_count??3;
    const vehicles=shift.transport_events_count??0;
    $("shift-summary").innerHTML=`<strong>${escape(phases[shift.phase]||shift.phase||"Смена")}</strong><span>Люди: ${checked}/${expected} · событий транспорта: ${vehicles}</span><small>Один эпизод ${escape(shift.shift_id.slice(-8))}${shift.transport_asset_ids?.length?` · ${escape(shift.transport_asset_ids.join(", "))}`:""}; сигналы датчиков сохраняются во время проверки.</small>`;
  }
  function renderActivity(data){$("activity-summary").innerHTML=[['Принято',data.claimed],['Передано',data.transferred],['Завершено',data.closed],['Реакций',data.responses]].map(([label,value])=>`<span><b>${Number(value)||0}</b>${label}</span>`).join("");}
  async function update(request){
    if(pending)return;pending=true;const context=version;
    try{const [journal,shift,activity]=await Promise.all([request(`/checkpoint/journal?${filters()}`),request("/shifts/current"),request("/operator-activity")]);if(context!==version)return;renderGate(journal);renderShift(shift);renderActivity(activity);}
    catch(error){$("gate-error").hidden=false;$("gate-error").textContent=error.message;}
    finally{pending=false;}
  }
  async function admin(request){
    if(adminBusy)return;adminBusy=true;
    try{
      const [data,audit]=await Promise.all([request("/admin/users"),request("/admin/audit")]);
      $("admin-users").innerHTML=(data.users||[]).map(user=>`<div class="admin-user" data-user-id="${escape(user.id)}"><div><strong>${escape(user.name)}</strong><small>${escape(user.username)} · ${user.role==="admin"?"Администратор":"Диспетчер"} · ${escape({pending:"Ожидает подтверждения",active:"Активен",blocked:"Заблокирован"}[user.status]||user.status)}</small></div>${user.role==="admin"?"<span>Управляется на сервере</span>":`<select aria-label="Рабочее место ${escape(user.username)}" class="admin-profile">${Object.entries(names).map(([id,name])=>`<option value="${id}"${user.operator_id===id?" selected":""}>${escape(name)}</option>`).join("")}</select><div><button data-status="active">${user.status==="pending"?"Подтвердить":"Сохранить"}</button><button data-status="blocked">Блокировать</button></div>`}</div>`).join("");
      $("admin-users").querySelectorAll("[data-status]").forEach(button=>button.onclick=async()=>{button.disabled=true;const row=button.closest("[data-user-id]");try{await request(`/admin/users/${encodeURIComponent(row.dataset.userId)}`,"PATCH",{status:button.dataset.status,operator_id:row.querySelector("select").value});await admin(request);}catch(error){$("admin-message").textContent=error.message;}finally{button.disabled=false;}});
      const auditNames={login_failed:"Ошибка входа",login_rejected:"Вход отклонён",login_success:"Успешный вход",logout:"Выход",account_created:"Создание аккаунта",account_updated:"Изменение аккаунта"};
      $("admin-audit").innerHTML=(audit.items||[]).slice(0,20).map(row=>`<p>${escape(humanTime(row.created_at||row.at))} · ${escape(auditNames[row.action]||row.action)} · ${escape(row.details?.username||row.username||row.target_id||row.actor_id||"—")}</p>`).join("")||"Нет записей";
      $("admin-message").textContent="Роль администратора назначается только на сервере. Заявка сама не даёт доступа.";
    }catch(error){$("admin-message").textContent=error.message;}finally{adminBusy=false;}
  }
  function bind(request,onError){
    let timer;refreshCallback=()=>update(request);
    for(const id of ["gate-search","gate-direction","gate-permission","gate-since","gate-until"]){$(id).addEventListener(id==="gate-search"?"input":"change",()=>{clearTimeout(timer);offset=0;version++;timer=setTimeout(refreshCallback,250);});}
    $("gate-prev").onclick=()=>{offset=Math.max(0,offset-12);version++;refreshCallback();};
    $("gate-next").onclick=()=>{offset+=12;version++;refreshCallback();};
    $("gate-export").onclick=()=>{const query=filters();query.set("limit","200");query.set("offset","0");ProductAuth.download(`/checkpoint/export.csv?${query}`,"checkpoint.csv").catch(onError);};
    $("activity-export").onclick=()=>ProductAuth.download("/dispatch-history/export.csv","dispatcher-actions.csv").catch(onError);
    $("admin-open").onclick=()=>{$("admin-dialog").showModal();admin(request);};
    $("admin-close").onclick=()=>$("admin-dialog").close();
    $("admin-refresh").onclick=()=>admin(request);
  }
  root.OperationsView={bind,update,renderGate,renderShift,renderActivity};
})(globalThis);
