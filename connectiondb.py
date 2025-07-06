import os
import json
import getpass
from fabric import Connection
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

class ConnectionDB:
    def __init__(self):
        ''' check for existence of connection file and load if present
        '''
        dbpath = './connections.json'
        if os.path.exists(dbpath):
            with open(dbpath, 'r') as file:
                self.connections = json.load(file)
        else:
            self.connections = {}

    def _generate_and_save_key_pair(self, name):
        ''' Generate a rsa key pair and save to files in ./keyfiles directory
        '''
        key = rsa.generate_private_key(
            backend = default_backend(),
            public_exponent = 65537,
            key_size = 2048
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
        ''' Create a fabric connection to the named entity
        '''
        if name not in self.connections:
            print(f'ERROR: connection to {name} does not exist', file=output)
            return
        md = self.connections[name]
        c = Connection(host=md['ip'], user=md['username'], port=md['port'],
                       connect_kwargs={
                           "key_filename": f"./keyfiles/{name}_rsa",
                           "disabled_algorithms": dict(pubkeys=["rsa-sha2-512", "rsa-sha2-256"]),
                       })
        return c
        
    def new_connection(self, args, output):
        ''' Setup a new connection to a router, and save the connections object
            -- generate key pair
            -- use login credentials to upload pub key and install
            -- TODO: for now ask user to cut and paste the key text
            -- save connection metadata
        '''
        if args.name in self.connections:
            print(f'ERROR: connection to {args.name} already exists', file=output)
            return

        # generate key pair and save
        try:
            self._generate_and_save_key_pair(args.name)
        except IOError as e:
            print(f"ERROR: writing to key file: {e}", file=output)

        # ask the user to cut and paste the key to the router accepted keys screen
        # TODO: we can automate this using selenium or some such thing
        print('Please cut and paste the following text to your router\'s authorized keys input:')
        with open(f"./keyfiles/{args.name}_rsa.pub", 'r') as f:
            print(f.read())

        # save the connection metadata
        self.connections[args.name] = { 'ip' : args.ip, 'port' : args.port, 'username' : args.username }
        self._save_connections()
        
    def list_connections(self, output):
        ''' List all the saved connections
        '''
        for s in self.connections:
            json.dump(s, output)
