// SPDX-License-Identifier: GPL-3.0-only
'use strict';

let mode=null;
let rate=12000;
let slotSamples=180000;
let received=0;
let queuedBytes=0;
let chunks=[];
let firstTimestampUs=0;
let warned=false;
let wasmDecoder=null;
let wasmLoadStarted=false;
let lastStatus=0;

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
  received=0;
  queuedBytes=0;
  chunks=[];
  firstTimestampUs=0;
  warned=false;
  lastStatus=0;
  postMessage({type:'status',message:`${mode} activo · esperando slot de ${slotSamples/rate} s`});
  loadDecoder();
}

function consume(data){
  if(!mode||data.mode!==mode)return;
  const bytes=new Uint8Array(data.payload);
  if(!firstTimestampUs)firstTimestampUs=data.timestampUs;
  chunks.push(bytes);
  queuedBytes+=bytes.byteLength;
  received+=Math.floor(bytes.byteLength/2);
  const slotBytes=slotSamples*2;
  while(wasmDecoder&&queuedBytes>=slotBytes){
    const slot=new Uint8Array(slotBytes);
    let offset=0;
    while(offset<slotBytes&&chunks.length){
      const first=chunks[0],take=Math.min(first.byteLength,slotBytes-offset);
      slot.set(first.subarray(0,take),offset);
      offset+=take;
      queuedBytes-=take;
      if(take===first.byteLength)chunks.shift();
      else chunks[0]=first.subarray(take);
    }
    const decoded=wasmDecoder.decode_slot(mode,slot,firstTimestampUs)||[];
    firstTimestampUs=0;
    for(const result of decoded)postMessage({type:'decoded',result});
  }
  const now=performance.now();
  if(received>=slotSamples){
    const slots=Math.floor(received/slotSamples);
    received-=slots*slotSamples;
    postMessage({type:'status',message:wasmDecoder?`${mode} decodificando slot…`:`${mode} recibiendo 12 kHz · decoder pendiente`});
    lastStatus=now;
  }else if(now-lastStatus>500){
    const progress=Math.floor(received/slotSamples*100);
    postMessage({type:'status',message:`${mode} recibiendo 12 kHz · ${progress}% del slot`});
    lastStatus=now;
  }
}

self.onmessage=event=>{
  const data=event.data||{};
  if(data.type==='start')start(data);
  if(data.type==='samples')consume(data);
};
