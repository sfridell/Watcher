"""
Kivy mobile UI for Watcher - Network Router Monitor
"""
import json
import math
import logging
import io
from kivymd.app import MDApp

from kivy.uix.screenmanager import ScreenManager, Screen
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDRaisedButton, MDFlatButton, MDIconButton
from kivymd.uix.tooltip import MDTooltip


class TooltipMDIconButton(MDIconButton, MDTooltip):
    pass
from kivymd.uix.label import MDLabel
from kivymd.uix.dialog import MDDialog
from kivymd.uix.selectioncontrol import MDCheckbox
from kivymd.uix.button import MDRectangleFlatButton  # noqa: F401 - used in KV
from kivymd.uix.textfield import MDTextField  # noqa: F401 - used in KV
from kivymd.uix.toolbar import MDTopAppBar  # noqa: F401 - used in KV
from kivy.uix.spinner import Spinner  # noqa: F401 - used in KV
from kivy.uix.scrollview import ScrollView  # noqa: F401 - used in KV
from kivy.uix.floatlayout import FloatLayout  # noqa: F401 - used in KV
from kivy.uix.popup import Popup  # noqa: F401 - used in KV
from kivy.uix.filechooser import FileChooserListView  # noqa: F401 - used in KV
from kivy.metrics import dp
from kivy.properties import StringProperty, NumericProperty
from kivy.graphics import Color, Line, Ellipse, Triangle, InstructionGroup
from kivy.clock import Clock
import watcher
import connectiondb
from networkconfig import NetworkConfig
from vpnconfig import parse_ovpn_file, get_ddwrt_nvram_from_config, config_summary

logging.getLogger('paramiko').setLevel(logging.WARNING)
logging.getLogger('invoke').setLevel(logging.WARNING)
logging.getLogger('fabric').setLevel(logging.WARNING)



DOUBLE_CLICK_TIMEOUT = 0.3
LONG_PRESS_TIMEOUT = 0.5
LINE_HIT_THRESHOLD = dp(25)
CIRCLE_RADIUS = dp(35)


def polygon_positions(n, cx, cy, radius):
    if n == 0:
        return []
    if n == 1:
        return [(cx, cy)]
    if n == 2:
        return [(cx - radius, cy), (cx + radius, cy)]
    return [(cx + radius * math.cos(2 * math.pi * i / n - math.pi / 2),
             cy + radius * math.sin(2 * math.pi * i / n - math.pi / 2))
            for i in range(n)]


def connections_from_restrictions(vlans, restrictions):
    connections = {}
    vlan_ids = sorted(int(name.replace('vlan', '')) for name in vlans.keys())

    restricted = set()
    restriction_is_bidi = {}
    for r in restrictions:
        pair = (r['from'], r['to'])
        restricted.add(pair)
        restriction_is_bidi[pair] = r.get('bidirectional', False)

    for i in range(len(vlan_ids)):
        for j in range(i + 1, len(vlan_ids)):
            a, b = vlan_ids[i], vlan_ids[j]
            a_b_restricted = (a, b) in restricted
            b_a_restricted = (b, a) in restricted
            a_b_bidi = restriction_is_bidi.get((a, b), False)
            b_a_bidi = restriction_is_bidi.get((b, a), False)

            if a_b_restricted and b_a_restricted and a_b_bidi and b_a_bidi:
                continue
            elif a_b_restricted and b_a_restricted:
                continue
            elif a_b_restricted and not b_a_restricted:
                connections[(a, b)] = "backward"
            elif b_a_restricted and not a_b_restricted:
                connections[(a, b)] = "forward"
            else:
                connections[(a, b)] = "bidirectional"

    return connections


def sync_connections_to_config(config, connections):
    config.network["vlan_restrictions"] = []

    vlan_ids = sorted(
        int(name.replace('vlan', ''))
        for name in config.network.get("vlans", {}).keys()
    )

    for i in range(len(vlan_ids)):
        for j in range(i + 1, len(vlan_ids)):
            a, b = vlan_ids[i], vlan_ids[j]
            key = (a, b)
            if key in connections:
                state = connections[key]
                if state == "forward":
                    config.network["vlan_restrictions"].append({
                        "from": b, "to": a, "bidirectional": False
                    })
                elif state == "backward":
                    config.network["vlan_restrictions"].append({
                        "from": a, "to": b, "bidirectional": False
                    })
            else:
                config.network["vlan_restrictions"].append({
                    "from": a, "to": b, "bidirectional": True
                })


def point_to_segment_distance(px, py, x1, y1, x2, y2):
    dx = x2 - x1
    dy = y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.sqrt((px - x1) ** 2 + (py - y1) ** 2)
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / length_sq))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.sqrt((px - proj_x) ** 2 + (py - proj_y) ** 2)


class VlanCircle(MDLabel):
    vlan_id = NumericProperty(0)

    def __init__(self, vlan_id, vlan_data, **kwargs):
        super().__init__(**kwargs)
        self.theme_text_color = 'Custom'
        self.vlan_id = vlan_id
        self.vlan_data = vlan_data
        self.size_hint = (None, None)
        self.size = (CIRCLE_RADIUS * 2, CIRCLE_RADIUS * 2)
        self.halign = 'center'
        self.valign = 'center'
        ip = vlan_data.get("ip", "0.0.0.0")
        self.text = f'vlan{vlan_id}\n{ip}'
        self.bold = True
        self.font_size = '10sp'
        self.color = (1, 1, 1, 1)

        with self.canvas.before:
            self._circle_color = Color(0.2, 0.6, 0.9, 1)
            self._circle_ellipse = Ellipse(pos=self.pos, size=self.size)

    def on_pos(self, *args):
        if hasattr(self, '_circle_ellipse'):
            self._circle_ellipse.pos = self.pos

    def on_size(self, *args):
        if hasattr(self, '_circle_ellipse'):
            self._circle_ellipse.size = self.size

    def collide_point(self, x, y):
        cx, cy = self.center
        return math.sqrt((x - cx) ** 2 + (y - cy) ** 2) <= CIRCLE_RADIUS


class ConnectionListScreen(Screen):
    def on_enter(self):
        self.load_connections()

    def load_connections(self):
        try:
            self.ids.button_layout.clear_widgets()
            db = connectiondb.ConnectionDB()
            connections = list(db.connections.keys())
            for conn_name in connections:
                row = MDBoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
                row.add_widget(MDLabel(text=conn_name, font_style='H6'))
                status_btn = TooltipMDIconButton(icon='magnify', tooltip_text='Status')
                status_btn.bind(on_press=lambda instance, name=conn_name: self.show_status(name))
                config_btn = TooltipMDIconButton(icon='cog', tooltip_text='Config')
                config_btn.bind(on_press=lambda instance, name=conn_name: self.show_configure(name))
                row.add_widget(status_btn)
                row.add_widget(config_btn)
                self.ids.button_layout.add_widget(row)
        except Exception as e:
            self.show_error(f"Failed to load connections: {str(e)}")

    def show_status(self, conn_name):
        self.manager.get_screen('status').set_connection(conn_name)
        self.manager.current = 'status'

    def show_configure(self, conn_name):
        self.manager.get_screen('configure').set_connection(conn_name)
        self.manager.current = 'configure'

    def new_connection(self):
        self.manager.current = 'new_connection'

    def show_error(self, message):
        ok_button = MDFlatButton(text='OK')
        dialog = MDDialog(
            title='Error',
            text=message,
            buttons=[ok_button]
        )
        ok_button.bind(on_press=lambda x: dialog.dismiss())
        dialog.open()


