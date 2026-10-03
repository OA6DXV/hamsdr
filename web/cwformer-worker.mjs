// SPDX-License-Identifier: GPL-3.0-only
// Client-only CWformer v0.2.0 inference; the network remains digiraw/12 kHz.
import * as ort from './cwformer/ort.wasm.min.mjs';
import {CwSpectrum} from './cw-spectrum.mjs';

ort.env.wasm.numThreads=1;
ort.env.wasm.proxy=false;
ort.env.wasm.wasmPaths=new URL('./cwformer/',import.meta.url).href;
const vocabulary=['',' ',...'ABCDEFGHIJKLMNOPQRSTUVWXYZ',...'0123456789',...'.,?/(&=+','AR','SK','BT','KN','AS','CT'];
let session,basis,windowTable,state,spectrum,lastSequence=null,previousToken=-1,blankFrames=0;
let pcm=[],overlap=[],resamplePosition=0,resampleCarry=[],queue=Promise.resolve();

async function floatTable(name){
  const response=await fetch(new URL(`./cwformer/${name}`,import.meta.url));
  if(!response.ok)throw new Error(`No se pudo cargar ${name}`);
  const bytes=await response.arrayBuffer(),view=new DataView(bytes);
  if(view.getUint8(0)!==147||new TextDecoder().decode(bytes.slice(1,6))!=='NUMPY')throw new Error('Invalid NumPy table');
  const major=view.getUint8(6),offset=major===1?10+view.getUint16(8,true):12+view.getUint32(8,true);
  const header=new TextDecoder().decode(bytes.slice(major===1?10:12,offset));
  if(!header.includes('<f4')||!header.includes('False'))throw new Error('Unsupported NumPy layout');
  return new Float32Array(bytes.slice(offset));
}
const loading=(async()=>{
  postMessage({type:'status',message:'Cargando CW Experimental (~90 MiB, primera vez)…'});
  [basis,windowTable]=await Promise.all([floatTable('mel_basis.npy'),floatTable('mel_window.npy')]);
  session=await ort.InferenceSession.create(new URL('./cwformer/cwformer_streaming_fp32.onnx',import.meta.url).href,{executionProviders:['wasm'],graphOptimizationLevel:'all'});
})();

function zero(dims){return new ort.Tensor('float32',new Float32Array(dims.reduce((a,b)=>a*b,1)),dims);}
function resetState(){
  state={pos_offset:new ort.Tensor('int64',BigInt64Array.of(0n),[1])};
  for(let i=0;i<12;i++){
    state[`kv_k_layer${i}`]=zero([1,4,0,64]);state[`kv_v_layer${i}`]=zero([1,4,0,64]);
    state[`conv_buf_layer${i}`]=zero([1,256,62]);
  }
  state.sub_buf1=zero([1,1,2,40]);state.sub_buf2=zero([1,256,2,20]);blankFrames=0;
}
function reset(){
  resetState();pcm=[];overlap=new Array(200).fill(0);resampleCarry=[];resamplePosition=0;previousToken=-1;lastSequence=null;
  spectrum=new CwSpectrum((data,transfers)=>postMessage(data,transfers));
}

// Precompute the exact 400-point DFT basis (not a zero-padded 512-point FFT).
const cosine=new Float64Array(201*400),sine=new Float64Array(201*400);
for(let k=0;k<=200;k++)for(let n=0;n<400;n++){
  const angle=2*Math.PI*k*n/400;cosine[k*400+n]=Math.cos(angle);sine[k*400+n]=Math.sin(angle);
}
function mel(samples){
  const audio=overlap.concat(samples),count=Math.max(0,Math.floor((audio.length-400)/160)+1);
  overlap=audio.slice(count*160);
  const features=new Float32Array(count*40),power=new Float64Array(201),frame=new Float64Array(400);
  for(let t=0;t<count;t++){
    for(let n=0;n<400;n++)frame[n]=Math.fround(audio[t*160+n]*windowTable[n]);
    for(let k=0;k<=200;k++){
      let re=0,im=0;for(let n=0;n<400;n++){re+=frame[n]*cosine[k*400+n];im-=frame[n]*sine[k*400+n];}
      power[k]=re*re+im*im;
    }
    for(let m=0;m<40;m++){let energy=0;for(let k=0;k<=200;k++)energy+=power[k]*basis[m*201+k];features[t*40+m]=Math.log(energy+1e-6);}
  }
  return new ort.Tensor('float32',features,[1,count,40]);
}
function resample(samples){
  const audio=resampleCarry.concat(Array.from(samples)),output=[];
  while(resamplePosition+1<audio.length){
    const i=Math.floor(resamplePosition),fraction=resamplePosition-i;
    output.push(audio[i]*(1-fraction)+audio[i+1]*fraction);resamplePosition+=.75;
  }
  const consumed=Math.floor(resamplePosition);resampleCarry=audio.slice(consumed);resamplePosition-=consumed;return output;
}
function trimCache(tensor){
  const [batch,heads,length,width]=tensor.dims;if(length<=250)return tensor;
  const data=new Float32Array(batch*heads*250*width);
  for(let h=0;h<heads;h++)data.set(tensor.data.subarray((h*length+length-250)*width,(h+1)*length*width),h*250*width);
  return new ort.Tensor('float32',data,[batch,heads,250,width]);
}
async function infer(chunk){
  const started=performance.now(),outputs=await session.run({...state,mel_chunk:mel(chunk)});
  const values=session.outputNames.map(name=>outputs[name]);let index=0;
  const logits=values[index++];state.pos_offset=values[index++];
  for(let i=0;i<12;i++){state[`kv_k_layer${i}`]=trimCache(values[index++]);state[`kv_v_layer${i}`]=trimCache(values[index++]);}
  for(let i=0;i<12;i++)state[`conv_buf_layer${i}`]=values[index++];
  state.sub_buf1=values[index++];state.sub_buf2=values[index++];
  let text='';const classes=logits.dims.at(-1);
  for(let frame=0;frame<logits.data.length/classes;frame++){
    let best=0;for(let c=1;c<classes;c++)if(logits.data[frame*classes+c]>logits.data[frame*classes+best])best=c;
    if(best!==previousToken&&best!==0)text+=vocabulary[best]||'';
    previousToken=best;blankFrames=best===0?blankFrames+1:0;
  }
  if(text)postMessage({type:'text',text});
  const elapsed=performance.now()-started;
  postMessage({type:'status',message:elapsed>500?'CW Experimental · procesamiento más lento que tiempo real':'CW Experimental · escuchando…'});
  // Limit attention history and reset after sustained blank output, not each chunk.
  if(blankFrames>=250){resetState();previousToken=-1;}
}
async function handle(data){
  await loading;
  if(data.type==='configure'){reset();postMessage({type:'ready'});return;}
  if(data.type!=='samples')return;
  if(lastSequence!==null&&((data.sequence-lastSequence)>>>0)!==1)reset();
  lastSequence=data.sequence;
  const samples=Float32Array.from(new Int16Array(data.payload),value=>value/32768);
  spectrum.push(samples);pcm.push(...resample(samples));
  while(pcm.length>=8000){const chunk=pcm.slice(0,8000);pcm=pcm.slice(8000);await infer(chunk);}
  postMessage({type:'consumed'});
}
// Serialize asynchronous inference so ONNX state and tuning resets cannot race.
self.onmessage=({data})=>{queue=queue.then(()=>handle(data)).catch(error=>postMessage({type:'error',message:`CW Experimental: ${error.message||error}`}));};
