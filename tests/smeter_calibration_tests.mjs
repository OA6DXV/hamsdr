// SPDX-License-Identifier: GPL-3.0-only
import assert from 'node:assert/strict';
await import('../web/smeter-calibration.js');

const engine=globalThis.SMeterCalibration;
assert(engine,'calibration engine exported');
const row=new Uint8Array(1024).fill(100);
for(let index=300;index<308;index++)row[index]=200;
const frame=engine.frameStatistics(row,6588500,1024000,7100500);
assert(frame,'valid waterfall frame');
assert(Math.abs(frame.noiseDbfs-(-72.94))<.1,`noise percentile ${frame.noiseDbfs}`);
assert(Math.abs(frame.strongDbfs-(-25.88))<.1,`signal percentile ${frame.strongDbfs}`);

const noises=Array.from({length:40},(_,index)=>frame.noiseDbfs+(index%3-1)*.2);
const signals=Array.from({length:40},(_,index)=>frame.strongDbfs+(index%5-2)*.3);
const calibration=engine.finalize(noises,signals);
assert(calibration,'enough samples produce a calibration');
assert(calibration.noiseS>=1&&calibration.noiseS<=7,'noise floor is capped at S7');
assert.equal(engine.finalize(noises.slice(0,10),signals.slice(0,10)),null,'short captures are rejected');
assert(engine.displayPosition(calibration.strongDbfs,calibration)>=3.8,'reference signal maps near S9');
assert(engine.displayPosition(-200,calibration)===0,'low levels clamp to meter start');
assert(engine.displayPosition(100,calibration)===7,'high levels clamp to meter end');
assert.match(engine.formatS(calibration.noiseDbfs,calibration),/^S[1-7]/);
console.log('S-meter calibration tests passed');
