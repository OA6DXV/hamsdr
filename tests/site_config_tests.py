import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import ROOT, load_listen_config, load_site_config


class SiteConfigTests(unittest.TestCase):
    def test_generic_example(self):
        generic, logo = load_site_config(ROOT / "site.example.json")
        self.assertEqual(generic["receiver_name"], "HamSDR")
        self.assertEqual(generic["version"], "0.2.10-unstable")
        self.assertNotIn("footer_text", generic)
        self.assertIsNone(logo)
        self.assertEqual(load_listen_config(ROOT / "site.example.json"), ("127.0.0.1", 18093))

    def test_listener_configuration_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site.json"
            data = json.loads((ROOT / "site.example.json").read_text())
            data["server"] = {"bind": "0.0.0.0", "port": 18099}
            path.write_text(json.dumps(data))
            self.assertEqual(load_listen_config(path), ("0.0.0.0", 18099))
            data["server"]["port"] = 70000
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "server port"):
                load_listen_config(path)
            data["server"] = {"bind": "not-an-address", "port": 18099}
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "server bind"):
                load_listen_config(path)

    def test_rejects_unsafe_or_missing_branding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site.json"
            data = json.loads((ROOT / "site.example.json").read_text())
            data["administrator"]["url"] = "javascript:alert(1)"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "administrator url"):
                load_site_config(path)
            data["administrator"]["url"] = ""
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
