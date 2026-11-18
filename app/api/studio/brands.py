"""Brand profiles and scoped user preferences."""
from __future__ import annotations

import json

from . import config, db

PREF_SCOPES = {"clip": "Только этот ролик", "project": "Текущий проект", "brand": "Бренд", "studio": "Вся студия"}

# Generic defaults. The owner's own brands, modes and voice rules live in config/brands.local.json
# (git-ignored), so the public code ships brand-neutral and a private studio keeps its exact settings.
_DEFAULTS = {
    "seed_brands": [
        {"id": "brand_my", "name": "Мой бренд", "data": {
            "kind": "пример — переименуйте и заполните профиль во вкладке «Бренды»",
            "audience": "", "language": "", "tone_of_voice": "", "visual_rules": [], "allowed_assets": [],
            "prohibitions": [], "examples": [], "accent_color": "",
            "origin": "Начальный пример студии; не результат анализа"}},
    ],
    "seed_prefs": [],
    "project_modes": {
        "standard": "Обычный (по бренду)",
        "to_camera": "Обращение в камеру с демонстрацией",
        "voice_only": "Без меня в кадре — моя запись или синтетический голос, если его разрешает бренд",
        "screen_voice": "Экран продукта + мой голос за кадром",
        "non_real": "Пресет «Non Real»: синтетический рассказчик, без моего лица и голоса",
        "cinematic": "Кинематографичные планы + AI-вставки",
    },
    "voice_sources": {
        "own_recording": "Моя настоящая запись голоса — синтетическая замена не создаётся",
        "own_recording_pending": "Моя запись ещё не предоставлена — сдаём текст и задание на запись, ассет «записать голос»; синтетической замены нет, даже временной",
        "synthetic": "Синтетический голос — в пресете Non Real и в брендах, где его разрешает профиль",
        "on_camera": "Живой звук в кадре",
        "none": "Без голоса",
    },
    "mode_rules": {
        "voice_only": "Автора нет в кадре. Голос — настоящая запись автора или синтетический голос, если его разрешает профиль бренда. Если бренд синтетику не разрешает и записи нет: сдать текст озвучки и задание на запись (ассет «записать голос»), синтетическую замену не создавать.",
        "non_real": "Применять только при явном указании «Non Real». Голос — синтетический нейтральный рассказчик; реального лица, видео и голоса автора нет. Историю несут действие предмета в кадре и текст.",
        "screen_voice": "Запись реального продукта (сайт/приложение) + голос автора за кадром. Демо-данные и концепты подписываются.",
        "to_camera": "Автор в кадре, демонстрация реального продукта.",
    },
}
LOCAL_FILE = config.PROJECT_ROOT / "config" / "brands.local.json"


def _settings() -> dict:
    if LOCAL_FILE.exists():
        return {**_DEFAULTS, **json.loads(LOCAL_FILE.read_text(encoding="utf-8"))}
    return _DEFAULTS


_S = _settings()
SEED_BRANDS: list[dict] = _S["seed_brands"]
SEED_PREFS: list[dict] = _S["seed_prefs"]
PROJECT_MODES: dict = _S["project_modes"]
VOICE_SOURCES: dict = _S["voice_sources"]
MODE_RULES: dict = _S["mode_rules"]


def seed(con) -> dict:
    added = []
    with db.tx(con):
        for b in SEED_BRANDS:
            if not con.execute("SELECT 1 FROM brands WHERE id=?", (b["id"],)).fetchone():
                con.execute("INSERT INTO brands VALUES (?,?,?,?,?)", (b["id"], b["name"], db.j(b["data"]), 1, db.now()))
                added.append(b["id"])
        for p in SEED_PREFS:
            if not con.execute("SELECT 1 FROM preferences WHERE id=?", (p["id"],)).fetchone():
                con.execute("INSERT INTO preferences(id,scope,scope_id,text,active,created_at,origin) VALUES (?,?,?,?,1,?,?)",
                            (p["id"], p["scope"], p["scope_id"], p["text"], db.now(), "user:initial-brief"))
                added.append(p["id"])
    for b in list_brands(con):
        export_brand(con, b["id"])
    return {"added": added}


def list_brands(con):
    return db.rows(con.execute("SELECT * FROM brands ORDER BY name"))


def save_brand(con, bid: str | None, name: str, data: dict) -> dict:
    with db.tx(con):
        if bid and con.execute("SELECT 1 FROM brands WHERE id=?", (bid,)).fetchone():
            con.execute("UPDATE brands SET name=?, data=?, version=version+1, updated_at=? WHERE id=?",
                        (name, db.j(data), db.now(), bid))
        else:
            bid = bid or db.new_id("brand")
            con.execute("INSERT INTO brands VALUES (?,?,?,?,?)", (bid, name, db.j(data), 1, db.now()))
    export_brand(con, bid)
    return db.row(con.execute("SELECT * FROM brands WHERE id=?", (bid,)).fetchone())


def export_brand(con, bid: str):
    b = db.row(con.execute("SELECT * FROM brands WHERE id=?", (bid,)).fetchone())
    d = config.DATA_DIR / "brands" / bid
    d.mkdir(parents=True, exist_ok=True)
    (d / "brand.json").write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")
    prefs = db.rows(con.execute("SELECT * FROM preferences WHERE scope='brand' AND scope_id=? AND active=1", (bid,)))
    L = [f"# {b['name']}", "", f"Версия {b['version']} · {b['updated_at']}", ""]
    for k, v in b["data"].items():
        L.append(f"- **{k}:** {', '.join(v) if isinstance(v, list) else v or '—'}")
    L += ["", "## Предпочтения бренда"] + [f"- {p['text']} (`{p['id']}`)" for p in prefs]
    (d / "brand.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def add_pref(con, scope: str, scope_id: str | None, text: str, replaces: str | None = None) -> dict:
    if scope not in PREF_SCOPES:
        raise ValueError("scope: clip|project|brand|studio")
    if scope != "studio" and not scope_id:
        raise ValueError("Для этой области нужен scope_id (проект или бренд)")
    pid = db.new_id("pref")
    with db.tx(con):
        con.execute("INSERT INTO preferences(id,scope,scope_id,text,active,created_at,origin) VALUES (?,?,?,?,1,?, 'user')",
                    (pid, scope, scope_id, text, db.now()))
        if replaces:
            con.execute("UPDATE preferences SET active=0, replaced_by=? WHERE id=?", (pid, replaces))
        db.index(con, "preference", pid, PREF_SCOPES[scope], text)
    if scope == "brand":
        export_brand(con, scope_id)
    return db.row(con.execute("SELECT * FROM preferences WHERE id=?", (pid,)).fetchone())


def set_pref_active(con, pid: str, active: bool):
    con.execute("UPDATE preferences SET active=? WHERE id=?", (int(active), pid))


def prefs_for_project(con, project: dict) -> list[dict]:
    """Studio-wide + this project's brand + this project (+ one-off clip edits of this project only)."""
    return db.rows(con.execute(
        "SELECT * FROM preferences WHERE active=1 AND (scope='studio' OR (scope='brand' AND scope_id=?) "
        "OR (scope IN ('project','clip') AND scope_id=?)) ORDER BY scope, created_at",
        (project.get("brand_id"), project["id"])))
