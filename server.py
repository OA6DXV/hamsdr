"""Small HTTP/WebSocket gateway. DSP stays in one shared C++ subprocess."""
import argparse
import asyncio
import contextlib
import ipaddress
import json
import logging
import math
import re
import resource
import sqlite3
from pathlib import Path
import struct
import time
from urllib.parse import urlsplit

from aiohttp import web, WSMsgType
from audio_codec import downsample_2, encode_ima_adpcm, pcm16le_samples
from community import History, plain_text
from waterfall_codec import encode as encode_waterfall

ROOT = Path(__file__).resolve().parent
VERSION = "0.2.7-unstable"
MODES = {"USB": (300, 2700), "LSB": (-2700, -300), "AM": (-4000, 4000),
         "CW": (450, 950), "NFM": (-5000, 5000)}

def load_site_config(requested=None):
    """Load operator branding separately from application code and assets."""
    path = Path(requested).expanduser() if requested else ROOT / "site.json"
    if not path.exists() and not requested:
        path = ROOT / "site.example.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"site config: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("site config: root must be an object")

    def text(name, limit, default=""):
        value = data.get(name, default)
        if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
            raise ValueError(f"site config: invalid {name}")
        return value.strip()

    description = data.get("description", [])
    if not isinstance(description, list) or len(description) > 4 or any(
            not isinstance(line, str) or not line.strip() or len(line) > 240 or any(ord(c) < 32 for c in line)
            for line in description):
        raise ValueError("site config: invalid description")
    administrator = data.get("administrator", {})
    if not isinstance(administrator, dict):
        raise ValueError("site config: invalid administrator")
    admin_name = administrator.get("name", "Administrator")
    admin_url = administrator.get("url", "")
    if not isinstance(admin_name, str) or not admin_name.strip() or len(admin_name) > 80:
        raise ValueError("site config: invalid administrator name")
    if not isinstance(admin_url, str) or len(admin_url) > 300:
        raise ValueError("site config: invalid administrator url")
    if admin_url:
        parsed = urlsplit(admin_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("site config: administrator url must use http or https")

    logo = data.get("logo", {})
    if not isinstance(logo, dict):
        raise ValueError("site config: invalid logo")
    logo_file = logo.get("file", "")
    logo_path = None
    if logo_file:
        if not isinstance(logo_file, str) or len(logo_file) > 300:
            raise ValueError("site config: invalid logo file")
        logo_path = (path.parent / logo_file).resolve()
        if logo_path.suffix.lower() not in (".svg", ".png", ".jpg", ".jpeg", ".webp") or not logo_path.is_file() or logo_path.stat().st_size > 2_000_000:
            raise ValueError("site config: logo must be an SVG, PNG, JPEG or WebP file up to 2 MB")
    logo_alt = logo.get("alt", "Receiver logo")
    if not isinstance(logo_alt, str) or len(logo_alt) > 120:
        raise ValueError("site config: invalid logo alt text")

    public = {
        "language": text("language", 12, "en"),
        "receiver_name": text("receiver_name", 120, "HamSDR"),
        "description": [line.strip() for line in description],
        "administrator_label": text("administrator_label", 80, "Maintained by"),
        "administrator": {"name": admin_name.strip(), "url": admin_url},
        "footer_administrator_label": text("footer_administrator_label", 40, "Administrator"),
        "version": VERSION,
        "logo": {"enabled": logo_path is not None, "url": "./site-logo" if logo_path else "", "alt": logo_alt},
    }
    return public, logo_path

class Gateway:
    def __init__(self, args):
        self.args = args
        self.clients = {}
        self.next_id = 1
        self.process = None
        self.source = "connecting"
        self.restarts = 0
        self.dropped = 0
        self.last_data = 0
        self.task = None
        self.lock = asyncio.Lock()
        self.history = History(getattr(args, "database", ROOT / "var/community.sqlite3"),
                               getattr(args, "retention_days", 90))
        self.community_lock = asyncio.Lock()
        self.community_tokens, self.community_last = 30.0, time.monotonic()
        self.sent_bytes = 0
        self.metrics = {"cpu_percent": 0, "kbps": 0}
        self.stats_task = None
        self.waterfall_sequence = 0
        self.site_config, self.site_logo = load_site_config(getattr(args, "site_config", None))

    def info(self):
        return {"type": "status", "version": VERSION, "source": self.source, "users": len(self.clients),
                "center": 7100500, "sample_rate": 1024000, "audio_rate": 16000,
                "audio_profiles": {"original": 16000, "balanced": 16000, "mobile": 8000},
                "fft_size": 65536, "demo": self.args.demo, "restarts": self.restarts,
                "dropped": self.dropped, **self.metrics}

    async def command(self, text):
        async with self.lock:
            if self.process and self.process.returncode is None:
                try:
                    self.process.stdin.write((text + "\n").encode("ascii"))
                    await asyncio.wait_for(self.process.stdin.drain(), 2)
                except (BrokenPipeError, ConnectionResetError, asyncio.TimeoutError):
                    if self.process.returncode is None:
                        self.process.kill()

    def publish(self, packet, target=0):
        for ident, client in tuple(self.clients.items()):
            if target and target != ident:
                continue
            queue = client["reliable"] if isinstance(packet, str) else client["queue"]
            if queue.full():
                if isinstance(packet, str):
                    if not client["closing"]:
                        client["closing"] = True
                        asyncio.create_task(client["ws"].close(code=1013, message=b"Reconnect to recover history"))
                    continue
                queue.get_nowait()
                self.dropped += 1
                client["congestion"] += 1
            queue.put_nowait(packet)
            client["wake"].set()

    def waterfall_profile(self, ident, profile, preference=None, ceiling=None):
        client = self.clients.get(ident)
        if not client:
            return
        client["waterfall_profile"] = profile
        if preference is not None: client["waterfall_preference"] = preference
        if ceiling is not None: client["waterfall_ceiling"] = ceiling
        self.publish(json.dumps({"type":"waterfall-profile", "preference":client["waterfall_preference"],
            "profile":profile}), ident)

    def audio_state(self, ident, enabled):
        client = self.clients.get(ident)
        if not client: return
        client["audio_enabled"] = enabled
        if not enabled:
            retained = []
            while not client["queue"].empty():
                packet = client["queue"].get_nowait()
                if not isinstance(packet, bytes) or not packet or packet[0] not in (2, 8, 9): retained.append(packet)
            for packet in retained: client["queue"].put_nowait(packet)
        self.publish(json.dumps({"type":"audio-state", "enabled":enabled,
            "profile":client["audio_profile"]}), ident)

    def audio_profile(self, ident, profile):
        client = self.clients.get(ident)
        if not client: return
        client["audio_profile"] = profile
        client["adpcm_state"] = (0, 0)
        retained = []
        while not client["queue"].empty():
            packet = client["queue"].get_nowait()
            if not isinstance(packet, bytes) or not packet or packet[0] not in (2, 8, 9): retained.append(packet)
        for packet in retained: client["queue"].put_nowait(packet)
        self.publish(json.dumps({"type":"audio-profile", "profile":profile,
            "rate":8000 if profile == "mobile" else 16000}), ident)

    def publish_audio(self, ident, data):
        client = self.clients.get(ident)
        if not client or not client["audio_enabled"]:
            return
        profile = client["audio_profile"]
        if profile == "original":
            self.publish(bytes([2])+data, ident)
            return
        samples = pcm16le_samples(data)
        if profile == "mobile":
            samples = downsample_2(samples)
        encoded, client["adpcm_state"] = encode_ima_adpcm(samples, client["adpcm_state"])
        self.publish(bytes([9 if profile == "mobile" else 8])+encoded, ident)

    def publish_spectrum(self, data):
        sequence = self.waterfall_sequence
        self.waterfall_sequence = (sequence+1) & 0xffffffff
        compressed = {}
        for ident, client in tuple(self.clients.items()):
            profile = client["waterfall_profile"]
            if sequence % client["waterfall_speed"]: continue
            zoom, view_center = client["waterfall_zoom"], client["waterfall_center"]
            span = round(1024000/zoom)
            lower = round(view_center-span/2)
            start = max(0, min(len(data)-1, round((lower-6588500)/1024000*len(data))))
            end = max(start+1, min(len(data), round((lower+span-6588500)/1024000*len(data))))
            key = profile, start, end, lower, span
            if key not in compressed:
                compressed[key] = bytes([7])+encode_waterfall(data, profile, sequence, start, end, lower, span)
            self.publish(compressed[key], ident)

    def status(self):
        self.publish(json.dumps(self.info()))

    def settings_command(self, ident, settings):
        # CW's displayed frequency is the RF carrier, with a 700 Hz beat note.
        offset = settings['frequency']-7100500-(700 if settings['mode']=='CW' else 0)
        return f"set {ident} {offset} {settings['mode']} {settings['low']} {settings['high']} {settings['squelch']} {int(settings.get('notch', False))} {settings.get('nr', 0)}"

    def presence(self):
        colors = ["#ff4040", "#ffa000", "#c0c000", "#80ff00", "#00ff00", "#00bbbb", "#559fff", "#ff40ff"]
        self.publish(json.dumps({"type": "presence", "users": [
            {"id": ident, "name": c["name"] or "Anónimo", "frequency": c["settings"]["frequency"],
             "mode": c["settings"]["mode"], "color": colors[(ident-1) % len(colors)]}
            for ident, c in self.clients.items() if c["ws"].prepared]}))

    async def stats(self):
        def cpu():
            usages = [resource.getrusage(r) for r in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN)]
            # Live subprocess CPU is not included in RUSAGE_CHILDREN on Linux.
            engine = 0.0
            if self.process:
                try:
                    import os
                    fields = Path(f"/proc/{self.process.pid}/stat").read_text().rsplit(")", 1)[1].split()
                    engine = (int(fields[11])+int(fields[12])) / os.sysconf("SC_CLK_TCK")
                except (OSError, ValueError, IndexError):
                    pass
            return sum(u.ru_utime+u.ru_stime for u in usages)+engine
        previous, last, sent = cpu(), time.monotonic(), self.sent_bytes
        while True:
            await asyncio.sleep(5)
            now, current = time.monotonic(), cpu()
            elapsed = now-last
            self.metrics = {"cpu_percent": round(max(0, current-previous)/elapsed*100, 1),
                            "kbps": round((self.sent_bytes-sent)*8/elapsed/1000, 1)}
            previous, last, sent = current, now, self.sent_bytes
            levels = ["mobile", "balanced", "raw"]
            for ident, client in tuple(self.clients.items()):
                rate = round((client["sent_bytes"]-client["last_sent"])*8/elapsed/1000, 1)
                client["last_sent"] = client["sent_bytes"]
                if client["waterfall_preference"] == "auto":
                    current_level, ceiling = levels.index(client["waterfall_profile"]), levels.index(client["waterfall_ceiling"])
                    if client["congestion"] >= 2 and current_level > 0:
                        client["stable_intervals"] = 0
                        self.waterfall_profile(ident, levels[current_level-1])
                    elif client["congestion"] == 0:
                        client["stable_intervals"] += 1
                        if client["stable_intervals"] >= 6 and current_level < ceiling:
                            client["stable_intervals"] = 0
                            self.waterfall_profile(ident, levels[current_level+1])
                    else: client["stable_intervals"] = 0
                client["congestion"] = 0
                self.publish(json.dumps({"type":"stream-stats", "kbps":rate,
                    "waterfall_profile":client["waterfall_profile"],
                    "audio_profile":client["audio_profile"]}), ident)
            self.status()

    async def community(self, ident, update):
        client = self.clients[ident]
        kind = update["type"]
        if kind == "identify":
            key = update.get("key", "")
            if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key):
                raise ValueError("Sesión inválida")
            if client["key"] and client["key"] != key:
                raise ValueError("La sesión no puede cambiar")
            client["name"] = plain_text(update.get("name", ""), 32, allow_empty=True)
            client["key"] = key
            self.publish(json.dumps({"type": "identified", "id": ident, "name": client["name"]}), ident)
            self.presence()
            return
        if kind == "history":
            category, before = update.get("kind", "chat"), update.get("before", 0)
            if category not in ("chat", "log") or type(before) is not int or not 0 <= before <= 9223372036854775807:
                raise ValueError("Historial inválido")
            if time.monotonic()-client["history_last"] < .5:
                raise ValueError("Espera antes de cargar más historial")
            client["history_last"] = time.monotonic()
            async with self.community_lock:
                self.publish(json.dumps(await self.history.history(category, before)), ident)
            return
        if not client["key"]:
            raise ValueError("Identifica primero tu sesión")
        request_id = update.get("request_id", "")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise ValueError("Identificador de mensaje inválido")
        text = plain_text(update.get("text"), 500)
        call = plain_text(update.get("call", ""), 32, allow_empty=kind == "chat")
        now = time.monotonic()
        client["chat_tokens"] = min(4, client["chat_tokens"]+(now-client["chat_last"])/2)
        client["chat_last"] = now
        self.community_tokens = min(30, self.community_tokens+(now-self.community_last)*5)
        self.community_last = now
        if client["chat_tokens"] < 1 or self.community_tokens < 1:
            raise ValueError("Espera unos segundos antes de volver a publicar")
        client["chat_tokens"] -= 1
        self.community_tokens -= 1
        event = {"kind": kind, "time": int(time.time()), "name": client["name"] or "Anónimo", "text": text,
                 "frequency": round(client["settings"]["frequency"]), "mode": client["settings"]["mode"],
                 "call": call, "client_key": client["key"], "request_id": request_id}
        async with self.community_lock:
            saved, created = await self.history.append(event)
            if created:
                self.publish(json.dumps({"type": "event", "event": saved}))
            self.publish(json.dumps({"type": "ack", "request_id": request_id, "event": saved}), ident)

    async def supervise(self):
        delay = 0.25
        while True:
            process = None
            try:
                self.source = "connecting"
                self.status()
                process = await asyncio.create_subprocess_exec(
                    str(ROOT / "build/hamsdr-next-engine"),
                    "--demo" if self.args.demo else self.args.source_host,
                    "0" if self.args.demo else str(self.args.source_port),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    limit=262144)
                self.process = process
                for ident, client in tuple(self.clients.items()):
                    await self.command(self.settings_command(ident, client["settings"]))
                started = time.monotonic()
                while True:
                    header = await process.stdout.readexactly(9)
                    kind, ident, size = struct.unpack("<BII", header)
                    if size > 262144 or kind not in (1, 2, 3, 4):
                        raise ValueError("invalid engine frame")
                    data = await process.stdout.readexactly(size)
                    if kind == 4:
                        self.source = data.decode("ascii")
                        self.status()
                        if self.source == "streaming":
                            for cid, client in tuple(self.clients.items()):
                                await self.command(self.settings_command(cid, client["settings"]))
                    elif kind == 1:
                        self.last_data = time.monotonic()
                        self.publish_spectrum(data)
                    elif kind == 2:
                        self.last_data = time.monotonic()
                        self.publish_audio(ident, data)
                    else:
                        self.last_data = time.monotonic()
                        self.publish(bytes([kind]) + data, ident)
                    if time.monotonic() - started > 10:
                        delay = 0.25
            except asyncio.CancelledError:
                raise
            except (OSError, ValueError, asyncio.IncompleteReadError) as error:
                logging.warning("DSP restart: %s", error)
                self.restarts += 1
                self.source = "reconnecting"
                self.status()
            finally:
                self.process = None
                if process and process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 3)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10)

    async def lifecycle(self, app):
        await self.history.open()
        self.task = asyncio.create_task(self.supervise())
        self.stats_task = asyncio.create_task(self.stats())
        yield
        for client in tuple(self.clients.values()):
            await client["ws"].close(code=1001, message=b"Server stopping")
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task
        self.stats_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.stats_task
        await self.history.close()

    async def health(self, request):
        healthy = self.source in ("streaming", "demo")
        return web.json_response(self.info(), status=200 if healthy else 503)

    async def websocket(self, request):
        origin = request.headers.get("Origin", "")
        parsed = urlsplit(origin)
        # Explicit optional origin for proxy deployments; never rewrite Origin.
        allowed = origin == self.args.origin if self.args.origin else (
            parsed.scheme in ("http", "https") and parsed.netloc == request.host)
        if not allowed:
            raise web.HTTPForbidden(text="Origin not allowed")
        if len(self.clients) >= self.args.max_clients:
            raise web.HTTPServiceUnavailable(text="Receptor completo. Intenta más tarde.")
        # 500 Unicode characters can occupy 6000 bytes when JSON uses escaped
        # surrogate pairs. Text limits remain enforced after JSON decoding.
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=8192, compress=False)
        # Reserve the slot before the first await, including handshake processing.
        ident = self.next_id
        self.next_id += 1
        settings = {"frequency": 7100000, "mode": "LSB", "low": -2700, "high": -300, "squelch": -150, "notch": False, "nr": 0}
        queue = asyncio.Queue(maxsize=32)
        client = {"queue": queue, "reliable": asyncio.Queue(maxsize=64), "wake": asyncio.Event(),
                  "closing": False, "settings": settings, "ws": ws, "name": "", "key": "",
                  "chat_tokens": 4.0, "chat_last": time.monotonic(), "history_last": 0,
                  "waterfall_profile": "raw", "waterfall_preference": "raw", "waterfall_ceiling": "raw",
                  "waterfall_zoom": 1.0, "waterfall_center": 7100500,
                  "waterfall_speed": 1,
                  "congestion": 0, "stable_intervals": 0, "sent_bytes": 0, "last_sent": 0,
                  "audio_enabled": False, "audio_profile": "original", "adpcm_state": (0, 0)}
        self.clients[ident] = client
        sender = None
        try:
            await ws.prepare(request)
            await self.command(self.settings_command(ident, settings))
            async def send():
                try:
                    while True:
                        if client["reliable"].empty() and queue.empty():
                            client["wake"].clear()
                            await client["wake"].wait()
                        packet = (client["reliable"] if not client["reliable"].empty() else queue).get_nowait()
                        async with asyncio.timeout(3):
                            if isinstance(packet, str):
                                await ws.send_str(packet)
                            else:
                                await ws.send_bytes(packet)
                            self.sent_bytes += len(packet.encode("utf-8") if isinstance(packet, str) else packet)
                            client["sent_bytes"] += len(packet.encode("utf-8") if isinstance(packet, str) else packet)
                except (ConnectionError, asyncio.TimeoutError):
                    await ws.close()
            sender = asyncio.create_task(send())
            self.status()
            self.presence()
            async with self.community_lock:
                self.publish(json.dumps(await self.history.history("chat")), ident)
                self.publish(json.dumps(await self.history.history("log")), ident)
            tokens, last = 30.0, time.monotonic()
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                now = time.monotonic()
                tokens = min(30, tokens + (now-last)*15)
                last = now
                if tokens < 1:
                    await ws.close(code=1008, message=b"Too many controls")
                    break
                tokens -= 1
                try:
                    update = json.loads(message.data)
                    if not isinstance(update, dict):
                        raise ValueError("Control desconocido")
                    if update.get("type") in ("identify", "history", "chat", "log"):
                        await self.community(ident, update)
                        continue
                    if update.get("type") == "waterfall":
                        preference, profile = update.get("preference"), update.get("profile")
                        if preference not in ("auto", "mobile", "balanced", "raw") or profile not in ("mobile", "balanced", "raw"):
                            raise ValueError("Perfil de cascada desconocido")
                        if preference != "auto" and preference != profile:
                            raise ValueError("Perfil de cascada inconsistente")
                        self.waterfall_profile(ident, profile, preference, profile)
                        continue
                    if update.get("type") == "waterfall-view":
                        zoom, view_center = update.get("zoom"), update.get("center")
                        if type(zoom) not in (int,float) or type(view_center) not in (int,float) or not math.isfinite(zoom) or not math.isfinite(view_center) or not 1 <= zoom <= 64:
                            raise ValueError("Ventana de cascada inválida")
                        span = 1024000/zoom
                        if not 6588500+span/2 <= view_center <= 7612500-span/2:
                            raise ValueError("Ventana de cascada fuera de banda")
                        client["waterfall_zoom"], client["waterfall_center"] = float(zoom), round(view_center)
                        continue
                    if update.get("type") == "waterfall-speed":
                        speed = update.get("divisor")
                        if type(speed) is not int or speed not in (1,2,6):
                            raise ValueError("Velocidad de cascada inválida")
                        client["waterfall_speed"] = speed
                        self.publish(json.dumps({"type":"waterfall-speed", "divisor":speed,
                            "fps":{1:7.8,2:3.9,6:1.3}[speed]}), ident)
                        continue
                    if update.get("type") == "audio":
                        enabled = update.get("enabled")
                        if type(enabled) is not bool: raise ValueError("Estado de audio inválido")
                        self.audio_state(ident, enabled)
                        continue
                    if update.get("type") == "audio-profile":
                        profile = update.get("profile")
                        if profile not in ("original", "balanced", "mobile"):
                            raise ValueError("Perfil de audio desconocido")
                        self.audio_profile(ident, profile)
                        continue
                    if update.get("type") != "tune":
                        raise ValueError("Control desconocido")
                    mode = update.get("mode", settings["mode"])
                    if mode not in MODES:
                        raise ValueError("Modo desconocido")
                    low, high = MODES[mode]
                    new = {"mode": mode, "frequency": float(update.get("frequency", settings["frequency"])),
                           "low": float(update.get("low", low)), "high": float(update.get("high", high)),
                           "squelch": float(update.get("squelch", -150)),
                           "notch": update.get("notch", settings["notch"]), "nr": update.get("nr", settings["nr"])}
                    if type(new["notch"]) is not bool or type(new["nr"]) is not int or not 0 <= new["nr"] <= 4:
                        raise ValueError("Control DSP inválido")
                    numbers = [new[k] for k in ("frequency", "low", "high", "squelch")]
                    if not all(math.isfinite(x) for x in numbers):
                        raise ValueError("Número inválido")
                    if not (6600500 <= new["frequency"] <= 7600500 and -6000 <= new["low"] < new["high"] <= 6000
                            and new["high"]-new["low"] >= 100 and -150 <= new["squelch"] <= 0):
                        raise ValueError("Control fuera de rango")
                    client["settings"] = settings = new
                    await self.command(self.settings_command(ident, new))
                    self.publish(json.dumps({"type": "tuned", **new}), ident)
                    self.presence()
                except (TypeError, ValueError, OverflowError, RecursionError) as error:
                    self.publish(json.dumps({"type": "error", "message": str(error) or "Parámetros inválidos"}), ident)
                except sqlite3.Error:
                    logging.exception("History write failed")
                    self.publish(json.dumps({"type": "error", "message": "No se pudo guardar. Tu texto sigue en el formulario; vuelve a intentar."}), ident)
        finally:
            self.clients.pop(ident, None)
            if sender:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError, ConnectionError):
                    await sender
            await self.command(f"del {ident}")
            self.status()
            self.presence()
        return ws

