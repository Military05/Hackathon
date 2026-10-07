(async function(){
  if(new URLSearchParams(window.location.search).get("preview")!=="1")return;
  const host=document.getElementById("preview-stage");
  const update=async()=>{try{const response=await fetch("/preview-progress.json",{cache:"no-store"});if(!response.ok)return;const data=await response.json();host.hidden=false;host.replaceChildren();const label=document.createElement("strong");label.textContent="Предпросмотр разработки";host.append(label);for(const item of data.stages||[]){const chip=document.createElement("span");chip.className=`stage-chip ${item.status==="done"?"done":item.status==="active"?"active":"waiting"}`;chip.textContent=`${item.status==="done"?"✓":item.status==="active"?"●":"○"} ${item.title}`;host.append(chip);}if(data.note){const note=document.createElement("small");note.textContent=data.note;host.append(note);}}catch{}};
  await update();setInterval(update,3000);
})();
