"""Thin wrappers over FFmpeg / PyAV. Prefers system binaries, falls back to the bundled imageio-ffmpeg."""
from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache
def ffmpeg_exe() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def run_ffmpeg(args: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    exe = ffmpeg_exe()
    if not exe:
        raise RuntimeError("FFmpeg не найден")
    return subprocess.run([exe, "-hide_banner", "-nostdin", *args], capture_output=True, text=True,
                          timeout=timeout)


def probe(path: Path) -> dict:
    """Technical description via PyAV (works without ffprobe)."""
    import av
    info: dict = {"streams": []}
    with av.open(str(path)) as c:
        info["format"] = c.format.name
        info["duration"] = round(c.duration / 1_000_000, 3) if c.duration else None
        info["bit_rate"] = c.bit_rate
        for s in c.streams:
            cc = s.codec_context
            d = {"index": s.index, "type": s.type, "codec": cc.name if cc else None}
            if s.type == "video":
                d.update(width=cc.width, height=cc.height,
                         fps=float(s.average_rate) if s.average_rate else None,
                         frames=s.frames or None, pix_fmt=cc.pix_fmt)
            elif s.type == "audio":
                d.update(sample_rate=cc.sample_rate, channels=cc.channels,
                         layout=cc.layout.name if cc.layout else None)
            if s.duration and s.time_base:
                d["duration"] = round(float(s.duration * s.time_base), 3)
            info["streams"].append(d)
        # decode check: can we actually decode the first video/audio frame?
        info["decodable"] = {}
        for t in ("video", "audio"):
            ss = [s for s in c.streams if s.type == t]
            if not ss:
                continue
            try:
                c.seek(0)
                for f in c.decode(ss[0]):
                    info["decodable"][t] = True
                    break
            except Exception as e:  # noqa: BLE001
                info["decodable"][t] = f"error: {e}"
    return info


def extract_frame(src: Path, t: float, out: Path, width: int = 540) -> bool:
    r = run_ffmpeg(["-ss", f"{max(t, 0):.3f}", "-i", str(src), "-frames:v", "1",
                    "-vf", f"scale={width}:-2", "-q:v", "3", "-y", str(out)], timeout=120)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0


def scene_changes(src: Path, threshold: float = 0.3) -> list[tuple[float, float]]:
    """Returns (time, scene_score) for frames whose scene score exceeds threshold."""
    r = run_ffmpeg(["-i", str(src), "-an", "-vf",
                    f"select='gt(scene,{threshold})',metadata=print", "-f", "null", "-"])
    out, t = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
        m2 = re.search(r"lavfi\.scene_score=([\d.]+)", line)
        if m2 and t is not None:
            out.append((round(t, 3), round(float(m2.group(1)), 3)))
            t = None
    return out


def extract_audio(src: Path, out: Path) -> subprocess.CompletedProcess:
    return run_ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                       "-y", str(out)])


def loudness(src: Path) -> dict:
    r = run_ffmpeg(["-i", str(src), "-vn", "-af", "ebur128=framelog=quiet", "-f", "null", "-"])
    res = {}
    for key, pat in (("integrated_lufs", r"I:\s+(-?[\d.]+) LUFS"), ("lra_lu", r"LRA:\s+(-?[\d.]+) LU"),
                     ("true_peak_dbfs", r"Peak:\s+(-?[\d.]+) dBFS")):
        m = re.findall(pat, r.stderr)
        if m:
            res[key] = float(m[-1])
    return res


def silences(src: Path, noise_db: int = -35, min_d: float = 0.3) -> list[dict]:
    r = run_ffmpeg(["-i", str(src), "-vn", "-af", f"silencedetect=noise={noise_db}dB:d={min_d}",
                    "-f", "null", "-"])
    out, start = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            out.append({"start": round(start, 2), "end": round(float(m.group(1)), 2)})
            start = None
    return out


def mime_for(path: Path) -> str:
    import mimetypes
    m, _ = mimetypes.guess_type(path.name)
    return m or "application/octet-stream"
