// SPDX-License-Identifier: GPL-3.0-only
import assert from 'node:assert/strict';
import {Ita2Decoder,RttyDecoder,RTTY_PRESETS} from '../web/rtty-core.mjs';

const letters=['','E','\n','A',' ','S','I','U','\r','D','R','J','N','F','C','K','T','Z','L','W','H','Y','P','Q','O','B','G','','M','X','V',''];
const letterCodes=new Map(letters.map((value,index)=>[value,index]));

function encodeText(text){
  const bits=Array(10).fill(1);
  for(const character of text){
    const code=letterCodes.get(character);assert.notEqual(code,undefined,`unsupported test character ${character}`);
    bits.push(0);for(let bit=0;bit<5;bit++)bits.push((code>>bit)&1);bits.push(1,1);
  }
  bits.push(...Array(5).fill(1));return bits;
}

function generateRtty({text,sampleRate=12000,baud=45.45,shift=170,centerFrequency=1000,frequencyOffset=0,reverse=false,noise=0}){
  const bits=encodeText(text),samplesPerBit=sampleRate/baud,output=new Float32Array(Math.ceil(bits.length*samplesPerBit));
  let phase=0,random=7100;
  for(let index=0;index<output.length;index++){
    const bit=bits[Math.min(bits.length-1,Math.floor(index/samplesPerBit))];
    const normal=bit?-shift/2:shift/2,frequency=centerFrequency+frequencyOffset+(reverse?-normal:normal);
    phase+=2*Math.PI*frequency/sampleRate;random=(random*1664525+1013904223)>>>0;
    output[index]=Math.sin(phase)*.7+((random/0xffffffff)*2-1)*noise;
  }
  return output;
}

function decode(options){
  let text='',status;
  const decoder=new RttyDecoder({...options,sampleRate:options.sampleRate||12000,onCharacter:value=>text+=value,onStatus:value=>status=value});
  decoder.process(generateRtty(options));return{text,status};
}

const ita2=new Ita2Decoder();
assert.equal(ita2.decode(3),'A');assert.equal(ita2.decode(27),'');assert.equal(ita2.decode(1),'3');assert.equal(ita2.decode(31),'');assert.equal(ita2.decode(1),'E');
assert.equal(RTTY_PRESETS.amateur45.baud,45.45);assert.equal(RTTY_PRESETS.amateur45.shift,170);
for(const options of [
  {text:'CQ CQ TEST '},
  {text:'CQ CQ TEST ',reverse:true},
  {text:'THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG ',baud:45.4},
  {text:'THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG ',baud:45.5},
  ...[0,5,-5,15,-15,30,-30].map(frequencyOffset=>({text:'CQ CQ TEST ',frequencyOffset,noise:.03,afc:true}))
])assert.equal(decode(options).text,options.text,JSON.stringify(options));
console.log('RTTY core tests passed');
