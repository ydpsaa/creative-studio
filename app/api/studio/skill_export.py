"""Export of the portable creative skill package and its self-containment checks.

The package must work WITHOUT the studio: no absolute paths, no localhost, no database, no media.
Static parts (workflow, templates, quality rules) live in config/skill_package/; methods, examples,
brands, preferences and provenance are generated from the database at export time."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

from . import brands as brands_mod
from . import config, db, knowledge

DEFAULT_NAME = "dolishx-creative-method"
STATIC = config.PROJECT_ROOT / "config" / "skill_package"
FORBIDDEN = [(r"/Users/|/home/|[A-Za-z]:\\\\", "абсолютный путь"), (r"localhost|127\.0\.0\.1", "localhost"),
             (r"(?i)sqlite", "база данных"), (r"studio-data|\./studio\b|derived/|originals/", "путь студии"),
             (r"\bsrc_\d{6}_[0-9a-f]{6}/", "путь к файлам источника"), (r"contact_\d|dense-", "внутренний термин студии")]
REQUIRED = ["SKILL.md", "README.md", "AGENTS.md", "methods/INDEX.md", "methods/SELECTION.md", "examples/INDEX.md",
            "preferences.md", "brands/modes.md", "quality/rubric.md", "quality/evidence.md", "templates/storyboard.csv",
            "provenance/manifest.json", "provenance/LIMITATIONS.md"]
EV = {"author_explained": "автор объяснил", "observed": "наблюдается", "hypothesis": "гипотеза",
      "user_preference": "предпочтение пользователя", "project_confirmed": "подтверждено проектом"}
# task → (title, categories, tags) for methods/SELECTION.md
TASKS = [
    ("Начало ролика (первые 1–3 с)", {"hooks"}, {"hook", "intro", "contrarian"}),
    ("Удержание и перезацеп в середине", set(), {"retention", "re-hook", "open-loop", "suspense"}),
    ("Структура сценария и связки событий", {"scripting", "storytelling"}, {"storytelling", "scripting", "list-format", "series"}),
    ("Финал и CTA", set(), {"cta", "saves", "ending", "teaser", "endcard", "comment-keyword"}),
    ("Монтаж, переходы, движение", {"editing"}, {"motion-graphics", "split-screen", "flash", "chapters"}),
    ("Субтитры и типографика", set(), {"captions", "typography", "kinetic-type"}),
    ("Визуальный стиль и цвет", {"visual-styles"}, {"monochrome", "color-grade", "visual-style", "3d", "pixel"}),
    ("Звук: подложка, паузы, акценты, SFX", {"sound"}, {"sound", "sfx", "sound-design", "sound-pack"}),
    ("Производство без автора в кадре (voice-only)", set(), {"voice-only", "no-face", "ai-visuals", "screen-recording"}),
    ("Показ работы / портфолио / до-после", {"examples", "templates"}, {"showcase", "portfolio", "before-after", "behind-the-scenes"}),
]


def _slug(s: str) -> str:
    tr = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
                                                     "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
    s = "".join(tr.get(ch, ch) for ch in s.lower())
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40]


def _t(t) -> str:
    if t is None:
        return "—"
    m, s = divmod(float(t), 60)
    return f"{int(m)}:{s:04.1f}"


def _clean(text: str | None) -> str:
    """Remove studio-internal references from free text (frame file names, studio commands)."""
    if not text:
        return ""
    text = re.sub(r"frames/d(\d{6})\.jpg", lambda m: f"кадр {int(m.group(1)) / 1000:.1f} с", text)
    text = re.sub(r"frames/f\d{3}_(\d{4}\.\d{2})\.jpg", lambda m: f"кадр {float(m.group(1)):.1f} с", text)
    text = re.sub(r"\./studio [\w-]+[^\n]*", "(инструмент студии)", text)
    text = re.sub(r"contact_\d+(?:\.jpg)?(?:[–-]\d+)?", "контактные листы", text)
    text = re.sub(r"dense-прогон\w*", "плотных проходов", text)
    text = re.sub(r"\bf\d{3}\b", "кадр", text)
    return text


def _example_slugs(con, cards) -> dict[str, str]:
    sids = sorted({r["source_id"] for k in cards for r in k["refs"]},
                  key=lambda x: con.execute("SELECT title FROM sources WHERE id=?", (x,)).fetchone()["title"])
    out = {}
    for i, sid in enumerate(sids, 1):
        title = con.execute("SELECT title FROM sources WHERE id=?", (sid,)).fetchone()["title"]
        hint = _slug(title.split("—", 1)[-1]) if "—" in title else _slug(title)
        out[sid] = f"ex{i:02d}-{hint[:32].strip('-')}"
    return out


def _method_md(k: dict, ex: dict[str, str], con) -> str:
    L = [f"# {k['title']}", "",
         f"`{k['id']}` · версия {k['version']} · **{EV.get(k['evidence'], k['evidence'])}**"
         + (" · ⚠ требует проверки" if "needs-review" in (k.get("tags") or []) else "")
         + f" · тип: {knowledge.KINDS.get(k['kind'], k['kind'])} · категория: {k['category']}",
         f"Область: бренд `{k.get('brand_scope') or 'all'}` · формат: {k.get('format_scope') or '—'} · язык: {k.get('language') or '—'}", ""]
    for label, key in (("Суть", "statement"), ("Задача", "task"), ("Когда применять", "when_use"),
                       ("Когда НЕ применять", "when_not"), ("Условия", "conditions")):
        if k.get(key):
            L += [f"## {label}", _clean(k[key]), ""]
    if k.get("steps"):
        L += ["## Шаги"] + [f"{i}. {_clean(s)}" for i, s in enumerate(k["steps"], 1)] + [""]
    for label, key in (("Пример", "example"), ("Типичная ошибка", "mistake"), ("Ограничения", "limitations")):
        if k.get(key):
            L += [f"## {label}", _clean(k[key]), ""]
    L += ["## Источник (для понимания, не для копирования)"]
    for r in k["refs"]:
        slug = ex.get(r["source_id"])
        where = f"{_t(r['t_start'])}–{_t(r['t_end'])}" if r.get("t_start") is not None else (r.get("page") or "")
        L.append(f"- [{slug}](../../examples/{slug}.md) · {where}" + (f" · «{r['quote']}»" if r.get("quote") else ""))
    L += ["", f"Тэги: {', '.join(t for t in (k.get('tags') or []))}", ""]
    return "\n".join(L)


def _example_md(con, sid: str, slug: str, card_ids: list[str]) -> str:
    s = db.row(con.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone())
    an = con.execute("SELECT * FROM analyses WHERE source_id=? ORDER BY version DESC LIMIT 1", (sid,)).fetchone()
    d = json.loads(an["data"]) if an else {}
    L = [f"# Пример {slug}: {s['title'].split('—', 1)[-1].strip()}", "",
         f"Исходный ролик: {s['kind']}, {s['duration']:.1f} с, язык {s['language']}"
         + (f", ссылка: {s['url']}" if s.get("url") else "") + f". Статус использования: **{s['usage_status']}** — "
         "только как референс метода; материал ролика не использовать.",
         f"Разбор версии {an['version'] if an else '—'} ({an['origin'] if an else '—'}).", ""]
    if d.get("summary"):
        L += ["## Кратко", _clean(d["summary"]), ""]
    st = d.get("structure") or {}
    if st:
        names = {"hook": "Hook", "development": "Развитие", "proof": "Доказательство", "turn": "Поворот", "ending": "Завершение", "cta": "CTA"}
        L += ["## Конструкция"] + [f"- **{n}:** " + (f"{_t(v.get('t_start'))}–{_t(v.get('t_end'))} — {_clean(v.get('description'))}"
                                                    if isinstance(v, dict) else "нет") for key, n in names.items() for v in [st.get(key)]] + [""]
    m = d.get("methodology")
    if m:
        L += ["## Методика, которую даёт ролик"] + [f"{i}. [{_t(x.get('t_start'))}] {_clean(x.get('action'))}"
                                                   for i, x in enumerate(m.get("steps") or [], 1)] + [""]
    if d.get("editing"):
        L += ["## Монтаж и постановка"] + [f"- **{k}:** {_clean(v)}" for k, v in d["editing"].items()] + [""]
    if d.get("sound"):
        L += ["## Звук (оценки звуковой модели, не прослушано человеком)"] + [f"- {_clean(o)}" for o in d["sound"].get("observations", [])] + [""]
    if d.get("timeline"):
        L += ["## Шкала", "", "| Время | Говорится | Видно | Чему учит |", "|---|---|---|---|"]
        for t in d["timeline"]:
            L.append(f"| {_t(t.get('t_start'))}–{_t(t.get('t_end'))} | " + " | ".join(
                _clean(str(t.get(c) or "—")).replace("|", "/").replace("\n", " ") for c in ("speech", "visual", "teaches")) + " |")
        L.append("")
    nc = (d.get("coverage") or {}).get("not_covered") or []
    if nc:
        L += ["## Что не проверено"] + [f"- {_clean(x)}" for x in nc] + [""]
    L += ["## Методы из этого примера"] + [f"- [`{c}`](../methods/INDEX.md#{c})" for c in card_ids] + [""]
    return "\n".join(L)


def _short(text: str, n: int) -> str:
    """Краткое описание для оглавления: обрезка только по границе слова и с многоточием."""
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0].rstrip(",.;:—- ") + "…"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def export(con, name: str = DEFAULT_NAME, out_root: Path | None = None) -> dict:
    root = out_root or (config.DATA_DIR / "exports" / "skills" / name)
    root.mkdir(parents=True, exist_ok=True)
    prev = sorted([p for p in root.glob("v*") if p.is_dir()], key=lambda p: int(p.name[1:]))
    v = int(prev[-1].name[1:]) + 1 if prev else 1
    import datetime as _dt
    local_date = _dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    version = f"{v}.{local_date.replace('-', '')}"
    pkg = root / f"v{v}" / name
    shutil.copytree(STATIC, pkg)
    cards = [knowledge.get(con, r["id"]) for r in con.execute(
        "SELECT id FROM knowledge WHERE status='active' AND is_test=0 ORDER BY category, title")]
    ex = _example_slugs(con, cards)
    (pkg / "methods").mkdir(exist_ok=True)
    (pkg / "examples").mkdir(exist_ok=True)
    # methods
    for k in cards:
        p = pkg / "methods" / k["category"] / f"{k['id']}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_method_md(k, ex, con), encoding="utf-8")
    idx = ["# Методы", "", "Обозначения: A — автор объяснил, O — наблюдается, H — гипотеза, ⚠ — требует проверки.", ""]
    ab = {"author_explained": "A", "observed": "O", "hypothesis": "H", "user_preference": "U", "project_confirmed": "P"}
    cat = None
    for k in cards:
        if k["category"] != cat:
            cat = k["category"]
            idx += ["", f"## {cat}", ""]
        warn = " ⚠" if "needs-review" in (k.get("tags") or []) else ""
        idx.append(f"- <a id=\"{k['id']}\"></a>[{ab.get(k['evidence'])}{warn}] [{k['title']}]({k['category']}/{k['id']}.md) — `{k['id']}`")
    (pkg / "methods" / "INDEX.md").write_text("\n".join(idx) + "\n", encoding="utf-8")
    sel = ["# Выбор приёма по задаче", "",
           "Найдите задачу ролика → прочитайте карточки → проверьте «когда НЕ применять». Порядок внутри задачи: сначала *автор объяснил*, затем *наблюдается*, затем гипотезы.", ""]
    order = {"author_explained": 0, "user_preference": 0, "observed": 1, "hypothesis": 2}
    for title, cats, tags in TASKS:
        hit = [k for k in cards if k["category"] in cats or set(k.get("tags") or []) & tags]
        hit.sort(key=lambda k: (order.get(k["evidence"], 3), k["title"]))
        sel += [f"## {title}", ""] + ([f"- [{EV.get(k['evidence'])}{' ⚠' if 'needs-review' in (k.get('tags') or []) else ''}] "
                                       f"[{k['title']}]({k['category']}/{k['id']}.md) — {_short(_clean(k.get('task') or k.get('statement') or ''), 160)}"
                                       for k in hit] or ["_Изученных приёмов нет — решение будет собственным._"]) + [""]
    (pkg / "methods" / "SELECTION.md").write_text("\n".join(sel) + "\n", encoding="utf-8")
    # examples
    (pkg / "examples").mkdir(exist_ok=True)
    eidx = ["# Примеры (разборы исходных роликов)", "", "Нужны, чтобы понять, как приём выглядит в реальном ролике. Переносится механика, не содержание.", ""]
    ex_meta = []
    for sid, slug in ex.items():
        cids = [k["id"] for k in cards if any(r["source_id"] == sid for r in k["refs"])]
        (pkg / "examples" / f"{slug}.md").write_text(_example_md(con, sid, slug, cids), encoding="utf-8")
        an = con.execute("SELECT version, origin FROM analyses WHERE source_id=? ORDER BY version DESC LIMIT 1", (sid,)).fetchone()
        s = con.execute("SELECT duration FROM sources WHERE id=?", (sid,)).fetchone()
        t = con.execute("SELECT title FROM sources WHERE id=?", (sid,)).fetchone()["title"]
        eidx.append(f"- [{slug}]({slug}.md) — {t.split('—', 1)[-1].strip()} · {s['duration']:.0f} с · методов: {len(cids)}")
        ex_meta.append({"slug": slug, "source_id": sid, "analysis_version": an["version"] if an else None,
                        "analysis_origin": an["origin"] if an else None, "methods": cids})
    (pkg / "examples" / "INDEX.md").write_text("\n".join(eidx) + "\n", encoding="utf-8")
    # brands & modes
    (pkg / "brands").mkdir(exist_ok=True)
    bmeta = []
    for b in brands_mod.list_brands(con):
        dta = b["data"]
        bp = db.rows(con.execute("SELECT * FROM preferences WHERE scope='brand' AND scope_id=? AND active=1", (b["id"],)))
        L = [f"# {b['name']}", "", f"ID `{b['id']}` · версия профиля {b['version']} · обновлён {_dt.datetime.fromisoformat(b['updated_at']).astimezone().strftime('%Y-%m-%d')}", ""]
        labels = {"kind": "Тип", "spelling": "Написание", "product": "Продукт / о чём бренд", "language": "Язык",
                  "default_mode": "Режим по умолчанию", "modes": "Доступные режимы", "audience": "Аудитория",
                  "tone_of_voice": "Tone of voice", "principle": "Принцип подачи", "accent_color": "Акцентный цвет",
                  "palette": "Палитра", "typography": "Шрифты", "logo": "Логотип", "visual_rules": "Визуальные правила",
                  "allowed_assets": "Разрешённые ассеты", "prohibitions": "Запреты", "examples": "Примеры/выпуски"}
        src = dta.get("field_sources") or {}
        L += ["| Поле | Значение | Источник |", "|---|---|---|"]
        for key, lab in labels.items():
            val = dta.get(key)
            if isinstance(val, list):
                val = "; ".join(val)
            if key == "accent_color" and val and "уточнить" in val:
                val += " — ⚠ не подтверждено владельцем"
            L.append(f"| {lab} | {(_clean(str(val)) if val else 'не задано — не выдумывать, пометить допущением').replace('|', '/')} | "
                     f"{_clean(src.get(key, 'профиль бренда' if val else '—')).replace('|', '/')} |")
        for pname, pr in (dta.get("presets") or {}).items():
            L += ["", f"## Пресет `{pname}`"] + [f"- **{k}:** {_clean(str(v))}" for k, v in pr.items()]
        L += ["", "## Предпочтения бренда"] + ([f"- {p['text']} (`{p['id']}`)" for p in bp] or ["- пока нет"])
        L += ["", "Методы с областью этого бренда: " + (", ".join(f"`{k['id']}`" for k in cards if k.get("brand_scope") == b["id"]) or "нет (используются общие методы `all`)")]
        fn = _slug(b["name"].replace("×", "x")) + ".md"
        (pkg / "brands" / fn).write_text("\n".join(L) + "\n", encoding="utf-8")
        bmeta.append({"id": b["id"], "name": b["name"], "version": b["version"], "file": f"brands/{fn}"})
    ML = ["# Режимы производства и источник голоса", "",
          "Режим берётся из брифа; если не указан — `Режим по умолчанию` из профиля бренда. Конкретный бриф выпуска не меняет профиль бренда.", "",
          "| Режим | Что это | Правила |", "|---|---|---|"]
    ML += [f"| `{k}` | {v} | {brands_mod.MODE_RULES.get(k, '—')} |" for k, v in brands_mod.PROJECT_MODES.items()]
    ML += ["", "## Источник голоса (поле брифа)", ""] + [f"- `{k}` — {v}" for k, v in brands_mod.VOICE_SOURCES.items()]
    (pkg / "brands" / "modes.md").write_text("\n".join(ML) + "\n", encoding="utf-8")
    # preferences with scope
    prefs = db.rows(con.execute("SELECT * FROM preferences WHERE active=1"))
    inc = [p for p in prefs if p["scope"] in ("studio", "brand")]
    exc = [p for p in prefs if p["scope"] not in ("studio", "brand")]
    bname = {b["id"]: b["name"] for b in brands_mod.list_brands(con)}
    PL = ["# Предпочтения владельца", "", "Применяются по области: `studio` — во всех работах; `brand` — только для указанного бренда.",
          f"Правки уровня проекта/ролика в пакет не входят (их {len(exc)}): они относились к конкретным работам.", "",
          "| ID | Область | Предпочтение | Как применять | Происхождение |", "|---|---|---|---|---|"]
    def how(p):
        if re.match(r"(?i)\s*режим", p["text"]):
            return "режим должен быть доступен; применяется, когда бриф выбирает этот режим"
        return "всегда в своей области (бриф может переопределить явно)"
    PL += [f"| `{p['id']}` | {p['scope']}{' · ' + bname.get(p['scope_id'], p['scope_id']) if p['scope_id'] else ''} | {p['text']} | {how(p)} | {p['origin']} |" for p in inc]
    (pkg / "preferences.md").write_text("\n".join(PL) + "\n", encoding="utf-8")
    # limitations
    def st(v, working=False):
        return ("не задано" if not v else ("задано (рабочее)" if working or "рабоч" in str(v).lower() or "РАБОЧ" in str(v) else "задано"))
    lim = ["# Ограничения методики", "",
           f"- Изучено роликов: {len(ex)}. Методов: {len(cards)} (автор объяснил: {sum(k['evidence'] == 'author_explained' for k in cards)}, "
           f"наблюдается: {sum(k['evidence'] == 'observed' for k in cards)}, гипотез: {sum(k['evidence'] == 'hypothesis' for k in cards)}; "
           f"требуют проверки: {sum('needs-review' in (k.get('tags') or []) for k in cards)}).",
           "- Визуал разобран по выборке кадров (1–2 с) с плотными проходами (0,1–0,25 с) только в ключевых интервалах.",
           "- Звук описан по оценкам звуковой модели (классы AudioSet + акустические признаки); человеком не прослушан. Конкретные треки не определялись.",
           "- Эффективность приёмов (удержание, охваты) нигде не измерена; утверждения авторов не проверены по первоисточникам.",
           "- Подтверждённых результатом проекта правил пока нет.", "",
           "## Состояние профилей брендов (на момент экспорта)", ""]
    for b in brands_mod.list_brands(con):
        d = b.get("data") or {}
        extra = [f"{label} — {st(d.get(k), True)}" for k, label in (("product", "описание продукта"), ("typography", "шрифт")) if k in d]
        lim.append(f"- **{b['name']}:** " + "; ".join([f"цвета — {st(d.get('palette'), True)}", *extra,
                                                       f"аудитория — {st(d.get('audience'), True)}", f"логотип — {d.get('logo') or 'не задано'}"]) + ".")
    lim.append("- Рабочие значения можно использовать, но не выдавать за утверждённые владельцем.")
    (pkg / "provenance").mkdir(exist_ok=True)
    (pkg / "provenance" / "LIMITATIONS.md").write_text("\n".join(lim) + "\n", encoding="utf-8")
    # fill placeholders
    for f in pkg.rglob("*.md"):
        t = f.read_text(encoding="utf-8")
        t = t.replace("{{NAME}}", name).replace("{{VERSION}}", version).replace("{{DATE}}", local_date) \
             .replace("{{N_METHODS}}", str(len(cards))).replace("{{N_EXAMPLES}}", str(len(ex)))
        f.write_text(t, encoding="utf-8")
    # changelog vs previous export
    prev_man = json.loads((prev[-1] / name / "provenance" / "manifest.json").read_text()) if prev else None
    cur = {k["id"]: k["version"] for k in cards}
    ch = [f"# Изменения версии {version}", ""]
    if prev_man:
        old = {m["id"]: m["version"] for m in prev_man["methods"]}
        ch += [f"Сравнение с {prev_man['version']}:", ""]
        ch += [f"- добавлен `{i}`" for i in cur if i not in old]
        ch += [f"- удалён `{i}`" for i in old if i not in cur]
        ch += [f"- обновлён `{i}` v{old[i]} → v{cur[i]}" for i in cur if i in old and old[i] != cur[i]]
        if len(ch) == 4:
            ch.append("- изменений в методах нет")
        cur_files = {str(f.relative_to(pkg)): _sha(f) for f in pkg.rglob("*") if f.is_file()
                     and not str(f.relative_to(pkg)).startswith(("methods/", "provenance/", "examples/"))}
        old_files = {k: v for k, v in prev_man.get("files", {}).items() if not k.startswith(("methods/", "provenance/", "examples/"))}
        ch += ["", "Процесс, шаблоны, бренды, предпочтения:"]
        diff = [f"- добавлен файл `{k}`" for k in cur_files if k not in old_files] + \
               [f"- удалён файл `{k}`" for k in old_files if k not in cur_files] + \
               [f"- изменён `{k}`" for k in cur_files if k in old_files and old_files[k] != cur_files[k]]
        ch += diff or ["- без изменений"]
    else:
        ch.append("Первая версия пакета.")
    (pkg / "provenance" / "CHANGELOG.md").write_text("\n".join(ch) + "\n", encoding="utf-8")
    # manifest with provenance of every rule
    man = {"package": name, "version": version, "exported_at": db.now(), "format": "creative-skill/1",
           "entry": "SKILL.md",
           "methods": [{"id": k["id"], "title": k["title"], "version": k["version"], "evidence": k["evidence"],
                        "needs_review": "needs-review" in (k.get("tags") or []), "brand_scope": k.get("brand_scope") or "all",
                        "origin": k.get("origin"), "updated_at": k.get("updated_at"),
                        "history": [{"version": h["version"], "at": h["changed_at"], "by": h["author"], "note": h["note"]} for h in k["history"]],
                        "examples": sorted({ex[r["source_id"]] for r in k["refs"]}),
                        "file": f"methods/{k['category']}/{k['id']}.md"} for k in cards],
           "examples": ex_meta, "brands": bmeta,
           "preferences": [{"id": p["id"], "scope": p["scope"], "scope_id": p["scope_id"], "origin": p["origin"], "created_at": p["created_at"]} for p in inc],
           "excluded_preferences": len(exc)}
    files = {}
    for f in sorted(pkg.rglob("*")):
        if f.is_file() and f.name != "manifest.json":
            files[str(f.relative_to(pkg))] = _sha(f)
    man["files"] = files
    (pkg / "provenance" / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    zp = pkg.parent / f"{name}-v{v}.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(pkg.rglob("*")):
            if f.is_file():
                z.write(f, f"{name}/{f.relative_to(pkg)}")
    chk = check(pkg)
    return {"version": version, "dir": config.rel(pkg), "zip": config.rel(zp), "methods": len(cards), "examples": len(ex),
            "files": len(files) + 1, "check": chk}


def check(pkg: Path) -> dict:
    """Self-containment check of a package directory (works on any copy, no studio needed)."""
    pkg = Path(pkg)
    problems, warnings = [], []
    for r in REQUIRED:
        if not (pkg / r).exists():
            problems.append(f"нет обязательного файла {r}")
    ids = {p.stem for p in (pkg / "methods").rglob("kn_*.md")}
    for f in pkg.rglob("*"):
        if not f.is_file() or f.suffix not in (".md", ".csv", ".json"):
            continue
        t = f.read_text(encoding="utf-8")
        rel = f.relative_to(pkg)
        if rel.as_posix() != "provenance/manifest.json":
            for pat, what in FORBIDDEN:
                if re.search(pat, t):
                    problems.append(f"{rel}: {what} ({re.search(pat, t).group(0)})")
        for kid in set(re.findall(r"kn_\d{6}_[0-9a-f]{6}", t)):
            if kid not in ids:
                problems.append(f"{rel}: ссылка на несуществующий метод {kid}")
        if f.suffix == ".md":
            for link in re.findall(r"\]\(([^)#\s]+)", t):
                if link.startswith(("http://", "https://", "mailto:")):
                    continue
                if not (f.parent / link).resolve().exists():
                    problems.append(f"{rel}: битая ссылка {link}")
    man_p = pkg / "provenance" / "manifest.json"
    if man_p.exists():
        man = json.loads(man_p.read_text())
        for rel, h in man.get("files", {}).items():
            p = pkg / rel
            if not p.exists():
                problems.append(f"манифест: нет файла {rel}")
            elif _sha(p) != h:
                warnings.append(f"изменён после экспорта: {rel}")
        if len(ids) != len(man.get("methods", [])):
            problems.append(f"методов в папке {len(ids)}, в манифесте {len(man.get('methods', []))}")
    sk = (pkg / "SKILL.md").read_text(encoding="utf-8") if (pkg / "SKILL.md").exists() else ""
    if not sk.startswith("---") or "name:" not in sk.split("---")[1]:
        problems.append("SKILL.md без frontmatter name/description")
    return {"ok": not problems, "methods": len(ids), "problems": problems[:50], "problem_count": len(problems), "warnings": warnings[:20]}


def check_output(pkg: Path, out: Path) -> dict:
    """Mechanical checks of a work produced WITH the package (the rubric's judgement parts need a reviewer)."""
    import csv
    pkg, out = Path(pkg), Path(out)
    ids = {p.stem for p in (pkg / "methods").rglob("kn_*.md")}
    res: dict = {"files": sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())}
    text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in out.rglob("*") if p.suffix in (".md", ".csv", ".json"))
    used = set(re.findall(r"kn_\d{6}_[0-9a-f]{6}", text))
    res["methods_mentioned"] = sorted(used & ids)
    res["unknown_method_ids"] = sorted(used - ids)
    sb = next(iter(out.rglob("storyboard.csv")), None)
    if sb:
        rows = list(csv.DictReader(sb.open(encoding="utf-8")))
        res["storyboard_rows"] = len(rows)
        res["rows_without_basis"] = [r.get("scene") for r in rows if not (r.get("basis") or "").strip()]
        res["storyboard_methods"] = sorted({m for r in rows for m in re.findall(r"kn_\d{6}_[0-9a-f]{6}", r.get("basis") or "")})
        try:
            res["storyboard_duration_s"] = round(max(float(r["t_end"]) for r in rows) - min(float(r["t_start"]) for r in rows), 1)
        except (KeyError, ValueError):
            res["storyboard_duration_s"] = None
        # плотность речи по сценам: средняя может скрывать перегруженную реплику
        dens = []
        for r in rows:
            try:
                d = float(r["t_end"]) - float(r["t_start"])
            except (KeyError, ValueError):
                continue
            v = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", r.get("voice") or "")
            if re.match(r"\s*(нет|—|-|none|тишина|пауза)\b", v, re.I):
                continue
            n = len(re.findall(r"[A-Za-zА-Яа-яЁё0-9’']+", v))
            if d > 0 and n:
                dens.append({"scene": r.get("scene"), "words": n, "sec": round(d, 1), "wpm": round(n / d * 60)})
        res["scene_wpm_over_170"] = [x for x in dens if x["wpm"] > 170]
    else:
        res["storyboard_rows"] = 0
    # voice: pending own recording must become a production task, never a synthetic substitute
    brief = next(iter(out.rglob("brief.md")), None)
    btxt = brief.read_text(encoding="utf-8") if brief else ""
    pending = bool(re.search(r"own_recording_pending|запис\w* (голоса )?(ещё |пока )?(нет|не предоставлен)", btxt, re.I))
    assets = next(iter(out.rglob("assets.csv")), None)
    a_rows = list(csv.DictReader(assets.open(encoding="utf-8"))) if assets else []
    res["voice_pending"] = pending
    if pending:
        res["voice_record_asset"] = any("записать голос" in (r.get("status") or "").lower() for r in a_rows)
        res["recording_task"] = any(out.rglob("recording-task.md"))
    # voiceover density
    vo = next(iter(out.rglob("voiceover.md")), None)
    if vo:
        t = vo.read_text(encoding="utf-8")
        m = re.search(r"##\s*Чистый текст\s*\n(.*?)(\n##\s|\Z)", t, re.S)
        body = m.group(1) if m else t
        body = re.sub(r"^>\s*", "", body, flags=re.M)
        body = "\n".join(l for l in body.splitlines()
                          if l.strip() and not re.match(r"\s*[(≈*—–-]|.*(Вариант|вариант|слов[оа]?\b|сл/с)", l))
        body = re.sub(r"\[[^\]]*\]|\([^)]*\)|^#.*$|^[-*]\s.*:$|^---$", " ", body, flags=re.M)
        words = re.findall(r"[A-Za-zА-Яа-яЁё0-9’']+", body)
        res["voiceover_words"] = len(words)
        if res.get("storyboard_duration_s"):
            res["words_per_minute"] = round(len(words) / res["storyboard_duration_s"] * 60)
    # originality: 6-word shingles shared with example texts (quotes, speech) — copied phrasing indicator
    def sh(t):
        w = re.findall(r"\w+", t.lower())
        return {" ".join(w[i:i + 6]) for i in range(len(w) - 5)}
    ex_text = "\n".join(p.read_text(encoding="utf-8") for p in (pkg / "examples").glob("ex*.md"))
    quotes = re.findall(r"«([^»]{25,})»", ex_text) + re.findall(r"\| [^|]+ \| ([^|]{25,}) \|", ex_text)
    common = sh(text) & sh("\n".join(quotes))
    res["shared_6grams_with_examples"] = sorted(common)[:20]
    res["ok_mechanical"] = bool(res.get("storyboard_methods")) and not res["unknown_method_ids"] and not res.get("rows_without_basis") \
        and res["storyboard_rows"] > 0 and len(common) <= 2 and not res.get("scene_wpm_over_170") \
        and (not res.get("voice_pending") or (res.get("voice_record_asset") and res.get("recording_task")))
    return res
