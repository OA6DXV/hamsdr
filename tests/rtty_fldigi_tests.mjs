// SPDX-License-Identifier: GPL-3.0-only
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import createDecoder from '../web/rtty-fldigi.mjs';

export const letters=['','E','\n','A',' ','S','I','U','\r','D','R','J','N','F','C','K','T','Z','L','W','H','Y','P','Q','O','B','G','','M','X','V',''];
export function signal({text,baud=45.45,shift=170,center=1000,offset=0,reverse=false,noise=0,amplitude=.7,markScale=1}){
  const bits=Array(12).fill(1);
  for(const character of text){const code=letters.indexOf(character);assert.ok(code>=0);bits.push(0);for(let i=0;i<5;i++)bits.push((code>>i)&1);bits.push(1,1);}
  bits.push(...Array(12).fill(1));const values=new Float32Array(Math.ceil(bits.length*12000/baud));let phase=0,random=20261003;
  for(let i=0;i<values.length;i++){
    const bit=bits[Math.min(bits.length-1,Math.floor(i*baud/12000))],delta=(bit?-1:1)*shift/2*(reverse?-1:1);
    phase+=2*Math.PI*(center+offset+delta)/12000;random=(1664525*random+1013904223)>>>0;
    values[i]=Math.sin(phase)*amplitude*(bit?markScale:1)+noise*(random/4294967295*2-1);
  }
  return values;
}
const runtime=await createDecoder({wasmBinary:readFileSync(new URL('../web/rtty-fldigi.wasm',import.meta.url))});
for(const options of [
  {text:'CQ CQ DE TEST '},
  {text:'CQ CQ DE TEST ',reverse:true},
  {text:'CQ CQ DE TEST ',baud:50},
  {text:'CQ CQ DE TEST ',baud:75},
  {text:'CQ CQ DE TEST ',baud:50,shift:450},
  {text:'CQ CQ DE TEST ',baud:75,shift:850},
  {text:'CQ CQ DE TEST ',noise:.3,markScale:.4},
  ...[-15,15].map(offset=>({text:'CQ CQ DE TEST ',offset,noise:.1}))
]){
  const handle=runtime._rtty_create();runtime._rtty_configure(handle,12000,options.baud||45.45,options.shift||170,1000,Number(!!options.reverse),1,50);
  const samples=signal(options);let text='';
  for(let i=0;i<samples.length;i+=1200){
    const block=samples.subarray(i,i+1200),pointer=runtime._malloc(block.byteLength);runtime.HEAPF32.set(block,pointer/4);
    text+=runtime.UTF8ToString(runtime._rtty_process(handle,pointer,block.length));runtime._free(pointer);
  }
  console.log('Fldigi receiver:',JSON.stringify(options),JSON.stringify(text));
  assert.equal(text,options.text);runtime._rtty_destroy(handle);
}
console.log('RTTY Fldigi WASM tests passed');
