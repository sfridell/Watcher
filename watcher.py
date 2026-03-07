"""
Tool to monitor home network
"""
import connectiondb
import argparse
import sys
import io
import re
import json
from tabulate import tabulate

def list_dhcp_clients(args, connections, output):
    '''Connect to named router and list the connected clients
    '''
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    data = router.get_dhcp_leases(conn)
    headers = ['Expiration', 'MAC', 'IP', 'Hostname']
    print(tabulate(data, headers=headers, tablefmt='simple'), file=output)

def list_dhcp_static_leases(args, connections, output):
    '''Connect to named router and list the configured static dhcp
       leases
    '''
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    data = router.get_static_leases(conn)
    headers = ['MAC', 'Hostname', 'IP']
    print(tabulate(data, headers=headers, tablefmt='simple'), file=output)

def rm_dhcp_static_lease(args, connections, output):
    '''Connect to named router and remove the indicated static dhcp
       lease
    '''
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    data = router.get_static_leases(conn)
    data = [d for d in data if d[1] != args.hostname]
    router.set_static_leases(conn, data)
    router.commit_config(conn)
    router.restart_dhcp_service(conn)
    print(f'Static lease for {args.hostname} removed', file=output)

def new_dhcp_static_lease(args, connections, output):
    '''Connect to named router and add the indicated static dhcp
       lease
    '''
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    data = router.get_static_leases(conn)
    for d in data:
        if d[0] == args.mac:
            raise Exception(f'static lease for mac {args.mac} already exists')
        elif d[1] == args.hostname:
            raise Exception(f'static lease for host {args.hostname} already exists')
        elif d[2] == args.ip:
            raise Exception(f'static lease for ip {args.ip} already exists')
    data.append([args.mac, args.hostname, args.ip])
    router.set_static_leases(conn, data)
    router.commit_config(conn)
    router.restart_dhcp_service(conn)
    print(f'Static lease for {args.hostname} added', file=output)

def query_connection_config(conn, router, output):
    ''' Query the router and build a json representation of the
        current config
    '''
    dhcp = {
        "static_leases": []
    }
    network = {
        "interfaces": {},
        "vlans": {},
        "bridges": {}
    }

    # DHCP
    dhcp["static_leases"] = router.get_static_leases(conn)
    
    # Network
    
    # 1 Get all interfaces
    network["interfaces"] = router.get_interfaces(conn)

    # 2 Get bridge membership
    network["bridges"] = router.get_bridges(conn)

    # 2.1 Get Bridge DHCP info
    bridge_dhcp = router.get_bridge_dhcp_config(conn)

    for bridge, start, size, lease in bridge_dhcp:
        network["bridges"][bridge]["dhcp"] = {
            "enabled": True,
            "range_start": int(start),
            "range_size": int(size),
            "lease_time_min": int(lease)
        }

    # 2.2 Get the bridge ip info
    for bridge in network["bridges"]:
        bridge_ip_info = router.get_bridge_ip_info(conn, bridge)
        for ip, netmask in bridge_ip_info:
            network["bridges"][bridge]["ip"] = ip
            network["bridges"][bridge]["netmask"] = netmask
        
    # 3 Get VLAN IP addresses and DHCP/NAT info from nvram
    network["vlans"] = router.get_vlans(conn)

    # 4 Get port-to-VLAN mapping
    port_vlan_map = router.get_port_vlan_map(conn)
    network["ports"] = port_vlan_map

    # 5 Map VLANs to physical ports
    for vlan_name in network["vlans"]:
        match = re.search(r'\d+', vlan_name)
        if match:
            vlan_id = int(match.group())
            members = []
            for port, vlan_list in port_vlan_map.items():
                if vlan_id in vlan_list:
                    members.append(port)
            network["vlans"][vlan_name]["members"] = members

    summary = {
        "network" : network,
        "dhcp" : dhcp
    }
    return json.dumps(summary, indent=4)

def manage_connection_config(args, connections, output):
    ''' Possible actions are:
        show --- query the router and print the current config
        backup --- save the current router config to the db
        verify --- compare saved config in db to router current config
    '''
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    if args.action == 'show':
        config_json = query_connection_config(conn, router, output)
        print(config_json, file=output)
        
def get_args(argv):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')

    parser_connections = subparsers.add_parser('connections')
    parser_connections_commands = parser_connections.add_subparsers(dest='connections_command')
    parser_connections_list = parser_connections_commands.add_parser('list')
    parser_connections_show = parser_connections_commands.add_parser('show')
    parser_connections_show.add_argument("--connection", type=str)
    parser_connections_new = parser_connections_commands.add_parser('new')
    parser_connections_new.add_argument("--ip", type=str)
    parser_connections_new.add_argument("--port", type=str)
    parser_connections_new.add_argument("--name", type=str)
    parser_connections_new.add_argument("--username", type=str)
    parser_connections_new.add_argument("--pw", type=str)
    parser_connections_new.add_argument("--router-type", type=str, default='ddwrt',
                                        help="Router type (e.g., ddwrt, ddwrt_v3_netgear_r7000)")
    parser_connections_config = parser_connections_commands.add_parser('config')
    parser_connections_config.add_argument("--action",
                                           type=str,
                                           default='list',
                                           choices=['show','backup','verify'])
    parser_connections_config.add_argument("--connection", type=str)
        
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
        elif args.connections_command == 'show':
            connections.show_connection(args, output)
        elif args.connections_command == 'new':
            connections.new_connection(args, output)
        elif args.connections_command == 'config':
            manage_connection_config(args, connections, output)
            
    return output

if __name__ == '__main__':
    print(process_command().getvalue())