@web.middleware
async def security(request, handler):
    response = await handler(request)
    if not response.prepared:
        response.headers.update({"X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin",
            "Cache-Control": "no-store", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'"})
    return response

GATEWAY = web.AppKey("gateway", Gateway)

def application(args):
    gateway = Gateway(args)
    app = web.Application(middlewares=[security], client_max_size=4096)
    app[GATEWAY] = gateway
    app.cleanup_ctx.append(gateway.lifecycle)
    app.router.add_get("/ws", gateway.websocket)
    app.router.add_get("/health", gateway.health)
    async def site_config(request):
        payload = json.dumps(gateway.site_config, ensure_ascii=True, separators=(",", ":"))
        return web.Response(text=f"window.hamSdrSiteConfig={payload};\n", content_type="application/javascript")
    async def site_logo(request):
        if not gateway.site_logo:
            raise web.HTTPNotFound()
        return web.FileResponse(gateway.site_logo)
    app.router.add_get("/site-config.js", site_config)
    app.router.add_get("/site-logo", site_logo)
    async def asset(request):
        name = request.match_info.get("name", "index.html")
        if name not in {"index.html", "style.css", "zoom.css", "site.js", "app.js", "audio-worklet.js", "waterfall-codec.js", "community.js", "palette.js"}:
            raise web.HTTPNotFound()
        return web.FileResponse(ROOT / "web" / name)
    app.router.add_get("/", asset)
    app.router.add_get("/{name}", asset)
    return app

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18093)
    parser.add_argument("--source-host", default="127.0.0.1")
    parser.add_argument("--source-port", type=int, default=1231)
    parser.add_argument("--max-clients", type=int, default=10)
    parser.add_argument("--origin", default="", help="Exact public HTTPS origin when behind a proxy")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--database", type=Path, default=ROOT / "var/community.sqlite3")
    parser.add_argument("--retention-days", type=int, default=90)
    parser.add_argument("--site-config", type=Path, help="Site identity JSON (defaults to ./site.json, then generic example)")
    args = parser.parse_args()
    if not 1 <= args.retention_days <= 3650:
        parser.error("retention-days: 1..3650")
    ipaddress.ip_address(args.source_host)
    if not 1 <= args.source_port <= 65535 or not 1 <= args.port <= 65535 or not 1 <= args.max_clients <= 20:
        parser.error("Ports: 1..65535; max clients: 1..20")
    web.run_app(application(args), host=args.bind, port=args.port, access_log=None)
