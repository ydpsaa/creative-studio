"""Cut your own recording into single sound takes for the STACKER renderer.

Record 20–40 s of the sound you want (typing on your keyboard, a marker on paper, paper crumples) on your phone or Mac,
then:
    .venv/bin/python stacker-kit/tools/make_sfx_takes.py typing.m4a --name key --out my-sfx
    .venv/bin/python stacker-kit/tools/make_sfx_takes.py paper.m4a  --name paper --out my-sfx --len 0.35 --count 3
and put "sfx": "my-sfx" (path relative to the scenario, or absolute) in your scenario.
Names the renderer uses: key (every word), enter (punch word), paper (sticker), rip (torn word), click (cut to the talking part).
Each name can have several takes (key_1.wav, key_2.wav…): they are used in turn so typing never sounds machine-made.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import render_stacker as RS  # noqa: E402

SR = RS.SR


def onsets(y: np.ndarray, thresh_db: float = -30, gap: float = .05) -> list[int]:
    """Simple energy onset detector (no librosa needed): rising edges of a 5 ms envelope above the threshold."""
    hop = int(.005 * SR)
    env = np.sqrt(np.convolve(y ** 2, np.ones(hop) / hop, "same"))
    lvl = 20 * np.log10(env + 1e-9) - 20 * np.log10(env.max() + 1e-9)
    above = lvl > thresh_db
    out, last = [], -10**9
    for i in np.flatnonzero(above[1:] & ~above[:-1]) + 1:
        if i - last > gap * SR:
            out.append(int(max(0, i - .002 * SR)))
            last = i
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("recording")
    ap.add_argument("--name", required=True, help="key | enter | paper | rip | click (or any name you use)")
    ap.add_argument("--out", required=True, help="folder for the takes")
    ap.add_argument("--len", type=float, default=.09, help="seconds per take (key 0.09, paper 0.35, rip 0.6)")
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--thresh", type=float, default=-30, help="dB below the loudest moment that counts as a hit")
    a = ap.parse_args()
    raw = subprocess.run([RS.ffmpeg(), "-loglevel", "error", "-i", a.recording, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    y = np.frombuffer(raw, np.float32).astype(np.float64)
    n = int(a.len * SR)
    ons = onsets(y, a.thresh)
    takes = []
    for k, i in enumerate(ons):
        nxt = ons[k + 1] if k + 1 < len(ons) else len(y)
        if nxt - i < n * .8:            # overlaps the next hit: not a clean single take
            continue
        seg = y[i:i + n].copy()
        takes.append((np.abs(seg).max(), seg))
    takes = [s for _, s in sorted(takes, key=lambda t: -t[0])[: a.count]]
    if not takes:
        sys.exit(f"Не нашёл отдельных ударов (найдено касаний: {len(ons)}). Запишите с паузами между нажатиями или уменьшите --len.")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for k, s in enumerate(takes, 1):
        fade = min(len(s) // 3, int(.01 * SR))
        s[-fade:] *= np.linspace(1, 0, fade)
        s = s / (np.abs(s).max() + 1e-9) * .9
        with wave.open(str(out / f"{a.name}_{k}.wav"), "wb") as w:
            w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR)
            w.writeframes((np.repeat(s[:, None], 2, 1) * 32767).astype(np.int16).tobytes())
    print(f"{len(takes)} дублей «{a.name}» → {out}/  (касаний в записи: {len(ons)})")


if __name__ == "__main__":
    main()
