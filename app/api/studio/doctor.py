"""./studio doctor — check that this machine can run Creative Studio, and say exactly how to fix what is missing.

Every check returns (status, what, fix): status ok | warn | fail. `fail` = the studio cannot start or a core stage
cannot run; `warn` = a stage will be skipped or downloaded on first use.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import socket
import sqlite3
import sys
from pathlib import Path

from . import config

OK, WARN, FAIL = "ok", "warn", "fail"


def _hub() -> Path:
    return Path(os.environ.get("HF_HUB_CACHE") or Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub")


def _mod(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def checks() -> list[dict]:
    out: list[dict] = []

    def add(group, status, what, fix=""):
        out.append({"group": group, "status": status, "what": what, "fix": fix})

    # --- system
    mac = sys.platform == "darwin"
    arm = platform.machine() == "arm64"
    add("система", OK if mac and arm else WARN, f"{platform.system()} {platform.machine()}",
        "" if mac and arm else "Распознавание речи (MLX) и OCR (Apple Vision) работают только на Mac с Apple Silicon; "
                               "остальное (библиотека, MCP, проекты, STACKER) работает и здесь.")
    v = sys.version_info
    add("система", OK if v >= (3, 12) else FAIL, f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})",
        "" if v >= (3, 12) else "Нужен Python 3.12+: uv venv --python 3.13 .venv")
    free = shutil.disk_usage(config.DATA_DIR.parent if config.DATA_DIR.parent.exists() else Path.home()).free / 1e9
    add("система", OK if free >= 5 else WARN if free >= 2 else FAIL, f"свободно на диске {free:.1f} ГБ",
        "" if free >= 5 else "Модели займут ~2 ГБ, плюс место под ролики. Можно вынести данные: STUDIO_DATA_DIR=/Volumes/Disk/studio-data")

    # --- python packages
    core = {"fastapi": "fastapi", "uvicorn": "uvicorn", "av": "av", "PIL": "pillow", "numpy": "numpy", "httpx": "httpx",
            "mcp": "mcp", "imageio_ffmpeg": "imageio-ffmpeg"}
    missing = [pip for mod, pip in core.items() if not _mod(mod)]
    add("пакеты", FAIL if missing else OK, "основные пакеты" + (f": нет {', '.join(missing)}" if missing else " на месте"),
        "uv pip install --python .venv/bin/python -r requirements.lock.txt" if missing else "")
    stages = {"mlx_whisper": ("речь (mlx-whisper)", "mlx-whisper"), "ocrmac": ("OCR (Apple Vision)", "ocrmac"),
              "transformers": ("звуковая карта (AST)", "transformers"), "librosa": ("звуковые признаки", "librosa")}
    for mod, (label, pip) in stages.items():
        ok = _mod(mod)
        add("пакеты", OK if ok else WARN, f"{label}: {'есть' if ok else 'нет'}", "" if ok else f"uv pip install --python .venv/bin/python {pip}")

    # --- tools and models
    from . import media
    ff = media.ffmpeg_exe()
    add("инструменты", OK if ff else FAIL, f"FFmpeg: {ff or 'не найден'}", "" if ff else "uv pip install imageio-ffmpeg (или brew install ffmpeg)")
    node = shutil.which("node")
    add("инструменты", OK if node else WARN, f"Node: {node or 'не найден'}", "" if node else "Нужен только для сборки интерфейса: https://nodejs.org (20+)")
    dist = config.WEB_DIST / "index.html"
    add("инструменты", OK if dist.exists() else FAIL, "интерфейс собран" if dist.exists() else "интерфейс не собран",
        "" if dist.exists() else "cd app/web && npm install && npm run build")
    hub = _hub()
    wh = (hub / "models--mlx-community--whisper-large-v3-turbo").exists()
    add("модели", OK if wh else WARN, "модель речи whisper-large-v3-turbo " + ("в кэше" if wh else "ещё не скачана"),
        "" if wh else "Скачается сама при первой транскрипции (~1,6 ГБ, нужна сеть).")
    ast = (hub / "models--MIT--ast-finetuned-audioset-10-10-0.4593").exists()
    add("модели", OK if ast else WARN, "модель звуковой карты AST " + ("в кэше" if ast else "ещё не скачана"),
        "" if ast else "Скачается сама при первом анализе звука (~330 МБ).")

    # --- data
    try:
        config.ensure_dirs()
        probe = config.DATA_DIR / "cache" / ".doctor"
        probe.write_text("ok")
        probe.unlink()
        add("данные", OK, f"папка данных: {config.DATA_DIR}")
    except OSError as e:
        add("данные", FAIL, f"папка данных недоступна для записи: {e}", "Проверьте права или задайте STUDIO_DATA_DIR")
    if config.DB_PATH.exists():
        try:
            con = sqlite3.connect(config.DB_PATH)
            ic = con.execute("PRAGMA quick_check").fetchone()[0]
            ver = con.execute("PRAGMA user_version").fetchone()[0]
            con.close()
            from . import db
            add("данные", OK if ic == "ok" else FAIL, f"база: {ic}, схема v{ver} из {len(db.MIGRATIONS)}",
                "" if ic == "ok" else "Восстановите из копии: ./studio restore-check <копия> <пустая папка>")
        except sqlite3.Error as e:
            add("данные", FAIL, f"база не открывается: {e}", "Восстановите из копии (./studio backup делает копии)")
    else:
        add("данные", OK, "база ещё не создана — появится при первом запуске")

    # --- server
    s = socket.socket()
    s.settimeout(.3)
    busy = s.connect_ex((config.HOST, config.PORT)) == 0
    s.close()
    add("сервер", OK, f"порт {config.PORT}: " + ("занят (студия уже запущена?)" if busy else "свободен"),
        "Если это не студия: STUDIO_PORT=8770 ./studio start" if busy else "")

    # --- kits and configs
    kit = config.PROJECT_ROOT / "stacker-kit"
    if kit.exists():
        fonts = list((kit / "fonts").glob("inter-tight-*.ttf"))
        stickers = list((kit / "stickers" / "xerox").glob("G*/*.png"))
        add("STACKER", OK if fonts and stickers else WARN, f"шрифтов {len(fonts)}, стикеров {len(stickers)}",
            "" if fonts and stickers else "Папка stacker-kit неполная — скачайте репозиторий заново")
    try:
        from . import review
        roots = review.roots()
        miss = [k for k, p in roots.items() if not p.exists()]
        add("Ревью", OK if not miss else WARN, f"папки ревью: {len(roots) - len(miss)} из {len(roots)} на месте",
            "" if not miss else f"Нет: {', '.join(miss)} — поправьте config/review_sets.json (это не мешает остальному)")
    except Exception as e:  # noqa: BLE001
        add("Ревью", WARN, f"config/review_sets.json: {e}", "Проверьте JSON")
    return out


ICON = {OK: "✓", WARN: "!", FAIL: "✗"}


def report(as_json: bool = False) -> int:
    res = checks()
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        group = None
        for c in res:
            if c["group"] != group:
                group = c["group"]
                print(f"\n{group.upper()}")
            print(f"  {ICON[c['status']]} {c['what']}")
            if c["fix"]:
                print(f"      → {c['fix']}")
        n_fail = sum(c["status"] == FAIL for c in res)
        n_warn = sum(c["status"] == WARN for c in res)
        print("\n" + ("Всё готово: ./studio start" if not n_fail and not n_warn else
                      f"Ошибок: {n_fail}, предупреждений: {n_warn}." + (" Сначала исправьте ✗." if n_fail else " Студия запустится.")))
    return 1 if any(c["status"] == FAIL for c in res) else 0
