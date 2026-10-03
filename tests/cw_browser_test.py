# SPDX-License-Identifier: GPL-3.0-only
"""Isolated browser tests for CW/WASM, digital lifecycle and responsive layout."""
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from playwright.async_api import async_playwright, expect
from server import application, GATEWAY

async def main():
    with tempfile.TemporaryDirectory(prefix='hamsdr-cw-test-') as directory:
        args=SimpleNamespace(demo=True,source_host='127.0.0.1',source_port=1231,max_clients=5,
                             origin='',digimodes=True,full_quality_sessions_per_ip=5,
                             max_bandwidth_kbps_per_ip=1000000,
                             site_config=Path(__file__).resolve().parents[1]/'site.example.toml',
                             database=Path(directory)/'community.sqlite3')
        app=application(args);app[GATEWAY].site_config['digimodes']=True
        runner=web.AppRunner(app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/'
        try:
            async with async_playwright() as p:
                browser=await p.chromium.launch()
                for viewport in [{'width':1440,'height':1000},{'width':390,'height':844}]:
                    page=await browser.new_page(viewport=viewport);errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    await page.goto(url)
                    await expect(page.locator('[data-cw-mode]')).to_be_visible()
                    await expect(page.locator('#cw-panel')).to_be_hidden()
                    assert await page.evaluate('cwPanel.worker===null')
                    positions=await page.locator('[data-rtty-mode],[data-cw-mode],[data-digital-menu]').evaluate_all('(buttons)=>buttons.map(b=>{const r=b.getBoundingClientRect();return [r.x,r.y]})')
                    assert len({pos[1] for pos in positions})==1 and positions[0][0]<positions[1][0]<positions[2][0]
                    await page.locator('[data-cw-mode]').click()
                    await expect(page.locator('#cw-panel')).to_be_visible()
                    await expect(page.locator('#audio-quality')).to_have_value('digiraw')
                    await expect(page.locator('#mode-display')).to_have_text('CW')
                    await page.wait_for_function('()=>cwPanel.ready&&cwPanel.frames>0')
                    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    # Freeze only synthetic live packets; use the real browser decoder and PCM handler.
                    await page.evaluate('''()=>{const original=socket.onmessage;socket.onmessage=event=>{if(typeof event.data==='string')original(event)};cwPanel.stop();updateCwPanel();}''')
                    await page.wait_for_function('()=>cwPanel.ready')
                    await page.locator('#cw-mute').click()
                    await expect(page.locator('#cw-mute')).to_have_text('Silenciado')
                    decoded=await page.evaluate('''async()=>{
                      const symbols={C:'-.-.',Q:'--.-',D:'-..',E:'.',W:'.--','1':'.----',A:'.-'};
                      const rate=12000,unit=rate*1.2/20,pcm=[];
                      const silence=count=>{for(let i=0;i<count;i++)pcm.push(0)};
                      silence(rate);
                      for(const [wi,word] of ['CQ','DE','W1AW'].entries()){
                        for(const [ci,char] of [...word].entries()){
                          if(ci)silence(unit*2);
                          for(const mark of symbols[char]){
                            for(let i=0;i<unit*(mark==='-'?3:1);i++)pcm.push(Math.round(12000*Math.sin(2*Math.PI*700*pcm.length/rate)));
                            silence(unit);
                          }
                        }
                        silence(unit*(wi<2?6:12));
                      }
                      let sequence=0;
                      for(let i=0;i<pcm.length;i+=1200){
                        while(!cwPanel.ready||cwPanel.pending)await new Promise(r=>setTimeout(r,5));
                        const count=Math.min(1200,pcm.length-i),packet=new Uint8Array(15+count*2),view=new DataView(packet.buffer);
                        view.setUint8(0,4);view.setUint32(1,sequence++,true);view.setBigUint64(5,BigInt(Date.now())*1000n,true);view.setUint16(13,count,true);
                        for(let j=0;j<count;j++)view.setInt16(15+j*2,pcm[i+j],true);
                        handleDigitalPacket(packet);
                      }
                      while(cwPanel.pending)await new Promise(r=>setTimeout(r,5));
                      return document.querySelector('#cw-terminal').textContent.trim();
                    }''')
                    assert decoded=='CQ DE W1AW',decoded
                    assert await page.locator('#cw-waterfall').get_attribute('data-minimum')=='450'
                    assert await page.locator('#cw-waterfall').get_attribute('data-maximum')=='950'
                    before=await page.evaluate('cwPanel.frames')
                    await page.locator('#cw-clear').click()
                    await expect(page.locator('#cw-terminal')).to_have_text('')
                    assert await page.evaluate('cwPanel.frames')==before
                    await page.locator('#listen').click()
                    assert await page.evaluate('cwPanel.worker===null')
                    await expect(page.locator('#cw-audio-start')).to_be_visible()
                    await page.locator('#cw-audio-start').click();await page.wait_for_function('()=>cwPanel.ready')
                    await page.locator('#audio-quality').select_option('raw')
                    await expect(page.locator('#cw-waterfall-message')).to_have_text('Perfil de audio incompatible, reinicie el modo digital')
                    assert await page.evaluate('cwPanel.worker===null')
                    await page.locator('[data-cw-mode]').click();await page.locator('[data-cw-mode]').click()
                    await page.wait_for_function('()=>cwPanel.ready')
                    await expect(page.locator('#cw-waterfall-message')).to_be_hidden()
                    await page.locator('[data-rtty-mode]').click()
                    await expect(page.locator('#cw-panel')).to_be_hidden()
                    await expect(page.locator('#rtty-panel')).to_be_visible()
                    assert await page.evaluate('cwPanel.worker===null')
                    await page.locator('[data-digital-mode="FT8"]').click()
                    await expect(page.locator('#digital-panel')).to_be_visible()
                    await expect(page.locator('#rtty-panel')).to_be_hidden()
                    await page.goto(url+'?digital=CW&mode=CW&tone=700&wpm=20&mute=1')
                    await expect(page.locator('#cw-panel')).to_be_visible()
                    await expect(page.locator('#cw-audio-start')).to_be_visible()
                    await expect(page.locator('#cw-mute')).to_have_text('Silenciado')
                    assert await page.evaluate('cwPanel.worker===null')
                    assert not errors,errors
                    await page.close()
                await browser.close()
        finally:await runner.cleanup()
    print('CW browser tests passed: desktop/mobile, WASM decode, pause/mute, mode switches and shared links')

if __name__=='__main__':asyncio.run(main())
