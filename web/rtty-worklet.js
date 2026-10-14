// SPDX-License-Identifier: GPL-3.0-only
import {findRttyCandidates,Ita2Decoder,RttyDecoder} from './rtty-core.mjs';

export const MULTI_PROFILES=Object.freeze([
  Object.freeze({id:'45',label:'45.45/170',baud:45.45,shift:170}),
  Object.freeze({id:'50',label:'50/170',baud:50,shift:170}),
  Object.freeze({id:'75',label:'75/170',baud:75,shift:170})
]);

export class RttyFrameGate{
  constructor(emit){this.emit=emit;this.ita2=new Ita2Decoder();this.reset();}
  reset(){this.starts=[];this.frames=[];this.words=[];this.open=false;this.ita2.reset();}
  metrics(){
    const startQuality=this.starts.length?this.starts.filter(Boolean).length/this.starts.length:0;
    const frameQuality=this.frames.length?this.frames.filter(Boolean).length/this.frames.length:0;
    return{startQuality,frameQuality};
  }
  frame(frame,evidence=true){
    if(!evidence){this.words=[];if(this.open){this.open=false;this.ita2.reset();}}
    this.starts.push(frame.startValid!==false);if(this.starts.length>24)this.starts.shift();
    if(frame.startValid===false){
      const{startQuality}=this.metrics();
      if(this.open&&this.starts.length>=12&&startQuality<.35){this.open=false;this.words=[];this.ita2.reset();}
      return;
    }
    this.frames.push(Boolean(frame.valid));if(this.frames.length>24)this.frames.shift();
    const{startQuality,frameQuality}=this.metrics();
    if(frame.valid&&evidence){
      if(this.open){const value=this.ita2.decode(frame.word);if(value)this.emit(value);}
      else{this.words.push(frame.word);if(this.words.length>32)this.words.shift();}
    }
    const strongShortWindow=this.starts.length>=6&&this.frames.length>=6&&startQuality>=.8&&frameQuality>=.9&&evidence;
    const stableWindow=this.starts.length>=12&&this.frames.length>=12&&startQuality>=.55&&frameQuality>=.72&&evidence;
    if(!this.open&&(strongShortWindow||stableWindow)){
      this.open=true;this.ita2.reset();for(const word of this.words){const value=this.ita2.decode(word);if(value)this.emit(value);}this.words=[];
    }
    if(this.open&&((this.frames.length>=12&&frameQuality<.42)||(this.starts.length>=12&&startQuality<.35)||!evidence)){this.open=false;this.words=[];this.ita2.reset();}
  }
}

export class RttyOutputGate{
  constructor(post){this.post=post;this.frameGate=new RttyFrameGate(value=>this.post({type:'character',value}));this.configure({});this.reset();}
  configure(options){const center=Number(options.centerFrequency??this.center??1000),shift=Number(options.shift??this.shift??170),baud=Number(options.baud??this.baud??45.45),reverse=Boolean(options.reverse??this.reverse??false),sampleRate=Number(options.sampleRate??this.sampleRate??12000),low=Number(options.low??this.low??0),high=Number(options.high??this.high??3000),changed=center!==this.center||shift!==this.shift||baud!==this.baud||reverse!==this.reverse||sampleRate!==this.sampleRate||low!==this.low||high!==this.high;this.center=center;this.shift=shift;this.baud=baud;this.reverse=reverse;this.sampleRate=sampleRate;this.low=low;this.high=high;if(changed&&this.frameGate)this.reset();}
  reset(){this.present=0;this.missing=0;this.signalScore=0;this.frameGate.reset();}
  updateSpectrum(message){
    const candidates=findRttyCandidates(message.levels,message.sampleRate,message.fftSize,{low:this.low,high:this.high,shift:this.shift,thresholdDb:6,maxCandidates:8});
    const candidate=candidates.reduce((best,item)=>Math.abs(item.centerFrequency-this.center)<Math.abs((best?.centerFrequency??Infinity)-this.center)?item:best,null);
    if(candidate&&Math.abs(candidate.centerFrequency-this.center)<=Math.max(70,this.shift*.35)){this.present=Math.min(12,this.present+1);this.missing=0;this.signalScore=candidate.score;}
    else{this.present=Math.max(0,this.present-1);this.missing++;if(this.missing===8)this.frameGate.reset();}
  }
  frame(frame){this.frameGate.frame(frame,this.present>=3);}
  status(status){
    const{startQuality,frameQuality}=this.frameGate.metrics(),spectral=Math.min(1,this.signalScore/14),confidence=this.present?Math.min(1,spectral*.35+frameQuality*.4+startQuality*.25):0;
    this.post({type:'status',...status,confidence,validation:this.frameGate.open?'locked':this.present?'candidate':'idle'});
  }
}

