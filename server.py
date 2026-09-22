# SPDX-License-Identifier: GPL-3.0-only
"""Small HTTP/WebSocket gateway. DSP stays in one shared C++ subprocess."""
import argparse
import asyncio
from collections import OrderedDict
import contextlib
import ipaddress
import json
import logging
import math
import re
import resource
import sqlite3
import ssl
from pathlib import Path
import struct
import time
import tomllib
from urllib.parse import urlsplit

from aiohttp import web, WSMsgType
from community import History, plain_text
from opus_codec import OPUS_AVAILABLE, OpusEncoder
from waterfall_codec import VERSION as WATERFALL_PROTOCOL_VERSION, encode as encode_waterfall

ROOT = Path(__file__).resolve().parent
VERSION = "0.5.2-dev"
PROTOCOL_VERSION = 1
MODES = {"USB": (300, 2700), "LSB": (-2700, -300), "AM": (-4000, 4000),
         "CW": (450, 950), "NFM": (-5000, 5000)}
DIGITAL_AUDIO_RATE = 12000
DIGITAL_PACKET_KIND = 12

def read_site_config(requested=None):
    """Read the installation-specific configuration outside application code."""
    path = Path(requested).expanduser() if requested else ROOT / "site.toml"
    if not path.exists() and not requested:
        legacy = ROOT / "site.json"
        path = legacy if legacy.exists() else ROOT / "site.example.toml"
    try:
        if path.suffix.lower() == ".toml":
            with path.open("rb") as stream:
                data = tomllib.load(stream)
        elif path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            raise ValueError("site config: file must use .toml or .json")
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"site config: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("site config: root must be an object")
    return data, path

def working_directory(data, config_path):
    """Return the one absolute directory allowed to hold installation files."""
    value = data.get("working_directory", str(config_path.parent))
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("site config: invalid working_directory")
    root = Path(value).expanduser()
    if not root.is_absolute():
        raise ValueError("site config: working_directory must be absolute")
    return root.resolve()

def working_file(root, value, label, extensions=None):
    """Resolve one direct child of the working directory without path traversal."""
    if value in (None, ""):
        return None
    if not isinstance(value, str) or "\0" in value:
        raise ValueError(f"site config: invalid {label}")
    name = Path(value)
    if name.is_absolute() or len(name.parts) != 1 or name.name in ("", ".", ".."):
        raise ValueError(f"site config: {label} must be a filename in working_directory")
    if extensions and name.suffix.lower() not in extensions:
        raise ValueError(f"site config: invalid {label} file type")
    candidate = root / name.name
    if candidate.is_symlink():
        raise ValueError(f"site config: {label} must not be a symbolic link")
    return candidate

def load_station_config(data):
    """Validate station metadata, indexed bands and ReceiverBook settings."""
    station = data.get("station", {})
    if not isinstance(station, dict):
        raise ValueError("site config: invalid station")

    def optional_text(source, name, limit):
        value = source.get(name, "")
        if (not isinstance(value, str) or len(value) > limit or
                any(ord(character) < 32 for character in value)):
            raise ValueError(f"site config: invalid {name}")
        return value.strip()

    qth = optional_text(station, "qth", 8)
    if qth and not re.fullmatch(r"[A-Ra-r]{2}[0-9]{2}(?:[A-Xa-x]{2})?", qth):
        raise ValueError("site config: invalid station qth")
    station_data = {
        "qth": qth,
        "description": optional_text(station, "description", 240),
        "email": optional_text(station, "email", 254),
        "mobile_page": optional_text(station, "mobile_page", 120) or "/",
        "flag": optional_text(station, "flag", 255),
        "flag_description": optional_text(station, "flag_description", 120) or "Station flag",
    }

    raw_bands = data.get("band", {})
    if not isinstance(raw_bands, dict):
        raise ValueError("site config: invalid band sections")
    indexes = []
    for key in raw_bands:
        if not isinstance(key, str) or not key.isdecimal():
            raise ValueError("site config: band indexes must be non-negative integers")
        indexes.append(int(key))
    indexes.sort()
    if indexes and indexes != list(range(indexes[-1] + 1)):
        raise ValueError("site config: band indexes must be contiguous from 0")
    bands = []
    for index in indexes:
        source = raw_bands[str(index)]
        if not isinstance(source, dict):
            raise ValueError(f"site config: invalid band.{index}")
        enabled = source.get("enable", False)
        if not isinstance(enabled, bool):
            raise ValueError(f"site config: band.{index}.enable must be true or false")
        name = optional_text(source, "name", 80)
        antenna = optional_text(source, "antenna", 160)
        center = source.get("center_frequency_khz", 0.0)
        sample_rate = source.get("sample_rate_khz", 0.0)
        receiver_type = source.get("receiver_type", "rtltcp")
        host = source.get("receiver_host", "127.0.0.1")
        port = source.get("receiver_port", 1231)
        gain = source.get("receiver_gain", "auto")
        if gain != "auto" and (isinstance(gain, bool) or not isinstance(gain, (int, float)) or
                               not math.isfinite(gain)):
            raise ValueError(f"site config: band.{index} receiver_gain must be auto or a finite dB value")
        if enabled:
            if not name or not antenna:
                raise ValueError(f"site config: enabled band.{index} requires name and antenna")
            if (isinstance(center, bool) or not isinstance(center, (int, float)) or
                    not math.isfinite(center) or center <= 0):
                raise ValueError(f"site config: invalid band.{index} center frequency")
            if (isinstance(sample_rate, bool) or not isinstance(sample_rate, (int, float)) or
                    not math.isfinite(sample_rate) or sample_rate != 1024.0):
                raise ValueError(f"site config: band.{index} sample rate must currently be 1024.0 kHz")
            if receiver_type != "rtltcp":
                raise ValueError(f"site config: band.{index} receiver type must currently be rtltcp")
            if not isinstance(host, str):
                raise ValueError(f"site config: invalid band.{index} receiver host")
            try:
                host = ipaddress.ip_address(host.strip()).compressed
            except ValueError as error:
                raise ValueError(f"site config: invalid band.{index} receiver host") from error
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise ValueError(f"site config: invalid band.{index} receiver port")
        bands.append({
            "index": index, "enable": enabled, "name": name,
            "center_frequency_khz": float(center), "sample_rate_khz": float(sample_rate),
            "antenna": antenna, "receiver_type": receiver_type,
            "receiver_host": host, "receiver_port": port,
            "receiver_gain": "auto" if gain == "auto" else float(gain),
        })

    # Keep installations using the previous single [receiver] section readable
    # until their external configuration is migrated.
    if not bands and isinstance(data.get("receiver"), dict):
        receiver = data["receiver"]
        bands = [{
            "index": 0, "enable": True, "name": "40m",
            "center_frequency_khz": 7100.5, "sample_rate_khz": 1024.0,
            "antenna": "Receiver antenna", "receiver_type": receiver.get("type", "rtltcp"),
            "receiver_host": receiver.get("host", "127.0.0.1"),
            "receiver_port": receiver.get("port", 1231), "receiver_gain": "auto",
        }]

    receiverbook = data.get("receiverbook", {})
    if not isinstance(receiverbook, dict):
        raise ValueError("site config: invalid receiverbook")
    receiverbook_enabled = receiverbook.get("enable", False)
    receiverbook_tag = receiverbook.get("tag", "")
    if not isinstance(receiverbook_enabled, bool) or not isinstance(receiverbook_tag, str):
        raise ValueError("site config: invalid receiverbook settings")
    confirmation = ""
    if receiverbook_enabled:
        match = re.fullmatch(
            r'\s*<meta\s+name=["\']receiverbook-confirmation["\']\s+'
            r'content=["\']([0-9a-f]{64})["\']\s*/?>\s*', receiverbook_tag)
        if not match:
            raise ValueError("site config: receiverbook tag must contain one valid confirmation meta tag")
        confirmation = match.group(1)
        if not station_data["description"] or not station_data["email"]:
            raise ValueError("site config: ReceiverBook requires station description and email")
    return station_data, bands, {
        "enable": receiverbook_enabled, "tag": receiverbook_tag.strip(), "confirmation": confirmation,
    }

