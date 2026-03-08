"""
Kivy mobile UI for Watcher - Network Router Monitor
"""
import io
import logging
from kivy.app import App
from kivy.lang import Builder
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.metrics import dp
from kivy.properties import ObjectProperty, StringProperty
import watcher
import connectiondb

# Suppress Fabric debug logging
logging.getLogger('paramiko').setLevel(logging.WARNING)
logging.getLogger('invoke').setLevel(logging.WARNING)
logging.getLogger('fabric').setLevel(logging.WARNING)

# Load the kv file explicitly
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


class WatcherApp(App):
    def build(self):
        sm = ScreenManager()
        
        sm.add_widget(ConnectionListScreen(name='connections'))
        sm.add_widget(ConnectionMenuScreen(name='menu'))
        sm.add_widget(ClientsScreen(name='clients'))
        sm.add_widget(StaticLeasesScreen(name='leases'))
        sm.add_widget(ConfigScreen(name='config'))
        
        return sm


if __name__ == '__main__':
    WatcherApp().run()
