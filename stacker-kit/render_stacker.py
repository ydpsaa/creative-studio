"""STACKER renderer — voice-synced word stacks in a 4:3 band + die-cut stickers + intro sound design.

    ./studio stacker examples/im_done.json                 # full video (mp4 next to the scenario)
    ./studio stacker examples/im_done.json --preview 1,2.5 # still frames for a quick look
    python render_stacker.py scenario.json [--out file.mp4]

Scenario (JSON, paths relative to the scenario file):
  voice        your recording (mp3/wav/m4a/mov…). Optional: without it the timing is simulated from `script` (demo).
  words        word timings JSON [{w,t,e}] (optional; otherwise the voice is transcribed with mlx-whisper and cached).
  script       full text (needed only without a voice).
  band_color   intro band colour, e.g. "#0E1E5C" (one colour per episode).
  counter      small series counter shown at the end of the intro, e.g. "01/30" (optional).
  caption_color captions colour of the talking part (default "#FFB547").
  talk_video   your shot for the talking part, cropped into the 4:3 band (optional). talk_offset — seconds of the
               video that match voice t=0 (use 0 when the voice IS the video's own audio). For B-roll/screens that
               are not your recording use talk_start — the second of that video to start from at the cut.
  pages        the intro, page by page:
               {"rows": ["you tell", "everyone", "your idea"], "sticker": "G01_ideas/01_light_bulb.png"}
               {"punch": "but"}                                        — one big word (or several, line by line)
               {"punch": "i'm done.", "sticker": "…", "tear": "done."}  — the last word tears around the sticker
  sfx          true (default, sounds generated in code) | false | folder with your own .wav takes (key_1.wav, key_2.wav…,
               enter, paper, rip, click — or tap/drop/punch/boom/cut for the impact style).
  sfx_style    "typing" (default): words = keyboard keys, punch word = heavier key, sticker = paper, tear = paper rip,
               cut = mouse click. Text never gets bass or punches. "impact": the older snap/thump/boom set.
  format       "9:16" (default, 1080×1920 Reels) | "4:5" (1080×1350, Instagram feed / carousel next to photos).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

KIT = Path(__file__).resolve().parent
W, H, FPS, SR = 1080, 1920, 30, 48000
BY, BH = 554, 812                       # the 4:3 band inside 9:16
FORMATS = {"9:16": (1920, 554, 812),     # Reels / Shorts / TikTok
           "4:5": (1350, 270, 810)}      # Instagram feed & carousels (mixed photo + video posts)
WHITE = (255, 255, 255)
X_STEPS = [0.08, 0.30, 0.14, 0.24]      # each row has its own left offset (left / right / centre…)
ANGLES = [-9, 7, -5, 6, -4]


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def hexrgb(s: str) -> tuple:
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def norm(s: str) -> str:
    return re.sub(r"[^\w']+", "", s.lower().replace("’", "'"))


# ----------------------------------------------------------------- fonts + drawing
_FC: dict = {}


def font(size: int, weight: int = 800) -> ImageFont.FreeTypeFont:
    k = (int(size), weight)
    if k not in _FC:
        _FC[k] = ImageFont.truetype(str(KIT / "fonts" / f"inter-tight-{weight}-normal.ttf"), int(size))
    return _FC[k]


def text_img(s: str, size: int, weight: int = 800, color=WHITE, track: float = -0.06, keep_v: bool = False) -> Image.Image:
    f = font(size, weight)
    adv = [f.getlength(c) for c in s]
    wid = int(sum(adv) + track * size * max(len(s) - 1, 0)) + size
    im = Image.new("RGBA", (max(wid, 1), int(size * 1.5)), (0, 0, 0, 0))
    d, x = ImageDraw.Draw(im), size * .1
    for c, a in zip(s, adv):
        d.text((x, size * .15), c, font=f, fill=color)
        x += a + track * size
    bb = im.getbbox()
    if not bb:
        return im
    return im.crop((bb[0], 0, bb[2], im.height)) if keep_v else im.crop(bb)


def fit_text(s: str, max_w: float, max_h: float, weight: int = 900, start: int = 420) -> Image.Image:
    size = start
    while size > 40:
        im = text_img(s, size, weight, track=-0.07)
        if im.width <= max_w and im.height <= max_h:
            return im
        size -= 10
    return text_img(s, size, weight, track=-0.07)


def paste(f: Image.Image, im: Image.Image, x: float, y: float, anchor: str = "lt") -> None:
    if anchor == "c":
        x, y = x - im.width / 2, y - im.height / 2
    f.alpha_composite(im, (int(x), int(y)))


def sticker_img(path: Path, height: int, angle: float) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    im = im.resize((int(im.width * height / im.height), height), Image.LANCZOS).rotate(angle, expand=True, resample=Image.BICUBIC)
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    sh.putalpha(im.getchannel("A").point(lambda v: v * .45).filter(ImageFilter.GaussianBlur(10)))
    out = Image.new("RGBA", (im.width + 30, im.height + 30), (0, 0, 0, 0))
    out.alpha_composite(sh, (18, 22))
    out.alpha_composite(im, (6, 6))
    return out


def by(fr: float) -> float:
    return BY + fr * BH


# ----------------------------------------------------------------- audio helpers
def decode(path: Path, ch: int = 2, sr: int = SR) -> np.ndarray:
    raw = subprocess.run([ffmpeg(), "-loglevel", "error", "-i", str(path), "-vn", "-f", "f32le", "-ac", str(ch), "-ar", str(sr), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, ch).astype(np.float64)


def transcribe(voice: Path) -> list[dict]:
    try:
        import mlx_whisper
    except ImportError:
        sys.exit("Нет mlx-whisper: запустите через ./studio stacker (окружение студии) или передайте \"words\" в сценарии.")
    audio = decode(voice, 1, 16000)[:, 0].astype(np.float32)
    r = mlx_whisper.transcribe(audio, path_or_hf_repo="mlx-community/whisper-large-v3-turbo", word_timestamps=True)
    return [{"w": w["word"].strip(), "t": round(w["start"], 3), "e": round(w["end"], 3)}
            for s in r["segments"] for w in s.get("words", []) if w["word"].strip()]


def demo_words(text: str) -> list[dict]:
    """No voice yet: ~2.7 words/s with pauses on punctuation, so the layout can be previewed."""
    out, t = [], 0.3
    for w in text.split():
        d = 0.16 + 0.045 * len(norm(w))
        out.append({"w": w, "t": round(t, 3), "e": round(t + d, 3)})
        t += d + 0.06 + (0.8 if w[-1] in ".?!…" else 0.2 if w[-1] in ",;:" else 0)
    return out


# ----------------------------------------------------------------- sound design (generated in code, no licences needed)
def _env(n: int, tau: float) -> np.ndarray:
    return np.exp(-np.arange(n) / (tau * SR))


def _lp(x: np.ndarray, k: int) -> np.ndarray:
    return np.convolve(x, np.ones(k) / k, mode="same")


def _band(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Band-pass by FFT mask (no extra dependencies)."""
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    X[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(X, len(x))


def _tone(freq: float, n: int, tau: float) -> np.ndarray:
    return np.sin(2 * np.pi * freq * np.arange(n) / SR) * _env(n, tau)


def synth(name: str, variant: int = 0) -> np.ndarray:
    """All sounds are generated here, so the kit carries no third-party audio.
    TYPING set (default): text = keyboard/writing, never bass. IMPACT set: the older tap/drop/punch/boom/cut."""
    rng = np.random.default_rng(hash((name, variant)) % 2**32)
    if name in ("key", "enter"):       # mechanical key: plastic click + short body (thock) + quieter key-up
        big = name == "enter"
        n = int((.16 if big else .11) * SR)
        x = np.zeros(n)
        m = int(.004 * SR)
        x[:m] += np.diff(rng.uniform(-1, 1, m + 1)) * _env(m, .0012)                          # contact click
        x += _band(rng.uniform(-1, 1, n), 1800, 4200) * _env(n, .006) * .5                       # plastic ring
        body = (95 if big else 190) * (1 + rng.uniform(-.08, .08))
        x += _tone(body, n, .03 if big else .018) * (.55 if big else .35)                          # body, not bass
        up = int(rng.uniform(.06, .085) * SR) if not big else int(.095 * SR)
        k = min(m, n - up)
        x[up:up + k] += np.diff(rng.uniform(-1, 1, k + 1)) * _env(k, .001) * .35                  # key-up
    elif name == "click":              # mouse click: press + release
        n = int(.12 * SR)
        x = np.zeros(n)
        for at, g in ((0, 1), (int(.065 * SR), .55)):
            m = int(.003 * SR)
            x[at:at + m] += np.diff(rng.uniform(-1, 1, m + 1)) * _env(m, .0008) * g
        x += _band(x, 2500, 9000) * 2
    elif name == "paper":              # a sticker lands: paper slap + crinkle, no low end
        n = int(.32 * SR)
        slap = _band(rng.uniform(-1, 1, n), 700, 5000) * _env(n, .025)
        crk = np.zeros(n)
        for at in rng.integers(0, int(.22 * SR), 70):
            crk[at] += rng.uniform(.3, 1) * rng.choice([-1, 1])
        crk = _band(crk, 2500, 9000) * _env(n, .09)
        x = slap + crk * 1.4
    elif name == "rip":                # the last word tears: paper rip
        n = int(.55 * SR)
        env = np.sin(np.linspace(0, np.pi, n)) ** .7 * np.linspace(1, .5, n)
        imp = np.zeros(n)
        for at in np.sort(rng.integers(0, n, 420)):
            imp[at] += rng.uniform(.2, 1)
        x = (_band(imp, 1200, 7000) * 1.6 + _band(rng.uniform(-1, 1, n), 1500, 6000) * .35) * env
    elif name == "marker":             # marker on paper: short strokes of filtered friction
        n = int(.6 * SR)
        t = np.arange(n) / SR
        strokes = np.clip(np.sin(2 * np.pi * 7 * t + rng.uniform(0, 3)), 0, 1) ** .6
        x = _band(rng.uniform(-1, 1, n), 2500, 8500) * strokes * np.linspace(1, .7, n)
    elif name == "pop":                # small 8-bit blip (pixel sprites)
        n = int(.07 * SR)
        f = np.linspace(700, 1300, n)
        x = np.sign(np.sin(2 * np.pi * np.cumsum(f) / SR)) * _env(n, .025) * .6
    elif name == "tick":               # progress / UI tick
        n = int(.03 * SR)
        x = _tone(3200, n, .004)
    # ---- IMPACT set (sfx_style: "impact")
    elif name == "tap":
        n = int(.05 * SR)
        x = np.diff(rng.uniform(-1, 1, n + 1)) * _env(n, .006)
    elif name == "drop":
        n = int(.45 * SR)
        x = _lp(rng.uniform(-1, 1, n), 6) * _env(n, .03) * 1.4 + np.sin(2 * np.pi * 70 * np.arange(n) / SR) * _env(n, .12)
    elif name == "punch":
        n = int(1.0 * SR)
        f = np.linspace(130, 42, n)
        x = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, .28) + _lp(rng.uniform(-1, 1, n), 12) * _env(n, .08)
    elif name == "boom":
        n = int(1.6 * SR)
        x = np.tanh(2.2 * (np.sin(2 * np.pi * 41 * np.arange(n) / SR) * _env(n, .5)))
    elif name == "cut":
        n = int(.12 * SR)
        x = np.zeros(n)
        for at in (0, int(.035 * SR)):
            m = int(.02 * SR)
            x[at:at + m] += np.diff(rng.uniform(-1, 1, m + 1)) * _env(m, .004)
    else:
        raise ValueError(name)
    x = x / (np.abs(x).max() + 1e-9)
    return np.stack([x, x], 1)


