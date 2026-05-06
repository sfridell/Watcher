from abc import ABC, abstractmethod
from typing import List, Dict, Any


class RouterBase(ABC):
    @abstractmethod
    def get_dhcp_leases(self, conn) -> List[List[str]]:
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
        pass

    @abstractmethod
    def set_vlan_bridged(self, conn, vlan_id: int, bridged: bool):
        pass

    @abstractmethod
    def set_vlan_nat(self, conn, vlan_id: int, nat: bool):
        pass

    @abstractmethod
    def set_vlan_dhcp(self, conn, vlan_id: int, start: int, size: int, lease: int):
        pass

    @abstractmethod
    def remove_vlan_dhcp(self, conn, vlan_id: int):
        pass

    @abstractmethod
    def delete_vlan(self, conn, vlan_id: int):
        pass

    @abstractmethod
    def set_port_vlan_map(self, conn, port_map: Dict[str, List[int]]):
        pass

    @abstractmethod
    def set_bridge_dhcp(self, conn, bridge: str, start: int, size: int, lease: int):
        pass

    @abstractmethod
    def set_bridge_ip(self, conn, bridge: str, ip: str, netmask: str):
        pass

    @abstractmethod
    def add_bridge_member(self, conn, bridge: str, interface: str):
        pass

    @abstractmethod
    def remove_bridge_member(self, conn, bridge: str, interface: str):
        pass

    @abstractmethod
    def set_vlan_members(self, conn, vlan_name: str, members: List[str]):
        pass
