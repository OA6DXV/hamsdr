// SPDX-License-Identifier: GPL-3.0-only
'use strict';
(function(root){
  const clamp=(value,minimum,maximum)=>Math.max(minimum,Math.min(maximum,value));
  const byteToDb=value=>value/255*120-120;
  function percentile(values,fraction){
    if(!values.length)return NaN;
    const sorted=Array.from(values).sort((a,b)=>a-b);
    return sorted[Math.min(sorted.length-1,Math.max(0,Math.round((sorted.length-1)*fraction)))];
  }
  function histogramPercentile(histogram,count,fraction){
    const target=Math.max(0,Math.ceil(count*fraction)-1);let total=0;
    for(let index=0;index<histogram.length;index++){
      total+=histogram[index];if(total>target)return index;
    }
    return histogram.length-1;
  }
  function frameStatistics(data,rowLower,rowSpan,receiverCenter){
    if(!data||data.length<32||!Number.isFinite(rowLower)||!Number.isFinite(rowSpan)||rowSpan<=0)return null;
    const histogram=new Uint32Array(256),start=Math.ceil(data.length*.02),end=Math.floor(data.length*.98);
    const dc=(receiverCenter-rowLower)/rowSpan*data.length,dcGuard=Math.max(2,Math.round(data.length*.002));
    let count=0;
    for(let index=start;index<end;index++){
      if(Number.isFinite(dc)&&Math.abs(index-dc)<=dcGuard)continue;
      histogram[data[index]]++;count++;
    }
    if(count<24)return null;
    return{
      noiseDbfs:byteToDb(histogramPercentile(histogram,count,.25)),
      strongDbfs:byteToDb(histogramPercentile(histogram,count,.995))
    };
  }
  function finalize(noiseSamples,strongSamples){
    if(noiseSamples.length<20||strongSamples.length<20)return null;
    const noiseDbfs=percentile(noiseSamples,.5);
    const strongDbfs=Math.max(noiseDbfs+1,percentile(strongSamples,.9));
    const dynamicRange=strongDbfs-noiseDbfs;
    // The measured noise floor is the S1 reference. Every following S-unit
    // remains six dB above it, so the display keeps a stable radio-style scale.
    const noiseS=1;
    return{noiseDbfs,strongDbfs,noiseS,dynamicRange};
  }
  function sUnits(power,calibration){return 1+(power-calibration.noiseDbfs)/6;}
  function displayPosition(power,calibration){
    if(!calibration)return clamp((power+120)/120*7,0,7);
    const units=sUnits(power,calibration);
    return clamp(units<=9?(units-1)/2:4+(units-9)*6/20,0,7);
  }
  function formatS(power,calibration){
    if(!calibration)return'Escala S sin calibrar';
    const units=sUnits(power,calibration);
    if(units<=9)return`S${clamp(units,1,9).toFixed(1).replace('.0','')}`;
    return`S9+${Math.max(0,Math.round((units-9)*6))}`;
  }
  root.SMeterCalibration=Object.freeze({frameStatistics,finalize,displayPosition,formatS});
})(globalThis);
