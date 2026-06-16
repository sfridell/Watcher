import pytest


pytestmark = pytest.mark.timeout(300)


class TestDDWRTRouterRead:
    """Test read operations against a live DD-WRT QEMU VM."""

    def test_get_interfaces(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        result = handler.get_interfaces(conn)
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_get_bridges(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        result = handler.get_bridges(conn)
        assert isinstance(result, dict)

    def test_get_vlans(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        result = handler.get_vlans(conn)
        assert isinstance(result, dict)

    def test_get_dhcp_leases(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        result = handler.get_dhcp_leases(conn)
        assert isinstance(result, list)

    def test_get_static_leases(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        result = handler.get_static_leases(conn)
        assert isinstance(result, list)

    def test_get_port_vlan_map(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        result = handler.get_port_vlan_map(conn)
        assert isinstance(result, dict)

    def test_get_bridge_dhcp_config(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        result = handler.get_bridge_dhcp_config(conn)
        assert isinstance(result, list)

    def test_get_bridge_ip_info(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        result = handler.get_bridge_ip_info(conn, "br0")
        assert isinstance(result, list)

    def test_get_firewall_rules(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        result = handler.get_firewall_rules(conn)
        assert isinstance(result, list)

    def test_get_vpn_status(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        result = handler.get_vpn_status(conn)
        assert isinstance(result, dict)

    def test_get_vpn_config(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        result = handler.get_vpn_config(conn)
        assert isinstance(result, dict)


class TestDDWRTRouterWriteReadback:
    """Test write + readback operations against a live DD-WRT QEMU VM."""

    def test_set_vlan_ip_readback(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        handler.set_vlan_ip(conn, 1, "192.168.1.1", "255.255.255.0")
        handler.commit_config(conn)
        info = handler.get_bridge_ip_info(conn, "br0")
        ips = {row[0]: row[1] for row in info}
        assert "192.168.1.1" in ips

    def test_set_static_leases_readback(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        leases = [["aa:bb:cc:dd:ee:ff", "testhost", "192.168.1.100"]]
        handler.set_static_leases(conn, leases)
        handler.commit_config(conn)
        result = handler.get_static_leases(conn)
        found = any(lease[0] == "aa:bb:cc:dd:ee:ff" for lease in result)
        assert found

    def test_set_bridge_ip_readback(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        handler.set_bridge_ip(conn, "br0", "192.168.1.1", "255.255.255.0")
        handler.commit_config(conn)
        info = handler.get_bridge_ip_info(conn, "br0")
        assert len(info) > 0

    def test_set_bridge_dhcp_readback(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        handler.set_bridge_dhcp(conn, "br0", 100, 50, 1440)
        handler.commit_config(conn)
        result = handler.get_bridge_dhcp_config(conn)
        assert isinstance(result, list)

    def test_set_firewall_rules_readback(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        rules = [{"from_iface": "vlan2", "to_iface": "br0", "description": "block vlan2 to br0"}]
        handler.set_firewall_rules(conn, rules)
        handler.commit_config(conn)
        result = handler.get_firewall_rules(conn)
        assert len(result) >= 1

    def test_remove_dhcp_leases(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        handler.remove_dhcp_leases(conn, [])
        leases = handler.get_dhcp_leases(conn)
        assert isinstance(leases, list)

    def test_delete_vlan_readback(self, ddwrt_vm):
        conn, handler = ddwrt_vm
        handler.set_vlan_ip(conn, 3, "10.0.3.1", "255.255.255.0")
        handler.set_vlan_nat(conn, 3, True)
        handler.commit_config(conn)
        handler.delete_vlan(conn, 3)
        handler.commit_config(conn)
        vlans = handler.get_vlans(conn)
        assert "vlan3" not in vlans