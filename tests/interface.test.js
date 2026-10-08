"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const {webcrypto}=require("node:crypto");
const source=fs.readFileSync(path.join(__dirname,"../src/interface/web/app.js"),"utf8");

test("failed stale job describes changed input without presenting an outdated result",async()=>{
  const h=harness(async()=>({job_id:"J1",incident_id:"I1",status:"failed",stale:true,error:{message:"JSON incomplete"}}));
  h.ui.S.selected="I1";h.ui.S.job="J1";
  await h.ui.renderJob();
  assert.match(h.$("analysis").innerHTML,/Данные изменились после начала запроса/);
  assert.doesNotMatch(h.$("analysis").innerHTML,/УСТАРЕЛ/);
  h.setFetch(async()=>({job_id:"J1",incident_id:"I1",status:"completed",stale:true,result:{summary:"verified"}}));
  await h.ui.renderJob();
  assert.match(h.$("analysis").innerHTML,/УСТАРЕЛ/);
});

function harness(handler=async()=>({})){ 
  const nodes=new Map();
  class Element{
    constructor(id){this.id=id;this.value="";this.checked=false;this.hidden=false;this.disabled=false;this.dataset={};this.style={};this.textContent="";this.options=[];this.writes=0;this._html="";}
    get innerHTML(){return this._html;}
    set innerHTML(value){
      this._html=value;this.writes++;
      for(const match of value.matchAll(/id="([^"]+)"/g))nodes.set(match[1],new Element(match[1]));
      if(this.id==="recipient")this.options=[...value.matchAll(/<option value="([^"]+)">([^<]*)<\/option>/g)].map(m=>({value:m[1],textContent:m[2],disabled:false}));
    }
    querySelectorAll(){return [];}
  }
  const document={getElementById(id){if(!nodes.has(id))nodes.set(id,new Element(id));return nodes.get(id);}};
  let fetchHandler=handler;
  const calls=[];
  const context=vm.createContext({document,crypto:webcrypto,Date,URLSearchParams,URL,AbortController,setTimeout:()=>1,clearTimeout:()=>{},setInterval:()=>1,fetch:async(url,options)=>{calls.push({url,options,body:options.body?JSON.parse(options.body):undefined});const value=await fetchHandler(url,options);return value?.__response?value:{ok:true,status:200,json:async()=>value};}});
  vm.runInContext(source+"\nglobalThis.ui={S,api,command,applyDetails,renderDetails,renderJob,startAnalysis,notifications,sendPresence,switchOperator,recoveryAllowed,renderIncidents,renderSummary,compareIncidents,showAsset,initialOperator,focusSector,selectIncident,showSelectedOnMap,entityName,placeName};",context);
  context.ui.S.site={dispatch_config:{position_stale_seconds:5},site_areas:[]};
  context.ui.S.map={fitBounds(){}};
  context.ui.S.profiles=[{operator_id:"dispatcher-1",name:"Склады",sector_id:"logistics",operator_ready:true},{operator_id:"dispatcher-2",name:"Цех",sector_id:"production",operator_ready:true},{operator_id:"dispatcher-3",name:"Координатор",sector_id:"coordination",operator_ready:true}];
  return {ui:context.ui,$:document.getElementById,calls,setFetch(fn){fetchHandler=fn;}};
}
function incident(extra={}){return {incident_id:"I1",type:"forbidden_zone",severity:"critical",detected_at:"2026-10-07T09:00:00Z",status:"open",condition_active:true,condition_state:"active",dispatch_revision:7,assigned_operator_id:null,site_area_id:"warehouse-raw",asset_id:"V1",pending_transfer:null,escalation_level:0,evidence_event_ids:[],history:[],response_plan:{contact:"Служба безопасности",steps:["Принять случай","Проверить источник"]},...extra};}
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
function errorResponse(status,body){return {__response:true,ok:false,status,json:async()=>body};}

