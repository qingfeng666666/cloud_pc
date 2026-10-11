"""从机音频播放服务：接收 WAV（base64）+ 延迟毫秒，延迟后播放。

设计保持最简单：收到一段音频就直接播放（sounddevice 常驻输出流）；
播放中收到新音频则打断当前播放、立即播放新音频。连续性由主机保证
（主机把整段语音作为一条消息发送）。sounddevice 不可用时回退到每段
起进程（Windows PowerShell SoundPlayer / Linux aplay / paplay）。
"""

from __future__ import annotations

import asyncio
import base64
import datetime
from concurrent.futures import ThreadPoolExecutor
import io
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path


logger = logging.getLogger("slave.audio")

WAV_RATE = 44100
WAV_CHANNELS = 1

_continuous_player = None
_player_lock = threading.Lock()
# Exactly one worker owns all sounddevice writes.  Network/package tasks may
# arrive concurrently, but they never access the audio device themselves.
# This is a transport boundary, not dialogue buffering: items are appended in
# the order in which the host sent them.
_audio_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dh-audio")


class ContinuousAudioPlayer:
    """sounddevice 常驻输出流；``play_new()`` 打断当前播放并立即播新音频。

    write() 会阻塞到样本被声卡消费完，因此按 0.5 秒分块写入，块间检查
    打断标记——新音频到达时，旧音频最多再播 0.5 秒就停止。

    输出流只创建/启动一次并保持常驻；播放新音频时不 stop/start 流，
    避免频繁启停导致播放截断（前一段尾部/后一段头部丢失）。
    """

    def __init__(self, rate: int = WAV_RATE, channels: int = WAV_CHANNELS):
        import numpy as np
        import sounddevice as sd

        self._np = np
        self._sd = sd
        self._rate = rate
        self._channels = channels
        self._stream = None
        self._lock = threading.Lock()
        self._interrupt = threading.Event()
        # Incremented by the performance dispatcher on a new dialogue turn.
        # It is intentionally lock-free so a blocked ``stream.write`` does
        # not prevent cancellation from reaching the next 100ms PCM block.
        self._pcm_generation = 0

    def _open_stream(self):
        if self._stream is None:
            self._stream = self._sd.OutputStream(
                samplerate=self._rate, channels=self._channels, dtype="float32"
            )
        try:
            self._stream.start()
        except Exception:  # noqa: BLE001 - 已开启则忽略
            pass
        return self._stream

    def _decode(self, wav_bytes: bytes):
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            rate = wf.getframerate()
            channels = wf.getnchannels()
            raw = wf.readframes(wf.getnframes())
        samples = self._np.frombuffer(raw, dtype=self._np.int16).astype(self._np.float32) / 32768.0
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
        if rate != self._rate:
            samples = self._resample(samples, rate, self._rate)
        return samples

    def _decode_pcm(self, raw: bytes, rate: int, channels: int, sample_format: str):
        if sample_format.lower() != "f32le":
            raise ValueError(f"unsupported PCM format: {sample_format}")
        if rate <= 0 or channels <= 0:
            raise ValueError("PCM rate/channels must be positive")
        samples = self._np.frombuffer(raw, dtype="<f4")
        if samples.size % channels:
            raise ValueError("PCM payload does not align to channel count")
        samples = self._np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
        if rate != self._rate:
            samples = self._resample(samples, rate, self._rate)
        return self._np.clip(samples.astype(self._np.float32), -1.0, 1.0)

    def play_new(self, wav_bytes: bytes) -> None:
        """打断当前播放，立即开始播放新音频。"""
        self._interrupt.set()
        with self._lock:
            self._interrupt.clear()
            # 流常驻：不 stop/start。旧播放循环检测到 interrupt 后
            # 在 0.5s 分块边界退出，这里拿到锁即可无缝接续新音频。
            stream = self._open_stream()
            samples = self._decode(wav_bytes)
            # 调试：保存实际播出前的音频（重采样后），便于与主机源文件对比
            try:
                # PyInstaller onefile 打包后 __file__ 指向临时解压目录，
                # 用固定路径保存（从机 Root 下）。
                save_dir = r"G:\eu5_project\cloud_pc\playback_debug"
                os.makedirs(save_dir, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                import struct
                with open(os.path.join(save_dir, "play_" + ts + ".wav"), "wb") as wf:
                    n = len(samples)
                    data = (samples * 32767).astype("<i2").tobytes()
                    wf.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt ")
                    wf.write(struct.pack("<IHHIIHH", 16, 1, self._channels, self._rate,
                                          self._rate * self._channels * 2, 2, 16))
                    wf.write(b"data" + struct.pack("<I", len(data)) + data)
            except Exception:
                pass
            chunk = max(1, int(0.5 * self._rate))
            for start in range(0, len(samples), chunk):
                if self._interrupt.is_set():
                    break
                stream.write(samples[start:start + chunk])

    def play_append_pcm(self, raw: bytes, rate: int, channels: int = 1, sample_format: str = "f32le") -> None:
        """Append PCM to the already-open device stream without stop/start.

        The performance queue calls this serially.  Unlike ``play_new`` it
        never sets the interrupt flag, so adjacent CosyVoice PCM hops meet on
        one sounddevice stream rather than replacing one another.
        """
        generation = self._pcm_generation
        with self._lock:
            stream = self._open_stream()
            samples = self._decode_pcm(raw, int(rate), int(channels), sample_format)
            chunk = max(1, int(0.1 * self._rate))
            for start in range(0, len(samples), chunk):
                if generation != self._pcm_generation:
                    break
                stream.write(samples[start:start + chunk])

    def interrupt_pcm(self) -> None:
        """Invalidate active/late PCM hops from an older dialogue turn."""
        self._pcm_generation += 1

    @staticmethod
    def _resample(samples, src_rate: int, dst_rate: int):
        import numpy as np

        if src_rate == dst_rate:
            return samples
        count = max(1, int(len(samples) * dst_rate / src_rate))
        # 用 float64 生成源采样位置，避免 np.arange 默认 int32 在
        # count*src_rate 时整数溢出（Windows 上 numpy 默认 int32，
        # 会导致重采样后半段时间映射错误、音频失真/丢字）。
        x = np.arange(count, dtype=np.float64) * float(src_rate) / float(dst_rate)
        x0 = np.clip(np.floor(x).astype(np.int64), 0, len(samples) - 1)
        x1 = np.clip(x0 + 1, 0, len(samples) - 1)
        frac = (x - x0).astype("float32")
        return samples[x0] * (1.0 - frac) + samples[x1] * frac

    def close(self) -> None:
        for fn in (self._stream.stop, self._stream.close):
            try:
                fn()
            except Exception:  # noqa: BLE001 - 关闭阶段不抛错
                pass


def _get_player() -> ContinuousAudioPlayer:
    global _continuous_player
    if _continuous_player is None:
        with _player_lock:
            if _continuous_player is None:
                _continuous_player = ContinuousAudioPlayer()
    return _continuous_player


def interrupt_pcm_playback() -> None:
    """Stop the active performance PCM hop at its next 100ms boundary."""
    global _continuous_player
    if _continuous_player is not None:
        _continuous_player.interrupt_pcm()


def _play_command(path: str) -> list[str]:
    if sys.platform.startswith("win"):
        # PowerShell 只支持 .wav 的 SoundPlayer；PlaySync 阻塞直到播完。
        return [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(New-Object Media.SoundPlayer '{path}').PlaySync()",
        ]
    for player in ("aplay", "paplay"):
        exe = shutil.which(player)
        if exe:
            return [exe, path]
    raise RuntimeError("no audio player found (need aplay or paplay)")


async def _play_audio_process(raw: bytes, timeout: float = 120.0) -> dict:
    """回退路径：写临时 WAV，起单个进程播完（每段一次进程启动）。"""
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="dh_audio_", suffix=".wav")
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        command = _play_command(tmp_path)
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        if proc.returncode != 0:
            return {"ok": False, "error": f"audio player exited with {proc.returncode}"}
        return {"ok": True, "path": tmp_path}
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"audio playback timed out after {timeout}s"}
    except Exception as exc:  # noqa: BLE001 - 协议层统一返回错误
        return {"ok": False, "error": str(exc)}
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass


