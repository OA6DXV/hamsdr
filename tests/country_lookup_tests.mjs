// SPDX-License-Identifier: GPL-3.0-only
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {gunzipSync} from 'node:zlib';

globalThis.self=globalThis;
const {parseCountryDatabase,countryForCallsign,countriesForMessage}=await import('../web/digital-worker.js');
const database=parseCountryDatabase(gunzipSync(readFileSync(new URL('../web/cty.dat.gz',import.meta.url))).toString());

for(const [callsign,country] of [
  ['RA3AA','European Russia'],
  ['R9AAA','Asiatic Russia'],
  ['UA9AAA','Asiatic Russia'],
  ['UI6AAA','European Russia'],
  ['7Z1AL','Saudi Arabia'],
  ['HZ1SK','Saudi Arabia'],
  ['A71A','Qatar'],
  ['EP2A','Iran'],
  ['4X1AA','Israel'],
])assert.equal(countryForCallsign(callsign,database),country,callsign);

assert.equal(countriesForMessage('CQ RA3AA KO85',database),'European Russia');
assert.equal(countriesForMessage('RA3AA 7Z1AL -10',database),'European Russia → Saudi Arabia');
assert.equal(countryForCallsign('',database),'');
console.log('Country lookup tests passed');