class NewConnectionScreen(Screen):
    def on_router_type_change(self, spinner, text):
        is_mock = (text == 'mock')
        self.ids.conn_ip.readonly = is_mock
        self.ids.conn_port.readonly = is_mock
        self.ids.conn_username.readonly = is_mock
        if is_mock:
            self.ids.conn_ip.text = 'mock'
            self.ids.conn_port.text = '0'
            self.ids.conn_username.text = 'mock'
        else:
            self.ids.conn_ip.text = ''
            self.ids.conn_port.text = ''
            self.ids.conn_username.text = ''

    def save_connection(self):
        try:
            name = self.ids.conn_name.text.strip()
            if not name:
                self.show_error("Connection name is required")
                return

            router_type = self.ids.router_type.text

            class Args:
                pass

            args = Args()
            args.name = name
            args.router_type = router_type

            if router_type == 'mock':
                args.ip = 'mock'
                args.port = '0'
                args.username = 'mock'
            else:
                args.ip = self.ids.conn_ip.text.strip()
                args.port = int(self.ids.conn_port.text or '22')
                args.username = self.ids.conn_username.text.strip()

                if not args.ip:
                    self.show_error("IP Address is required")
                    return
                if not args.username:
                    self.show_error("Username is required")
                    return

            db = connectiondb.ConnectionDB()
            output = io.StringIO()
            db.new_connection(args, output)
            output_text = output.getvalue()
            if output_text:
                self.show_error(output_text)
                return

            self.manager.current = 'connections'
        except Exception as e:
            self.show_error(f"Failed to create connection: {str(e)}")

    def go_back(self):
        self.manager.current = 'connections'

    def show_error(self, message):
        ok_button = MDFlatButton(text='OK')
        dialog = MDDialog(
            title='Error',
            text=message,
            buttons=[ok_button]
        )
        ok_button.bind(on_press=lambda x: dialog.dismiss())
        dialog.open()


class StatusScreen(Screen):
    connection_name = StringProperty('')

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.toolbar.title = f'Status - {conn_name}'

    def show_clients(self):
        self.manager.get_screen('clients').set_connection(self.connection_name)
        self.manager.current = 'clients'

    def show_leases(self):
        self.manager.get_screen('leases').set_connection(self.connection_name)
        self.manager.current = 'leases'

    def show_vlans(self):
        self.manager.get_screen('vlan_list').set_connection(self.connection_name)
        self.manager.current = 'vlan_list'

    def show_config(self):
        self.manager.get_screen('config').set_connection(self.connection_name)
        self.manager.current = 'config'

    def show_vpn_status(self):
        self.manager.get_screen('vpn_status').set_connection(self.connection_name)
        self.manager.current = 'vpn_status'

    def go_back(self):
        self.manager.current = 'connections'


class ConfigureScreen(Screen):
    connection_name = StringProperty('')

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.toolbar.title = f'Configure - {conn_name}'

    def show_static_leases(self):
        self.manager.get_screen('configure_static_leases').set_connection(self.connection_name)
        self.manager.current = 'configure_static_leases'

    def show_dhcp_revoke(self):
        self.manager.get_screen('configure_dhcp').set_connection(self.connection_name)
        self.manager.current = 'configure_dhcp'

    def show_vlan_config(self):
        self.manager.get_screen('configure_vlan').set_connection(self.connection_name)
        self.manager.current = 'configure_vlan'

    def show_vpn_config(self):
        self.manager.get_screen('vpn_config_list').set_connection(self.connection_name)
        self.manager.current = 'vpn_config_list'

    def go_back(self):
        self.manager.current = 'connections'


class ClientsScreen(Screen):
    connection_name = StringProperty('')

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.toolbar.title = f'DHCP Clients - {conn_name}'

    def on_enter(self):
        self.load_data()

    def load_data(self):
        try:
            self.ids.data_layout.clear_widgets()
            output = watcher.process_command(['dhcp', 'clients', 'list', '--connection', self.connection_name])
            data = output.getvalue()
            lines = data.strip().split('\n')
            if len(lines) > 2:
                for i, line in enumerate(lines[2:], start=2):
                    parts = line.split()
                    if len(parts) >= 4:
                        row = MDBoxLayout(size_hint_y=None, height=dp(40), spacing=dp(2))
                        row.add_widget(MDLabel(text=parts[1], size_hint_x=0.35, font_style='Body2'))
                        row.add_widget(MDLabel(text=parts[2], size_hint_x=0.30, font_style='Body2'))
                        row.add_widget(MDLabel(text=parts[3], size_hint_x=0.35, font_style='Body2'))
                        self.ids.data_layout.add_widget(row)
        except Exception as e:
            self.show_error(f"Failed to load clients: {str(e)}")

    def go_back(self):
        self.manager.current = 'status'

    def show_error(self, message):
        ok_button = MDFlatButton(text='OK')
        dialog = MDDialog(
            title='Error',
            text=message,
            buttons=[ok_button]
        )
        ok_button.bind(on_press=lambda x: dialog.dismiss())
        dialog.open()


class StaticLeasesScreen(Screen):
    connection_name = StringProperty('')

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.toolbar.title = f'Static Leases - {conn_name}'

    def on_enter(self):
        self.load_data()

    def load_data(self):
        try:
            self.ids.data_layout.clear_widgets()
            output = watcher.process_command(['dhcp', 'static-leases', 'list', '--connection', self.connection_name])
            data = output.getvalue()
            lines = data.strip().split('\n')
            if len(lines) > 2:
                for i, line in enumerate(lines[2:], start=2):
                    parts = line.split()
                    if len(parts) >= 3:
                        row = MDBoxLayout(size_hint_y=None, height=dp(40), spacing=dp(2))
                        row.add_widget(MDLabel(text=parts[0], size_hint_x=0.35, font_style='Body2'))
                        row.add_widget(MDLabel(text=parts[1], size_hint_x=0.35, font_style='Body2'))
                        row.add_widget(MDLabel(text=parts[2], size_hint_x=0.30, font_style='Body2'))
                        self.ids.data_layout.add_widget(row)
        except Exception as e:
            self.show_error(f"Failed to load leases: {str(e)}")

    def go_back(self):
        self.manager.current = 'status'

    def show_error(self, message):
        ok_button = MDFlatButton(text='OK')
        dialog = MDDialog(
            title='Error',
            text=message,
            buttons=[ok_button]
        )
        ok_button.bind(on_press=lambda x: dialog.dismiss())
        dialog.open()


class ConfigScreen(Screen):
    connection_name = StringProperty('')

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.toolbar.title = f'Router Config - {conn_name}'

    def on_enter(self):
        self.load_data()

    def load_data(self):
        try:
            output = watcher.process_command(['connections', 'config', '--action', 'show', '--connection', self.connection_name])
            data = output.getvalue()
            self.ids.config_text.text = data
        except Exception as e:
            self.show_error(f"Failed to load config: {str(e)}")

    def go_back(self):
        self.manager.current = 'status'

    def show_error(self, message):
        ok_button = MDFlatButton(text='OK')
        dialog = MDDialog(
            title='Error',
            text=message,
            buttons=[ok_button]
        )
        ok_button.bind(on_press=lambda x: dialog.dismiss())
        dialog.open()


