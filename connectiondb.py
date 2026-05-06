import os
import json
from fabric import Connection
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from routers import get_router_handler


class _MockConnection:
    pass


class ConnectionDB:
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
        if name not in self.connections:
            print(f'ERROR: connection to {name} does not exist', file=output)
            return
        md = self.connections[name]
        if md.get('router_type') == 'mock':
            return _MockConnection()
        c = Connection(host=md['ip'], user=md['username'], port=md['port'],
                       connect_kwargs={
                           "key_filename": f"./keyfiles/{name}_rsa",
                           "disabled_algorithms": dict(pubkeys=["rsa-sha2-512", "rsa-sha2-256"]),
                       })
        return c

    def get_connection_with_handler(self, name, output):
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
                           "key_filename": f"./keyfiles/{name}_rsa",
                           "disabled_algorithms": dict(pubkeys=["rsa-sha2-512", "rsa-sha2-256"]),
                       })
        return c, handler

    def new_connection(self, args, output):
        if args.name in self.connections:
            print(f'ERROR: connection to {args.name} already exists', file=output)
            return

        router_type = args.router_type

        if router_type != 'mock':
            try:
                self._generate_and_save_key_pair(args.name)
            except IOError as e:
                print(f"ERROR: writing to key file: {e}", file=output)

            print('Please cut and paste the following text to your router\'s authorized keys input:')
            with open(f"./keyfiles/{args.name}_rsa.pub", 'r') as f:
                print(f.read())

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
