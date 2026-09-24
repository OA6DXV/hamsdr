// SPDX-License-Identifier: GPL-3.0-only
import assert from 'node:assert/strict';
await import('../web/smeter-calibration.js');

const engine=globalThis.SMeterCalibration;
assert(engine,'calibration engine exported');
const samples=Array.from({length:80},(_,index)=>-50+(index%5-2)*.2);
samples.push(...Array.from({length:12},(_,index)=>-25+(index%3-1)*.3));
const calibration=engine.finalizePower(samples);
assert(calibration,'enough samples produce a calibration');
assert.equal(calibration.source,'channel-power','calibration uses tuned channel power');
assert(Math.abs(calibration.noiseDbfs-(-50))<.5,`channel noise percentile ${calibration.noiseDbfs}`);
assert.equal(engine.finalizePower(samples.slice(0,10)),null,'short captures are rejected');
assert(engine.displayPosition(calibration.strongDbfs,calibration)>=3.8,'reference signal maps near S9');
assert(engine.displayPosition(-200,calibration)===0,'low levels clamp to meter start');
assert(engine.displayPosition(100,calibration)===7,'high levels clamp to meter end');
assert.match(engine.formatS(calibration.noiseDbfs,calibration),/^S[1-7]/);
console.log('S-meter calibration tests passed');
