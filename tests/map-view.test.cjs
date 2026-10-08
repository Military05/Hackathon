"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const api=require("../src/interface/web/map-view.js");
const site=JSON.parse(fs.readFileSync(path.join(__dirname,"../data/demo/site.json"),"utf8"));
function harness(options={}){
  const layers=[],fits=[],pans=[],handlers={};
  class Layer{
    constructor(points,options={}){this.points=points;this.options=options;this.iconWrites=0;this.styleWrites=0;this.classes=new Set();this.children=[];}
    addTo(target){this.target=target;if(target.children)target.children.push(this);layers.push(this);return this;}
    setStyle(value){this.styleWrites++;Object.assign(this.options,value);return this;}
    bindTooltip(value){this.tooltip=value;return this;}
    on(type,handler){this.handler=handler;return this;}
    setIcon(icon){this.options.icon=icon;this.iconWrites++;return this;}
    setLatLng(value){this.points=value;return this;}
    getLatLng(){return this.points;}
    getElement(){return {classList:{toggle:(key,value)=>value?this.classes.add(key):this.classes.delete(key)}};}
    clearLayers(){this.children=[];return this;}
    getLayers(){return this.children;}
  }
  const map={children:[],removed:[],createPane(){return {style:{}};},setMaxBounds(points){this.maximumBounds=points;},panTo(point,options){pans.push({point,options});handlers["zoomend moveend"]?.();return this;},stop(){},fitBounds(points,options){fits.push({points,options});return this;},flyToBounds(points,options){fits.push({points,options,animated:true});return this;},latLngToLayerPoint(p){return {x:p[1]*4,y:p[0]*4};},getBounds(){return {getWest:()=>-40,getEast:()=>140,getSouth:()=>-20,getNorth:()=>120};},on(events,fn){handlers[events]=fn;},off(){},remove(){},removeLayer(layer){this.removed.push(layer);},getContainer(){return {};},invalidateSize(){}};
  globalThis.L={CRS:{Simple:{}},map(id,options){map.options=options;return map;},layerGroup:()=>new Layer(),polyline:(points,options)=>new Layer(points,options),rectangle:(points,options)=>new Layer(points,options),marker:(points,options)=>new Layer(points,options),divIcon:options=>options};
  const callbacks={asset:[],sensor:[],building:[]};
  const instance=api.create({...options,site:options.site||site,onAsset:id=>callbacks.asset.push(id),onSensor:id=>callbacks.sensor.push(id),onBuilding:id=>callbacks.building.push(id)});
  return {instance,map,layers,fits,pans,handlers,callbacks};
}
const freshAsset={asset_id:"V1",vehicle_type:"forklift",x:39,y:42,last_seen:new Date().toISOString()};
test("labels shorten to stable codes when the projected building is too small",()=>{
  const b={id:"O1",name:"Центральный административный офис предприятия",short_name:"Офис"};
  assert.equal(api.labelMetrics(b,45,25).label,"Офис");
  assert.equal(api.labelMetrics(b,20,20).label,"O1");
  assert.equal(api.labelMetrics(b,70,40).label,"Офис");
  const large=api.labelMetrics(b,350,90);assert.equal(large.label,b.name);
});
test("road width scales with plan geometry, independent of screen zoom",()=>{
  assert.equal(api.roadWidthPixels(5,4),20);assert.equal(api.roadWidthPixels(5,8),40);
  const h=harness(),road=h.layers.find(l=>l.options.pane==="enterpriseRoads"&&l.options.color==="#293f4d");
  assert.equal(road.options.weight,site.roads[0].width*4);
  assert.deepEqual(road.points,site.roads[0].points.map(p=>[100-p[1],p[0]]));
});
test("grid covers the viewport outside the plan and remains bounded",()=>{
  const h=harness(),grid=h.layers.filter(l=>l.options.pane==="enterpriseGrid");
  assert.ok(grid.some(l=>l.points.some(p=>p[1]<0)));assert.ok(grid.some(l=>l.points.some(p=>p[1]>100)));
  assert.ok(h.instance.getLayerCounts().grid<=80);
});
test("map starts without attribution and sensor location is never invented",()=>{
  const h=harness();assert.equal(h.map.options.attributionControl,false);
  assert.equal(h.instance.getLayerCounts().sensors,site.sensors.filter(s=>s.position).length);
  h.instance.update({assets:[freshAsset],sensors:[{sensor_id:"POS-V1",status:"online",last_received_at:freshAsset.last_seen}]});
  assert.equal(h.instance.getLayerCounts().sensors,site.sensors.filter(s=>s.position).length+1);
  const mounted=h.layers.find(l=>l.options.icon?.className?.includes("mounted"));
  assert.deepEqual(mounted.points,[58,39]);assert.match(mounted.tooltip,/Датчик положения POS-V1/);
});
test("polling reuses transport and sensor markers and leaves map position unchanged",()=>{
  const h=harness(),count=h.fits.length;
  const data={assets:[freshAsset],sensors:[{sensor_id:"POS-V1",status:"online",last_received_at:freshAsset.last_seen}],incidents:[]};
  h.instance.update(data);const vehicle=h.layers.find(l=>l.options.icon?.className?.includes("vehicle-marker"));
  const markerCount=h.layers.filter(l=>l.options.icon?.className?.includes("vehicle-marker")||l.options.icon?.className?.includes("sensor-marker")).length;
  for(let i=0;i<10;i++)h.instance.update({...data,assets:[{...freshAsset,x:39+i/10}]});
  assert.equal(vehicle.iconWrites,0);assert.equal(h.layers.filter(l=>l.options.icon?.className?.includes("vehicle-marker")||l.options.icon?.className?.includes("sensor-marker")).length,markerCount);
  assert.equal(h.fits.length,count);
});
test("selection pulses its objects without recenter; only showIncident changes the view",()=>{
  const h=harness();h.instance.update({assets:[freshAsset],sensors:[],incidents:[]});
  const incident={incident_id:"I1",asset_id:"V1",zone_id:"Z1",status:"open"},count=h.fits.length;
  assert.equal(h.instance.highlight(incident,{recenter:false}),true);assert.equal(h.fits.length,count);
  const vehicle=h.layers.find(l=>l.options.icon?.className?.includes("vehicle-marker"));assert.ok(vehicle.classes.has("enterprise-selected"));
  assert.equal(h.instance.showIncident(incident),true);assert.equal(h.fits.length,count+1);
  h.instance.clearHighlight();assert.ok(!vehicle.classes.has("enterprise-selected"));
});
test("unknown vehicle keeps last known coordinates and explicitly says they are stale",()=>{
  const h=harness(),old={...freshAsset,last_seen:"2020-01-01T00:00:00Z"};h.instance.update({assets:[old],sensors:[],incidents:[]});
  const vehicle=h.layers.find(l=>l.options.icon?.className?.includes("vehicle-marker"));
  assert.deepEqual(vehicle.points,[58,39]);assert.match(vehicle.options.icon.className,/stale/);assert.match(vehicle.tooltip,/текущее место неизвестно/);
  const before=h.fits.length;assert.equal(h.instance.showIncident({asset_id:"missing",condition_state:"unknown"}),false);assert.equal(h.fits.length,before);
});
test("staleness uses server time and the configured threshold",()=>{
  assert.equal(api.isStale({last_seen:"2026-10-07T00:00:00Z"},0,5,Date.parse("2026-10-07T00:00:04Z")),false);
  assert.equal(api.isStale({last_seen:"2026-10-07T00:00:00Z"},1000,5,Date.parse("2026-10-07T00:00:04Z")),true);
  assert.equal(api.isStale({last_seen:"bad"}),true);
});
test("each dispatcher sector changes the bounds and checkpoint no longer opens the whole site",()=>{
  const h=harness(),initial=h.fits.at(-1).points,views=[];
  for(const sectorId of ["logistics","production","coordination"]){
    assert.equal(h.instance.focusSector(sectorId),true);
    const fit=h.fits.at(-1),box=site.sectors.find(s=>s.id===sectorId).focus_bounds;
    assert.deepEqual(fit.points,[[100-box.y-box.height,box.x],[100-box.y,box.x+box.width]]);
    assert.notDeepEqual(fit.points,initial);assert.deepEqual(fit.options.padding,[18,18]);assert.equal(fit.options.animate,false);views.push(fit.points);
  }
  assert.notDeepEqual(views[0],views[1]);assert.notDeepEqual(views[1],views[2]);assert.equal(h.map.options.zoomSnap,.1);
  assert.deepEqual(h.map.maximumBounds,[[-100,-100],[200,200]],"Wide panels must be allowed to center western and eastern sectors");
  assert.equal(h.instance.focusSector("missing"),false);assert.deepEqual(h.fits.at(-1).points,initial);
});
test("sector button flies to its bounds while reduced motion uses an immediate view",()=>{
  const original=globalThis.matchMedia;
  try{
    globalThis.matchMedia=()=>({matches:false});
    const h=harness();h.instance.focusSector("production",{animate:true});
    assert.equal(h.fits.at(-1).animated,true);assert.equal(h.fits.at(-1).options.duration,.85);h.instance.destroy();
    globalThis.matchMedia=()=>({matches:true});
    const reduced=harness();reduced.instance.focusSector("production",{animate:true});
    assert.equal(reduced.fits.at(-1).animated,undefined);assert.equal(reduced.fits.at(-1).options.animate,false);reduced.instance.destroy();
  }finally{if(original)globalThis.matchMedia=original;else delete globalThis.matchMedia;}
});

