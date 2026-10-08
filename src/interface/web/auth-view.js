(function(root){
  "use strict";
  let session=null;
  const $=id=>document.getElementById(id);
  const headers=()=>session?.csrf_token?{"X-CSRF-Token":session.csrf_token,"X-Expected-User":session.user.id}:{};
  async function request(path,method="GET",body){
    const response=await fetch(`/api${path}`,{method,credentials:"same-origin",headers:{...headers(),...(body?{"Content-Type":"application/json"}:{})},...(body?{body:JSON.stringify(body)}:{})});
    const value=await response.json();
    if(!response.ok){const error=Error(value.message||`HTTP ${response.status}`);error.status=response.status;error.code=value.code;throw error;}
    return value;
  }
  function message(text,error=false){$("auth-message").textContent=text;$("auth-message").className=error?"auth-message failure":"auth-message";}
  function showLogin(reason){
    if($("admin-dialog")?.open)$("admin-dialog").close();
    session=null;$("auth-screen").hidden=false;$("product-shell").hidden=true;$("user-controls").hidden=true;
    $("auth-login").hidden=false;$("auth-register").hidden=true;
    if(reason)message(reason,true);
  }
  async function submit(form,path){
    const button=form.querySelector("button[type=submit]");if(button.disabled)return;
    button.disabled=true;message("Проверка…");
    try{
      const fields=Object.fromEntries(new FormData(form));
      if(path==="/auth/register"&&fields.password!==fields.confirm_password)throw Error("Пароли не совпадают");
      delete fields.confirm_password;
      const result=await request(path,"POST",fields);
      if(path==="/auth/login"){session=result;window.location.reload();}
      else{form.reset();$("auth-register").hidden=true;$("auth-login").hidden=false;message("Заявка отправлена. После подтверждения администратором войдите с вашим логином.");}
    }catch(error){message(error.message,true);}finally{button.disabled=false;}
  }
  async function init(options={}){
    $("auth-login").onsubmit=event=>{event.preventDefault();submit(event.currentTarget,"/auth/login");};
    $("auth-register").onsubmit=event=>{event.preventDefault();submit(event.currentTarget,"/auth/register");};
    $("show-register").onclick=()=>{$("auth-login").hidden=true;$("auth-register").hidden=false;message("Роль и рабочее место назначит администратор.");};
    $("show-login").onclick=()=>{$("auth-register").hidden=true;$("auth-login").hidden=false;message("");};
    const status=await request("/auth/status");
    if(!status.enabled){$("auth-screen").hidden=true;$("product-shell").hidden=false;return {enabled:false};}
    try{session=await request("/auth/me");}catch(error){if(error.status!==401)throw error;showLogin();if(!status.configured)message("Первый администратор ещё не создан. Запустите scripts/manage_users.py на сервере.");return {enabled:true,user:null};}
    $("auth-screen").hidden=true;$("product-shell").hidden=false;$("user-controls").hidden=false;
    $("current-user").textContent=session.user.role==="admin"?session.user.username:session.user.name;
    $("admin-open").hidden=session.user.role!=="admin";
    $("logout").onclick=async()=>{try{if(options.beforeLogout)await options.beforeLogout().catch(()=>{});await request("/auth/logout","POST",{});window.location.reload();}catch(error){if(options.onError)options.onError(error);else message(error.message,true);}};
    return {enabled:true,...session};
  }
  async function download(path,filename){
    const response=await fetch(`/api${path}`,{credentials:"same-origin",headers:headers()});
    if(!response.ok){let body;try{body=await response.json();}catch{}const error=Error(body?.message||`HTTP ${response.status}`);error.status=response.status;error.code=body?.code;throw error;}
    const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement("a");
    link.href=url;link.download=filename;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  root.ProductAuth={init,headers,request,download,showLogin,getSession:()=>session};
})(globalThis);
