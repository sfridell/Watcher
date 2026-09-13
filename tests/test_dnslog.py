import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import connectiondb
import watcher
from crypto_helpers import encrypt_secret, decrypt_secret
from dnslog.mock import MockDnsLog, period_seconds
from dnslog.pihole import PiHoleDnsLog, BLOCKED_STATUSES


def _table_rows(output):
    """Parse a tabulate 'simple' table from a StringIO into list-of-lists of strings."""
    lines = output.getvalue().strip().split('\n')
    if len(lines) < 3:
        return []
    return [line.split() for line in lines[2:]]


class _FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else (json.dumps(payload) if payload else "")

    def json(self):
        return self._payload


class TestPeriodSeconds(unittest.TestCase):
    def test_units(self):
        self.assertEqual(period_seconds('1h'), 3600)
        self.assertEqual(period_seconds('24h'), 86400)
        self.assertEqual(period_seconds('7d'), 7 * 86400)
        self.assertEqual(period_seconds('30m'), 1800)
        self.assertEqual(period_seconds('10s'), 10)

    def test_invalid(self):
        for bad in ('', 'abc', '5y', 'h'):
            with self.assertRaises(ValueError):
                period_seconds(bad)


class TestCryptoHelpers(unittest.TestCase):
    def test_round_trip(self):
        token, salt = encrypt_secret('1234', 'super-secret-password')
        self.assertEqual(decrypt_secret('1234', token, salt), 'super-secret-password')

    def test_wrong_pin(self):
        token, salt = encrypt_secret('1234', 'secret')
        with self.assertRaises(ValueError):
            decrypt_secret('0000', token, salt)

    def test_corrupt_salt(self):
        token, salt = encrypt_secret('1234', 'secret')
        with self.assertRaises(ValueError):
            decrypt_secret('1234', token, 'not-base64')

    def test_empty_pin(self):
        with self.assertRaises(ValueError):
            encrypt_secret('', 'secret')


class TestMockDnsLog(unittest.TestCase):
    def test_defaults_sorted_desc(self):
        h = MockDnsLog()
        lookups = h.get_dns_lookups(None, '24h')
        counts = [d['count'] for d in lookups]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertIn('192.168.1.10', {d['ip'] for d in lookups})

    def test_period_validated(self):
        h = MockDnsLog()
        with self.assertRaises(ValueError):
            h.get_dns_lookups(None, '5y')

    def test_blocks_separate(self):
        h = MockDnsLog()
        lookups = h.get_dns_lookups(None, '24h')
        blocks = h.get_dns_blocks(None, '24h')
        # an IP that exists only in blocks shouldn't appear in lookups
        lookup_ips = {d['ip'] for d in lookups}
        self.assertIn('192.168.1.30', {d['ip'] for d in blocks})
        self.assertNotIn('192.168.1.30', lookup_ips)


