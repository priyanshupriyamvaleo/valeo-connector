"""Build the connector's square icons from the Valeo wordmark.

Dev-only helper: needs Pillow, which the server itself does not. Run it when the
brand asset changes, then commit the generated PNGs.

    python3 assets/make_icon.py

The wordmark is a wide lockup and unreadable at 32px, so the icon uses just the V
mark on the brand yellow, which is what an icon at that size can actually carry.
"""

import collections
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "logo-source.png")

src = Image.open(SOURCE).convert("RGBA")
width, height = src.size

# Brand yellow: the most common fully-opaque colour in the lockup.
counts = collections.Counter(
    px[:3] for px in src.getdata() if px[3] > 250
)
brand = counts.most_common(1)[0][0]

# The V mark sits left of the type. Isolate it, then trim to the glyph itself.
mark_area = src.crop((0, 0, int(width * 0.44), height))
pixels = mark_area.load()
min_x, min_y, max_x, max_y = mark_area.width, mark_area.height, 0, 0
for y in range(mark_area.height):
    for x in range(mark_area.width):
        r, g, b, a = pixels[x, y]
        # The glyph is white-on-yellow; distance from the brand colour finds it.
        if a > 128 and abs(r - brand[0]) + abs(g - brand[1]) + abs(b - brand[2]) > 90:
            min_x, min_y = min(min_x, x), min(min_y, y)
            max_x, max_y = max(max_x, x), max(max_y, y)

glyph = mark_area.crop((min_x, min_y, max_x + 1, max_y + 1))
print("brand colour %s, glyph %dx%d" % (brand, glyph.width, glyph.height))

# Square canvas with the glyph at ~62% so it breathes inside a rounded mask.
side = int(max(glyph.size) / 0.62)
canvas = Image.new("RGBA", (side, side), brand + (255,))
canvas.alpha_composite(glyph, ((side - glyph.width) // 2, (side - glyph.height) // 2))

for size in (512, 128):
    out = os.path.join(HERE, "icon-%d.png" % size)
    canvas.resize((size, size), Image.LANCZOS).save(out, optimize=True)
    print("wrote %s (%d bytes)" % (out, os.path.getsize(out)))
