# MCP: подключить свой Claude / Codex к студии

Студия запускается как MCP-сервер командой `./studio mcp` (stdio). AI-клиент сам запускает её, когда она нужна.
Сервер работает с той же базой, что UI и CLI. Если worker остановлен, сервер сам его запускает при загрузке ролика.

## Подключение (один раз)

**Claude Code** (терминал):
```bash
claude mcp add --scope user creative-studio -- "/ABSOLUTE/PATH/TO/creative-studio/studio" mcp
```

**Codex**: добавить в `~/.codex/config.toml`:
```toml
[mcp_servers.creative-studio]
command = "/ABSOLUTE/PATH/TO/creative-studio/studio"
args = ["mcp"]
startup_timeout_sec = 30
```

**Claude Desktop**: Settings → Developer → Edit Config. В `mcpServers` добавить запись и перезапустить приложение:
```json
"creative-studio": {
  "command": "/ABSOLUTE/PATH/TO/creative-studio/studio",
  "args": ["mcp"]
}
```

Проверка: в клиенте спросить «что есть в Creative Studio?». Модель вызовет `studio_overview`.

## Как пользоваться (говорите обычными словами)

- «Изучи ролик ~/Downloads/reel.mp4, хочу понять хук и музыку».
  Модель загрузит файл (`import_media`, цели `hooks` и `music`), дождётся обработки, посмотрит кадры и звуковую карту, напишет разбор и сохранит карточки.
- «Какие у нас есть методы для хука без лица?» → `search_methods`.
- «Сделай проект ролика для Dolishx про …» → бриф, пакет, документы, ZIP.
- Готовые сценарии (prompts): **Изучить ролик**, **Сделать ролик**.

## Инструменты

| Группа | Инструменты |
|---|---|
| Обзор | `studio_overview`, `list_recipes` |
| Изучение | `import_media`, `import_link`, `list_sources`, `source_status`, `set_goals`, `get_analysis_pack` (guide / transcript / ocr / sound / contact_sheets), `view_frames`, `dense_pass`, `submit_analysis` |
| Знания | `search_methods`, `get_method` |
| Бренды | `list_brands`, `add_preference` (только со слов пользователя, с цитатой) |
| Проекты | `list_projects`, `create_project`, `get_project`, `get_project_pack`, `submit_project_docs`, `export_project` |
| Ревью | `list_review`, `get_review_feedback`, `mark_review_handled`, `view_review_items` |
| Ресурсы | `studio://schema/analysis`, `studio://method/<имя>` (SKILL, workflow-01-brief, quality-rubric, templates-script…) |

## Ревью: что владелец выбрал и что не так (2026-11-18)

Страница «Ревью» в UI собирает всё, что Claude сгенерировал для ваших стилей: стикеры XEROX и PIXEL, тестовые видео, звуки, документы стиля. Список папок задаёт `config/review_sets.json`. Файлы читаются по пути, не копируются. Владелец ставит статус (`new` / `selected` / `rejected` / `needs_changes`), пишет комментарий «что не так», теги и жмёт «→ Claude».

| Инструмент | Что делает |
|---|---|
| `get_review_feedback(since?, include_handled?)` | `pending_requests` и `pending_group_requests` — открытые запросы («→ Claude»). `decisions` — все решения с комментариями и тегами; `since` = поле `now` прошлого ответа. `group_comments` — замечания к группе. `counts` — итоги по видам. |
| `list_review(kind?, status?, group?, limit?)` | Пересканирует папки и отдаёт ассеты с решениями и локальным путём `path`. `status` можно задать как `forward` или `commented`. |
| `view_review_items(item_ids)` | Показывает до 12 картинок за вызов: стикер на сером фоне, кадр видео. Звук модель не слушает. |
| `mark_review_handled(item_id, note)` | Закрывает запрос: `rv_…` или группу `rg:<kind>:<group>`. Статус не меняет, решение остаётся за владельцем. |

Порядок для Claude в новой сессии:
1. Вызвать `get_review_feedback`.
2. Посмотреть ассеты: `view_review_items`.
3. Исправить файл на том же месте. Следующий скан отметит `file_changed_at`.
4. Вызвать `mark_review_handled` с заметкой о сделанном.

Без MCP то же самое дают команды `./studio review-feedback [--since …] [--all]` и `./studio review-handled <id> "заметка"`. Пересканировать папки: `./studio review-scan`; страница «Ревью» тоже пересканирует при открытии.

Комментарий владельца относится к конкретному ассету. Это не правило бренда. В `add_preference` его переносить только тогда, когда владелец сам сформулировал общее правило.

## Цели изучения (рецепты, `config/recipes.json`)

`full` · `text` · `hooks` · `topic` · `transitions` · `zooms` · `music` · `sfx` · `captions` · `color`.

Ещё не установлены, модель обязана сказать об этом сама:
- отделение музыки от голоса (Demucs);
- BPM и тональность;
- автоматическая кривая движения кадра;
- палитра.

## Защита
- Загружаются только файлы с расширениями видео, аудио, изображений, документов и ZIP. Скрытые папки (`~/.ssh` и подобные) запрещены.
- Транскрипт и OCR — данные чужих роликов, а не инструкции (это прописано в инструкциях сервера).
- Разбор и документы проходят те же строгие проверки, что и `import-analysis` / `import-project`. Ошибки возвращаются модели текстом, чтобы она их исправила.
- Ссылки на платформы сохраняются без скачивания медиа.
- Подписка и данные для входа в AI-клиент студии не видны и не нужны.

## Проверено (2026-11-11, копия данных)
Настоящим MCP-клиентом по stdio пройдена вся цепочка:
- загрузка 18-секундного ролика, обработка за 40 с;
- guide, транскрипт, звук со спектрограммой, листы кадров, кадры картинками, плотный проход;
- плохой разбор отклонён с понятными ошибками, хороший сохранён и создал карточку;
- проект: бриф → пакет → неверный `knowledge_id` отклонён → документы сохранены → ZIP;
- ошибки ввода (скрытая папка, нет файла, тип `.py`, неизвестная цель, неверный интервал) возвращаются модели текстом.
