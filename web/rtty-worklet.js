// SPDX-License-Identifier: GPL-3.0-only
import {RttyDecoder} from './rtty-core.mjs';

class RttyProcessor extends AudioWorkletProcessor{
  constructor(options){
    super();this.enabled=false;
    this.decoder=new RttyDecoder({sampleRate,
      ...(options.processorOptions||{}),
      onCharacter:value=>this.port.postMessage({type:'character',value}),
      onStatus:status=>this.port.postMessage({type:'status',...status})});
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
    if(this.enabled&&input)this.decoder.process(input);
    return true;
  }
}
registerProcessor('rtty-decoder',RttyProcessor);
