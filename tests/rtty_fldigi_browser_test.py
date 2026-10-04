# SPDX-License-Identifier: GPL-3.0-only
"""Isolated browser tests for the receive-only Fldigi adaptation."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from playwright.async_api import async_playwright,expect
from server import application,GATEWAY

async def main():
    with tempfile.TemporaryDirectory(prefix='hamsdr-rtty-fldigi-') as directory:
        args=SimpleNamespace(demo=True,source_host='127.0.0.1',source_port=1231,max_clients=5,origin='',digimodes=True,full_quality_sessions_per_ip=5,max_bandwidth_kbps_per_ip=1000000,site_config=Path(__file__).resolve().parents[1]/'site.example.toml',database=Path(directory)/'db.sqlite3')
        app=application(args);app[GATEWAY].site_config['digimodes']=True
        runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/'
        try:
            async with async_playwright() as p:
                browser=await getattr(p,os.environ.get('BROWSER','chromium')).launch()
                for viewport in [{'width':1440,'height':1000},{'width':390,'height':844}]:
                    page=await browser.new_page(viewport=viewport);errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.on('console',lambda message:print('Browser:',message.text) if message.type=='error' else None)
                    page.on('requestfailed',lambda request:print('Request failed:',request.url,request.failure))
                    page.on('response',lambda response:print('Bad response:',response.status,response.url) if response.status>=400 else None)
                    await page.goto(url+'?digital=RTTY-EXPERIMENTAL&mute=1')
                    await expect(page.locator('#rtty-title')).to_contain_text('RTTY Experimental')
                    await expect(page.locator('#mode-display')).to_have_text('LSB')
                    await expect(page.locator('#audio-quality')).to_have_value('digiraw')
                    await expect(page.locator('#rtty-multi')).to_be_disabled()
                    await expect(page.locator('#rtty-audio-start')).to_be_visible()
                    assert await page.evaluate('rttyExperimentalWorker===null')
                    await page.locator('#rtty-audio-start').click()
                    try:await page.wait_for_function('()=>rttyExperimentalReady',timeout=10000)
                    except Exception:
                        print('Decoder state:',await page.locator('#rtty-state').text_content(),await page.evaluate('({enabled:audioEnabled,worker:!!rttyExperimentalWorker,node:!!node,context:!!context,type:rttyDecoderType})'));raise
                    await page.wait_for_function('()=>rttySpectrumFrames>0')
                    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    await page.evaluate('''()=>{const original=socket.onmessage;socket.onmessage=event=>{if(typeof event.data==='string')original(event)};stopRttyExperimentalWorker();ensureRttyExperimentalWorker();}''')
                    await page.wait_for_function('()=>rttyExperimentalReady')
                    result=await page.evaluate(r'''async()=>{
                        const chars=['','E','\n','A',' ','S','I','U','\r','D','R','J','N','F','C','K','T','Z','L','W','H','Y','P','Q','O','B','G','','M','X','V',''];
                        const bits=Array(12).fill(1),text='CQ CQ DE TEST ';
                        for(const char of text){const code=chars.indexOf(char);bits.push(0);for(let i=0;i<5;i++)bits.push(code>>i&1);bits.push(1,1);}
                        bits.push(...Array(12).fill(1));let phase=0,sequence=0;
                        const count=Math.ceil(bits.length*12000/45.45),pcm=new Int16Array(count);
                        for(let i=0;i<count;i++){const bit=bits[Math.min(bits.length-1,Math.floor(i*45.45/12000))];phase+=2*Math.PI*(1000+(bit?-85:85))/12000;pcm[i]=Math.round(20000*Math.sin(phase));}
                        for(let offset=0;offset<pcm.length;offset+=1200){
                            while(!rttyExperimentalReady||rttyExperimentalPending)await new Promise(r=>setTimeout(r,5));
                            const block=pcm.slice(offset,offset+1200),packet=new Uint8Array(15+block.byteLength),view=new DataView(packet.buffer);
                            view.setUint8(0,3);view.setUint32(1,sequence++,true);view.setBigUint64(5,BigInt(Date.now())*1000n,true);view.setUint16(13,block.length,true);packet.set(new Uint8Array(block.buffer),15);handleDigitalPacket(packet);
                        }
                        while(rttyExperimentalPending)await new Promise(r=>setTimeout(r,5));return document.getElementById('rtty-terminal').textContent;
                    }''')
                    print('Browser Fldigi:',viewport,result)
                    assert result=='CQ CQ DE TEST ',repr(result)
                    await expect(page.locator('#rtty-mute')).to_have_text('Silenciado')
                    await page.evaluate('''()=>{stopRttyExperimentalWorker();ensureRttyExperimentalWorker();}''')
                    await page.wait_for_function('()=>rttyExperimentalReady');await page.locator('#rtty-clear').click()
                    noise_text=await page.evaluate('''async()=>{
                        let random=1234;
                        for(let sequence=0;sequence<40;sequence++){
                            while(!rttyExperimentalReady||rttyExperimentalPending)await new Promise(r=>setTimeout(r,5));
                            const pcm=new Int16Array(1200);
                            for(let i=0;i<pcm.length;i++){random=(1664525*random+1013904223)>>>0;pcm[i]=Math.round((random/4294967295*2-1)*12000);}
                            rttyExperimentalPending++;rttyExperimentalWorker.postMessage({type:'samples',payload:pcm.buffer,sequence},[pcm.buffer]);
                        }
                        while(rttyExperimentalPending)await new Promise(r=>setTimeout(r,5));return document.getElementById('rtty-terminal').textContent;
                    }''')
                    assert noise_text=='',repr(noise_text)
                    frames=await page.evaluate('rttySpectrumFrames');await page.locator('#rtty-clear').click()
                    assert await page.evaluate('rttySpectrumFrames')==frames
                    await expect(page.locator('#rtty-terminal')).to_have_text('')
                    await page.locator('#listen').click();assert await page.evaluate('rttyExperimentalWorker===null')
                    await expect(page.locator('#rtty-audio-start')).to_be_visible()
                    await page.locator('#rtty-audio-start').click();await page.wait_for_function('()=>rttyExperimentalReady')
                    await page.locator('[data-digital-menu]').select_option('RTTY')
                    await expect(page.locator('#rtty-multi')).to_be_enabled()
                    assert await page.evaluate('rttyExperimentalWorker===null')
                    await expect(page.locator('html')).to_have_attribute('data-rtty-engine','worklet')
                    await page.locator('#rtty-multi').check()
                    await page.locator('[data-digital-menu]').select_option('RTTY-EXPERIMENTAL')
                    await expect(page.locator('#rtty-multi')).not_to_be_checked()
                    await expect(page.locator('#rtty-multi')).to_be_disabled()
                    await page.wait_for_function('()=>rttyExperimentalReady')
                    await page.locator('[data-digital-menu]').select_option('RTTY')
                    await expect(page.locator('#rtty-multi')).to_be_checked()
                    await expect(page.locator('html')).to_have_attribute('data-rtty-engine','worklet')
                    await page.locator('[data-cw-mode]').click();await expect(page.locator('#rtty-panel')).to_be_hidden()
                    assert not errors,errors
                    await page.close()
                await browser.close()
        finally:await runner.cleanup()
    print('RTTY Experimental browser tests passed')

if __name__=='__main__':asyncio.run(main())
