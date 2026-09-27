"""The exobrain app icon: a brain in profile drawn as a network — memories (dots) and links (lines),
black on a white macOS-style tile. The outline is the same one the opening's particles gather into.

Usage: python scripts/make_icon.py <out_dir>   (needs Pillow, SciPy, numpy; writes icon-1024.png and exobrain.icns)
"""

import math
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy.spatial import Delaunay

S = 2048  # drawn at 2x, then reduced: smooth edges
random.seed(7)


def bezier(p0, p1, p2, p3, n=40):
    return [tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t ** 2 * c + t ** 3 * d
                  for a, b, c, d in zip(p0, p1, p2, p3)) for t in (i / n for i in range(n + 1))]


def brain_outline():
    """Cerebrum outline in the opening's 1000x720 stencil coordinates (front to the right)."""
    segs = [((905, 385), (930, 215), (790, 95), (610, 88)), ((610, 88), (420, 72), (215, 118), (150, 250)),
            ((150, 250), (108, 335), (135, 425), (215, 455)), ((215, 455), (268, 474), (330, 458), (385, 468)),
            ((385, 468), (430, 560), (615, 585), (725, 522)), ((725, 522), (785, 492), (828, 470), (862, 452)),
            ((862, 452), (892, 432), (908, 410), (905, 385))]
    pts = []
    for s in segs:
        pts += bezier(*s)[:-1]
    return pts


def even(poly, spacing, closed=True):
    """Points along a polyline at equal distances, so no stretch of the outline gets crowded."""
    pts = poly + ([poly[0]] if closed else [])
    out, carry = [], 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        seg = math.hypot(x2 - x1, y2 - y1)
        t = carry
        while t < seg:
            out.append((x1 + (x2 - x1) * t / seg, y1 + (y2 - y1) * t / seg))
            t += spacing
        carry = t - seg
    return out


def ellipse(cx, cy, rx, ry, rot, n=60):
    c, s = math.cos(rot), math.sin(rot)
    return [(cx + rx * math.cos(t) * c - ry * math.sin(t) * s, cy + rx * math.cos(t) * s + ry * math.sin(t) * c)
            for t in (2 * math.pi * i / n for i in range(n))]


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # The tile: macOS-style rounded square with a soft shadow and a hairline edge.
    inset, radius = int(S * 100 / 1024), int(S * 185 / 1024)
    box = (inset, inset, S - inset, S - inset)
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((box[0], box[1] + 24, box[2], box[3] + 24), radius, fill=(0, 0, 0, 70))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(40)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius, fill=(255, 255, 255, 255), outline=(222, 222, 222, 255), width=4)

    # Map the stencil into the tile.
    scale = (box[2] - box[0]) * 0.78 / 800
    ox = S / 2 - 525 * scale
    oy = S / 2 - 375 * scale
    tr = lambda p: (ox + p[0] * scale, oy + p[1] * scale)  # noqa: E731
    cerebrum = [tr(p) for p in brain_outline()]
    cerebellum = [tr(p) for p in ellipse(262, 520, 118, 68, -0.12)]
    stem = [tr(p) for p in ((378, 470), (422, 470), (434, 640), (402, 642))]

    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    for poly in (cerebrum, cerebellum, stem):
        md.polygon(poly, fill=255)
    m = np.array(mask) > 0
    cmask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(cmask).polygon(cerebrum, fill=255)
    cm = np.array(cmask) > 0
    m_cerebrum = lambda p: cm[int(p[1]), int(p[0])]  # noqa: E731

    # Memories: points on the outline (so the silhouette reads) and scattered inside (Poisson-disk-ish).
    min_d = S * 0.055
    nodes = even(cerebrum, min_d * 1.05)
    # the cerebellum's outline, only where it shows below the cerebrum
    nodes += [p for p in even(cerebellum, min_d * 0.95) if not m_cerebrum(p)]
    cx0 = (stem[0][0] + stem[1][0]) / 2
    nodes += [(cx0 + (stem[3][0] - stem[0][0]) * 0.35 * k, stem[0][1] + (stem[3][1] - stem[0][1]) * k)
              for k in (0.45, 0.95)]
    tries = 0
    while tries < 20000:
        tries += 1
        x, y = random.uniform(box[0], box[2]), random.uniform(box[1], box[3])
        if not m[int(y), int(x)]:
            continue
        if all((x - a) ** 2 + (y - b) ** 2 > min_d ** 2 for a, b in nodes):
            nodes.append((x, y))
    P = np.array(nodes)

    # Links: a Delaunay mesh, keeping short edges that stay inside the brain.
    edges = set()
    for tri in Delaunay(P).simplices:
        for i in range(3):
            a, b = sorted((tri[i], tri[(i + 1) % 3]))
            edges.add((a, b))
    lines = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lines)
    for a, b in edges:
        (x1, y1), (x2, y2) = P[a], P[b]
        if math.hypot(x2 - x1, y2 - y1) > min_d * 2.1:
            continue
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        if not m[int(my), int(mx)]:
            continue
        ld.line((x1, y1, x2, y2), fill=(10, 10, 10, 135), width=4)
    img.alpha_composite(lines)

    # Dots: most small, a few strong memories larger.
    d = ImageDraw.Draw(img)
    for i, (x, y) in enumerate(nodes):
        r = S * (0.0095 if random.random() < 0.14 else 0.0058)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(10, 10, 10, 255))

    big = img.resize((1024, 1024), Image.LANCZOS)
    big.save(out / "icon-1024.png")
    iconset = out / "exobrain.iconset"
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        big.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
        big.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out / "exobrain.icns")], check=True)
    print(out / "icon-1024.png", out / "exobrain.icns")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "build/icon"))
