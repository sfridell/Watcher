import re
import ipaddress
from typing import List, Dict, Any
from .base import RouterBase


class DDWRTRouter(RouterBase):
    def get_dhcp_leases(self, conn) -> List[List[str]]:
        result = conn.run('cat /tmp/dnsmasq.leases', hide=True)
        if result.exited != 0:
            raise Exception('remote list command failed')
        data = []
        for line in result.stdout.splitlines():
            data.append(line.split()[:-1])
        return data

    def get_static_leases(self, conn) -> List[List[str]]:
        result = conn.run('nvram show | grep static_leases', hide=True)
        if result.exited != 0:
            raise Exception('remote list command failed')
        data = []
        for lease in result.stdout.removeprefix('static_leases=').split():
            data.append(lease.split('=')[:-1])
        return data

    def set_static_leases(self, conn, leases: List[List[str]]):
        lease_string = ''
        for d in leases:
            lease_string = lease_string + f'{d[0]}={d[1]}={d[2]}= '
        result = conn.run(f'nvram set static_leases="{lease_string}"')
        if result.exited != 0:
            raise Exception('remote set command failed')

    def restart_dhcp_service(self, conn):
        result = conn.run('service dnsmasq restart', hide=True)
        if result.exited != 0:
            raise Exception('remote dnsmasq restart command failed')

    def commit_config(self, conn):
        result = conn.run('nvram commit', hide=True)
        if result.exited != 0:
            raise Exception('remote commit command failed')

    def get_interfaces(self, conn) -> Dict[str, Any]:
        result = conn.run("ip link", hide=True)
        if result.exited != 0:
            raise Exception('remote ip command failed')
        interfaces = {}
        for line in result.stdout.splitlines():
            match = re.match(r'\d+: (\S+):.*', line)
            if match:
                iface = match.group(1)
                if iface != "lo":
                    interfaces[iface] = {"type": "unknown", "vlan": None}
        return interfaces

    def get_bridges(self, conn) -> Dict[str, Any]:
        result = conn.run("brctl show", hide=True)
        if result.exited != 0:
            raise Exception('remote brctl command failed')
        bridges = {}
        current_bridge = None
        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            if i == 0:
                continue
            if not line.strip():
                continue
            parts = line.split()
            if not line.startswith("\t") and not line.startswith(" "):
                bridge_name = parts[0]
                bridges.setdefault(bridge_name, {"members": []})
                current_bridge = bridge_name
                if len(parts) == 4:
                    bridges[bridge_name]["members"].append(parts[3])
            else:
                if current_bridge and parts:
                    bridges[current_bridge]["members"].append(parts[0])
        return bridges

    def get_vlans(self, conn) -> Dict[str, Any]:
        result = conn.run("nvram show | grep vlan", hide=True)
        if result.exited != 0:
            raise Exception('remote nvram command failed')
        vlans = {}
        vlan_ips = re.findall(r'(vlan\d+)_ipaddr=([0-9\.]+)', result.stdout)
        vlan_netmasks = re.findall(r'(vlan\d+)_netmask=([0-9\.]+)', result.stdout)
        vlan_bridged = re.findall(r'(vlan\d+)_bridged=(\d)', result.stdout)
        vlan_nat = re.findall(r'(vlan\d+)_nat=(\d)', result.stdout)
        vlan_dhcp = re.findall(r'mdhcpd=.*? (vlan\d+)>On>(\d+)>(\d+)>(\d+)', result.stdout)

        for vlan, ip in vlan_ips:
            vlans.setdefault(vlan, {})["ip"] = ip
        for vlan, nm in vlan_netmasks:
            vlans.setdefault(vlan, {})["netmask"] = nm
        for vlan, bridged in vlan_bridged:
            vlans.setdefault(vlan, {})["bridged"] = bridged == "1"
        for vlan, nat in vlan_nat:
            vlans.setdefault(vlan, {})["nat"] = nat == "1"
        for vlan, start, size, lease in vlan_dhcp:
            vlans.setdefault(vlan, {})["dhcp"] = {
                "enabled": True,
                "range_start": int(start),
                "range_size": int(size),
                "lease_time_min": int(lease)
            }
        return vlans

    def get_port_vlan_map(self, conn) -> Dict[str, List[int]]:
        result = conn.run("nvram show | grep port.*vlans", hide=True)
        if result.exited != 0:
            raise Exception('remote nvram command failed')
        port_vlan_map = {}
        for line in result.stdout.splitlines():
            port_match = re.match(r'port(\d+)vlans=(.*)', line)
            if port_match:
                port_num = int(port_match.group(1))
                vlans = [int(x) for x in port_match.group(2).split()]
                port_vlan_map[f"port{port_num}"] = vlans
        return port_vlan_map

    def get_bridge_dhcp_config(self, conn) -> List[tuple]:
        result = conn.run("nvram show | grep mdhcpd", hide=True)
        if result.exited != 0:
            raise Exception('remote nvram command failed')
        return re.findall(r'mdhcpd=.*?(br\d+)>On>(\d+)>(\d+)>(\d+)', result.stdout)

    def get_bridge_ip_info(self, conn, bridge: str) -> List[tuple]:
        result = conn.run(f"ip addr show {bridge}", hide=True)
        if result.exited != 0:
            raise Exception('remote ip command failed')
        matches = re.findall(r'.*?inet (\d+.\d+.\d+.\d+)/(\d+) ', result.stdout)
        result_list = []
        for ip, prefix in matches:
            netmask = str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)
            result_list.append((ip, netmask))
        return result_list
