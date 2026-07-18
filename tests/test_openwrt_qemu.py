import pytest


pytestmark = pytest.mark.timeout(300)


class TestOpenWRTRouterRead:
    """Test read operations against a live OpenWrt QEMU VM."""

    def test_get_interfaces(self, openwrt_vm):
        conn, handler = openwrt_vm
        result = handler.get_interfaces(conn)
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_get_bridges(self, openwrt_vm):
        conn, handler = openwrt_vm
        result = handler.get_bridges(conn)
        assert isinstance(result, dict)
        assert "br-lan" in result

    def test_get_vlans(self, openwrt_vm):
        conn, handler = openwrt_vm
        result = handler.get_vlans(conn)
        assert isinstance(result, dict)

    def test_get_dhcp_leases(self, openwrt_vm):
        conn, handler = openwrt_vm
        result = handler.get_dhcp_leases(conn)
        assert isinstance(result, list)

    def test_get_static_leases(self, openwrt_vm):
        conn, handler = openwrt_vm
        result = handler.get_static_leases(conn)
        assert isinstance(result, list)

    def test_get_port_vlan_map(self, openwrt_vm):
        conn, handler = openwrt_vm
        result = handler.get_port_vlan_map(conn)
        assert isinstance(result, dict)

    def test_get_bridge_dhcp_config(self, openwrt_vm):
        conn, handler = openwrt_vm
        result = handler.get_bridge_dhcp_config(conn)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_get_bridge_ip_info(self, openwrt_vm):
        conn, handler = openwrt_vm
        result = handler.get_bridge_ip_info(conn, "br-lan")
        assert isinstance(result, list)
        assert len(result) > 0
        ips = {row[0]: row[1] for row in result}
        assert "192.168.1.1" in ips

    def test_get_firewall_rules(self, openwrt_vm):
        conn, handler = openwrt_vm
        result = handler.get_firewall_rules(conn)
        assert isinstance(result, list)

    def test_get_vpn_status(self, openwrt_vm):
        conn, handler = openwrt_vm
        result = handler.get_vpn_status(conn)
        assert isinstance(result, dict)
        assert "enabled" in result
        assert "connected" in result

    def test_get_vpn_config(self, openwrt_vm):
        conn, handler = openwrt_vm
        result = handler.get_vpn_config(conn)
        assert isinstance(result, dict)


class TestOpenWRTRouterWriteReadback:
    """Test write + readback operations against a live OpenWrt QEMU VM.

    Readback uses UCI-backed getters (get_vlans, get_bridge_dhcp_config,
    get_static_leases, get_firewall_rules) rather than live ``ip addr`` state,
    because OpenWrt does not apply UCI changes until an explicit service
    reload (which commit_config intentionally does not trigger, mirroring the
    DD-WRT ``nvram commit`` semantics).
    """

    def test_set_vlan_ip_readback(self, openwrt_vm):
        conn, handler = openwrt_vm
        handler.set_vlan_ip(conn, 1, "192.168.1.1", "255.255.255.0")
        handler.commit_config(conn)
        vlans = handler.get_vlans(conn)
        assert "vlan1" in vlans
        assert vlans["vlan1"].get("ip") == "192.168.1.1"
        assert vlans["vlan1"].get("netmask") == "255.255.255.0"

    def test_set_static_leases_readback(self, openwrt_vm):
        conn, handler = openwrt_vm
        leases = [["aa:bb:cc:dd:ee:ff", "testhost", "192.168.1.100"]]
        handler.set_static_leases(conn, leases)
        handler.commit_config(conn)
        result = handler.get_static_leases(conn)
        found = any(lease[0] == "aa:bb:cc:dd:ee:ff" for lease in result)
        assert found

    def test_set_bridge_ip_readback(self, openwrt_vm):
        conn, handler = openwrt_vm
        handler.set_bridge_ip(conn, "br-lan", "192.168.1.1", "255.255.255.0")
        handler.commit_config(conn)
        info = handler.get_bridge_ip_info(conn, "br-lan")
        assert len(info) > 0

    def test_set_bridge_dhcp_readback(self, openwrt_vm):
        conn, handler = openwrt_vm
        handler.set_bridge_dhcp(conn, "br-lan", 100, 50, 1440)
        handler.commit_config(conn)
        result = handler.get_bridge_dhcp_config(conn)
        assert isinstance(result, list)
        assert len(result) >= 1
        entry = next((r for r in result if r[0] == "br-lan"), None)
        assert entry is not None
        assert entry[1] == 100
        assert entry[2] == 50
        assert entry[3] == 1440

    def test_set_firewall_rules_readback(self, openwrt_vm):
        conn, handler = openwrt_vm
        rules = [{"from_iface": "eth0", "to_iface": "br-lan",
                  "description": "block eth0 to br-lan"}]
        handler.set_firewall_rules(conn, rules)
        result = handler.get_firewall_rules(conn)
        assert len(result) >= 1
        assert any(r["from_iface"] == "eth0" and r["to_iface"] == "br-lan"
                   for r in result)

    def test_remove_dhcp_leases(self, openwrt_vm):
        conn, handler = openwrt_vm
        handler.remove_dhcp_leases(conn, [])
        leases = handler.get_dhcp_leases(conn)
        assert isinstance(leases, list)

    def test_delete_vlan_readback(self, openwrt_vm):
        conn, handler = openwrt_vm
        handler.set_vlan_ip(conn, 3, "10.0.3.1", "255.255.255.0")
        handler.set_vlan_nat(conn, 3, True)
        handler.commit_config(conn)
        handler.delete_vlan(conn, 3)
        handler.commit_config(conn)
        vlans = handler.get_vlans(conn)
        assert "vlan3" not in vlans