class TestConnectionDBDnsLog(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_cwd = os.getcwd()
        os.chdir(self.tmpdir)
        with open('connections.json', 'w') as f:
            json.dump({'test_router': {
                'ip': '192.168.1.1', 'port': '22',
                'username': 'root', 'router_type': 'mock',
            }}, f)

    def tearDown(self):
        os.chdir(self.orig_cwd)
        shutil.rmtree(self.tmpdir)

    def test_set_get_mock(self):
        db = connectiondb.ConnectionDB()
        db.set_dns_log('test_router', dns_type='mock')
        entry = db.get_dns_log('test_router')
        self.assertEqual(entry.get('type'), 'mock')
        self.assertNotIn('encrypted_apikey', entry)

    def test_set_pihole_encrypts_and_round_trip(self):
        db = connectiondb.ConnectionDB()
        db.set_dns_log('test_router', dns_type='pihole', ip='192.168.12.50',
                       apikey='pw-secret', pin='1234')
        with open('connections.json') as f:
            on_disk = f.read()
        # plaintext must never be written to disk
        self.assertNotIn('pw-secret', on_disk)
        entry = db.get_dns_log('test_router')
        self.assertEqual(entry['type'], 'pihole')
        self.assertIn('encrypted_apikey', entry)
        conn, handler = db.get_dns_log_handler('test_router', io_dummy(), pin='1234')
        self.assertIsInstance(handler, PiHoleDnsLog)
        self.assertEqual(conn['apikey'], 'pw-secret')
        self.assertEqual(conn['ip'], '192.168.12.50')

    def test_wrong_pin_returns_none(self):
        db = connectiondb.ConnectionDB()
        db.set_dns_log('test_router', dns_type='pihole', ip='1.2.3.4',
                       apikey='pw', pin='1234')
        out = io_dummy()
        conn, handler = db.get_dns_log_handler('test_router', out, pin='0000')
        self.assertIsNone(conn)
        self.assertIn('incorrect PIN', out.getvalue())

    def test_missing_pin_required(self):
        db = connectiondb.ConnectionDB()
        db.set_dns_log('test_router', dns_type='pihole', ip='1.2.3.4',
                       apikey='pw', pin='1234')
        out = io_dummy()
        conn, handler = db.get_dns_log_handler('test_router', out, pin=None)
        self.assertIsNone(conn)
        self.assertIn('PIN is required', out.getvalue())

    def test_no_endpoint_configured(self):
        db = connectiondb.ConnectionDB()
        out = io_dummy()
        conn, handler = db.get_dns_log_handler('test_router', out)
        self.assertIsNone(conn)
        self.assertIn('no DNS-log endpoint', out.getvalue())

    def test_set_requires_apikey_for_pihole(self):
        db = connectiondb.ConnectionDB()
        with self.assertRaises(ValueError):
            db.set_dns_log('test_router', dns_type='pihole', ip='1.2.3.4', pin='1')

    def test_plaintext_storage_no_pin(self):
        """Without a PIN the apikey is stored in plaintext (revocable-token mode)."""
        db = connectiondb.ConnectionDB()
        db.set_dns_log('test_router', dns_type='pihole', ip='1.2.3.4',
                       apikey='tok-plain', pin=None)
        with open('connections.json') as f:
            on_disk = f.read()
        self.assertIn('tok-plain', on_disk)  # plaintext by design
        entry = db.get_dns_log('test_router')
        self.assertIn('apikey', entry)
        self.assertNotIn('encrypted_apikey', entry)
        conn, handler = db.get_dns_log_handler('test_router', io_dummy(), pin=None)
        self.assertEqual(conn['apikey'], 'tok-plain')

    def test_encrypted_storage_with_pin(self):
        """With a PIN the apikey is encrypted; plaintext is not on disk."""
        db = connectiondb.ConnectionDB()
        db.set_dns_log('test_router', dns_type='pihole', ip='1.2.3.4',
                       apikey='tok-secret', pin='1234')
        with open('connections.json') as f:
            on_disk = f.read()
        self.assertNotIn('tok-secret', on_disk)
        conn, handler = db.get_dns_log_handler('test_router', io_dummy(), pin='1234')
        self.assertEqual(conn['apikey'], 'tok-secret')

    def test_show_does_not_leak_plaintext_key(self):
        db = connectiondb.ConnectionDB()
        db.set_dns_log('test_router', dns_type='pihole', ip='1.2.3.4',
                       apikey='leak-me-please', pin=None)
        out = watcher.process_command(['dns-log', 'show', '--connection', 'test_router'])
        self.assertNotIn('leak-me-please', out.getvalue())
        self.assertIn('plaintext', out.getvalue())

    def test_set_nonexistent_connection(self):
        db = connectiondb.ConnectionDB()
        with self.assertRaises(ValueError):
            db.set_dns_log('nope', dns_type='mock')

    def test_delete(self):
        db = connectiondb.ConnectionDB()
        db.set_dns_log('test_router', dns_type='mock')
        self.assertTrue(db.get_dns_log('test_router'))
        db.delete_dns_log('test_router')
        self.assertEqual(db.get_dns_log('test_router'), {})


class TestCliMock(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_cwd = os.getcwd()
        os.chdir(self.tmpdir)
        with open('connections.json', 'w') as f:
            json.dump({'r': {'ip': 'mock', 'port': '0',
                             'username': 'mock', 'router_type': 'mock'}}, f)

    def tearDown(self):
        os.chdir(self.orig_cwd)
        shutil.rmtree(self.tmpdir)

    def test_set_lookups_blocks(self):
        out = watcher.process_command(['dns-log', 'set', '--connection', 'r', '--type', 'mock'])
        self.assertIn('configured', out.getvalue())

        out = watcher.process_command(['dns-log', 'lookups', '--connection', 'r', '--period', '24h'])
        rows = _table_rows(out)
        ips = {r[0] for r in rows}
        self.assertIn('192.168.1.10', ips)
        for r in rows:
            self.assertTrue(r[1].isdigit())

        out = watcher.process_command(['dns-log', 'blocks', '--connection', 'r', '--period', '1h'])
        rows = _table_rows(out)
        self.assertIn('192.168.1.30', {r[0] for r in rows})

    def test_show_and_clear(self):
        watcher.process_command(['dns-log', 'set', '--connection', 'r', '--type', 'mock'])
        out = watcher.process_command(['dns-log', 'show', '--connection', 'r'])
        data = json.loads(out.getvalue())
        self.assertEqual(data['type'], 'mock')
        watcher.process_command(['dns-log', 'clear', '--connection', 'r'])
        out = watcher.process_command(['dns-log', 'show', '--connection', 'r'])
        self.assertIn('No DNS-log endpoint', out.getvalue())


class TestPiHoleAggregation(unittest.TestCase):
    """Unit-test PiHoleDnsLog aggregation with mocked HTTP."""

    def _make_queries(self):
        return [
            {'client': {'ip': '10.0.0.1'}, 'status': 'FORWARDED'},
            {'client': {'ip': '10.0.0.1'}, 'status': 'CACHE'},
            {'client': {'ip': '10.0.0.2'}, 'status': 'GRAVITY'},
            {'client': {'ip': '10.0.0.2'}, 'status': 'REGEX'},
            {'client': {'ip': '10.0.0.2'}, 'status': 'FORWARDED'},
            {'client': {'ip': '10.0.0.3'}, 'status': 'DENYLIST'},
        ]

    def test_block_status_classification(self):
        # sanity-check the BLOCKED_STATUSES set covers what we assert below
        self.assertIn('GRAVITY', BLOCKED_STATUSES)
        self.assertIn('REGEX', BLOCKED_STATUSES)
        self.assertIn('DENYLIST', BLOCKED_STATUSES)
        self.assertNotIn('FORWARDED', BLOCKED_STATUSES)
        self.assertNotIn('CACHE', BLOCKED_STATUSES)

    def test_aggregate_lookups(self):
        h = PiHoleDnsLog()
        result = h._aggregate(self._make_queries(), blocked=False)
        by_ip = {d['ip']: d['count'] for d in result}
        self.assertEqual(by_ip, {'10.0.0.1': 2, '10.0.0.2': 1})

    def test_aggregate_blocks(self):
        h = PiHoleDnsLog()
        result = h._aggregate(self._make_queries(), blocked=True)
        by_ip = {d['ip']: d['count'] for d in result}
        self.assertEqual(by_ip, {'10.0.0.2': 2, '10.0.0.3': 1})


def _auth_payload(sid='sid=', csrf='csrf='):
    return {
        'session': {'valid': True, 'sid': sid, 'csrf': csrf, 'validity': 300},
        'took': 0.0,
    }


class TestPiHoleHandlerHttp(unittest.TestCase):
    """Mock requests to verify auth + pagination plumbing of get_dns_lookups."""

    def setUp(self):
        self.h = PiHoleDnsLog()
        self.conn = {'type': 'pihole', 'ip': '192.168.12.50', 'apikey': 'pw'}

    def _patch(self, responses):
        it = iter(responses)

        def fake_request(method, url, **kwargs):
            resp = next(it)
            return resp

        return mock.patch('dnslog.pihole.requests.request', side_effect=fake_request), fake_request

    def _patch_specific(self, post_resp, get_resp, delete_resp=None):
        post = mock.patch('dnslog.pihole.requests.post', return_value=post_resp)
        get = mock.patch('dnslog.pihole.requests.get', return_value=get_resp)
        dele = mock.patch('dnslog.pihole.requests.delete',
                          return_value=delete_resp or _FakeResp(204))
        return post, get, dele

    def test_get_dns_lookups_auth_and_paginate(self):
        # v6 semantics: request cursor = inclusive upper id bound; response
        # cursor = snapshot marker. Page 2 is requested with cursor=1001
        # (oldest id of page 1) and re-sends the boundary row (id 1001).
        queries_page1 = [{'id': 1000 + i, 'client': {'ip': '10.0.0.1'}, 'status': 'FORWARDED'}
                         for i in range(1, 1001)]
        queries_page2 = [{'id': 1001 - i, 'client': {'ip': '10.0.0.2'}, 'status': 'FORWARDED'}
                         for i in range(0, 6)]
        post_resp = _FakeResp(200, _auth_payload())
        get_resp1 = _FakeResp(200, {'queries': queries_page1, 'cursor': 2000})
        get_resp2 = _FakeResp(200, {'queries': queries_page2, 'cursor': 1001})
        captured = []

        def fake_get(url, params=None, **kwargs):
            captured.append(params)
            return [get_resp1, get_resp2][len(captured) - 1]

        post = mock.patch('dnslog.pihole.requests.post', return_value=post_resp)
        get = mock.patch('dnslog.pihole.requests.get', side_effect=fake_get)
        dele = mock.patch('dnslog.pihole.requests.delete', return_value=_FakeResp(204))
        with post, get, dele:
            result = self.h.get_dns_lookups(self.conn, '24h')
        by_ip = {d['ip']: d['count'] for d in result}
        self.assertEqual(by_ip, {'10.0.0.1': 1000, '10.0.0.2': 5})
        # second page must continue below the first page's oldest id
        self.assertEqual(captured[0].get('cursor'), None)
        self.assertEqual(captured[1].get('cursor'), 1001)

    def test_pagination_terminates_on_repeated_page(self):
        # Regression: a server that keeps returning the same full page and
        # echoes the same cursor (as the old code caused by feeding the
        # response cursor back) must not produce an infinite loop.
        queries_page = [{'id': 1000 + i, 'client': {'ip': '10.0.0.1'}, 'status': 'FORWARDED'}
                        for i in range(1, 1001)]
        get_resp = _FakeResp(200, {'queries': queries_page, 'cursor': 2000})
        calls = []

        def fake_get(url, params=None, **kwargs):
            calls.append(params)
            return get_resp

        post = mock.patch('dnslog.pihole.requests.post', return_value=_FakeResp(200, _auth_payload()))
        get = mock.patch('dnslog.pihole.requests.get', side_effect=fake_get)
        dele = mock.patch('dnslog.pihole.requests.delete', return_value=_FakeResp(204))
        with post, get, dele:
            result = self.h.get_dns_lookups(self.conn, '24h')
        self.assertEqual(result, [{'ip': '10.0.0.1', 'count': 1000}])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].get('cursor'), 1001)

    def test_401_triggers_relogin(self):
        queries = [{'client': {'ip': '10.0.0.1'}, 'status': 'FORWARDED'}]
        post1 = _FakeResp(200, _auth_payload(sid='sid1'))
        post2 = _FakeResp(200, _auth_payload(sid='sid2'))
        get_unauth = _FakeResp(401, {'error': {'key': 'unauthorized', 'message': 'Unauthorized'}})
        get_ok = _FakeResp(200, {'queries': queries, 'cursor': None})
        post = mock.patch('dnslog.pihole.requests.post', side_effect=[post1, post2])
        get = mock.patch('dnslog.pihole.requests.get', side_effect=[get_unauth, get_ok])
        dele = mock.patch('dnslog.pihole.requests.delete', return_value=_FakeResp(204))
        with post, get, dele:
            result = self.h.get_dns_lookups(self.conn, '1h')
        self.assertEqual(result, [{'ip': '10.0.0.1', 'count': 1}])

    def test_auth_failure_raises(self):
        post_resp = _FakeResp(401, {'error': {'key': 'unauthorized', 'message': 'Unauthorized'}})
        post = mock.patch('dnslog.pihole.requests.post', return_value=post_resp)
        with post:
            with self.assertRaises(Exception) as ctx:
                self.h.get_dns_lookups(self.conn, '1h')
        self.assertIn('auth failed', str(ctx.exception))


