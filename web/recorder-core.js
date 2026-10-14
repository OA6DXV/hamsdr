// SPDX-License-Identifier: GPL-3.0-only
'use strict';
(() => {
  const WAV_HEADER_SIZE=80;
  const crcTable=Uint32Array.from({length:256},(_,index)=>{
    let value=index<<24;
    for(let bit=0;bit<8;bit++)value=(value<<1)^((value&0x80000000)?0x04c11db7:0);
    return value>>>0;
  });
  function text(bytes,offset,value){for(let i=0;i<value.length;i++)bytes[offset+i]=value.charCodeAt(i);}
  function wavHeader(dataBytes,rate){
    const bytes=new Uint8Array(WAV_HEADER_SIZE),view=new DataView(bytes.buffer),large=dataBytes+72>0xffffffff;
    text(bytes,0,large?'RF64':'RIFF');view.setUint32(4,large?0xffffffff:dataBytes+72,true);text(bytes,8,'WAVE');
    // Reserve ds64 space from the start so crossing 4 GiB needs no audio copy.
    text(bytes,12,large?'ds64':'JUNK');view.setUint32(16,28,true);
    if(large){view.setBigUint64(20,BigInt(dataBytes+72),true);view.setBigUint64(28,BigInt(dataBytes),true);view.setBigUint64(36,BigInt(dataBytes/2),true);}
    text(bytes,48,'fmt ');view.setUint32(52,16,true);view.setUint16(56,1,true);view.setUint16(58,1,true);
    view.setUint32(60,rate,true);view.setUint32(64,rate*2,true);view.setUint16(68,2,true);view.setUint16(70,16,true);
    text(bytes,72,'data');view.setUint32(76,large?0xffffffff:dataBytes,true);
    return bytes;
  }
  function oggChecksum(bytes){
    bytes.fill(0,22,26);let crc=0;
    for(const byte of bytes)crc=((crc<<8)^crcTable[((crc>>>24)^byte)&255])>>>0;
    new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength).setUint32(22,crc,true);return bytes;
  }
  function oggPage(packets,flags,granule,serial,sequence){
    const lacing=[];
    for(const packet of packets){let length=packet.length;while(length>=255){lacing.push(255);length-=255;}lacing.push(length);}
    if(lacing.length>255)throw new Error('Too many Ogg segments');
    const bytes=new Uint8Array(27+lacing.length+packets.reduce((sum,packet)=>sum+packet.length,0));
    const view=new DataView(bytes.buffer);text(bytes,0,'OggS');bytes[5]=flags;view.setBigUint64(6,BigInt(granule),true);
    view.setUint32(14,serial,true);view.setUint32(18,sequence,true);bytes[26]=lacing.length;bytes.set(lacing,27);
    let offset=27+lacing.length;for(const packet of packets){bytes.set(packet,offset);offset+=packet.length;}
    return oggChecksum(bytes);
  }
  function opusSamples(packet){
    if(!packet.length)throw new Error('Empty Opus packet');
    const config=packet[0]>>>3,code=packet[0]&3;
    const frame=config>=16?120*(1<<(config&3)):config>=12?480*(1<<(config&1)):[480,960,1920,2880][config&3];
    const count=code===0?1:code===3?(packet[1]||0)&63:2,samples=frame*count;
    if(!count||samples>5760)throw new Error('Invalid Opus duration');return samples;
  }
  class OggOpus {
    constructor(serial){
      this.serial=serial>>>0;this.sequence=0;this.samples=0;this.offset=0;this.packets=[];this.segments=0;this.lastPage=null;this.lastPageOffset=0;
    }
    headers(){
      const head=new Uint8Array(19),view=new DataView(head.buffer);text(head,0,'OpusHead');head[8]=1;head[9]=1;
      // Capture starts on an already-running stream; do not trim live samples.
      view.setUint16(10,0,true);view.setUint32(12,16000,true);
      const vendor=new TextEncoder().encode('HamSDR'),tags=new Uint8Array(16+vendor.length);text(tags,0,'OpusTags');
      new DataView(tags.buffer).setUint32(8,vendor.length,true);tags.set(vendor,12);
      return [this.page([head],2,0),this.page([tags],0,0)];
    }
    page(packets,flags,granule){
      const page=oggPage(packets,flags,granule,this.serial,this.sequence++);
      this.lastPageOffset=this.offset;this.offset+=page.length;this.lastPage=page;return page;
    }
    append(packet){
      if(packet.length>1275)throw new Error('Oversized Opus packet');
      const segments=Math.floor(packet.length/255)+1,output=[];
      if(this.segments+segments>255)output.push(...this.flush());
      this.packets.push(packet);this.segments+=segments;this.samples+=opusSamples(packet);
      if(this.packets.length>=50)output.push(...this.flush());return output;
    }
    flush(final=false){
      if(!this.packets.length)return [];
      const page=this.page(this.packets,final?4:0,this.samples);this.packets=[];this.segments=0;return [page];
    }
    endPatch(){if(!this.lastPage||this.sequence<=2)return null;const page=this.lastPage.slice();page[5]|=4;return{offset:this.lastPageOffset,bytes:oggChecksum(page)};}
    checkpoint(){return{lastPageOffset:this.lastPageOffset,lastPageSize:this.lastPage?.length||0};}
  }
  function counter(samples,rate,bytes){
    const seconds=Math.floor(samples/rate),hours=Math.floor(seconds/3600),minutes=Math.floor(seconds/60)%60,remaining=seconds%60;
    const pad=value=>String(value).padStart(2,'0');
    return (seconds>=3600?pad(hours)+':':'')+pad(minutes)+':'+pad(remaining)+' • '+(bytes/1048576).toFixed(2)+' MiB';
  }
  globalThis.HamRecorderCore={WAV_HEADER_SIZE,wavHeader,oggChecksum,oggPage,opusSamples,OggOpus,counter};
})();
