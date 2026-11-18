# Ожидаемый ответ анализа источника — `analysis/1`

Ответ — один JSON-объект. Импорт: `./studio import-analysis <source_id> answer.json` (или `--dry-run` для проверки).
Импорт отклоняется целиком, если таймкод выходит за длительность, кадр не существует, источник не найден
или у карточки знания нет ссылки на источник. Проверка структуры не доказывает истинность — выводы остаются проверяемыми вручную.

## Правила доказательности

- `author_explained` — автор прямо говорит/показывает это как совет. Дайте цитату (`quote`) из транскрипта или OCR.
- `observed` — приём виден/слышен в ролике, но автор его не объясняет.
- `hypothesis` — ваше объяснение «почему это работает». Никогда не выдавайте его за слова автора.
- `user_preference`, `project_confirmed` — **запрещены** в анализе: их ставит только пользователь/результат проекта.
- Не приписывайте ролику элементы, которых в нём нет: отсутствующий элемент структуры — `null`.
- Не делайте выводов о звучании по waveform или имени файла. Если звук не прослушивался — так и напишите в `sound.limitations` и `coverage.audio_listened=false`.
- Один пример ≠ универсальный закон: указывайте `when_not` и `limitations`.

## Структура

```json
{
  "schema": "analysis/1",
  "source_id": "src_…",
  "origin": "claude-manual",            // claude-manual | provider:<name> | user
  "model": "claude-opus-5-5",           // если известно
  "prompt_version": "source-analysis/1",
  "purpose": "обучение",                // обучение | референс | сочетание (необязательно)
  "source_type": "tutorial | reference | mixed",
  "summary": "2–5 предложений: о чём ролик и чем он полезен студии",
  "timeline": [
    {"t_start": 0.0, "t_end": 4.9,
     "speech": "что говорится (кратко, по транскрипту)",
     "visual": "что видно: план, действие, графика, титры",
     "audio": "что слышно, если известно; иначе 'не прослушано, по транскрипту: …'",
     "teaches": "чему это учит / какую роль играет",
     "evidence": [{"type": "frame", "file": "frames/f003_0003.00.jpg"},
                  {"type": "transcript", "t": 0.0}, {"type": "ocr", "t": 2.0}]}
  ],
  "on_screen": [ {"t": 2.0, "frame": "frames/…jpg", "kind": "caption|tool|parameter|formula|ui_step|before_after|brand", "text": "…"} ],
  "editing": {"composition": "…", "shots": "…", "camera_movement": "…", "pace": "…", "transitions": "…",
              "graphics_titles": "…", "light_color": "…", "captions_style": "…"},
  "sound": {"observations": ["…"], "limitations": "…"},
  "structure": {"hook": {"t_start":0,"t_end":4.9,"description":"…"}, "development": {…} | null,
                "proof": … | null, "turn": … | null, "ending": … | null, "cta": … | null},
  "methodology": null | {"inputs": "…", "steps": [{"action":"…","settings":"…","t_start":0,"t_end":1}],
                         "result": "…", "mistakes": "…", "limitations": "…"},
  "knowledge": [
    {"title": "…", "kind": "rule|method|script_structure|hook|editing_technique|visual_style|sound_technique|template|anti_example|workflow",
     "category": "scripting|hooks|storytelling|cinematography|editing|sound|visual-styles|workflows|examples|templates",
     "statement": "понятная формулировка", "task": "какую практическую задачу решает",
     "when_use": "…", "when_not": "…", "conditions": "…",
     "steps": ["шаг 1", "шаг 2"], "example": "конкретный пример (можно новый, оригинальный)", "mistake": "типичная ошибка",
     "evidence": "author_explained|observed|hypothesis", "limitations": "…",
     "brand_scope": "all", "format_scope": "вертикальное видео 9:16, 15–90 с", "language": "ru",
     "tags": ["hook", "retention", "voice-only"],
     "refs": [{"source_id": "src_…", "t_start": 0.0, "t_end": 4.9, "frame": "frames/…jpg", "quote": "дословно из транскрипта"}]}
  ],
  "coverage": {"frames_viewed": ["frames/…jpg"], "transcript_read": true, "ocr_read": true, "audio_listened": false,
               "not_covered": ["что не проверено и почему"]},
  "open_questions": ["…"]
}
```

Тэги `voice-only`, `no-face`, `ai-visuals`, `screen-recording` собирают playbook «Производство без съёмки автора».

## Дополнения схемы (2026-11-10, версия разбора v2+)

- **Пересмотр существующей карточки:** укажите `"id": "kn_…"` (карточка этого источника) и `"change_note"`. Карточка получит новую версию с историей; ID в проектах не ломаются. Карточки без `id` создаются заново; немодифицированные карточки прошлой версии, которых нет в ответе, станут `superseded`.
- **Звук:** если у источника есть `sound_map.json` (AST + librosa), используйте его и ставьте `coverage.audio_analyzed_by_model: true`. Интерпретация метки модели — `observed` только при высокой вероятности и совпадении с изображением; иначе — `hypothesis`. `audio_listened` остаётся `false` (человек/Claude не слушали).
- **Сомнительная речь:** после проверки сегментов с пометкой «сомнительно» поставьте `coverage.unclear_speech_checked: true` и опишите вывод в `not_covered`/`open_questions`.
- **Плотные кадры:** `./studio dense <src> <t0> <t1> --step 0.1` создаёт кадры `frames/dNNNNNN.jpg` (NNNNNN — миллисекунды), их можно цитировать как любые кадры.
- **Требует проверки:** утверждение, которое нельзя подтвердить имеющимися данными, оставьте с тэгом `needs-review` и объяснением в `limitations`, либо понизьте до `hypothesis`.
