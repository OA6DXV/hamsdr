// SPDX-License-Identifier: GPL-3.0-only
import init, {CwDecoder} from './cw-decoder.js';
import {CwSpectrum} from './cw-spectrum.mjs';
import {CwToneTracker,CwChannel} from './cw-detector.mjs';

let decoder=null,spectrum=null,tracker=null,configuration=null,lastSequence=null;
let elapsed=0,clock=0,tone=700,selected=null,selectedSeen=0,tracks=[],streams=new Map();
let history=[],historySamples=0;
const ready=init({module_or_path:new URL('./cw-decoder_bg.wasm',import.meta.url)});
function clearStreams(){for(const stream of streams.values())stream.decoder.free();streams.clear();}
function retune(next,acquire=false){
  if(acquire){decoder.free();decoder=new CwDecoder(next,configuration.wpm);let text='';for(const samples of history)text+=decoder.process(samples);if(text)postMessage({type:'text',text});}
  else decoder.retune(next);
  tone=next;
}
function analyse(data,transfers){
  tracks=tracker.update(data,clock);
  if(configuration.auto&&selected===null){
    const active=tracks.filter(t=>t.active);
    const candidate=active.find(t=>Math.abs(t.tone-configuration.tone)<40)||active.sort((a,b)=>b.snr-a.snr)[0];
    if(candidate){selected=candidate.id;selectedSeen=clock;if(Math.abs(candidate.tone-tone)>12)retune(candidate.tone,true);}
  }
  const current=tracks.find(t=>t.id===selected);
  if(current?.active) {
    selectedSeen=clock;
    if(configuration.afc&&Math.abs(current.tone-tone)>=1)retune(tone+Math.max(-8,Math.min(8,current.tone-tone)));
  }else if(clock-selectedSeen>5)selected=null;
  if(!configuration.auto&&configuration.afc) {
    const nearby=tracks.filter(t=>t.active&&Math.abs(t.tone-tone)<40).sort((a,b)=>Math.abs(a.tone-tone)-Math.abs(b.tone-tone))[0];
    if(nearby&&Math.abs(nearby.tone-tone)>=1)retune(tone+Math.max(-4,Math.min(4,nearby.tone-tone)));
  }
  if(configuration.multi) {
    const ids=new Set(tracks.map(t=>t.id));
    for(const [id,stream]of streams)if(!ids.has(id)){stream.decoder.free();streams.delete(id);}
    for(const track of tracks) {
      let stream=streams.get(track.id);
      if(!stream){stream={decoder:new CwDecoder(track.tone,configuration.wpm),channel:new CwChannel(track.tone,configuration.wpm),tone:track.tone,text:'',confirmed:false};stream.channel.setNoise(track.noise);for(const samples of history)stream.text+=stream.decoder.process(stream.channel.process(samples));streams.set(track.id,stream);}
      stream.channel.setNoise(track.noise);
      if(configuration.afc&&Math.abs(track.tone-stream.tone)>=1){stream.tone=track.tone;stream.decoder.retune(stream.tone);stream.channel.retune(stream.tone);}
    }
  }
  postMessage(data,transfers);
}
function reset(){
  decoder?.free();clearStreams();decoder=new CwDecoder(configuration.tone,configuration.wpm);
  tone=configuration.tone;selected=null;selectedSeen=0;tracks=[];
  tracker=new CwToneTracker(configuration);spectrum=new CwSpectrum(analyse);
  lastSequence=null;elapsed=0;clock=0;
  history=[];historySamples=0;
}
function status(){
  const nearby=tracks.some(t=>t.active&&Math.abs(t.tone-tone)<40);
  postMessage({type:'status',tone,offset:tone-configuration.tone,wpm:decoder.wpm(),locked:decoder.locked(),keyed:decoder.keyed(),detected:nearby});
  if(configuration.multi)postMessage({type:'streams',streams:tracks.map(track=>{
    const stream=streams.get(track.id);if(!stream)return null;
    return {id:track.id,tone:stream.tone,wpm:stream.decoder.wpm(),active:track.active,
      confirmed:stream.confirmed,text:stream.confirmed?stream.text:''};
  }).filter(Boolean)});
}
// Initialization is shared; PCM is accepted only after the UI receives ready.
self.onmessage=async({data})=>{
  try{
    await ready;
    if(data.type==='configure'){
      const previous=configuration;
      configuration={tone:Math.max(100,Math.min(5000,Number(data.tone)||700)),wpm:Math.max(5,Math.min(60,Number(data.wpm)||20)),
        minimum:Math.max(0,Number(data.minimum)||0),maximum:Math.min(5000,Number(data.maximum)||5000),
        auto:data.auto!==false,afc:data.afc!==false,multi:data.multi===true};
      // Toggling multi/AFC must not erase the selected decoder's learned speed.
      if(!decoder||['tone','wpm','minimum','maximum'].some(key=>previous[key]!==configuration[key]))reset();
      else {if(!configuration.multi)clearStreams();if(!configuration.auto)selected=null;}
      postMessage({type:'ready'});status();return;
    }
    if(data.type==='clear'){for(const stream of streams.values())stream.text='';return;}
    if(data.type!=='samples'||!decoder)return;
    if(lastSequence!==null&&((data.sequence-lastSequence)>>>0)!==1){
      const missing=(data.sequence-lastSequence)>>>0;
      if(missing>20)reset();
      else {decoder.gap();for(const stream of streams.values())stream.decoder.gap();spectrum=new CwSpectrum(analyse);history=[];historySamples=0;}
    }
    lastSequence=data.sequence;
    const raw=new Int16Array(data.payload),samples=Float32Array.from(raw,value=>value/32768);
    clock+=samples.length/12000;
    const text=decoder.process(samples);if(text)postMessage({type:'text',text});
    for(const stream of streams.values()){
      const decoded=stream.decoder.process(stream.channel.process(samples));stream.text=(stream.text+decoded).slice(-4000);
      const useful=(stream.text.match(/[A-Z0-9]/g)||[]).length,invalid=(stream.text.match(/·/g)||[]).length;
      // Carrier detection alone cannot confirm a CW stream or stationary noise.
      const diversity=new Set(stream.text.replace(/[^A-Z0-9]/g,'')).size;
      if(stream.decoder.locked()&&useful>=6&&diversity>=3&&invalid<=useful*.2&&stream.decoder.wpm()>=5&&stream.decoder.wpm()<=60)stream.confirmed=true;
    }
    // One second of bounded pre-roll retains the first character during acquisition.
    history.push(samples);historySamples+=samples.length;
    while(historySamples>12000&&history.length>1)historySamples-=history.shift().length;
    spectrum.push(samples);elapsed+=samples.length;
    if(elapsed>=2400){elapsed=0;status();}
    postMessage({type:'consumed'});
  }catch(error){postMessage({type:'error',message:String(error.message||error)});}
};
