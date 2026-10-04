# SPDX-License-Identifier: GPL-3.0-only
"""CW auto-acquisition, AFC and bounded multidetection in the actual browser worker."""
import asyncio
import os
import re
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from playwright.async_api import async_playwright, expect
from server import application, GATEWAY

FEED = r'''async ({tones,noise=false,carrier=false,drift=0,gap=false})=>{
  let seed=12345;
  const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296};
  const rate=12000,pattern={'C':'-.-.','Q':'--.-','D':'-..','E':'.','W':'.--','1':'.----','A':'.-'};
  function wave(tone,wpm){
    const pcm=[],unit=Math.round(rate*1.2/wpm);
    const silence=n=>{for(let j=0;j<n;j++)pcm.push(0)};
    silence(rate);
    for(const [wi,word] of ['CQ','DE','W1AW','CQ','DE','W1AW'].entries()){
      for(const [ci,ch]of[...word].entries()){
        if(ci)silence(unit*2);
        for(const mark of pattern[ch]){
          for(let j=0;j<unit*(mark==='-'?3:1);j++){
            const t=pcm.length/rate;
            pcm.push(.20*Math.sin(2*Math.PI*(tone*t+drift*t*t/2)));
          }
          silence(unit);
        }
      }
      silence(unit*6);
    }
    silence(rate*2);return pcm;
  }
  const signals=tones.map(([hz,wpm])=>wave(hz,wpm));
  const length=signals.length?Math.max(...signals.map(s=>s.length)):rate*15;
  let sequence=0;
  for(let i=0;i<length;i+=1200){
    while(!cwPanel.ready||cwPanel.pending)await new Promise(r=>setTimeout(r,1));
    if(gap&&i===120000)sequence+=2;
    const count=Math.min(1200,length-i),raw=new Int16Array(count);
    for(let j=0;j<count;j++){
      const value=signals.reduce((sum,s)=>sum+(s[i+j]||0),0)+(noise?(random()-.5)*.03:0)+(carrier?.2*Math.sin(2*Math.PI*800*(i+j)/rate):0);
      raw[j]=Math.round(value*32767);
    }
    cwPanel.samples(raw.buffer,sequence++);
  }
  while(cwPanel.pending)await new Promise(r=>setTimeout(r,1));
  return {text:document.querySelector('#cw-terminal').textContent,streams:cwPanel.streams,
    tracking:document.querySelector('#cw-tracking').textContent,state:document.querySelector('#cw-state').textContent};
}'''

async def main():
    with tempfile.TemporaryDirectory(prefix='hamsdr-cw-tracking-') as directory:
        args=SimpleNamespace(demo=True,source_host='127.0.0.1',source_port=1231,max_clients=5,
            origin='',digimodes=True,full_quality_sessions_per_ip=5,max_bandwidth_kbps_per_ip=1000000,
            site_config=Path(__file__).resolve().parents[1]/'site.example.toml',database=Path(directory)/'community.sqlite3')
        app=application(args);app[GATEWAY].site_config['digimodes']=True
        runner=web.AppRunner(app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/'
        try:
            async with async_playwright() as p:
                browser=await getattr(p,os.environ.get('BROWSER','chromium')).launch()
                for viewport in [{'width':1440,'height':1000},{'width':390,'height':844}]:
                    page=await browser.new_page(viewport=viewport);errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    await page.goto(url+'?digital=CW&mode=USB&low=0&high=3000&multi=1&mute=1')
                    await expect(page.locator('#cw-multi')).to_be_checked()
                    await page.locator('#cw-audio-start').click()
                    await page.wait_for_function('()=>cwPanel.ready')
                    # Keep transport/control UI real while supplying deterministic PCM to its worker.
                    await page.evaluate('''()=>{const original=socket.onmessage;socket.onmessage=e=>{if(typeof e.data==='string')original(e)};cwPanel.stop();updateCwPanel();}''')
                    await page.wait_for_function('()=>cwPanel.ready')
                    result=await page.evaluate(FEED,{'tones':[[1050,20],[1170,28]],'noise':True})
                    print('multi',viewport['width'],result)
                    confirmed=[s for s in result['streams'] if s['confirmed']]
                    assert len(confirmed)==2,result
                    assert all('DE W1AW' in s['text'] for s in confirmed),result
                    assert all(abs(s['tone']-target)<2 for s,target in zip(sorted(confirmed,key=lambda s:s['tone']),[1050,1170]))
                    await expect(page.locator('#cw-stream-count')).to_have_text('Streams detectados: 2')
                    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    before=await page.evaluate('cwPanel.frames')
                    await page.locator('#cw-clear').click()
                    await expect(page.locator('#cw-terminal')).to_have_text('')
                    assert await page.evaluate('cwPanel.frames')==before
                    # Select a stream without moving RF tuning or changing the source.
                    before_frequency=await page.evaluate('frequency')
                    await page.locator('#cw-streams tr[data-tone]').first.click()
                    assert await page.evaluate('frequency')==before_frequency
                    await page.wait_for_function('()=>cwPanel.ready')
                    result=await page.evaluate(FEED,{'tones':[[1050,20]],'drift':1,'gap':True})
                    assert 'DE W1AW' in result['text'],result
                    assert 'AFC +' in result['tracking'],result
                    tracked_tone=float(re.search(r'Tono ([\d.]+)',result['tracking']).group(1))
                    assert 1055<tracked_tone<1100,result
                    # Neither noise nor a stationary carrier confirms a CW transmission.
                    await page.locator('#cw-clear').click()
                    await page.evaluate('()=>{cwPanel.stop();updateCwPanel();}')
                    await page.wait_for_function('()=>cwPanel.ready')
                    result=await page.evaluate(FEED,{'tones':[],'noise':True,'carrier':True})
                    assert not any(s['confirmed'] for s in result['streams']),result
                    assert not result['text'].strip(),result
                    await page.locator('#cw-auto').uncheck();await page.locator('#cw-afc').uncheck()
                    shared=await page.evaluate('sharedUrl().href')
                    assert 'auto=0' in shared and 'afc=0' in shared and 'multi=1' in shared
                    await page.goto(shared)
                    await expect(page.locator('#cw-auto')).not_to_be_checked()
                    await expect(page.locator('#cw-afc')).not_to_be_checked()
                    await expect(page.locator('#cw-multi')).to_be_checked()
                    assert await page.evaluate('cwPanel.worker===null')
                    # Neural inference remains opt-in; do not download/run the model during this test.
                    await page.locator('[data-digital-menu]').select_option('CWFORMER')
                    await expect(page.locator('#cw-multi')).to_be_disabled()
                    await expect(page.locator('#cw-multi-panel')).to_be_hidden()
                    assert not errors,errors
                    await page.close()
                await browser.close()
        finally:await runner.cleanup()
    print('CW tracking browser tests passed: auto, AFC drift, multi, noise/carrier rejection, clear, mobile and URLs')

if __name__=='__main__':asyncio.run(main())
