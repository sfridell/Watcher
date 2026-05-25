import json
import copy
import ipaddress
import re
from typing import List, Dict, Any


class ConfigDiff:
    """Represents the differences between two NetworkConfig instances."""

    def __init__(self):
        self.added_vlans: List[Dict[str, Any]] = []
        self.removed_vlans: List[str] = []
        self.modified_vlans: List[Dict[str, Any]] = []
        self.added_ports: Dict[str, List[int]] = {}
        self.removed_ports: Dict[str, List[int]] = {}
        self.added_bridge_members: Dict[str, List[str]] = {}
        self.removed_bridge_members: Dict[str, List[str]] = {}
        self.added_bridge_dhcp: List[Dict[str, Any]] = []
        self.removed_bridge_dhcp: List[str] = []
        self.modified_bridge_dhcp: List[Dict[str, Any]] = []
        self.added_bridge_ip: List[Dict[str, Any]] = []
        self.removed_bridge_ip: List[str] = []
        self.added_restrictions: List[Dict[str, Any]] = []
        self.removed_restrictions: List[Dict[str, Any]] = []

    def is_empty(self) -> bool:
        return (
            not self.added_vlans
            and not self.removed_vlans
            and not self.modified_vlans
            and not self.added_ports
            and not self.removed_ports
            and not self.added_bridge_members
            and not self.removed_bridge_members
            and not self.added_bridge_dhcp
            and not self.removed_bridge_dhcp
            and not self.modified_bridge_dhcp
            and not self.added_bridge_ip
            and not self.removed_bridge_ip
            and not self.added_restrictions
            and not self.removed_restrictions
        )

    def __str__(self):
        lines = []
        if self.added_vlans:
            lines.append(f"  Added VLANs: {[v['name'] for v in self.added_vlans]}")
        if self.removed_vlans:
            lines.append(f"  Removed VLANs: {self.removed_vlans}")
        if self.modified_vlans:
            for v in self.modified_vlans:
                lines.append(f"  Modified VLAN {v['name']}: {v['changes']}")
        if self.added_ports:
            for port, vlans in self.added_ports.items():
                lines.append(f"  Added port {port} to VLANs: {vlans}")
        if self.removed_ports:
            for port, vlans in self.removed_ports.items():
                lines.append(f"  Removed port {port} from VLANs: {vlans}")
        if self.added_bridge_members:
            for bridge, members in self.added_bridge_members.items():
                lines.append(f"  Added to bridge {bridge}: {members}")
        if self.removed_bridge_members:
            for bridge, members in self.removed_bridge_members.items():
                lines.append(f"  Removed from bridge {bridge}: {members}")
        if self.added_bridge_dhcp:
            for d in self.added_bridge_dhcp:
                lines.append(f"  Added DHCP on {d['bridge']}: start={d['range_start']}, size={d['range_size']}, lease={d['lease_time_min']}")
        if self.removed_bridge_dhcp:
            lines.append(f"  Removed DHCP on: {self.removed_bridge_dhcp}")
        if self.modified_bridge_dhcp:
            for d in self.modified_bridge_dhcp:
                lines.append(f"  Modified DHCP on {d['bridge']}: {d['changes']}")
        if self.added_bridge_ip:
            for d in self.added_bridge_ip:
                lines.append(f"  Set {d['bridge']} IP: {d['ip']}/{d['netmask']}")
        if self.removed_bridge_ip:
            lines.append(f"  Removed IP on: {self.removed_bridge_ip}")
        if self.added_restrictions:
            for r in self.added_restrictions:
                desc = f" ({r['description']})" if r.get("description") else ""
                bidi = " <-> " if r.get("bidirectional") else " -> "
                lines.append(f"  Add restriction: vlan{r['from']}{bidi}vlan{r['to']}{desc}")
        if self.removed_restrictions:
            for r in self.removed_restrictions:
                desc = f" ({r['description']})" if r.get("description") else ""
                bidi = " <-> " if r.get("bidirectional") else " -> "
                lines.append(f"  Remove restriction: vlan{r['from']}{bidi}vlan{r['to']}{desc}")
        return "\n".join(lines) if lines else "  (no changes)"


def _ip_network(ip_str, netmask_str):
    """Convert an IP address and netmask to an IPv4Network, or return None on failure."""
    try:
        prefix = ipaddress.IPv4Network(f"0.0.0.0/{netmask_str}").prefixlen
        return ipaddress.IPv4Network(f"{ip_str}/{prefix}", strict=False)
    except (ipaddress.AddressValueError, ValueError):
        return None


