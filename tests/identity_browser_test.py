# SPDX-License-Identifier: GPL-3.0-only
"""Isolated demo browser checks for insecure, native TLS and TLS proxy modes."""
import asyncio
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
from types import SimpleNamespace

from aiohttp import ClientSession, WSServerHandshakeError, WSMsgType, web
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import application, GATEWAY

ROOT = Path(__file__).resolve().parents[1]


async def serve(app, context=None):
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0, ssl_context=context)
    await site.start()
    return runner, f'{"https" if context else "http"}://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'


async def check_browser(browser, url, gateway, fallback=False):
    gateway.connection_attempts.clear()
    errors = []
    context = await browser.new_context(ignore_https_errors=True, viewport={'width': 390, 'height': 844})
    if fallback:
        await context.add_init_script("Object.defineProperty(navigator,'locks',{value:{request:()=>Promise.reject(new DOMException('Denied','SecurityError'))}});")
    pages = [await context.new_page(), await context.new_page()]
    for page in pages:
        page.on('pageerror', lambda error: errors.append(str(error)))
    await asyncio.gather(*(page.goto(url) for page in pages))
    await asyncio.gather(*(page.wait_for_function('()=>protocolReady || !document.getElementById("session-retry").hidden') for page in pages))
    states = [await page.evaluate('protocolReady') for page in pages]
    assert sum(states) == 1, 'simultaneous first tabs must admit one stream'
    active, blocked = (pages if states[0] else pages[::-1])
    assert 'otra pestaña' in await blocked.locator('#connection').inner_text()
    token = await active.evaluate('localStorage.getItem("hamsdr-identity-v1")')
    assert token == await blocked.evaluate('localStorage.getItem("hamsdr-identity-v1")')
    count = await gateway.history.call(lambda: gateway.history.db.execute('SELECT COUNT(*) FROM browser_identities').fetchone()[0])
    # Each test browser context adds exactly one registry row.
    identity = await active.evaluate('radioIdentity.id')
    assert len(gateway.identity_owners[identity]) == 1
    await active.locator('#username').fill('oa6dxv')
    await active.wait_for_function('()=>!document.getElementById("send-chat").disabled')
    assert await active.locator('#username').input_value() == 'OA6DXV'
    await active.locator('#audio-quality').select_option('raw')
    await active.locator('#listen').click()
    await active.wait_for_function('()=>audioPackets > 5')
    await active.evaluate("window.savedPackets=audioPackets")
    await blocked.wait_for_timeout(300)
    assert await active.evaluate('audioPackets>window.savedPackets')
    assert not await context.cookies(), 'identity must issue no cookies'
    await active.reload()
    await active.wait_for_function('()=>protocolReady')
    assert await active.evaluate('radioIdentity.id') == identity
    assert await active.evaluate('localStorage.getItem("hamsdr-identity-v1")') == token
    # Simulate an interrupted WebSocket, retaining page state and the credential.
    await active.evaluate('socket.close()')
    await active.wait_for_function('()=>!protocolReady')
    await active.wait_for_function('()=>protocolReady')
    assert await active.evaluate('radioIdentity.id') == identity
    await active.close()
    await blocked.locator('#session-dialog-retry').click()
    await blocked.wait_for_function('()=>protocolReady')
    assert await blocked.evaluate('radioIdentity.id') == identity
    assert await gateway.history.call(lambda: gateway.history.db.execute('SELECT COUNT(*) FROM browser_identities').fetchone()[0]) == count
    await context.close()
    assert not errors, errors


async def storage_denied(browser, url, gateway):
    gateway.connection_attempts.clear()
    context = await browser.new_context(ignore_https_errors=True)
    await context.add_init_script("""
        Object.defineProperty(window,'localStorage',{get(){throw new DOMException('Denied','SecurityError');}});
        Object.defineProperty(window,'indexedDB',{get(){throw new DOMException('Denied','SecurityError');}});
        Object.defineProperty(navigator,'locks',{value:{request:()=>Promise.reject(new DOMException('Denied','SecurityError'))}});
    """)
    page = await context.new_page()
    await page.goto(url)
    await page.wait_for_function('()=>protocolReady')
    assert await page.locator('#identity-storage').is_visible()
    identity = await page.evaluate('radioIdentity.id')
    await page.evaluate('socket.close()')
    await page.wait_for_function('()=>!protocolReady')
    await page.wait_for_function('()=>protocolReady')
    assert await page.evaluate('radioIdentity.id') == identity
    await context.close()


