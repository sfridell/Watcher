import json
import os
import copy
from typing import List, Dict, Any
from .base import RouterBase

_DEFAULT_STATE = {
    "interfaces": {
        "eth0": {"type": "unknown", "vlan": None},
        "vlan1": {"type": "unknown", "vlan": None},
        "vlan2": {"type": "unknown", "vlan": None},
        "br0": {"type": "unknown", "vlan": None},
        "eth1": {"type": "unknown", "vlan": None},
        "lo": {"type": "unknown", "vlan": None},
    },
    "bridges": {
        "br0": {
            "members": ["vlan1", "eth1"],
            "ip": "192.168.1.1",
            "netmask": "255.255.255.0",
            "dhcp": {
                "enabled": True,
                "range_start": 100,
                "range_size": 150,
                "lease_time_min": 1440,
            },
        }
    },
    "vlans": {
        "vlan1": {
            "ip": "0.0.0.0",
            "netmask": "0.0.0.0",
            "bridged": True,
            "nat": False,
        },
        "vlan2": {
            "ip": "0.0.0.0",
            "netmask": "0.0.0.0",
            "bridged": False,
            "nat": True,
        },
    },
    "port_vlan_map": {
        "port0": [1],
        "port1": [1],
        "port2": [1],
        "port3": [1],
        "port4": [2],
        "port5": [1, 2],
    },
    "mdhcpd": "br0>On>100>150>1440",
    "dhcp_leases": [
        ["1000000000", "aa:bb:cc:dd:ee:ff", "192.168.1.100", "laptop"],
        ["1000000000", "11:22:33:44:55:66", "192.168.1.101", "phone"],
    ],
    "static_leases": [["aa:bb:cc:dd:ee:ff", "server", "192.168.1.50"]],
    "nvram": {
        "vlan1_ipaddr": "0.0.0.0",
        "vlan1_netmask": "0.0.0.0",
        "vlan1_bridged": "1",
        "vlan1_nat": "0",
        "vlan2_ipaddr": "0.0.0.0",
        "vlan2_netmask": "0.0.0.0",
        "vlan2_bridged": "0",
        "vlan2_nat": "1",
        "port0vlans": "1",
        "port1vlans": "1",
        "port2vlans": "1",
        "port3vlans": "1",
        "port4vlans": "2",
        "port5vlans": "1 2",
        "static_leases": "aa:bb:cc:dd:ee:ff=server=192.168.1.50= ",
        "mdhcpd": "br0>On>100>150>1440",
        "lan_ipaddr": "192.168.1.1",
        "lan_netmask": "255.255.255.0",
    },
    "vpn": {
        "enabled": False,
        "connected": False,
        "remote": "",
        "port": "",
        "proto": "",
        "interface": "",
    },
    "vpn_config": {},
}

_MOCK_STATE_DIR = "./mock_state"


