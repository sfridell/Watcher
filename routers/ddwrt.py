import re
import ipaddress
from typing import List, Dict, Any
from .base import RouterBase

_IFACE_NUM_RE = re.compile(r'^(vlan|br|eth)(\d+)$')


class DDWRTRouter(RouterBase):
    """Router handler that communicates with DD-WRT routers via SSH nvram/shell commands.

    Automatically probes the remote system for available service management commands
    (service, startservice/stopservice, or killall+binary) and tolerates missing
    NVRAM keys, so it works across DD-WRT variants (hardware routers, x86 VMs, etc.).
    """

    def __init__(self):
        self._capabilities_probed = False
        self._service_restart_cmd = None

    def _probe_capabilities(self, conn):
        if self._capabilities_probed:
            return
        result = conn.run("which service 2>/dev/null", hide=True, warn=True)
        if result.exited == 0 and result.stdout.strip():
            self._service_restart_cmd = "service"
        else:
            result = conn.run("which startservice 2>/dev/null", hide=True, warn=True)
            if result.exited == 0 and result.stdout.strip():
                self._service_restart_cmd = "ddwrt"
            else:
                self._service_restart_cmd = "killall"
        self._capabilities_probed = True

    def _restart_service(self, conn, service_name):
        self._probe_capabilities(conn)
        if self._service_restart_cmd == "service":
            result = conn.run(f"service {service_name} restart", hide=True, warn=True)
        elif self._service_restart_cmd == "ddwrt":
            result = conn.run(f"stopservice {service_name} && startservice {service_name}", hide=True, warn=True)
        else:
            result = conn.run(f"killall {service_name} 2>/dev/null; sleep 1; /usr/sbin/{service_name}", hide=True, warn=True)
        if result.exited != 0:
            raise Exception(f'remote {service_name} restart command failed')

    def _start_service(self, conn, service_name):
        self._probe_capabilities(conn)
        if self._service_restart_cmd == "service":
            result = conn.run(f"service {service_name} start", hide=True, warn=True)
        elif self._service_restart_cmd == "ddwrt":
            result = conn.run(f"startservice {service_name}", hide=True, warn=True)
        else:
            result = conn.run(f"/usr/sbin/{service_name}", hide=True, warn=True)
        if result.exited != 0:
            raise Exception(f'failed to start {service_name} service')

    def _stop_service(self, conn, service_name):
        self._probe_capabilities(conn)
        if self._service_restart_cmd == "service":
            result = conn.run(f"service {service_name} stop", hide=True, warn=True)
        elif self._service_restart_cmd == "ddwrt":
            result = conn.run(f"stopservice {service_name}", hide=True, warn=True)
        else:
            result = conn.run(f"killall {service_name}", hide=True, warn=True)
        if result.exited != 0:
            raise Exception(f'failed to stop {service_name} service')

    @staticmethod
    def _iface_num(iface):
        m = _IFACE_NUM_RE.match(iface)
        return int(m.group(2)) if m else None

    def _nvram_grep(self, conn, pattern):
        result = conn.run(f"nvram show | grep '{pattern}'", hide=True, warn=True)
        if result.exited == 1:
            return ""
        if result.exited != 0:
            raise Exception('remote nvram grep command failed')
        return result.stdout

    def get_dhcp_leases(self, conn) -> List[List[str]]:
        result = conn.run('cat /tmp/dnsmasq.leases', hide=True)
        if result.exited != 0:
            raise Exception('remote list command failed')
        data = []
        for line in result.stdout.splitlines():
            data.append(line.split()[:-1])
        return data

    def get_static_leases(self, conn) -> List[List[str]]:
        stdout = self._nvram_grep(conn, "static_leases")
        if not stdout:
            return []
        data = []
        for lease in stdout.removeprefix('static_leases=').split():
            data.append(lease.split('=')[:-1])
        return data

    def set_static_leases(self, conn, leases: List[List[str]]):
        lease_string = ''
        for d in leases:
            lease_string = lease_string + f'{d[0]}={d[1]}={d[2]}= '
        result = conn.run(f'nvram set static_leases="{lease_string}"')
        if result.exited != 0:
            raise Exception('remote set command failed')

    def remove_dhcp_leases(self, conn, mac_addresses: List[str]):
        result = conn.run('cat /tmp/dnsmasq.leases', hide=True)
        if result.exited != 0:
            raise Exception('remote cat dnsmasq.leases failed')
        lines = result.stdout.splitlines()
        filtered = [line for line in lines if len(line.split()) < 2 or line.split()[1] not in mac_addresses]
        lease_content = '\n'.join(filtered) + '\n'
        conn.run(f"echo '{lease_content}' > /tmp/dnsmasq.leases", hide=True)
        self._restart_service(conn, "dnsmasq")

    def restart_dhcp_service(self, conn):
        self._restart_service(conn, "dnsmasq")

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
        stdout = self._nvram_grep(conn, "vlan")
        if not stdout:
            return {}
        vlans = {}
        vlan_ips = re.findall(r'(vlan\d+)_ipaddr=([0-9\.]+)', stdout)
        vlan_netmasks = re.findall(r'(vlan\d+)_netmask=([0-9\.]+)', stdout)
        vlan_bridged = re.findall(r'(vlan\d+)_bridged=(\d)', stdout)
        vlan_nat = re.findall(r'(vlan\d+)_nat=(\d)', stdout)
        vlan_dhcp = re.findall(r'mdhcpd=.*? (vlan\d+)>On>(\d+)>(\d+)>(\d+)', stdout)

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
        stdout = self._nvram_grep(conn, "port.*vlans")
        if not stdout:
            return {}
        port_vlan_map = {}
        for line in stdout.splitlines():
            port_match = re.match(r'port(\d+)vlans=(.*)', line)
            if port_match:
                port_num = int(port_match.group(1))
                vlans = [int(x) for x in port_match.group(2).split()]
                port_vlan_map[f"port{port_num}"] = vlans
        return port_vlan_map

    def get_bridge_dhcp_config(self, conn) -> List[tuple]:
        stdout = self._nvram_grep(conn, "mdhcpd")
        if not stdout:
            return []
        return re.findall(r'mdhcpd=.*?(br\d+)>On>(\d+)>(\d+)>(\d+)', stdout)

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

    def set_vlan_ip(self, conn, vlan_id: int, ip: str, netmask: str):
        vlan_key = f"vlan{vlan_id}"
        result = conn.run(f'nvram set {vlan_key}_ipaddr="{ip}"', hide=True)
        if result.exited != 0:
            raise Exception(f'remote set {vlan_key}_ipaddr failed')
        result = conn.run(f'nvram set {vlan_key}_netmask="{netmask}"', hide=True)
        if result.exited != 0:
            raise Exception(f'remote set {vlan_key}_netmask failed')

    def set_vlan_bridged(self, conn, vlan_id: int, bridged: bool):
        vlan_key = f"vlan{vlan_id}"
        val = "1" if bridged else "0"
        result = conn.run(f'nvram set {vlan_key}_bridged="{val}"', hide=True)
        if result.exited != 0:
            raise Exception(f'remote set {vlan_key}_bridged failed')

    def set_vlan_nat(self, conn, vlan_id: int, nat: bool):
        vlan_key = f"vlan{vlan_id}"
        val = "1" if nat else "0"
        result = conn.run(f'nvram set {vlan_key}_nat="{val}"', hide=True)
        if result.exited != 0:
            raise Exception(f'remote set {vlan_key}_nat failed')

    def set_vlan_dhcp(self, conn, vlan_id: int, start: int, size: int, lease: int):
        vlan_key = f"vlan{vlan_id}"
        result = conn.run("nvram get mdhcpd", hide=True, warn=True)
        mdhcpd = result.stdout.strip() if result.exited == 0 else ""
        existing = mdhcpd.split() if mdhcpd else []
        filtered = [e for e in existing if not e.startswith(f"{vlan_key}>")]
        new_entry = f"{vlan_key}>On>{start}>{size}>{lease}"
        filtered.append(new_entry)
        new_mdhcpd = " ".join(filtered)
        result = conn.run(f'nvram set mdhcpd="{new_mdhcpd}"', hide=True)
        if result.exited != 0:
            raise Exception('remote set mdhcpd failed')

    def remove_vlan_dhcp(self, conn, vlan_id: int):
        vlan_key = f"vlan{vlan_id}"
        result = conn.run("nvram get mdhcpd", hide=True, warn=True)
        mdhcpd = result.stdout.strip() if result.exited == 0 else ""
        existing = mdhcpd.split() if mdhcpd else []
        filtered = [e for e in existing if not e.startswith(f"{vlan_key}>")]
        new_mdhcpd = " ".join(filtered)
        result = conn.run(f'nvram set mdhcpd="{new_mdhcpd}"', hide=True)
        if result.exited != 0:
            raise Exception('remote set mdhcpd failed')

    def delete_vlan(self, conn, vlan_id: int):
        vlan_key = f"vlan{vlan_id}"
        for key in [f"{vlan_key}_ipaddr", f"{vlan_key}_netmask",
                     f"{vlan_key}_bridged", f"{vlan_key}_nat"]:
            conn.run(f'nvram unset {key}', hide=True)
        result = conn.run("nvram get mdhcpd", hide=True, warn=True)
        mdhcpd = result.stdout.strip() if result.exited == 0 else ""
        existing = mdhcpd.split() if mdhcpd else []
        filtered = [e for e in existing if not e.startswith(f"{vlan_key}>")]
        new_mdhcpd = " ".join(filtered)
        conn.run(f'nvram set mdhcpd="{new_mdhcpd}"', hide=True)

    def set_port_vlan_map(self, conn, port_map: Dict[str, List[int]]):
        for port, vlans in port_map.items():
            val = " ".join(str(v) for v in vlans)
            result = conn.run(f'nvram set {port}vlans="{val}"', hide=True)
            if result.exited != 0:
                raise Exception(f'remote set {port}vlans failed')

    def set_bridge_dhcp(self, conn, bridge: str, start: int, size: int, lease: int):
        result = conn.run("nvram get mdhcpd", hide=True, warn=True)
        mdhcpd = result.stdout.strip() if result.exited == 0 else ""
        existing = mdhcpd.split() if mdhcpd else []
        filtered = [e for e in existing if not e.startswith(f"{bridge}>")]
        new_entry = f"{bridge}>On>{start}>{size}>{lease}"
        filtered.append(new_entry)
        new_mdhcpd = " ".join(filtered)
        result = conn.run(f'nvram set mdhcpd="{new_mdhcpd}"', hide=True)
        if result.exited != 0:
            raise Exception('remote set mdhcpd failed')

    def set_bridge_ip(self, conn, bridge: str, ip: str, netmask: str):
        result = conn.run(f'nvram set {bridge}_ipaddr="{ip}"', hide=True)
        if result.exited != 0:
            raise Exception(f'remote set {bridge}_ipaddr failed')
        result = conn.run(f'nvram set {bridge}_netmask="{netmask}"', hide=True)
        if result.exited != 0:
            raise Exception(f'remote set {bridge}_netmask failed')

    def add_bridge_member(self, conn, bridge: str, interface: str):
        """Not directly supported via nvram on DD-WRT; would require brctl addif at runtime."""

    def remove_bridge_member(self, conn, bridge: str, interface: str):
        """Not directly supported via nvram on DD-WRT; would require brctl delif at runtime."""

    def set_vlan_members(self, conn, vlan_name: str, members: List[str]):
        """No-op on DD-WRT; port membership is managed via the port VLAN map nvram variables."""

    def get_firewall_rules(self, conn) -> List[Dict[str, Any]]:
        """Parse interface routing restrictions from rc_firewall.

        Collects rules from both the watcher-managed block and any unmanaged
        iptables FORWARD DROP rules matching the interface restriction pattern,
        so that manually-created firewall rules are also discovered.
        Accepts any interface name pattern (vlanN, brN, ethN, etc.).
        """
        result = conn.run("nvram get rc_firewall", hide=True)
        rules = []
        seen_keys = set()
        if result.exited != 0 or not result.stdout.strip():
            return rules
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line.startswith("iptables") or "-j DROP" not in line:
                continue
            if "-I FORWARD" not in line and "-A FORWARD" not in line:
                continue
            iface_match = re.search(r"-i\s+(\S+)", line)
            oface_match = re.search(r"-o\s+(\S+)", line)
            if not iface_match or not oface_match:
                continue
            from_iface = iface_match.group(1)
            to_iface = oface_match.group(1)
            key = (from_iface, to_iface)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            comment_match = re.search(r"-m comment --comment \"([^\"]+)\"", line)
            rule = {
                "from": self._iface_num(from_iface),
                "to": self._iface_num(to_iface),
                "from_iface": from_iface,
                "to_iface": to_iface,
            }
            if comment_match:
                rule["description"] = comment_match.group(1)
            rules.append(rule)
        return rules

    def set_firewall_rules(self, conn, rules: List[Dict[str, Any]]):
        """Write watcher-managed firewall rules to rc_firewall.

        Preserves any existing non-watcher, non-interface-restriction content.
        Any FORWARD DROP rules with both -i and -o outside the watcher block
        are removed (since they are now managed within the watcher block).
        """
        result = conn.run("nvram get rc_firewall", hide=True)
        existing = ""
        if result.exited == 0 and result.stdout.strip():
            existing = result.stdout.strip()
        lines = []
        in_block = False
        for line in existing.splitlines():
            stripped = line.strip()
            if stripped == "# BEGIN watcher-firewall":
                in_block = True
                continue
            if stripped == "# END watcher-firewall":
                in_block = False
                continue
            if in_block:
                continue
            if stripped.startswith("iptables") and "-j DROP" in stripped:
                if ("-I FORWARD" in stripped or "-A FORWARD" in stripped):
                    iface_match = re.search(r"-i\s+\S+", stripped)
                    oface_match = re.search(r"-o\s+\S+", stripped)
                    if iface_match and oface_match:
                        continue
            lines.append(line)
        watcher_lines = ["# BEGIN watcher-firewall"]
        for rule in rules:
            iptables_cmd = f"iptables -I FORWARD -i {rule['from_iface']} -o {rule['to_iface']} -j DROP"
            if rule.get("description"):
                iptables_cmd += f" -m comment --comment \"{rule['description']}\""
            watcher_lines.append(iptables_cmd)
        watcher_lines.append("# END watcher-firewall")
        lines.extend(watcher_lines)
        new_rc = "\n".join(lines)
        conn.run(f"nvram set rc_firewall='{new_rc}'", hide=True)
        conn.run("nvram commit", hide=True)

    def get_vpn_status(self, conn) -> Dict[str, Any]:
        status = {
            "enabled": False,
            "connected": False,
            "remote": "",
            "port": "",
            "proto": "",
            "interface": "",
        }
        result = conn.run("nvram get openvpncl_enable", hide=True)
        if result.exited == 0 and result.stdout.strip() == "1":
            status["enabled"] = True
        result = conn.run("ip link show tun0 2>/dev/null", hide=True, warn=True)
        if result.exited == 0 and "UP" in result.stdout:
            status["connected"] = True
            status["interface"] = "tun0"
        elif result.exited == 0 and result.stdout.strip():
            for line in result.stdout.splitlines():
                m = re.match(r'\d+:\s+(\S+):.*UP', line)
                if m:
                    status["connected"] = True
                    status["interface"] = m.group(1)
                    break
        if status["enabled"]:
            result = conn.run("nvram get openvpncl_remoteip", hide=True, warn=True)
            if result.exited == 0:
                status["remote"] = result.stdout.strip()
            result = conn.run("nvram get openvpncl_remoteport", hide=True, warn=True)
            if result.exited == 0:
                status["port"] = result.stdout.strip()
            result = conn.run("nvram get openvpncl_proto", hide=True, warn=True)
            if result.exited == 0:
                status["proto"] = result.stdout.strip()
        return status

    def get_vpn_config(self, conn) -> Dict[str, str]:
        config = {}
        keys = [
            "openvpncl_enable", "openvpncl_remoteip", "openvpncl_remoteport",
            "openvpncl_proto", "openvpncl_tuntap", "openvpncl_mtu",
            "openvpncl_ca", "openvpncl_client", "openvpncl_key",
            "openvpncl_cipher", "openvpncl_sec", "openvpncl_lzo",
            "openvpncl_upauth", "openvpncl_user", "openvpncl_pass",
            "openvpncl_keydirection", "openvpncl_tlsauth",
            "openvpncl_nat", "openvpncl_config",
        ]
        for key in keys:
            result = conn.run(f"nvram get {key}", hide=True, warn=True)
            if result.exited == 0 and result.stdout.strip():
                config[key] = result.stdout.strip()
        return config

    def apply_vpn_config(self, conn, vpn_config: Dict[str, str]):
        for key, value in vpn_config.items():
            escaped = value.replace("'", "'\\''")
            result = conn.run(f"nvram set {key}='{escaped}'", hide=True)
            if result.exited != 0:
                raise Exception(f'remote nvram set {key} failed')
        result = conn.run("nvram commit", hide=True)
        if result.exited != 0:
            raise Exception('remote nvram commit failed')

    def start_vpn(self, conn):
        conn.run("nvram set openvpncl_enable=1", hide=True)
        conn.run("nvram commit", hide=True)
        self._start_service(conn, "openvpn")

    def stop_vpn(self, conn):
        conn.run("nvram set openvpncl_enable=0", hide=True)
        conn.run("nvram commit", hide=True)
        self._stop_service(conn, "openvpn")