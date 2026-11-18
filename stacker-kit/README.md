# STACKER kit

**Русская версия — [README.ru.md](README.ru.md)**

STACKER is a short-form video style made of words that appear exactly on the voice, stacked in a horizontal 4:3 band. Die-cut stickers drop on the key words. The intro has dry, punchy sound design. Then there is a hard cut to the author talking.

This kit has everything you need to make one today:
- the style guide below;
- a renderer;
- 162 ready XEROX stickers;
- the prompts and the tool to make your own stickers.

> The word-stack format is popular in short-form video. This kit is our own implementation of it: the code, the sticker pack and the sound design were made from scratch by [Dolishx](#links).

## 1. Anatomy of the style

**Frame.** The video is vertical 9:16 (1080×1920). The picture sits in a **horizontal 4:3 band** in the middle (1080×812, y = 554…1366), with black above and below. Both the intro and the talking part live in this band.

**Band colour: one per episode.** Deep blue `#0E1E5C`, burgundy `#870000`, near-black `#020002`. The colour is the identity of the episode.

**Intro (the first 6–12 s) is built page by page:**

| Page | What happens |
|---|---|
| **Stack** | 2–3 lines of 1–3 words. Lowercase, heavy grotesque, very tight tracking, white, modest size (~6% of the frame width). Each line has its own offset (left / right / centre). Every word **switches on exactly when it is spoken**. There is no animation: the precision of the sync *is* the effect. |
| **Punch word** | One word (BUT, DONE, HOW) in CAPITALS, black weight, full width. A long word splits into two lines (COMM / ANDS). |
| **Sticker** | It drops ~0.3–0.5 s after the last word of the phrase, in one frame, with no animation. It covers part of the text and takes ~35–45% of the band width. |
| **Tear** | The final word tears into pieces around the sticker (DO / NE, one piece turned 90°). A small series counter (`01/30`) sits under it. |

**The sticker is the literal picture of the key word**, never decoration: "idea" → light bulb, "deadline" → hourglass, "win" → trophy. Stickers are black-and-white (xerox / halftone / engraving) with a white die-cut border and a soft shadow.

**Talking part.** After a hard cut, the author speaks in the same 4:3 band. Captions show one phrase of 2–4 words at a time, centred at chest/chin height, in one accent colour (ours: amber `#FFB547`), with no outline and no box.

**Sound.** The intro has sound; the talking part is voice only.
- a dry snap on every new line;
- a paper/wood slap and a low thump when a sticker lands;
- a short impact on the punch word;
- a deep low boom when the last word tears;
- a shutter click on the cut to the author.

The renderer generates all five sounds in code, so you don't need any licence. You can also drop in your own sounds.

**Pace.** Give the voice a short pause (0.3–0.6 s) after each phrase that gets a sticker, and ~1.5 s after the last intro word, so the tear can be seen.

## 2. Make a video

You need the Creative Studio environment (see the main [README](../README.md)): macOS on Apple Silicon. The renderer uses ffmpeg, Pillow and numpy. Word timings come from mlx-whisper, locally.

1. **Write the intro**: 3–5 pages that hook in one thought. Then write the talking part.
2. **Record your voice**, or a video of yourself (phone, horizontal is best). Leave the pauses described above.
3. **Write a scenario.** Copy [examples/im_done.json](examples/im_done.json):
   ```json
   {
     "voice": "me.mov",
     "talk_video": "me.mov",
     "talk_offset": 0,
     "band_color": "#870000",
     "counter": "01/30",
     "pages": [
       {"rows": ["you tell", "everyone", "your idea."], "sticker": "G01_ideas/01_light_bulb.png"},
       {"punch": "but."},
       {"rows": ["your brain", "just heard..."], "sticker": "G01_ideas/02_human_brain_model.png"},
       {"punch": "i'm done.", "sticker": "G09_strength/06_trophy_cup.png", "tear": "done."}
     ]
   }
   ```
   - `pages` holds the intro words in the order you say them. The screen shows exactly this text; times come from your voice.
   - `voice` can be the video itself.
   - Pick stickers from [stickers/CATALOG.md](stickers/CATALOG.md).
   - Without `voice`, the timing is simulated from `script`. Use this to preview the layout before recording.
4. **Preview, then render:**
   ```bash
   ./studio stacker my_video.json --preview 1,2.5,4
   ./studio stacker my_video.json
   ```
   The first run transcribes the voice (word timings are cached next to the scenario). If a word is misheard, the renderer warns and keeps your text.

Try the demo right away (no voice needed):
```bash
./studio stacker stacker-kit/examples/im_done.json
```

### Let your AI do it
With Creative Studio connected to Claude Code (see [docs/CONNECT.md](../docs/CONNECT.md)), just ask: *"Make a stacker video from my recording ~/Movies/take3.mov about why I stopped announcing ideas"*. The skill [.claude/skills/stacker-video](../.claude/skills/stacker-video/SKILL.md) tells Claude how to:
- write the pages;
- pick stickers that literally match the key words;
- render, check the preview frames and fix them.

## 3. Your own stickers

1. Generate a 3×3 grid per group with any image model. The prompt per group is in [tools/grid_prompts.json](tools/grid_prompts.json), and the items are in [tools/pack_list.json](tools/pack_list.json). Swap the item names for your topics.
2. Build the cutter once (macOS, Apple Vision subject lift):
   ```bash
   swiftc -O stacker-kit/tools/cutout.swift -o stacker-kit/tools/cutout
   ```
3. Run:
   ```bash
   .venv/bin/python stacker-kit/tools/make_stickers.py G19_coffee grid.png "coffee cup" "laptop" … (9 names)
   ```

## Licence

- **Code** (`render_stacker.py`, `tools/*.py`, `tools/cutout.swift`): MIT, like the whole repository.
- **Style guide and sticker pack**: [CC BY 4.0](LICENSE). Use them in your videos, including commercial ones. Credit: *"STACKER kit by Dolishx"* with a link to this repository or the site, in the description or pinned comment.
- **Fonts**: Inter Tight, [SIL Open Font License 1.1](fonts/OFL.txt).

## Links
- Creative Studio (hosted version, no setup): https://dolishx-creative-studio.pan-manager.chatgpt.site
- Instagram: [@dolishx](https://www.instagram.com/dolishx/)
