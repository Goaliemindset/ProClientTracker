#!/usr/bin/env python3
"""Makes the white background of logo.png transparent (keeps the white outline around the letters).
Run from the tracker folder:  python3 fix_logo.py
The original is kept as logo_original.png.
"""
import os, re
from PIL import Image

if not os.path.exists("logo_original.png"):
    Image.open("logo.png").save("logo_original.png")
im = Image.open("logo_original.png").convert("RGBA")
w, h = im.size
px = im.load()

def is_background(p):
    return p[3] > 0 and max(p[:3]) - min(p[:3]) < 28 and sum(p[:3]) / 3 > 110

def fill(stack):
    seen = set()
    while stack:
        x, y = stack.pop()
        if (x, y) in seen or x < 0 or y < 0 or x >= im.size[0] or y >= im.size[1]:
            continue
        seen.add((x, y))
        if not is_background(px[x, y]):
            continue
        px[x, y] = (0, 0, 0, 0)
        stack += [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]

# background around the outside
fill([(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)] + [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)])
im = im.crop(im.getbbox())
px = im.load()
w, h = im.size
# the hole inside the P
fill([(int(w * 0.355), int(h * 0.24))])
im.save("logo.png")

# make the browser load the new image instead of a saved old copy
page = open("index.html").read()
page = re.sub(r'src="logo\.png(\?v=\d+)?"', 'src="logo.png?v=%d"' % (int(os.path.getmtime("logo.png")) % 100000), page)
open("index.html", "w").write(page)
print("done: logo.png now has a transparent background", im.size)
