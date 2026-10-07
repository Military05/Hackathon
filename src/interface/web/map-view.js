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
  const sensorStatusNames={online:"На связи",offline:"Нет связи",unknown:"Связь ещё не подтверждена"};
  const sensorTypeNames={position:"Датчик положения",access:"Датчик прохода",heartbeat:"Контроль связи"};
  function isStale(asset,serverOffset=0,thresholdSeconds=5,now=Date.now()){
    const stamp=Date.parse(asset?.last_seen);
    return !Number.isFinite(stamp)||now+serverOffset-stamp>=thresholdSeconds*1000;
  }
  function roadWidthPixels(planWidth,pixelsPerUnit){return Math.max(1,Number(planWidth||5)*pixelsPerUnit);}
  function interpolatePoint(from,to,fraction){const t=Math.max(0,Math.min(1,fraction));return {x:from.x+(to.x-from.x)*t,y:from.y+(to.y-from.y)*t};}
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
  function create({elementId="map",site,onAsset=()=>{},onSensor=()=>{},onBuilding=()=>{}}){
    const Leaflet=globalThis.L;
    if(!Leaflet)throw Error("Локальная библиотека карты ещё не загружена");
    if(!site)throw Error("Нет геометрии предприятия");
    const minimum=site.coordinate_system?.min??0,maximum=site.coordinate_system?.max??100;
    const xy=(x,y)=>[maximum-y,x];
    const rectangleBounds=r=>[xy(r.x,r.y+r.height),xy(r.x+r.width,r.y)];
    const map=Leaflet.map(elementId,{crs:Leaflet.CRS.Simple,minZoom:0,maxZoom:5,zoomSnap:.25,attributionControl:false});
    const pane=(name,z)=>{map.createPane(name).style.zIndex=z;};
    pane("enterpriseGrid",220);pane("enterpriseRoads",300);pane("enterpriseObjects",360);pane("enterpriseLabels",420);pane("enterpriseHighlight",450);pane("enterpriseSensors",620);
    const roads=[],buildings=new Map(),zones=new Map(),assets=new Map(),sensors=new Map(),sensorDefinitions=new Map((site.sensors||[]).map(s=>[s.id||s.sensor_id,s]));
    const grid=Leaflet.layerGroup().addTo(map),highlightLayer=Leaflet.layerGroup().addTo(map);
    let state={assets:[],sensors:[],incidents:[],serverOffset:0},selection=null,gridSignature="",destroyed=false,viewMode="all",settingView=false,animationFrame=null,lastPaint=0;
    const motionClock=()=>typeof performance!=="undefined"?performance.now():Date.now();
    const reducedMotion=typeof matchMedia!=="undefined"&&matchMedia("(prefers-reduced-motion: reduce)").matches;
    const frame=[xy(minimum,maximum),xy(maximum,minimum)];
    map.setMaxBounds?.([xy(minimum-40,maximum+40),xy(maximum+40,minimum-40)]);
    for(const road of site.roads||[]){
      const source=road.points||road.path||[];
      const points=source.map(point).filter(validPoint).map(p=>xy(p.x,p.y));
      if(points.length<2)continue;
      const style={pane:"enterpriseRoads",interactive:false,lineCap:"round",lineJoin:"round",smoothFactor:0};
      const edge=Leaflet.polyline(points,{...style,color:"#425462",opacity:1}).addTo(map);
      const fill=Leaflet.polyline(points,{...style,color:"#293f4d",opacity:1}).addTo(map);
      roads.push({road,edge,fill});
    }
    for(const zone of site.zones||[]){
      if(!zone.rectangle)continue;
      const layer=Leaflet.rectangle(rectangleBounds(zone.rectangle),{pane:"enterpriseObjects",color:"#d99c5f",weight:1.5,dashArray:"5 5",fillColor:"#b27436",fillOpacity:.12});
      layer.addTo(map).bindTooltip(`${escape(zone.name||zone.id)} · ограничение доступа`);
      zones.set(zone.id,{zone,layer});
    }
    for(const building of site.buildings||[]){
      const r=building.rectangle;if(!r)continue;
      const office=building.id==="O1";
      const fillColor=/^#[0-9a-f]{6}$/i.test(building.color||"")?building.color:office?"#445779":"#24465c";
      const borderColor=office?"#a9b6df":"#668faa";
      const layer=Leaflet.rectangle(rectangleBounds(r),{pane:"enterpriseObjects",color:borderColor,weight:1.5,fillColor,fillOpacity:.95});
      layer.addTo(map).bindTooltip(`${escape(building.name)} · ${escape(building.id)}`);
      layer.on("click",()=>onBuilding(building.id));
      const label=Leaflet.marker(xy(r.x+r.width/2,r.y+r.height/2),{pane:"enterpriseLabels",interactive:false,keyboard:false,icon:Leaflet.divIcon({className:"enterprise-label-marker",html:"",iconSize:[0,0]})}).addTo(map);
      buildings.set(building.id,{building,layer,label,borderColor,labelSignature:""});
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
        record.label.setIcon(Leaflet.divIcon({className:"enterprise-label-marker",html:`<span class="enterprise-building-label" style="width:${m.width}px;height:${m.height}px;font-size:${m.fontSize}px" title="${escape(record.building.name)}"><span>${escape(m.label)}</span></span>`,iconSize:[m.width,m.height],iconAnchor:[m.width/2,m.height/2]}));
      }
      drawGrid(unit);
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
    function vehicleIcon(asset,alarm,stale){
      const color=stale?"stale":alarm?"alarm":"normal",id=asset.asset_id||asset.id;
      const symbol=asset.vehicle_type==="forklift"?"П":"С",name=symbol==="П"?"Погрузчик":"Служебный транспорт";
      return Leaflet.divIcon({className:`enterprise-vehicle-marker ${color}`,html:`<span class="enterprise-vehicle-glyph"><span class="enterprise-vehicle-symbol" title="${name}">${symbol}</span><b>${escape(id)}</b>${stale?'<small class="enterprise-stale-badge" title="Последняя известная позиция">?</small>':""}</span>`,iconSize:[36,38],iconAnchor:[18,20]});
    }
    function sensorIcon(sensor,status,mounted){
      const type=sensor.type||"heartbeat";
      return Leaflet.divIcon({className:`enterprise-sensor-marker ${escape(status)} ${mounted?"mounted":"stationary"}`,html:`<span class="enterprise-sensor-glyph" title="${escape(sensorTypeNames[type]||"Датчик")}">${sensorSvg}</span>`,iconSize:mounted?[18,18]:[25,25],iconAnchor:mounted?[-10,25]:[12.5,12.5]});
    }
    function ageText(stamp){
      const t=Date.parse(stamp);return Number.isFinite(t)?`${Math.max(0,Math.round((Date.now()+state.serverOffset-t)/1000))} сек. назад`:"сигнал ещё не получен";
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
    function update({assets:nextAssets=[],sensors:nextSensors=[],incidents:nextIncidents=[],serverOffset=0}){
      state={assets:nextAssets,sensors:nextSensors,incidents:nextIncidents,serverOffset};
      const liveAssets=new Map(nextAssets.map(a=>[a.asset_id||a.id,a]));
      const liveSensors=new Map(nextSensors.map(s=>[s.sensor_id||s.id,s]));
      for(const asset of nextAssets){
        const id=asset.asset_id||asset.id;if(!id||!validPoint(asset))continue;
        const alarm=nextIncidents.some(i=>i.asset_id===id&&i.condition_active&&workable(i)),stale=isStale(asset,serverOffset,site.dispatch_config?.position_stale_seconds||5);
        const signature=[asset.vehicle_type,alarm,stale].join(":");
        let record=assets.get(id);
        if(!record){const layer=Leaflet.marker(xy(asset.x,asset.y),{icon:vehicleIcon(asset,alarm,stale),riseOnHover:true}).addTo(map);layer.on("click",()=>onAsset(id));record={layer,signature,display:{x:asset.x,y:asset.y},stamp:asset.last_seen};assets.set(id,record);}
        if(record.signature!==signature){record.layer.setIcon(vehicleIcon(asset,alarm,stale));record.signature=signature;}
        if(record.stamp!==asset.last_seen||stale){
          const changed=record.display.x!==asset.x||record.display.y!==asset.y;
          if(changed&&!stale&&!reducedMotion&&typeof requestAnimationFrame!=="undefined")record.motion={from:{...record.display},to:{x:asset.x,y:asset.y},started:motionClock(),duration:750};
          else{record.motion=null;record.display={x:asset.x,y:asset.y};record.layer.setLatLng(xy(asset.x,asset.y));}
          record.stamp=asset.last_seen;
        }
        record.layer.bindTooltip(`${escape(id)} · ${asset.vehicle_type==="forklift"?"Погрузчик":"Служебный транспорт"}<br>${stale?"Последняя известная позиция · текущее место неизвестно":"Полученная позиция"}<br>${ageText(asset.last_seen)}`);
      }
      for(const [id,record] of assets)if(!liveAssets.has(id)||!validPoint(liveAssets.get(id))){map.removeLayer(record.layer);assets.delete(id);}
      const shownSensors=new Set();
      for(const [id,definition] of sensorDefinitions){
        const runtime=liveSensors.get(id)||{},sensor={...definition,...runtime};
        const mounted=definition.type==="position"||Boolean(definition.asset_id),asset=liveAssets.get(definition.asset_id);
        const position=mounted?assets.get(definition.asset_id)?.display||asset:definition.position||definition;
        if(!validPoint(position))continue;
        shownSensors.add(id);
        const status=["online","offline"].includes(runtime.status)?runtime.status:"unknown",signature=[status,mounted].join(":");
        let record=sensors.get(id);
        if(!record){const layer=Leaflet.marker(xy(position.x,position.y),{pane:"enterpriseSensors",icon:sensorIcon(sensor,status,mounted),riseOnHover:true}).addTo(map);layer.on("click",()=>onSensor(id));record={layer,signature};sensors.set(id,record);}
        if(signature!==record.signature){record.layer.setIcon(sensorIcon(sensor,status,mounted));record.signature=signature;}
        record.layer.setLatLng(xy(position.x,position.y));
        const location=mounted?` · на ${escape(definition.asset_id)}${isStale(asset,serverOffset,site.dispatch_config?.position_stale_seconds||5)?" · последнее известное место":""}`:"";
        record.layer.bindTooltip(`${escape(sensorTypeNames[definition.type]||"Датчик")} ${escape(id)}${location}<br>${sensorStatusNames[status]} · ${ageText(runtime.last_received_at)}<br>Связь и свежесть измерений проверяются отдельно`);
      }
      for(const [id,record] of sensors)if(!shownSensors.has(id)){map.removeLayer(record.layer);sensors.delete(id);}
      for(const [id,record] of buildings){const danger=nextIncidents.some(i=>i.building_id===id&&i.condition_active&&workable(i));record.layer.setStyle({color:danger?"#ff7c7c":record.borderColor});}
      applySelection();scheduleMotion();
    }
    function selectedGeometry(incident){
      const geometry=[];
      for(const field of ["zone_id","building_id","site_area_id"]){
        const collection=field==="zone_id"?site.zones:field==="building_id"?site.buildings:site.site_areas;
        const object=(collection||[]).find(o=>o.id===incident[field]);
        if(object?.rectangle){geometry.push(object.rectangle);break;}
      }
      return geometry;
    }
    function selectionPoints(incident){
      const points=[];
      for(const r of selectedGeometry(incident))points.push(...rectangleBounds(r));
      const asset=state.assets.find(a=>(a.asset_id||a.id)===incident.asset_id);
      if(validPoint(asset))points.push(xy(asset.x,asset.y));
      const sensor=sensors.get(incident.sensor_id);if(sensor)points.push(sensor.layer.getLatLng());
      return points;
    }
    let geometrySignature="";
    function applySelection(){
      for(const [id,r] of assets)r.layer.getElement()?.classList.toggle("enterprise-selected",Boolean(selection&&(selection.asset_id===id||sensorDefinitions.get(selection.sensor_id)?.asset_id===id)));
      for(const [id,r] of sensors)r.layer.getElement()?.classList.toggle("enterprise-selected",Boolean(selection&&selection.sensor_id===id));
      const rectangles=selection?selectedGeometry(selection):[],signature=JSON.stringify(rectangles);
      if(signature===geometrySignature)return;
      geometrySignature=signature;highlightLayer.clearLayers();
      for(const r of rectangles)Leaflet.rectangle(rectangleBounds(r),{pane:"enterpriseHighlight",className:"enterprise-incident-halo",color:"#ffd079",weight:3,fillOpacity:0,interactive:false}).addTo(highlightLayer);
    }
    function highlight(incident){selection=incident||null;applySelection();return selectionPoints(incident||{}).length>0;}
    function clearHighlight(){selection=null;applySelection();}
    function fitAll(){viewMode="all";settingView=true;try{map.fitBounds(frame,{padding:[24,24],animate:false});}finally{settingView=false;}}
    function focusSector(sectorId){
      const rectangles=(site.site_areas||[]).filter(a=>a.responsible_sector_id===sectorId&&a.rectangle).map(a=>a.rectangle);
      if(!rectangles.length||sectorId==="coordination"){fitAll();return;}
      viewMode="sector";settingView=true;try{map.fitBounds(rectangles.flatMap(rectangleBounds),{padding:[28,28],maxZoom:3.25,animate:false});}finally{settingView=false;}
    }
    function showIncident(incident){
      highlight(incident);const points=selectionPoints(incident||{});if(!points.length)return false;
      viewMode="incident";settingView=true;try{map.fitBounds(points,{padding:[48,48],maxZoom:3.75,animate:false});}finally{settingView=false;}return true;
    }
    map.on("zoomend moveend",redrawGeometry);
    map.on("dragstart zoomstart",()=>{if(!settingView)viewMode="manual";});
    const resize=typeof ResizeObserver!=="undefined"?new ResizeObserver(()=>{if(!destroyed){map.invalidateSize({pan:false});if(viewMode==="all")fitAll();redrawGeometry();}}):null;
    if(resize)resize.observe(map.getContainer());
    fitAll();redrawGeometry();update(state);
    return {map,update,fitAll,focusSector,highlight,clearHighlight,showIncident,destroy(){destroyed=true;if(animationFrame!==null&&typeof cancelAnimationFrame!=="undefined")cancelAnimationFrame(animationFrame);resize?.disconnect();map.off("zoomend moveend",redrawGeometry);map.remove();},getLayerCounts(){return {buildings:buildings.size,roads:roads.length,assets:assets.size,sensors:sensors.size,grid:grid.getLayers().length};}};
  }
  return {create,labelMetrics,estimatedLabelWidth,roadWidthPixels,isStale,interpolatePoint};
});
