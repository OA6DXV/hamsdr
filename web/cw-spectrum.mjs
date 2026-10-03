// SPDX-License-Identifier: GPL-3.0-only
// Overlapping Hann-window FFTs preserve narrow CW traces at a modest draw rate.
export class CwSpectrum {
  constructor(post){
    this.post=post;this.size=4096;this.rate=12000;this.hop=2000;this.count=0;this.position=0;
    this.buffer=new Float32Array(this.size);this.real=new Float64Array(this.size);this.imag=new Float64Array(this.size);
    this.window=Float64Array.from({length:this.size},(_,i)=>.5-.5*Math.cos(2*Math.PI*i/(this.size-1)));
  }
  push(samples){
    for(const sample of samples){
      this.buffer[this.position]=sample;this.position=(this.position+1)%this.size;this.count++;
      if(this.count>=this.size&&(this.count-this.size)%this.hop===0)this.emit();
    }
  }
  emit(){
    const n=this.size,r=this.real,im=this.imag;
    for(let i=0;i<n;i++){r[i]=this.buffer[(this.position+i)%n]*this.window[i];im[i]=0;}
    for(let i=1,j=0;i<n;i++){
      let bit=n>>1;for(;j&bit;bit>>=1)j^=bit;j^=bit;
      if(i<j){[r[i],r[j]]=[r[j],r[i]];[im[i],im[j]]=[im[j],im[i]];}
    }
    for(let length=2;length<=n;length<<=1){
      const angle=-2*Math.PI/length,cs=Math.cos(angle),ss=Math.sin(angle);
      for(let start=0;start<n;start+=length){
        let wr=1,wi=0;
        for(let j=0;j<length/2;j++){
          const a=start+j,b=a+length/2,tr=wr*r[b]-wi*im[b],ti=wr*im[b]+wi*r[b];
          r[b]=r[a]-tr;im[b]=im[a]-ti;r[a]+=tr;im[a]+=ti;
          const next=wr*cs-wi*ss;wi=wr*ss+wi*cs;wr=next;
        }
      }
    }
    const levels=new Float32Array(n/2+1);
    for(let i=0;i<levels.length;i++)levels[i]=20*Math.log10(Math.hypot(r[i],im[i])/(n*.25)+1e-8);
    this.post({type:'spectrum',levels,fftSize:n,sampleRate:this.rate},[levels.buffer]);
  }
}
