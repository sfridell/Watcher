"""
Tool to monitor home network
"""
import connectiondb
import argparse
import sys
import io
import fabric
from tabulate import tabulate

def list_dhcp_clients(args, connections, output):
    '''Connect to named router and list the connected clients
    '''
    conn = connections.get_connection(args.connection, output)
    result = conn.run('cat /tmp/dnsmasq.leases', hide=True)
    data = []
    for line in result.stdout.splitlines():
        data.append(line.split()[:-1])
    headers = ['Expiration', 'MAC', 'IP', 'Hostname']
    print(tabulate(data, headers=headers, tablefmt='simple'), file=output)
    
def list_dhcp_static_leases(args, connections, output):
    '''Connect to named router and list the configured static dhcp
       leases
    '''
    conn = connections.get_connection(args.connection, output)
    result = conn.run('nvram show | grep static_leases', hide=True)
    data = []
    for lease in result.stdout.removeprefix('static_leases=').split():
        data.append(lease.split('=')[:-1])
    headers = ['MAC', 'Hostname', 'IP']
    print(tabulate(data, headers=headers, tablefmt='simple'), file=output)
    
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

    parser_dhcp = subparsers.add_parser('dhcp')
    parser_dhcp_commands = parser_dhcp.add_subparsers(dest='dhcp_command')
    parser_dhcp_clients = parser_dhcp_commands.add_parser('clients')
    parser_dhcp_clients.add_argument("--connection", type=str)
    parser_dhcp_leases = parser_dhcp_commands.add_parser('static-leases')
    parser_dhcp_leases.add_argument("--connection", type=str)
    
    return parser.parse_args(argv)


def process_command(argv = sys.argv[1:]):
    ''' Main program -- if invoked from command line, otherwise, an entry point for UI
    '''
    connections = connectiondb.ConnectionDB()
    args = get_args(argv)
    output = io.StringIO()
    
    if args.command == 'dhcp':
        if args.dhcp_command == 'clients':
            list_dhcp_clients(args, connections, output)
        if args.dhcp_command == 'static-leases':
            list_dhcp_static_leases(args, connections, output)
    if args.command == 'connections':
        if args.connections_command == 'list':
            connections.list_connections(output)
        if args.connections_command == 'new':
            connections.new_connection(args, output)
                
    return output

if __name__ == '__main__':
    print(process_command().getvalue())