function readableJob(extra={}){
  return {job_id:"J1",incident_id:"I1",status:"completed",stale:false,result:{
    presentation:{version:1,title:"Въезд в зону с ограничением",description:"Погрузчик 1. Закрытая погрузочная зона.",
      as_of:"2026-10-07T09:00:00Z",state:{text:"Условие происшествия наблюдалось."},
      observations:["Координаты заданы в условных единицах плана."],recommendations:["Уточните допуск к зоне."]},
    technical:{verified_facts:[{id:"long-event-id-123",field:"payload.y",value:27.0}],
      model_answer:{recommendations:["Непроверенное требование объявить пожар."]}}},...extra};
}

test("readable analysis hides raw facts and model prose inside closed technical disclosure",async()=>{
  const h=harness(async()=>readableJob());h.ui.S.selected="I1";h.ui.S.job="J1";
  await h.ui.renderJob();const html=h.$("analysis").innerHTML,[main,technical]=html.split("<details");
  assert.match(main,/Погрузчик 1/);assert.match(main,/Закрытая погрузочная зона/);
  assert.match(main,/Состояние на момент анализа/);assert.match(main,/Актуальность результата/);
  assert.match(main,/Уточните допуск/);assert.doesNotMatch(main,/long-event-id|payload.y|пожар/);
  assert.match(technical,/Технические данные/);assert.match(technical,/long-event-id-123/);
  assert.match(technical,/payload.y/);assert.match(technical,/27/);assert.match(technical,/пожар/);
  assert.doesNotMatch(technical.split(">")[0],/\bopen\b/);
});

test("stale analysis keeps captured state and names even when current card disagrees",async()=>{
  const h=harness(async()=>readableJob({stale:true}));h.ui.S.selected="I1";h.ui.S.job="J1";
  h.ui.S.detail=incident({condition_state:"restored",condition_active:false});
  h.ui.S.site.assets=[{id:"V1",name:"Новое имя объекта"}];
  await h.ui.renderJob();const main=h.$("analysis").innerHTML.split("<details")[0];
  assert.match(main,/УСТАРЕЛ/);assert.match(main,/Состояние объекта сейчас может отличаться/);
  assert.match(main,/analysis-freshness-stale/);assert.match(main,/Анализ устарел — данные изменились/);
  assert.doesNotMatch(main,/analysis-freshness-current/);
  assert.match(main,/Условие происшествия наблюдалось/);assert.match(main,/Погрузчик 1/);
  assert.doesNotMatch(main,/Новое имя|вышел|восстановлен/);
});

test("fresh analysis explains that snapshot is not current position confirmation",async()=>{
  const h=harness(async()=>readableJob());h.ui.S.selected="I1";h.ui.S.job="J1";
  await h.ui.renderJob();assert.match(h.$("analysis").innerHTML,/не подтверждение текущего положения/);
  assert.match(h.$("analysis").innerHTML,/Время среза:/);
  assert.match(h.$("analysis").innerHTML,/analysis-freshness-current/);
  assert.doesNotMatch(h.$("analysis").innerHTML,/analysis-freshness-stale/);
});

test("empty observations do not create a filler section",async()=>{
  const job=readableJob();job.result.presentation.observations=[];
  const h=harness(async()=>job);h.ui.S.selected="I1";h.ui.S.job="J1";await h.ui.renderJob();
  assert.doesNotMatch(h.$("analysis").innerHTML.split("<details")[0],/Наблюдения|привязку/);
});

test("MLP numbers stay readable without percentages or invented accident probability",async()=>{
  const job=readableJob();job.result.presentation.title="Необычное движение";
  job.result.presentation.observations=["Оценка необычности движения: ≈ 0,999876.","Порог срабатывания модели: ≈ 0,151226.","Это не вероятность аварии."];
  const h=harness(async()=>job);h.ui.S.selected="I1";h.ui.S.job="J1";await h.ui.renderJob();
  const main=h.$("analysis").innerHTML.split("<details")[0];
  assert.match(main,/0,999876/);assert.match(main,/0,151226/);assert.match(main,/не вероятность аварии/);
  assert.doesNotMatch(main,/%|payload/);
});