test("untrusted building names remain escaped inside constrained label markup",()=>{
  const copy={...site,buildings:[{id:"A",name:'<img src=x onerror="alert(1)">',rectangle:{x:1,y:1,width:80,height:50}}]};
  const h=harness();const renderer=api.create({site:copy});
  const labels=h.layers.filter(l=>l.options.icon?.className==="enterprise-label-marker"),markup=labels.at(-1).options.icon.html;
  assert.ok(!markup.includes("<img"));assert.match(markup,/&lt;img/);assert.match(markup,/width:\d+px;height:\d+px/);renderer.destroy();
});

test("transport uses abstract symbols and roads contain no lane markings",()=>{
  const h=harness();h.instance.update({assets:[freshAsset],sensors:[],incidents:[]});
  const vehicle=h.layers.find(l=>l.options.icon?.className?.includes("vehicle-marker"));
  assert.match(vehicle.options.icon.html,/enterprise-vehicle-symbol/);assert.doesNotMatch(vehicle.options.icon.html,/<svg/);
  assert.ok(h.layers.filter(l=>l.options.pane==="enterpriseRoads").every(l=>!l.options.dashArray));
});

test("motion interpolates only between received positions and moves the mounted sensor with the marker",()=>{
  const oldRequest=globalThis.requestAnimationFrame,oldCancel=globalThis.cancelAnimationFrame;
  let next=0;const frames=new Map();globalThis.requestAnimationFrame=fn=>{frames.set(++next,fn);return next;};globalThis.cancelAnimationFrame=id=>frames.delete(id);
  try{
    const h=harness(),data={assets:[freshAsset],sensors:[],incidents:[]};h.instance.update(data);const count=h.fits.length;
    h.instance.update({...data,assets:[{...freshAsset,x:43,last_seen:new Date(Date.now()+1).toISOString()}]});
    const now=performance.now(),[id,frame]=frames.entries().next().value;frames.delete(id);frame(now+375);
    const vehicle=h.layers.find(l=>l.options.icon?.className?.includes("vehicle-marker")),sensor=h.layers.find(l=>l.options.icon?.className?.includes("mounted"));
    assert.ok(vehicle.points[1]>39&&vehicle.points[1]<43);assert.deepEqual(sensor.points,vehicle.points);assert.equal(h.fits.length,count);
    const [endId,endFrame]=frames.entries().next().value;frames.delete(endId);endFrame(now+800);assert.deepEqual(vehicle.points,[58,43]);
    h.instance.destroy();assert.equal(frames.size,0);
  }finally{if(oldRequest)globalThis.requestAnimationFrame=oldRequest;else delete globalThis.requestAnimationFrame;if(oldCancel)globalThis.cancelAnimationFrame=oldCancel;else delete globalThis.cancelAnimationFrame;}
});