export class RttyProfileScout{
  constructor(post,rate){this.post=post;this.sampleRate=rate;this.configure({});this.reset();}
  configure(options){const rate=Number(options.sampleRate??this.sampleRate),reverse=Boolean(options.reverse??this.reverse??false),selectedBaud=Number(options.baud??this.selectedBaud??45.45),selectedShift=Number(options.shift??this.selectedShift??170),rebuild=rate!==this.sampleRate||reverse!==this.reverse||selectedBaud!==this.selectedBaud||selectedShift!==this.selectedShift;this.sampleRate=rate;this.enabled=!Boolean(options.multi);this.low=Number(options.low??this.low??0);this.high=Number(options.high??this.high??3000);this.selectedBaud=selectedBaud;this.selectedShift=selectedShift;this.reverse=reverse;if(!this.enabled||(rebuild&&this.entries?.length))this.reset();}
  reset(){this.center=null;this.lastSeen=0;this.frames=0;this.entries=[];this.notified=null;this.ignored=null;}
  build(center){
    this.center=center;this.frames=0;this.entries=MULTI_PROFILES.map(profile=>{const stats={valid:0,total:0,startPass:0,startFail:0,center};const decoder=new RttyDecoder({sampleRate:this.sampleRate,baud:profile.baud,shift:profile.shift,centerFrequency:center,reverse:this.reverse,afc:true,afcRange:45,onFrame:frame=>{if(frame.startValid===false)stats.startFail++;else{stats.startPass++;stats.total++;if(frame.valid)stats.valid++;}if(stats.total+stats.startFail>80){stats.total=Math.ceil(stats.total/2);stats.valid=Math.ceil(stats.valid/2);stats.startPass=Math.ceil(stats.startPass/2);stats.startFail=Math.ceil(stats.startFail/2);}},onStatus:status=>{stats.center=(status.markFrequency+status.spaceFrequency)/2;}});return{profile,stats,decoder};});
  }
  updateSpectrum(message){
    if(!this.enabled)return;
    const candidates=findRttyCandidates(message.levels,message.sampleRate,message.fftSize,{low:this.low,high:this.high,shift:170,thresholdDb:8,maxCandidates:4});
    const candidate=candidates.sort((a,b)=>b.score-a.score)[0];
    if(!candidate){if(++this.lastSeen>18)this.reset();return;}this.lastSeen=0;
    if(this.center===null||Math.abs(this.center-candidate.centerFrequency)>70)this.build(candidate.centerFrequency);
    if(++this.frames<18)return;
    const ranked=this.entries.map(entry=>{const{stats}=entry,startEvents=stats.startPass+stats.startFail,startAcceptance=startEvents?stats.startPass/startEvents:0,stopValidity=stats.total>=8?stats.valid/stats.total:0;return{...entry,startAcceptance,quality:startEvents>=12?stopValidity*startAcceptance:0};}).sort((a,b)=>b.quality-a.quality),winner=ranked[0],runner=ranked[1];
    const same=Math.abs(winner.profile.baud-this.selectedBaud)<.1&&winner.profile.shift===this.selectedShift;
    if(!same&&winner.quality>=.78&&winner.quality-(runner?.quality??0)>=.10&&this.notified!==winner.profile.id&&this.ignored!==winner.profile.id){this.notified=winner.profile.id;this.post({type:'profile-detected',profile:winner.profile.id,label:winner.profile.label,baud:winner.profile.baud,shift:winner.profile.shift,centerFrequency:Math.round(winner.stats.center),confidence:winner.quality});}
  }
  process(input){if(this.enabled)for(const entry of this.entries)entry.decoder.process(input);}
  ignore(profile){this.ignored=profile;}
}

