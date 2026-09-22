// SPDX-License-Identifier: GPL-3.0-only
const LETTERS=['','E','\n','A',' ','S','I','U','\r','D','R','J','N','F','C','K','T','Z','L','W','H','Y','P','Q','O','B','G','','M','X','V',''];
const FIGURES=['','3','\n','-',' ','\'','8','7','\r','$','4','\u0007',',','!',':','(','5','"',')','2','#','6','0','1','9','?','&','','.','/',';',''];
export const RTTY_PRESETS=Object.freeze({
  amateur45:Object.freeze({name:'RTTY Amateur 45',baud:45.45,shift:170,stopBits:1.5}),
  baud50:Object.freeze({name:'RTTY 50',baud:50,shift:170,stopBits:1.5}),
  baud75:Object.freeze({name:'RTTY 75',baud:75,shift:170,stopBits:1.5})
});

export class Ita2Decoder{
  constructor(){this.figures=false;}
  reset(){this.figures=false;}
  decode(code){
    code&=31;
    if(code===27){this.figures=true;return'';}
    if(code===31){this.figures=false;return'';}
    return(this.figures?FIGURES:LETTERS)[code]||'';
  }
}

class ToneDetector{
  constructor(sampleRate,frequency,decay){this.sampleRate=sampleRate;this.decay=decay;this.i=0;this.q=0;this.oscI=1;this.oscQ=0;this.oscillatorSamples=0;this.setFrequency(frequency);}
  setFrequency(frequency){this.frequency=frequency;const step=2*Math.PI*frequency/this.sampleRate;this.stepI=Math.cos(step);this.stepQ=Math.sin(step);}
  reset(){this.i=0;this.q=0;this.oscI=1;this.oscQ=0;this.oscillatorSamples=0;}
  push(sample){
    this.i=this.i*this.decay+sample*this.oscI;this.q=this.q*this.decay+sample*this.oscQ;
    const nextI=this.oscI*this.stepI-this.oscQ*this.stepQ;
    this.oscQ=this.oscI*this.stepQ+this.oscQ*this.stepI;this.oscI=nextI;
    if(++this.oscillatorSamples===4096){const length=Math.hypot(this.oscI,this.oscQ)||1;this.oscI/=length;this.oscQ/=length;this.oscillatorSamples=0;}
    return this.i*this.i+this.q*this.q;
  }
}

function spectrumPeak(levels,bin){
  let peak=-160;for(let offset=-1;offset<=1;offset++){const index=bin+offset;if(index>=0&&index<levels.length)peak=Math.max(peak,levels[index]);}return peak;
}

export function findRttyCandidates(levels,sampleRate,fftSize,{low=0,high=3000,shift=170,thresholdDb=7,maxCandidates=8}={}){
  const minimumBin=Math.max(1,Math.ceil((low+shift/2)*fftSize/sampleRate));
  const maximumBin=Math.min(levels.length-2,Math.floor((high-shift/2)*fftSize/sampleRate));
  if(maximumBin<=minimumBin)return[];
  const noiseValues=[];for(let bin=minimumBin;bin<=maximumBin;bin++)noiseValues.push(levels[bin]);
  noiseValues.sort((a,b)=>a-b);const noiseFloor=noiseValues[Math.floor(noiseValues.length*.35)]??-160;
  const halfShiftBins=shift/2*fftSize/sampleRate,candidates=[];
  for(let bin=minimumBin;bin<=maximumBin;bin++){
    const mark=spectrumPeak(levels,Math.round(bin-halfShiftBins)),space=spectrumPeak(levels,Math.round(bin+halfShiftBins));
    const score=Math.min(mark,space)-noiseFloor;
    if(score>=thresholdDb)candidates.push({centerFrequency:bin*sampleRate/fftSize,score,markLevel:mark,spaceLevel:space,noiseFloor});
  }
  candidates.sort((a,b)=>b.score-a.score);const selected=[];
  for(const candidate of candidates){if(selected.every(current=>Math.abs(current.centerFrequency-candidate.centerFrequency)>=Math.max(55,shift*.45)))selected.push(candidate);if(selected.length>=maxCandidates)break;}
  return selected.sort((a,b)=>a.centerFrequency-b.centerFrequency);
}