class io_dummy:
    def __init__(self):
        import io
        self._b = io.StringIO()

    def write(self, s):
        self._b.write(s)

    def getvalue(self):
        return self._b.getvalue()


class TestMockDnsLogDomains(unittest.TestCase):
    def test_blocks_by_domain(self):
        h = MockDnsLog()
        result = h.get_dns_blocks_by_domain(None, '24h')
        by_domain = {d['domain']: d['count'] for d in result}
        self.assertEqual(by_domain['ads.evil.com'], 50)
        self.assertEqual(by_domain['tracker.net'], 30)
        self.assertEqual(by_domain['malware.org'], 10)
        # sorted desc
        counts = [d['count'] for d in result]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_lookups_for_client(self):
        h = MockDnsLog()
        result = h.get_dns_lookups_for_client(None, '24h', '192.168.1.10')
        by_domain = {d['domain']: d['count'] for d in result}
        self.assertEqual(by_domain, {'google.com': 80, 'facebook.com': 62})

    def test_blocks_for_client(self):
        h = MockDnsLog()
        result = h.get_dns_blocks_for_client(None, '24h', '192.168.1.10')
        by_domain = {d['domain']: d['count'] for d in result}
        self.assertEqual(by_domain, {'ads.evil.com': 8, 'tracker.net': 4})

    def test_lookups_for_client_empty(self):
        h = MockDnsLog()
        result = h.get_dns_lookups_for_client(None, '24h', '10.0.0.99')
        self.assertEqual(result, [])