test("road fills follow all road borders so real junctions form one continuous surface",()=>{
  const h=harness(),layers=h.layers.filter(l=>l.options.pane==="enterpriseRoads"),firstFill=layers.findIndex(l=>l.options.color==="#293f4d");
  assert.equal(firstFill,site.roads.length);assert.ok(layers.slice(0,firstFill).every(l=>l.options.color==="#425462"));
  assert.ok(layers.slice(firstFill).every(l=>l.options.color==="#293f4d"));assert.equal(layers.length,site.roads.length*2);
});

test("permit and hard-prohibition zones have distinct semantics and sector outlines have no fill",()=>{
  const h=harness(),zones=h.layers.filter(l=>l.options.pane==="enterpriseZones"),forbidden=zones.find(l=>l.options.className==="enterprise-zone-forbidden"),permit=zones.find(l=>l.options.className==="enterprise-zone-permit");
  assert.ok(forbidden);assert.ok(permit);assert.match(forbidden.tooltip,/Въезд запрещён всем/);assert.match(permit.tooltip,/индивидуальному допуску/);
  assert.notEqual(forbidden.options.fillColor,permit.options.fillColor);
  const sectors=h.layers.filter(l=>l.options.pane==="enterpriseSectors");assert.ok(sectors.length>=3);assert.ok(sectors.every(l=>l.options.fill===false));
  assert.ok(zones.every(l=>!["#00bfff","#87ceeb","blue"].includes(l.options.fillColor)));
});

