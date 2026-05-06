import json
import os
import tempfile
import unittest
from routers.mock import MockRouter
from networkconfig import NetworkConfig, ConfigDiff


class TestMockRouterReadMethods(unittest.TestCase):
    def setUp(self):
        self.router = MockRouter()
        self.conn = None

    def test_get_dhcp_leases(self):
        leases = self.router.get_dhcp_leases(self.conn)
        self.assertIsInstance(leases, list)
        self.assertGreater(len(leases), 0)
        self.assertEqual(len(leases[0]), 4)

    def test_get_static_leases(self):
        leases = self.router.get_static_leases(self.conn)
        self.assertIsInstance(leases, list)
        self.assertGreater(len(leases), 0)

    def test_get_interfaces(self):
        ifaces = self.router.get_interfaces(self.conn)
        self.assertIn("eth0", ifaces)
        self.assertIn("br0", ifaces)

    def test_get_bridges(self):
        bridges = self.router.get_bridges(self.conn)
        self.assertIn("br0", bridges)
        self.assertIn("members", bridges["br0"])

    def test_get_vlans(self):
        vlans = self.router.get_vlans(self.conn)
        self.assertIn("vlan1", vlans)
        self.assertIn("vlan2", vlans)
        self.assertTrue(vlans["vlan1"]["bridged"])
        self.assertTrue(vlans["vlan2"]["nat"])

    def test_get_port_vlan_map(self):
        port_map = self.router.get_port_vlan_map(self.conn)
        self.assertIn("port0", port_map)
        self.assertIn(1, port_map["port0"])

    def test_get_bridge_dhcp_config(self):
        dhcp = self.router.get_bridge_dhcp_config(self.conn)
        self.assertGreater(len(dhcp), 0)
        bridge, start, size, lease = dhcp[0]
        self.assertEqual(bridge, "br0")

    def test_get_bridge_ip_info(self):
        ip_info = self.router.get_bridge_ip_info(self.conn, "br0")
        self.assertGreater(len(ip_info), 0)
        ip, netmask = ip_info[0]
        self.assertEqual(ip, "192.168.1.1")


