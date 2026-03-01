"""
Tool to monitor home network
"""
import connectiondb
import argparse
import sys
import io
import fabric
import re
import json
import ipaddress
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

def fetch_dhcp_static_leases(conn):
    '''Return a list of the static leases for dhcp
    '''
    result = conn.run('nvram show | grep static_leases', hide=True)
    if result.exited != 0:
        raise Exception('remote list command failed')
    data = []
    for lease in result.stdout.removeprefix('static_leases=').split():
        data.append(lease.split('=')[:-1])
    return data

def list_dhcp_static_leases(args, connections, output):
    '''Connect to named router and list the configured static dhcp
       leases
    '''
    conn = connections.get_connection(args.connection, output)
    data = fetch_dhcp_static_leases(conn)
    headers = ['MAC', 'Hostname', 'IP']
    print(tabulate(data, headers=headers, tablefmt='simple'), file=output)

def rm_dhcp_static_lease(args, connections, output):
    '''Connect to named router and remove the indicated static dhcp
       lease
    '''
    conn = connections.get_connection(args.connection, output)
    data = fetch_dhcp_static_leases(conn)
    data = [ d for d in data if d[1] != args.hostname ]
    lease_string = ''
    for d in data:
        lease_string = lease_string + f'{d[0]}={d[1]}={d[2]}= '
    result = conn.run(f'nvram set static_leases=\"{lease_string}\"')
    if result.exited != 0:
        raise Exception('remote set command failed')
    result = conn.run('nvram commit', hide=True)
    if result.exited != 0:
        raise Exception('remote commit command failed')
    result = conn.run('service dnsmasq restart', hide=True)
    if result.exited != 0:
        raise Exception('remote dnsmasq restart command failed')
    print(f'Static lease for {args.hostname} removed', file=output)

def new_dhcp_static_lease(args, connections, output):
    '''Connect to named router and add the indicated static dhcp
       lease
    '''
    conn = connections.get_connection(args.connection, output)
    data = fetch_dhcp_static_leases(conn)
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
    result = conn.run(f'nvram set static_leases=\"{lease_string}\"')
    if result.exited != 0:
        raise Exception('remote set command failed')
    result = conn.run('nvram commit', hide=True)
    if result.exited != 0:
        raise Exception('remote commit command failed')
    result = conn.run('service dnsmasq restart', hide=True)
    if result.exited != 0:
        raise Exception('remote dnsmasq restart command failed')
    print(f'Static lease for {args.hostname} added', file=output)

