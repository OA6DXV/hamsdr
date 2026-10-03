# SPDX-License-Identifier: GPL-3.0-only
"""Exercise the neural model in a real browser, including PCM and lifecycle."""
import asyncio
from pathlib import Path
import sys
import tempfile
import wave
import base64
import os
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from playwright.async_api import async_playwright, expect
from server import application, GATEWAY

async def main():
    with tempfile.TemporaryDirectory(prefix='hamsdr-cwformer-') as directory:
        args=SimpleNamespace(demo=True,source_host='127.0.0.1',source_port=1231,max_clients=5,
            origin='',digimodes=True,full_quality_sessions_per_ip=5,max_bandwidth_kbps_per_ip=1000000,
            site_config=Path(__file__).resolve().parents[1]/'site.example.toml',database=Path(directory)/'db.sqlite3')
        app=application(args);app[GATEWAY].site_config['digimodes']=True
        runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/'
        try:
            async with async_playwright() as p:
                browser=await getattr(p,os.environ.get('BROWSER','chromium')).launch();page=await browser.new_page();errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('console',lambda message:print('Browser:',message.text) if message.type=='error' else None)
                await page.goto(url+'?digital=CWFORMER&mute=1')
                await expect(page.locator('#cw-title')).to_contain_text('CW Experimental')
                await expect(page.locator('#cw-audio-start')).to_be_visible()
                await page.locator('#cw-audio-start').click()
                try:await page.wait_for_function('()=>cwPanel.ready',timeout=30000)
                except Exception:
                    print('Decoder state:',await page.locator('#cw-state').text_content());raise
                await page.evaluate('''()=>{const original=socket.onmessage;socket.onmessage=event=>{if(typeof event.data==='string')original(event)};cwPanel.stop();updateCwPanel();}''')
                await page.wait_for_function('()=>cwPanel.ready',timeout=120000)
                decoded=await page.evaluate('''async()=>{
                    const symbols={C:'-.-.',Q:'--.-',D:'-..',E:'.',W:'.--','1':'.----',A:'.-'};
                    const pcm=[],rate=12000,unit=720,silence=n=>{for(let i=0;i<n;i++)pcm.push(0)};
                    silence(rate);
                    for(const word of ['CQ','CQ','DE','W1AW','W1AW']){
                        for(const [ci,char] of [...word].entries()){
                            if(ci)silence(unit*2);
                            for(const mark of symbols[char]){
                                const count=unit*(mark==='-'?3:1);
                                for(let i=0;i<count;i++)pcm.push(Math.round(10000*Math.sin(2*Math.PI*700*pcm.length/rate)));
                                silence(unit);
                            }
                        }
                        silence(unit*6);
                    }
                    silence(rate*4);let sequence=0;
                    for(let i=0;i<pcm.length;i+=1200){
                        while(!cwPanel.ready||cwPanel.pending)await new Promise(r=>setTimeout(r,5));
                        const values=Int16Array.from(pcm.slice(i,i+1200));cwPanel.samples(values.buffer,sequence++);
                    }
                    while(cwPanel.pending)await new Promise(r=>setTimeout(r,5));
                    return document.getElementById('cw-terminal').textContent;
                }''')
                print('CWformer synthetic decode:',repr(decoded))
                assert 'CQ' in decoded and 'W1AW' in decoded,decoded
                for path in sys.argv[1:]:
                    with wave.open(path,'rb') as recording:
                        assert recording.getnchannels()==1 and recording.getsampwidth()==2 and recording.getframerate()==12000
                        payload=recording.readframes(recording.getnframes())+bytes(12000*2*4)
                    await page.locator('[data-digital-menu]').select_option('')
                    await page.locator('[data-digital-menu]').select_option('CWFORMER')
                    await page.wait_for_function('()=>cwPanel.ready',timeout=30000)
                    await page.locator('#cw-clear').click()
                    result=await page.evaluate('''async encoded=>{
                        const raw=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));let sequence=0;
                        for(let offset=0;offset<raw.length;offset+=2400){
                            while(!cwPanel.ready||cwPanel.pending)await new Promise(r=>setTimeout(r,5));
                            cwPanel.samples(raw.slice(offset,offset+2400).buffer,sequence++);
                        }
                        while(cwPanel.pending)await new Promise(r=>setTimeout(r,5));
                        return document.getElementById('cw-terminal').textContent;
                    }''',base64.b64encode(payload).decode())
                    print('Real recording:',Path(path).name,repr(result))
                await page.locator('[data-cw-mode]').click();await page.wait_for_function('()=>cwPanel.ready')
                await expect(page.locator('#cw-title')).to_contain_text('Morse')
                await page.locator('[data-digital-menu]').select_option('FT4')
                await expect(page.locator('#digital-panel')).to_be_visible()
                assert await page.evaluate('cwPanel.worker===null')
                await page.locator('[data-digital-menu]').select_option('RTTY')
                await expect(page.locator('#rtty-panel')).to_be_visible()
                assert not errors,errors
                await browser.close()
        finally:await runner.cleanup()
    print('CWformer browser inference and mode selector tests passed')

if __name__=='__main__':asyncio.run(main())
