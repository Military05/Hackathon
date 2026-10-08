const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
function fixture(){
  class Element{
    constructor(type=""){this.type=type;this.dataset={};this.attrs={};this.children=[];this.value="not-to-be-changed";this.id="";this.resetHandlers=[];this.form={addEventListener:(event,fn)=>this.resetHandlers.push(fn)};}
    setAttribute(key,value){this.attrs[key]=value;}
    insertBefore(child,before){child.parentNode=this;this.children.splice(this.children.indexOf(before),0,child);}
    append(...children){for(const child of children){if(child.parentNode){const i=child.parentNode.children.indexOf(child);if(i>=0)child.parentNode.children.splice(i,1);}child.parentNode=this;this.children.push(child);}}
  }
  const inputs=Array.from({length:3},()=>new Element("password"));for(const input of inputs){input.parentNode=new Element();input.parentNode.children.push(input);}
  const document={querySelectorAll:()=>inputs,createElement:()=>new Element()};const ctx=vm.createContext({document});vm.runInContext(fs.readFileSync(path.join(__dirname,"../../src/interface/web/auth-view.js"),"utf8"),ctx);
  return {inputs,setup:ctx.ProductAuth.setupPasswordToggles};
}
test("every password field gets a keyboard button; repeated setup stays idempotent",()=>{
  const h=fixture();h.setup();h.setup();
  for(const input of h.inputs){const wrap=input.parentNode,button=wrap.children[1];assert.equal(wrap.children.length,2);assert.equal(input.type,"password");assert.equal(button.type,"button");assert.equal(button.attrs["aria-controls"],input.id);button.onclick();assert.equal(input.type,"text");assert.equal(button.attrs["aria-pressed"],"true");button.onclick();assert.equal(input.type,"password");assert.equal(input.value,"not-to-be-changed");}
});
test("reset hides a revealed password",()=>{const h=fixture();h.setup();const input=h.inputs[2],button=input.parentNode.children[1];button.onclick();input.resetHandlers[0]();assert.equal(input.type,"password");assert.equal(button.attrs["aria-label"],"Показать пароль");});
test("admin combined export includes all-scope while dispatcher export stays personal",async()=>{
  const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,{addEventListener(){},hidden:false});return elements.get(id);};const downloads=[];
  const ctx=vm.createContext({document:{getElementById:get},URLSearchParams,Date,setTimeout,clearTimeout,ProductAuth:{download:async(...args)=>downloads.push(args)}});vm.runInContext(fs.readFileSync(path.join(__dirname,"../../src/interface/web/operations-view.js"),"utf8"),ctx);
  ctx.OperationsView.bind(async()=>({}),error=>{throw error;});ctx.OperationsView.configure({role:"admin"});await get("activity-export").onclick();assert.equal(downloads[0][0],"/dispatch-history/export.csv?scope=all&limit=2000");assert.match(downloads[0][1],/Общий_журнал/);
  ctx.OperationsView.configure({role:"dispatcher"});await get("activity-export").onclick();assert.equal(downloads[1][0],"/dispatch-history/export.csv");
});
