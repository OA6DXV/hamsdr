// SPDX-License-Identifier: GPL-3.0-only
'use strict';
// Credentials are origin-scoped browser state, never cookies or URL parameters.
window.radioIdentity=(()=>{
  const storageKey='hamsdr-identity-v1', valid=token=>typeof token==='string'&&/^[a-f0-9]{64}$/.test(token);
  let memoryToken=null, identity=null, persistent=true;
  const read=()=>{
    try{const token=localStorage.getItem(storageKey);return valid(token)?token:memoryToken;}
    catch{persistent=false;return memoryToken;}
  };
  function remember(token){
    memoryToken=token;
    try{localStorage.setItem(storageKey,token);}catch{persistent=false;}
  }
  function forget(){
    memoryToken=null;identity=null;
    try{localStorage.removeItem(storageKey);}catch{}
  }
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  async function databaseLease(){
    // IndexedDB read/write transactions serialize simultaneous HTTP tabs too.
    const db=await new Promise((resolve,reject)=>{
      const request=indexedDB.open('hamsdr-identity-bootstrap',1);
      let finished=false;
      const fail=error=>{if(finished)return;finished=true;clearTimeout(timer);reject(error);};
      const timer=setTimeout(()=>fail(new Error('Tiempo de coordinación agotado')),3000);
      request.onupgradeneeded=()=>request.result.createObjectStore('lease');
      request.onsuccess=()=>{if(finished){request.result.close();return;}finished=true;clearTimeout(timer);resolve(request.result);};
      request.onerror=()=>fail(request.error);
      request.onblocked=()=>fail(new Error('Almacenamiento ocupado'));
    });
    const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);
    const owner=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
    const attempt=remove=>new Promise((resolve,reject)=>{
      const transaction=db.transaction('lease','readwrite'),store=transaction.objectStore('lease');
      let acquired=false;
      const request=store.get('bootstrap');
      request.onsuccess=()=>{
        const previous=request.result;
        if(remove){if(previous?.owner===owner)store.delete('bootstrap');}
        else if(!previous||previous.expires<=Date.now()){
          store.put({owner,expires:Date.now()+12000},'bootstrap');acquired=true;
        }
      };
      transaction.oncomplete=()=>resolve(acquired);
      transaction.onabort=()=>reject(transaction.error||new Error('No se pudo coordinar la identidad'));
      transaction.onerror=()=>{};
    });
    const deadline=Date.now()+14000;
    try{
      while(!await attempt(false)){
        if(Date.now()>deadline)throw new Error('Tiempo de coordinación agotado');
        await pause(100);
      }
    }catch(error){db.close();throw error;}
    let released=false;
    const release=()=>{if(released)return;released=true;clearTimeout(timer);void attempt(true).finally(()=>db.close()).catch(()=>{});};
    const timer=setTimeout(release,10000);
    return release;
  }
  async function bootstrapLease(){
    let locks;
    try{locks=navigator.locks;}catch{}
    if(locks?.request){
      const controller=new AbortController();
      const timer=setTimeout(()=>controller.abort(),14000);
      try{
        return await new Promise((resolve,reject)=>{
          locks.request('hamsdr-identity-bootstrap',{signal:controller.signal},async()=>{
            clearTimeout(timer);
            await new Promise(done=>{
              let released=false;
              const release=()=>{if(released)return;released=true;clearTimeout(expiry);done();};
              const expiry=setTimeout(release,10000);
              resolve(release);
            });
          }).catch(reject);
        });
      }catch(error){
        clearTimeout(timer);
        // An occupied lock timing out is not permission to bypass coordination.
        // Denied/unusable Web Locks can still use the transactional IDB lease.
        if(error?.name==='AbortError')throw error;
      }
    }
    try{return await databaseLease();}
    catch{
      // Storage-denied pages still receive a genuine, memory-only credential.
      persistent=false;return ()=>{};
    }
  }
  async function prepare(){
    let token=read(),release=()=>{};
    if(!token){release=await bootstrapLease();token=read();}
    return{token,release};
  }
  function accept(message){
    if(typeof message.identity!=='string'||!/^[a-f0-9]{32}$/.test(message.identity))throw new Error('Identidad del servidor inválida');
    if(message.token!==undefined){if(!valid(message.token))throw new Error('Credencial del servidor inválida');remember(message.token);}
    identity=message.identity;
    return persistent;
  }
  async function session(action){
    const token=read();
    if(!token)throw new Error('No hay un identificador guardado; vuelve a conectar.');
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),7000);
    try{
      const response=await fetch(new URL('./session',location.href),{
        method:'POST',headers:{'Content-Type':'application/json'},credentials:'omit',
        cache:'no-store',signal:controller.signal,body:JSON.stringify({action,token}),
      });
      const result=await response.json();
      if(!response.ok)throw new Error(result.message||'No se pudo cambiar de sesión.');
      return result;
    }catch(error){
      if(error.name==='AbortError')throw new Error('El servidor tardó demasiado; vuelve a intentar.');
      throw error;
    }finally{clearTimeout(timer);}
  }
  return{prepare,accept,forget,session,get id(){return identity;},get persistent(){return persistent;}};
})();
