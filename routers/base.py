from abc import ABC, abstractmethod
from typing import List, Dict, Any


class RouterBase(ABC):
    """Abstract base class for router handlers. Defines the read/write interface for querying
    and modifying router configuration via SSH (DD-WRT) or in-memory state (MockRouter)."""

    @abstractmethod
    def get_dhcp_leases(self, conn) -> List[List[str]]:
        pass

    @abstractmethod
    def remove_dhcp_leases(self, conn, mac_addresses: List[str]):
        """Remove DHCP lease entries matching the given MAC addresses and restart dnsmasq."""
        pass

    @abstractmethod
    def get_static_leases(self, conn) -> List[List[str]]:
        pass

    @abstractmethod
    def set_static_leases(self, conn, leases: List[List[str]]):
        pass

    @abstractmethod
    def restart_dhcp_service(self, conn):
        pass

    @abstractmethod
    def commit_config(self, conn):
        pass

    @abstractmethod
    def get_interfaces(self, conn) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_bridges(self, conn) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_vlans(self, conn) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_port_vlan_map(self, conn) -> Dict[str, List[int]]:
        pass

    @abstractmethod
    def get_bridge_dhcp_config(self, conn) -> List[tuple]:
        pass

    @abstractmethod
    def get_bridge_ip_info(self, conn, bridge: str) -> List[tuple]:
        pass

    @abstractmethod
    def set_vlan_ip(self, conn, vlan_id: int, ip: str, netmask: str):
        """Set the IP address and netmask on a VLAN interface."""
        pass

    @abstractmethod
    def set_vlan_bridged(self, conn, vlan_id: int, bridged: bool):
        """Set whether a VLAN is bridged."""
        pass

    @abstractmethod
    def set_vlan_nat(self, conn, vlan_id: int, nat: bool):
        """Enable or disable NAT on a VLAN."""
        pass

    @abstractmethod
    def set_vlan_dhcp(self, conn, vlan_id: int, start: int, size: int, lease: int):
        """Configure DHCP for a VLAN with the given range start, pool size, and lease time (minutes)."""
        pass

    @abstractmethod
    def remove_vlan_dhcp(self, conn, vlan_id: int):
        """Remove DHCP configuration from a VLAN."""
        pass

    @abstractmethod
    def delete_vlan(self, conn, vlan_id: int):
        """Fully remove a VLAN and all its associated configuration."""
        pass

    @abstractmethod
    def set_port_vlan_map(self, conn, port_map: Dict[str, List[int]]):
        """Write the complete port-to-VLAN membership mapping."""
        pass

    @abstractmethod
    def set_bridge_dhcp(self, conn, bridge: str, start: int, size: int, lease: int):
        """Configure DHCP on a bridge with the given range start, pool size, and lease time (minutes)."""
        pass

    @abstractmethod
    def set_bridge_ip(self, conn, bridge: str, ip: str, netmask: str):
        """Set the IP address and netmask on a bridge interface."""
        pass

    @abstractmethod
    def add_bridge_member(self, conn, bridge: str, interface: str):
        """Add an interface as a member of a bridge."""
        pass

    @abstractmethod
    def remove_bridge_member(self, conn, bridge: str, interface: str):
        """Remove an interface from a bridge."""
        pass

    @abstractmethod
    def set_vlan_members(self, conn, vlan_name: str, members: List[str]):
        """Set the list of physical ports that are members of a VLAN."""
        pass

    @abstractmethod
    def get_firewall_rules(self, conn) -> List[Dict[str, Any]]:
        """Return the current VLAN routing restrictions as a list of dicts with from_iface/to_iface/description."""
        pass

    @abstractmethod
    def set_firewall_rules(self, conn, rules: List[Dict[str, Any]]):
        """Apply VLAN routing restriction rules to the router's firewall configuration."""
        pass

    @abstractmethod
    def get_vpn_status(self, conn) -> Dict[str, Any]:
        """Return VPN status info: whether connected, interface, remote server, etc."""
        pass

    @abstractmethod
    def get_vpn_config(self, conn) -> Dict[str, str]:
        """Return the current VPN client configuration from the router as a dict of nvram-like keys."""
        pass

    @abstractmethod
    def apply_vpn_config(self, conn, vpn_config: Dict[str, str]):
        """Apply a VPN client configuration to the router."""
        pass

    @abstractmethod
    def start_vpn(self, conn):
        """Start the OpenVPN client on the router."""
        pass

    @abstractmethod
    def stop_vpn(self, conn):
        """Stop the OpenVPN client on the router."""
        pass

    @abstractmethod
    def install_authorized_key(self, conn, pub_key: str):
        """Install a public SSH key on the router so key-based auth works.

        Each adapter owns its router-specific persistence strategy
        (DD-WRT NVRAM, OpenWrt /etc/dropbear, etc.). The common
        ``~/.ssh/authorized_keys`` step is provided by
        ``_install_in_home_ssh`` and should typically be called from
        the adapter's implementation.
        """
        pass

    def _install_in_home_ssh(self, conn, pub_key: str):
        """Append ``pub_key`` to ``~/.ssh/authorized_keys`` on the router.

        Concrete helper shared by all SSH-backed adapters. Resolves the
        home directory via ``$HOME`` (falling back to ``/tmp/root`` for
        embedded routers where root has no real home) and appends the
        key with permissions tightened to 600.
        """
        home_dir = conn.run('echo $HOME', hide=True).stdout.strip() or '/tmp/root'
        conn.run(
            f'mkdir -p {home_dir}/.ssh '
            f'&& echo "{pub_key}" >> {home_dir}/.ssh/authorized_keys '
            f'&& chmod 600 {home_dir}/.ssh/authorized_keys',
            hide=True,
        )