test("old site geometry falls back to actual sector areas and ignores invalid explicit bounds",()=>{
  const copy={...site,sectors:site.sectors.map(s=>({...s,focus_bounds:undefined}))};
  assert.ok(api.sectorRectangles(copy,"coordination").every(r=>r.x>=0&&r.width<100));
  const invalid={...copy,sectors:copy.sectors.map(s=>({...s,focus_bounds:{x:NaN,y:0,width:100,height:100}}))};
  assert.deepEqual(api.sectorRectangles(invalid,"logistics"),api.sectorRectangles(copy,"logistics"));assert.deepEqual(api.sectorRectangles(copy,"absent"),[]);
});

test("last received times are visible next to vehicle and stationary sensor without inventing a signal",()=>{
  const h=harness(),stamp="2026-10-07T10:12:13Z";
  h.instance.update({assets:[{...freshAsset,last_seen:stamp}],sensors:[{sensor_id:"ACCESS-G1",status:"online",last_received_at:stamp}],incidents:[]});
  const vehicle=h.layers.find(l=>l.options.icon?.className?.includes("vehicle-marker")),sensor=h.layers.find(l=>l.tooltip?.includes("ACCESS-G1"));
  assert.match(vehicle.options.icon.html,/enterprise-signal-time/);assert.ok(vehicle.options.icon.html.includes(api.signalTime(stamp)));
  assert.match(sensor.options.icon.html,/enterprise-sensor-time/);assert.ok(sensor.options.icon.html.includes(api.signalTime(stamp)));
  assert.equal(api.signalTime(null),"—");assert.equal(api.signalTime("invalid"),"—");
});

