from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import threading
import ipaddress
from pathlib import Path
from typing import Iterable

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SHARED_DIR = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "shared_components"))
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)
_PROJECT_DIR = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", ".."))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from host.api_server import HostAPI
from host.connection import SlaveClient
from host.config import DEFAULT_SLAVE_PORT
from common.rpc import READ_OPERATIONS
from common.portable_config import ConfigStore
from common.release import __version__


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_slave_address(value: str) -> tuple[str, int]:
    text = value.strip()
    if not text:
        raise ValueError("empty slave address")
    if ":" in text:
        host, port_text = text.rsplit(":", 1)
        return host.strip(), int(port_text)
    return text, DEFAULT_SLAVE_PORT


def create_clients(addresses: Iterable[str]) -> list[SlaveClient]:
    clients: list[SlaveClient] = []
    for address in addresses:
        host, port = parse_slave_address(address)
        clients.append(SlaveClient(host, port))
    return clients


def start_async_runtime(loop: asyncio.AbstractEventLoop,
                        api: HostAPI,
                        clients: list[SlaveClient]) -> threading.Thread:
    def runner() -> None:
        asyncio.set_event_loop(loop)
        api.set_loop(loop)
        for client in clients:
            client.set_loop(loop)
            api.register_client(client)
            loop.create_task(client.run())
        loop.create_task(api.start())
        loop.run_forever()

        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()

    thread = threading.Thread(target=runner, name="cloud-host-async", daemon=True)
    thread.start()
    return thread


def shutdown_async_runtime(loop: asyncio.AbstractEventLoop,
                           clients: list[SlaveClient],
                           async_thread: threading.Thread, api: HostAPI | None = None) -> None:
    async def stop_all() -> None:
        if api:
            await api.close()
        await asyncio.gather(*(client.stop() for client in clients), return_exceptions=True)

    if loop.is_running():
        future = asyncio.run_coroutine_threadsafe(stop_all(), loop)
        try:
            future.result(timeout=5)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
    async_thread.join(timeout=5)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cloud PC Host (Windows Desktop)")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--headless", action="store_true",
                        help="Run the host service without a management window or PySide6.")
    parser.add_argument("--management-bind", default="0.0.0.0", help="CLDC management registration listener (configured slaves only)")
    parser.add_argument("--management-port", type=int, default=19526)
    parser.add_argument("--management-interval", type=float, default=5)
    parser.add_argument("--management-state", default=str(Path.home() / ".local/state/cloud_pc/management.json"))
    parser.add_argument("--llm-config", help="主机本地 LLM API 配置 JSON（模型和登录由用户预先设置）")
    parser.add_argument("--current-slave", action="append", default=[],
                        help="Android ip[:port] for Codex text/SEND/STOP; networking is owned by the host.")
    parser.add_argument("--relay-grant", action="append", default=[],
                        help="Explicit relay grant: source_ip,target_ip,file.list or file.stat")
    parser.add_argument(
        "--slave",
        action="append",
        default=[],
        help="Optional slave address, format ip[:port]. Can be repeated.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return parser


def parse_relay_grant(value: str) -> tuple[str, str, str]:
    source, target, operation = (part.strip() for part in value.split(","))
    if operation not in READ_OPERATIONS:
        raise ValueError("Unsupported relay operation")
    return str(ipaddress.ip_address(source)), str(ipaddress.ip_address(target)), operation


def main() -> int:
    parser = build_arg_parser()
    parser.add_argument('--config', help='host.json 路径；默认程序同目录')
    args = parser.parse_args()
    try:
        store = ConfigStore('host', args.config)
        settings = store.load()
        mapping = {'slave': 'slaves', 'current_slave': 'current_slaves', 'relay_grant': 'relay_grants',
                   'headless': 'headless', 'management_bind': 'management_bind',
                   'management_port': 'management_port', 'management_interval': 'management_interval',
                   'log_level': 'log_level'}
        changed = {}
        for option, key in mapping.items():
            flag = '--' + option.replace('_', '-')
            if any(arg == flag or arg.startswith(flag + '=') for arg in sys.argv[1:]):
                changed[key] = getattr(args, option)
            else: setattr(args, option, settings[key])
        if changed: settings = store.update(changed)
        if not args.llm_config: args.llm_config = str(store.path)
    except (ValueError, OSError) as error: parser.error(str(error))
    setup_logging(args.log_level)
    logging.getLogger('host').info('配置文件：%s', store.path)

    addresses = list(dict.fromkeys([*args.slave, *args.current_slave]))
    clients = create_clients(addresses)
    try:
        grants = [parse_relay_grant(value) for value in args.relay_grant]
    except ValueError as exc:
        parser.error(str(exc))
    api = HostAPI(relay_grants=grants, management_bind=args.management_bind,
                  management_port=args.management_port, management_interval=args.management_interval,
                  management_state=args.management_state, llm_config=args.llm_config, config_store=store)
    current_addresses = {parse_slave_address(address) for address in args.current_slave}
    for client in clients:
        if (client.host_ip, client.port) in current_addresses:
            api.enable_android_current(client)
    loop = asyncio.new_event_loop()
    async_thread = start_async_runtime(loop, api, clients)

    if args.headless:
        try:
            while async_thread.is_alive():
                async_thread.join(timeout=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            shutdown_async_runtime(loop, clients, async_thread, api)
        return 0

    from PySide6.QtWidgets import QApplication
    from host.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationVersion(__version__)
    window = MainWindow(clients, on_add_slave=api.register_client)
    window._loop_ref = loop
    window.show()

    exit_code = app.exec()
    shutdown_async_runtime(loop, list(api._clients.values()), async_thread, api)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
