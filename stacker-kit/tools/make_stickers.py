"""Turn AI-generated 3×3 object grids into die-cut stickers (macOS: subject lift via Apple Vision).

1. Generate a 1:1 grid image per group with the prompt from grid_prompts.json (any image model; ours: Gemini).
2. Build the cutter once:  swiftc -O tools/cutout.swift -o tools/cutout
3. python tools/make_stickers.py G19_myset grid.png "coffee cup" "laptop" … (9 names, reading order)
   → stickers/xerox/G19_myset/01_coffee_cup.png …
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

KIT = Path(__file__).resolve().parents[1]
CUT = KIT / "tools" / "cutout"


def cells(im, n=3):
    w, h = im.size
    for r in range(n):
        for c in range(n):
            yield im.crop((c * w // n, r * h // n, (c + 1) * w // n, (r + 1) * h // n))


def cutout(cell):
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d) / "c.png", Path(d) / "o.png"
        cell.convert("RGB").save(src)
        r = subprocess.run([str(CUT), str(src), str(dst)], capture_output=True, text=True)
        if r.returncode or not dst.exists():
            return None
        im = Image.open(dst).convert("RGBA")
        bb = im.getbbox()
        return im.crop(bb) if bb else None


def finish(obj, target=640):
    a = np.asarray(obj).copy()
    a[..., 3] = np.clip((a[..., 3].astype(np.float32) - 60) * 1.6, 0, 255).astype(np.uint8)  # crisp edge, no grey halo
    im = Image.fromarray(a)
    s = target / max(im.size)
    if s > 1:
        im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS).filter(ImageFilter.UnsharpMask(2, 60, 2))
    return im


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    if not CUT.exists():
        sys.exit("Сначала соберите вырезалку: swiftc -O tools/cutout.swift -o tools/cutout")
    group, grid, names = sys.argv[1], Path(sys.argv[2]), sys.argv[3:]
    out = KIT / "stickers" / "xerox" / group
    out.mkdir(parents=True, exist_ok=True)
    for k, (cell, name) in enumerate(zip(cells(Image.open(grid)), names)):
        obj = cutout(cell)
        if obj is None:
            print("  нет объекта:", name)
            continue
        p = out / f"{k + 1:02d}_{name.strip().replace(' ', '_')}.png"
        finish(obj).save(p)
        print(p.relative_to(KIT))


if __name__ == "__main__":
    main()