class VlanListScreen(Screen):
    connection_name = StringProperty('')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._circles = {}
        self._connections = {}
        self._canvas_group = None

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.toolbar.title = f'VLANs - {conn_name}'

    def on_enter(self):
        self.load_data()

    def load_data(self):
        try:
            app = MDApp.get_running_app()
            output = watcher.process_command(['config', 'snapshot', '--connection', self.connection_name])
            data = output.getvalue()
            config = NetworkConfig.from_dict(json.loads(data))
            app.network_config = config

            vlans = config.network.get("vlans", {})
            restrictions = config.network.get("vlan_restrictions", [])
            self._connections = connections_from_restrictions(vlans, restrictions)
            self._rebuild_canvas(vlans)
        except Exception as e:
            self.show_error(f"Failed to load VLANs: {str(e)}")

    def _rebuild_canvas(self, vlans):
        layout = self.ids.canvas_layout
        for circle in list(self._circles.values()):
            layout.remove_widget(circle)
        self._circles.clear()

        if self._canvas_group is not None:
            layout.canvas.before.remove(self._canvas_group)
            self._canvas_group = None

        if not vlans:
            return

        Clock.schedule_once(lambda dt: self._position_circles(vlans), 0)

    def _position_circles(self, vlans):
        layout = self.ids.canvas_layout
        if layout.width == 0 or layout.height == 0:
            Clock.schedule_once(lambda dt: self._position_circles(vlans), 0.05)
            return

        cx = layout.width / 2
        cy = layout.height / 2
        radius = min(layout.width, layout.height) * 0.35

        sorted_vlan_ids = sorted(
            int(name.replace('vlan', '')) for name in vlans.keys()
        )

        positions = polygon_positions(n=len(sorted_vlan_ids), cx=cx, cy=cy, radius=radius)

        for i, vid in enumerate(sorted_vlan_ids):
            vlan_data = vlans[f'vlan{vid}']
            circle = VlanCircle(vlan_id=vid, vlan_data=vlan_data)
            circle.center = positions[i]
            layout.add_widget(circle)
            self._circles[vid] = circle

        self._draw_lines()

    def _draw_lines(self):
        layout = self.ids.canvas_layout
        if self._canvas_group is not None:
            layout.canvas.before.remove(self._canvas_group)

        self._canvas_group = InstructionGroup()

        for (a_id, b_id), state in self._connections.items():
            if a_id not in self._circles or b_id not in self._circles:
                continue
            a_circle = self._circles[a_id]
            b_circle = self._circles[b_id]
            self._draw_connection_line(a_circle, b_circle, state)

        layout.canvas.before.add(self._canvas_group)

    def _draw_connection_line(self, a, b, state):
        ax, ay = a.center
        bx, by = b.center

        if state == "bidirectional":
            color = (0.2, 0.8, 0.2, 1)
        elif state == "forward":
            color = (0.2, 0.6, 0.9, 1)
        else:
            color = (0.9, 0.6, 0.2, 1)

        g = self._canvas_group
        g.add(Color(*color))
        g.add(Line(points=[ax, ay, bx, by], width=dp(2)))

        mid_x = (ax + bx) / 2
        mid_y = (ay + by) / 2
        arrow_size = dp(12)

        if state == "bidirectional":
            self._add_arrowhead(g, at_x=mid_x, at_y=mid_y, from_x=ax, from_y=ay, size=arrow_size, color=color)
            self._add_arrowhead(g, at_x=mid_x, at_y=mid_y, from_x=bx, from_y=by, size=arrow_size, color=color)
        elif state == "forward":
            self._add_arrowhead(g, at_x=mid_x, at_y=mid_y, from_x=ax, from_y=ay, size=arrow_size, color=color)
        else:
            self._add_arrowhead(g, at_x=mid_x, at_y=mid_y, from_x=bx, from_y=by, size=arrow_size, color=color)

    def _add_arrowhead(self, g, at_x, at_y, from_x, from_y, size, color):
        angle = math.atan2(at_y - from_y, at_x - from_x)
        p1x = at_x
        p1y = at_y
        p2x = at_x - size * math.cos(angle - 0.5)
        p2y = at_y - size * math.sin(angle - 0.5)
        p3x = at_x - size * math.cos(angle + 0.5)
        p3y = at_y - size * math.sin(angle + 0.5)
        g.add(Color(*color))
        g.add(Triangle(points=[p1x, p1y, p2x, p2y, p3x, p3y]))

    def go_back(self):
        self.manager.current = 'status'

    def show_error(self, message):
        ok_button = MDFlatButton(text='OK')
        dialog = MDDialog(
            title='Error',
            text=message,
            buttons=[ok_button]
        )
        ok_button.bind(on_press=lambda x: dialog.dismiss())
        dialog.open()


class ConfigureStaticLeasesScreen(Screen):
    connection_name = StringProperty('')
    _pending_leases = None

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.toolbar.title = f'Configure Static Leases - {conn_name}'

    def on_enter(self):
        self.refresh_data()

    def refresh_data(self):
        try:
            output = watcher.process_command(['dhcp', 'static-leases', 'list', '--connection', self.connection_name])
            data = output.getvalue()
            lines = data.strip().split('\n')
            self._pending_leases = []
            if len(lines) > 2:
                for line in lines[2:]:
                    parts = line.split()
                    if len(parts) >= 3:
                        self._pending_leases.append([parts[0], parts[1], parts[2]])
            self._update_display()
            self.ids.status_label.text = f'{len(self._pending_leases)} leases loaded from router'
        except Exception as e:
            self.show_error(f"Failed to load leases: {str(e)}")

    def _update_display(self):
        self.ids.data_layout.clear_widgets()
        if self._pending_leases is None:
            return
        for i, lease in enumerate(self._pending_leases):
            row = MDBoxLayout(size_hint_y=None, height=dp(30), spacing=dp(1))
            row.add_widget(MDLabel(text=lease[0], size_hint_x=0.33, font_style='Caption'))
            row.add_widget(MDLabel(text=lease[1], size_hint_x=0.30, font_style='Caption'))
            row.add_widget(MDLabel(text=lease[2], size_hint_x=0.27, font_style='Caption'))
            del_btn = MDIconButton(icon='close', size_hint_x=0.10)
            del_btn.bind(on_press=lambda instance, idx=i: self.remove_lease(idx))
            row.add_widget(del_btn)
            self.ids.data_layout.add_widget(row)

    def add_lease(self):
        mac = self.ids.new_mac.text.strip()
        hostname = self.ids.new_hostname.text.strip()
        ip = self.ids.new_ip.text.strip()

        if not mac or not hostname or not ip:
            self.show_error("All fields (MAC, Hostname, IP) are required")
            return

        for existing in self._pending_leases:
            if existing[0] == mac:
                self.show_error(f"MAC address {mac} already exists")
                return
            if existing[1] == hostname:
                self.show_error(f"Hostname {hostname} already exists")
                return
            if existing[2] == ip:
                self.show_error(f"IP address {ip} already exists")
                return

        self._pending_leases.append([mac, hostname, ip])
        self.ids.new_mac.text = ''
        self.ids.new_hostname.text = ''
        self.ids.new_ip.text = ''
        self._update_display()
        self.ids.status_label.text = f'{len(self._pending_leases)} pending leases (modified)'

    def remove_lease(self, idx):
        if self._pending_leases and idx < len(self._pending_leases):
            self._pending_leases.pop(idx)
            self._update_display()
            self.ids.status_label.text = f'{len(self._pending_leases)} pending leases (modified)'

    def commit_changes(self):
        if self._pending_leases is None:
            self.show_error("No leases loaded. Refresh first.")
            return

        def confirm(instance):
            dialog.dismiss()
            try:
                db = connectiondb.ConnectionDB()
                conn, router = db.get_connection_with_handler(self.connection_name, io.StringIO())
                if conn is None:
                    self.show_error("Failed to connect")
                    return
                router.set_static_leases(conn, self._pending_leases)
                router.commit_config(conn)
                router.restart_dhcp_service(conn)
                self.ids.status_label.text = 'Committed successfully'
                self.refresh_data()
            except Exception as e:
                self.show_error(f"Commit failed: {str(e)}")

        apply_btn = MDRaisedButton(text='Apply')
        cancel_btn = MDFlatButton(text='Cancel')

        apply_btn.bind(on_press=confirm)
        cancel_btn.bind(on_press=lambda x: dialog.dismiss())

        dialog = MDDialog(
            title='Commit Static Leases',
            text=f'Apply {len(self._pending_leases)} static leases to the router?',
            buttons=[apply_btn, cancel_btn]
        )
        dialog.open()

    def go_back(self):
        self.manager.current = 'configure'

    def show_error(self, message):
        ok_button = MDFlatButton(text='OK')
        dialog = MDDialog(
            title='Error',
            text=message,
            buttons=[ok_button]
        )
        ok_button.bind(on_press=lambda x: dialog.dismiss())
        dialog.open()


