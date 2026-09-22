// SPDX-License-Identifier: GPL-3.0-only
'use strict';

let mode=null;
let rate=12000;
let slotSamples=180000;
let warned=false;
let wasmDecoder=null;
let wasmLoadStarted=false;
let lastProgress=0;
let slotIndex=null;
let slotBuffer=null;
let slotFirstSample=0;
let slotLastSample=0;
let nextAbsoluteSample=null;
let lastSequence=null;

async function loadDecoder(){
  if(wasmDecoder||wasmLoadStarted)return wasmDecoder;
  wasmLoadStarted=true;
  try{
    const module=await import('./mfsk-decoder.js');
    if(typeof module.default==='function')await module.default(new URL('./mfsk-decoder_bg.wasm',import.meta.url));
    if(typeof module.decode_slot==='function')wasmDecoder=module;
  }catch(error){
    if(!warned){
      warned=true;
      postMessage({type:'notice',message:'Decoder MFSK/WASM no instalado todavía; el flujo digital 12 kHz está activo.'});
    }
  }
  return wasmDecoder;
}

function start(data){
  mode=data.mode;
  rate=data.rate||12000;
  slotSamples=mode==='FT4'?Math.round(rate*7.5):Math.round(rate*15);
  warned=false;
  lastProgress=0;
  slotIndex=null;
  slotBuffer=null;
  slotFirstSample=0;
  slotLastSample=0;
  nextAbsoluteSample=null;
  lastSequence=null;
  postMessage({type:'progress',value:0});
  postMessage({type:'status',message:`${mode} activo · esperando sincronización`});
  loadDecoder();
}

function beginSlot(index,offset){
  slotIndex=index;
  slotBuffer=new Uint8Array(slotSamples*2);
  slotFirstSample=offset;
  slotLastSample=offset;
}

function finishSlot(){
  if(slotIndex===null||!slotBuffer)return;
  const tolerance=Math.round(rate*.1);
  if(wasmDecoder&&slotFirstSample<=tolerance&&slotLastSample>=slotSamples-tolerance){
    const timestampUs=slotIndex*slotSamples/rate*1_000_000;
    const decoded=wasmDecoder.decode_slot(mode,slotBuffer,timestampUs)||[];
    for(const result of decoded)postMessage({type:'decoded',result});
    postMessage({type:'progress',value:100});
    postMessage({type:'status',message:`${mode} · ${decoded.length} mensaje(s) decodificado(s)`});
  }else if(wasmDecoder){
    postMessage({type:'progress',value:0});
    postMessage({type:'status',message:`${mode} sincronizando…`});
  }
  lastProgress=performance.now();
  slotIndex=null;
  slotBuffer=null;
}

function consume(data){
  if(!mode||data.mode!==mode)return;
  const bytes=new Uint8Array(data.payload);
  const sampleCount=Math.min(Number(data.sampleCount)||0,Math.floor(bytes.byteLength/2));
  if(!sampleCount)return;
  const sequence=Number(data.sequence)>>>0;
  const continuous=lastSequence===null||((sequence-lastSequence)>>>0)===1;
  let absoluteSample=continuous&&nextAbsoluteSample!==null
    ?nextAbsoluteSample
    :Math.round(Number(data.timestampUs)*rate/1_000_000)-sampleCount;
  if(!continuous)finishSlot();
  lastSequence=sequence;
  let sourceSample=0;
  while(sourceSample<sampleCount){
    const index=Math.floor(absoluteSample/slotSamples);
    const offset=absoluteSample-index*slotSamples;
    if(slotIndex!==index){
      finishSlot();
      beginSlot(index,offset);
    }
    const take=Math.min(sampleCount-sourceSample,slotSamples-offset);
    slotBuffer.set(bytes.subarray(sourceSample*2,(sourceSample+take)*2),offset*2);
    slotFirstSample=Math.min(slotFirstSample,offset);
    slotLastSample=Math.max(slotLastSample,offset+take);
    sourceSample+=take;
    absoluteSample+=take;
    if(offset+take===slotSamples)finishSlot();
  }
  nextAbsoluteSample=absoluteSample;
  const now=performance.now();
  if(now-lastProgress>100&&slotIndex!==null){
    postMessage({type:'progress',value:Math.floor(slotLastSample/slotSamples*100)});
    lastProgress=now;
  }
}

self.onmessage=event=>{
  const data=event.data||{};
  if(data.type==='start')start(data);
  if(data.type==='samples')consume(data);
};
