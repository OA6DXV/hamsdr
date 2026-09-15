import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import ROOT, load_listen_config, load_runtime_config, load_site_config


class SiteConfigTests(unittest.TestCase):
    def test_generic_example(self):
        generic, logo = load_site_config(ROOT / "site.example.toml")
        self.assertEqual(generic["receiver_name"], "HamSDR")
        self.assertEqual(generic["callsign"], "N0CALL")
        self.assertTrue(generic["show_admin"])
        self.assertEqual(generic["version"], "0.3.2-dev")
        self.assertNotIn("footer_text", generic)
        self.assertIsNone(logo)
        self.assertEqual(load_listen_config(ROOT / "site.example.toml"), ("127.0.0.1", 18093))
        runtime = load_runtime_config(ROOT / "site.example.toml")
        self.assertEqual(runtime["receiver_type"], "rtltcp")
        self.assertEqual((runtime["source_host"], runtime["source_port"]), ("127.0.0.1", 1234))
        self.assertEqual(runtime["database"], ROOT / "var/community.sqlite3")
        self.assertEqual(runtime["retention_days"], 90)
        self.assertFalse(runtime["tls_enabled"])
        self.assertEqual(runtime["tls_certificate"], ROOT / "tls/fullchain.pem")
        self.assertEqual(runtime["tls_private_key"], ROOT / "tls/privkey.pem")

    def test_listener_configuration_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site.toml"
            path.write_text('language="en"\nreceiver_name="Test"\ndescription=[]\nadministrator_label="By"\nfooter_administrator_label="Admin"\n[server]\nbind="0.0.0.0"\nport=18099\n[administrator]\nname="Admin"\nurl=""\n[logo]\nfile=""\nalt="Logo"\n')
            self.assertEqual(load_listen_config(path), ("0.0.0.0", 18099))
            path.write_text('[server]\nbind="0.0.0.0"\nport=70000\n')
            with self.assertRaisesRegex(ValueError, "server port"):
                load_listen_config(path)
            path.write_text('[server]\nbind="not-an-address"\nport=18099\n')
            with self.assertRaisesRegex(ValueError, "server bind"):
                load_listen_config(path)

    def test_secure_section_controls_tls_and_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site.toml"
            path.write_text('[server]\nbind="127.0.0.1"\nport=8443\n[secure]\nenable=true\ncertificate="cert.pem"\nprivate_key="key.pem"\norigin="https://radio.example.test:8443"\n')
            runtime = load_runtime_config(path)
            self.assertTrue(runtime["tls_enabled"])
            self.assertEqual(runtime["origin"], "https://radio.example.test:8443")
            self.assertEqual(runtime["tls_certificate"], Path(directory) / "cert.pem")
            path.write_text('[server]\nbind="127.0.0.1"\nport=8080\n[secure]\nenable=false\ncertificate=""\nprivate_key=""\norigin="https://ignored.example"\n')
            self.assertEqual(load_runtime_config(path)["origin"], "")

    def test_rejects_unsafe_or_missing_branding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site.json"
            data = {"html":{"receiver_name":"HamSDR", "description":[],
                    "callsign":"N0CALL", "show_admin":True},
                    "logo":{"file":"", "alt":"Receiver logo"}}
            data["html"]["show_admin"] = "yes"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "show_admin"):
                load_site_config(path)
            data["html"]["show_admin"] = True
            data["logo"]["file"] = "missing.svg"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "logo must"):
                load_site_config(path)
            outside = Path(directory).parent / f"{Path(directory).name}-outside-logo.svg"
            outside.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
            data["logo"]["file"] = f"../{outside.name}"
            path.write_text(json.dumps(data))
            try:
                with self.assertRaisesRegex(ValueError, "logo must"):
                    load_site_config(path)
            finally:
                outside.unlink()


if __name__ == "__main__":
    unittest.main()