test("resizing retains the selected sector while a manual view remains untouched",()=>{
  const original=globalThis.ResizeObserver;let resize;
  globalThis.ResizeObserver=class{constructor(callback){resize=callback;}observe(){}disconnect(){}};
  try{
    const h=harness();h.instance.focusSector("coordination");const before=h.fits.at(-1).points,count=h.fits.length;resize();
    assert.equal(h.fits.length,count+1);assert.deepEqual(h.fits.at(-1).points,before);
    h.handlers["dragstart zoomstart"]();const manualCount=h.fits.length;resize();assert.equal(h.fits.length,manualCount);h.instance.destroy();
  }finally{if(original)globalThis.ResizeObserver=original;else delete globalThis.ResizeObserver;}
});

test("typed prohibitions are red and identify precisely which vehicle types are restricted",()=>{
  const h=harness(),red=h.layers.filter(l=>l.options.pane==="enterpriseZones"&&l.options.className==="enterprise-zone-forbidden");
  assert.equal(red.length,site.zones.filter(z=>["forbidden","restricted"].includes(z.kind)).length);
  assert.ok(red.some(l=>/погрузчикам/.test(l.tooltip)));assert.ok(red.some(l=>/служебному транспорту/.test(l.tooltip)));
  assert.deepEqual(api.zoneStyle({kind:"restricted"}),api.zoneStyle({kind:"forbidden"}));
  assert.match(api.zonePolicyText({kind:"restricted",restricted_vehicle_types:["forklift"]}),/погрузчикам/);
});

test("entrance roads end at the building wall with flat caps and walking paths stay separate",()=>{
  const h=harness(),roads=h.layers.filter(l=>l.options.pane==="enterpriseRoads"&&l.options.color==="#293f4d");
  site.roads.forEach((road,index)=>assert.equal(roads[index].options.lineCap,road.entrance_building_id?"butt":"round"));
  const walking=h.layers.filter(l=>l.options.pane==="enterprisePedestrians");assert.equal(walking.length,site.pedestrian_paths.length);
  assert.ok(walking.every(l=>l.options.dashArray&&l.options.interactive===false));
});

test("personal routes are subtle by default, selected independently, and toggle without rebuilding layers",()=>{
  const h=harness(),routes=h.layers.filter(l=>l.options.pane==="enterpriseRoutes"),count=h.layers.length,fitCount=h.fits.length;
  assert.equal(routes.length,3);assert.ok(routes.every(l=>l.options.opacity===.23&&l.options.dashArray));
  assert.deepEqual(routes[0].points,site.safety_routes.V1.map(p=>[100-p[1],p[0]]));
  assert.equal(h.instance.selectAsset("V2"),true);assert.equal(routes[1].options.opacity,.95);assert.equal(routes[0].options.opacity,.23);
  h.instance.setRoutesVisible(false);assert.ok(routes.every(l=>l.options.opacity===0));
  const endpoints=h.layers.filter(l=>l.options.icon?.className==="enterprise-route-destination-marker");assert.ok(endpoints.every(l=>l.options.icon.html===""));
  h.instance.setRoutesVisible(true);assert.equal(routes[1].options.opacity,.95);assert.ok(endpoints.some(l=>l.options.icon.html.includes("focused")));
  assert.equal(h.layers.length,count);assert.equal(h.fits.length,fitCount);
});

test("collision selection includes both received vehicle positions without framing the whole road area",()=>{
  const h=harness(),other={...freshAsset,asset_id:"V3",vehicle_type:"service_vehicle",x:40.8,y:74};
  const collision={type:"collision",asset_id:"V1",other_asset_id:"V3",site_area_id:"common-roads",status:"open",condition_active:true};
  h.instance.update({assets:[{...freshAsset,x:40,y:74},other],incidents:[collision]});h.instance.showIncident(collision);
  const vehicles=h.layers.filter(l=>l.options.icon?.className?.includes("vehicle-marker"));
  assert.equal(vehicles.length,2);assert.ok(vehicles.every(l=>l.classes.has("enterprise-selected")));assert.ok(vehicles.every(l=>l.options.icon.className.includes("alarm")));
  assert.deepEqual(h.fits.at(-1).points,[[26,40],[26,40.8]]);h.instance.clearHighlight();assert.ok(vehicles.every(l=>!l.classes.has("enterprise-selected")));
});

