"""Passive local snapshot capture for a running Cloud Slave.

The service deliberately never sends screen or microphone data over the
network.  It maintains only these two files below the configured slave root:

``runtime_snapshot/latest_screen.jpg``
    A compressed desktop screenshot, atomically replaced every 0.5 seconds.
``runtime_snapshot/latest_speech.wav``
    The most recent completed VAD-confirmed microphone utterance, atomically
    replaced after speech ends.

The host can retrieve either file later through the normal explicit file
download request.  There is no push channel and no historical archive.
"""

from __future__ import annotations

import base64
from collections import deque
import hashlib
import json
import logging
import os
import queue
import threading
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

from slave.screen_capture import capture_screen_base64


logger = logging.getLogger("slave.runtime_capture")


class _EnergyVAD:
    """Same energy/VAD timing used by the earlier local ASR input path."""

    # Calibrated against the Cloud PC's Realtek microphone idle RMS
    # (median about 0.087, P95 about 0.116, peaks about 0.195).  A 0.13
    # threshold still produced false 15-second speech segments, so require a
    # sustained signal above the normal idle floor while still accepting
    # ordinary speaking volume.
    def __init__(self, sample_rate: int, threshold: float = 0.15) -> None:
        self.sample_rate = sample_rate
        self.threshold = threshold
        # A softer gate opens a tentative utterance so low-energy initial
        # syllables are retained; ``threshold`` still validates it as speech.
        self.candidate_threshold = threshold * 0.60
        # Do not reuse the soft onset threshold to detect an endpoint.  The
        # Cloud PC's 300--3400 Hz room-noise P95 is about 0.096, so 0.09
        # keeps an utterance open indefinitely even after a real pause.
        # 0.125 remains below ordinary speech energy while allowing a genuine
        # 800-ms quiet interval to complete short commands.
        self.endpoint_threshold = 0.125
        # 100 ms confirms short commands such as “继续”, while the unchanged
        # 0.15 RMS threshold still keeps ordinary room noise from opening a
        # segment.  200 ms was needlessly strict for two-character commands.
        self.start_confirm_samples = int(sample_rate * 0.10)
        self.candidate_gap_samples = int(sample_rate * 0.12)
        self.candidate_max_samples = int(sample_rate * 1.00)
        self.end_silence_samples = int(sample_rate * 0.80)
        # 300ms retains short ordinary acknowledgements such as “你好”, while
        # still rejecting a single transient click/noise block.
        self.min_speech_samples = int(sample_rate * 0.30)
        self.max_speech_samples = int(sample_rate * 15.0)
        self.reset()

    def reset(self) -> None:
        self._state = "silence"
        self._chunks: list[np.ndarray] = []
        # Keep a *time* based pre-roll.  ``sounddevice`` uses a variable
        # callback block size when ``blocksize=0``; retaining a fixed number
        # of blocks can therefore shrink to far less than 100 ms and cut a
        # low-energy initial syllable (for example the “从” in “从机”).
        self._pre_roll_samples = max(1, int(self.sample_rate * 0.20))
        self._pre_chunks: deque[np.ndarray] = deque()
        self._pre_sample_count = 0
        self._sample_count = 0
        self._speech_start = 0
        self._loud_start: int | None = None
        self._silence_start: int | None = None
        self._candidate_silence_start: int | None = None
        self._strong_samples = 0

    def _retain_pre_chunk(self, samples: np.ndarray) -> None:
        """Retain exactly the most recent 200 ms before a possible onset."""
        if not len(samples):
            return
        chunk = samples.copy()
        self._pre_chunks.append(chunk)
        self._pre_sample_count += len(chunk)
        while self._pre_chunks and self._pre_sample_count > self._pre_roll_samples:
            overflow = self._pre_sample_count - self._pre_roll_samples
            first = self._pre_chunks[0]
            if len(first) <= overflow:
                self._pre_chunks.popleft()
                self._pre_sample_count -= len(first)
            else:
                self._pre_chunks[0] = first[overflow:].copy()
                self._pre_sample_count -= overflow

    def process(
        self, samples: np.ndarray, original_samples: np.ndarray | None = None,
    ) -> np.ndarray | None:
        samples = np.asarray(samples, dtype=np.float32).reshape(-1).copy()
        if not len(samples):
            return None
        original = (
            samples if original_samples is None
            else np.asarray(original_samples, dtype=np.float32).reshape(-1).copy()
        )
        if len(original) != len(samples):
            raise ValueError("filtered and original sample blocks must have equal length")
        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
        candidate_loud = rms >= self.candidate_threshold
        strong_loud = rms >= self.threshold

        if self._state == "silence":
            if candidate_loud:
                # Prefix this utterance with the retained 200-ms pre-roll,
                # then establish all offsets in the combined sample timeline.
                prefix = (
                    np.concatenate(tuple(self._pre_chunks))
                    if self._pre_chunks else np.empty(0, dtype=np.float32)
                )
                self._chunks = [prefix.copy(), original]
                self._sample_count = len(prefix) + len(original)
                self._state = "pre_speech"
                self._loud_start = len(prefix)
                self._pre_chunks.clear()
                self._pre_sample_count = 0
                self._strong_samples = len(samples) if strong_loud else 0
                logger.info(
                    "runtime_snapshot_vad_candidate rms=%.4f candidate_threshold=%.4f strong=%s pre_roll_ms=%d",
                    rms, self.candidate_threshold, strong_loud,
                    round(len(prefix) * 1000 / self.sample_rate),
                )
            else:
                self._retain_pre_chunk(original)
            return None

        chunk_start = self._sample_count
        self._chunks.append(original)
        self._sample_count += len(original)

        if self._state == "pre_speech":
            if candidate_loud:
                self._candidate_silence_start = None
            elif self._candidate_silence_start is None:
                self._candidate_silence_start = chunk_start
            if strong_loud:
                self._strong_samples += len(samples)
            candidate_expired = (
                self._candidate_silence_start is not None
                and self._sample_count - self._candidate_silence_start >= self.candidate_gap_samples
            ) or (
                self._loud_start is not None
                and self._sample_count - self._loud_start >= self.candidate_max_samples
                and self._strong_samples < self.start_confirm_samples
            )
            if candidate_expired:
                # A false/weak start becomes part of the next idle pre-roll.
                logger.info(
                    "runtime_snapshot_vad_candidate_rejected elapsed_ms=%d strong_ms=%d required_strong_ms=%d",
                    round((self._sample_count - (self._loud_start or 0)) * 1000 / self.sample_rate),
                    round(self._strong_samples * 1000 / self.sample_rate),
                    round(self.start_confirm_samples * 1000 / self.sample_rate),
                )
                recent_chunks = [chunk.copy() for chunk in self._chunks[-4:]]
                self.reset()
                for chunk in recent_chunks:
                    self._retain_pre_chunk(chunk)
                return None
            if self._strong_samples >= self.start_confirm_samples:
                self._state = "speech"
                # ``_chunks`` already begins with exactly the retained
                # four-block pre-roll.
                self._speech_start = 0
                logger.info(
                    "runtime_snapshot_vad_confirmed strong_ms=%d threshold=%.4f",
                    round(self._strong_samples * 1000 / self.sample_rate), self.threshold,
                )
            return None

        # Confirmed speech: retain it until a real quiet endpoint or max cap.
        endpoint_loud = rms >= self.endpoint_threshold
        if endpoint_loud:
            self._silence_start = None
        elif self._silence_start is None:
            self._silence_start = chunk_start
        speech_length = self._sample_count - self._speech_start
        endpoint = (
            self._silence_start is not None
            and self._sample_count - self._silence_start >= self.end_silence_samples
        ) or speech_length >= self.max_speech_samples
        if not endpoint:
            return None
        merged = np.concatenate(self._chunks) if self._chunks else np.empty(0, dtype=np.float32)
        segment_end = min(len(merged), self._speech_start + self.max_speech_samples)
        # Silence is required only to decide that an utterance has completed;
        # it is deliberately not written into latest_speech.wav.
        if self._silence_start is not None:
            segment_end = min(segment_end, self._silence_start)
        segment = merged[self._speech_start:segment_end]
        logger.info(
            "runtime_snapshot_vad_segment_completed seconds=%.3f endpoint=%s endpoint_threshold=%.3f",
            len(segment) / self.sample_rate,
            "silence" if self._silence_start is not None else "max_duration",
            self.endpoint_threshold,
        )
        self.reset()
        return segment if len(segment) >= self.min_speech_samples else None