class TestMockRouterWriteMethods(unittest.TestCase):
    def setUp(self):
        self.router = MockRouter()
        self.conn = None

    def test_set_vlan_ip(self):
        self.router.set_vlan_ip(self.conn, 1, "10.0.0.1", "255.255.255.0")
        vlans = self.router.get_vlans(self.conn)
        self.assertEqual(vlans["vlan1"]["ip"], "10.0.0.1")
        self.assertEqual(vlans["vlan1"]["netmask"], "255.255.255.0")

    def test_set_vlan_bridged(self):
        self.router.set_vlan_bridged(self.conn, 1, False)
        vlans = self.router.get_vlans(self.conn)
        self.assertFalse(vlans["vlan1"]["bridged"])

    def test_set_vlan_nat(self):
        self.router.set_vlan_nat(self.conn, 1, True)
        vlans = self.router.get_vlans(self.conn)
        self.assertTrue(vlans["vlan1"]["nat"])

    def test_set_vlan_dhcp(self):
        self.router.set_vlan_dhcp(self.conn, 3, 10, 50, 120)
        vlans = self.router.get_vlans(self.conn)
        self.assertIn("vlan3", vlans)
        dhcp = vlans["vlan3"]["dhcp"]
        self.assertEqual(dhcp["range_start"], 10)
        self.assertEqual(dhcp["range_size"], 50)
        self.assertEqual(dhcp["lease_time_min"], 120)

    def test_remove_vlan_dhcp(self):
        self.router.set_vlan_dhcp(self.conn, 3, 10, 50, 120)
        self.router.remove_vlan_dhcp(self.conn, 3)
        vlans = self.router.get_vlans(self.conn)
        self.assertNotIn("dhcp", vlans["vlan3"])

    def test_delete_vlan(self):
        self.router.delete_vlan(self.conn, 1)
        vlans = self.router.get_vlans(self.conn)
        self.assertNotIn("vlan1", vlans)
        port_map = self.router.get_port_vlan_map(self.conn)
        for port, vlans_list in port_map.items():
            self.assertNotIn(1, vlans_list)

    def test_set_port_vlan_map(self):
        new_map = {"port0": [1, 3], "port1": [1], "port2": [2]}
        self.router.set_port_vlan_map(self.conn, new_map)
        result = self.router.get_port_vlan_map(self.conn)
        self.assertEqual(result["port0"], [1, 3])
        self.assertEqual(result["port2"], [2])

    def test_set_bridge_dhcp(self):
        self.router.set_bridge_dhcp(self.conn, "br0", 50, 100, 720)
        dhcp = self.router.get_bridge_dhcp_config(self.conn)
        br0_entries = [d for d in dhcp if d[0] == "br0"]
        self.assertGreater(len(br0_entries), 0)
        self.assertEqual(br0_entries[0][1], "50")

    def test_set_bridge_ip(self):
        self.router.set_bridge_ip(self.conn, "br0", "10.0.0.1", "255.255.255.0")
        ip_info = self.router.get_bridge_ip_info(self.conn, "br0")
        self.assertEqual(ip_info[0][0], "10.0.0.1")

    def test_add_bridge_member(self):
        self.router.add_bridge_member(self.conn, "br0", "vlan3")
        bridges = self.router.get_bridges(self.conn)
        self.assertIn("vlan3", bridges["br0"]["members"])

    def test_remove_bridge_member(self):
        self.router.remove_bridge_member(self.conn, "br0", "eth1")
        bridges = self.router.get_bridges(self.conn)
        self.assertNotIn("eth1", bridges["br0"]["members"])

    def test_set_static_leases(self):
        new_leases = [["00:11:22:33:44:55", "testhost", "192.168.1.99"]]
        self.router.set_static_leases(self.conn, new_leases)
        result = self.router.get_static_leases(self.conn)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], "testhost")

    def test_state_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import routers.mock as mock_module
            original_dir = mock_module._MOCK_STATE_DIR
            mock_module._MOCK_STATE_DIR = os.path.join(tmpdir, "mock_state")
            try:
                router = MockRouter(name="test_persist")
                router.set_vlan_ip(self.conn, 1, "10.0.0.1", "255.255.255.0")
                del router
                router2 = MockRouter(name="test_persist")
                vlans = router2.get_vlans(self.conn)
                self.assertEqual(vlans["vlan1"]["ip"], "10.0.0.1")
            finally:
                mock_module._MOCK_STATE_DIR = original_dir


class TestNetworkConfigFromRouter(unittest.TestCase):
    def setUp(self):
        self.router = MockRouter()
        self.conn = None

    def test_from_router(self):
        config = NetworkConfig.from_router(self.conn, self.router)
        self.assertIn("vlan1", config.network["vlans"])
        self.assertIn("vlan2", config.network["vlans"])
        self.assertIn("br0", config.network["bridges"])
        self.assertIn("port0", config.network["ports"])

    def test_to_json_round_trip(self):
        config = NetworkConfig.from_router(self.conn, self.router)
        json_str = config.to_json()
        config2 = NetworkConfig.from_dict(json.loads(json_str))
        self.assertEqual(config.network["vlans"], config2.network["vlans"])
        self.assertEqual(config.network["bridges"], config2.network["bridges"])
        self.assertEqual(config.network["ports"], config2.network["ports"])

    def test_to_json_file_round_trip(self):
        config = NetworkConfig.from_router(self.conn, self.router)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            path = f.name
        try:
            config.to_json_file(path)
            config2 = NetworkConfig.from_json_file(path)
            self.assertEqual(config.network["vlans"], config2.network["vlans"])
        finally:
            os.unlink(path)