test("route deviation is warned on the affected vehicle and HH:MM signals avoid second-by-second icon churn",()=>{
  const h=harness(),incident={type:"route_deviation",asset_id:"V1",status:"open",condition_active:true};
  h.instance.update({assets:[freshAsset],incidents:[incident]});
  const vehicle=h.layers.find(l=>l.options.icon?.className?.includes("vehicle-marker"));assert.match(vehicle.options.icon.html,/enterprise-route-warning/);
  const route=h.layers.find(l=>l.options.pane==="enterpriseRoutes");assert.equal(route.options.color,"#79baff");assert.match(vehicle.options.icon.className,/alarm.*route-deviation/);
  assert.match(api.signalTime("2026-10-07T10:12:13Z"),/^\d{2}:\d{2}$/);
  assert.doesNotMatch(vehicle.tooltip,/сек\. назад/);assert.match(vehicle.tooltip,/только что/);
});

function fakeMotion(){
  const original={request:globalThis.requestAnimationFrame,cancel:globalThis.cancelAnimationFrame,performance:globalThis.performance,matchMedia:globalThis.matchMedia};
  const frames=new Map();let next=0,now=1000;
  globalThis.requestAnimationFrame=fn=>{frames.set(++next,fn);return next;};
  globalThis.cancelAnimationFrame=id=>frames.delete(id);
  globalThis.performance={now:()=>now};
  globalThis.matchMedia=()=>({matches:false});
  return {frames,step(time){now=time;const [id,fn]=frames.entries().next().value;frames.delete(id);fn(now);},restore(){for(const [key,value] of [["requestAnimationFrame",original.request],["cancelAnimationFrame",original.cancel],["performance",original.performance],["matchMedia",original.matchMedia]]){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}}};
}

test("clicking any received vehicle centers once and follows its rendered motion at no more than 15 fps",()=>{
  const motion=fakeMotion();let h;
  try{
    h=harness();const asset={...freshAsset,asset_id:"V99"};
    h.instance.update({assets:[asset]});
    const vehicle=h.layers.find(l=>l.options.icon?.className?.includes("vehicle-marker")),before=h.fits.length;
    vehicle.handler();
    assert.deepEqual(h.callbacks.asset,["V99"]);assert.equal(h.instance.getFollowedAsset(),"V99");
    assert.equal(h.fits.length,before+1);assert.deepEqual(h.fits.at(-1).points,[[58,39]]);
    h.instance.selectAsset("V99");assert.equal(h.fits.length,before+1,"Passive duplicate selection does not reframe the same target");
    h.instance.update({assets:[{...asset,x:43,last_seen:new Date(Date.now()+1).toISOString()}]});
    assert.equal(h.pans.length,0,"Polling starts interpolation without panning ahead to the raw endpoint");
    const pending=[...motion.frames.keys()];
    h.instance.update({assets:[{...asset,x:43,last_seen:new Date(Date.now()+2).toISOString()}]});
    assert.deepEqual([...motion.frames.keys()],pending,"A 1 Hz refresh does not cancel or restart the animation loop");
    const road=h.layers.find(l=>l.options.pane==="enterpriseRoads"),roadWrites=road.styleWrites;
    motion.step(1100);
    assert.deepEqual(h.pans.at(-1).point,vehicle.points);assert.ok(vehicle.points[1]>39&&vehicle.points[1]<43);
    assert.equal(h.pans.at(-1).options.animate,false,"Each frame pans immediately rather than restarting a Leaflet animation");
    const count=h.pans.length,position=[...vehicle.points];motion.step(1130);
    assert.equal(h.pans.length,count);assert.deepEqual(vehicle.points,position,"Frames less than 1/15 second apart do not repaint");
    motion.step(1180);assert.equal(h.pans.length,count+1);
    motion.step(1800);assert.deepEqual(h.pans.at(-1).point,[58,43]);assert.equal(motion.frames.size,0,"An idle map schedules no frames");
    assert.equal(road.styleWrites,roadWrites,"Following at the same zoom does not redraw static road geometry");
  }finally{h?.instance.destroy();motion.restore();}
});

