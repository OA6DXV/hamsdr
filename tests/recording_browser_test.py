# SPDX-License-Identifier: GPL-3.0-only
"""Recording containers, storage, recovery and profile isolation on a local demo."""
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request
import wave

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "test-results" / "recording"
URL = "http://127.0.0.1:18104/"


def ogg_packets(data):
    offset = 0
    pending = bytearray()
    packets = []
    while offset < len(data):
        assert data[offset:offset + 4] == b"OggS"
        count = data[offset + 26]
        segments = data[offset + 27:offset + 27 + count]
        body = offset + 27 + count
        for length in segments:
            pending.extend(data[body:body + length])
            body += length
            if length < 255:
                packets.append(bytes(pending))
                pending.clear()
        offset = body
    assert not pending
    return packets


async def download(page, button, name):
    async with page.expect_download() as event:
        await page.locator(button).click()
    result = await event.value
    assert await result.failure() is None
    path = OUTPUT / (name + Path(result.suggested_filename).suffix)
    await result.save_as(path)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
                   check=True, capture_output=True)
    return path


async def load(page):
    await page.goto(URL + "?freq=7100&mode=USB")
    await page.wait_for_function("()=>protocolReady&&recorder")
    await page.evaluate("()=>recorder.ready")


async def start_recording(page):
    await page.locator("#record").click()
    try:
        await page.wait_for_function("()=>recording&&recorder.active&&!recorder.busy", timeout=5000)
    except Exception:
        print(await page.evaluate("""()=>({recording,audioEnabled,audioStarting,engine:document.documentElement.dataset.audioEngine,
            context:context?.state,backend:recorder.backend,busy:recorder.busy,error:recorder.error,
            message:document.querySelector('#message').textContent})"""), flush=True)
        raise


async def stop_recording(page):
    await page.locator("#record").click()
    await expect(page.locator("#download")).to_be_visible()
    await expect(page.locator("#audio-quality")).to_be_enabled()


async def profile_cases(browser, name):
    context = await browser.new_context(viewport={"width": 390, "height": 844})
    page = await context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await load(page)
    backend = await page.evaluate("recorder.backend")
    assert backend in ("opfs", "indexeddb"), backend
    await page.evaluate("""()=>{
        window.recordedWire=[];
        socket.addEventListener('message',event=>{
            if(!recording||typeof event.data==='string')return;
            const bytes=new Uint8Array(event.data),kind=bytes[0],profile=recorder.metadata.profile;
            if(kind===2&&profile==='raw')recordedWire.push(Array.from(bytes.subarray(1)));
            if(kind===12&&profile==='digiraw')recordedWire.push(Array.from(bytes.subarray(16)));
            if((kind===10&&profile==='balanced')||(kind===11&&profile==='mobile'))recordedWire.push(Array.from(bytes.subarray(5)));
        });
    }""")
    for profile in ("raw", "balanced", "mobile", "digiraw"):
        if profile == "digiraw":
            await page.locator('[data-digital-mode="FT8"]').click()
            await expect(page.locator("#audio-quality")).to_have_value("digiraw")
        else:
            await page.locator("#audio-quality").select_option(profile)
        await page.wait_for_timeout(150)
        await page.evaluate("recordedWire=[]")
        await start_recording(page)
        await expect(page.locator("#audio-quality")).to_be_disabled()
        await expect(page.locator('[data-digital-mode="FT8"]')).to_be_disabled()
        await expect(page.locator('[data-digital-menu]')).to_be_disabled()
        # Even a programmatic change event must leave the recording profile alone.
        await page.locator("#audio-quality").evaluate("""element=>{
            element.value=element.value==='raw'?'balanced':'raw';
            element.dispatchEvent(new Event('change',{bubbles:true}));
        }""")
        await expect(page.locator("#audio-quality")).to_have_value(profile)
        await page.locator("#mute").check()
        await page.wait_for_timeout(2100)
        await expect(page.locator("#record-status")).to_have_text(re.compile(r"^\d{2}:\d{2} • \d+\.\d{2} MiB$"))
        await stop_recording(page)
        # A normal stop (including repeated cycles) must use Save, not recovery.
        await expect(page.locator("#record-recovery")).to_be_hidden()
        meta = await page.evaluate("recorder.metadata")
        wire = [bytes(packet) for packet in await page.evaluate("recordedWire")]
        path = await download(page, "#download", name + "-" + profile)
        if profile in ("raw", "digiraw"):
            with wave.open(str(path), "rb") as audio:
                assert audio.getframerate() == (12000 if profile == "digiraw" else 16000)
                assert audio.getnchannels() == 1 and audio.getsampwidth() == 2
                assert audio.readframes(audio.getnframes()) == b"".join(wire)
        else:
            packets = ogg_packets(path.read_bytes())
            assert packets[0].startswith(b"OpusHead") and packets[1].startswith(b"OpusTags")
            assert packets[2:] == wire, "Compressed packets were changed or lost"
            assert path.stat().st_size / (meta["samples"] / meta["rate"]) < 6500
        print(name, profile, backend, path.stat().st_size, "bytes; exact received payload", flush=True)
    await expect(page.locator("#record-storage-status")).to_have_text("")
    assert not errors, errors
    await context.close()


