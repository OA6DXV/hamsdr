# SPDX-License-Identifier: GPL-3.0-only
"""Real WebSocket moderation feedback and progress in isolated demo browsers."""
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

from aiohttp import web
from playwright.async_api import async_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import application, GATEWAY

ROOT = Path(__file__).resolve().parents[1]


async def check(browser, url, gateway, engine):
    context = await browser.new_context(viewport={'width': 390, 'height': 844})
    try:
        page = await context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(url)
        await page.locator('#username').fill('OA6DXV')
        await expect(page.locator('#send-chat')).to_be_enabled()
        for text in ('fuck', 'caralho', 'pendejo', 'fuck'):
            await page.locator('#chat-text').fill(text)
            await page.locator('#send-chat').click()
            await expect(page.locator('#chat-error')).to_be_visible()
            await expect(page.locator('#send-chat')).to_be_enabled()
        await page.locator('#chat-text').fill('caralho')
        await page.locator('#send-chat').click()
        await page.wait_for_function('()=>document.getElementById("send-chat").dataset.cooldown==="true"')
        await expect(page.locator('#send-chat')).to_be_disabled()
        await expect(page.locator('#send-log')).to_be_enabled()
        await expect(page.locator('#message')).to_be_empty()
        await page.wait_for_function('()=>parseFloat(document.getElementById("send-chat").style.getPropertyValue("--cooldown-progress"))>=25')
        visual = await page.evaluate('''() => {
            const button=document.getElementById('send-chat'),notice=document.getElementById('chat-error');
            return {progress:parseFloat(button.style.getPropertyValue('--cooldown-progress')),
                background:getComputedStyle(button).backgroundImage,disabled:button.disabled,
                color:getComputedStyle(notice).color,weight:getComputedStyle(notice).fontWeight,
                y:notice.getBoundingClientRect().y,bottom:document.getElementById('chatform').getBoundingClientRect().bottom};
        }''')
        assert 25 <= visual['progress'] < 100 and visual['disabled'], visual
        assert 'linear-gradient' in visual['background'], visual
        assert visual['color'] == 'rgb(196, 0, 0)' and int(visual['weight']) >= 700, visual
        assert visual['y'] >= visual['bottom'], visual
        await page.locator('#language').select_option('en')
        await expect(page.locator('#chat-error')).to_have_text('The message contains offensive language. Review the text before sending it.')
        await page.locator('#language').select_option('pt-BR')
        await expect(page.locator('#chat-error')).to_have_text('A mensagem contém linguagem ofensiva. Revise o texto antes de enviá-la.')
        await expect(page.locator('#send-chat')).to_be_enabled()
        await page.locator('#chat-text').fill('pendejo')
        await page.locator('#send-chat').click()
        await page.wait_for_function('()=>document.getElementById("send-chat").dataset.cooldown==="true"')
        await page.locator('#username').fill('TESTER')
        await page.locator('#username').dispatch_event('change')
        await expect(page.locator('#send-chat')).to_be_disabled()
        await page.reload()
        await page.wait_for_function('()=>protocolReady&&document.getElementById("send-chat").dataset.cooldown==="true"')
        await expect(page.locator('#send-chat')).to_be_disabled()
        await expect(page.locator('#chat-error')).to_have_text('Chat temporariamente desativado após repetidas tentativas de envio bloqueadas.')
        await expect(page.locator('#message')).to_be_empty()
        await expect(page.locator('#send-chat')).to_be_enabled()
        assert await page.locator('#send-chat').get_attribute('data-cooldown') == 'false'
        assert await page.locator('#chat-text').input_value() == 'pendejo'
        assert not (await gateway.history.history('chat'))['events']
        await page.locator('#chat-text').fill('CQ 73')
        await page.locator('#send-chat').click()
        await expect(page.locator('#chat-text')).to_have_value('')
        await expect(page.locator('#chat-error')).to_be_hidden()
        assert not errors, errors
        print(f'{engine}: progress, local bold red feedback, translations, rename/reload, editable draft and expiry: OK', flush=True)
    finally:
        await context.close()


async def main():
    with tempfile.TemporaryDirectory(prefix='hamsdr-chat-cooldown-') as folder:
        args = SimpleNamespace(demo=True, source_host='127.0.0.1', source_port=1231,
            max_clients=5, max_clients_per_ip=5, origin='', digimodes=False,
            database=Path(folder)/'community.sqlite3', working_directory=Path(folder),
            site_config=ROOT/'site.example.toml')
        app = application(args)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        try:
            async with async_playwright() as playwright:
                for engine in sys.argv[1:] or ('chromium', 'webkit'):
                    browser = await getattr(playwright, engine).launch()
                    try:
                        await check(browser, url, app[GATEWAY], engine)
                        await app[GATEWAY].history.call(app[GATEWAY].history.events_db.execute, 'DELETE FROM events')
                        await app[GATEWAY].history.call(app[GATEWAY].history.events_db.commit)
                        app[GATEWAY].recent_history.clear()
                    finally:
                        await browser.close()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
