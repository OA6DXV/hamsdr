// SPDX-License-Identifier: GPL-3.0-only
import assert from 'node:assert/strict';
import {CwToneTracker,cwCarrierPresent} from '../web/cw-detector.mjs';
import {CwSpectrum} from '../web/cw-spectrum.mjs';

let seed=42;
const noise=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return (seed/4294967296-.5)*.01;};
function scan(tones,tracker,seconds=3){
  let time=0,latest=[];
  const spectrum=new CwSpectrum(data=>{latest=tracker.update(data,time);});
  for(let start=0;start<seconds*12000;start+=1200){
    time=(start+1200)/12000;
    spectrum.push(Float32Array.from({length:1200},(_,j)=>noise()+tones.reduce((sum,t)=>sum+.1*Math.sin(2*Math.PI*t*(start+j)/12000),0)));
  }
  return latest;
}
assert.equal(scan([],new CwToneTracker()).length,0,'Stationary noise is not a tone');
let tracker=new CwToneTracker({minimum:450,maximum:1500});
let tracks=scan([700,1000,1800],tracker);
assert.equal(tracks.length,2,'Only tones inside the visible passband are tracked');
assert(tracks.some(t=>Math.abs(t.tone-700)<1));assert(tracks.some(t=>Math.abs(t.tone-1000)<1));
const spectrum={levels:new Float32Array(2049).fill(-100),fftSize:4096,sampleRate:12000};
assert.equal(tracker.update(spectrum,20).length,0,'Expired candidates release their slots');
tracks=scan([300,500,700,900,1100,1300,1500,1700],new CwToneTracker());
assert(tracks.length<=6,'CPU/decoder count is bounded');
// Broad energy and isolated, nonpersistent impulses cannot pass the narrow-tone gate.
tracker=new CwToneTracker();spectrum.levels.fill(-20);
assert.equal(tracker.update(spectrum,0).length,0);
spectrum.levels.fill(-100);spectrum.levels[240]=-20;
assert.equal(tracker.update(spectrum,.1).length,0);
// A keyed carrier is wider than an unmodulated tone. Its shoulders must not
// cause the scanner to discard actual CW and select weaker noise instead.
tracker=new CwToneTracker({minimum:450,maximum:950});
let time=0;
const keyedSpectrum=new CwSpectrum(data=>{tracks=tracker.update(data,time);});
for(let start=0;start<12000*5;start+=1200){
  time=(start+1200)/12000;
  keyedSpectrum.push(Float32Array.from({length:1200},(_,j)=>{
    const i=start+j,key=Math.floor(i/720)%2===0;
    return noise()+(key?.15*Math.sin(2*Math.PI*687*i/12000):0);
  }));
}
assert(tracks.some(t=>Math.abs(t.tone-687)<2),'The scanner retains keyed 20 WPM CW');
const activity={levels:new Float32Array(2049).fill(-100),fftSize:4096,sampleRate:12000};
activity.levels[240]=-30;
assert(cwCarrierPresent(activity,703),'A strong selected carrier is active');
activity.levels[240]=-75;activity.levels[280]=-30;
assert(!cwCarrierPresent(activity,703),'A strong neighbour cannot masquerade as its weak sidelobe');
console.log('CW tone tracker tests passed: noise, passband, precision, expiry and resource cap');
