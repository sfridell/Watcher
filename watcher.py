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
from vpnconfig import parse_ovpn_file, get_ddwrt_nvram_from_config

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
    """Query a router and return its full network + DHCP config as a JSON string."""
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
    """Fetch the current config from a router and print it as JSON to stdout."""
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    config = NetworkConfig.from_router(conn, router)
    print(config.to_json(), file=output)


def config_save(args, connections, output):
    """Fetch the current config from a router and write it to a file (--file path)."""
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    config = NetworkConfig.from_router(conn, router)
    config.to_json_file(args.file)
    print(f'Config saved to {args.file}', file=output)


def config_validate(args, connections, output):
    """Load a config file and run validation checks, printing any errors found."""
    config = NetworkConfig.from_json_file(args.file)
    errors = config.validate()
    if errors:
        print('Validation errors:', file=output)
        for err in errors:
            print(f'  - {err}', file=output)
    else:
        print('Configuration is valid.', file=output)


def config_diff(args, connections, output):
    """Compare a router's live config against a saved config file and print the differences."""
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
    """Apply a config file to a router after validating it. Supports --mode diff or full."""
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
    """Re-read a router's config and compare it against a saved spec file to confirm they match."""
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
    """Display a table of VLANs from a local config file."""
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
    """Add a VLAN to a local config file and save it."""
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
    """Remove a VLAN from a local config file and save it."""
    config = NetworkConfig.from_json_file(args.file)
    try:
        config.remove_vlan(vlan_id=args.id)
    except ValueError as e:
        print(f'Error: {e}', file=output)
        return
    config.to_json_file(args.file)
    print(f'VLAN {args.id} removed from {args.file}', file=output)


def vlan_show(args, connections, output):
    """Show detailed properties of a single VLAN from a local config file."""
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
    """Display port-to-VLAN assignments from a local config file."""
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
    """Assign a port to a VLAN in a local config file and save it."""
    config = NetworkConfig.from_json_file(args.file)
    try:
        config.assign_port(port=args.port, vlan_id=args.vlan)
    except ValueError as e:
        print(f'Error: {e}', file=output)
        return
    config.to_json_file(args.file)
    print(f'Port {args.port} assigned to VLAN {args.vlan}', file=output)


def port_unassign(args, connections, output):
    """Remove a port from a VLAN in a local config file and save it."""
    config = NetworkConfig.from_json_file(args.file)
    try:
        config.unassign_port(port=args.port, vlan_id=args.vlan)
    except ValueError as e:
        print(f'Error: {e}', file=output)
        return
    config.to_json_file(args.file)
    print(f'Port {args.port} unassigned from VLAN {args.vlan}', file=output)


def vpn_status(args, connections, output):
    """Display VPN connection status for a router."""
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    status = router.get_vpn_status(conn)
    headers = ['Field', 'Value']
    rows = [
        ['Enabled', 'Yes' if status.get('enabled') else 'No'],
        ['Connected', 'Yes' if status.get('connected') else 'No'],
        ['Remote', status.get('remote', '')],
        ['Port', status.get('port', '')],
        ['Protocol', status.get('proto', '')],
        ['Interface', status.get('interface', '')],
    ]
    print(tabulate(rows, headers=headers, tablefmt='simple'), file=output)


def vpn_config_show(args, connections, output):
    """Display the active VPN configuration on a router."""
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    config = router.get_vpn_config(conn)
    if not config:
        print('No VPN configuration found on router.', file=output)
        return
    for key, value in sorted(config.items()):
        if value:
            print(f'{key}: {value}', file=output)


def vpn_config_apply(args, connections, output):
    """Apply a VPN configuration to the router. Reads from an .ovpn file or stored config."""
    db = connectiondb.ConnectionDB()
    if args.config_name:
        vpn_configs = db.get_vpn_configs(args.connection)
        if args.config_name not in vpn_configs:
            print(f'VPN config "{args.config_name}" not found for connection "{args.connection}"', file=output)
            return
        vpn_config = vpn_configs[args.config_name]
    elif args.ovpn_file:
        parsed = parse_ovpn_file(args.ovpn_file)
        vpn_config = get_ddwrt_nvram_from_config(parsed)
    else:
        print('Either --config-name or --ovpn-file must be specified', file=output)
        return

    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    try:
        router.apply_vpn_config(conn, vpn_config)
    except Exception as e:
        print(f'Failed to apply VPN config: {e}', file=output)
        return
    print(f'VPN config applied to {args.connection}', file=output)
    if args.start:
        try:
            router.start_vpn(conn)
            print('VPN started.', file=output)
        except Exception as e:
            print(f'Failed to start VPN: {e}', file=output)


