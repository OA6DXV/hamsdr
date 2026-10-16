// SPDX-License-Identifier: GPL-3.0-only
'use strict';
(()=>{
  const el=id=>document.getElementById(id), random=()=>{
    if(crypto.randomUUID)return crypto.randomUUID().replaceAll('-','');
    const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);
    return Array.from(bytes,byte=>byte.toString(16).padStart(2,'0')).join('');
  };
  const callsignPattern=/^[A-Z0-9\\|\/@_-]{1,10}$/;
  const callsignHint='Indicativo: máximo 10 caracteres; usa letras A-Z, números 0-9 y los símbolos \\ | / @ - _.';
  const translatedHint=()=>window.HamI18n?.t(callsignHint)||callsignHint;
  const normalizeCallsign=value=>{
    const trimmed=value.trim();
    return /^[\x00-\x7F]*$/.test(trimmed)?trimmed.toUpperCase():trimmed;
  };
  const events={chat:new Map(),log:new Map()}, pending={};
  let ready=false,identifyTimer=0,acknowledgedName='';
  const waiting={chat:false,log:false};
  let cooldownEnd=0,cooldownDuration=0,cooldownTimer=0;
  try{
    const name=localStorage.getItem('hamsdr-name')||'';
    const normalized=normalizeCallsign(name);
    el('username').value=Array.from(normalized).length<=10?normalized:'';
  }catch{}
  try{
    const saved=JSON.parse(sessionStorage.getItem('hamsdr-pending')||'{}');
    for(const kind of ['chat','log'])if(saved[kind]?.type===kind&&typeof saved[kind].text==='string'&&saved[kind].text.length<=500&&/^[a-f0-9]{32}$/.test(saved[kind].request_id)){
      pending[kind]={type:kind,text:saved[kind].text,request_id:saved[kind].request_id,
        identity:saved[kind].identity,name:saved[kind].name,
        review:saved[kind].review===true||!/^[a-f0-9]{32}$/.test(saved[kind].identity)||!callsignPattern.test(saved[kind].name)};
      el(kind+'-text').value=saved[kind].text;
    }
  }catch{}
  function savePending(){try{sessionStorage.setItem('hamsdr-pending',JSON.stringify(pending));}catch{}}
  function identify(report=false){
    const field=el('username'),name=normalizeCallsign(field.value);
    field.value=name;
    if(name&&!callsignPattern.test(name)){
      field.setCustomValidity(translatedHint());ready=false;availability();
      el('chat-status').textContent=callsignHint;
      if(report)field.reportValidity();
      return false;
    }
    field.setCustomValidity('');
    return window.radioSend({type:'identify',name});
  }
  function scheduleIdentify(){
    clearTimeout(identifyTimer);
    identifyTimer=setTimeout(()=>{
      identifyTimer=0;
      const name=normalizeCallsign(el('username').value);
      try{localStorage.setItem('hamsdr-name',name);}catch{}
      identify();
    },600);
  }
  const cooling=()=>cooldownEnd>performance.now();
  function availability(){
    for(const kind of ['chat','log'])el('send-'+kind).disabled=!ready||waiting[kind]||(kind==='chat'&&cooling());
  }
  function errorMessage(kind,text=''){
    const target=el(kind+'-error');target.textContent=text;target.hidden=!text;
  }
  function drawCooldown(){
    clearTimeout(cooldownTimer);cooldownTimer=0;
    const remaining=Math.max(0,cooldownEnd-performance.now()),button=el('send-chat');
    button.dataset.cooldown=remaining>0?'true':'false';
    button.style.setProperty('--cooldown-progress',`${cooldownDuration?Math.min(100,Math.max(0,100*(1-remaining/cooldownDuration))):100}%`);
    availability();
    if(remaining>0)cooldownTimer=setTimeout(drawCooldown,50);
  }
  function applyCooldown(state){
    if(!state||!Number.isFinite(state.remaining_ms)||!Number.isFinite(state.duration_ms)||state.remaining_ms<0||state.duration_ms<0)return;
    cooldownDuration=state.duration_ms;cooldownEnd=performance.now()+state.remaining_ms;
    if(state.remaining_ms>0&&!el('chat-error').textContent)errorMessage('chat','Chat temporalmente deshabilitado por intentos de envío bloqueados.');
    drawCooldown();
  }
  window.addEventListener('pageshow',drawCooldown);
  document.addEventListener('visibilitychange',drawCooldown);
  el('username').addEventListener('input',()=>{
    const field=el('username');
    field.value=normalizeCallsign(field.value);
    field.setCustomValidity(field.value&&!callsignPattern.test(field.value)?translatedHint():'');
    ready=false;availability();
    if(field.value&&!callsignPattern.test(field.value)){
      clearTimeout(identifyTimer);identifyTimer=0;el('chat-status').textContent=callsignHint;
    }else{
      el('chat-status').textContent='Verificando indicativo…';
      scheduleIdentify();
    }
  });
  el('username').addEventListener('change',()=>{
    clearTimeout(identifyTimer);identifyTimer=0;
    ready=false;availability();
    const field=el('username'),name=normalizeCallsign(field.value);
    field.value=name;
    if(name&&!callsignPattern.test(name)){
      field.setCustomValidity(translatedHint());el('chat-status').textContent=callsignHint;field.reportValidity();return;
    }
    field.setCustomValidity('');
    try{localStorage.setItem('hamsdr-name',name);}catch{}
    identify(true);
  });
  window.addEventListener('radio-open',()=>{ready=false;availability();identify();});
  window.addEventListener('languagechange',()=>{
    const field=el('username');field.setCustomValidity(field.value&&!callsignPattern.test(field.value)?translatedHint():'');
  });
  window.addEventListener('radio-close',()=>{ready=false;waiting.chat=waiting.log=false;availability();el('chat-status').textContent='Desconectado · recuperando conexión e historial…';});
  function eventRow(kind,e){
    const row=document.createElement('div');row.className='chat-entry';row.dataset.id=e.id;
    const stamp=document.createElement('span');stamp.className='chat-time';stamp.textContent=new Date(e.time*1000).toISOString().replace('T',' ').slice(0,19)+' UTC ';
    const name=document.createElement('span');name.className='chat-name';name.textContent=(kind==='log'?(e.call||e.name):e.name)+': ';
    row.append(stamp,name,document.createTextNode(kind==='log'?`${(e.frequency/1000).toFixed(2)} kHz ${e.mode} · ${e.text}`:e.text));
    return row;
  }
  function render(kind,older=false){
    const target=el(kind==='chat'?'chatbox':'logbook');
    const bottom=target.scrollHeight-target.scrollTop-target.clientHeight<35,position=target.scrollTop,height=target.scrollHeight;
    const fragment=document.createDocumentFragment();
    for(const e of [...events[kind].values()].sort((a,b)=>a.id-b.id)){
      fragment.append(eventRow(kind,e));
    }
    target.replaceChildren(fragment);
    if(older)target.scrollTop=position+target.scrollHeight-height;else if(bottom)target.scrollTop=target.scrollHeight;else target.scrollTop=position;
  }
  function add(event){
    const rows=events[event.kind];if(!rows)return;
    const existed=rows.has(event.id);
    rows.set(event.id,event);
    let evicted=null;
    if(rows.size>10000){evicted=Math.min(...rows.keys());rows.delete(evicted);}
    return{existed,evicted};
  }
  function renderEvent(event){
    const change=add(event);if(!change)return;
    const target=el(event.kind==='chat'?'chatbox':'logbook');
    const bottom=target.scrollHeight-target.scrollTop-target.clientHeight<35,position=target.scrollTop;
    if(change.evicted!==null){
      const first=target.firstElementChild;
      if(first&&Number(first.dataset.id)===change.evicted)first.remove();
    }
    // An idempotent acknowledgement may refer to an event older than the
    // retained window. Do not render a row that the in-memory window evicted.
    if(!events[event.kind].has(event.id))return;
    if(!change.existed){
      const row=eventRow(event.kind,event);
      let previous=target.lastElementChild;
      while(previous&&Number(previous.dataset.id)>event.id)previous=previous.previousElementSibling;
      if(previous)previous.after(row);else target.prepend(row);
    }
    if(bottom)target.scrollTop=target.scrollHeight;else target.scrollTop=position;
  }
  function resend(kind){
    if(!pending[kind]||!ready)return;
    if(pending[kind].review||pending[kind].identity!==window.radioIdentity?.id||pending[kind].name!==acknowledgedName){
      // Retain held drafts across reloads, but never retry them automatically.
      pending[kind].review=true;waiting[kind]=false;savePending();availability();
      el('chat-status').textContent='Borrador recuperado: revisa el texto y pulsa Enviar para publicarlo con tu identidad actual.';
      return;
    }
    if(waiting[kind]||(kind==='chat'&&cooling()))return;
    const {type,text,request_id}=pending[kind];
    waiting[kind]=true;window.radioSend({type,text,request_id});availability();
    setTimeout(()=>{if(pending[kind]?.request_id===request_id&&!pending[kind].review){waiting[kind]=false;availability();el('chat-status').textContent='Sin confirmación todavía. Puedes volver a enviar; no se duplicará el mensaje.';}},10000);
  }
  window.addEventListener('radio-event',({detail:m})=>{
    if(m.type==='chat-cooldown'){
      applyCooldown(m);
    }else if(m.type==='identified'){
      // An acknowledgement for earlier input must not enable posting while
      // the user is still editing a newer callsign.
      if(m.name!==normalizeCallsign(el('username').value))return;
      acknowledgedName=m.name;
      const wasReady=ready;ready=!!m.name?.trim()&&callsignPattern.test(m.name);availability();
      el('chat-status').textContent=ready?'Chat en vivo conectado · historial guardado':'Configura un indicativo válido (máximo 10 caracteres) para publicar';
      if(ready&&!wasReady)for(const kind of ['chat','log'])resend(kind);
      if(ready&&Object.values(pending).some(item=>item.review))el('chat-status').textContent='Borrador recuperado: revisa el texto y pulsa Enviar para publicarlo con tu identidad actual.';
    }else if(m.type==='history'){
      if(!events[m.kind])return;
      // A reconnect starts with the latest window; older entries remain accessible
      // through the explicit cursor, including a long offline interval.
      if(!m.before)events[m.kind].clear();
      m.events.forEach(add);el('older-'+m.kind).hidden=!m.more;render(m.kind,!!m.before);
    }else if(m.type==='event'){renderEvent(m.event);}
    else if(m.type==='ack'){
      renderEvent(m.event);
      for(const kind of ['chat','log'])if(pending[kind]?.request_id===m.request_id){
        if(el(kind+'-text').value===pending[kind].text)el(kind+'-text').value='';
        delete pending[kind];waiting[kind]=false;errorMessage(kind);savePending();availability();
      }
      el('chat-status').textContent='Guardado y enviado';
    }else if(m.type==='error'){
      if(['offensive-language','repeated-character','chat-cooldown'].includes(m.code)&&['chat','log'].includes(m.kind)&&pending[m.kind]?.request_id===m.request_id){
        // Keep a rejected submission as a draft. Reconnect must not retry it.
        pending[m.kind].review=true;savePending();
      }
      if(m.chat_cooldown)applyCooldown(m.chat_cooldown);
      if(['chat','log'].includes(m.kind)){
        if(pending[m.kind]?.request_id===m.request_id)waiting[m.kind]=false;
        errorMessage(m.kind,m.message);
        if(m.kind==='chat')el('chat-status').textContent='';
      }else el('chat-status').textContent=m.message;
      availability();
    }
  });
  for(const kind of ['chat','log']){
    el(kind==='chat'?'chatform':'logform').addEventListener('submit',e=>{
      e.preventDefault();if(!ready||waiting[kind]||(kind==='chat'&&cooling()))return;
      const text=el(kind+'-text').value.trim();
      if(!text)return;
      errorMessage(kind);
      // Keep the id on retry of the exact same submission, including reconnects.
      if(!pending[kind]||pending[kind].review||pending[kind].text!==text||pending[kind].identity!==window.radioIdentity?.id||pending[kind].name!==acknowledgedName)
        pending[kind]={type:kind,text,request_id:random(),identity:window.radioIdentity?.id,name:acknowledgedName};
      savePending();resend(kind);
    });
    el('older-'+kind).onclick=()=>{
      const ids=[...events[kind].keys()];if(!ids.length)return;
      window.radioSend({type:'history',kind,before:Math.min(...ids)});
    };
  }
  availability();
})();
