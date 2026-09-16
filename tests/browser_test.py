"""Visual/control/audio smoke checks; community writes are restricted to local demo."""
import asyncio
from pathlib import Path
import sys
import os
import re
import uuid
import wave
import struct
from playwright.async_api import async_playwright, expect

async def main():
    url=sys.argv[1] if len(sys.argv)>1 else "http://127.0.0.1:18094/"
    local=url.startswith('http://127.0.0.1:')
    Path("test-results").mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser=await getattr(p,os.environ.get('BROWSER','chromium')).launch()
        for viewport in [{"width":2560,"height":1440},{"width":1440,"height":1000},{"width":768,"height":1024},{"width":390,"height":844}]:
            page=await browser.new_page(viewport=viewport)
            errors=[]
            page.on("pageerror",lambda error:errors.append(str(error)))
            if viewport['width']>=1440:
                async def delayed_community(route):
                    await asyncio.sleep(.75)
                    await route.continue_()
                await page.route('**/community.js',delayed_community)
            await page.goto(url)
            site=await page.evaluate("window.hamSdrSiteConfig")
            assert site and await page.locator('#site-name').text_content()==site['receiver_name']
            assert await page.title()==f"{site['receiver_name']} · Preview"
            assert await page.locator('html').get_attribute('lang')=='es'
            assert await page.locator('#site-footer-operator').text_content()==site['callsign']
            assert await page.locator('#site-footer-operator-row').is_visible()==site['show_admin']
            assert await page.locator('#site-logo').is_visible()==site['logo']['enabled']
            assert await page.locator('[name="view"],#allowkeys,#audio-format').count()==0
            await expect(page.locator('#site-footer-text')).to_have_text(f"HamSDR v{site['version']}")
            assert await page.locator('#site-operator-row').count()==0
            await expect(page.locator('label').filter(has=page.locator('#wfmode'))).to_contain_text('Gráfico:')
            await expect(page.locator('label').filter(has=page.locator('#waterfall-quality'))).to_contain_text('Cascada:')
            await expect(page.locator('label').filter(has=page.locator('#audio-quality'))).to_contain_text('Audio:')
            self_audio_options=await page.locator('#audio-quality option').evaluate_all('(options)=>options.map(option=>[option.value,option.textContent])')
            assert self_audio_options==[['raw','raw'],['balanced','balanceado'],['mobile','bajo consumo']]
            assert await page.locator('.stream-profile-control #audio-quality').count()==1
            assert await page.locator('.stream-profile-control #waterfall-quality').count()==1
            assert await page.locator('.waterfall-panel #waterfall-quality').count()==0
            waterfall_options=await page.locator('#waterfall-quality option').evaluate_all('(options)=>options.map(option=>[option.value,option.textContent])')
            assert waterfall_options==[['auto','automático'],['slow','conexión lenta'],['low','baja definición'],['balanced','balanceado'],['high','alta definición']]
            await expect(page.locator('#stream-warning')).to_be_hidden()
            await page.evaluate("window.reportStreamInterruption('test')")
            await expect(page.locator('#stream-warning')).to_be_hidden()
            await expect(page.locator('#stream-warning')).to_have_attribute('data-samples','1')
            await page.evaluate("window.reportStreamInterruption('test')")
            await expect(page.locator('#stream-warning')).to_be_hidden()
            await page.evaluate("window.reportStreamInterruption('test')")
            await expect(page.locator('#stream-warning')).to_be_visible()
            await expect(page.locator('#stream-warning')).to_contain_text('Cascada: conexión lenta')
            assert await page.locator('#stream-warning').evaluate("e=>getComputedStyle(e).color==='rgb(196, 0, 0)'")
            await page.locator('#stream-warning').evaluate("e=>e.hidden=true")
            assert await page.locator('#wfspeed option').all_text_contents()==['normal','lento','muy lento']
            await expect(page.locator('#send-chat')).to_be_enabled()
            await expect(page.locator('#waterfall')).to_have_attribute('data-frames',re.compile(r'^[1-9][0-9]+$'))
            await expect(page.locator('#waterfall')).to_have_attribute('data-speed','1')
            await page.locator('#wfspeed').select_option('2')
            await expect(page.locator('#waterfall')).to_have_attribute('data-speed','2')
            await page.locator('#wfspeed').select_option('6')
            await expect(page.locator('#waterfall')).to_have_attribute('data-speed','6')
            await page.locator('#wfspeed').select_option('1')
            await expect(page.locator('#waterfall')).to_have_attribute('data-speed','1')
            assert await page.locator('#listen').evaluate("e=>e.classList.contains('audio-initial')")
            assert await page.locator('#username').evaluate("e=>{const profiles=e.closest('.identity-control').nextElementSibling;return profiles.classList.contains('stream-profile-control')&&profiles.nextElementSibling.id==='panorama'}")
            await expect(page.locator('#client-traffic')).to_have_text(re.compile(r'^(?:—|[0-9]+(?:\.[0-9]+)?) kb/s$'))
            await expect(page.locator('#client-traffic')).not_to_have_text('— kb/s',timeout=2000)
            expected_profile='balanced'
            if viewport['width']>=1440:
                await expect(page.locator('#audio-quality')).to_have_value('balanced')
            await expect(page.locator('#waterfall-quality')).to_have_value(expected_profile)
            await expect(page.locator('#waterfall')).to_have_attribute('data-profile',expected_profile)
            assert await page.locator('footer a',has_text='Estado del receptor').count()==0
            assert await page.locator('#nr').evaluate("e=>e.closest('.signal-panel')!==null")
            layout=await page.evaluate("""()=>{const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,right:r.right}};return{panorama:rect('#panorama'),controls:rect('.controls'),frequency:rect('.frequency-panel'),waterfall:rect('.waterfall-panel'),signal:rect('.signal-panel'),filter:rect('.filter-panel'),chat:rect('[aria-label=\"Chat en vivo\"]'),log:rect('[aria-label=\"Logbook\"]'),main:rect('main')}}""")
            assert abs(layout['panorama']['x']-layout['controls']['x'])<=1 and abs(layout['panorama']['right']-layout['controls']['right'])<=1,'receiver sections do not share margins'
            if viewport['width']>=1440:
                assert max(layout[name]['y'] for name in ('frequency','waterfall','signal','filter'))-min(layout[name]['y'] for name in ('frequency','waterfall','signal','filter'))<=1,'desktop control panels are not in one row'
                assert abs(layout['chat']['y']-layout['log']['y'])<=1 and 2.8<=layout['chat']['w']/layout['log']['w']<=3.2,'community columns are not 3:1'
                assert abs(layout['main']['x']-(viewport['width']-layout['main']['w'])/2)<=1,'page is not centered'
            for profile in ('slow','low','balanced','high'):
                await page.locator('#waterfall-quality').select_option(profile)
                await expect(page.locator('#waterfall')).to_have_attribute('data-profile',profile)
            await page.locator('#waterfall-quality').select_option('auto')
            await expect(page.locator('#waterfall')).to_have_attribute('data-profile',expected_profile)
            await page.locator('[data-mode="USB"]:not([data-narrow])').click()
            await page.locator('#listen').click()
            await expect(page.locator('#listen')).to_have_text('Pausar audio')
            await expect(page.locator('#listen')).to_have_attribute('aria-pressed','true')
            assert await page.locator('#listen').evaluate("e=>e.classList.contains('audio-playing')")
            secure_context=await page.evaluate('window.isSecureContext')
            assert await page.locator('html').get_attribute('data-audio-engine')==('worklet' if secure_context else 'classic')
            await expect(page.locator('#audio-status')).to_have_attribute('data-rms',re.compile(r'^0\.(?:[1-9]|0[1-9]|00[1-9])'),timeout=15000)
            await page.locator('#audio-quality').select_option('balanced')
            await expect(page.locator('#audio-profile-status')).to_contain_text('Opus · 32 kb/s')
            await page.locator('#audio-quality').select_option('mobile')
            await expect(page.locator('#audio-profile-status')).to_contain_text('Opus · 12 kb/s')
            await expect(page.locator('#audio-status')).to_have_attribute('data-rms',re.compile(r'^0\.(?:[1-9]|0[1-9]|00[1-9])'),timeout=15000)
            await page.locator('#audio-status').evaluate("e=>e.removeAttribute('data-rms')")
            await page.locator('#audio-quality').select_option('raw')
            await expect(page.locator('#audio-profile-status')).to_contain_text('PCM16 · 16 kHz')
            await expect(page.locator('#audio-status')).to_have_attribute('data-rms',re.compile(r'^0\.(?:[1-9]|0[1-9]|00[1-9])'),timeout=15000)
            await page.locator('#audio-status').evaluate("e=>e.removeAttribute('data-rms')")
            await page.locator('#listen').click()
            await expect(page.locator('#listen')).to_have_text('Iniciar audio')
            await expect(page.locator('#listen')).to_have_attribute('aria-pressed','false')
            assert await page.locator('#listen').evaluate("e=>e.classList.contains('audio-ready')")
            packets=int(await page.locator('#audio-status').get_attribute('data-packets'))
            await page.wait_for_timeout(700)
            self_packets=int(await page.locator('#audio-status').get_attribute('data-packets'))
            assert self_packets==packets,'PCM continued after pausing audio'
            await page.locator('#listen').click()
            await expect(page.locator('#listen')).to_have_text('Pausar audio')
            history_before=int(await page.locator('#waterfall').get_attribute('data-history'))
            zoom_block_ms=await page.locator('#zoom-in').evaluate("e=>{const start=performance.now();e.click();return performance.now()-start}")
            assert zoom_block_ms<100,f'zoom blocked the browser main thread for {zoom_block_ms:.1f} ms'
            await expect(page.locator('#zoom-label')).to_have_text('2×')
            await expect(page.locator('#waterfall')).to_have_attribute('data-span','512000')
            history_after=int(await page.locator('#waterfall').get_attribute('data-history'))
            assert history_after>=history_before,'zoom discarded waterfall history'
            if viewport['width']==390:
                await page.locator('#waterfall').dispatch_event('pointerdown',{'pointerId':11,'pointerType':'touch','clientX':120,'clientY':100})
                await page.locator('#waterfall').dispatch_event('pointerdown',{'pointerId':12,'pointerType':'touch','clientX':220,'clientY':100})
                await page.locator('#waterfall').dispatch_event('pointermove',{'pointerId':12,'pointerType':'touch','clientX':300,'clientY':100})
                await page.locator('#waterfall').dispatch_event('pointerup',{'pointerId':11,'pointerType':'touch','clientX':120,'clientY':100})
                await page.locator('#waterfall').dispatch_event('pointerup',{'pointerId':12,'pointerType':'touch','clientX':300,'clientY':100})
                assert float((await page.locator('#zoom-label').text_content()).rstrip('×'))>2,'pinch did not zoom'
            await page.locator('[data-step="10"]').click()
            await expect(page.locator('#frequency')).to_have_attribute('data-confirmed','7100010')
            await page.locator('[data-fix]').first.click()
            await expect(page.locator('#frequency')).to_have_attribute('data-confirmed','7100000')
            await page.locator('[data-mode="USB"][data-narrow]').click()
            await expect(page.locator('#bandwidth')).to_have_text('1.70')
            packets_before_controls=int(await page.locator('#audio-status').get_attribute('data-packets'))
            filter_block_ms=await page.locator('#filter-wide').evaluate("e=>{const start=performance.now();e.click();return performance.now()-start}")
            assert filter_block_ms<50,f'filter adjustment blocked the browser main thread for {filter_block_ms:.1f} ms'
            await expect(page.locator('#bandwidth')).to_have_text('1.80')
            await page.wait_for_timeout(600)
            packets_after_controls=int(await page.locator('#audio-status').get_attribute('data-packets'))
            assert packets_after_controls>packets_before_controls,'audio stopped after zoom/filter adjustment'
            await expect(page.locator('#stream-warning')).to_be_hidden()
            await page.locator('[data-mode="USB"]:not([data-narrow])').click()
            await page.locator('[data-step="1000"]').click()
            await expect(page.locator('#frequency')).to_have_attribute('data-confirmed','7101000')
            await page.locator('#memory-name').fill('Prueba de memoria')
            await page.locator('#save').click()
            await page.locator('[data-step="1000"]').click()
            await page.locator('#memories').select_option('0')
            await expect(page.locator('#frequency')).to_have_attribute('data-confirmed','7101000')
            await page.locator('#mute').check()
            await expect(page.locator('#mute')).to_have_attribute('aria-pressed','true')
            await page.locator('#mute').uncheck()
            await page.locator('#frequency').fill('7100')
            await page.locator('#frequency').press('Tab')
            await page.locator('#wfsize').select_option('200')
            await expect(page.locator('#waterfall')).to_have_attribute('height','200')
            await page.locator('#wfmode').select_option('spectrum-fixed')
            await page.wait_for_timeout(300)
            await expect(page.locator('#spectrum-axis')).to_be_visible()
            await expect(page.locator('#spectrum-axis')).to_have_attribute('aria-label',re.compile(r'-74 a -40 dBFS'))
            assert await page.locator('#spectrum-axis span').count()>=6
            assert all('dBFS' not in text for text in await page.locator('#spectrum-axis span').all_text_contents())
            alignment=await page.locator('#spectrum-axis span').first.evaluate("e=>{const line=e.getBoundingClientRect(),label=e.firstElementChild.getBoundingClientRect();return Math.abs(line.top+(line.height/2)-(label.top+label.height/2))}")
            assert alignment<=1,'spectrum value is not centered on its level line'
            purple=await page.locator('#waterfall').evaluate("c=>{const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=0;i<d.length;i+=4)if(d[i]>30&&d[i+2]>d[i]*1.1&&d[i+1]<d[i])n++;return n}")
            assert purple>100,'purple spectrum fill missing'
            await page.locator('#brightness').evaluate("e=>{e.value='10';e.dispatchEvent(new Event('input',{bubbles:true}))}")
            await expect(page.locator('#spectrum-axis')).to_have_attribute('aria-label',re.compile(r'-84 a -50 dBFS'))
            await page.locator('#brightness').evaluate("e=>{e.value='0';e.dispatchEvent(new Event('input',{bubbles:true}))}")
            await page.locator('#wfmode').select_option('spectrum-dynamic')
            await page.wait_for_timeout(500)
            await expect(page.locator('#spectrum-axis')).to_be_visible()
            await expect(page.locator('#waterfall')).to_have_attribute('data-scale-mode','dynamic')
            dynamic_bottom=float(await page.locator('#waterfall').get_attribute('data-scale-bottom'))
            dynamic_top=float(await page.locator('#waterfall').get_attribute('data-scale-top'))
            assert -120<=dynamic_bottom<dynamic_top<=0,'invalid dynamic spectrum range'
            assert dynamic_top-dynamic_bottom>=33.9,'dynamic spectrum range became too narrow'
            assert all('dBFS' not in text for text in await page.locator('#spectrum-axis span').all_text_contents())
            await page.locator('#wfmode').select_option('waterfall')
            await expect(page.locator('#spectrum-axis')).to_be_hidden()
            await page.locator('#nr').select_option('2')
            await page.locator('#notch').check()
            await page.locator('#notch').uncheck()
            await page.locator('#nr').select_option('0')
            recording_profile=await page.locator('#audio-quality').input_value()
            recording_rate=16000 if recording_profile=='raw' else 48000
            await page.locator('#record').click()
            await page.wait_for_timeout(2000)
            await page.locator('#record').click()
            await expect(page.locator('#download')).to_be_visible()
            async with page.expect_download() as dl:
                await page.locator('#download').click()
            download=await dl.value
            assert await download.failure() is None
            recording=Path('test-results')/download.suggested_filename
            await download.save_as(recording)
            assert recording.stat().st_size>500
            with wave.open(str(recording)) as wav:
                assert wav.getframerate()==recording_rate and wav.getnchannels()==1 and wav.getsampwidth()==2
                samples=wav.readframes(wav.getnframes())
                assert max(abs(x[0]) for x in struct.iter_unpack('<h',samples))>100
            await expect(page.locator('#audio-status')).to_have_attribute('data-rms',re.compile(r'^0\.(?:[1-9]|0[1-9]|00[1-9])'),timeout=15000)
            if local and viewport['width']==1440:
                # Two independent browsers demonstrate live identity and safe chat text.
                other=await browser.new_page()
                await other.goto(url)
                await page.locator('#username').fill('TESTER')
                await page.locator('#username').press('Tab')
                await expect(other.locator('#users')).to_contain_text('TESTER')
                label=other.locator('#users .user').filter(has_text='TESTER')
                await page.locator('[data-step="1000"]').click()
                await expect(label).to_have_attribute('data-frequency','7101000')
                test_text='<img src=x onerror=alert(1)> '+uuid.uuid4().hex
                await page.locator('#chat-text').fill(test_text)
                await page.locator('#send-chat').click()
                await expect(other.locator('#chatbox')).to_contain_text(test_text)
                assert await other.locator('#chatbox img').count()==0
                await other.reload()
                await expect(other.locator('#chatbox')).to_contain_text(test_text)
                await expect(page.locator('#chat-text')).to_have_value('')
                await page.reload()
                await expect(page.locator('#username')).to_have_value('TESTER')
                await expect(page.locator('#chatbox')).to_contain_text(test_text)
                await other.close()
            await page.wait_for_timeout(2000)
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth'),'horizontal overflow'
            pixels=await page.evaluate("(()=>{const c=document.getElementById('waterfall');return Array.from(c.getContext('2d').getImageData(0,0,c.width,c.height).data).filter((x,i)=>i%4!==3&&x>0).length})()")
            assert pixels>100,'waterfall is blank'
            if viewport['width']==1440:
                boxes=await page.locator('.frequency-panel,.waterfall-panel,.signal-panel').evaluate_all('(es)=>es.map(e=>{const r=e.getBoundingClientRect();return [r.x,r.y]})')
                assert max(b[1] for b in boxes)-min(b[1] for b in boxes)<2,boxes
                assert boxes[0][0]<boxes[1][0]<boxes[2][0],boxes
                widths=await page.locator('#panorama,.controls').evaluate_all('(es)=>es.map(e=>e.getBoundingClientRect().width)')
                assert min(widths)>1300,widths
            await page.screenshot(path=f"test-results/{os.environ.get('BROWSER','chromium')}-{viewport['width']}.png",full_page=True)
            assert not errors,errors
            print({'viewport':viewport,'colored_pixels':pixels,'errors':errors})
            await page.close()
        await browser.close()

asyncio.run(main())
