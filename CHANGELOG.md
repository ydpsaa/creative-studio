# Changelog

## 0.2.0 — 2026-11-18
- **STACKER in the app**: new «Стакер» page — write the intro pages, pick stickers from the catalog, upload your voice and your shot, preview frames with a timeline, render in the background, play and download.
- **Text-first sound design** (new default `sfx_style: typing`): every word is a key press, the punch word a heavier key, stickers land on paper, the last word rips like paper, the cut is a mouse click. No bass on text. The old impact set stays as `"sfx_style": "impact"`.
- **Your own sounds**: `stacker-kit/tools/make_sfx_takes.py` cuts a recording (your keyboard, paper) into clean takes; `"sfx": "<folder>"` uses them in turn.
- **4:5 format** for feed posts and carousels (`"format": "4:5"`), B-roll / screens for the talking part (`talk_start`), captions never run across a comma.
- **`./studio doctor`**: checks system, packages, models, UI, database, port, kits — and prints the exact fix.
- **CI** on every push (API, UI build, STACKER render, Review, doctor), issue templates, CONTRIBUTING.

## 0.1.0 — 2026-11-18 — first public release
- **Library and processing.** Import videos, audio, images, documents, links and notes. Local speech recognition with word timings, scene-cut and grid frames, OCR, sound map. Analysis packs for your AI.
- **Knowledge.** The strict `analysis/1` import, method cards with evidence levels and timecodes, full-text search.
- **Projects.** Brief → method selection → `project-docs/1` (concept, hooks, script, voice-over, storyboard with reasons) → export ZIP.
- **MCP server** (`./studio mcp`) with 25 tools, plus Claude skills for analysis, knowledge, production and STACKER.
- **STACKER kit.** Style guide (EN/RU), renderer (`./studio stacker`), 162 XEROX stickers, generated sound design, sticker-making tools.
- **Review board.** Pick, reject, comment on and forward AI-generated assets; your AI reads the decisions back through MCP.
