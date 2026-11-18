# Архитектура

```
Browser (React+TS, app/web/dist) ──HTTP 127.0.0.1:8765──▶ FastAPI (app/api/studio/main.py)
                                                              │  читает/пишет
CLI ./studio ──────────────── тот же код ────────────────────▶ SQLite (WAL, FTS5) ◀── worker (jobs.worker_loop)
                                                              │                         │ processors.py
                                                              ▼                         ▼
                                                    studio-data/ (originals, derived, analyses, knowledge, projects, exports…)
```

## Модули (`app/api/studio/`)

| Модуль | Назначение |
|---|---|
| `config.py` | `STUDIO_DATA_DIR`, версии этапов, `safe_path` (запрет выхода за папку данных) |
| `db.py` | подключение, миграции (`PRAGMA user_version`), FTS-индекс `search` |
| `sources.py` | импорт файлов/ссылок/аккаунтов/заметок/ZIP, дедупликация по SHA-256, manifest.json |
| `jobs.py` | очередь: ключ задачи = хеш содержимого + этап + версия этапа; claim, heartbeat, восстановление, атомарная публикация, retry/cancel/rerun |
| `processors.py` | этапы: probe, audio, transcribe, frames, ocr, audio_features, image_probe, document_text, page_fetch, context_pack |
| `coverage.py` | coverage.json: что обработано каким методом и что нет |
| `results.py` | **единый проверяемый импорт** разбора (`analysis/1`) → analyses, карточки, индекс, экспорт |
| `knowledge.py` | карточки, версии, поиск, Markdown/JSON-экспорт, INDEX, playbooks, импорт правленой карточки |
| `brands.py` | бренды, предпочтения с областью действия, режимы производства |
| `projects.py` | бриф, подбор знаний, версии документов, пакет project-generation, импорт project-docs/1, ZIP-экспорт |
| `backup.py` | резервная копия (SQLite backup API + файлы + манифест), восстановление с проверкой |
| `main.py` | HTTP API, защита (Host-allowlist, `X-Studio` на запись), раздача UI |
| `cli.py` | команды `./studio …` |

## Состояния

Задачи: `queued → running → completed | partial | failed | blocked | cancelled`. Failed повторяется до 3 раз (кроме page_fetch).
Зависимость не выполнена → `blocked`. `context_pack` — «мягкая» зависимость: собирается из того, что есть.
Источник хранит отдельные статусы: получение, речь, изображение, звук, разбор, знания.

## Надёжность

- Результат этапа пишется во временную папку `derived/<src>/.tmp-<run>` → `os.replace` → одна транзакция БД (артефакты, статусы, индекс, состояние задачи).
- Heartbeat каждые 10 с; задача без heartbeat 120 с возвращается в очередь при старте/цикле worker.
- Отмена во время выполнения: результат выбрасывается при завершении этапа.
- Оригиналы копируются через `.part` + проверку хеша, затем доступны только для чтения.
- Резервная копия: `sqlite3.Connection.backup` (согласованный снимок при работе студии).

## Безопасность

Сервер на 127.0.0.1. Запросы с чужим `Host` (DNS rebinding) → 403. Любая запись требует заголовок `X-Studio: 1` и локальный `Origin` → посторонняя страница не может отправить форму.
Пути к файлам из API проходят `safe_path`. ZIP: проверка путей, симлинков, размера и количества файлов. Имена файлов нормализуются.
Тексты из уроков, страниц и транскриптов не исполняются и не влияют на настройки.
