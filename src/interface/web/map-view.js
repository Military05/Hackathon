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
    if(asset?.monitoring_paused)return false;
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
    pane("enterpriseTerrain",180);pane("enterpriseArchitecture",350);pane("enterpriseGrid",220);pane("enterpriseSectors",260);pane("enterpriseRoads",300);pane("enterpriseRoadTexture",305);pane("enterprisePedestrians",310);pane("enterpriseRoutes",320);pane("enterpriseZones",330);pane("enterpriseObjects",360);pane("enterpriseLabels",420);pane("enterpriseHighlight",450);pane("enterpriseSensors",620);
    const roads=[],pedestrians=[],personalRoutes=new Map(),buildings=new Map(),zones=new Map(),assets=new Map(),sensors=new Map(),sensorDefinitions=new Map((site.sensors||[]).map(s=>[s.id||s.sensor_id,s]));
    const grid=Leaflet.layerGroup().addTo(map),highlightLayer=Leaflet.layerGroup().addTo(map);
    let state={assets:[],sensors:[],incidents:[],serverOffset:0},selection=null,selectedAsset=null,followedAsset=null,routesVisible=true,gridSignature="",destroyed=false,viewMode="all",focusedSectorId=null,settingView=false,animationFrame=null,lastPaint=0,geometryUnit=1,followPosition=null,sectorHighlightId=null,sectorHighlightTimer=null;
    let currentOperator=operator,currentRole=role;
    const siteAssets=new Map((site.assets||[]).map(a=>[a.id,a]));
    function areaOperator(areaId){const area=(site.site_areas||[]).find(a=>a.id===areaId);return (site.operator_profiles||[]).find(p=>p.sector_id===area?.responsible_sector_id)?.id||"dispatcher-3";}
    function assetOperator(assetId){const definition=siteAssets.get(assetId);const building=(site.buildings||[]).find(b=>b.id===definition?.destination);return building?buildingControl(site,building).operatorId:areaOperator(definition?.site_area_id);}
    function sensorOperator(definition){return definition.asset_id?assetOperator(definition.asset_id):areaOperator(definition.site_area_id);}
    const visibleOwner=id=>currentRole==="admin"||currentOperator==="admin"||!currentOperator||currentOperator===id;
    const ownershipColor=id=>visibleOwner(id)?operatorColors[id]||"#bca3ed":"#87929d";
    function buildingStyle(record){const color=ownershipColor(record.control.operatorId);return {color,fillColor:color,fillOpacity:visibleOwner(record.control.operatorId) ? 0.09 : 0.05};}
    function setOperator(id,nextRole="dispatcher"){clearFollow();clearSectorHighlight();currentOperator=id;currentRole=nextRole;for(const record of buildings.values())record.layer.setStyle(buildingStyle(record));roofOverlay.setUrl(roofArt());update(state);}
    const hatchId=`enterprise-forbidden-hatch-${++instanceSequence}`;
    const motionClock=()=>typeof performance!=="undefined"?performance.now():Date.now();
    const reducedMotion=typeof matchMedia!=="undefined"&&matchMedia("(prefers-reduced-motion: reduce)").matches;
    const sceneryBounds=[xy(minimum-16,maximum+16),xy(maximum+16,minimum-16)];
    const svgUrl=body=>`data:image/svg+xml;charset=UTF-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="-16 -16 132 132">${body}</svg>`)}`;
    let photoMaterials=null;
    const materialPattern=(name,id,size)=>{
      const image=photoMaterials?.[name]?.data;
      if(typeof image!=="string"||!/^data:image\/(jpeg|png);base64,/.test(image))return "";
      return `<pattern id="${id}" width="${size}" height="${size}" patternUnits="userSpaceOnUse"><image href="${image}" width="${size}" height="${size}" preserveAspectRatio="none"/></pattern>`;
    };
    function terrainArt(){
      const parts=[`<defs><pattern id="grass" width="5" height="5" patternUnits="userSpaceOnUse"><rect width="5" height="5" fill="#304b38"/><path d="M1 2l.3-.8M3 4l.4-.6" stroke="#527350" stroke-width=".16" opacity=".5"/></pattern><pattern id="yard" width="6" height="6" patternUnits="userSpaceOnUse"><rect width="6" height="6" fill="#626c61"/><path d="M0 0h6v6" fill="none" stroke="#889086" stroke-width=".07"/></pattern><g id="tree"><ellipse cy=".9" rx="1.3" ry=".65" fill="#16291d" opacity=".7"/><path d="M-.15 0h.3v1.1h-.3" fill="#856546"/><circle cx="-.5" cy="-.3" r=".9" fill="#294931"/><circle cx=".4" cy="-.6" r="1" fill="#416c42"/><circle cy="-1" r=".85" fill="#557e4d"/><circle cx="-.3" cy="-1.3" r=".45" fill="#6a9254"/></g></defs><rect x="-16" y="-16" width="132" height="132" fill="url(#grass)"/><rect x="2" y="3" width="96" height="97" rx="2" fill="url(#yard)"/><path d="M2 3H98V100H44M26 100H2V3" fill="none" stroke="#1b272b" stroke-width=".9"/><path d="M2 3H98V100H44M26 100H2V3" fill="none" stroke="#a4aaa3" stroke-width=".35"/>`];
      let seed=781;const random=()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296;};
      for(let y=-12;y<=112;y+=4.3)for(let x=-12;x<=112;x+=4.3){const px=x+(random()-.5)*2,py=y+(random()-.5)*2;if(px>-.5&&px<101&&py>0&&py<103)continue;if(py>98&&px>23&&px<47)continue;parts.push(`<use href="#tree" transform="translate(${px.toFixed(2)} ${py.toFixed(2)}) scale(${(.9+random()*.65).toFixed(2)})"/>`);}
      for(let n=3;n<=98;n+=3){parts.push(`<path d="M${n} 2.6v.9M${n} 99.6v.9" stroke="#c3c8bd" stroke-width=".3"/>`);}
      for(let n=5;n<=98;n+=3){parts.push(`<path d="M1.6 ${n}h.9M97.6 ${n}h.9" stroke="#c3c8bd" stroke-width=".3"/>`);}
      parts.push('<path d="M26 100v2M44 100v2" stroke="#d2c29a" stroke-width=".8"/><path d="M27 102H43" stroke="#c5b48b" stroke-width=".25" stroke-dasharray="1 1"/>');
      // Decorative loading aprons and parked cars do not create sensor objects.
      for(const building of site.buildings||[]){
        const r=building.rectangle;if(!r||!/^W/.test(building.id))continue;
        const x=r.x+r.width*.25,y=r.y+r.height+.35,w=r.width*.5;
        parts.push(`<rect x="${x}" y="${y}" width="${w}" height="1.25" fill="#778075" stroke="#d0b568" stroke-width=".16"/><path d="M${x+.3} ${y+.3}h${w-.6}" stroke="#dcc17b" stroke-width=".2" stroke-dasharray=".6 .5"/>`);
      }
      parts.push('<rect x="6" y="80.9" width="17" height="2.3" fill="#454f4c" stroke="#929c8e" stroke-width=".12"/><text x="6.4" y="82.4" font-family="sans-serif" font-size="1.2" fill="#e4e8cb">P</text>');
      for(let n=0;n<6;n++){
        const x=8.2+n*2.25;parts.push(`<path d="M${x} 81.05v1.85h1.9" stroke="#d4d9bf" stroke-width=".1" fill="none"/>`);
        if(n%3!==1)parts.push(`<rect x="${x+.4}" y="81.25" width="1.15" height="1.5" rx=".25" fill="${n%2?'#c4b7a1':'#acb8b4'}" stroke="#253735" stroke-width=".12"/><rect x="${x+.58}" y="81.5" width=".8" height=".35" fill="#3c565c"/>`);
      }
      parts.push('<path d="M44.3 100.5v-1.2" stroke="#d3c2a0" stroke-width=".5"/><path d="M44.3 99.3l.9-2.1" stroke="#eadbb0" stroke-width=".35"/><path d="M44.3 99.3l.9-2.1" stroke="#b76452" stroke-width=".35" stroke-dasharray=".3 .4"/>');
      let art=parts.join('');
      if(photoMaterials){
        art=art.replace('<defs>','<defs>'+materialPattern('grass','photo-grass',12)+materialPattern('concrete','photo-concrete',9)+materialPattern('grass','photo-canopy',3));
        if(photoMaterials.grass)art=art.replace('fill="url(#grass)"','fill="url(#photo-grass)"').replace(/fill="#(416c42|557e4d)"/g,'fill="url(#photo-canopy)"');
        if(photoMaterials.concrete)art=art.replace('fill="url(#yard)"','fill="url(#photo-concrete)"');
      }
      return svgUrl(art);
    }
    function roofArt(){
      const parts=[`<defs>${materialPattern('metal','photo-metal',5)}<filter id="metal-neutral"><feColorMatrix type="saturate" values="0"/></filter></defs>`];
      for(const building of site.buildings||[]){
        const r=building.rectangle;if(!r)continue;
        const color=ownershipColor(buildingControl(site,building).operatorId),x=r.x,y=r.y,w=r.width,h=r.height;
        parts.push(`<rect x="${x+.8}" y="${y+1}" width="${w}" height="${h}" rx=".4" fill="#132323" opacity=".55"/><rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${photoMaterials?.metal?'url(#photo-metal)':'#384c52'}" filter="url(#metal-neutral)"/><rect x="${x+.3}" y="${y+.3}" width="${w-.6}" height="${h-.6}" fill="${color}" fill-opacity=".42"/><path d="M${x} ${y+h}H${x+w}V${y}" fill="none" stroke="#1e3039" stroke-width=".65"/><path d="M${x} ${y+h}V${y}H${x+w}" fill="none" stroke="#ced9d8" stroke-opacity=".65" stroke-width=".23"/>`);
        const industrial=/^[WP]/.test(building.id);
        if(industrial){
          // Roof facets add depth without changing collision footprints.
          const ridge=y+h*.45;
          parts.push(`<path d="M${x+.35} ${y+.35}H${x+w-.35}L${x+w-.7} ${ridge}H${x+.7}Z" fill="#e5ede6" fill-opacity=".16"/><path d="M${x+.7} ${ridge}H${x+w-.7}L${x+w-.35} ${y+h-.45}H${x+.35}Z" fill="#162f3a" fill-opacity=".23"/><path d="M${x+.7} ${ridge}H${x+w-.7}" stroke="#d4e0dc" stroke-width=".22"/>`);
          if(/^P/.test(building.id))for(let n=1;n<w-2;n+=4)parts.push(`<path d="M${x+n} ${y+2.3}l1.5-1.1 1.5 1.1Z" fill="#bdc9c6" fill-opacity=".8"/><path d="M${x+n} ${y+2.3}h3" stroke="#233a40" stroke-width=".16"/>`);
          for(let n=2;n<w-1;n+=2.4)parts.push(`<path d="M${x+n} ${y+.4}V${y+h-.5}" stroke="#142c36" stroke-opacity=".28" stroke-width=".16"/>`);
          for(let n=1.5;n<w-1.5;n+=5)parts.push(`<rect x="${x+n}" y="${y+1}" width="2.6" height=".8" fill="#b8d6d7" fill-opacity=".78" stroke="#253b44" stroke-width=".15"/>`);
          parts.push(`<rect x="${x+w*.4}" y="${y+h-.45}" width="${w*.2}" height=".65" fill="#a1a9a8"/><path d="M${x+w*.42} ${y+h}h${w*.16}" stroke="#303e43" stroke-width=".2"/>`);
        }else{
          for(let n=1;n<w-1;n+=2.2)parts.push(`<rect x="${x+n}" y="${y+h-.7}" width="1.2" height=".4" fill="#bcd8da"/>`);
          parts.push(`<rect x="${x+w-2.8}" y="${y+1}" width="1.8" height="1.1" fill="#c1c8c2" stroke="#536368" stroke-width=".15"/><path d="M${x+w-2.6} ${y+1.4}h1.3" stroke="#526367" stroke-width=".18"/>`);
        }
        if(building.id==='O1'){
          parts.push(`<rect x="${x+1}" y="${y+.8}" width="${w-2}" height="1.8" fill="#b2beb7" fill-opacity=".35"/><path d="M${x+1} ${y+2.6}H${x+w-1}" stroke="#243c44" stroke-width=".2"/>`);
          for(let n=1.2;n<w-1;n+=2.1)parts.push(`<rect x="${x+n}" y="${y+.9}" width="1.2" height="1.5" fill="#b9d3d5" stroke="#3e5964" stroke-width=".15"/>`);
          for(let n=0;n<3;n++)parts.push(`<rect x="${x+w*.38-.2*n}" y="${y+h+.2*n}" width="${w*.24+.4*n}" height=".22" fill="#adb8ae" stroke="#475950" stroke-width=".05"/>`);
        }
        if(building.id==='G1'){
          parts.push(`<rect x="${x+.8}" y="${y+.5}" width="${w-1.6}" height="1.5" fill="#b1cecc" stroke="#2e4850" stroke-width=".18"/><path d="M${x+w/2} ${y+.5}v1.5" stroke="#435d65" stroke-width=".2"/><rect x="${x+.8}" y="${y+h-1.2}" width="1.4" height="1.1" fill="#b9c5b6"/>`);
        }
        if(building.id==='E2'){
          for(let n=0;n<2;n++){const cx=x+1.8+n*3.1;parts.push(`<ellipse cx="${cx+.25}" cy="${y+1.3}" rx="1.1" ry=".8" fill="#263d43"/><rect x="${cx-1}" y="${y+.6}" width="2" height="1.4" fill="#8ca6a5"/><ellipse cx="${cx}" cy="${y+.6}" rx="1" ry=".45" fill="#c7d4cc" stroke="#506b73" stroke-width=".15"/>`);}
        }
        if(building.id==='M1')parts.push(`<rect x="${x+.9}" y="${y+.7}" width="2.4" height="2.4" rx=".3" fill="#415d56"/><path d="M${x+2.1} ${y+1.1}v1.6m-.8-.8h1.6" stroke="#e1e8dc" stroke-width=".45"/>`);
        if(building.id==='F1')for(let n=0;n<2;n++)parts.push(`<rect x="${x+.7+n*3.4}" y="${y+h-1.8}" width="2.6" height="1.4" fill="#9aa8a7" stroke="#3c5359" stroke-width=".15"/><path d="M${x+.9+n*3.4} ${y+h-1.35}h2.2" stroke="#51696d" stroke-width=".15"/>`);
        if(building.id==='C1')for(let n=0;n<3;n++)parts.push(`<path d="M${x+.9+n*2.8} ${y+.8}h1.8l-.3 1.1h-1.5Z" fill="#748a77" stroke="#d0d9c7" stroke-width=".15"/>`);
        if(building.id==='E1'){
          parts.push(`<path d="M${x+1} ${y+1.3}h${w-4}v1.1" fill="none" stroke="#b8beb2" stroke-width=".5"/><path d="M${x+1} ${y+1.3}h${w-4}v1.1" fill="none" stroke="#7b8d85" stroke-width=".15"/>`);
          for(let n=0;n<2;n++)parts.push(`<circle cx="${x+1.8+n*2.7}" cy="${y+1.5}" r=".75" fill="#859791" stroke="#cad3c5" stroke-width=".2"/>`);
        }
        if(building.id==='E1')parts.push(`<ellipse cx="${x+w-1.5}" cy="${y-1}" rx=".85" ry=".4" fill="#182b2d"/><path d="M${x+w-2.3} ${y-1}v3h1.6v-3" fill="#89908a"/><ellipse cx="${x+w-1.5}" cy="${y-1}" rx=".8" ry=".35" fill="#c0c4bc"/>`);
      }
      return svgUrl(parts.join(''));
    }
    const terrainOverlay=Leaflet.imageOverlay(terrainArt(),sceneryBounds,{pane:"enterpriseTerrain",interactive:false}).addTo(map);
    const roadPhotoOverlay=Leaflet.imageOverlay(svgUrl(''),sceneryBounds,{pane:"enterpriseRoadTexture",interactive:false}).addTo(map);
    if(typeof fetch==="function")fetch('/map-materials.json',{cache:'force-cache'}).then(response=>response.ok?response.json():null).then(materials=>{
      if(destroyed||!materials)return;
      photoMaterials=materials;terrainOverlay.setUrl(terrainArt());roofOverlay.setUrl(roofArt());
      if(materials.asphalt){
        const paths=(site.roads||[]).map(road=>{const points=(road.points||road.path||[]).map(point).filter(validPoint);if(points.length<2)return '';return `<path d="M${points.map(p=>`${p.x} ${p.y}`).join('L')}" fill="none" stroke="url(#photo-asphalt)" stroke-width="${Number(road.width)||4}" stroke-linecap="${road.entrance_building_id?'butt':'round'}" stroke-linejoin="round"/>`;});
        roadPhotoOverlay.setUrl(svgUrl(`<defs>${materialPattern('asphalt','photo-asphalt',8)}</defs>${paths.join('')}`));
      }
    }).catch(()=>{});
    const roofOverlay=Leaflet.imageOverlay(roofArt(),sceneryBounds,{pane:"enterpriseArchitecture",interactive:false}).addTo(map);
    const frame=[xy(minimum-11,maximum+11),xy(maximum+11,minimum-11)];
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
      const edge=Leaflet.polyline(points,{...style,color:"#a6afa6",opacity:1}).addTo(map);
      roads.push({road,edge,points,style});
    }
    // All road edges precede all road fills: a junction remains one continuous surface.
    for(const road of roads)road.fill=Leaflet.polyline(road.points,{...road.style,color:"#394348",opacity:1}).addTo(map);
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
      const layer=Leaflet.rectangle(rectangleBounds(r),{pane:"enterpriseObjects",color:borderColor,weight:1.5,fillColor,fillOpacity:visibleOwner(control.operatorId) ? 0.09 : 0.05});
      layer.addTo(map).bindTooltip(`${escape(controlTitle)} · ${escape(building.id)}`);
      layer.on("click",()=>{clearFollow();onBuilding(building.id);});
      const label=Leaflet.marker(xy(r.x+r.width/2,r.y+r.height/2),{pane:"enterpriseLabels",interactive:false,keyboard:false,icon:Leaflet.divIcon({className:"enterprise-label-marker",html:"",iconSize:[0,0]})}).addTo(map);
      buildings.set(building.id,{building,control,controlTitle,layer,label,borderColor,labelSignature:""});
    }
    function gridStep(unitsPerPixel){
      return unitsPerPixel<.12?5:unitsPerPixel<.5?10:20;
    }
    function redrawGeometry(){
      if(destroyed)return;
      // Following pans at the same zoom: road widths and labels do not need recalculation.
      if(settingView&&followedAsset){drawGrid(geometryUnit);return;}
      const unit=map.latLngToLayerPoint(xy(1,0)).x-map.latLngToLayerPoint(xy(0,0)).x;
      geometryUnit=unit;
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
      const style={pane:"enterpriseGrid",color:"#83947d",weight:1,opacity:.10,interactive:false};
      for(let x=left;x<=right;x+=step)Leaflet.polyline([[bottom,x],[top,x]],style).addTo(grid);
      for(let y=bottom;y<=top;y+=step)Leaflet.polyline([[y,left],[y,right]],style).addTo(grid);
    }
    const sensorSvg='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="9" width="12" height="11" rx="2"/><path d="M12 9V4m-4 3a5 5 0 0 1 8 0M5 4a9 9 0 0 1 14 0M3 12h3m12 0h3M3 17h3m12 0h3"/><circle cx="12" cy="14" r="1.4"/></svg>';
    function vehicleIcon(asset,alarm,stale,deviation){
      const owner=assetOperator(asset.asset_id||asset.id),ownerColor=ownershipColor(owner),foreign=!visibleOwner(owner);
      const color=stale?"stale":alarm?"alarm":"normal",id=asset.asset_id||asset.id;
      const forklift=asset.vehicle_type==="forklift",name=forklift?"Погрузчик":"Служебный транспорт";
      const symbol=`<svg class="enterprise-vehicle-body" style="transform:rotate(var(--vehicle-heading,0deg))" viewBox="0 0 28 34" aria-hidden="true"><ellipse cx="15" cy="19" rx="10" ry="12" fill="#101b20" opacity=".45"/><path class="enterprise-front-wheels" d="M5 12v6m18-6v6" stroke="#152328" stroke-width="4" stroke-linecap="round"/><path d="M5 25v5m18-5v5" stroke="#152328" stroke-width="4" stroke-linecap="round"/>${forklift?'<path d="M9 10V2m10 8V2" stroke="#c3c9c4" stroke-width="2"/><rect x="7" y="9" width="14" height="23" rx="3" fill="currentColor"/><rect x="9" y="13" width="10" height="10" rx="1" fill="#243941" stroke="#bfd2cf" stroke-width="1"/><path d="M10 14v8m8-8v8" stroke="#b8c7b8" stroke-width="1.2"/><rect x="10" y="26" width="8" height="3" fill="#cad4c7"/>':'<rect x="6" y="3" width="16" height="29" rx="4" fill="currentColor"/><path d="M8 10h12l-1-4H9zM9 25h10v4H9z" fill="#243b49"/><rect x="8" y="12" width="12" height="11" rx="1" fill="#d0d8cf" fill-opacity=".65"/><path d="M7 5h3m8 0h3" stroke="#f0e4ae" stroke-width="1.5"/>'}<path class="enterprise-brake-lights" d="M7 30h3m8 0h3" stroke="#cf665e" stroke-width="1.6"/></svg>`;
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
      syncFollow();
      return moving;
    }
    function animationTick(now){
      animationFrame=null;if(destroyed)return;
      if(now-lastPaint<1000/15){animationFrame=requestAnimationFrame(animationTick);return;}
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
        if(!record){const layer=Leaflet.marker(xy(asset.x,asset.y),{icon:vehicleIcon(asset,alarm,stale,deviation),riseOnHover:true}).addTo(map);layer.on("click",()=>{selectAsset(id);onAsset(id);});record={layer,signature,display:{x:asset.x,y:asset.y},received:{x:asset.x,y:asset.y},stamp:asset.last_seen};assets.set(id,record);}
        if(record.signature!==signature){record.layer.setIcon(vehicleIcon(asset,alarm,stale,deviation));record.signature=signature;}
        const changed=record.received.x!==asset.x||record.received.y!==asset.y;
        let turn=0;
        if(changed){const dx=asset.x-record.received.x,dy=asset.y-record.received.y,target=Math.atan2(dy,dx)*180/Math.PI+90;const prior=record.heading??target;turn=(((target-prior)%360)+540)%360-180;record.heading=prior+turn;}
        const vehicleElement=record.layer.getElement();
        vehicleElement?.style.setProperty("--vehicle-heading",`${record.heading||0}deg`);
        vehicleElement?.style.setProperty("--steering-angle",`${Math.max(-16,Math.min(16,turn*.25))}deg`);
        vehicleElement?.classList.toggle("is-stopped",!changed||Boolean(asset.monitoring_paused));
        if(asset.monitoring_paused){record.motion=null;record.received={x:asset.x,y:asset.y};}
        else if(changed||stale){
          if(changed&&!stale&&!reducedMotion&&typeof requestAnimationFrame!=="undefined"){
            const now=motionClock();
            if(record.motion)record.display=interpolatePoint(record.motion.from,record.motion.to,(now-record.motion.started)/record.motion.duration);
            record.motion={from:{...record.display},to:{x:asset.x,y:asset.y},started:now,duration:750};
          }else{record.motion=null;record.display={x:asset.x,y:asset.y};record.layer.setLatLng(xy(asset.x,asset.y));}
          record.received={x:asset.x,y:asset.y};
        }
        record.stamp=asset.last_seen;
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
        if(!record){const layer=Leaflet.marker(xy(position.x,position.y),{pane:"enterpriseSensors",icon:sensorIcon(sensor,status,mounted),riseOnHover:true}).addTo(map);layer.on("click",()=>{clearFollow();onSensor(id);});record={layer,signature};sensors.set(id,record);}
        if(signature!==record.signature){record.layer.setIcon(sensorIcon(sensor,status,mounted));record.signature=signature;}
        record.layer.setLatLng(xy(position.x,position.y));
        const location=mounted?` · на ${escape(definition.asset_id)}${isStale(asset,serverOffset,site.dispatch_config?.position_stale_seconds||5)?" · последнее известное место":""}`:"";
        record.layer.bindTooltip(`${escape(sensorTypeNames[definition.type]||"Датчик")} ${escape(id)}${location}<br>${sensorStatusNames[status]} · ${ageText(runtime.last_received_at)} · ${escape(runtime.last_received_at||"—")}<br>Связь и свежесть измерений проверяются отдельно`);
      }
      for(const [id,record] of sensors)if(!shownSensors.has(id)){map.removeLayer(record.layer);sensors.delete(id);}
      for(const [id,record] of buildings){const danger=nextIncidents.some(i=>i.building_id===id&&i.condition_active&&workable(i));record.layer.setStyle({...buildingStyle(record),weight:danger?3:1.5});}
      applySelection();scheduleMotion();syncFollow();
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
    function clearFollow(){followedAsset=null;followPosition=null;if(viewMode==="asset"||viewMode==="incident")viewMode="manual";}
    function syncFollow(){
      if(!followedAsset||destroyed)return;
      const record=assets.get(followedAsset),target=state.assets.find(a=>(a.asset_id||a.id)===followedAsset);
      if(!record||!validPoint(target)){clearFollow();return;}
      if(target.monitoring_paused||isStale(target,state.serverOffset,site.dispatch_config?.position_stale_seconds||5))return;
      const position=record.display;
      if(followPosition?.x===position.x&&followPosition?.y===position.y)return;
      settingView=true;
      try{map.panTo(xy(position.x,position.y),{animate:false,noMoveStart:true});followPosition={...position};}finally{settingView=false;}
    }
    function followAsset(id,{recenter=true}={}){
      const record=assets.get(id);
      if(!record){clearFollow();return false;}
      if(followedAsset===id)return true;
      clearFollow();clearSectorHighlight();focusedSectorId=null;
      if(recenter){settingView=true;try{map.stop?.();map.fitBounds([xy(record.display.x,record.display.y)],{padding:[48,48],maxZoom:3.75,animate:false});}finally{settingView=false;}}
      followedAsset=id;followPosition=recenter?{...record.display}:null;viewMode="asset";return true;
    }
    function selectAsset(id,{follow=true}={}){
      if(followedAsset!==id||!follow)clearFollow();
      selectedAsset=siteAssets.has(id)||assets.has(id)||personalRoutes.has(id)?id:null;selection=null;applySelection();
      if(follow&&selectedAsset)followAsset(id);
      return Boolean(selectedAsset);
    }
    function applySelection(){
      for(const [id,r] of assets){r.layer.getElement()?.classList.toggle("enterprise-selected",Boolean(selection&&(incidentAssets(selection).includes(id)||sensorDefinitions.get(selection.sensor_id)?.asset_id===id)));r.layer.getElement()?.classList.toggle("enterprise-object-selected",selectedAsset===id);}
      for(const [id,r] of sensors)r.layer.getElement()?.classList.toggle("enterprise-selected",Boolean(selection&&selection.sensor_id===id));
      drawPersonalRoutes();
      const rectangles=selection?selectedGeometry(selection):[],signature=JSON.stringify(rectangles);
      if(signature===geometrySignature)return;
      geometrySignature=signature;highlightLayer.clearLayers();
      for(const r of rectangles)Leaflet.rectangle(rectangleBounds(r),{pane:"enterpriseHighlight",className:"enterprise-incident-halo",color:"#ffd079",weight:3,fillOpacity:0,interactive:false}).addTo(highlightLayer);
    }
    function highlight(incident,options={}){const key=i=>i?.incident_id||[i?.type,...incidentAssets(i),i?.sensor_id,i?.building_id,i?.zone_id,i?.site_area_id].filter(Boolean).join(":");if(key(selection)!==key(incident)||!incident)clearFollow();selection=incident||null;selectedAsset=null;applySelection();if(options.recenter)return showIncident(incident);return selectionPoints(incident||{}).length>0;}
    function clearHighlight(){clearFollow();selection=null;selectedAsset=null;applySelection();}
    function clearSectorHighlight(){
      if(sectorHighlightTimer!==null)clearTimeout(sectorHighlightTimer);
      sectorHighlightTimer=null;sectorHighlightId=null;
      for(const record of buildings.values()){record.layer.getElement()?.classList.toggle("enterprise-sector-focus",false);record.label.getElement()?.classList.toggle("enterprise-sector-focus",false);}
    }
    function flashSector(sectorId){
      clearSectorHighlight();sectorHighlightId=sectorId;
      for(const record of buildings.values())if(record.control.sectorId===sectorId&&visibleOwner(record.control.operatorId)){record.layer.getElement()?.classList.toggle("enterprise-sector-focus",true);record.label.getElement()?.classList.toggle("enterprise-sector-focus",true);}
      sectorHighlightTimer=setTimeout(clearSectorHighlight,3000);
    }
    function fitAll({animate=true}={}){clearFollow();clearSectorHighlight();viewMode="all";focusedSectorId=null;settingView=true;try{map.stop?.();if(animate&&!reducedMotion&&map.flyToBounds)map.flyToBounds(frame,{padding:[24,24],duration:.85});else map.fitBounds(frame,{padding:[24,24],animate:false});}finally{settingView=false;}}
    function focusSector(sectorId,{animate=false,highlight=animate}={}){
      clearFollow();const rectangles=sectorRectangles(site,sectorId);
      if(!rectangles.length){fitAll();return false;}
      viewMode="sector";focusedSectorId=sectorId;settingView=true;
      try{
        map.stop?.();map.invalidateSize({pan:false});
        const bounds=rectangles.flatMap(rectangleBounds),options={padding:[18,18],maxZoom:3.5};
        if(animate&&!reducedMotion&&map.flyToBounds)map.flyToBounds(bounds,{...options,duration:0.85});
        else map.fitBounds(bounds,{...options,animate:false});
      }finally{settingView=false;}
      if(highlight)flashSector(sectorId);else if(sectorHighlightId!==sectorId)clearSectorHighlight();return true;
    }
    function showIncident(incident){
      highlight(incident);
      const identifiers=[...incidentAssets(incident),sensorDefinitions.get(incident?.sensor_id)?.asset_id].filter(Boolean);
      const targets=identifiers.map(id=>state.assets.find(a=>(a.asset_id||a.id)===id&&validPoint(a))).filter(Boolean),target=targets[0];
      const primary=target&&(target.asset_id||target.id),points=incident?.type==="collision"&&targets.length?targets.map(a=>xy(a.x,a.y)):target?[xy(target.x,target.y)]:selectionPoints(incident||{});
      if(!points.length)return false;
      clearFollow();clearSectorHighlight();viewMode="incident";focusedSectorId=null;settingView=true;try{map.stop?.();map.fitBounds(points,{padding:[48,48],maxZoom:3.75,animate:false});}finally{settingView=false;}followedAsset=primary||null;return true;
    }
    map.on("zoomend moveend",redrawGeometry);
    map.on("dragstart zoomstart",()=>{if(!settingView){clearFollow();viewMode="manual";focusedSectorId=null;}});
    const resize=typeof ResizeObserver!=="undefined"?new ResizeObserver(()=>{if(!destroyed){map.invalidateSize({pan:false});if(viewMode==="all")fitAll({animate:false});else if(viewMode==="sector")focusSector(focusedSectorId);redrawGeometry();}}):null;
    if(resize)resize.observe(map.getContainer());
    fitAll({animate:false});redrawGeometry();update(state);
    return {map,update,fitAll,focusSector,highlight,clearHighlight,showIncident,setRoutesVisible,selectAsset,followAsset,clearFollow,getFollowedAsset:()=>followedAsset,setOperator,destroy(){destroyed=true;clearFollow();clearSectorHighlight();if(animationFrame!==null&&typeof cancelAnimationFrame!=="undefined")cancelAnimationFrame(animationFrame);resize?.disconnect();map.off("zoomend moveend",redrawGeometry);map.remove();},getLayerCounts(){return {buildings:buildings.size,roads:roads.length,pedestrians:pedestrians.length,routes:personalRoutes.size,assets:assets.size,sensors:sensors.size,grid:grid.getLayers().length};}};
  }
  return {create,labelMetrics,buildingControl,estimatedLabelWidth,roadWidthPixels,isStale,interpolatePoint,sectorRectangles,zoneStyle,zonePolicyText,signalTime};
});
