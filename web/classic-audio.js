'use strict';
// Low-latency HTTP fallback for browsers that block AudioWorklet outside a secure context.
(() => {
  const IMA_INDEX=[-1,-1,-1,-1,2,4,6,8];
  const IMA_STEP=[7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,20350,22385,24623,27086,29794,32767];

  class ClassicRadioAudio {
    constructor(context){
      this.context=context;this.buffer=new Float32Array(32768);this.write=0;this.read=0;
      this.started=false;this.blocks=0;this.rate=16000;this.underruns=0;
      this.port={onmessage:null,postMessage:data=>this.receive(data)};
      this.processor=context.createScriptProcessor(1024,0,1);
      this.processor.onaudioprocess=event=>this.process(event.outputBuffer.getChannelData(0));
    }
    connect(destination){this.processor.connect(destination);return destination;}
    disconnect(){this.processor.disconnect();}
    emit(data){if(this.port.onmessage)this.port.onmessage({data});}
    decodePCM(payload){
      if(payload.byteLength%2)return null;
      const view=new DataView(payload),samples=new Int16Array(payload.byteLength/2);
      for(let i=0;i<samples.length;i++)samples[i]=view.getInt16(i*2,true);
      return samples;
    }
    decodeADPCM(payload){
      if(payload.byteLength<4)return null;
      const view=new DataView(payload);let predictor=view.getInt16(0,true),index=view.getUint8(2);
      if(index>88)return null;
      const samples=new Int16Array((payload.byteLength-4)*2);let position=0;
      for(let offset=4;offset<payload.byteLength;offset++){
        const byte=view.getUint8(offset);
        for(const code of [byte&15,byte>>4]){
          const step=IMA_STEP[index];let delta=step>>3;
          if(code&4)delta+=step;if(code&2)delta+=step>>1;if(code&1)delta+=step>>2;
          predictor=Math.max(-32768,Math.min(32767,predictor+(code&8?-delta:delta)));
          index=Math.max(0,Math.min(88,index+IMA_INDEX[code&7]));samples[position++]=predictor;
        }
      }
      return samples;
    }
    receive(data){
      if(data.reset){this.read=this.write;this.started=false;return;}
      if(!data.payload||!['pcm16','ima-adpcm','float32'].includes(data.codec))return;
      const samples=data.codec==='pcm16'?this.decodePCM(data.payload):data.codec==='ima-adpcm'?this.decodeADPCM(data.payload):new Float32Array(data.payload);
      if(!samples)return;
      if(data.rate!==this.rate){this.rate=data.rate;this.read=this.write;this.started=false;}
      const floating=data.codec==='float32';
      for(let i=0;i<samples.length;i++)this.buffer[(this.write++)%this.buffer.length]=floating?samples[i]:samples[i]/32768;
      if(this.write-this.read>this.rate/2){this.read=this.write-this.rate/16;this.started=false;}
      if(data.record){
        const copy=new Int16Array(samples.length);
        if(floating)for(let i=0;i<samples.length;i++)copy[i]=Math.max(-32768,Math.min(32767,Math.round(samples[i]*32767)));
        else copy.set(samples);
        this.emit({recording:copy.buffer,rate:this.rate});
      }
    }
    process(out){
      if(!this.started&&this.write-this.read>=this.rate/16)this.started=true;
      let power=0;const buffered=this.write-this.read,target=this.rate/12,wasStarted=this.started;
      let starved=false;const correction=Math.max(-0.004,Math.min(0.004,(buffered-target)*0.000003));
      const step=this.rate/this.context.sampleRate*(1+correction);
      for(let i=0;i<out.length;i++){
        if(!this.started||this.read+1>=this.write){if(wasStarted)starved=true;out[i]=0;this.started=false;continue;}
        const n=Math.floor(this.read),fraction=this.read-n;
        out[i]=this.buffer[n%this.buffer.length]*(1-fraction)+this.buffer[(n+1)%this.buffer.length]*fraction;
        this.read+=step;power+=out[i]*out[i];
      }
      if(starved){this.underruns++;this.emit({underrun:this.underruns,buffered:this.write-this.read});}
      if(++this.blocks%50===0)this.emit({rms:Math.sqrt(power/out.length),buffered:this.write-this.read,underruns:this.underruns});
    }
  }
  window.ClassicRadioAudio=ClassicRadioAudio;
})();
