import os
import json
import paramiko
from fabric import Connection
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from routers import get_router_handler

from crypto_helpers import encrypt_secret, decrypt_secret
from dnslog import get_dns_handler

if 'ssh-rsa' not in paramiko.Transport._preferred_keys:
    paramiko.Transport._preferred_keys = ('ssh-rsa',) + tuple(paramiko.Transport._preferred_keys)
if 'ssh-rsa' not in paramiko.Transport._preferred_pubkeys:
    paramiko.Transport._preferred_pubkeys = ('ssh-rsa',) + tuple(paramiko.Transport._preferred_pubkeys)
if 'ssh-rsa' not in paramiko.Transport._key_info:
    paramiko.Transport._key_info['ssh-rsa'] = paramiko.RSAKey
if 'ssh-rsa' not in paramiko.RSAKey.HASHES:
    paramiko.RSAKey.HASHES['ssh-rsa'] = hashes.SHA1

from paramiko.kex_group14 import KexGroup14SHA256
from paramiko.kex_gex import KexGexSHA256
import hashlib


class KexGroup1SHA1(KexGroup14SHA256):
    name = "diffie-hellman-group1-sha1"
    hash_algo = hashlib.sha1
    P = 0xFFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE65381FFFFFFFFFFFFFFFF  # noqa
    G = 2


class KexGroup14SHA1(KexGroup14SHA256):
    name = "diffie-hellman-group14-sha1"
    hash_algo = hashlib.sha1


class KexGexSHA1(KexGexSHA256):
    name = "diffie-hellman-group-exchange-sha1"
    hash_algo = hashlib.sha1


if 'diffie-hellman-group1-sha1' not in paramiko.Transport._kex_info:
    paramiko.Transport._kex_info['diffie-hellman-group1-sha1'] = KexGroup1SHA1
if 'diffie-hellman-group14-sha1' not in paramiko.Transport._kex_info:
    paramiko.Transport._kex_info['diffie-hellman-group14-sha1'] = KexGroup14SHA1
if 'diffie-hellman-group-exchange-sha1' not in paramiko.Transport._kex_info:
    paramiko.Transport._kex_info['diffie-hellman-group-exchange-sha1'] = KexGexSHA1

for _kex in ('diffie-hellman-group1-sha1', 'diffie-hellman-group14-sha1'):
    if _kex not in paramiko.Transport._preferred_kex:
        paramiko.Transport._preferred_kex = (_kex,) + tuple(paramiko.Transport._preferred_kex)


class _MockConnection:
    """Placeholder connection object for MockRouter. Carries no state since the mock handler manages its own."""

    pass


class _MockDnsConnection:
    """Placeholder connection object for the mock DNS-log handler."""

    pass


