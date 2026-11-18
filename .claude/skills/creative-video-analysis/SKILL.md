---
name: creative-video-analysis
description: Последовательный разбор видео/аудио источника в Creative Studio — проверка извлечения (речь, кадры, OCR, звуковая карта), просмотр кадров, плотные проходы для коротких вставок, разбор по схеме analysis/1, импорт через единый валидатор и отчёт о покрытии. Использовать, когда пользователь просит изучить/разобрать ролик, добавленный в студию, или дополнить разбор.
---

# Разбор видео в Creative Studio

Работать из корня проекта `Creative-Studio/` (там лежит `./studio`). Никаких результатов «в голове»: всё, что считается изученным, проходит через `./studio import-analysis`.

## 1. Найти источник и убедиться, что извлечение завершено
```bash
./studio ps                      # worker должен работать; иначе ./studio start
./studio status
./studio source <src_id>         # jobs: probe, audio, transcribe, frames, ocr, audio_features, sound_events, context_pack
```
- Если этап `failed/blocked` — `./studio retry <job_id>`; если обработчик обновлён — `./studio rerun <src_id> <stage>`; новые этапы для старых источников — `./studio replan <src_id>`.
- Не начинать смысловой разбор, пока `context_pack` не `completed`.

## 2. Прочитать пакет `source-analysis`
`studio-data/derived/<src_id>/<новейший *-context_pack-*>/source-analysis/`:
`transcript.original.md` (⚠ сомнительные сегменты), `ocr.md`, `frames.json`, `contact_*.jpg`, `sound_map.md`, `spectrogram.png`, `technical.json`, `EXPECTED_ANSWER.md`.
- **Посмотреть все контактные листы** (Read по .jpg) и отдельные кадры там, где важны детали.
- **Посмотреть spectrogram.png** и сопоставить слои речь/музыка/эффект с картинкой.

## 3. Плотный проход там, где выборки мало
Короткие вставки, переходы, движение камеры, смена карточек, синхронизация звука с изображением:
```bash
./studio dense <src_id> <t0> <t1> --step 0.1     # кадры frames/dNNNNNN.jpg + OCR + контактные листы
```
Посмотреть листы. Выборка кадров ≠ просмотр каждого кадра — писать, что проверено плотно, а что по выборке.

## 4. Написать ответ `analysis/1`
Схема: `config/schemas/analysis.schema.md` (включая дополнения). Файл: `studio-data/inbox/analysis-<src_id>-vN.json`.
- timeline на всю длительность; `on_screen` отдельно от речи; `editing`, `sound`, `structure` (отсутствующее — `null`), `methodology` только для обучающих роликов.
- Звук: из `sound_map.json` (оценки AST/librosa). `coverage.audio_analyzed_by_model=true`, `audio_listened=false`. Названия треков/эффектов не угадывать.
- Пересмотр существующей карточки — с `"id"` и `"change_note"` (ID в проектах не ломаются).
- Карточки знаний — по навыку **creative-knowledge-extraction**.

## 5. Проверить и импортировать
```bash
./studio import-analysis <src_id> studio-data/inbox/analysis-<src_id>-vN.json --dry-run
./studio import-analysis <src_id> studio-data/inbox/analysis-<src_id>-vN.json
```
Ошибки валидатора (несуществующий кадр, таймкод вне длительности, чужой ID) исправлять, а не обходить.

## 6. Отчёт о покрытии
```bash
./studio coverage-report <src_id>
```
Показать пользователю таблицу как есть. Статус «разобрано» означает выполнение плана, а не извлечение всех деталей; если статус `partial (...)` — назвать недостающее.
