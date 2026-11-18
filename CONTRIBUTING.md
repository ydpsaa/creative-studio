# Contributing

Thanks for wanting to help! Creative Studio is a local studio + MCP server; the STACKER kit is a video style built on it.

## Setup
```bash
uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python -r requirements.lock.txt
(cd app/web && npm install && npm run build)
./studio doctor        # tells you what is missing and how to fix it
./studio start         # http://127.0.0.1:8765
```

## Before a pull request
- `python tests/ci_smoke.py` — fast checks (also run by CI on every push).
- `.venv/bin/python tests/acceptance.py` — full acceptance on a Mac (isolated data dir, test fixtures; never touches your `studio-data/`).
- UI changes: `cd app/web && npm run build` must pass (TypeScript is strict).

## Rules of the house
- **Nothing gets into the knowledge base without the validator.** Analyses go through `import-analysis` / `submit_analysis`, project docs through `import-project` / `submit_project_docs`.
- **Be honest about coverage.** A transcript is not a visual analysis; sampled frames are not every frame; models don't hear audio — say so.
- **No third-party media in the repo.** Stickers, sounds and fonts must be ours, CC0/CC-BY/OFL or generated in code. Other people's videos are a source of methods, never of footage.
- **No downloading behind logins.** A closed link stays a card with a status and "add the file".
- UI text is Russian for now; English UI is on the roadmap — PRs welcome.

## Where things are
| Path | What |
|---|---|
| `app/api/studio/` | FastAPI app, CLI, worker, MCP server (`mcp_server.py`), stacker (`stacker.py`), review board, doctor |
| `app/web/src/` | React UI (pages in `pages/`) |
| `stacker-kit/` | STACKER renderer, stickers, style guide, tools |
| `.claude/skills/` | Skills for Claude Code |
| `config/` | recipes, schemas, methodology package, review sets, links |
