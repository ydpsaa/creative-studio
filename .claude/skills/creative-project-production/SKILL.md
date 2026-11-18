---
name: creative-project-production
description: Применение базы знаний Creative Studio к новому ролику — бриф, подбор карточек, концепция, hooks, сценарий, озвучка, раскадровка с основаниями, ассеты, промпты, экспорт ZIP для монтажа. Использовать, когда пользователь просит придумать/написать ролик, сценарий или раскадровку для любого бренда из студии.
---

# Производство ролика из базы знаний

## 1. Бриф
Нужны: бренд, цель, аудитория, площадка, длительность, язык, режим (`standard | voice_only | to_camera | cinematic`), материалы, ограничения, CTA.
Не хватает — взять из профиля бренда (`studio-data/brands/<id>/brand.md`) и предпочтений, отметить **допущения**. Спрашивать только то, от чего результат зависит существенно.
Создать проект: UI «Проекты → Новый проект» или
```bash
.venv/bin/python -c "import sys;sys.path.insert(0,'app/api');from studio import db,projects;print(projects.create(db.open_db(),'Название',{...})['id'])"
```

## 2. Подбор знаний
```bash
./studio pack <prj_id>     # studio-data/projects/<prj>/packs/project-generation-*.json
```
Прочитать пакет: карточки (evidence, when_not, limitations), фрагменты источников с контекстом, предпочтения (studio/brand/project/clip).
- Карточки чужого бренда не применять. `hypothesis` и `needs-review` — только как творческий выбор, с пометкой.
- Не копировать сюжеты/фразы/графику источников — переносить методы.

## 3. Документы `project-docs/1`
`concept, hooks (≥3 различных), script, voiceover (+ указания по произнесению), storyboard[{scene,t_start,t_end,timing:"оценка",visual,voice,on_screen_text,sound,transition,asset_id,asset_task,basis}], assets[{asset_id,description,status,source_id,usage_status,how_to_get,prompt_ref}], generation-prompts, edit-notes, review`.
- В каждой строке раскадровки `basis` — ID карточек.
- Режим `voice_only`: для каждой сцены конкретный способ визуала (запись экрана, свои материалы, разрешённые ассеты, AI-кадр по промпту, типографика/HTML-анимация).
- Промпт ≠ готовое изображение. Тайминги — оценка до записи голоса.

## 4. Импорт, правки, экспорт
```bash
./studio import-project <prj_id> studio-data/inbox/project-<prj_id>.json
./studio export <prj_id> --mode portable     # или links
```
Правки пользователя → `review.md` и предпочтение с областью (clip/project/brand/studio) в UI «Бренды → Предпочтения». Разовую правку не делать правилом бренда.