async def play_audio(audio_b64: str, delay_ms: int = 0, timeout: float = 120.0) -> dict:
    """解码 WAV、等待 delay_ms、播放，返回结果。

    直接播放（打断旧音频）；sounddevice 不可用时回退逐段起进程。
    """
    started = time.monotonic()
    try:
        raw = base64.b64decode(audio_b64)
        if not raw.startswith(b"RIFF"):
            return {"ok": False, "error": "audio payload is not a WAV file"}
        with wave.open(io.BytesIO(raw), "rb") as wf:
            duration = wf.getnframes() / max(1, float(wf.getframerate() or 1))
        if delay_ms and delay_ms > 0:
            await asyncio.sleep(max(0, delay_ms) / 1000.0)
        try:
            player = _get_player()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(_audio_worker, player.play_new, raw)
            result = {"ok": True, "continuous": True, "mode": "replace"}
        except Exception as exc:  # noqa: BLE001 - 回退到进程播放
            logger.warning("continuous playback unavailable (%s); fallback to per-segment process", exc)
            result = await _play_audio_process(raw, timeout)
        logger.info(
            "audio_play delay_ms=%s duration=%.2fs result=%s elapsed=%.3fs ended=%s",
            delay_ms, duration, result.get("ok"), time.monotonic() - started,
            datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
        )
        return result
    except Exception as exc:  # noqa: BLE001 - 协议层统一返回错误
        result = {"ok": False, "error": str(exc)}
        logger.warning("audio_play error: %s", exc)
        return result


async def play_pcm(
    pcm_b64: str,
    rate: int,
    channels: int = 1,
    sample_format: str = "f32le",
    timeout: float = 120.0,
) -> dict:
    """Append one raw PCM hop to the persistent audio stream."""
    started = time.monotonic()
    try:
        raw = base64.b64decode(pcm_b64)
        if not raw:
            return {"ok": True, "skipped": "pcm"}
        if sample_format.lower() != "f32le":
            return {"ok": False, "error": f"unsupported PCM format: {sample_format}"}
        bytes_per_frame = 4 * max(1, int(channels))
        duration = len(raw) / max(1.0, float(rate) * bytes_per_frame)
        player = _get_player()
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(
            loop.run_in_executor(
                _audio_worker,
                player.play_append_pcm,
                raw,
                int(rate),
                int(channels),
                sample_format,
            ),
            timeout=timeout,
        )
        logger.info(
            "pcm_append rate=%s channels=%s duration=%.3fs elapsed=%.3fs ended=%s",
            rate, channels, duration, time.monotonic() - started,
            datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
        )
        return {"ok": True, "continuous": True, "mode": "append", "duration": duration}
    except Exception as exc:  # noqa: BLE001
        logger.warning("pcm_append error: %s", exc)
        return {"ok": False, "error": str(exc)}