def vpn_start(args, connections, output):
    """Start the VPN client on a router."""
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    try:
        router.start_vpn(conn)
        print(f'VPN started on {args.connection}', file=output)
    except Exception as e:
        print(f'Failed to start VPN: {e}', file=output)
    db = connectiondb.ConnectionDB()
    if args.config_name:
        db.set_active_vpn(args.connection, args.config_name)


def vpn_stop(args, connections, output):
    """Stop the VPN client on a router."""
    conn, router = connections.get_connection_with_handler(args.connection, output)
    if conn is None:
        return
    try:
        router.stop_vpn(conn)
        print(f'VPN stopped on {args.connection}', file=output)
    except Exception as e:
        print(f'Failed to stop VPN: {e}', file=output)


def vpn_config_import(args, connections, output):
    """Import an .ovpn file and store it as a named VPN config for a connection."""
    parsed = parse_ovpn_file(args.ovpn_file)
    db = connectiondb.ConnectionDB()
    db.add_vpn_config(args.connection, args.name, parsed)
    db.set_active_vpn(args.connection, args.name)
    print(f'VPN config "{args.name}" imported for connection "{args.connection}"', file=output)


def vpn_config_list(args, connections, output):
    """List stored VPN configs for a connection."""
    db = connectiondb.ConnectionDB()
    vpn_configs = db.get_vpn_configs(args.connection)
    active = db.get_active_vpn(args.connection)
    if not vpn_configs:
        print(f'No VPN configs stored for connection "{args.connection}"', file=output)
        return
    headers = ['Name', 'Active', 'Server', 'Port', 'Proto']
    rows = []
    for name, config in vpn_configs.items():
        rows.append([
            name,
            '*' if name == active else '',
            config.get('remote', ''),
            config.get('port', ''),
            config.get('proto', ''),
        ])
    print(tabulate(rows, headers=headers, tablefmt='simple'), file=output)


def vpn_config_delete(args, connections, output):
    """Delete a stored VPN config by name."""
    db = connectiondb.ConnectionDB()
    db.delete_vpn_config(args.connection, args.name)
    print(f'VPN config "{args.name}" deleted from connection "{args.connection}"', file=output)


def vlan_restrict(args, connections, output):
    """Add a VLAN routing restriction to a local config file and save it."""
    config = NetworkConfig.from_json_file(args.file)
    try:
        config.add_restriction(
            from_id=args.from_id,
            to_id=args.to_id,
            description=args.description or "",
            bidirectional=args.bidirectional,
        )
    except ValueError as e:
        print(f'Error: {e}', file=output)
        return
    config.to_json_file(args.file)
    bidi = " (bidirectional)" if args.bidirectional else ""
    print(f'Restriction: vlan{args.from_id} -> vlan{args.to_id}{bidi} added to {args.file}', file=output)


def vlan_unrestrict(args, connections, output):
    """Remove a VLAN routing restriction from a local config file and save it."""
    config = NetworkConfig.from_json_file(args.file)
    config.remove_restriction(
        from_id=args.from_id,
        to_id=args.to_id,
        bidirectional=args.bidirectional,
    )
    config.to_json_file(args.file)
    bidi = " (bidirectional)" if args.bidirectional else ""
    print(f'Restriction: vlan{args.from_id} -> vlan{args.to_id}{bidi} removed from {args.file}', file=output)


def vlan_restrictions(args, connections, output):
    """Display current VLAN routing restrictions from a local config file."""
    config = NetworkConfig.from_json_file(args.file)
    restrictions = config.network.get("vlan_restrictions", [])
    if not restrictions:
        print('No VLAN restrictions configured.', file=output)
        return
    headers = ['From', 'To', 'Bidirectional', 'Description']
    rows = []
    for r in restrictions:
        rows.append([
            f"vlan{r['from']}",
            f"vlan{r['to']}",
            "Yes" if r.get("bidirectional") else "No",
            r.get("description", ""),
        ])
    print(tabulate(rows, headers=headers, tablefmt='simple'), file=output)


