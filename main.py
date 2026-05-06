"""
Kivy mobile UI for Watcher - Network Router Monitor
"""
import json
import logging
from kivy.app import App
from kivy.lang import Builder
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.metrics import dp
from kivy.properties import StringProperty
import watcher
import connectiondb
from networkconfig import NetworkConfig

logging.getLogger('paramiko').setLevel(logging.WARNING)
logging.getLogger('invoke').setLevel(logging.WARNING)
logging.getLogger('fabric').setLevel(logging.WARNING)

Builder.load_file('watcher.kv')


class ConnectionListScreen(Screen):
    def on_enter(self):
        self.load_connections()

    def load_connections(self):
        try:
            self.ids.button_layout.clear_widgets()
            db = connectiondb.ConnectionDB()
            connections = list(db.connections.keys())
            for conn_name in connections:
                btn = Button(text=conn_name,
                             size_hint_y=None,
                             height=dp(50),
                             font_size='18sp')
                btn.bind(on_press=lambda instance, name=conn_name: self.select_connection(name))
                self.ids.button_layout.add_widget(btn)
        except Exception as e:
            self.show_error(f"Failed to load connections: {str(e)}")

    def select_connection(self, conn_name):
        self.manager.get_screen('menu').set_connection(conn_name)
        self.manager.current = 'menu'

    def show_error(self, message):
        popup = Popup(title='Error',
                      content=Label(text=message),
                      size_hint=(0.8, 0.3))
        popup.open()


class ConnectionMenuScreen(Screen):
    connection_name = StringProperty('')

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.title_label.text = f'Selected: {conn_name}'

    def show_clients(self):
        self.manager.get_screen('clients').set_connection(self.connection_name)
        self.manager.current = 'clients'

    def show_leases(self):
        self.manager.get_screen('leases').set_connection(self.connection_name)
        self.manager.current = 'leases'

    def show_config(self):
        self.manager.get_screen('config').set_connection(self.connection_name)
        self.manager.current = 'config'

    def show_vlan_list(self):
        self.manager.get_screen('vlan_list').set_connection(self.connection_name)
        self.manager.current = 'vlan_list'

    def show_config_actions(self):
        self.manager.get_screen('config_actions').set_connection(self.connection_name)
        self.manager.current = 'config_actions'

    def go_back(self):
        self.manager.current = 'connections'


class ClientsScreen(Screen):
    connection_name = StringProperty('')

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.title_label.text = f'DHCP Clients - {conn_name}'

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
                        row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(2))
                        row.add_widget(Label(text=parts[1], size_hint_x=0.35, font_size='12sp'))
                        row.add_widget(Label(text=parts[2], size_hint_x=0.30, font_size='12sp'))
                        row.add_widget(Label(text=parts[3], size_hint_x=0.35, font_size='12sp'))
                        self.ids.data_layout.add_widget(row)
        except Exception as e:
            self.show_error(f"Failed to load clients: {str(e)}")

    def go_back(self):
        self.manager.current = 'menu'

    def show_error(self, message):
        popup = Popup(title='Error',
                      content=Label(text=message),
                      size_hint=(0.8, 0.3))
        popup.open()


class StaticLeasesScreen(Screen):
    connection_name = StringProperty('')

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.title_label.text = f'Static Leases - {conn_name}'

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
                        row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(2))
                        row.add_widget(Label(text=parts[0], size_hint_x=0.35, font_size='12sp'))
                        row.add_widget(Label(text=parts[1], size_hint_x=0.35, font_size='12sp'))
                        row.add_widget(Label(text=parts[2], size_hint_x=0.30, font_size='12sp'))
                        self.ids.data_layout.add_widget(row)
        except Exception as e:
            self.show_error(f"Failed to load leases: {str(e)}")

    def go_back(self):
        self.manager.current = 'menu'

    def show_error(self, message):
        popup = Popup(title='Error',
                      content=Label(text=message),
                      size_hint=(0.8, 0.3))
        popup.open()


