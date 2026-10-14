// SPDX-License-Identifier: GPL-3.0-only
import assert from 'node:assert/strict';
import '../web/recorder-core.js';
const {wavHeader,OggOpus,opusSamples,counter}=globalThis.HamRecorderCore;
const decoder=new TextDecoder();
for(const rate of [12000,16000]){
  const header=wavHeader(rate*4,rate),view=new DataView(header.buffer);
  assert.equal(decoder.decode(header.subarray(0,4)),'RIFF');
  assert.equal(view.getUint32(4,true),rate*4+72);
  assert.equal(view.getUint32(60,true),rate);assert.equal(view.getUint32(76,true),rate*4);
}
const large=wavHeader(2**32,16000),largeView=new DataView(large.buffer);
assert.equal(decoder.decode(large.subarray(0,4)),'RF64');
assert.equal(decoder.decode(large.subarray(12,16)),'ds64');
assert.equal(largeView.getBigUint64(28,true),2n**32n);
assert.equal(largeView.getBigUint64(36,true),2n**31n);
assert.equal(counter(16000*62,16000,1048576),'01:02 • 1.00 MiB');
assert.equal(counter(12000*3661,12000,262144),'01:01:01 • 0.25 MiB');
function independentCrc(page){
  const copy=page.slice();copy.fill(0,22,26);let crc=0;
  for(const byte of copy){crc^=byte<<24;for(let bit=0;bit<8;bit++)crc=(crc<<1)^((crc&0x80000000)?0x04c11db7:0);}
  return crc>>>0;
}
for(const length of [80,255,510]){
  const muxer=new OggOpus(123),pages=muxer.headers(),packet=new Uint8Array(length);packet[0]=0x98;
  assert.equal(opusSamples(packet),960);
  for(let i=0;i<53;i++)pages.push(...muxer.append(packet.slice()));
  pages.push(...muxer.flush(true));
  assert.equal(muxer.samples,53*960);
  for(let i=0;i<pages.length;i++){
    const page=pages[i],view=new DataView(page.buffer);
    assert.equal(view.getUint32(18,true),i);assert.equal(view.getUint32(14,true),123);
    assert.equal(view.getUint32(22,true),independentCrc(page));
    const segments=page[26],payload=page.subarray(27+segments);
    if(i>=2){assert.equal(payload.length%packet.length,0);assert.equal(view.getBigUint64(6,true),BigInt((i===2?50:53)*960));}
  }
  assert.equal(pages[0][5],2);assert.equal(pages.at(-1)[5],4);
}
const muxer=new OggOpus(4);muxer.headers();muxer.append(Uint8Array.of(0x98,1,2));muxer.flush();
const patch=muxer.endPatch();assert.equal(patch.bytes[5],4);assert.equal(new DataView(patch.bytes.buffer).getUint32(22,true),independentCrc(patch.bytes));
assert.throws(()=>opusSamples(Uint8Array.of(0x9b,0)),/Invalid/);
console.log('Recorder containers, CRC, timestamps, counters and RF64 passed');
