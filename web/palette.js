'use strict';
// Interpolate the familiar purple/yellow radio palette once at startup.
window.radioPalette = Array.from({length:256},(_,i)=>{
  const stops=[[0,0,0],[0,0,95],[80,0,175],[175,0,255],[232,90,200],[255,240,0],[255,255,255]];
  const p=i/255*6,k=Math.min(5,Math.floor(p)),f=p-k;
  return stops[k].map((v,c)=>Math.round(v+(stops[k+1][c]-v)*f));
});
