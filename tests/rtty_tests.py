# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

class RttyTests(unittest.TestCase):
    def test_client_side_decoder(self):
        result = subprocess.run(["node", "tests/rtty_core_tests.mjs"], cwd=ROOT,
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RTTY core tests passed", result.stdout)

if __name__ == "__main__":
    unittest.main()
