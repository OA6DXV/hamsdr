import random
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waterfall_codec import decode, encode

class WaterfallCodecTests(unittest.TestCase):
    def test_sizes_ranges_and_peak_preservation(self):
        row=bytearray(random.Random(7100).randrange(256) for _ in range(65536))
        row[32000:32064]=bytes([255])*64
        for profile,size,bins,code,error in [('mobile',529,1024,1,17),('balanced',1553,2048,2,5),('raw',4113,4096,3,0)]:
            packet=encode(row,profile,123456,0,len(row),6588500,1024000)
            self.assertEqual(len(packet),size)
            got_code,sequence,lower,span,decoded=decode(packet)
            self.assertEqual((got_code,sequence,lower,span,len(decoded)),(code,123456,6588500,1024000,bins))
            self.assertGreaterEqual(decoded[bins*32000//65536],255-error)
            group=len(row)//bins
            expected=[max(row[i:i+group]) for i in range(0,len(row),group)]
            self.assertLessEqual(max(abs(a-b) for a,b in zip(expected,decoded)),error)

    def test_zoom_window_has_real_bins_and_metadata(self):
        row=bytes(i%256 for i in range(65536))
        packet=encode(row,'mobile',7,32000,33024,7092500,16000)
        code,sequence,lower,span,decoded=decode(packet)
        self.assertEqual((code,sequence,lower,span,len(decoded)),(1,7,7092500,16000,1024))

    def test_experimental_profiles_are_lossless_and_variable_length(self):
        smooth=bytes(90+(index//16)%12 for index in range(4096))
        for profile,bins,code in [('exp1024',1024,4),('exp2048',2048,5),('exp4096',4096,6)]:
            packet=encode(smooth,profile,8,lower_hz=6588500,span_hz=1024000)
            got_code,sequence,lower,span,decoded=decode(packet)
            expected=bytes(max(smooth[index*4096//bins:(index+1)*4096//bins]) for index in range(bins))
            self.assertEqual((got_code,sequence,lower,span),(code,8,6588500,1024000))
            self.assertEqual(decoded,expected)
            self.assertLess(len(packet),bins)

    def test_rejects_invalid_input(self):
        with self.assertRaises(ValueError):encode(b'bad','mobile',0)
        with self.assertRaises(ValueError):encode(bytes(4096),'unknown',0)
        packet=encode(bytes(4096),'balanced',1,lower_hz=1,span_hz=1)
        experimental=encode(bytes(range(256))*16,'exp1024',1,lower_hz=1,span_hz=1)
        for invalid in [packet[:-1],b'',bytes([1])+packet[1:],experimental[:-2]]:
            with self.assertRaises(ValueError):decode(invalid)

if __name__=='__main__':unittest.main()
