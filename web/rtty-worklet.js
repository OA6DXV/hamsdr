// SPDX-License-Identifier: GPL-3.0-only
import {RttyDecoder} from './rtty-core.mjs';

class RttySpectrum{
  constructor(post){
    this.post=post;this.size=2048;this.real=new Float64Array(this.size);this.imag=new Float64Array(this.size);
    this.buffer=new Float32Array(this.size);this.count=0;this.skip=0;
  }
  push(input){
    for(let index=0;index<input.length;index++){
      if(this.skip){this.skip--;continue;}
      this.buffer[this.count++]=input[index];
      if(this.count===this.size){this.emit();this.count=0;this.skip=Math.max(0,Math.round(sampleRate/6)-this.size);}
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
    this.post({type:'spectrum',sampleRate,fftSize:size,levels},[levels.buffer]);
  }
}

class RttyProcessor extends AudioWorkletProcessor{
  constructor(options){
    super();this.enabled=false;
    this.decoder=new RttyDecoder({sampleRate,
      ...(options.processorOptions||{}),
      onCharacter:value=>this.port.postMessage({type:'character',value}),
      onStatus:status=>this.port.postMessage({type:'status',...status})});
    this.spectrum=new RttySpectrum((message,transfer)=>this.port.postMessage(message,transfer));
    this.decoder.setEnabled(false);
    this.port.onmessage=({data})=>{
      if(data.type==='config')this.decoder.configure(data);
      if(data.type==='enabled'){this.enabled=Boolean(data.enabled);this.decoder.setEnabled(this.enabled);}
      if(data.type==='reset')this.decoder.reset();
    };
  }
  process(inputs,outputs){
    const input=inputs[0]?.[0],output=outputs[0]?.[0];
    if(output)output.fill(0);
    if(this.enabled&&input){this.decoder.process(input);this.spectrum.push(input);}
    return true;
  }
}
registerProcessor('rtty-decoder',RttyProcessor);
