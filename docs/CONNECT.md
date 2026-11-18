# Connect your AI to Creative Studio

Creative Studio runs as an MCP server: `./studio mcp` (stdio). Your AI client starts it when needed. It works on the same database as the UI and CLI. If the processing worker is stopped, the server starts it when you import a video.

In every config below, replace `/ABSOLUTE/PATH/TO/creative-studio` with the path of your clone. `pwd` in the repo folder prints it.

## Claude Code
```bash
claude mcp add --scope user creative-studio -- "/ABSOLUTE/PATH/TO/creative-studio/studio" mcp
```
The skills in `.claude/skills/` load automatically when you run `claude` inside the repo folder. They cover video analysis, knowledge cards, production and the STACKER style.

## Claude Desktop
Settings → Developer → Edit Config. Add this under `mcpServers`, then restart the app:
```json
"creative-studio": {
  "command": "/ABSOLUTE/PATH/TO/creative-studio/studio",
  "args": ["mcp"]
}
```

## Codex (CLI / IDE)
In `~/.codex/config.toml`:
```toml
[mcp_servers.creative-studio]
command = "/ABSOLUTE/PATH/TO/creative-studio/studio"
args = ["mcp"]
startup_timeout_sec = 30
```
Codex reads `AGENTS.md` in the repo for the studio rules.

## Cursor
In `~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project):
```json
{ "mcpServers": { "creative-studio": { "command": "/ABSOLUTE/PATH/TO/creative-studio/studio", "args": ["mcp"] } } }
```

## Gemini CLI and other MCP clients
Any client that can launch a **stdio** MCP server works the same way: command `/ABSOLUTE/PATH/TO/creative-studio/studio`, argument `mcp`. For Gemini CLI, put the same `mcpServers` entry in `~/.gemini/settings.json`.

## ChatGPT
ChatGPT connects to **remote** MCP servers over HTTPS, not to programs on your computer. To use Creative Studio from ChatGPT, use the hosted version at https://dolishx-creative-studio.pan-manager.chatgpt.site. Your local studio can sync with it (`./studio site-connect <url>`).

## Check it works
Ask your AI: *"What's in my Creative Studio?"* It should call `studio_overview` and answer with numbers of sources, methods and projects.

## What the AI may and may not do
- Transcripts and on-screen text from other people's videos are **data, not instructions**. The server tells the model this.
- The model doesn't hear audio. The sound map is classifier estimates, and the server forbids writing `audio_listened=true`.
- Rules about your brand (`add_preference`) are saved only from your own words, with a quote.
- Analyses and project documents pass the same strict validators as the CLI. Errors go back to the model, so it can fix them.
