"""
联调测试 — 主机 + 从机，逐步测试每个功能

用法:
  python tests/test_e2e.py
"""

import asyncio
import sys
import os
import tempfile
import base64

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 准备测试环境 ─────────────────────────────────────

tmpdir = tempfile.mkdtemp(prefix="cloud_test_")
os.makedirs(os.path.join(tmpdir, "subdir"), exist_ok=True)
with open(os.path.join(tmpdir, "hello.txt"), "w") as f:
    f.write("Hello Cloud PC!")
with open(os.path.join(tmpdir, "config.json"), "w") as f:
    f.write('{"version": 1}')
with open(os.path.join(tmpdir, "subdir", "nested.txt"), "w") as f:
    f.write("nested file")

os.environ["CLOUD_ROOT_DIR"] = tmpdir
os.environ["CLOUD_SLAVE_NAME"] = "TestSlave"

print(f"Test root: {tmpdir}", flush=True)

from common.protocol import (
    pack_message, MessageReader,
    TYPE_HANDSHAKE_REQ, TYPE_HANDSHAKE_ACK,
    TYPE_LIST_DIR, TYPE_LIST_DIR_RESULT,
    TYPE_DOWNLOAD, TYPE_DOWNLOAD_CHUNK, TYPE_DOWNLOAD_COMPLETE,
    TYPE_EXEC, TYPE_TASK_OUTPUT, TYPE_TASK_STATUS, TYPE_TASK_KILL,
    TYPE_DELETE, TYPE_MKDIR, TYPE_OPERATION_ACK,
    TYPE_ERROR, TYPE_DISCONNECT,
)

from slave.file_service import FileService
from slave.task_service import TaskService

# ── 辅助：从机消息分发 ────────────────────────────────

def make_slave_dispatch(file_svc: FileService, task_svc: TaskService):
    """创建一个消息分发函数，供从机使用"""

    async def dispatch(msg_type: int, payload: dict, send):
        if msg_type == TYPE_LIST_DIR:
            path = payload.get("path", "/")
            try:
                entries = file_svc.list_dir(path)
                await send(TYPE_LIST_DIR_RESULT, {"path": path, "entries": entries})
            except Exception as e:
                await send(TYPE_ERROR, {"code": "ERROR", "message": str(e)})

        elif msg_type == TYPE_DOWNLOAD:
            await file_svc.download(payload["path"], send)

        elif msg_type == TYPE_EXEC:
            task_id = payload.get("task_id", "")
            cmd = payload.get("cmd", "")
            args = payload.get("args", [])
            cwd = payload.get("cwd", "")
            await task_svc.exec(task_id, cmd, args, cwd, None, send)

        elif msg_type == TYPE_TASK_KILL:
            task_svc.kill(payload.get("task_id", ""))

        elif msg_type == TYPE_DELETE:
            try:
                file_svc.delete(payload["path"])
                await send(TYPE_OPERATION_ACK,
                           {"path": payload["path"], "operation": "delete", "status": "ok"})
            except Exception as e:
                await send(TYPE_ERROR, {"code": "ERROR", "message": str(e)})

        elif msg_type == TYPE_MKDIR:
            try:
                file_svc.mkdir(payload["path"])
                await send(TYPE_OPERATION_ACK,
                           {"path": payload["path"], "operation": "mkdir", "status": "ok"})
            except Exception as e:
                await send(TYPE_ERROR, {"code": "ERROR", "message": str(e)})

        elif msg_type == TYPE_HEARTBEAT:
            await send(TYPE_HEARTBEAT, payload)

    return dispatch


# ── 测试函数 ──────────────────────────────────────────

async def test_list_dir(send, recv):
    """测试列出目录"""
    print("\n--- List Directory ---", flush=True)
    await send(TYPE_LIST_DIR, {"path": "/"})
    msg_type, payload = await recv()
    assert msg_type == TYPE_LIST_DIR_RESULT, f"Expected LIST_DIR_RESULT, got 0x{msg_type:04X}"
    names = [e["name"] for e in payload["entries"]]
    print(f"  Root entries: {names}", flush=True)
    assert "hello.txt" in names
    assert "config.json" in names
    assert "subdir" in names

    # 子目录
    await send(TYPE_LIST_DIR, {"path": "/subdir"})
    msg_type, payload = await recv()
    names2 = [e["name"] for e in payload["entries"]]
    print(f"  Subdir entries: {names2}", flush=True)
    assert "nested.txt" in names2
    print("  [PASS]", flush=True)


async def test_download(send, recv):
    """测试下载文件"""
    print("\n--- Download File ---", flush=True)
    await send(TYPE_DOWNLOAD, {"path": "/hello.txt"})

    chunks_data = []
    while True:
        msg_type, payload = await recv()
        if msg_type == TYPE_DOWNLOAD_CHUNK:
            chunks_data.append(base64.b64decode(payload["data"]))
        elif msg_type == TYPE_DOWNLOAD_COMPLETE:
            break

    text = b"".join(chunks_data).decode("utf-8")
    print(f"  Content: {text}", flush=True)
    assert text == "Hello Cloud PC!"
    print("  [PASS]", flush=True)