export class RttyDecoder{
  constructor(options={}){
    this.onCharacter=options.onCharacter||(()=>{});this.onStatus=options.onStatus||(()=>{});
    this.ita2=new Ita2Decoder();this.sampleIndex=0;this.afcOffset=0;this.enabled=true;
    this.configure(options,true);
  }
  configure(options={},initial=false){
    this.sampleRate=Number(options.sampleRate??this.sampleRate??48000);
    this.baud=Number(options.baud??this.baud??45.45);
    this.shift=Number(options.shift??this.shift??170);
    this.centerFrequency=Number(options.centerFrequency??this.centerFrequency??1000);
    this.reverse=Boolean(options.reverse??this.reverse??false);
    this.afc=Boolean(options.afc??this.afc??true);
    this.afcRange=Math.max(0,Number(options.afcRange??this.afcRange??50));
    this.filterBandwidth=Math.max(50,Number(options.filterBandwidth??this.filterBandwidth??Math.max(250,this.shift+100)));
    this.stopBits=Number(options.stopBits??this.stopBits??1.5);
    this.samplesPerBit=this.sampleRate/this.baud;
    // The resonator integration time acts as a lightweight input filter. It
    // rejects nearby speech energy without smearing 45--75 baud transitions.
    const integrationSeconds=Math.min(.008,Math.max(.003,1.5/this.filterBandwidth));
    const decay=Math.exp(-1/(this.sampleRate*integrationSeconds));
    if(initial||!this.mark){
      this.mark=new ToneDetector(this.sampleRate,1,decay);this.space=new ToneDetector(this.sampleRate,1,decay);
      this.markLow=new ToneDetector(this.sampleRate,1,decay);this.markHigh=new ToneDetector(this.sampleRate,1,decay);
      this.spaceLow=new ToneDetector(this.sampleRate,1,decay);this.spaceHigh=new ToneDetector(this.sampleRate,1,decay);
    }else for(const detector of [this.mark,this.space,this.markLow,this.markHigh,this.spaceLow,this.spaceHigh]){detector.sampleRate=this.sampleRate;detector.decay=decay;}
    this.updateFrequencies();this.resetFrame();
    this.statusInterval=Math.max(1,Math.round(this.sampleRate/5));this.nextStatus=this.sampleIndex+this.statusInterval;
  }
  updateFrequencies(){
    const center=this.centerFrequency+this.afcOffset,half=this.shift/2,probe=10;
    const mark=center-half,space=center+half;
    this.mark.setFrequency(mark);this.space.setFrequency(space);
    this.markLow.setFrequency(mark-probe);this.markHigh.setFrequency(mark+probe);
    this.spaceLow.setFrequency(space-probe);this.spaceHigh.setFrequency(space+probe);
  }
  setEnabled(enabled){this.enabled=Boolean(enabled);if(!this.enabled)this.resetFrame();}
  resetFrame(){this.state='idle';this.bitIndex=0;this.word=0;this.nextSample=0;this.symbol=true;this.previousSymbol=true;this.ita2.reset();}
  reset(){
    this.afcOffset=0;this.sampleIndex=0;for(const detector of [this.mark,this.space,this.markLow,this.markHigh,this.spaceLow,this.spaceHigh])detector.reset();
    this.updateFrequencies();this.resetFrame();
  }
  pushSample(sample){
    const markEnergy=this.mark.push(sample),spaceEnergy=this.space.push(sample);
    const total=markEnergy+spaceEnergy+1e-18,discriminator=(markEnergy-spaceEnergy)/total;
    let decided=this.symbol;
    if(discriminator>.06)decided=!this.reverse;else if(discriminator<-.06)decided=this.reverse;
    this.previousSymbol=this.symbol;this.symbol=decided;
    if(this.state==='idle'){
      if(this.previousSymbol&&!this.symbol){this.state='data';this.bitIndex=0;this.word=0;this.nextSample=this.sampleIndex+this.samplesPerBit*1.5;}
    }else{
      // Transitions belong near bit boundaries. Correct only a small portion
      // of the phase error, keeping the clock stable in noise.
      if(this.previousSymbol!==this.symbol){
        const boundary=this.nextSample-this.samplesPerBit*.5,error=this.sampleIndex-boundary;
        if(Math.abs(error)<this.samplesPerBit*.35){
          const correction=Math.max(-this.samplesPerBit*.06,Math.min(this.samplesPerBit*.06,error*.12));
          this.nextSample+=correction;
        }
      }
      if(this.sampleIndex>=this.nextSample){
        if(this.state==='data'){
          if(this.symbol)this.word|=1<<this.bitIndex;
          this.bitIndex++;this.nextSample+=this.samplesPerBit;
          if(this.bitIndex===5)this.state='stop';
        }else{
          if(this.symbol){const value=this.ita2.decode(this.word);if(value)this.onCharacter(value);}
          this.state='idle';
        }
      }
    }
    const low=this.markLow.push(sample)+this.spaceLow.push(sample),high=this.markHigh.push(sample)+this.spaceHigh.push(sample);
    this.sampleIndex++;
    if(this.sampleIndex>=this.nextStatus){
      const confidence=Math.abs(markEnergy-spaceEnergy)/total;
      if(this.afc&&confidence>.15){
        const gradient=(high-low)/(high+low+1e-18);
        this.afcOffset=Math.max(-this.afcRange,Math.min(this.afcRange,this.afcOffset+gradient*.8));
        this.updateFrequencies();
      }
      this.onStatus({markFrequency:this.mark.frequency,spaceFrequency:this.space.frequency,afcOffset:this.afcOffset,confidence,baud:this.baud,state:this.state});
      this.nextStatus=this.sampleIndex+this.statusInterval;
    }
  }
  process(samples){if(!this.enabled)return;for(let index=0;index<samples.length;index++)this.pushSample(samples[index]);}
}