test("missing presentation and missing snapshot time have safe explicit fallbacks",async()=>{
  const h=harness(async()=>readableJob({result:{summary:"payload.y long-event-id"}}));
  h.ui.S.selected="I1";h.ui.S.job="J1";await h.ui.renderJob();
  assert.match(h.$("analysis").innerHTML,/понятное описание недоступно/);
  assert.doesNotMatch(h.$("analysis").innerHTML.split("<details")[0],/payload.y|long-event-id/);
  const job=readableJob();delete job.result.presentation.as_of;delete job.result.presentation.state;
  h.setFetch(async()=>job);await h.ui.renderJob();
  assert.match(h.$("analysis").innerHTML,/Время среза: не указано/);
  assert.match(h.$("analysis").innerHTML,/Состояние не указано/);
});

test("MLP display separates suspicion from cause and escapes all output",async()=>{
  const job=readableJob();job.result.presentation={version:1,title:"Необычное движение",description:"Погрузчик 1",
    state:{text:"Состояние объекта не подтверждено данными."},observations:["Порог модели превышен. Причина движения не подтверждена."],
    recommendations:["<script>alert(1)</script>"]};job.result.technical.raw="<img src=x onerror=alert(1)>";
  const h=harness(async()=>job);h.ui.S.selected="I1";h.ui.S.job="J1";await h.ui.renderJob();
  const html=h.$("analysis").innerHTML;
  assert.match(html,/Необычное движение/);assert.match(html,/Причина движения не подтверждена/);
  assert.doesNotMatch(html,/<script>|<img /);assert.match(html,/&lt;script&gt;/);assert.match(html,/&lt;img/);
});

test("PATCH uses the visible revision and never fetches a newer revision before acting",async()=>{
  const h=harness(async()=>incident({dispatch_revision:8,assigned_operator_id:"dispatcher-1"}));
  h.ui.S.selected="I1";h.ui.applyDetails(incident());h.ui.S.busy=true;
  await h.ui.command("claim");
  assert.equal(h.calls.length,1);assert.equal(h.calls[0].options.method,"PATCH");assert.equal(h.calls[0].body.expected_revision,7);assert.equal(h.ui.S.detail.dispatch_revision,8);
});

test("409 shows the changed card, preserves the draft and requires a new user action",async()=>{
  const changed=incident({dispatch_revision:8,assigned_operator_id:"dispatcher-2"});
  const h=harness(async()=>errorResponse(409,{code:"revision_conflict",message:"changed",details:{incident:changed}}));
  h.ui.S.selected="I1";h.ui.applyDetails(incident());h.$("reason").value="Проверка начата";
  await assert.rejects(h.ui.command("claim"),e=>e.code==="revision_conflict"&&e.message.includes("другого оператора"));
  assert.equal(h.calls.length,1);assert.equal(h.ui.S.detail.dispatch_revision,8);assert.equal(h.$("reason").value,"Проверка начата");
});

test("polling changes facts without replacing the reason or recipient elements",()=>{
  const h=harness();h.ui.S.selected="I1";h.ui.applyDetails(incident({assigned_operator_id:"dispatcher-1"}));
  const reason=h.$("reason"),recipient=h.$("recipient"),shell=h.$("details").writes;
  reason.value="Не терять ввод";recipient.value="dispatcher-2";
  h.ui.applyDetails(incident({assigned_operator_id:"dispatcher-1",dispatch_revision:8,as_of:"new timestamp",evidence_event_ids:["EV2"]}));
  assert.equal(h.$("reason"),reason);assert.equal(h.$("recipient"),recipient);assert.equal(reason.value,"Не терять ввод");assert.equal(recipient.value,"dispatcher-2");assert.equal(h.$("details").writes,shell);assert.match(h.$("detail-technical").innerHTML,/ревизия 8/);
});