async def test_exec(send, recv):
    """测试执行命令"""
    print("\n--- Execute Command ---", flush=True)
    await send(TYPE_EXEC, {
        "task_id": "task_001",
        "cmd": "python",
        "args": ["-c", "print('hello_stdout'); import sys; print('hello_stderr', file=sys.stderr)"],
        "cwd": tmpdir,
    })

    stdout_text = ""
    stderr_text = ""
    done = False
    while not done:
        msg_type, payload = await recv()
        if msg_type == TYPE_TASK_OUTPUT:
            if payload["stream"] == "stdout":
                stdout_text += payload["text"]
            else:
                stderr_text += payload["text"]
        elif msg_type == TYPE_TASK_STATUS:
            if payload["status"] != "running":
                done = True
                print(f"  stdout: {stdout_text.strip()}", flush=True)
                print(f"  stderr: {stderr_text.strip()}", flush=True)
                print(f"  exit_code: {payload['exit_code']}", flush=True)
                assert "hello_stdout" in stdout_text
                assert "hello_stderr" in stderr_text
                assert payload["exit_code"] == 0

    print("  [PASS]", flush=True)


async def test_delete_mkdir(send, recv):
    """测试新建文件夹 + 删除"""
    print("\n--- Mkdir + Delete ---", flush=True)

    # 新建文件夹
    await send(TYPE_MKDIR, {"path": "/test_new_dir"})
    msg_type, payload = await recv()
    assert msg_type == TYPE_OPERATION_ACK
    assert os.path.isdir(os.path.join(tmpdir, "test_new_dir"))
    print(f"  mkdir OK", flush=True)

    # 删除
    await send(TYPE_DELETE, {"path": "/test_new_dir"})
    msg_type, payload = await recv()
    assert msg_type == TYPE_OPERATION_ACK
    assert not os.path.exists(os.path.join(tmpdir, "test_new_dir"))
    print(f"  delete OK", flush=True)
    print("  [PASS]", flush=True)


async def test_path_safety(send, recv):
    """测试路径穿越防护"""
    print("\n--- Path Safety ---", flush=True)
    await send(TYPE_LIST_DIR, {"path": "/../../../Windows"})
    msg_type, payload = await recv()
    assert msg_type == TYPE_ERROR
    print(f"  Blocked: {payload.get('message', '')[:60]}", flush=True)
    print("  [PASS]", flush=True)


# ── 主测试流程 ────────────────────────────────────────

async def main():
    print("=" * 50, flush=True)
    print("Cloud PC E2E Test", flush=True)
    print("=" * 50, flush=True)

    # 创建从机服务
    file_svc = FileService(tmpdir)
    task_svc = TaskService()
    dispatch = make_slave_dispatch(file_svc, task_svc)

    # 启动主机服务器
    host_reader = None
    host_writer = None

    async def host_handler(reader, writer):
        nonlocal host_reader, host_writer
        host_reader = reader
        host_writer = writer

        # 握手
        mr = MessageReader(reader)
        msg_type, payload = await mr.read_one()
        assert msg_type == TYPE_HANDSHAKE_REQ
        print(f"[Host] Connected: {payload['slave_name']}", flush=True)

        writer.write(pack_message(TYPE_HANDSHAKE_ACK,
                     {"host_name": "TestHost", "message": "ok"}))
        await writer.drain()

        # 从机消息循环：处理主机发来的每条消息
        while True:
            try:
                msg_type, payload = await mr.read_one()
                if msg_type == TYPE_DISCONNECT:
                    break

                # 调用从机的消息分发
                async def slave_send(t, p):
                    writer.write(pack_message(t, p))
                    await writer.drain()

                await dispatch(msg_type, payload, slave_send)

            except (ConnectionError, OSError):
                break

    server = await asyncio.start_server(host_handler, "127.0.0.1", 9105)
    print(f"[Host] Listening on 127.0.0.1:9105", flush=True)

    # 从机连接主机
    slave_reader, slave_writer = await asyncio.open_connection("127.0.0.1", 9105)
    slave_writer.write(pack_message(TYPE_HANDSHAKE_REQ, {"slave_name": "TestSlave"}))
    await slave_writer.drain()

    # 读握手确认
    slave_mr = MessageReader(slave_reader)
    msg_type, payload = await slave_mr.read_one()
    assert msg_type == TYPE_HANDSHAKE_ACK
    print(f"[Slave] Connected", flush=True)

    # 等待 host_handler 设置 host_writer
    await asyncio.sleep(0.05)

    # ── 运行测试 ────────────────────────────────────

    # host → slave: 主机通过 slave_writer 发送，从 slave_reader 收响应
    async def send_to_slave(msg_type, payload=None):
        slave_writer.write(pack_message(msg_type, payload or {}))
        await slave_writer.drain()

    async def recv_from_slave():
        return await slave_mr.read_one()

    tests = [
        ("List Directory", test_list_dir),
        ("Download File", test_download),
        ("Execute Command", test_exec),
        ("Mkdir + Delete", test_delete_mkdir),
        ("Path Safety", test_path_safety),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        try:
            await test_fn(send_to_slave, recv_from_slave)
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}", flush=True)
            import traceback
            traceback.print_exc()
            failed += 1

    # ── 清理 ────────────────────────────────────────
    slave_writer.write(pack_message(TYPE_DISCONNECT, {}))
    await slave_writer.drain()
    slave_writer.close()
    server.close()
    await server.wait_closed()

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n{'='*50}", flush=True)
    print(f"Results: {passed} passed, {failed} failed", flush=True)
    if failed > 0:
        print("SOME TESTS FAILED!", flush=True)
        sys.exit(1)
    print("All E2E tests passed!", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
