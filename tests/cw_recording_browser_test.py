# SPDX-License-Identifier: GPL-3.0-only
"""Replay a captured 12 kHz CW WAV through the shipped Morse browser worker."""
import asyncio
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import wave
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from playwright.async_api import async_playwright, expect
from server import application, GATEWAY

async def main():
    if len(sys.argv)!=2:
        raise SystemExit('Usage: python tests/cw_recording_browser_test.py /path/to/12k-mono-pcm16.wav')
    path=Path(sys.argv[1])
    with wave.open(str(path),'rb') as recording:
        spec=(recording.getnchannels(),recording.getsampwidth(),recording.getframerate(),recording.getnframes())
        if spec[:3]!=(1,2,12000):raise SystemExit(f'Expected mono PCM16 12 kHz, got {spec[:3]}')
        audio=recording.readframes(recording.getnframes())
    print(f'Input: {spec[3]/12000:.1f}s, {len(audio):,} PCM bytes, tone scan range 0–3000 Hz',flush=True)
    with tempfile.TemporaryDirectory(prefix='hamsdr-cw-recording-') as directory:
        args=SimpleNamespace(demo=True,source_host='127.0.0.1',source_port=1231,max_clients=5,
            origin='',digimodes=True,full_quality_sessions_per_ip=5,max_bandwidth_kbps_per_ip=1000000,
            site_config=Path(__file__).resolve().parents[1]/'site.example.toml',database=Path(directory)/'community.sqlite3')
        app=application(args);app[GATEWAY].site_config['digimodes']=True
        runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/'
        try:
            async with async_playwright() as p:
                browser=await getattr(p,os.environ.get('BROWSER','chromium')).launch()
                page=await browser.new_page(viewport={'width':1024,'height':900});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                await page.goto(url+'?digital=CW&mode=USB&low=0&high=3000&tone=700&wpm=20&multi=1&mute=1')
                await expect(page.locator('#cw-multi')).to_be_checked()
                await page.locator('#cw-audio-start').click();await page.wait_for_function('()=>cwPanel.ready')
                await page.evaluate('''()=>{const original=socket.onmessage;socket.onmessage=e=>{if(typeof e.data==='string')original(e)};cwPanel.stop();updateCwPanel();window.cwReplayTrace=[];window.cwTraceTimer=setInterval(()=>cwReplayTrace.push({state:document.querySelector('#cw-state').textContent,tracking:document.querySelector('#cw-tracking').textContent,text:cwPanel.text}),250)}''')
                await page.wait_for_function('()=>cwPanel.ready')
                payload=base64.b64encode(audio).decode('ascii')
                result=await page.evaluate('''async encoded=>{
                  const bytes=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));let sequence=0;
                  for(let offset=0;offset<bytes.length;offset+=2400){
                    while(!cwPanel.ready||cwPanel.pending)await new Promise(r=>setTimeout(r,1));
                    const pcm=bytes.slice(offset,offset+2400).buffer;cwPanel.samples(pcm,sequence++);
                  }
                  while(cwPanel.pending)await new Promise(r=>setTimeout(r,2));
                  clearInterval(cwTraceTimer);
                  return {text:cwPanel.text,streams:cwPanel.streams,tracking:document.querySelector('#cw-tracking').textContent,
                    state:document.querySelector('#cw-state').textContent,trace:cwReplayTrace};
                }''',payload)
                result['page_errors']=errors
                print(json.dumps(result,ensure_ascii=False))
                assert not errors,errors
                assert result['trace'], 'The real worker must report status while replaying.'
                assert len(result['streams'])<=6
                await browser.close()
        finally:await runner.cleanup()

if __name__=='__main__':asyncio.run(main())
