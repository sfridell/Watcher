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
