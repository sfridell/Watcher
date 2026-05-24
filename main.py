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
from kivymd.uix.label import MDLabel
from kivymd.uix.dialog import MDDialog
from kivymd.uix.selectioncontrol import MDCheckbox
from kivymd.uix.button import MDRectangleFlatButton  # noqa: F401 - used in KV
from kivymd.uix.textfield import MDTextField  # noqa: F401 - used in KV
from kivymd.uix.toolbar import MDTopAppBar  # noqa: F401 - used in KV
from kivy.uix.spinner import Spinner  # noqa: F401 - used in KV
from kivy.uix.scrollview import ScrollView  # noqa: F401 - used in KV
from kivy.uix.floatlayout import FloatLayout  # noqa: F401 - used in KV
from kivy.metrics import dp
from kivy.properties import StringProperty, NumericProperty
from kivy.graphics import Color, Line, Ellipse, Triangle, InstructionGroup
from kivy.clock import Clock
import watcher
import connectiondb
from networkconfig import NetworkConfig

logging.getLogger('paramiko').setLevel(logging.WARNING)
logging.getLogger('invoke').setLevel(logging.WARNING)
logging.getLogger('fabric').setLevel(logging.WARNING)



DOUBLE_CLICK_TIMEOUT = 0.3
LONG_PRESS_TIMEOUT = 0.5
LINE_HIT_THRESHOLD = dp(15)
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
                btn = MDRaisedButton(text=conn_name,
                                     size_hint_y=None,
                                     size_hint_x=None,
                                     width=dp(280),
                                     height=dp(50),
                                     pos_hint={'center_x': 0.5})
                btn.bind(on_press=lambda instance, name=conn_name: self.select_connection(name))
                self.ids.button_layout.add_widget(btn)
        except Exception as e:
            self.show_error(f"Failed to load connections: {str(e)}")

    def select_connection(self, conn_name):
        self.manager.get_screen('menu').set_connection(conn_name)
        self.manager.current = 'menu'

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


class ConnectionMenuScreen(Screen):
    connection_name = StringProperty('')

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.toolbar.title = f'{conn_name}'

    def show_status(self):
        self.manager.get_screen('status').set_connection(self.connection_name)
        self.manager.current = 'status'

    def show_configure(self):
        self.manager.get_screen('configure').set_connection(self.connection_name)
        self.manager.current = 'configure'

    def go_back(self):
        self.manager.current = 'connections'


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

    def go_back(self):
        self.manager.current = 'menu'


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

    def go_back(self):
        self.manager.current = 'menu'


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

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.toolbar.title = f'VLAN Configuration - {conn_name}'

    def on_enter(self):
        app = MDApp.get_running_app()
        config = getattr(app, 'network_config', None)
        if config is not None:
            self.ids.status_label.text = 'Config loaded (from previous session)'
            self.ids.status_label.color = (0.2, 0.7, 0.2, 1)
        else:
            self.ids.status_label.text = 'No config loaded - take a snapshot first'
            self.ids.status_label.color = (0.8, 0.4, 0.1, 1)
        self.ids.result_text.text = ''

    def snapshot(self):
        try:
            output = watcher.process_command(['config', 'snapshot', '--connection', self.connection_name])
            data = output.getvalue()
            app = MDApp.get_running_app()
            app.network_config = NetworkConfig.from_dict(json.loads(data))
            self.ids.status_label.text = 'Config loaded from router'
            self.ids.status_label.color = (0.2, 0.7, 0.2, 1)
            self.ids.result_text.text = 'Snapshot loaded successfully.\n\n' + data
        except Exception as e:
            self.show_error(f"Snapshot failed: {str(e)}")

    def go_to_edit(self):
        app = MDApp.get_running_app()
        config = getattr(app, 'network_config', None)
        if config is None:
            self.show_error("No config loaded. Take a snapshot first.")
            return
        self.manager.get_screen('vlan_canvas').set_connection(self.connection_name)
        self.manager.current = 'vlan_canvas'

    def commit_changes(self):
        try:
            app = MDApp.get_running_app()
            config = getattr(app, 'network_config', None)
            if config is None:
                self.show_error("No config loaded. Take a snapshot first.")
                return
            errors = config.validate()
            if errors:
                self.ids.result_text.text = 'Validation errors:\n' + '\n'.join(f'  - {e}' for e in errors)
                self.show_error("Cannot apply - validation errors found")
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
                    self.ids.result_text.text = 'Changes applied successfully.'
                    self.ids.status_label.text = 'Committed to router'
                    self.ids.status_label.color = (0.2, 0.7, 0.2, 1)
                except Exception as e:
                    self.show_error(f"Apply failed: {str(e)}")
                    self.ids.result_text.text = f'Apply failed: {str(e)}'

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