async def full_receiver(browser, url, gateway):
    # Preserve the hard connection cap; failed HTTP upgrades must not loop.
    for _ in range(100):
        if not gateway.clients:
            break
        await asyncio.sleep(.05)
    assert not gateway.clients
    gateway.connection_attempts.clear()
    original_limit = gateway.args.max_clients
    gateway.args.max_clients = 1
    context = await browser.new_context(ignore_https_errors=True)
    try:
        first = await context.new_page()
        await first.goto(url)
        await first.wait_for_function('()=>protocolReady')
        second = await context.new_page()
        attempts = []
        second.on('websocket', lambda _: attempts.append(1))
        await second.goto(url)
        await second.wait_for_function('()=>!document.getElementById("session-retry").hidden')
        await second.wait_for_timeout(1800)
        assert len(attempts) == 1, 'capacity rejection must not auto-retry'
        assert not await second.evaluate('protocolReady')
        assert gateway.info()['users'] == 1
        await second.locator('#session-transfer').wait_for(state='visible')
        await second.locator('#session-transfer').click()
        await second.wait_for_function('()=>protocolReady')
        await first.wait_for_function('()=>document.getElementById("session-dialog").open')
        assert 'trasladó' in await first.locator('#session-dialog-title').inner_text()
        assert len(attempts) == 2
        assert await second.locator('#message').inner_text() == ''
    finally:
        await context.close()
        gateway.args.max_clients = original_limit


async def transfer_session(browser, url, gateway, screenshot=False):
    gateway.connection_attempts.clear()
    context = await browser.new_context(ignore_https_errors=True, viewport={'width':390,'height':844})
    errors = []
    try:
        first = await context.new_page()
        first.on('pageerror', lambda error: errors.append(str(error)))
        await first.goto(url)
        await first.wait_for_function('()=>protocolReady')
        identity = await first.evaluate('radioIdentity.id')
        await first.locator('#audio-quality').select_option('raw')
        await first.locator('#listen').click()
        await first.wait_for_function('()=>audioPackets>5')
        await first.locator('#record').click()
        await first.wait_for_function('()=>recording')
        second = await context.new_page()
        second.on('pageerror', lambda error: errors.append(str(error)))
        await second.goto(url)
        await second.wait_for_function('()=>document.getElementById("session-dialog").open')
        assert 'Ya hay una sesión abierta' == await second.locator('#session-dialog-title').inner_text()
        box = await second.locator('#session-dialog').bounding_box()
        assert box['x'] >= 8 and box['x'] + box['width'] <= 382
        assert abs(box['y'] + box['height']/2 - 422) < 3
        assert await second.evaluate('document.getElementById("session-dialog").contains(document.activeElement)')
        if screenshot:
            output = ROOT / 'test-results' / 'identity-session-modal.png'
            output.parent.mkdir(parents=True, exist_ok=True)
            await second.screenshot(path=str(output))
        await second.locator('#session-transfer').click()
        await second.wait_for_function('()=>protocolReady')
        await first.wait_for_function('()=>document.getElementById("session-dialog").open && !audioEnabled && !recording')
        assert 'trasladó' in await first.locator('#session-dialog-title').inner_text()
        assert await second.evaluate('radioIdentity.id') == identity
        await second.wait_for_timeout(1500)
        assert not await first.evaluate('protocolReady')
        assert await second.evaluate('protocolReady')
        assert gateway.info()['users'] == 1
        assert len(gateway.identity_owners[identity]) == 1
        assert not await context.cookies()
        assert not errors, errors
    finally:
        await context.close()