class TestPiHoleV6DomainAggregation(unittest.TestCase):
    def test_aggregate_by_domain(self):
        queries = [
            {"domain": "ads.evil", "status": "GRAVITY", "client": {"ip": "10.0.0.1"}},
            {"domain": "ads.evil", "status": "GRAVITY", "client": {"ip": "10.0.0.2"}},
            {"domain": "ok.com", "status": "FORWARDED", "client": {"ip": "10.0.0.1"}},
        ]
        result = PiHoleDnsLog._aggregate_by_domain(queries, blocked=True)
        self.assertEqual(result, [{"domain": "ads.evil", "count": 2}])

    def test_aggregate_by_domain_client_filter(self):
        queries = [
            {"domain": "ads.evil", "status": "GRAVITY", "client": {"ip": "10.0.0.1"}},
            {"domain": "ads.evil", "status": "GRAVITY", "client": {"ip": "10.0.0.2"}},
            {"domain": "ok.com", "status": "FORWARDED", "client": {"ip": "10.0.0.1"}},
        ]
        result = PiHoleDnsLog._aggregate_by_domain(queries, blocked=False, client_ip="10.0.0.1")
        self.assertEqual(result, [{"domain": "ok.com", "count": 1}])


class TestCliBlockedAndClient(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_cwd = os.getcwd()
        os.chdir(self.tmpdir)
        with open('connections.json', 'w') as f:
            json.dump({'r': {'ip': 'mock', 'port': '0',
                             'username': 'mock', 'router_type': 'mock'}}, f)

    def tearDown(self):
        os.chdir(self.orig_cwd)
        shutil.rmtree(self.tmpdir)

    def test_blocked_command(self):
        watcher.process_command(['dns-log', 'set', '--connection', 'r', '--type', 'mock'])
        out = watcher.process_command(['dns-log', 'blocked', '--connection', 'r', '--period', '24h'])
        rows = _table_rows(out)
        domains = {r[0] for r in rows}
        self.assertIn('ads.evil.com', domains)
        self.assertIn('tracker.net', domains)

    def test_blocked_limit(self):
        watcher.process_command(['dns-log', 'set', '--connection', 'r', '--type', 'mock'])
        out = watcher.process_command(['dns-log', 'blocked', '--connection', 'r', '--limit', '1'])
        rows = _table_rows(out)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 'ads.evil.com')

    def test_lookups_with_client(self):
        watcher.process_command(['dns-log', 'set', '--connection', 'r', '--type', 'mock'])
        out = watcher.process_command([
            'dns-log', 'lookups', '--connection', 'r',
            '--client', '192.168.1.10', '--limit', '5',
        ])
        rows = _table_rows(out)
        domains = {r[0] for r in rows}
        self.assertIn('google.com', domains)
        self.assertIn('facebook.com', domains)

    def test_blocks_with_client(self):
        watcher.process_command(['dns-log', 'set', '--connection', 'r', '--type', 'mock'])
        out = watcher.process_command([
            'dns-log', 'blocks', '--connection', 'r',
            '--client', '192.168.1.10', '--limit', '5',
        ])
        rows = _table_rows(out)
        domains = {r[0] for r in rows}
        self.assertIn('ads.evil.com', domains)
        self.assertIn('tracker.net', domains)


if __name__ == '__main__':
    unittest.main()
