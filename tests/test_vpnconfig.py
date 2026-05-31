import os
import tempfile
import unittest
from vpnconfig import (
    parse_ovpn_content,
    parse_ovpn_file,
    get_ddwrt_nvram_from_config,
    config_summary,
    validate_vpn_config,
)


SAMPLE_OVPN = """\
client
dev tun0
proto udp
remote vpn.example.com 1194
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
cipher AES-256-CBC
auth SHA256
comp-lzo no
key-direction 1
verb 3

<ca>
-----BEGIN CERTIFICATE-----
CA CERT DATA
-----END CERTIFICATE-----
</ca>

<cert>
-----BEGIN CERTIFICATE-----
CLIENT CERT DATA
-----END CERTIFICATE-----
</cert>

<key>
-----BEGIN PRIVATE KEY-----
CLIENT KEY DATA
-----END PRIVATE KEY-----
</key>

<tls-auth>
-----BEGIN OpenVPN Static key V1-----
TLS AUTH KEY DATA
-----END OpenVPN Static key V1-----
</tls-auth>
"""

SAMPLE_OVPN_TCP = """\
client
dev tun
proto tcp
remote tcp.vpn.example.com 443
cipher AES-128-GCM
auth-user-pass
verb 3

<ca>
-----BEGIN CERTIFICATE-----
TCP CA CERT
-----END CERTIFICATE-----
</ca>
"""


class TestParseOvpnContent(unittest.TestCase):
    def test_basic_parsing(self):
        config = parse_ovpn_content(SAMPLE_OVPN)
        self.assertEqual(config['remote'], 'vpn.example.com')
        self.assertEqual(config['port'], '1194')
        self.assertEqual(config['proto'], 'udp')
        self.assertEqual(config['dev'], 'tun0')
        self.assertEqual(config['cipher'], 'AES-256-CBC')
        self.assertEqual(config['auth'], 'SHA256')
        self.assertEqual(config['comp-lzo'], 'no')
        self.assertEqual(config['key-direction'], '1')

    def test_inline_ca(self):
        config = parse_ovpn_content(SAMPLE_OVPN)
        self.assertIn('CA CERT DATA', config['ca'])
        self.assertIn('-----BEGIN CERTIFICATE-----', config['ca'])

    def test_inline_cert(self):
        config = parse_ovpn_content(SAMPLE_OVPN)
        self.assertIn('CLIENT CERT DATA', config['cert'])

    def test_inline_key(self):
        config = parse_ovpn_content(SAMPLE_OVPN)
        self.assertIn('CLIENT KEY DATA', config['key'])

    def test_inline_tls_auth(self):
        config = parse_ovpn_content(SAMPLE_OVPN)
        self.assertIn('TLS AUTH KEY DATA', config['tls-auth'])

    def test_boolean_directives(self):
        config = parse_ovpn_content(SAMPLE_OVPN)
        self.assertEqual(config['nobind'], 'true')
        self.assertEqual(config['persist-key'], 'true')
        self.assertEqual(config['persist-tun'], 'true')

    def test_tcp_config(self):
        config = parse_ovpn_content(SAMPLE_OVPN_TCP)
        self.assertEqual(config['remote'], 'tcp.vpn.example.com')
        self.assertEqual(config['port'], '443')
        self.assertEqual(config['proto'], 'tcp')
        self.assertEqual(config['dev'], 'tun')
        self.assertEqual(config['cipher'], 'AES-128-GCM')
        self.assertEqual(config['auth-user-pass'], 'true')

    def test_remote_with_port_override(self):
        content = "remote vpn.example.com 443 tcp\n"
        config = parse_ovpn_content(content)
        self.assertEqual(config['remote'], 'vpn.example.com')
        self.assertEqual(config['port'], '443')
        self.assertEqual(config['proto'], 'tcp')

    def test_comments_ignored(self):
        content = "# this is a comment\nremote vpn.example.com 1194\n; this is also a comment\n"
        config = parse_ovpn_content(content)
        self.assertEqual(config['remote'], 'vpn.example.com')
        self.assertEqual(len(config), 2)

    def test_empty_content(self):
        config = parse_ovpn_content('')
        self.assertEqual(len(config), 0)

    def test_external_ca_file_reference(self):
        content = "remote vpn.example.com\nca /path/to/ca.crt\n"
        config = parse_ovpn_content(content)
        self.assertEqual(config['ca'], '/path/to/ca.crt')