class ConnectionDB:
    """Manages saved router connection profiles and creates live connections (Fabric SSH or mock)."""

    def __init__(self):
        if os.path.exists('./connections.json'):
            with open('./connections.json', 'r') as file:
                self.connections = json.load(file)
        else:
            self.connections = {}

    def _generate_and_save_key_pair(self, name):
        key = rsa.generate_private_key(
            backend=default_backend(),
            public_exponent=65537,
            key_size=2048
        )
        private_key = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()
        )
        public_key = key.public_key().public_bytes(
            serialization.Encoding.OpenSSH,
            serialization.PublicFormat.OpenSSH
        )
        os.makedirs("./keyfiles", exist_ok=True)
        with open(f"./keyfiles/{name}_rsa", "wb") as f:
            f.write(private_key)
        with open(f"./keyfiles/{name}_rsa.pub", "wb") as f:
            f.write(public_key)

    def _save_connections(self):
        with open('./connections.json', 'w') as f:
            json.dump(self.connections, f)

    def get_connection(self, name, output):
        """Create a Fabric SSH connection (or _MockConnection for mock routers) for the named profile."""
        if name not in self.connections:
            print(f'ERROR: connection to {name} does not exist', file=output)
            return
        md = self.connections[name]
        if md.get('router_type') == 'mock':
            return _MockConnection()
        c = Connection(host=md['ip'], user=md['username'], port=md['port'],
                       connect_kwargs={
                           "pkey": paramiko.RSAKey.from_private_key_file(f"./keyfiles/{name}_rsa"),
                           "disabled_algorithms": dict(pubkeys=["rsa-sha2-512", "rsa-sha2-256"]),
                           "look_for_keys": False,
                           "allow_agent": False,
                       })
        return c

    def get_connection_with_handler(self, name, output):
        """Return a (connection, router_handler) pair for the named profile. Uses _MockConnection for mock routers."""
        if name not in self.connections:
            print(f'ERROR: connection to {name} does not exist', file=output)
            return None, None
        md = self.connections[name]
        router_type = md.get('router_type', 'ddwrt')
        handler = get_router_handler(router_type, name=name)
        if router_type == 'mock':
            return _MockConnection(), handler
        c = Connection(host=md['ip'], user=md['username'], port=md['port'],
                       connect_kwargs={
                           "pkey": paramiko.RSAKey.from_private_key_file(f"./keyfiles/{name}_rsa"),
                           "disabled_algorithms": dict(pubkeys=["rsa-sha2-512", "rsa-sha2-256"]),
                           "look_for_keys": False,
                           "allow_agent": False,
                       })
        return c, handler

    def provision_ssh_keys(self, name, ip, port, username, password, output,
                           router_type="ddwrt"):
        """Generate an RSA key pair, connect to the router via password auth,
        and install the public key so SSH key-based auth works on subsequent
        connections.

        DD-WRT routers also get the key stored in NVRAM (``sshd_authorized_keys``)
        so it survives reboot; OpenWrt routers have no NVRAM, so the key is
        written to dropbear's ``authorized_keys`` location instead.
        """
        self._generate_and_save_key_pair(name)

        with open(f"./keyfiles/{name}_rsa.pub", 'r') as f:
            pub_key = f.read().strip()

        conn = Connection(
            host=ip, user=username, port=int(port),
            connect_kwargs={
                "password": password,
                "disabled_algorithms": dict(pubkeys=["rsa-sha2-512", "rsa-sha2-256"]),
            },
        )
        try:
            if router_type == "ddwrt" or router_type.startswith("ddwrt"):
                conn.run(f'nvram set sshd_authorized_keys="{pub_key}"', hide=True)
                conn.run('nvram commit', hide=True)
            home_dir = conn.run('echo $HOME', hide=True).stdout.strip() or '/tmp/root'
            conn.run(f'mkdir -p {home_dir}/.ssh && echo "{pub_key}" >> {home_dir}/.ssh/authorized_keys && chmod 600 {home_dir}/.ssh/authorized_keys', hide=True)
            if router_type == "openwrt" or router_type.startswith("openwrt"):
                conn.run(f'mkdir -p /etc/dropbear && echo "{pub_key}" >> /etc/dropbear/authorized_keys && chmod 600 /etc/dropbear/authorized_keys', hide=True)
        except Exception as e:
            print(f"ERROR: failed to provision SSH key on router: {e}", file=output)
            raise
        finally:
            conn.close()

        verify_conn = Connection(
            host=ip, user=username, port=int(port),
            connect_kwargs={
                "pkey": paramiko.RSAKey.from_private_key_file(f"./keyfiles/{name}_rsa"),
                "disabled_algorithms": dict(pubkeys=["rsa-sha2-512", "rsa-sha2-256"]),
                "look_for_keys": False,
                "allow_agent": False,
            },
        )
        try:
            verify_conn.run('echo ok', hide=True)
            print(f'SSH key provisioned successfully for {name}', file=output)
        except Exception as e:
            print(f"WARNING: SSH key verification failed: {e}", file=output)
        finally:
            verify_conn.close()

    def new_connection(self, args, output):
        """Create a new connection profile. For real routers, requires --pw and auto-provisions SSH keys."""
        if args.name in self.connections:
            print(f'ERROR: connection to {args.name} already exists', file=output)
            return

        router_type = args.router_type

        if router_type != 'mock':
            if not getattr(args, 'pw', None):
                print('ERROR: --pw is required for non-mock router connections', file=output)
                return
            try:
                self.provision_ssh_keys(
                    args.name, args.ip, args.port, args.username, args.pw, output,
                    router_type=router_type,
                )
            except Exception:
                return

        self.connections[args.name] = {
            'ip': args.ip if router_type != 'mock' else 'mock',
            'port': args.port if router_type != 'mock' else '0',
            'username': args.username if router_type != 'mock' else 'mock',
            'router_type': router_type
        }
        self._save_connections()

    def list_connections(self, output):
        for s in self.connections:
            json.dump(s, output)

    def show_connection(self, args, output):
        json.dump(self.connections[args.connection], output)

    def get_vpn_configs(self, conn_name):
        if conn_name not in self.connections:
            return {}
        return self.connections[conn_name].get('vpn_configs', {})

    def add_vpn_config(self, conn_name, vpn_name, vpn_config):
        if conn_name not in self.connections:
            raise ValueError(f"Connection '{conn_name}' does not exist")
        if 'vpn_configs' not in self.connections[conn_name]:
            self.connections[conn_name]['vpn_configs'] = {}
        self.connections[conn_name]['vpn_configs'][vpn_name] = vpn_config
        self._save_connections()

    def delete_vpn_config(self, conn_name, vpn_name):
        if conn_name not in self.connections:
            raise ValueError(f"Connection '{conn_name}' does not exist")
        configs = self.connections[conn_name].get('vpn_configs', {})
        if vpn_name in configs:
            del configs[vpn_name]
            if self.connections[conn_name].get('active_vpn') == vpn_name:
                self.connections[conn_name]['active_vpn'] = ''
            self._save_connections()

    def get_active_vpn(self, conn_name):
        if conn_name not in self.connections:
            return ''
        return self.connections[conn_name].get('active_vpn', '')

    def set_active_vpn(self, conn_name, vpn_name):
        if conn_name not in self.connections:
            raise ValueError(f"Connection '{conn_name}' does not exist")
        self.connections[conn_name]['active_vpn'] = vpn_name
        self._save_connections()

    # -- DNS-log endpoint management --------------------------------
    def set_dns_log(self, conn_name, dns_type, ip=None, apikey=None, pin=None,
                    scheme=None):
        """Attach a DNS-log endpoint to an existing router connection.

        ``apikey`` (e.g. Pi-hole web/app password or revocable API token) is:
          - omitted (and not required) for ``mock`` endpoints,
          - stored in **plaintext** when no ``pin`` is supplied (convenient for
            revocable API tokens; the trade-off is at-rest exposure),
          - **encrypted** with the PIN via Scrypt+Fernet when a ``pin`` is
            supplied (the PIN itself is never stored).

        Stored shape within ``connections[conn_name]['dns_log']``::

            # plaintext (default)
            {"type": "pihole", "ip": "192.168.12.50", "apikey": "..."}
            # encrypted (opt-in via --pin)
            {"type": "pihole", "ip": "...", "encrypted_apikey": "<b64>", "salt": "<b64>"}
        """
        if conn_name not in self.connections:
            raise ValueError(f"Connection '{conn_name}' does not exist")
        entry = {'type': dns_type}
        if ip is not None:
            entry['ip'] = ip
        if scheme:
            entry['scheme'] = scheme
        if apikey:
            if pin:
                token, salt = encrypt_secret(pin, apikey)
                entry['encrypted_apikey'] = token
                entry['salt'] = salt
            else:
                entry['apikey'] = apikey
        elif dns_type not in ('mock',):
            raise ValueError(
                f"API key is required for dns-log type '{dns_type}'"
            )
        self.connections[conn_name]['dns_log'] = entry
        self._save_connections()

    def get_dns_log(self, conn_name):
        """Return the (still-encrypted) stored DNS-log entry, or ``{}``."""
        if conn_name not in self.connections:
            return {}
        return self.connections[conn_name].get('dns_log', {})

    def delete_dns_log(self, conn_name):
        if conn_name not in self.connections:
            raise ValueError(f"Connection '{conn_name}' does not exist")
        self.connections[conn_name].pop('dns_log', None)
        self._save_connections()

    def get_dns_log_handler(self, conn_name, output, pin=None):
        """Return a ``(conn, handler)`` pair for the connection's DNS-log endpoint.

        ``conn`` is the dict the handler consumes (with a decrypted ``apikey``
        when applicable). For mock endpoints it is the placeholder
        ``_MockDnsConnection``. Returns ``(None, None)`` and prints an error
        when no DNS-log endpoint is configured.
        """
        if conn_name not in self.connections:
            print(f'ERROR: connection to {conn_name} does not exist', file=output)
            return None, None
        entry = self.connections[conn_name].get('dns_log')
        if not entry:
            print(f'ERROR: no DNS-log endpoint configured for {conn_name}', file=output)
            return None, None
        dns_type = entry.get('type', '')
        handler = get_dns_handler(dns_type, name=conn_name)
        if dns_type == 'mock':
            return _MockDnsConnection(), handler
        # resolve apikey (encrypted -> PIN required; plaintext -> use directly)
        apikey = None
        if 'encrypted_apikey' in entry:
            if not pin:
                print('ERROR: PIN is required to access this DNS-log endpoint', file=output)
                return None, None
            try:
                apikey = decrypt_secret(pin, entry['encrypted_apikey'], entry['salt'])
            except ValueError as e:
                print(f'ERROR: {e}', file=output)
                return None, None
        elif 'apikey' in entry:
            apikey = entry['apikey']
        conn_dict = {k: v for k, v in entry.items()
                     if k not in ('encrypted_apikey', 'salt', 'apikey')}
        if apikey is not None:
            conn_dict['apikey'] = apikey
        return conn_dict, handler
