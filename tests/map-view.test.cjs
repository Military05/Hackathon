"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const api=require("../src/interface/web/map-view.js");
const site=JSON.parse(fs.readFileSync(path.join(__dirname,"../data/demo/site.json"),"utf8"));
function harness(){
  const layers=[],fits=[],handlers={};
  class Layer{
    constructor(points,options={}){this.points=points;this.options=options;this.iconWrites=0;this.classes=new Set();this.children=[];}
    addTo(target){this.target=target;if(target.children)target.children.push(this);layers.push(this);return this;}
    setStyle(value){Object.assign(this.options,value);return this;}
    bindTooltip(value){this.tooltip=value;return this;}
    on(type,handler){this.handler=handler;return this;}
    setIcon(icon){this.options.icon=icon;this.iconWrites++;return this;}
    setLatLng(value){this.points=value;return this;}
    getLatLng(){return this.points;}
    getElement(){return {classList:{toggle:(key,value)=>value?this.classes.add(key):this.classes.delete(key)}};}
    clearLayers(){this.children=[];return this;}
    getLayers(){return this.children;}
  }
  const map={children:[],removed:[],createPane(){return {style:{}};},setMaxBounds(){},fitBounds(points,options){fits.push({points,options});return this;},latLngToLayerPoint(p){return {x:p[1]*4,y:p[0]*4};},getBounds(){return {getWest:()=>-40,getEast:()=>140,getSouth:()=>-20,getNorth:()=>120};},on(events,fn){handlers[events]=fn;},off(){},remove(){},removeLayer(layer){this.removed.push(layer);},getContainer(){return {};},invalidateSize(){}};
  globalThis.L={CRS:{Simple:{}},map(id,options){map.options=options;return map;},layerGroup:()=>new Layer(),polyline:(points,options)=>new Layer(points,options),rectangle:(points,options)=>new Layer(points,options),marker:(points,options)=>new Layer(points,options),divIcon:options=>options};
  const callbacks={asset:[],sensor:[],building:[]};
  const instance=api.create({site,onAsset:id=>callbacks.asset.push(id),onSensor:id=>callbacks.sensor.push(id),onBuilding:id=>callbacks.building.push(id)});
  return {instance,map,layers,fits,handlers,callbacks};
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
test("sector view uses assigned areas and coordination has the complete plan",()=>{
  const h=harness();h.instance.focusSector("logistics");const sector=h.fits.at(-1);
  assert.ok(sector.points.every(p=>p[1]<=38));h.instance.focusSector("coordination");assert.deepEqual(h.fits.at(-1).points,[[0,0],[100,100]]);
});
test("untrusted building names remain escaped inside constrained label markup",()=>{
  const copy={...site,buildings:[{id:"A",name:'<img src=x onerror="alert(1)">',rectangle:{x:1,y:1,width:80,height:50}}]};
  const h=harness();const renderer=api.create({site:copy});
  const labels=h.layers.filter(l=>l.options.icon?.className==="enterprise-label-marker"),markup=labels.at(-1).options.icon.html;
  assert.ok(!markup.includes("<img"));assert.match(markup,/&lt;img/);assert.match(markup,/width:\d+px;height:\d+px/);renderer.destroy();
});
