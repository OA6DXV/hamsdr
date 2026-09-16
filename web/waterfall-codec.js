'use strict';
window.decodeWaterfallRow=bytes=>{
  if(!(bytes instanceof Uint8Array)||bytes.length<17)return null;
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  const version=bytes[0],profile=bytes[1],bins=view.getUint16(2,true),bits=bytes[4],sequence=view.getUint32(5,true),lower=view.getInt32(9,true),span=view.getUint32(13,true);
  const limits={4:[1024,8,'low'],5:[2048,8,'balanced'],6:[4096,8,'high'],7:[1024,6,'slow']},spec=limits[profile];
  if(version!==2||!spec||bins<1||bins>spec[0]||bits!==spec[1]||!span)return null;
  if(profile===7){
    const expected=17+Math.ceil(bins*bits/8);if(bytes.length!==expected)return null;
    const output=new Uint8Array(bins),mask=(1<<bits)-1;let accumulator=0,used=0,at=0;
    for(let i=17;i<bytes.length;i++){accumulator+=bytes[i]*2**used;used+=8;while(used>=bits&&at<bins){const q=accumulator&mask;output[at++]=Math.round(q*255/mask);accumulator=Math.floor(accumulator/2**bits);used-=bits;}}
    return at===bins?{data:output,profile:spec[2],sequence,lower,span}:null;
  }
  if(bytes.length<18)return null;
  const output=new Uint8Array(bins);output[0]=bytes[17];let at=1,nibble=0;
  const next=()=>{const value=(bytes[18+(nibble>>1)]>>(nibble%2?4:0))&15;nibble++;return value;};
  while(at<bins){if(18+(nibble>>1)>=bytes.length)return null;const code=next();let value;
    if(code===15){if(18+(nibble>>1)>=bytes.length)return null;const low=next();if(18+(nibble>>1)>=bytes.length)return null;value=low|(next()<<4);}
    else value=output[at-1]+code-7;
    if(value<0||value>255)return null;output[at++]=value;
  }
  return{data:output,profile:spec[2],sequence,lower,span};
};