def query_connection_config(conn, output):
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
    dhcp["static_leases"] = fetch_dhcp_static_leases(conn)
    
    # Network
    
    # 1️ Get all interfaces
    ip_link_result = conn.run("ip link", hide=True)
    if ip_link_result.exited != 0:
        raise Exception('remote ip command failed')
    ip_link_output = ip_link_result.stdout
    for line in ip_link_output.splitlines():
        match = re.match(r'\d+: (\S+):.*', line)
        if match:
            iface = match.group(1)
            # Skip loopback
            if iface == "lo":
                continue
            network["interfaces"][iface] = {"type": "unknown", "vlan": None}

    # 2️ Get bridge membership
    brctl_result = conn.run("brctl show", hide=True)
    if brctl_result.exited != 0:
        raise Exception('remote brctl command failed')
    brctl_output = brctl_result.stdout
    lines = brctl_output.splitlines()
    current_bridge = None
    for i, line in enumerate(lines):
        if i == 0:
            # Skip header line
            continue
        if not line.strip():
            continue
        parts = line.split()

        # New bridge line (starts at column 0)
        if not line.startswith("\t") and not line.startswith(" "):
            bridge_name = parts[0]
            network['bridges'].setdefault(bridge_name, {"members": []})
            current_bridge = bridge_name
            # Interface may appear on same line
            if len(parts) == 4:
                network['bridges'][bridge_name]["members"].append(parts[3])
        else:
            # Continuation line (additional interfaces)
            if current_bridge and parts:
                network['bridges'][current_bridge]["members"].append(parts[0])

    # 2.1 Get Bridge DHCP info
    bridge_dhcp_result = conn.run("nvram show | grep mdhcpd", hide=True)
    if bridge_dhcp_result.exited != 0:
        raise Exception('remote nvram command failed')
    bridge_dhcp_output = bridge_dhcp_result.stdout
    bridge_dhcp = re.findall(r'mdhcpd=.*?(br\d+)>On>(\d+)>(\d+)>(\d+)', bridge_dhcp_output)

    for bridge, start, size, lease in bridge_dhcp:
        network["bridges"][bridge]["dhcp"] = {
            "enabled": True,
            "range_start": int(start),
            "range_size": int(size),
            "lease_time_min": int(lease)
        }

    # 2.2 Get the bridge ip info
    for bridge in network["bridges"]:
        bridge_ip_result = conn.run(f"ip addr show {bridge}", hide=True)
        if bridge_ip_result.exited != 0:
            raise Exception('remote ip command failed')
        bridge_ip_output = bridge_ip_result.stdout
        bridge_ip = re.findall(r'.*?inet (\d+.\d+.\d+.\d+)/(\d+) ', bridge_ip_output)
        for ip, prefix in bridge_ip:
            netmask = str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)
            network["bridges"][bridge]["ip"] = ip
            network["bridges"][bridge]["netmask"] = netmask
        
    # 3️ Get VLAN IP addresses and DHCP/NAT info from nvram
    vlan_result = conn.run("nvram show | grep vlan", hide=True)
    if vlan_result.exited != 0:
        raise Exception('remote nvram command failed')
    vlan_output = vlan_result.stdout
    vlan_ips = re.findall(r'(vlan\d+)_ipaddr=([0-9\.]+)', vlan_output)
    vlan_netmasks = re.findall(r'(vlan\d+)_netmask=([0-9\.]+)', vlan_output)
    vlan_bridged = re.findall(r'(vlan\d+)_bridged=(\d)', vlan_output)
    vlan_nat = re.findall(r'(vlan\d+)_nat=(\d)', vlan_output)
    vlan_dhcp = re.findall(r'mdhcpd=.*? (vlan\d+)>On>(\d+)>(\d+)>(\d+)', vlan_output)

    for vlan, ip in vlan_ips:
        network["vlans"].setdefault(vlan, {})
        network["vlans"][vlan]["ip"] = ip
    for vlan, nm in vlan_netmasks:
        network["vlans"].setdefault(vlan, {})
        network["vlans"][vlan]["netmask"] = nm
    for vlan, bridged in vlan_bridged:
        network["vlans"].setdefault(vlan, {})
        network["vlans"][vlan]["bridged"] = bridged == "1"
    for vlan, nat in vlan_nat:
        network["vlans"].setdefault(vlan, {})
        network["vlans"][vlan]["nat"] = nat == "1"
    for vlan, start, size, lease in vlan_dhcp:
        network["vlans"].setdefault(vlan, {})
        network["vlans"][vlan]["dhcp"] = {
            "enabled": True,
            "range_start": int(start),
            "range_size": int(size),
            "lease_time_min": int(lease)
        }

    # 4️ Get port-to-VLAN mapping
    port_vlans_result = conn.run("nvram show | grep port.*vlans", hide=True)
    if vlan_result.exited != 0:
        raise Exception('remote nvram command failed')
    port_vlans_output = port_vlans_result.stdout
    port_vlan_map = {}
    for line in port_vlans_output.splitlines():
        port_match = re.match(r'port(\d+)vlans=(.*)', line)
        if port_match:
            port_num = int(port_match.group(1))
            vlans = [int(x) for x in port_match.group(2).split()]
            port_vlan_map[f"port{port_num}"] = vlans

    network["ports"] = port_vlan_map

    # 5️ Map VLANs to physical ports
    for vlan_name in network["vlans"]:
        vlan_id = int(re.search(r'\d+', vlan_name).group())
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
    conn = connections.get_connection(args.connection, output)
    if args.action == 'show':
        config_json = query_connection_config(conn, output)
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

