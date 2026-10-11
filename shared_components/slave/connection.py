"""
从机连接管理 — 静默监听 TCP、接受主机连接、被动回复心跳
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import Callable

from common.protocol import (
    MessageReader, pack_message,
    TYPE_HANDSHAKE_REQ, TYPE_HANDSHAKE_ACK,
    TYPE_HEARTBEAT, TYPE_DISCONNECT,
    TYPE_LIST_DIR, TYPE_LIST_DIR_RESULT,
    TYPE_DOWNLOAD, TYPE_DOWNLOAD_CHUNK, TYPE_DOWNLOAD_COMPLETE,
    TYPE_UPLOAD, TYPE_UPLOAD_ACK,
    TYPE_DELETE, TYPE_RENAME, TYPE_MKDIR, TYPE_FILE_STAT, TYPE_FILE_MUTATE,
    TYPE_OPERATION_ACK, TYPE_FILE_STAT_RESULT,
    TYPE_EXEC, TYPE_TASK_OUTPUT, TYPE_TASK_STATUS, TYPE_TASK_KILL,
    TYPE_UE_MCP_CALL, TYPE_UE_MCP_RESULT,
    TYPE_DH_BRIDGE_CALL, TYPE_DH_BRIDGE_RESULT,
    TYPE_DH_STREAM_OPEN, TYPE_DH_STREAM_FRAME, TYPE_DH_STREAM_CLOSE,
    TYPE_DH_STREAM_RESULT,
    TYPE_DH_AUDIO_PLAY, TYPE_DH_AUDIO_RESULT,
    TYPE_DH_PERFORMANCE_PACKAGE, TYPE_DH_PERFORMANCE_RESULT,
    TYPE_RUNTIME_SNAPSHOT_CONFIG, TYPE_RUNTIME_SNAPSHOT_CONFIG_RESULT,
    TYPE_ERROR,
    TYPE_RPC_REQUEST, TYPE_RPC_RESULT, TYPE_RELAY_RESULT,
)
from common.rpc import RPC_CAPABILITY, RELAY_CAPABILITY, RelaySession, execute_read
from common.release import __version__
from slave.config import SLAVE_NAME, ROOT_DIR, LISTEN_PORT, HEARTBEAT_TIMEOUT
from slave.file_service import FileService
from slave.management_bridge import ManagementBridge, CAPABILITY as MANAGEMENT_CAPABILITY, PUSH_CAPABILITY
from slave.screen_capture import capture_screen_base64
from slave.task_service import TaskService
from slave.ue_mcp_service import call_ue_mcp
from slave.dh_bridge_service import call_dh_bridge, stream_dh_bridge, DigitalHumanPoseStream
from slave.audio_service import play_audio, play_pcm
from slave.runtime_capture import RuntimeSnapshotCapture

logger = logging.getLogger("slave")


class PerformancePlaybackQueue:
    """Immediate consumer for completed SentencePerformancePackage payloads.

    There is deliberately **no asyncio.Queue** here.  A package starts as
    soon as it arrives.  The host assigns an offset to every PCM hop in one
    utterance, so direct tasks still share one playback clock without a
    receiver-side FIFO.  Each payload is already complete on the host:
    ``pcm`` / ``body_package`` / ``face_frames`` are consumed as supplied.
    An omitted field is a no-op; the slave never waits for, synthesizes, or
    substitutes a missing modality. Packet ordering and interruption are host
    concerns: the slave does not interpret dialogue turns or cancel a prior
    packet just because another package arrives.
    """

    def __init__(self, on_avatar_playback: Callable[[bool], None] | None = None) -> None:
        self._stream = DigitalHumanPoseStream(timeout=10.0)
        # Do not create an asyncio primitive before the server loop is
        # running.  A packaged slave can be restarted/re-entered by its
        # launcher and an asyncio.Lock bound by an earlier loop then raises
        # ``Future attached to a different loop`` under concurrent packages.
        self._stream_lock: asyncio.Lock | None = None
        self._stream_loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task] = set()
        # The UE socket is shared by all performance packages belonging to
        # one host TCP connection.  Keep explicit ownership so an old host
        # disconnect cannot accidentally close a newer reconnect's stream.
        self._owner_connection_id: int | None = None
        self._on_avatar_playback = on_avatar_playback
        self._avatar_audio_tasks = 0

    def _set_avatar_audio_task(self, active: bool) -> None:
        """Reference-count direct packages that contain locally played audio."""
        if active:
            self._avatar_audio_tasks += 1
            if self._avatar_audio_tasks == 1 and self._on_avatar_playback:
                self._on_avatar_playback(True)
            return
        self._avatar_audio_tasks = max(0, self._avatar_audio_tasks - 1)
        if self._avatar_audio_tasks == 0 and self._on_avatar_playback:
            self._on_avatar_playback(False)

    def _get_stream_lock(self) -> asyncio.Lock:
        """Return the pose-stream guard owned by the current event loop.

        Playback remains direct: this guard only protects one shared UE TCP
        writer.  It is not a package FIFO and does not schedule audio.
        """
        loop = asyncio.get_running_loop()
        if self._stream_lock is None or self._stream_loop is not loop:
            if self._stream_loop is not None and self._stream_loop is not loop:
                # A writer created by a former loop cannot safely be reused.
                # Replace it; the old loop is already gone at this point.
                self._stream = DigitalHumanPoseStream(timeout=10.0)
                logger.warning("performance_pose_stream_recreated_for_event_loop")
            self._stream_loop = loop
            self._stream_lock = asyncio.Lock()
        return self._stream_lock

    async def submit(self, payload: dict) -> int:
        logger.info(
            "performance_package_received seq=%s body_frames=%s body_format=%s face_frames=%s pcm=%s host_submit_monotonic=%s at=%s",
            payload.get("package_sequence", "?"),
            len((payload.get("body_package") or {}).get("frames", [])),
            (payload.get("body_package") or {}).get("format", "metahuman"),
            len(payload.get("face_frames") or []),
            bool(payload.get("pcm")),
            payload.get("host_submit_monotonic"),
            datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
        )
        task = asyncio.create_task(self._run_one(payload), name="dh-performance-direct")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return len(self._tasks)

    async def close(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        self._tasks.clear()
        try:
            from slave.audio_service import interrupt_pcm_playback
            interrupt_pcm_playback()
        except Exception:  # noqa: BLE001
            pass
        async with self._get_stream_lock():
            await self._stream.close()

    async def reset_for_new_host_connection(self, host_name: str, connection_id: int) -> None:
        """Clear only tasks from a disconnected host connection."""
        cancelled = 0
        for task in tuple(self._tasks):
            if not task.done():
                task.cancel()
                cancelled += 1
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        self._tasks.clear()
        try:
            from slave.audio_service import interrupt_pcm_playback
            interrupt_pcm_playback()
        except Exception:  # noqa: BLE001 - audio fallback may be unavailable
            pass
        async with self._get_stream_lock():
            await self._stream.close()
        logger.info(
            "performance_connection_reset host=%s connection_id=%s cancelled_tasks=%s",
            host_name,
            connection_id,
            cancelled,
        )
        self._owner_connection_id = connection_id

    async def release_host_connection(self, host_name: str, connection_id: int) -> None:
        """Release UE stream when its owning performance sender disconnects.

        Without this, a JSON/action runtime can close its Cloud Slave TCP
        connection while the slave keeps the inner 127.0.0.1:55693 socket
        open.  DigitalHumanBridge handles one client at a time, so a later
        raw NPZ stream remains queued and never reaches UE.
        """
        if self._owner_connection_id != connection_id:
            logger.info(
                "performance_connection_release_ignored host=%s connection_id=%s owner=%s",
                host_name,
                connection_id,
                self._owner_connection_id,
            )
            return
        await self.close()
        self._owner_connection_id = None
        logger.info(
            "performance_connection_released host=%s connection_id=%s",
            host_name,
            connection_id,
        )

    async def _run_one(self, payload: dict) -> None:
        try:
            await self._play_one(payload)
        except asyncio.CancelledError:
            logger.info("performance_package_cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - a failed packet must not stop future packets
            logger.exception("performance_package_play_error error=%r", exc)

    async def _play_one(self, payload: dict) -> None:
        package_sequence = payload.get("package_sequence", "?")
        received_at = time.monotonic()
        wav = str(payload.get("wav", "") or "")
        pcm = str(payload.get("pcm", "") or "")
        pcm_rate = int(payload.get("pcm_rate", 0) or 0)
        pcm_channels = int(payload.get("pcm_channels", 1) or 1)
        pcm_format = str(payload.get("pcm_format", "f32le") or "f32le")
        start_delay_ms = max(0, int(payload.get("start_delay_ms", 0) or 0))
        face = payload.get("face_frames") or []
        body_package = payload.get("body_package") or {}
        body_frames = body_package.get("frames", []) if isinstance(body_package, dict) else []
        body_fps = float(body_package.get("fps", 30.0) or 30.0) if isinstance(body_package, dict) else 30.0
        body_format = str(body_package.get("format", "metahuman") or "metahuman") if isinstance(body_package, dict) else "metahuman"
        face_fps = float(payload.get("face_fps", 60.0) or 60.0)

        # Empty fields are explicitly no-ops.  Do not synthesize an identity
        # transform or a silent WAV because that would reset a live channel.
        if not (wav or pcm or face or body_frames):
            logger.info("performance_package_noop")
            return

        loop = asyncio.get_running_loop()
        if start_delay_ms:
            # This is a small jitter buffer applied only to the first
            # complete sentence-performance hop.  The queue continues to
            # receive later packages during the wait; audio, body and face
            # still start together immediately afterwards.
            logger.info("performance_package_start_buffer delay_ms=%s", start_delay_ms)
            await asyncio.sleep(start_delay_ms / 1000.0)
        start = loop.time()
        # A package is the synchronization boundary.  Start the consumers
        # together, but do not make one modality a prerequisite for another:
        # UE bridge trouble must not suppress already-prepared PCM, and a
        # missing Audio2Face/Body field remains an intentional no-op.
        audio_task: asyncio.Task | None = None
        if pcm:
            self._set_avatar_audio_task(True)
            audio_task = asyncio.create_task(
                play_pcm(pcm, pcm_rate, pcm_channels, pcm_format, 120.0),
                name=f"dh-pcm-{package_sequence}",
            )
        elif wav:
            self._set_avatar_audio_task(True)
            audio_task = asyncio.create_task(
                play_audio(wav, 0, 120.0), name=f"dh-wav-{package_sequence}"
            )
        visual_task: asyncio.Task | None = None
        if body_frames or face:
            visual_task = asyncio.create_task(
                self._forward_visual_frames(
                    body_frames, body_fps, body_format, face, face_fps, start,
                ),
                name=f"dh-visual-{package_sequence}",
            )
        logger.info(
            "performance_package_play_start seq=%s at=%s receive_to_start_seconds=%.4f body_frames=%s body_format=%s face_frames=%s wav=%s pcm=%s start_delay_ms=%s",
            package_sequence, datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3], loop.time() - received_at,
            len(body_frames), body_format, len(face), bool(wav), bool(pcm), start_delay_ms,
        )

        pending = [task for task in (audio_task, visual_task) if task is not None]
        try:
            if pending:
                await asyncio.gather(*pending)
        finally:
            if audio_task is not None:
                self._set_avatar_audio_task(False)
        logger.info("performance_package_play_done seq=%s elapsed=%.3f", package_sequence, loop.time() - start)

    async def _forward_visual_frames(
        self,
        body_frames: list,
        body_fps: float,
        body_format: str,
        face: list,
        face_fps: float,
        start: float,
    ) -> None:
        """Forward only the Body/Face data already present in one package.

        This is intentionally separate from PCM playback.  Body and Face do
        share the one UE bridge socket, so they are merged into a single UE
        frame when both are supplied; no default skeleton pose or facial curve
        is invented when either field is absent.
        """
        loop = asyncio.get_running_loop()
        async with self._get_stream_lock():
            await self._stream.open()

        if body_frames:
            interval = 1.0 / max(body_fps, 1.0)
            for index, body in enumerate(body_frames):
                if body_format == "smplx_axis_angle":
                    frame = {"smplx": body}
                else:
                    frame = {"body": body}
                if face:
                    face_index = min(len(face) - 1, int(index * face_fps / max(body_fps, 1.0)))
                    frame["face"] = face[face_index]
                async with self._get_stream_lock():
                    await self._stream.send_frame(frame)
                await asyncio.sleep(max(0.0, start + (index + 1) * interval - loop.time()))
            return

        # Face-only package: forward exactly its supplied curve frames.
        interval = 1.0 / max(face_fps, 1.0)
        for index, face_frame in enumerate(face):
            async with self._get_stream_lock():
                await self._stream.send_frame({"face": face_frame})
            await asyncio.sleep(max(0.0, start + (index + 1) * interval - loop.time()))


class SlaveServer:
    """从机服务器：监听 TCP，接受主机连接，处理请求"""

    def __init__(self):
        self._running = False
        self._shutdown_requested = False
        self.file_service = FileService(ROOT_DIR)
        self.task_service = TaskService()
        self._audio_seq = 0
        self._connection_seq = 0
        self.host_sessions: dict[int, RelaySession] = {}
        self.management_bridge = ManagementBridge(LISTEN_PORT)
        self.runtime_capture = RuntimeSnapshotCapture(ROOT_DIR)
        self.performance_playback = PerformancePlaybackQueue(
            self.runtime_capture.set_avatar_playback,
        )

    async def start(self):
        """启动 TCP 监听，返回是否由主机主动触发关闭"""
        self._running = True
        self._shutdown_requested = False
        self.runtime_capture.start()
        self.management_bridge.start()
        server = await asyncio.start_server(
            self._handle_connection, "0.0.0.0", LISTEN_PORT
        )
        logger.info(f"Slave [{SLAVE_NAME}] listening on TCP:{LISTEN_PORT}")
        logger.info(f"  Root: {ROOT_DIR}")

        async with server:
            # 定期检查是否需要关闭
            while self._running and not self._shutdown_requested:
                await asyncio.sleep(0.5)

            server.close()
            await server.wait_closed()

        return self._shutdown_requested

    async def stop(self):
        """停止服务"""
        self._running = False
        self.runtime_capture.stop()
        self.management_bridge.close()
        await self.performance_playback.close()
        for task_id in list(self.task_service._running.keys()):
            self.task_service.kill(task_id)

    async def _handle_connection(self, reader: asyncio.StreamReader,
                                  writer: asyncio.StreamWriter):
        """处理一个主机连接"""
        addr = writer.get_extra_info("peername")
        self._connection_seq += 1
        connection_id = self._connection_seq
        logger.info("connection_open id=%s peer=%s", connection_id, addr)
        # 等待握手（主机发送）
        msg_reader = MessageReader(reader)
        try:
            msg_type, payload = await asyncio.wait_for(
                msg_reader.read_one(), timeout=10.0
            )
        except asyncio.TimeoutError:
            logger.warning("connection_handshake_timeout id=%s peer=%s", connection_id, addr)
            writer.close()
            return
        except (ConnectionError, OSError) as exc:
            logger.warning(
                "connection_handshake_closed id=%s peer=%s error=%r",
                connection_id, addr, exc,
            )
            writer.close()
            return

        if msg_type != TYPE_HANDSHAKE_REQ:
            writer.write(pack_message(TYPE_ERROR, {
                "code": "BAD_HANDSHAKE",
                "message": "Expected handshake request",
            }))
            await writer.drain()
            writer.close()
            return

        host_name = payload.get("host_name", "Unknown")
        logger.info("connection_handshake_ok id=%s host=%s peer=%s", connection_id, host_name, addr)

        # The dedicated performance sender starts turn numbering from one on
        # every Linux runtime launch.  Reset only for that channel so normal
        # file/command clients and log fetching cannot interrupt a dialogue.
        if host_name == "old-linux-performance":
            await self.performance_playback.reset_for_new_host_connection(host_name, connection_id)

        # 发送握手确认
        writer.write(pack_message(TYPE_HANDSHAKE_ACK, {
            "slave_name": SLAVE_NAME,
            "app_version": __version__,
            "message": "connected",
            "capabilities": [RPC_CAPABILITY, RELAY_CAPABILITY, MANAGEMENT_CAPABILITY, PUSH_CAPABILITY],
        }))
        await writer.drain()

        # The stream belongs to this host TCP connection and is always
        # forwarded to UE on this slave's localhost.
        pose_stream: DigitalHumanPoseStream | None = None

        # 进入消息循环
        last_heartbeat = time.time()
        relay_session = RelaySession(lambda kind, body: self._send(writer, kind, body))
        self.host_sessions[connection_id] = relay_session
        if host_name == "Host" and PUSH_CAPABILITY in payload.get("capabilities", []):
            management_loop = asyncio.get_running_loop()
            def push_management(event):
                # Invoked by the loopback HTTP worker thread. No new socket.
                future = asyncio.run_coroutine_threadsafe(self._send(writer, 0x0607, event), management_loop)
                future.result(timeout=5)
            self.management_bridge.connected(connection_id, writer.get_extra_info("peername")[0], push_management)

        try:
            while self._running:
                try:
                    msg_type, payload = await asyncio.wait_for(
                        msg_reader.read_one(), timeout=0.5
                    )
                except asyncio.TimeoutError:
                    # 检查心跳超时
                    if time.time() - last_heartbeat > HEARTBEAT_TIMEOUT:
                        logger.warning("connection_heartbeat_timeout id=%s host=%s", connection_id, host_name)
                        break
                    continue

                # A persistent 30 FPS pose stream is itself proof that the
                # host is alive.  Do not tear it down merely because it does
                # not also emit separate heartbeat packets.
                last_heartbeat = time.time()

                if msg_type == TYPE_HEARTBEAT:
                    # 主机发来心跳，回复
                    last_heartbeat = time.time()
                    writer.write(pack_message(TYPE_HEARTBEAT, {
                        "echo": payload.get("echo", 0),
                    }))
                    await writer.drain()
                    continue

                if msg_type == TYPE_DISCONNECT:
                    logger.info("connection_disconnect_requested id=%s host=%s", connection_id, host_name)
                    self._shutdown_requested = True
                    break

                if msg_type == TYPE_RELAY_RESULT:
                    relay_session.resolve(payload)
                    continue

                # 其他消息交给分发器
                pose_stream = await self._dispatch(
                    msg_type, payload, writer, pose_stream, connection_id
                )

        except (ConnectionError, OSError) as e:
            logger.warning(
                "connection_closed_by_peer id=%s host=%s peer=%s error=%r",
                connection_id, host_name, addr, e,
            )
        finally:
            relay_session.close()
            self.host_sessions.pop(connection_id, None)
            self.management_bridge.disconnected(connection_id)
            if pose_stream:
                await pose_stream.close()
            if host_name == "old-linux-performance":
                await self.performance_playback.release_host_connection(host_name, connection_id)
            writer.close()
            logger.info("connection_closed id=%s host=%s peer=%s writer_closing=%s", connection_id, host_name, addr, writer.is_closing())

    async def _dispatch(self, msg_type: int, payload: dict, writer,
                        pose_stream: DigitalHumanPoseStream | None = None,
                        connection_id: int | None = None) -> DigitalHumanPoseStream | None:
        """消息分发"""
        try:
            if msg_type == 0x0606:
                self.management_bridge.resolve(payload)
                await self._send(writer, 0x0686, {"ok": True})
            elif msg_type == TYPE_RPC_REQUEST:
                await self._send(writer, TYPE_RPC_RESULT, execute_read(self.file_service, payload))

            elif msg_type == TYPE_LIST_DIR:
                path = payload.get("path", "/")
                entries = self.file_service.list_dir(path)
                await self._send(writer, TYPE_LIST_DIR_RESULT,
                                 {"path": path, "entries": entries})

            elif msg_type == TYPE_DOWNLOAD:
                await self.file_service.download(
                    payload["path"],
                    lambda t, p: self._send(writer, t, p)
                )

            elif msg_type == TYPE_UPLOAD:
                await self.file_service.handle_upload_chunk(
                    payload,
                    lambda t, p: self._send(writer, t, p)
                )

            elif msg_type == TYPE_FILE_MUTATE:
                await self.file_service.mutate(
                    payload,
                    lambda t, p: self._send(writer, t, p)
                )

            elif msg_type == TYPE_RUNTIME_SNAPSHOT_CONFIG:
                result = self.runtime_capture.configure_push(
                    bool(payload.get("enabled", False)),
                    str(payload.get("endpoint", "")),
                    str(payload.get("token", "")),
                    str(payload.get("slave_name", SLAVE_NAME)),
                )
                await self._send(writer, TYPE_RUNTIME_SNAPSHOT_CONFIG_RESULT, result)

            elif msg_type == TYPE_FILE_STAT:
                info = self.file_service.file_stat(payload.get("path", ""))
                await self._send(writer, TYPE_FILE_STAT_RESULT,
                                 {"path": payload["path"], **info})

            elif msg_type == TYPE_DELETE:
                self.file_service.delete(payload["path"])
                await self._send(writer, TYPE_OPERATION_ACK,
                                 {"path": payload["path"], "operation": "delete",
                                  "status": "ok"})

            elif msg_type == TYPE_RENAME:
                new_path = self.file_service.rename(
                    payload["path"], payload.get("new_name", "")
                )
                await self._send(writer, TYPE_OPERATION_ACK,
                                 {"path": payload["path"], "new_path": new_path,
                                  "operation": "rename", "status": "ok"})

            elif msg_type == TYPE_MKDIR:
                self.file_service.mkdir(payload["path"])
                await self._send(writer, TYPE_OPERATION_ACK,
                                 {"path": payload["path"], "operation": "mkdir",
                                  "status": "ok"})

            elif msg_type == TYPE_EXEC:
                task_id = payload.get("task_id", "")
                cmd = payload.get("cmd", "")
                args = payload.get("args", [])
                cwd = payload.get("cwd", "")
                env = payload.get("env", None)
                await self.task_service.exec(
                    task_id, cmd, args, cwd, env,
                    lambda t, p: self._send(writer, t, p)
                )

            elif msg_type == TYPE_TASK_KILL:
                self.task_service.kill(payload.get("task_id", ""))

            elif msg_type == TYPE_UE_MCP_CALL:
                request = payload.get("request", {})
                if not isinstance(request, dict):
                    raise ValueError("request must be a JSON object")
                result = await call_ue_mcp(
                    request,
                    host=payload.get("host", "127.0.0.1"),
                    port=int(payload.get("port", 55701)),
                    timeout=float(payload.get("timeout", 30.0)),
                )
                await self._send(writer, TYPE_UE_MCP_RESULT, result)

            elif msg_type == TYPE_DH_BRIDGE_CALL:
                if payload.get("stream"):
                    frames = payload.get("frames", [])
                    if not isinstance(frames, list):
                        raise ValueError("frames must be a JSON array")
                    result = await stream_dh_bridge(
                        frames, float(payload.get("fps", 30.0)),
                        host=payload.get("host", "127.0.0.1"),
                        port=int(payload.get("port", 55693)),
                        timeout=float(payload.get("timeout", 60.0)),
                    )
                else:
                    request = payload.get("request", {})
                    if not isinstance(request, dict):
                        raise ValueError("request must be a JSON object")
                    result = await call_dh_bridge(
                        request,
                        host=payload.get("host", "127.0.0.1"),
                        port=int(payload.get("port", 55693)),
                        timeout=float(payload.get("timeout", 60.0)),
                    )
                await self._send(writer, TYPE_DH_BRIDGE_RESULT, result)

            elif msg_type == TYPE_DH_STREAM_OPEN:
                if pose_stream:
                    await pose_stream.close()
                pose_stream = DigitalHumanPoseStream(timeout=float(payload.get("timeout", 10.0)))
                await pose_stream.open()
                logger.info("DigitalHuman persistent pose stream opened to 127.0.0.1:55693")
                await self._send(writer, TYPE_DH_STREAM_RESULT, {"ok": True, "state": "open"})

            elif msg_type == TYPE_DH_STREAM_FRAME:
                frame = payload.get("frame")
                if not isinstance(frame, dict):
                    raise ValueError("frame must be a JSON object")
                if not pose_stream:
                    raise ConnectionError("DigitalHuman persistent pose stream is not open")
                # Empty payload classes are explicit no-ops.  Never forward
                # {} for Body/Face/SourceBody/SMPL-X: the UE bridge interprets an
                # empty class as a reset/identity update rather than "leave
                # unchanged".
                forward = dict(frame)
                skipped: list[str] = []
                for field in ("body", "source_body", "face", "smplx"):
                    if field in forward and not forward[field]:
                        forward.pop(field, None)
                        skipped.append(field)
                # A raw SMPL-X frame is a supported persistent-stream payload.
                # It is forwarded unchanged to DigitalHumanBridge, whose
                # legacy branch applies RTG_ACCAD_to_Tifa and publishes Body.
                if (not forward.get("body") and not forward.get("source_body")
                        and not forward.get("face") and not forward.get("smplx")):
                    logger.debug("performance_field_skipped stream frame fields=%s", skipped or ["body", "source_body", "face", "smplx"])
                    return pose_stream
                # No acknowledgement per frame: it would add one network RTT
                # to every 30 FPS pose.  TCP back-pressure through drain() is
                # sufficient for this ordered stream.
                await pose_stream.send_frame(forward)
                # Keep a lightweight proof that raw SMPL-X frames actually
                # crossed the slave→UE bridge.  This is intentionally sparse
                # so normal 30 FPS playback does not flood slave.log.
                if "smplx" in forward:
                    frame_number = forward.get("frame", -1)
                    try:
                        should_log = int(frame_number) % 30 == 0
                    except (TypeError, ValueError):
                        should_log = False
                    if should_log:
                        poses = forward["smplx"].get("poses", []) if isinstance(forward["smplx"], dict) else []
                        logger.info(
                            "smplx_persistent_frame_forwarded frame=%s pose_values=%s",
                            frame_number,
                            len(poses) if isinstance(poses, list) else 0,
                        )

            elif msg_type == TYPE_DH_STREAM_CLOSE:
                if pose_stream:
                    await pose_stream.close()
                    pose_stream = None
                await self._send(writer, TYPE_DH_STREAM_RESULT, {"ok": True, "state": "closed"})

            elif msg_type == TYPE_DH_AUDIO_PLAY:
                self._audio_seq += 1
                audio_seq = self._audio_seq
                audio = str(payload.get("audio", ""))
                if not audio:
                    logger.info("performance_field_skipped field=wav")
                    await self._send(writer, TYPE_DH_AUDIO_RESULT, {"ok": True, "skipped": "wav"})
                    return pose_stream
                delay_ms = int(payload.get("delay_ms", 0) or 0)
                timeout = float(payload.get("timeout", 120.0) or 120.0)
                logger.info(
                    "audio_received conn=%s seq=%s at=%s b64_bytes=%s delay_ms=%s timeout=%s",
                    connection_id, audio_seq, datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
                    len(payload.get("audio", "")), delay_ms, timeout,
                )
                async def _play_queued(_audio=audio, _delay=delay_ms, _timeout=timeout,
                                       _writer=writer, _seq=audio_seq, _conn=connection_id):
                    logger.info("audio_play_start conn=%s seq=%s at=%s", _conn, _seq, datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3])
                    self.runtime_capture.set_avatar_playback(True)
                    try:
                        result = await play_audio(_audio, _delay, _timeout)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("audio_play_exception conn=%s seq=%s error=%r", _conn, _seq, exc)
                        result = {"ok": False, "error": str(exc)}
                    finally:
                        self.runtime_capture.set_avatar_playback(False)
                    try:
                        await self._send(_writer, TYPE_DH_AUDIO_RESULT, result)
                        logger.info("audio_result_sent conn=%s seq=%s ok=%s", _conn, _seq, result.get("ok"))
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "audio_result_send_failed conn=%s seq=%s writer_closing=%s error=%r",
                            _conn, _seq, _writer.is_closing(), exc,
                        )

                asyncio.ensure_future(_play_queued())

            elif msg_type == TYPE_DH_PERFORMANCE_PACKAGE:
                # Direct execution: this does not enqueue a FIFO playback
                # backlog.  The payload's own start offset defines its clock.
                active = await self.performance_playback.submit(payload)
                logger.info(
                    "performance_package_started active_tasks=%s body=%s face=%s wav=%s pcm=%s",
                    active,
                    bool((payload.get("body_package") or {}).get("frames", [])),
                    bool(payload.get("face_frames")),
                    bool(payload.get("wav")),
                    bool(payload.get("pcm")),
                )

            elif msg_type == 0x0301:
                preview = capture_screen_base64(
                    payload.get("width", 320),
                    payload.get("height", 180),
                    payload.get("quality", 45),
                )
                if preview.get("ok"):
                    await self._send(writer, 0x0381, preview)
                else:
                    await self._send(writer, TYPE_ERROR,
                                     {"code": "SCREEN_CAPTURE_FAILED",
                                      "message": preview.get("error", "capture failed")})

        except PermissionError as e:
            await self._send(writer, TYPE_ERROR,
                             {"code": "PERMISSION_DENIED", "message": str(e)})
        except FileNotFoundError as e:
            await self._send(writer, TYPE_ERROR,
                             {"code": "NOT_FOUND", "message": str(e)})
        except Exception as e:
            logger.exception(f"Dispatch error: {e}")
            await self._send(writer, TYPE_ERROR,
                             {"code": "INTERNAL_ERROR", "message": str(e)})
        return pose_stream

    async def _send(self, writer, msg_type: int, payload: dict):
        """发送消息到主机"""
        writer.write(pack_message(msg_type, payload or {}))
        await writer.drain()
