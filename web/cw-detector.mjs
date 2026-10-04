// SPDX-License-Identifier: GPL-3.0-only
// Tone candidates are NOT decoded transmissions. Keying/timing must confirm them.
const median = values => {
  values.sort((a,b)=>a-b);
  return values[Math.floor(values.length/2)] ?? -160;
};
// A cheap complex preselector isolates neighbouring carriers before cw-dit DSP.
// The original single-tone decoder remains unchanged and is reused per channel.
export class CwChannel {
  constructor(tone,wpm=20){
    this.cos=1;this.sin=0;this.i=new Float64Array(4);this.q=new Float64Array(4);
    this.width=Math.max(30,Math.min(150,wpm*2.5));this.gate=0;
    this.alpha=1-Math.exp(-2*Math.PI*this.width/12000);
    this.retune(tone);
  }
  retune(tone){const step=2*Math.PI*tone/12000;this.cs=Math.cos(step);this.ss=Math.sin(step);}
  setNoise(db){this.gate=3*Math.pow(10,db/20)*Math.sqrt(this.width/(12000/4096));}
  process(samples){
    const output=new Float32Array(samples.length),{i,q,alpha}=this;
    let c=this.cos,s=this.sin;
    for(let n=0;n<samples.length;n++){
      let re=2*samples[n]*c,im=2*samples[n]*s;
      for(let k=0;k<4;k++){i[k]+=alpha*(re-i[k]);q[k]+=alpha*(im-q[k]);re=i[k];im=q[k];}
      // Preselection must not turn broadband noise into an apparently keyed carrier.
      output[n]=Math.hypot(re,im)>=this.gate?re*c+im*s:0;
      const next=c*this.cs-s*this.ss;s=s*this.cs+c*this.ss;c=next;
    }
    const norm=Math.hypot(c,s)||1;this.cos=c/norm;this.sin=s/norm;
    return output;
  }
}
export class CwToneTracker {
  constructor({minimum=100,maximum=5000,maxTracks=6}={}) {
    this.minimum=minimum;this.maximum=maximum;this.maxTracks=maxTracks;
    this.tracks=[];this.nextId=1;
  }
  update({levels,fftSize,sampleRate},now) {
    const binHz=sampleRate/fftSize;
    const first=Math.max(3,Math.ceil(Math.max(100,this.minimum)/binHz));
    const last=Math.min(levels.length-4,Math.floor(Math.min(5000,this.maximum)/binHz));
    const floor=median(Array.from(levels.slice(first,last+1)));
    const peaks=[];
    for(let i=first;i<=last;i++) {
      const peak=levels[i];
      if(!Number.isFinite(peak)||peak<-105||peak<floor+12||peak<=levels[i-1]||peak<levels[i+1])continue;
      const neighbours=[];
      for(let j=4;j<=12;j++)for(const k of [i-j,i+j])if(k>=first&&k<=last)neighbours.push(levels[k]);
      const noise=Math.max(floor,median(neighbours)),snr=peak-noise;
      if(snr<10||levels[i-3]>peak-5||levels[i+3]>peak-5)continue;
      const denominator=levels[i-1]-2*peak+levels[i+1];
      const delta=denominator?Math.max(-.5,Math.min(.5,.5*(levels[i-1]-levels[i+1])/denominator)):0;
      peaks.push({tone:(i+delta)*binHz,snr,noise,level:peak});
    }
    peaks.sort((a,b)=>b.snr-a.snr);
    const selected=[];
    for(const peak of peaks)if(selected.every(p=>Math.abs(p.tone-peak.tone)>35)) {
      selected.push(peak);if(selected.length>=this.maxTracks)break;
    }
    this.tracks=this.tracks.filter(t=>now-t.seen<8);
    const assigned=new Set();
    for(const peak of selected) {
      const track=this.tracks.filter(t=>!assigned.has(t.id)&&Math.abs(t.tone-peak.tone)<30)
        .sort((a,b)=>Math.abs(a.tone-peak.tone)-Math.abs(b.tone-peak.tone))[0];
      if(track) {
        if(now-track.seen>1)track.hits=0;
        track.tone+=.35*(peak.tone-track.tone);track.snr=peak.snr;track.level=peak.level;track.noise=peak.noise;
        track.hits++;track.established ||= track.hits>=2;track.seen=now;assigned.add(track.id);
      }else if(this.tracks.length<this.maxTracks) {
        const fresh={...peak,id:this.nextId++,hits:1,seen:now,established:false};
        this.tracks.push(fresh);assigned.add(fresh.id);
      }
    }
    return this.tracks.filter(t=>t.established).map(t=>({...t,active:now-t.seen<.8}));
  }
}