def get_args(argv=sys.argv[1:]):
    """Build and return the argparse parser with all subcommands for connections, dhcp, config, vlan, and port."""
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
    vlan_restrict_cmd = vlan_commands.add_parser('restrict')
    vlan_restrict_cmd.add_argument("--file", type=str, required=True)
    vlan_restrict_cmd.add_argument("--from", type=int, required=True, dest="from_id")
    vlan_restrict_cmd.add_argument("--to", type=int, required=True, dest="to_id")
    vlan_restrict_cmd.add_argument("--description", type=str, default="")
    vlan_restrict_cmd.add_argument("--bidirectional", action="store_true", default=False)
    vlan_unrestrict_cmd = vlan_commands.add_parser('unrestrict')
    vlan_unrestrict_cmd.add_argument("--file", type=str, required=True)
    vlan_unrestrict_cmd.add_argument("--from", type=int, required=True, dest="from_id")
    vlan_unrestrict_cmd.add_argument("--to", type=int, required=True, dest="to_id")
    vlan_unrestrict_cmd.add_argument("--bidirectional", action="store_true", default=False)
    vlan_restrictions_cmd = vlan_commands.add_parser('restrictions')
    vlan_restrictions_cmd.add_argument("--file", type=str, required=True)

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

    parser_vpn = subparsers.add_parser('vpn')
    vpn_commands = parser_vpn.add_subparsers(dest='vpn_command')
    vpn_status_cmd = vpn_commands.add_parser('status')
    vpn_status_cmd.add_argument("--connection", type=str, required=True)
    vpn_show_cmd = vpn_commands.add_parser('show')
    vpn_show_cmd.add_argument("--connection", type=str, required=True)
    vpn_apply_cmd = vpn_commands.add_parser('apply')
    vpn_apply_cmd.add_argument("--connection", type=str, required=True)
    vpn_apply_cmd.add_argument("--config-name", type=str, default=None)
    vpn_apply_cmd.add_argument("--ovpn-file", type=str, default=None)
    vpn_apply_cmd.add_argument("--start", action="store_true", default=False)
    vpn_start_cmd = vpn_commands.add_parser('start')
    vpn_start_cmd.add_argument("--connection", type=str, required=True)
    vpn_start_cmd.add_argument("--config-name", type=str, default=None)
    vpn_stop_cmd = vpn_commands.add_parser('stop')
    vpn_stop_cmd.add_argument("--connection", type=str, required=True)
    vpn_import_cmd = vpn_commands.add_parser('import')
    vpn_import_cmd.add_argument("--connection", type=str, required=True)
    vpn_import_cmd.add_argument("--name", type=str, required=True)
    vpn_import_cmd.add_argument("--ovpn-file", type=str, required=True)
    vpn_list_cmd = vpn_commands.add_parser('list')
    vpn_list_cmd.add_argument("--connection", type=str, required=True)
    vpn_delete_cmd = vpn_commands.add_parser('delete')
    vpn_delete_cmd.add_argument("--connection", type=str, required=True)
    vpn_delete_cmd.add_argument("--name", type=str, required=True)

    parser_dnslog = subparsers.add_parser('dns-log')
    dnslog_commands = parser_dnslog.add_subparsers(dest='dnslog_command')

    dnslog_set_cmd = dnslog_commands.add_parser('set')
    dnslog_set_cmd.add_argument("--connection", type=str, required=True)
    dnslog_set_cmd.add_argument("--type", type=str, required=True,
                                choices=['pihole', 'pihole_v5', 'mock'])
    dnslog_set_cmd.add_argument("--ip", type=str, default=None,
                                help="DNS-log endpoint IP (optional ':port')")
    dnslog_set_cmd.add_argument("--scheme", type=str, default=None,
                                choices=['http', 'https'])
    dnslog_set_cmd.add_argument("--apikey", type=str, default=None,
                                help="API key / password (prompted if omitted for non-mock)")
    dnslog_set_cmd.add_argument("--pin", type=str, default=None,
                                help="Encrypt the API key at rest with this PIN (optional; "
                                     "omit to store it in plaintext for easy revocation)")

    dnslog_show_cmd = dnslog_commands.add_parser('show')
    dnslog_show_cmd.add_argument("--connection", type=str, required=True)

    dnslog_clear_cmd = dnslog_commands.add_parser('clear')
    dnslog_clear_cmd.add_argument("--connection", type=str, required=True)

    dnslog_lookups_cmd = dnslog_commands.add_parser('lookups')
    dnslog_lookups_cmd.add_argument("--connection", type=str, required=True)
    dnslog_lookups_cmd.add_argument("--period", type=str, default='24h',
                                    choices=['1h', '24h', '7d'])
    dnslog_lookups_cmd.add_argument("--pin", type=str, default=None,
                                    help="PIN to decrypt an encrypted API key "
                                         "(only needed if --pin was used with 'set')")
    dnslog_lookups_cmd.add_argument("--client", type=str, default=None,
                                    help="Filter to a specific client IP; shows top domains for that client")
    dnslog_lookups_cmd.add_argument("--limit", type=int, default=10,
                                    help="Max domains to show with --client (default 10)")

    dnslog_blocks_cmd = dnslog_commands.add_parser('blocks')
    dnslog_blocks_cmd.add_argument("--connection", type=str, required=True)
    dnslog_blocks_cmd.add_argument("--period", type=str, default='24h',
                                   choices=['1h', '24h', '7d'])
    dnslog_blocks_cmd.add_argument("--pin", type=str, default=None,
                                   help="PIN to decrypt an encrypted API key "
                                        "(only needed if --pin was used with 'set')")
    dnslog_blocks_cmd.add_argument("--client", type=str, default=None,
                                   help="Filter to a specific client IP; shows top blocked domains for that client")
    dnslog_blocks_cmd.add_argument("--limit", type=int, default=10,
                                   help="Max domains to show with --client (default 10)")

    dnslog_blocked_cmd = dnslog_commands.add_parser('blocked')
    dnslog_blocked_cmd.add_argument("--connection", type=str, required=True)
    dnslog_blocked_cmd.add_argument("--period", type=str, default='24h',
                                    choices=['1h', '24h', '7d'])
    dnslog_blocked_cmd.add_argument("--pin", type=str, default=None,
                                    help="PIN to decrypt an encrypted API key "
                                         "(only needed if --pin was used with 'set')")
    dnslog_blocked_cmd.add_argument("--limit", type=int, default=20,
                                    help="Max blocked domains to show (default 20)")

    return parser.parse_args(argv)