class TestParseOvpnFile(unittest.TestCase):
    def test_file_parsing(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ovpn', delete=False) as f:
            f.write(SAMPLE_OVPN)
            path = f.name
        try:
            config = parse_ovpn_file(path)
            self.assertEqual(config['remote'], 'vpn.example.com')
            self.assertEqual(config['port'], '1194')
            self.assertIn('CA CERT DATA', config['ca'])
        finally:
            os.unlink(path)


class TestGetDdwrtNvramFromConfig(unittest.TestCase):
    def test_basic_conversion(self):
        config = {
            'remote': 'vpn.example.com',
            'port': '1194',
            'proto': 'udp',
            'dev': 'tun0',
            'cipher': 'AES-256-CBC',
            'auth': 'SHA256',
            'comp-lzo': 'no',
            'ca': '---CA---',
            'cert': '---CERT---',
            'key': '---KEY---',
        }
        nvram = get_ddwrt_nvram_from_config(config)
        self.assertEqual(nvram['openvpncl_remoteip'], 'vpn.example.com')
        self.assertEqual(nvram['openvpncl_remoteport'], '1194')
        self.assertEqual(nvram['openvpncl_proto'], 'udp-client')
        self.assertEqual(nvram['openvpncl_tuntap'], 'tun')
        self.assertEqual(nvram['openvpncl_cipher'], 'AES-256-CBC')
        self.assertEqual(nvram['openvpncl_sec'], 'SHA256')
        self.assertEqual(nvram['openvpncl_lzo'], '0')
        self.assertEqual(nvram['openvpncl_upauth'], '0')
        self.assertEqual(nvram['openvpncl_ca'], '---CA---')
        self.assertEqual(nvram['openvpncl_client'], '---CERT---')
        self.assertEqual(nvram['openvpncl_key'], '---KEY---')

    def test_tcp_protocol(self):
        config = {'remote': 'vpn.example.com', 'proto': 'tcp'}
        nvram = get_ddwrt_nvram_from_config(config)
        self.assertEqual(nvram['openvpncl_proto'], 'tcp-client')

    def test_tap_device(self):
        config = {'remote': 'vpn.example.com', 'dev': 'tap0'}
        nvram = get_ddwrt_nvram_from_config(config)
        self.assertEqual(nvram['openvpncl_tuntap'], 'tap')

    def test_compression_enabled(self):
        config = {'remote': 'vpn.example.com', 'comp-lzo': 'yes'}
        nvram = get_ddwrt_nvram_from_config(config)
        self.assertEqual(nvram['openvpncl_lzo'], '1')

    def test_auth_user_pass(self):
        config = {
            'remote': 'vpn.example.com',
            'auth-user-pass': 'true',
            'username': 'user1',
            'password': 'pass1',
        }
        nvram = get_ddwrt_nvram_from_config(config)
        self.assertEqual(nvram['openvpncl_upauth'], '1')
        self.assertEqual(nvram['openvpncl_user'], 'user1')
        self.assertEqual(nvram['openvpncl_pass'], 'pass1')

    def test_key_direction(self):
        config = {'remote': 'vpn.example.com', 'key-direction': '1'}
        nvram = get_ddwrt_nvram_from_config(config)
        self.assertEqual(nvram['openvpncl_keydirection'], '1')

    def test_tls_auth(self):
        config = {'remote': 'vpn.example.com', 'tls-auth': 'TLS_AUTH_DATA'}
        nvram = get_ddwrt_nvram_from_config(config)
        self.assertEqual(nvram['openvpncl_tlsauth'], 'TLS_AUTH_DATA')

    def test_tls_crypt(self):
        config = {'remote': 'vpn.example.com', 'tls-crypt': 'TLS_CRYPT_DATA'}
        nvram = get_ddwrt_nvram_from_config(config)
        self.assertEqual(nvram['openvpncl_tlsauth'], 'TLS_CRYPT_DATA')

    def test_mtu(self):
        config = {'remote': 'vpn.example.com', 'tun-mtu': '1400'}
        nvram = get_ddwrt_nvram_from_config(config)
        self.assertEqual(nvram['openvpncl_mtu'], '1400')

    def test_extra_directives_in_config(self):
        config = {
            'remote': 'vpn.example.com',
            'nobind': 'true',
            'persist-key': 'true',
            'persist-tun': 'true',
            'verb': '3',
        }
        nvram = get_ddwrt_nvram_from_config(config)
        self.assertIn('openvpncl_config', nvram)
        extra = nvram['openvpncl_config']
        self.assertIn('verb 3', extra)
        self.assertIn('nobind', extra)
        self.assertIn('persist-key', extra)
        self.assertIn('persist-tun', extra)


class TestConfigSummary(unittest.TestCase):
    def test_basic_summary(self):
        config = {
            'remote': 'vpn.example.com',
            'port': '1194',
            'proto': 'udp',
            'dev': 'tun0',
            'cipher': 'AES-256-CBC',
            'auth': 'SHA256',
        }
        summary = config_summary(config)
        self.assertEqual(summary['Server'], 'vpn.example.com')
        self.assertEqual(summary['Port'], '1194')
        self.assertEqual(summary['Protocol'], 'udp')
        self.assertEqual(summary['Device'], 'tun0')
        self.assertEqual(summary['Cipher'], 'AES-256-CBC')
        self.assertEqual(summary['Auth'], 'SHA256')

    def test_compression_shown_when_enabled(self):
        config = {'remote': 'vpn.example.com', 'comp-lzo': 'yes'}
        summary = config_summary(config)
        self.assertEqual(summary['Compression'], 'yes')

    def test_compression_hidden_when_disabled(self):
        config = {'remote': 'vpn.example.com', 'comp-lzo': 'no'}
        summary = config_summary(config)
        self.assertNotIn('Compression', summary)

    def test_auth_user_pass_shown(self):
        config = {'remote': 'vpn.example.com', 'auth-user-pass': 'true'}
        summary = config_summary(config)
        self.assertEqual(summary['Auth Type'], 'User/Password')

    def test_empty_config(self):
        summary = config_summary({})
        self.assertEqual(len(summary), 0)


class TestValidateVpnConfig(unittest.TestCase):
    def test_valid_config(self):
        config = {
            'remote': 'vpn.example.com',
            'port': '1194',
            'ca': '---CA---',
            'cert': '---CERT---',
            'key': '---KEY---',
        }
        errors = validate_vpn_config(config)
        self.assertEqual(len(errors), 0)

    def test_missing_remote(self):
        config = {'port': '1194', 'ca': 'x', 'cert': 'x', 'key': 'x'}
        errors = validate_vpn_config(config)
        self.assertTrue(any('remote' in e.lower() for e in errors))

    def test_missing_port(self):
        config = {'remote': 'vpn.example.com', 'ca': 'x', 'cert': 'x', 'key': 'x'}
        errors = validate_vpn_config(config)
        self.assertTrue(any('port' in e.lower() for e in errors))

    def test_missing_ca(self):
        config = {'remote': 'vpn.example.com', 'port': '1194', 'cert': 'x', 'key': 'x'}
        errors = validate_vpn_config(config)
        self.assertTrue(any('ca' in e.lower() for e in errors))

    def test_missing_cert(self):
        config = {'remote': 'vpn.example.com', 'port': '1194', 'ca': 'x', 'key': 'x'}
        errors = validate_vpn_config(config)
        self.assertTrue(any('certificate' in e.lower() for e in errors))

    def test_missing_key(self):
        config = {'remote': 'vpn.example.com', 'port': '1194', 'ca': 'x', 'cert': 'x'}
        errors = validate_vpn_config(config)
        self.assertTrue(any('key' in e.lower() for e in errors))

    def test_empty_config(self):
        errors = validate_vpn_config({})
        self.assertEqual(len(errors), 5)


if __name__ == '__main__':
    unittest.main()