class TestNetworkConfigMutations(unittest.TestCase):
    def test_add_vlan(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(10, ip="10.0.0.1", netmask="255.255.255.0")
        self.assertIn("vlan10", config.network["vlans"])
        self.assertEqual(config.network["vlans"]["vlan10"]["ip"], "10.0.0.1")

    def test_add_vlan_duplicate_raises(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(10)
        with self.assertRaises(ValueError):
            config.add_vlan(10)

    def test_remove_vlan(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(10, ip="10.0.0.1", netmask="255.255.255.0")
        config.assign_port("port0", 10)
        config.remove_vlan(10)
        self.assertNotIn("vlan10", config.network["vlans"])
        self.assertNotIn(10, config.network["ports"]["port0"])

    def test_remove_nonexistent_vlan_raises(self):
        config = NetworkConfig.from_scratch()
        with self.assertRaises(ValueError):
            config.remove_vlan(99)

    def test_update_vlan(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(10, ip="10.0.0.1")
        config.update_vlan(10, ip="10.0.0.2", bridged=True, nat=True)
        self.assertEqual(config.network["vlans"]["vlan10"]["ip"], "10.0.0.2")
        self.assertTrue(config.network["vlans"]["vlan10"]["bridged"])
        self.assertTrue(config.network["vlans"]["vlan10"]["nat"])

    def test_assign_port(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(10)
        config.assign_port("port0", 10)
        self.assertIn(10, config.network["ports"]["port0"])

    def test_assign_port_nonexistent_vlan_raises(self):
        config = NetworkConfig.from_scratch()
        with self.assertRaises(ValueError):
            config.assign_port("port0", 99)

    def test_unassign_port(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(10)
        config.assign_port("port0", 10)
        config.unassign_port("port0", 10)
        self.assertNotIn(10, config.network["ports"]["port0"])

    def test_add_bridge_vlan(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(10)
        config.add_bridge_vlan("br0", "vlan10")
        self.assertIn("vlan10", config.network["bridges"]["br0"]["members"])

    def test_add_bridge_vlan_nonexistent_raises(self):
        config = NetworkConfig.from_scratch()
        with self.assertRaises(ValueError):
            config.add_bridge_vlan("br0", "vlan99")

    def test_remove_bridge_vlan(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(10)
        config.add_bridge_vlan("br0", "vlan10")
        config.remove_bridge_vlan("br0", "vlan10")
        self.assertNotIn("vlan10", config.network["bridges"]["br0"]["members"])

    def test_set_vlan_dhcp(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(10)
        config.set_vlan_dhcp(10, 10, 50, 120)
        dhcp = config.network["vlans"]["vlan10"]["dhcp"]
        self.assertEqual(dhcp["range_start"], 10)
        self.assertEqual(dhcp["range_size"], 50)

    def test_remove_vlan_dhcp(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(10)
        config.set_vlan_dhcp(10, 10, 50, 120)
        config.remove_vlan_dhcp(10)
        self.assertNotIn("dhcp", config.network["vlans"]["vlan10"])

    def test_set_bridge_dhcp(self):
        config = NetworkConfig.from_scratch()
        config.set_bridge_dhcp("br0", 50, 100, 720)
        dhcp = config.network["bridges"]["br0"]["dhcp"]
        self.assertEqual(dhcp["range_start"], 50)
        self.assertEqual(dhcp["range_size"], 100)

    def test_remove_bridge_dhcp(self):
        config = NetworkConfig.from_scratch()
        config.set_bridge_dhcp("br0", 50, 100, 720)
        config.remove_bridge_dhcp("br0")
        self.assertNotIn("dhcp", config.network["bridges"]["br0"])


class TestNetworkConfigValidation(unittest.TestCase):
    def test_valid_config(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1, ip="192.168.1.1", netmask="255.255.255.0")
        errors = config.validate()
        self.assertEqual(len(errors), 0)

    def test_overlapping_subnets(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1, ip="192.168.1.1", netmask="255.255.255.0")
        config.add_vlan(2, ip="192.168.1.2", netmask="255.255.255.0")
        errors = config.validate()
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("overlap" in e.lower() for e in errors))

    def test_duplicate_vlan_id(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1)
        config.network["vlans"]["vlan1_dup"] = config.network["vlans"]["vlan1"].copy()
        errors = config.validate()
        self.assertGreater(len(errors), 0)

    def test_port_references_nonexistent_vlan(self):
        config = NetworkConfig.from_scratch()
        config.network["ports"]["port0"] = [99]
        errors = config.validate()
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("non-existent" in e.lower() for e in errors))

    def test_bridge_references_nonexistent_member(self):
        config = NetworkConfig.from_scratch()
        config.network["bridges"]["br0"] = {"members": ["vlan99"]}
        errors = config.validate()
        self.assertGreater(len(errors), 0)


class TestNetworkConfigDiff(unittest.TestCase):
    def test_diff_empty(self):
        config1 = NetworkConfig.from_scratch()
        config2 = NetworkConfig.from_scratch()
        d = config1.diff(config2)
        self.assertTrue(d.is_empty())

    def test_diff_added_vlan(self):
        config1 = NetworkConfig.from_scratch()
        config2 = NetworkConfig.from_scratch()
        config2.add_vlan(10, ip="10.0.0.1")
        d = config1.diff(config2)
        self.assertFalse(d.is_empty())
        self.assertEqual(len(d.added_vlans), 1)
        self.assertEqual(d.added_vlans[0]["name"], "vlan10")

    def test_diff_removed_vlan(self):
        config1 = NetworkConfig.from_scratch()
        config1.add_vlan(10)
        config2 = NetworkConfig.from_scratch()
        d = config1.diff(config2)
        self.assertFalse(d.is_empty())
        self.assertEqual(len(d.removed_vlans), 1)
        self.assertEqual(d.removed_vlans[0], "vlan10")

    def test_diff_modified_vlan(self):
        config1 = NetworkConfig.from_scratch()
        config1.add_vlan(10, ip="10.0.0.1")
        config2 = NetworkConfig.from_scratch()
        config2.add_vlan(10, ip="10.0.0.2")
        d = config1.diff(config2)
        self.assertFalse(d.is_empty())
        self.assertEqual(len(d.modified_vlans), 1)
        self.assertIn("ip", d.modified_vlans[0]["changes"])

    def test_diff_port_changes(self):
        config1 = NetworkConfig.from_scratch()
        config1.add_vlan(10)
        config2 = NetworkConfig.from_scratch()
        config2.add_vlan(10)
        config2.assign_port("port0", 10)
        d = config1.diff(config2)
        self.assertIn("port0", d.added_ports)
        self.assertEqual(d.added_ports["port0"], [10])

    def test_diff_str_representation(self):
        config1 = NetworkConfig.from_scratch()
        config2 = NetworkConfig.from_scratch()
        config2.add_vlan(10)
        d = config1.diff(config2)
        s = str(d)
        self.assertIn("vlan10", s)


class TestNetworkConfigApplyDiff(unittest.TestCase):
    def setUp(self):
        self.router = MockRouter()
        self.conn = None

    def test_apply_diff_add_vlan(self):
        current = NetworkConfig.from_router(self.conn, self.router)
        desired = NetworkConfig.from_dict(json.loads(current.to_json()))
        desired.add_vlan(3, ip="192.168.3.1", netmask="255.255.255.0")
        desired.apply_to_router(self.conn, self.router, mode="diff")
        vlans = self.router.get_vlans(self.conn)
        self.assertIn("vlan3", vlans)
        self.assertEqual(vlans["vlan3"]["ip"], "192.168.3.1")

    def test_apply_full_rewrite(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(3, ip="192.168.3.1", netmask="255.255.255.0", nat=True)
        config.network["ports"] = {"port0": [3], "port1": [3]}
        config.apply_to_router(self.conn, self.router, mode="full")
        vlans = self.router.get_vlans(self.conn)
        self.assertIn("vlan3", vlans)
        port_map = self.router.get_port_vlan_map(self.conn)
        self.assertIn(3, port_map["port0"])

    def test_apply_and_verify(self):
        current = NetworkConfig.from_router(self.conn, self.router)
        desired = NetworkConfig.from_dict(json.loads(current.to_json()))
        desired.add_vlan(3, ip="192.168.3.1", netmask="255.255.255.0")
        desired.apply_to_router(self.conn, self.router, mode="diff")
        issues = desired.verify(self.conn, self.router)
        self.assertEqual(len(issues), 0)

    def test_verify_detects_difference(self):
        current = NetworkConfig.from_router(self.conn, self.router)
        desired = NetworkConfig.from_dict(json.loads(current.to_json()))
        desired.add_vlan(99, ip="10.99.0.1")
        issues = desired.verify(self.conn, self.router)
        self.assertGreater(len(issues), 0)


class TestConfigDiff(unittest.TestCase):
    def test_is_empty_true(self):
        d = ConfigDiff()
        self.assertTrue(d.is_empty())

    def test_is_empty_false_after_addition(self):
        d = ConfigDiff()
        d.added_vlans.append({"name": "vlan10"})
        self.assertFalse(d.is_empty())


class TestNetworkConfigRestrictions(unittest.TestCase):
    def test_add_restriction(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1)
        config.add_vlan(2)
        config.add_restriction(1, 2, description="block guest to LAN")
        restrictions = config.network["vlan_restrictions"]
        self.assertEqual(len(restrictions), 1)
        self.assertEqual(restrictions[0]["from"], 1)
        self.assertEqual(restrictions[0]["to"], 2)
        self.assertEqual(restrictions[0]["description"], "block guest to LAN")

    def test_add_restriction_bidirectional(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1)
        config.add_vlan(2)
        config.add_restriction(1, 2, bidirectional=True)
        restrictions = config.network["vlan_restrictions"]
        self.assertEqual(len(restrictions), 2)
        directions = {(r["from"], r["to"]) for r in restrictions}
        self.assertIn((1, 2), directions)
        self.assertIn((2, 1), directions)

    def test_add_restriction_self_raises(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1)
        with self.assertRaises(ValueError):
            config.add_restriction(1, 1)

    def test_add_restriction_duplicate_raises(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1)
        config.add_vlan(2)
        config.add_restriction(1, 2)
        with self.assertRaises(ValueError):
            config.add_restriction(1, 2)

    def test_remove_restriction(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1)
        config.add_vlan(2)
        config.add_restriction(1, 2)
        config.remove_restriction(1, 2)
        self.assertEqual(len(config.network["vlan_restrictions"]), 0)

    def test_remove_restriction_bidirectional(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1)
        config.add_vlan(2)
        config.add_restriction(1, 2, bidirectional=True)
        self.assertEqual(len(config.network["vlan_restrictions"]), 2)
        config.remove_restriction(1, 2, bidirectional=True)
        self.assertEqual(len(config.network["vlan_restrictions"]), 0)

    def test_validate_restriction_nonexistent_vlan(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1)
        config.network["vlan_restrictions"] = [{"from": 1, "to": 99}]
        errors = config.validate()
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("non-existent" in e for e in errors))

    def test_validate_restriction_self(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1)
        config.network["vlan_restrictions"] = [{"from": 1, "to": 1}]
        errors = config.validate()
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("itself" in e for e in errors))

    def test_validate_restriction_same_bridge_warning(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1)
        config.add_vlan(2)
        config.network["bridges"]["br0"] = {"members": ["vlan1", "vlan2"]}
        config.add_restriction(1, 2)
        errors = config.validate()
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("same-bridge" in e for e in errors))

    def test_validate_restriction_different_bridges_ok(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1, bridged=True)
        config.add_vlan(2, bridged=True)
        config.add_restriction(1, 2)
        errors = config.validate()
        bridge_errors = [e for e in errors if "bridge" in e.lower() and "same-bridge" in e]
        self.assertEqual(len(bridge_errors), 0)

    def test_validate_restriction_duplicate(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1)
        config.add_vlan(2)
        config.network["vlan_restrictions"] = [
            {"from": 1, "to": 2},
            {"from": 1, "to": 2},
        ]
        errors = config.validate()
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("Duplicate" in e for e in errors))

    def test_diff_restrictions(self):
        config1 = NetworkConfig.from_scratch()
        config2 = NetworkConfig.from_scratch()
        config2.add_vlan(1)
        config2.add_vlan(2)
        config2.add_restriction(1, 2, description="test")
        d = config1.diff(config2)
        self.assertEqual(len(d.added_restrictions), 1)
        self.assertEqual(d.added_restrictions[0]["from"], 1)

    def test_diff_restriction_removed(self):
        config1 = NetworkConfig.from_scratch()
        config1.add_vlan(1)
        config1.add_vlan(2)
        config1.add_restriction(1, 2)
        config2 = NetworkConfig.from_scratch()
        config2.add_vlan(1)
        config2.add_vlan(2)
        d = config1.diff(config2)
        self.assertEqual(len(d.removed_restrictions), 1)

    def test_expand_restrictions(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1)
        config.add_vlan(2)
        config.add_restriction(1, 2, description="test", bidirectional=True)
        rules = config._expand_restrictions()
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0]["from_iface"], "vlan1")
        self.assertEqual(rules[0]["to_iface"], "vlan2")
        self.assertEqual(rules[1]["from_iface"], "vlan2")
        self.assertEqual(rules[1]["to_iface"], "vlan1")

    def test_add_vlan_bridged_creates_bridge(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(10, ip="10.0.0.1", netmask="255.255.255.0", bridged=True)
        self.assertIn("br10", config.network["bridges"])
        self.assertIn("vlan10", config.network["bridges"]["br10"]["members"])

    def test_restriction_round_trip_json(self):
        config = NetworkConfig.from_scratch()
        config.add_vlan(1)
        config.add_vlan(2)
        config.add_restriction(1, 2, description="block")
        json_str = config.to_json()
        config2 = NetworkConfig.from_dict(json.loads(json_str))
        self.assertEqual(len(config2.network["vlan_restrictions"]), 1)
        self.assertEqual(config2.network["vlan_restrictions"][0]["description"], "block")


class TestMockRouterFirewall(unittest.TestCase):
    def setUp(self):
        self.router = MockRouter()
        self.conn = None

    def test_get_firewall_rules_default(self):
        rules = self.router.get_firewall_rules(self.conn)
        self.assertIsInstance(rules, list)

    def test_set_firewall_rules(self):
        rules = [
            {"from_iface": "vlan3", "to_iface": "vlan1", "description": "guest to LAN"},
        ]
        self.router.set_firewall_rules(self.conn, rules)
        result = self.router.get_firewall_rules(self.conn)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["from_iface"], "vlan3")

    def test_set_firewall_rules_overwrite(self):
        self.router.set_firewall_rules(self.conn, [{"from_iface": "vlan1", "to_iface": "vlan2"}])
        self.router.set_firewall_rules(self.conn, [{"from_iface": "vlan3", "to_iface": "vlan1"}])
        result = self.router.get_firewall_rules(self.conn)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["from_iface"], "vlan3")


class TestNetworkConfigFromRouterRestrictions(unittest.TestCase):
    def setUp(self):
        self.router = MockRouter()
        self.conn = None

    def test_from_router_discovers_restrictions(self):
        self.router.set_firewall_rules(self.conn, [
            {"from_iface": "vlan3", "to_iface": "vlan1", "from": 3, "to": 1, "description": "guest to LAN"},
            {"from_iface": "vlan1", "to_iface": "vlan3", "from": 1, "to": 3, "description": "LAN to guest"},
        ])
        config = NetworkConfig.from_router(self.conn, self.router)
        restrictions = config.network.get("vlan_restrictions", [])
        self.assertEqual(len(restrictions), 2)
        self.assertEqual(restrictions[0]["from"], 3)
        self.assertEqual(restrictions[0]["to"], 1)

    def test_from_router_empty_restrictions(self):
        config = NetworkConfig.from_router(self.conn, self.router)
        restrictions = config.network.get("vlan_restrictions", [])
        self.assertEqual(len(restrictions), 0)


if __name__ == '__main__':
    unittest.main()
