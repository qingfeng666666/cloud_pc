"""
协议打包/解包测试
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.protocol import (
    pack_message, unpack_message, unpack_header,
    HEADER_SIZE, MAGIC, VERSION,
    TYPE_HANDSHAKE_REQ, TYPE_LIST_DIR, TYPE_EXEC,
    TYPE_LIST_DIR_RESULT,
)


def test_pack_unpack():
    """测试打包 → 解包"""
    payload = {"slave_name": "TestPC", "version": 1}
    data = pack_message(TYPE_HANDSHAKE_REQ, payload)

    # 验证头部
    assert data[:4] == MAGIC
    version, msg_type, length = unpack_header(data[:HEADER_SIZE])
    assert version == VERSION
    assert msg_type == TYPE_HANDSHAKE_REQ

    # 验证完整解包
    msg_type, result = unpack_message(data)
    assert msg_type == TYPE_HANDSHAKE_REQ
    assert result["slave_name"] == "TestPC"
    print("[OK] pack/unpack")


def test_empty_payload():
    """测试空 payload"""
    data = pack_message(TYPE_LIST_DIR, {})
    msg_type, result = unpack_message(data)
    assert msg_type == TYPE_LIST_DIR
    assert result == {}
    print("[OK] empty payload")


def test_unicode_payload():
    """测试中文 payload"""
    payload = {"path": "/项目/场景", "name": "示例文件.txt"}
    data = pack_message(TYPE_EXEC, payload)
    msg_type, result = unpack_message(data)
    assert result["path"] == "/项目/场景"
    assert result["name"] == "示例文件.txt"
    print("[OK] unicode payload")


def test_large_payload():
    """测试大数据 payload"""
    payload = {"data": "A" * 100000}  # 100KB
    data = pack_message(TYPE_LIST_DIR_RESULT, payload)
    msg_type, result = unpack_message(data)
    assert len(result["data"]) == 100000
    print(f"[OK] large payload ({len(data)} bytes)")


if __name__ == "__main__":
    test_pack_unpack()
    test_empty_payload()
    test_unicode_payload()
    test_large_payload()
    print("\nAll protocol tests passed!")
