"""Exercise a packaged Windows management UI without contacting any devices."""
import tkinter as tk
from pathlib import Path
from unittest.mock import patch, MagicMock
from cloud_pc.management_ui.windows_main import Window


class OfflineClient:
    base_url = 'http://127.0.0.1:19528'
    host_ip = '10.0.0.2'

    def activate(self): return {'ok': True}
    def release(self): return {'ok': True}
    def slaves(self): return {'ok': True, 'slaves': []}


def main():
    root = tk.Tk()
    root.withdraw()
    store = MagicMock()
    store.path = Path('management-smoke.json')
    store.load.return_value = {'role': 'slave', 'host_ip': '10.0.0.2'}
    recent = MagicMock()
    recent.load.return_value = None
    with patch.object(Window, 'client', return_value=OfflineClient()), patch.object(Window, 'tick'), \
            patch('cloud_pc.management_ui.windows_main.ConfigStore', return_value=store), \
            patch('cloud_pc.management_ui.windows_main.RecentDialogue', return_value=recent):
        window = Window(root)
        root.update()
        print(root.title(), flush=True)
        window.close()


if __name__ == '__main__':
    main()
