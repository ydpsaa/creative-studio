"""STACKER inside the studio: projects (scenario + voice + optional shot), preview frames, background render.

Projects live in studio-data/stacker/<stk_id>/ (scenario.json, voice.*, talk.*, previews/, out.mp4, status.json).
The renderer is stacker-kit/render_stacker.py — the same code as `./studio stacker`.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import threading
import time
from pathlib import Path

from . import config, db

KIT = config.PROJECT_ROOT / "stacker-kit"
ROOT = config.DATA_DIR / "stacker"
SFX_LIBS = config.DATA_DIR / "sfx-library"
_LOCK = threading.Lock()            # the renderer keeps frame geometry in module globals: one job at a time
_RUNNING: dict[str, threading.Thread] = {}

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".mov", ".mp4", ".m4v", ".ogg", ".flac"}
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".webm"}


def _rs():
    if str(KIT) not in sys.path:
        sys.path.insert(0, str(KIT))
    import render_stacker
    return render_stacker


def _dir(pid: str) -> Path:
    if not pid.startswith("stk_") or "/" in pid or ".." in pid:
        raise ValueError("bad id")
    d = ROOT / pid
    if not d.is_dir():
        raise ValueError(f"нет проекта {pid}")
    return d


def _rel(p: Path) -> str:
    return config.rel(p)


# ------------------------------------------------------------------ catalog
def stickers() -> list[dict]:
    out = []
    for f in sorted((KIT / "stickers" / "xerox").glob("G*/*.png")):
        out.append({"group": f.parent.name, "file": f"{f.parent.name}/{f.name}", "name": f.stem.split("_", 1)[-1].replace("_", " ")})
    return out


def sticker_path(group: str, name: str) -> Path:
    p = (KIT / "stickers" / "xerox" / group / name).resolve()
    if (KIT / "stickers").resolve() not in p.parents or not p.is_file():
        raise ValueError("нет такого стикера")
    return p


def sfx_libraries() -> list[dict]:
    libs = [{"id": "", "label": "генерируемые кодом (без лицензий)"}]
    if SFX_LIBS.exists():
        for d in sorted(SFX_LIBS.iterdir()):
            if d.is_dir() and any(d.glob("*.wav")):
                libs.append({"id": d.name, "label": f"{d.name} ({len(list(d.glob('*.wav')))} звуков)"})
    return libs


# ------------------------------------------------------------------ projects
def _status(d: Path) -> dict:
    f = d / "status.json"
    return json.loads(f.read_text()) if f.exists() else {"state": "draft"}


def _set_status(d: Path, **kw) -> None:
    (d / "status.json").write_text(json.dumps({**_status(d), **kw, "at": db.now()}, ensure_ascii=False))


def list_projects() -> list[dict]:
    if not ROOT.exists():
        return []
    out = []
    for d in sorted(ROOT.glob("stk_*"), key=lambda p: -p.stat().st_mtime):
        sc = json.loads((d / "scenario.json").read_text()) if (d / "scenario.json").exists() else {}
        out.append({"id": d.name, "title": sc.get("title") or d.name, "status": _status(d), "has_video": (d / "out.mp4").exists()})
    return out


def create(title: str) -> dict:
    pid = db.new_id("stk")
    d = ROOT / pid
    d.mkdir(parents=True)
    sc = json.loads((KIT / "examples" / "im_done.json").read_text(encoding="utf-8"))
    sc["title"] = title or "Новый стакер"
    (d / "scenario.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1), encoding="utf-8")
    return get(pid)


def get(pid: str) -> dict:
    d = _dir(pid)
    sc = json.loads((d / "scenario.json").read_text(encoding="utf-8"))
    words = d / "scenario.words.json"
    prev = sorted((d / "previews").glob("*.jpg")) if (d / "previews").exists() else []
    return {"id": pid, "scenario": sc, "status": _status(d),
            "voice": _rel(d / sc["voice"]) if sc.get("voice") and (d / sc["voice"]).exists() else None,
            "talk": _rel(d / sc["talk_video"]) if sc.get("talk_video") and (d / sc["talk_video"]).exists() else None,
            "words": json.loads(words.read_text()) if words.exists() else None,
            "previews": [{"t": float(p.stem.split("_p")[-1]), "path": _rel(p)} for p in prev],
            "video": _rel(d / "out.mp4") if (d / "out.mp4").exists() else None,
            "sfx_libraries": sfx_libraries()}


def _validate(sc: dict) -> dict:
    if not isinstance(sc, dict) or not isinstance(sc.get("pages"), list) or not sc["pages"]:
        raise ValueError("нужен хотя бы один экран заставки (pages)")
    for k, p in enumerate(sc["pages"], 1):
        if not isinstance(p, dict) or not (p.get("rows") or p.get("punch")):
            raise ValueError(f"экран {k}: нужны строки (rows) или слово-удар (punch)")
        if p.get("rows") and (not isinstance(p["rows"], list) or not all(isinstance(r, str) and r.strip() for r in p["rows"])):
            raise ValueError(f"экран {k}: строки должны быть непустым текстом")
        if p.get("sticker"):
            sticker_path(*p["sticker"].split("/", 1))
    if sc.get("format", "9:16") not in ("9:16", "4:5"):
        raise ValueError("format: 9:16 или 4:5")
    if sc.get("sfx") and not (SFX_LIBS / Path(sc["sfx"]).name).is_dir():
        raise ValueError("нет такой библиотеки звуков")
    return sc


def save(pid: str, sc: dict) -> dict:
    d = _dir(pid)
    old = json.loads((d / "scenario.json").read_text(encoding="utf-8"))
    for k in ("voice", "talk_video"):              # file fields are set by upload only
        if old.get(k):
            sc[k] = old[k]
        else:
            sc.pop(k, None)
    (d / "scenario.json").write_text(json.dumps(_validate(sc), ensure_ascii=False, indent=1), encoding="utf-8")
    return get(pid)


def attach(pid: str, kind: str, filename: str, data: bytes) -> dict:
    d = _dir(pid)
    ext = Path(filename).suffix.lower()
    allowed = AUDIO_EXT if kind == "voice" else VIDEO_EXT
    if ext not in allowed:
        raise ValueError(f"тип файла {ext} не подходит; можно: {', '.join(sorted(allowed))}")
    for old in d.glob(f"{kind}.*"):
        old.unlink()
    (d / f"{kind}{ext}").write_bytes(data)
    sc = json.loads((d / "scenario.json").read_text(encoding="utf-8"))
    sc["voice" if kind == "voice" else "talk_video"] = f"{kind}{ext}"
    if kind == "talk" and "talk_start" not in sc and "talk_offset" not in sc:
        sc["talk_offset"] = 0
    (d / "scenario.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "scenario.words.json").unlink(missing_ok=True)
    (d / "_render.words.json").unlink(missing_ok=True)
    return get(pid)


def detach(pid: str, kind: str) -> dict:
    d = _dir(pid)
    sc = json.loads((d / "scenario.json").read_text(encoding="utf-8"))
    key = "voice" if kind == "voice" else "talk_video"
    if sc.get(key):
        (d / sc[key]).unlink(missing_ok=True)
        sc.pop(key)
    (d / "scenario.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1), encoding="utf-8")
    return get(pid)


def _scenario_for_render(d: Path) -> Path:
    """A render copy of the scenario: `sfx` resolved to the library folder; word timings cache travels with it."""
    sc = json.loads((d / "scenario.json").read_text(encoding="utf-8"))
    if sc.get("sfx"):
        sc["sfx"] = str(SFX_LIBS / Path(sc["sfx"]).name)
    tmp = d / "_render.json"
    tmp.write_text(json.dumps(sc, ensure_ascii=False), encoding="utf-8")
    words, cache = d / "scenario.words.json", d / "_render.words.json"
    if words.exists():
        shutil.copy(words, cache)
    else:
        cache.unlink(missing_ok=True)
    return tmp


def _load(d: Path):
    rs = _rs()
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            st = rs.Stacker(_scenario_for_render(d))
    except SystemExit as e:
        raise ValueError(str(e)) from None
    cache = d / "_render.words.json"
    if cache.exists():
        shutil.copy(cache, d / "scenario.words.json")
    warnings = [l.strip(" !") for l in buf.getvalue().splitlines() if "не найдено" in l]
    return st, warnings


def preview(pid: str, times: list[float] | None = None) -> dict:
    d = _dir(pid)
    with _LOCK:
        st, warnings = _load(d)
        if not times:
            times = []
            for pg in st.pages:
                times.append(pg["t0"] + .3)
                if pg.get("t_sticker"):
                    times.append(pg["t_sticker"] + .1)
            last = st.pages[-1]
            if last.get("tear"):
                times.append(last["t_tear"] + .2)
            if st.rest or st.S.get("talk_video"):
                times.append(st.intro_end + 1.0)
        pv = d / "previews"
        shutil.rmtree(pv, ignore_errors=True)
        pv.mkdir()
        for t in sorted(set(round(x, 2) for x in times if 0 <= x < st.total)):
            shot = _shot_at(st, t) if st.S.get("talk_video") and t >= st.intro_end else None
            im = st.frame(t, shot)
            im.resize((540, int(540 * im.height / im.width))).save(pv / f"f_p{t:06.2f}.jpg", quality=86)
        timeline = [{"page": p["k"] + 1, "from": round(p["t0"], 2), "to": round(p["t1"], 2),
                     "kind": "rows" if "rows" in p else "punch"} for p in st.pages]
        info = {"timeline": timeline, "intro_end": round(st.intro_end, 2), "total": round(st.total, 1), "warnings": warnings,
                "simulated": not st.voice and not st.S.get("words")}
    _set_status(d, preview=info)
    return {**get(pid), "preview_info": info}


def _shot_at(st, t: float):
    """One frame of the talking-part video at time t (for previews only)."""
    import subprocess
    from PIL import Image
    rs = _rs()
    ss = float(st.S["talk_start"]) + (t - st.intro_end) if "talk_start" in st.S else t + float(st.S.get("talk_offset", 0))
    vf = f"crop='min(iw,ih*4/3)':'min(ih,iw*3/4)',scale={rs.W}:{rs.BH}"
    r = subprocess.run([rs.ffmpeg(), "-loglevel", "error", "-ss", f"{ss:.3f}", "-i", str(st.base / st.S["talk_video"]), "-frames:v", "1",
                        "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    size = rs.W * rs.BH * 3
    return Image.frombytes("RGB", (rs.W, rs.BH), r.stdout[:size]) if len(r.stdout) >= size else None


def render(pid: str) -> dict:
    d = _dir(pid)
    if pid in _RUNNING and _RUNNING[pid].is_alive():
        return get(pid)
    _validate(json.loads((d / "scenario.json").read_text(encoding="utf-8")))

    def job():
        t0 = time.time()
        _set_status(d, state="rendering", error=None)
        try:
            with _LOCK:
                st, warnings = _load(d)
                tmp = d / "_out.mp4"
                with contextlib.redirect_stdout(io.StringIO()):
                    st.render(tmp)
                tmp.replace(d / "out.mp4")
            _set_status(d, state="done", seconds=round(time.time() - t0, 1), duration=round(st.total, 1), warnings=warnings)
        except Exception as e:  # noqa: BLE001
            _set_status(d, state="failed", error=str(e)[:500])
    th = threading.Thread(target=job, daemon=True)
    _RUNNING[pid] = th
    th.start()
    return get(pid)


def delete(pid: str) -> dict:
    """Projects are user data: move to studio-data/stacker/_trash instead of deleting."""
    d = _dir(pid)
    trash = ROOT / "_trash"
    trash.mkdir(exist_ok=True)
    d.rename(trash / pid)
    return {"ok": True, "moved_to": _rel(trash / pid)}
