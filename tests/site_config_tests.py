import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import ROOT, load_site_config


class SiteConfigTests(unittest.TestCase):
    def test_generic_example(self):
        generic, logo = load_site_config(ROOT / "site.example.json")
        self.assertEqual(generic["receiver_name"], "HamSDR")
        self.assertEqual(generic["version"], "0.2.7-unstable")
        self.assertNotIn("footer_text", generic)
        self.assertIsNone(logo)

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


if __name__ == "__main__":
    unittest.main()
