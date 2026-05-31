import json
import os
import tempfile
import unittest
import connectiondb
import watcher
from routers.mock import MockRouter


class TestConnectionDBVpnConfig(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.conn_file = os.path.join(self.tmpdir, 'connections.json')
        self.orig_cwd = os.getcwd()
        os.chdir(self.tmpdir)
        with open('connections.json', 'w') as f:
            json.dump({
                'test_router': {
                    'ip': '192.168.1.1',
                    'port': '22',
                    'username': 'root',
                    'router_type': 'mock',
                },
            }, f)

    def tearDown(self):
        os.chdir(self.orig_cwd)
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_get_vpn_configs_empty(self):
        db = connectiondb.ConnectionDB()
        configs = db.get_vpn_configs('test_router')
        self.assertEqual(configs, {})

    def test_get_vpn_configs_nonexistent_connection(self):
        db = connectiondb.ConnectionDB()
        configs = db.get_vpn_configs('nonexistent')
        self.assertEqual(configs, {})

    def test_add_vpn_config(self):
        db = connectiondb.ConnectionDB()
        vpn_config = {'remote': 'vpn.example.com', 'port': '1194', 'proto': 'udp'}
        db.add_vpn_config('test_router', 'myvpn', vpn_config)
        configs = db.get_vpn_configs('test_router')
        self.assertIn('myvpn', configs)
        self.assertEqual(configs['myvpn']['remote'], 'vpn.example.com')

    def test_add_vpn_config_nonexistent_connection(self):
        db = connectiondb.ConnectionDB()
        with self.assertRaises(ValueError):
            db.add_vpn_config('nonexistent', 'myvpn', {})

    def test_add_multiple_vpn_configs(self):
        db = connectiondb.ConnectionDB()
        db.add_vpn_config('test_router', 'vpn1', {'remote': 'vpn1.example.com'})
        db.add_vpn_config('test_router', 'vpn2', {'remote': 'vpn2.example.com'})
        configs = db.get_vpn_configs('test_router')
        self.assertEqual(len(configs), 2)
        self.assertIn('vpn1', configs)
        self.assertIn('vpn2', configs)

    def test_add_vpn_config_overwrite(self):
        db = connectiondb.ConnectionDB()
        db.add_vpn_config('test_router', 'myvpn', {'remote': 'old.example.com'})
        db.add_vpn_config('test_router', 'myvpn', {'remote': 'new.example.com'})
        configs = db.get_vpn_configs('test_router')
        self.assertEqual(configs['myvpn']['remote'], 'new.example.com')

    def test_delete_vpn_config(self):
        db = connectiondb.ConnectionDB()
        db.add_vpn_config('test_router', 'myvpn', {'remote': 'vpn.example.com'})
        db.delete_vpn_config('test_router', 'myvpn')
        configs = db.get_vpn_configs('test_router')
        self.assertNotIn('myvpn', configs)

    def test_delete_vpn_config_nonexistent_name(self):
        db = connectiondb.ConnectionDB()
        db.delete_vpn_config('test_router', 'nonexistent_vpn')
        configs = db.get_vpn_configs('test_router')
        self.assertEqual(configs, {})

    def test_delete_active_vpn_clears_reference(self):
        db = connectiondb.ConnectionDB()
        db.add_vpn_config('test_router', 'myvpn', {'remote': 'vpn.example.com'})
        db.set_active_vpn('test_router', 'myvpn')
        db.delete_vpn_config('test_router', 'myvpn')
        self.assertEqual(db.get_active_vpn('test_router'), '')

    def test_get_active_vpn_default(self):
        db = connectiondb.ConnectionDB()
        self.assertEqual(db.get_active_vpn('test_router'), '')

    def test_set_active_vpn(self):
        db = connectiondb.ConnectionDB()
        db.add_vpn_config('test_router', 'myvpn', {'remote': 'vpn.example.com'})
        db.set_active_vpn('test_router', 'myvpn')
        self.assertEqual(db.get_active_vpn('test_router'), 'myvpn')

    def test_set_active_vpn_nonexistent_connection(self):
        db = connectiondb.ConnectionDB()
        with self.assertRaises(ValueError):
            db.set_active_vpn('nonexistent', 'myvpn')

    def test_persistence(self):
        db = connectiondb.ConnectionDB()
        db.add_vpn_config('test_router', 'myvpn', {'remote': 'vpn.example.com', 'port': '1194'})
        db.set_active_vpn('test_router', 'myvpn')
        db2 = connectiondb.ConnectionDB()
        configs = db2.get_vpn_configs('test_router')
        self.assertIn('myvpn', configs)
        self.assertEqual(configs['myvpn']['remote'], 'vpn.example.com')
        self.assertEqual(db2.get_active_vpn('test_router'), 'myvpn')


class TestMockRouterVpnMethods(unittest.TestCase):
    def setUp(self):
        self.router = MockRouter()
        self.conn = None

    def test_get_vpn_status_default(self):
        status = self.router.get_vpn_status(self.conn)
        self.assertFalse(status['enabled'])
        self.assertFalse(status['connected'])
        self.assertEqual(status['remote'], '')

    def test_apply_vpn_config(self):
        config = {
            'openvpncl_remoteip': 'vpn.example.com',
            'openvpncl_remoteport': '1194',
            'openvpncl_proto': 'udp-client',
        }
        self.router.apply_vpn_config(self.conn, config)
        result = self.router.get_vpn_config(self.conn)
        self.assertEqual(result['openvpncl_remoteip'], 'vpn.example.com')

    def test_start_vpn(self):
        config = {
            'openvpncl_remoteip': 'vpn.example.com',
            'openvpncl_remoteport': '1194',
        }
        self.router.apply_vpn_config(self.conn, config)
        self.router.start_vpn(self.conn)
        status = self.router.get_vpn_status(self.conn)
        self.assertTrue(status['enabled'])
        self.assertTrue(status['connected'])

    def test_start_vpn_without_config_raises(self):
        with self.assertRaises(Exception):
            self.router.start_vpn(self.conn)

    def test_stop_vpn(self):
        config = {
            'openvpncl_remoteip': 'vpn.example.com',
            'openvpncl_remoteport': '1194',
        }
        self.router.apply_vpn_config(self.conn, config)
        self.router.start_vpn(self.conn)
        self.router.stop_vpn(self.conn)
        status = self.router.get_vpn_status(self.conn)
        self.assertFalse(status['enabled'])
        self.assertFalse(status['connected'])

    def test_start_vpn_sets_remote_from_config(self):
        config = {
            'openvpncl_remoteip': 'vpn.example.com',
            'openvpncl_remoteport': '443',
            'openvpncl_proto': 'tcp-client',
        }
        self.router.apply_vpn_config(self.conn, config)
        self.router.start_vpn(self.conn)
        status = self.router.get_vpn_status(self.conn)
        self.assertEqual(status['remote'], 'vpn.example.com')
        self.assertEqual(status['port'], '443')
        self.assertEqual(status['proto'], 'tcp-client')
        self.assertEqual(status['interface'], 'tun0')

    def test_stop_vpn_clears_interface(self):
        config = {
            'openvpncl_remoteip': 'vpn.example.com',
            'openvpncl_remoteport': '1194',
        }
        self.router.apply_vpn_config(self.conn, config)
        self.router.start_vpn(self.conn)
        self.router.stop_vpn(self.conn)
        status = self.router.get_vpn_status(self.conn)
        self.assertEqual(status['interface'], '')

    def test_get_vpn_config_empty(self):
        result = self.router.get_vpn_config(self.conn)
        self.assertEqual(result, {})

    def test_vpn_status_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import routers.mock as mock_module
            original_dir = mock_module._MOCK_STATE_DIR
            mock_module._MOCK_STATE_DIR = os.path.join(tmpdir, "mock_state")
            try:
                router = MockRouter(name="test_vpn_persist")
                config = {'openvpncl_remoteip': 'vpn.example.com'}
                router.apply_vpn_config(self.conn, config)
                router.start_vpn(self.conn)
                del router
                router2 = MockRouter(name="test_vpn_persist")
                status = router2.get_vpn_status(self.conn)
                self.assertTrue(status['enabled'])
                self.assertTrue(status['connected'])
            finally:
                mock_module._MOCK_STATE_DIR = original_dir


class TestWatcherVpnCliCommands(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_cwd = os.getcwd()
        os.chdir(self.tmpdir)
        with open('connections.json', 'w') as f:
            json.dump({
                'test_router': {
                    'ip': 'mock',
                    'port': '0',
                    'username': 'mock',
                    'router_type': 'mock',
                },
            }, f)

    def tearDown(self):
        os.chdir(self.orig_cwd)
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_vpn_status_command(self):
        output = watcher.process_command(['vpn', 'status', '--connection', 'test_router'])
        text = output.getvalue()
        self.assertIn('Enabled', text)
        self.assertIn('Connected', text)

    def test_vpn_config_show_empty(self):
        output = watcher.process_command(['vpn', 'show', '--connection', 'test_router'])
        text = output.getvalue()
        self.assertIn('No VPN configuration', text)

    def test_vpn_config_import(self):
        ovpn_content = "client\ndev tun\nproto udp\nremote vpn.example.com 1194\n"
        ovpn_path = os.path.join(self.tmpdir, 'test.ovpn')
        with open(ovpn_path, 'w') as f:
            f.write(ovpn_content)
        output = watcher.process_command([
            'vpn', 'import', '--connection', 'test_router',
            '--name', 'myvpn', '--ovpn-file', ovpn_path
        ])
        text = output.getvalue()
        self.assertIn('imported', text.lower())

    def test_vpn_config_list_after_import(self):
        ovpn_content = "client\ndev tun\nproto udp\nremote vpn.example.com 1194\n"
        ovpn_path = os.path.join(self.tmpdir, 'test.ovpn')
        with open(ovpn_path, 'w') as f:
            f.write(ovpn_content)
        watcher.process_command([
            'vpn', 'import', '--connection', 'test_router',
            '--name', 'myvpn', '--ovpn-file', ovpn_path
        ])
        output = watcher.process_command(['vpn', 'list', '--connection', 'test_router'])
        text = output.getvalue()
        self.assertIn('myvpn', text)

    def test_vpn_config_delete(self):
        db = connectiondb.ConnectionDB()
        db.add_vpn_config('test_router', 'myvpn', {'remote': 'vpn.example.com'})
        output = watcher.process_command([
            'vpn', 'delete', '--connection', 'test_router', '--name', 'myvpn'
        ])
        text = output.getvalue()
        self.assertIn('deleted', text.lower())

    def test_vpn_start_stop(self):
        db = connectiondb.ConnectionDB()
        db.add_vpn_config('test_router', 'myvpn', {
            'remote': 'vpn.example.com',
            'port': '1194',
            'proto': 'udp',
        })
        output = watcher.process_command([
            'vpn', 'apply', '--connection', 'test_router', '--config-name', 'myvpn'
        ])
        output = watcher.process_command(['vpn', 'start', '--connection', 'test_router'])
        text = output.getvalue()
        self.assertIn('started', text.lower())

        output = watcher.process_command(['vpn', 'stop', '--connection', 'test_router'])
        text = output.getvalue()
        self.assertIn('stopped', text.lower())

    def test_vpn_apply_with_config_name(self):
        db = connectiondb.ConnectionDB()
        db.add_vpn_config('test_router', 'myvpn', {
            'remote': 'vpn.example.com',
            'port': '1194',
            'proto': 'udp',
        })
        output = watcher.process_command([
            'vpn', 'apply', '--connection', 'test_router', '--config-name', 'myvpn'
        ])
        text = output.getvalue()
        self.assertIn('applied', text.lower())

    def test_vpn_apply_nonexistent_config(self):
        output = watcher.process_command([
            'vpn', 'apply', '--connection', 'test_router', '--config-name', 'nonexistent'
        ])
        text = output.getvalue()
        self.assertIn('not found', text.lower())

    def test_vpn_apply_no_args(self):
        output = watcher.process_command([
            'vpn', 'apply', '--connection', 'test_router'
        ])
        text = output.getvalue()
        self.assertIn('must be specified', text.lower())


if __name__ == '__main__':
    unittest.main()