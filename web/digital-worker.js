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
let countryDatabase=null;
let countryLoadStarted=false;
let countryRowSequence=0;
let pendingCountryRows=[];

function parseCountryDatabase(text){
  const exact=new Map(),prefixes=[];
  for(const record of text.split(';')){
    const fields=record.split(':');
    if(fields.length<8)continue;
    const country=fields[0].trim();
    if(!country)continue;
    for(const alias of fields.slice(7).join(':').replaceAll(/\s+/g,'').split(',')){
      if(!alias)continue;
      const isExact=alias.startsWith('=');
      const token=(isExact?alias.slice(1):alias).match(/^[A-Z0-9/]+/i)?.[0]?.toUpperCase();
      if(!token)continue;
      if(isExact)exact.set(token,country);else prefixes.push([token,country]);
    }
  }
  prefixes.sort((left,right)=>right[0].length-left[0].length);
  return {exact,prefixes};
}

function isCallsign(value){
  if(!/^[A-Z0-9]+(?:\/[A-Z0-9]+)*$/.test(value)||!/[A-Z]/.test(value)||!/[0-9]/.test(value))return false;
  return !/^[A-R]{2}\d{2}(?:[A-X]{2})?$/.test(value);
}

function callsignsFromMessage(text){
  return String(text||'').toUpperCase().split(/\s+/).map(value=>value.replace(/^[^A-Z0-9/]+|[^A-Z0-9/]+$/g,'')).filter(isCallsign).slice(0,2);
}

function countryForCallsign(callsign){
  if(!countryDatabase)return '';
  if(countryDatabase.exact.has(callsign))return countryDatabase.exact.get(callsign);
  for(const [prefix,country] of countryDatabase.prefixes)if(callsign.startsWith(prefix))return country;
  return '';
}

function countriesForMessage(text){
  const callsigns=callsignsFromMessage(text);
  if(!callsigns.length)return '';
  const source=countryForCallsign(callsigns[0])||'—';
  if(callsigns.length<2)return source;
  return `${source} → ${countryForCallsign(callsigns[1])||'—'}`;
}

async function loadCountryDatabase(){
  if(countryDatabase||countryLoadStarted)return countryDatabase;
  countryLoadStarted=true;
  try{
    const response=await fetch(new URL('./cty.dat',import.meta.url),{cache:'force-cache'});
    if(!response.ok)throw new Error(`country database request failed: ${response.status}`);
    countryDatabase=parseCountryDatabase(await response.text());
    for(const pending of pendingCountryRows)postMessage({type:'country-update',countryId:pending.countryId,countries:countriesForMessage(pending.text)});
    pendingCountryRows=[];
  }catch(error){
    countryLoadStarted=false;
    for(const pending of pendingCountryRows)postMessage({type:'country-update',countryId:pending.countryId,countries:'No disponible'});
    pendingCountryRows=[];
  }
  return countryDatabase;
}

function enrichCountry(result){
  result.countryId=++countryRowSequence;
  if(countryDatabase)result.countries=countriesForMessage(result.text);
  else{
    result.countries='Cargando…';
    result.countryPending=true;
    pendingCountryRows.push({countryId:result.countryId,text:result.text});
  }
  return result;
}

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
  void loadCountryDatabase();
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
    for(const result of decoded)postMessage({type:'decoded',result:enrichCountry(result)});
    postMessage({type:'progress',value:100});
    postMessage({type:'status',message:`${mode} · ${decoded.length} mensaje(s) decodificado(s)`,decodedCount:decoded.length});
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
