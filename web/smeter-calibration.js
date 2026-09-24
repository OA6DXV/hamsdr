// SPDX-License-Identifier: GPL-3.0-only
'use strict';
(function(root){
  const clamp=(value,minimum,maximum)=>Math.max(minimum,Math.min(maximum,value));
  function percentile(values,fraction){
    if(!values.length)return NaN;
    const sorted=Array.from(values).sort((a,b)=>a-b);
    return sorted[Math.min(sorted.length-1,Math.max(0,Math.round((sorted.length-1)*fraction)))];
  }
  function finalizePower(samples){
    if(!samples||samples.length<20)return null;
    // These samples are taken after the receiver's selected channel filter.
    // A lower temporal percentile remains representative when a station is
    // active during part of the 15-second measurement.
    const noiseDbfs=percentile(samples,.25);
    const strongDbfs=Math.max(noiseDbfs+1,percentile(samples,.9));
    const dynamicRange=strongDbfs-noiseDbfs;
    // The measured noise floor is the S1 reference. Every following S-unit
    // remains six dB above it, so the display keeps a stable radio-style scale.
    const noiseS=1;
    return{noiseDbfs,strongDbfs,noiseS,dynamicRange,source:'channel-power'};
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
  root.SMeterCalibration=Object.freeze({finalizePower,displayPosition,formatS});
})(globalThis);
