// SPDX-License-Identifier: GPL-3.0-only
// Tone candidates are NOT decoded transmissions. Keying/timing must confirm them.
const median = values => {
  values.sort((a,b)=>a-b);
  return values[Math.floor(values.length/2)] ?? -160;
};
// Activity gating is deliberately broader than candidate acquisition: keying
// sidebands must not erase valid samples of an already selected transmission.
export function cwCarrierPresent({levels,fftSize,sampleRate},tone){
  const bin=Math.round(tone*fftSize/sampleRate),local=[];
  let peak=-160;
  for(let j=-3;j<=3;j++)peak=Math.max(peak,levels[bin+j]??-160);
  for(let j=10;j<=24;j++)for(const k of [bin-j,bin+j])if(k>=0&&k<levels.length)local.push(levels[k]);
  let neighbour=-160;
  for(let j=Math.ceil(80*fftSize/sampleRate);j<=Math.ceil(150*fftSize/sampleRate);j++)
    for(const k of [bin-j,bin+j])neighbour=Math.max(neighbour,levels[k]??-160);
  // A weak sidelobe of a much stronger nearby station is not this carrier.
  return peak>median(local)+6&&peak>neighbour-20;
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
      for(let j=10;j<=24;j++)for(const k of [i-j,i+j])if(k>=first&&k<=last)neighbours.push(levels[k]);
      const noise=Math.max(floor,median(neighbours)),snr=peak-noise;
      // Keying broadens a real CW peak. A +/-9 Hz gate rejects clean dits;
      // compare shoulders farther out without accepting flat broadband noise.
      if(snr<10||(levels[i-8]??-160)>peak-3||(levels[i+8]??-160)>peak-3)continue;
      const denominator=levels[i-1]-2*peak+levels[i+1];
      const delta=denominator?Math.max(-.5,Math.min(.5,.5*(levels[i-1]-levels[i+1])/denominator)):0;
      peaks.push({tone:(i+delta)*binHz,snr,noise,level:peak});
    }
    peaks.sort((a,b)=>(b.level+.25*b.snr)-(a.level+.25*a.snr));
    const selected=[];
    for(const peak of peaks)if(selected.every(p=>Math.abs(p.tone-peak.tone)>35)) {
      selected.push(peak);if(selected.length>=this.maxTracks)break;
    }
    this.tracks=this.tracks.filter(t=>now-t.seen<(t.established?8:1.2));
    const assigned=new Set();
    for(const peak of selected) {
      const track=this.tracks.filter(t=>!assigned.has(t.id)&&Math.abs(t.tone-peak.tone)<30)
        .sort((a,b)=>Math.abs(a.tone-peak.tone)-Math.abs(b.tone-peak.tone))[0];
      if(track) {
        if(now-track.seen>1)track.hits=0;
        track.tone+=.35*(peak.tone-track.tone);track.snr=peak.snr;track.level=peak.level;track.noise=peak.noise;
        track.hits++;track.established ||= track.hits>=3;track.seen=now;assigned.add(track.id);
      }else {
        // Stale noise tracks must not occupy all slots when a stronger keyed
        // carrier appears. Never exceed the fixed decoder/CPU resource cap.
        if(this.tracks.length>=this.maxTracks){
          const stale=this.tracks.filter(t=>!this.protected?.has(t.id)&&!assigned.has(t.id)&&(now-t.seen>.8||peak.level>t.level+15))
            .sort((a,b)=>a.level-b.level)[0];
          if(stale)this.tracks=this.tracks.filter(t=>t.id!==stale.id);
        }
        if(this.tracks.length>=this.maxTracks)continue;
        const fresh={...peak,id:this.nextId++,hits:1,seen:now,established:false};
        this.tracks.push(fresh);assigned.add(fresh.id);
      }
    }
    return this.tracks.filter(t=>t.established).map(t=>({...t,active:now-t.seen<.8}));
  }
}
