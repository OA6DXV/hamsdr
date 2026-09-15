"""Small, packet-independent IMA ADPCM encoder for browser audio transport."""
import struct

INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8)
STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
    157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544,
    598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707,
    1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871,
    5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635,
    13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
)


def _clamp(value, low, high):
    return max(low, min(high, value))


def encode_ima_adpcm(pcm, state=(0, 0)):
    """Encode signed PCM samples; header carries state so every packet decodes alone."""
    predictor, index = state
    start_predictor, start_index = predictor, index
    codes = []
    for sample in pcm:
        step = STEP_TABLE[index]
        difference = int(sample) - predictor
        code = 8 if difference < 0 else 0
        magnitude = -difference if difference < 0 else difference
        delta = step >> 3
        if magnitude >= step:
            code |= 4; magnitude -= step; delta += step
        if magnitude >= step >> 1:
            code |= 2; magnitude -= step >> 1; delta += step >> 1
        if magnitude >= step >> 2:
            code |= 1; delta += step >> 2
        predictor = _clamp(predictor-delta if code & 8 else predictor+delta, -32768, 32767)
        index = _clamp(index+INDEX_TABLE[code & 7], 0, 88)
        codes.append(code)
    payload = bytearray(struct.pack("<hBB", start_predictor, start_index, 0))
    for position in range(0, len(codes), 2):
        low = codes[position]
        high = codes[position+1] if position+1 < len(codes) else 0
        payload.append(low | high << 4)
    return bytes(payload), (predictor, index)


def decode_ima_adpcm(packet, sample_count):
    """Reference decoder used by tests and diagnostics."""
    if len(packet) < 4:
        raise ValueError("truncated ADPCM packet")
    predictor, index, _ = struct.unpack_from("<hBB", packet)
    if index > 88:
        raise ValueError("invalid ADPCM index")
    output = []
    for byte in packet[4:]:
        for code in (byte & 15, byte >> 4):
            if len(output) == sample_count:
                return output
            step = STEP_TABLE[index]
            delta = step >> 3
            if code & 4: delta += step
            if code & 2: delta += step >> 1
            if code & 1: delta += step >> 2
            predictor = _clamp(predictor-delta if code & 8 else predictor+delta, -32768, 32767)
            index = _clamp(index+INDEX_TABLE[code & 7], 0, 88)
            output.append(predictor)
    if len(output) != sample_count:
        raise ValueError("truncated ADPCM samples")
    return output


def pcm16le_samples(payload):
    if len(payload) % 2:
        raise ValueError("odd PCM16 payload")
    return struct.unpack("<" + "h"*(len(payload)//2), payload)


def downsample_2(pcm):
    """Low-cost anti-aliasing for the explicitly bandwidth-first 8 kHz profile."""
    return [round((int(pcm[i])+int(pcm[i+1]))/2) for i in range(0, len(pcm)-1, 2)]