class NetworkConfig:
    """Editable network configuration specification for a router.

    Supports construction from a live router, JSON files, or from scratch.
    Provides mutation methods, validation, diffing, and application to a router.
    """

    def __init__(self):
        self.network: Dict[str, Any] = {
            "interfaces": {},
            "vlans": {},
            "bridges": {},
            "ports": {},
            "vlan_restrictions": [],
        }
        self.dhcp: Dict[str, Any] = {"static_leases": []}

    @classmethod
    def from_router(cls, conn, router) -> "NetworkConfig":
        """Query a live router and build a config from its current state."""
        config = cls()
        config.network["interfaces"] = router.get_interfaces(conn)
        config.network["bridges"] = router.get_bridges(conn)
        bridge_dhcp = router.get_bridge_dhcp_config(conn)
        for bridge, start, size, lease in bridge_dhcp:
            config.network["bridges"].setdefault(bridge, {"members": []})
            config.network["bridges"][bridge]["dhcp"] = {
                "enabled": True,
                "range_start": int(start),
                "range_size": int(size),
                "lease_time_min": int(lease),
            }
        for bridge in config.network["bridges"]:
            bridge_ip_info = router.get_bridge_ip_info(conn, bridge)
            for ip, netmask in bridge_ip_info:
                config.network["bridges"][bridge]["ip"] = ip
                config.network["bridges"][bridge]["netmask"] = netmask
        config.network["vlans"] = router.get_vlans(conn)
        port_vlan_map = router.get_port_vlan_map(conn)
        config.network["ports"] = port_vlan_map
        for vlan_name in config.network["vlans"]:
            match = re.search(r"\d+", vlan_name)
            if match:
                vlan_id = int(match.group())
                members = []
                for port, vlan_list in port_vlan_map.items():
                    if vlan_id in vlan_list:
                        members.append(port)
                config.network["vlans"][vlan_name]["members"] = members
        config._resolve_bridged_vlans()
        config.dhcp["static_leases"] = router.get_static_leases(conn)
        raw_rules = router.get_firewall_rules(conn)
        restrictions = []
        seen = set()
        for rule in raw_rules:
            from_id = rule.get("from")
            to_id = rule.get("to")
            if from_id is not None and to_id is not None:
                key = (from_id, to_id)
                if key not in seen:
                    seen.add(key)
                    r = {"from": from_id, "to": to_id}
                    if rule.get("description"):
                        r["description"] = rule["description"]
                    restrictions.append(r)
        config.network["vlan_restrictions"] = restrictions
        return config

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NetworkConfig":
        """Build a NetworkConfig from a raw dictionary (e.g. parsed JSON)."""
        config = cls()
        if "network" in data:
            config.network.update(data["network"])
        if "dhcp" in data:
            config.dhcp.update(data["dhcp"])
        config._normalize()
        return config

    @classmethod
    def from_json_file(cls, path: str) -> "NetworkConfig":
        """Load a NetworkConfig from a JSON file on disk."""
        with open(path, "r") as f:
            data = json.load(f)
        config = cls.from_dict(data)
        return config

    @classmethod
    def from_scratch(cls) -> "NetworkConfig":
        return cls()

    def _normalize(self):
        """Ensure all expected top-level keys exist and sub-dicts have default values."""
        self.network.setdefault("interfaces", {})
        self.network.setdefault("vlans", {})
        self.network.setdefault("bridges", {})
        self.network.setdefault("ports", {})
        self.network.setdefault("vlan_restrictions", [])
        self.dhcp.setdefault("static_leases", [])
        for vlan_name in self.network["vlans"]:
            self.network["vlans"][vlan_name].setdefault("members", [])

    def _resolve_bridged_vlans(self):
        """For bridged VLANs with 0.0.0.0 IP, resolve effective IP/netmask/DHCP from their bridge."""
        bridges = self.network.get("bridges", {})
        bridge_for_vlan = {}
        for bridge_name, bridge_data in bridges.items():
            for member in bridge_data.get("members", []):
                if member.startswith("vlan"):
                    bridge_for_vlan[member] = (bridge_name, bridge_data)

        for vlan_name, vlan_data in self.network.get("vlans", {}).items():
            if not vlan_data.get("bridged", False):
                continue
            if vlan_name not in bridge_for_vlan:
                continue
            bridge_name, bridge_data = bridge_for_vlan[vlan_name]
            if vlan_data.get("ip") == "0.0.0.0" and bridge_data.get("ip"):
                vlan_data["ip"] = bridge_data["ip"]
            if vlan_data.get("netmask") == "0.0.0.0" and bridge_data.get("netmask"):
                vlan_data["netmask"] = bridge_data["netmask"]
            if "dhcp" not in vlan_data and "dhcp" in bridge_data:
                vlan_data["dhcp"] = copy.deepcopy(bridge_data["dhcp"])

    def to_dict(self) -> Dict[str, Any]:
        self._normalize()
        return {
            "network": copy.deepcopy(self.network),
            "dhcp": copy.deepcopy(self.dhcp),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=4)

    def to_json_file(self, path: str):
        with open(path, "w") as f:
            f.write(self.to_json())

    def validate(self) -> List[str]:
        """Check the config for errors like overlapping subnets, duplicate IDs, and dangling references.

        Returns a list of error strings; empty list means valid.
        """
        errors = []
        seen_ids = {}
        for vlan_name, vlan_data in self.network.get("vlans", {}).items():
            match = re.search(r"\d+", vlan_name)
            if not match:
                errors.append(f"VLAN name '{vlan_name}' does not contain a numeric ID")
                continue
            vlan_id = int(match.group())
            if vlan_id in seen_ids:
                errors.append(f"Duplicate VLAN ID {vlan_id} in '{vlan_name}' and '{seen_ids[vlan_id]}'")
            seen_ids[vlan_id] = vlan_name
            ip = vlan_data.get("ip")
            netmask = vlan_data.get("netmask")
            if ip and netmask and ip != "0.0.0.0":
                net = _ip_network(ip, netmask)
                if net is None:
                    errors.append(f"VLAN {vlan_name}: invalid IP/netmask {ip}/{netmask}")
        subnets = []
        for vlan_name, vlan_data in self.network.get("vlans", {}).items():
            ip = vlan_data.get("ip")
            netmask = vlan_data.get("netmask")
            if ip and netmask and ip != "0.0.0.0":
                net = _ip_network(ip, netmask)
                if net is not None:
                    for other_name, other_net in subnets:
                        if net.overlaps(other_net):
                            errors.append(f"VLAN {vlan_name} subnet {net} overlaps with {other_name} subnet {other_net}")
                    subnets.append((vlan_name, net))
        for bridge_name, bridge_data in self.network.get("bridges", {}).items():
            ip = bridge_data.get("ip")
            netmask = bridge_data.get("netmask")
            if ip and netmask:
                net = _ip_network(ip, netmask)
                if net is not None:
                    for other_name, other_net in subnets:
                        if net.overlaps(other_net):
                            errors.append(f"Bridge {bridge_name} subnet {net} overlaps with {other_name} subnet {other_net}")
                    subnets.append((bridge_name, net))
            dhcp = bridge_data.get("dhcp")
            if dhcp and dhcp.get("enabled"):
                start = dhcp.get("range_start", 0)
                size = dhcp.get("range_size", 0)
                if ip and netmask:
                    net = _ip_network(ip, netmask)
                    if net is not None:
                        num_hosts = net.num_addresses - 2
                        if start < 1 or start >= num_hosts:
                            errors.append(f"Bridge {bridge_name}: DHCP range_start {start} out of subnet range")
                        if start + size > num_hosts:
                            errors.append(f"Bridge {bridge_name}: DHCP range_start({start}) + range_size({size}) exceeds subnet")
        port_untagged = {}
        for port, vlans in self.network.get("ports", {}).items():
            if len(vlans) > 1:
                untagged_candidates = [v for v in vlans if v == vlans[0]]
                if len(untagged_candidates) == 0 and len(vlans) > 0:
                    pass
            for vlan_id in vlans:
                vlan_name = f"vlan{vlan_id}"
                if vlan_name not in self.network.get("vlans", {}):
                    errors.append(f"Port {port} references non-existent VLAN {vlan_id}")
        for port, vlans in self.network.get("ports", {}).items():
            primary = vlans[0] if vlans else None
            if primary is not None:
                if port in port_untagged and port_untagged[port] != primary:
                    pass
                port_untagged[port] = primary
        for bridge_name, bridge_data in self.network.get("bridges", {}).items():
            for member in bridge_data.get("members", []):
                if (member not in self.network.get("vlans", {})
                        and member not in self.network.get("interfaces", {})):
                    errors.append(f"Bridge {bridge_name} member '{member}' not found in interfaces or VLANs")
        for vlan_name, vlan_data in self.network.get("vlans", {}).items():
            dhcp = vlan_data.get("dhcp")
            if dhcp and dhcp.get("enabled"):
                start = dhcp.get("range_start", 0)
                size = dhcp.get("range_size", 0)
                ip = vlan_data.get("ip")
                netmask = vlan_data.get("netmask")
                if ip and netmask and ip != "0.0.0.0":
                    net = _ip_network(ip, netmask)
                    if net is not None:
                        num_hosts = net.num_addresses - 2
                        if start < 1 or start >= num_hosts:
                            errors.append(f"VLAN {vlan_name}: DHCP range_start {start} out of subnet range")
                        if start + size > num_hosts:
                            errors.append(f"VLAN {vlan_name}: DHCP range exceeds subnet")
        for r in self.network.get("vlan_restrictions", []):
            from_id = r.get("from")
            to_id = r.get("to")
            if from_id == to_id:
                errors.append(f"VLAN restriction from VLAN {from_id} to itself is not valid")
            from_name = f"vlan{from_id}"
            to_name = f"vlan{to_id}"
            if from_name not in self.network.get("vlans", {}):
                errors.append(f"VLAN restriction references non-existent source VLAN {from_id}")
            if to_name not in self.network.get("vlans", {}):
                errors.append(f"VLAN restriction references non-existent destination VLAN {to_id}")
            from_bridge = None
            to_bridge = None
            for bridge_name, bridge_data in self.network.get("bridges", {}).items():
                if from_name in bridge_data.get("members", []):
                    from_bridge = bridge_name
                if to_name in bridge_data.get("members", []):
                    to_bridge = bridge_name
            if from_bridge and to_bridge and from_bridge == to_bridge:
                errors.append(f"VLAN restriction from {from_id} to {to_id}: both VLANs are on bridge {from_bridge}; "
                              f"iptables will not filter same-bridge traffic without bridge-nf-call-iptables")
        seen_restriction_keys = set()
        for r in self.network.get("vlan_restrictions", []):
            key = (r.get("from"), r.get("to"))
            if key in seen_restriction_keys:
                errors.append(f"Duplicate VLAN restriction from VLAN {key[0]} to VLAN {key[1]}")
            seen_restriction_keys.add(key)
            if r.get("bidirectional"):
                reverse_key = (key[1], key[0])
                if reverse_key in seen_restriction_keys:
                    for r2 in self.network.get("vlan_restrictions", []):
                        if (r2.get("from"), r2.get("to")) == reverse_key:
                            if not r2.get("bidirectional"):
                                errors.append(f"Redundant: bidirectional restriction from {key[0]} to {key[1]} "
                                             f"overlaps with explicit reverse from {reverse_key[0]} to {reverse_key[1]}")
        return errors

    def diff(self, other: "NetworkConfig") -> ConfigDiff:
        """Compute the differences between self (old/current) and other (new/desired).

        Returns a ConfigDiff listing what was added, removed, or modified.
        """
        d = ConfigDiff()
        my_vlans = self.network.get("vlans", {})
        other_vlans = other.network.get("vlans", {})
        for vlan_name in other_vlans:
            if vlan_name not in my_vlans:
                d.added_vlans.append({"name": vlan_name, **copy.deepcopy(other_vlans[vlan_name])})
        for vlan_name in my_vlans:
            if vlan_name not in other_vlans:
                d.removed_vlans.append(vlan_name)
        for vlan_name in my_vlans:
            if vlan_name in other_vlans:
                changes = {}
                my_v = my_vlans[vlan_name]
                other_v = other_vlans[vlan_name]
                for key in set(list(my_v.keys()) + list(other_v.keys())):
                    my_val = my_v.get(key)
                    other_val = other_v.get(key)
                    if my_val != other_val:
                        changes[key] = {"from": my_val, "to": other_val}
                if changes:
                    d.modified_vlans.append({"name": vlan_name, "changes": changes})
        my_ports = self.network.get("ports", {})
        other_ports = other.network.get("ports", {})
        all_ports = set(list(my_ports.keys()) + list(other_ports.keys()))
        for port in all_ports:
            my_vlans = set(my_ports.get(port, []))
            other_vlans_set = set(other_ports.get(port, []))
            added = other_vlans_set - my_vlans
            removed = my_vlans - other_vlans_set
            if added:
                d.added_ports[port] = sorted(added)
            if removed:
                d.removed_ports[port] = sorted(removed)
        my_bridges = self.network.get("bridges", {})
        other_bridges = other.network.get("bridges", {})
        all_bridges = set(list(my_bridges.keys()) + list(other_bridges.keys()))
        for bridge in all_bridges:
            my_b = my_bridges.get(bridge, {})
            other_b = other_bridges.get(bridge, {})
            my_members = set(my_b.get("members", []))
            other_members = set(other_b.get("members", []))
            added = other_members - my_members
            removed = my_members - other_members
            if added:
                d.added_bridge_members.setdefault(bridge, []).extend(sorted(added))
            if removed:
                d.removed_bridge_members.setdefault(bridge, []).extend(sorted(removed))
            my_dhcp = my_b.get("dhcp")
            other_dhcp = other_b.get("dhcp")
            if other_dhcp and not my_dhcp:
                d.added_bridge_dhcp.append({
                    "bridge": bridge,
                    "range_start": other_dhcp.get("range_start"),
                    "range_size": other_dhcp.get("range_size"),
                    "lease_time_min": other_dhcp.get("lease_time_min"),
                })
            elif my_dhcp and not other_dhcp:
                d.removed_bridge_dhcp.append(bridge)
            elif my_dhcp and other_dhcp:
                changes = {}
                for key in ["range_start", "range_size", "lease_time_min", "enabled"]:
                    if my_dhcp.get(key) != other_dhcp.get(key):
                        changes[key] = {"from": my_dhcp.get(key), "to": other_dhcp.get(key)}
                if changes:
                    d.modified_bridge_dhcp.append({"bridge": bridge, "changes": changes})
            my_ip = my_b.get("ip")
            other_ip = other_b.get("ip")
            other_nm = other_b.get("netmask")
            if other_ip and not my_ip:
                d.added_bridge_ip.append({"bridge": bridge, "ip": other_ip, "netmask": other_nm})
            elif my_ip and not other_ip:
                d.removed_bridge_ip.append(bridge)
            elif my_ip and other_ip and (my_ip != other_ip or my_b.get("netmask") != other_nm):
                d.added_bridge_ip.append({"bridge": bridge, "ip": other_ip, "netmask": other_nm})
        my_restrictions = {(r["from"], r["to"]): r for r in self.network.get("vlan_restrictions", [])}
        other_restrictions = {(r["from"], r["to"]): r for r in other.network.get("vlan_restrictions", [])}
        for key, r in other_restrictions.items():
            if key not in my_restrictions:
                d.added_restrictions.append(copy.deepcopy(r))
        for key, r in my_restrictions.items():
            if key not in other_restrictions:
                d.removed_restrictions.append(copy.deepcopy(r))
        return d

    def add_vlan(self, vlan_id: int, ip: str = "0.0.0.0",
                 netmask: str = "0.0.0.0", bridged: bool = False,
                 nat: bool = False, dhcp_enabled: bool = False,
                 dhcp_start: int = 0, dhcp_size: int = 0,
                 dhcp_lease: int = 0):
        """Add a new VLAN to the config. Raises ValueError if the VLAN ID already exists.

        When bridged is True, automatically creates a dedicated bridge (br<vlan_id>)
        with this VLAN as its sole member, avoiding same-bridge filtering issues.
        """
        vlan_name = f"vlan{vlan_id}"
        if vlan_name in self.network.get("vlans", {}):
            raise ValueError(f"VLAN {vlan_name} already exists")
        vlan_data = {
            "ip": ip,
            "netmask": netmask,
            "bridged": bridged,
            "nat": nat,
            "members": [],
        }
        if dhcp_enabled:
            vlan_data["dhcp"] = {
                "enabled": True,
                "range_start": dhcp_start,
                "range_size": dhcp_size,
                "lease_time_min": dhcp_lease,
            }
        self.network.setdefault("vlans", {})[vlan_name] = vlan_data
        if bridged:
            bridge_name = f"br{vlan_id}"
            bridges = self.network.setdefault("bridges", {})
            if bridge_name not in bridges:
                bridges[bridge_name] = {"members": [vlan_name]}
            else:
                members = bridges[bridge_name].setdefault("members", [])
                if vlan_name not in members:
                    members.append(vlan_name)
        return vlan_name

    def remove_vlan(self, vlan_id: int):
        """Remove a VLAN and clean up port/bridge references to it. Raises ValueError if not found."""
        vlan_name = f"vlan{vlan_id}"
        if vlan_name not in self.network.get("vlans", {}):
            raise ValueError(f"VLAN {vlan_name} does not exist")
        del self.network["vlans"][vlan_name]
        for port, vlans in self.network.get("ports", {}).items():
            self.network["ports"][port] = [v for v in vlans if v != vlan_id]
        for bridge_name, bridge_data in self.network.get("bridges", {}).items():
            members = bridge_data.get("members", [])
            if vlan_name in members:
                members.remove(vlan_name)

    def update_vlan(self, vlan_id: int, **kwargs):
        """Update properties of an existing VLAN. Accepts ip, netmask, bridged, nat, dhcp_enabled, dhcp_start/size/lease."""
        vlan_name = f"vlan{vlan_id}"
        if vlan_name not in self.network.get("vlans", {}):
            raise ValueError(f"VLAN {vlan_name} does not exist")
        vlan_data = self.network["vlans"][vlan_name]
        for key in ["ip", "netmask", "bridged", "nat"]:
            if key in kwargs:
                vlan_data[key] = kwargs[key]
        if "dhcp_enabled" in kwargs:
            if kwargs["dhcp_enabled"]:
                vlan_data["dhcp"] = {
                    "enabled": True,
                    "range_start": kwargs.get("dhcp_start", vlan_data.get("dhcp", {}).get("range_start", 0)),
                    "range_size": kwargs.get("dhcp_size", vlan_data.get("dhcp", {}).get("range_size", 0)),
                    "lease_time_min": kwargs.get("dhcp_lease", vlan_data.get("dhcp", {}).get("lease_time_min", 0)),
                }
            else:
                vlan_data.pop("dhcp", None)

    def assign_port(self, port: str, vlan_id: int):
        """Add a port to a VLAN's member list and update the port-to-VLAN mapping. Raises ValueError if VLAN doesn't exist."""
        if f"vlan{vlan_id}" not in self.network.get("vlans", {}):
            raise ValueError(f"VLAN {vlan_id} does not exist")
        ports = self.network.setdefault("ports", {})
        if port not in ports:
            ports[port] = []
        if vlan_id not in ports[port]:
            ports[port].append(vlan_id)
            ports[port].sort()
        vlan_name = f"vlan{vlan_id}"
        if vlan_name in self.network.get("vlans", {}):
            members = self.network["vlans"][vlan_name].setdefault("members", [])
            if port not in members:
                members.append(port)

    def unassign_port(self, port: str, vlan_id: int):
        ports = self.network.get("ports", {})
        if port in ports and vlan_id in ports[port]:
            ports[port].remove(vlan_id)
        vlan_name = f"vlan{vlan_id}"
        if vlan_name in self.network.get("vlans", {}):
            members = self.network["vlans"][vlan_name].get("members", [])
            if port in members:
                members.remove(port)

    def add_bridge_vlan(self, bridge: str, vlan_name: str):
        """Add a VLAN interface as a member of a bridge. Creates the bridge if it doesn't exist. Raises ValueError if VLAN doesn't exist."""
        if vlan_name not in self.network.get("vlans", {}):
            raise ValueError(f"VLAN {vlan_name} does not exist")
        bridges = self.network.setdefault("bridges", {})
        if bridge not in bridges:
            bridges[bridge] = {"members": []}
        members = bridges[bridge].setdefault("members", [])
        if vlan_name not in members:
            members.append(vlan_name)

    def remove_bridge_vlan(self, bridge: str, vlan_name: str):
        bridges = self.network.get("bridges", {})
        if bridge in bridges:
            members = bridges[bridge].get("members", [])
            if vlan_name in members:
                members.remove(vlan_name)

    def set_vlan_dhcp(self, vlan_id: int, start: int, size: int, lease: int):
        vlan_name = f"vlan{vlan_id}"
        if vlan_name not in self.network.get("vlans", {}):
            raise ValueError(f"VLAN {vlan_name} does not exist")
        self.network["vlans"][vlan_name]["dhcp"] = {
            "enabled": True,
            "range_start": start,
            "range_size": size,
            "lease_time_min": lease,
        }

    def remove_vlan_dhcp(self, vlan_id: int):
        vlan_name = f"vlan{vlan_id}"
        if vlan_name not in self.network.get("vlans", {}):
            raise ValueError(f"VLAN {vlan_name} does not exist")
        self.network["vlans"][vlan_name].pop("dhcp", None)

    def set_bridge_dhcp(self, bridge: str, start: int, size: int, lease: int):
        bridges = self.network.setdefault("bridges", {})
        if bridge not in bridges:
            bridges[bridge] = {"members": []}
        bridges[bridge]["dhcp"] = {
            "enabled": True,
            "range_start": start,
            "range_size": size,
            "lease_time_min": lease,
        }

    def remove_bridge_dhcp(self, bridge: str):
        bridges = self.network.get("bridges", {})
        if bridge in bridges:
            bridges[bridge].pop("dhcp", None)

    @staticmethod
    def _restriction_key(r):
        return (r["from"], r["to"])

    def add_restriction(self, from_id: int, to_id: int,
                        description: str = "", bidirectional: bool = False):
        """Add a VLAN routing restriction. Raises ValueError if from == to or duplicate."""
        if from_id == to_id:
            raise ValueError(f"Cannot create a restriction from VLAN {from_id} to itself")
        restrictions = self.network.setdefault("vlan_restrictions", [])
        new_entry = {"from": from_id, "to": to_id, "bidirectional": bidirectional}
        if description:
            new_entry["description"] = description
        for r in restrictions:
            if self._restriction_key(r) == (from_id, to_id):
                raise ValueError(f"Restriction from VLAN {from_id} to VLAN {to_id} already exists")
        restrictions.append(new_entry)
        if bidirectional:
            reverse = {"from": to_id, "to": from_id, "bidirectional": True}
            if description:
                reverse["description"] = f"{description} (reverse)"
            for r in restrictions:
                if self._restriction_key(r) == (to_id, from_id):
                    raise ValueError(f"Reverse restriction from VLAN {to_id} to VLAN {from_id} already exists")
            restrictions.append(reverse)

    def remove_restriction(self, from_id: int, to_id: int, bidirectional: bool = False):
        """Remove a VLAN routing restriction. If bidirectional, removes the reverse too."""
        restrictions = self.network.get("vlan_restrictions", [])
        target = (from_id, to_id)
        self.network["vlan_restrictions"] = [r for r in restrictions if self._restriction_key(r) != target]
        if bidirectional:
            reverse = (to_id, from_id)
            self.network["vlan_restrictions"] = [r for r in self.network["vlan_restrictions"] if self._restriction_key(r) != reverse]

    def apply_to_router(self, conn, router, mode="diff"):
        """Push this config to a router. In 'diff' mode, only changed settings are applied.
        In 'full' mode, all settings are written regardless of current state."""
        if mode not in ("diff", "full"):
            raise ValueError(f"Invalid apply mode: {mode}. Must be 'diff' or 'full'.")
        if mode == "full":
            self._apply_full(conn, router)
        else:
            current = NetworkConfig.from_router(conn, router)
            d = current.diff(self)
            self._apply_diff(conn, router, d)

    def _apply_full(self, conn, router):
        """Write the entire config to the router, overwriting all relevant settings."""
        for vlan_name, vlan_data in self.network.get("vlans", {}).items():
            match = re.search(r"\d+", vlan_name)
            if not match:
                continue
            vlan_id = int(match.group())
            router.set_vlan_ip(conn, vlan_id, vlan_data.get("ip", "0.0.0.0"),
                               vlan_data.get("netmask", "0.0.0.0"))
            router.set_vlan_bridged(conn, vlan_id, vlan_data.get("bridged", False))
            router.set_vlan_nat(conn, vlan_id, vlan_data.get("nat", False))
            dhcp = vlan_data.get("dhcp")
            if dhcp and dhcp.get("enabled"):
                router.set_vlan_dhcp(conn, vlan_id,
                                    dhcp.get("range_start", 0),
                                    dhcp.get("range_size", 0),
                                    dhcp.get("lease_time_min", 0))
            else:
                router.remove_vlan_dhcp(conn, vlan_id)
        for bridge_name, bridge_data in self.network.get("bridges", {}).items():
            for member in bridge_data.get("members", []):
                router.add_bridge_member(conn, bridge_name, member)
            dhcp = bridge_data.get("dhcp")
            if dhcp and dhcp.get("enabled"):
                router.set_bridge_dhcp(conn, bridge_name,
                                       dhcp.get("range_start", 0),
                                       dhcp.get("range_size", 0),
                                       dhcp.get("lease_time_min", 0))
            ip = bridge_data.get("ip")
            netmask = bridge_data.get("netmask")
            if ip and netmask:
                router.set_bridge_ip(conn, bridge_name, ip, netmask)
        router.set_port_vlan_map(conn, self.network.get("ports", {}))
        for vlan_name, vlan_data in self.network.get("vlans", {}).items():
            members = vlan_data.get("members", [])
            router.set_vlan_members(conn, vlan_name, members)
        restrictions = self._expand_restrictions()
        router.set_firewall_rules(conn, restrictions)
        router.commit_config(conn)
        router.restart_dhcp_service(conn)

    def _apply_diff(self, conn, router, d: ConfigDiff):
        """Apply only the changes described in a ConfigDiff to the router."""
        for vlan_info in d.added_vlans:
            vlan_name = vlan_info["name"]
            match = re.search(r"\d+", vlan_name)
            if not match:
                continue
            vlan_id = int(match.group())
            router.set_vlan_ip(conn, vlan_id,
                               vlan_info.get("ip", "0.0.0.0"),
                               vlan_info.get("netmask", "0.0.0.0"))
            router.set_vlan_bridged(conn, vlan_id, vlan_info.get("bridged", False))
            router.set_vlan_nat(conn, vlan_id, vlan_info.get("nat", False))
            dhcp = vlan_info.get("dhcp")
            if dhcp and dhcp.get("enabled"):
                router.set_vlan_dhcp(conn, vlan_id,
                                     dhcp.get("range_start", 0),
                                     dhcp.get("range_size", 0),
                                     dhcp.get("lease_time_min", 0))
        for vlan_name in d.removed_vlans:
            match = re.search(r"\d+", vlan_name)
            if not match:
                continue
            vlan_id = int(match.group())
            router.delete_vlan(conn, vlan_id)
        for mod in d.modified_vlans:
            vlan_name = mod["name"]
            match = re.search(r"\d+", vlan_name)
            if not match:
                continue
            vlan_id = int(match.group())
            changes = mod["changes"]
            if "ip" in changes or "netmask" in changes:
                ip_val = changes.get("ip", {}).get("to", "0.0.0.0")
                if isinstance(ip_val, dict):
                    ip_val = ip_val.get("to", "0.0.0.0")
                nm_val = changes.get("netmask", {}).get("to", "0.0.0.0")
                if isinstance(nm_val, dict):
                    nm_val = nm_val.get("to", "0.0.0.0")
                router.set_vlan_ip(conn, vlan_id, ip_val, nm_val)
            if "bridged" in changes:
                val = changes["bridged"]["to"]
                router.set_vlan_bridged(conn, vlan_id, val)
            if "nat" in changes:
                val = changes["nat"]["to"]
                router.set_vlan_nat(conn, vlan_id, val)
            if "dhcp" in changes:
                dhcp_val = changes["dhcp"]["to"]
                if dhcp_val and dhcp_val.get("enabled"):
                    router.set_vlan_dhcp(conn, vlan_id,
                                         dhcp_val.get("range_start", 0),
                                         dhcp_val.get("range_size", 0),
                                         dhcp_val.get("lease_time_min", 0))
                else:
                    router.remove_vlan_dhcp(conn, vlan_id)
        all_port_changes = set(list(d.added_ports.keys()) + list(d.removed_ports.keys()))
        if all_port_changes:
            current = NetworkConfig.from_router(conn, router)
            new_port_map = copy.deepcopy(current.network.get("ports", {}))
            for port, vlans in d.added_ports.items():
                if port not in new_port_map:
                    new_port_map[port] = []
                for v in vlans:
                    if v not in new_port_map[port]:
                        new_port_map[port].append(v)
                new_port_map[port] = sorted(new_port_map[port])
            for port, vlans in d.removed_ports.items():
                if port in new_port_map:
                    new_port_map[port] = [v for v in new_port_map[port] if v not in vlans]
            router.set_port_vlan_map(conn, new_port_map)
        for bridge, members in d.added_bridge_members.items():
            for member in members:
                router.add_bridge_member(conn, bridge, member)
        for bridge, members in d.removed_bridge_members.items():
            for member in members:
                router.remove_bridge_member(conn, bridge, member)
        for dhcp_info in d.added_bridge_dhcp:
            bridge = dhcp_info["bridge"]
            router.set_bridge_dhcp(conn, bridge,
                                   dhcp_info.get("range_start", 0),
                                   dhcp_info.get("range_size", 0),
                                   dhcp_info.get("lease_time_min", 0))
        for bridge in d.removed_bridge_dhcp:
            current = NetworkConfig.from_router(conn, router)
            if bridge in current.network.get("bridges", {}):
                bridge_data = current.network["bridges"][bridge]
                bridge_data.pop("dhcp", None)
        for mod in d.modified_bridge_dhcp:
            bridge = mod["bridge"]
            changes = mod["changes"]
            if any(k in changes for k in ["range_start", "range_size", "lease_time_min", "enabled"]):
                current = NetworkConfig.from_router(conn, router)
                if bridge in current.network.get("bridges", {}):
                    existing = current.network["bridges"][bridge].get("dhcp", {})
                    router.set_bridge_dhcp(conn, bridge,
                                           changes.get("range_start", {}).get("to", existing.get("range_start", 0)) if "range_start" in changes else existing.get("range_start", 0),
                                           changes.get("range_size", {}).get("to", existing.get("range_size", 0)) if "range_size" in changes else existing.get("range_size", 0),
                                           changes.get("lease_time_min", {}).get("to", existing.get("lease_time_min", 0)) if "lease_time_min" in changes else existing.get("lease_time_min", 0))
        for ip_info in d.added_bridge_ip:
            router.set_bridge_ip(conn, ip_info["bridge"], ip_info["ip"], ip_info["netmask"])
        for bridge_name in d.removed_bridge_ip:
            pass
        if d.added_restrictions or d.removed_restrictions:
            current_restrictions = self._expand_restrictions()
            router.set_firewall_rules(conn, current_restrictions)
        router.commit_config(conn)
        router.restart_dhcp_service(conn)

    def _expand_restrictions(self):
        """Convert vlan_restrictions into firewall rule dicts.

        Each entry is converted to a rule with from_iface/to_iface.
        The bidirectional flag was already expanded into separate entries
        by add_restriction, so no further expansion is needed here.
        """
        rules = []
        for r in self.network.get("vlan_restrictions", []):
            from_id = r["from"]
            to_id = r["to"]
            desc = r.get("description", "")
            rule = {"from_iface": f"vlan{from_id}", "to_iface": f"vlan{to_id}"}
            if desc:
                rule["description"] = desc
            rules.append(rule)
        return rules

    def verify(self, conn, router) -> List[str]:
        """Re-read the router's current config and compare against this spec.

        Returns a list of issue strings; empty list means the router matches.
        """
        current = NetworkConfig.from_router(conn, router)
        d = current.diff(self)
        issues = []
        if not d.is_empty():
            issues.append("Router configuration does not match desired specification.")
            issues.append(f"Changes remaining:\n{d}")
        return issues