class ConfigureDhcpScreen(Screen):
    connection_name = StringProperty('')
    _selected_macs = None

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.toolbar.title = f'DHCP Lease Revocation - {conn_name}'

    def on_enter(self):
        self._selected_macs = set()
        self.load_data()

    def load_data(self):
        try:
            self.ids.data_layout.clear_widgets()
            self._selected_macs = set()
            output = watcher.process_command(['dhcp', 'clients', 'list', '--connection', self.connection_name])
            data = output.getvalue()
            lines = data.strip().split('\n')
            if len(lines) > 2:
                for i, line in enumerate(lines[2:], start=2):
                    parts = line.split()
                    if len(parts) >= 4:
                        mac = parts[1]
                        row = MDBoxLayout(size_hint_y=None, height=dp(35), spacing=dp(1))
                        cb = MDCheckbox(active=False, size_hint_x=0.10)
                        cb.bind(active=lambda instance, value, m=mac: self._toggle_mac(m, value))
                        row.add_widget(cb)
                        row.add_widget(MDLabel(text=parts[1], size_hint_x=0.30, font_style='Caption'))
                        row.add_widget(MDLabel(text=parts[2], size_hint_x=0.30, font_style='Caption'))
                        row.add_widget(MDLabel(text=parts[3], size_hint_x=0.30, font_style='Caption'))
                        self.ids.data_layout.add_widget(row)
        except Exception as e:
            self.show_error(f"Failed to load DHCP clients: {str(e)}")

    def _toggle_mac(self, mac, active):
        if active:
            self._selected_macs.add(mac)
        else:
            self._selected_macs.discard(mac)

    def revoke_selected(self):
        if not self._selected_macs:
            self.show_error("No leases selected for revocation")
            return

        mac_list = ', '.join(sorted(self._selected_macs))

        def confirm(instance):
            dialog.dismiss()
            try:
                db = connectiondb.ConnectionDB()
                conn, router = db.get_connection_with_handler(self.connection_name, io.StringIO())
                if conn is None:
                    self.show_error("Failed to connect")
                    return
                router.remove_dhcp_leases(conn, list(self._selected_macs))
                self._selected_macs = set()
                self.load_data()
            except Exception as e:
                self.show_error(f"Revoke failed: {str(e)}")

        revoke_btn = MDRaisedButton(text='Revoke')
        cancel_btn = MDFlatButton(text='Cancel')

        revoke_btn.bind(on_press=confirm)
        cancel_btn.bind(on_press=lambda x: dialog.dismiss())

        dialog = MDDialog(
            title='Revoke Leases',
            text=f'Revoke {len(self._selected_macs)} DHCP lease(s)?\nMACs: {mac_list}',
            buttons=[revoke_btn, cancel_btn]
        )
        dialog.open()

    def go_back(self):
        self.manager.current = 'configure'

    def show_error(self, message):
        ok_button = MDFlatButton(text='OK')
        dialog = MDDialog(
            title='Error',
            text=message,
            buttons=[ok_button]
        )
        ok_button.bind(on_press=lambda x: dialog.dismiss())
        dialog.open()