test("late incident requests cannot replace the newly selected card",async()=>{
  const d=deferred(),h=harness(()=>d.promise);h.ui.S.selected="I1";
  const pending=h.ui.renderDetails();h.ui.S.selected="I2";h.ui.applyDetails(incident({incident_id:"I2"}));
  d.resolve(incident());await pending;
  assert.equal(h.ui.S.detail.incident_id,"I2");assert.equal(h.$("selected-id").textContent,"I2");
});

test("late GET from before a PATCH cannot roll the visible revision backwards",async()=>{
  const oldGet=deferred(),h=harness(async(url,options)=>options.method==="GET"?oldGet.promise:incident({dispatch_revision:8,assigned_operator_id:"dispatcher-1"}));
  h.ui.S.selected="I1";h.ui.applyDetails(incident());h.ui.S.busy=true;
  const pending=h.ui.renderDetails();await h.ui.command("claim");oldGet.resolve(incident());await pending;
  assert.equal(h.ui.S.detail.dispatch_revision,8);assert.equal(h.ui.S.detail.assigned_operator_id,"dispatcher-1");
});

test("analysis submission and job results are guarded by selected incident and job",async()=>{
  const d=deferred(),h=harness(()=>d.promise);h.ui.S.selected="I1";
  const submission=h.ui.startAnalysis();h.ui.S.selected="I2";h.ui.S.job=null;d.resolve({job_id:"J1",incident_id:"I1"});await submission;
  assert.equal(h.ui.S.job,null);
  const d2=deferred();h.setFetch(()=>d2.promise);h.ui.S.job="J2";h.$("analysis").innerHTML="new card";
  const job=h.ui.renderJob();h.ui.S.job="J3";d2.resolve({job_id:"J2",incident_id:"I2",status:"completed",result:{summary:"old"}});await job;
  assert.equal(h.$("analysis").innerHTML,"new card");
});

test("profile switch serialises prior ready heartbeat, away old session and ready new session",async()=>{
  const first=deferred();let count=0;
  const h=harness(async url=>url.includes("operator-presence")&&++count===1?first.promise:url.includes("dispatch-notifications")?{notifications:[],next_seq:0}:{});
  h.$("ready").checked=true;h.ui.S.busy=true;const oldSession=h.ui.S.session;
  const heartbeat=h.ui.sendPresence("dispatcher-1",oldSession,"ready");await Promise.resolve();
  const switched=h.ui.switchOperator("dispatcher-2");await Promise.resolve();
  assert.equal(h.calls.length,1);first.resolve({});await heartbeat;await switched;
  const leases=h.calls.filter(c=>c.url.includes("operator-presence"));
  assert.deepEqual(leases.map(c=>[c.options.headers["X-Demo-Operator"],c.body.availability]),[["dispatcher-1","ready"],["dispatcher-1","away"],["dispatcher-2","ready"]]);
  assert.equal(leases[1].body.session_id,oldSession);assert.notEqual(leases[2].body.session_id,oldSession);assert.equal(h.ui.S.operator,"dispatcher-2");
});

test("all initial notification pages are drained quietly before live notifications",async()=>{
  const h=harness(async url=>{const after=Number(new URL(url,"http://local").searchParams.get("after_seq"));const amount=after<200?100:50;return {notifications:Array.from({length:amount},(_,i)=>({seq:after+i+1,kind:"new_incident",incident_id:"I1"})),next_seq:after+amount};});
  let sounds=0;h.ui.S.sound=true;h.ui.S.audio={createOscillator(){sounds++;return {frequency:{},connect(){},start(){},stop(){}};},createGain(){return {gain:{},connect(){}};},currentTime:0};
  await h.ui.notifications(true);
  assert.equal(h.calls.length,3);assert.equal(h.ui.S.cursor,250);assert.equal(sounds,0);assert.equal(h.$("notifications").textContent,"");
  h.setFetch(async()=>({notifications:[{seq:251,kind:"transfer_requested",incident_id:"I2"}],next_seq:251}));await h.ui.notifications();
  assert.equal(sounds,1);assert.match(h.$("notifications").textContent,/I2/);
});

