/* Local enterprise plan. Geometry is shared with server and simulator via site.json. */
(function(root,factory){
  "use strict";
  const api=factory();
  if(typeof module==="object"&&module.exports)module.exports=api;
  if(root)root.EnterpriseMap=api;
})(typeof globalThis!=="undefined"?globalThis:this,function(){
  "use strict";
  const escape=x=>String(x??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const point=p=>Array.isArray(p)?{x:p[0],y:p[1]}:p;
  const validPoint=p=>p&&Number.isFinite(p.x)&&Number.isFinite(p.y);
  const workable=i=>i.status!=="closed"&&i.disposition!=="rejected_model_signal";
  const incidentAssets=i=>[i?.asset_id,i?.other_asset_id,i?.details?.other_asset_id].filter(Boolean);
  const redZone=zone=>zone.kind==="forbidden"||zone.kind==="restricted";
  const vehicleTypeNames={forklift:"погрузчикам",service_vehicle:"служебному транспорту"};
  const sensorStatusNames={online:"На связи",offline:"Нет связи",unknown:"Связь ещё не подтверждена"};
  const sensorTypeNames={position:"Датчик положения",access:"Датчик прохода",heartbeat:"Контроль связи"};
  function isStale(asset,serverOffset=0,thresholdSeconds=5,now=Date.now()){
    const stamp=Date.parse(asset?.last_seen);
    return !Number.isFinite(stamp)||now+serverOffset-stamp>=thresholdSeconds*1000;
  }
  function roadWidthPixels(planWidth,pixelsPerUnit){return Math.max(1,Number(planWidth||5)*pixelsPerUnit);}
  function interpolatePoint(from,to,fraction){const t=Math.max(0,Math.min(1,fraction));return {x:from.x+(to.x-from.x)*t,y:from.y+(to.y-from.y)*t};}
  const validRectangle=r=>r&&[r.x,r.y,r.width,r.height].every(Number.isFinite)&&r.width>0&&r.height>0;
  function sectorRectangles(site,sectorId){
    const sector=(site.sectors||[]).find(s=>s.id===sectorId);
    if(!sector)return [];
    if(validRectangle(sector.focus_bounds))return [sector.focus_bounds];
    return (site.site_areas||[]).filter(a=>a.responsible_sector_id===sectorId&&validRectangle(a.rectangle)).map(a=>a.rectangle);
  }
  function zoneStyle(zone){
    return redZone(zone)
      ?{color:"#f37979",fillColor:"#b84b4b",fillOpacity:.23,weight:1.5,className:"enterprise-zone-forbidden"}
      :{color:"#e8b166",fillColor:"#c78b3a",fillOpacity:.16,weight:1.5,dashArray:"5 4",className:"enterprise-zone-permit"};
  }
  function zonePolicyText(zone){
    if(zone.kind==="forbidden")return "Въезд запрещён всем транспортным средствам";
    if(zone.kind==="restricted")return `Въезд запрещён ${(zone.restricted_vehicle_types||[]).map(type=>vehicleTypeNames[type]||type).join(", ")}. Другим типам въезд в этот участок разрешён`;
    return "Въезд только по индивидуальному допуску";
  }
  function signalTime(stamp){const parsed=Date.parse(stamp);return Number.isFinite(parsed)?new Date(parsed).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit",hour12:false}):"—";}
  let instanceSequence=0;
  function estimatedLabelWidth(text,fontSize){
    return [...String(text)].reduce((total,c)=>total+(/[ilI1 .,:]/.test(c)?.3:/[MWШЩЖ]/.test(c)?.85:.61)*fontSize,0);
  }
  function linesFor(text,maxWidth,fontSize){
    let width=0,lines=1;
    for(const word of String(text).split(/\s+/)){
      const next=estimatedLabelWidth(word,fontSize);
      if(next>maxWidth)return Infinity;
      if(width&&width+next+fontSize*.3>maxWidth){lines++;width=next;}
      else width+=next+(width?fontSize*.3:0);
    }
    return lines;
  }
  function labelMetrics(building,width,height){
    const innerWidth=Math.max(0,width-8),innerHeight=Math.max(0,height-6);
    const preferred=Math.max(9,Math.min(12,Math.floor(innerHeight/2.4)));
    const candidates=width<50||height<28?[building.short_name]:[building.name,building.short_name];
    for(const label of candidates.filter(Boolean))for(let fontSize=preferred;fontSize>=9;fontSize--){
      const maxLines=Math.max(1,Math.min(2,Math.floor(innerHeight/(fontSize*1.15))));
      if(linesFor(label,innerWidth,fontSize)<=maxLines)return {label,fontSize,width:Math.max(0,width),height:Math.max(0,height),maxLines};
    }
    return {label:building.id||"",fontSize:9,width:Math.max(0,width),height:Math.max(0,height),maxLines:1};
  }
  function buildingControl(site,building){
    const area=(site.site_areas||[]).find(a=>a.id===building.site_area_id),sectorId=area?.responsible_sector_id;
    const profile=sectorId&&(site.operator_profiles||[]).find(p=>p.sector_id===sectorId);
    const operatorId=profile?.operator_id||profile?.id,number=/^dispatcher-(\d+)$/.exec(operatorId||"")?.[1];
    const label=number?`Диспетчер ${number}`:profile?.name||"Не назначен";
    return {operatorId,sectorId,label,compactLabel:number?`Д${number}`:"—",shortLabel:number?`Дисп. ${number}`:label};
  }
  const operatorColors={"dispatcher-1":"#79baff","dispatcher-2":"#75d4a0","dispatcher-3":"#bca3ed"};
  function create({elementId="map",site,operator=null,role="dispatcher",onAsset=()=>{},onSensor=()=>{},onBuilding=()=>{}}){
    const Leaflet=globalThis.L;
    if(!Leaflet)throw Error("Локальная библиотека карты ещё не загружена");
    if(!site)throw Error("Нет геометрии предприятия");
    const minimum=site.coordinate_system?.min??0,maximum=site.coordinate_system?.max??100;
    const xy=(x,y)=>[maximum-y,x];
    const rectangleBounds=r=>[xy(r.x,r.y+r.height),xy(r.x+r.width,r.y)];
    const map=Leaflet.map(elementId,{crs:Leaflet.CRS.Simple,minZoom:0,maxZoom:5,zoomSnap:.1,attributionControl:false});
    const pane=(name,z)=>{map.createPane(name).style.zIndex=z;};
    pane("enterpriseGrid",220);pane("enterpriseSectors",260);pane("enterpriseRoads",300);pane("enterprisePedestrians",310);pane("enterpriseRoutes",320);pane("enterpriseZones",330);pane("enterpriseObjects",360);pane("enterpriseLabels",420);pane("enterpriseHighlight",450);pane("enterpriseSensors",620);
    const roads=[],pedestrians=[],personalRoutes=new Map(),buildings=new Map(),zones=new Map(),assets=new Map(),sensors=new Map(),sensorDefinitions=new Map((site.sensors||[]).map(s=>[s.id||s.sensor_id,s]));
    const grid=Leaflet.layerGroup().addTo(map),highlightLayer=Leaflet.layerGroup().addTo(map);
    let state={assets:[],sensors:[],incidents:[],serverOffset:0},selection=null,selectedAsset=null,followedAsset=null,routesVisible=true,gridSignature="",destroyed=false,viewMode="all",focusedSectorId=null,settingView=false,animationFrame=null,lastPaint=0;
    let currentOperator=operator,currentRole=role;
    const siteAssets=new Map((site.assets||[]).map(a=>[a.id,a]));
    function areaOperator(areaId){const area=(site.site_areas||[]).find(a=>a.id===areaId);return (site.operator_profiles||[]).find(p=>p.sector_id===area?.responsible_sector_id)?.id||"dispatcher-3";}
    function assetOperator(assetId){const definition=siteAssets.get(assetId);const building=(site.buildings||[]).find(b=>b.id===definition?.destination);return building?buildingControl(site,building).operatorId:areaOperator(definition?.site_area_id);}
    function sensorOperator(definition){return definition.asset_id?assetOperator(definition.asset_id):areaOperator(definition.site_area_id);}
    const visibleOwner=id=>currentRole==="admin"||currentOperator==="admin"||!currentOperator||currentOperator===id;
    const ownershipColor=id=>visibleOwner(id)?operatorColors[id]||"#bca3ed":"#87929d";
    function buildingStyle(record){const color=ownershipColor(record.control.operatorId);return {color,fillColor:color,fillOpacity:visibleOwner(record.control.operatorId) ? 0.31 : 0.16};}
    function setOperator(id,nextRole="dispatcher"){currentOperator=id;currentRole=nextRole;for(const record of buildings.values())record.layer.setStyle(buildingStyle(record));update(state);}
    const hatchId=`enterprise-forbidden-hatch-${++instanceSequence}`;
    const motionClock=()=>typeof performance!=="undefined"?performance.now():Date.now();
    const reducedMotion=typeof matchMedia!=="undefined"&&matchMedia("(prefers-reduced-motion: reduce)").matches;
    const frame=[xy(minimum,maximum),xy(maximum,minimum)];
    // Wide map panels need room outside the square plan to center the western/eastern sector.
    map.setMaxBounds?.([xy(minimum-100,maximum+100),xy(maximum+100,minimum-100)]);
    for(const sector of site.sectors||[]){
      for(const r of sector.map_regions||[])if(validRectangle(r))Leaflet.rectangle(rectangleBounds(r),{pane:"enterpriseSectors",color:"#aebbbb",opacity:.55,weight:1,dashArray:"3 5",fill:false,interactive:false,className:"enterprise-sector-outline"}).addTo(map);
      if(validPoint(sector.label_position))Leaflet.marker(xy(sector.label_position.x,sector.label_position.y),{pane:"enterpriseLabels",interactive:false,keyboard:false,icon:Leaflet.divIcon({className:"enterprise-sector-label-marker",html:`<span class="enterprise-sector-label">${escape(sector.name)}</span>`,iconSize:[150,15],iconAnchor:[75,7.5]})}).addTo(map);
    }
    for(const road of site.roads||[]){
      const source=road.points||road.path||[];
      const points=source.map(point).filter(validPoint).map(p=>xy(p.x,p.y));
      if(points.length<2)continue;
      const style={pane:"enterpriseRoads",interactive:false,lineCap:road.entrance_building_id?"butt":"round",lineJoin:"round",smoothFactor:0};
      const edge=Leaflet.polyline(points,{...style,color:"#425462",opacity:1}).addTo(map);
      roads.push({road,edge,points,style});
    }
    // All road edges precede all road fills: a junction remains one continuous surface.
    for(const road of roads)road.fill=Leaflet.polyline(road.points,{...road.style,color:"#293f4d",opacity:1}).addTo(map);
    for(const path of site.pedestrian_paths||[]){
      const points=(path.points||[]).map(point).filter(validPoint).map(p=>xy(p.x,p.y));if(points.length<2)continue;
      const layer=Leaflet.polyline(points,{pane:"enterprisePedestrians",color:"#c6d2bc",opacity:.72,weight:1.5,dashArray:"3 5",lineCap:"butt",interactive:false,smoothFactor:0,className:"enterprise-pedestrian-path"}).addTo(map);
      pedestrians.push({path,layer});
    }
    const routeColors=["#d7cc8b","#baaff1","#72ccb4"];
    for(const [id,route] of Object.entries(site.safety_routes||site.demo_routes||site.routes||{})){
      const points=route.map(point).filter(validPoint).map(p=>xy(p.x,p.y));if(points.length<2)continue;
      const color=routeColors[personalRoutes.size%routeColors.length];
      const layer=Leaflet.polyline(points,{pane:"enterpriseRoutes",color,opacity:.23,weight:1.5,dashArray:"5 7",lineCap:"round",interactive:false,smoothFactor:0,className:"enterprise-personal-route"}).addTo(map);
      const destinations=(site.demo_route_destinations?.[id]||[]).map(destination=>(site.buildings||[]).find(b=>b.id===destination)).filter(b=>validPoint(b?.entrance)).map(building=>({building,marker:Leaflet.marker(xy(building.entrance.x,building.entrance.y),{pane:"enterpriseHighlight",interactive:false,keyboard:false,icon:Leaflet.divIcon({className:"enterprise-route-destination-marker",html:"",iconSize:[10,10],iconAnchor:[5,5]})}).addTo(map),signature:""}));
      personalRoutes.set(id,{layer,color,destinations});
    }
    for(const zone of site.zones||[]){
      if(!zone.rectangle)continue;
      const layer=Leaflet.rectangle(rectangleBounds(zone.rectangle),{pane:"enterpriseZones",...zoneStyle(zone)});
      layer.addTo(map).bindTooltip(`${escape(zone.name||zone.id)}<br>${escape(zonePolicyText(zone))}`);
      const r=zone.rectangle;
      const label=Leaflet.marker(xy(r.x+r.width/2,r.y+r.height/2),{pane:"enterpriseLabels",interactive:false,keyboard:false,icon:Leaflet.divIcon({className:"enterprise-zone-label-marker",html:"",iconSize:[0,0]})}).addTo(map);
      zones.set(zone.id,{zone,layer,label,labelSignature:""});
    }
    for(const building of site.buildings||[]){
      const r=building.rectangle;if(!r)continue;
      const control=buildingControl(site,building),controlTitle=`${building.name} · Под контролем: ${control.label}`;
      const borderColor=ownershipColor(control.operatorId),fillColor=borderColor;
      const layer=Leaflet.rectangle(rectangleBounds(r),{pane:"enterpriseObjects",color:borderColor,weight:1.5,fillColor,fillOpacity:visibleOwner(control.operatorId) ? 0.31 : 0.16});
      layer.addTo(map).bindTooltip(`${escape(controlTitle)} · ${escape(building.id)}`);
      layer.on("click",()=>onBuilding(building.id));
      const label=Leaflet.marker(xy(r.x+r.width/2,r.y+r.height/2),{pane:"enterpriseLabels",interactive:false,keyboard:false,icon:Leaflet.divIcon({className:"enterprise-label-marker",html:"",iconSize:[0,0]})}).addTo(map);
      buildings.set(building.id,{building,control,controlTitle,layer,label,borderColor,labelSignature:""});
    }
    function gridStep(unitsPerPixel){
      return unitsPerPixel<.12?5:unitsPerPixel<.5?10:20;
    }
    function redrawGeometry(){
      if(destroyed)return;
      const unit=map.latLngToLayerPoint(xy(1,0)).x-map.latLngToLayerPoint(xy(0,0)).x;
      for(const {road,edge,fill} of roads){const width=roadWidthPixels(road.width,unit);edge.setStyle({weight:width+2});fill.setStyle({weight:width});}
      for(const record of buildings.values()){
        const r=record.building.rectangle,p1=map.latLngToLayerPoint(xy(r.x,r.y)),p2=map.latLngToLayerPoint(xy(r.x+r.width,r.y+r.height));
        const m=labelMetrics(record.building,Math.abs(p2.x-p1.x)-4,Math.abs(p2.y-p1.y)-4),signature=JSON.stringify(m);
        if(signature===record.labelSignature)continue;
        record.labelSignature=signature;
        record.label.setIcon(Leaflet.divIcon({className:"enterprise-label-marker",html:`<span class="enterprise-building-label" data-building-id="${escape(record.building.id)}" style="width:${m.width}px;height:${m.height}px;font-size:${m.fontSize}px" title="${escape(record.controlTitle)}"><span class="enterprise-building-name">${escape(m.label)}</span></span>`,iconSize:[m.width,m.height],iconAnchor:[m.width/2,m.height/2]}));
      }
      for(const record of zones.values()){
        const r=record.zone.rectangle,p1=map.latLngToLayerPoint(xy(r.x,r.y)),p2=map.latLngToLayerPoint(xy(r.x+r.width,r.y+r.height));
        const m=labelMetrics({id:record.zone.id,name:record.zone.short_name||record.zone.id,short_name:record.zone.short_name||record.zone.id},Math.abs(p2.x-p1.x)-4,Math.abs(p2.y-p1.y)-4),signature=JSON.stringify(m);
        if(signature!==record.labelSignature){record.labelSignature=signature;record.label.setIcon(Leaflet.divIcon({className:"enterprise-zone-label-marker",html:`<span class="enterprise-zone-label ${redZone(record.zone)?"forbidden":"permit"}" style="width:${m.width}px;height:${m.height}px;font-size:${m.fontSize}px" title="${escape(zonePolicyText(record.zone))}"><span>${escape(m.label)}</span></span>`,iconSize:[m.width,m.height],iconAnchor:[m.width/2,m.height/2]}));}
        if(redZone(record.zone))applyHatching(record.layer);
      }
      drawGrid(unit);
    }
    function applyHatching(layer){
      const path=layer.getElement?.(),svg=path?.ownerSVGElement;
      if(!svg||typeof document==="undefined")return;
      if(!svg.querySelector(`#${hatchId}`)){
        const namespace="http://www.w3.org/2000/svg",defs=document.createElementNS(namespace,"defs"),pattern=document.createElementNS(namespace,"pattern");
        pattern.setAttribute("id",hatchId);pattern.setAttribute("patternUnits","userSpaceOnUse");pattern.setAttribute("width","8");pattern.setAttribute("height","8");
        const base=document.createElementNS(namespace,"rect");base.setAttribute("width","8");base.setAttribute("height","8");base.setAttribute("fill","#b84b4b");pattern.appendChild(base);
        const hatch=document.createElementNS(namespace,"path");hatch.setAttribute("d","M-2 2L2 -2M0 8L8 0M6 10L10 6");hatch.setAttribute("stroke","#ffc3c3");hatch.setAttribute("stroke-width","1.5");pattern.appendChild(hatch);defs.appendChild(pattern);svg.insertBefore(defs,svg.firstChild);
      }
      path.setAttribute("fill",`url(#${hatchId})`);
    }
    function drawGrid(unit){
      const b=map.getBounds(),west=b.getWest(),east=b.getEast(),south=b.getSouth(),north=b.getNorth();
      let step=gridStep(1/Math.max(unit,.1));
      const count=()=>Math.ceil((east-west)/step)+Math.ceil((north-south)/step)+4;
      while(count()>80)step*=2;
      const left=Math.floor(west/step)*step,right=Math.ceil(east/step)*step,bottom=Math.floor(south/step)*step,top=Math.ceil(north/step)*step;
      const signature=[left,right,bottom,top,step].join(":");if(signature===gridSignature)return;
      gridSignature=signature;grid.clearLayers();
      const style={pane:"enterpriseGrid",color:"#2c4555",weight:1,opacity:.35,interactive:false};
      for(let x=left;x<=right;x+=step)Leaflet.polyline([[bottom,x],[top,x]],style).addTo(grid);
      for(let y=bottom;y<=top;y+=step)Leaflet.polyline([[y,left],[y,right]],style).addTo(grid);
    }
    const sensorSvg='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="9" width="12" height="11" rx="2"/><path d="M12 9V4m-4 3a5 5 0 0 1 8 0M5 4a9 9 0 0 1 14 0M3 12h3m12 0h3M3 17h3m12 0h3"/><circle cx="12" cy="14" r="1.4"/></svg>';
    function vehicleIcon(asset,alarm,stale,deviation){
      const owner=assetOperator(asset.asset_id||asset.id),ownerColor=ownershipColor(owner),foreign=!visibleOwner(owner);
      const color=stale?"stale":alarm?"alarm":"normal",id=asset.asset_id||asset.id;
      const symbol=asset.vehicle_type==="forklift"?"П":"С",name=symbol==="П"?"Погрузчик":"Служебный транспорт";
      return Leaflet.divIcon({className:`enterprise-vehicle-marker ${color}${deviation?" route-deviation":""}${foreign?" foreign":""}`,html:`<span class="enterprise-vehicle-glyph" style="--marker-color:${ownerColor}"><span class="enterprise-vehicle-symbol" title="${name}">${symbol}</span><b>${escape(id)}</b><time class="enterprise-signal-time" title="Последняя позиция: ${escape(asset.last_seen||"нет сигнала")}">${escape(signalTime(asset.last_seen))}</time>${stale?'<small class="enterprise-stale-badge" title="Последняя известная позиция">?</small>':alarm?`<small class="${deviation?"enterprise-route-warning":"enterprise-alarm-badge"}" title="${deviation?"Отклонение от личного маршрута":"Активное происшествие"}">!</small>`:""}</span>`,iconSize:[36,50],iconAnchor:[18,20]});
    }
    function sensorIcon(sensor,status,mounted){
      const owner=sensorOperator(sensor),ownerColor=ownershipColor(owner),foreign=!visibleOwner(owner);
      const type=sensor.type||"heartbeat";
      return Leaflet.divIcon({className:`enterprise-sensor-marker ${escape(status)} ${mounted?"mounted":"stationary"}${foreign?" foreign":""}`,html:`<span class="enterprise-sensor-glyph" style="--sensor-color:${ownerColor}" title="${escape(sensorTypeNames[type]||"Датчик")}">${sensorSvg}</span>${mounted?"":`<time class="enterprise-sensor-time" title="Последний сигнал: ${escape(sensor.last_received_at||"нет сигнала")}">${escape(signalTime(sensor.last_received_at))}</time>`}`,iconSize:mounted?[18,18]:[25,25],iconAnchor:mounted?[-10,25]:[12.5,12.5]});
    }
    function ageText(stamp){
      const t=Date.parse(stamp);if(!Number.isFinite(t))return "сигнал ещё не получен";
      const seconds=Math.max(0,Math.round((Date.now()+state.serverOffset-t)/1000));
      return seconds<60?"только что":seconds<3600?`${Math.floor(seconds/60)} мин. назад`:`${Math.floor(seconds/3600)} ч. назад`;
    }
    function paintMotion(now){
      let moving=false;
      for(const record of assets.values())if(record.motion){
        const fraction=(now-record.motion.started)/record.motion.duration;
        record.display=interpolatePoint(record.motion.from,record.motion.to,fraction);
        record.layer.setLatLng(xy(record.display.x,record.display.y));
        if(fraction>=1)record.motion=null;else moving=true;
      }
      for(const [id,record] of sensors){const mounted=assets.get(sensorDefinitions.get(id)?.asset_id);if(mounted?.display)record.layer.setLatLng(xy(mounted.display.x,mounted.display.y));}
      return moving;
    }
    function animationTick(now){
      animationFrame=null;if(destroyed)return;
      if(now-lastPaint<66){animationFrame=requestAnimationFrame(animationTick);return;}
      lastPaint=now;if(paintMotion(now))animationFrame=requestAnimationFrame(animationTick);
    }
    function scheduleMotion(){if(!destroyed&&animationFrame===null&&typeof requestAnimationFrame!=="undefined"&&[...assets.values()].some(r=>r.motion))animationFrame=requestAnimationFrame(animationTick);}
    function update({assets:nextAssets=[],sensors:nextSensors=[],incidents:nextIncidents=[],serverOffset=0,operator:nextOperator,operatorId,role:nextRole}){
      if(nextOperator!==undefined||operatorId!==undefined)currentOperator=nextOperator??operatorId;
      if(nextRole!==undefined)currentRole=nextRole;
      state={assets:nextAssets,sensors:nextSensors,incidents:nextIncidents,serverOffset};
      const liveAssets=new Map(nextAssets.map(a=>[a.asset_id||a.id,a]));
      const liveSensors=new Map(nextSensors.map(s=>[s.sensor_id||s.id,s]));
      for(const asset of nextAssets){
        const id=asset.asset_id||asset.id;if(!id||!validPoint(asset))continue;
        const alarm=nextIncidents.some(i=>incidentAssets(i).includes(id)&&i.condition_active&&workable(i)),stale=isStale(asset,serverOffset,site.dispatch_config?.position_stale_seconds||5),deviation=nextIncidents.some(i=>i.asset_id===id&&i.type==="route_deviation"&&i.condition_active&&workable(i));
        const signature=[asset.vehicle_type,alarm,stale,deviation,signalTime(asset.last_seen),currentOperator,currentRole].join(":");
        let record=assets.get(id);
        if(!record){const layer=Leaflet.marker(xy(asset.x,asset.y),{icon:vehicleIcon(asset,alarm,stale,deviation),riseOnHover:true}).addTo(map);layer.on("click",()=>{selectAsset(id);onAsset(id);});record={layer,signature,display:{x:asset.x,y:asset.y},stamp:asset.last_seen};assets.set(id,record);}
        if(record.signature!==signature){record.layer.setIcon(vehicleIcon(asset,alarm,stale,deviation));record.signature=signature;}
        if(record.stamp!==asset.last_seen||stale){
          const changed=record.display.x!==asset.x||record.display.y!==asset.y;
          if(changed&&!stale&&!reducedMotion&&typeof requestAnimationFrame!=="undefined")record.motion={from:{...record.display},to:{x:asset.x,y:asset.y},started:motionClock(),duration:750};
          else{record.motion=null;record.display={x:asset.x,y:asset.y};record.layer.setLatLng(xy(asset.x,asset.y));}
          record.stamp=asset.last_seen;
        }
        record.layer.bindTooltip(`${escape(id)} · ${asset.vehicle_type==="forklift"?"Погрузчик":"Служебный транспорт"}<br>${stale?"Последняя известная позиция · текущее место неизвестно":"Полученная позиция"}<br>${ageText(asset.last_seen)} · ${escape(asset.last_seen||"—")}<br>Под контролем: ${escape((site.operator_profiles||[]).find(p=>p.id===assetOperator(id))?.name||"Диспетчер КПП")}`);
      }
      for(const [id,record] of assets)if(!liveAssets.has(id)||!validPoint(liveAssets.get(id))){map.removeLayer(record.layer);assets.delete(id);}
      const shownSensors=new Set();
      for(const [id,definition] of sensorDefinitions){
        const runtime=liveSensors.get(id)||{},sensor={...definition,...runtime};
        const mounted=definition.type==="position"||Boolean(definition.asset_id),asset=liveAssets.get(definition.asset_id);
        const position=mounted?assets.get(definition.asset_id)?.display||asset:definition.position||definition;
        if(!validPoint(position))continue;
        shownSensors.add(id);
        const status=["online","offline"].includes(runtime.status)?runtime.status:"unknown",signature=[status,mounted,mounted?"":signalTime(runtime.last_received_at),currentOperator,currentRole].join(":");
        let record=sensors.get(id);
        if(!record){const layer=Leaflet.marker(xy(position.x,position.y),{pane:"enterpriseSensors",icon:sensorIcon(sensor,status,mounted),riseOnHover:true}).addTo(map);layer.on("click",()=>onSensor(id));record={layer,signature};sensors.set(id,record);}
        if(signature!==record.signature){record.layer.setIcon(sensorIcon(sensor,status,mounted));record.signature=signature;}
        record.layer.setLatLng(xy(position.x,position.y));
        const location=mounted?` · на ${escape(definition.asset_id)}${isStale(asset,serverOffset,site.dispatch_config?.position_stale_seconds||5)?" · последнее известное место":""}`:"";
        record.layer.bindTooltip(`${escape(sensorTypeNames[definition.type]||"Датчик")} ${escape(id)}${location}<br>${sensorStatusNames[status]} · ${ageText(runtime.last_received_at)} · ${escape(runtime.last_received_at||"—")}<br>Связь и свежесть измерений проверяются отдельно`);
      }
      for(const [id,record] of sensors)if(!shownSensors.has(id)){map.removeLayer(record.layer);sensors.delete(id);}
      for(const [id,record] of buildings){const danger=nextIncidents.some(i=>i.building_id===id&&i.condition_active&&workable(i));record.layer.setStyle({...buildingStyle(record),weight:danger?3:1.5});}
      applySelection();scheduleMotion();
      if(followedAsset){const target=liveAssets.get(followedAsset);if(validPoint(target)&&!isStale(target,serverOffset,site.dispatch_config?.position_stale_seconds||5)){settingView=true;try{map.panTo(xy(target.x,target.y),{animate:!reducedMotion,duration:.65,noMoveStart:true});}finally{settingView=false;}}}

    }
    function selectedGeometry(incident){
      const geometry=[];
      for(const field of ["zone_id","building_id","site_area_id"]){
        if(field==="site_area_id"&&["collision","route_deviation"].includes(incident.type))continue;
        const collection=field==="zone_id"?site.zones:field==="building_id"?site.buildings:site.site_areas;
        const object=(collection||[]).find(o=>o.id===incident[field]);
        if(object?.rectangle){geometry.push(object.rectangle);break;}
      }
      return geometry;
    }
    function selectionPoints(incident){
      const points=[];
      for(const r of selectedGeometry(incident))points.push(...rectangleBounds(r));
      for(const id of incidentAssets(incident)){const asset=state.assets.find(a=>(a.asset_id||a.id)===id);if(validPoint(asset))points.push(xy(asset.x,asset.y));}
      const sensor=sensors.get(incident.sensor_id);if(sensor)points.push(sensor.layer.getLatLng());
      return points;
    }
    let geometrySignature="";
    function drawPersonalRoutes(){
      const selected=new Set(selection?incidentAssets(selection):selectedAsset?[selectedAsset]:[]);
      for(const [id,record] of personalRoutes){
        const focused=selected.has(id);
        const color=ownershipColor(assetOperator(id));
        record.layer.setStyle({color,opacity:routesVisible?(focused?.95:.23):0,weight:focused?3:1.5,dashArray:focused?"6 4":"5 7"});
        for(const destination of record.destinations){
          const signature=[routesVisible,focused,color].join(":");if(signature===destination.signature)continue;destination.signature=signature;
          destination.marker.setIcon(Leaflet.divIcon({className:"enterprise-route-destination-marker",html:routesVisible?`<span class="enterprise-route-endpoint${focused?" focused":""}" style="color:${color}" title="${escape(id)} → ${escape(destination.building.name)}"></span>`:"",iconSize:[10,10],iconAnchor:[5,5]}));
        }
      }
    }
    function setRoutesVisible(visible){routesVisible=Boolean(visible);drawPersonalRoutes();}
    function selectAsset(id){followedAsset=null;selectedAsset=personalRoutes.has(id)?id:null;selection=null;applySelection();return Boolean(selectedAsset);}
    function applySelection(){
      for(const [id,r] of assets)r.layer.getElement()?.classList.toggle("enterprise-selected",Boolean(selection&&(incidentAssets(selection).includes(id)||sensorDefinitions.get(selection.sensor_id)?.asset_id===id)));
      for(const [id,r] of sensors)r.layer.getElement()?.classList.toggle("enterprise-selected",Boolean(selection&&selection.sensor_id===id));
      drawPersonalRoutes();
      const rectangles=selection?selectedGeometry(selection):[],signature=JSON.stringify(rectangles);
      if(signature===geometrySignature)return;
      geometrySignature=signature;highlightLayer.clearLayers();
      for(const r of rectangles)Leaflet.rectangle(rectangleBounds(r),{pane:"enterpriseHighlight",className:"enterprise-incident-halo",color:"#ffd079",weight:3,fillOpacity:0,interactive:false}).addTo(highlightLayer);
    }
    function highlight(incident,options={}){const key=i=>i?.incident_id||[...incidentAssets(i),i?.sensor_id].filter(Boolean).join(":");if(key(selection)!==key(incident)||!incident)followedAsset=null;selection=incident||null;selectedAsset=null;applySelection();if(options.recenter)return showIncident(incident);return selectionPoints(incident||{}).length>0;}
    function clearHighlight(){followedAsset=null;selection=null;applySelection();}
    function fitAll({animate=true}={}){followedAsset=null;viewMode="all";focusedSectorId=null;settingView=true;try{map.stop?.();if(animate&&!reducedMotion&&map.flyToBounds)map.flyToBounds(frame,{padding:[24,24],duration:.85});else map.fitBounds(frame,{padding:[24,24],animate:false});}finally{settingView=false;}}
    function focusSector(sectorId,{animate=false}={}){
      followedAsset=null;const rectangles=sectorRectangles(site,sectorId);
      if(!rectangles.length){fitAll();return false;}
      viewMode="sector";focusedSectorId=sectorId;settingView=true;
      try{
        map.stop?.();map.invalidateSize({pan:false});
        const bounds=rectangles.flatMap(rectangleBounds),options={padding:[18,18],maxZoom:3.5};
        if(animate&&!reducedMotion&&map.flyToBounds)map.flyToBounds(bounds,{...options,duration:0.85});
        else map.fitBounds(bounds,{...options,animate:false});
      }finally{settingView=false;}return true;
    }
    function showIncident(incident){
      highlight(incident);
      const identifiers=[...incidentAssets(incident),sensorDefinitions.get(incident?.sensor_id)?.asset_id].filter(Boolean);
      const targets=identifiers.map(id=>state.assets.find(a=>(a.asset_id||a.id)===id&&validPoint(a))).filter(Boolean),target=targets[0];
      const primary=target&&(target.asset_id||target.id),points=incident?.type==="collision"&&targets.length?targets.map(a=>xy(a.x,a.y)):target?[xy(target.x,target.y)]:selectionPoints(incident||{});
      if(!points.length)return false;
      viewMode="incident";focusedSectorId=null;settingView=true;try{map.stop?.();map.fitBounds(points,{padding:[48,48],maxZoom:3.75,animate:false});}finally{settingView=false;}followedAsset=primary||null;return true;
    }
    map.on("zoomend moveend",redrawGeometry);
    map.on("dragstart zoomstart",()=>{if(!settingView){followedAsset=null;viewMode="manual";focusedSectorId=null;}});
    const resize=typeof ResizeObserver!=="undefined"?new ResizeObserver(()=>{if(!destroyed){map.invalidateSize({pan:false});if(viewMode==="all")fitAll({animate:false});else if(viewMode==="sector")focusSector(focusedSectorId);redrawGeometry();}}):null;
    if(resize)resize.observe(map.getContainer());
    fitAll({animate:false});redrawGeometry();update(state);
    return {map,update,fitAll,focusSector,highlight,clearHighlight,showIncident,setRoutesVisible,selectAsset,setOperator,destroy(){destroyed=true;if(animationFrame!==null&&typeof cancelAnimationFrame!=="undefined")cancelAnimationFrame(animationFrame);resize?.disconnect();map.off("zoomend moveend",redrawGeometry);map.remove();},getLayerCounts(){return {buildings:buildings.size,roads:roads.length,pedestrians:pedestrians.length,routes:personalRoutes.size,assets:assets.size,sensors:sensors.size,grid:grid.getLayers().length};}};
  }
  return {create,labelMetrics,buildingControl,estimatedLabelWidth,roadWidthPixels,isStale,interpolatePoint,sectorRectangles,zoneStyle,zonePolicyText,signalTime};
});