class ConfigureVlanScreen(Screen):
    connection_name = StringProperty('')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._circles = {}
        self._connections = {}
        self._canvas_group = None
        self._touch_data = {}

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.toolbar.title = f'VLAN Configuration - {conn_name}'

    def on_enter(self):
        app = MDApp.get_running_app()
        config = getattr(app, 'network_config', None)
        if config is not None:
            self.ids.status_label.text = 'Config loaded'
            self.ids.status_label.color = (0.2, 0.7, 0.2, 1)
            self.refresh()
        else:
            self.ids.status_label.text = 'No config loaded - take a snapshot first'
            self.ids.status_label.color = (0.8, 0.4, 0.1, 1)
            self._rebuild_canvas({})

    def refresh(self):
        app = MDApp.get_running_app()
        config = getattr(app, 'network_config', None)
        if config is None:
            self._rebuild_canvas({})
            return

        vlans = config.network.get("vlans", {})
        restrictions = config.network.get("vlan_restrictions", [])
        self._connections = connections_from_restrictions(vlans, restrictions)
        self._rebuild_canvas(vlans)

    def _rebuild_canvas(self, vlans):
        layout = self.ids.canvas_layout
        for circle in list(self._circles.values()):
            layout.remove_widget(circle)
        self._circles.clear()

        if self._canvas_group is not None:
            layout.canvas.before.remove(self._canvas_group)
            self._canvas_group = None

        if not vlans:
            return

        Clock.schedule_once(lambda dt: self._position_circles(vlans), 0)

    def _position_circles(self, vlans):
        layout = self.ids.canvas_layout
        if layout.width == 0 or layout.height == 0:
            Clock.schedule_once(lambda dt: self._position_circles(vlans), 0.05)
            return

        cx = layout.width / 2
        cy = layout.height / 2
        radius = min(layout.width, layout.height) * 0.35

        sorted_vlan_ids = sorted(
            int(name.replace('vlan', '')) for name in vlans.keys()
        )

        positions = polygon_positions(n=len(sorted_vlan_ids), cx=cx, cy=cy, radius=radius)

        for i, vid in enumerate(sorted_vlan_ids):
            vlan_data = vlans[f'vlan{vid}']
            circle = VlanCircle(vlan_id=vid, vlan_data=vlan_data)
            circle.center = positions[i]
            layout.add_widget(circle)
            self._circles[vid] = circle

        self._draw_lines()

    def _draw_lines(self):
        layout = self.ids.canvas_layout
        if self._canvas_group is not None:
            layout.canvas.before.remove(self._canvas_group)

        self._canvas_group = InstructionGroup()

        for (a_id, b_id), state in self._connections.items():
            if a_id not in self._circles or b_id not in self._circles:
                continue
            a_circle = self._circles[a_id]
            b_circle = self._circles[b_id]
            self._draw_connection_line(a_circle, b_circle, state)

        layout.canvas.before.add(self._canvas_group)

    def _draw_connection_line(self, a, b, state):
        ax, ay = a.center
        bx, by = b.center

        if state == "bidirectional":
            color = (0.2, 0.8, 0.2, 1)
        elif state == "forward":
            color = (0.2, 0.6, 0.9, 1)
        else:
            color = (0.9, 0.6, 0.2, 1)

        g = self._canvas_group
        g.add(Color(*color))
        g.add(Line(points=[ax, ay, bx, by], width=dp(2)))

        mid_x = (ax + bx) / 2
        mid_y = (ay + by) / 2
        arrow_size = dp(12)

        if state == "bidirectional":
            self._add_arrowhead(g, at_x=mid_x, at_y=mid_y, from_x=ax, from_y=ay, size=arrow_size, color=color)
            self._add_arrowhead(g, at_x=mid_x, at_y=mid_y, from_x=bx, from_y=by, size=arrow_size, color=color)
        elif state == "forward":
            self._add_arrowhead(g, at_x=mid_x, at_y=mid_y, from_x=ax, from_y=ay, size=arrow_size, color=color)
        else:
            self._add_arrowhead(g, at_x=mid_x, at_y=mid_y, from_x=bx, from_y=by, size=arrow_size, color=color)

    def _add_arrowhead(self, g, at_x, at_y, from_x, from_y, size, color):
        angle = math.atan2(at_y - from_y, at_x - from_x)
        p1x = at_x
        p1y = at_y
        p2x = at_x - size * math.cos(angle - 0.5)
        p2y = at_y - size * math.sin(angle - 0.5)
        p3x = at_x - size * math.cos(angle + 0.5)
        p3y = at_y - size * math.sin(angle + 0.5)
        g.add(Color(*color))
        g.add(Triangle(points=[p1x, p1y, p2x, p2y, p3x, p3y]))

    def add_vlan(self):
        app = MDApp.get_running_app()
        config = getattr(app, 'network_config', None)
        if config is None:
            self.show_error("No config loaded. Snapshot first.")
            return

        vlan_ids = [
            int(name.replace('vlan', ''))
            for name in config.network.get("vlans", {}).keys()
        ]
        next_id = max(vlan_ids, default=0) + 1

        try:
            config.add_vlan(
                vlan_id=next_id,
                ip="0.0.0.0",
                netmask="255.255.255.0",
                bridged=True,
                nat=False,
                dhcp_enabled=False,
                dhcp_start=0,
                dhcp_size=0,
                dhcp_lease=0,
            )
        except ValueError as e:
            self.show_error(f"Failed to add VLAN: {str(e)}")
            return

        self.refresh()

        self.manager.get_screen('vlan_edit').set_connection(self.connection_name)
        self.manager.get_screen('vlan_edit').edit_vlan(next_id)
        self.manager.current = 'vlan_edit'

    def cycle_connection(self, a_id, b_id):
        key = (min(a_id, b_id), max(a_id, b_id))
        if key not in self._connections:
            return

        current = self._connections[key]
        if current == "bidirectional":
            self._connections[key] = "forward"
        elif current == "forward":
            self._connections[key] = "backward"
        else:
            self._connections[key] = "bidirectional"

        app = MDApp.get_running_app()
        config = getattr(app, 'network_config', None)
        if config is not None:
            sync_connections_to_config(config, self._connections)

        self._draw_lines()

    def remove_connection(self, a_id, b_id):
        key = (min(a_id, b_id), max(a_id, b_id))
        if key in self._connections:
            del self._connections[key]

        app = MDApp.get_running_app()
        config = getattr(app, 'network_config', None)
        if config is not None:
            sync_connections_to_config(config, self._connections)

        self._draw_lines()

    def on_touch_down(self, touch):
        layout = self.ids.canvas_layout
        if not layout.collide_point(*touch.pos):
            return super().on_touch_down(touch)

        local_x, local_y = layout.to_local(touch.pos[0], touch.pos[1])

        for vid, circle in self._circles.items():
            if circle.collide_point(local_x, local_y):
                now = Clock.get_time()
                target_key = ('circle', vid)
                if target_key in self._touch_data and now - self._touch_data[target_key] < DOUBLE_CLICK_TIMEOUT:
                    self._touch_data.pop(target_key, None)
                    self.manager.get_screen('vlan_edit').set_connection(self.connection_name)
                    self.manager.get_screen('vlan_edit').edit_vlan(vid)
                    self.manager.current = 'vlan_edit'
                    return True
                self._touch_data[target_key] = now
                return True

        for (a_id, b_id), state in list(self._connections.items()):
            if a_id not in self._circles or b_id not in self._circles:
                continue
            a = self._circles[a_id]
            b = self._circles[b_id]
            dist = point_to_segment_distance(
                local_x, local_y,
                a.center_x, a.center_y,
                b.center_x, b.center_y,
            )
            if dist < LINE_HIT_THRESHOLD:
                now = Clock.get_time()
                target_key = ('line', a_id, b_id)
                if target_key in self._touch_data and now - self._touch_data[target_key] < DOUBLE_CLICK_TIMEOUT:
                    self._touch_data.pop(target_key, None)
                    self._on_long_press(a_id, b_id)
                    return True
                self.cycle_connection(a_id, b_id)
                self._touch_data[target_key] = now
                return True

        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        return super().on_touch_up(touch)

    def on_touch_move(self, touch):
        return super().on_touch_move(touch)

    def _on_long_press(self, a_id, b_id):
        target_key = ('line', a_id, b_id)
        for k in list(self._touch_data.keys()):
            if k == target_key:
                del self._touch_data[k]

        def confirm(instance):
            dialog.dismiss()
            self.remove_connection(a_id, b_id)

        delete_btn = MDRaisedButton(text='Delete')
        cancel_btn = MDFlatButton(text='Cancel')

        delete_btn.bind(on_press=confirm)
        cancel_btn.bind(on_press=lambda x: dialog.dismiss())

        dialog = MDDialog(
            title='Delete Connection',
            text=f'Delete connection between vlan{a_id} and vlan{b_id}?',
            buttons=[delete_btn, cancel_btn]
        )
        dialog.open()

    def snapshot(self):
        try:
            output = watcher.process_command(['config', 'snapshot', '--connection', self.connection_name])
            data = output.getvalue()
            app = MDApp.get_running_app()
            app.network_config = NetworkConfig.from_dict(json.loads(data))
            self.ids.status_label.text = 'Config loaded from router'
            self.ids.status_label.color = (0.2, 0.7, 0.2, 1)
            self.refresh()
        except Exception as e:
            self.show_error(f"Snapshot failed: {str(e)}")

    def commit_changes(self):
        try:
            app = MDApp.get_running_app()
            config = getattr(app, 'network_config', None)
            if config is None:
                self.show_error("No config loaded. Take a snapshot first.")
                return
            errors = config.validate()
            if errors:
                self.show_error("Cannot apply - validation errors found:\n" + '\n'.join(f'  - {e}' for e in errors))
                return

            def confirm(instance):
                dialog.dismiss()
                try:
                    db = connectiondb.ConnectionDB()
                    conn, router = db.get_connection_with_handler(self.connection_name, io.StringIO())
                    if conn is None:
                        self.show_error("Failed to connect")
                        return
                    config.apply_to_router(conn, router, mode='diff')
                    self.ids.status_label.text = 'Committed to router'
                    self.ids.status_label.color = (0.2, 0.7, 0.2, 1)
                except Exception as e:
                    self.show_error(f"Apply failed: {str(e)}")

            apply_btn = MDRaisedButton(text='Apply')
            cancel_btn = MDFlatButton(text='Cancel')

            apply_btn.bind(on_press=confirm)
            cancel_btn.bind(on_press=lambda x: dialog.dismiss())

            dialog = MDDialog(
                title='Commit VLAN Changes',
                text='Apply config changes to the router?\nThis will modify the router configuration.',
                buttons=[apply_btn, cancel_btn]
            )
            dialog.open()
        except Exception as e:
            self.show_error(f"Commit failed: {str(e)}")

    def go_back(self):
        self.manager.current = 'configure'

    def show_error(self, message):
        ok_button = MDFlatButton(text='OK')
        dialog = MDDialog(
            title='Error',
            text=message,
            buttons=[ok_button]
        )
        ok_button.bind(on_press=lambda x: dialog.dismiss())
        dialog.open()


