"use strict";
(function(root,factory){const api=factory();if(typeof module==="object"&&module.exports)module.exports=api;else root.SensorDiagnostics=api;})(typeof globalThis!=="undefined"?globalThis:this,function(){
  const esc=value=>String(value??"—").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const statusNames={online:"На связи",offline:"Нет связи",unknown:"Неизвестно"};
  const typeNames={position:"Позиционный датчик",access:"Датчик прохода",heartbeat:"Контроль связи"};
  function secondsSince(value,now){const parsed=Date.parse(value);return value&&Number.isFinite(parsed)?Math.max(0,(now-parsed)/1000):null;}
  function classify(sensor,now=Date.now(),positionStaleSeconds=5){
    const age=secondsSince(sensor.last_received_at,now);
    const threshold=Number(sensor.threshold_seconds)||5;
    const snapshotAge=secondsSince(sensor.as_of,now),snapshotStale=snapshotAge!==null&&snapshotAge>=Math.max(threshold,5);
    const communication=snapshotStale?"unknown":["online","offline","unknown"].includes(sensor.status)?sensor.status:age===null?"unknown":age>=threshold?"offline":"online";
    const measurementAge=secondsSince(sensor.last_measurement_at,now);
    const freshness=sensor.type!=="position"?"event":measurementAge===null?"unknown":measurementAge>=positionStaleSeconds?"stale":"fresh";
    const cause=snapshotStale?"Нет свежего ответа сервера; состояние источника неизвестно":communication==="offline"?(age===null?"Первый пакет не получен":"Нет новых пакетов от источника"):communication==="unknown"?"Источник ещё не передал первый пакет":freshness==="stale"?"Связь есть; измерение позиции устарело":freshness==="unknown"?"Связь есть; координаты ещё не получены":sensor.type==="position"?"Связь и измерение актуальны":"Связь есть; измерения поступают при событиях";
    return {id:sensor.sensor_id||sensor.id,communication,age,threshold,measurementAge,freshness,cause,snapshotStale,attention:communication!=="online"||["stale","unknown"].includes(freshness)};
  }
  function summarize(sensors,now,positionStaleSeconds){
    const rows=sensors.map(s=>classify(s,now,positionStaleSeconds));
    return {total:rows.length,online:rows.filter(s=>s.communication==="online").length,offline:rows.filter(s=>s.communication==="offline").length,unknown:rows.filter(s=>s.communication==="unknown").length,stale:rows.filter(s=>s.freshness==="stale").length,attention:rows.filter(s=>s.attention).length,rows};
  }
  function create(options={}){
    const doc=options.document===undefined?(typeof document==="undefined"?null:document):options.document;
    const host=options.element||(doc&&doc.getElementById(options.elementId||"sensors"));
    const now=options.serverTime||options.clock||Date.now;
    const staleSeconds=Number(options.positionStaleSeconds)||5;
    const schedule=options.setInterval||setInterval,cancel=options.clearInterval||clearInterval;
    let sensors=[],history=[],selected=null,automatic=true,pending=null,destroyed=false,lastInspection=null,lastError=null,seen=false;
    const stateSignatures=new Map();
    const formatTime=value=>value?new Date(value).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"}):"Ещё не получен";
    const ageText=value=>value===null?"Нет пакета":`${Math.round(value)} с назад`;
    let list,summaryEl,inspectionEl,errorEl,checkButton,autoInput,dialog,dialogBody;
    function record(kind,message,sensorId=null){history.push({at:now(),kind,message,sensor_id:sensorId});if(history.length>20)history.shift();}
    function readSensors(value){const rows=Array.isArray(value)?value:value?.sensors;if(!Array.isArray(rows))throw Error("Сервер вернул некорректный список датчиков");return rows;}
    function render(){
      if(!host||destroyed)return;
      const health=summarize(sensors,now(),staleSeconds);
      const text=`${health.online} на связи · ${health.offline} без связи · ${health.unknown} ожидают сигнал${health.stale?` · ${health.stale} с устаревшей позицией`:""}`;
      if(summaryEl.textContent!==text)summaryEl.textContent=text;
      const inspectionText=lastInspection?`${lastInspection.mode==="manual"?"Ручная":"Автоматическая"} проверка: ${formatTime(lastInspection.at)} · ${lastInspection.summary.attention?`${lastInspection.summary.attention} требуют внимания`:"без замечаний по полученным данным"}`:"Проверка по последнему пакету · ожидание данных";
      inspectionEl.textContent=inspectionText;
      errorEl.hidden=!lastError;errorEl.textContent=lastError||"";
      checkButton.disabled=!!pending;checkButton.classList.toggle("is-loading",!!pending);checkButton.textContent=pending?"Перечитываем данные…":"Проверить сейчас";
      const dialogCheck=host.querySelector(".diagnostic-dialog-check");
      dialogCheck.disabled=!!pending;dialogCheck.classList.toggle("is-loading",!!pending);
      const activeId=doc?.activeElement?.getAttribute?.("data-sensor-id");
      const markup=sensors.length?sensors.map(s=>{const h=classify(s,now(),staleSeconds);return `<button class="sensor-row ${h.attention?"sensor-attention":""}" data-sensor-id="${esc(h.id)}"><span class="diagnostic-sensor-icon" aria-hidden="true"><span></span></span><span class="sensor-description"><strong>${esc(h.id)}</strong><small>${esc(typeNames[s.type]||s.type)} · ${esc(s.asset_id||s.building_id||"Источник предприятия")}</small><small>${esc(ageText(h.age))}${s.type==="position"?` · ${h.freshness==="fresh"?"позиция актуальна":h.freshness==="stale"?"позиция устарела":"нет координат"}`:""}</small></span><span class="sensor-state ${h.communication}">${statusNames[h.communication]}</span><span class="sensor-row-arrow" aria-hidden="true">›</span></button>`;}).join(""):"<p class='sensor-empty'>Источники ещё не загружены.</p>";
      if(list.innerHTML!==markup){list.innerHTML=markup;if(activeId){const button=Array.from(list.querySelectorAll("[data-sensor-id]")).find(el=>el.dataset.sensorId===activeId);button?.focus({preventScroll:true});}}
      if(selected&&dialog.open)renderDialog();
    }
    function renderDialog(){
      const sensor=sensors.find(s=>(s.sensor_id||s.id)===selected);
      if(!sensor){dialogBody.innerHTML="<p>Источник отсутствует в текущем наборе данных.</p>";return;}
      const h=classify(sensor,now(),staleSeconds);
      dialogBody.innerHTML=`<div class="diagnostic-heading"><span class="diagnostic-sensor-icon" aria-hidden="true"><span></span></span><div><h2>${esc(h.id)}</h2><p>${esc(typeNames[sensor.type]||sensor.type)} · ${esc(sensor.asset_id||sensor.building_id||"Предприятие")}</p></div><span class="sensor-state ${h.communication}">${statusNames[h.communication]}</span></div><p class="diagnostic-cause">${esc(h.cause)}</p><dl class="diagnostic-facts"><div><dt>Последний принятый пакет</dt><dd>${formatTime(sensor.last_received_at)} · ${ageText(h.age)}</dd></div><div><dt>Последний heartbeat</dt><dd>${formatTime(sensor.last_heartbeat_at)}</dd></div><div><dt>Последнее измерение</dt><dd>${formatTime(sensor.last_measurement_at)}${h.measurementAge!==null?` · ${ageText(h.measurementAge)}`:""}</dd></div><div><dt>Порог потери связи</dt><dd>${h.threshold} с</dd></div>${sensor.type==="position"?`<div><dt>Порог свежести позиции</dt><dd>${staleSeconds} с</dd></div>`:""}<div><dt>Начало ожидания сигнала</dt><dd>${formatTime(sensor.first_expected_at)}</dd></div></dl><p class="diagnostic-disclaimer">Проверяем связь и свежесть уже полученных данных. Это не физическая самодиагностика прибора. Heartbeat не подтверждает исправность измерений.${sensor.type==="access"?" Отсутствие проходов само по себе нормально.":""}</p>`;
    }
    function update(value){
      if(destroyed)return;
      sensors=readSensors(value).map(s=>({...s}));
      for(const sensor of sensors){const h=classify(sensor,now(),staleSeconds),signature=`${h.communication}:${h.freshness}`;if(seen&&stateSignatures.has(h.id)&&stateSignatures.get(h.id)!==signature)record("transition",h.cause,h.id);stateSignatures.set(h.id,signature);}
      for(const id of stateSignatures.keys())if(!sensors.some(s=>(s.sensor_id||s.id)===id))stateSignatures.delete(id);
      if(!seen&&sensors.length){seen=true;lastInspection={at:now(),mode:"auto",summary:summarize(sensors,now(),staleSeconds)};record("initial","Получен первый снимок состояния");}
      render();
    }
    function inspect(mode){
      const summary=summarize(sensors,now(),staleSeconds);
      if(!summary.total){render();return null;}
      lastInspection={at:now(),mode,summary};
      record(mode,`${mode==="manual"?"Ручная":"Автоматическая"} проверка: ${summary.online}/${summary.total} на связи; требуют внимания: ${summary.attention}`);
      render();return summary;
    }
    function automaticCheck(){
      if(destroyed||!automatic||pending)return null;
      if(options.getSensors)update(options.getSensors());
      return inspect("auto");
    }
    function manualCheck(){
      if(destroyed)return Promise.resolve(null);
      if(pending)return pending;
      lastError=null;
      pending=Promise.resolve().then(async()=>{
        const value=options.requestCheck?await options.requestCheck():options.getSensors?options.getSensors():sensors;
        if(destroyed)return null;
        update(value);return inspect("manual");
      }).catch(error=>{if(!destroyed){lastError=`Проверка не выполнена: ${error?.message||String(error)}`;record("error",lastError);render();}return null;}).finally(()=>{pending=null;render();});
      render();return pending;
    }
    function selectSensor(id,{notify=false}={}){
      if(destroyed)return;
      selected=id;
      if(dialog){renderDialog();if(!dialog.open)dialog.showModal();}
      if(notify&&options.onSelectSensor)options.onSelectSensor(id);
    }
    if(host){
      host.innerHTML=`<div class="diagnostic-toolbar"><div class="diagnostic-options"><label><input class="diagnostic-auto" type="checkbox" checked>Автопроверка · каждые 30 с</label></div><button class="diagnostic-check">Проверить сейчас</button></div><p class="diagnostic-summary"></p><p class="diagnostic-inspection" role="status"></p><p class="diagnostic-error" role="alert" hidden></p><div class="diagnostic-list"></div><dialog class="diagnostic-dialog"><div class="diagnostic-dialog-header"><span>Диагностика источника</span><button class="diagnostic-close" aria-label="Закрыть диагностику">✕</button></div><div class="diagnostic-dialog-body"></div><div class="diagnostic-dialog-actions"><button class="diagnostic-dialog-check">Проверить сейчас</button><button class="diagnostic-map">Выбрать на карте</button></div></dialog>`;
      list=host.querySelector(".diagnostic-list");summaryEl=host.querySelector(".diagnostic-summary");inspectionEl=host.querySelector(".diagnostic-inspection");errorEl=host.querySelector(".diagnostic-error");checkButton=host.querySelector(".diagnostic-check");autoInput=host.querySelector(".diagnostic-auto");dialog=host.querySelector("dialog");dialogBody=host.querySelector(".diagnostic-dialog-body");
      checkButton.addEventListener("click",manualCheck);autoInput.addEventListener("change",()=>{automatic=autoInput.checked;});
      list.addEventListener("click",event=>{const row=event.target.closest("[data-sensor-id]");if(row)selectSensor(row.dataset.sensorId);});
      host.querySelector(".diagnostic-close").addEventListener("click",()=>dialog.close());
      host.querySelector(".diagnostic-dialog-check").addEventListener("click",manualCheck);
      host.querySelector(".diagnostic-map").addEventListener("click",()=>{dialog.close();if(options.onSelectSensor)options.onSelectSensor(selected);});
      dialog.addEventListener("click",event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close();}});
      render();
    }
    const timer=schedule(automaticCheck,30000);
    return {update,manualCheck,automaticCheck,selectSensor,inspectSensor:selectSensor,setAutomatic:value=>{automatic=!!value;if(autoInput)autoInput.checked=automatic;},getState:()=>({sensors:sensors.map(s=>({...s})),history:history.map(h=>({...h})),lastInspection,lastError,automatic,pending:!!pending}),destroy(){destroyed=true;cancel(timer);dialog?.close();}};
  }
  return {create,classify,summarize};
});