class RuntimeSnapshotCapture:
    """Owns passive screenshot and VAD-audio capture threads for one slave."""

    SCREEN_INTERVAL_SECONDS = 0.5
    SCREEN_WIDTH = 640
    SCREEN_HEIGHT = 360
    SCREEN_QUALITY = 45
    # 原始滚动录音仅用于临时排障；默认关闭，避免持续保留麦克风内容。
    SAVE_RAW_MICROPHONE = False

    def __init__(self, root_dir: str | os.PathLike[str]) -> None:
        self.directory = Path(root_dir) / "runtime_snapshot"
        self.screen_path = self.directory / "latest_screen.jpg"
        self.audio_path = self.directory / "latest_speech.wav"
        # Diagnostic ring: unfiltered microphone samples, including speech
        # that the energy VAD might reject.  It is intentionally only the
        # latest short window, never an archive.
        self.raw_audio_path = self.directory / "latest_microphone_raw.wav"
        self._stop = threading.Event()
        self._screen_thread: threading.Thread | None = None
        self._audio_writer_thread: threading.Thread | None = None
        self._audio_start_thread: threading.Thread | None = None
        self._audio_stream = None
        # Set while the slave itself plays avatar speech.  The audio callback
        # owns VAD resets, avoiding cross-thread mutation of VAD state.
        self._avatar_playing = threading.Event()
        self._suppression_seen = False
        self._speech_queue: queue.Queue[tuple[np.ndarray, int]] = queue.Queue(maxsize=1)
        self._raw_snapshot_queue: queue.Queue[tuple[np.ndarray, int]] = queue.Queue(maxsize=1)
        self._raw_chunks: deque[np.ndarray] = deque()
        self._raw_sample_count = 0
        self._raw_ring_seconds = 15.0
        self._raw_last_snapshot = 0.0
        self._raw_lock = threading.Lock()
        self._audio_rate = 0
        self._vad: _EnergyVAD | None = None
        self._speech_filter_sos = None
        self._speech_filter_zi = None
        # The host explicitly enables this callback after the operator picks
        # an active slave in Voice Bridge.  It is empty by default, so a slave
        # never uploads microphone/screen data merely because it is running.
        self._push_lock = threading.Lock()
        self._push_endpoint = ""
        self._push_token = ""
        self._push_slave_name = ""
        self._push_pending: dict[str, tuple[bytes, str, str]] = {}
        self._push_digests: dict[str, str] = {}
        self._push_event = threading.Event()
        self._push_thread: threading.Thread | None = None
        # 采集按需：默认不截屏、不开麦克风。主机在 Voice Bridge 里点了"连接"
        # 之后下发快照推送配置（TYPE_RUNTIME_SNAPSHOT_CONFIG）才会打开，断开即停。
        self._capture_enabled = threading.Event()
        self._microphone_lock = threading.Lock()

    def start(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._screen_thread = threading.Thread(
            target=self._screen_loop, name="slave-screen-snapshot", daemon=True,
        )
        self._screen_thread.start()
        self._audio_writer_thread = threading.Thread(
            target=self._audio_writer_loop, name="slave-vad-snapshot", daemon=True,
        )
        self._audio_writer_thread.start()
        self._push_thread = threading.Thread(
            target=self._push_loop, name="slave-runtime-snapshot-push", daemon=True,
        )
        self._push_thread.start()
        # 麦克风不在这里打开：等主机连接后再开（见 _enable_capture）。
        logger.info(
            "runtime_snapshot_ready capture=on_demand interval=%.1fs screen=%s speech=%s raw_microphone=%s",
            self.SCREEN_INTERVAL_SECONDS, self.screen_path, self.audio_path, self.raw_audio_path,
        )

    def stop(self) -> None:
        self._disable_capture()
        self._stop.set()
        self._push_event.set()
        if self._audio_stream is not None:
            try:
                self._audio_stream.stop()
                self._audio_stream.close()
            except Exception:  # noqa: BLE001 - shutdown must continue
                pass
            self._audio_stream = None
        for worker in (self._screen_thread, self._audio_writer_thread, self._push_thread):
            if worker is not None:
                worker.join(timeout=2.0)
        logger.info("runtime_snapshot_stopped")

    # ── 按需采集：主机连接后开、断开即停 ────────────────────────────
    def _enable_capture(self) -> None:
        """主机已连接：开始截屏并打开麦克风（麦克风可能阻塞，放独立线程）。"""
        if self._capture_enabled.is_set():
            return
        self._capture_enabled.set()
        logger.info("runtime_snapshot_capture_enabled screen=%s audio=%s", self.screen_path, self.audio_path)
        with self._microphone_lock:
            if self._audio_stream is None and self._audio_start_thread is not None \
                    and self._audio_start_thread.is_alive():
                logger.info("runtime_snapshot_microphone_open_in_progress")
                return
            if self._audio_stream is not None:
                return
            # Windows/PortAudio 枚举远程桌面默认输入设备时可能阻塞，不能挡住调用方。
            self._audio_start_thread = threading.Thread(
                target=self._start_microphone, name="slave-vad-device-open", daemon=True,
            )
            self._audio_start_thread.start()

    def _disable_capture(self) -> None:
        """主机断开：停止截屏并关掉麦克风，丢弃可能残留的语音段。"""
        if not self._capture_enabled.is_set():
            return
        self._capture_enabled.clear()
        stream = self._audio_stream
        self._audio_stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:  # noqa: BLE001 - 关闭失败不影响从机其它功能
                logger.warning("runtime_snapshot_microphone_close_failed error=%r", exc)
        self._vad = None
        for pending in (self._speech_queue, self._raw_snapshot_queue):
            while True:
                try:
                    pending.get_nowait()
                except queue.Empty:
                    break
        with self._raw_lock:
            self._raw_chunks.clear()
            self._raw_sample_count = 0
        logger.info("runtime_snapshot_capture_disabled")

    def set_avatar_playback(self, active: bool) -> None:
        """Exclude locally played avatar audio from microphone snapshots."""
        if active:
            self._avatar_playing.set()
            # Discard a segment that may have completed immediately before
            # playback began, rather than writing the avatar's first syllable.
            while True:
                try:
                    self._speech_queue.get_nowait()
                except queue.Empty:
                    break
        else:
            self._avatar_playing.clear()

    def configure_push(self, enabled: bool, endpoint: str = "", token: str = "", slave_name: str = "") -> dict:
        """Enable/disable explicit host snapshot push.

        ``endpoint`` is supplied by the local host's Voice Bridge.  The slave
        accepts only HTTP(S) endpoints and never persists the secret token.
        """
        if enabled and not endpoint.startswith(("http://", "https://")):
            raise ValueError("runtime snapshot push endpoint must be http(s)")
        if enabled and not token:
            raise ValueError("runtime snapshot push token is required")
        with self._push_lock:
            self._push_endpoint = endpoint if enabled else ""
            self._push_token = token if enabled else ""
            self._push_slave_name = slave_name if enabled else ""
            self._push_pending.clear()
            self._push_digests.clear()
        self._push_event.set()
        # 采集与推送同一条开关：主机连接后才截屏/录音，断开即停。
        if enabled:
            self._enable_capture()
        else:
            self._disable_capture()
        logger.info("runtime_snapshot_push_config enabled=%s endpoint=%s", enabled, endpoint if enabled else "")
        return {"ok": True, "enabled": bool(enabled), "endpoint": endpoint if enabled else "",
                "capturing": bool(self._capture_enabled.is_set())}

    def _queue_push(self, kind: str, payload: bytes, content_type: str) -> None:
        """Queue only a changed latest snapshot; never block capture threads."""
        digest = hashlib.sha256(payload).hexdigest()
        with self._push_lock:
            if not self._push_endpoint or self._push_digests.get(kind) == digest:
                return
            self._push_digests[kind] = digest
            # Latest-wins avoids a screenshot backlog on a slow link while
            # retaining one independent pending audio snapshot.
            self._push_pending[kind] = (payload, content_type, digest)
        self._push_event.set()

    def _push_loop(self) -> None:
        """Deliver changed snapshots outside audio/screen capture callbacks."""
        while not self._stop.is_set():
            self._push_event.wait(0.5)
            self._push_event.clear()
            with self._push_lock:
                endpoint, token, slave_name = self._push_endpoint, self._push_token, self._push_slave_name
                pending = self._push_pending
                self._push_pending = {}
            if not endpoint or not pending:
                continue
            for kind, (payload, content_type, digest) in pending.items():
                body = json.dumps({
                    "token": token,
                    "kind": kind,
                    "content_type": content_type,
                    "sha256": digest,
                    "slave_name": slave_name,
                    "data_base64": base64.b64encode(payload).decode("ascii"),
                }, ensure_ascii=False).encode("utf-8")
                request = urllib.request.Request(
                    endpoint, data=body, method="POST",
                    headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
                )
                try:
                    with urllib.request.urlopen(request, timeout=5) as response:
                        if not 200 <= int(response.status) < 300:
                            raise RuntimeError(f"HTTP {response.status}")
                    logger.info("runtime_snapshot_pushed kind=%s bytes=%s", kind, len(payload))
                except Exception as exc:  # noqa: BLE001 - snapshot loss must not affect the slave
                    logger.warning("runtime_snapshot_push_failed kind=%s error=%r", kind, exc)

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, path)

    def _screen_loop(self) -> None:
        while not self._stop.is_set():
            # 按需采集：主机没连接时不截屏（wait 期间每 0.5 秒醒一次看开关）。
            if not self._capture_enabled.wait(0.5):
                continue
            if not self._capture_enabled.is_set():
                continue
            started = time.monotonic()
            result = capture_screen_base64(
                self.SCREEN_WIDTH, self.SCREEN_HEIGHT, self.SCREEN_QUALITY,
            )
            if result.get("ok"):
                try:
                    self._atomic_write(self.screen_path, base64.b64decode(result["image_base64"]))
                    self._queue_push("screen", self.screen_path.read_bytes(), "image/jpeg")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("runtime_snapshot_screen_write_failed error=%r", exc)
            else:
                logger.debug("runtime_snapshot_screen_capture_failed error=%s", result.get("error"))
            self._stop.wait(max(0.0, self.SCREEN_INTERVAL_SECONDS - (time.monotonic() - started)))

    def _start_microphone(self) -> None:
        try:
            import sounddevice as sd

            device = sd.query_devices(kind="input")
            rate = max(8_000, int(round(float(device["default_samplerate"]))))
            self._audio_rate = rate
            self._vad = _EnergyVAD(rate)
            self._speech_filter_sos = butter(
                4, (300.0, 3400.0), btype="bandpass", fs=rate, output="sos",
            )
            self._speech_filter_zi = sosfilt_zi(self._speech_filter_sos) * 0.0

            def callback(indata, _frames, _time_info, status) -> None:
                if status:
                    logger.debug("runtime_snapshot_audio_status=%s", status)
                vad = self._vad
                if vad is None:
                    return
                if self._avatar_playing.is_set():
                    if not self._suppression_seen:
                        vad.reset()
                        self._suppression_seen = True
                        logger.info("runtime_snapshot_vad_suppressed_avatar_playback")
                    return
                if self._suppression_seen:
                    # Never join the end of avatar audio to a later user
                    # utterance after playback stops.
                    vad.reset()
                    self._suppression_seen = False
                    logger.info("runtime_snapshot_vad_resumed_after_avatar_playback")
                raw_samples = np.asarray(indata[:, 0], dtype=np.float32)
                if self.SAVE_RAW_MICROPHONE:
                    self._retain_raw_microphone(raw_samples, rate)
                filtered, self._speech_filter_zi = sosfilt(
                    self._speech_filter_sos, raw_samples, zi=self._speech_filter_zi,
                )
                segment = vad.process(filtered, raw_samples)
                if segment is None:
                    return
                try:
                    self._speech_queue.put_nowait((segment, rate))
                except queue.Full:
                    try:
                        self._speech_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self._speech_queue.put_nowait((segment, rate))
                    except queue.Full:
                        pass

            self._audio_stream = sd.InputStream(
                samplerate=rate, channels=1, dtype="float32", callback=callback,
            )
            self._audio_stream.start()
            if not self._capture_enabled.is_set():
                # 打开过程中主机已断开：立刻关掉，别留下没人用的采集流。
                try:
                    self._audio_stream.stop()
                    self._audio_stream.close()
                except Exception:  # noqa: BLE001
                    pass
                self._audio_stream = None
                logger.info("runtime_snapshot_microphone_closed_immediately")
                return
            logger.info("runtime_snapshot_microphone_started rate=%s device=%s", rate, device.get("name", "default"))
        except Exception as exc:  # noqa: BLE001 - screenshot capture remains useful without a microphone
            logger.warning("runtime_snapshot_microphone_unavailable error=%r", exc)

    def _retain_raw_microphone(self, samples: np.ndarray, rate: int) -> None:
        """Keep and periodically persist a bounded unfiltered microphone ring."""
        if samples.size == 0:
            return
        now = time.monotonic()
        with self._raw_lock:
            chunk = np.asarray(samples, dtype=np.float32).reshape(-1).copy()
            self._raw_chunks.append(chunk)
            self._raw_sample_count += len(chunk)
            maximum = int(rate * self._raw_ring_seconds)
            while self._raw_chunks and self._raw_sample_count > maximum:
                overflow = self._raw_sample_count - maximum
                first = self._raw_chunks[0]
                if len(first) <= overflow:
                    self._raw_chunks.popleft()
                    self._raw_sample_count -= len(first)
                else:
                    self._raw_chunks[0] = first[overflow:].copy()
                    self._raw_sample_count -= overflow
            if now - self._raw_last_snapshot < 1.0:
                return
            self._raw_last_snapshot = now
            snapshot = np.concatenate(tuple(self._raw_chunks)).copy()
        try:
            self._raw_snapshot_queue.put_nowait((snapshot, rate))
        except queue.Full:
            try:
                self._raw_snapshot_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._raw_snapshot_queue.put_nowait((snapshot, rate))
            except queue.Full:
                pass

    @staticmethod
    def _write_wav(path: Path, segment: np.ndarray, rate: int) -> None:
        temporary = path.with_suffix(".wav.tmp")
        with wave.open(str(temporary), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes((np.clip(segment, -1.0, 1.0) * 32767.0).astype("<i2").tobytes())
        os.replace(temporary, path)

    def _audio_writer_loop(self) -> None:
        while not self._stop.is_set():
            # A diagnostic file must survive even when a short utterance did
            # not satisfy the VAD confirmation condition.
            try:
                raw, raw_rate = self._raw_snapshot_queue.get_nowait()
                self._write_wav(self.raw_audio_path, raw, raw_rate)
            except queue.Empty:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("runtime_snapshot_raw_microphone_write_failed error=%r", exc)
            try:
                segment, rate = self._speech_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._write_wav(self.audio_path, segment, rate)
                self._queue_push("audio", self.audio_path.read_bytes(), "audio/wav")
                logger.info("runtime_snapshot_speech_saved seconds=%.2f path=%s", len(segment) / rate, self.audio_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("runtime_snapshot_speech_write_failed error=%r", exc)
