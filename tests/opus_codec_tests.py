import math
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from opus_codec import OPUS_AVAILABLE, OpusDecoder, OpusEncoder


@unittest.skipUnless(OPUS_AVAILABLE, "system libopus unavailable")
class OpusCodecTests(unittest.TestCase):
    def test_high_and_low_profiles_round_trip(self):
        pcm = [round(16000*math.sin(2*math.pi*1000*n/16000)) for n in range(320)]
        for bitrate,narrow,maximum in ((32000,False,130),(12000,True,50)):
            encoder,decoder=OpusEncoder(bitrate,narrow),OpusDecoder()
            packets=[encoder.encode(pcm) for _ in range(8)]
            direct=encoder.encode_pcm16le(struct.pack("<"+"h"*len(pcm),*pcm))
            self.assertGreater(len(direct),0)
            decoded=[sample for packet in packets for sample in decoder.decode(packet)]
            steady=decoded[1600:]
            self.assertTrue(all(0<len(packet)<=maximum for packet in packets))
            self.assertGreater(max(steady)-min(steady),1000)
            encoder.close();decoder.close()


if __name__ == "__main__":
    unittest.main()
