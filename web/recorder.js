// SPDX-License-Identifier: GPL-3.0-only
'use strict';
(() => {
  const {wavHeader,OggOpus,oggChecksum}=HamRecorderCore;
  class RadioRecorder {
    constructor(onchange){
      this.onchange=onchange;this.active=false;this.busy=false;this.metadata=null;this.records=[];this.backend='memory';
      this.pending=new Map();this.serial=0;this.worker=null;this.local=false;this.localRecords=new Map();this.error='';this.sessionIds=new Set();
      this.ready=this.initialize();
    }
    state(){
      // Normal stops in this page use Save. Older unsaved recordings and
      // incomplete checkpoints remain available if a session is interrupted.
      const recoveries=this.records.filter(record=>!record.completed||!this.sessionIds.has(record.id));
      this.onchange?.({active:this.active,busy:this.busy,metadata:this.metadata,records:this.records,recoveries,backend:this.backend,error:this.error});
    }
    async initialize(){
      try{
        this.worker=new Worker(new URL('./recorder-worker.js',document.baseURI));
        this.worker.onmessage=({data})=>{
          if(data.type==='reply'||data.type==='error'){
            const waiting=this.pending.get(data.request);this.pending.delete(data.request);
            if(waiting){if(data.type==='error')waiting.reject(new Error(data.error));else waiting.resolve(data.result);}
          }else if(data.type==='progress'||data.type==='checkpoint'){this.metadata=data.metadata;this.state();}
          else if(data.type==='failure'){
            this.active=false;this.busy=false;this.metadata=data.metadata;this.error=data.error;this.state();void this.refresh();
          }
        };
        this.worker.onerror=()=>{
          const error=new Error('No se pudo iniciar el almacenamiento de grabación.');
          for(const waiting of this.pending.values())waiting.reject(error);this.pending.clear();
          if(this.active){this.active=false;this.error=error.message;this.state();}
        };
        const result=await this.request('init');this.backend=result.backend;
      }catch{this.worker?.terminate();this.worker=null;this.local=true;this.backend='memory';}
      this.state();await this.refresh();
      // A legacy browser without Web Locks may hide an abandoned checkpoint
      // for its 15-second lease. Refresh once after that lease expires.
      if(!navigator.locks?.query&&!this.local)setTimeout(()=>{void this.refresh().catch(()=>{});},16000);
      return this.backend;
    }
    request(type,data={}){
      const request=++this.serial;
      return new Promise((resolve,reject)=>{this.pending.set(request,{resolve,reject});this.worker.postMessage({type,request,...data});});
    }
    async refresh(){
      this.records=this.local?Array.from(this.localRecords.values(),record=>record.metadata):await this.request('list');this.state();return this.records;
    }
    async start(profile){
      await this.ready;if(this.active||this.busy)return;
      this.busy=true;this.error='';this.state();
      try{
        if(this.local){
          const format=['raw','digiraw'].includes(profile)?'wav':'opus',rate=profile==='digiraw'?12000:format==='wav'?16000:48000,started=Date.now();
          const id=crypto.randomUUID?crypto.randomUUID():'local-'+started;
          this.muxer=format==='opus'?new OggOpus(Math.floor(Math.random()*0xffffffff)):null;
          this.parts=this.muxer?this.muxer.headers():[wavHeader(0,rate)];
          this.metadata={id,profile,format,rate,started,samples:0,bytes:this.parts.reduce((sum,part)=>sum+part.length,0),filename:'hamsdr-'+new Date(started).toISOString().replaceAll(':','-')+'.'+(format==='wav'?'wav':'opus')};
          this.localTimer=setInterval(()=>this.state(),1000);
        }else this.metadata=await this.request('start',{profile});
        this.sessionIds.add(this.metadata.id);this.active=true;
      }finally{this.busy=false;this.state();}
    }
    pcm(payload,rate){
      if(!this.active||this.metadata.format!=='wav')return;
      if(rate!==this.metadata.rate){this.error='La frecuencia de muestreo cambió; se detuvo la grabación.';void this.stop();return;}
      if(this.local){const bytes=new Uint8Array(payload);this.parts.push(bytes);this.metadata.bytes+=bytes.length;this.metadata.samples+=bytes.length/2;}
      else this.worker.postMessage({type:'append',profile:this.metadata.profile,rate,payload},[payload]);
    }
    opus(packet,sequence){
      if(!this.active||this.metadata.format!=='opus')return;
      const payload=packet.slice().buffer;
      if(this.local){const parts=this.muxer.append(new Uint8Array(payload));this.parts.push(...parts);this.metadata.bytes+=parts.reduce((sum,part)=>sum+part.length,0);this.metadata.samples=this.muxer.samples;}
      else this.worker.postMessage({type:'append',profile:this.metadata.profile,sequence,payload},[payload]);
    }
    async stop(){
      if(!this.active)return;
      this.active=false;this.busy=true;this.state();
      try{
        if(this.local){
          clearInterval(this.localTimer);
          if(this.muxer){const parts=this.muxer.flush(true);this.parts.push(...parts);this.metadata.bytes+=parts.reduce((sum,part)=>sum+part.length,0);Object.assign(this.metadata,this.muxer.checkpoint());}
          this.metadata.completed=true;this.localRecords.set(this.metadata.id,{metadata:{...this.metadata},blob:this.localBlob()});this.parts=[];this.muxer=null;
        }else await this.request('stop');
        await this.refresh();
      }finally{this.busy=false;this.state();}
    }
    localBlob(){
      const stored=new Blob(this.parts),meta=this.metadata;
      if(meta.format==='wav')return new Blob([wavHeader(meta.bytes-80,meta.rate),stored.slice(80)],{type:'audio/wav'});
      const patch=this.muxer.endPatch();return patch?new Blob([stored.slice(0,patch.offset),patch.bytes,stored.slice(patch.offset+patch.bytes.length)],{type:'audio/ogg'}):stored;
    }
    async export(id){
      await this.ready;
      if(this.local){const record=this.localRecords.get(id);if(!record)throw new Error('Grabación no disponible');return record;}
      return this.request('export',{id});
    }
    async remove(id){
      if(this.local)this.localRecords.delete(id);else await this.request('delete',{id});
      if(this.metadata?.id===id&&!this.active)this.metadata=null;
      await this.refresh();
    }
    checkpoint(){if(this.active&&!this.local)void this.request('checkpoint').catch(error=>{this.error=error.message;this.state();});}
  }
  globalThis.RadioRecorder=RadioRecorder;
})();