class VlanEditScreen(Screen):
    connection_name = StringProperty('')
    _editing_vlan = None

    def set_connection(self, conn_name):
        self.connection_name = conn_name

    def new_vlan(self):
        self._editing_vlan = None
        self.ids.toolbar.title = 'New VLAN'
        self.ids.vlan_id.text = ''
        self.ids.vlan_ip.text = '0.0.0.0'
        self.ids.vlan_netmask.text = '255.255.255.0'
        self.ids.vlan_bridged.active = True
        self.ids.vlan_nat.active = False
        self.ids.vlan_dhcp_enabled.active = False
        self.ids.vlan_dhcp_start.text = '0'
        self.ids.vlan_dhcp_size.text = '0'
        self.ids.vlan_dhcp_lease.text = '0'

    def edit_vlan(self, vlan_id):
        self._editing_vlan = vlan_id
        self.ids.toolbar.title = f'Edit VLAN {vlan_id}'

        app = MDApp.get_running_app()
        config = getattr(app, 'network_config', None)
        if config is None:
            return

        vlan_data = config.network.get("vlans", {}).get(f"vlan{vlan_id}", {})
        self.ids.vlan_id.text = str(vlan_id)
        self.ids.vlan_ip.text = vlan_data.get("ip", "0.0.0.0")
        self.ids.vlan_netmask.text = vlan_data.get("netmask", "255.255.255.0")
        self.ids.vlan_bridged.active = vlan_data.get("bridged", False)
        self.ids.vlan_nat.active = vlan_data.get("nat", False)

        dhcp = vlan_data.get("dhcp", {})
        self.ids.vlan_dhcp_enabled.active = dhcp.get("enabled", False)
        self.ids.vlan_dhcp_start.text = str(dhcp.get("range_start", 0))
        self.ids.vlan_dhcp_size.text = str(dhcp.get("range_size", 0))
        self.ids.vlan_dhcp_lease.text = str(dhcp.get("lease_time_min", 0))

    def save_vlan(self):
        try:
            app = MDApp.get_running_app()
            config = getattr(app, 'network_config', None)
            if config is None:
                config = NetworkConfig.from_scratch()
                app.network_config = config
            vlan_id = int(self.ids.vlan_id.text)
            if self._editing_vlan is not None:
                config.update_vlan(
                    vlan_id=vlan_id,
                    ip=self.ids.vlan_ip.text,
                    netmask=self.ids.vlan_netmask.text,
                    bridged=self.ids.vlan_bridged.active,
                    nat=self.ids.vlan_nat.active,
                    dhcp_enabled=self.ids.vlan_dhcp_enabled.active,
                    dhcp_start=int(self.ids.vlan_dhcp_start.text or '0'),
                    dhcp_size=int(self.ids.vlan_dhcp_size.text or '0'),
                    dhcp_lease=int(self.ids.vlan_dhcp_lease.text or '0'),
                )
            else:
                config.add_vlan(
                    vlan_id=vlan_id,
                    ip=self.ids.vlan_ip.text,
                    netmask=self.ids.vlan_netmask.text,
                    bridged=self.ids.vlan_bridged.active,
                    nat=self.ids.vlan_nat.active,
                    dhcp_enabled=self.ids.vlan_dhcp_enabled.active,
                    dhcp_start=int(self.ids.vlan_dhcp_start.text or '0'),
                    dhcp_size=int(self.ids.vlan_dhcp_size.text or '0'),
                    dhcp_lease=int(self.ids.vlan_dhcp_lease.text or '0'),
                )
            self.manager.current = 'configure_vlan'
        except Exception as e:
            self.show_error(f"Failed to save VLAN: {str(e)}")

    def delete_vlan(self):
        try:
            app = MDApp.get_running_app()
            config = getattr(app, 'network_config', None)
            if config is None or self._editing_vlan is None:
                return

            vlan_id = self._editing_vlan

            def confirm(instance):
                dialog.dismiss()
                config.remove_vlan(vlan_id=vlan_id)
                restriction_keys = [
                    (r['from'], r['to'])
                    for r in config.network.get("vlan_restrictions", [])
                    if r['from'] == vlan_id or r['to'] == vlan_id
                ]
                for (f, t) in restriction_keys:
                    config.remove_restriction(from_id=f, to_id=t)
                self.manager.current = 'configure_vlan'

            delete_btn = MDRaisedButton(text='Delete')
            cancel_btn = MDFlatButton(text='Cancel')

            delete_btn.bind(on_press=confirm)
            cancel_btn.bind(on_press=lambda x: dialog.dismiss())

            dialog = MDDialog(
                title='Delete VLAN',
                text=f'Are you sure you want to delete\nvlan{vlan_id} and all its connections?',
                buttons=[delete_btn, cancel_btn]
            )
            dialog.open()
        except Exception as e:
            self.show_error(f"Failed to delete VLAN: {str(e)}")

    def go_back(self):
        self.manager.current = 'configure_vlan'

    def show_error(self, message):
        ok_button = MDFlatButton(text='OK')
        dialog = MDDialog(
            title='Error',
            text=message,
            buttons=[ok_button]
        )
        ok_button.bind(on_press=lambda x: dialog.dismiss())
        dialog.open()


class VpnStatusScreen(Screen):
    connection_name = StringProperty('')

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.toolbar.title = f'VPN Status - {conn_name}'

    def on_enter(self):
        self.load_status()

    def load_status(self):
        try:
            db = connectiondb.ConnectionDB()
            conn, router = db.get_connection_with_handler(self.connection_name, io.StringIO())
            if conn is None:
                self.ids.status_text.text = 'Failed to connect'
                return
            status = router.get_vpn_status(conn)
            active_vpn = db.get_active_vpn(self.connection_name)
            vpn_configs = db.get_vpn_configs(self.connection_name)
            active_config = vpn_configs.get(active_vpn, {})

            lines = []
            connected = status.get('connected', False)
            status_str = 'Connected' if connected else 'Disconnected'
            lines.append(f'Status: {status_str}')
            lines.append(f'Enabled: {"Yes" if status.get("enabled") else "No"}')
            if active_vpn:
                lines.append(f'Active Config: {active_vpn}')
            if status.get('remote'):
                lines.append(f'Remote: {status["remote"]}')
            if status.get('port'):
                lines.append(f'Port: {status["port"]}')
            if status.get('proto'):
                lines.append(f'Protocol: {status["proto"]}')
            if status.get('interface'):
                lines.append(f'Interface: {status["interface"]}')
            if active_config:
                summary = config_summary(active_config)
                for key, value in summary.items():
                    lines.append(f'{key}: {value}')
            self.ids.status_text.text = '\n'.join(lines) if lines else 'No VPN status available'
        except Exception as e:
            self.ids.status_text.text = f'Error: {str(e)}'

    def toggle_vpn(self):
        try:
            db = connectiondb.ConnectionDB()
            conn, router = db.get_connection_with_handler(self.connection_name, io.StringIO())
            if conn is None:
                self.show_error('Failed to connect')
                return
            status = router.get_vpn_status(conn)
            if status.get('connected'):
                router.stop_vpn(conn)
            else:
                self.apply_and_start(router, conn, db)
            self.load_status()
        except Exception as e:
            self.show_error(f'Failed: {str(e)}')

    def apply_and_start(self, router, conn, db):
        active_vpn = db.get_active_vpn(self.connection_name)
        if not active_vpn:
            self.show_error('No VPN config selected. Set an active config first.')
            return
        vpn_configs = db.get_vpn_configs(self.connection_name)
        vpn_config = vpn_configs.get(active_vpn)
        if not vpn_config:
            self.show_error(f'VPN config "{active_vpn}" not found.')
            return
        nvram_config = get_ddwrt_nvram_from_config(vpn_config)
        router.apply_vpn_config(conn, nvram_config)
        router.start_vpn(conn)

    def go_back(self):
        self.manager.current = 'status'

    def show_error(self, message):
        ok_button = MDFlatButton(text='OK')
        dialog = MDDialog(
            title='Error',
            text=message,
            buttons=[ok_button]
        )
        ok_button.bind(on_press=lambda x: dialog.dismiss())
        dialog.open()


