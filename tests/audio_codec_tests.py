import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audio_codec import decode_ima_adpcm, downsample_2, encode_ima_adpcm


class AudioCodecTests(unittest.TestCase):
    def test_packet_is_independently_decodable(self):
        pcm = [round(18000*math.sin(2*math.pi*1000*n/16000)) for n in range(256)]
        packet, state = encode_ima_adpcm(pcm)
        decoded = decode_ima_adpcm(packet, len(pcm))
        error = math.sqrt(sum((a-b)**2 for a,b in zip(pcm,decoded))/len(pcm))
        self.assertEqual(len(packet), 132)
        self.assertLess(error, 3000)
        second, _ = encode_ima_adpcm(pcm, state)
        self.assertEqual(len(decode_ima_adpcm(second, len(pcm))), len(pcm))

    def test_low_profile_halves_samples_and_payload(self):
        pcm = list(range(-256, 256))
        reduced = downsample_2(pcm)
        packet, _ = encode_ima_adpcm(reduced)
        self.assertEqual(len(reduced), 256)
        self.assertEqual(len(packet), 132)

    def test_rejects_truncated_packet(self):
        with self.assertRaises(ValueError):
            decode_ima_adpcm(b"\0\0", 1)


if __name__ == "__main__":
    unittest.main()
