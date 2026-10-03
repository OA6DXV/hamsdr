// SPDX-License-Identifier: GPL-3.0-only
'use strict';
window.CWDecodePanel=class {
  constructor({listen,mute,configure}){
    this.worker=null;this.ready=false;this.pending=0;this.active=false;this.audio=false;this.compatible=true;this.text='';this.frames=0;
    this.configuration={tone:700,wpm:20,minimum:450,maximum:950};this.floor=null;
    this.$=id=>document.getElementById(id);this.configure=configure;
    this.$('cw-audio-start').onclick=()=>void listen();this.$('cw-mute').onclick=mute;
    this.$('cw-clear').onclick=()=>{this.text='';this.$('cw-terminal').textContent='';};
    for(const id of ['cw-tone','cw-wpm'])this.$(id).onchange=()=>configure();
    this.$('cw-waterfall').onclick=event=>{
      if(this.decoderType==='cwformer')return;
      const rect=event.currentTarget.getBoundingClientRect(),{minimum,maximum}=this.configuration;
      this.$('cw-tone').value=String(Math.round(minimum+(maximum-minimum)*Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width))));configure();
    };
  }
  update({active,audio,compatible=true,decoderType='morse',mode,frequency,low,high}){
    if(decoderType!==this.decoderType){this.stop();this.decoderType=decoderType;this.text='';this.$('cw-terminal').textContent='';}
    this.active=active;this.audio=audio;this.compatible=compatible;this.$('cw-panel').hidden=!active;
    this.$('cw-title').textContent=`${decoderType==='cwformer'?'CW Experimental':'Morse'} · ${mode} · ${(frequency/1000).toFixed(3)} kHz`;
    this.$('cw-tone').disabled=decoderType==='cwformer';this.$('cw-wpm').disabled=decoderType==='cwformer';
    this.$('cw-tone-marker').hidden=decoderType==='cwformer';
    this.$('cw-audio-start').hidden=!(active&&!audio&&compatible);
    this.$('cw-waterfall-message').hidden=compatible;
    this.$('cw-waterfall-message').textContent=compatible?'':'Perfil de audio incompatible, reinicie el modo digital';
    const minimum=low>=0?low:high<=0?Math.abs(high):0,maximum=Math.max(minimum+100,Math.abs(low),Math.abs(high));
    const toneInput=this.$('cw-tone');toneInput.min=Math.max(100,minimum);toneInput.max=Math.min(5000,maximum);
    const tone=Math.max(Number(toneInput.min),Math.min(Number(toneInput.max),Number(toneInput.value)||700));toneInput.value=String(tone);
    const wpm=Math.max(5,Math.min(60,Number(this.$('cw-wpm').value)||20));this.$('cw-wpm').value=String(wpm);
    const signature=JSON.stringify([mode,frequency,low,high,tone,wpm]),changed=signature!==this.signature;this.signature=signature;
    if(minimum!==this.configuration.minimum||maximum!==this.configuration.maximum){
      const canvas=this.$('cw-waterfall');canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);this.floor=null;
    }
    this.configuration={tone,wpm,minimum,maximum};
    const axis=document.querySelector('.cw-frequency-axis');axis.replaceChildren();
    for(let i=0;i<=6;i++){const label=document.createElement('span');label.textContent=`${Math.round(minimum+(maximum-minimum)*i/6)}${i===6?' Hz':''}`;axis.append(label);}
    this.$('cw-tone-marker').style.left=`${(tone-minimum)/(maximum-minimum)*100}%`;
    if(!active||!audio||!compatible){this.stop();this.$('cw-state').textContent=!compatible?'Perfil de audio incompatible':audio?'Inactivo':'Inicia audio para decodificar.';return;}
    if(!this.worker){this.start();}
    else if(changed){this.ready=false;this.worker.postMessage({type:'configure',tone,wpm});}
  }
  start(){
    this.$('cw-state').textContent='Preparando decoder…';this.ready=false;this.pending=0;
    const worker=this.worker=new Worker(new URL(this.decoderType==='cwformer'?'./cwformer-worker.mjs':'./cw-worker.js',location.href),{type:'module'});
    worker.onmessage=({data})=>{
      if(worker!==this.worker)return;
      if(data.type==='ready'){this.ready=true;this.$('cw-state').textContent='Buscando señal CW…';}
      else if(data.type==='consumed'){this.pending=Math.max(0,this.pending-1);}
      else if(data.type==='text'){
        this.text=(this.text+data.text).slice(-20000);const terminal=this.$('cw-terminal');terminal.textContent=this.text;terminal.scrollTop=terminal.scrollHeight;
      }else if(data.type==='spectrum')this.draw(data);
      else if(data.type==='status')this.$('cw-state').textContent=data.message||(data.locked?`${data.wpm.toFixed(1)} WPM · ${data.keyed?'Señal':'Escuchando'}`:'Reconociendo velocidad…');
      else if(data.type==='error')this.failed(data.message);
    };
    worker.onerror=()=>this.failed('No se pudo cargar el decoder CW.');
    worker.postMessage({type:'configure',...this.configuration});
  }
  failed(message){this.stop();this.$('cw-state').textContent=message;}
  stop(){this.worker?.terminate();this.worker=null;this.ready=false;this.pending=0;}
  samples(payload,sequence){
    // Bound worker backlog to avoid accumulating stale audio on slow phones.
    if(!this.ready||this.pending>=4)return;
    this.pending++;this.worker.postMessage({type:'samples',payload,sequence},[payload]);
  }
  draw({levels,fftSize,sampleRate}){
    if(!this.active)return;
    const canvas=this.$('cw-waterfall'),g=canvas.getContext('2d'),w=canvas.width,h=canvas.height,{minimum,maximum}=this.configuration;
    const visible=[];for(let i=Math.floor(minimum*fftSize/sampleRate);i<=Math.ceil(maximum*fftSize/sampleRate);i++)visible.push(levels[i]??-160);
    const sorted=visible.slice().sort((a,b)=>a-b),target=sorted[Math.floor(sorted.length*.25)]-3;
    this.floor=this.floor===null?target:this.floor+(target-this.floor)*.10;
    const ceiling=this.floor+40;g.drawImage(canvas,0,1,w,h-1,0,0,w,h-1);const row=g.createImageData(w,1);
    for(let x=0;x<w;x++){
      const bin=(minimum+(maximum-minimum)*x/w)*fftSize/sampleRate;
      const level=levels[Math.round(bin)]??-160,color=window.radioPalette[Math.max(0,Math.min(255,Math.round((level-this.floor)/(ceiling-this.floor)*255)))];
      row.data.set([...color,255],x*4);
    }
    g.putImageData(row,0,h-1);canvas.dataset.frames=String(++this.frames);canvas.dataset.minimum=String(minimum);canvas.dataset.maximum=String(maximum);
  }
};