def load_runtime_config(requested=None, allow_unconfigured=False):
    """Return validated private server, receiver and storage settings."""
    data, path = read_site_config(requested)
    root = working_directory(data, path)
    server = data.get("server", {})
    if not isinstance(server, dict):
        raise ValueError("site config: invalid server")
    bind = server.get("bind", "127.0.0.1")
    port = server.get("port", 18093)
    trusted_proxy = server.get("trusted_proxy", "")
    max_clients = server.get("max_clients", 10)
    max_clients_per_ip = server.get("max_clients_per_ip", 3)
    full_quality_sessions_per_ip = server.get("full_quality_sessions_per_ip", 2)
    max_bandwidth_kbps_per_ip = server.get("max_bandwidth_kbps_per_ip", 1000)
    digimodes = server.get("digimodes", False)
    secure = data.get("secure", server.get("tls", {}))
    if not isinstance(bind, str):
        raise ValueError("site config: invalid server bind")
    try:
        bind = ipaddress.ip_address(bind.strip()).compressed
    except ValueError as error:
        raise ValueError("site config: server bind must be an IP address") from error
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("site config: server port must be 1..65535")
    if not isinstance(trusted_proxy, str):
        raise ValueError("site config: invalid trusted proxy")
    if trusted_proxy:
        try:
            trusted_proxy = ipaddress.ip_address(trusted_proxy.strip()).compressed
        except ValueError as error:
            raise ValueError("site config: trusted proxy must be an IP address") from error
    if (isinstance(max_clients, bool) or not isinstance(max_clients, int) or
            isinstance(max_clients_per_ip, bool) or not isinstance(max_clients_per_ip, int) or
            not 1 <= max_clients <= 20 or not 1 <= max_clients_per_ip <= max_clients):
        raise ValueError("site config: clients must be 1..20 and per-IP must not exceed total")
    if (isinstance(full_quality_sessions_per_ip, bool) or
            not isinstance(full_quality_sessions_per_ip, int) or
            not 1 <= full_quality_sessions_per_ip <= max_clients_per_ip):
        raise ValueError("site config: full-quality sessions must be 1..max_clients_per_ip")
    if (isinstance(max_bandwidth_kbps_per_ip, bool) or
            not isinstance(max_bandwidth_kbps_per_ip, int) or
            not 1 <= max_bandwidth_kbps_per_ip <= 1000000):
        raise ValueError("site config: per-IP bandwidth maximum must be 1..1000000 kb/s")
    if not isinstance(digimodes, bool):
        raise ValueError("site config: digimodes must be true or false")
    if not isinstance(secure, dict):
        raise ValueError("site config: invalid secure")
    security_mode = secure.get("mode")
    if security_mode is None:
        legacy_mode = secure.get("enable", secure.get("enabled", False))
        security_mode = "proxy" if legacy_mode == "proxy" else "native" if legacy_mode is True else "insecure"
    tls_certificate = secure.get("certificate", "")
    tls_private_key = secure.get("private_key", "")
    secure_origin = secure.get("origin", server.get("origin", ""))
    if security_mode not in ("insecure", "native", "proxy"):
        raise ValueError('site config: secure mode must be "insecure", "native" or "proxy"')
    tls_enabled = security_mode == "native"
    if not isinstance(tls_certificate, str) or not isinstance(tls_private_key, str):
        raise ValueError("site config: invalid TLS certificate path")
    if not isinstance(secure_origin, str):
        raise ValueError("site config: invalid secure origin")
    if secure_origin and secure_origin != "*":
        parsed = urlsplit(secure_origin)
        if (parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path or
                parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise ValueError("site config: secure origin must be an exact http(s) origin")
    tls_certificate = working_file(root, tls_certificate, "TLS certificate")
    tls_private_key = working_file(root, tls_private_key, "TLS private key")
    if tls_enabled and (not tls_certificate or not tls_private_key):
        raise ValueError("site config: native secure mode requires certificate and private_key")

    station, bands, receiverbook = load_station_config(data)
    enabled_bands = [band for band in bands if band["enable"]]
    if not enabled_bands and allow_unconfigured:
        enabled_bands = [{
            "index": 0, "enable": True, "name": "demo",
            "center_frequency_khz": 7100.5, "sample_rate_khz": 1024.0,
            "antenna": "Demo source", "receiver_type": "rtltcp",
            "receiver_host": "127.0.0.1", "receiver_port": 1234,
            "receiver_gain": "auto",
        }]
    if len(enabled_bands) != 1:
        raise ValueError("site config: exactly one band must be enabled by the current receiver engine")
    band = enabled_bands[0]
    receiver_type = band["receiver_type"]
    source_host = band["receiver_host"]
    source_port = band["receiver_port"]
    gain = band["receiver_gain"]
    gain_mode = "auto" if gain == "auto" else "manual"
    gain_db = 0.0 if gain == "auto" else gain
    scaled_gain = gain_db * 10
    gain_tenth_db = (math.floor(scaled_gain + 0.5) if scaled_gain >= 0
                     else math.ceil(scaled_gain - 0.5))
    if not -2147483648 <= gain_tenth_db <= 2147483647:
        raise ValueError("site config: gain exceeds the rtl_tcp signed 32-bit protocol field")
    center_frequency = round(band["center_frequency_khz"] * 1000)
    sample_rate = round(band["sample_rate_khz"] * 1000)

    storage = data.get("storage", {})
    if not isinstance(storage, dict):
        raise ValueError("site config: invalid storage")
    database = storage.get("database", "community.sqlite3")
    max_size_mb = storage.get("max_size_mb", 50)
    if not isinstance(database, str) or not database.strip() or "\0" in database:
        raise ValueError("site config: invalid database path")
    database = working_file(root, database, "database", {".sqlite", ".sqlite3", ".db"})
    if (isinstance(max_size_mb, bool) or not isinstance(max_size_mb, int) or
            not 1 <= max_size_mb <= 1048576):
        raise ValueError("site config: database maximum must be 1..1048576 MB")
    return {
        "working_directory": root,
        "bind": bind, "port": port, "origin": secure_origin if security_mode != "insecure" else "",
        "trusted_proxy": trusted_proxy,
        "max_clients": max_clients, "max_clients_per_ip": max_clients_per_ip,
        "full_quality_sessions_per_ip": full_quality_sessions_per_ip,
        "max_bandwidth_kbps_per_ip": max_bandwidth_kbps_per_ip,
        "digimodes": digimodes,
        "security_mode": security_mode, "tls_enabled": tls_enabled, "tls_certificate": tls_certificate,
        "tls_private_key": tls_private_key,
        "receiver_type": receiver_type, "source_host": source_host, "source_port": source_port,
        "receiver_gain": gain, "gain_mode": gain_mode, "gain_tenth_db": gain_tenth_db,
        "center_frequency": center_frequency, "sample_rate": sample_rate,
        "initial_frequency": round(center_frequency / 1000) * 1000,
        "station": station, "bands": bands, "receiverbook": receiverbook,
        "database": database, "max_size_mb": max_size_mb,
    }

def load_listen_config(requested=None):
    """Compatibility helper returning the configured HTTP listener."""
    config = load_runtime_config(requested)
    return config["bind"], config["port"]

def create_tls_context(args):
    """Build the optional HTTPS/WSS server context from validated settings."""
    if not args.tls_enabled:
        return None
    if not args.tls_certificate or not args.tls_private_key:
        raise ValueError("TLS requires both certificate and private_key")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    try:
        context.load_cert_chain(args.tls_certificate, args.tls_private_key)
    except (OSError, ssl.SSLError) as error:
        raise ValueError(f"TLS certificate: {error}") from error
    return context

def load_site_config(requested=None):
    """Load operator branding separately from application code and assets."""
    data, path = read_site_config(requested)
    root = working_directory(data, path)
    html = data.get("html", data)
    if not isinstance(html, dict):
        raise ValueError("site config: invalid html")

    def text(name, limit, default=""):
        value = html.get(name, default)
        if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
            raise ValueError(f"site config: invalid {name}")
        return value.strip()

    description = html.get("description", [])
    if not isinstance(description, list) or len(description) > 4 or any(
            not isinstance(line, str) or not line.strip() or len(line) > 240 or any(ord(c) < 32 for c in line)
            for line in description):
        raise ValueError("site config: invalid description")
    legacy_administrator = data.get("administrator", {})
    legacy_callsign = legacy_administrator.get("name", "Administrator") if isinstance(legacy_administrator, dict) else "Administrator"
    callsign = text("callsign", 80, legacy_callsign)
    show_admin = html.get("show_admin", True)
    if not isinstance(show_admin, bool):
        raise ValueError("site config: show_admin must be true or false")
    server = data.get("server", {})
    digimodes = server.get("digimodes", False) if isinstance(server, dict) else False
    if not isinstance(digimodes, bool):
        raise ValueError("site config: digimodes must be true or false")

    station, bands, receiverbook = load_station_config(data)
    legacy_logo = data.get("logo", {})
    if not isinstance(legacy_logo, dict):
        raise ValueError("site config: invalid logo")
    logo_file = station["flag"] or legacy_logo.get("file", "")
    logo_alt = station["flag_description"] or legacy_logo.get("alt", "Station flag")
    logo_path = working_file(root, logo_file, "station flag", {".svg", ".png", ".jpg", ".jpeg", ".webp"})
    if logo_path and (not logo_path.is_file() or logo_path.stat().st_size > 2_000_000):
        raise ValueError("site config: station flag must be an SVG, PNG, JPEG or WebP file up to 2 MB")

    public = {
        "receiver_name": text("receiver_name", 120, "HamSDR"),
        "description": [line.strip() for line in description],
        "callsign": callsign,
        "show_admin": show_admin,
        "digimodes": digimodes,
        "version": VERSION,
        "logo": {"enabled": logo_path is not None, "url": "./site-logo" if logo_path else "", "alt": logo_alt},
    }
    return public, logo_path, station, bands, receiverbook

class Gateway:
    def __init__(self, args):
        self.args = args
        self.center_frequency = int(getattr(args, "center_frequency", 7100500))
        self.sample_rate = int(getattr(args, "sample_rate", 1024000))
        self.band_lower = self.center_frequency - self.sample_rate // 2
        self.band_upper = self.center_frequency + self.sample_rate // 2
        self.initial_frequency = int(getattr(args, "initial_frequency",
                                             round(self.center_frequency / 1000) * 1000))
        self.gain_mode = getattr(args, "gain_mode", "auto")
        self.gain_tenth_db = int(getattr(args, "gain_tenth_db", 0))
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
                               getattr(args, "max_size_mb", 50))
        self.community_lock = asyncio.Lock()
        self.community_tokens, self.community_last = 30.0, time.monotonic()
        self.sent_bytes = 0
        self.metrics = {"cpu_percent": 0, "kbps": 0}
        self.stats_task = None
        self.stopping = False
        self.waterfall_sequence = 0
        self.connection_attempts = OrderedDict()
        self.address_limit_stage = {}
        self.address_bandwidth_samples = {}
        (self.site_config, self.site_logo, self.station,
         self.bands, self.receiverbook) = load_site_config(getattr(args, "site_config", None))

    def client_address(self, request):
        """Use a proxy-supplied address only when the TCP peer is explicitly trusted."""
        peer = request.remote or "unknown"
        trusted = getattr(self.args, "trusted_proxy", "")
        candidate = request.headers.get("X-HamSDR-Client-IP", "") if trusted and peer == trusted else peer
        try:
            return ipaddress.ip_address(candidate).compressed
        except ValueError:
            return "unknown"

    def allow_connection_attempt(self, address):
        """Bound reconnect/open floods without retaining an unbounded IP table."""
        now = time.monotonic()
        tokens, previous = self.connection_attempts.pop(address, (6.0, now))
        tokens = min(6.0, tokens + (now-previous)/5.0)
        allowed = tokens >= 1.0
        self.connection_attempts[address] = (tokens-1.0 if allowed else tokens, now)
        while len(self.connection_attempts) > 4096:
            self.connection_attempts.popitem(last=False)
        return allowed

    def info(self):
        return {"type": "status", "version": VERSION, "source": self.source, "users": len(self.clients),
                "center": self.center_frequency, "sample_rate": self.sample_rate,
                "initial_frequency": self.initial_frequency, "audio_rate": 16000,
                "digital_audio_rate": DIGITAL_AUDIO_RATE,
                "digimodes": bool(getattr(self.args, "digimodes", False)),
                "audio_profiles": {"raw": 16000, "balanced": 16000, "mobile": 16000,
                                   "digiraw": DIGITAL_AUDIO_RATE},
                "opus_available": OPUS_AVAILABLE,
                "fft_size": 65536, "demo": getattr(self.args, "demo", False), "restarts": self.restarts,
                "dropped": self.dropped, **self.metrics}

    def receiverbook_status(self):
        """Return the legacy receiver-directory station discovery document."""
        bands = [band for band in self.bands if band["enable"]]
        lines = [
            f"Description: {self.station['description']}",
            f"Email: {self.station['email']}",
            f"Qth: {self.station['qth']}",
            f"Users: {len(self.clients)}",
            f"Bands: {len(bands)}",
        ]
        for position, band in enumerate(bands):
            lines.append(
                f"Band: {position} {band['center_frequency_khz']:.3f} "
                f"{band['sample_rate_khz']:.3f} {band['antenna']}")
        return "\n".join(lines) + "\n"

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
            if not isinstance(packet, str) and not client.get("protocol_ready", False):
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
        client["waterfall_phase"] = 0
        client["waterfall_emitted"] = 0
        reset_speed = profile != "high" and client["waterfall_speed"] == "high"
        if reset_speed: client["waterfall_speed"] = 1
        if preference is not None: client["waterfall_preference"] = preference
        if ceiling is not None: client["waterfall_ceiling"] = ceiling
        self.publish(json.dumps({"type":"waterfall-profile", "preference":client["waterfall_preference"],
            "profile":profile}), ident)
        if reset_speed:
            self.publish(json.dumps({"type":"waterfall-speed", "divisor":1, "fps":7.8}), ident)

    def address_clients(self, address):
        return [(ident, client) for ident, client in self.clients.items()
                if client["address"] == address]

    def address_profile_limits(self, address):
        """Return the highest profiles currently allowed for one public address."""
        crowded = len(self.address_clients(address)) > getattr(
            self.args, "full_quality_sessions_per_ip", 2)
        stage = self.address_limit_stage.get(address, 0)
        if stage >= 3:
            return "slow", "mobile", True
        if stage >= 2:
            return "low", "mobile", True
        if crowded or stage >= 1:
            return "balanced", "balanced", True
        return "high", "raw", False

    def publish_address_policy(self, address, rate=None):
        """Tell every session which profiles its shared address may select."""
        waterfall_max, audio_max, restricted = self.address_profile_limits(address)
        sessions = len(self.address_clients(address))
        crowded = sessions > getattr(self.args, "full_quality_sessions_per_ip", 2)
        bandwidth_limited = self.address_limit_stage.get(address, 0) > 0
        if bandwidth_limited and crowded:
            reason = "sessions-and-bandwidth"
            message = "Calidad limitada por sesiones múltiples y tráfico agregado de esta red."
        elif bandwidth_limited:
            reason = "bandwidth"
            message = "Calidad limitada porque esta red superó el máximo de tráfico agregado."
        elif crowded:
            reason = "sessions"
            message = "Calidad limitada porque esta red tiene más de dos sesiones activas."
        else:
            reason, message = "none", ""
        signature = (restricted, waterfall_max, audio_max, reason)
        for ident, client in self.address_clients(address):
            if client.get("resource_policy") == signature:
                continue
            client["resource_policy"] = signature
            self.publish(json.dumps({"type":"resource-policy", "restricted":restricted,
                "reason":reason, "message":message, "sessions":sessions,
                "waterfall_max":waterfall_max, "audio_max":audio_max,
                "measured_kbps":round(rate, 1) if rate is not None else None,
                "max_kbps":getattr(self.args, "max_bandwidth_kbps_per_ip", 1000)}), ident)

    def enforce_address_limits(self, address, notify=True):
        """Reduce expensive profiles without dropping an otherwise healthy session."""
        waterfall_max, audio_max, restricted = self.address_profile_limits(address)
        waterfall_levels = ["slow", "low", "balanced", "high"]
        audio_levels = ["mobile", "balanced", "digiraw", "raw"]
        changed = []
        for ident, client in self.address_clients(address):
            altered = False
            if waterfall_levels.index(client["waterfall_profile"]) > waterfall_levels.index(waterfall_max):
                self.waterfall_profile(ident, waterfall_max, waterfall_max, waterfall_max)
                altered = True
            if audio_levels.index(client["audio_profile"]) > audio_levels.index(audio_max):
                if audio_max in ("balanced", "mobile") and not OPUS_AVAILABLE:
                    self.audio_state(ident, False)
                else:
                    self.audio_profile(ident, audio_max)
                altered = True
            if altered:
                changed.append(ident)
        if notify and changed:
            message = ("Esta red alcanzó su límite compartido de recursos; "
                       f"se aplicaron cascada {waterfall_max} y audio {audio_max}.")
            for ident in changed:
                self.publish(json.dumps({"type":"resource-limit", "message":message,
                    "waterfall_max":waterfall_max, "audio_max":audio_max,
                    "restricted":restricted}), ident)
        self.publish_address_policy(address)

    def observe_address_bandwidth(self, address, rate):
        """Apply three-sample activation and release hysteresis to one address."""
        maximum = getattr(self.args, "max_bandwidth_kbps_per_ip", 1000)
        state = self.address_bandwidth_samples.setdefault(address, {"over": 0, "under": 0})
        stage = self.address_limit_stage.get(address, 0)
        changed = False
        if rate > maximum:
            state["over"] += 1
            state["under"] = 0
            if state["over"] >= 3:
                new_stage = min(3, stage + 1)
                changed = new_stage != stage
                self.address_limit_stage[address] = new_stage
                state["over"] = 0
        elif rate < maximum * 0.8:
            state["under"] += 1
            state["over"] = 0
            if state["under"] >= 3:
                new_stage = max(0, stage - 1)
                changed = new_stage != stage
                if new_stage:
                    self.address_limit_stage[address] = new_stage
                else:
                    self.address_limit_stage.pop(address, None)
                state["under"] = 0
        else:
            state["over"] = state["under"] = 0
        if changed:
            self.enforce_address_limits(address)
        else:
            self.publish_address_policy(address, rate)

    def audio_state(self, ident, enabled):
        client = self.clients.get(ident)
        if not client: return
        client["audio_enabled"] = enabled
        if not enabled:
            self.reset_audio_codec(client)
            self.reset_digital_audio(client)
            retained = []
            while not client["queue"].empty():
                packet = client["queue"].get_nowait()
                if not isinstance(packet, bytes) or not packet or packet[0] not in (2, 10, 11, DIGITAL_PACKET_KIND):
                    retained.append(packet)
            for packet in retained: client["queue"].put_nowait(packet)
        self.publish(json.dumps({"type":"audio-state", "enabled":enabled,
            "profile":client["audio_profile"]}), ident)

    def audio_profile(self, ident, profile):
        client = self.clients.get(ident)
        if not client: return
        if profile in ("balanced", "mobile") and not OPUS_AVAILABLE:
            raise ValueError("Opus no está disponible en el servidor")
        client["audio_profile"] = profile
        self.reset_audio_codec(client)
        retained = []
        while not client["queue"].empty():
            packet = client["queue"].get_nowait()
            if not isinstance(packet, bytes) or not packet or packet[0] not in (2, 10, 11, DIGITAL_PACKET_KIND):
                retained.append(packet)
        for packet in retained: client["queue"].put_nowait(packet)
        details = {"raw":("pcm16",16000,256000), "balanced":("opus",16000,32000),
                   "mobile":("opus",16000,12000),
                   "digiraw":("pcm16",DIGITAL_AUDIO_RATE,DIGITAL_AUDIO_RATE*16)}[profile]
        self.publish(json.dumps({"type":"audio-profile", "profile":profile,
            "codec":details[0], "rate":details[1], "bitrate":details[2]}), ident)

    @staticmethod
    def reset_audio_codec(client):
        encoder = client.get("opus_encoder")
        if encoder:
            encoder.close()
        client["opus_encoder"] = None
        client["opus_pending"] = bytearray()
        client["opus_sequence"] = 0

    @staticmethod
    def reset_digital_audio(client):
        client["digital_tail"] = bytearray()
        client["digital_sequence"] = 0

    def digital_mode(self, ident, mode):
        client = self.clients.get(ident)
        if not client:
            return
        if mode not in (None, "FT8", "FT4"):
            raise ValueError("Modo digital desconocido")
        if mode and not getattr(self.args, "digimodes", False):
            raise ValueError("Digimodos no están habilitados en este receptor")
        if mode:
            audio_max = self.address_profile_limits(client["address"])[1]
            levels = ("mobile", "balanced", "digiraw", "raw")
            if levels.index("digiraw") > levels.index(audio_max):
                raise ValueError(f"Esta red permite audio hasta {audio_max}")
        if client["digital_mode"] != mode:
            self.reset_digital_audio(client)
        client["digital_mode"] = mode
        if mode and client["audio_profile"] != "digiraw":
            self.audio_profile(ident, "digiraw")
        if mode is None:
            retained = []
            while not client["queue"].empty():
                packet = client["queue"].get_nowait()
                if not isinstance(packet, bytes) or not packet or packet[0] != DIGITAL_PACKET_KIND:
                    retained.append(packet)
            for packet in retained:
                client["queue"].put_nowait(packet)
        self.publish(json.dumps({"type":"digital-mode", "mode":mode,
            "enabled":bool(mode), "rate":DIGITAL_AUDIO_RATE}), ident)

    def publish_digital_audio(self, ident, data):
        client = self.clients.get(ident)
        if (not client or not client["audio_enabled"] or client["audio_profile"] != "digiraw" or
                client["digital_mode"] not in ("FT8", "FT4")):
            return
        pending = client["digital_tail"]
        pending.extend(data)
        block_bytes = 8
        if len(pending) < block_bytes:
            return
        usable = len(pending) - (len(pending) % block_bytes)
        source = memoryview(pending)[:usable]
        samples = struct.unpack("<" + "h" * (usable // 2), source)
        out = bytearray()
        for index in range(0, len(samples), 4):
            a, b, c, d = samples[index:index+4]
            converted = (
                a,
                round(b * (2 / 3) + c * (1 / 3)),
                round(c * (1 / 3) + d * (2 / 3)),
            )
            for sample in converted:
                out.extend(struct.pack("<h", max(-32768, min(32767, sample))))
        del source
        del pending[:usable]
        if not out:
            return
        sequence = client["digital_sequence"]
        client["digital_sequence"] = (sequence + 1) & 0xffffffff
        mode_code = 1 if client["digital_mode"] == "FT8" else 2
        timestamp_us = time.time_ns() // 1000
        sample_count = len(out) // 2
        header = struct.pack("<BIQH", mode_code, sequence, timestamp_us, sample_count)
        self.publish(bytes([DIGITAL_PACKET_KIND]) + header + out, ident)

    def publish_audio(self, ident, data):
        client = self.clients.get(ident)
        if not client or not client["audio_enabled"]:
            return
        self.publish_digital_audio(ident, data)
        profile = client["audio_profile"]
        if profile == "digiraw":
            return
        if profile == "raw":
            self.publish(bytes([2])+data, ident)
            return
        if client["opus_encoder"] is None:
            client["opus_encoder"] = OpusEncoder(32000 if profile == "balanced" else 12000,
                                                   profile == "mobile")
        pending = client["opus_pending"]
        pending.extend(data)
        frame_bytes = OpusEncoder.FRAME_SAMPLES * 2
        while len(pending) >= frame_bytes:
            packet = client["opus_encoder"].encode_pcm16le(pending[:frame_bytes])
            del pending[:frame_bytes]
            sequence = client["opus_sequence"]
            client["opus_sequence"] = (sequence+1) & 0xffffffff
            self.publish(bytes([10 if profile == "balanced" else 11])+struct.pack("<I",sequence)+packet, ident)

    def publish_spectrum(self, data):
        sequence = self.waterfall_sequence
        self.waterfall_sequence = (sequence+1) & 0xffffffff
        compressed = {}
        for ident, client in tuple(self.clients.items()):
            profile = client["waterfall_profile"]
            speed = client["waterfall_speed"]
            # Source cadence is exactly 15.625 fps. Fractional accumulators
            # select 5, 7.8125 or 11.71875 fps without duplicating rows.
            numerator, denominator = ((8,25) if profile == "slow" else
                                      (3,4) if speed == "high" else (1,2))
            client["waterfall_phase"] += numerator
            if client["waterfall_phase"] < denominator: continue
            client["waterfall_phase"] -= denominator
            emitted = client["waterfall_emitted"]
            client["waterfall_emitted"] += 1
            divisor = speed if isinstance(speed,int) else 1
            if emitted % divisor: continue
            zoom, view_center = client["waterfall_zoom"], client["waterfall_center"]
            span = round(self.sample_rate/zoom)
            lower = round(view_center-span/2)
            start = max(0, min(len(data)-1, round((lower-self.band_lower)/self.sample_rate*len(data))))
            end = max(start+1, min(len(data), round((lower+span-self.band_lower)/self.sample_rate*len(data))))
            key = profile, start, end, lower, span
            if key not in compressed:
                compressed[key] = bytes([7])+encode_waterfall(data, profile, sequence, start, end, lower, span)
            self.publish(compressed[key], ident)

    def status(self):
        self.publish(json.dumps(self.info()))

    def settings_command(self, ident, settings):
        effective = settings
        client = self.clients.get(ident)
        if client and client.get("digital_mode") in ("FT8", "FT4"):
            # Digital mode keeps the selected 0-5 kHz passband, but bypasses
            # squelch, autonotch and noise reduction so decoding remains intact.
            effective = {**settings, "squelch": -150, "notch": False, "nr": 0}
        # CW's displayed frequency is the RF carrier, with a 700 Hz beat note.
        offset = effective['frequency']-self.center_frequency-(700 if effective['mode']=='CW' else 0)
        return f"set {ident} {offset} {effective['mode']} {effective['low']} {effective['high']} {effective['squelch']} {int(effective.get('notch', False))} {effective.get('nr', 0)}"

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
            levels = ["slow", "low", "balanced", "high"]
            address_rates = {}
            for ident, client in tuple(self.clients.items()):
                rate = round((client["sent_bytes"]-client["last_sent"])*8/elapsed/1000, 1)
                client["last_sent"] = client["sent_bytes"]
                address_rates[client["address"]] = address_rates.get(client["address"], 0.0) + rate
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
            active_addresses = set(address_rates)
            for address, rate in address_rates.items():
                self.observe_address_bandwidth(address, rate)
            for address in set(self.address_limit_stage) - active_addresses:
                self.address_limit_stage.pop(address, None)
                self.address_bandwidth_samples.pop(address, None)
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
                    str(self.center_frequency), str(self.sample_rate),
                    self.gain_mode, str(self.gain_tenth_db),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    limit=262144)
                self.process = process
                for ident, client in tuple(self.clients.items()):
                    await self.command(self.settings_command(ident, client["settings"]))
                started = time.monotonic()
                while True:
                    try:
                        header = await asyncio.wait_for(process.stdout.readexactly(9), 20)
                    except asyncio.TimeoutError:
                        if self.clients:
                            raise RuntimeError("DSP output stalled")
                        continue
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
            except Exception as error:
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
        self.stopping = True
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task
        self.stats_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.stats_task
        sockets = [client["ws"] for client in tuple(self.clients.values())]
        if sockets:
            try:
                async with asyncio.timeout(2):
                    await asyncio.gather(*(ws.close(code=1001, message=b"Server stopping") for ws in sockets),
                                         return_exceptions=True)
            except asyncio.TimeoutError:
                logging.warning("Timed out while closing %d WebSocket(s)", len(sockets))
        await self.history.close()

    async def health(self, request):
        recent = not self.clients or time.monotonic()-self.last_data < 10
        healthy = self.source in ("streaming", "demo") and recent and self.task and not self.task.done()
        return web.json_response(self.info(), status=200 if healthy else 503)

    async def websocket(self, request):
        origin = request.headers.get("Origin", "")
        parsed = urlsplit(origin)
        # Explicit optional origin for proxy deployments; never rewrite Origin.
        allowed = self.args.origin == "*" or (origin == self.args.origin if self.args.origin else (
            parsed.scheme in ("http", "https") and parsed.netloc == request.host)
        )
        if not allowed:
            raise web.HTTPForbidden(text="Origin not allowed")
        address = self.client_address(request)
        if not self.allow_connection_attempt(address):
            raise web.HTTPTooManyRequests(text="Demasiados intentos de conexión. Espera unos segundos.")
        if len(self.clients) >= self.args.max_clients:
            raise web.HTTPServiceUnavailable(text="Receptor completo. Intenta más tarde.")
        per_ip = getattr(self.args, "max_clients_per_ip", self.args.max_clients)
        if sum(client["address"] == address for client in self.clients.values()) >= per_ip:
            raise web.HTTPTooManyRequests(text="Demasiadas conexiones desde esta dirección.")
        # 500 Unicode characters can occupy 6000 bytes when JSON uses escaped
        # surrogate pairs. Text limits remain enforced after JSON decoding.
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=8192, compress=False)
        # Reserve the slot before the first await, including handshake processing.
        ident = self.next_id
        self.next_id += 1
        settings = {"frequency": self.initial_frequency, "mode": "LSB", "low": -2700, "high": -300, "squelch": -150, "notch": False, "nr": 0}
        queue = asyncio.Queue(maxsize=32)
        client = {"queue": queue, "reliable": asyncio.Queue(maxsize=64), "wake": asyncio.Event(),
                  "closing": False, "settings": settings, "ws": ws, "name": "", "key": "", "address": address,
                  "chat_tokens": 4.0, "chat_last": time.monotonic(), "history_last": 0,
                  "waterfall_profile": "balanced", "waterfall_preference": "balanced", "waterfall_ceiling": "balanced",
                  "waterfall_zoom": 1.0, "waterfall_center": self.center_frequency,
                  "waterfall_speed": 1, "waterfall_phase": 0, "waterfall_emitted": 0,
                  "congestion": 0, "stable_intervals": 0, "sent_bytes": 0, "last_sent": 0,
                  "audio_enabled": False, "audio_profile": "balanced",
                  "opus_encoder": None, "opus_pending": bytearray(), "opus_sequence": 0,
                  "digital_mode": None, "digital_tail": bytearray(), "digital_sequence": 0,
                  "protocol_ready": False, "resource_policy": None}
        self.clients[ident] = client
        self.enforce_address_limits(address)
        sender = None
        protocol_timer = None
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
            async def require_protocol():
                await asyncio.sleep(5)
                if not client["protocol_ready"]:
                    await ws.close(code=1002, message=b"Protocol negotiation timeout")
            protocol_timer = asyncio.create_task(require_protocol())
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
                    if update.get("type") == "hello":
                        if client["protocol_ready"]:
                            raise ValueError("El protocolo ya fue negociado")
                        if (update.get("protocol") != PROTOCOL_VERSION or
                                update.get("waterfall") != WATERFALL_PROTOCOL_VERSION):
                            await ws.close(code=1002, message=b"Incompatible protocol")
                            break
                        client["protocol_ready"] = True
                        protocol_timer.cancel()
                        self.publish(json.dumps({"type":"hello", "protocol":PROTOCOL_VERSION,
                            "waterfall":WATERFALL_PROTOCOL_VERSION}), ident)
                        self.status()
                        self.presence()
                        async with self.community_lock:
                            self.publish(json.dumps(await self.history.history("chat")), ident)
                            self.publish(json.dumps(await self.history.history("log")), ident)
                        continue
                    if not client["protocol_ready"]:
                        await ws.close(code=1002, message=b"Protocol negotiation required")
                        break
                    if update.get("type") in ("identify", "history", "chat", "log"):
                        await self.community(ident, update)
                        continue
                    if update.get("type") == "waterfall":
                        preference, profile = update.get("preference"), update.get("profile")
                        profiles = ("auto", "slow", "low", "balanced", "high")
                        if preference not in profiles or profile not in profiles[1:]:
                            raise ValueError("Perfil de cascada desconocido")
                        if preference != "auto" and preference != profile:
                            raise ValueError("Perfil de cascada inconsistente")
                        waterfall_max = self.address_profile_limits(address)[0]
                        levels = ("slow", "low", "balanced", "high")
                        if levels.index(profile) > levels.index(waterfall_max):
                            raise ValueError(f"Esta red permite cascada hasta {waterfall_max}")
                        self.waterfall_profile(ident, profile, preference, profile)
                        continue
                    if update.get("type") == "waterfall-view":
                        zoom, view_center = update.get("zoom"), update.get("center")
                        if type(zoom) not in (int,float) or type(view_center) not in (int,float) or not math.isfinite(zoom) or not math.isfinite(view_center) or not 1 <= zoom <= 64:
                            raise ValueError("Ventana de cascada inválida")
                        span = self.sample_rate/zoom
                        if not self.band_lower+span/2 <= view_center <= self.band_upper-span/2:
                            raise ValueError("Ventana de cascada fuera de banda")
                        client["waterfall_zoom"], client["waterfall_center"] = float(zoom), round(view_center)
                        continue
                    if update.get("type") == "waterfall-speed":
                        speed = update.get("divisor")
                        if speed == "high":
                            if client["waterfall_profile"] != "high":
                                raise ValueError("Velocidad alta requiere cascada en alta definición")
                        elif type(speed) is not int or speed not in (1,2,6):
                            raise ValueError("Velocidad de cascada inválida")
                        client["waterfall_speed"] = speed
                        client["waterfall_phase"] = 0
                        client["waterfall_emitted"] = 0
                        self.publish(json.dumps({"type":"waterfall-speed", "divisor":speed,
                            "fps":11.7 if speed == "high" else round((5 if client["waterfall_profile"] == "slow" else 7.8125)/speed, 1)}), ident)
                        continue
                    if update.get("type") == "audio":
                        enabled = update.get("enabled")
                        if type(enabled) is not bool: raise ValueError("Estado de audio inválido")
                        if enabled:
                            audio_max = self.address_profile_limits(address)[1]
                            levels = ("mobile", "balanced", "digiraw", "raw")
                            if levels.index(client["audio_profile"]) > levels.index(audio_max):
                                raise ValueError(f"Esta red permite audio hasta {audio_max}")
                        self.audio_state(ident, enabled)
                        continue
                    if update.get("type") == "audio-profile":
                        profile = update.get("profile")
                        if profile not in ("raw", "balanced", "mobile", "digiraw"):
                            raise ValueError("Perfil de audio desconocido")
                        if profile == "digiraw" and client["digital_mode"] not in ("FT8", "FT4"):
                            raise ValueError("El perfil digiraw requiere un modo digital activo")
                        audio_max = self.address_profile_limits(address)[1]
                        levels = ("mobile", "balanced", "digiraw", "raw")
                        if levels.index(profile) > levels.index(audio_max):
                            raise ValueError(f"Esta red permite audio hasta {audio_max}")
                        if client["digital_mode"] and profile != "digiraw":
                            self.digital_mode(ident, None)
                            await self.command(self.settings_command(ident, client["settings"]))
                        self.audio_profile(ident, profile)
                        continue
                    if update.get("type") == "digital-mode":
                        mode = update.get("mode")
                        if mode == "":
                            mode = None
                        self.digital_mode(ident, mode)
                        await self.command(self.settings_command(ident, client["settings"]))
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
                    filter_valid = (0 <= new["low"] < new["high"] <= 5000 if client["digital_mode"]
                                    else -6000 <= new["low"] < new["high"] <= 6000)
                    if not (self.band_lower <= new["frequency"] <= self.band_upper and filter_valid
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
            if not self.address_clients(address):
                self.address_limit_stage.pop(address, None)
                self.address_bandwidth_samples.pop(address, None)
            else:
                self.enforce_address_limits(address, notify=False)
            if sender:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError, ConnectionError):
                    await sender
            if protocol_timer:
                protocol_timer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await protocol_timer
            if not self.stopping:
                await self.command(f"del {ident}")
            self.status()
            self.presence()
        return ws