test("vehicle selection replaces follow, while manual movement, other objects and map overview stop it",()=>{
  const h=harness(),other={...freshAsset,asset_id:"V3",x:60};
  h.instance.update({assets:[freshAsset,other]});
  const moving=()=>h.instance.selectAsset("V1");
  moving();h.instance.selectAsset("V3");assert.equal(h.instance.getFollowedAsset(),"V3");
  h.handlers["dragstart zoomstart"]();assert.equal(h.instance.getFollowedAsset(),null);
  const before=h.pans.length;h.instance.update({assets:[{...freshAsset,x:42},other]});assert.equal(h.pans.length,before);
  moving();h.layers.find(l=>l.options.pane==="enterpriseObjects").handler();assert.equal(h.instance.getFollowedAsset(),null);
  moving();h.layers.find(l=>l.options.icon?.className?.includes("sensor-marker")).handler();assert.equal(h.instance.getFollowedAsset(),null);
  moving();h.instance.focusSector("logistics");assert.equal(h.instance.getFollowedAsset(),null);
  moving();h.instance.fitAll({animate:false});assert.equal(h.instance.getFollowedAsset(),null);
  moving();h.instance.clearFollow();assert.equal(h.instance.getFollowedAsset(),null);
  moving();assert.equal(h.instance.followAsset("missing"),false);assert.equal(h.instance.getFollowedAsset(),null);
  assert.equal(h.instance.selectAsset("V2"),true);assert.equal(h.instance.getFollowedAsset(),null,"A registered object without received coordinates is never positioned");
  h.instance.destroy();
});

test("Show on map first frames both collision vehicles and preserves follow during the same incident refresh",()=>{
  const h=harness(),other={...freshAsset,asset_id:"V3",x:40.8,y:74};
  const incident={incident_id:"C1",type:"collision",asset_id:"V1",other_asset_id:"V3",status:"open"};
  h.instance.update({assets:[{...freshAsset,x:40,y:74},other]});
  assert.equal(h.instance.showIncident(incident),true);assert.deepEqual(h.fits.at(-1).points,[[26,40],[26,40.8]]);
  assert.equal(h.instance.getFollowedAsset(),"V1");
  h.instance.highlight({...incident,revision:2});assert.equal(h.instance.getFollowedAsset(),"V1");
  h.instance.update({assets:[{...freshAsset,x:41,y:74},other]});assert.deepEqual(h.pans.at(-1).point,[26,41]);
  h.instance.highlight({building_id:"W1"});assert.equal(h.instance.getFollowedAsset(),null);
  h.instance.showIncident(incident);h.instance.update({assets:[other]});assert.equal(h.instance.getFollowedAsset(),null,"A removed vehicle stops follow");
  h.instance.destroy();
});

test("reduced motion follows only received positions, and stale or paused sources do not move the camera",()=>{
  const original=globalThis.matchMedia,oldRequest=globalThis.requestAnimationFrame;let requested=0;
  globalThis.matchMedia=()=>({matches:true});globalThis.requestAnimationFrame=()=>{requested++;return 1;};
  const h=harness();
  try{
    h.instance.update({assets:[freshAsset]});h.instance.selectAsset("V1");
    h.instance.update({assets:[{...freshAsset,x:43}]});assert.deepEqual(h.pans.at(-1).point,[58,43]);assert.equal(requested,0);
    const count=h.pans.length;
    h.instance.update({assets:[{...freshAsset,x:47,monitoring_paused:true}]});assert.equal(h.pans.length,count);
    const vehicle=h.layers.find(l=>l.options.icon?.className?.includes("vehicle-marker"));assert.deepEqual(vehicle.points,[58,43],"Pause freezes the displayed position");
    h.instance.update({assets:[{...freshAsset,x:50,last_seen:"2020-01-01T00:00:00Z"}]});assert.equal(h.pans.length,count);
    h.instance.update({assets:[{...freshAsset,x:52}]});assert.deepEqual(h.pans.at(-1).point,[58,52]);
  }finally{h.instance.destroy();if(original)globalThis.matchMedia=original;else delete globalThis.matchMedia;if(oldRequest)globalThis.requestAnimationFrame=oldRequest;else delete globalThis.requestAnimationFrame;}
});

