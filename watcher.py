"""
Tool to monitor home network
"""
import logging
import connectiondb
import argparse
import sys
import io
import re
import json
from tabulate import tabulate
from networkconfig import NetworkConfig

logging.getLogger('paramiko').setLevel(logging.WARNING)
logging.getLogger('invoke').setLevel(logging.WARNING)
logging.getLogger('fabric').setLevel(logging.WARNING)


def list_dhcp_clients(args, connections, output):
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    data = router.get_dhcp_leases(conn)
    headers = ['Expiration', 'MAC', 'IP', 'Hostname']
    print(tabulate(data, headers=headers, tablefmt='simple'), file=output)


def list_dhcp_static_leases(args, connections, output):
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    data = router.get_static_leases(conn)
    headers = ['MAC', 'Hostname', 'IP']
    print(tabulate(data, headers=headers, tablefmt='simple'), file=output)


def rm_dhcp_static_lease(args, connections, output):
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
    dhcp = {
        "static_leases": []
    }
    network = {
        "interfaces": {},
        "vlans": {},
        "bridges": {}
    }

    dhcp["static_leases"] = router.get_static_leases(conn)

    network["interfaces"] = router.get_interfaces(conn)

    network["bridges"] = router.get_bridges(conn)

    bridge_dhcp = router.get_bridge_dhcp_config(conn)

    for bridge, start, size, lease in bridge_dhcp:
        network["bridges"][bridge]["dhcp"] = {
            "enabled": True,
            "range_start": int(start),
            "range_size": int(size),
            "lease_time_min": int(lease)
        }

    for bridge in network["bridges"]:
        bridge_ip_info = router.get_bridge_ip_info(conn, bridge)
        for ip, netmask in bridge_ip_info:
            network["bridges"][bridge]["ip"] = ip
            network["bridges"][bridge]["netmask"] = netmask

    network["vlans"] = router.get_vlans(conn)

    port_vlan_map = router.get_port_vlan_map(conn)
    network["ports"] = port_vlan_map

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
        "network": network,
        "dhcp": dhcp
    }
    return json.dumps(summary, indent=4)


def manage_connection_config(args, connections, output):
    if args.action == 'show':
        conn, router = connections.get_connection_with_handler(args.connection, output)
        if conn is None:
            return
        config_json = query_connection_config(conn, router, output)
        print(config_json, file=output)


def config_snapshot(args, connections, output):
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    config = NetworkConfig.from_router(conn, router)
    print(config.to_json(), file=output)


def config_save(args, connections, output):
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    config = NetworkConfig.from_router(conn, router)
    config.to_json_file(args.file)
    print(f'Config saved to {args.file}', file=output)


def config_validate(args, connections, output):
    config = NetworkConfig.from_json_file(args.file)
    errors = config.validate()
    if errors:
        print('Validation errors:', file=output)
        for err in errors:
            print(f'  - {err}', file=output)
    else:
        print('Configuration is valid.', file=output)


def config_diff(args, connections, output):
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    current = NetworkConfig.from_router(conn, router)
    desired = NetworkConfig.from_json_file(args.file)
    d = current.diff(desired)
    if d.is_empty():
        print('No differences found.', file=output)
    else:
        print('Differences:', file=output)
        print(str(d), file=output)


def config_apply(args, connections, output):
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    desired = NetworkConfig.from_json_file(args.file)
    errors = desired.validate()
    if errors:
        print('Cannot apply - validation errors:', file=output)
        for err in errors:
            print(f'  - {err}', file=output)
        return
    mode = getattr(args, 'mode', 'diff')
    print(f'Applying config to {args.connection} (mode={mode})...', file=output)
    desired.apply_to_router(conn, router, mode=mode)
    print('Config applied successfully.', file=output)


def config_verify(args, connections, output):
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    desired = NetworkConfig.from_json_file(args.file)
    issues = desired.verify(conn, router)
    if issues:
        print('Verification FAILED:', file=output)
        for issue in issues:
            print(f'  - {issue}', file=output)
    else:
        print('Verification succeeded - router matches spec.', file=output)


def vlan_list(args, connections, output):
    config = NetworkConfig.from_json_file(args.file)
    vlans = config.network.get("vlans", {})
    if not vlans:
        print('No VLANs found.', file=output)
        return
    headers = ['VLAN', 'IP', 'Netmask', 'Bridged', 'NAT', 'Members']
    rows = []
    for vlan_name, vlan_data in sorted(vlans.items()):
        members = ",".join(vlan_data.get("members", []))
        rows.append([
            vlan_name,
            vlan_data.get("ip", ""),
            vlan_data.get("netmask", ""),
            "Yes" if vlan_data.get("bridged") else "No",
            "Yes" if vlan_data.get("nat") else "No",
            members
        ])
    print(tabulate(rows, headers=headers, tablefmt='simple'), file=output)


