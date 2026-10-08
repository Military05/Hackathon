"use strict";
// Standalone preview rendered by the real interface from isolated HTTP reports.
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {webcrypto}=require("node:crypto");
const root=path.resolve(__dirname,"..");
const source=fs.readFileSync(path.join(root,"src/interface/web/app.js"),"utf8");
const esc=value=>String(value).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function render(job){
  const nodes=new Map(),document={getElementById(id){if(!nodes.has(id))nodes.set(id,{innerHTML:"",textContent:""});return nodes.get(id);}};
  const context=vm.createContext({document,crypto:webcrypto,Date,URLSearchParams,URL,AbortController,
    setTimeout:()=>1,clearTimeout:()=>{},setInterval:()=>1,
    fetch:async()=>({ok:true,status:200,json:async()=>job})});
  vm.runInContext(source+"\nglobalThis.ui={S,renderJob};",context);
  context.ui.S.selected=job.incident_id;context.ui.S.job=job.job_id;
  await context.ui.renderJob();return document.getElementById("analysis").innerHTML;
}
async function main(){
  const args=process.argv.slice(2),options={};
  for(let i=0;i<args.length;i+=2){
    if(!["--zone-report","--mlp-report","--output"].includes(args[i])||!args[i+1])throw Error("Укажите --zone-report, --mlp-report и --output.");
    options[args[i]]=args[i+1];
  }
  if(!options["--zone-report"]||!options["--mlp-report"]||!options["--output"])throw Error("Не указаны входные отчёты или путь HTML-примера.");
  const zone=JSON.parse(fs.readFileSync(options["--zone-report"],"utf8"));
  const mlp=JSON.parse(fs.readFileSync(options["--mlp-report"],"utf8"));
  if(zone.status!=="PASS"||mlp.status!=="PASS"||zone.job.status!=="completed"||zone.job.stale||
     !zone.stale_after_actual_change_checked||!zone.stale_job?.stale||mlp.job.status!=="completed"||mlp.job.stale||
     zone.stale_job.job_id!==zone.job.job_id||
     JSON.stringify(zone.stale_job.result.presentation)!==JSON.stringify(zone.job.result.presentation))
    throw Error("Нужны успешные тестовые анализы и подтверждённое устаревание после изменения тестовых данных.");
  const rows=[
    ["1 · Запрещённая зона — завершённый актуальный анализ",zone.job],
    ["2 · Та же зона — данные изменились после анализа",zone.stale_job],
    ["3 · Необычное движение",mlp.job]];
  const css=["styles.css","dispatch-view.css","product-v5.css"].map(file=>fs.readFileSync(path.join(root,"src/interface/web",file),"utf8")).join("\n");
  let html=`<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Контур — три варианта AI-анализа</title><style>${css}
    .preview-analysis-page main{max-width:1180px;margin:24px auto;padding:0 18px}
    .preview-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
    .preview-card{min-width:0}.preview-card:last-child{grid-column:1/-1}
    .preview-card .analysis{padding:16px;margin:0;border:0}
    .preview-card h3{font-size:17px}.preview-card p{overflow-wrap:anywhere}
    .preview-note{margin:16px 0 22px;color:var(--muted)}
    @media(max-width:760px){.preview-cards{grid-template-columns:1fr}}
    </style></head><body class="preview-analysis-page"><header class="app-header"><div class="brand"><span class="brand-icon" aria-hidden="true">К</span><div><strong>КОНТУР</strong></div></div><span class="connection">Демонстрация · тестовые происшествия</span></header><main><h1>Заключение для диспетчера</h1><p class="preview-note">Первая карточка показывает завершённый анализ. Во второй после проверки изменились данные: описание относится к прежнему состоянию. Третья объясняет обнаруженное необычное движение. Это тестовые примеры, а не текущее состояние рабочего сервера.</p><div class="preview-cards">`;
  for(const [title,job] of rows)html+=`<section class="detail preview-card"><div class="section-head"><h2>${esc(title)}</h2></div><div class="analysis">${await render(job)}</div></section>`;
  fs.writeFileSync(options["--output"],html+"</div></main></body></html>");
  console.log("Созданы три карточки: "+path.resolve(options["--output"]));
}
main().catch(error=>{console.error("Ошибка подготовки примера: "+error.message);process.exitCode=1;});
