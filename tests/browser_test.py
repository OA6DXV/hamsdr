# SPDX-License-Identifier: GPL-3.0-only
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
            target=url
            if viewport['width']==768:target=f"{url}?freq=7102.5&mode=USB&low=350&high=2450&zoom=4"
            await page.goto(target)
            site=await page.evaluate("window.hamSdrSiteConfig")
            assert site and await page.locator('#site-name').text_content()==site['receiver_name']
            assert await page.title()==f"{site['receiver_name']} · Preview"
            assert await page.locator('html').get_attribute('lang')=='es'
            assert await page.locator('#site-footer-operator').text_content()==site['callsign']
            assert await page.locator('#site-footer-operator-row').is_visible()==site['show_admin']
            assert await page.locator('#site-logo').is_visible()==site['logo']['enabled']
            assert await page.locator('#site-icon').get_attribute('href')==(site['logo']['url'] if site['logo']['enabled'] else None)
            assert await page.locator('#site-touch-icon').get_attribute('href')==(site['logo']['url'] if site['logo']['enabled'] else None)
            assert await page.locator('[name="view"],#allowkeys,#audio-format').count()==0
            await expect(page.locator('#site-footer-text')).to_have_text(f"HamSDR v{site['version']}")
            assert await page.locator('#site-operator-row').count()==0
            await expect(page.locator('label').filter(has=page.locator('#wfmode'))).to_contain_text('Gráfico:')
            await expect(page.locator('label').filter(has=page.locator('#waterfall-quality'))).to_contain_text('Cascada:')
            await expect(page.locator('label').filter(has=page.locator('#audio-quality'))).to_contain_text('Audio:')
            self_audio_options=await page.locator('#audio-quality option').evaluate_all('(options)=>options.map(option=>[option.value,option.textContent])')
            assert self_audio_options==[['raw','raw'],['balanced','balanceado'],['mobile','bajo consumo'],['digiraw','digiraw']]
            assert await page.locator('.stream-profile-control #audio-quality').count()==1
            assert await page.locator('.stream-profile-control #waterfall-quality').count()==1
            assert await page.locator('.waterfall-panel #waterfall-quality').count()==0
            if viewport['width']==768:
                await expect(page.locator('#frequency')).to_have_value('7102.50')
                await expect(page.locator('#mode-display')).to_have_text('USB')
                await expect(page.locator('#low')).to_have_value('350')
                await expect(page.locator('#high')).to_have_value('2450')
                await expect(page.locator('#zoom-label')).to_have_text('4×')
                assert 'freq=7102.5' in page.url and 'mode=USB' in page.url and 'zoom=4' in page.url
                await page.evaluate("Object.defineProperty(navigator,'clipboard',{value:{writeText:text=>{window.copiedLink=text;return Promise.resolve();}},configurable:true})")
                await page.locator('#copy-link').click()
                await expect(page.locator('#copy-link')).to_have_text('Copiado')
                assert 'freq=7102.5' in await page.evaluate('window.copiedLink')
                await page.goto(url)
            waterfall_options=await page.locator('#waterfall-quality option').evaluate_all('(options)=>options.map(option=>[option.value,option.textContent])')
            assert waterfall_options==[['auto','automático'],['slow','conexión lenta'],['low','baja definición'],['balanced','balanceado'],['high','alta definición']]
            assert await page.locator('#stream-warning').count()==0
            await expect(page.locator('.waterfall-title')).to_have_text('Cascada')
            assert await page.locator('.waterfall-title').evaluate("e=>getComputedStyle(e).textAlign==='center'")
            await expect(page.locator('#brightness')).to_be_visible()
            await expect(page.locator('#pause')).to_have_attribute('aria-pressed','false')
            await expect(page.locator('#pause')).to_have_css('background-color','rgb(255, 255, 255)')
            await page.locator('#pause').click()
            await expect(page.locator('#pause')).to_have_attribute('aria-pressed','true')
            await expect(page.locator('#pause')).to_have_css('background-color','rgb(196, 0, 0)')
            await page.locator('#pause').click()
            await expect(page.locator('#threshold')).to_be_visible()
            await expect(page.locator('.filter-limits')).to_be_visible()
            filter_limits=await page.locator('.filter-limits label').evaluate_all('(labels)=>labels.map(label=>{const r=label.getBoundingClientRect(),i=label.querySelector("input").getBoundingClientRect();return{y:r.y,w:r.width,iy:i.y,iw:i.width}})')
            assert len(filter_limits)==2 and abs(filter_limits[0]['y']-filter_limits[1]['y'])<=1 and abs(filter_limits[0]['iy']-filter_limits[1]['iy'])<=1 and abs(filter_limits[0]['w']-filter_limits[1]['w'])<=2
            await expect(page.locator('#threshold')).to_be_disabled()
            await expect(page.locator('#squelch-threshold')).to_have_attribute('data-active','false')
            await page.locator('#squelch').check()
            await expect(page.locator('#threshold')).to_be_enabled()
            await expect(page.locator('#squelch-threshold')).to_have_attribute('data-active','true')
            await page.locator('#threshold').fill('-65')
            await expect(page.locator('#threshold-value')).to_have_text('−65 dBFS')
            await page.locator('#squelch').uncheck()
            await expect(page.locator('#threshold')).to_be_disabled()
            await page.evaluate("window.reportStreamInterruption('test')")
            assert await page.locator('#wfspeed option').all_text_contents()==['alta','normal','lento','muy lento']
            assert await page.locator('#speed-high').evaluate('option=>option.disabled&&option.hidden')
            await expect(page.locator('#send-chat')).to_be_enabled()
            await expect(page.locator('#waterfall')).to_have_attribute('data-frames',re.compile(r'^[1-9][0-9]+$'))
            await expect(page.locator('#calibrate-smeter')).to_be_visible()
            meter_layout=await page.evaluate("""()=>{const button=document.querySelector('#calibrate-smeter').getBoundingClientRect(),power=document.querySelector('.signal-reading-line>span').getBoundingClientRect(),meter=document.querySelector('#meter');return{buttonX:button.x,buttonY:button.y,powerX:power.x,powerY:power.y,min:meter.min,max:meter.max}}""")
            assert meter_layout['buttonX']<meter_layout['powerX'] and abs(meter_layout['buttonY']-meter_layout['powerY'])<12
            assert meter_layout['min']==0 and meter_layout['max']==7
            if viewport['width']==768:
                await page.locator('#calibrate-smeter').click()
                await expect(page.locator('#calibrate-smeter')).to_contain_text('Calibrando')
                await expect(page.locator('#smeter-calibration-status')).to_contain_text('Calibrado',timeout=18000)
                assert await page.locator('.signal-panel').get_attribute('data-calibrated')=='true'
                assert await page.evaluate("Object.keys(localStorage).some(key=>key.startsWith('hamsdr-smeter:v1:'))")
                assert 'dBFS' in await page.locator('#power').text_content()
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
            if site['digimodes']:
                country_response=await page.request.get(url+'cty.dat')
                assert country_response.status==200
                assert country_response.headers.get('content-encoding')=='gzip'
                assert (await country_response.text()).startswith('Sov Mil Order of Malta:')
                modes=page.locator('#modes button')
                assert await modes.nth(10).text_content()=='FT8'
                assert await modes.nth(11).text_content()=='FT4'
                assert await modes.nth(12).text_content()=='RTTY'
                assert await modes.nth(13).text_content()=='Digimodos'
                await page.locator('[data-digital-mode="FT8"]').click()
                await expect(page.locator('#mode-display')).to_have_text('USB')
                await expect(page.locator('#audio-quality')).to_have_value('digiraw')
                await expect(page.locator('#audio-profile-status')).to_contain_text('PCM16 · 12 kHz')
                await expect(page.locator('#waterfall-quality')).to_have_value('slow')
                assert 'digital=FT8' in page.url
                await expect(page.locator('#digital-panel')).to_be_visible()
                await expect(page.locator('#digital-waterfall-message')).to_be_hidden()
                await expect(page.locator('#digital-audio-start')).to_be_hidden()
                await expect(page.locator('#digital-progress')).to_have_attribute('max','100')
                await expect(page.locator('#digital-download')).to_have_text('Descargar log')
                assert await page.locator('#digital-log thead').text_content()=='UTCSNRDTHzMensajePaíses'
                await expect(page.locator('#listen')).to_have_text('Pausar audio',timeout=10000)
                await expect(page.locator('#bandwidth')).to_have_text('3000')
                await expect(page.locator('#filter-unit')).to_have_text('Hz')
                await expect(page.locator('#low')).to_have_value('0')
                await expect(page.locator('#high')).to_have_value('3000')
                for control in ('#low','#high','#filter-narrow','#filter-wide'):
                    await expect(page.locator(control)).to_be_enabled()
                await page.locator('#filter-wide').click()
                await expect(page.locator('#high')).to_have_value('3100')
                await expect(page.locator('#bandwidth')).to_have_text('3100')
                await expect(page.locator('#digital-waterfall')).to_have_attribute('data-frequency-range','0:3100')
                await expect(page.locator('.digital-frequency-axis span').last).to_have_text('3100 Hz')
                await page.locator('#high').fill('5000')
                await page.locator('#high').press('Tab')
                await expect(page.locator('#bandwidth')).to_have_text('5000')
                await expect(page.locator('#digital-waterfall')).to_have_attribute('data-frequency-range','0:5000')
                await page.locator('#filter-wide').click()
                await expect(page.locator('#high')).to_have_value('5000')
                await page.locator('#high').fill('3000')
                await page.locator('#high').press('Tab')
                await page.locator('#digital-mute').click()
                await expect(page.locator('#digital-mute')).to_have_attribute('aria-pressed','true')
                await expect(page.locator('#digital-mute')).to_have_text('Silenciado')
                await expect(page.locator('#mute')).to_be_checked()
                assert 'mute=1' in page.url
                await page.locator('#mute').uncheck()
                await expect(page.locator('#digital-mute')).to_have_attribute('aria-pressed','false')
                await expect(page.locator('#digital-mute')).to_have_text('Silenciar')
                assert 'mute=1' not in page.url
                for control in ('#squelch','#notch','#nr'):
                    await expect(page.locator(control)).to_be_disabled()
                assert await page.locator('.digital-log-wrap').evaluate("element=>getComputedStyle(element).resize==='vertical'")
                digital_layout=await page.evaluate("""()=>{const box=id=>{const r=document.querySelector(id).getBoundingClientRect();return{w:r.width,h:r.height}};return{body:box('.digital-body'),canvas:box('#digital-waterfall'),log:box('.digital-log-wrap')}}""")
                assert abs(digital_layout['canvas']['w']-digital_layout['body']['w'])<=2
                assert abs(digital_layout['log']['w']-digital_layout['body']['w'])<=2
                assert digital_layout['canvas']['h']>=140
                if viewport['width']<=850:
                    mobile_log=await page.locator('.digital-log-wrap').evaluate("""element=>{const table=element.querySelector('table');const message=table.querySelector('th:nth-child(5)');return{client:element.clientWidth,scroll:element.scrollWidth,table:table.getBoundingClientRect().width,message:message.getBoundingClientRect().width,whiteSpace:getComputedStyle(message).whiteSpace}}""")
                    assert mobile_log['scroll']>mobile_log['client']
                    assert mobile_log['table']>=760
                    assert mobile_log['message']>=260
                    assert mobile_log['whiteSpace']=='nowrap'
                if viewport['width']<=650:
                    mobile_controls=await page.evaluate("""()=>{const rect=id=>{const r=document.querySelector(id).getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height}};return{autoclear:rect('#digital-autoclear').parentElement.getBoundingClientRect().toJSON(),mute:rect('#digital-mute'),download:rect('#digital-download'),clear:rect('#digital-clear'),progress:rect('#digital-progress')}}""")
                    assert mobile_controls['autoclear']['y']<mobile_controls['mute']['y']
                    assert max(mobile_controls[name]['y'] for name in ('mute','download','clear'))-min(mobile_controls[name]['y'] for name in ('mute','download','clear'))<=2
                    assert mobile_controls['progress']['y']>mobile_controls['mute']['y']
                await page.wait_for_timeout(300)
                await expect(page.locator('#digital-waterfall')).to_have_attribute('data-color-mode','adaptive')
                await expect(page.locator('#digital-waterfall')).to_have_attribute('data-noise-floor',re.compile(r'^-?[0-9]+\.[0-9]$'))
                await expect(page.locator('#digital-waterfall')).to_have_attribute('data-color-ceiling',re.compile(r'^-?[0-9]+\.[0-9]$'))
                assert await page.locator('#digital-log').get_by_text('Decoder MFSK/WASM no instalado',exact=False).count()==0
                await page.locator('[data-mode="AM"]:not([data-narrow])').click()
                await expect(page.locator('#mode-display')).to_have_text('AM')
                await page.locator('#audio-quality').select_option('balanced')
                await expect(page.locator('#digital-waterfall-message')).to_have_text('Perfil de audio incompatible, reinicie el modo digital')
                await expect(page.locator('#digital-waterfall-message')).to_be_visible()
                await page.locator('[data-digital-mode="FT8"]').click()
                await expect(page.locator('#digital-panel')).to_be_hidden()
                await expect(page.locator('#filter-narrow')).to_be_enabled()
                await page.locator('[data-digital-mode="FT8"]').click()
                await expect(page.locator('#digital-waterfall-message')).to_be_hidden()
                await expect(page.locator('#audio-quality')).to_have_value('digiraw')
                await expect(page.locator('#mode-display')).to_have_text('USB')
                await page.locator('[data-digital-mode="FT8"]').click()
                await expect(page.locator('#digital-panel')).to_be_hidden()
                await page.locator('#listen').click()
                await expect(page.locator('#listen')).to_have_text('Iniciar audio')
                if viewport['width']==768:
                    await page.goto(url+'?digital=FT8&mute=1')
                    await expect(page.locator('#digital-panel')).to_be_visible()
                    await expect(page.locator('#mode-display')).to_have_text('USB')
                    await expect(page.locator('#mute')).to_be_checked()
                    await expect(page.locator('#listen')).to_have_text('Iniciar audio')
                    await expect(page.locator('#digital-audio-start')).to_be_visible()
                    assert 'digital=FT8' in page.url and 'mute=1' in page.url
                    await page.locator('#digital-audio-start').click()
                    await expect(page.locator('#listen')).to_have_text('Pausar audio')
                    await expect(page.locator('#digital-audio-start')).to_be_hidden()
                    await page.locator('#listen').click()
                    await expect(page.locator('#listen')).to_have_text('Iniciar audio')
                    await page.locator('#mute').uncheck()
                    await page.locator('[data-digital-mode="FT8"]').click()
                    await expect(page.locator('#digital-panel')).to_be_hidden()
                    await page.goto(url+'?digital=RTTY')
                    await expect(page.locator('#rtty-panel')).to_be_visible()
                    await expect(page.locator('#listen')).to_have_text('Iniciar audio')
                    await expect(page.locator('#rtty-audio-start')).to_be_visible()
                    starter_position=await page.locator('#rtty-audio-start').evaluate("button=>{const buttonRect=button.getBoundingClientRect(),canvasRect=document.querySelector('#rtty-waterfall').getBoundingClientRect();return{buttonX:buttonRect.x+buttonRect.width/2,buttonY:buttonRect.y+buttonRect.height/2,canvasX:canvasRect.x+canvasRect.width/2,canvasY:canvasRect.y+canvasRect.height/2}}")
                    assert abs(starter_position['buttonX']-starter_position['canvasX'])<=2
                    assert abs(starter_position['buttonY']-starter_position['canvasY'])<=2
                    assert await page.locator('#rtty-clear').evaluate("button=>button.parentElement.classList.contains('rtty-terminal-header')")
                    await page.locator('#rtty-audio-start').click()
                    await expect(page.locator('#listen')).to_have_text('Pausar audio')
                    await expect(page.locator('#rtty-audio-start')).to_be_hidden()
                    await page.locator('#listen').click()
                    await page.locator('[data-rtty-mode]').click()
                    await expect(page.locator('#rtty-panel')).to_be_hidden()
                await page.locator('[data-rtty-mode]').click()
                await expect(page.locator('#rtty-panel')).to_be_visible()
                await expect(page.locator('#audio-quality')).to_have_value('digiraw')
                await expect(page.locator('#audio-profile-status')).to_contain_text('PCM16 · 12 kHz')
                await expect(page.locator('#mode-display')).to_have_text('LSB')
                await expect(page.locator('#low')).to_have_value('-3000')
                await expect(page.locator('#high')).to_have_value('0')
                await expect(page.locator('#rtty-reverse')).not_to_be_checked()
                await expect(page.locator('#rtty-profile-alert')).to_be_hidden()
                await expect(page.locator('#listen')).to_have_text('Pausar audio',timeout=10000)
                await expect(page.locator('html')).to_have_attribute('data-rtty-engine','worklet')
                await expect(page.locator('#rtty-title')).to_contain_text('RTTY · LSB')
                await expect(page.locator('#rtty-mark')).to_contain_text('Hz')
                await expect(page.locator('#rtty-waterfall')).to_have_attribute('data-frequency-range','0:3000')
                await expect(page.locator('#rtty-waterfall')).to_have_attribute('data-frames',re.compile(r'^[1-9][0-9]*$'),timeout=10000)
                await page.locator('[data-mode="LSB"]:not([data-narrow])').click()
                await expect(page.locator('#low')).to_have_value('-3000');await expect(page.locator('#high')).to_have_value('0')
                await expect(page.locator('#rtty-reverse')).not_to_be_checked()
                await expect(page.locator('#rtty-waterfall')).to_have_attribute('data-frequency-range','0:3000')
                await page.locator('[data-mode="AM"]:not([data-narrow])').click()
                await expect(page.locator('#low')).to_have_value('-4000');await expect(page.locator('#high')).to_have_value('4000')
                await expect(page.locator('#rtty-waterfall')).to_have_attribute('data-frequency-range','0:4000')
                await page.locator('[data-mode="NFM"]:not([data-narrow])').click()
                await expect(page.locator('#low')).to_have_value('-5000');await expect(page.locator('#high')).to_have_value('5000')
                await expect(page.locator('#rtty-waterfall')).to_have_attribute('data-frequency-range','0:5000')
                await page.locator('[data-mode="CW"]:not([data-narrow])').click()
                await expect(page.locator('#low')).to_have_value('450');await expect(page.locator('#high')).to_have_value('950')
                await expect(page.locator('#rtty-waterfall')).to_have_attribute('data-frequency-range','0:950')
                await page.locator('[data-mode="USB"]:not([data-narrow])').click()
                await expect(page.locator('#low')).to_have_value('0');await expect(page.locator('#high')).to_have_value('3000')
                await expect(page.locator('#rtty-reverse')).to_be_checked()
                await page.locator('#rtty-reverse').uncheck()
                await expect(page.locator('#rtty-reverse')).not_to_be_checked()
                assert 'reverse=0' in page.url
                await page.locator('#rtty-multi').check()
                await expect(page.locator('#rtty-multi-panel')).to_be_visible()
                await expect(page.locator('#rtty-waterfall')).to_have_attribute('data-multi','true')
                await expect(page.locator('#rtty-multi-count')).to_have_text('0')
                await expect(page.locator('#rtty-multi-table thead')).to_contain_text('Perfil')
                await expect(page.locator('.rtty-multi-empty')).to_contain_text('45.45/170, 50/170 y 75/170')
                assert 'multi=1' in page.url
                await page.locator('#filter-wide').click()
                await expect(page.locator('#high')).to_have_value('3100')
                await expect(page.locator('#rtty-waterfall')).to_have_attribute('data-frequency-range','0:3100')
                await expect(page.locator('.rtty-frequency-axis span').last).to_have_text('3100 Hz')
                await page.locator('#rtty-center').fill('1200')
                await page.locator('#rtty-center').press('Tab')
                await page.locator('#rtty-reverse').check()
                assert 'digital=RTTY' in page.url and 'center=1200' in page.url and 'reverse=1' in page.url
                await page.locator('#rtty-mute').click()
                await expect(page.locator('#rtty-mute')).to_have_text('Silenciado')
                await expect(page.locator('#mute')).to_be_checked()
                await page.locator('#rtty-mute').click()
                await expect(page.locator('#rtty-mute')).to_have_text('Silenciar')
                await page.locator('#rtty-multi').uncheck()
                await expect(page.locator('#rtty-multi-panel')).to_be_hidden()
                await page.locator('[data-rtty-mode]').click()
                await expect(page.locator('#rtty-panel')).to_be_hidden()
                await expect(page.locator('#audio-quality')).to_have_value('balanced')
                await page.locator('#listen').click()
                await expect(page.locator('#listen')).to_have_text('Iniciar audio')
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
            assert await page.locator('#speed-high').evaluate('option=>!option.disabled&&!option.hidden')
            await page.locator('#wfspeed').select_option('high')
            await expect(page.locator('#waterfall')).to_have_attribute('data-speed','high')
            await expect(page.locator('#waterfall-profile-status')).to_contain_text('11.7 fps')
            await page.locator('#waterfall-quality').select_option('balanced')
            await expect(page.locator('#wfspeed')).to_have_value('1')
            assert await page.locator('#speed-high').evaluate('option=>option.disabled&&option.hidden')
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
            await expect(page.locator('#waterfall')).to_have_attribute('data-history-span','1024000')
            history_after=int(await page.locator('#waterfall').get_attribute('data-history'))
            assert history_before>0 and history_after>0,'zoom failed to restore waterfall history'
            if viewport['width']==390:
                before_pan=await page.locator('#scale').evaluate("e=>({frequency:Number(e.dataset.frequency),center:Number(e.dataset.viewCenter)})")
                await page.locator('#waterfall').dispatch_event('pointerdown',{'pointerId':10,'pointerType':'touch','clientX':160,'clientY':100})
                await page.locator('#waterfall').dispatch_event('pointermove',{'pointerId':10,'pointerType':'touch','clientX':220,'clientY':102})
                await expect(page.locator('#waterfall')).to_have_attribute('data-navigation-active','true')
                assert int(await page.locator('#waterfall').get_attribute('data-navigation-rows'))>0
                await page.locator('#waterfall').dispatch_event('pointerup',{'pointerId':10,'pointerType':'touch','clientX':220,'clientY':102})
                await expect(page.locator('#waterfall')).not_to_have_attribute('data-navigation-active','true')
                after_pan=await page.locator('#scale').evaluate("e=>({frequency:Number(e.dataset.frequency),center:Number(e.dataset.viewCenter)})")
                assert after_pan['frequency']<before_pan['frequency'],'right swipe did not move to a lower frequency'
                assert abs((after_pan['frequency']-before_pan['frequency'])-(after_pan['center']-before_pan['center']))<=2,'tuning marker moved relative to the waterfall view'
                await expect(page.locator('#waterfall')).to_have_css('transform','none')
                await page.locator('#frequency').fill('7100.00')
                await page.locator('#frequency').press('Tab')
                await expect(page.locator('#frequency')).to_have_attribute('data-confirmed','7100000')
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
                third=await browser.new_page()
                await third.goto(url)
                await expect(page.locator('#resource-policy')).to_be_visible()
                await expect(page.locator('#resource-policy')).to_contain_text('más de dos sesiones')
                assert await page.locator('#waterfall-quality option[value="high"]').evaluate('option=>option.disabled')
                assert await page.locator('#audio-quality option[value="raw"]').evaluate('option=>option.disabled')
                await third.close()
                await expect(page.locator('#resource-policy')).to_be_hidden()
                assert not await page.locator('#waterfall-quality option[value="high"]').evaluate('option=>option.disabled')
                assert not await page.locator('#audio-quality option[value="raw"]').evaluate('option=>option.disabled')
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
