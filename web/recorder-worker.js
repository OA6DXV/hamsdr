// SPDX-License-Identifier: GPL-3.0-only
'use strict';
importScripts('./recorder-core.js');
const {WAV_HEADER_SIZE,wavHeader,OggOpus,oggChecksum}=HamRecorderCore;
const CHECKPOINT_BYTES=256*1024,CHECKPOINT_MS=5000,LOCK_PREFIX='hamsdr-recording:';
let directory=null,database=null,backend='memory',session=null,operations=Promise.resolve();
const memory=new Map();
function recordingId(){return Array.from(crypto.getRandomValues(new Uint8Array(16)),byte=>byte.toString(16).padStart(2,'0')).join('');}
function reply(request,result){postMessage({type:'reply',request,result});}
function problem(request,error){postMessage({type:'error',request,error:error.message||String(error)});}
function transactionDone(transaction){return new Promise((resolve,reject)=>{transaction.oncomplete=resolve;transaction.onabort=()=>reject(transaction.error||new Error('Storage transaction aborted'));transaction.onerror=()=>{};});}
function requested(request){return new Promise((resolve,reject)=>{request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});}
async function openDatabase(){
  const request=indexedDB.open('hamsdr-recordings',1);
  request.onupgradeneeded=()=>{const db=request.result;db.createObjectStore('recordings',{keyPath:'id'});db.createObjectStore('chunks',{keyPath:['id','offset']});};
  return requested(request);
}
async function initialize(){
  if(navigator.storage?.getDirectory){
    let probe,candidate;
    try{
      directory=candidate=await(await navigator.storage.getDirectory()).getDirectoryHandle('hamsdr-recordings',{create:true});
      probe='probe-'+recordingId();const file=await directory.getFileHandle(probe,{create:true});
      if(!file.createWritable)throw new Error('Atomic metadata writes unavailable');
      const handle=await file.createSyncAccessHandle();handle.close();backend='opfs';
    }catch{directory=null;}
    finally{if(candidate&&probe)await candidate.removeEntry(probe).catch(()=>{});}
  }
  if(backend!=='opfs'&&typeof indexedDB!=='undefined'){try{database=await openDatabase();backend='indexeddb';}catch{}}
  return{backend,checkpointBytes:CHECKPOINT_BYTES,checkpointMs:CHECKPOINT_MS};
}
async function saveMetadata(meta){
  if(backend==='opfs'){
    const handle=await directory.getFileHandle(meta.id+'.json',{create:true}),writer=await handle.createWritable();
    try{await writer.write(JSON.stringify(meta));await writer.close();}catch(error){await writer.abort().catch(()=>{});throw error;}
  }else if(backend==='indexeddb'){
    const tx=database.transaction('recordings','readwrite');tx.objectStore('recordings').put(meta);await transactionDone(tx);
  }else memory.get(meta.id).meta={...meta};
}
async function allMetadata(){
  if(backend==='opfs'){
    const output=[];
    for await(const [name,handle]of directory.entries())if(name.endsWith('.json')){
      try{const meta=JSON.parse(await(await handle.getFile()).text());if(/^[a-f0-9-]+$/i.test(meta.id)&&['wav','opus'].includes(meta.format)&&Number.isSafeInteger(meta.bytes)&&meta.bytes>0)output.push(meta);}catch{}
    }
    return output;
  }
  if(backend==='indexeddb')return requested(database.transaction('recordings').objectStore('recordings').getAll());
  return Array.from(memory.values(),record=>record.meta);
}
async function lockedIds(){
  if(!navigator.locks?.query)return null;
  try{return new Set((await navigator.locks.query()).held.filter(lock=>lock.name.startsWith(LOCK_PREFIX)).map(lock=>lock.name.slice(LOCK_PREFIX.length)));}catch{return null;}
}
async function list(){
  const held=await lockedIds();
  return(await allMetadata()).filter(meta=>meta.samples>0&&meta.id!==session?.meta.id&&!(held?held.has(meta.id):!meta.completed&&Date.now()-meta.updated<15000)).sort((a,b)=>b.started-a.started);
}
async function holdLock(id){
  if(!navigator.locks?.request)return()=>{};
  return new Promise((resolve,reject)=>{
    navigator.locks.request(LOCK_PREFIX+id,async()=>new Promise(release=>resolve(release))).catch(reject);
  });
}
function writeAll(handle,bytes,offset){
  let written=0;
  while(written<bytes.byteLength){const count=handle.write(bytes.subarray(written),{at:offset+written});if(count<=0)throw new Error('Could not write recording');written+=count;}
}
async function start(profile){
  if(session)throw new Error('A recording is already active');
  const format=['raw','digiraw'].includes(profile)?'wav':'opus',rate=profile==='digiraw'?12000:format==='wav'?16000:48000;
  const id=recordingId(),started=Date.now(),release=await holdLock(id);
  const meta={id,profile,format,rate,started,updated:started,bytes:0,samples:0,completed:false,gaps:0,filename:'hamsdr-'+new Date(started).toISOString().replaceAll(':','-')+'.'+(format==='wav'?'wav':'opus')};
  let handle=null;
  try{if(backend==='opfs')handle=await(await directory.getFileHandle(id+'.audio',{create:true})).createSyncAccessHandle();}
  catch(error){release();throw error;}
  if(backend==='memory')memory.set(id,{meta,parts:[]});
  const muxer=format==='opus'?new OggOpus(crypto.getRandomValues(new Uint32Array(1))[0]):null;
  const parts=muxer?muxer.headers():[wavHeader(0,rate)];
  session={meta,handle,release,muxer,parts,pendingBytes:parts.reduce((sum,part)=>sum+part.byteLength,0),committedBytes:0,committed:null,accepting:true,lastSequence:null,flushPending:false};
  meta.bytes=session.pendingBytes;
  try{await checkpoint(false);}catch(error){await failure(error);throw error;}
  return{...meta,backend};
}
function addParts(parts){
  for(const part of parts){session.parts.push(part);session.pendingBytes+=part.byteLength;session.meta.bytes+=part.byteLength;}
}
function append(data){
  if(!session?.accepting)return;
  if(data.profile!==session.meta.profile)return;
  const bytes=new Uint8Array(data.payload);
  if(session.meta.format==='wav'){
    if(data.rate!==session.meta.rate||bytes.byteLength%2)throw new Error('Recording sample rate changed');
    addParts([bytes]);session.meta.samples+=bytes.byteLength/2;
  }else{
    if(session.lastSequence!==null&&((data.sequence-session.lastSequence)>>>0)!==1)session.meta.gaps++;
    session.lastSequence=data.sequence;addParts(session.muxer.append(bytes));session.meta.samples=session.muxer.samples;
  }
  if(session.pendingBytes>=CHECKPOINT_BYTES)scheduleCheckpoint();
}
async function checkpoint(final){
  const current=session;if(!current)return;
  if(current.muxer)addParts(current.muxer.flush(final));
  const parts=current.parts.splice(0);current.pendingBytes=0;
  const meta={...current.meta,updated:Date.now(),completed:final,...(current.muxer?current.muxer.checkpoint():{})};
  if(backend==='opfs'){
    let offset=current.committedBytes;
    for(const part of parts){writeAll(current.handle,part,offset);offset+=part.byteLength;}
    if(meta.format==='wav')writeAll(current.handle,wavHeader(meta.bytes-WAV_HEADER_SIZE,meta.rate),0);
    else if(final){const patch=current.muxer.endPatch();if(patch)writeAll(current.handle,patch.bytes,patch.offset);}
    current.handle.flush();await saveMetadata(meta);
  }else if(backend==='indexeddb'){
    const tx=database.transaction(['recordings','chunks'],'readwrite');
    if(parts.length){
      // ArrayBuffer storage also works on WebKit versions that cannot put a
      // worker-created Blob in IndexedDB. Only the checkpoint is joined here.
      const bytes=new Uint8Array(parts.reduce((size,part)=>size+part.byteLength,0));
      let position=0;for(const part of parts){bytes.set(part,position);position+=part.byteLength;}
      tx.objectStore('chunks').put({id:meta.id,offset:current.committedBytes,data:bytes.buffer});
    }
    tx.objectStore('recordings').put(meta);await transactionDone(tx);
  }else{
    memory.get(meta.id).parts.push(...parts);await saveMetadata(meta);
  }
  current.committedBytes=meta.bytes;current.committed=meta;
  postMessage({type:'checkpoint',metadata:meta,backend});
  if(final){current.handle?.close();current.release();session=null;}
}
function enqueue(action){operations=operations.then(action).catch(async error=>{if(session)await failure(error);});return operations;}
function scheduleCheckpoint(){
  if(!session||session.flushPending)return;session.flushPending=true;
  enqueue(async()=>{if(!session)return;session.flushPending=false;await checkpoint(false);});
}
async function failure(error){
  const current=session;if(!current)return;
  current.accepting=false;try{current.handle?.close();}catch{}current.release();session=null;
  postMessage({type:'failure',error:error.name==='QuotaExceededError'?'El almacenamiento del navegador está lleno.':error.message,metadata:current.committed});
}
async function recordBlob(meta){
  let stored,blob;
  if(backend==='opfs'){
    const file=await(await directory.getFileHandle(meta.id+'.audio')).getFile();
    if(file.size<meta.bytes)throw new Error('Incomplete recording checkpoint');
    stored=file.slice(0,meta.bytes);
  }else{
    let parts;
    if(backend==='indexeddb'){
      const range=IDBKeyRange.bound([meta.id,0],[meta.id,Number.MAX_SAFE_INTEGER]);
      parts=(await requested(database.transaction('chunks').objectStore('chunks').getAll(range))).map(chunk=>chunk.data);
    }else parts=memory.get(meta.id).parts;
    stored=new Blob(parts).slice(0,meta.bytes);
    if(stored.size!==meta.bytes)throw new Error('Incomplete recording checkpoint');
  }
  // Repair only the exported Blob. Even a full/read-only store can export its
  // previous checkpoint, and another export cannot invalidate its File view.
  if(meta.format==='wav')blob=new Blob([wavHeader(meta.bytes-WAV_HEADER_SIZE,meta.rate),stored.slice(WAV_HEADER_SIZE)],{type:'audio/wav'});
  else{
    const page=new Uint8Array(await stored.slice(meta.lastPageOffset,meta.lastPageOffset+meta.lastPageSize).arrayBuffer());page[5]|=4;
    blob=new Blob([stored.slice(0,meta.lastPageOffset),oggChecksum(page),stored.slice(meta.lastPageOffset+meta.lastPageSize)],{type:'audio/ogg'});
  }
  await saveMetadata({...meta,completed:true}).catch(()=>{});
  return blob;
}
async function remove(meta){
  if(backend==='opfs'){await directory.removeEntry(meta.id+'.audio');await directory.removeEntry(meta.id+'.json');}
  else if(backend==='indexeddb'){
    const tx=database.transaction(['recordings','chunks'],'readwrite');tx.objectStore('recordings').delete(meta.id);
    tx.objectStore('chunks').delete(IDBKeyRange.bound([meta.id,0],[meta.id,Number.MAX_SAFE_INTEGER]));await transactionDone(tx);
  }else memory.delete(meta.id);
}
onmessage=({data})=>{
  if(data.type==='append'){try{append(data);}catch(error){enqueue(()=>failure(error));}return;}
  if(data.type==='stop'&&session)session.accepting=false;
  enqueue(async()=>{
    try{
      let result;
      if(data.type==='init')result=await initialize();
      else if(data.type==='start')result=await start(data.profile);
      else if(data.type==='stop'){await checkpoint(true);result=await list();}
      else if(data.type==='checkpoint'){await checkpoint(false);result=session?.committed||null;}
      else if(data.type==='list')result=await list();
      else if(data.type==='export'||data.type==='delete'){
        const meta=(await list()).find(item=>item.id===data.id);if(!meta)throw new Error('Recording unavailable or in use by another tab');
        if(data.type==='export')result={blob:await recordBlob(meta),metadata:meta};
        else{await remove(meta);result=await list();}
      }else throw new Error('Unknown recording command');
      reply(data.request,result);
    }catch(error){problem(data.request,error);if(session&&['start','stop','checkpoint'].includes(data.type))await failure(error);}
  });
};
setInterval(()=>{if(session)postMessage({type:'progress',metadata:{...session.meta}});},1000);
setInterval(()=>{if(session)scheduleCheckpoint();},CHECKPOINT_MS);