class VpnConfigListScreen(Screen):
    connection_name = StringProperty('')

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.toolbar.title = f'VPN Configs - {conn_name}'

    def on_enter(self):
        self.load_configs()

    def load_configs(self):
        try:
            self.ids.config_layout.clear_widgets()
            db = connectiondb.ConnectionDB()
            vpn_configs = db.get_vpn_configs(self.connection_name)
            active_vpn = db.get_active_vpn(self.connection_name)
            if not vpn_configs:
                self.ids.status_label.text = 'No VPN configs found. Add one below.'
                return
            self.ids.status_label.text = f'{len(vpn_configs)} config(s) found'
            for name, config in vpn_configs.items():
                row = MDBoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
                label_text = name
                if name == active_vpn:
                    label_text += ' *'
                row.add_widget(MDLabel(text=label_text, font_style='H6', size_hint_x=0.4))
                summary = config_summary(config)
                detail = ', '.join(f'{k}: {v}' for k, v in summary.items())
                row.add_widget(MDLabel(text=detail, font_style='Caption', size_hint_x=0.35))

                activate_btn = TooltipMDIconButton(icon='check-circle', tooltip_text='Set Active')
                activate_btn.bind(on_press=lambda instance, n=name: self.activate_config(n))
                row.add_widget(activate_btn)

                edit_btn = TooltipMDIconButton(icon='pencil', tooltip_text='Edit')
                edit_btn.bind(on_press=lambda instance, n=name: self.edit_config(n))
                row.add_widget(edit_btn)

                delete_btn = TooltipMDIconButton(icon='delete', tooltip_text='Delete')
                delete_btn.bind(on_press=lambda instance, n=name: self.delete_config(n))
                row.add_widget(delete_btn)
                self.ids.config_layout.add_widget(row)
        except Exception as e:
            self.show_error(f'Failed to load configs: {str(e)}')

    def activate_config(self, name):
        try:
            db = connectiondb.ConnectionDB()
            db.set_active_vpn(self.connection_name, name)
            self.load_configs()
        except Exception as e:
            self.show_error(f'Failed to activate: {str(e)}')

    def add_config(self):
        self.manager.get_screen('vpn_config_edit').set_connection(self.connection_name)
        self.manager.get_screen('vpn_config_edit').new_config()
        self.manager.current = 'vpn_config_edit'

    def import_ovpn(self):
        self.manager.get_screen('vpn_config_edit').set_connection(self.connection_name)
        self.manager.get_screen('vpn_config_edit').show_file_chooser()
        self.manager.current = 'vpn_config_edit'

    def edit_config(self, name):
        self.manager.get_screen('vpn_config_edit').set_connection(self.connection_name)
        self.manager.get_screen('vpn_config_edit').edit_config(name)
        self.manager.current = 'vpn_config_edit'

    def delete_config(self, name):
        def confirm(instance):
            dialog.dismiss()
            try:
                db = connectiondb.ConnectionDB()
                db.delete_vpn_config(self.connection_name, name)
                self.load_configs()
            except Exception as e:
                self.show_error(f'Failed to delete: {str(e)}')

        delete_btn = MDRaisedButton(text='Delete')
        cancel_btn = MDFlatButton(text='Cancel')
        delete_btn.bind(on_press=confirm)
        cancel_btn.bind(on_press=lambda x: dialog.dismiss())
        dialog = MDDialog(
            title='Delete VPN Config',
            text=f'Delete VPN config "{name}"?',
            buttons=[delete_btn, cancel_btn]
        )
        dialog.open()

    def go_back(self):
        self.manager.current = 'configure'

    def show_error(self, message):
        ok_button = MDFlatButton(text='OK')
        dialog = MDDialog(
            title='Error',
            text=message,
            buttons=[ok_button]
        )
        ok_button.bind(on_press=lambda x: dialog.dismiss())
        dialog.open()


