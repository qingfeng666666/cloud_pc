"""Cloud-PC file service with a separate read and mutation policy."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import shutil
import tempfile
from typing import Dict, List

logger = logging.getLogger("slave.file")


class FileService:
    """Read freely when explicitly requested; mutate only approved roots.

    ``CLOUD_MUTATION_ROOTS`` is an OS-path-separator delimited list. It
    defaults to ``CLOUD_ROOT_DIR``. Legacy upload/delete/rename/mkdir packet
    types are rejected so every mutation goes through :meth:`mutate`.
    """

    def __init__(self, root_dir: str):
        self.root_dir = os.path.normpath(os.path.abspath(root_dir))
        os.makedirs(self.root_dir, exist_ok=True)
        configured = os.environ.get("CLOUD_MUTATION_ROOTS", self.root_dir)
        roots = [item.strip() for item in configured.split(os.pathsep) if item.strip()]
        self.mutation_roots = tuple(os.path.realpath(os.path.abspath(item)) for item in roots)
        self._uploads: Dict[str, dict] = {}
        logger.info("FileService root: %s; mutation_roots=%s", self.root_dir, self.mutation_roots)

    @staticmethod
    def _inside(path: str, root: str) -> bool:
        try:
            return os.path.commonpath([path, root]) == root
        except ValueError:
            return False

    def _root_relative_path(self, path: str) -> str:
        value = str(path or "")
        if os.path.isabs(value):
            return os.path.normpath(os.path.abspath(value))
        return os.path.normpath(os.path.abspath(os.path.join(self.root_dir, value.lstrip("/\\"))))

    def _read_path(self, path: str) -> str:
        """Read/list/stat may use an absolute path, as requested by owner."""
        return self._root_relative_path(path)

    def _mutation_path(self, path: str) -> str:
        """Resolve a mutation target and reject traversal/junction escapes."""
        candidate = self._root_relative_path(path)
        parent = os.path.realpath(os.path.dirname(candidate) or self.root_dir)
        resolved = os.path.join(parent, os.path.basename(candidate))
        if not any(self._inside(resolved, root) for root in self.mutation_roots):
            raise PermissionError("mutation target is outside CLOUD_MUTATION_ROOTS")
        return resolved

    def list_dir(self, path: str = "/") -> List[dict]:
        full_path = self._read_path(path)
        if not os.path.isdir(full_path):
            raise FileNotFoundError("Not a directory: %s" % path)
        entries = []
        with os.scandir(full_path) as iterator:
            for entry in iterator:
                try:
                    stat = entry.stat()
                    entries.append({"name": entry.name, "size": stat.st_size,
                                    "type": "dir" if entry.is_dir() else "file",
                                    "mtime": int(stat.st_mtime)})
                except OSError:
                    pass
        entries.sort(key=lambda item: (item["type"] != "dir", item["name"].lower()))
        return entries

    async def download(self, path: str, send):
        full_path = self._read_path(path)
        if not os.path.isfile(full_path):
            raise FileNotFoundError("Not a file: %s" % path)
        total_size = os.path.getsize(full_path)
        chunk_size = 64 * 1024
        total = max(1, (total_size + chunk_size - 1) // chunk_size)
        with open(full_path, "rb") as handle:
            for index in range(total):
                data = handle.read(chunk_size)
                await send(0x0182, {"path": path, "chunk": index, "total": total,
                                    "size": len(data), "data": base64.b64encode(data).decode("ascii")})
        await send(0x0183, {"path": path, "total_chunks": total, "total_size": total_size})

    def file_stat(self, path: str) -> dict:
        full_path = self._read_path(path)
        stat = os.stat(full_path)
        return {"name": os.path.basename(full_path), "size": stat.st_size,
                "type": "dir" if os.path.isdir(full_path) else "file",
                "mtime": int(stat.st_mtime), "ctime": int(stat.st_ctime)}

    async def handle_upload_chunk(self, _msg: dict, _send):
        raise PermissionError("legacy TYPE_UPLOAD is disabled; use TYPE_FILE_MUTATE")

    def delete(self, _path: str) -> None:
        raise PermissionError("legacy TYPE_DELETE is disabled; use TYPE_FILE_MUTATE")

    def rename(self, _path: str, _new_name: str) -> str:
        raise PermissionError("legacy TYPE_RENAME is disabled; use TYPE_FILE_MUTATE")

    def mkdir(self, _path: str) -> None:
        raise PermissionError("legacy TYPE_MKDIR is disabled; use TYPE_FILE_MUTATE")

    async def mutate(self, msg: dict, send) -> None:
        """The sole remote file-mutation command, restricted to allowed roots."""
        operation = str(msg.get("operation", "") or "")
        path = str(msg.get("path", "") or "")
        if not path:
            raise ValueError("path is required")
        full_path = self._mutation_path(path)

        if operation == "write_chunk":
            await self._write_chunk(path, full_path, msg, send)
            return
        if operation == "mkdir":
            os.makedirs(full_path, exist_ok=False)
        elif operation == "delete":
            if os.path.isdir(full_path):
                shutil.rmtree(full_path)
            else:
                os.remove(full_path)
        elif operation == "rename":
            new_name = str(msg.get("new_name", "") or "")
            if not new_name or "/" in new_name or "\\" in new_name:
                raise ValueError("new_name must be one filename")
            os.rename(full_path, os.path.join(os.path.dirname(full_path), new_name))
        else:
            raise ValueError("unsupported mutation operation")
        logger.info("file_mutate operation=%s path=%s", operation, path)
        await send(0x0187, {"operation": operation, "path": path, "status": "ok"})

    async def _write_chunk(self, path: str, full_path: str, msg: dict, send) -> None:
        chunk = int(msg.get("chunk", 0) or 0)
        total = int(msg.get("total", 1) or 1)
        if total < 1 or chunk < 0 or chunk >= total:
            raise ValueError("invalid chunk range")
        data = base64.b64decode(str(msg.get("data", "") or ""), validate=True)
        if len(data) > 1024 * 1024:
            raise ValueError("mutation chunk exceeds 1 MiB")
        key = os.path.normcase(full_path)
        if chunk == 0:
            if key in self._uploads:
                self._uploads.pop(key)["f"].close()
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".cloud_mutate_", dir=os.path.dirname(full_path))
            self._uploads[key] = {"f": os.fdopen(fd, "wb"), "temporary": temporary,
                                  "target": full_path, "total": total, "got": 0,
                                  "sha256": str(msg.get("sha256", "") or "").lower()}
        state = self._uploads.get(key)
        if state is None or state["total"] != total or state["got"] != chunk:
            raise RuntimeError("unexpected mutation chunk order")
        state["f"].write(data)
        state["got"] += 1
        if state["got"] != total:
            return
        state["f"].close()
        with open(state["temporary"], "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        expected = state["sha256"]
        if expected and digest != expected:
            os.remove(state["temporary"])
            del self._uploads[key]
            raise ValueError("SHA-256 validation failed")
        os.replace(state["temporary"], state["target"])
        del self._uploads[key]
        logger.info("file_mutate operation=write path=%s sha256=%s", path, digest)
        await send(0x0187, {"operation": "write", "path": path, "status": "ok", "sha256": digest})
