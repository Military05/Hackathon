(function(root){
"use strict";
let ready=false, facility=null, facilityTimer=null;
const $=id=>document.getElementById(id);
function refreshFactorySize(){
 if(typeof S!=="undefined"&&S.map)requestAnimationFrame(()=>{S.map.invalidateSize({pan:false});S.mapRenderer?.fitAll({animate:false});});
}
function switchView(mode){
 const network=mode==="logistics";
 $("logistics-view").hidden=!network;document.querySelector(".dashboard").hidden=network;
 $("network-tab").setAttribute("aria-pressed",String(network));$("factory-tab").setAttribute("aria-pressed",String(!network));
 const params=new URLSearchParams(location.search);params.set("view",mode);history.replaceState(null,"",location.pathname+"?"+params);
 if(network){root.LogisticsView?.activate();clearInterval(facilityTimer);facilityTimer=null;}
 else{root.LogisticsView?.deactivate();refreshFactorySize();if(facility){updateFacility();clearInterval(facilityTimer);facilityTimer=setInterval(updateFacility,5000);}}
}
async function updateFacility(){
 if(!facility||$("product-shell").hidden)return;
 try{
  const state=await root.ProductAuth.request("/logistics/state");
  const current=state.facilities.find(item=>item.id===facility.id);if(!current)return;facility=current;
  $("facility-context-name").textContent=current.name;
  $("facility-context-summary").textContent=(current.kind==="factory"?"Завод":"Склад")+" · запас "+Number(current.stock||0).toLocaleString("ru-RU")+" ед. · доков "+current.docks+" · очередь "+(Array.isArray(current.queue)?current.queue.length:current.queue||0);
  const trips=state.trips.filter(item=>item.origin_id===current.id||item.destination_id===current.id);
  const labels={gate_check:"Проверка КПП",queued:"В очереди",loading:"Погрузка",in_transit:"В пути",unloading:"Разгрузка",delivered:"Доставлено",blocked:"Рейс заблокирован",waiting_route:"Ожидание маршрута"};
  const box=$("facility-context-trips");box.replaceChildren();
  for(const trip of trips.slice(0,8)){const row=document.createElement("span");row.className="facility-context-trip";row.textContent=trip.vehicle_id+" · "+(labels[trip.status]||trip.status)+" · "+(trip.cargo?.name||"Груз");box.append(row);}
 }catch(error){$("facility-context-summary").textContent=error.message;}
}
function init(){
 if(ready||!root.LogisticsView||!$("logistics-view"))return;ready=true;
 root.LogisticsView.init();
 $("network-tab").onclick=()=>switchView("logistics");$("factory-tab").onclick=()=>switchView("factory");$("facility-back").onclick=()=>switchView("logistics");
 root.addEventListener("logistics:facility",event=>{
  facility=event.detail;$("facility-context").hidden=false;$("facility-context-name").textContent=facility.name;
  document.querySelector(".dashboard .page-heading h1").textContent=facility.name;
  $("facility-template-note").textContent=facility.kind==="factory"?"План завода и действующие датчики":"Типовой план площадки · складские операции относятся к выбранному складу; внутренние датчики — общая демонстрация";
  switchView("factory");
 });
 new MutationObserver(()=>{if($("product-shell").hidden){root.LogisticsView.deactivate();clearInterval(facilityTimer);}}).observe($("product-shell"),{attributes:true,attributeFilter:["hidden"]});
 switchView(new URLSearchParams(location.search).get("view")||"logistics");
}
root.addEventListener("contour:ready",init);root.ContourNetwork={init,switchView};
if(root.ProductAuth?.getSession()?.user&&typeof S!=="undefined"&&S.mapRenderer)init();
})(globalThis);