async def recovery_case(browser, name, fallback=None, profile="raw"):
    context = await browser.new_context()
    if fallback:
        source = (ROOT / "web" / "recorder-worker.js").read_text()
        shim = "Object.defineProperty(navigator.storage,'getDirectory',{value:undefined});\nObject.defineProperty(crypto,'randomUUID',{value:undefined});\n"
        if fallback == "memory":
            shim += "Object.defineProperty(self,'indexedDB',{value:undefined});\n"
        await context.route("**/recorder-worker.js", lambda route: route.fulfill(
            body=shim + source, content_type="application/javascript"))
    page = await context.new_page()
    await load(page)
    backend = await page.evaluate("recorder.backend")
    if fallback:
        assert backend == fallback
    await page.locator("#audio-quality").select_option(profile)
    await start_recording(page)
    await page.wait_for_timeout(1200)
    if backend == "memory":
        await expect(page.locator("#record-storage-status")).to_contain_text("memoria")
        await stop_recording(page)
        await download(page, "#download", name + "-memory-" + profile)
        await context.close()
        print(name, profile, "memory fallback and export passed", flush=True)
        return
    meta = await page.evaluate("()=>recorder.request('checkpoint')")
    other = await context.new_page()
    await load(other)
    assert meta["id"] not in await other.evaluate("recorder.records.map(record=>record.id)")
    # Simulate process loss: no stop/finalization or pagehide flush.
    meta = await page.evaluate("""async()=>{
        const checkpoint=await recorder.request('checkpoint');
        recorder.worker.terminate();return checkpoint;
    }""")
    await page.close()
    await other.reload()
    await other.evaluate("()=>recorder.ready")
    await expect(other.locator("#record-recovery")).to_be_visible(timeout=20000)
    await other.locator("#record-recovery-list").select_option(meta["id"])
    path = await download(other, "#record-recover", name + "-" + backend + "-" + profile + "-recovered")
    if profile == "raw":
        with wave.open(str(path), "rb") as audio:
            assert audio.getnframes() == meta["samples"], "Recovery exceeded its committed checkpoint"
    else:
        assert len(ogg_packets(path.read_bytes())) - 2 == meta["samples"] // 960
    await other.locator("#record-delete").click()
    await expect(other.locator("#record-recovery")).to_be_hidden()
    print(name, profile, backend, "crash recovery, active-tab exclusion and explicit deletion passed", flush=True)
    await context.close()