class VpnConfigEditScreen(Screen):
    connection_name = StringProperty('')
    _editing_config = None
    _ovpn_content = None

    def set_connection(self, conn_name):
        self.connection_name = conn_name

    def new_config(self):
        self._editing_config = None
        self._ovpn_content = None
        self.ids.toolbar.title = 'New VPN Config'
        self._clear_fields()

    def edit_config(self, name):
        self._editing_config = name
        self._ovpn_content = None
        self.ids.toolbar.title = f'Edit VPN - {name}'
        try:
            db = connectiondb.ConnectionDB()
            vpn_configs = db.get_vpn_configs(self.connection_name)
            config = vpn_configs.get(name, {})
            self._clear_fields()
            self.ids.vpn_name.text = name
            self.ids.vpn_remote.text = config.get('remote', '')
            self.ids.vpn_port.text = config.get('port', '1194')
            self.ids.vpn_proto.text = config.get('proto', 'udp')
            self.ids.vpn_dev.text = config.get('dev', 'tun0')
            self.ids.vpn_cipher.text = config.get('cipher', '')
            self.ids.vpn_auth.text = config.get('auth', '')
            self.ids.vpn_complzo.text = config.get('comp-lzo', 'no')
            self.ids.vpn_username.text = config.get('username', '')
            self.ids.vpn_password.text = config.get('password', '')
            self.ids.vpn_ca.text = config.get('ca', '')
            self.ids.vpn_cert.text = config.get('cert', '')
            self.ids.vpn_key.text = config.get('key', '')
        except Exception as e:
            self.show_error(f'Failed to load config: {str(e)}')

    def _clear_fields(self):
        self.ids.vpn_name.text = ''
        self.ids.vpn_remote.text = ''
        self.ids.vpn_port.text = '1194'
        self.ids.vpn_proto.text = 'udp'
        self.ids.vpn_dev.text = 'tun0'
        self.ids.vpn_cipher.text = ''
        self.ids.vpn_auth.text = ''
        self.ids.vpn_complzo.text = 'no'
        self.ids.vpn_username.text = ''
        self.ids.vpn_password.text = ''
        self.ids.vpn_ca.text = ''
        self.ids.vpn_cert.text = ''
        self.ids.vpn_key.text = ''

    def show_file_chooser(self):
        content = MDBoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))
        file_chooser = FileChooserListView(dirselect=False)
        file_chooser.path = '.'
        file_chooser.filters = ['*.ovpn']
        content.add_widget(file_chooser)

        select_btn = MDRaisedButton(text='Select')
        cancel_btn = MDFlatButton(text='Cancel')

        popup = Popup(
            title='Select .ovpn File',
            content=content,
            size_hint=(0.9, 0.9),
        )

        def on_select(instance):
            if file_chooser.selection:
                filepath = file_chooser.selection[0]
                popup.dismiss()
                self._load_ovpn(filepath)

        select_btn.bind(on_press=on_select)
        cancel_btn.bind(on_press=lambda x: popup.dismiss())

        btn_row = MDBoxLayout(size_hint_y=None, height=dp(48), spacing=dp(10))
        btn_row.add_widget(select_btn)
        btn_row.add_widget(cancel_btn)
        content.add_widget(btn_row)

        content.popup = popup
        popup.open()

    def _load_ovpn(self, filepath):
        try:
            parsed = parse_ovpn_file(filepath)
            self._ovpn_content = parsed
            self._clear_fields()
            if self._editing_config is None:
                import os
                self.ids.vpn_name.text = os.path.splitext(os.path.basename(filepath))[0]
            if 'remote' in parsed:
                self.ids.vpn_remote.text = parsed['remote']
            if 'port' in parsed:
                self.ids.vpn_port.text = parsed['port']
            if 'proto' in parsed:
                self.ids.vpn_proto.text = parsed['proto']
            if 'dev' in parsed:
                self.ids.vpn_dev.text = parsed['dev']
            if 'cipher' in parsed:
                self.ids.vpn_cipher.text = parsed['cipher']
            if 'auth' in parsed:
                self.ids.vpn_auth.text = parsed['auth']
            if 'comp-lzo' in parsed:
                self.ids.vpn_complzo.text = parsed['comp-lzo']
            if 'auth-user-pass' in parsed:
                pass
            if 'ca' in parsed:
                self.ids.vpn_ca.text = parsed['ca']
            if 'cert' in parsed:
                self.ids.vpn_cert.text = parsed['cert']
            if 'key' in parsed:
                self.ids.vpn_key.text = parsed['key']
            self.ids.status_label.text = f'Loaded: {filepath}'
            self.ids.status_label.color = (0.2, 0.7, 0.2, 1)
        except Exception as e:
            self.show_error(f'Failed to parse .ovpn file: {str(e)}')

    def save_config(self):
        try:
            name = self.ids.vpn_name.text.strip()
            if not name:
                self.show_error('Config name is required')
                return
            config = {}
            if self.ids.vpn_remote.text.strip():
                config['remote'] = self.ids.vpn_remote.text.strip()
            if self.ids.vpn_port.text.strip():
                config['port'] = self.ids.vpn_port.text.strip()
            if self.ids.vpn_proto.text.strip():
                config['proto'] = self.ids.vpn_proto.text.strip()
            if self.ids.vpn_dev.text.strip():
                config['dev'] = self.ids.vpn_dev.text.strip()
            if self.ids.vpn_cipher.text.strip():
                config['cipher'] = self.ids.vpn_cipher.text.strip()
            if self.ids.vpn_auth.text.strip():
                config['auth'] = self.ids.vpn_auth.text.strip()
            if self.ids.vpn_complzo.text.strip():
                config['comp-lzo'] = self.ids.vpn_complzo.text.strip()
            if self.ids.vpn_username.text.strip():
                config['username'] = self.ids.vpn_username.text.strip()
                config['auth-user-pass'] = 'true'
            if self.ids.vpn_password.text.strip():
                config['password'] = self.ids.vpn_password.text.strip()
            if self.ids.vpn_ca.text.strip():
                config['ca'] = self.ids.vpn_ca.text.strip()
            if self.ids.vpn_cert.text.strip():
                config['cert'] = self.ids.vpn_cert.text.strip()
            if self.ids.vpn_key.text.strip():
                config['key'] = self.ids.vpn_key.text.strip()
            if self._ovpn_content:
                for key in ('key-direction', 'tls-auth', 'tls-crypt', 'resolv-retry',
                            'remote-cert-tls', 'keepalive', 'mssfix', 'mtu',
                            'tun-mtu', 'fragment', 'nobind', 'persist-key',
                            'persist-tun', 'verb', 'tls-version-min', 'dh',
                            'secret', 'crl-verify', 'pkcs12'):
                    if key in self._ovpn_content:
                        config[key] = self._ovpn_content[key]

            db = connectiondb.ConnectionDB()
            db.add_vpn_config(self.connection_name, name, config)
            if self._editing_config is None:
                db.set_active_vpn(self.connection_name, name)
            self.manager.get_screen('vpn_config_list').set_connection(self.connection_name)
            self.manager.current = 'vpn_config_list'
        except Exception as e:
            self.show_error(f'Failed to save config: {str(e)}')

    def load_ovpn_from_path(self):
        filepath = self.ids.ovpn_path.text.strip()
        if not filepath:
            self.show_error('Enter a file path or use the file chooser')
            return
        self._load_ovpn(filepath)

    def go_back(self):
        self.manager.current = 'vpn_config_list'

    def show_error(self, message):
        ok_button = MDFlatButton(text='OK')
        dialog = MDDialog(
            title='Error',
            text=message,
            buttons=[ok_button]
        )
        ok_button.bind(on_press=lambda x: dialog.dismiss())
        dialog.open()


class WatcherScreenManager(ScreenManager):
    def on_touch_down(self, touch):
        if self.transition.is_active:
            return False
        if self.current_screen:
            return self.current_screen.on_touch_down(touch)
        return False

    def on_touch_move(self, touch):
        if self.transition.is_active:
            return False
        if self.current_screen:
            return self.current_screen.on_touch_move(touch)
        return False

    def on_touch_up(self, touch):
        if self.transition.is_active:
            return False
        if self.current_screen:
            return self.current_screen.on_touch_up(touch)
        return False


class WatcherApp(MDApp):
    network_config = None

    def build(self):
        self.theme_cls.primary_palette = 'Gray'
        self.theme_cls.accent_palette = 'Gray'
        self.theme_cls.theme_style = 'Dark'

        sm = WatcherScreenManager()
        sm.add_widget(ConnectionListScreen(name='connections'))
        sm.add_widget(NewConnectionScreen(name='new_connection'))
        sm.add_widget(StatusScreen(name='status'))
        sm.add_widget(ConfigureScreen(name='configure'))
        sm.add_widget(ClientsScreen(name='clients'))
        sm.add_widget(StaticLeasesScreen(name='leases'))
        sm.add_widget(ConfigScreen(name='config'))
        sm.add_widget(VlanListScreen(name='vlan_list'))
        sm.add_widget(ConfigureStaticLeasesScreen(name='configure_static_leases'))
        sm.add_widget(ConfigureDhcpScreen(name='configure_dhcp'))
        sm.add_widget(ConfigureVlanScreen(name='configure_vlan'))
        sm.add_widget(VlanEditScreen(name='vlan_edit'))
        sm.add_widget(VpnStatusScreen(name='vpn_status'))
        sm.add_widget(VpnConfigListScreen(name='vpn_config_list'))
        sm.add_widget(VpnConfigEditScreen(name='vpn_config_edit'))
        return sm


if __name__ == '__main__':
    WatcherApp().run()
