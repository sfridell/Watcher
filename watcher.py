"""
Tool to monitor home network
"""
import connectiondb
import argparse
import sys
import io
import fabric

def list_clients(args, connections, output):
    '''Connect to named router and list the connected clients
    '''
    conn = connections.get_connection(args.connection, output)
    result = conn.run('cat /tmp/dnsmasq.leases')

def list_static_leases(args, connections, output):
    '''Connect to named router and list the configured static dhcp
       leases
    '''
    conn = connections.get_connection(args.connection, output)
    result = conn.run('nvram show | grep static_leases')

def get_args(argv):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')

    parser_connections = subparsers.add_parser('connections')
    parser_connections_commands = parser_connections.add_subparsers(dest='connections_command')
    parser_connections_list = parser_connections_commands.add_parser('list')
    parser_connections_new = parser_connections_commands.add_parser('new')
    parser_connections_new.add_argument("--ip", type=str)
    parser_connections_new.add_argument("--port", type=str)
    parser_connections_new.add_argument("--name", type=str)
    parser_connections_new.add_argument("--username", type=str)
    parser_connections_new.add_argument("--pw", type=str)

    parser_clients = subparsers.add_parser('clients')
    parser_clients_commands = parser_clients.add_subparsers(dest='clients_command')
    parser_clients_list = parser_clients_commands.add_parser('list')
    parser_clients_list.add_argument("--connection", type=str)
    parser_clients_list = parser_clients_commands.add_parser('static-leases')
    parser_clients_list.add_argument("--connection", type=str)
    
    return parser.parse_args(argv)


def process_command(argv = sys.argv[1:]):
    ''' Main program -- if invoked from command line, otherwise, an entry point for UI
    '''
    connections = connectiondb.ConnectionDB()
    args = get_args(argv)
    output = io.StringIO()
    
    if args.command == 'clients':
        if args.clients_command == 'list':
            list_clients(args, connections, output)
        if args.clients_command == 'static-leases':
            list_static_leases(args, connections, output)
    if args.command == 'connections':
        if args.connections_command == 'list':
            connections.list_connections(output)
        if args.connections_command == 'new':
            connections.new_connection(args, output)
                
    return output

if __name__ == '__main__':
    print(process_command().getvalue())

