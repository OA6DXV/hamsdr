// SPDX-License-Identifier: GPL-3.0-only
class RadioAudio extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer=new Float32Array(32768);
    this.write=0;this.read=0;this.started=false;this.blocks=0;this.rate=16000;this.underruns=0;
    this.port.onmessage=({data})=>{
      if(data.reset){this.read=this.write;this.started=false;return;}
      if(!data.payload||!['pcm16','float32'].includes(data.codec))return;
      const samples=data.codec==='pcm16'?this.decodePCM(data.payload):new Float32Array(data.payload);
      if(!samples)return;
      if(data.rate!==this.rate){this.rate=data.rate;this.read=this.write;this.started=false;}
      const floating=data.codec==='float32';
      for(let i=0;i<samples.length;i++)this.buffer[(this.write++)%this.buffer.length]=floating?samples[i]:samples[i]/32768;
      if(this.write-this.read>this.rate/2){this.read=this.write-this.rate/10;this.started=false;}
      if(data.record){
        const copy=new Int16Array(samples.length);
        if(floating)for(let i=0;i<samples.length;i++)copy[i]=Math.max(-32768,Math.min(32767,Math.round(samples[i]*32767)));
        else copy.set(samples);
        this.port.postMessage({recording:copy.buffer,rate:this.rate},[copy.buffer]);
      }
    };
  }
  decodePCM(payload){
    if(payload.byteLength%2)return null;
    const view=new DataView(payload),samples=new Int16Array(payload.byteLength/2);
    for(let i=0;i<samples.length;i++)samples[i]=view.getInt16(i*2,true);
    return samples;
  }
  process(inputs,outputs){
    const out=outputs[0][0];
    if(!this.started&&this.write-this.read>=this.rate/10)this.started=true;
    let power=0;const buffered=this.write-this.read,target=this.rate/8,wasStarted=this.started;let starved=false;
    const correction=Math.max(-0.004,Math.min(0.004,(buffered-target)*0.000003));
    const step=this.rate/sampleRate*(1+correction);
    for(let i=0;i<out.length;i++){
      if(!this.started||this.read+1>=this.write){if(wasStarted)starved=true;out[i]=0;this.started=false;continue;}
      const n=Math.floor(this.read),fraction=this.read-n;
      out[i]=this.buffer[n%this.buffer.length]*(1-fraction)+this.buffer[(n+1)%this.buffer.length]*fraction;
      this.read+=step;power+=out[i]*out[i];
    }
    if(starved){this.underruns++;this.port.postMessage({underrun:this.underruns,buffered:this.write-this.read});}
    if(++this.blocks%100===0)this.port.postMessage({rms:Math.sqrt(power/out.length),buffered:this.write-this.read,underruns:this.underruns});
    return true;
  }
}
registerProcessor('radio-audio',RadioAudio);