export class RttyMultiDetector{
  constructor(post,rate){this.post=post;this.sampleRate=rate;this.enabled=false;this.streams=[];this.pending=[];this.nextId=1;this.scanNumber=0;this.average=null;this.profileKey=null;this.configure({});}
  configure(options){
    const wasEnabled=this.enabled;
    this.sampleRate=Number(options.sampleRate??this.sampleRate);
    this.enabled=Boolean(options.multi??this.enabled);this.low=Number(options.low??this.low??0);this.high=Number(options.high??this.high??3000);
    this.reverse=Boolean(options.reverse??this.reverse??false);this.selectedBaud=Number(options.baud??this.selectedBaud??45.45);this.selectedShift=Number(options.shift??this.selectedShift??170);this.allProfiles=Boolean(options.multiProfiles??this.allProfiles??false);
    const profileKey=this.allProfiles?'all-standard':`selected:${this.selectedBaud}/${this.selectedShift}`,profilesChanged=this.profileKey!==profileKey;this.profileKey=profileKey;
    if(!this.enabled){if(wasEnabled)this.clear();return;}
    if(!wasEnabled||profilesChanged){this.clear();return;}
    for(const stream of this.streams)for(const entry of stream.decoders){entry.decoder.configure({sampleRate:this.sampleRate,centerFrequency:stream.centerFrequency,reverse:this.reverse,afc:true});entry.gate.reset();}
  }
  clear(){this.streams=[];this.pending=[];this.average=null;this.post({type:'multi-streams',streams:[]});}
  profilesToDecode(){return this.allProfiles?MULTI_PROFILES:[{id:'selected',label:`${this.selectedBaud}/${this.selectedShift}`,baud:this.selectedBaud,shift:this.selectedShift}];}
  createStream(candidate){
    const stream={id:this.nextId++,centerFrequency:candidate.centerFrequency,score:candidate.score,lastSeen:this.scanNumber,decoders:[]};
    stream.decoders=this.profilesToDecode().map(profile=>{const gate=new RttyFrameGate(value=>this.post({type:'multi-character',id:stream.id,centerFrequency:stream.centerFrequency,profile:profile.id,profileLabel:profile.label,value}));const decoder=new RttyDecoder({sampleRate:this.sampleRate,baud:profile.baud,shift:profile.shift,centerFrequency:stream.centerFrequency,reverse:this.reverse,afc:true,afcRange:45,filterBandwidth:Math.max(250,profile.shift+100),onFrame:frame=>gate.frame(frame)});return{profile,decoder,gate};});
    this.streams.push(stream);
  }
  updateSpectrum(message){
    if(!this.enabled)return;
    const levels=message.levels;
    if(!this.average||this.average.length!==levels.length)this.average=Float32Array.from(levels);
    else for(let index=0;index<levels.length;index++)this.average[index]=this.average[index]*.78+levels[index]*.22;
    if(++this.scanNumber%3)return;
    const shifts=this.allProfiles?[...new Set(MULTI_PROFILES.map(profile=>profile.shift))]:[this.selectedShift],found=[];
    for(const shift of shifts){
      for(const candidate of findRttyCandidates(this.average,message.sampleRate,message.fftSize,{low:this.low,high:this.high,shift,thresholdDb:7,maxCandidates:8}).sort((a,b)=>b.score-a.score)){
        const duplicate=found.find(item=>Math.abs(item.centerFrequency-candidate.centerFrequency)<Math.max(55,shift*.45));
        if(duplicate){if(candidate.score>duplicate.score)Object.assign(duplicate,candidate);continue;}
        found.push(candidate);
      }
    }
    found.sort((a,b)=>b.score-a.score);
    for(const candidate of found){
      const stream=this.streams.find(item=>Math.abs(item.centerFrequency-candidate.centerFrequency)<70);
      if(stream){stream.lastSeen=this.scanNumber;stream.score=candidate.score;continue;}
      let pending=this.pending.find(item=>Math.abs(item.centerFrequency-candidate.centerFrequency)<70);
      if(!pending){pending={...candidate,hits:0,lastSeen:this.scanNumber};this.pending.push(pending);}
      pending.centerFrequency=(pending.centerFrequency*pending.hits+candidate.centerFrequency)/(pending.hits+1);pending.score=candidate.score;pending.hits++;pending.lastSeen=this.scanNumber;
      if(pending.hits>=2&&this.streams.length<8){this.createStream(pending);this.pending=this.pending.filter(item=>item!==pending);}
    }
    this.pending=this.pending.filter(item=>this.scanNumber-item.lastSeen<=9);
    this.streams=this.streams.filter(stream=>this.scanNumber-stream.lastSeen<=60);
    this.post({type:'multi-streams',streams:this.streams.map(({id,centerFrequency,score,decoders})=>({id,centerFrequency,score,profiles:decoders.map(({profile})=>({id:profile.id,label:profile.label}))}))});
  }
  process(input){if(this.enabled)for(const stream of this.streams)for(const entry of stream.decoders)entry.decoder.process(input);}
}

