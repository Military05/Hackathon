"use strict";
const test=require("node:test"),assert=require("node:assert/strict");
const Diagnostics=require("../src/interface/web/sensors-panel.js");
const now=Date.parse("2026-10-07T12:00:20Z"),at=seconds=>new Date(now-seconds*1000).toISOString();
const position=overrides=>({sensor_id:"POS-V1",type:"position",asset_id:"V1",status:"online",last_received_at:at(1),last_heartbeat_at:at(1),last_measurement_at:at(1),threshold_seconds:5,...overrides});
function fixture(options={}){let tick,delay,cancelled=false;const panel=Diagnostics.create({document:null,serverTime:()=>now,getSensors:()=>[position()],setInterval:(fn,ms)=>{tick=fn;delay=ms;return 71;},clearInterval:id=>{assert.equal(id,71);cancelled=true;},...options});return {panel,tick:()=>tick(),delay:()=>delay,cancelled:()=>cancelled};}

test("a recent heartbeat confirms communication but never freshens position",()=>{
  const result=Diagnostics.classify(position({last_measurement_at:at(30)}),now,5);
  assert.equal(result.communication,"online");assert.equal(result.freshness,"stale");assert.equal(result.attention,true);assert.match(result.cause,/измерение позиции устарело/);
});
test("an access reader can stay healthy while no access event happens",()=>{
  const result=Diagnostics.classify(position({type:"access",last_measurement_at:at(600)}),now,5);
  assert.equal(result.freshness,"event");assert.equal(result.attention,false);assert.match(result.cause,/при событиях/);
});
test("no first packet differs from a previously connected offline reader",()=>{
  const never=Diagnostics.classify(position({status:"offline",last_received_at:null,last_measurement_at:null}),now);
  const lost=Diagnostics.classify(position({status:"offline",last_received_at:at(10)}),now);
  assert.match(never.cause,/Первый пакет/);assert.match(lost.cause,/Нет новых пакетов/);assert.equal(never.age,null);assert.equal(lost.age,10);
});
test("server clock and configured position threshold decide freshness",()=>{
  assert.equal(Diagnostics.classify(position({last_measurement_at:at(5)}),now,5).freshness,"stale");
  assert.equal(Diagnostics.classify(position({last_measurement_at:at(5)}),now,8).freshness,"fresh");
  assert.equal(Diagnostics.classify(position({last_measurement_at:at(5)}),now-4000,5).freshness,"fresh");
});
test("a stale server snapshot cannot certify connection after polling stops",()=>{
  const result=Diagnostics.classify(position({as_of:at(8)}),now,5);
  assert.equal(result.communication,"unknown");assert.equal(result.snapshotStale,true);assert.match(result.cause,/Нет свежего ответа сервера/);
  assert.equal(Diagnostics.classify(position({as_of:at(1)}),now,5).communication,"online");
});
test("initial data and automatic checks read snapshots, without requests or sound by default",()=>{
  let requests=0,sounds=0;const f=fixture({requestCheck:()=>{requests++;return [position()];},onSound:()=>sounds++});
  f.panel.update([position()]);assert.equal(f.panel.getState().history.length,1);
  f.tick();assert.equal(f.delay(),30000);assert.equal(requests,0);assert.equal(sounds,0);assert.equal(f.panel.getState().lastInspection.mode,"auto");f.panel.destroy();assert.equal(f.cancelled(),true);
});
test("manual check makes exactly one supplied read and does not alter source timestamps",async()=>{
  let requests=0;const source=position();const f=fixture({requestCheck:async()=>{requests++;return {sensors:[source]};}});
  const before=JSON.stringify(source),summary=await f.panel.manualCheck();
  assert.equal(requests,1);assert.equal(summary.online,1);assert.equal(JSON.stringify(source),before);assert.equal(f.panel.getState().lastInspection.mode,"manual");assert.equal(f.panel.getState().pending,false);f.panel.destroy();
});
test("two manual clicks share one request and an automatic tick does not overlap it",async()=>{
  let resolve,requests=0;const f=fixture({requestCheck:()=>{requests++;return new Promise(done=>resolve=done);}});
  const first=f.panel.manualCheck(),second=f.panel.manualCheck();assert.equal(first,second);
  await Promise.resolve();assert.equal(requests,1);assert.equal(f.tick(),null);resolve([position()]);await first;assert.equal(f.panel.getState().history.filter(h=>h.kind==="manual").length,1);f.panel.destroy();
});
test("a failed manual read is visible and does not invent a healthy state",async()=>{
  const f=fixture({requestCheck:async()=>{throw Error("HTTP 503");}});f.panel.update([position({status:"offline",last_received_at:at(12)})]);
  assert.equal(await f.panel.manualCheck(),null);assert.match(f.panel.getState().lastError,/HTTP 503/);assert.equal(f.panel.getState().sensors[0].status,"offline");assert.equal(f.panel.getState().history.at(-1).kind,"error");f.panel.destroy();
});
test("automatic checks can be turned off and never emit removed chimes",()=>{
  let sounds=0;const f=fixture({onSound:(summary,mode)=>{assert.equal(mode,"auto");assert.equal(summary.total,1);sounds++;}});
  f.panel.update([position()]);f.panel.setAutomatic(false);f.tick();assert.equal(f.panel.getState().history.length,1);
  f.panel.setAutomatic(true);f.tick();assert.equal(f.panel.getState().history.length,2);assert.equal(sounds,0);f.tick();assert.equal(sounds,0);assert.equal(f.panel.setChime,undefined);f.panel.destroy();
});
test("observations are bounded to 20 and recovery does not change server data",()=>{
  const f=fixture();f.panel.update([position({status:"offline",last_received_at:at(15)})]);f.panel.update([position()]);
  assert.equal(f.panel.getState().history.at(-1).kind,"transition");
  for(let n=0;n<35;n++)f.tick();assert.equal(f.panel.getState().history.length,20);assert.equal(f.panel.getState().sensors[0].last_received_at,at(1));f.panel.destroy();
});
test("destroy stops checking and ignores a late response",async()=>{
  let resolve;const f=fixture({requestCheck:()=>new Promise(done=>resolve=done)});const work=f.panel.manualCheck();await Promise.resolve();f.panel.destroy();resolve([position()]);
  assert.equal(await work,null);assert.equal(f.panel.getState().sensors.length,0);assert.equal(f.tick(),null);assert.equal(f.cancelled(),true);
});
