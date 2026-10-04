# SPDX-License-Identifier: GPL-3.0-only
"""Read-only live digital transport/playback checks; no chat or receiver controls."""
import asyncio
import sys
from playwright.async_api import async_playwright, expect


async def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: digital_audio_browser_test.py SERVER_URL")
    base = sys.argv[1].rstrip("/")
    async with async_playwright() as playwright:
        for engine in (playwright.chromium, playwright.webkit):
            browser = await engine.launch()
            try:
                for mode, button in (("FT8", "digital"), ("FT4", "digital"),
                                     ("RTTY", "rtty"), ("CW", "cw")):
                    page = await browser.new_page(viewport={"width": 390, "height": 844})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    await page.goto(base + f"/?freq=7074&digital={mode}&mute=1")
                    await page.wait_for_function("()=>protocolReady && spectrumFrames>2")
                    await page.evaluate("""()=>{
                        window.digitalTransport={frames:0,samples:0,other:0};
                        socket.addEventListener('message',event=>{
                            if(typeof event.data==='string')return;
                            const view=new DataView(event.data),kind=view.getUint8(0);
                            if([2,10,11].includes(kind))digitalTransport.other++;
                            if(kind!==12)return;
                            const count=view.getUint16(14,true);
                            if(view.byteLength!==16+count*2)throw Error('Invalid digital PCM frame');
                            digitalTransport.frames++;digitalTransport.samples+=count;
                        });
                    }""")
                    await page.locator(f"#{button}-audio-start").click()
                    await page.wait_for_function("()=>audioEnabled && audioPackets>30 && digitalTransport.frames>30")
                    await expect(page.locator("#audio-quality")).to_have_value("digiraw")
                    await page.wait_for_function("()=>context && context.state==='running'")
                    if mode == "CW":
                        await page.wait_for_function("()=>cwPanel.ready && cwPanel.frames>5 && cwPanel.pending<4")
                    if mode in ("FT8", "FT4"):
                        assert await page.evaluate("digitalWorker!==null && !digitalRows.some(row=>row.placeholder)")
                    transport = await page.evaluate("digitalTransport")
                    assert transport["other"] == 0, transport
                    # Live rtl_tcp input blocks contain 16384 IQ samples:
                    # 256 listening samples, or 192 digital samples per frame.
                    assert transport["samples"] == transport["frames"] * 192, transport
                    await page.locator("#listen").click()
                    await page.wait_for_function("()=>!audioEnabled")
                    await page.wait_for_timeout(250)
                    stopped = await page.evaluate("digitalTransport.frames")
                    await page.wait_for_timeout(350)
                    assert await page.evaluate("digitalTransport.frames") == stopped
                    assert not errors, errors
                    print(f"{engine.name}: {mode}: {transport['frames']} native PCM12k frames, playback/pause OK", flush=True)
                    await page.close()
            finally:
                await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
