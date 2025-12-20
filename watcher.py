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
    if result.exited != 0:
        raise Exception('remote list command failed')
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
    if result.exited != 0:
        raise Exception('remote list command failed')
    data = []
    for lease in result.stdout.removeprefix('static_leases=').split():
        data.append(lease.split('=')[:-1])
    headers = ['MAC', 'Hostname', 'IP']
    print(tabulate(data, headers=headers, tablefmt='simple'), file=output)

def rm_dhcp_static_lease(args, connections, output):
    '''Connect to named router and remove the indicated static dhcp
       lease
    '''
    conn = connections.get_connection(args.connection, output)
    result = conn.run('nvram show | grep static_leases', hide=True)
    data = []
    if result.exited != 0:
        raise Exception('remote list command failed')
    for lease in result.stdout.removeprefix('static_leases=').split():
        data.append(lease.split('=')[:-1])
    data = [ d for d in data if d[1] != args.hostname ]
    lease_string = ''
    for d in data:
        lease_string = lease_string + f'{d[0]}={d[1]}={d[2]}= '
    result = conn.run(f'nvram set static_leases=\"{lease_string}\"')
    if result.exited != 0:
        raise Exception('remote set command failed')
    result = conn.run('nvram show | grep static_leases', hide=True)
    if result.exited != 0:
        raise Exception('remote commit command failed')
    print(f'Static lease for {args.hostname} removed')

def new_dhcp_static_lease(args, connections, output):
    '''Connect to named router and add the indicated static dhcp
       lease
    '''
    conn = connections.get_connection(args.connection, output)
    result = conn.run('nvram show | grep static_leases', hide=True)
    data = []
    if result.exited != 0:
        raise Exception('remote list command failed')
    for lease in result.stdout.removeprefix('static_leases=').split():
        data.append(lease.split('=')[:-1])
    for d in data:
        if d[0] == args.mac:
            raise Exception(f'static lease for mac {args.mac} already exists')
        elif d[1] == args.hostname:
            raise Exception(f'static lease for host {args.hostname} already exists')
        elif d[2] == args.ip:
            raise Exception(f'static lease for ip {args.ip} already exists')
    lease_string = ''
    for d in data:
        lease_string = lease_string + f'{d[0]}={d[1]}={d[2]}= '
    lease_string = lease_string + f'{args.mac}={args.hostname}={args.ip}= '
    result = conn.run(f'nvram set static_leases=\"{lease_string}\" | nvram commit')
    if result.exited != 0:
        raise Exception('remote set command failed')
    print(f'Static lease for {args.hostname} added')

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
    parser_dhcp_clients_commands = parser_dhcp_clients.add_subparsers(dest='dhcp_clients_command')
    parser_dhcp_clients_list = parser_dhcp_clients_commands.add_parser('list')
    parser_dhcp_clients_list.add_argument("--connection", type=str)

    parser_dhcp_leases = parser_dhcp_commands.add_parser('static-leases')
    parser_dhcp_leases_commands = parser_dhcp_leases.add_subparsers(dest='dhcp_leases_command')
    parser_dhcp_leases_list = parser_dhcp_leases_commands.add_parser('list')
    parser_dhcp_leases_list.add_argument("--connection", type=str)
    parser_dhcp_leases_rm = parser_dhcp_leases_commands.add_parser('remove')
    parser_dhcp_leases_rm.add_argument("--connection", type=str)
    parser_dhcp_leases_rm.add_argument("--hostname", type=str)
    parser_dhcp_leases_rm = parser_dhcp_leases_commands.add_parser('new')
    parser_dhcp_leases_rm.add_argument("--connection", type=str)
    parser_dhcp_leases_rm.add_argument("--hostname", type=str)
    parser_dhcp_leases_rm.add_argument("--ip", type=str)
    parser_dhcp_leases_rm.add_argument("--mac", type=str)
    
    
    return parser.parse_args(argv)


def process_command(argv = sys.argv[1:]):
    ''' Main program -- if invoked from command line, otherwise, an entry point for UI
    '''
    connections = connectiondb.ConnectionDB()
    args = get_args(argv)
    output = io.StringIO()
    
    if args.command == 'dhcp':
        if args.dhcp_command == 'clients':
            if args.dhcp_clients_command == 'list':
                list_dhcp_clients(args, connections, output)
        elif args.dhcp_command == 'static-leases':
            if args.dhcp_leases_command == 'list':
                list_dhcp_static_leases(args, connections, output)
            elif args.dhcp_leases_command == 'remove':
                rm_dhcp_static_lease(args, connections, output)
            elif args.dhcp_leases_command == 'new':
                new_dhcp_static_lease(args, connections, output)
    elif args.command == 'connections':
        if args.connections_command == 'list':
            connections.list_connections(output)
        elif args.connections_command == 'new':
            connections.new_connection(args, output)
                
    return output

if __name__ == '__main__':
    print(process_command().getvalue())

