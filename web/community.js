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
  const normalizeCallsign=value=>{
    const trimmed=value.trim();
    return /^[\x00-\x7F]*$/.test(trimmed)?trimmed.toUpperCase():trimmed;
  };
  const events={chat:new Map(),log:new Map()}, pending={};
  let key=random(),ready=false,identifyTimer=0;
  try{
    const stored=sessionStorage.getItem('hamsdr-session');
    if(stored&&/^[a-f0-9]{32}$/.test(stored))key=stored;
    sessionStorage.setItem('hamsdr-session',key);
    const name=localStorage.getItem('hamsdr-name')||'';
    const normalized=normalizeCallsign(name);
    el('username').value=Array.from(normalized).length<=10?normalized:'';
    const saved=JSON.parse(sessionStorage.getItem('hamsdr-pending')||'{}');
    for(const kind of ['chat','log'])if(saved[kind]?.type===kind&&typeof saved[kind].text==='string'&&/^[a-f0-9]{32}$/.test(saved[kind].request_id)){
      pending[kind]={type:kind,text:saved[kind].text,request_id:saved[kind].request_id};
      el(kind+'-text').value=saved[kind].text;
    }
  }catch{}
  function savePending(){try{sessionStorage.setItem('hamsdr-pending',JSON.stringify(pending));}catch{}}
  function identify(report=false){
    const field=el('username'),name=normalizeCallsign(field.value);
    field.value=name;
    if(name&&!callsignPattern.test(name)){
      field.setCustomValidity(callsignHint);ready=false;availability();
      el('chat-status').textContent=callsignHint;
      if(report)field.reportValidity();
      return false;
    }
    field.setCustomValidity('');
    return window.radioSend({type:'identify',name,key});
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
  function availability(){for(const kind of ['chat','log'])el('send-'+kind).disabled=!ready;}
  el('username').addEventListener('input',()=>{
    const field=el('username');
    field.value=normalizeCallsign(field.value);
    field.setCustomValidity(field.value&&!callsignPattern.test(field.value)?callsignHint:'');
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
      field.setCustomValidity(callsignHint);el('chat-status').textContent=callsignHint;field.reportValidity();return;
    }
    field.setCustomValidity('');
    try{localStorage.setItem('hamsdr-name',name);}catch{}
    identify(true);
  });
  window.addEventListener('radio-open',()=>{ready=false;availability();identify();});
  window.addEventListener('radio-close',()=>{ready=false;availability();el('chat-status').textContent='Desconectado · recuperando conexión e historial…';});
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
    window.radioSend(pending[kind]);el('send-'+kind).disabled=true;
    setTimeout(()=>{if(pending[kind]){el('send-'+kind).disabled=!ready;el('chat-status').textContent='Sin confirmación todavía. Puedes volver a enviar; no se duplicará el mensaje.';}},10000);
  }
  window.addEventListener('radio-event',({detail:m})=>{
    if(m.type==='identified'){
      const wasReady=ready;ready=!!m.name?.trim()&&callsignPattern.test(m.name);availability();
      el('chat-status').textContent=ready?'Chat en vivo conectado · historial guardado':'Configura un indicativo válido (máximo 10 caracteres) para publicar';
      if(ready&&!wasReady)for(const kind of ['chat','log'])resend(kind);
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
        delete pending[kind];savePending();availability();
      }
      el('chat-status').textContent='Guardado y enviado';
    }else if(m.type==='error'){availability();el('chat-status').textContent=m.message;}
  });
  for(const kind of ['chat','log']){
    el(kind==='chat'?'chatform':'logform').addEventListener('submit',e=>{
      e.preventDefault();if(!ready)return;
      const text=el(kind+'-text').value.trim();
      if(!text)return;
      // Keep the id on retry of the exact same submission, including reconnects.
      if(!pending[kind]||pending[kind].text!==text)
        pending[kind]={type:kind,text,request_id:random()};
      savePending();resend(kind);
    });
    el('older-'+kind).onclick=()=>{
      const ids=[...events[kind].keys()];if(!ids.length)return;
      window.radioSend({type:'history',kind,before:Math.min(...ids)});
    };
  }
  availability();
})();