class MockRouter(RouterBase):
    """In-memory router simulator for testing. Persists state to JSON files in ./mock_state/."""

    def __init__(self, state=None, name=None):
        """Initialize with an explicit state dict, a named state file, or default state."""
        if state is not None:
            self._state = copy.deepcopy(state)
        elif name is not None:
            self._load_state(name)
        else:
            self._state = copy.deepcopy(_DEFAULT_STATE)
        self._name = name

    def _state_path(self, name):
        return os.path.join(_MOCK_STATE_DIR, f"{name}.json")

    def _load_state(self, name):
        """Load router state from a JSON file in mock_state/, or use defaults if file doesn't exist."""
        path = self._state_path(name)
        if os.path.exists(path):
            with open(path, "r") as f:
                self._state = json.load(f)
        else:
            self._state = copy.deepcopy(_DEFAULT_STATE)

    def _save_state(self):
        """Persist current state to disk if this instance has a name (stateless mocks are not saved)."""
        if self._name is None:
            return
        path = self._state_path(self._name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(self._state, f, indent=2)

    def get_dhcp_leases(self, conn) -> List[List[str]]:
        return copy.deepcopy(self._state.get("dhcp_leases", []))

    def get_static_leases(self, conn) -> List[List[str]]:
        return copy.deepcopy(self._state.get("static_leases", []))

    def set_static_leases(self, conn, leases: List[List[str]]):
        self._state["static_leases"] = copy.deepcopy(leases)
        parts = []
        for d in leases:
            parts.append(f"{d[0]}={d[1]}={d[2]}= ")
        self._state["nvram"]["static_leases"] = " ".join(parts)
        self._save_state()

    def remove_dhcp_leases(self, conn, mac_addresses: List[str]):
        self._state["dhcp_leases"] = [
            lease for lease in self._state.get("dhcp_leases", [])
            if lease[1] not in mac_addresses
        ]
        self._save_state()

    def restart_dhcp_service(self, conn):
        self._save_state()

    def commit_config(self, conn):
        self._save_state()

    def get_interfaces(self, conn) -> Dict[str, Any]:
        return copy.deepcopy(self._state.get("interfaces", {}))

    def get_bridges(self, conn) -> Dict[str, Any]:
        result = {}
        for bridge_name, bridge_data in self._state.get("bridges", {}).items():
            result[bridge_name] = {"members": list(bridge_data.get("members", []))}
        return result

    def get_vlans(self, conn) -> Dict[str, Any]:
        return copy.deepcopy(self._state.get("vlans", {}))

    def get_port_vlan_map(self, conn) -> Dict[str, List[int]]:
        return copy.deepcopy(self._state.get("port_vlan_map", {}))

    def get_bridge_dhcp_config(self, conn) -> List[tuple]:
        """Return bridge DHCP entries as (bridge, start, size, lease) tuples, parsing both nvram and bridge state."""
        results = []
        mdhcpd = self._state.get("nvram", {}).get("mdhcpd", "")
        if mdhcpd:
            for entry in mdhcpd.split():
                parts = entry.split(">")
                if len(parts) == 5 and parts[1] == "On":
                    results.append((parts[0], parts[2], parts[3], parts[4]))
        for bridge_name, bridge_data in self._state.get("bridges", {}).items():
            dhcp = bridge_data.get("dhcp")
            if dhcp and dhcp.get("enabled"):
                found = any(r[0] == bridge_name for r in results)
                if not found:
                    results.append((
                        bridge_name,
                        str(dhcp["range_start"]),
                        str(dhcp["range_size"]),
                        str(dhcp["lease_time_min"]),
                    ))
        return results

    def get_bridge_ip_info(self, conn, bridge: str) -> List[tuple]:
        bridge_data = self._state.get("bridges", {}).get(bridge, {})
        ip = bridge_data.get("ip")
        netmask = bridge_data.get("netmask")
        if ip and netmask:
            return [(ip, netmask)]
        return []

    def set_vlan_ip(self, conn, vlan_id: int, ip: str, netmask: str):
        vlan_key = f"vlan{vlan_id}"
        if vlan_key in self._state.get("vlans", {}):
            self._state["vlans"][vlan_key]["ip"] = ip
            self._state["vlans"][vlan_key]["netmask"] = netmask
        else:
            self._state.setdefault("vlans", {})[vlan_key] = {
                "ip": ip,
                "netmask": netmask,
                "bridged": False,
                "nat": False,
            }
        self._state.setdefault("nvram", {})[f"{vlan_key}_ipaddr"] = ip
        self._state.setdefault("nvram", {})[f"{vlan_key}_netmask"] = netmask
        self._save_state()

    def set_vlan_bridged(self, conn, vlan_id: int, bridged: bool):
        vlan_key = f"vlan{vlan_id}"
        if vlan_key not in self._state.get("vlans", {}):
            self._state.setdefault("vlans", {})[vlan_key] = {
                "ip": "0.0.0.0",
                "netmask": "0.0.0.0",
                "bridged": bridged,
                "nat": False,
            }
        else:
            self._state["vlans"][vlan_key]["bridged"] = bridged
        self._state.setdefault("nvram", {})[f"{vlan_key}_bridged"] = "1" if bridged else "0"
        self._save_state()

    def set_vlan_nat(self, conn, vlan_id: int, nat: bool):
        vlan_key = f"vlan{vlan_id}"
        if vlan_key not in self._state.get("vlans", {}):
            self._state.setdefault("vlans", {})[vlan_key] = {
                "ip": "0.0.0.0",
                "netmask": "0.0.0.0",
                "bridged": False,
                "nat": nat,
            }
        else:
            self._state["vlans"][vlan_key]["nat"] = nat
        self._state.setdefault("nvram", {})[f"{vlan_key}_nat"] = "1" if nat else "0"
        self._save_state()

    def set_vlan_dhcp(self, conn, vlan_id: int, start: int, size: int, lease: int):
        vlan_key = f"vlan{vlan_id}"
        if vlan_key not in self._state.get("vlans", {}):
            self._state.setdefault("vlans", {})[vlan_key] = {
                "ip": "0.0.0.0",
                "netmask": "0.0.0.0",
                "bridged": False,
                "nat": False,
            }
        self._state["vlans"][vlan_key]["dhcp"] = {
            "enabled": True,
            "range_start": start,
            "range_size": size,
            "lease_time_min": lease,
        }
        new_entry = f"{vlan_key}>On>{start}>{size}>{lease}"
        mdhcpd = self._state.get("nvram", {}).get("mdhcpd", "")
        existing = mdhcpd.split() if mdhcpd else []
        filtered = [e for e in existing if not e.startswith(f"{vlan_key}>")]
        filtered.append(new_entry)
        self._state["nvram"]["mdhcpd"] = " ".join(filtered)
        self._save_state()

    def remove_vlan_dhcp(self, conn, vlan_id: int):
        vlan_key = f"vlan{vlan_id}"
        vlan_data = self._state.get("vlans", {}).get(vlan_key, {})
        vlan_data.pop("dhcp", None)
        mdhcpd = self._state.get("nvram", {}).get("mdhcpd", "")
        existing = mdhcpd.split() if mdhcpd else []
        filtered = [e for e in existing if not e.startswith(f"{vlan_key}>")]
        self._state["nvram"]["mdhcpd"] = " ".join(filtered)
        self._save_state()

    def delete_vlan(self, conn, vlan_id: int):
        """Remove a VLAN entirely: delete its data, strip it from port maps and bridges, and remove its DHCP entry."""
        vlan_key = f"vlan{vlan_id}"
        self._state.get("vlans", {}).pop(vlan_key, None)
        nvram = self._state.get("nvram", {})
        for key in [f"{vlan_key}_ipaddr", f"{vlan_key}_netmask",
                     f"{vlan_key}_bridged", f"{vlan_key}_nat"]:
            nvram.pop(key, None)
        mdhcpd = nvram.get("mdhcpd", "")
        existing = mdhcpd.split() if mdhcpd else []
        filtered = [e for e in existing if not e.startswith(f"{vlan_key}>")]
        nvram["mdhcpd"] = " ".join(filtered)
        port_map = self._state.get("port_vlan_map", {})
        for port in list(port_map.keys()):
            port_map[port] = [v for v in port_map[port] if v != vlan_id]
        for bridge_name, bridge_data in self._state.get("bridges", {}).items():
            members = bridge_data.get("members", [])
            if vlan_key in members:
                members.remove(vlan_key)
        self._save_state()

    def set_port_vlan_map(self, conn, port_map: Dict[str, List[int]]):
        self._state["port_vlan_map"] = copy.deepcopy(port_map)
        for port, vlans in port_map.items():
            nvram_key = f"{port}vlans"
            self._state.setdefault("nvram", {})[nvram_key] = " ".join(str(v) for v in vlans)
        self._save_state()

    def set_bridge_dhcp(self, conn, bridge: str, start: int, size: int, lease: int):
        if bridge not in self._state.get("bridges", {}):
            self._state.setdefault("bridges", {})[bridge] = {"members": []}
        self._state["bridges"][bridge]["dhcp"] = {
            "enabled": True,
            "range_start": start,
            "range_size": size,
            "lease_time_min": lease,
        }
        new_entry = f"{bridge}>On>{start}>{size}>{lease}"
        mdhcpd = self._state.get("nvram", {}).get("mdhcpd", "")
        existing = mdhcpd.split() if mdhcpd else []
        filtered = [e for e in existing if not e.startswith(f"{bridge}>")]
        filtered.append(new_entry)
        self._state["nvram"]["mdhcpd"] = " ".join(filtered)
        self._save_state()

    def set_bridge_ip(self, conn, bridge: str, ip: str, netmask: str):
        if bridge not in self._state.get("bridges", {}):
            self._state.setdefault("bridges", {})[bridge] = {"members": []}
        self._state["bridges"][bridge]["ip"] = ip
        self._state["bridges"][bridge]["netmask"] = netmask
        self._save_state()

    def add_bridge_member(self, conn, bridge: str, interface: str):
        if bridge not in self._state.get("bridges", {}):
            self._state.setdefault("bridges", {})[bridge] = {"members": []}
        members = self._state["bridges"][bridge].setdefault("members", [])
        if interface not in members:
            members.append(interface)
        self._save_state()

    def remove_bridge_member(self, conn, bridge: str, interface: str):
        if bridge in self._state.get("bridges", {}):
            members = self._state["bridges"][bridge].get("members", [])
            if interface in members:
                members.remove(interface)
        self._save_state()

    def set_vlan_members(self, conn, vlan_name: str, members: List[str]):
        """Set the list of physical ports that are members of a VLAN."""
        if vlan_name in self._state.get("vlans", {}):
            self._state["vlans"][vlan_name]["members"] = list(members)
        self._save_state()

    def get_firewall_rules(self, conn) -> List[Dict[str, Any]]:
        """Return the stored VLAN routing restrictions."""
        return copy.deepcopy(self._state.get("vlan_restrictions", []))

    def set_firewall_rules(self, conn, rules: List[Dict[str, Any]]):
        """Store VLAN routing restrictions in mock state."""
        self._state["vlan_restrictions"] = copy.deepcopy(rules)
        self._save_state()

    def get_vpn_status(self, conn) -> Dict[str, Any]:
        vpn_state = self._state.get("vpn", {})
        return {
            "enabled": vpn_state.get("enabled", False),
            "connected": vpn_state.get("connected", False),
            "remote": vpn_state.get("remote", ""),
            "port": vpn_state.get("port", ""),
            "proto": vpn_state.get("proto", ""),
            "interface": vpn_state.get("interface", ""),
        }

    def get_vpn_config(self, conn) -> Dict[str, str]:
        return copy.deepcopy(self._state.get("vpn_config", {}))

    def apply_vpn_config(self, conn, vpn_config: Dict[str, str]):
        self._state["vpn_config"] = copy.deepcopy(vpn_config)
        self._save_state()

    def start_vpn(self, conn):
        vpn_config = self._state.get("vpn_config", {})
        if not vpn_config:
            raise Exception('No VPN configuration applied. Apply a config before starting.')
        self._state.setdefault("vpn", {})["enabled"] = True
        self._state.setdefault("vpn", {})["connected"] = True
        self._state.setdefault("vpn", {})["remote"] = vpn_config.get("openvpncl_remoteip", "")
        self._state.setdefault("vpn", {})["port"] = vpn_config.get("openvpncl_remoteport", "")
        self._state.setdefault("vpn", {})["proto"] = vpn_config.get("openvpncl_proto", "")
        self._state.setdefault("vpn", {})["interface"] = "tun0"
        self._save_state()

    def stop_vpn(self, conn):
        self._state.setdefault("vpn", {})["enabled"] = False
        self._state.setdefault("vpn", {})["connected"] = False
        self._state.setdefault("vpn", {})["interface"] = ""
        self._save_state()

    def install_authorized_key(self, conn, pub_key: str):
        """No-op: the mock router has no SSH daemon to provision keys for."""
        pass
