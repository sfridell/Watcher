import os
import json
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
    
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
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048
        )
        pem_private_key = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        pem_public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        os.makedirs("./keyfiles", exist_ok=True)
        with open("./keyfiles/{name}_rsa", "wb") as f:
            f.write(pem_private_key)
        with open("./keyfiles/{name}_rsa.pub", "wb") as f:
            f.write(pem_public_key)
            
        
    def new_connection(self, args, output):
        ''' Setup a new connection to a router, and save the connections object
            -- generate key pair
            -- use login credentials to upload pub key and install
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

        
