"""
从机入口 — 静默监听 TCP，等待主机连接

用法:
  python -m slave.main
  python -m slave.main --root D:\shared --port 9527 --name MyServer

环境变量:
  CLOUD_ROOT_DIR   共享根目录 (默认: 用户目录)
  CLOUD_SLAVE_NAME 从机名称 (默认: 主机名)
"""

import argparse
import importlib
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.release import __version__


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main():
    from common.portable_config import ConfigStore
    parser = argparse.ArgumentParser(description="云电脑 — 从机")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--root", default=None, help="共享根目录")
    parser.add_argument("--port", type=int, default=9527, help="监听端口 (默认: 9527)")
    parser.add_argument("--name", default=None, help="从机名称")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument('--config', help='slave.json 路径；默认程序同目录')
    args = parser.parse_args()
    try:
        store = ConfigStore('slave', args.config)
        settings = store.load()
        changed = {}
        for key in ('root', 'port', 'name', 'log_level'):
            flag = '--' + key.replace('_', '-')
            if any(arg == flag or arg.startswith(flag + '=') for arg in sys.argv[1:]): changed[key] = getattr(args, key)
            else: setattr(args, key, settings[key])
        if changed: store.update(changed)
        root = Path(args.root).expanduser()
        args.root = str(root if root.is_absolute() else store.path.parent / root)
    except (ValueError, OSError) as error: parser.error(str(error))

    setup_logging(args.log_level)
    logging.getLogger('slave').info('配置文件：%s', store.path)

    if args.root:
        os.environ["CLOUD_ROOT_DIR"] = args.root
    if args.name:
        os.environ["CLOUD_SLAVE_NAME"] = args.name

    # These imports must happen only after --root/--name have populated the
    # environment.  slave.config evaluates ROOT_DIR and SLAVE_NAME at import
    # time, and SlaveServer captures ROOT_DIR when it creates FileService and
    # RuntimeSnapshotCapture.
    import slave.config as cfg
    # Be defensive for PyInstaller builds: a hidden import can have loaded
    # slave.config before this function received --root/--name.  Reloading
    # makes configuration reflect the CLI environment unconditionally.
    cfg = importlib.reload(cfg)
    from slave.config import SLAVE_NAME, ROOT_DIR, LISTEN_PORT
    from slave.firewall import close_port, open_port
    from slave.connection import SlaveServer
    cfg.LISTEN_PORT = args.port

    logger = logging.getLogger("slave")

    # exe 控制台默认隐藏：日志同时写入 {root}/slave.log，便于排查音频/连接问题。
    try:
        log_file = os.path.join(cfg.ROOT_DIR, "slave.log")
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
        logging.getLogger().addHandler(file_handler)
        logger.info("File log enabled: %s", log_file)
    except Exception as exc:  # noqa: BLE001 - 日志失败不影响从机运行
        logger.warning("File log not enabled: %s", exc)

    # 自动开放防火墙端口
    open_port(args.port)
    logger.info("=" * 50)
    logger.info(f"Cloud PC Slave {__version__} — {SLAVE_NAME}")
    logger.info(f"  Root: {ROOT_DIR}")
    logger.info(f"  Listen: TCP:{args.port}")
    logger.info("=" * 50)

    server = SlaveServer()

    async def run():
        shutdown_requested = False
        try:
            shutdown_requested = await server.start()
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt...")
        finally:
            await server.stop()
            if shutdown_requested:
                close_port(args.port)
                logger.info("Shutdown complete — firewall rule removed")

    asyncio.run(run())


if __name__ == "__main__":
    main()
