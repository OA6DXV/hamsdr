# SPDX-License-Identifier: GPL-3.0-only
"""Language switching with real demo audio; no live SDR or external service."""
import asyncio
import gzip
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

from aiohttp import web
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import application, GATEWAY

ROOT = Path(__file__).resolve().parents[1]


async def check(browser, url, gateway, engine):
    gateway.connection_attempts.clear()
    context = await browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = await context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    await page.goto(url)
    await page.wait_for_function('()=>protocolReady')
    assert await page.locator('html').get_attribute('lang') == 'es'
    assert await page.locator('#listen').inner_text() == 'Iniciar audio'
    assert await page.locator('#listeners-label').inner_text() == 'usuario conectado.'
    await page.evaluate(r"""() => {
        const seen=new Set(),variables=text=>[...text.matchAll(/\{([a-z]+)\}/g)].map(m=>m[1]).sort().join(',');
        for(const [source,en,pt,es=source] of HamTranslations){
            if(seen.has(source))throw new Error('Duplicate translation: '+source);seen.add(source);
            for(const value of [en,pt,es])if(!value||variables(value)!==variables(source))throw new Error('Invalid translation: '+source);
        }
        window.originalReceiver=socket;window.originalIdentity=radioIdentity.id;
        setDigitalCountryList(['Spain','Brazil','European Russia','United States']);
        addDigitalRow({utc:'00:00:00',snr:-10,dt:.2,hz:1000,text:'CQ TEST N0CALL',countries:'Spain → Brazil',batchId:'one'});
        window.dispatchEvent(new CustomEvent('radio-event',{detail:{type:'event',event:{kind:'chat',id:999999,time:1700000000,name:'N0CALL',text:'Iniciar audio'}}}));
        window.originalTitle=document.getElementById('site-name').textContent;
    }""")
    await page.locator('#username').fill('test1')
    await page.wait_for_function('()=>!document.getElementById("send-chat").disabled')
    await page.locator('#audio-quality').select_option('raw')
    await page.locator('#listen').click()
    await page.wait_for_function('()=>audioPackets>5')
    await page.locator('#record').click()
    await page.wait_for_function('()=>recording')
    await page.evaluate('window.originalAudioNode=node;window.originalRecorderWorker=recorder.worker')
    expected={
        'en':('Pause audio','Waterfall','Mute','Countries','Spain → Brazil','English'),
        'pt-BR':('Pausar áudio','Cascata','Silenciar','Países','Espanha → Brasil','Português (Brasil)'),
        'es':('Pausar audio','Cascada','Silenciar','Países','España → Brasil','Español'),
    }
    for language,(audio,waterfall,mute,countries,country_pair,option) in expected.items():
        await page.locator('#language').select_option(language)
        await page.wait_for_function('(lang)=>document.documentElement.lang===lang', arg=language)
        await page.evaluate(r"""lang => {
            const index={es:3,en:1,'pt-BR':2}[lang];
            for(const row of HamTranslations){
                const expand=value=>value.replace(/\{[a-z]+\}/g,'17');
                const source=expand(row[0]),expected=expand(row[index]??row[0]);
                if(HamI18n.t(source)!==expected)throw new Error('Translation mismatch: '+source+' / '+HamI18n.t(source)+' / '+expected);
            }
        }""",language)
        assert await page.locator('#listen').inner_text() == audio
        assert await page.locator('.waterfall-title').inner_text() == waterfall
        assert await page.locator('#digital-mute').inner_text() == mute
        assert await page.locator('[data-sort="countries"]').inner_text() == countries
        assert await page.locator('#digital-log tbody td').nth(4).inner_text() == 'CQ TEST N0CALL'
        assert await page.locator('#digital-log tbody td').nth(5).inner_text() == country_pair
        assert 'Iniciar audio' in await page.locator('#chatbox').inner_text(), 'User content must never be translated'
        assert await page.evaluate('document.getElementById("site-name").textContent===originalTitle')
        assert await page.evaluate('socket===originalReceiver && node===originalAudioNode && recorder.worker===originalRecorderWorker && recording && radioIdentity.id===originalIdentity')
        assert await page.locator('#language option:checked').inner_text() == option
        # Repeated identical source writes must remain translated, including
        # interpolated worker/server messages and normalized Spanish labels.
        await page.evaluate("document.getElementById('digital-state').textContent='(27) Mensajes'")
        expected_count={'en':'(27) Messages','pt-BR':'(27) Mensagens','es':'(27) Mensajes'}[language]
        await page.wait_for_function('(value)=>document.getElementById("digital-state").textContent===value',arg=expected_count)
        await page.locator('#digital-country').select_option('Spain',force=True)
        assert await page.locator('.digital-country-match').count() == 1
    await page.evaluate('window.audioBeforeLanguage=audioPackets')
    await page.wait_for_function('()=>audioPackets>audioBeforeLanguage+5')
    await page.locator('#record').click()
    await page.wait_for_function('()=>!recording && !recordingFinishing')
    await page.locator('#language').select_option('pt-BR')
    await page.reload()
    await page.wait_for_function('()=>protocolReady')
    assert await page.locator('#listen').inner_text() == 'Iniciar áudio'
    assert await page.locator('html').get_attribute('lang') == 'pt-BR'
    # No language change reloads the decoder or alters the pending FT8 slot.
    await page.evaluate("setDigitalMode('FT8')")
    await page.wait_for_function('()=>digitalWorker!==null')
    await page.evaluate('window.originalDigitalWorker=digitalWorker')
    await page.locator('#language').select_option('en')
    assert await page.evaluate('digitalWorker===originalDigitalWorker')
    await page.evaluate("document.getElementById('digital-state').textContent='Sincronizando…'")
    await page.wait_for_function('()=>document.getElementById("digital-state").textContent==="Synchronizing…"')
    # Backend validation feedback and the open session dialog switch in place.
    await page.locator('#username').fill('!')
    assert (await page.locator('#username').evaluate('(el)=>el.validationMessage')).startswith('Callsign:')
    await page.evaluate("showSessionDialog('duplicate')")
    await page.evaluate("HamI18n.setLanguage('pt-BR')")
    assert await page.locator('#session-dialog-title').inner_text() == 'Já existe uma sessão aberta'
    await page.evaluate("HamI18n.setLanguage('es')")
    assert await page.locator('#session-dialog-title').inner_text() == 'Ya hay una sesión abierta'
    await page.evaluate("document.getElementById('session-dialog').close()")
    # Every DXCC entity has a localized label, including regional aliases.
    entities=[line.split(':')[0] for line in gzip.decompress((ROOT/'web/cty.dat.gz').read_bytes()).decode().splitlines() if line and not line[0].isspace() and ':' in line]
    labels=await page.evaluate('(entities)=>entities.map(name=>[name,HamI18n.country(name)])',entities)
    assert await page.evaluate('(entities)=>entities.every(name=>HamCountries.has(name))',entities)
    assert len(labels)==346 and all(translated for _,translated in labels)
    assert dict(labels)['European Russia']=='Rusia europea'
    assert dict(labels)['United States']=='Estados Unidos'
    await page.set_viewport_size({'width':390,'height':844})
    await page.locator('#language').select_option('pt-BR')
    boxes=await page.evaluate("""() => {
        const select=document.getElementById('language').getBoundingClientRect();
        const users=document.getElementById('listeners').parentElement.getBoundingClientRect();
        const audio=document.querySelector('.audio-control').getBoundingClientRect();
        return {select:select.toJSON(),users:users.toJSON(),audio:audio.toJSON(),width:innerWidth,overflow:document.documentElement.scrollWidth};
    }""")
    assert boxes['select']['x'] >= boxes['users']['right'], boxes
    assert boxes['users']['y'] >= boxes['audio']['bottom'], boxes
    assert boxes['select']['right'] <= boxes['width'], boxes
    assert boxes['overflow'] <= boxes['width'], boxes
    if engine=='chromium':
        folder=ROOT/'test-results';folder.mkdir(exist_ok=True)
        await page.screenshot(path=folder/'i18n-mobile-pt-BR.png',full_page=True)
        await page.set_viewport_size({'width':1440,'height':1000})
        await page.locator('#language').select_option('en')
        await page.screenshot(path=folder/'i18n-desktop-en.png',full_page=True)
    assert not errors, errors
    await context.close()
    # Real server moderation feedback follows the selected UI language, while
    # drafts stay editable and rejected submissions never reach public history.
    context=await browser.new_context()
    page=await context.new_page()
    await page.goto(url)
    await page.wait_for_function('()=>protocolReady')
    await page.locator('#username').fill('TESTER')
    await page.wait_for_function('()=>!document.getElementById("send-chat").disabled')
    repetition_notices={
        'es':'No repitas el mismo carácter más de 10 veces seguidas.',
        'en':'Do not repeat the same character more than 10 times in a row.',
        'pt-BR':'Não repita o mesmo caractere mais de 10 vezes seguidas.',
    }
    for language,notice in repetition_notices.items():
        await page.locator('#language').select_option(language)
        for kind in ('chat','log'):
            await page.locator('#'+kind+'-error').evaluate('(element)=>element.textContent=""')
            await page.locator('#'+kind+'-text').fill('='*11)
            await page.locator('#send-'+kind).click()
            await page.wait_for_function('({notice,kind})=>document.getElementById(kind+"-error").textContent===notice&&JSON.parse(sessionStorage.getItem("hamsdr-pending"))[kind].review===true',arg={'notice':notice,'kind':kind})
            assert await page.locator('#'+kind+'-text').input_value()=='='*11
            assert await page.evaluate('(kind)=>JSON.parse(sessionStorage.getItem("hamsdr-pending"))[kind].review',kind)
    notices={
        'es':'El mensaje contiene lenguaje ofensivo. Revisa el texto antes de enviarlo.',
        'en':'The message contains offensive language. Review the text before sending it.',
        'pt-BR':'A mensagem contém linguagem ofensiva. Revise o texto antes de enviá-la.',
    }
    for language,offensive in [('es','pendejo'),('en','fuck'),('pt-BR','caralho')]:
        await page.locator('#language').select_option(language)
        for kind in ('chat','log'):
            await page.locator('#'+kind+'-text').fill(offensive)
            await page.locator('#send-'+kind).click()
            await page.wait_for_function('({notice,kind})=>document.getElementById(kind+"-error").textContent===notice&&JSON.parse(sessionStorage.getItem("hamsdr-pending"))[kind].review===true',arg={'notice':notices[language],'kind':kind})
            assert await page.locator('#'+kind+'-text').input_value()==offensive
            assert await page.evaluate('(kind)=>JSON.parse(sessionStorage.getItem("hamsdr-pending"))[kind].review',kind)
    assert not (await gateway.history.history('chat'))['events']
    assert not (await gateway.history.history('log'))['events']
    await page.reload()
    await page.wait_for_function('()=>!document.getElementById("send-chat").disabled')
    assert await page.locator('#chat-text').input_value()=='caralho'
    assert await page.evaluate('JSON.parse(sessionStorage.getItem("hamsdr-pending")).chat.review')
    await page.locator('#chat-text').fill('CQ de TESTER, 73')
    await page.locator('#send-chat').click()
    await page.wait_for_function('()=>document.getElementById("chat-text").value===""')
    assert len((await gateway.history.history('chat'))['events'])==1
    # Keep the shared demo database independent for the next browser engine.
    await gateway.history.call(gateway.history.events_db.execute, 'DELETE FROM events')
    await gateway.history.call(gateway.history.events_db.commit)
    gateway.recent_history.clear()
    await context.close()
    # Storage-denied clients still get a working, session-local selector.
    context=await browser.new_context()
    await context.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw new DOMException('Denied','SecurityError');}})")
    page=await context.new_page()
    await page.goto(url)
    await page.locator('#language').select_option('en')
    assert await page.locator('#listen').inner_text()=='Start audio'
    await context.close()
    print(f'{engine}: 3 languages, audio/recording, decoded/user data, countries, validation, session modal, mobile layout, moderation/drafts, denied storage: OK',flush=True)


async def main():
    with tempfile.TemporaryDirectory(prefix='hamsdr-i18n-') as folder:
        args=SimpleNamespace(demo=True,source_host='127.0.0.1',source_port=1231,
            max_clients=10,max_clients_per_ip=10,full_quality_sessions_per_ip=10,
            origin='',digimodes=True,database=Path(folder)/'community.sqlite3',site_config=ROOT/'site.example.toml',working_directory=Path(folder))
        app=application(args);app[GATEWAY].site_config['digimodes']=True
        runner=web.AppRunner(app,access_log=None);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        try:
            async with async_playwright() as playwright:
                for engine in sys.argv[1:] or ('chromium','webkit'):
                    browser=await getattr(playwright,engine).launch()
                    try:await check(browser,url,app[GATEWAY],engine)
                    finally:await browser.close()
        finally:await runner.cleanup()


if __name__=='__main__':
    asyncio.run(main())
