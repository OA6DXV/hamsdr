// SPDX-License-Identifier: GPL-3.0-only
import init, {CwDecoder} from './cw-decoder.js';
import {CwSpectrum} from './cw-spectrum.mjs';
import {CwToneTracker,cwCarrierPresent} from './cw-detector.mjs';

let decoder=null,spectrum=null,tracker=null,configuration=null,lastSequence=null;
let elapsed=0,clock=0,tone=700,selected=null,selectedSeen=0,acquired=false,anchorWpm=20,anchorLevel=-160,tracks=[],streams=new Map();
let history=[],historySamples=0,historyTime=0,lastPublishedTime=-Infinity;
const ready=init({module_or_path:new URL('./cw-decoder_bg.wasm',import.meta.url)});
function clearStreams(){for(const stream of streams.values())stream.decoder.free();streams.clear();}
function retune(next,acquire=false){
  // The selected candidate has already learned timing from isolated audio.
  // Publish its bounded pre-roll once; subsequent samples emit only new text.
  if(configuration.auto){if(acquire){
    const entries=streams.get(selected).entries.filter(e=>e.time>lastPublishedTime);
    const text=entries.map(e=>e.text).join('');
    if(text){postMessage({type:'text',text});lastPublishedTime=entries.at(-1).time;}
  }}
  else decoder.retune(next);
  tone=next;
  if(configuration.auto&&acquire){anchorWpm=streams.get(selected).decoder.wpm();anchorLevel=tracks.find(t=>t.id===selected).level;}
}
function analyse(data,transfers){
  tracker.protected=new Set([...streams].filter(([id,s])=>id===selected||s.confirmed).map(([id])=>id));
  tracks=tracker.update(data,clock);
  // Candidate decoders validate timing even in single mode. FFT peaks alone
  // cannot distinguish a CW transmission from a carrier or a noise impulse.
  const ids=new Set(tracks.map(t=>t.id));
  for(const [id,stream]of streams)if(!ids.has(id)){stream.decoder.free();streams.delete(id);}
  for(const track of tracks){
    let stream=streams.get(track.id);
    if(!stream){
      stream={decoder:new CwDecoder(track.tone,configuration.wpm),tone:track.tone,text:'',evidence:'',entries:[],carrierSeen:clock,confirmed:false};
      let time=historyTime-historySamples/12000;
      for(const samples of history){time+=samples.length/12000;const text=stream.decoder.process(samples);if(text){stream.text+=text;stream.entries.push({time,text});}}
      stream.evidence=stream.text.slice(-100);
      streams.set(track.id,stream);
    }
    if(configuration.afc&&Math.abs(track.tone-stream.tone)>=1){stream.tone=track.tone;stream.decoder.retune(stream.tone);}
    if(cwCarrierPresent(data,stream.tone))stream.carrierSeen=clock;
  }
  const valid=tracks.filter(t=>{
    const stream=streams.get(t.id);if(!t.active||!stream?.confirmed)return false;
    if(!acquired)return true;
    const ratio=stream.decoder.wpm()/anchorWpm;
    return ratio>=.5&&ratio<=2&&t.level>=anchorLevel-20;
  });
  if(configuration.auto&&selected===null){
    const candidate=valid.filter(t=>Math.abs(t.tone-tone)<150).sort((a,b)=>b.level-a.level)[0]
      ||(!acquired&&valid.filter(t=>streams.get(t.id).decoder.confidence()>=.9).sort((a,b)=>b.level-a.level)[0]);
    if(candidate){selected=candidate.id;selectedSeen=clock;acquired=true;retune(candidate.tone,true);}
  }
  const current=tracks.find(t=>t.id===selected);
  if(current?.active) {
    selectedSeen=clock;
    const stream=streams.get(selected);
    if(stream.confirmed){anchorWpm=stream.decoder.wpm();anchorLevel=Math.max(current.level,anchorLevel-.5);}
    if(configuration.afc)tone=streams.get(selected).tone;
  }else if(configuration.auto){
    // Preserve selection through word gaps/fading; reacquire nearby stepped
    // tones only after an independent decoder confirms actual Morse cadence.
    const replacement=valid.filter(t=>t.id!==selected&&Math.abs(t.tone-tone)<150)
      .sort((a,b)=>b.level-a.level)[0];
    if(replacement&&clock-selectedSeen>3){selected=replacement.id;selectedSeen=clock;retune(replacement.tone,true);}
    else if(clock-selectedSeen>12)selected=null;
  }
  if(!configuration.auto&&configuration.afc) {
    const nearby=valid.filter(t=>Math.abs(t.tone-tone)<40).sort((a,b)=>Math.abs(a.tone-tone)-Math.abs(b.tone-tone))[0];
    if(nearby&&Math.abs(nearby.tone-tone)>=1)retune(tone+Math.max(-4,Math.min(4,nearby.tone-tone)));
  }
  postMessage(data,transfers);
}
function reset(){
  decoder?.free();clearStreams();decoder=new CwDecoder(configuration.tone,configuration.wpm);
  tone=configuration.tone;selected=null;selectedSeen=0;acquired=false;anchorWpm=configuration.wpm;anchorLevel=-160;tracks=[];
  tracker=new CwToneTracker(configuration);spectrum=new CwSpectrum(analyse);
  lastSequence=null;elapsed=0;clock=0;
  history=[];historySamples=0;historyTime=0;lastPublishedTime=-Infinity;
}
function status(){
  const nearby=tracks.some(t=>t.active&&Math.abs(t.tone-tone)<40);
  const primary=configuration.auto?streams.get(selected)?.decoder:decoder;
  postMessage({type:'status',time:clock,tone,offset:tone-configuration.tone,wpm:primary?.wpm()??configuration.wpm,confidence:primary?.confidence()??0,locked:primary?.locked()??false,keyed:primary?.keyed()??false,detected:nearby});
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
      else if(previous.auto&&!configuration.auto){
        // Turning acquisition off keeps the actual selected tone and learned
        // clock. Transfer ownership so each WASM decoder is freed once.
        const stream=streams.get(selected);
        if(stream){decoder.free();decoder=stream.decoder;tone=stream.tone;streams.delete(selected);}
        selected=null;
      }
      postMessage({type:'ready'});status();return;
    }
    if(data.type==='clear'){for(const stream of streams.values()){stream.text='';stream.entries=[];}lastPublishedTime=clock;return;}
    if(data.type!=='samples'||!decoder)return;
    if(lastSequence!==null&&((data.sequence-lastSequence)>>>0)!==1){
      const missing=(data.sequence-lastSequence)>>>0;
      if(missing>20)reset();
      else {decoder.gap();for(const stream of streams.values())stream.decoder.gap();spectrum=new CwSpectrum(analyse);history=[];historySamples=0;}
    }
    lastSequence=data.sequence;
    const raw=new Int16Array(data.payload),samples=Float32Array.from(raw,value=>value/32768);
    clock+=samples.length/12000;
    // Inspect this block before decoding it; stale FFT activity must not blank
    // the first dit when a station starts again after a word gap or fading.
    spectrum.push(samples);
    if(!configuration.auto){const text=decoder.process(samples);if(text)postMessage({type:'text',text});}
    for(const [id,stream]of streams){
      // Keep the clock/last character alive through pauses, without allowing
      // a fading carrier's neighbour to become its next decoded word.
      const input=clock-stream.carrierSeen<.45?samples:new Float32Array(samples.length);
      const decoded=stream.decoder.process(input);stream.text=(stream.text+decoded).slice(-4000);
      if(decoded){stream.entries.push({time:clock,text:decoded});if(stream.entries.length>128)stream.entries.shift();}
      if(configuration.auto&&id===selected&&decoded){postMessage({type:'text',text:decoded});lastPublishedTime=clock;}
      stream.evidence=(stream.evidence+decoded).slice(-100);
      const recent=stream.evidence,useful=(recent.match(/[A-Z0-9]/g)||[]).length,invalid=(recent.match(/·/g)||[]).length;
      // Carrier detection alone cannot confirm a CW stream or stationary noise.
      const diversity=new Set(recent.replace(/[^A-Z0-9]/g,'')).size;
      const isolated=(recent.match(/[ET]/g)||[]).length;
      const quality=stream.decoder.confidence();
      const evidence=quality>=.9?useful>=4&&diversity>=3:useful>=8&&diversity>=4;
      stream.confirmed=stream.decoder.locked()&&quality>=.82&&evidence&&invalid<=useful*.2&&isolated<=useful*.55;
    }
    // Four seconds of bounded pre-roll support slow CW without unbounded audio.
    history.push(samples);historySamples+=samples.length;historyTime=clock;
    while(historySamples>48000&&history.length>1)historySamples-=history.shift().length;
    elapsed+=samples.length;
    if(elapsed>=2400){elapsed=0;status();}
    postMessage({type:'consumed'});
  }catch(error){postMessage({type:'error',message:String(error.message||error)});}
};