@web.middleware
async def security(request, handler):
    response = await handler(request)
    if not response.prepared:
        response.headers.update({"X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin",
            "Permissions-Policy": "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'self'; base-uri 'none'; form-action 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' data:; connect-src 'self'; media-src 'self' blob:; worker-src 'self'; object-src 'none'; frame-ancestors 'none'"})
        response.headers.setdefault("Cache-Control", "no-store")
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
    async def receiverbook_status(request):
        if not gateway.receiverbook["enable"]:
            raise web.HTTPNotFound()
        return web.Response(text=gateway.receiverbook_status(), content_type="text/plain")
    app.router.add_get("/site-config.js", site_config)
    app.router.add_get("/site-logo", site_logo)
    app.router.add_get("/~~orgstatus", receiverbook_status)
    async def asset(request):
        name = request.match_info.get("name", "index.html")
        if name not in {"index.html", "style.css", "zoom.css", "site.js", "app.js", "audio-worklet.js", "classic-audio.js", "digital-worker.js", "mfsk-decoder.js", "mfsk-decoder_bg.wasm", "cty.dat", "waterfall-codec.js", "community.js", "palette.js"}:
            raise web.HTTPNotFound()
        if name in {"mfsk-decoder.js", "mfsk-decoder_bg.wasm"} and not (ROOT / "web" / name).exists():
            raise web.HTTPNotFound()
        if name == "index.html" and gateway.receiverbook["enable"]:
            document = (ROOT / "web/index.html").read_text(encoding="utf-8")
            document = document.replace("<head>", f"<head>\n  {gateway.receiverbook['tag']}", 1)
            return web.Response(text=document, content_type="text/html")
        if name == "cty.dat":
            return web.FileResponse(ROOT / "web" / "cty.dat.gz", headers={
                "Content-Encoding": "gzip",
                "Content-Type": "text/plain; charset=utf-8",
                "Cache-Control": "public, max-age=604800",
            })
        return web.FileResponse(ROOT / "web" / name)
    app.router.add_get("/", asset)
    app.router.add_get("/{name}", asset)
    return app

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", help="Listener IP (overrides site config)")
    parser.add_argument("--port", type=int, help="Listener port (overrides site config)")
    parser.add_argument("--receiver-type", choices=("rtltcp",), help="Receiver connector (overrides site config)")
    parser.add_argument("--source-host", help="Receiver IP (overrides site config)")
    parser.add_argument("--source-port", type=int, help="Receiver port (overrides site config)")
    parser.add_argument("--max-clients", type=int, help="Global connection limit (overrides site config)")
    parser.add_argument("--max-clients-per-ip", type=int, help="Per-IP limit (overrides site config)")
    parser.add_argument("--full-quality-sessions-per-ip", type=int,
                        help="Sessions allowed premium profiles per IP")
    parser.add_argument("--max-bandwidth-kbps-per-ip", type=int,
                        help="Adaptive aggregate egress limit per IP")
    parser.add_argument("--trusted-proxy", help="Proxy IP allowed to set X-HamSDR-Client-IP")
    parser.add_argument("--origin", help="Exact public origin, or * to disable origin protection")
    parser.add_argument("--tls", dest="tls_enabled", action=argparse.BooleanOptionalAction,
                        default=None, help="Enable or disable native HTTPS/WSS")
    parser.add_argument("--tls-certificate", type=Path, help="PEM certificate chain (overrides site config)")
    parser.add_argument("--tls-private-key", type=Path, help="PEM private key (overrides site config)")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--database", type=Path, help="Community database path (overrides site config)")
    parser.add_argument("--max-size-mb", type=int, help="Community database maximum size in MB")
    parser.add_argument("--site-config", type=Path, help="Site configuration TOML (defaults to ./site.toml, then generic example)")
    args = parser.parse_args()
    try:
        configured = load_runtime_config(args.site_config, allow_unconfigured=args.demo)
    except ValueError as error:
        parser.error(str(error))
    for name, value in configured.items():
        if getattr(args, name, None) is None:
            setattr(args, name, value)
    try:
        args.bind = ipaddress.ip_address(args.bind).compressed
        args.source_host = ipaddress.ip_address(args.source_host).compressed
    except ValueError as error:
        parser.error(f"invalid IP address: {error}")
    if args.trusted_proxy:
        try:
            args.trusted_proxy = ipaddress.ip_address(args.trusted_proxy).compressed
        except ValueError as error:
            parser.error(f"invalid trusted proxy: {error}")
    if args.origin and args.origin != "*":
        parsed_origin = urlsplit(args.origin)
        if (parsed_origin.scheme not in ("http", "https") or not parsed_origin.netloc or
                parsed_origin.path or parsed_origin.query or parsed_origin.fragment or
                parsed_origin.username or parsed_origin.password):
            parser.error("origin must be an exact http(s) origin, or *")
    if (not 1 <= args.source_port <= 65535 or not 1 <= args.port <= 65535 or
            not 1 <= args.max_clients <= 20 or not 1 <= args.max_clients_per_ip <= args.max_clients or
            not 1 <= args.full_quality_sessions_per_ip <= args.max_clients_per_ip or
            not 1 <= args.max_bandwidth_kbps_per_ip <= 1000000 or
            not 1 <= args.max_size_mb <= 1048576):
        parser.error("Invalid ports, clients, bandwidth or database limits")
    try:
        ssl_context = create_tls_context(args)
    except ValueError as error:
        parser.error(str(error))
    web.run_app(application(args), host=args.bind, port=args.port, ssl_context=ssl_context,
                shutdown_timeout=2, handler_cancellation=True, access_log=None)
