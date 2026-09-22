// SPDX-License-Identifier: GPL-3.0-only
import assert from 'node:assert/strict';
import {findRttyCandidates,Ita2Decoder,RttyDecoder,RTTY_PRESETS} from '../web/rtty-core.mjs';

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
  {text:'CQ CQ TEST ',baud:50,shift:450},
  {text:'CQ CQ TEST ',baud:75,shift:850},
  ...[45.45,50,75].map(baud=>({text:'CQ MULTI TEST ',baud,shift:170})),
  {text:'THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG ',baud:45.4},
  {text:'THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG ',baud:45.5},
  ...[0,5,-5,15,-15,30,-30].map(frequencyOffset=>({text:'CQ CQ TEST ',frequencyOffset,noise:.03,afc:true}))
])assert.equal(decode(options).text,options.text,JSON.stringify(options));
const spectrum=new Float32Array(1025).fill(-100),sampleRate=48000,fftSize=2048;
for(const center of [900,2100])for(const tone of [center-85,center+85])spectrum[Math.round(tone*fftSize/sampleRate)]=-45;
const candidates=findRttyCandidates(spectrum,sampleRate,fftSize,{low:0,high:3000});
assert.equal(candidates.length,2);assert.ok(Math.abs(candidates[0].centerFrequency-900)<30);assert.ok(Math.abs(candidates[1].centerFrequency-2100)<30);
const first=generateRtty({text:'CQ TEST ',sampleRate,centerFrequency:900}),second=generateRtty({text:'DE RADIO ',sampleRate,centerFrequency:2100});
const mixed=new Float32Array(Math.max(first.length,second.length));for(let index=0;index<mixed.length;index++)mixed[index]=(first[index]||0)*.5+(second[index]||0)*.5;
let firstText='',secondText='';
new RttyDecoder({sampleRate,centerFrequency:900,afc:true,onCharacter:value=>firstText+=value}).process(mixed);
new RttyDecoder({sampleRate,centerFrequency:2100,afc:true,onCharacter:value=>secondText+=value}).process(mixed);
assert.equal(firstText,'CQ TEST ');assert.equal(secondText,'DE RADIO ');

globalThis.AudioWorkletProcessor=class{constructor(){this.port={postMessage(){},onmessage:null};}};
globalThis.registerProcessor=()=>{};
const {MULTI_PROFILES,RttyOutputGate,RttyProfileScout}=await import('../web/rtty-worklet.js');
assert.deepEqual(MULTI_PROFILES.map(profile=>[profile.baud,profile.shift]),[[45.45,170],[50,170],[75,170]]);
const gated=[];const gate=new RttyOutputGate(message=>gated.push(message));
gate.configure({centerFrequency:1000,shift:170,low:0,high:3000});
const gateSpectrum=new Float32Array(1025).fill(-100);
for(const tone of [915,1085])gateSpectrum[Math.round(tone*2048/12000)]=-40;
for(let scan=0;scan<3;scan++)gate.updateSpectrum({levels:gateSpectrum,sampleRate:12000,fftSize:2048});
for(const value of 'CQ TES')gate.frame({valid:true,value});
assert.equal(gated.filter(message=>message.type==='character').map(message=>message.value).join(''),'CQ TES');
gate.reset();for(const value of 'NOISE')gate.frame({valid:value!=='I',value});
assert.equal(gated.filter(message=>message.type==='character').map(message=>message.value).join(''),'CQ TES');
const suggestions=[];const scout=new RttyProfileScout(message=>suggestions.push(message),12000);
scout.configure({sampleRate:12000,multi:false,baud:45.45,shift:170,low:0,high:3000});
scout.updateSpectrum({levels:gateSpectrum,sampleRate:12000,fftSize:2048});
scout.process(generateRtty({text:'CQ CQ TEST CQ CQ TEST CQ CQ TEST ',sampleRate:12000,baud:75,shift:170,centerFrequency:1000}));
for(let scan=1;scan<18;scan++)scout.updateSpectrum({levels:gateSpectrum,sampleRate:12000,fftSize:2048});
assert.equal(suggestions.at(-1)?.baud,75);
scout.configure({multi:true});assert.equal(scout.enabled,false);
console.log('RTTY core tests passed');
