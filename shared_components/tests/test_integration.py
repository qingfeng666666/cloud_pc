"""
集成测试 — 启动主机 + 从机在同一进程中，验证发现和连接流程

用法:
  python tests/test_integration.py
"""

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.protocol import (
    pack_message, unpack_message, MessageReader,
    TYPE_HANDSHAKE_REQ, TYPE_HANDSHAKE_ACK,
    TYPE_LIST_DIR, TYPE_LIST_DIR_RESULT,
    TYPE_DOWNLOAD, TYPE_DOWNLOAD_CHUNK, TYPE_DOWNLOAD_COMPLETE,
    TYPE_EXEC, TYPE_TASK_OUTPUT, TYPE_TASK_STATUS,
    TYPE_HEARTBEAT,
)


async def test_discovery():
    """测试设备发现流程"""
    import socket
    import json
    from common.discovery import (
        create_broadcast_socket, create_listen_socket,
        send_discover, send_offer, parse_discovery_packet,
        DISCOVERY_MAGIC,
    )

    # 启动主机 UDP 监听
    host_sock = create_listen_socket(19999)
    host_sock.settimeout(0.5)

    # 从机广播
    slave_sock = create_broadcast_socket()
    send_discover(slave_sock, "TestSlave", 19999)

    # 主机接收
    try:
        data, addr = host_sock.recvfrom(4096)
        info = parse_discovery_packet(data)
        assert info is not None, "Failed to parse discovery packet"
        assert info["type"] == "discover"
        assert info["slave_name"] == "TestSlave"
        print("[OK] Discovery: host received broadcast")

        # 主机单播回复
        send_offer(host_sock, addr, "TestHost", 9000)

        # 从机接收回复
        slave_sock.settimeout(0.5)
        data2, addr2 = slave_sock.recvfrom(4096)
        info2 = parse_discovery_packet(data2)
        assert info2 is not None
        assert info2["type"] == "offer"
        assert info2["tcp_port"] == 9000
        print("[OK] Discovery: slave received offer")

    finally:
        host_sock.close()
        slave_sock.close()


async def test_handshake():
    """测试 TCP 握手流程"""
    # 启动主机 TCP 服务器
    host_server = await asyncio.start_server(
        lambda r, w: _handle_handshake_test(r, w),
        "127.0.0.1", 9001,
    )

    # 从机连接
    reader, writer = await asyncio.open_connection("127.0.0.1", 9001)

    # 发送握手请求
    writer.write(pack_message(TYPE_HANDSHAKE_REQ, {"slave_name": "TestSlave"}))
    await writer.drain()

    # 接收握手确认
    msg_reader = MessageReader(reader)
    msg_type, payload = await msg_reader.read_one()
    assert msg_type == TYPE_HANDSHAKE_ACK
    assert payload.get("host_name") == "TestHost"
    print("[OK] Handshake: connection established")

    writer.close()
    host_server.close()
    await host_server.wait_closed()


async def _handle_handshake_test(reader, writer):
    msg_reader = MessageReader(reader)
    msg_type, payload = await msg_reader.read_one()
    assert msg_type == TYPE_HANDSHAKE_REQ
    print(f"[OK] Handshake: host received request from {payload['slave_name']}")

    # 发送确认
    writer.write(pack_message(TYPE_HANDSHAKE_ACK, {
        "host_name": "TestHost",
        "message": "connected",
    }))
    await writer.drain()


async def test_file_list():
    """测试文件列表请求/响应"""
    host_server = await asyncio.start_server(
        lambda r, w: _handle_file_list_test(r, w),
        "127.0.0.1", 9002,
    )

    reader, writer = await asyncio.open_connection("127.0.0.1", 9002)

    # 请求列出目录
    writer.write(pack_message(TYPE_LIST_DIR, {"path": "/"}))
    await writer.drain()

    # 接收响应
    msg_reader = MessageReader(reader)
    msg_type, payload = await msg_reader.read_one()
    assert msg_type == TYPE_LIST_DIR_RESULT
    assert "entries" in payload
    print(f"[OK] File list: got {len(payload['entries'])} entries")

    writer.close()
    host_server.close()
    await host_server.wait_closed()


async def _handle_file_list_test(reader, writer):
    msg_reader = MessageReader(reader)
    msg_type, payload = await msg_reader.read_one()
    assert msg_type == TYPE_LIST_DIR

    # 模拟文件列表响应
    entries = [
        {"name": "test.txt", "size": 1024, "type": "file", "mtime": 1717200000},
        {"name": "subdir", "size": 0, "type": "dir", "mtime": 1717200000},
    ]
    writer.write(pack_message(TYPE_LIST_DIR_RESULT, {
        "path": payload["path"],
        "entries": entries,
    }))
    await writer.drain()


async def test_task_exec():
    """测试任务执行（模拟）"""
    host_server = await asyncio.start_server(
        lambda r, w: _handle_task_test(r, w),
        "127.0.0.1", 9003,
    )

    reader, writer = await asyncio.open_connection("127.0.0.1", 9003)

    # 执行命令
    writer.write(pack_message(TYPE_EXEC, {
        "task_id": "test_001",
        "cmd": "echo",
        "args": ["hello", "world"],
        "cwd": "/tmp",
    }))
    await writer.drain()

    # 接收输出
    msg_reader = MessageReader(reader)
    outputs = []
    final_status = None

    while True:
        msg_type, payload = await msg_reader.read_one()
        if msg_type == TYPE_TASK_OUTPUT:
            outputs.append(payload)
        elif msg_type == TYPE_TASK_STATUS:
            final_status = payload
            if payload.get("status") != "running":
                break

    assert final_status is not None
    assert final_status["status"] == "completed"
    assert final_status["exit_code"] == 0
    print(f"[OK] Task exec: {len(outputs)} output lines, exit_code=0")

    writer.close()
    host_server.close()
    await host_server.wait_closed()


async def _handle_task_test(reader, writer):
    msg_reader = MessageReader(reader)
    msg_type, payload = await msg_reader.read_one()
    assert msg_type == TYPE_EXEC
    assert payload["cmd"] == "echo"

    # 模拟输出
    writer.write(pack_message(TYPE_TASK_OUTPUT, {
        "task_id": payload["task_id"],
        "stream": "stdout",
        "text": "hello world\n",
    }))
    await writer.drain()

    writer.write(pack_message(TYPE_TASK_STATUS, {
        "task_id": payload["task_id"],
        "status": "completed",
        "exit_code": 0,
    }))
    await writer.drain()


async def main():
    print("=" * 40)
    print("Cloud PC Integration Tests")
    print("=" * 40)

    tests = [
        ("Discovery", test_discovery),
        ("Handshake", test_handshake),
        ("File List", test_file_list),
        ("Task Execution", test_task_exec),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        try:
            await test_fn()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("All tests passed!")
    else:
        print("Some tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
