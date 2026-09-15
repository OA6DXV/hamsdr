"""Minimal ctypes binding to the system libopus for low-latency mono packets."""
import ctypes
import ctypes.util

OPUS_OK = 0
OPUS_APPLICATION_AUDIO = 2049
OPUS_SET_BITRATE_REQUEST = 4002
OPUS_SET_MAX_BANDWIDTH_REQUEST = 4004
OPUS_SET_VBR_REQUEST = 4006
OPUS_SET_COMPLEXITY_REQUEST = 4010
OPUS_SET_VBR_CONSTRAINT_REQUEST = 4020
OPUS_SET_SIGNAL_REQUEST = 4024
OPUS_BANDWIDTH_NARROWBAND = 1101
OPUS_BANDWIDTH_WIDEBAND = 1103
OPUS_SIGNAL_MUSIC = 3002


def _library():
    name = ctypes.util.find_library("opus")
    if not name:
        raise RuntimeError("libopus is not installed")
    library = ctypes.CDLL(name)
    library.opus_encoder_create.restype = ctypes.c_void_p
    library.opus_encoder_create.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                             ctypes.POINTER(ctypes.c_int))
    library.opus_encoder_destroy.argtypes = (ctypes.c_void_p,)
    library.opus_encode.restype = ctypes.c_int
    library.opus_encode.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16),
                                    ctypes.c_int, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int32)
    library.opus_decoder_create.restype = ctypes.c_void_p
    library.opus_decoder_create.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
    library.opus_decoder_destroy.argtypes = (ctypes.c_void_p,)
    library.opus_decode.restype = ctypes.c_int
    library.opus_decode.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte),
                                    ctypes.c_int32, ctypes.POINTER(ctypes.c_int16),
                                    ctypes.c_int, ctypes.c_int)
    return library


try:
    LIBOPUS = _library()
    OPUS_AVAILABLE = True
except (OSError, RuntimeError):
    LIBOPUS = None
    OPUS_AVAILABLE = False


class OpusEncoder:
    SAMPLE_RATE = 16000
    FRAME_SAMPLES = 320

    def __init__(self, bitrate, narrowband=False):
        if not OPUS_AVAILABLE:
            raise RuntimeError("libopus is not installed")
        error = ctypes.c_int()
        self._encoder = LIBOPUS.opus_encoder_create(self.SAMPLE_RATE, 1, OPUS_APPLICATION_AUDIO,
                                                     ctypes.byref(error))
        if not self._encoder or error.value != OPUS_OK:
            raise RuntimeError(f"opus encoder creation failed: {error.value}")
        try:
            self._control(OPUS_SET_BITRATE_REQUEST, bitrate)
            self._control(OPUS_SET_VBR_REQUEST, 1)
            self._control(OPUS_SET_VBR_CONSTRAINT_REQUEST, 1)
            self._control(OPUS_SET_COMPLEXITY_REQUEST, 5)
            self._control(OPUS_SET_SIGNAL_REQUEST, OPUS_SIGNAL_MUSIC)
            self._control(OPUS_SET_MAX_BANDWIDTH_REQUEST,
                          OPUS_BANDWIDTH_NARROWBAND if narrowband else OPUS_BANDWIDTH_WIDEBAND)
        except Exception:
            self.close()
            raise

    def _control(self, request, value):
        result = LIBOPUS.opus_encoder_ctl(self._encoder, ctypes.c_int(request), ctypes.c_int(value))
        if result != OPUS_OK:
            raise RuntimeError(f"opus encoder control failed: {result}")

    def encode(self, samples):
        if len(samples) != self.FRAME_SAMPLES:
            raise ValueError("Opus requires exactly 20 ms / 320 samples")
        pcm = (ctypes.c_int16 * len(samples))(*samples)
        output = (ctypes.c_ubyte * 512)()
        size = LIBOPUS.opus_encode(self._encoder, pcm, len(samples), output, len(output))
        if size < 0:
            raise RuntimeError(f"opus encode failed: {size}")
        return bytes(output[:size])

    def close(self):
        if getattr(self, "_encoder", None):
            LIBOPUS.opus_encoder_destroy(self._encoder)
            self._encoder = None

    def __del__(self):
        self.close()


class OpusDecoder:
    """Reference decoder used only by tests and local diagnostics."""
    def __init__(self, sample_rate=16000):
        if not OPUS_AVAILABLE:
            raise RuntimeError("libopus is not installed")
        error = ctypes.c_int()
        self.sample_rate = sample_rate
        self._decoder = LIBOPUS.opus_decoder_create(sample_rate, 1, ctypes.byref(error))
        if not self._decoder or error.value != OPUS_OK:
            raise RuntimeError(f"opus decoder creation failed: {error.value}")

    def decode(self, packet):
        encoded = (ctypes.c_ubyte * len(packet)).from_buffer_copy(packet)
        pcm = (ctypes.c_int16 * 960)()
        count = LIBOPUS.opus_decode(self._decoder, encoded, len(packet), pcm, len(pcm), 0)
        if count < 0:
            raise RuntimeError(f"opus decode failed: {count}")
        return list(pcm[:count])

    def close(self):
        if getattr(self, "_decoder", None):
            LIBOPUS.opus_decoder_destroy(self._decoder)
            self._decoder = None

    def __del__(self):
        self.close()