async def quota_case(browser):
    context = await browser.new_context()
    source = (ROOT / "web" / "recorder-worker.js").read_text()
    shim = """
const originalWrite=FileSystemSyncAccessHandle.prototype.write;
FileSystemSyncAccessHandle.prototype.write=function(bytes,options){
    if(self.testQuota)throw new DOMException('Test quota exhausted','QuotaExceededError');
    return originalWrite.call(this,bytes,options);
};
self.addEventListener('message',event=>{if(event.data.triggerQuota)self.testQuota=true;});
"""
    await context.route("**/recorder-worker.js", lambda route: route.fulfill(
        body=shim + source, content_type="application/javascript"))
    page = await context.new_page()
    await load(page)
    assert await page.evaluate("recorder.backend") == "opfs"
    await page.locator("#audio-quality").select_option("raw")
    await start_recording(page)
    await page.wait_for_timeout(1200)
    committed = await page.evaluate("()=>recorder.request('checkpoint')")
    await page.evaluate("()=>recorder.request('checkpoint',{triggerQuota:true}).catch(()=>null)")
    await page.wait_for_function("()=>!recording")
    await expect(page.locator("#record-storage-status")).to_contain_text("lleno")
    await expect(page.locator("#audio-quality")).to_be_enabled()
    await expect(page.locator("#download")).to_be_visible()
    path = await download(page, "#download", "quota-checkpoint")
    with wave.open(str(path), "rb") as audio:
        assert audio.getnframes() == committed["samples"]
    await context.close()
    print("Quota failure preserved/exported the previous durable checkpoint", flush=True)


async def forced_profile_case(browser, name):
    context = await browser.new_context()
    page = await context.new_page()
    await load(page)
    await page.locator("#audio-quality").select_option("balanced")
    await start_recording(page)
    await page.wait_for_timeout(1200)
    await page.evaluate("window.radioSend({type:'audio-profile',profile:'mobile'})")
    await expect(page.locator("#download")).to_be_visible()
    await expect(page.locator("#audio-quality")).to_have_value("mobile")
    assert await page.evaluate("!recording&&recorder.metadata.profile==='balanced'")
    await download(page, "#download", name + "-forced-profile")
    # The counter's one-hour threshold is independent from storage caps.
    await page.evaluate("""()=>{
        window.savedRecorderMetadata=recorder.metadata;
        recorder.metadata={...recorder.metadata,samples:recorder.metadata.rate*3601};recorder.state();
    }""")
    await expect(page.locator("#record-status")).to_have_text(re.compile(r"^01:00:01 • "))
    assert await page.locator("#record-status").evaluate("element=>element.classList.contains('record-long')")
    await page.evaluate("recorder.metadata=savedRecorderMetadata;recorder.state()")
    await context.close()
    print(name, "server-forced profile finalization and one-hour counter passed", flush=True)


async def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="recorder-", dir=ROOT / "var") as directory:
        directory = Path(directory)
        config = directory / "site.toml"
        config.write_text('[server]\ndigimodes = true\n[secure]\nmode = "insecure"\n')
        with (OUTPUT / "server.log").open("w") as log:
            server = subprocess.Popen([
                str(ROOT / ".venv/bin/python"), str(ROOT / "server.py"), "--demo",
                "--site-config", str(config), "--bind", "127.0.0.1", "--port", "18104",
                "--database", str(directory / "history.sqlite3")], cwd=ROOT,
                stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(URL + "health", timeout=.2) as response:
                            assert json.load(response)["version"] == "0.9.1-dev"
                        break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError((OUTPUT / "server.log").read_text())
                        await asyncio.sleep(.1)
                else:
                    raise RuntimeError("Local demo did not start")
                async with async_playwright() as playwright:
                    for engine in (playwright.chromium, playwright.webkit):
                        if os.environ.get("RECORDING_BROWSER") not in (None, engine.name):
                            continue
                        browser = await engine.launch()
                        try:
                            await profile_cases(browser, engine.name)
                            await recovery_case(browser, engine.name)
                            await recovery_case(browser, engine.name, profile="balanced")
                            await forced_profile_case(browser, engine.name)
                            if engine.name == "chromium":
                                await recovery_case(browser, engine.name, "indexeddb")
                                await recovery_case(browser, engine.name, "indexeddb", "balanced")
                                await recovery_case(browser, engine.name, "memory")
                                await recovery_case(browser, engine.name, "memory", "balanced")
                                await quota_case(browser)
                        finally:
                            await browser.close()
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    asyncio.run(main())
