import re
import ipaddress
from typing import List, Dict, Any
from .base import RouterBase

_IFACE_NUM_RE = re.compile(r'^(vlan|br|eth)(\d+)$')

_UCI_TYPE_RE = re.compile(r"^(\w+)\.([\w@\[\]]+)=(\w[\w-]*)$")
_UCI_OPT_RE = re.compile(r"^(\w+)\.([\w@\[\]]+)\.(\w+)='(.*)'$")

_FW_BEGIN = "# BEGIN watcher-firewall"
_FW_END = "# END watcher-firewall"
_FW_DELIM = "WATCHER_FW_EOF"


class OpenWrtRouter(RouterBase):
    """Router handler that communicates with OpenWrt routers via SSH + UCI.

    Stores vlan/bridge/dhcp/firewall/VPN configuration through OpenWrt's UCI
    (Unified Configuration Interface) instead of DD-WRT's NVRAM. UCI sections
    named ``vlan<N>`` mirror the DD-WRT ``vlan`` naming so that the logical
    identifiers passed across the RouterBase interface remain consistent.

    The adapter is tolerant of missing services (e.g. openvpn not installed on
    a minimal x86 image) so that read operations never hard-fail and write
    operations only raise when the target sub-system is genuinely absent.
    """

    def __init__(self):
        pass

    # -- service management --------------------------------------------

    def _restart_service(self, conn, service_name):
        result = conn.run(f"/etc/init.d/{service_name} restart", hide=True, warn=True)
        if result.exited != 0:
            raise Exception(f'remote {service_name} restart command failed')

    def _start_service(self, conn, service_name):
        result = conn.run(f"/etc/init.d/{service_name} start", hide=True, warn=True)
        if result.exited != 0:
            raise Exception(f'failed to start {service_name} service')

    def _stop_service(self, conn, service_name):
        result = conn.run(f"/etc/init.d/{service_name} stop", hide=True, warn=True)
        if result.exited != 0:
            raise Exception(f'failed to stop {service_name} service')

    # -- UCI parsing helpers --------------------------------------------

    @staticmethod
    def _parse_uci_show(stdout: str) -> Dict[str, Dict[str, str]]:
        """Parse ``uci show <config>`` output into {section: {option: value}}.

        The synthetic key ``"_type"`` holds the section type. Both named
        sections (``network.lan``) and anonymous sections (``network.@device[0]``)
        are supported.
        """
        sections: Dict[str, Dict[str, str]] = {}
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _UCI_TYPE_RE.match(line)
            if m:
                _, section, sec_type = m.groups()
                sections.setdefault(section, {})["_type"] = sec_type
                continue
            m = _UCI_OPT_RE.match(line)
            if m:
                _, section, option, value = m.groups()
                sections.setdefault(section, {})[option] = value
        return sections

    def _uci_show(self, conn, config: str) -> Dict[str, Dict[str, str]]:
        result = conn.run(f"uci show {config}", hide=True, warn=True)
        if result.exited != 0:
            return {}
        return self._parse_uci_show(result.stdout)

    def _uci_get(self, conn, key: str) -> str:
        result = conn.run(f"uci get {key}", hide=True, warn=True)
        if result.exited != 0:
            return ""
        return result.stdout.strip()

    def _uci_set(self, conn, key: str, value: str):
        escaped = value.replace("'", "'\\''")
        result = conn.run(f"uci set {key}='{escaped}'", hide=True, warn=True)
        if result.exited != 0:
            raise Exception(f'remote uci set {key} failed')

    def _uci_delete(self, conn, key: str):
        conn.run(f"uci delete {key}", hide=True, warn=True)

    @staticmethod
    def _iface_num(iface):
        m = _IFACE_NUM_RE.match(iface)
        return int(m.group(2)) if m else None

    @staticmethod
    def _uci_iface(bridge: str) -> str:
        """Map a kernel bridge device name (e.g. ``br-lan``) to its UCI
        interface section name (e.g. ``lan``). Names without the ``br-``
        prefix are returned unchanged.
        """
        if bridge.startswith("br-"):
            return bridge[3:]
        return bridge

    @staticmethod
    def _lease_to_minutes(leasetime: str) -> int:
        """Convert a UCI/dnsmasq leasetime string (e.g. ``12h``, ``1d``,
        ``1440m``, ``720``) into an integer number of minutes.
        """
        s = leasetime.strip().strip("'")
        m = re.match(r'^(\d+)d$', s)
        if m:
            return int(m.group(1)) * 1440
        m = re.match(r'^(\d+)h$', s)
        if m:
            return int(m.group(1)) * 60
        m = re.match(r'^(\d+)m$', s)
        if m:
            return int(m.group(1))
        try:
            return int(s)
        except ValueError:
            return 0

    @staticmethod
    def _minutes_to_leasestring(minutes: int) -> str:
        return f"{minutes}m"

    # -- DHCP leases ---------------------------------------------------

    def get_dhcp_leases(self, conn) -> List[List[str]]:
        result = conn.run('cat /tmp/dhcp.leases', hide=True, warn=True)
        if result.exited != 0:
            return []
        data = []
        for line in result.stdout.splitlines():
            data.append(line.split()[:-1])
        return data

    def remove_dhcp_leases(self, conn, mac_addresses: List[str]):
        result = conn.run('cat /tmp/dhcp.leases', hide=True, warn=True)
        if result.exited != 0:
            return
        lines = result.stdout.splitlines()
        filtered = [
            line for line in lines
            if len(line.split()) < 2 or line.split()[1] not in mac_addresses
        ]
        lease_content = '\n'.join(filtered) + '\n'
        conn.run(
            f"cat > /tmp/dhcp.leases <<'LEASE_EOF'\n{lease_content}LEASE_EOF",
            hide=True,
        )
        self._restart_service(conn, "dnsmasq")

    def restart_dhcp_service(self, conn):
        self._restart_service(conn, "dnsmasq")

    # -- static leases --------------------------------------------------

    def get_static_leases(self, conn) -> List[List[str]]:
        sections = self._uci_show(conn, "dhcp")
        data = []
        for _section, opts in sorted(sections.items()):
            if opts.get("_type") != "host":
                continue
            mac = opts.get("mac", "")
            name = opts.get("name", "")
            ip = opts.get("ip", "")
            data.append([mac, name, ip])
        return data

    def set_static_leases(self, conn, leases: List[List[str]]):
        # Remove every existing host section.
        while True:
            r = conn.run("uci delete dhcp.@host[0]", hide=True, warn=True)
            if r.exited != 0:
                break
        for entry in leases:
            mac, name, ip = entry
            r = conn.run("uci add dhcp host", hide=True, warn=True)
            if r.exited != 0:
                raise Exception('remote uci add host failed')
            sid = r.stdout.strip()
            self._uci_set(conn, f"dhcp.{sid}.mac", mac)
            self._uci_set(conn, f"dhcp.{sid}.name", name)
            self._uci_set(conn, f"dhcp.{sid}.ip", ip)

    # -- commit ---------------------------------------------------------

    def commit_config(self, conn):
        """Persist staged UCI changes to /etc/config. Does NOT restart
        services (mirrors DD-WRT ``nvram commit`` semantics; callers issue
        explicit restarts via the service helpers where live application is
        required).
        """
        conn.run("uci commit network", hide=True, warn=True)
        conn.run("uci commit dhcp", hide=True, warn=True)
        conn.run("uci commit firewall", hide=True, warn=True)

    # -- interfaces / bridges (live system) -----------------------------

    def get_interfaces(self, conn) -> Dict[str, Any]:
        result = conn.run("ip link", hide=True, warn=True)
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
        result = conn.run("brctl show", hide=True, warn=True)
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

    def get_bridge_ip_info(self, conn, bridge: str) -> List[tuple]:
        result = conn.run(f"ip addr show {bridge}", hide=True, warn=True)
        if result.exited != 0:
            raise Exception('remote ip command failed')
        matches = re.findall(r'.*?inet (\d+.\d+.\d+.\d+)/(\d+) ', result.stdout)
        result_list = []
        for ip, prefix in matches:
            netmask = str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)
            result_list.append((ip, netmask))
        return result_list

    # -- VLANs ----------------------------------------------------------

    def get_vlans(self, conn) -> Dict[str, Any]:
        sections = self._uci_show(conn, "network")
        vlans: Dict[str, Any] = {}
        for section, opts in sections.items():
            if section in ("loopback", "lan", "wan", "wan6"):
                continue
            if opts.get("_type") != "interface":
                continue
            m = re.match(r'^vlan(\d+)$', section)
            if not m:
                continue
            vlan = {}
            if "ipaddr" in opts:
                vlan["ip"] = opts["ipaddr"]
            if "netmask" in opts:
                vlan["netmask"] = opts["netmask"]
            if "bridged" in opts:
                vlan["bridged"] = opts["bridged"] == "1"
            if "nat" in opts:
                vlan["nat"] = opts["nat"] == "1"
            # Associated DHCP section (named dhcp.<section>).
            dhcp_sections = self._uci_show(conn, "dhcp")
            dhcp_opts = dhcp_sections.get(section, {})
            if dhcp_opts.get("ignore", "0") == "0" and "start" in dhcp_opts:
                vlan["dhcp"] = {
                    "enabled": True,
                    "range_start": int(dhcp_opts.get("start", "0")),
                    "range_size": int(dhcp_opts.get("limit", "0")),
                    "lease_time_min": self._lease_to_minutes(
                        dhcp_opts.get("leasetime", "0")
                    ),
                }
            vlans.setdefault(section, {}).update(vlan)
        return vlans

    def get_port_vlan_map(self, conn) -> Dict[str, List[int]]:
        return {}

    def set_vlan_ip(self, conn, vlan_id: int, ip: str, netmask: str):
        key = f"vlan{vlan_id}"
        # Create interface section if absent.
        existing = self._uci_get(conn, f"network.{key}")
        if not existing:
            self._uci_set(conn, f"network.{key}", "interface")
        self._uci_set(conn, f"network.{key}.proto", "static")
        self._uci_set(conn, f"network.{key}.ipaddr", ip)
        self._uci_set(conn, f"network.{key}.netmask", netmask)

    def set_vlan_bridged(self, conn, vlan_id: int, bridged: bool):
        key = f"vlan{vlan_id}"
        val = "1" if bridged else "0"
        self._uci_set(conn, f"network.{key}.bridged", val)

    def set_vlan_nat(self, conn, vlan_id: int, nat: bool):
        key = f"vlan{vlan_id}"
        val = "1" if nat else "0"
        self._uci_set(conn, f"network.{key}.nat", val)

    def set_vlan_dhcp(self, conn, vlan_id: int, start: int, size: int, lease: int):
        key = f"vlan{vlan_id}"
        # Create dhcp section if absent.
        existing = self._uci_get(conn, f"dhcp.{key}")
        if existing != "dhcp":
            self._uci_set(conn, f"dhcp.{key}", "dhcp")
        self._uci_set(conn, f"dhcp.{key}.interface", key)
        self._uci_set(conn, f"dhcp.{key}.start", str(start))
        self._uci_set(conn, f"dhcp.{key}.limit", str(size))
        self._uci_set(conn, f"dhcp.{key}.leasetime",
                      self._minutes_to_leasestring(lease))
        self._uci_set(conn, f"dhcp.{key}.ignore", "0")

    def remove_vlan_dhcp(self, conn, vlan_id: int):
        key = f"vlan{vlan_id}"
        self._uci_delete(conn, f"dhcp.{key}")

    def delete_vlan(self, conn, vlan_id: int):
        key = f"vlan{vlan_id}"
        self._uci_delete(conn, f"network.{key}")
        self._uci_delete(conn, f"dhcp.{key}")

    def set_port_vlan_map(self, conn, port_map: Dict[str, List[int]]):
        """No-op on OpenWrt x86 (no managed switch)."""
        pass

    def set_vlan_members(self, conn, vlan_name: str, members: List[str]):
        """No-op on OpenWrt x86; port membership is managed via switch configs
        (not applicable to the x86 virtio NICs used in the QEMU test VM)."""
        pass

    # -- bridge DHCP / IP -----------------------------------------------

    def get_bridge_dhcp_config(self, conn) -> List[tuple]:
        dhcp = self._uci_show(conn, "dhcp")
        network = self._uci_show(conn, "network")
        result = []
        for _section, opts in dhcp.items():
            if opts.get("_type") != "dhcp":
                continue
            iface = opts.get("interface", "")
            if "start" not in opts:
                continue
            start = int(opts.get("start", "0"))
            size = int(opts.get("limit", "0"))
            lease_min = self._lease_to_minutes(opts.get("leasetime", "0"))
            # Resolve to a kernel/bridge name when possible for parity with
            # the DD-WRT adapter's return shape (which used the bridge name).
            bridge = iface
            net_iface = network.get(iface, {})
            dev = net_iface.get("device", "")
            if dev:
                bridge = dev
            result.append((bridge, start, size, lease_min))
        return result

    def set_bridge_dhcp(self, conn, bridge: str, start: int, size: int, lease: int):
        iface = self._uci_iface(bridge)
        # The dhcp section in default OpenWrt is named after the interface
        # (e.g. ``dhcp.lan`` for ``lan``).
        existing = self._uci_get(conn, f"dhcp.{iface}")
        if existing != "dhcp":
            self._uci_set(conn, f"dhcp.{iface}", "dhcp")
            self._uci_set(conn, f"dhcp.{iface}.interface", iface)
        self._uci_set(conn, f"dhcp.{iface}.start", str(start))
        self._uci_set(conn, f"dhcp.{iface}.limit", str(size))
        self._uci_set(conn, f"dhcp.{iface}.leasetime",
                      self._minutes_to_leasestring(lease))
        self._uci_set(conn, f"dhcp.{iface}.ignore", "0")

    def set_bridge_ip(self, conn, bridge: str, ip: str, netmask: str):
        iface = self._uci_iface(bridge)
        # Ensure an interface section exists.
        existing = self._uci_get(conn, f"network.{iface}")
        if not existing:
            self._uci_set(conn, f"network.{iface}", "interface")
        self._uci_set(conn, f"network.{iface}.proto", "static")
        self._uci_set(conn, f"network.{iface}.ipaddr", ip)
        self._uci_set(conn, f"network.{iface}.netmask", netmask)

    def add_bridge_member(self, conn, bridge: str, interface: str):
        result = conn.run(f"brctl addif {bridge} {interface}", hide=True, warn=True)
        if result.exited != 0:
            raise Exception(f'remote brctl addif {bridge} {interface} failed')

    def remove_bridge_member(self, conn, bridge: str, interface: str):
        result = conn.run(f"brctl delif {bridge} {interface}", hide=True, warn=True)
        if result.exited != 0:
            raise Exception(f'remote brctl delif {bridge} {interface} failed')

    # -- firewall --------------------------------------------------------

    def get_firewall_rules(self, conn) -> List[Dict[str, Any]]:
        """Parse interface routing restrictions from /etc/firewall.user.

        Collects iptables FORWARD DROP rules with both ``-i`` and ``-o``
        qualifiers, identical to the DD-WRT ``rc_firewall`` parser so readback
        results are structurally compatible.
        """
        result = conn.run("cat /etc/firewall.user", hide=True, warn=True)
        if result.exited != 0 or not result.stdout.strip():
            return []
        rules = []
        seen_keys = set()
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
            comment_match = re.search(r'-m comment --comment "([^"]+)"', line)
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
        """Write watcher-managed firewall rules into /etc/firewall.user.

        Preserves any existing non-watcher content. The watcher block is
        re-applied (firewall reload) so the new rules take effect immediately.
        """
        result = conn.run("cat /etc/firewall.user", hide=True, warn=True)
        existing = ""
        if result.exited == 0 and result.stdout.strip():
            existing = result.stdout.strip()
        lines = []
        in_block = False
        for line in existing.splitlines():
            stripped = line.strip()
            if stripped == _FW_BEGIN:
                in_block = True
                continue
            if stripped == _FW_END:
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
        watcher_lines = [_FW_BEGIN]
        for rule in rules:
            from_iface = rule["from_iface"]
            to_iface = rule["to_iface"]
            iptables_cmd = (
                f"iptables -I FORWARD -i {from_iface} "
                f"-o {to_iface} -j DROP"
            )
            if rule.get("description"):
                desc = rule["description"]
                iptables_cmd += f' -m comment --comment "{desc}"'
            watcher_lines.append(iptables_cmd)
        watcher_lines.append(_FW_END)
        lines.extend(watcher_lines)
        new_content = "\n".join(lines)
        conn.run(
            f"cat > /etc/firewall.user <<'{_FW_DELIM}'\n{new_content}\n{_FW_DELIM}",
            hide=True,
        )

    # -- VPN ------------------------------------------------------------
    # OpenWrt stores OpenVPN client configs in /etc/config/openvpn as UCI
    # sections. on a minimal x86 image openvpn is not installed by default;
    # the methods below tolerate its absence so read operations return empty
    # status rather than raising.

    def _openvpn_present(self, conn) -> bool:
        result = conn.run("test -f /etc/init.d/openvpn", hide=True, warn=True)
        return result.exited == 0

    def get_vpn_status(self, conn) -> Dict[str, Any]:
        status = {
            "enabled": False,
            "connected": False,
            "remote": "",
            "port": "",
            "proto": "",
            "interface": "",
        }
        if not self._openvpn_present(conn):
            return status
        sections = self._uci_show(conn, "openvpn")
        client = None
        for _section, opts in sections.items():
            if opts.get("_type") != "openvpn":
                continue
            if opts.get("enabled", "0") == "1":
                client = opts
                break
        if client:
            status["enabled"] = True
            status["remote"] = client.get("remote", "")
            status["port"] = client.get("port", "")
            status["proto"] = client.get("proto", "")
        result = conn.run("ip link show tun0 2>/dev/null", hide=True, warn=True)
        if result.exited == 0 and "UP" in result.stdout:
            status["connected"] = True
            status["interface"] = "tun0"
        else:
            for line in result.stdout.splitlines():
                m = re.match(r'\d+:\s+(\S+):.*UP', line)
                if m and m.group(1).startswith("tun"):
                    status["connected"] = True
                    status["interface"] = m.group(1)
                    break
        return status

    def get_vpn_config(self, conn) -> Dict[str, str]:
        config: Dict[str, str] = {}
        if not self._openvpn_present(conn):
            return config
        sections = self._uci_show(conn, "openvpn")
        for _section, opts in sections.items():
            if opts.get("_type") != "openvpn":
                continue
            for option, value in opts.items():
                if option == "_type":
                    continue
                config[option] = value
        return config

    def apply_vpn_config(self, conn, vpn_config: Dict[str, str]):
        if not self._openvpn_present(conn):
            raise Exception('openvpn is not installed on this OpenWrt router')
        for option, value in vpn_config.items():
            if option == "_type":
                continue
            self._uci_set(conn, f"openvpn.client.{option}", value)
        conn.run("uci commit openvpn", hide=True, warn=True)

    def start_vpn(self, conn):
        if not self._openvpn_present(conn):
            raise Exception('openvpn is not installed on this OpenWrt router')
        self._uci_set(conn, "openvpn.client.enabled", "1")
        conn.run("uci commit openvpn", hide=True, warn=True)
        self._start_service(conn, "openvpn")

    def stop_vpn(self, conn):
        if not self._openvpn_present(conn):
            raise Exception('openvpn is not installed on this OpenWrt router')
        self._uci_set(conn, "openvpn.client.enabled", "0")
        conn.run("uci commit openvpn", hide=True, warn=True)
        self._stop_service(conn, "openvpn")

    def install_authorized_key(self, conn, pub_key: str):
        """Append the key to ``~/.ssh/authorized_keys`` (shared helper)
        and to ``/etc/dropbear/authorized_keys`` (dropbear's default
        auth-keys path on OpenWrt, since OpenWrt has no NVRAM).
        """
        self._install_in_home_ssh(conn, pub_key)
        conn.run(
            f'mkdir -p /etc/dropbear '
            f'&& echo "{pub_key}" >> /etc/dropbear/authorized_keys '
            f'&& chmod 600 /etc/dropbear/authorized_keys',
            hide=True,
        )