def vlan_add(args, connections, output):
    config = NetworkConfig.from_json_file(args.file)
    try:
        config.add_vlan(
            vlan_id=args.id,
            ip=args.ip,
            netmask=args.netmask,
            bridged=args.bridged,
            nat=args.nat,
            dhcp_enabled=args.dhcp_enabled,
            dhcp_start=args.dhcp_start,
            dhcp_size=args.dhcp_size,
            dhcp_lease=args.dhcp_lease,
        )
    except ValueError as e:
        print(f'Error: {e}', file=output)
        return
    config.to_json_file(args.file)
    print(f'VLAN {args.id} added to {args.file}', file=output)


def vlan_remove(args, connections, output):
    config = NetworkConfig.from_json_file(args.file)
    try:
        config.remove_vlan(vlan_id=args.id)
    except ValueError as e:
        print(f'Error: {e}', file=output)
        return
    config.to_json_file(args.file)
    print(f'VLAN {args.id} removed from {args.file}', file=output)


def vlan_show(args, connections, output):
    config = NetworkConfig.from_json_file(args.file)
    vlan_name = f"vlan{args.id}"
    vlans = config.network.get("vlans", {})
    if vlan_name not in vlans:
        print(f'VLAN {vlan_name} not found in config.', file=output)
        return
    vlan_data = vlans[vlan_name]
    print(f'VLAN: {vlan_name}', file=output)
    for key, value in vlan_data.items():
        print(f'  {key}: {value}', file=output)


def port_list(args, connections, output):
    config = NetworkConfig.from_json_file(args.file)
    ports = config.network.get("ports", {})
    if not ports:
        print('No port assignments found.', file=output)
        return
    headers = ['Port', 'VLANs']
    rows = []
    for port, vlans in sorted(ports.items()):
        rows.append([port, ",".join(str(v) for v in vlans)])
    print(tabulate(rows, headers=headers, tablefmt='simple'), file=output)


def port_assign(args, connections, output):
    config = NetworkConfig.from_json_file(args.file)
    try:
        config.assign_port(port=args.port, vlan_id=args.vlan)
    except ValueError as e:
        print(f'Error: {e}', file=output)
        return
    config.to_json_file(args.file)
    print(f'Port {args.port} assigned to VLAN {args.vlan}', file=output)


def port_unassign(args, connections, output):
    config = NetworkConfig.from_json_file(args.file)
    try:
        config.unassign_port(port=args.port, vlan_id=args.vlan)
    except ValueError as e:
        print(f'Error: {e}', file=output)
        return
    config.to_json_file(args.file)
    print(f'Port {args.port} unassigned from VLAN {args.vlan}', file=output)