class VlanCanvasScreen(Screen):
    connection_name = StringProperty('')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._circles = {}
        self._connections = {}
        self._canvas_group = None
        self._touch_data = {}

    def on_enter(self):
        self.refresh()

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.toolbar.title = f'VLANs - {conn_name}'

    def refresh(self):
        app = MDApp.get_running_app()
        config = getattr(app, 'network_config', None)
        if config is None:
            try:
                output = watcher.process_command(['config', 'snapshot', '--connection', self.connection_name])
                data = output.getvalue()
                config = NetworkConfig.from_dict(json.loads(data))
                app.network_config = config
            except Exception as e:
                self.show_error(f"Failed to load config: {str(e)}")
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
            self.show_error("No config loaded. Snapshot or load first.")
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
        if super().on_touch_down(touch):
            return True

        layout = self.ids.canvas_layout
        if not layout.collide_point(*touch.pos):
            return False

        local_x, local_y = layout.to_local(touch.pos[0], touch.pos[1])
        now = Clock.get_time()

        for vid, circle in self._circles.items():
            if circle.collide_point(local_x, local_y):
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
                target_key = ('line', a_id, b_id)
                if target_key in self._touch_data and now - self._touch_data[target_key] < DOUBLE_CLICK_TIMEOUT:
                    self._touch_data.pop(target_key, None)
                    self.cycle_connection(a_id, b_id)
                    return True

                long_press_event = Clock.schedule_once(
                    lambda dt, ka=a_id, kb=b_id: self._on_long_press(ka, kb),
                    LONG_PRESS_TIMEOUT,
                )
                self._touch_data[target_key] = now
                touch.grab(self)
                touch.ud['line_key'] = (a_id, b_id)
                touch.ud['long_press'] = long_press_event
                return True

        return False

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            if 'long_press' in touch.ud and touch.ud['long_press']:
                touch.ud['long_press'].cancel()
            touch.ungrab(self)
        return super().on_touch_up(touch)

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            if 'long_press' in touch.ud and touch.ud['long_press']:
                touch.ud['long_press'].cancel()
            touch.ungrab(self)
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
            self.manager.current = 'vlan_canvas'
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
                self.manager.current = 'vlan_canvas'

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
        self.manager.current = 'vlan_canvas'

    def show_error(self, message):
        ok_button = MDFlatButton(text='OK')
        dialog = MDDialog(
            title='Error',
            text=message,
            buttons=[ok_button]
        )
        ok_button.bind(on_press=lambda x: dialog.dismiss())
        dialog.open()


class WatcherApp(MDApp):
    network_config = None

    def build(self):
        self.theme_cls.primary_palette = 'Gray'
        self.theme_cls.accent_palette = 'Gray'
        self.theme_cls.theme_style = 'Dark'

        sm = ScreenManager()
        sm.add_widget(ConnectionListScreen(name='connections'))
        sm.add_widget(NewConnectionScreen(name='new_connection'))
        sm.add_widget(ConnectionMenuScreen(name='menu'))
        sm.add_widget(StatusScreen(name='status'))
        sm.add_widget(ConfigureScreen(name='configure'))
        sm.add_widget(ClientsScreen(name='clients'))
        sm.add_widget(StaticLeasesScreen(name='leases'))
        sm.add_widget(ConfigScreen(name='config'))
        sm.add_widget(VlanListScreen(name='vlan_list'))
        sm.add_widget(ConfigureStaticLeasesScreen(name='configure_static_leases'))
        sm.add_widget(ConfigureDhcpScreen(name='configure_dhcp'))
        sm.add_widget(ConfigureVlanScreen(name='configure_vlan'))
        sm.add_widget(VlanCanvasScreen(name='vlan_canvas'))
        sm.add_widget(VlanEditScreen(name='vlan_edit'))
        return sm


if __name__ == '__main__':
    WatcherApp().run()