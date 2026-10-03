// SPDX-License-Identifier: GPL-3.0-only
import init, {CwDecoder} from './cw-decoder.js';
import {CwSpectrum} from './cw-spectrum.mjs';

let decoder=null,spectrum=null,configuration=null,lastSequence=null,elapsed=0;
const ready=init({module_or_path:new URL('./cw-decoder_bg.wasm',import.meta.url)});
function reset(){
  decoder?.free();decoder=new CwDecoder(configuration.tone,configuration.wpm);
  spectrum=new CwSpectrum((data,transfers)=>postMessage(data,transfers));lastSequence=null;elapsed=0;
}
// Initialization is shared; PCM is accepted only after the UI receives ready.
self.onmessage=async({data})=>{
  try{
    await ready;
    if(data.type==='configure'){
      configuration={tone:Number(data.tone)||700,wpm:Number(data.wpm)||20};reset();postMessage({type:'ready'});return;
    }
    if(data.type!=='samples'||!decoder)return;
    if(lastSequence!==null&&((data.sequence-lastSequence)>>>0)!==1)reset();
    lastSequence=data.sequence;
    const raw=new Int16Array(data.payload),samples=Float32Array.from(raw,value=>value/32768);
    const text=decoder.process(samples);if(text)postMessage({type:'text',text});
    spectrum.push(samples);elapsed+=samples.length;
    if(elapsed>=2400){elapsed=0;postMessage({type:'status',wpm:decoder.wpm(),locked:decoder.locked(),keyed:decoder.keyed()});}
    postMessage({type:'consumed'});
  }catch(error){postMessage({type:'error',message:String(error.message||error)});}
};