test("old profile notification replies cannot advance the new profile cursor",async()=>{
  const d=deferred(),h=harness(()=>d.promise);const pending=h.ui.notifications(true);
  h.ui.S.context++;h.ui.S.cursor=0;d.resolve({notifications:[{seq:900}],next_seq:900});await pending;
  assert.equal(h.ui.S.cursor,0);
});

test("working incidents sort by severity, escalation, unclaimed, then oldest",()=>{
  const h=harness(),rows=[incident({incident_id:"closed",status:"closed"}),incident({incident_id:"warning",severity:"warning",escalation_level:5}),incident({incident_id:"critical-new",detected_at:"2026-10-07T10:00:00Z"}),incident({incident_id:"critical-old"}),incident({incident_id:"escalated",escalation_level:1})];
  rows.sort(h.ui.compareIncidents);assert.deepEqual(rows.map(i=>i.incident_id),["escalated","critical-old","critical-new","warning","closed"]);
});

test("dismissed signals remain inspectable in history and show the rejection reason",()=>{
  const h=harness(),dismissed=incident({type:"model_anomaly",disposition:"rejected_model_signal",disposition_reason:"Штатная операция",reviewed_at:"2026-10-07T10:00:00Z"});
  h.ui.S.incidents=[dismissed];h.ui.renderIncidents();assert.doesNotMatch(h.$("incidents").innerHTML,/data-id=/);
  h.$("history-toggle").checked=true;h.ui.renderIncidents();assert.match(h.$("incidents").innerHTML,/Подозрение отклонено/);
  h.ui.S.selected="I1";h.ui.applyDetails(dismissed);assert.match(h.$("detail-history").innerHTML,/Штатная операция/);assert.match(h.$("response-plan").innerHTML,/Служба безопасности/);assert.match(h.$("response-plan").innerHTML,/Проверить источник/);
});

test("summary displays escalation and unknown place counts",()=>{
  const h=harness();h.ui.S.summary={sectors:[{sector_id:"logistics",active_count:2,unclaimed_count:1,escalated_count:3,operator_ready:false,operator_online:true}],unknown_count:4};h.ui.renderSummary();
  assert.match(h.$("summary").innerHTML,/3 эскалировано/);assert.match(h.$("summary").innerHTML,/не определено: <strong>4/);assert.match(h.$("summary").innerHTML,/оператор отсутствует/);
});

test("unconnected agent is shown honestly and cannot submit analysis",async()=>{
  const h=harness();h.ui.S.agentAvailable=false;h.ui.S.selected="I1";h.ui.applyDetails(incident());
  assert.match(h.$("detail-actions").innerHTML,/ИИ ещё не подключён/);assert.match(h.$("analysis").innerHTML,/пока не подключён/);
  await h.ui.startAnalysis();assert.equal(h.calls.length,0);
});

test("only first ready reserve can request recovery of an unavailable owner",()=>{
  const h=harness(),i=incident({assigned_operator_id:"dispatcher-1"});h.ui.S.profiles[0].operator_ready=false;
  h.ui.S.operator="dispatcher-2";assert.equal(h.ui.recoveryAllowed(i),false);
  h.ui.S.operator="dispatcher-3";assert.equal(h.ui.recoveryAllowed(i),true);
  h.ui.S.selected="I1";h.ui.applyDetails(i);assert.match(h.$("detail-actions").innerHTML,/id="reassign"/);
  h.ui.S.profiles[0].operator_ready=true;assert.equal(h.ui.recoveryAllowed(i),false);
});

