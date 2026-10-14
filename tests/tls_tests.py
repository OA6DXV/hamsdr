# SPDX-License-Identifier: GPL-3.0-only
import ssl
import subprocess
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_tls_context


class TlsTests(unittest.TestCase):
    def test_disabled_and_missing_material(self):
        disabled = SimpleNamespace(tls_enabled=False, tls_certificate=None, tls_private_key=None)
        self.assertIsNone(create_tls_context(disabled))
        missing = SimpleNamespace(tls_enabled=True, tls_certificate=None, tls_private_key=None)
        with self.assertRaisesRegex(ValueError, "requires both"):
            create_tls_context(missing)

    def test_certificate_context(self):
        with tempfile.TemporaryDirectory() as directory:
            certificate = Path(directory) / "certificate.pem"
            private_key = Path(directory) / "private-key.pem"
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", private_key, "-out", certificate, "-days", "1",
                "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            args = SimpleNamespace(tls_enabled=True, tls_certificate=certificate,
                                   tls_private_key=private_key)
            context = create_tls_context(args)
            self.assertIsInstance(context, ssl.SSLContext)
            self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)


if __name__ == "__main__":
    unittest.main()