def get_args(argv=sys.argv[1:]):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')

    parser_connections = subparsers.add_parser('connections')
    parser_connections_commands = parser_connections.add_subparsers(dest='connections_command')
    parser_connections_commands.add_parser('list')
    parser_connections_show = parser_connections_commands.add_parser('show')
    parser_connections_show.add_argument("--connection", type=str)
    parser_connections_new = parser_connections_commands.add_parser('new')
    parser_connections_new.add_argument("--ip", type=str)
    parser_connections_new.add_argument("--port", type=str)
    parser_connections_new.add_argument("--name", type=str)
    parser_connections_new.add_argument("--username", type=str)
    parser_connections_new.add_argument("--pw", type=str)
    parser_connections_new.add_argument("--router-type", type=str, default='ddwrt',
                                         help="Router type (e.g., ddwrt, ddwrt_v3_netgear_r7000, mock)")
    parser_connections_config = parser_connections_commands.add_parser('config')
    parser_connections_config.add_argument("--action",
                                           type=str,
                                           default='list',
                                           choices=['show', 'backup', 'verify'])
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

    parser_config = subparsers.add_parser('config')
    config_commands = parser_config.add_subparsers(dest='config_command')
    config_snapshot = config_commands.add_parser('snapshot')
    config_snapshot.add_argument("--connection", type=str, required=True)
    config_save = config_commands.add_parser('save')
    config_save.add_argument("--connection", type=str, required=True)
    config_save.add_argument("--file", type=str, required=True)
    config_validate = config_commands.add_parser('validate')
    config_validate.add_argument("--file", type=str, required=True)
    config_diff = config_commands.add_parser('diff')
    config_diff.add_argument("--connection", type=str, required=True)
    config_diff.add_argument("--file", type=str, required=True)
    config_apply = config_commands.add_parser('apply')
    config_apply.add_argument("--connection", type=str, required=True)
    config_apply.add_argument("--file", type=str, required=True)
    config_apply.add_argument("--mode", type=str, default='diff',
                              choices=['diff', 'full'],
                              help="Apply mode: 'diff' (only changed settings) or 'full' (rewrite all)")
    config_verify = config_commands.add_parser('verify')
    config_verify.add_argument("--connection", type=str, required=True)
    config_verify.add_argument("--file", type=str, required=True)

    parser_vlan = subparsers.add_parser('vlan')
    vlan_commands = parser_vlan.add_subparsers(dest='vlan_command')
    vlan_list_cmd = vlan_commands.add_parser('list')
    vlan_list_cmd.add_argument("--file", type=str, required=True)
    vlan_add_cmd = vlan_commands.add_parser('add')
    vlan_add_cmd.add_argument("--file", type=str, required=True)
    vlan_add_cmd.add_argument("--id", type=int, required=True)
    vlan_add_cmd.add_argument("--ip", type=str, default="0.0.0.0")
    vlan_add_cmd.add_argument("--netmask", type=str, default="0.0.0.0")
    vlan_add_cmd.add_argument("--bridged", action="store_true", default=False)
    vlan_add_cmd.add_argument("--nat", action="store_true", default=False)
    vlan_add_cmd.add_argument("--dhcp-enabled", action="store_true", default=False)
    vlan_add_cmd.add_argument("--dhcp-start", type=int, default=0)
    vlan_add_cmd.add_argument("--dhcp-size", type=int, default=0)
    vlan_add_cmd.add_argument("--dhcp-lease", type=int, default=0)
    vlan_remove_cmd = vlan_commands.add_parser('remove')
    vlan_remove_cmd.add_argument("--file", type=str, required=True)
    vlan_remove_cmd.add_argument("--id", type=int, required=True)
    vlan_show_cmd = vlan_commands.add_parser('show')
    vlan_show_cmd.add_argument("--file", type=str, required=True)
    vlan_show_cmd.add_argument("--id", type=int, required=True)

    parser_port = subparsers.add_parser('port')
    port_commands = parser_port.add_subparsers(dest='port_command')
    port_list_cmd = port_commands.add_parser('list')
    port_list_cmd.add_argument("--file", type=str, required=True)
    port_assign_cmd = port_commands.add_parser('assign')
    port_assign_cmd.add_argument("--file", type=str, required=True)
    port_assign_cmd.add_argument("--port", type=str, required=True)
    port_assign_cmd.add_argument("--vlan", type=int, required=True)
    port_unassign_cmd = port_commands.add_parser('unassign')
    port_unassign_cmd.add_argument("--file", type=str, required=True)
    port_unassign_cmd.add_argument("--port", type=str, required=True)
    port_unassign_cmd.add_argument("--vlan", type=int, required=True)

    return parser.parse_args(argv)


def process_command(argv=sys.argv[1:]):
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
    elif args.command == 'config':
        if args.config_command == 'snapshot':
            config_snapshot(args, connections, output)
        elif args.config_command == 'save':
            config_save(args, connections, output)
        elif args.config_command == 'validate':
            config_validate(args, connections, output)
        elif args.config_command == 'diff':
            config_diff(args, connections, output)
        elif args.config_command == 'apply':
            config_apply(args, connections, output)
        elif args.config_command == 'verify':
            config_verify(args, connections, output)
    elif args.command == 'vlan':
        if args.vlan_command == 'list':
            vlan_list(args, connections, output)
        elif args.vlan_command == 'add':
            vlan_add(args, connections, output)
        elif args.vlan_command == 'remove':
            vlan_remove(args, connections, output)
        elif args.vlan_command == 'show':
            vlan_show(args, connections, output)
    elif args.command == 'port':
        if args.port_command == 'list':
            port_list(args, connections, output)
        elif args.port_command == 'assign':
            port_assign(args, connections, output)
        elif args.port_command == 'unassign':
            port_unassign(args, connections, output)

    return output


if __name__ == '__main__':
    print(process_command().getvalue())