class RttySpectrum{
  constructor(post,rate){
    this.post=post;this.sampleRate=rate;this.size=2048;this.real=new Float64Array(this.size);this.imag=new Float64Array(this.size);
    this.buffer=new Float32Array(this.size);this.count=0;this.skip=0;
  }
  setSampleRate(rate){if(rate===this.sampleRate)return;this.sampleRate=rate;this.count=0;this.skip=0;}
  push(input){
    for(let index=0;index<input.length;index++){
      if(this.skip){this.skip--;continue;}
      this.buffer[this.count++]=input[index];
      if(this.count===this.size){this.emit();this.count=0;this.skip=Math.max(0,Math.round(this.sampleRate/6)-this.size);}
    }
  }
  emit(){
    const size=this.size;
    for(let index=0;index<size;index++){this.real[index]=this.buffer[index]*(.5-.5*Math.cos(2*Math.PI*index/(size-1)));this.imag[index]=0;}
    for(let index=1,j=0;index<size;index++){
      let bit=size>>1;for(;j&bit;bit>>=1)j^=bit;j^=bit;
      if(index<j){[this.real[index],this.real[j]]=[this.real[j],this.real[index]];[this.imag[index],this.imag[j]]=[this.imag[j],this.imag[index]];}
    }
    for(let length=2;length<=size;length<<=1){
      const angle=-2*Math.PI/length,cosStep=Math.cos(angle),sinStep=Math.sin(angle);
      for(let start=0;start<size;start+=length){
        let wr=1,wi=0;
        for(let offset=0;offset<length/2;offset++){
          const even=start+offset,odd=even+length/2,tr=wr*this.real[odd]-wi*this.imag[odd],ti=wr*this.imag[odd]+wi*this.real[odd];
          this.real[odd]=this.real[even]-tr;this.imag[odd]=this.imag[even]-ti;this.real[even]+=tr;this.imag[even]+=ti;
          const nextWr=wr*cosStep-wi*sinStep;wi=wr*sinStep+wi*cosStep;wr=nextWr;
        }
      }
    }
    const levels=new Float32Array(size/2+1);
    for(let bin=0;bin<levels.length;bin++)levels[bin]=20*Math.log10(Math.hypot(this.real[bin],this.imag[bin])/(size*.5)+1e-8);
    this.post({type:'spectrum',sampleRate:this.sampleRate,fftSize:size,levels},[levels.buffer]);
  }
}

class RttyProcessor extends AudioWorkletProcessor{
  constructor(options){
    super();this.enabled=false;this.inputRate=Number(options.processorOptions?.sampleRate||sampleRate);this.externalPcm=Boolean(options.processorOptions?.externalPcm);
    this.gate=new RttyOutputGate(message=>this.port.postMessage(message));
    this.decoder=new RttyDecoder({sampleRate:this.inputRate,
      ...(options.processorOptions||{}),
      onFrame:frame=>this.gate.frame(frame),
      onStatus:status=>this.gate.status(status)});
    this.multi=new RttyMultiDetector(message=>this.port.postMessage(message),this.inputRate);
    this.scout=new RttyProfileScout(message=>this.port.postMessage(message),this.inputRate);
    this.spectrum=new RttySpectrum((message,transfer)=>{this.gate.updateSpectrum(message);this.multi.updateSpectrum(message);this.scout.updateSpectrum(message);this.port.postMessage(message,transfer);},this.inputRate);
    this.decoder.setEnabled(false);
    this.port.onmessage=({data})=>{
      if(data.type==='config'){
        this.inputRate=Number(data.sampleRate??this.inputRate);this.externalPcm=Boolean(data.externalPcm??this.externalPcm);
        this.decoder.configure({...data,sampleRate:this.inputRate});this.gate.configure(data);this.multi.configure({...data,sampleRate:this.inputRate});this.scout.configure({...data,sampleRate:this.inputRate});this.spectrum.setSampleRate(this.inputRate);
      }
      if(data.type==='enabled'){this.enabled=Boolean(data.enabled);this.decoder.setEnabled(this.enabled);}
      if(data.type==='reset'){this.decoder.reset();this.gate.reset();this.multi.clear();this.scout.reset();}
      if(data.type==='ignore-profile')this.scout.ignore(data.profile);
      if(data.type==='samples'&&this.enabled&&data.payload){
        const pcm=new Int16Array(data.payload),samples=new Float32Array(pcm.length);for(let index=0;index<pcm.length;index++)samples[index]=pcm[index]/32768;this.processSamples(samples);
      }
    };
  }
  processSamples(input){this.decoder.process(input);this.multi.process(input);this.scout.process(input);this.spectrum.push(input);}
  process(inputs,outputs){
    const input=inputs[0]?.[0],output=outputs[0]?.[0];
    if(output)output.fill(0);
    if(this.enabled&&input&&!this.externalPcm)this.processSamples(input);
    return true;
  }
}
registerProcessor('rtty-decoder',RttyProcessor);
