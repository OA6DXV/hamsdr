# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SMeterCalibrationTests(unittest.TestCase):
    def test_client_side_calibration_math(self):
        result = subprocess.run(
            ["node", "tests/smeter_calibration_tests.mjs"], cwd=ROOT,
            text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("S-meter calibration tests passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
