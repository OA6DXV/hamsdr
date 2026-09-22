// SPDX-License-Identifier: GPL-3.0-only
import {findRttyCandidates,RttyDecoder} from './rtty-core.mjs';

const MULTI_PROFILES=Object.freeze([
  Object.freeze({id:'45',label:'45.45/170',baud:45.45,shift:170}),
  Object.freeze({id:'50',label:'50/170',baud:50,shift:170}),
  Object.freeze({id:'75',label:'75/170',baud:75,shift:170})
]);

class RttyMultiDetector{
  constructor(post,rate){this.post=post;this.sampleRate=rate;this.enabled=false;this.streams=[];this.pending=[];this.nextId=1;this.scanNumber=0;this.average=null;this.configure({});}
  configure(options){
    const wasEnabled=this.enabled;
    this.sampleRate=Number(options.sampleRate??this.sampleRate);
    this.enabled=Boolean(options.multi??this.enabled);this.low=Number(options.low??this.low??0);this.high=Number(options.high??this.high??3000);
    this.reverse=Boolean(options.reverse??this.reverse??false);
    if(!this.enabled){if(wasEnabled)this.clear();return;}
    for(const stream of this.streams)for(const entry of stream.decoders)entry.decoder.configure({sampleRate:this.sampleRate,centerFrequency:stream.centerFrequency,reverse:this.reverse,afc:true});
  }
  clear(){this.streams=[];this.pending=[];this.average=null;this.post({type:'multi-streams',streams:[]});}
  createStream(candidate){
    const stream={id:this.nextId++,centerFrequency:candidate.centerFrequency,score:candidate.score,lastSeen:this.scanNumber,decoders:[]};
    stream.decoders=MULTI_PROFILES.map(profile=>({profile,decoder:new RttyDecoder({sampleRate:this.sampleRate,baud:profile.baud,shift:profile.shift,centerFrequency:stream.centerFrequency,reverse:this.reverse,afc:true,afcRange:45,filterBandwidth:Math.max(250,profile.shift+100),
      onCharacter:value=>this.post({type:'multi-character',id:stream.id,centerFrequency:stream.centerFrequency,profile:profile.id,profileLabel:profile.label,value})})}));
    this.streams.push(stream);
  }
  updateSpectrum(message){
    if(!this.enabled)return;
    const levels=message.levels;
    if(!this.average||this.average.length!==levels.length)this.average=Float32Array.from(levels);
    else for(let index=0;index<levels.length;index++)this.average[index]=this.average[index]*.78+levels[index]*.22;
    if(++this.scanNumber%3)return;
    const found=findRttyCandidates(this.average,message.sampleRate,message.fftSize,{low:this.low,high:this.high,shift:170,thresholdDb:7,maxCandidates:8});
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
    this.post({type:'multi-streams',streams:this.streams.map(({id,centerFrequency,score})=>({id,centerFrequency,score,profiles:MULTI_PROFILES.map(({id,label})=>({id,label}))}))});
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
    this.decoder=new RttyDecoder({sampleRate:this.inputRate,
      ...(options.processorOptions||{}),
      onCharacter:value=>this.port.postMessage({type:'character',value}),
      onStatus:status=>this.port.postMessage({type:'status',...status})});
    this.multi=new RttyMultiDetector(message=>this.port.postMessage(message),this.inputRate);
    this.spectrum=new RttySpectrum((message,transfer)=>{this.multi.updateSpectrum(message);this.port.postMessage(message,transfer);},this.inputRate);
    this.decoder.setEnabled(false);
    this.port.onmessage=({data})=>{
      if(data.type==='config'){
        this.inputRate=Number(data.sampleRate??this.inputRate);this.externalPcm=Boolean(data.externalPcm??this.externalPcm);
        this.decoder.configure({...data,sampleRate:this.inputRate});this.multi.configure({...data,sampleRate:this.inputRate});this.spectrum.setSampleRate(this.inputRate);
      }
      if(data.type==='enabled'){this.enabled=Boolean(data.enabled);this.decoder.setEnabled(this.enabled);}
      if(data.type==='reset'){this.decoder.reset();this.multi.clear();}
      if(data.type==='samples'&&this.enabled&&data.payload){
        const pcm=new Int16Array(data.payload),samples=new Float32Array(pcm.length);for(let index=0;index<pcm.length;index++)samples[index]=pcm[index]/32768;this.processSamples(samples);
      }
    };
  }
  processSamples(input){this.decoder.process(input);this.multi.process(input);this.spectrum.push(input);}
  process(inputs,outputs){
    const input=inputs[0]?.[0],output=outputs[0]?.[0];
    if(output)output.fill(0);
    if(this.enabled&&input&&!this.externalPcm)this.processSamples(input);
    return true;
  }
}
registerProcessor('rtty-decoder',RttyProcessor);
