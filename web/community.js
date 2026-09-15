'use strict';
(()=>{
  const el=id=>document.getElementById(id), random=()=>crypto.randomUUID().replaceAll('-','');
  const events={chat:new Map(),log:new Map()}, pending={};
  let key=random(),ready=false;
  try{
    const stored=sessionStorage.getItem('hamsdr-session');
    if(stored&&/^[a-f0-9]{32}$/.test(stored))key=stored;
    sessionStorage.setItem('hamsdr-session',key);
    el('username').value=localStorage.getItem('hamsdr-name')||'';
    const saved=JSON.parse(sessionStorage.getItem('hamsdr-pending')||'{}');
    for(const kind of ['chat','log'])if(saved[kind]?.type===kind&&typeof saved[kind].text==='string'&&/^[a-f0-9]{32}$/.test(saved[kind].request_id)){
      pending[kind]=saved[kind];el(kind+'-text').value=saved[kind].text;if(kind==='log')el('log-call').value=saved[kind].call;
    }
  }catch{}
  function savePending(){try{sessionStorage.setItem('hamsdr-pending',JSON.stringify(pending));}catch{}}
  function identify(){window.radioSend({type:'identify',name:el('username').value.trim(),key});}
  function availability(){for(const kind of ['chat','log'])el('send-'+kind).disabled=!ready;}
  el('username').addEventListener('change',()=>{
    try{localStorage.setItem('hamsdr-name',el('username').value.trim());}catch{}
    identify();
  });
  window.addEventListener('radio-open',()=>{ready=false;availability();identify();});
  window.addEventListener('radio-close',()=>{ready=false;availability();el('chat-status').textContent='Desconectado · recuperando conexión e historial…';});
  function render(kind,older=false){
    const target=el(kind==='chat'?'chatbox':'logbook');
    const bottom=target.scrollHeight-target.scrollTop-target.clientHeight<35,position=target.scrollTop,height=target.scrollHeight;
    const fragment=document.createDocumentFragment();
    for(const e of [...events[kind].values()].sort((a,b)=>a.id-b.id)){
      const row=document.createElement('div');row.className='chat-entry';row.dataset.id=e.id;
      const stamp=document.createElement('span');stamp.className='chat-time';stamp.textContent=new Date(e.time*1000).toISOString().replace('T',' ').slice(0,19)+' UTC ';
      const name=document.createElement('span');name.className='chat-name';name.textContent=e.name+': ';
      row.append(stamp,name,document.createTextNode(kind==='log'?`${e.call} · ${(e.frequency/1000).toFixed(2)} kHz ${e.mode} · ${e.text}`:e.text));
      fragment.append(row);
    }
    target.replaceChildren(fragment);
    if(older)target.scrollTop=position+target.scrollHeight-height;else if(bottom)target.scrollTop=target.scrollHeight;else target.scrollTop=position;
  }
  function add(event){
    const rows=events[event.kind];if(!rows)return;
    rows.set(event.id,event);
    if(rows.size>10000)rows.delete(Math.min(...rows.keys()));
  }
  function resend(kind){
    if(!pending[kind]||!ready)return;
    window.radioSend(pending[kind]);el('send-'+kind).disabled=true;
    setTimeout(()=>{if(pending[kind]){el('send-'+kind).disabled=!ready;el('chat-status').textContent='Sin confirmación todavía. Puedes volver a enviar; no se duplicará el mensaje.';}},10000);
  }
  window.addEventListener('radio-event',({detail:m})=>{
    if(m.type==='identified'){
      const wasReady=ready;ready=true;availability();el('chat-status').textContent='Chat en vivo conectado · historial guardado';
      if(!wasReady)for(const kind of ['chat','log'])resend(kind);
    }else if(m.type==='history'){
      if(!events[m.kind])return;
      // A reconnect starts with the latest window; older entries remain accessible
      // through the explicit cursor, including a long offline interval.
      if(!m.before)events[m.kind].clear();
      m.events.forEach(add);el('older-'+m.kind).hidden=!m.more;render(m.kind,!!m.before);
    }else if(m.type==='event'){add(m.event);render(m.event.kind);}
    else if(m.type==='ack'){
      add(m.event);render(m.event.kind);
      for(const kind of ['chat','log'])if(pending[kind]?.request_id===m.request_id){
        if(el(kind+'-text').value===pending[kind].text)el(kind+'-text').value='';
        delete pending[kind];savePending();el('send-'+kind).disabled=!ready;
      }
      el('chat-status').textContent='Guardado y enviado';
    }else if(m.type==='error'){availability();el('chat-status').textContent=m.message;}
  });
  for(const kind of ['chat','log']){
    el(kind==='chat'?'chatform':'logform').addEventListener('submit',e=>{
      e.preventDefault();if(!ready)return;
      const text=el(kind+'-text').value.trim(),call=kind==='log'?el('log-call').value.trim():'';
      if(!text)return;
      // Keep the id on retry of the exact same submission, including reconnects.
      if(!pending[kind]||pending[kind].text!==text||pending[kind].call!==call)
        pending[kind]={type:kind,text,call,request_id:random()};
      savePending();resend(kind);
    });
    el('older-'+kind).onclick=()=>{
      const ids=[...events[kind].keys()];if(!ids.length)return;
      window.radioSend({type:'history',kind,before:Math.min(...ids)});
    };
  }
  availability();
})();