test("asset click reads current coordinates and current freshness",()=>{
  const h=harness();h.ui.S.assets=[{asset_id:"V1",x:1,y:2,last_seen:new Date().toISOString()}];h.ui.showAsset("V1");assert.doesNotMatch(h.$("asset-info").textContent,/не подтверждено/);
  h.ui.S.assets=[{asset_id:"V1",x:10,y:20,last_seen:new Date(Date.now()-10000).toISOString()}];h.ui.showAsset("V1");assert.match(h.$("asset-info").textContent,/10.0, 20.0/);assert.match(h.$("asset-info").textContent,/не подтверждено/);
});

test("profile links accept valid operator values and sector focus is bounded by plan coordinates",()=>{
  const h=harness();assert.equal(h.ui.initialOperator("?operator=dispatcher-2"),"dispatcher-2");assert.equal(h.ui.initialOperator("?operator=admin"),"dispatcher-1");
  h.ui.S.site.site_areas=[{responsible_sector_id:"logistics",rectangle:{x:0,y:12,width:38,height:76}}];let bounds;h.ui.S.map={fitBounds(value){bounds=value;}};h.ui.focusSector();assert.equal(JSON.stringify(bounds),"[[12,0],[88,38]]");
});

test("selecting and polling highlight without recenter; only show button focuses",async()=>{
  const h=harness(async()=>incident());const calls=[];
  h.ui.S.mapRenderer={clearHighlight(){calls.push("clear");},highlight(i,options){calls.push(options.recenter?"focus":"highlight");},showIncident(){calls.push("focus");return true;}};
  await h.ui.selectIncident("I1");h.ui.applyDetails(incident({dispatch_revision:8}));
  assert.deepEqual(calls,["clear","highlight","highlight"]);
  h.$("show-on-map").onclick();assert.deepEqual(calls,["clear","highlight","highlight","focus"]);
});

test("unknown target cannot create a made up location",()=>{
  const h=harness();h.ui.S.selected="I1";h.ui.applyDetails(incident({site_area_id:"unknown"}));
  h.ui.S.mapRenderer={showIncident(){return false;}};h.ui.showSelectedOnMap();
  assert.equal(h.$("error").hidden,false);assert.match(h.$("error").textContent,/нет подтверждённых координат/);
});

test("foreign-sector incidents remain viewable without offering an unauthorised claim",()=>{
  const h=harness();h.ui.S.selected="I1";
  h.ui.applyDetails(incident({can_claim:false}));
  assert.match(h.$("detail-actions").innerHTML,/id="show-on-map"/);
  assert.doesNotMatch(h.$("detail-actions").innerHTML,/id="claim"/);
  h.ui.applyDetails(incident({can_claim:true,dispatch_revision:8}));
  assert.match(h.$("detail-actions").innerHTML,/id="claim"/);
});

test("journal combines type, owner and text filters using shared human asset names",()=>{
  const h=harness();h.ui.S.site.assets=[{id:"V1",name:"Погрузчик сырья"}];h.ui.S.site.site_areas=[{id:"warehouse-raw",name:"Склад сырья"}];
  h.ui.S.incidents=[incident({assigned_operator_id:"dispatcher-1"}),incident({incident_id:"I2",type:"sensor_offline",assigned_operator_id:"dispatcher-1"}),incident({incident_id:"I3",assigned_operator_id:"dispatcher-2"})];
  h.$("filter-type").value="forbidden_zone";h.$("filter-state").value="mine";h.$("search-incidents").value="сырья";h.ui.renderIncidents();
  assert.match(h.$("incidents").innerHTML,/data-id="I1"/);assert.doesNotMatch(h.$("incidents").innerHTML,/data-id="I[23]"/);assert.match(h.$("incidents").innerHTML,/Погрузчик сырья/);
});
