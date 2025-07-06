"""
Tool to monitor home network
"""
import connectiondb
import argparse
import sys
import io
import fabric

def get_args(argv):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')

    parser_connections = subparsers.add_parser('connections')
    parser_connections_commands = parser_connections.add_subparsers(dest='connectons_command')
    parser_connections_list = parser_connections_commands.add_parser('list')
    parser_connections_new = parser_connections_commands.add_parser('new')
    parser_connections_new.add_argument("--ip", type=str)
    parser_connections_new.add_argument("--port", type=str)
    parser_connections_new.add_argument("--name", type=str)
    parser_connections_new.add_argument("--username", type=str)
    parser_connections_new.add_argument("--prompt-pw", action='store_true')
    parser_connections_new.add_argument("--pw", type=str)

    parser_clients = subparsers.add_parser('clients')
    parser_clients_commands = parser_clients.add_subparsers(dest='clients_command')
    parser_clients_list = parser_clients_commands.add_parser('list')

    return parser.parse_args(argv)


def process_command(argv = sys.argv[1:]):
''' Main program -- if invoked from command line, otherwise, an entry point for UI
'''
    connections = ConnectionDB()
    args = get_args(argv)
    output = io.StringIO()
    
    if args.command == 'clients':
        if args.clients_command == 'list':
            list_clients(file=output)

    if args.command == 'connections':
        if args.connections_command == 'list':
            connections.list_connections(output)
        if args.connections_command == 'new':
            connections.new_connection(args, output)
                
    return output

if __name__ == '__main__':
    print(process_command().getvalue())

