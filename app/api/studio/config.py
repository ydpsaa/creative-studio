"""Paths and settings. All data lives under STUDIO_DATA_DIR; DB stores paths relative to it."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.environ.get("STUDIO_DATA_DIR", PROJECT_ROOT / "studio-data")).expanduser().resolve()
DB_PATH = DATA_DIR / "database" / "studio.sqlite3"
WEB_DIST = PROJECT_ROOT / "app" / "web" / "dist"
HOST = "127.0.0.1"
PORT = int(os.environ.get("STUDIO_PORT", "8765"))

# Bump when the processing plan of a stage changes: job keys depend on it,
# so a changed plan re-runs the stage instead of silently reusing old output.
STAGE_VERSIONS = {
    "probe": "1",
    "audio": "1",
    "transcribe": "1",
    "frames": "1",
    "ocr": "1",
    "audio_features": "1",
    "context_pack": "2",
    "sound_events": "1",
    "page_fetch": "1",
    "image_probe": "1",
    "document_text": "1",
}

DATA_SUBDIRS = [
    "inbox", "originals", "derived", "analyses", "knowledge", "brands", "projects",
    "exports", "database", "cache", "logs", "backups",
]
KNOWLEDGE_CATEGORIES = [
    "scripting", "hooks", "storytelling", "cinematography", "editing", "sound",
    "visual-styles", "workflows", "examples", "templates",
]


def ensure_dirs() -> None:
    for d in DATA_SUBDIRS:
        (DATA_DIR / d).mkdir(parents=True, exist_ok=True)
    for c in KNOWLEDGE_CATEGORIES:
        (DATA_DIR / "knowledge" / c).mkdir(parents=True, exist_ok=True)


def safe_path(rel: str | Path) -> Path:
    """Resolve a DATA_DIR-relative path and refuse anything that escapes DATA_DIR."""
    p = (DATA_DIR / rel).resolve()
    if p != DATA_DIR and DATA_DIR not in p.parents:
        raise ValueError(f"path escapes data dir: {rel}")
    return p


def rel(p: Path) -> str:
    return str(Path(p).resolve().relative_to(DATA_DIR))
