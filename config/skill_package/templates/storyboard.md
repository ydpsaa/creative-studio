# Раскадровка

Поля: `scene, t_start, t_end, timing (оценка|точно), visual, voice, on_screen_text, sound, transition, asset_id, asset_task, basis`.
- `visual` — что в кадре и что движется; в режиме voice_only — конкретный источник (запись экрана / AI-кадр Pn / HTML-анимация / свой материал).
- `sound` — функция звука: подложка, тишина, пауза, SFX-акцент на событии.
- `basis` — ID методов через запятую или «собственное решение».
