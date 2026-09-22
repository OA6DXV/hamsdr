# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import ROOT, load_listen_config, load_runtime_config, load_site_config


def installation(directory, secure='mode="insecure"\ncertificate=""\nprivate_key=""\norigin=""'):
    return f'''working_directory="{directory}"
[server]
bind="127.0.0.1"
port=18093
trusted_proxy=""
max_clients=10
max_clients_per_ip=3
full_quality_sessions_per_ip=2
max_bandwidth_kbps_per_ip=1000
digimodes=true
[secure]
{secure}
[html]
receiver_name="HamSDR"
description=["Test receiver"]
callsign="N0CALL"
show_admin=true
[station]
qth="AA00aa"
description="Test HamSDR"
email="operator@example.test"
mobile_page="/"
flag=""
flag_description="Station flag"
[band.0]
enable=true
name="40m"
center_frequency_khz=7100.5
sample_rate_khz=1024.0
antenna="Test antenna"
receiver_type="rtltcp"
receiver_host="127.0.0.1"
receiver_port=1234
receiver_gain=8.0
[receiverbook]
enable=false
tag=""
[storage]
database="community.sqlite3"
max_size_mb=50
'''


class SiteConfigTests(unittest.TestCase):
    def test_generic_example_is_unconfigured(self):
        generic, logo, station, bands, receiverbook = load_site_config(ROOT / "site.example.toml")
        self.assertEqual(generic["receiver_name"], "HamSDR")
        self.assertEqual(generic["callsign"], "N0CALL")
        self.assertTrue(generic["show_admin"])
        self.assertEqual(generic["version"], "0.5.2-dev")
        self.assertFalse(generic["digimodes"])
        self.assertIsNone(logo)
        self.assertEqual(station["mobile_page"], "/")
        self.assertFalse(bands[0]["enable"])
        self.assertFalse(receiverbook["enable"])
        with self.assertRaisesRegex(ValueError, "exactly one band"):
            load_runtime_config(ROOT / "site.example.toml")

    def test_runtime_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site.toml"
            path.write_text(installation(directory))
            self.assertEqual(load_listen_config(path), ("127.0.0.1", 18093))
            runtime = load_runtime_config(path)
            self.assertEqual(runtime["security_mode"], "insecure")
            self.assertEqual(runtime["receiver_type"], "rtltcp")
            self.assertEqual((runtime["source_host"], runtime["source_port"]), ("127.0.0.1", 1234))
            self.assertEqual((runtime["center_frequency"], runtime["sample_rate"]), (7100500, 1024000))
            self.assertEqual((runtime["receiver_gain"], runtime["gain_mode"], runtime["gain_tenth_db"]),
                             (8.0, "manual", 80))
            self.assertEqual(runtime["database"], Path(directory) / "community.sqlite3")
            self.assertEqual(runtime["max_size_mb"], 50)
            self.assertEqual(runtime["full_quality_sessions_per_ip"], 2)
            self.assertEqual(runtime["max_bandwidth_kbps_per_ip"], 1000)
            self.assertTrue(runtime["digimodes"])
            path.write_text(installation(directory).replace('receiver_gain=8.0\n', ''))
            automatic = load_runtime_config(path)
            self.assertEqual((automatic["receiver_gain"], automatic["gain_mode"], automatic["gain_tenth_db"]),
                             ("auto", "auto", 0))

    def test_listener_configuration_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site.toml"
            text = installation(directory).replace('bind="127.0.0.1"', 'bind="0.0.0.0"').replace('port=18093', 'port=18099')
            path.write_text(text)
            self.assertEqual(load_listen_config(path), ("0.0.0.0", 18099))
            path.write_text(text.replace('trusted_proxy=""', 'trusted_proxy="192.0.2.10"'))
            self.assertEqual(load_runtime_config(path)["trusted_proxy"], "192.0.2.10")
            path.write_text(text.replace('port=18099', 'port=70000'))
            with self.assertRaisesRegex(ValueError, "server port"):
                load_listen_config(path)
            path.write_text(text.replace('bind="0.0.0.0"', 'bind="not-an-address"'))
            with self.assertRaisesRegex(ValueError, "server bind"):
                load_listen_config(path)

    def test_secure_modes_and_working_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site.toml"
            native = 'mode="native"\ncertificate="cert.pem"\nprivate_key="key.pem"\norigin="https://radio.example.test:8443"'
            path.write_text(installation(directory, native).replace('port=18093', 'port=8443'))
            runtime = load_runtime_config(path)
            self.assertTrue(runtime["tls_enabled"])
            self.assertEqual(runtime["origin"], "https://radio.example.test:8443")
            self.assertEqual(runtime["tls_certificate"], Path(directory) / "cert.pem")
            proxy = 'mode="proxy"\ncertificate=""\nprivate_key=""\norigin="https://radio.example.test"'
            path.write_text(installation(directory, proxy))
            runtime = load_runtime_config(path)
            self.assertFalse(runtime["tls_enabled"])
            self.assertEqual(runtime["origin"], "https://radio.example.test")
            invalid = 'mode="invalid"\ncertificate=""\nprivate_key=""\norigin=""'
            path.write_text(installation(directory, invalid))
            with self.assertRaisesRegex(ValueError, "secure mode"):
                load_runtime_config(path)
            traversal = installation(directory).replace('database="community.sqlite3"', 'database="../outside.sqlite3"')
            path.write_text(traversal)
            with self.assertRaisesRegex(ValueError, "must be a filename"):
                load_runtime_config(path)
            path.write_text(installation(directory).replace('receiver_gain=8.0', 'receiver_gain="manual"'))
            with self.assertRaisesRegex(ValueError, "receiver_gain must"):
                load_runtime_config(path)
            path.write_text(installation(directory).replace('receiver_gain=8.0', 'receiver_gain=-5.5'))
            negative = load_runtime_config(path)
            self.assertEqual((negative["receiver_gain"], negative["gain_tenth_db"]), (-5.5, -55))
            path.write_text(installation(directory).replace('receiver_gain=8.0', 'receiver_gain=72.5'))
            above_typical = load_runtime_config(path)
            self.assertEqual((above_typical["receiver_gain"], above_typical["gain_tenth_db"]), (72.5, 725))

    def test_station_flag_and_receiverbook_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site.toml"
            flag = Path(directory) / "flag.svg"
            flag.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
            text = installation(directory).replace('flag=""', 'flag="flag.svg"')
            path.write_text(text)
            public, logo, station, bands, receiverbook = load_site_config(path)
            self.assertEqual(logo, flag)
            self.assertTrue(public["logo"]["enabled"])
            token = "a" * 64
            enabled = text.replace('enable=false\ntag=""\n[storage]',
                f'''enable=true
tag='<meta name="receiverbook-confirmation" content="{token}">'\n[storage]''')
            path.write_text(enabled)
            self.assertEqual(load_site_config(path)[4]["confirmation"], token)
            path.write_text(enabled.replace(token, "invalid"))
            with self.assertRaisesRegex(ValueError, "confirmation meta tag"):
                load_site_config(path)


if __name__ == "__main__":
    unittest.main()