class ConfigScreen(Screen):
    connection_name = StringProperty('')

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.title_label.text = f'Router Config - {conn_name}'

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
        self.manager.current = 'menu'

    def show_error(self, message):
        popup = Popup(title='Error',
                      content=Label(text=message),
                      size_hint=(0.8, 0.3))
        popup.open()


class VlanListScreen(Screen):
    connection_name = StringProperty('')

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.title_label.text = f'VLANs - {conn_name}'

    def on_enter(self):
        self.load_data()

    def load_data(self):
        try:
            self.ids.data_layout.clear_widgets()
            app = App.get_running_app()
            config = getattr(app, 'network_config', None)
            if config is None:
                output = watcher.process_command(['config', 'snapshot', '--connection', self.connection_name])
                data = output.getvalue()
                config = NetworkConfig.from_dict(json.loads(data))
            vlans = config.network.get("vlans", {})
            for vlan_name in sorted(vlans.keys()):
                vlan_data = vlans[vlan_name]
                members = ",".join(vlan_data.get("members", []))
                row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(2))
                row.add_widget(Label(text=vlan_name, size_hint_x=0.20, font_size='12sp'))
                row.add_widget(Label(text=vlan_data.get("ip", ""), size_hint_x=0.25, font_size='12sp'))
                row.add_widget(Label(text="B" if vlan_data.get("bridged") else "-", size_hint_x=0.10, font_size='12sp'))
                row.add_widget(Label(text="N" if vlan_data.get("nat") else "-", size_hint_x=0.10, font_size='12sp'))
                row.add_widget(Label(text=members, size_hint_x=0.35, font_size='12sp'))
                self.ids.data_layout.add_widget(row)
        except Exception as e:
            self.show_error(f"Failed to load VLANs: {str(e)}")

    def add_vlan(self):
        self.manager.get_screen('vlan_edit').set_connection(self.connection_name)
        self.manager.get_screen('vlan_edit').new_vlan()
        self.manager.current = 'vlan_edit'

    def go_back(self):
        self.manager.current = 'menu'

    def show_error(self, message):
        popup = Popup(title='Error',
                      content=Label(text=message),
                      size_hint=(0.8, 0.3))
        popup.open()


class VlanEditScreen(Screen):
    connection_name = StringProperty('')
    _editing_vlan = None

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.title_label.text = f'Edit VLAN - {conn_name}'

    def new_vlan(self):
        self._editing_vlan = None
        self.ids.title_label.text = 'New VLAN'
        self.ids.vlan_id.text = ''
        self.ids.vlan_ip.text = '0.0.0.0'
        self.ids.vlan_netmask.text = '255.255.255.0'
        self.ids.vlan_bridged.active = False
        self.ids.vlan_nat.active = False
        self.ids.vlan_dhcp_enabled.active = False
        self.ids.vlan_dhcp_start.text = '0'
        self.ids.vlan_dhcp_size.text = '0'
        self.ids.vlan_dhcp_lease.text = '0'

    def save_vlan(self):
        try:
            app = App.get_running_app()
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
            self.manager.current = 'vlan_list'
        except Exception as e:
            self.show_error(f"Failed to save VLAN: {str(e)}")

    def go_back(self):
        self.manager.current = 'vlan_list'

    def show_error(self, message):
        popup = Popup(title='Error',
                      content=Label(text=message),
                      size_hint=(0.8, 0.3))
        popup.open()


