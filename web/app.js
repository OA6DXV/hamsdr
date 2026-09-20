'use strict';
const $ = id => document.getElementById(id);
const utf8Encoder=new TextEncoder();
const protocolVersion=1,waterfallProtocolVersion=2;
const defaults = {LSB:[-2700,-300],USB:[300,2700],AM:[-4000,4000],CW:[450,950],NFM:[-5000,5000]};
const narrowDefaults={LSB:[-2200,-500],USB:[500,2200],AM:[-2500,2500],CW:[600,800],NFM:[-3000,3000]};
let narrow=false, peakPower=-120, lastPeak=0, lastGraph=0, lastDraw=0, occupants=[];
let frequency=7100000, mode='LSB', low=-2700, high=-300, center=7100500, rate=1024000;
let bandConfigured=false,protocolReady=false,opusAvailable=true,currentResourcePolicy=null;
let zoom=1, viewCenter=center, socket, retry=500, timer, tuneTimer, lastRow, view='waterfall', muted=false;
let dynamicSpectrumBottom=null,dynamicSpectrumTop=null;
let context, node, gain, audioStarting=false, audioEnabled=false, audioEverStarted=false, audioProfile='balanced', opusDecoder=null, opusSupportPromise=null, lastOpusSequence=null, spectrumFrames=0, audioPackets=0;
let spectrumHistory=[];
let waterfallPreference='balanced',waterfallProfile='balanced',waterfallSpeed=1,drawAverage=0,lastProfileRequest=0,profileTimer,pendingRow,waterfallFrame;
let trafficBytes=0,trafficAt=performance.now();
let lastWaterfallSequence=null,lastWaterfallAt=0,streamWarningTimer,socketOpenedAt=0,interruptionSamples=[];
let memories=[];
try {
  const saved=JSON.parse(localStorage.getItem('hamsdr-memories')||'[]');
  if(Array.isArray(saved)) memories=saved.filter(m=>m && typeof m.name==='string' && defaults[m.mode] &&
    Number.isFinite(m.frequency) && m.frequency>0 &&
    Number.isFinite(m.low) && Number.isFinite(m.high) && m.low>=-6000 && m.high<=6000 && m.high-m.low>=100).slice(0,30);
  const savedWaterfall=localStorage.getItem('hamsdr-waterfall');
  const preference={mobile:'low',raw:'high',exp1024:'low',exp2048:'balanced',exp4096:'high'}[savedWaterfall]||savedWaterfall;
  if(['auto','slow','low','balanced','high'].includes(preference))waterfallPreference=preference;
  const savedAudio=localStorage.getItem('hamsdr-audio-profile');
  const migratedAudio={original:'raw','opus-high':'balanced','opus-low':'mobile'}[savedAudio]||savedAudio;
  if(['raw','balanced','mobile'].includes(migratedAudio))audioProfile=migratedAudio;
} catch {}
const canvas=$('waterfall'), ctx=canvas.getContext('2d'), scale=$('scale'), dial=scale.getContext('2d'), projectionCanvas=document.createElement('canvas');
const lower=()=>viewCenter-rate/(2*zoom), width=()=>rate/zoom;
const beat=()=>mode==='CW'?700:0;
function waterfallFps(){if(waterfallSpeed==='high')return 11.71875;return(waterfallProfile==='slow'?5:7.8125)/waterfallSpeed;}
function waterfallSourceGap(){if(waterfallSpeed==='high')return 4/3;return(waterfallProfile==='slow'?25/8:2)*waterfallSpeed;}
function message(text=''){ $('message').textContent=text; }
function applyResourcePolicy(policy){
  if(policy!==undefined)currentResourcePolicy=policy;
  policy=currentResourcePolicy;
  const waterfallRanks={slow:0,low:1,balanced:2,high:3},audioRanks={mobile:0,balanced:1,raw:2};
  const waterfallMax=policy?.waterfall_max||'high',audioMax=policy?.audio_max||'raw';
  document.querySelectorAll('#waterfall-quality option').forEach(option=>{
    option.disabled=option.value!=='auto'&&waterfallRanks[option.value]>waterfallRanks[waterfallMax];
  });
  document.querySelectorAll('#audio-quality option').forEach(option=>{
    option.disabled=audioRanks[option.value]>audioRanks[audioMax]||(option.value!=='raw'&&!opusAvailable);
  });
  const notice=$('resource-policy');notice.hidden=!policy?.restricted;
  notice.textContent=policy?.restricted?`${policy.message} Máximo: cascada ${waterfallMax}, audio ${audioMax}.`:'';
}
function reportStreamInterruption(reason){
  if(document.visibilityState==='hidden')return;
  const now=performance.now();
  interruptionSamples=interruptionSamples.filter(sample=>now-sample.time<=30000);
  interruptionSamples.push({time:now,reason});
  const warning=$('stream-warning');
  warning.dataset.samples=String(interruptionSamples.length);
  if(interruptionSamples.length<3)return;
  interruptionSamples=[];
  warning.hidden=false;warning.dataset.reason=reason;clearTimeout(streamWarningTimer);
  streamWarningTimer=setTimeout(()=>{warning.hidden=true;delete warning.dataset.reason;delete warning.dataset.samples;},45000);
}
window.reportStreamInterruption=reportStreamInterruption;
function observeWaterfall(sequence){
  const now=performance.now(),expected=waterfallSourceGap(),delta=lastWaterfallSequence===null?expected:(sequence-lastWaterfallSequence)>>>0;
  if(lastWaterfallSequence!==null&&(delta>expected*2||(lastWaterfallAt&&now-lastWaterfallAt>Math.max(1500,3500/waterfallFps()))))reportStreamInterruption('waterfall');
  lastWaterfallSequence=sequence;lastWaterfallAt=now;
}
function resetOpusDecoder(){if(opusDecoder){if(opusDecoder.state!=='closed')opusDecoder.close();opusDecoder=null;}lastOpusSequence=null;}
function resetAudio(){if(node)node.port.postMessage({reset:true});resetOpusDecoder();}
const opusConfig={codec:'opus',sampleRate:48000,numberOfChannels:1};
function browserSupportsOpus(){
  if(!opusSupportPromise)opusSupportPromise=typeof AudioDecoder==='undefined'?Promise.resolve(false):AudioDecoder.isConfigSupported(opusConfig).then(result=>result.supported).catch(()=>false);
  return opusSupportPromise;
}
function fallbackFromOpus(reason){
  if(audioProfile==='raw')return;
  audioProfile='raw';
  try{localStorage.setItem('hamsdr-audio-profile',audioProfile);}catch{}
  resetAudio();showAudioProfile();sendAudioProfile();message(`${reason} Se activó audio raw PCM16.`);
}
function createOpusDecoder(){
  if(opusDecoder&&opusDecoder.state!=='closed')return opusDecoder;
  if(typeof AudioDecoder==='undefined')return null;
  try{
    opusDecoder=new AudioDecoder({
      output:audio=>{
        try{
          const options={planeIndex:0,format:'f32-planar'},buffer=new ArrayBuffer(audio.allocationSize(options)),rate=audio.sampleRate;
          audio.copyTo(buffer,options);audio.close();
          if(node&&context?.state==='running')node.port.postMessage({codec:'float32',rate,payload:buffer,record:recording},[buffer]);
        }catch(error){audio.close();fallbackFromOpus(`No se pudo convertir Opus: ${error.message}.`);}
      },
      error:error=>fallbackFromOpus(`El decodificador Opus falló: ${error.message}.`)
    });
    opusDecoder.configure(opusConfig);return opusDecoder;
  }catch(error){resetOpusDecoder();fallbackFromOpus(`Opus no está disponible: ${error.message}.`);return null;}
}
function decodeOpusPacket(bytes){
  if(bytes.byteLength<5)return;
  const sequence=new DataView(bytes.buffer,bytes.byteOffset,4).getUint32(0,true);
  let decoder=createOpusDecoder();
  if(!decoder)return;
  if(decoder.decodeQueueSize>8){reportStreamInterruption('audio-decoder');resetOpusDecoder();decoder=createOpusDecoder();if(!decoder)return;}
  if(lastOpusSequence!==null&&((sequence-lastOpusSequence)>>>0)!==1)reportStreamInterruption('audio-sequence');
  lastOpusSequence=sequence;
  try{decoder.decode(new EncodedAudioChunk({type:'key',timestamp:sequence*20000,duration:20000,data:bytes.subarray(4)}));}
  catch(error){fallbackFromOpus(`No se pudo decodificar Opus: ${error.message}.`);}
}
function recommendedWaterfallProfile(){
  return'balanced';
}
function sendWaterfallPreference(forced){
  const profile=forced||(waterfallPreference==='auto'?recommendedWaterfallProfile():waterfallPreference);
  if(socket?.readyState===WebSocket.OPEN){socket.send(JSON.stringify({type:'waterfall',preference:waterfallPreference,profile}));lastProfileRequest=performance.now();}
}
function sendWaterfallView(){if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'waterfall-view',zoom,center:Math.round(viewCenter)}));}
function sendWaterfallSpeed(){if(socket?.readyState===WebSocket.OPEN){const value=$('wfspeed').value;socket.send(JSON.stringify({type:'waterfall-speed',divisor:value==='high'?'high':Number(value)}));}}
function sendAudioProfile(){if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'audio-profile',profile:audioProfile}));}
function showWaterfallProfile(){
  const names={slow:'conexión lenta · 1024 bins/6 bits',low:'baja definición · 1024 bins/8 bits',balanced:'balanceado · 2048 bins/8 bits',high:'alta definición · 4096 bins/8 bits'};
  const highOption=$('speed-high'),unlocked=waterfallProfile==='high';highOption.hidden=!unlocked;highOption.disabled=!unlocked;
  const fps=waterfallFps();
  $('waterfall-profile-status').textContent=`Activo: ${names[waterfallProfile]} · ${fps.toFixed(1).replace('.0','')} fps`;
  $('waterfall').dataset.profile=waterfallProfile;
}
function showAudioProfile(){
  const names={raw:'PCM16 · 16 kHz',balanced:'Opus · 32 kb/s · 16 kHz',mobile:'Opus · 12 kb/s · 16 kHz'};
  $('audio-profile-status').textContent=names[audioProfile];
  $('audio-quality').value=audioProfile;
}
const isSpectrum=()=>view==='spectrum-fixed'||view==='spectrum-dynamic';
function spectrumRange(){
  const brightness=Number($('brightness').value);
  if(view==='spectrum-dynamic'&&dynamicSpectrumBottom!==null&&dynamicSpectrumTop!==null)return{bottom:dynamicSpectrumBottom-brightness,top:dynamicSpectrumTop-brightness};
  return{bottom:-74-brightness,top:-40-brightness};
}
function percentile(sorted,fraction){
  if(!sorted.length)return-120;
  const position=(sorted.length-1)*fraction,lowerIndex=Math.floor(position),upperIndex=Math.ceil(position);
  return sorted[lowerIndex]+(sorted[upperIndex]-sorted[lowerIndex])*(position-lowerIndex);
}
function updateDynamicSpectrum(data,rowLower,rowSpan){
  const first=Math.max(0,Math.floor((lower()-rowLower)/rowSpan*data.length));
  const last=Math.min(data.length,Math.ceil((lower()+width()-rowLower)/rowSpan*data.length));
  const values=[];
  for(let i=first;i<last;i++)values.push(data[i]/255*120-120);
  if(!values.length)return;
  values.sort((a,b)=>a-b);
  const noise=percentile(values,.25),strong=percentile(values,.995);
  let targetBottom=Math.max(-120,Math.min(-25,noise-6));
  let targetTop=Math.min(0,Math.max(strong+4,targetBottom+34));
  if(targetTop-targetBottom<34)targetBottom=Math.max(-120,targetTop-34);
  if(dynamicSpectrumBottom===null||dynamicSpectrumTop===null){dynamicSpectrumBottom=targetBottom;dynamicSpectrumTop=targetTop;}
  else{
    const topRate=targetTop>dynamicSpectrumTop ? .65 : .04;
    const bottomRate=targetBottom<dynamicSpectrumBottom ? .30 : .08;
    dynamicSpectrumTop+=(targetTop-dynamicSpectrumTop)*topRate;
    dynamicSpectrumBottom+=(targetBottom-dynamicSpectrumBottom)*bottomRate;
  }
  if(dynamicSpectrumTop-dynamicSpectrumBottom<34)dynamicSpectrumBottom=dynamicSpectrumTop-34;
}
function renderSpectrumAxis(){
  const axis=$('spectrum-axis');axis.hidden=!isSpectrum();if(axis.hidden)return;
  const {bottom,top}=spectrumRange();
  axis.style.height=`${canvas.getBoundingClientRect().height}px`;axis.replaceChildren();
  for(let value=Math.ceil(bottom/5)*5;value<=top;value+=5){
    const tick=document.createElement('span'),label=document.createElement('b');
    tick.style.top=`${(top-value)/(top-bottom)*100}%`;label.textContent=String(value);tick.append(label);axis.append(tick);
  }
  const roundedBottom=Math.round(bottom),roundedTop=Math.round(top);
  axis.setAttribute('aria-label',`Escala vertical del espectro: ${roundedBottom} a ${roundedTop} dBFS`);
  canvas.dataset.scaleMode=view==='spectrum-dynamic'?'dynamic':'fixed';
  canvas.dataset.scaleBottom=bottom.toFixed(1);canvas.dataset.scaleTop=top.toFixed(1);
}
function controls(){
  $('frequency').value=(frequency/1000).toFixed(2);
  $('mode-display').textContent=mode==='NFM'?'FM':mode;
  $('low').value=low; $('high').value=high;
  $('bandwidth').value=((high-low)/1000).toFixed(2);
  document.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===mode&&!!b.dataset.narrow===narrow)));
  scale.setAttribute('aria-valuenow',String(frequency/1000));
  scale.setAttribute('aria-valuetext',`${(frequency/1000).toFixed(2)} kHz, ${mode}`);
  drawScale();
}
function tune(){
  if(!Number.isFinite(frequency)||!Number.isFinite(low)||!Number.isFinite(high)||low < -6000||high > 6000||high-low<100){message('Revisa los límites del filtro (−6000 a 6000 Hz).');return;}
  frequency=Math.round(Math.max(center-rate/2,Math.min(center+rate/2,frequency)));
  if(frequency<lower()||frequency>lower()+width()){const oldLower=lower(),oldWidth=width();viewCenter=frequency;clampView();reprojectWaterfall(oldLower,oldWidth);sendWaterfallView();}
  controls(); message();
  clearTimeout(tuneTimer);
  tuneTimer=setTimeout(()=>{
    if(socket?.readyState===WebSocket.OPEN) socket.send(JSON.stringify({type:'tune',frequency,mode,low,high,squelch:$('squelch').checked?Number($('threshold').value):-150,notch:$('notch').checked,nr:Number($('nr').value)}));
  },100);
}
function clampView(){viewCenter=Math.max(center-rate/2+width()/2,Math.min(center+rate/2-width()/2,viewCenter));}
function clearWaterfall(){ctx.fillStyle='#000';ctx.fillRect(0,0,canvas.width,canvas.height);}
function redrawHistory(){clearWaterfall();lastDraw=0;const rows=spectrumHistory.slice(-canvas.height);if(isSpectrum()){if(rows.length)drawRow(rows.at(-1),true);}else for(const row of rows)drawRow(row,true);}
function reprojectWaterfall(oldLower,oldWidth){
  if(isSpectrum()){if(lastRow)drawRow(lastRow,true);return;}
  const w=canvas.width,h=canvas.height,newLower=lower(),newWidth=width();
  const start=Math.max(oldLower,newLower),end=Math.min(oldLower+oldWidth,newLower+newWidth);
  if(projectionCanvas.width!==w)projectionCanvas.width=w;if(projectionCanvas.height!==h)projectionCanvas.height=h;
  projectionCanvas.getContext('2d').drawImage(canvas,0,0);
  clearWaterfall();if(end<=start)return;
  const sx=(start-oldLower)/oldWidth*w,sw=(end-start)/oldWidth*w;
  const dx=(start-newLower)/newWidth*w,dw=(end-start)/newWidth*w;
  ctx.imageSmoothingEnabled=false;ctx.drawImage(projectionCanvas,sx,0,sw,h,dx,0,dw,h);
}
function changeZoom(next,anchor=frequency,fraction=.5){
  const oldLower=lower(),oldWidth=width();
  zoom=Math.max(1,Math.min(64,next));viewCenter=anchor+(.5-fraction)*width();clampView();
  $('zoom-label').value=`${zoom.toFixed(zoom<10?1:0).replace('.0','')}×`;reprojectWaterfall(oldLower,oldWidth);drawScale();sendWaterfallView();
}
function drawScale(){
  const w=scale.width; dial.fillStyle='#050505';dial.fillRect(0,0,w,44);dial.font='10px monospace';
  const x=f=>(f-lower())/width()*w;
  const tickTarget=width()/Math.max(3,Math.floor(w/48)), base=10**Math.floor(Math.log10(tickTarget));
  const step=[1,2,5,10].map(v=>v*base).find(v=>v>=tickTarget);
  dial.strokeStyle='#aaa';dial.fillStyle='#ddd';dial.beginPath();
  for(let f=Math.ceil(lower()/step)*step;f<lower()+width();f+=step){const p=x(f);dial.moveTo(p,0);dial.lineTo(p,9);dial.fillText((f/1000).toFixed(step<1000?1:0),Math.max(0,Math.min(w-40,p-18)),25);}
  dial.stroke();
  dial.fillStyle='rgba(255,255,0,.15)';dial.fillRect(x(frequency+low-beat()),29,Math.max(2,x(frequency+high)-x(frequency+low)),13);
  dial.strokeStyle='#ffec00';dial.beginPath();
  dial.moveTo(x(frequency+low-beat())-5,42);dial.lineTo(x(frequency+low-beat()),30);dial.lineTo(x(frequency+high-beat()),30);dial.lineTo(x(frequency+high-beat())+5,42);
  dial.moveTo(x(frequency),27);dial.lineTo(x(frequency),44);dial.stroke();
  drawUsers();drawMarkers();
}
function drawMarkers(){
  $('markers').replaceChildren();if(!$('labels').checked)return;
  for(const m of memories){
    const fraction=(m.frequency-lower())/width();if(fraction<0||fraction>1)continue;
    const b=document.createElement('button');b.textContent=m.name;b.title=`${m.name}: ${(m.frequency/1000).toFixed(2)} kHz`;
    b.style.left=`${Math.max(.04,Math.min(.96,fraction))*100}%`;b.onclick=()=>{({frequency,mode,low,high}=m);narrow=!!m.narrow;tune();};$('markers').append(b);
  }
}
function drawUsers(){
  $('users').replaceChildren();
  for(const user of occupants){
    const fraction=(user.frequency-lower())/width();
    if(fraction<0||fraction>1)continue;
    const row=document.createElement('div');row.className='user';row.dataset.frequency=user.frequency;row.dataset.id=user.id;
    const b=document.createElement('button');b.textContent=user.name;b.title=`${user.name}: ${(user.frequency/1000).toFixed(2)} kHz ${user.mode}`;
    b.style.left=`${Math.max(.02,Math.min(.98,fraction))*100}%`;b.style.color=user.color;
    b.onclick=()=>{frequency=user.frequency;tune();};row.append(b);$('users').append(row);
  }
  const c=$('users-scale'),s=c.getContext('2d');c.width=scale.width;s.fillStyle='#000';s.fillRect(0,0,c.width,c.height);
  s.drawImage(scale,0,0,scale.width,26,0,0,c.width,24);
}
window.addEventListener('radio-event',({detail})=>{if(detail.type==='presence'){occupants=detail.users;drawUsers();$('listeners').textContent=occupants.length;}});
function drawRow(frame,replay=false){
  const drawStarted=performance.now(),data=frame.data||frame,rowLower=frame.lower??center-rate/2,rowSpan=frame.span??rate;
  lastRow=frame;if(!replay){++spectrumFrames;canvas.dataset.frames=String(spectrumFrames);}
  if($('pause').checked&&!replay)return;
  lastDraw=performance.now();
  if(!replay){spectrumHistory.push({data:data.slice(),lower:rowLower,span:rowSpan});if(spectrumHistory.length>600)spectrumHistory.shift();canvas.dataset.history=String(spectrumHistory.length);}
  const w=canvas.width,h=canvas.height;
  if(!isSpectrum())ctx.drawImage(canvas,0,1,w,h-1,0,0,w,h-1);else clearWaterfall();
  const row=ctx.createImageData(w,1),brightness=Number($('brightness').value);
  if(view==='spectrum-dynamic'&&(!replay||dynamicSpectrumBottom===null))updateDynamicSpectrum(data,rowLower,rowSpan);
  const range=spectrumRange();
  if(isSpectrum())ctx.beginPath();
  for(let i=0;i<w;i++){
    const rf=lower()+i/w*width(),relative=(rf-rowLower)/rowSpan;
    const bin=Math.max(0,Math.min(data.length-1,Math.floor(relative*data.length)));
    const binEnd=Math.min(data.length,Math.max(bin+1,Math.ceil((relative+width()/w/rowSpan)*data.length)));
    let peak=relative<0||relative>=1?0:data[bin];for(let j=bin+1;j<binEnd;j++)peak=Math.max(peak,data[j]);
    const db=peak/255*120-120,contrast=$('wfmode').value==='weak'?12:$('wfmode').value==='strong'?-12:0;
    const v=isSpectrum()?Math.max(0,Math.min(1,(db-range.bottom)/(range.top-range.bottom))):Math.max(0,Math.min(1,(db+74+brightness+contrast)/34)),color=window.radioPalette[Math.round(v*255)];
    for(let c=0;c<3;c++)row.data[i*4+c]=color[c];row.data[i*4+3]=255;
    if(isSpectrum()){const y=h-v*h;if(i===0)ctx.moveTo(i,y);else ctx.lineTo(i,y);}
  }
  if(!isSpectrum())ctx.putImageData(row,0,h-1);
  else{ctx.lineTo(w,h);ctx.lineTo(0,h);ctx.closePath();const gradient=ctx.createLinearGradient(0,h,0,0);gradient.addColorStop(0,'#210038');gradient.addColorStop(.55,'#7900a8');gradient.addColorStop(1,'#e180ff');ctx.fillStyle=gradient;ctx.fill();renderSpectrumAxis();}
  if(!replay){
    const elapsed=performance.now()-drawStarted;drawAverage=drawAverage?drawAverage*.9+elapsed*.1:elapsed;
    if(waterfallPreference==='auto'&&drawAverage>14&&waterfallProfile!=='slow'&&performance.now()-lastProfileRequest>15000)sendWaterfallPreference('slow');
  }
}
function enqueueRow(frame){
  pendingRow=frame;if(waterfallFrame)return;waterfallFrame=requestAnimationFrame(()=>{waterfallFrame=undefined;if(!pendingRow)return;const latest=pendingRow;pendingRow=undefined;drawRow(latest);});
}
function connect(){
  clearTimeout(timer);
  const address=new URL('./ws',location.href);address.protocol=location.protocol==='https:'?'wss:':'ws:';
  socket=new WebSocket(address);
  socket.binaryType='arraybuffer';
  socket.onopen=()=>{retry=500;socketOpenedAt=performance.now();lastWaterfallSequence=null;lastWaterfallAt=0;protocolReady=false;socket.send(JSON.stringify({type:'hello',protocol:protocolVersion,waterfall:waterfallProtocolVersion}));};
  socket.onmessage=({data})=>{
    trafficBytes+=typeof data==='string'?utf8Encoder.encode(data).byteLength:data.byteLength;
    if(typeof data==='string'){
      const msg=JSON.parse(data);
      window.dispatchEvent(new CustomEvent('radio-event',{detail:msg}));
      if(msg.type==='hello'){
        if(msg.protocol!==protocolVersion||msg.waterfall!==waterfallProtocolVersion){message('El servidor usa un protocolo incompatible.');socket.close(1002,'Incompatible protocol');return;}
        protocolReady=true;if(bandConfigured){tune();sendWaterfallView();}sendWaterfallPreference();sendWaterfallSpeed();sendAudioProfile();window.radioSend({type:'audio',enabled:audioEnabled});window.dispatchEvent(new Event('radio-open'));
      }else if(msg.type==='status'){
        center=msg.center;rate=msg.sample_rate;$('listeners').textContent=msg.users;
        if(!bandConfigured){
          frequency=Number.isFinite(msg.initial_frequency)?msg.initial_frequency:Math.round(center/1000)*1000;
          viewCenter=center;bandConfigured=true;
          const minimum=(center-rate/2)/1000,maximum=(center+rate/2)/1000;
          $('frequency').min=String(minimum);$('frequency').max=String(maximum);
          scale.setAttribute('aria-valuemin',String(minimum));scale.setAttribute('aria-valuemax',String(maximum));
          memories=memories.filter(memory=>memory.frequency>=center-rate/2&&memory.frequency<=center+rate/2);
          controls();renderMemories();if(protocolReady){sendWaterfallView();tune();}
        }
        $('demo').hidden=!msg.demo;
        const labels={streaming:'● Receptor conectado',demo:'● Receptor de prueba',connecting:'Conectando al SDR…',disconnected:'SDR desconectado · reconectando…',reconnecting:'Recuperando receptor…','connect-failed':'SDR no disponible · reintentando…','invalid-header':'Entrada IQ incompatible','stopped':'SDR detenido'};
        $('connection').textContent=labels[msg.source]||msg.source;
        $('stats').textContent=`Carga: ${msg.cpu_percent??0}% de un núcleo · ${msg.users} oyente(s) · Tráfico total: ${msg.kbps??0} kb/s · Recuperaciones: ${msg.restarts}`;
        opusAvailable=msg.opus_available!==false;applyResourcePolicy();
        if(msg.opus_available===false&&audioProfile!=='raw')fallbackFromOpus('Opus no está instalado en el servidor.');
        if(!['streaming','demo'].includes(msg.source))resetAudio();
      }else if(msg.type==='waterfall-profile'){
        waterfallProfile=msg.profile;if(msg.preference){waterfallPreference=msg.preference;$('waterfall-quality').value=waterfallPreference;}lastWaterfallSequence=null;lastWaterfallAt=0;showWaterfallProfile();
      }else if(msg.type==='waterfall-speed'){
        waterfallSpeed=msg.divisor;lastWaterfallSequence=null;lastWaterfallAt=0;$('wfspeed').value=String(msg.divisor);$('waterfall').dataset.speed=String(msg.divisor);showWaterfallProfile();
      }else if(msg.type==='stream-stats'){
        $('client-traffic').dataset.serverKbps=String(msg.kbps);
      }else if(msg.type==='audio-state'){
        audioEnabled=msg.enabled;if(msg.profile)audioProfile=msg.profile;showAudioProfile();showAudioState();
      }else if(msg.type==='audio-profile'){
        audioProfile=msg.profile;resetAudio();showAudioProfile();
      }else if(msg.type==='resource-policy')applyResourcePolicy(msg);
      else if(msg.type==='resource-limit')message(msg.message);
      else if(msg.type==='error')message(msg.message);
      else if(msg.type==='tuned'){$('frequency').dataset.confirmed=String(msg.frequency);}
      return;
    }
    const bytes=new Uint8Array(data),kind=bytes[0];
    if(kind===1)enqueueRow({data:bytes.subarray(1),lower:center-rate/2,span:rate});
    if(kind===7){const decoded=window.decodeWaterfallRow(bytes.subarray(1));if(!decoded){message('Fila de cascada inválida; reconectando…');socket.close(1002,'Invalid waterfall row');return;}observeWaterfall(decoded.sequence);canvas.dataset.sequence=String(decoded.sequence);canvas.dataset.lower=String(decoded.lower);canvas.dataset.span=String(decoded.span);enqueueRow(decoded);}
    if(kind===2&&audioEnabled){
      audioPackets++;$('audio-status').dataset.packets=String(audioPackets);
      const payload=data.slice(1),rate=16000;
      if(recording)recordPCM(payload.slice(0));
      if(node&&context.state==='running')node.port.postMessage({codec:'pcm16',rate,payload,record:false},[payload]);
    }
    if([10,11].includes(kind)&&audioEnabled){audioPackets++;$('audio-status').dataset.packets=String(audioPackets);decodeOpusPacket(bytes.subarray(1));}
    if(kind===3)updateMeter(Number(new TextDecoder().decode(bytes.subarray(1))));
  };
  socket.onclose=()=>{if(socketOpenedAt&&performance.now()-socketOpenedAt>3000)reportStreamInterruption('websocket');socketOpenedAt=0;protocolReady=false;lastWaterfallSequence=null;lastWaterfallAt=0;resetAudio();occupants=[];drawUsers();window.dispatchEvent(new Event('radio-close'));$('connection').textContent='Conexión interrumpida · reintentando…';timer=setTimeout(connect,retry);retry=Math.min(retry*2,10000);};
  socket.onerror=()=>socket.close();
}
window.radioSend=data=>{if(socket?.readyState!==WebSocket.OPEN)return false;socket.send(JSON.stringify(data));return true;};
function updateMeter(power){
  const now=performance.now();$('meter').value=power;$('power').value=`${power.toFixed(1)} dBFS`;
  if(power>peakPower||now-lastPeak>2000){peakPower=power;lastPeak=now;}
  $('peak').value=`${peakPower.toFixed(1)} dBFS`;$('peak-bar').style.left=`${Math.max(1,Math.min(98,(peakPower+120)/120*100))}%`;
  const speed=Number($('sgraphchoice').value),c=$('sgraph');c.hidden=!speed;
  if(speed&&now-lastGraph>=speed*100){
    lastGraph=now;const g=c.getContext('2d');g.drawImage(c,1,0,c.width-1,c.height,0,0,c.width-1,c.height);
    g.fillStyle='#eee';g.fillRect(c.width-1,0,1,c.height);g.fillStyle='#00f';g.fillRect(c.width-1,Math.max(0,Math.min(c.height-1,-power/120*c.height)),1,2);
  }
}
async function listen(){
  if(audioStarting||audioEnabled)return;
  audioStarting=true;
  try{
    if('audioSession'in navigator){try{navigator.audioSession.type='playback';}catch{}}
    if(!context){context=new AudioContext({latencyHint:'interactive'});}
    // resume is invoked during the click gesture, before awaiting module loading.
    const resumed=context.resume();
    if(!node){
      if(window.isSecureContext&&context.audioWorklet){
        await context.audioWorklet.addModule(new URL('./audio-worklet.js',location.href));
        node=new AudioWorkletNode(context,'radio-audio',{numberOfInputs:0,numberOfOutputs:1,outputChannelCount:[1]});
        document.documentElement.dataset.audioEngine='worklet';
      }else{
        if(!window.ClassicRadioAudio||!context.createScriptProcessor)throw new Error('Este navegador no ofrece un reproductor de audio compatible.');
        node=new window.ClassicRadioAudio(context);document.documentElement.dataset.audioEngine='classic';
      }
      gain=context.createGain();node.connect(gain).connect(context.destination);
      node.port.onmessage=({data})=>{if(data.recording){if(recordBytes===0&&data.rate)recordingRate=data.rate;recordPCM(data.recording);}if(data.rms!==undefined)$('audio-status').dataset.rms=String(data.rms);if(data.underrun!==undefined&&audioEnabled)reportStreamInterruption('audio');};
      context.onstatechange=()=>{if(audioEnabled&&context.state!=='running')$('audio-status').textContent='Audio suspendido por el navegador. Pulsa Pausar y vuelve a iniciarlo.';};
    }
    await resumed;
    if(audioProfile!=='raw'&&!await browserSupportsOpus())fallbackFromOpus('Este navegador no ofrece decodificación Opus mediante WebCodecs.');
    gain.gain.value=muted?0:10**(Number($('volume').value)/20);
    audioEnabled=true;audioEverStarted=true;window.radioSend({type:'audio',enabled:true});showAudioState();message();
  }catch(error){message(error.message);$('audio-status').textContent='No se pudo iniciar el audio. Pulsa Escuchar para reintentar.';}
  finally{audioStarting=false;}
}
function showAudioState(){
  $('listen').textContent=audioEnabled?'Pausar audio':'Iniciar audio';$('listen').setAttribute('aria-pressed',String(audioEnabled));
  $('listen').className=audioEnabled?'audio-playing':audioEverStarted?'audio-ready':'audio-initial';
  $('audio-status-top').textContent=audioEnabled?'Audio transmitiendo':'Audio detenido';
  $('audio-status').textContent=audioEnabled?'Audio activado.':'Audio detenido; no consume ancho de banda.';
}
async function pauseAudio(){
  if(!audioEnabled)return;audioEnabled=false;window.radioSend({type:'audio',enabled:false});resetAudio();stopRecording();
  if(context?.state==='running')await context.suspend();showAudioState();
}
$('listen').addEventListener('click',()=>audioEnabled?pauseAudio():listen());
$('mute').addEventListener('change',()=>{muted=$('mute').checked;$('mute').setAttribute('aria-pressed',String(muted));if(gain)gain.gain.value=muted?0:10**(Number($('volume').value)/20);});
$('volume').addEventListener('input',()=>{if(gain)gain.gain.value=muted?0:10**(Number($('volume').value)/20);});
$('frequency').addEventListener('change',()=>{frequency=Number($('frequency').value)*1000;tune();});
document.querySelectorAll('[data-step]').forEach(b=>b.addEventListener('click',()=>{frequency+=Number(b.dataset.step);tune();}));
document.querySelectorAll('[data-fix]').forEach(b=>b.addEventListener('click',()=>{frequency=Math.round(frequency/1000)*1000;tune();}));
document.querySelectorAll('[data-mode]').forEach(b=>b.addEventListener('click',()=>{mode=b.dataset.mode;narrow=!!b.dataset.narrow;[low,high]=(narrow?narrowDefaults:defaults)[mode];tune();}));
for(const id of ['low','high','squelch','threshold','notch','nr'])$(id).addEventListener('change',()=>{low=Number($('low').value);high=Number($('high').value);tune();});
$('filter-narrow').onclick=()=>{if(high-low>200){low+=50;high-=50;tune();}};
$('filter-wide').onclick=()=>{low=Math.max(-6000,low-50);high=Math.min(6000,high+50);tune();};
$('zoom-in').addEventListener('click',()=>changeZoom(zoom*2));$('zoom-out').addEventListener('click',()=>changeZoom(zoom/2));$('full-band').addEventListener('click',()=>changeZoom(1));
$('max-zoom').onclick=()=>changeZoom(64);
document.querySelectorAll('[name=view]').forEach(r=>r.addEventListener('change',()=>{$('panorama').hidden=r.value==='none';}));
$('wfmode').onchange=()=>{view=$('wfmode').value;if(view==='spectrum-dynamic'){dynamicSpectrumBottom=null;dynamicSpectrumTop=null;}renderSpectrumAxis();redrawHistory();};
$('wfsize').onchange=()=>{canvas.height=Number($('wfsize').value);canvas.style.height=`${canvas.height}px`;renderSpectrumAxis();redrawHistory();};
$('wfspeed').onchange=sendWaterfallSpeed;
$('brightness').addEventListener('input',()=>{if(isSpectrum()){renderSpectrumAxis();redrawHistory();}});
$('labels').onchange=drawMarkers;
$('waterfall-quality').value=waterfallPreference;
$('waterfall-quality').addEventListener('change',()=>{waterfallPreference=$('waterfall-quality').value;try{localStorage.setItem('hamsdr-waterfall',waterfallPreference);}catch{}sendWaterfallPreference();});
$('audio-quality').value=audioProfile;
$('audio-quality').addEventListener('change',async()=>{if(recording)stopRecording();audioProfile=$('audio-quality').value;if(audioProfile!=='raw'&&!await browserSupportsOpus()){fallbackFromOpus('Este navegador no ofrece decodificación Opus mediante WebCodecs.');return;}try{localStorage.setItem('hamsdr-audio-profile',audioProfile);}catch{}resetAudio();showAudioProfile();sendAudioProfile();});
let suppressWaterfallClick=false;
canvas.addEventListener('click',e=>{if(suppressWaterfallClick){suppressWaterfallClick=false;return;}const rect=canvas.getBoundingClientRect();frequency=lower()+(e.clientX-rect.left)/rect.width*width();tune();});
canvas.addEventListener('wheel',e=>{e.preventDefault();changeZoom(e.deltaY<0?zoom*2:zoom/2);},{passive:false});
const waterfallTouches=new Map();let pinch=null;
canvas.addEventListener('pointerdown',e=>{if(e.pointerType!=='touch')return;waterfallTouches.set(e.pointerId,{x:e.clientX,y:e.clientY});try{canvas.setPointerCapture(e.pointerId);}catch{}if(waterfallTouches.size===2){const p=[...waterfallTouches.values()],rect=canvas.getBoundingClientRect(),mid=(p[0].x+p[1].x)/2;pinch={distance:Math.max(1,Math.hypot(p[0].x-p[1].x,p[0].y-p[1].y)),zoom,anchor:lower()+(mid-rect.left)/rect.width*width(),fraction:(mid-rect.left)/rect.width};suppressWaterfallClick=true;}});
canvas.addEventListener('pointermove',e=>{if(!waterfallTouches.has(e.pointerId))return;waterfallTouches.set(e.pointerId,{x:e.clientX,y:e.clientY});if(pinch&&waterfallTouches.size===2){e.preventDefault();const p=[...waterfallTouches.values()],distance=Math.hypot(p[0].x-p[1].x,p[0].y-p[1].y);pinch.next=Math.max(1,Math.min(64,pinch.zoom*distance/pinch.distance));canvas.style.transformOrigin=`${pinch.fraction*100}% 50%`;canvas.style.transform=`scaleX(${pinch.next/pinch.zoom})`;$('zoom-label').value=`${pinch.next.toFixed(pinch.next<10?1:0).replace('.0','')}×`;}});
function endWaterfallTouch(e){const finished=pinch&&waterfallTouches.size===2?pinch:null;waterfallTouches.delete(e.pointerId);if(waterfallTouches.size<2){pinch=null;canvas.style.transform='';canvas.style.transformOrigin='';if(finished?.next)changeZoom(finished.next,finished.anchor,finished.fraction);}}
canvas.addEventListener('pointerup',endWaterfallTouch);canvas.addEventListener('pointercancel',endWaterfallTouch);
let drag=null;
scale.addEventListener('pointerdown',e=>{
  const rect=scale.getBoundingClientRect(),x=e.clientX-rect.left;
  const px=f=>(f-lower())/width()*rect.width;
  drag=Math.abs(x-px(frequency+low-beat()))<9?'low':Math.abs(x-px(frequency+high-beat()))<9?'high':'dial';
  scale.setPointerCapture(e.pointerId);
});
scale.addEventListener('pointermove',e=>{
  if(!drag)return;const rect=scale.getBoundingClientRect();const f=lower()+(e.clientX-rect.left)/rect.width*width();
  if(drag==='low')low=Math.round(Math.max(-6000,Math.min(high-100,f-frequency+beat())));
  else if(drag==='high')high=Math.round(Math.min(6000,Math.max(low+100,f-frequency+beat())));
  else frequency=f;tune();
});
scale.addEventListener('pointerup',()=>{drag=null;});scale.addEventListener('pointercancel',()=>{drag=null;});
scale.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();frequency+=(e.key==='ArrowLeft'?-1:1)*(e.shiftKey?1000:100);tune();}});
function renderMemories(){
  $('memories').replaceChildren(new Option('(new)',''));
  memories.forEach((m,i)=>$('memories').add(new Option(`${m.name} · ${(m.frequency/1000).toFixed(2)} ${m.mode}`,String(i))));
  drawMarkers();
}
function storeMemories(){try{localStorage.setItem('hamsdr-memories',JSON.stringify(memories));}catch{message('No se pudieron guardar las memorias en este navegador.');}renderMemories();}
$('save').addEventListener('click',()=>{if(memories.length>=30){message('Máximo 30 memorias.');return;}memories.push({name:$('memory-name').value.trim()||'Memoria',frequency,mode,low,high,narrow});storeMemories();});
function recall(){const m=memories[Number($('memories').value)];if($('memories').value===''||!m||!defaults[m.mode])return;({frequency,mode,low,high}=m);narrow=!!m.narrow;$('memory-name').value=m.name;tune();}
$('memories').addEventListener('change',recall);$('recall').onclick=recall;
$('delete').addEventListener('click',()=>{if($('memories').value==='')return;memories.splice(Number($('memories').value),1);storeMemories();});
window.addEventListener('pagehide',()=>{stopRecording();clearTimeout(timer);socket.onclose=null;socket.close();});
window.addEventListener('pageshow',e=>{if(e.persisted)connect();});
new ResizeObserver(()=>{const available=Math.max(1,Math.round($('panorama').getBoundingClientRect().width));if(canvas.width!==available){canvas.width=available;redrawHistory();}scale.width=available;drawScale();clearTimeout(profileTimer);profileTimer=setTimeout(()=>{if(waterfallPreference==='auto')sendWaterfallPreference();},500);}).observe($('panorama'));
const networkConnection=navigator.connection||navigator.mozConnection||navigator.webkitConnection;
if(networkConnection?.addEventListener)networkConnection.addEventListener('change',()=>{if(waterfallPreference==='auto')sendWaterfallPreference();});
setInterval(()=>{const now=performance.now(),elapsed=(now-trafficAt)/1000;$('client-traffic').textContent=`${(trafficBytes*8/elapsed/1000).toFixed(1)} kb/s`;trafficBytes=0;trafficAt=now;},1000);
clearWaterfall();controls();renderMemories();showAudioProfile();showAudioState();connect();
let recording=false,recordChunks=[],recordBytes=0,recordingRate=16000,recordTimer,downloadURL;
function recordPCM(pcm){
  recordChunks.push(pcm);recordBytes+=pcm.byteLength;
  $('record-status').textContent=`Grabando · ${Math.floor(recordBytes/(recordingRate*2))} s`;
  if(recordBytes>=32*1024*1024)stopRecording();
}
function stopRecording(){
  if(!recording)return;recording=false;clearTimeout(recordTimer);
  // Canonical PCM16 WAV works even when MediaRecorder/codecs are unavailable.
  const header=new ArrayBuffer(44),v=new DataView(header);
  const text=(offset,s)=>{for(let i=0;i<s.length;i++)v.setUint8(offset+i,s.charCodeAt(i));};
  text(0,'RIFF');v.setUint32(4,36+recordBytes,true);text(8,'WAVE');text(12,'fmt ');
  v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,recordingRate,true);
  v.setUint32(28,recordingRate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);text(36,'data');v.setUint32(40,recordBytes,true);
  const blob=new Blob([header,...recordChunks],{type:'audio/wav'});
  if(downloadURL)URL.revokeObjectURL(downloadURL);downloadURL=URL.createObjectURL(blob);
  $('download').href=downloadURL;$('download').download=`hamsdr-${new Date().toISOString().replaceAll(':','-')}.wav`;
  $('download').hidden=false;$('record').textContent='iniciar';$('record-status').textContent=`${Math.round(recordBytes/(recordingRate*2))} s guardados`;
  recordChunks=[];
}
$('record').onclick=async()=>{
  if(recording){stopRecording();return;}
  await listen();if(!node||context.state!=='running')return;
  recordChunks=[];recordBytes=0;recordingRate=audioProfile==='raw'?16000:48000;recording=true;$('record').textContent='detener';$('record-status').textContent='Grabando…';$('download').hidden=true;
  recordTimer=setTimeout(stopRecording,30*60*1000);
};