def dns_log_set(args, connections, output):
    """Configure a DNS-log endpoint on an existing router connection."""
    apikey = getattr(args, 'apikey', None)
    if apikey is None and args.type != 'mock':
        import getpass
        try:
            apikey = getpass.getpass('API key / password: ')
        except Exception:
            print('ERROR: could not read API key from terminal', file=output)
            return
    pin = getattr(args, 'pin', None)
    if pin:
        # confirm only when a PIN was supplied (opt-in encryption)
        import getpass
        confirm = getpass.getpass('Confirm PIN: ')
        if pin != confirm:
            print('ERROR: PINs do not match', file=output)
            return
    try:
        connections.set_dns_log(
            args.connection,
            dns_type=args.type,
            ip=getattr(args, 'ip', None),
            apikey=apikey or None,
            pin=pin or None,
            scheme=getattr(args, 'scheme', None),
        )
    except ValueError as e:
        print(f'ERROR: {e}', file=output)
        return
    mode = 'encrypted' if pin else 'plaintext'
    print(f'DNS-log endpoint configured for {args.connection} (apikey stored {mode})', file=output)


def dns_log_show(args, connections, output):
    """Show the stored DNS-log endpoint metadata (API key is never printed)."""
    entry = connections.get_dns_log(args.connection)
    if not entry:
        print(f'No DNS-log endpoint configured for {args.connection}', file=output)
        return
    safe = {k: v for k, v in entry.items() if k not in ('apikey', 'encrypted_apikey')}
    if 'encrypted_apikey' in entry:
        safe['apikey'] = '<encrypted>'
    elif 'apikey' in entry:
        safe['apikey'] = '<plaintext>'
    json.dump(safe, output, indent=2, default=str)
    print(file=output)


def dns_log_clear(args, connections, output):
    """Remove the DNS-log endpoint from a connection."""
    try:
        connections.delete_dns_log(args.connection)
    except ValueError as e:
        print(f'ERROR: {e}', file=output)
        return
    print(f'DNS-log endpoint removed from {args.connection}', file=output)