async def reconnect_upgrade_failure(browser, url, gateway):
    gateway.connection_attempts.clear()
    context = await browser.new_context(ignore_https_errors=True)
    original_origin = gateway.args.origin
    try:
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_function('()=>protocolReady')
        identity = await page.evaluate('radioIdentity.id')
        # Reject a few upgrade attempts on this demo server, then restore it.
        gateway.args.origin = 'https://temporarily-unavailable.example.invalid'
        await page.evaluate('socket.close()')
        await page.wait_for_function('()=>!protocolReady')
        await page.wait_for_timeout(1800)
        assert not await page.locator('#session-retry').is_visible()
        assert not await page.evaluate('protocolReady')
        gateway.args.origin = original_origin
        await page.wait_for_function('()=>protocolReady')
        assert await page.evaluate('radioIdentity.id') == identity
    finally:
        gateway.args.origin = original_origin
        await context.close()


async def main():
    with tempfile.TemporaryDirectory(prefix='hamsdr-identity-') as directory:
        folder = Path(directory)
        certificate, key = folder / 'cert.pem', folder / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
                        '-out', str(certificate), '-days', '1', '-subj', '/CN=localhost'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(certificate, key)
        async with async_playwright() as playwright:
            for engine in ('chromium', 'webkit'):
                browser = await getattr(playwright, engine).launch()
                try:
                    for mode in ('insecure', 'native', 'proxy'):
                        args = SimpleNamespace(demo=True, source_host='127.0.0.1', source_port=1231,
                            max_clients=10, max_clients_per_ip=10, full_quality_sessions_per_ip=10,
                            origin='', database=folder / f'{engine}-{mode}.sqlite3', site_config=ROOT / 'site.example.toml')
                        app = application(args)
                        runner, url = await serve(app, tls if mode == 'native' else None)
                        proxy_runner = None
                        async with ClientSession(auto_decompress=False) as upstream:
                            if mode == 'proxy':
                                backend = url
                                async def proxy(request):
                                    if request.headers.get('Upgrade', '').lower() == 'websocket':
                                        try:
                                            target = await upstream.ws_connect(backend + str(request.rel_url), origin=request.headers.get('Origin'))
                                        except WSServerHandshakeError as error:
                                            return web.Response(status=error.status, text='Upstream admission rejected')
                                        async with target:
                                            ws = web.WebSocketResponse()
                                            await ws.prepare(request)
                                            async def relay(source, destination):
                                                try:
                                                    async for message in source:
                                                        if message.type == WSMsgType.TEXT:
                                                            await destination.send_str(message.data)
                                                        elif message.type == WSMsgType.BINARY:
                                                            await destination.send_bytes(message.data)
                                                finally:
                                                    # Deliberately normalize the close code:
                                                    # session transfer must also work from
                                                    # its authenticated application notice.
                                                    await destination.close()
                                            tasks = [asyncio.create_task(relay(ws, target)), asyncio.create_task(relay(target, ws))]
                                            try:
                                                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                                            finally:
                                                for task in tasks:
                                                    task.cancel()
                                                await asyncio.gather(*tasks, return_exceptions=True)
                                            return ws
                                    headers = {key:value for key,value in request.headers.items()
                                               if key.lower() in ('origin','content-type')}
                                    async with upstream.request(request.method, backend + str(request.rel_url),
                                                                data=await request.read(), headers=headers) as response:
                                        headers = {k: v for k, v in response.headers.items() if k.lower() not in ('transfer-encoding', 'connection', 'content-length')}
                                        return web.Response(body=await response.read(), status=response.status, headers=headers)
                                proxy_app = web.Application()
                                proxy_app.router.add_route('*', '/{tail:.*}', proxy)
                                proxy_runner, url = await serve(proxy_app, tls)
                                args.origin = url
                            try:
                                await check_browser(browser, url, app[GATEWAY], fallback=mode == 'insecure')
                                await storage_denied(browser, url, app[GATEWAY])
                                await full_receiver(browser, url, app[GATEWAY])
                                await transfer_session(browser, url, app[GATEWAY], screenshot=engine == 'chromium' and mode == 'insecure')
                                if mode == 'insecure':
                                    await reconnect_upgrade_failure(browser, url, app[GATEWAY])
                                print(f'{engine}: {mode}: modal, explicit transfer, full capacity, recording stop, tabs, reconnect, PCM, no cookies: OK', flush=True)
                            finally:
                                if proxy_runner:
                                    await proxy_runner.cleanup()
                                await runner.cleanup()
                finally:
                    await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
