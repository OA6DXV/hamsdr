# SPDX-License-Identifier: GPL-3.0-only
"""Dependency-free HamSDR WSS load client for disposable remote execution."""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import ssl
import struct
import time
import math
from urllib.parse import urlsplit


class WebSocket:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer

    @classmethod
    async def connect(cls, url, origin=None):
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        ssl_context = ssl.create_default_context() if parsed.scheme == "wss" else None
        started = time.monotonic()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ssl_context,
                                    server_hostname=host if ssl_context else None), 10)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        origin = origin or f"{'https' if parsed.scheme == 'wss' else 'http'}://{parsed.netloc}"
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {parsed.netloc}\r\n"
            f"Origin: {origin}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        writer.write(request.encode("ascii"))
        await writer.drain()
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        lines = header.decode("latin1").split("\r\n")
        if not lines[0].startswith("HTTP/1.1 101"):
            raise RuntimeError(lines[0])
        headers = {line.split(":", 1)[0].lower(): line.split(":", 1)[1].strip()
                   for line in lines[1:] if ":" in line}
        expected = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != expected:
            raise RuntimeError("invalid WebSocket accept")
        return cls(reader, writer), (time.monotonic() - started) * 1000

    async def send(self, payload, opcode=1):
        if isinstance(payload, str):
            payload = payload.encode()
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length < 65536:
            header = bytes((0x80 | opcode, 0xFE)) + struct.pack("!H", length)
        else:
            header = bytes((0x80 | opcode, 0xFF)) + struct.pack("!Q", length)
        masked = bytes(value ^ mask[index & 3] for index, value in enumerate(payload))
        self.writer.write(header + mask + masked)
        await self.writer.drain()

    async def json(self, value):
        await self.send(json.dumps(value, separators=(",", ":")))

    async def receive(self):
        first, second = await self.reader.readexactly(2)
        opcode, length = first & 0x0F, second & 0x7F
        if length == 126:
            length = struct.unpack("!H", await self.reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", await self.reader.readexactly(8))[0]
        if second & 0x80:
            mask = await self.reader.readexactly(4)
        else:
            mask = None
        payload = await self.reader.readexactly(length)
        if mask:
            payload = bytes(value ^ mask[index & 3] for index, value in enumerate(payload))
        if opcode == 9:
            await self.send(payload, 10)
            return await self.receive()
        return opcode, payload

    async def close(self):
        try:
            await self.send(struct.pack("!H", 1000), 8)
        except (ConnectionError, asyncio.CancelledError):
            pass
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError, ssl.SSLError):
            pass


def percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)]


