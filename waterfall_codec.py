"""Versioned, independently decodable waterfall row codec.

This codec deliberately has no dependency between rows: a dropped WebSocket
packet, profile change, or late join is fully recovered by the next row.
"""
import struct

VERSION = 2
PROFILES = {
    "mobile": {"code": 1, "bins": 1024, "bits": 4},
    "balanced": {"code": 2, "bins": 2048, "bits": 6},
    "raw": {"code": 3, "bins": 4096, "bits": 8},
    "classic": {"code": 4, "bins": 1024, "bits": 8},
}
HEADER = struct.Struct("<BBHBIiI")  # version, profile, bins, bits, sequence, lower Hz, span Hz

def _pack(values, bits):
    output = bytearray()
    accumulator = used = 0
    for value in values:
        accumulator |= value << used
        used += bits
        while used >= 8:
            output.append(accumulator & 0xff)
            accumulator >>= 8
            used -= 8
    if used:
        output.append(accumulator & 0xff)
    return bytes(output)

def _pack_classic(values):
    """Losslessly encode spatial deltas; 0xf introduces an absolute sample."""
    output = bytearray([values[0]])
    nibbles = []
    previous = values[0]
    for value in values[1:]:
        delta = value-previous
        if -7 <= delta <= 7:
            nibbles.append(delta+7)
        else:
            nibbles.extend((15, value & 15, value >> 4))
        previous = value
    for index in range(0, len(nibbles), 2):
        output.append(nibbles[index] | ((nibbles[index+1] if index+1 < len(nibbles) else 0) << 4))
    return bytes(output)

def encode(row: bytes, profile: str, sequence: int, start: int = 0, end: int | None = None,
           lower_hz: int = 0, span_hz: int = 0) -> bytes:
    """Project one frequency window to a bounded, directly drawable row."""
    end = len(row) if end is None else end
    if not row or profile not in PROFILES or not 0 <= sequence <= 0xffffffff:
        raise ValueError("invalid waterfall row")
    if not 0 <= start < end <= len(row) or not -(2**31) <= lower_hz < 2**31 or not 0 < span_hz < 2**32:
        raise ValueError("invalid waterfall window")
    spec = PROFILES[profile]
    source_bins = end-start
    bins = min(spec["bins"], source_bins)
    shift = 8-spec["bits"]
    values = []
    for output in range(bins):
        first = start + output*source_bins//bins
        last = start + (output+1)*source_bins//bins
        values.append(max(row[first:max(first+1,last)]) >> shift)
    payload = _pack_classic(values) if profile == "classic" else _pack(values, spec["bits"])
    return HEADER.pack(VERSION, spec["code"], bins, spec["bits"], sequence, lower_hz, span_hz) + payload

def decode(packet: bytes):
    """Reference decoder used by tests and offline format analysis."""
    if len(packet) < HEADER.size:
        raise ValueError("truncated waterfall row")
    version, code, bins, bits, sequence, lower_hz, span_hz = HEADER.unpack_from(packet)
    expected = HEADER.size + (bins*bits+7)//8
    valid = {1:(1024,4), 2:(2048,6), 3:(4096,8), 4:(1024,8)}
    maximum, expected_bits = valid.get(code, (0,0))
    if version != VERSION or not 0 < bins <= maximum or bits != expected_bits or span_hz == 0 or (code != 4 and len(packet) != expected):
        raise ValueError("invalid waterfall packet")
    if code == 4:
        payload = packet[HEADER.size:]
        if not payload: raise ValueError("truncated classic waterfall row")
        values, previous, nibbles = [payload[0]], payload[0], []
        for byte in payload[1:]: nibbles.extend((byte & 15, byte >> 4))
        at = 0
        while len(values) < bins:
            if at >= len(nibbles): raise ValueError("truncated classic waterfall row")
            codeword = nibbles[at]; at += 1
            if codeword == 15:
                if at+1 >= len(nibbles): raise ValueError("truncated classic escape")
                value = nibbles[at] | (nibbles[at+1] << 4); at += 2
            else:
                value = previous+codeword-7
                if not 0 <= value <= 255: raise ValueError("invalid classic delta")
            values.append(value); previous = value
        return code, sequence, lower_hz, span_hz, bytes(values)
    data, mask, accumulator, used, values = packet[HEADER.size:], (1 << bits)-1, 0, 0, []
    for byte in data:
        accumulator |= byte << used; used += 8
        while used >= bits and len(values) < bins:
            quantized = accumulator & mask
            values.append(round(quantized*255/mask))
            accumulator >>= bits; used -= bits
    return code, sequence, lower_hz, span_hz, bytes(values)
