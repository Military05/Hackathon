(function(root){
  "use strict";
  const escape=value=>String(value??"—").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const $=id=>document.getElementById(id);
  const names={"dispatcher-1":"Логистика и склады","dispatcher-2":"Производство","dispatcher-3":"КПП"};
  let pending=false,lastKey="",offset=0,total=0,version=0,refreshCallback=null,adminBusy=false,context={role:"dispatcher",operator:"dispatcher-1",tab:"objects"};
  function configure(value){context={...context,...value};}
  function gateVisible(){return (context.role==="admin"||context.operator==="dispatcher-3")&&context.tab==="people";}
  function filters(){const query=new URLSearchParams({limit:"12",offset:String(offset),sort:$("gate-sort")?.value||"newest"});for(const [id,key] of [["gate-search","q"],["gate-direction","direction"],["gate-permission","permission"],["gate-since","since"],["gate-until","until"]]){const value=$(id)?.value;if(value)query.set(key,key==="since"||key==="until"?new Date(value).toISOString():value);}return query;}
  function humanTime(value){return value?new Date(value).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"}):"—";}
  function renderGate(data){
    total=data.total||0;const rows=data.items||[];
    $("gate-count").textContent=`${total} записей`;
    const body=rows.length?rows.map(row=>`<tr><td><time title="${escape(row.event_time)}">${escape(humanTime(row.event_time))}</time></td><td>${escape(row.employee_name||row.employee_id)}<small>${escape(row.employee_id)}</small></td><td>${row.direction==="in"?"Вход":"Выход"}</td><td><span class="status-tag ${row.permission==="violation"?"violation":"allowed"}">${row.permission==="violation"?"Без допуска":"Допуск есть"}</span>${row.shift_id?`<small>Смена · ${escape(row.shift_id.slice(-8))}</small>`:""}</td></tr>`).join(""):"<tr><td colspan='4' class='muted'>Нет проходов по выбранному фильтру.</td></tr>";
    if(body!==lastKey){$("gate-rows").innerHTML=body;lastKey=body;}
    $("gate-prev").disabled=offset===0;$("gate-next").disabled=offset+12>=total;
    $("gate-page").textContent=total?`${offset+1}–${Math.min(offset+12,total)} из ${total}`:"Нет записей";$("gate-error").hidden=true;
    const occupancy=data.occupancy;$("gate-occupancy").textContent=occupancy?`По журналу внутри: ${occupancy.observed_inside_count??0}. ${occupancy.gate_source_online?"":"Датчик КПП не на связи."}`:"";
  }
  function renderShift(data){
    const shift=data?.shift||data;if(!shift?.shift_id){$("shift-summary").hidden=true;return;}$("shift-summary").hidden=false;
    const phases={checking:"Проверка людей на КПП",gate_checks:"Проверка людей на КПП",transport:"Допуск проверен · транспорт работает",running:"Транспорт работает",completed:"Смена завершена",stopped:"Сценарий остановлен",error:"Ошибка проверки КПП",interrupted:"Прервано при перезапуске"};
    $("shift-summary").innerHTML=`<strong>${escape(phases[shift.phase]||shift.phase||"Смена")}</strong><span>Проверено людей: ${shift.gate_count??shift.checked_count??0}/${shift.expected_gate_count??shift.expected_count??3}</span>${shift.error?`<small class="panel-error">${escape(shift.error)}</small>`:""}`;
  }
  function renderActivity(data){$("activity-summary").innerHTML=[['Принято',data.claimed],['Передано',data.transferred],['Завершено',data.closed],['Реакций',data.responses]].map(([label,value])=>`<span><b>${Number(value)||0}</b>${label}</span>`).join("");}
  async function update(request){
    if(pending)return;pending=true;const current=version;
    try{const jobs=[];if(gateVisible())jobs.push(request(`/checkpoint/journal?${filters()}`).then(data=>{if(current===version)renderGate(data);}),request("/shifts/current").then(data=>{if(current===version)renderShift(data);}));if(context.role!=="admin")jobs.push(request("/operator-activity").then(renderActivity));await Promise.all(jobs);}
    catch(error){if(gateVisible()){$("gate-error").hidden=false;$("gate-error").textContent=error.message;}}
    finally{pending=false;}
  }
  async function admin(request){
    if(adminBusy)return;adminBusy=true;
    try{
      const [data,audit]=await Promise.all([request("/admin/users"),request("/admin/audit")]);
      $("admin-users").innerHTML=(data.users||[]).map(user=>`<div class="admin-user" data-user-id="${escape(user.id)}"><div><strong>${escape(user.name)}</strong><small>${escape(user.username)} · ${user.role==="admin"?"Единственный администратор":escape({pending:"Ожидает подтверждения",active:"Активен",blocked:"Заблокирован"}[user.status]||user.status)}</small></div>${user.role==="admin"?"<span>Наблюдение и управление аккаунтами</span>":`<select aria-label="Сектор ${escape(user.username)}" class="admin-profile">${Object.entries(names).map(([id,name])=>`<option value="${id}"${user.operator_id===id?" selected":""}>${escape(name)}</option>`).join("")}</select><div><button data-account-action="${user.status==="pending"?"approve":"save"}">${user.status==="pending"?"Подтвердить":"Сохранить сектор"}</button><button data-account-action="${user.status==="blocked"?"unblock":"block"}">${user.status==="blocked"?"Разблокировать":"Блокировать"}</button></div>`}</div>`).join("");
      $("admin-users").querySelectorAll("[data-account-action]").forEach(button=>button.onclick=async()=>{
        const row=button.closest("[data-user-id]"),action=button.dataset.accountAction,id=encodeURIComponent(row.dataset.userId);row.querySelectorAll("button").forEach(b=>b.disabled=true);
        try{if(action==="unblock"){await request(`/admin/users/${id}`,"PATCH",{operator_id:row.querySelector("select").value});await request(`/admin/users/${id}/unblock`,"POST",{});}else if(action==="block")await request(`/admin/users/${id}/block`,"POST",{});else await request(`/admin/users/${id}`,"PATCH",{operator_id:row.querySelector("select").value,...(action==="approve"?{status:"active"}:{})});await admin(request);}
        catch(error){$("admin-message").textContent=error.message;}finally{row.querySelectorAll("button").forEach(b=>b.disabled=false);}
      });
      const auditNames={login_failed:"Ошибка входа",login_rejected:"Вход отклонён",login_success:"Успешный вход",logout:"Выход",account_created:"Создание аккаунта",account_updated:"Изменение аккаунта",account_blocked:"Блокировка",account_unblocked:"Разблокировка"};
      $("admin-audit").innerHTML=(audit.items||[]).slice(0,20).map(row=>`<p>${escape(humanTime(row.created_at||row.at))} · ${escape(auditNames[row.action]||row.action)} · ${escape(row.details?.username||row.username||row.target_id||row.actor_id||"—")}</p>`).join("")||"Нет записей";
      $("admin-message").textContent="Сохранение сектора не меняет блокировку. Для восстановления доступа нажмите «Разблокировать».";
    }catch(error){$("admin-message").textContent=error.message;}finally{adminBusy=false;}
  }
  function bind(request,onError){
    let timer;refreshCallback=()=>update(request);
    async function exportFile(path,name){try{await ProductAuth.download(path,`${name}_${new Date().toLocaleDateString("ru-RU")}.csv`);$("export-status").hidden=false;$("export-status").textContent="CSV подготовлен";}catch(error){onError(error);}}
    for(const id of ["gate-search","gate-direction","gate-permission","gate-since","gate-until","gate-sort"]){$(id).addEventListener(id==="gate-search"?"input":"change",()=>{clearTimeout(timer);offset=0;version++;timer=setTimeout(refreshCallback,250);});}
    $("gate-prev").onclick=()=>{offset=Math.max(0,offset-12);version++;refreshCallback();};$("gate-next").onclick=()=>{offset+=12;version++;refreshCallback();};
    $("gate-export").onclick=()=>{const query=filters();query.set("limit","200");query.set("offset","0");exportFile(`/checkpoint/export.csv?${query}`,"Журнал_проходов_КПП");};
    $("activity-export").onclick=()=>exportFile(context.role==="admin"?"/dispatch-history/export.csv?scope=all&limit=2000":"/dispatch-history/export.csv",context.role==="admin"?"Общий_журнал_действий_диспетчеров":"Журнал_действий_диспетчера");
    $("admin-open").onclick=()=>{$("admin-dialog").showModal();admin(request);};$("admin-close").onclick=()=>$("admin-dialog").close();$("admin-refresh").onclick=()=>admin(request);
  }
  root.OperationsView={bind,update,configure,renderGate,renderShift,renderActivity};
})(globalThis);