async def listener(url, duration, churn, settle, index, read_delay, origin):
    ws, handshake_ms = await WebSocket.connect(url, origin)
    revision = 100000 + index * 10000
    await ws.json({"type": "hello", "protocol": 2, "waterfall": 2})
    await ws.json({"type": "waterfall", "preference": "high", "profile": "high"})
    await ws.json({"type": "waterfall-speed", "divisor": "high"})
    await ws.json({"type": "audio-profile", "profile": "raw"})
    await ws.json({"type": "audio", "enabled": True})
    sent_at = time.monotonic()
    await ws.json({"type": "waterfall-view", "zoom": 1, "center": 7100500,
                   "revision": revision, "rows": 600, "history": True})
    churn_deadline = time.monotonic() + duration
    deadline = churn_deadline + settle
    next_churn = time.monotonic() + 0.3
    binary_bytes = waterfall = raw_audio = opus_audio = snapshots = errors = 0
    error_messages = []
    controls = {}
    audio_last = None
    audio_gaps = []
    control_rtts = []
    control_sent_at = None
    next_control = time.monotonic() + 1.0
    snapshot_ms = None
    if read_delay:
        await asyncio.sleep(read_delay)
    receive_task = asyncio.create_task(ws.receive())
    try:
      while time.monotonic() < deadline:
        if churn and time.monotonic() < churn_deadline and time.monotonic() >= next_churn:
            revision += 1
            center = 7100500 + ((revision % 5) - 2) * 50000
            await ws.json({"type": "waterfall-view", "zoom": 4, "center": center,
                           "revision": revision, "rows": 600, "history": True})
            sent_at = time.monotonic()
            next_churn += 0.3
        if time.monotonic() >= next_control and control_sent_at is None:
            control_sent_at = time.monotonic()
            await ws.json({"type": "tune", "frequency": 7100000, "mode": "LSB",
                           "low": -2700, "high": -300, "squelch": -150,
                           "notch": False, "nr": 0})
            next_control += 1.0
        timeout = min(0.1 if churn else 1.0, max(0.01, deadline - time.monotonic()))
        done, _ = await asyncio.wait((receive_task,), timeout=timeout)
        if not done:
            continue
        opcode, payload = receive_task.result()
        receive_task = asyncio.create_task(ws.receive())
        if opcode == 2 and payload:
            binary_bytes += len(payload)
            if payload[0] == 7:
                waterfall += 1
            elif payload[0] == 2:
                now = time.monotonic()
                raw_audio += 1
                if audio_last is not None:
                    audio_gaps.append((now - audio_last) * 1000)
                audio_last = now
            elif payload[0] in (10, 11):
                now = time.monotonic()
                opus_audio += 1
                if audio_last is not None:
                    audio_gaps.append((now - audio_last) * 1000)
                audio_last = now
            elif payload[0] == 13:
                snapshots += 1
                received_revision = struct.unpack_from("<I", payload, 1)[0]
                if received_revision == revision:
                    snapshot_ms = (time.monotonic() - sent_at) * 1000
        elif opcode == 1:
            try:
                message = json.loads(payload)
                kind = message.get("type", "unknown")
                controls[kind] = controls.get(kind, 0) + 1
                if kind == "tuned" and control_sent_at is not None:
                    control_rtts.append((time.monotonic() - control_sent_at) * 1000)
                    control_sent_at = None
                if kind == "error":
                    errors += 1
                    if len(error_messages) < 5:
                        error_messages.append(str(message.get("message", ""))[:160])
            except (UnicodeDecodeError, json.JSONDecodeError):
                errors += 1
        elif opcode == 8:
            break
    finally:
        if not receive_task.done():
            receive_task.cancel()
        try:
            await receive_task
        except (asyncio.CancelledError, ConnectionError, OSError, ssl.SSLError):
            pass
    await ws.close()
    return {"handshake_ms": round(handshake_ms, 1), "bytes": binary_bytes,
            "waterfall": waterfall, "raw_audio": raw_audio, "opus_audio": opus_audio,
            "snapshots": snapshots,
            "snapshot_ms": round(snapshot_ms, 1) if snapshot_ms is not None else None,
            "audio_gap_p95_ms": round(percentile(audio_gaps, 95), 1) if audio_gaps else None,
            "audio_gap_p99_ms": round(percentile(audio_gaps, 99), 1) if audio_gaps else None,
            "audio_max_gap_ms": round(max(audio_gaps), 1) if audio_gaps else 0.0,
            "control_samples": len(control_rtts),
            "control_rtt_p95_ms": round(percentile(control_rtts, 95), 1) if control_rtts else None,
            "control_rtt_p99_ms": round(percentile(control_rtts, 99), 1) if control_rtts else None,
            "control_rtt_max_ms": round(max(control_rtts), 1) if control_rtts else None,
            "read_delay_s": read_delay, "errors": errors,
            "error_messages": error_messages, "controls": controls}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="wss://hamsdrdev.republicaoa6.com/ws")
    parser.add_argument("--clients", type=int, default=1)
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--churn", action="store_true")
    parser.add_argument("--settle", type=float, default=0)
    parser.add_argument("--read-delay", type=float, default=0,
                        help="Do not read after handshake for this many seconds")
    parser.add_argument("--origin", default=None)
    args = parser.parse_args()
    results = await asyncio.gather(*(
        listener(args.url, args.seconds, args.churn, args.settle, index,
                 args.read_delay, args.origin) for index in range(args.clients)),
        return_exceptions=True)
    output = []
    for result in results:
        output.append({"fatal": f"{type(result).__name__}: {result}"}
                      if isinstance(result, BaseException) else result)
    print(json.dumps(output, separators=(",", ":")))


if __name__ == "__main__":
    asyncio.run(main())
