// SPDX-License-Identifier: GPL-3.0-only
import createDecoder from './rtty-fldigi.mjs';
import {CwSpectrum} from './cw-spectrum.mjs';
import {findRttyCandidates} from './rtty-core.mjs';

let runtime,handle=0,configuration={},spectrum,lastSequence=null,signature='',queue=Promise.resolve();
let present=0,missing=0,open=false,pendingText='',statusSamples=0;
const loading=createDecoder({locateFile:name=>new URL(name,import.meta.url).href}).then(module=>{runtime=module;handle=runtime._rtty_create();});
function reset(){
  const c=configuration;
  runtime._rtty_configure(handle,12000,c.baud||45.45,c.shift||170,c.centerFrequency||1000,Number(!!c.reverse),Number(c.afc!==false),c.afcRange??50);
  present=missing=statusSamples=0;open=false;pendingText='';lastSequence=null;
  spectrum=new CwSpectrum((message,transfers)=>{
    const center=(c.centerFrequency||1000)+runtime._rtty_metric(handle,1);
    const candidates=findRttyCandidates(message.levels,message.sampleRate,message.fftSize,{low:c.low??0,high:c.high??3000,shift:c.shift||170,thresholdDb:7,maxCandidates:8});
    const found=candidates.some(candidate=>Math.abs(candidate.centerFrequency-center)<=Math.max(45,(c.shift||170)*.25));
    if(found){present=Math.min(12,present+1);missing=0;}
    else {present=Math.max(0,present-1);if(++missing>=8){open=false;pendingText='';}}
    postMessage(message,transfers);
  });
}
async function receive(data){
  await loading;
  if(data.type==='config'){
    const next=JSON.stringify([data.baud,data.shift,data.centerFrequency,data.reverse,data.afc,data.afcRange,data.low,data.high,data.frequency,data.demodulation]);
    configuration=data;if(next!==signature){signature=next;reset();}
    postMessage({type:'ready'});return;
  }
  if(data.type==='reset'){reset();postMessage({type:'ready'});return;}
  if(data.type!=='samples'||!spectrum)return;
  if(lastSequence!==null&&((data.sequence-lastSequence)>>>0)!==1)reset();lastSequence=data.sequence;
  const samples=Float32Array.from(new Int16Array(data.payload),value=>value/32768),pointer=runtime._malloc(samples.length*4);
  let text='';
  try{
    runtime.HEAPF32.set(samples,pointer/4);
    text=runtime.UTF8ToString(runtime._rtty_process(handle,pointer,samples.length));
  }finally{runtime._free(pointer);}
  spectrum.push(samples);
  const confidence=runtime._rtty_metric(handle,0),offset=runtime._rtty_metric(handle,1),quality=runtime._rtty_metric(handle,2),frames=runtime._rtty_metric(handle,3);
  pendingText=(pendingText+text).slice(-256);
  if(present>=3&&frames>=6&&quality>=.72&&confidence>=.25)open=true;
  if(open&&frames>=12&&quality<.42){open=false;pendingText='';}
  if(open&&pendingText&&missing<8){postMessage({type:'character',value:pendingText});pendingText='';}
  const center=(configuration.centerFrequency||1000)+offset,shift=configuration.shift||170;
  statusSamples+=samples.length;
  if(statusSamples>=2400){statusSamples=0;postMessage({type:'status',markFrequency:center-shift/2,spaceFrequency:center+shift/2,afcOffset:offset,confidence,validation:open?'locked':present?'candidate':'idle'});}
  postMessage({type:'consumed'});
}
// Inference and configuration are serialized; the UI bounds the PCM backlog.
self.onmessage=({data})=>{queue=queue.then(()=>receive(data)).catch(error=>postMessage({type:'error',message:`RTTY Experimental: ${error.message||error}`}));};