def load_sfx(spec, name: str, variant: int = 0) -> np.ndarray:
    """A folder may hold name.wav or several takes name_1.wav, name_2.wav… (picked in turn, so typing never repeats)."""
    if isinstance(spec, str):
        d = Path(spec)
        takes = sorted(d.glob(f"{name}_*.wav")) or ([d / f"{name}.wav"] if (d / f"{name}.wav").exists() else [])
        if takes:
            x = decode(takes[variant % len(takes)])
            return x / (np.abs(x).max() + 1e-9)
    return synth(name, variant % 6)


# ----------------------------------------------------------------- the stacker
class Stacker:
    def __init__(self, scen_path: Path):
        self.path = scen_path
        self.base = scen_path.parent
        S = self.S = json.loads(scen_path.read_text(encoding="utf-8"))
        global H, BY, BH
        if S.get("format", "9:16") not in FORMATS:
            sys.exit(f"format: {' | '.join(FORMATS)}")
        H, BY, BH = FORMATS[S.get("format", "9:16")]
        self.band = hexrgb(S.get("band_color", "#0E1E5C"))
        self.cap_color = hexrgb(S.get("caption_color", "#FFB547"))
        self.voice = self.base / S["voice"] if S.get("voice") else None
        self.words = self._words()
        self._layout()

    # -- timing
    def _words(self) -> list[dict]:
        S = self.S
        if S.get("words"):
            return json.loads((self.base / S["words"]).read_text())
        if self.voice:
            cache = self.path.with_suffix(".words.json")
            if cache.exists() and cache.stat().st_mtime > self.voice.stat().st_mtime:
                return json.loads(cache.read_text())
            print("Распознаю речь (mlx-whisper, тайминги слов)…", flush=True)
            ws = transcribe(self.voice)
            cache.write_text(json.dumps(ws, ensure_ascii=False, indent=1))
            return ws
        text = S.get("script") or " ".join(" ".join(p.get("rows", [])) + " " + p.get("punch", "") for p in S["pages"])
        return demo_words(text)

    def _match(self, tokens: list[str], start: int) -> tuple[list[dict], int]:
        """Page words → spoken words, in order. Tolerates ASR slips ("yours" for "your"); a word that is not found
        gets the time right after the previous one and a warning (the text on screen is always the page text)."""
        from difflib import SequenceMatcher

        def same(a: str, b: str) -> bool:
            a, b = norm(a), norm(b)
            return a == b or (len(a) > 2 and SequenceMatcher(None, a, b).ratio() >= .6)
        out, i = [], start
        for tok in tokens:
            j = next((j for j in range(i, min(i + 6, len(self.words))) if same(self.words[j]["w"], tok)), None)
            if j is None:
                prev = out[-1]["e"] if out else (self.words[i - 1]["e"] if i > 0 else 0.3)
                near = " ".join(w["w"] for w in self.words[i:i + 6])
                print(f"  ! «{tok}» не найдено в речи (дальше: «{near}») — время подставлено", flush=True)
                out.append({"w": tok, "t": prev + .05, "e": prev + .35, "txt": tok})
                continue
            out.append({**self.words[j], "txt": tok})
            i = j + 1
        return out, i

    def _layout(self) -> None:
        i = 0
        self.pages = []
        demo = not self.S.get("words") and not self.voice
        for k, p in enumerate(self.S["pages"]):
            pg = {"k": k, "sticker": None, "tear": p.get("tear")}
            if "rows" in p:
                pg["rows"] = []
                for r in p["rows"]:
                    toks, i = self._match(r.split(), i)
                    pg["rows"].append(toks)
                flat = [t for r in pg["rows"] for t in r]
            else:
                pg["punch"], i = self._match(p["punch"].split(), i)
                flat = pg["punch"]
            pg["t0"], pg["t_last"] = flat[0]["t"], flat[-1]["e"]
            if p.get("sticker"):
                f = KIT / "stickers" / "xerox" / p["sticker"]
                f = f if f.exists() else self.base / p["sticker"]
                if not f.exists():
                    sys.exit(f"Нет стикера {p['sticker']} (ищу в stacker-kit/stickers/xerox и рядом со сценарием)")
                pg["sticker"] = sticker_img(f, int(BH * (.62 if "rows" in pg else .66)), ANGLES[k % len(ANGLES)])
                pg["t_sticker"] = pg["t_last"] + .35
            self.pages.append(pg)
        for a, b in zip(self.pages, self.pages[1:]):
            a["t1"] = b["t0"]
            if "t_sticker" in a:          # the sticker must land before the page changes, even with no pause
                a["t_sticker"] = min(a["t_sticker"], max(a["t_last"] + .05, a["t1"] - .25))
        last = self.pages[-1]
        hit = last.get("t_sticker", last["t_last"])
        last["t_tear"] = hit + .8
        tail = hit + (1.6 if last["tear"] else .7)
        if demo and i < len(self.words):            # simulated voice: leave room for the tear before talking on
            shift = tail + .2 - self.words[i]["t"]
            self.words = self.words[:i] + [dict(w, t=w["t"] + shift, e=w["e"] + shift) for w in self.words[i:]]
        self.rest = self.words[i:]
        min_end = last["t_tear"] + .6 if last["tear"] else hit + .4
        end = min(tail, self.rest[0]["t"] - .05) if self.rest else tail
        last["t1"] = self.intro_end = float(self.S.get("intro_end", max(end, min_end)))
        voice_end = self.words[-1]["e"] if self.words else self.intro_end
        self.total = max(self.intro_end + (0.8 if not self.rest else 0), voice_end + .6)
        if self.voice:
            self.total = max(self.total, len(decode(self.voice)) / SR + .3)
        self._captions()
        self._prerender()

    def _captions(self) -> None:
        """2–4 words per caption, sentence-aware, no one-word orphans."""
        sents, cur = [], []
        for w in self.rest:            # break at sentence ends and at commas (a caption never runs across a comma)
            cur.append(w)
            if w["w"][-1:] in ".?!…" or (w["w"][-1:] in ",;:" and len(cur) >= 2):
                sents.append(cur)
                cur = []
        if cur:
            sents.append(cur)
        caps = []
        for se in sents:
            n = -(-len(se) // 4)
            cuts = np.linspace(0, len(se), n + 1).round().astype(int)
            for a, b in zip(cuts, cuts[1:]):
                ws = se[a:b]
                caps.append({"t": max(ws[0]["t"], self.intro_end), "e": ws[-1]["e"], "s": " ".join(w["w"] for w in ws).lower().rstrip(".,:;")})
        for a, b in zip(caps, caps[1:]):
            a["e"] = b["t"]
        if caps:
            caps[-1]["e"] = self.total
        self.caps = [dict(c, im=text_img(c["s"], 78, 700, self.cap_color, -0.05)) for c in caps]

    def _prerender(self) -> None:
        for pg in self.pages:
            if "rows" in pg:
                n = len(pg["rows"])
                pg["row_ims"] = []
                for ri, row in enumerate(pg["rows"]):
                    ims = [text_img(t["txt"].lower(), 100, keep_v=True) for t in row]
                    width = sum(im.width for im in ims) + 24 * (len(ims) - 1)
                    x = min(X_STEPS[ri % len(X_STEPS)] * W, W * .94 - width)
                    if pg["sticker"] is not None:
                        x = min(x, W * .56 - width * .5)
                    y = .5 + (ri - (n - 1) / 2) * .155
                    pg["row_ims"].append((ims, max(x, W * .04), y))
            else:
                words = [t["txt"].upper() for t in pg["punch"]]
                lines = words
                if len(words) == 1 and len(norm(words[0])) >= 7:      # long word → two lines (COMM / ANDS)
                    h = len(words[0]) // 2
                    lines = [words[0][:h], words[0][h:]]
                mh = BH * .8 / len(lines)
                pg["punch_ims"] = [fit_text(s, W * .86, mh) for s in lines]
                # which line appears when: one line per word, or both halves of a split word at once
                pg["punch_t"] = [t["t"] for t in pg["punch"]] if len(lines) == len(pg["punch"]) else [pg["punch"][0]["t"]] * len(lines)
            if pg["tear"]:
                s = pg["tear"].upper()
                h = max(1, len(s) // 2)
                pg["tear_ims"] = (text_img(s[:h], 170, 900, track=-0.07).rotate(90, expand=True), text_img(s[h:], 170, 900, track=-0.07))
        self.counter = text_img(self.S["counter"], 40, 700, track=-0.03) if self.S.get("counter") else None
        self.ghost = text_img("ВАШ КАДР · 4:3 · YOUR SHOT", 30, 500, (90, 90, 96), -0.01)

    # -- drawing
    def draw_page(self, f: Image.Image, pg: dict, t: float) -> None:
        stk = pg["sticker"]
        if pg["tear"] and t >= pg["t_tear"] and stk is not None:
            a, b = pg["tear_ims"]
            paste(f, stk, W * .5, by(.5), "c")
            paste(f, a, W * .5 - stk.width / 2 - a.width * .45, by(.40), "c")
            paste(f, b, W * .5 + stk.width * .34, by(.80), "c")
            if self.counter:
                paste(f, self.counter, W / 2, by(.955), "c")
            return
        if "rows" in pg:
            for (ims, x, yf), row in zip(pg["row_ims"], pg["rows"]):
                for im, tok in zip(ims, row):
                    if t >= tok["t"]:
                        paste(f, im, x, by(yf) - im.height * .8)
                    x += im.width + 24
            if stk is not None and t >= pg["t_sticker"]:
                paste(f, stk, W * .76, by(.5), "c")
        else:
            ims = pg["punch_ims"]
            total_h = sum(im.height for im in ims) + 20 * (len(ims) - 1)
            y = by(.5) - total_h / 2
            for im, on in zip(ims, pg["punch_t"]):
                if t >= on:
                    paste(f, im, W / 2 - im.width / 2, y)
                y += im.height + 20
            if stk is not None and t >= pg["t_sticker"]:
                paste(f, stk, W * .64, by(.55), "c")

    def frame(self, t: float, shot: Image.Image | None = None) -> Image.Image:
        f = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        d = ImageDraw.Draw(f)
        if t < self.intro_end:
            d.rectangle((0, BY, W, BY + BH), fill=self.band)
            pg = next((p for p in self.pages if p["t0"] - .05 <= t < p["t1"]), None)
            if pg is None and t < self.pages[0]["t0"]:
                pg = None
            if pg is not None:
                self.draw_page(f, pg, t)
        else:
            if shot is not None:
                f.paste(shot, (0, BY))
            else:
                d.rectangle((0, BY, W, BY + BH), fill=(26, 26, 28))
                paste(f, self.ghost, 40, BY + 30)
            for c in self.caps:
                if c["t"] <= t < c["e"]:
                    paste(f, c["im"], W / 2, by(.62), "c")
        return f.convert("RGB")

    # -- sound
    def events(self) -> list[tuple[str, float, float]]:
        if self.S.get("sfx_style", "typing") == "typing":
            # text = keyboard (one key per word, a heavier key on the punch word); paper for stickers; rip for the tear
            ev = []
            for pg in self.pages:
                toks = [t for r in pg["rows"] for t in r] if "rows" in pg else []
                ev += [("key", t["t"], .5) for t in toks]
                if "punch" in pg:
                    ev += [("enter", t["t"], .6) for t in pg["punch"]]
                if pg["sticker"] is not None:
                    ev.append(("paper", pg["t_sticker"], .6))
                if pg["tear"]:
                    ev.append(("rip", pg["t_tear"], .55))
            if self.rest or self.S.get("talk_video"):
                ev.append(("click", self.intro_end, .5))
            return ev + list(self.S.get("_extra_events", []))
        ev = []
        for pg in self.pages:
            if "rows" in pg:
                ev += [("tap", r[0]["t"], .55) for r in pg["rows"]]
            else:
                ev.append(("punch", pg["punch"][0]["t"], .55))
            if pg["sticker"] is not None:
                ev.append(("drop", pg["t_sticker"], .7))
            if pg["tear"]:
                ev.append(("boom", pg["t_tear"], .6))
        if self.rest or self.S.get("talk_video"):
            ev.append(("cut", self.intro_end, .45))
        return ev

    def audio(self, out: Path) -> None:
        n = int(self.total * SR) + SR
        mix = np.zeros((n, 2))
        if self.voice:
            v = decode(self.voice)
            mix[:len(v)] += v[:n]
            mix = mix / (np.abs(mix).max() + 1e-9) * .89
        spec = self.S.get("sfx", True)
        if spec is not False:
            if isinstance(spec, str):
                spec = str(self.base / spec)
            cache: dict = {}
            seen: dict = {}
            for name, at, g in self.events():
                v = seen[name] = seen.get(name, -1) + 1
                x = cache.setdefault((name, v % 6), load_sfx(spec, name, v))
                i = max(0, int(at * SR))
                j = min(n, i + len(x))
                mix[i:j] += x[: j - i] * g * .8
        mix = np.tanh(mix * 1.05) / np.tanh(1.05) * .84
        with wave.open(str(out), "wb") as wv:
            wv.setnchannels(2), wv.setsampwidth(2), wv.setframerate(SR)
            wv.writeframes((np.clip(mix, -1, 1) * 32767).astype(np.int16).tobytes())

    # -- talking part shot
    def shots(self):
        tv = self.S.get("talk_video")
        return self._shots(self.base / tv) if tv else None

    def _shots(self, src: Path):
        ss = (float(self.S["talk_start"]) if "talk_start" in self.S       # B-roll / screens: play from this second
              else self.intro_end + float(self.S.get("talk_offset", 0)))  # own recording: stay in sync with the voice
        vf = f"fps={FPS},crop='min(iw,ih*4/3)':'min(ih,iw*3/4)',scale={W}:{BH}"
        p = subprocess.Popen([ffmpeg(), "-loglevel", "error", "-ss", f"{ss:.3f}", "-i", str(src), "-an", "-vf", vf,
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        size = W * BH * 3
        last = None
        while True:
            buf = p.stdout.read(size)
            if len(buf) == size:
                last = Image.frombytes("RGB", (W, BH), buf)
            yield last

    def render(self, out: Path) -> None:
        wav = out.with_suffix(".tmp.wav")
        self.audio(wav)
        p = subprocess.Popen([ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                              "-i", "-", "-i", str(wav), "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac",
                              "-b:a", "192k", "-shortest", str(out)], stdin=subprocess.PIPE)
        shots = self.shots()
        for i in range(int(self.total * FPS)):
            t = i / FPS
            shot = next(shots) if shots is not None and t >= self.intro_end else None
            p.stdin.write(self.frame(t, shot).tobytes())
        p.stdin.close()
        p.wait()
        wav.unlink(missing_ok=True)
        print(f"Готово: {out}  ({self.total:.1f} с, заставка до {self.intro_end:.2f} с)")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="STACKER: слова в такт голосу + стикеры + звуки заставки")
    ap.add_argument("scenario")
    ap.add_argument("--out")
    ap.add_argument("--preview", help="кадры через запятую, например 1,2.5,4")
    a = ap.parse_args(argv)
    sc = Path(a.scenario).resolve()
    st = Stacker(sc)
    print("Заставка:", ", ".join(f"стр.{p['k'] + 1} {p['t0']:.2f}–{p['t1']:.2f}" for p in st.pages), f"· конец {st.intro_end:.2f} · всего {st.total:.1f} с")
    if a.preview:
        for s in a.preview.split(","):
            o = sc.with_name(f"{sc.stem}_p{float(s):05.2f}.jpg")
            st.frame(float(s)).save(o, quality=88)
            print(o)
        return
    st.render(Path(a.out).resolve() if a.out else sc.with_suffix(".mp4"))


if __name__ == "__main__":
    main()