class ConfigActionsScreen(Screen):
    connection_name = StringProperty('')

    def set_connection(self, conn_name):
        self.connection_name = conn_name
        self.ids.title_label.text = f'Config Actions - {conn_name}'

    def on_enter(self):
        self.ids.result_text.text = ''

    def snapshot(self):
        try:
            output = watcher.process_command(['config', 'snapshot', '--connection', self.connection_name])
            data = output.getvalue()
            app = App.get_running_app()
            app.network_config = NetworkConfig.from_dict(json.loads(data))
            self.ids.result_text.text = 'Snapshot loaded successfully.\n\n' + data
        except Exception as e:
            self.show_error(f"Snapshot failed: {str(e)}")

    def load_file(self):
        try:
            file_path = self.ids.file_path.text
            if not file_path:
                self.show_error("Please enter a file path")
                return
            config = NetworkConfig.from_json_file(file_path)
            app = App.get_running_app()
            app.network_config = config
            self.ids.result_text.text = 'Config loaded from file.\n\n' + config.to_json()
        except Exception as e:
            self.show_error(f"Load failed: {str(e)}")

    def save_file(self):
        try:
            file_path = self.ids.file_path.text
            if not file_path:
                self.show_error("Please enter a file path")
                return
            app = App.get_running_app()
            config = getattr(app, 'network_config', None)
            if config is None:
                self.show_error("No config loaded. Snapshot or load first.")
                return
            config.to_json_file(file_path)
            self.ids.result_text.text = f'Config saved to {file_path}'
        except Exception as e:
            self.show_error(f"Save failed: {str(e)}")

    def validate(self):
        try:
            app = App.get_running_app()
            config = getattr(app, 'network_config', None)
            if config is None:
                self.show_error("No config loaded. Snapshot or load first.")
                return
            errors = config.validate()
            if errors:
                self.ids.result_text.text = 'Validation errors:\n' + '\n'.join(f'  - {e}' for e in errors)
            else:
                self.ids.result_text.text = 'Configuration is valid.'
        except Exception as e:
            self.show_error(f"Validation failed: {str(e)}")

    def diff_router(self):
        try:
            app = App.get_running_app()
            config = getattr(app, 'network_config', None)
            if config is None:
                self.show_error("No config loaded. Snapshot or load first.")
                return
            import tempfile
            import os
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                f.write(config.to_json())
                temp_path = f.name
            try:
                output = watcher.process_command(['config', 'diff', '--connection', self.connection_name, '--file', temp_path])
                self.ids.result_text.text = output.getvalue()
            finally:
                os.unlink(temp_path)
        except Exception as e:
            self.show_error(f"Diff failed: {str(e)}")

    def apply_config(self):
        try:
            app = App.get_running_app()
            config = getattr(app, 'network_config', None)
            if config is None:
                self.show_error("No config loaded. Snapshot or load first.")
                return
            errors = config.validate()
            if errors:
                self.show_error("Cannot apply - validation errors:\n" + '\n'.join(errors))
                return
            import tempfile
            import os
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                f.write(config.to_json())
                temp_path = f.name
            try:
                mode = self.ids.apply_mode.text if hasattr(self.ids, 'apply_mode') else 'diff'
                output = watcher.process_command(['config', 'apply', '--connection', self.connection_name, '--file', temp_path, '--mode', mode])
                self.ids.result_text.text = output.getvalue()
            finally:
                os.unlink(temp_path)
        except Exception as e:
            self.show_error(f"Apply failed: {str(e)}")

    def verify_config(self):
        try:
            app = App.get_running_app()
            config = getattr(app, 'network_config', None)
            if config is None:
                self.show_error("No config loaded. Snapshot or load first.")
                return
            import tempfile
            import os
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                f.write(config.to_json())
                temp_path = f.name
            try:
                output = watcher.process_command(['config', 'verify', '--connection', self.connection_name, '--file', temp_path])
                self.ids.result_text.text = output.getvalue()
            finally:
                os.unlink(temp_path)
        except Exception as e:
            self.show_error(f"Verify failed: {str(e)}")

    def go_back(self):
        self.manager.current = 'menu'

    def show_error(self, message):
        popup = Popup(title='Error',
                      content=Label(text=message),
                      size_hint=(0.8, 0.3))
        popup.open()


class WatcherApp(App):
    network_config = None

    def build(self):
        sm = ScreenManager()
        sm.add_widget(ConnectionListScreen(name='connections'))
        sm.add_widget(ConnectionMenuScreen(name='menu'))
        sm.add_widget(ClientsScreen(name='clients'))
        sm.add_widget(StaticLeasesScreen(name='leases'))
        sm.add_widget(ConfigScreen(name='config'))
        sm.add_widget(VlanListScreen(name='vlan_list'))
        sm.add_widget(VlanEditScreen(name='vlan_edit'))
        sm.add_widget(ConfigActionsScreen(name='config_actions'))
        return sm


if __name__ == '__main__':
    WatcherApp().run()