def _dns_log_query(args, connections, output, kind):
    pin = getattr(args, 'pin', None)
    entry = connections.get_dns_log(args.connection)
    needs_pin = bool(entry) and entry.get('type') != 'mock' and 'encrypted_apikey' in entry
    if needs_pin and not pin:
        import getpass
        pin = getpass.getpass('PIN: ')
    conn, handler = connections.get_dns_log_handler(args.connection, output, pin=pin)
    if conn is None:
        return
    client_ip = getattr(args, 'client', None)
    limit = getattr(args, 'limit', 10)
    try:
        if client_ip:
            if kind == 'lookups':
                data = handler.get_dns_lookups_for_client(conn, args.period, client_ip)
            else:
                data = handler.get_dns_blocks_for_client(conn, args.period, client_ip)
            headers = ['Domain', 'Count']
            key = 'domain'
        else:
            if kind == 'lookups':
                data = handler.get_dns_lookups(conn, args.period)
            else:
                data = handler.get_dns_blocks(conn, args.period)
            headers = ['IP', 'Count']
            key = 'ip'
    except Exception as e:
        print(f'ERROR: {e}', file=output)
        return
    if not data:
        label = f' for {client_ip}' if client_ip else ''
        print(f'No DNS {kind}{label} in the last {args.period}.', file=output)
        return
    data = data[:limit]
    rows = [[d[key], d['count']] for d in data]
    print(tabulate(rows, headers=headers, tablefmt='simple'), file=output)


def dns_log_lookups(args, connections, output):
    """List per-client DNS lookup counts for the given period."""
    _dns_log_query(args, connections, output, 'lookups')


def dns_log_blocks(args, connections, output):
    """List per-client DNS block counts for the given period."""
    _dns_log_query(args, connections, output, 'blocks')


def dns_log_blocked(args, connections, output):
    """List DNS blocks indexed by the blocked domain."""
    pin = getattr(args, 'pin', None)
    entry = connections.get_dns_log(args.connection)
    needs_pin = bool(entry) and entry.get('type') != 'mock' and 'encrypted_apikey' in entry
    if needs_pin and not pin:
        import getpass
        pin = getpass.getpass('PIN: ')
    conn, handler = connections.get_dns_log_handler(args.connection, output, pin=pin)
    if conn is None:
        return
    try:
        data = handler.get_dns_blocks_by_domain(conn, args.period)
    except Exception as e:
        print(f'ERROR: {e}', file=output)
        return
    if not data:
        print(f'No DNS blocks in the last {args.period}.', file=output)
        return
    limit = getattr(args, 'limit', 20)
    data = data[:limit]
    rows = [[d['domain'], d['count']] for d in data]
    headers = ['Blocked Domain', 'Count']
    print(tabulate(rows, headers=headers, tablefmt='simple'), file=output)


def process_command(argv=sys.argv[1:]):
    """Main entry point: parse args, dispatch to the appropriate handler, and return captured output."""
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
        elif args.vlan_command == 'restrict':
            vlan_restrict(args, connections, output)
        elif args.vlan_command == 'unrestrict':
            vlan_unrestrict(args, connections, output)
        elif args.vlan_command == 'restrictions':
            vlan_restrictions(args, connections, output)
    elif args.command == 'port':
        if args.port_command == 'list':
            port_list(args, connections, output)
        elif args.port_command == 'assign':
            port_assign(args, connections, output)
        elif args.port_command == 'unassign':
            port_unassign(args, connections, output)
    elif args.command == 'vpn':
        if args.vpn_command == 'status':
            vpn_status(args, connections, output)
        elif args.vpn_command == 'show':
            vpn_config_show(args, connections, output)
        elif args.vpn_command == 'apply':
            vpn_config_apply(args, connections, output)
        elif args.vpn_command == 'start':
            vpn_start(args, connections, output)
        elif args.vpn_command == 'stop':
            vpn_stop(args, connections, output)
        elif args.vpn_command == 'import':
            vpn_config_import(args, connections, output)
        elif args.vpn_command == 'list':
            vpn_config_list(args, connections, output)
        elif args.vpn_command == 'delete':
            vpn_config_delete(args, connections, output)
    elif args.command == 'dns-log':
        if args.dnslog_command == 'set':
            dns_log_set(args, connections, output)
        elif args.dnslog_command == 'show':
            dns_log_show(args, connections, output)
        elif args.dnslog_command == 'clear':
            dns_log_clear(args, connections, output)
        elif args.dnslog_command == 'lookups':
            dns_log_lookups(args, connections, output)
        elif args.dnslog_command == 'blocks':
            dns_log_blocks(args, connections, output)
        elif args.dnslog_command == 'blocked':
            dns_log_blocked(args, connections, output)

    return output


if __name__ == '__main__':
    print(process_command().getvalue())