test("My sector briefly highlights only owned buildings, clears the effect, and keeps unrelated objects grey",()=>{
  const oldSet=globalThis.setTimeout,oldClear=globalThis.clearTimeout,oldMedia=globalThis.matchMedia;
  const timers=new Map();let next=0;
  globalThis.setTimeout=(fn,delay)=>{timers.set(++next,{fn,delay});return next;};
  globalThis.clearTimeout=id=>timers.delete(id);globalThis.matchMedia=()=>({matches:true});
  const h=harness({operator:"dispatcher-1"});
  try{
    const buildings=h.layers.filter(l=>l.options.pane==="enterpriseObjects");
    h.instance.focusSector("logistics");assert.ok(buildings.every(l=>!l.classes.has("enterprise-sector-focus")),"Startup sector focus does not flash");
    h.instance.focusSector("logistics",{animate:true});
    const own=site.buildings.filter(b=>api.buildingControl(site,b).operatorId==="dispatcher-1");
    assert.equal(buildings.filter(l=>l.classes.has("enterprise-sector-focus")).length,own.length);
    const foreign=buildings.filter(l=>!l.classes.has("enterprise-sector-focus"));assert.ok(foreign.length);assert.ok(foreign.every(l=>l.options.color==="#87929d"));
    h.instance.update({assets:[freshAsset]});assert.equal(buildings.filter(l=>l.classes.has("enterprise-sector-focus")).length,own.length,"Polling does not clear or restart the effect");
    assert.equal(timers.size,1);const timer=timers.values().next().value;assert.equal(timer.delay,3000);timer.fn();
    assert.equal(timers.size,0);assert.ok(buildings.every(l=>!l.classes.has("enterprise-sector-focus")));
    h.instance.focusSector("logistics",{highlight:true});assert.equal(timers.size,1);h.instance.destroy();assert.equal(timers.size,0);
    const css=fs.readFileSync(path.join(__dirname,"../src/interface/web/map-view.css"),"utf8");
    assert.match(css,/enterprise-sector-emphasis 3s ease-out/);assert.doesNotMatch(css,/enterprise-sector-emphasis[^}]*infinite/);
  }finally{h.instance.destroy();globalThis.setTimeout=oldSet;globalThis.clearTimeout=oldClear;if(oldMedia)globalThis.matchMedia=oldMedia;else delete globalThis.matchMedia;}
});

test("pausing during interpolation freezes the machine and follow camera and ends the frame loop",()=>{
  const motion=fakeMotion();let h;
  try{
    h=harness();h.instance.update({assets:[freshAsset]});h.instance.selectAsset("V1");
    const endpoint={...freshAsset,x:43,last_seen:new Date(Date.now()+1).toISOString()};
    h.instance.update({assets:[endpoint]});motion.step(1300);
    const vehicle=h.layers.find(l=>l.options.icon?.className?.includes("vehicle-marker")),position=[...vehicle.points],pans=h.pans.length;
    h.instance.update({assets:[{...endpoint,monitoring_paused:true}]});
    assert.deepEqual(vehicle.points,position);assert.equal(h.pans.length,pans);
    motion.step(1400);assert.deepEqual(vehicle.points,position);assert.equal(h.pans.length,pans);assert.equal(motion.frames.size,0);
  }finally{h?.instance.destroy();motion.restore();}
});
